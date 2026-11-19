# -*- coding: utf-8 -*-
"""八轮新攻击 e:清空后 apply 端行为实证(独立链路)。
 e1) 真实草稿被横扫清空 → apply:不崩、REJECTED、reason 含"为空"、LEDGER 留痕、db 字节不变
 e2) 手工空 diff(0 字节,四件齐)→ apply:同上
 e3) 同一空 diff 重复 apply:每次 REJECTED 各留一条(幂等不崩,无部分写入)
"""
import json
import os
import sys
import time

sys.path.insert(0, "/tmp/evo_attack")
from common import Sandbox, attempt

NOW_TS = time.strftime("%Y-%m-%dT%H:%M:%S")
TODAY = time.strftime("%Y-%m-%d")
results = []

# e1 真实草稿清空后 apply
sb = Sandbox()
ra = sb.ra
db = ra.load_db()
db["history"] = [attempt("A", "atomic_ejection", "f1", "fail", NOW_TS),
                 attempt("A", "atomic_ejection", "f2", "fail", NOW_TS)]
sb.write_db(db)
s1 = sb.observe.observe(days=7, min_occurrences=3)
pdir = s1["drafted"][0]
ra.locked_update(lambda d: ra.record_attempt(d, "A", "atomic_ejection", "c3"))
ra.locked_update(lambda d: ra.settle_pending(d, "success", case_id="c3"))
sb.observe.observe(days=7, min_occurrences=3)  # 横扫清空
patch = os.path.join(pdir, "diff.patch")
assert os.path.getsize(patch) == 0, "前置:diff 应已被清空"
before = open(sb.db_path).read()
rc = sb.run_apply([pdir])
rows = sb.ledger_rows()
row = rows[-1] if rows else {}
ok_e1 = (rc == 2 and row.get("result") == "REJECTED"
         and "为空" in str(row.get("reason")) and open(sb.db_path).read() == before)
print("e1) 清空后apply: rc=%d result=%s reason=%s LEDGER行数=%d db不变=%s → %s"
      % (rc, row.get("result"), row.get("reason"), len(rows),
         open(sb.db_path).read() == before, "PASS" if ok_e1 else "FAIL"))
results.append(("e1 清空后apply REJECTED留痕不写db", ok_e1))

# e2 手工空 diff
pdir2 = os.path.join(sb.observe.PROPOSALS, "%s-strategy-weight-M-n" % TODAY)
os.makedirs(pdir2)
open(os.path.join(pdir2, "diff.patch"), "w").close()
open(os.path.join(pdir2, "proposal.md"), "w").write("# x\n")
open(os.path.join(pdir2, "evidence.md"), "w").write("# x\n")
json.dump({"root": "kit", "class": "strategy_weight_tuning", "tier": "auto"},
          open(os.path.join(pdir2, "meta.json"), "w"))
rc2 = sb.run_apply([pdir2])
row2 = sb.ledger_rows()[-1]
ok_e2 = (rc2 == 2 and row2.get("result") == "REJECTED"
         and "为空" in str(row2.get("reason")) and open(sb.db_path).read() == before)
print("e2) 手工空diff: rc=%d result=%s reason=%s db不变=%s → %s"
      % (rc2, row2.get("result"), row2.get("reason"),
         open(sb.db_path).read() == before, "PASS" if ok_e2 else "FAIL"))
results.append(("e2 手工空diff同样REJECTED", ok_e2))

# e3 重复 apply 幂等
n_before = len(sb.ledger_rows())
rc3 = sb.run_apply([pdir2])
rc4 = sb.run_apply([pdir2])
rows3 = sb.ledger_rows()
ok_e3 = (rc3 == 2 and rc4 == 2 and len(rows3) == n_before + 2
         and all(r.get("result") == "REJECTED" for r in rows3[-2:])
         and open(sb.db_path).read() == before)
print("e3) 重复apply×2: rc=%d,%d LEDGER新增2条REJECTED=%s db不变=%s → %s"
      % (rc3, rc4, all(r.get("result") == "REJECTED" for r in rows3[-2:]),
         open(sb.db_path).read() == before, "PASS" if ok_e3 else "FAIL"))
results.append(("e3 重复apply幂等REJECTED", ok_e3))

print("\n== 八轮-e 总结 ==")
for name, ok in results:
    print("%s: %s" % (name, "通过" if ok else "失败"))
sys.exit(0 if all(ok for _, ok in results) else 1)
