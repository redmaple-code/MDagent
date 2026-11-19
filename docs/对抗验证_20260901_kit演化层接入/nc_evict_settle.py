# -*- coding: utf-8 -*-
"""收官新攻击 c:record_attempt ts 感知驱逐 + settle_pending 结账语义。
 c1) 200 条 history:190 条已结账(100h~11h 前) + 10 条 pending(p0..p9, ts 散在 50h~41h 前,
     文件位置也散在中间) → record_attempt 第 201 条(ts=now 最新)
     → 排序驱逐后:被逐的必须是最老的已结账,10 条 pending 一条不丢,新账必留
 c2) 驱逐后逐 case settle(交替 success/fail):每次恰好结 1 条、结的是本 case、后验随之重算
 c3) INFO:若最老一条恰是 pending(僵尸),超帽驱逐是否丢 pending(实录语义,不计失败)
"""
import os
import sys
import time

sys.path.insert(0, "/tmp/evo_attack")
from common import Sandbox, attempt

NOW = time.time()
def ts(ago_h):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW - ago_h * 3600))

results = []

# ---- c1/c2 ----
sb = Sandbox()
ra = sb.ra
db = ra.load_db()
hist = []
pend_cases = ["p%d" % i for i in range(10)]
pend_ages = {c: 50 - i for i, c in enumerate(pend_cases)}  # p0=50h ... p9=41h
# 190 条已结账,ts 100h→11h(每条隔约 0.47h 太碎;用 i 直接当小时:第 i 条=100-i? 需 190 条在 100h 内)
# 简化:settled 第 i 条 ts = 100 - i*0.47 不必要,直接 i 从 0..189, ts= (100+ i*0.0)? 用分钟级:
for i in range(190):
    hist.append(attempt("B", "thermal_runaway", "s%d" % i,
                        "success" if i % 2 == 0 else "fail",
                        ts(100 - i * 0.47)))  # 100h → ~10.7h,严格递增
# pending 插在文件中间位置(每 20 条插 1 条)
for i, c in enumerate(pend_cases):
    h = attempt("A", "atomic_ejection", c, "pending", ts(pend_ages[c]))
    hist.insert(20 * (i + 1), h)
assert len(hist) == 200
db["history"] = hist
sb.write_db(db)
oldest_ts = min(str(h["ts"]) for h in hist)

ra.locked_update(lambda d: ra.record_attempt(d, "A", "atomic_ejection", "new-1"))
h2 = sb.read_db()["history"]
pend_after = [h for h in h2 if h.get("outcome") == "pending"]
pend_ids = sorted(h["case_id"] for h in pend_after)
evicted_oldest = all(str(h["ts"]) != oldest_ts or h["case_id"] != "s0" for h in h2)
newest_kept = any(h["case_id"] == "new-1" for h in h2)
sorted_ok = all(str(h2[i].get("ts", "")) <= str(h2[i + 1].get("ts", "")) for i in range(len(h2) - 1))
ok_c1 = (len(h2) == 200 and len(pend_after) == 11  # 10 老 pending + 1 新
         and sorted(c for c in pend_ids if c != "new-1") == pend_cases
         and evicted_oldest and newest_kept and sorted_ok)
print("c1) 超帽驱逐: len=%d, pending=%d(p0..p9全留+新账), 逐的是最老已结账=%s, 新账留=%s, ts有序=%s → %s"
      % (len(h2), len(pend_after), evicted_oldest, newest_kept, sorted_ok,
         "PASS" if ok_c1 else "FAIL"))
results.append(("c1 超帽ts驱逐 pending不丢/新账必留", ok_c1))

# c2 逐 case 结账
settle_ok = True
for i, c in enumerate(pend_cases):
    want = "success" if i % 2 == 0 else "fail"
    cnt = []
    ra.locked_update(lambda d, w=want, cc=c: cnt.append(ra.settle_pending(d, w, case_id=cc)))
    h3 = sb.read_db()["history"]
    mine = [h for h in h3 if h.get("case_id") == c]
    others_pend = [h for h in h3 if h.get("outcome") == "pending"
                   and h["case_id"] in pend_cases and h["case_id"] != c]
    expect_left = len(pend_cases) - (i + 1)
    if not (cnt == [1] and len(mine) == 1 and mine[0]["outcome"] == want
            and len(others_pend) == expect_left):
        settle_ok = False
        print("    case %s 错位: cnt=%s mine=%s 剩余pending=%d(期望%d)"
              % (c, cnt, mine, len(others_pend), expect_left))
rate_a = sb.read_db()["strategies"]["A"]["success_rate"]
wins = sum(1 for i2 in range(10) if i2 % 2 == 0)  # 5 胜
expect_rate = round((wins + 1) / (10 + 2), 4)
rate_ok = abs(rate_a - expect_rate) < 1e-9
print("c2) 10 轮逐case结账: 每次恰1条且不错位=%s, A后验=%s(期望%s, 近20窗内仅这10条)=%s → %s"
      % (settle_ok, rate_a, expect_rate, rate_ok, "PASS" if settle_ok and rate_ok else "FAIL"))
results.append(("c2 驱逐后逐case结账语义不变", settle_ok and rate_ok))

# ---- c3 INFO: 最老是 pending 僵尸 ----
sb3 = Sandbox()
ra3 = sb3.ra
db3 = ra3.load_db()
h3 = [attempt("A", "atomic_ejection", "zombie-p", "pending", ts(500))]  # 最老是 pending
for i in range(199):
    h3.append(attempt("B", "x", "s%d" % i, "success", ts(400 - i)))
db3["history"] = h3
sb3.write_db(db3)
ra3.locked_update(lambda d: ra3.record_attempt(d, "B", "x", "new-z"))
h4 = sb3.read_db()["history"]
zombie_gone = all(h["case_id"] != "zombie-p" for h in h4)
print("c3) INFO: 最老为 pending 僵尸时,超帽驱逐把僵尸一并逐出=%s(语义实录:ts最老者先逐,不问结账态)" % zombie_gone)

print("\n== 收官-c 总结 ==")
for name, ok in results:
    print("%s: %s" % (name, "通过" if ok else "失败"))
sys.exit(0 if all(ok for _, ok in results) else 1)
