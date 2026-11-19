# -*- coding: utf-8 -*-
"""九轮新攻击 d:_recompute_rates(kit)与 _recent_outcomes(observe)过滤边界逐条对齐。
冻结同一时刻 T0:
 d1) ts == T0+3600(恰边界):kit 窗(t <= now+1h)与 observe 窗(ts <= now+3600)都收?
 d2) ts == T0+3601:两侧都拒?
 d3) ts == T0+3599:两侧都收?
 d4) 微秒格式 "....T01:00:00.123"(strptime 失败):两侧都拒?
 d5) _evict_key 边界:ts==now+3600 保留(排尾受保护)? ts==now+3601 键 "" 最先逐?
 d6) 下界口径:ts == T0-14d(observe 恰收)与 kit 无下界(近20窗)差异实录
"""
import sys
import time
from datetime import datetime as RealDatetime, timedelta
from unittest import mock

sys.path.insert(0, "/tmp/evo_attack")
from common import Sandbox, attempt

T0 = int(time.time())  # 冻结秒
T0DT = RealDatetime.fromtimestamp(T0)
def ts_of(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(epoch))

class FakeDatetime(RealDatetime):
    @classmethod
    def now(cls, tz=None):
        return T0DT

results = []

sb = Sandbox()
ra, ob = sb.ra, sb.observe
ra.datetime = FakeDatetime  # 冻结 kit 侧 now(模块级 from-import,直接换属性)

def kit_includes(ts_str, outcome="fail"):
    """该 ts 的已结账条目是否进 kit 后验窗(success_rate 偏离 0.5 即进窗)。"""
    db = {"strategies": {"A": {"name": "x", "desc": "x", "for": [], "success_rate": 0.5, "uses": 0}},
          "history": [attempt("A", "c", "k", outcome, ts_str)]}
    ra._recompute_rates(db)
    return db["strategies"]["A"]["success_rate"] != 0.5

def obs_includes(ts_str, outcome="fail"):
    h = [attempt("A", "c", "k", outcome, ts_str)]
    with mock.patch("time.time", lambda: float(T0)):
        return ob._recent_outcomes(h, "A", "c") == [outcome]

pairs = [
    ("d1 ts==now+3600恰边界", ts_of(T0 + 3600)),
    ("d2 ts==now+3601", ts_of(T0 + 3601)),
    ("d3 ts==now+3599", ts_of(T0 + 3599)),
    ("d4 微秒格式", time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(T0)) + ".123"),
    ("d6 下界ts==now-14d", ts_of(T0 - 14 * 86400)),
]
for name, t in pairs:
    k, o = kit_includes(t), obs_includes(t)
    consistent = (k == o)
    print("%s: kit窗收=%s, observe窗收=%s, 一致=%s → %s"
          % (name, k, o, consistent, "PASS" if consistent else "FAIL[口径不一]"))
    results.append((name + " 两侧一致", consistent))

# d6b 下界之外:observe 14d 连败窗拒;kit 后验无下界(近20条窗语义)——设计差异实录
t = ts_of(T0 - 14 * 86400 - 1)
k, o = kit_includes(t), obs_includes(t)
ok_d6b = (not o) and k  # 唯一合法差异形态:observe 拒且 kit 收
print("d6b 下界外1s: observe窗收=%s(期望False,14d连败窗), kit后验收=%s(期望True,近20窗无下界)"
      " → %s" % (o, k, "PASS" if ok_d6b else "FAIL"))
print("    实录: 下界口径差异系设计(observe=14d连败窗,kit=近20条后验窗),非bug[接受]")
results.append(("d6b 下界差异=已知设计", ok_d6b))

# d5 _evict_key 边界:199 旧账 + 边界条 + 越界条 + 记新账
db = ra.load_db()
hist = [attempt("B", "c", "old-%03d" % i, "success", ts_of(T0 - 1000 - i)) for i in range(199)]
hist.append(attempt("B", "c", "edge-in", "success", ts_of(T0 + 3600)))   # 恰边界
hist.append(attempt("B", "c", "edge-out", "success", ts_of(T0 + 3601)))  # 越界 1s
db["history"] = hist
sb.write_db(db)
ra.locked_update(lambda d: ra.record_attempt(d, "A", "atomic_ejection", "new-real"))
h2 = sb.read_db()["history"]
ids = {x.get("case_id") for x in h2 if isinstance(x, dict)}
edge_in_kept = "edge-in" in ids
edge_out_evicted = "edge-out" not in ids
new_kept = "new-real" in ids
ok_d5 = edge_in_kept and edge_out_evicted and new_kept
print("d5) evict边界: ts==now+3600保留=%s(期望True), ts==now+3601被逐=%s(期望True),"
      " 真实新账留=%s → %s" % (edge_in_kept, edge_out_evicted, new_kept,
                                "PASS" if ok_d5 else "FAIL"))
results.append(("d5 evict边界与窗口同口径", ok_d5))

print("\n== 九轮-d 总结 ==")
for name, ok in results:
    print("%s: %s" % (name, "通过" if ok else "失败"))
sys.exit(0 if all(ok for _, ok in results) else 1)
