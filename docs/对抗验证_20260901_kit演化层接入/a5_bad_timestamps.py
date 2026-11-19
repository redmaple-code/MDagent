# -*- coding: utf-8 -*-
"""攻击5:时间戳畸形 —— ts ∈ {"9999-99-99", "2026-13-45T99:99", "", int, None, 缺键}
打 observe 与 settle 两条路径,验证不崩、不误判;并实测 settle 侧 TypeError 是否炸整批。"""
import json
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import Sandbox, attempt

NOW_TS = time.strftime("%Y-%m-%dT%H:%M:%S")

# ---------- 面1: observe 不崩、畸形 ts 不计入连败 ----------
sb = Sandbox()
observe = sb.observe
db = sb.ra.load_db()
bad_variants = ["9999-99-99", "2026-13-45T99:99", "", 12345, None]
hist = []
for i, bad in enumerate(bad_variants):
    h = attempt("A", "atomic_ejection", "bad%d" % i, "fail", NOW_TS)
    h["ts"] = bad  # 直接塞畸形值(含 int/None)
    hist.append(h)
h_nokey = attempt("A", "atomic_ejection", "bad-nokey", "fail", NOW_TS)
del h_nokey["ts"]
hist.append(h_nokey)
db["history"] = hist
sb.write_db(db)
try:
    s = sb.observe.observe(days=7, min_occurrences=3)
    drafted = len(s["drafted"])
    recov = [p for p in s["patterns"] if p["kind"] == "recovery-outcome"]
    print("面1 observe: 未崩, drafted=%d (期望0:6条畸形fail绝不得凑出连败), settled计数=%s (期望6:入账不过滤ts)"
          % (drafted, recov[0]["count"] if recov else None))
    face1_ok = drafted == 0
except Exception:
    traceback.print_exc()
    face1_ok = False

# ---------- 面2: settle 对畸形 ts 的行为(复刻 orchestrator._settle 口径) ----------
def orch_settle(ra, case_id, cause=None):
    """复刻 orchestrator RESEARCH/FAILED 入口结账:同因7天fail + 年轻6h fail + 老6h success。"""
    def _settle(d):
        if cause:
            ra.settle_pending(d, "fail", case_id=case_id, root_cause=cause,
                              younger_than_days=7)
        ra.settle_pending(d, "fail", case_id=case_id, younger_than_hours=6)
        ra.settle_pending(d, "success", case_id=case_id, older_than_hours=6)
    ra.locked_update(_settle)

# 面2a: int ts 的 pending 是否会炸掉整批结账
sb2 = Sandbox()
ra2 = sb2.ra
db2 = ra2.load_db()
good = attempt("A", "atomic_ejection", "caseX", "pending", NOW_TS)
bad = attempt("A", "atomic_ejection", "caseX", "pending", NOW_TS)
bad["ts"] = 12345  # int 混入
db2["history"] = [dict(good, case_id="caseX"), bad]
sb2.write_db(db2)
settle_crashed = False
try:
    orch_settle(ra2, "caseX", cause="atomic_ejection")
except TypeError as e:
    settle_crashed = True
    print("面2a settle: TypeError 穿透 settle_pending → locked_update → 整批结账中止: %s" % e)
outcomes = [h["outcome"] for h in sb2.read_db()["history"]]
print("面2a 结账后 outcomes=%s → 同 case 的健康 pending %s"
      % (outcomes, "被连坐留 pending(整批未结)" if settle_crashed and "pending" in outcomes else "正常"))

# 面2b: 字符串畸形 ts(可过 str() 的)在 settle 里的行为
sb3 = Sandbox()
ra3 = sb3.ra
db3 = ra3.load_db()
hist3 = []
for i, badts in enumerate(["9999-99-99", "2026-13-45T99:99", ""]):
    hist3.append(attempt("A", "atomic_ejection", "caseY", "pending", NOW_TS))
    hist3[-1]["ts"] = badts
hist3.append(attempt("A", "atomic_ejection", "caseY", "pending", NOW_TS))  # 健康对照
db3["history"] = hist3
sb3.write_db(db3)
try:
    orch_settle(ra3, "caseY", cause="atomic_ejection")
    oc = [h["outcome"] for h in sb3.read_db()["history"]]
    print("面2b 字符串畸形ts: settle 未崩, outcomes=%s → 畸形条目永留 pending(僵尸),健康条目已结"
          % oc)
    face2b_ok = oc[-1] == "fail" and oc[:3] == ["pending"] * 3
except Exception:
    traceback.print_exc()
    face2b_ok = False

# 面2c: DONE 路径(全 success)会把僵尸 pending 记成 success(无年龄过滤 → 不解析 ts)
sb4 = Sandbox()
ra4 = sb4.ra
db4 = ra4.load_db()
z = attempt("A", "atomic_ejection", "caseZ", "pending", NOW_TS)
z["ts"] = ""
db4["history"] = [z]
sb4.write_db(db4)
ra4.locked_update(lambda d: ra4.settle_pending(d, "success", case_id="caseZ"))
oc4 = sb4.read_db()["history"][0]["outcome"]
print("面2c DONE 路径: 空ts僵尸 pending → %s (失败路径永不结它,DONE 白捡 success)" % oc4)

print("攻击5 结果: observe 面=%s; settle 面: int ts %s, 字符串畸形 %s, DONE 僵尸=%s"
      % ("通过(不崩不误判)" if face1_ok else "失败",
         "TypeError 炸整批结账" if settle_crashed else "未炸",
         "通过(留僵尸不崩)" if face2b_ok else "异常",
         "白捡 success" if oc4 == "success" else oc4))
