# -*- coding: utf-8 -*-
"""r11 —— 收官复攻(第二轮修复后): 只验新修复点。

  F-N1  gate_check 增 ppn 入闸: ppn=0/-3/bool/字符串全拒+入账;ppn=32 正用放行。
  F-MD2 work_root 逐段白名单: "../escape"、"a/../../b"、"a b" 拒; "ok-root/sub" 正用;
        CLI upload 亦过(submit/CLI 两处)。
  F-LD1 fchmod 兜底: 预置 644 的 token 缓存/pin.json/批准文件,写后实测 600。
  F-LD2 失败恢复不覆盖新批准: 取走期间人工重批(目标存在)→ 恢复删 taken 不覆盖,
        新批准 epoch 不变且后续可用。
  F-LD3 dry_run 异常记 dry_run_failed。
  F-LD4 _approval_path 16-hex 白名单: 穿越/大写/超长全拒不裸抛(_valid_approval=False)。
  F-LD7 query_jobs (data or {}).get("list") or []: list=None → 返回 []。
  F-HD1 PIN_MIN_LEN=8 且禁纯数字;pin.json 缺失时拒绝标 gate_not_armed(返回+审计)。
  F-MD1 kit 侧: validate_agent steps/cores/timeout 三处 int 捕 OverflowError(实弹
        Infinity 输入不崩、结果落盘);orchestrator _handle_validate 的
        validate_timeout=int(inf) 不崩、回退 300 并钳 [30,3600]、fail-closed 落 FAILED。
  F-LD5 kit 侧: validate_agent 写完报告清理历史 validate_<pid>.{in,stdout,err},
        本次三件保留。
环境: 沙盒零外发(atk_common 骨架),kit 侧用 /tmp 输入实跑 validate_agent 主流程
      (lmp/mpirun 不在则 shell 秒败,正好走通报告+清理路径),不改任何被测代码。
退出码 0 = 全部符合预期。
"""
import importlib.util
import json
import logging
import os
import stat
import subprocess
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atk_common import *  # noqa

KIT = "/opt/mdagent/kit/agents/runtime/v3.3"
VA = os.path.join(KIT, "validate_agent.py")
ORCH = os.path.join(KIT, "orchestrator.py")
bad = []

fake = bootstrap()
f1 = make_file("production.in", "run 100\n")
CMD = "mpirun -np 32 lmp_mpi -in production.in"

# ---------------- F-N1 ppn 入闸
for ppn, expect in [(0, False), (-3, False), (True, False), ("x", False),
                    (32, True)]:
    reset_state()
    fake.calls.clear()
    if expect:
        write_eff_approval("r11n1", "debug", 1, 32, "12:00:00", CMD, [f1])
    try:
        with no_tty():
            r = scnet_client.submit_job("r11n1", [f1], "debug", 1, ppn,
                                        "12:00:00")
        passed = r["submitted"]
        detail = "submitted=%r refused=%r" % (passed, str(r.get("refused"))[:50])
    except Exception as e:
        passed, detail = False, "裸抛 %s" % type(e).__name__
    ok = passed == expect
    print("F-N1 ppn=%-6r 放行=%r(期望%r) %s" % (ppn, passed, expect, detail))
    verdict("PASS" if ok else "FINDING", "r11_n1_ppn_%r" % ppn, detail)
    if not ok:
        bad.append("N1:%r" % ppn)
g = scnet_client.gate_check(GOOD_SCNET, "debug", 1, "01:00:00", ppn=True)
verdict("PASS" if g else "FINDING", "r11_n1_gate_bool_ppn",
        "gate_check 直喂 ppn=True 拒=%r" % g)
if not g:
    bad.append("N1:gate_bool")

