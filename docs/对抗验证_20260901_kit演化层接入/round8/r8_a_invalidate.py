# -*- coding: utf-8 -*-
"""八轮复攻 a:nd_reject_redraft 场景修复实证。
 a1) 连败2起草 → kit 结账尾部转 success → 下轮 observe:旧 diff.patch 应被清空(收尾横扫)
 a2) 空 diff 走 apply:应 REJECTED("diff.patch 为空")入 LEDGER,db 不变
 a3) 证据恢复(再连败2)→ 下轮 observe:同日草稿刷新回填新 diff → apply 可应用
"""
import json
import os
import sys
import time

sys.path.insert(0, "/tmp/evo_attack")
from common import Sandbox, attempt

NOW_TS = time.strftime("%Y-%m-%dT%H:%M:%S")
results = []

sb = Sandbox()
ra, observe = sb.ra, sb.observe
db = ra.load_db()
db["history"] = [attempt("A", "atomic_ejection", "f1", "fail", NOW_TS),
                 attempt("A", "atomic_ejection", "c2", "fail", NOW_TS)]
sb.write_db(db)
s1 = observe.observe(days=7, min_occurrences=3)
assert len(s1["drafted"]) == 1
pdir = s1["drafted"][0]
patch = os.path.join(pdir, "diff.patch")
assert os.path.getsize(patch) > 0
print("前置) 起草: %s, diff %d 字节" % (os.path.basename(pdir), os.path.getsize(patch)))

# a1 证据反转 → 横扫清空
ra.locked_update(lambda d: ra.record_attempt(d, "A", "atomic_ejection", "c3"))
ra.locked_update(lambda d: ra.settle_pending(d, "success", case_id="c3"))
s2 = observe.observe(days=7, min_occurrences=3)
empty = os.path.getsize(patch) == 0
ok_a1 = not s2["drafted"] and empty
print("a1) 尾部转success后再observe: drafted=%d, 旧diff被清空=%s → %s"
      % (len(s2["drafted"]), empty, "PASS" if ok_a1 else "FAIL"))
results.append(("a1 证据反转后旧diff被清空", ok_a1))

# a2 空 diff apply
before = open(sb.db_path).read()
rc = sb.run_apply([pdir])
row = sb.ledger_rows()[-1] if sb.ledger_rows() else {}
ok_a2 = (rc == 2 and row.get("result") == "REJECTED"
         and "为空" in str(row.get("reason")) and open(sb.db_path).read() == before)
print("a2) 空diff apply: rc=%d result=%s reason=%s, db不变=%s → %s"
      % (rc, row.get("result"), row.get("reason"), open(sb.db_path).read() == before,
         "PASS" if ok_a2 else "FAIL"))
results.append(("a2 空diff被REJECTED入LEDGER不写db", ok_a2))

# a3 证据恢复 → 刷新回填
ra.locked_update(lambda d: ra.record_attempt(d, "A", "atomic_ejection", "c4"))
ra.locked_update(lambda d: ra.settle_pending(d, "fail", case_id="c4"))
ra.locked_update(lambda d: ra.record_attempt(d, "A", "atomic_ejection", "c5"))
ra.locked_update(lambda d: ra.settle_pending(d, "fail", case_id="c5"))
s3 = observe.observe(days=7, min_occurrences=3)
refilled = os.path.getsize(patch) > 0
rc2 = sb.run_apply([pdir])
pen = sb.read_db().get("strategy_adjustments", {}).get("A:atomic_ejection", {}).get("penalty")
ok_a3 = refilled and rc2 == 0 and pen == 0.15
print("a3) 再连败2后observe: diff回填=%s(%d字节), apply rc=%d penalty=%s → %s"
      % (refilled, os.path.getsize(patch), rc2, pen, "PASS" if ok_a3 else "FAIL"))
results.append(("a3 证据恢复后刷新回填可应用", ok_a3))

print("\n== 八轮-a 总结 ==")
for name, ok in results:
    print("%s: %s" % (name, "通过" if ok else "失败"))
sys.exit(0 if all(ok for _, ok in results) else 1)
