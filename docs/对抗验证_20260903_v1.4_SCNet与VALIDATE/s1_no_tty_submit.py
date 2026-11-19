# -*- coding: utf-8 -*-
"""s1 —— 无 TTY 直接 submit(管道 stdin) + 拒绝路径外发计数。

攻击假设: agent 以子进程方式跑 submit(stdin 为管道,无 TTY),无批准文件。
步骤:
  A. 白名单拒绝路径(队列越界): 断言 refused 且 _http 调用数 == 0(零外发);
  B. 人工闸拒绝路径(白名单内但无批准无 TTY): 断言 refused、无 /job 提交;
     同时记录拒绝前实际产生了几次 _http 调用(authenticate/center 在人工闸之前)。
预期: 两者都 refused 且无作业提交 = 闸有效;
      若任何一次出现 /apptemplates/.../job 调用 = 打穿。
附察: B 路径若 _http 调用数 > 0,说明"无批准拒绝"仍触网(认证/区域发现),
      属"零外发"承诺的口径缺口,单独记录分级。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atk_common import *  # noqa

fake = bootstrap()
f1 = make_file("production.in", "run 100\n")

# ---- A: 白名单拒绝,期望零外发
with no_tty():
    r = scnet_client.submit_job("s1a", [f1], "bigmem", 1, 32, "12:00:00")
assert r["submitted"] is False and "白名单" in r["refused"], r
n_gate = len(fake.calls)
print("A 白名单拒绝: refused=%r 外发次数=%d" % (r["refused"], n_gate))
verdict("PASS" if n_gate == 0 else "FINDING",
        "s1a_gate_refuse_zero_egress",
        "白名单拒绝路径外发=%d(期望0)" % n_gate)

# ---- B: 无批准无 TTY,期望拒且无 /job
reset_state()
fake.calls.clear()
try:
    with no_tty():
        r = scnet_client.submit_job("s1b", [f1], "debug", 1, 32, "12:00:00")
    submitted = r["submitted"]
    refused = r.get("refused", "")
except Exception as e:
    # 人工闸在 authenticate 之后:离线/认证失败时拒绝变异常,记录
    submitted, refused = False, "EXCEPTION: %r" % e
jobs = fake.submit_calls()
print("B 无批准无TTY: submitted=%r refused=%r 外发次数=%d 提交次数=%d"
      % (submitted, refused, len(fake.calls), len(jobs)))
print("B 外发明细: %s" % [(m, u.split("/openapi/")[-1][:40]) for m, u, _ in fake.calls])
ok = (not submitted) and not jobs
verdict("PASS" if ok else "FINDING", "s1b_no_tty_no_approval_no_submit",
        "无TTY无批准: submitted=%r job提交=%d" % (submitted, len(jobs)))
if len(fake.calls) > 0:
    verdict("FINDING", "s1c_refusal_still_egresses",
            "人工闸位于authenticate/get_center之后: 无批准拒绝仍外发%d次(只读认证,非提交);"
            "离线环境下拒绝路径会变成异常而非干净refused" % len(fake.calls))
# 审计入账检查
acts = [e["action"] for e in audit_entries()]
verdict("PASS" if acts and acts[-1] == "refused" else "FINDING",
        "s1d_refusal_audited", "拒绝后审计actions=%r" % acts)

sys.exit(0 if not jobs and not submitted else 1)