# ---------------- F-MD2 work_root 逐段
for wr, expect in [("../escape", False), ("a/../../b", False), ("a b", False),
                   ("-lead", False), ("ok-root/sub.dir_2", True)]:
    reset_state()
    patch_config(dict(GOOD_SCNET, work_root=wr))
    fk = install_http(FakeHTTP())
    if expect:
        ph = eff_plan_hash("r11md2", "debug", 1, 32, "12:00:00", CMD, [f1])
        write_approval_file(ph, {"plan_hash": ph,
                                 "approved_epoch": scnet_client.time.time()})
    try:
        with no_tty():
            r = scnet_client.submit_job("r11md2", [f1], "debug", 1, 32,
                                        "12:00:00")
        passed = r["submitted"]
        detail = "submitted=%r refused=%r" % (passed, str(r.get("refused"))[:50])
    except Exception as e:
        passed, detail = False, "裸抛 %s" % type(e).__name__
    ok = passed == expect and (expect or not fk.calls)
    print("F-MD2 work_root=%-16r 放行=%r(期望%r) %s" % (wr, passed, expect, detail))
    verdict("PASS" if ok else "FINDING", "r11_md2_wr_%r" % wr, detail)
    if not ok:
        bad.append("MD2:%r" % wr)
# CLI upload 处
patch_config(dict(GOOD_SCNET, work_root="../escape"))
import unittest.mock as mock  # noqa: E402
with no_tty(), mock.patch.object(sys, "argv",
                                 ["x", "upload", "--file", f1,
                                  "--remote-case", "ok"]):
    rc = scnet_client.main()
ok = rc == 3
print("F-MD2 CLI upload 脏work_root: rc=%d(期望3)" % rc)
verdict("PASS" if ok else "FINDING", "r11_md2_cli_upload", "rc=%d" % rc)
if not ok:
    bad.append("MD2:cli")
patch_config()

# ---------------- F-LD1 fchmod 兜底(预置 644)
reset_state()
tc = scnet_client._token_cache_path()
os.makedirs(os.path.dirname(tc), exist_ok=True)
open(tc, "w").write("{}")
os.chmod(tc, 0o644)
install_http(FakeHTTP())
try:
    scnet_client.authenticate()  # 缓存 {} 无 regions→_platform_token 用不到;先看 mode
except Exception:
    pass
m1 = stat.S_IMODE(os.stat(tc).st_mode) == 0o600
pinp = scnet_client._pin_path()
open(pinp, "w").write("{}")
os.chmod(pinp, 0o644)
os.remove(pinp)  # set_pin 需不存在才不验旧——改为预置 644 后删内容直接测:
open(pinp, "w").write("{}")
os.chmod(pinp, 0o644)
try:
    scnet_client.set_pin("abcd1234", old_pin="x")  # 旧文件畸形→验旧失败→拒
except scnet_client.ScnetError:
    pass
m2 = stat.S_IMODE(os.stat(pinp).st_mode) == 0o600
if not m2:  # 拒绝路径不写;用合法路径再测: 删后重设为 644 旧文件场景
    os.remove(pinp)
    scnet_client.set_pin("abcd1234")
    os.chmod(pinp, 0o644)
    scnet_client.set_pin("efgh5678", old_pin="abcd1234")
    m2 = stat.S_IMODE(os.stat(pinp).st_mode) == 0o600
# 批准文件: 预置 644,走 approve 正规流
reset_state()
scnet_client.set_pin("abcd1234")
install_http(FakeHTTP())
with no_tty():
    dr = scnet_client.submit_job("r11ld1", [f1], "debug", 1, 32, "12:00:00",
                                 dry_run=True)
ap = scnet_client._approval_path(dr["plan"]["plan_hash"])
open(ap, "w").write("{}")
os.chmod(ap, 0o644)
human_approve(dr["plan"]["plan_hash"], pin="abcd1234")
m3 = stat.S_IMODE(os.stat(ap).st_mode) == 0o600
print("F-LD1 token=%r pin=%r approval=%r(全期望True=600)" % (m1, m2, m3))
verdict("PASS" if (m1 and m2 and m3) else "FINDING", "r11_ld1_fchmod",
        "预置644→写后600: token=%r pin=%r approval=%r" % (m1, m2, m3))
