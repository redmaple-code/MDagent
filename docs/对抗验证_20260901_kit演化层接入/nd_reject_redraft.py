# -*- coding: utf-8 -*-
"""收官新攻击 d:复核拒绝轮次的旧草稿处置。
 d1) 连败2起草(旧 diff 落盘) → kit 同期结出尾部 success(文件级并发,猴补丁在 diff 落笔前写盘)
     → _strategy_db_diff 复核拒绝(返回 None) → 该轮不得误刷新/误删旧草稿(内容+mtime 不变)
 d2) 旧 diff 走 apply 通道:
     d2a) db 仅远处变动(history 尾 append + A 率变,插入锚点上下文 3 行内未变)→ 实录是否被应用
     d2b) db 插入锚点上下文过期(末位策略 F 的 success_rate 变)→ 必须 REJECTED 且 db 不变
"""
import json
import os
import sys
import time

sys.path.insert(0, "/tmp/evo_attack")
from common import Sandbox, attempt

NOW_TS = time.strftime("%Y-%m-%dT%H:%M:%S")
results = []

# ---- 前置:连败2起草 ----
sb = Sandbox()
ra, observe = sb.ra, sb.observe
db = ra.load_db()
db["history"] = [attempt("A", "atomic_ejection", "f1", "fail", NOW_TS),
                 attempt("A", "atomic_ejection", "f2", "fail", NOW_TS)]
sb.write_db(db)
s1 = observe.observe(days=7, min_occurrences=3)
assert len(s1["drafted"]) == 1, s1["drafted"]
pdir = s1["drafted"][0]
diff_path = os.path.join(pdir, "diff.patch")
old_diff = open(diff_path).read()
old_mtime = os.path.getmtime(diff_path)
print("前置) 旧草稿: %s, diff %d 字节" % (os.path.basename(pdir), len(old_diff)))

# ---- d1: 复核拒绝轮次 → 旧草稿不动 ----
def writer_turn_success(sb):
    """模拟 kit 在 observe 初读与 diff 落笔之间结出尾部 success。"""
    ra.locked_update(lambda d: ra.record_attempt(d, "A", "atomic_ejection", "c3"))
    ra.locked_update(lambda d: ra.settle_pending(d, "success", case_id="c3"))

orig = observe._strategy_db_diff
state = {"fired": False}
def wrapper(db_path, sid, cause, direction, streak):
    if not state["fired"]:
        state["fired"] = True
        writer_turn_success(sb)
    return orig(db_path, sid, cause, direction, streak)
observe._strategy_db_diff = wrapper
try:
    s2 = observe.observe(days=7, min_occurrences=3)
finally:
    observe._strategy_db_diff = orig
time.sleep(0.01)
now_diff = open(diff_path).read()
now_mtime = os.path.getmtime(diff_path)
# 复核拒绝后,盘上是 f,f,s → 尾部 success×1,observe 侧检测亦不起草
ok_d1 = (not s2["drafted"]) and now_diff == old_diff and now_mtime == old_mtime
print("d1) 复核拒绝轮: drafted=%d, 旧diff内容不变=%s, mtime不变=%s → %s"
      % (len(s2["drafted"]), now_diff == old_diff, now_mtime == old_mtime,
         "PASS" if ok_d1 else "FAIL"))
results.append(("d1 复核拒绝不误刷新/误删旧草稿", ok_d1))

# ---- d2a: 旧 diff 走 apply,db 仅远处变动(c3 已入账 → 证据已反转) ----
db_now = sb.read_db()
tail = [h["case_id"] for h in db_now["history"][-2:]]
rc = sb.run_apply([pdir])
rows = sb.ledger_rows()
last = rows[-1] if rows else {}
pen = sb.read_db().get("strategy_adjustments", {}).get("A:atomic_ejection", {}).get("penalty")
applied_d2a = rc == 0 and last.get("result") in ("APPLIED", "AUTO_APPLIED")
print("d2a) 证据已反转(尾部=%s)的旧diff: apply rc=%d result=%s penalty=%s → %s"
      % (tail, rc, last.get("result"), pen,
         "被应用!(上下文未恰变,无证据复核)" if applied_d2a else "REJECTED( fail-closed )"))

# ---- d2b: 上下文防线覆盖面实测(锚点3行窗:上文=F的uses行,下文=history首条strategy行) ----
def fresh_draft():
    sx = Sandbox()
    d = sx.ra.load_db()
    d["history"] = [attempt("A", "atomic_ejection", "f1", "fail", NOW_TS),
                    attempt("A", "atomic_ejection", "f2", "fail", NOW_TS)]
    sx.write_db(d)
    return sx, sx.observe.observe(days=7, min_occurrences=3)["drafted"][0]

# d2b1) 改 F.success_rate(锚点窗外 1 行):实录是否 REJECTED
sb2, pdir2 = fresh_draft()
d = sb2.read_db(); d["strategies"]["F"]["success_rate"] = 0.31; sb2.write_db(d)
before = open(sb2.db_path).read()
rc = sb2.run_apply([pdir2])
res = (sb2.ledger_rows()[-1] or {}).get("result")
slipped = rc == 0 and res in ("APPLIED", "AUTO_APPLIED")
print("d2b1) 窗外变动(F.success_rate): rc=%d result=%s → %s"
      % (rc, res, "被应用(防线未覆盖)" if slipped else "REJECTED"))

# d2b2) 改 F.uses(恰在锚点上文3行内,kit 用 F 修复时的真实动作):必须 REJECTED 且 db 不变
sb3, pdir3 = fresh_draft()
d = sb3.read_db(); d["strategies"]["F"]["uses"] = 1; sb3.write_db(d)
before = open(sb3.db_path).read()
rc = sb3.run_apply([pdir3])
row = sb3.ledger_rows()[-1] or {}
after = open(sb3.db_path).read()
ok_d2b2 = rc != 0 and row.get("result") == "REJECTED" and after == before
print("d2b2) 窗内变动(F.uses): rc=%d result=%s reason=%s, db不变=%s → %s"
      % (rc, row.get("result"), row.get("reason"), after == before,
         "PASS" if ok_d2b2 else "FAIL"))
results.append(("d2b2 窗内上下文过期被REJECTED且db不变", ok_d2b2))

print("\n== 收官-d 总结 ==")
for name, ok in results:
    print("%s: %s" % (name, "通过" if ok else "失败"))
print("d2a 为语义实录项,见总结")
