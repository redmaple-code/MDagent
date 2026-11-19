# -*- coding: utf-8 -*-
"""r1 —— 复攻 F1(伪造批准) + PIN 人工通道全攻击面。

复攻 F1: 原"手写批准文件即自批"——修复后 _valid_approval 强 schema:
  plan_hash 字段必须等于文件名、approved_epoch 非 bool 数值、0<=age<=1800。
  六变体全试: 缺 plan_hash / ts 未来 / 字段不符 / epoch=True(bool) / epoch 字符串 /
  内容非 dict。预期: 全拒、零提交、拒绝入账。
信任边界(E2 同型,基线已定稿): 同 uid 手写"schema 完全合法"的批准文件仍被接受
  ——这是代码注释明示的接受项,验证其行为并判 ACCEPT(不翻案)。
PIN 新攻击面:
  P1 正规人工流: set_pin + dry-run + approve(TTY/YES/PIN 猴补丁) → 批准文件
     落盘 600、目录 700、提交成功 approved_via=approval_file;
  P2 错 PIN / 无 PIN → approve 拒,审计 approve_rejected;
  P3 无 TTY approve → ScnetError(gate),input 不被偷读;
  P4 set_pin: 无 TTY CLI 拒 / 太短拒 / 换 PIN 无旧拒 / 错旧拒 / 正旧成;
  P5 pin.json 被换成攻击者已知哈希(同 uid 写 state)= E2 信任边界,ACCEPT;
  P6 getpass 环境变量绕过: 静态断言源码不以环境变量取 PIN,getpass 不读 env;
  P7 畸形 pin.json(垃圾/缺字段)→ _verify_pin False 不裸抛;
  P8 approve 不存在 plan_hash → 干净 ScnetError。
退出码 0 = 全部符合预期。
"""
import os
import stat
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atk_common import *  # noqa

fake = bootstrap()
f1 = make_file("production.in", "run 100\n")
CMD = "mpirun -np 32 lmp_mpi -in production.in"
CASE, Q, N, P, W = "r1case", "debug", 1, 32, "12:00:00"
bad = []

# ---------------- 复攻 F1: 六伪造变体(全部用修复后口径的 ph)
ph = eff_plan_hash(CASE, Q, N, P, W, CMD, [f1])
VARIANTS = [
    ("r1a_missing_plan_hash", {"approved_epoch": time.time()}),
    ("r1b_future_ts", {"plan_hash": ph, "approved_epoch": time.time() + 9999}),
    ("r1c_field_mismatch", {"plan_hash": "别的hash", "approved_epoch": time.time()}),
    ("r1d_epoch_bool", {"plan_hash": ph, "approved_epoch": True}),
    ("r1e_epoch_str", {"plan_hash": ph, "approved_epoch": "now"}),
    ("r1f_not_dict", [ph, time.time()]),
]
for tag, payload in VARIANTS:
    reset_state()
    fake.calls.clear()
    write_approval_file(ph, payload)
    with no_tty():
        r = scnet_client.submit_job(CASE, [f1], Q, N, P, W)
    jobs = len(fake.submit_calls())
    ok = (not r["submitted"]) and jobs == 0
    acts = [e["action"] for e in audit_entries()]
    print("%-24s submitted=%r job=%d 审计=%r" % (tag, r["submitted"], jobs, acts))
    verdict("PASS" if ok and acts == ["refused"] else "FINDING", tag,
            "伪造变体: submitted=%r job=%d 审计=%r" % (r["submitted"], jobs, acts))
    if not ok:
        bad.append(tag)

# ---------------- E2 边界: 同 uid 手写 schema 全合法 → 仍被接受(接受项,验证实录)
reset_state()
fake.calls.clear()
write_eff_approval(CASE, Q, N, P, W, CMD, [f1])
with no_tty():
    r = scnet_client.submit_job(CASE, [f1], Q, N, P, W)
print("E2 同uid手写全合法批准: submitted=%r via=%r" % (r["submitted"],
                                                    r.get("approved_via")))