if not (m1 and m2 and m3):
    bad.append("LD1")

# ---------------- F-LD2 恢复不覆盖新批准
reset_state()
patch_config()
fake = install_http(FakeHTTP())
old_ph = write_eff_approval("r11ld2", "debug", 1, 32, "12:00:00", CMD, [f1])
new_epoch = [None]
orig_upload = scnet_client.upload_file


def reapprove_then_fail(*a, **kw):
    # 模拟: 取走期间人工重新批准同 ph(新 epoch)
    scnet_client.time.time()
    write_approval_file(old_ph, {"plan_hash": old_ph,
                                 "approved_epoch": scnet_client.time.time() + 0})
    new_epoch[0] = json.load(open(scnet_client._approval_path(old_ph))
                             )["approved_epoch"]
    raise scnet_client.ScnetError("network", "模拟上传断网", "up")


scnet_client.upload_file = reapprove_then_fail
try:
    with no_tty():
        scnet_client.submit_job("r11ld2", [f1], "debug", 1, 32, "12:00:00")
    raised = False
except scnet_client.ScnetError:
    raised = True
scnet_client.upload_file = orig_upload
ap_now = json.load(open(scnet_client._approval_path(old_ph)))["approved_epoch"]
orphans = [x for x in os.listdir(scnet_client._approval_dir())
           if ".taken-" in x]
kept_new = ap_now == new_epoch[0]
fake.fail_on.clear()
with no_tty():  # 新批准应可用 → 提交成功
    r = scnet_client.submit_job("r11ld2", [f1], "debug", 1, 32, "12:00:00")
ok = raised and kept_new and not orphans and r["submitted"]
print("F-LD2 恢复不覆盖: 异常=%r 新批准保留=%r 孤儿=%r 新批准可提交=%r"
      % (raised, kept_new, orphans, r["submitted"]))
verdict("PASS" if ok else "FINDING", "r11_ld2_restore_no_clobber",
        "新批准epoch不变=%r taken已删=%r 后续提交=%r"
        % (kept_new, not orphans, r["submitted"]))
if not ok:
    bad.append("LD2")

# ---------------- F-LD3 dry_run_failed
reset_state()
boom = install_http(FakeHTTP(fail_on={"/ac/openapi/v2/center":
                                      scnet_client.ScnetError("network", "x", "u")}))
try:
    with no_tty():
        scnet_client.submit_job("r11ld3", [f1], "debug", 1, 32, "12:00:00",
                                dry_run=True)
    raised = False
except scnet_client.ScnetError:
    raised = True
acts = [e["action"] for e in audit_entries()]
ok = raised and acts == ["dry_run_failed"]
print("F-LD3 dry_run中途炸: 异常=%r 审计=%r" % (raised, acts))
verdict("PASS" if ok else "FINDING", "r11_ld3_dry_run_failed",
        "审计=%r(期望['dry_run_failed'])" % acts)
if not ok:
    bad.append("LD3")

# ---------------- F-LD4 _approval_path 白名单
install_http(FakeHTTP())
l4 = True
for ph in ["../../etc/passwd", "ABCDEF0123456789", "a" * 17, "../x", "",
           "g123456789abcdef"]:
    try:
        scnet_client._approval_path(ph)
        l4 = False
        print("F-LD4 %r 未拒!" % ph)
    except scnet_client.ScnetError as e:
        if e.code != "param":
            l4 = False
    except Exception:
        l4 = False
ok_path = scnet_client._approval_path("0123456789abcdef")
v = scnet_client._valid_approval("../etc")  # 不裸抛
print("F-LD4 脏ph全拒=%r 合法ph=%r _valid_approval(脏)=False:%r"
      % (l4, ok_path.endswith("0123456789abcdef.json"), v is False))
