# -*- coding: utf-8 -*-
"""九轮新攻击 b:_evict_key / _recompute_rates 的 1h 未来容差滥用。
 b1) history 塞 200 条 now+1..59min 的"容差内近未来"合法条目 → record_attempt
     记真实新账:排序后新账 ts 最小排最前 → 被驱逐的是真实新账?
 b2) 20 条 now+30min 的未来 fail 账进后验窗(t <= now+1h)与 observe 连败窗
     (ts <= now+3600):success_rate 是否被未来账压低 + 幻影连败是否起压制提案?
     两侧口径是否一致(都收)?
"""
import sys
import time

sys.path.insert(0, "/tmp/evo_attack")
from common import Sandbox, attempt

NOW = time.time()
def ts_abs(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(epoch))
def ts(ago_h):
    return ts_abs(NOW - ago_h * 3600)

results = []

# ---------- b1 容差内未来条目挤走真实新账 ----------
sb = Sandbox()
ra = sb.ra
db = ra.load_db()
# 200 条容差内未来账(17s 步进:+17s..+3400s,全部 <1h 容差),形状完全合法
db["history"] = [attempt("B", "thermal_runaway", "future-%03d" % i, "success",
                         ts_abs(NOW + (i + 1) * 17)) for i in range(200)]
sb.write_db(db)
crashed = False
try:
    ra.locked_update(lambda d: ra.record_attempt(d, "A", "atomic_ejection", "real-now"))
except Exception as e:
    crashed = True
    print("    穿透: %r" % e)
hist = sb.read_db()["history"]
real_kept = any(isinstance(h, dict) and h.get("case_id") == "real-now" for h in hist)
future_kept = sum(1 for h in hist if isinstance(h, dict)
                  and str(h.get("case_id", "")).startswith("future-"))
ok_b1 = real_kept  # 期望真实新账存活
print("b1) 200条容差内未来账+记真实新账: 不崩=%s, 真实新账存活=%s, 未来账留存=%d/200 → %s"
      % (not crashed, real_kept, future_kept, "PASS" if ok_b1 else "FAIL[打穿]"))
if not real_kept:
    print("    [打穿] 容差内未来条目 _evict_key 返回自身 ts(>now)排最后受保护,"
          "真实新账 ts=now 排最前被 history[-200:] 切掉——攻击者用 1h 内未来 ts"
          "灌满 200 槽位即可让 kit 永远记不上新账(学习失声)")
results.append(("b1 容差内未来账不逐真实新账", ok_b1))

# ---------- b2 未来 fail 账压低后验 + 幻影连败 ----------
sb2 = Sandbox()
ra2 = sb2.ra
db2 = ra2.load_db()
db2["history"] = [attempt("A", "atomic_ejection", "ff-%02d" % i, "fail",
                          ts_abs(NOW + 1800)) for i in range(20)]  # now+30min
sb2.write_db(db2)
ra2.locked_update(lambda d: ra2.record_attempt(d, "A", "atomic_ejection", "real-1"))
ra2.locked_update(lambda d: ra2.settle_pending(d, "success", case_id="real-1"))
rate = sb2.read_db()["strategies"]["A"]["success_rate"]
# 后验窗按 ts 取近 20:真实 success(ts=now)排在 20 条未来 fail 之前 → 被挤出窗
EXP = round((0 + 1) / (20 + 2), 4)   # 若未来 fail 全进窗且真实账被挤出:1/22
s2 = sb2.observe.observe(days=7, min_occurrences=3)
drafted = len(s2["drafted"]) == 1
pen_proposed = drafted  # 幻影连败(尾部 20 条未来 fail)起压制提案
posterior_poisoned = abs(rate - EXP) < 1e-9
ok_b2 = not (posterior_poisoned and pen_proposed)  # 期望未来账不进任何窗
print("b2) 20条now+30min未来fail+1真实success: 后验=%s(被压低至1/22=%s),"
      " observe 幻影连败起压制=%s → %s" % (rate, posterior_poisoned, pen_proposed,
                                           "PASS" if ok_b2 else "FAIL[打穿]"))
if not ok_b2:
    print("    [打穿] 容差内未来 fail 账同时进 kit 后验窗与 observe 连败窗(两侧口径一致"
          "都收 ts<=now+3600):success_rate 被压到 %s 且演化层按幻影连败起草压制;"
          "真实 success 因 ts 较小被挤出近20窗" % rate)
results.append(("b2 容差内未来fail不污染后验/连败", ok_b2))

print("\n== 九轮-b 总结 ==")
for name, ok in results:
    print("%s: %s" % (name, "通过" if ok else "失败"))
sys.exit(0 if all(ok for _, ok in results) else 1)
