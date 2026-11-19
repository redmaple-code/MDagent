# -*- coding: utf-8 -*-
"""r9 —— 新攻击: 失败恢复路径滥用。

  R1 "批准延期": 批准临近过期(age=1700s) → 提交失败(恢复) → 时间前进 200s
     (猴补丁 time.time) → _valid_approval 仍卡 age(1900>1800)必须拒;
     恢复不得刷新 approved_epoch(读文件实证)。
  R2 孤儿 .taken-*: 手工造孤儿暂存 + 多次失败恢复,合法批准不受孤儿影响;
     成功后无孤儿;孤儿无 GC 堆积(实录,定级观察/低)。
  R3 连续失败两次: 恢复→再失败→再恢复→第三次成功,批准语义不漂移。
  R4 取走期间攻击者在原路径写伪造批准: 失败恢复 rename 会覆盖它(POSIX rename
     原子替换)→ 恢复后仍是原批准内容(实证 approved_epoch 不变)。
退出码 0 = 恢复路径无滥用。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atk_common import *  # noqa

f1 = make_file("production.in", "run 100\n")
CMD = "mpirun -np 32 lmp_mpi -in production.in"
bad = []

# ---------------- R1 批准延期
fake = bootstrap(fail_on={"/file/upload": scnet_client.ScnetError(
    "network", "断网", "up")})
ph = write_eff_approval("r9a", "debug", 1, 32, "12:00:00", CMD, [f1], age=1700)
ap_path = scnet_client._approval_path(ph)
try:
    with no_tty():
        scnet_client.submit_job("r9a", [f1], "debug", 1, 32, "12:00:00")
except scnet_client.ScnetError:
    pass
restored = os.path.exists(ap_path)
epoch_before = json.load(open(ap_path))["approved_epoch"] if restored else None
fake.fail_on.clear()
real_time = time.time
scnet_client.time.time = lambda: real_time() + 200  # 时间前进 200s → age≈1900
try:
    with no_tty():
        r = scnet_client.submit_job("r9a", [f1], "debug", 1, 32, "12:00:00")
    postponed = r["submitted"]
finally:
    scnet_client.time.time = real_time
epoch_after = json.load(open(ap_path))["approved_epoch"] if restored else None
r1 = restored and not postponed and epoch_before == epoch_after
print("R1 延期: 恢复=%r 延期后submitted=%r epoch不变=%r"
      % (restored, postponed, epoch_before == epoch_after))
verdict("PASS" if r1 else "FINDING", "r9a_approval_life_extension",
        "失败恢复不续命: age卡死拒绝=%r epoch不变=%r"
        % (not postponed, epoch_before == epoch_after))
if not r1:
    bad.append("R1")

# ---------------- R2 孤儿 taken
reset_state()
d = scnet_client._approval_dir()
for i in range(3):  # 攻击者/崩溃留下孤儿
    open(os.path.join(d, "deadbeef%d.taken-%s" % (i, uuid.hex if False else
                          scnet_client.uuid.uuid4().hex)), "w").write("{}")
ph2 = write_eff_approval("r9b", "debug", 1, 32, "12:00:00", CMD, [f1])
fake2 = install_http(FakeHTTP())
with no_tty():
    r2 = scnet_client.submit_job("r9b", [f1], "debug", 1, 32, "12:00:00")
left = [x for x in os.listdir(d) if ".taken-" in x]
r2ok = r2["submitted"] and len(left) == 3  # 只剩预置孤儿,本次无新增
print("R2 孤儿: 3孤儿存在下提交=%r 成功后新增孤儿=%d" % (r2["submitted"],
                                                        len(left) - 3))
verdict("PASS" if r2ok else "FINDING", "r9b_orphan_taken",
        "孤儿不干扰合法批准(submitted=%r);成功路径无孤儿残留(新增=%d)"
        % (r2["submitted"], len(left) - 3))
verdict("ACCEPT", "r9b2_orphan_gc",
        "孤儿 .taken-* 无 GC,崩溃/攻击可堆积(纯占盘,无安全语义——"
        "_valid_approval 只认 <ph>.json 精确名,taken 永不重新生效);定级:观察/低")
if not r2ok:
    bad.append("R2")
for x in left:
    os.remove(os.path.join(d, x))

# ---------------- R3 连续失败两次后成功
reset_state()
ph3 = write_eff_approval("r9c", "debug", 1, 32, "12:00:00", CMD, [f1])
fake3 = install_http(FakeHTTP(fail_on={"/file/upload": scnet_client.ScnetError(
    "network", "断网", "up")}))
fails = 0
for _ in range(2):
    try:
        with no_tty():
            scnet_client.submit_job("r9c", [f1], "debug", 1, 32, "12:00:00")
    except scnet_client.ScnetError:
        fails += 1
kept = os.path.exists(scnet_client._approval_path(ph3))
fake3.fail_on.clear()
with no_tty():
    r3 = scnet_client.submit_job("r9c", [f1], "debug", 1, 32, "12:00:00")
gone = not os.path.exists(scnet_client._approval_path(ph3))
r3ok = fails == 2 and kept and r3["submitted"] and gone
print("R3 连败两次: fails=%d 批准在=%r 三试成功=%r 用后销毁=%r"
      % (fails, kept, r3["submitted"], gone))
verdict("PASS" if r3ok else "FINDING", "r9c_double_fail_retry",
        "两次失败恢复后批准仍有效,第三次成功销毁")
if not r3ok:
    bad.append("R3")

# ---------------- R4 取走期间伪造被恢复覆盖
reset_state()
ph4 = write_eff_approval("r9d", "debug", 1, 32, "12:00:00", CMD, [f1])
orig_epoch = json.load(open(scnet_client._approval_path(ph4)))["approved_epoch"]
taken = scnet_client._take_approval(ph4)  # 模拟提交中途
write_approval_file(ph4, {"plan_hash": ph4, "approved_epoch": time.time() + 100})
os.rename(taken, scnet_client._approval_path(ph4))  # 模拟失败恢复
now_epoch = json.load(open(scnet_client._approval_path(ph4)))["approved_epoch"]
r4 = now_epoch == orig_epoch
print("R4 取走期间伪造: 恢复后epoch=%r(原=%r)覆盖伪造=%r"
      % (now_epoch, orig_epoch, r4))
verdict("PASS" if r4 else "FINDING", "r9d_restore_overwrites_forged",
        "失败恢复 rename 原子覆盖取走期间的伪造文件,恢复原批准")
if not r4:
    bad.append("R4")

print("R9 总不良项: %r" % bad)
sys.exit(1 if bad else 0)
