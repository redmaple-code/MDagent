# -*- coding: utf-8 -*-
"""s7 —— 重放: 同一批准文件连续 submit 两次,第二次必须拒(用后销毁)。

预期: 第一次 submitted,批准文件消失;第二次 refused(no_human_approval),
  零新增作业提交。打穿 = 第二次仍提交成功(一次性承诺失效)。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atk_common import *  # noqa

fake = bootstrap()
f1 = make_file("production.in", "run 100\n")
CMD = "mpirun -np 32 lmp_mpi -in production.in"
ph = scnet_client.plan_hash("s7case", "debug", 1, 32, "12:00:00", CMD, [f1])
ap = write_approval_file(ph, {"plan_hash": ph, "approved_epoch": time.time()})

with no_tty():
    r1 = scnet_client.submit_job("s7case", [f1], "debug", 1, 32, "12:00:00")
consumed = not os.path.exists(ap)
n1 = len(fake.submit_calls())
with no_tty():
    r2 = scnet_client.submit_job("s7case", [f1], "debug", 1, 32, "12:00:00")
n2 = len(fake.submit_calls()) - n1
print("第一次: submitted=%r 用后销毁=%r; 第二次: submitted=%r refused=%r 新提交=%d"
      % (r1["submitted"], consumed, r2["submitted"], r2.get("refused", "")[:30], n2))
verdict("PASS" if (r1["submitted"] and consumed and not r2["submitted"] and n2 == 0)
        else "FINDING", "s7_replay",
        "首次成功=%r 销毁=%r 重放被拒=%r 重放外发job=%d"
        % (r1["submitted"], consumed, not r2["submitted"], n2))
sys.exit(0 if (r1["submitted"] and consumed and not r2["submitted"] and n2 == 0) else 1)
