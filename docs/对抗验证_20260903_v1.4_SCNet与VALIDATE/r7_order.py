# -*- coding: utf-8 -*-
"""r7 —— 复攻 F9(人工闸位于 authenticate 之后,拒绝仍外发)。

修复后: 无批准无 TTY 的拒绝在 authenticate 之前(零外发),拒绝全入账。
  A. 无批准无 TTY → refused,断言 _http 调用 == 0;
  B. 过期批准无 TTY → refused,零外发;
  C. 批准被并发取走路径 → refused,零外发(手工 pre-take);
  D. dry_run(设计允许无批准) → 有只读外发但零 job 提交(设计实录,ACCEPT);
  E. 离线环境(fake 全炸)下无批准拒绝仍是干净 refused 而非异常(F9 原次生问题)。
退出码 0 = 拒绝路径零外发成立。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atk_common import *  # noqa

fake = bootstrap()
f1 = make_file("production.in", "run 100\n")
CMD = "mpirun -np 32 lmp_mpi -in production.in"
bad = []

# A 无批准无 TTY
with no_tty():
    r = scnet_client.submit_job("r7a", [f1], "debug", 1, 32, "12:00:00")
a = (not r["submitted"]) and len(fake.calls) == 0
print("A 无批准无TTY: refused=%r 外发=%d" % (str(r.get("refused"))[:40],
                                            len(fake.calls)))
verdict("PASS" if a else "FINDING", "r7a_no_approval_zero_egress",
        "外发=%d(期望0)" % len(fake.calls))
if not a:
    bad.append("A")

# B 过期批准
reset_state()
fake.calls.clear()
write_eff_approval("r7b", "debug", 1, 32, "12:00:00", CMD, [f1], age=1860)
with no_tty():
    r = scnet_client.submit_job("r7b", [f1], "debug", 1, 32, "12:00:00")
b = (not r["submitted"]) and len(fake.calls) == 0
print("B 过期批准: refused=%r 外发=%d" % (str(r.get("refused"))[:40],
                                         len(fake.calls)))
verdict("PASS" if b else "FINDING", "r7b_expired_zero_egress",
        "外发=%d(期望0)" % len(fake.calls))
if not b:
    bad.append("B")

# C 批准被并发取走(手工 pre-take 模拟)
reset_state()
fake.calls.clear()
ph = write_eff_approval("r7c", "debug", 1, 32, "12:00:00", CMD, [f1])
taken = scnet_client._take_approval(ph)  # 模拟另一并发者先取走
assert taken
with no_tty():
    r = scnet_client.submit_job("r7c", [f1], "debug", 1, 32, "12:00:00")
c = (not r["submitted"]) and len(fake.calls) == 0
acts = [e["action"] for e in audit_entries()]
print("C 批准已被取走: refused=%r 外发=%d 审计=%r"
      % (str(r.get("refused"))[:40], len(fake.calls), acts))
verdict("PASS" if c and acts == ["refused"] else "FINDING",
        "r7c_pretaken_zero_egress", "外发=%d 审计=%r" % (len(fake.calls), acts))
if not (c and acts == ["refused"]):
    bad.append("C")
os.remove(taken)

# D dry_run 设计实录
reset_state()
fake.calls.clear()
with no_tty():
    dr = scnet_client.submit_job("r7d", [f1], "debug", 1, 32, "12:00:00",
                                 dry_run=True)
d = dr.get("dry_run") and not fake.submit_calls()
print("D dry_run: dry=%r 只读外发=%d job=%d"
      % (dr.get("dry_run"), len(fake.calls), len(fake.submit_calls())))
verdict("ACCEPT" if d else "FINDING", "r7d_dry_run_egress",
        "dry_run 零批准可跑(设计): 只读外发(认证/发现)=%d,job提交=0;审计 dry_run 入账"
        % len(fake.calls))
if not d:
    bad.append("D")

# E 离线(fake 全炸)下无批准拒绝仍干净
reset_state()
boom = install_http(FakeHTTP(fail_on={"": scnet_client.ScnetError(
    "network", "模拟离线", "x")}))
try:
    with no_tty():
        r = scnet_client.submit_job("r7e", [f1], "debug", 1, 32, "12:00:00")
    e_ok = not r["submitted"] and "refused" in r
    detail = "干净 refused=%r" % str(r.get("refused"))[:40]
except Exception as ex:
    e_ok, detail = False, "裸抛 %s: %s" % (type(ex).__name__, str(ex)[:60])
print("E 离线无批准: %s" % detail)
verdict("PASS" if e_ok else "FINDING", "r7e_offline_clean_refusal", detail)
if not e_ok:
    bad.append("E")

print("R7 总不良项: %r" % bad)
sys.exit(1 if bad else 0)
