# -*- coding: utf-8 -*-
"""s12 —— 并发 approve+submit 竞态: 两线程同时消费同一批准文件。

攻击假设: 一次性语义依赖"先 _valid_approval 检查、提交成功后才
  _consume_approval"(scnet_client.py:451,494)——检查与销毁之间隔着整个
  上传+提交窗口,两个并发 submit 都能先看到批准文件存在。
步骤: 合法批准一份;barrier 卡在 upload_file 里,确保两线程都通过批准检查
  后才放行上传;统计 /job 提交次数。
预期: 一次性承诺 = 恰好 1 次提交;==2 = 打穿(批准双花)。
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atk_common import *  # noqa

fake = bootstrap()
f1 = make_file("production.in", "run 100\n")
CMD = "mpirun -np 32 lmp_mpi -in production.in"
ph = scnet_client.plan_hash("s12case", "debug", 1, 32, "12:00:00", CMD, [f1])
write_approval_file(ph, {"plan_hash": ph, "approved_epoch": time.time()})

barrier = threading.Barrier(2)
orig_upload = scnet_client.upload_file


def slow_upload(*a, **kw):
    barrier.wait(timeout=10)  # 两线程都过了批准检查才一起放行
    return orig_upload(*a, **kw)


scnet_client.upload_file = slow_upload

results = []
errors = []


def worker():
    try:
        r = scnet_client.submit_job("s12case", [f1], "debug", 1, 32,
                                    "12:00:00")
        results.append(r["submitted"])
    except Exception as e:
        errors.append(repr(e))
# stdin/input 在主线程全局 patch 后起跑两线程
import builtins  # noqa: E402
import unittest.mock as mock  # noqa: E402
with mock.patch.object(sys, "stdin", NoTTY()), \
        mock.patch.object(builtins, "input",
                          lambda prompt="": (_ for _ in ()).throw(AssertionError)):
    ts = [threading.Thread(target=worker) for _ in range(2)]
    [t.start() for t in ts]
    [t.join(30) for t in ts]

njobs = len(fake.submit_calls())
nsucc = sum(1 for x in results if x)
print("两线程消费同一批准: 成功数=%d job提交数=%d 错误=%r"
      % (nsucc, njobs, errors))
verdict("FINDING" if njobs > 1 else "PASS", "s12_race_double_spend",
        "一次性批准被并发消费: job提交=%d(期望1) 成功返回=%d" % (njobs, nsucc))
sys.exit(1 if njobs > 1 else 0)
