# -*- coding: utf-8 -*-
"""s11 —— md_config 被篡改: scnet 段缺键/类型错时必须 fail-closed 且入账。

攻击假设: md_config.json 被写坏(人手滑/agent 误写): scnet=null、
  whitelist="字符串"、"queues"=None、enabled 缺省等。
预期: 任何畸形都必须拒绝提交(fail-closed)且有审计;崩溃无审计 = 发现
  (拒绝仍成立但审计链断,且异常而非干净 refused)。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atk_common import *  # noqa

write_fake_credentials()
f1 = make_file("production.in", "run 100\n")

BAD = [
    ("scnet_整个缺失", None),
    ("scnet_空dict", {}),
    ("whitelist_字符串", dict(GOOD_SCNET, whitelist="tyhcnormal")),
    ("whitelist_列表", dict(GOOD_SCNET, whitelist=["tyhcnormal"])),
    ("queues_None", dict(GOOD_SCNET, whitelist={"queues": None,
                        "max_nodes": 2, "max_walltime_h": 24})),
    ("max_nodes_None", dict(GOOD_SCNET, whitelist={"queues": ["debug"],
                            "max_nodes": None, "max_walltime_h": 24})),
    ("max_walltime_字符串'abc'", dict(GOOD_SCNET, whitelist={
        "queues": ["debug"], "max_nodes": 2, "max_walltime_h": "abc"})),
    ("enabled_缺失", {k: v for k, v in GOOD_SCNET.items() if k != "enabled"}),
]
for tag, scnet in BAD:
    patch_config(scnet)
    fake = install_http(FakeHTTP())
    reset_state()
    try:
        with no_tty():
            r = scnet_client.submit_job("s11case", [f1], "debug", 1, 32,
                                        "12:00:00", cmd="c")
        outcome = "submitted=%r refused=%r" % (r["submitted"],
                                               str(r.get("refused"))[:60])
        submitted = r["submitted"]
    except Exception as e:
        outcome = "EXCEPTION %s: %s" % (type(e).__name__, str(e)[:80])
        submitted = False
    jobs = len(fake.submit_calls())
    acts = [e["action"] for e in audit_entries()]
    audited = bool(acts)
    print("%-24s %s | job=%d 审计=%r" % (tag, outcome, jobs, acts))
    if submitted or jobs:
        verdict("FINDING", "s11_" + tag, "畸形配置仍提交: " + outcome)
    elif not audited and not submitted:
        verdict("FINDING", "s11_" + tag,
                "fail-closed成立但拒绝未入审计(异常路径绕过_audit): " + outcome)
    else:
        verdict("PASS", "s11_" + tag, outcome + " 审计=%r" % acts)
sys.exit(0)
