# -*- coding: utf-8 -*-
"""八轮新攻击 d:收尾横扫误伤面。
 d1) 同日手工草稿 strategy-weight-X-y(db 无 (X,y) 记录)→ observe 后 diff.patch 应被清空
 d2) 同日前缀不匹配草稿 monitor-schedule-x → 不应被动
 d3) 昨日 strategy-weight-Z-z(日期前缀非今日)→ 不应被动
 d4) 整对蒸发:真草稿 (A,cause) 在,db 重写为无该对 → 应被清空
 d5) 空 history db + X-y 草稿(pairs 为空,循环不跑)→ 横扫仍应清空
 d6) 证据成立的同日草稿绝不被误清(对照)
"""
import json
import os
import sys
import time

sys.path.insert(0, "/tmp/evo_attack")
from common import Sandbox, attempt

NOW_TS = time.strftime("%Y-%m-%dT%H:%M:%S")
TODAY = time.strftime("%Y-%m-%d")
YEST = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
results = []

def handcraft(sb, dirname, diff_bytes=b"--- a/.v3.3_strategy_db.json\n+++ b/.v3.3_strategy_db.json\n"):
    pdir = os.path.join(sb.observe.PROPOSALS, dirname)
    os.makedirs(pdir, exist_ok=True)
    open(os.path.join(pdir, "diff.patch"), "wb").write(diff_bytes)
    open(os.path.join(pdir, "proposal.md"), "w").write("# x\n")
    open(os.path.join(pdir, "evidence.md"), "w").write("# x\n")
    json.dump({"root": "kit", "class": "strategy_weight_tuning", "tier": "auto"},
              open(os.path.join(pdir, "meta.json"), "w"))
    return os.path.join(pdir, "diff.patch")

# ---- d1/d2/d3/d6 同沙盒 ----
sb = Sandbox()
db = sb.ra.load_db()
db["history"] = [attempt("A", "atomic_ejection", "f1", "fail", NOW_TS),
                 attempt("A", "atomic_ejection", "f2", "fail", NOW_TS)]
sb.write_db(db)
p_xy = handcraft(sb, "%s-strategy-weight-X-y" % TODAY)
p_ms = handcraft(sb, "%s-monitor-schedule-x" % TODAY)
p_z = handcraft(sb, "%s-strategy-weight-Z-z" % YEST)
s = sb.observe.observe(days=7, min_occurrences=3)
real_ok = len(s["drafted"]) == 1
real_patch = os.path.join(s["drafted"][0], "diff.patch") if s["drafted"] else None

ok_d1 = os.path.getsize(p_xy) == 0
print("d1) 无记录对 X-y 草稿: diff被清空=%s → %s" % (ok_d1, "PASS" if ok_d1 else "FAIL"))
results.append(("d1 无记录对草稿被横扫清空", ok_d1))

ok_d2 = os.path.getsize(p_ms) > 0
print("d2) 前缀不匹配 monitor-schedule-x: 未被动=%s → %s" % (ok_d2, "PASS" if ok_d2 else "FAIL"))
results.append(("d2 非strategy-weight前缀不动", ok_d2))

ok_d3 = os.path.getsize(p_z) > 0
print("d3) 昨日 strategy-weight-Z-z: 未被动=%s → %s" % (ok_d3, "PASS" if ok_d3 else "FAIL"))
results.append(("d3 非今日草稿不动", ok_d3))

ok_d6 = real_ok and os.path.getsize(real_patch) > 0
print("d6) 证据成立的(A,cause)草稿: 仍在且diff非空=%s → %s" % (ok_d6, "PASS" if ok_d6 else "FAIL"))
results.append(("d6 证据成立草稿不被误清", ok_d6))

# ---- d4 整对蒸发 ----
sb4 = Sandbox()
db4 = sb4.ra.load_db()
db4["history"] = [attempt("A", "atomic_ejection", "f1", "fail", NOW_TS),
                  attempt("A", "atomic_ejection", "f2", "fail", NOW_TS)]
sb4.write_db(db4)
s4 = sb4.observe.observe(days=7, min_occurrences=3)
assert len(s4["drafted"]) == 1
patch4 = os.path.join(s4["drafted"][0], "diff.patch")
# 整对蒸发:db 重写,只剩其他对
db4 = sb4.read_db()
db4["history"] = [attempt("D", "extreme_temp", "q1", "success", NOW_TS)]
sb4.write_db(db4)
sb4.observe.observe(days=7, min_occurrences=3)
ok_d4 = os.path.getsize(patch4) == 0
print("d4) 整对蒸发后: 旧草稿diff被清空=%s → %s" % (ok_d4, "PASS" if ok_d4 else "FAIL"))
results.append(("d4 整对蒸发草稿被清空", ok_d4))

# ---- d5 空 history ----
sb5 = Sandbox()
db5 = sb5.ra.load_db()
db5["history"] = []
sb5.write_db(db5)
p5 = handcraft(sb5, "%s-strategy-weight-X-y" % TODAY)
sb5.observe.observe(days=7, min_occurrences=3)
ok_d5 = os.path.getsize(p5) == 0
print("d5) 空history(pairs为空): X-y 草稿diff被清空=%s → %s" % (ok_d5, "PASS" if ok_d5 else "FAIL"))
results.append(("d5 空history横扫仍生效", ok_d5))

print("\n== 八轮-d 总结 ==")
for name, ok in results:
    print("%s: %s" % (name, "通过" if ok else "失败"))
sys.exit(0 if all(ok for _, ok in results) else 1)