verdict("ACCEPT", "r1g_e2_same_uid_forged",
        "同uid写state者手写schema全合法批准仍被接受(submitted=%r)——代码注释明示的"
        "信任边界(基线E2同型,不翻案);闸的意义=正规代码路径无法无人自批"
        % r["submitted"])

# ---------------- P1 正规人工流: set_pin → dry_run → approve → submit
reset_state()
fake.calls.clear()
scnet_client.set_pin("abcd1234")
assert not os.environ.get("SCNET_PIN"), "测试前置"
with no_tty():
    dr = scnet_client.submit_job(CASE, [f1], Q, N, P, W, dry_run=True)
phr = dr["plan"]["plan_hash"]
ok_ap = human_approve(phr, pin="abcd1234")
ap_path = scnet_client._approval_path(phr)
mode_f = stat.S_IMODE(os.stat(ap_path).st_mode)
mode_d = stat.S_IMODE(os.stat(scnet_client._approval_dir()).st_mode)
with no_tty():
    r = scnet_client.submit_job(CASE, [f1], Q, N, P, W)
p1 = ok_ap and r["submitted"] and r.get("approved_via") == "approval_file" \
    and mode_f == 0o600 and mode_d == 0o700
print("P1 正规人工流: approve=%r submitted=%r via=%r 文件=%o 目录=%o"
      % (ok_ap, r["submitted"], r.get("approved_via"), mode_f, mode_d))
verdict("PASS" if p1 else "FINDING", "r1h_pin_legit_flow",
        "approve=%r submitted=%r via=%r 批准文件%o(期望600) 目录%o(期望700)"
        % (ok_ap, r["submitted"], r.get("approved_via"), mode_f, mode_d))
if not p1:
    bad.append("P1")

# ---------------- P2 错 PIN / 未设 PIN
reset_state()
fake.calls.clear()
with no_tty():
    dr = scnet_client.submit_job(CASE, [f1], Q, N, P, W, dry_run=True)
phr = dr["plan"]["plan_hash"]
if os.path.exists(scnet_client._pin_path()):
    os.remove(scnet_client._pin_path())  # 未设 PIN
r_nopin = human_approve(phr, pin="abcd1234")
scnet_client.set_pin("abcd1234")
r_badpin = human_approve(phr, pin="wrongpin1")
r_no_yes = None
with human_tty(pin="abcd1234", answer="no"):
    r_no_yes = scnet_client.approve(phr)
acts = [e["action"] for e in audit_entries()]
p2 = (r_nopin is False and r_badpin is False and r_no_yes is False
      and acts.count("approve_rejected") >= 2)
print("P2 未设PIN=%r 错PIN=%r 不答YES=%r 审计拒绝数=%d"
      % (r_nopin, r_badpin, r_no_yes, acts.count("approve_rejected")))
verdict("PASS" if p2 else "FINDING", "r1i_pin_wrong_or_unset",
        "未设/错PIN/不答YES全拒且入账=%r" % p2)
if not p2:
    bad.append("P2")

# ---------------- P3 无 TTY approve 直接拒
try:
    with no_tty():
        scnet_client.approve(phr)
    p3 = False
except scnet_client.ScnetError as e:
    p3 = "TTY" in str(e)
print("P3 无TTY approve: ScnetError=%r" % p3)
verdict("PASS" if p3 else "FINDING", "r1j_approve_no_tty",
        "无TTY approve 抛 gate 异常=%r" % p3)
if not p3:
    bad.append("P3")

# ---------------- P4 set_pin 规则
p4s = []
with no_tty():  # 无 TTY CLI set-pin
    import unittest.mock as mock
    with mock.patch.object(sys, "argv", ["x", "approve", "--set-pin"]):
        rc = scnet_client.main()
    p4s.append(("无TTY CLI set-pin rc=%d(期望3)" % rc, rc == 3))
try:
    scnet_client.set_pin("123")
    p4s.append(("短PIN未拒", False))
except scnet_client.ScnetError:
    p4s.append(("短PIN拒", True))
try:
    scnet_client.set_pin("newpin5678")  # 已设过,不给旧
    p4s.append(("无旧PIN换PIN未拒", False))
