# -*- coding: utf-8 -*-
"""八轮新攻击 f:连胜减压草稿的作废对称面。
 前置:db 含 penalty=0.15 + 连胜2 → observe 起减压草稿(down,diff 摘除条目)
 f1) 尾部转 fail×1(<阈值,无方向)→ 横扫应作废减压草稿(diff 清空)
 f2) 尾部转 fail×2(≥阈值,direction 翻转为 up,同 slug)→ 实录:减压 diff 是被清空
     还是被改写为压制 diff?proposal.md 标题是否与 diff 内容错位?
"""
import json
import os
import sys
import time

sys.path.insert(0, "/tmp/evo_attack")
from common import Sandbox, attempt

NOW = time.time()
NOW_TS = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW))
def ts(ago_h):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW - ago_h * 3600))

def fresh_down_draft():
    sb = Sandbox()
    db = sb.ra.load_db()
    db["strategy_adjustments"] = {"A:atomic_ejection": {
        "penalty": 0.15, "reason": "连败2次,演化层压制", "ts": NOW_TS}}
    db["history"] = [attempt("A", "atomic_ejection", "s1", "success", ts(2)),
                     attempt("A", "atomic_ejection", "s2", "success", ts(1))]
    sb.write_db(db)
    s = sb.observe.observe(days=7, min_occurrences=3)
    assert len(s["drafted"]) == 1, s["drafted"]
    pdir = s["drafted"][0]
    diff = open(os.path.join(pdir, "diff.patch")).read()
    assert diff, "减压草稿 diff 应非空"
    return sb, pdir, diff

results = []

# ---- f1 尾部 fail×1 → 作废 ----
sb, pdir, old_diff = fresh_down_draft()
sb.ra.locked_update(lambda d: sb.ra.record_attempt(d, "A", "atomic_ejection", "x1"))
sb.ra.locked_update(lambda d: sb.ra.settle_pending(d, "fail", case_id="x1"))
s2 = sb.observe.observe(days=7, min_occurrences=3)
patch = os.path.join(pdir, "diff.patch")
ok_f1 = not s2["drafted"] and os.path.getsize(patch) == 0
print("f1) 连胜减压草稿+尾部fail×1: 横扫作废(diff清空)=%s, 新起草=%d → %s"
      % (os.path.getsize(patch) == 0, len(s2["drafted"]), "PASS" if ok_f1 else "FAIL"))
results.append(("f1 减压草稿对称作废", ok_f1))

# ---- f2 尾部 fail×2 → 实录翻转行为 ----
sb2, pdir2, old_diff2 = fresh_down_draft()
proposal_md = open(os.path.join(pdir2, "proposal.md")).read()
sb2.ra.locked_update(lambda d: sb2.ra.record_attempt(d, "A", "atomic_ejection", "x1"))
sb2.ra.locked_update(lambda d: sb2.ra.settle_pending(d, "fail", case_id="x1"))
sb2.ra.locked_update(lambda d: sb2.ra.record_attempt(d, "A", "atomic_ejection", "x2"))
sb2.ra.locked_update(lambda d: sb2.ra.settle_pending(d, "fail", case_id="x2"))
s3 = sb2.observe.observe(days=7, min_occurrences=3)
patch2 = os.path.join(pdir2, "diff.patch")
new_diff = open(patch2).read()
is_up = ('-      "penalty": 0.15,' in new_diff and '+      "penalty": 0.3,' in new_diff)
still_down = new_diff == old_diff2
title_stale = "连胜" in proposal_md and "减压" in proposal_md and is_up
print("f2) 连胜减压草稿+尾部fail×2: diff被改写为压制(0.15→0.3)=%s, 原减压diff残留=%s"
      % (is_up, still_down))
print("    实录: proposal.md 标题仍='连胜…减压' 而 diff 已是压制 → 标题/diff错位=%s" % title_stale)
# 语义判定:减压 diff 未残留(被替换或被清空都算"作废")即通过;错位另报
ok_f2 = not still_down and os.path.getsize(patch2) > 0 and is_up
results.append(("f2 减压diff未残留(翻转为压制diff)", ok_f2))

print("\n== 八轮-f 总结 ==")
for name, ok in results:
    print("%s: %s" % (name, "通过" if ok else "失败"))
if title_stale:
    print("附注[低]: 方向翻转刷新只改 diff.patch,proposal.md/evidence.md 标题与证据滞留旧方向")
sys.exit(0 if all(ok for _, ok in results) else 1)
