# -*- coding: utf-8 -*-
"""收官新攻击 a:slug 截断+哈希尾巴。
 a1) 两个 10KB 策略名仅中间一个字符不同 → 48 字符前缀必然相同,sha256 尾巴必须不同
     → 同日两份提案并存不互踩(drafted==2,两个目录)
 a2) 确定性:同名多次跑 _slug_safe 恒同;同名跨沙盒重跑 slug 相同(同日去重位稳定)
 a3) 恰好 64/65 边界:64 不截断,65 触发截断且总长 61(48+1+12)
"""
import os
import sys

sys.path.insert(0, "/tmp/evo_attack")
from common import Sandbox, attempt

NOW_TS = __import__("time").strftime("%Y-%m-%dT%H:%M:%S")
results = []

# ---- a1: 10KB 名仅中间一字不同 ----
sid1 = "S" * 5000 + "X" + "S" * 4999
sid2 = "S" * 5000 + "Y" + "S" * 4999
assert len(sid1) == len(sid2) == 10000 and sid1 != sid2

sb = Sandbox()
ob = sb.observe
db = sb.ra.load_db()
db["history"] = [
    attempt(sid1, "atomic_ejection", "x1", "fail", NOW_TS),
    attempt(sid1, "atomic_ejection", "x2", "fail", NOW_TS),
    attempt(sid2, "atomic_ejection", "y1", "fail", NOW_TS),
    attempt(sid2, "atomic_ejection", "y2", "fail", NOW_TS),
]
sb.write_db(db)
s = ob.observe(days=7, min_occurrences=3)
# 20 轮口径:L2 specialist 通道对真耗尽根因会追加 install 草稿(已批准的新通道),
# 本用例的互踩断言只针对 strategy-weight 草稿;同时核对 specialist 草稿恰 1 份。
weight = sorted(os.path.basename(p) for p in s["drafted"]
                if "strategy-weight-" in os.path.basename(p))
spec = [p for p in s["drafted"]
        if "specialist-install-" in os.path.basename(p)]
slugs = weight
g1, g2 = ob._slug_safe(sid1), ob._slug_safe(sid2)
prefix_same = g1[:49] == g2[:49]          # 截断前缀(含连接符 '-')相同
hash_diff = g1[-12:] != g2[-12:]          # 哈希尾巴必须不同
two_dirs = len(weight) == 2 and len(set(slugs)) == 2 and len(spec) == 1
ok_a1 = prefix_same and hash_diff and two_dirs
print("a1) drafted=%d, 前缀相同=%s, 哈希尾不同=%s, 两目录不互踩=%s → %s"
      % (len(s["drafted"]), prefix_same, hash_diff, two_dirs, "PASS" if ok_a1 else "FAIL"))
for sl in slugs:
    print("    dir=%s (len=%d)" % (sl, len(sl)))
results.append(("a1 10KB名一字之差→双提案不互踩", ok_a1))

# ---- a2: 确定性 ----
det1 = all(ob._slug_safe(sid1) == g1 for _ in range(50))
sb2 = Sandbox()  # 全新沙盒,同名单跑
db2 = sb2.ra.load_db()
db2["history"] = [attempt(sid1, "atomic_ejection", "z1", "fail", NOW_TS),
                  attempt(sid1, "atomic_ejection", "z2", "fail", NOW_TS)]
sb2.write_db(db2)
s2 = sb2.observe.observe(days=7, min_occurrences=3)
det2 = (len(s2["drafted"]) == 1
        and os.path.basename(s2["drafted"][0]) == "%s-%s" % (
            NOW_TS[:10],
            sb2.observe._pair_slug(sid1, "atomic_ejection")))  # 九轮起 slug 带对哈希尾
ok_a2 = det1 and det2
print("a2) _slug_safe×50 恒同=%s, 跨沙盒同名同 slug=%s → %s" % (det1, det2, "PASS" if ok_a2 else "FAIL"))
results.append(("a2 截断后确定性(同名恒同slug)", ok_a2))

# ---- a3: 64/65 边界 ----
s64 = ob._slug_safe("a" * 64)
s65 = ob._slug_safe("a" * 65)
ok_a3 = s64 == "a" * 64 and len(s65) == 61 and s65.startswith("a" * 48 + "-")
print("a3) 64字不截断=%s, 65字截断为%d字(前缀48+hash12)=%s → %s"
      % (s64 == "a" * 64, len(s65), len(s65) == 61, "PASS" if ok_a3 else "FAIL"))
results.append(("a3 64/65 截断边界", ok_a3))

print("\n== 收官-a 总结 ==")
for name, ok in results:
    print("%s: %s" % (name, "通过" if ok else "失败"))
sys.exit(0 if all(ok for _, ok in results) else 1)
