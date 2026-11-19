# -*- coding: utf-8 -*-
"""收官新攻击 b:_recent_outcomes 同秒多条目与乱序 ts。
 b1) 同一秒按序写入 success,success,fail,fail(文件序即时间序)→ 尾部必须 fail×2,能起草
 b1c) 对照:同秒 fail,fail,success,success → 尾部 success×2,不得起草压制(且无压制可减→不起草)
 b2) 文件序打乱但 ts 不同:文件序 fail(最新),success(最老),fail(次新),success(次老)
     → 必须按 ts 排出 success,success,fail,fail → 尾部 fail×2 起草
 b2c) 反向:文件序 success(最新),fail(最老),success(次新),fail(次老)
     → ts 序 fail,fail,success,success → 尾部 success → 不起草
"""
import os
import sys
import time

sys.path.insert(0, "/tmp/evo_attack")
from common import Sandbox, attempt

NOW = time.time()
SAME_SEC = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW))
def ts(ago_h):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW - ago_h * 3600))

results = []

def run_case(hist):
    sb = Sandbox()
    db = sb.ra.load_db()
    db["history"] = hist
    sb.write_db(db)
    return sb.observe.observe(days=7, min_occurrences=3)

# b1 同秒 success,success,fail,fail
s = run_case([attempt("A", "atomic_ejection", "s1", "success", SAME_SEC),
              attempt("A", "atomic_ejection", "s2", "success", SAME_SEC),
              attempt("A", "atomic_ejection", "f1", "fail", SAME_SEC),
              attempt("A", "atomic_ejection", "f2", "fail", SAME_SEC)])
ok = len(s["drafted"]) == 1
print("b1) 同秒 s,s,f,f → drafted=%d (期望1) → %s" % (len(s["drafted"]), "PASS" if ok else "FAIL"))
results.append(("b1 同秒文件序即时间序 尾部fail×2起草", ok))

# b1c 同秒 fail,fail,success,success
s = run_case([attempt("A", "atomic_ejection", "f1", "fail", SAME_SEC),
              attempt("A", "atomic_ejection", "f2", "fail", SAME_SEC),
              attempt("A", "atomic_ejection", "s1", "success", SAME_SEC),
              attempt("A", "atomic_ejection", "s2", "success", SAME_SEC)])
ok = len(s["drafted"]) == 0
print("b1c) 同秒 f,f,s,s → drafted=%d (期望0) → %s" % (len(s["drafted"]), "PASS" if ok else "FAIL"))
results.append(("b1c 同秒尾部success×2不起草", ok))

# b2 文件序打乱,ts 不同:文件序 [fail(最新,0h), success(最老,3h), fail(次新,1h), success(次老,2h)]
s = run_case([attempt("A", "atomic_ejection", "f-new", "fail", ts(0)),
              attempt("A", "atomic_ejection", "s-old", "success", ts(3)),
              attempt("A", "atomic_ejection", "f-mid", "fail", ts(1)),
              attempt("A", "atomic_ejection", "s-mid", "success", ts(2))])
ok = len(s["drafted"]) == 1
print("b2) 乱序文件,ts序=s,s,f,f → drafted=%d (期望1,按ts排) → %s" % (len(s["drafted"]), "PASS" if ok else "FAIL"))
results.append(("b2 乱序文件按ts排 尾部fail×2起草", ok))

# b2c 反向乱序:文件序 [success(最新), fail(最老), success(次新), fail(次老)] → ts序 f,f,s,s
s = run_case([attempt("A", "atomic_ejection", "s-new", "success", ts(0)),
              attempt("A", "atomic_ejection", "f-old", "fail", ts(3)),
              attempt("A", "atomic_ejection", "s-mid", "success", ts(1)),
              attempt("A", "atomic_ejection", "f-mid", "fail", ts(2))])
ok = len(s["drafted"]) == 0
print("b2c) 乱序文件,ts序=f,f,s,s → drafted=%d (期望0) → %s" % (len(s["drafted"]), "PASS" if ok else "FAIL"))
results.append(("b2c 乱序文件按ts排 尾部success不起草", ok))

print("\n== 收官-b 总结 ==")
for name, ok in results:
    print("%s: %s" % (name, "通过" if ok else "失败"))
sys.exit(0 if all(ok for _, ok in results) else 1)
