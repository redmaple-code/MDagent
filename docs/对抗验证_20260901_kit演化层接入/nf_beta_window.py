# -*- coding: utf-8 -*-
"""收官新攻击 f:Beta 后验窗口口径(record_attempt ts 排序后 settled[-20:])。
 构造 25 条已结账,ts 有序递增;最老 5 条全 success,最新 20 条 = 5 胜 15 败:
   ts 近20条口径 wins=5 → rate=(5+1)/22=0.2727
   若按文件序后20条且文件序乱 → wins 不同 → rate 不同(错位)
 f1) 正常路径(文件序==ts序):settle 触发 _recompute_rates → rate 必须 == 0.2727
 f2) 对抗:文件序打乱(把 5 条老 success 挪到文件尾)但 ts 不动 → 实录后验按哪个窗口算
 f3) 超帽路径:210 条逐条 record_attempt(每次触发排序驱逐)→ 后验窗口按 ts 近20 算
"""
import random
import sys
import time

sys.path.insert(0, "/tmp/evo_attack")
from common import Sandbox, attempt

NOW = time.time()
def ts(ago_h):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW - ago_h * 3600))

results = []
EXPECT = round((5 + 1) / (20 + 2), 4)  # 0.2727

def make_25():
    """25 条 A 已结账:最老5条 success(30h..26h前), 最新20条 5胜15败(25h..6h前)。"""
    hist = []
    for i in range(5):
        hist.append(attempt("A", "atomic_ejection", "old-s%d" % i, "success", ts(30 - i)))
    for i in range(20):
        oc = "success" if i % 4 == 0 else "fail"  # 5 胜 15 败
        hist.append(attempt("A", "atomic_ejection", "new-%02d" % i, oc, ts(25 - i)))
    return hist

# ---- f1 正常路径 ----
sb = Sandbox()
ra = sb.ra
db = ra.load_db()
db["history"] = make_25()
sb.write_db(db)
ra.locked_update(lambda d: ra.record_attempt(d, "A", "atomic_ejection", "trig"))
ra.locked_update(lambda d: ra.settle_pending(d, "fail", case_id="trig"))
# trig fail 入账后窗口滑动:近20 = 原25条的第6..25 + trig? 不——trig 进来共26条,settled[-20:]=
# ts 第7..26条 = new-01..new-19(5胜15败中去掉 new-00 success=4胜) + trig fail → 4胜16败
EXP_F1 = round((4 + 1) / 22, 4)
rate = sb.read_db()["strategies"]["A"]["success_rate"]
ok_f1 = abs(rate - EXP_F1) < 1e-9
print("f1) 正常路径后验=%s (期望%s, ts近20含新结fail) → %s" % (rate, EXP_F1, "PASS" if ok_f1 else "FAIL"))
results.append(("f1 正常路径后验按ts近20", ok_f1))

# ---- f2 文件序打乱对抗 ----
sb2 = Sandbox()
ra2 = sb2.ra
db2 = ra2.load_db()
h = make_25()
# 把最老 5 条 success 挪到文件尾(文件序 ≠ ts 序;ts 不动)
shuffled = h[5:] + h[:5]
db2["history"] = shuffled
sb2.write_db(db2)
ra2.locked_update(lambda d: ra2.settle_pending(d, "success", case_id="none-match"))  # 无匹配,n=0 不重算
# 直接记一笔再结账触发 _recompute_rates
ra2.locked_update(lambda d: ra2.record_attempt(d, "A", "atomic_ejection", "trig2"))
ra2.locked_update(lambda d: ra2.settle_pending(d, "fail", case_id="trig2"))
db2r = sb2.read_db()
rate2 = db2r["strategies"]["A"]["success_rate"]
# 文件序后20 = shuffled 后19(含5条老success) + trig2? record_attempt append 在文件尾,
# 26条 settled[-20:] = 文件第7..26 = new-07..new-19(3胜) + 老success×5 + trig2(fail) = 8胜12败
EXP_F2_FILEORDER = round((8 + 1) / 22, 4)
by_ts = abs(rate2 - EXP_F1) < 1e-9
by_file = abs(rate2 - EXP_F2_FILEORDER) < 1e-9
print("f2) 文件序打乱后验=%s: 按ts近20(%s)=%s, 按文件序后20(%s)=%s"
      % (rate2, EXP_F1, by_ts, EXP_F2_FILEORDER, by_file))
results.append(("f2 乱序文件后验口径(实录)", by_ts))  # by_ts=True 才算与 observe 同口径

# ---- f3 超帽路径:210 条逐条入账 ----
sb3 = Sandbox()
ra3 = sb3.ra
db3 = ra3.load_db()
db3["history"] = []
sb3.write_db(db3)
# 先灌 190 条老 success(30h.. 前,逐条 record+settle 太慢——直接预写 190,再逐条 record 20 条触发排序)
pre = [attempt("A", "atomic_ejection", "pre-%03d" % i, "success", ts(60 - i * 0.1)) for i in range(190)]
db3["history"] = pre
sb3.write_db(db3)
# 逐条 record+settle 20 条 fail(每条触发 201→排序→逐最老)
for i in range(20):
    cid = "over-%02d" % i
    ra3.locked_update(lambda d, c=cid: ra3.record_attempt(d, "A", "atomic_ejection", c))
    ra3.locked_update(lambda d, c=cid: ra3.settle_pending(d, "fail", case_id=c))
db3r = sb3.read_db()
h3 = db3r["history"]
sorted_ok = all(str(h3[i].get("ts", "")) <= str(h3[i + 1].get("ts", "")) for i in range(len(h3) - 1))
rate3 = db3r["strategies"]["A"]["success_rate"]
EXP_F3 = round((0 + 1) / 22, 4)  # 近20全 fail
ok_f3 = (len(h3) == 200 and sorted_ok and abs(rate3 - EXP_F3) < 1e-9
         and all(h["case_id"].startswith("over-") for h in h3[-20:]))
print("f3) 超帽路径: len=%d, ts有序=%s, 尾部20条全是新fail=%s, 后验=%s(期望%s) → %s"
      % (len(h3), sorted_ok, all(h["case_id"].startswith("over-") for h in h3[-20:]),
         rate3, EXP_F3, "PASS" if ok_f3 else "FAIL"))
results.append(("f3 超帽排序后后验窗口正确", ok_f3))

print("\n== 收官-f 总结 ==")
for name, ok in results:
    print("%s: %s" % (name, "通过" if ok else "失败/实录"))
