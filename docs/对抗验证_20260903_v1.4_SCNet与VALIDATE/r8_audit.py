# -*- coding: utf-8 -*-
"""r8 —— 复攻 F10(审计写崩) + 新:审计降级不阻断。

修复后: _audit 写崩降级 stderr,不上抛。
注意: 原 s13 用猴补丁替换 _audit 整体抛 OSError——那测的是"被换掉的他者",
  不算数;本脚本让真实 _audit 内部写盘失败(events/scnet_submit.jsonl 占位为
  目录,open 必 OSError)。
  B1 白名单拒绝 + 审计崩 → 干净 refused 返回,不裸抛,stderr 有告警;
  B2 无批准拒绝 + 审计崩 → 同上;
  B3 已批准提交 + 审计崩 → 提交照常、调用方拿到 submitted=True(不再误判失败),
     stderr 告警;审计缺失属 E7 同型接受项(磁盘满时无审计可写,不可能兼得)。
退出码 0 = 降级语义成立。
"""
import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atk_common import *  # noqa

fake = bootstrap()
f1 = make_file("production.in", "run 100\n")
CMD = "mpirun -np 32 lmp_mpi -in production.in"
bad = []


def break_audit():
    """让真实 _audit 写盘必败: 把 jsonl 路径占位为目录。"""
    ev = os.path.join(TMP, "events")
    os.makedirs(ev, exist_ok=True)
    p = os.path.join(ev, "scnet_submit.jsonl")
    if not os.path.isdir(p):
        os.makedirs(p, exist_ok=True)


# B1 白名单拒绝 + 审计崩
break_audit()
err = io.StringIO()
try:
    with contextlib.redirect_stderr(err), no_tty():
        r = scnet_client.submit_job("r8a", [f1], "bigmem", 1, 32, "12:00:00")
    b1 = (not r["submitted"]) and "refused" in r and not fake.submit_calls()
    detail = "干净 refused"
except Exception as e:
    b1, detail = False, "裸抛 %s" % type(e).__name__
warn1 = "审计写盘失败" in err.getvalue()
print("B1 拒绝+审计崩: %s stderr告警=%r" % (detail, warn1))
verdict("PASS" if b1 and warn1 else "FINDING", "r8a_auditfail_gate",
        "%s stderr告警=%r job=%d" % (detail, warn1, len(fake.submit_calls())))
if not (b1 and warn1):
    bad.append("B1")

# B2 无批准拒绝 + 审计崩
err = io.StringIO()
try:
    with contextlib.redirect_stderr(err), no_tty():
        r = scnet_client.submit_job("r8b", [f1], "debug", 1, 32, "12:00:00")
    b2 = not r["submitted"] and "refused" in r
    detail = "干净 refused"
except Exception as e:
    b2, detail = False, "裸抛 %s" % type(e).__name__
print("B2 无批准+审计崩: %s stderr告警=%r" % (detail, "审计写盘失败" in err.getvalue()))
verdict("PASS" if b2 else "FINDING", "r8b_auditfail_human_gate", detail)
if not b2:
    bad.append("B2")

# B3 已批准提交 + 审计崩
ph = write_eff_approval("r8c", "debug", 1, 32, "12:00:00", CMD, [f1])
fake.calls.clear()
err = io.StringIO()
try:
    with contextlib.redirect_stderr(err), no_tty():
        r3 = scnet_client.submit_job("r8c", [f1], "debug", 1, 32, "12:00:00")
    b3 = r3["submitted"] and len(fake.submit_calls()) == 1
    detail = "submitted=%r 调用方正常拿到结果" % r3["submitted"]
except Exception as e:
    b3, detail = False, "裸抛 %s(调用方误判失败)" % type(e).__name__
warn3 = "审计写盘失败" in err.getvalue()
print("B3 提交+审计崩: %s job=%d stderr告警=%r"
      % (detail, len(fake.submit_calls()), warn3))
verdict("PASS" if b3 and warn3 else "FINDING", "r8c_auditfail_after_submit",
        "%s job=%d stderr告警=%r(审计缺失=E7同型接受项:盘满无审计可写)"
        % (detail, len(fake.submit_calls()), warn3))
if not (b3 and warn3):
    bad.append("B3")

print("R8 总不良项: %r" % bad)
sys.exit(1 if bad else 0)
