# -*- coding: utf-8 -*-
"""s3 —— 过期批准文件(approved_epoch 改 31 分钟前)必须拒。

攻击假设: 攻击者拿到一个旧批准文件(或改自己伪造文件的 ts 为 31 分钟前)。
预期: refused、零作业提交、审计 action=refused。打穿=提交成功。
边界附加: 恰好 1800s(<=APPROVAL_MAX_AGE 放行)与 1801s(拒)各打一枪钉边界。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atk_common import *  # noqa

fake = bootstrap()
f1 = make_file("production.in", "run 100\n")
CMD = "mpirun -np 32 lmp_mpi -in production.in"

for tag, age, expect_ok in [("s3a_expired_31min", 1860, False),
                            ("s3b_edge_1799s", 1799, True),
                            ("s3c_edge_1801s", 1801, False)]:
    reset_state()
    fake.calls.clear()
    ph = scnet_client.plan_hash("s3case", "debug", 1, 32, "12:00:00", CMD, [f1])
    write_approval_file(ph, {"plan_hash": ph,
                             "approved_epoch": time.time() - age})
    with no_tty():
        r = scnet_client.submit_job("s3case", [f1], "debug", 1, 32, "12:00:00")
    jobs = len(fake.submit_calls())
    print("%s: age=%ds submitted=%r job=%d(期望%s)"
          % (tag, age, r["submitted"], jobs, "放行" if expect_ok else "拒"))
    verdict("PASS" if r["submitted"] == expect_ok else "FINDING", tag,
            "age=%ds submitted=%r(期望%r)" % (age, r["submitted"], expect_ok))

# 过期拒绝的审计
acts = [e["action"] for e in audit_entries()]
print("末次审计: %r" % acts[-3:])
sys.exit(0)
