# -*- coding: utf-8 -*-
"""r2 —— 复攻 F2(并发双花) + _take_approval 竞态新变体。

复攻 F2: 修复后 _take_approval(rename 到 .taken-uuid)在通过校验后立刻原子取走,
  仅一个并发者成功。用修复后口径的 ph 写合法批准,barrier 卡 upload 逼两线程
  都进入提交窗口。预期: 恰好 1 次 job 提交、1 个成功返回;失败者干净 refused
  (不异常)且审计落一条"被并发取走"。
新变体:
  V1 直接并发 _take_approval(8 线程 barrier 齐发): 恰好 1 个拿到;
  V2 败者重试: 竞争落败者立刻再 submit 同参数 → 无批准可拿,干净拒、零新提交;
  V3 多批准并发: 3 份不同参数批准 + 6 线程(每参数2线程) → 每参数恰好 1 次提交。
退出码 0 = 一次性语义成立。
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
bad = []

# ---------------- 复攻 F2: 双线程消费同一批准
# 修复后败者在取走阶段即被拒,不再进入 upload——barrier 卡两人的旧打法失效,
# 改为 Event 卡住胜者上传,实证"胜者占用批准期间败者干净拒、全链恰好 1 次 job"。
ph = write_eff_approval("r2case", "debug", 1, 32, "12:00:00", CMD, [f1])
in_upload = threading.Event()
release = threading.Event()
orig_upload = scnet_client.upload_file


def slow_upload(*a, **kw):
    in_upload.set()
    release.wait(timeout=15)
    return orig_upload(*a, **kw)


scnet_client.upload_file = slow_upload
results, errors = [], []


def worker():
    try:
        r = scnet_client.submit_job("r2case", [f1], "debug", 1, 32, "12:00:00")
        results.append(r)
    except Exception as e:
        errors.append(repr(e))


import builtins  # noqa: E402
import unittest.mock as mock  # noqa: E402
with mock.patch.object(sys, "stdin", NoTTY()), \
        mock.patch.object(builtins, "input",
                          lambda prompt="": (_ for _ in ()).throw(AssertionError)):
    t1 = threading.Thread(target=worker)
    t1.start()
    assert in_upload.wait(10), "胜者未进入上传"
    t2 = threading.Thread(target=worker)  # 胜者上传中,败者发起
    t2.start()
    t2.join(20)
    release.set()
    t1.join(20)
scnet_client.upload_file = orig_upload

njobs = len(fake.submit_calls())
nsucc = sum(1 for r in results if r["submitted"])
# 败者干净拒: 取走窗口内撞"并发取走"或批准已消失撞"无人工批准",都合法;
# 关键是 不异常、不提交、有 refused 返回
loser_clean = any((not r["submitted"]) and r.get("refused") for r in results)
acts = [e["action"] for e in audit_entries()]
ok = njobs == 1 and nsucc == 1 and not errors and loser_clean
print("F2复攻: job=%d 成功=%d 败者干净拒=%r 异常=%r 审计=%r"
      % (njobs, nsucc, loser_clean, errors, acts))
verdict("PASS" if ok else "FINDING", "r2a_race_double_spend",
        "job=%d(期望1) 成功=%d(期望1) 败者=%r 异常=%r"
        % (njobs, nsucc, loser_clean, errors))
if not ok:
    bad.append("r2a")

# ---------------- V1 直接并发 _take_approval
reset_state()
ph1 = write_eff_approval("r2v1", "debug", 1, 32, "12:00:00", CMD, [f1])
bar1 = threading.Barrier(8)
taken = []


def taker():
    bar1.wait(timeout=10)
    taken.append(scnet_client._take_approval(ph1))


ts = [threading.Thread(target=taker) for _ in range(8)]
[t.start() for t in ts]
[t.join(15) for t in ts]
ngot = sum(1 for x in taken if x)
print("V1 8线程_take_approval: 拿到数=%d(期望1)" % ngot)
verdict("PASS" if ngot == 1 else "FINDING", "r2b_take_race",
        "原子取走并发: 成功=%d" % ngot)
if ngot != 1:
    bad.append("r2b")
# 清理拿到的暂存
for x in taken:
    if x and os.path.exists(x):
        os.remove(x)

# ---------------- V2 败者立即重试
reset_state()
fake.calls.clear()
write_eff_approval("r2case", "debug", 1, 32, "12:00:00", CMD, [f1])
with no_tty():
    r1 = scnet_client.submit_job("r2case", [f1], "debug", 1, 32, "12:00:00")
    r2 = scnet_client.submit_job("r2case", [f1], "debug", 1, 32, "12:00:00")
n2 = len(fake.submit_calls())
v2 = r1["submitted"] and not r2["submitted"] and n2 == 1
print("V2 串行重放: 首=%r 次=%r job=%d" % (r1["submitted"], r2["submitted"], n2))
verdict("PASS" if v2 else "FINDING", "r2c_sequential_replay",
        "首=%r 次拒=%r job=%d(期望1)" % (r1["submitted"], not r2["submitted"], n2))
if not v2:
    bad.append("r2c")

# ---------------- V3 多批准并发(每参数 2 线程抢)
reset_state()
fake.calls.clear()
cases = [("r2m%d" % i) for i in range(3)]
for c in cases:
    write_eff_approval(c, "debug", 1, 32, "12:00:00", CMD, [f1])
res = {c: [] for c in cases}


def mworker(c):
    try:
        with mock.patch.object(sys, "stdin", NoTTY()):
            res[c].append(scnet_client.submit_job(c, [f1], "debug", 1, 32,
                                                  "12:00:00")["submitted"])
    except Exception as e:
        res[c].append("EXC:%r" % e)


with mock.patch.object(sys, "stdin", NoTTY()), \
        mock.patch.object(builtins, "input",
                          lambda prompt="": (_ for _ in ()).throw(AssertionError)):
    ts = [threading.Thread(target=mworker, args=(c,)) for c in cases for _ in (0, 1)]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
per = {c: sum(1 for x in res[c] if x is True) for c in cases}
njobs3 = len(fake.submit_calls())
v3 = all(v == 1 for v in per.values()) and njobs3 == 3
print("V3 多批准并发: 每case成功=%r 总job=%d(期望3)" % (per, njobs3))
verdict("PASS" if v3 else "FINDING", "r2d_multi_approval_race",
        "每case成功=%r job=%d" % (per, njobs3))
if not v3:
    bad.append("r2d")

print("R2 总不良项: %r" % bad)
sys.exit(1 if bad else 0)
