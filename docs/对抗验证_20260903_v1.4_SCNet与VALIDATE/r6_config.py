# -*- coding: utf-8 -*-
"""r6 —— 复攻 F8(畸形配置崩溃式拒绝、审计缺口)。

修复后: gate fail-closed,一切畸形干净 refused + 审计,不裸抛。
用直接注入 _config 的方式覆盖: scnet=None / 空dict / whitelist 字符串/列表 /
queues None / max_nodes None / max_walltime 字符串 / enabled 缺失 /
scnet 段是字符串。预期: 全干净 refused、零 job、零裸抛、逐条审计。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atk_common import *  # noqa

write_fake_credentials()
f1 = make_file("production.in", "run 100\n")
bad = []


def inject(scnet):
    cfg = {"scnet": scnet}
    scnet_client._config = lambda: (cfg, cfg["scnet"])


BAD = [
    ("scnet_整个None", None),
    ("scnet_字符串", "tyhcnormal"),
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
    ("enabled_1而非true", dict(GOOD_SCNET, enabled=1)),
]
for tag, scnet in BAD:
    inject(scnet)
    fake = install_http(FakeHTTP())
    reset_state()
    try:
        with no_tty():
            r = scnet_client.submit_job("r6case", [f1], "debug", 1, 32,
                                        "12:00:00", cmd="c")
        outcome = "submitted=%r refused=%r" % (r["submitted"],
                                               str(r.get("refused"))[:55])
        submitted = r["submitted"]
        crashed = False
    except Exception as e:
        outcome = "裸抛 %s: %s" % (type(e).__name__, str(e)[:70])
        submitted, crashed = False, True
    jobs = len(fake.submit_calls())
    acts = [e["action"] for e in audit_entries()]
    ok = (not submitted) and jobs == 0 and acts == ["refused"] and not crashed
    print("%-26s %s | job=%d 审计=%r" % (tag, outcome, jobs, acts))
    verdict("PASS" if ok else "FINDING", "r6_" + tag,
            outcome + " job=%d 审计=%r 裸抛=%r" % (jobs, acts, crashed))
    if not ok:
        bad.append(tag)

print("R6 总不良项: %r" % bad)
sys.exit(1 if bad else 0)
