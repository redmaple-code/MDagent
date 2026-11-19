# -*- coding: utf-8 -*-
"""s13 —— 审计完整性: 每次被拒都入账?审计写失败(disk full)会不会 fail-open?

步骤:
  A. 三种拒绝(白名单/无批准/过期批准)各跑一次,审计必须各落一条 refused;
  B. _audit 猴补丁抛 OSError("disk full"):
     B1 白名单拒绝路径 —— 提交本就不发生,异常抛出(拒绝成立但调用方看到崩溃);
     B2 人工闸拒绝路径 —— 同上;
     B3 已批准提交路径 —— 提交在审计之前(scnet_client.py:494-497),审计写崩时
        作业已提交但调用方收到异常: "提交成功但无审计+调用方以为失败" = 审计缺口。
预期: A 全入账=通过;B 中只有"审计失败导致实际放行/重复提交"才算打穿,
  B3 的"已提交却报失败"记为发现(低,审计完整性与调用方语义)。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atk_common import *  # noqa

fake = bootstrap()
f1 = make_file("production.in", "run 100\n")
CMD = "mpirun -np 32 lmp_mpi -in production.in"

# ---- A: 三种拒绝各入账
with no_tty():
    scnet_client.submit_job("s13a", [f1], "bigmem", 1, 32, "12:00:00")       # 白名单拒
    scnet_client.submit_job("s13b", [f1], "debug", 1, 32, "12:00:00")        # 无批准拒
ph = scnet_client.plan_hash("s13c", "debug", 1, 32, "12:00:00", CMD, [f1])
write_approval_file(ph, {"plan_hash": ph, "approved_epoch": time.time() - 9999})
with no_tty():
    scnet_client.submit_job("s13c", [f1], "debug", 1, 32, "12:00:00")        # 过期拒
acts = [e["action"] for e in audit_entries()]
ok_a = acts.count("refused") == 3
print("A 三种拒绝审计: %r" % acts)
verdict("PASS" if ok_a else "FINDING", "s13a_refusals_audited",
        "3次拒绝入账=%r" % acts)

# ---- B: 审计写崩
orig_audit = scnet_client._audit


def boom(entry):
    raise OSError("No space left on device (模拟)")


scnet_client._audit = boom

# B1 白名单拒绝
try:
    with no_tty():
        r = scnet_client.submit_job("s13d", [f1], "bigmem", 1, 32, "12:00:00")
    b1 = "返回refused=%r" % bool(r.get("refused"))
except OSError:
    b1 = "OSError上抛"
b1_jobs = len(fake.submit_calls())
print("B1 审计崩+白名单拒: %s job=%d" % (b1, b1_jobs))
verdict("PASS" if b1_jobs == 0 else "FINDING", "s13b1_auditfail_gate",
        "审计写崩时白名单拒绝仍零提交(job=%d),调用方看到: %s" % (b1_jobs, b1))

# B3 已批准提交,审计在提交后崩
reset_state()
fake.calls.clear()
scnet_client._audit = orig_audit  # 先正常写批准不走路径
ph2 = scnet_client.plan_hash("s13e", "debug", 1, 32, "12:00:00", CMD, [f1])
write_approval_file(ph2, {"plan_hash": ph2, "approved_epoch": time.time()})
scnet_client._audit = boom
try:
    with no_tty():
        r3 = scnet_client.submit_job("s13e", [f1], "debug", 1, 32, "12:00:00")
    b3 = "正常返回 %r" % r3
except OSError:
    b3 = "OSError上抛(调用方以为失败)"
b3_jobs = len(fake.submit_calls())
print("B3 审计崩+已批准提交: %s, 实际job提交=%d" % (b3, b3_jobs))
verdict("FINDING" if b3_jobs else "PASS", "s13b3_auditfail_after_submit",
        "审计在提交之后: 写崩时作业已提交(job=%d)但调用方收到异常,且无审计记录"
        % b3_jobs)
sys.exit(0 if ok_a else 1)