verdict("PASS" if (l4 and v is False) else "FINDING", "r11_ld4_path_whitelist",
        "穿越/大写/超长全拒=%r 不裸抛=%r" % (l4, v is False))
if not (l4 and v is False):
    bad.append("LD4")

# ---------------- F-LD7 query list=None
class JobsNone(FakeHTTP):
    def __call__(self, method, url, token=None, body=None, headers=None,
                 timeout=30):
        if "/openapi/v2/jobs" in url:
            return {"list": None}
        return super().__call__(method, url, token=token, body=body,
                                headers=headers, timeout=timeout)


install_http(JobsNone())
res = scnet_client.query_jobs()
ok = res == []
print("F-LD7 query list=None → %r" % res)
verdict("PASS" if ok else "FINDING", "r11_ld7_query_list_none", "res=%r" % res)
if not ok:
    bad.append("LD7")

# ---------------- F-HD1 PIN 政策 + gate_not_armed
install_http(FakeHTTP())
h1 = []
for pin, expect in [("12345678", False), ("abcdefg", False), ("1234567a", True),
                    ("abcdefgh", True)]:
    reset_state()
    try:
        scnet_client.set_pin(pin)
        got = True
    except scnet_client.ScnetError:
        got = False
    h1.append((pin, got == expect))
    print("F-HD1 set_pin(%r) 接受=%r(期望%r)" % (pin, got, expect))
# gate_not_armed: 无 pin.json 拒绝带标
reset_state()
fake.calls.clear()
with no_tty():
    r = scnet_client.submit_job("r11hd1", [f1], "debug", 1, 32, "12:00:00")
acts = audit_entries()
armed_mark = "gate_not_armed" in str(r.get("refused")) \
    and "gate_not_armed" in acts[-1].get("reason", "")
# 设 PIN 后不再带标
scnet_client.set_pin("abcd1234")
reset_state_keep = None
with no_tty():
    r2 = scnet_client.submit_job("r11hd1", [f1], "debug", 1, 32, "12:00:00")
no_mark = "gate_not_armed" not in str(r2.get("refused"))
ok = all(x[1] for x in h1) and armed_mark and no_mark
print("F-HD1 PIN政策=%r 无PIN标gate_not_armed=%r 有PIN无标=%r"
      % (all(x[1] for x in h1), armed_mark, no_mark))
verdict("PASS" if ok else "FINDING", "r11_hd1_pin_policy",
        "政策=%r 标记=%r/%r" % (h1, armed_mark, no_mark))
if not ok:
    bad.append("HD1")

# ---------------- F-MD1 kit: validate_agent Infinity 输入不崩
tmpdir = os.path.join(TMP, "kitmd1")
os.makedirs(tmpdir, exist_ok=True)
inp = os.path.join(tmpdir, "in.json")
outp = os.path.join(tmpdir, "out.json")
json.dump({"prod_dir": tmpdir, "case_id": "md1", "steps": float("inf"),
           "cores": float("inf"), "timeout": float("inf"),
           "input_content": "units real\n"}, open(inp, "w"))
cp = subprocess.run([sys.executable, VA, inp, outp], capture_output=True,
                    text=True, timeout=60)
res = json.load(open(outp)) if os.path.exists(outp) else None
m1a = cp.returncode == 0 and res and res.get("pass") is False \
    and "OverflowError" not in cp.stderr
print("F-MD1a validate_agent(steps/cores/timeout=Infinity): rc=%d 结果落盘=%r "
      "无OverflowError=%r" % (cp.returncode, bool(res),
                              "OverflowError" not in cp.stderr))
verdict("PASS" if m1a else "FINDING", "r11_md1_validate_agent_overflow",
        "rc=%d res=%r stderr尾=%r" % (cp.returncode, bool(res),
                                      cp.stderr.strip()[-80:]))
if not m1a:
    bad.append("MD1a")

