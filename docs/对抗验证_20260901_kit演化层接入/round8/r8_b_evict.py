# -*- coding: utf-8 -*-
"""八轮复攻 b:nc_evict_settle 复跑 + 审计 AttributeError 复现。
 b0) 复跑七轮 nc_evict_settle.py(子进程)
 b1) history=["garbage"]+200 合法 → record_attempt 不崩(AttributeError 修复),garbage 被逐
 b2) 混合垃圾 5 种(非dict str/int、ts=None、ts=int、未来 ts、畸形串):连记 5 笔全部逐出
 b3) 亚帽下 settle_pending 遇垃圾条目:isinstance 防护不崩、垃圾不结账
"""
import subprocess
import sys
import time

sys.path.insert(0, "/tmp/evo_attack")
from common import Sandbox, attempt

NOW = time.time()
def ts(ago_h):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW - ago_h * 3600))

results = []

# b0 复跑七轮脚本
r = subprocess.run([sys.executable, "/tmp/evo_closing/nc_evict_settle.py"],
                   capture_output=True, text=True, timeout=300)
ok_b0 = r.returncode == 0
print("b0) 复跑 nc_evict_settle: rc=%d → %s" % (r.returncode, "PASS" if ok_b0 else "FAIL"))
if not ok_b0:
    print(r.stdout[-800:], r.stderr[-800:])
results.append(("b0 nc_evict_settle复跑", ok_b0))

# b1 "garbage" + 200 合法
sb = Sandbox()
ra = sb.ra
db = ra.load_db()
db["history"] = ["garbage"] + [attempt("B", "thermal_runaway", "s%d" % i,
                                       "success", ts(300 - i)) for i in range(200)]
sb.write_db(db)
try:
    ra.locked_update(lambda d: ra.record_attempt(d, "A", "atomic_ejection", "new-1"))
    crashed = False
except AttributeError as e:
    crashed = True
    print("   AttributeError 穿透: %s" % e)
h = sb.read_db()["history"]
garbage_gone = all(isinstance(x, dict) for x in h)
ok_b1 = (not crashed and len(h) == 200 and garbage_gone
         and any(x.get("case_id") == "new-1" for x in h))
print("b1) ['garbage']+200合法: 不崩=%s, len=%d, garbage被逐=%s, 新账留=%s → %s"
      % (not crashed, len(h), garbage_gone,
         any(x.get("case_id") == "new-1" for x in h), "PASS" if ok_b1 else "FAIL"))
results.append(("b1 非dict垃圾不崩且被逐", ok_b1))

# b2 五种垃圾混合,连记5笔
sb2 = Sandbox()
ra2 = sb2.ra
db2 = ra2.load_db()
garbage = ["garbage-str", 12345,
           {"strategy": "A", "root_cause": "x", "case_id": "g-none", "ts": None, "outcome": "pending"},
           {"strategy": "A", "root_cause": "x", "case_id": "g-int", "ts": 999, "outcome": "pending"},
           {"strategy": "A", "root_cause": "x", "case_id": "g-future", "ts": "2099-01-01T00:00:00", "outcome": "fail"},
           {"strategy": "A", "root_cause": "x", "case_id": "g-bad", "ts": "not-a-ts", "outcome": "pending"}]
db2["history"] = garbage + [attempt("B", "x", "v%d" % i, "success", ts(300 - i))
                            for i in range(194)]
sb2.write_db(db2)  # 200 条含 6 垃圾
crashed2 = False
try:
    for i in range(6):
        ra2.locked_update(lambda d, i=i: ra2.record_attempt(d, "A", "x", "nb-%d" % i))
except (AttributeError, TypeError) as e:
    crashed2 = True
    print("   穿透: %r" % e)
h2 = sb2.read_db()["history"]
left_garbage = [x for x in h2 if not isinstance(x, dict)
                or (isinstance(x, dict) and str(x.get("case_id", "")).startswith("g-"))]
ok_b2 = not crashed2 and len(h2) == 200 and not left_garbage
print("b2) 6种垃圾(非dict×2/ts None/int/未来/畸形)+194合法,记6笔: 不崩=%s, len=%d, 垃圾全逐=%s → %s"
      % (not crashed2, len(h2), not left_garbage, "PASS" if ok_b2 else "FAIL"))
if left_garbage:
    print("   残留垃圾: %r" % left_garbage[:3])
results.append(("b2 六种垃圾全部最先被逐", ok_b2))

# b3 亚帽 settle 遇垃圾
sb3 = Sandbox()
ra3 = sb3.ra
db3 = ra3.load_db()
db3["history"] = ["garbage", 42,
                  attempt("A", "atomic_ejection", "p1", "pending", ts(1)),
                  {"ts": None, "outcome": "pending", "case_id": "p2", "strategy": "A",
                   "root_cause": "atomic_ejection"}]
sb3.write_db(db3)
try:
    n = []
    ra3.locked_update(lambda d: n.append(ra3.settle_pending(d, "fail", case_id="p1")))
    crashed3 = False
except (AttributeError, TypeError) as e:
    crashed3 = True
    n = []
    print("   穿透: %r" % e)
h3 = sb3.read_db()["history"]
p2_zombie = any(isinstance(x, dict) and x.get("case_id") == "p2" and x.get("outcome") == "pending"
                for x in h3)
ok_b3 = not crashed3 and n == [1] and p2_zombie
print("b3) 亚帽settle遇垃圾: 不崩=%s, 结账数=%s(期望[1]), ts=None僵尸不结不崩=%s → %s"
      % (not crashed3, n, p2_zombie, "PASS" if ok_b3 else "FAIL"))
results.append(("b3 settle对垃圾isinstance防护", ok_b3))

print("\n== 八轮-b 总结 ==")
for name, ok in results:
    print("%s: %s" % (name, "通过" if ok else "失败"))
sys.exit(0 if all(ok for _, ok in results) else 1)