except scnet_client.ScnetError:
    p4s.append(("无旧PIN换PIN拒", True))
try:
    scnet_client.set_pin("newpin5678", old_pin="错误旧PIN")
    p4s.append(("错旧PIN未拒", False))
except scnet_client.ScnetError:
    p4s.append(("错旧PIN拒", True))
scnet_client.set_pin("newpin5678", old_pin="abcd1234")
p4s.append(("正旧PIN换成功", scnet_client._verify_pin("newpin5678")))
p4 = all(x[1] for x in p4s)
for name, ok in p4s:
    print("P4 %-28s %r" % (name, ok))
verdict("PASS" if p4 else "FINDING", "r1k_set_pin_rules",
        "; ".join(n for n, ok in p4s if not ok) or "set_pin 全规则符合")
if not p4:
    bad.append("P4")

# ---------------- P5 pin.json 替换(同 uid,E2 边界)
salt = "00" * 16
# LD4 后 _approval_path 只收 16-hex,pin.json 直接写(LD4 修复生效的旁证)
with open(scnet_client._pin_path(), "w") as _f:
    json.dump({"salt": salt,
               "hash": scnet_client._hash_pin("attacker-pin1", salt)}, _f)
os.chmod(scnet_client._pin_path(), 0o600)
reset_state_keep_pin = None  # noqa
with no_tty():
    dr = scnet_client.submit_job(CASE, [f1], Q, N, P, W, dry_run=True)
r_p5 = human_approve(dr["plan"]["plan_hash"], pin="attacker-pin1")
print("P5 pin.json被替换后用攻击者PIN approve=%r" % r_p5)
verdict("ACCEPT", "r1l_pin_json_replaced",
        "同uid可改写pin.json为已知哈希→approve=%r;与手写批准文件同型(E2信任边界内,"
        "基线已定稿);正规路径无PIN仍开不出" % r_p5)

# ---------------- P6 环境变量绕过(静态+动态)
src = open(scnet_client.__file__, encoding="utf-8").read()
env_pin = [ln for ln in src.splitlines()
           if "environ" in ln and "PIN" in ln.upper()]
os.environ["SCNET_PIN"] = "654321"
os.environ["PIN"] = "654321"
reset_state()
with no_tty():  # 环境变量喂 PIN 后无 TTY 仍必须拒
    r = scnet_client.submit_job(CASE, [f1], Q, N, P, W)
p6 = (not env_pin) and not r["submitted"]
print("P6 源码env取PIN行=%r 设env后无TTY提交=%r" % (env_pin, r["submitted"]))
verdict("PASS" if p6 else "FINDING", "r1m_pin_env_bypass",
        "getpass 不读环境变量;SCNET_PIN/PIN env 无任何效果(submitted=%r)"
        % r["submitted"])
if not p6:
    bad.append("P6")

# ---------------- P7 畸形 pin.json
for content in ["垃圾非json{", '{"salt": 1}', '{"hash": "x"}', "[]"]:
    with open(scnet_client._pin_path(), "w") as fh:
        fh.write(content)
    try:
        v = scnet_client._verify_pin("abcd1234")
        crashed = False
    except Exception:
        v, crashed = None, True
    print("P7 pin.json=%-18r verify=%r 崩溃=%r" % (content[:14], v, crashed))
    if v or crashed:
        bad.append("P7")
verdict("PASS" if "P7" not in bad else "FINDING", "r1n_malformed_pin_json",
        "畸形pin.json全返回False不裸抛")

# ---------------- P8 approve 不存在 ph
try:
    with human_tty(pin="newpin5678"):
        scnet_client.approve("deadbeefdeadbeef")
    p8 = False
except scnet_client.ScnetError as e:
    p8 = "dry-run" in str(e)
print("P8 不存在ph approve: 干净拒=%r" % p8)
verdict("PASS" if p8 else "FINDING", "r1o_approve_unknown_hash",
        "approve 未知 plan_hash 干净 ScnetError=%r" % p8)
if not p8:
    bad.append("P8")

print("R1 总不良项: %r" % bad)
sys.exit(1 if bad else 0)
