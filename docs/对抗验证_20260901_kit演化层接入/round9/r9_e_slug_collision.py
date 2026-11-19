# -*- coding: utf-8 -*-
"""九轮新攻击 e(自由发挥):slug 分隔符冲突 —— _slug_safe 白名单含 "-" 与 "_",
而 slug 用 "%s-%s" 拼接 sid/cause:
    (sid="A", cause="B-C")  与  (sid="A-B", cause="C")
  → 同一 slug "strategy-weight-A-B-C",同 proposal 目录!
 e1) 两对同向连败:一轮 observe 后目录只有一份,pair1 的 diff 被 pair2 刷新覆盖
     → pair1 压制永久失声(每轮都被 pair2 抢写)?
 e2) apply 幸存草稿:只落 pair2 的 adjustment,pair1 连败永不落地?
 e3) 交叉方向(pair1 连败 up / pair2 连胜 down 且 pair2 有 penalty):
     标题+diff 是 pair2 的减压,evidence.md 滞留 pair1 的连败证据 → 审计错配?
"""
import os
import sys
import time

sys.path.insert(0, "/tmp/evo_attack")
from common import Sandbox, attempt

NOW = time.time()
NOW_TS = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW))
def ts(ago_h):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW - ago_h * 3600))

results = []
S1, C1 = "A", "B-C"     # pair1
S2, C2 = "A-B", "C"     # pair2 —— 与 pair1 同 slug

def db_with(hist, adj=None):
    sb = Sandbox()
    db = sb.ra.load_db()
    db["strategies"]["A-B"] = {"name": "x", "desc": "x", "for": [],
                               "success_rate": 0.5, "uses": 0}
    if adj:
        db["strategy_adjustments"] = adj
    db["history"] = hist
    sb.write_db(db)
    return sb

# 前置:确认两对 slug 真的相同
sb0 = Sandbox()
slug1 = "strategy-weight-%s-%s" % (sb0.observe._slug_safe(S1), sb0.observe._slug_safe(C1))
slug2 = "strategy-weight-%s-%s" % (sb0.observe._slug_safe(S2), sb0.observe._slug_safe(C2))
print("前置) slug(%s,%s)=%s ; slug(%s,%s)=%s ; 相同=%s" % (S1, C1, slug1, S2, C2, slug2, slug1 == slug2))
assert slug1 == slug2

# ---------- e1 同向连败:先写者被覆盖失声 ----------
sb = db_with([attempt(S1, C1, "p1-1", "fail", ts(3)),
              attempt(S1, C1, "p1-2", "fail", ts(2)),
              attempt(S2, C2, "p2-1", "fail", ts(3)),
              attempt(S2, C2, "p2-2", "fail", ts(2))])
s = sb.observe.observe(days=7, min_occurrences=3)
one_dir = len(s["drafted"]) == 1
diff = open(os.path.join(s["drafted"][0], "diff.patch")).read()
has_p1 = '"%s:%s"' % (S1, C1) in diff
has_p2 = '"%s:%s"' % (S2, C2) in diff
# 再跑两轮:pair1 是否永远抢不回来(pairs 升序,pair2 恒在后刷新覆盖)
for _ in range(2):
    sN = sb.observe.observe(days=7, min_occurrences=3)
diffN = open(os.path.join(s["drafted"][0], "diff.patch")).read()
starved = ('"%s:%s"' % (S1, C1)) not in diffN
ok_e1 = one_dir and has_p2 and not has_p1 and starved
print("e1) 同slug两对连败: 目录仅1份=%s, diff含pair1(A:B-C)=%s, 含pair2(A-B:C)=%s,"
      " 连跑3轮pair1仍失声=%s → %s" % (one_dir, has_p1, has_p2, starved,
                                        "PASS" if not ok_e1 else "FAIL[打穿]"))
if ok_e1:
    print("    [打穿] pair2(sorted 在后)每轮经同日刷新通道覆盖 diff.patch+标题,"
          "pair1 连败压制永不落盘——_slug_safe 白名单含分隔符'-'/'_'致编码歧义")
results.append(("e1 同slug冲突不导致pair失声(打穿实录)", not ok_e1))

# ---------- e2 apply 幸存草稿:只有 pair2 生效 ----------
rc = sb.run_apply([s["drafted"][0]])
adj = sb.read_db().get("strategy_adjustments", {})
p1_pen = adj.get("A:B-C", {}).get("penalty")
p2_pen = adj.get("A-B:C", {}).get("penalty")
ok_e2 = not (rc == 0 and p2_pen and not p1_pen)
print("e2) apply 幸存草稿: rc=%d, pair1 pen=%s, pair2 pen=%s(pair1 压制落空) → %s"
      % (rc, p1_pen, p2_pen, "PASS" if ok_e2 else "FAIL[打穿]"))
results.append(("e2 冲突下apply不漏pair1", ok_e2))

# ---------- e3 交叉方向:标题/diff=pair2,evidence=pair1 ----------
sb3 = db_with([attempt(S1, C1, "p1-1", "fail", ts(3)),
               attempt(S1, C1, "p1-2", "fail", ts(2)),
               attempt(S2, C2, "p2-1", "success", ts(3)),
               attempt(S2, C2, "p2-2", "success", ts(2))],
              adj={"A-B:C": {"penalty": 0.15, "reason": "连败2次,演化层压制", "ts": NOW_TS}})
s3 = sb3.observe.observe(days=7, min_occurrences=3)
d3 = s3["drafted"][0]
diff3 = open(os.path.join(d3, "diff.patch")).read()
title3 = open(os.path.join(d3, "proposal.md")).readline()
evid3 = open(os.path.join(d3, "evidence.md")).read()
diff_is_down_p2 = '"penalty": 0.15' in diff3 and "- " in diff3  # 摘除 A-B:C 条目
title_is_p2 = S2 in title3
evid_is_p1 = ("策略%s" % S1 in evid3) and ("根因=%s" % C1 in evid3)
mismatch = diff_is_down_p2 and title_is_p2 and evid_is_p1
ok_e3 = not mismatch
print("e3) 交叉方向: diff=pair2减压(摘0.15条目)=%s, 标题=pair2=%s, evidence=pair1连败=%s"
      " → 审计错配=%s → %s" % (diff_is_down_p2, title_is_p2, evid_is_p1, mismatch,
                               "PASS" if ok_e3 else "FAIL[打穿]"))
if mismatch:
    print("    [打穿] 目录由 pair1 起草(evidence=pair1 连败),pair2 同日刷新覆写"
          " diff+标题为减压——evidence.md 永不刷新,审计看到的证据与 diff 语义相反")
results.append(("e3 冲突下evidence/diff不错配", ok_e3))

print("\n== 九轮-e 总结 ==")
for name, ok in results:
    print("%s: %s" % (name, "通过" if ok else "失败"))
sys.exit(0 if all(ok for _, ok in results) else 1)
