# -*- coding: utf-8 -*-
"""r4 —— 复攻 F5(case_id 注入) + 新变体。

复攻 F5(s10): 修复后 case_id 过 _check_safe_name(ASCII ^[A-Za-z0-9._-]+$,
  禁 . .. 与首字符 -)。原 9 个脏 case_id 全应干净拒、零外发、逐条入账。
新变体:
  C1 首字符 "-" / 空串 / 非 str(None) / 纯 "..x"(合法,正用) / 长度 200(无上限,实录);
  C2 CLI upload --remote-case 脏值 → rc=3、零外发、入账;
  C3 合法 case_id 正用(带批准)仍放行;
  C4 拒绝发生在 authenticate 之前(零外发)。
退出码 0 = 注入面封死。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atk_common import *  # noqa

fake = bootstrap()
f1 = make_file("production.in", "run 100\n")
CMD = "mpirun -np 32 lmp_mpi -in production.in"
bad = []

DIRTY = ["../../escape", "..%2f..%2fenc", "a b", "a;rm -rf /", "中文案例",
         "a/b/nested", ".", "..", "case`id`", "case$(id)", "-leading",
         "", "tab\there", "new\nline", "a:b", "a&b", "a|b", "a<b", "€"]
for cid in DIRTY:
    reset_state()
    fake.calls.clear()
    try:
        with no_tty():
            r = scnet_client.submit_job(cid, [f1], "debug", 1, 32, "12:00:00")
        ok = (not r["submitted"]) and not fake.calls
        acts = [e["action"] for e in audit_entries()]
        detail = "refused=%r 外发=%d 审计=%r" % (str(r.get("refused"))[:40],
                                               len(fake.calls), acts)
        good = ok and acts == ["refused"]
    except Exception as e:
        good, detail = False, "裸抛 %s: %s" % (type(e).__name__, str(e)[:60])
    print("case_id=%-16r %s" % (cid, detail))
    verdict("PASS" if good else "FINDING", "r4_dirty_%r" % cid, detail)
    if not good:
        bad.append(repr(cid))

# None / 非 str
reset_state()
fake.calls.clear()
try:
    with no_tty():
        r = scnet_client.submit_job(None, [f1], "debug", 1, 32, "12:00:00")
    ok = not r["submitted"] and not fake.calls
    detail = "None refused=%r 外发=%d" % (str(r.get("refused"))[:40], len(fake.calls))
except Exception as e:
    ok, detail = False, "裸抛 %s: %s" % (type(e).__name__, str(e)[:60])
verdict("PASS" if ok else "FINDING", "r4_none_case_id", detail)
if not ok:
    bad.append("None")

# 正用: 合法 case_id(含 ..x 这种含点但非 . .. 的)带批准放行
for cid in ["case-1.2_OK", "..x"]:
    reset_state()
    fake.calls.clear()
    write_eff_approval(cid, "debug", 1, 32, "12:00:00", CMD, [f1])
    with no_tty():
        r = scnet_client.submit_job(cid, [f1], "debug", 1, 32, "12:00:00")
    print("正用 case_id=%r submitted=%r remote_dir=%r"
          % (cid, r["submitted"], r.get("remote_dir")))
    verdict("PASS" if r["submitted"] else "FINDING", "r4_legit_%r" % cid,
            "合法case_id正用 submitted=%r" % r["submitted"])
    if not r["submitted"]:
        bad.append("legit:%r" % cid)

# C2 CLI upload 脏 remote_case
import unittest.mock as mock  # noqa: E402
reset_state()
fake.calls.clear()
with no_tty(), mock.patch.object(sys, "argv",
                                 ["x", "upload", "--file", f1,
                                  "--remote-case", "../escape"]):
    rc = scnet_client.main()
acts = [e["action"] for e in audit_entries()]
c2 = rc == 3 and not fake.calls and acts and acts[-1] == "refused"
print("C2 CLI upload 脏remote_case: rc=%d 外发=%d 审计=%r" % (rc, len(fake.calls), acts))
verdict("PASS" if c2 else "FINDING", "r4_cli_upload_dirty",
        "rc=%d(期望3) 外发=%d 审计=%r" % (rc, len(fake.calls), acts))
if not c2:
    bad.append("C2")

# C2b CLI upload 正用
reset_state()
fake.calls.clear()
with no_tty(), mock.patch.object(sys, "argv",
                                 ["x", "upload", "--file", f1,
                                  "--remote-case", "ok-case"]):
    rc = scnet_client.main()
print("C2b CLI upload 正用: rc=%d 上传=%d" % (rc, len(fake.upload_calls())))
verdict("PASS" if rc == 0 and len(fake.upload_calls()) == 1 else "FINDING",
        "r4_cli_upload_legit", "rc=%d 上传=%d" % (rc, len(fake.upload_calls())))
if not (rc == 0 and len(fake.upload_calls()) == 1):
    bad.append("C2b")

print("R4 总不良项: %r" % bad)
sys.exit(1 if bad else 0)
