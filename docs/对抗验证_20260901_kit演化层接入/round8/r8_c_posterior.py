# -*- coding: utf-8 -*-
"""八轮复攻 c:nf_beta_window f2 场景修复实证 —— 乱序亚帽后验应按 ts 口径。
 c0) 复跑七轮 nf_beta_window.py 全量
 c1) f2 同场景直测:文件序乱(5条老success挪文件尾)+ts不动,亚帽 settle 触发重算
     → 后验必须 == ts 口径 0.2273(修复前为文件序 0.4091)
 c2) 垃圾条目混入(settled 过滤 isinstance)+乱序 → 后验仍按 ts 口径不崩
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

r = subprocess.run([sys.executable, "/tmp/evo_closing/nf_beta_window.py"],
                   capture_output=True, text=True, timeout=600)
print("c0) 复跑 nf_beta_window 输出:")
for ln in r.stdout.splitlines():
    if ln.startswith(("f1", "f2", "f3")):
        print("   " + ln)
ok_c0 = "按ts近20(0.2273)=True" in r.stdout
print("    f2 已按 ts 口径=%s → %s" % (ok_c0, "PASS" if ok_c0 else "FAIL"))
results.append(("c0 nf f2复跑按ts口径", ok_c0))

def make_25():
    hist = []
    for i in range(5):
        hist.append(attempt("A", "atomic_ejection", "old-s%d" % i, "success", ts(30 - i)))
    for i in range(20):
        oc = "success" if i % 4 == 0 else "fail"
        hist.append(attempt("A", "atomic_ejection", "new-%02d" % i, oc, ts(25 - i)))
    return hist

# c1 f2 直测
sb = Sandbox()
ra = sb.ra
db = ra.load_db()
h = make_25()
db["history"] = h[5:] + h[:5]  # 文件序乱,ts 不动
sb.write_db(db)
ra.locked_update(lambda d: ra.record_attempt(d, "A", "atomic_ejection", "trig"))
ra.locked_update(lambda d: ra.settle_pending(d, "fail", case_id="trig"))
rate = sb.read_db()["strategies"]["A"]["success_rate"]
EXP_TS = round((4 + 1) / 22, 4)      # ts 近20:new-01..19(4胜)+trig fail
EXP_FILE = round((8 + 1) / 22, 4)    # 文件序后20(修复前口径)
ok_c1 = abs(rate - EXP_TS) < 1e-9
print("c1) 乱序亚帽后验=%s: ts口径(%s)=%s, 文件序口径(%s)=%s → %s"
      % (rate, EXP_TS, abs(rate - EXP_TS) < 1e-9, EXP_FILE, abs(rate - EXP_FILE) < 1e-9,
         "PASS" if ok_c1 else "FAIL"))
results.append(("c1 乱序亚帽后验按ts近20", ok_c1))

# c2 垃圾+乱序
sb2 = Sandbox()
ra2 = sb2.ra
db2 = ra2.load_db()
h2 = make_25()
db2["history"] = ["garbage", 7] + h2[5:] + h2[:5]
sb2.write_db(db2)
crashed = False
try:
    ra2.locked_update(lambda d: ra2.record_attempt(d, "A", "atomic_ejection", "trig2"))
    ra2.locked_update(lambda d: ra2.settle_pending(d, "fail", case_id="trig2"))
except (AttributeError, TypeError) as e:
    crashed = True
    print("   穿透: %r" % e)
rate2 = sb2.read_db()["strategies"]["A"]["success_rate"]
ok_c2 = not crashed and abs(rate2 - EXP_TS) < 1e-9
print("c2) 垃圾混入+乱序: 不崩=%s, 后验=%s(ts口径%s) → %s"
      % (not crashed, rate2, EXP_TS, "PASS" if ok_c2 else "FAIL"))
results.append(("c2 垃圾混入后验仍ts口径不崩", ok_c2))

print("\n== 八轮-c 总结 ==")
for name, ok in results:
    print("%s: %s" % (name, "通过" if ok else "失败"))
sys.exit(0 if all(ok for _, ok in results) else 1)