# orchestrator _handle_validate: validate_timeout=inf → 回退300钳位,不崩
spec = importlib.util.spec_from_file_location("orch_mod", ORCH)
om = importlib.util.module_from_spec(spec)
sys.path.insert(0, os.path.join(KIT, "..", ".."))  # agents 包根
try:
    spec.loader.exec_module(om)
    Orc = None
    for n in dir(om):
        o = getattr(om, n)
        if isinstance(o, type) and "_handle_validate" in o.__dict__:
            Orc = o
            break
    captured = {}
    self = types.SimpleNamespace(
        _state={"task": {"case_id": "md1o", "validate_timeout": float("inf"),
                         "prod_dir": tmpdir}, "agents": {}},
        logger=logging.getLogger("t"))
    self._resolve_schedule = lambda: None
    self._log_strategy = lambda *a, **k: None
    self._feishu = lambda *a: None
    self._update_bus = lambda *a: None

    def fake_run_agent(name, payload, timeout=0):
        captured.update(payload=payload, timeout=timeout)
        return {"pass": True, "checks": {}}

    self._run_agent = fake_run_agent
    Orc._handle_validate(self)
    to = captured.get("payload", {}).get("timeout")
    m1b = (to == 300 and captured.get("timeout") == 360
           and self._state["phase"] == "LAUNCH")
    detail = "回退timeout=%r 外层=%r phase=%r" % (to, captured.get("timeout"),
                                                self._state["phase"])
except Exception as e:
    m1b, detail = False, "裸抛 %s: %s" % (type(e).__name__, str(e)[:80])
print("F-MD1b orchestrator validate_timeout=inf: %s" % detail)
verdict("PASS" if m1b else "FINDING", "r11_md1_orchestrator_overflow", detail)
if not m1b:
    bad.append("MD1b")

# ---------------- F-LD5 kit: 历史 validate_<pid>.* 清理,本次保留
prod = os.path.join(TMP, "kitld5")
os.makedirs(prod, exist_ok=True)
for old in ("validate_99999.in", "validate_99999.stdout", "validate_99999.err",
            "validate_88888.stdout"):
    open(os.path.join(prod, old), "w").write("old\n")
open(os.path.join(prod, "keepme.txt"), "w").write("x")
df = os.path.join(tmpdir, "sys.data")
ff = os.path.join(tmpdir, "ffield.reax")
open(df, "w").write("data\n")
open(ff, "w").write("ff\n")
inp2 = os.path.join(tmpdir, "in2.json")
out2 = os.path.join(tmpdir, "out2.json")
json.dump({"prod_dir": prod, "case_id": "ld5", "steps": 5, "cores": 1,
           "timeout": 30, "data_file": df, "ff_file": ff,
           "input_content": "units real\nrun 5\n"},
          open(inp2, "w"))
cp = subprocess.run([sys.executable, VA, inp2, out2], capture_output=True,
                    text=True, timeout=120)
left = sorted(os.listdir(prod))
old_gone = not any(x.startswith(("validate_99999", "validate_88888"))
                   for x in left)
cur_kept = sum(1 for x in left if x.startswith("validate_")
               and x.split(".")[-1] in ("in", "stdout", "err"))
report = os.path.exists(os.path.join(prod, "validate_report.json"))
keepme = "keepme.txt" in left
l5 = cp.returncode == 0 and old_gone and cur_kept == 3 and report and keepme
print("F-LD5 清理: rc=%d 历史清空=%r 本次三件=%d 报告=%r 无关文件保留=%r"
      % (cp.returncode, old_gone, cur_kept, report, keepme))
print("       剩余: %r" % left)
verdict("PASS" if l5 else "FINDING", "r11_ld5_cleanup",
        "历史清理=%r 本次保留3件=%r 报告=%r" % (old_gone, cur_kept == 3, report))
if not l5:
    bad.append("LD5")

print("R11 总不良项: %r" % bad)
sys.exit(1 if bad else 0)
