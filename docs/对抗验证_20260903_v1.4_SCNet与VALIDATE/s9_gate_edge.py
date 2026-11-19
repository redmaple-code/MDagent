# -*- coding: utf-8 -*-
"""s9 —— gate 擦边/类型混淆: nodes 字符串、0、负数;walltime 擦边;
queue 大小写变体;whitelist 缺键/类型错。

攻击假设: 调用方(agent 代码)直接调 submit_job/gate_check,参数未必经 CLI
  type=int 收敛;配置文件可能被写成类型错的值。
预期: 白名单语义只应放行"队列精确命中 且 1<=nodes<=max 且 0<walltime<=max"。
  任何放行了语义越界请求的 = 打穿;异常崩溃但无审计 = 发现(审计缺口)。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atk_common import *  # noqa

write_fake_credentials()
f1 = make_file("production.in", "run 100\n")
findings = []


def try_submit(tag, scnet, case, queue, nodes, walltime, cmd="c", ppn=32):
    """全链 submit(带合法批准),看 gate 是否放行到 /job。"""
    patch_config(scnet)
    fake = install_http(FakeHTTP())
    reset_state()
    try:
        n = int(nodes)
        ph = scnet_client.plan_hash(case, queue, n, ppn, walltime, cmd, [f1])
        write_approval_file(ph, {"plan_hash": ph, "approved_epoch": time.time()})
    except Exception:
        pass
    try:
        with no_tty():
            r = scnet_client.submit_job(case, [f1], queue, nodes, ppn, walltime,
                                        cmd=cmd)
        passed = bool(r["submitted"]) or bool(fake.submit_calls())
        detail = "submitted=%r refused=%r" % (r["submitted"],
                                              str(r.get("refused"))[:60])
    except Exception as e:
        passed, detail = False, "EXCEPTION %s: %s" % (type(e).__name__, e)
    print("%-28s gate放行=%r  %s" % (tag, passed, detail))
    return passed, detail


G = lambda **kw: dict(GOOD_SCNET, **kw)  # noqa: E731

# ---- 语义擦边(期望全拒)
cases = [
    ("nodes_str_'1'", G(), "c1", "debug", "1", "01:00:00", True),   # int()收敛,合法
    ("nodes_0", G(), "c2", "debug", 0, "01:00:00", False),          # 0节点应拒
    ("nodes_-1", G(), "c3", "debug", -1, "01:00:00", False),        # 负数应拒
    ("walltime_48:00:01", G(), "c4", "debug", 1, "48:00:01", False),
    ("walltime_24:00:00_eq", G(), "c5", "debug", 1, "24:00:00", True),  # 边界含
    ("walltime_00:00:00", G(), "c6", "debug", 1, "00:00:00", False),    # 0墙时应拒
    ("queue_大小写_Debug", G(), "c7", "Debug", 1, "01:00:00", False),
    ("queue_前后空格", G(), "c8", " debug", 1, "01:00:00", False),
    # ---- 类型混淆 / 缺键
    ("enabled_字符串'false'", G(enabled="false"), "c9", "debug", 1,
     "01:00:00", False),  # 字符串truthy→fail-open?
    ("auto_submit_字符串'no'", G(auto_submit="no"), "c10", "debug", 1,
     "01:00:00", False),
    ("queues_字符串'debug'", G(whitelist={"queues": "debug", "max_nodes": 2,
     "max_walltime_h": 24}), "c11", "bug", 1, "01:00:00", False),  # 子串命中?
    ("queues_None", G(whitelist={"queues": None, "max_nodes": 2,
     "max_walltime_h": 24}), "c12", "任意队", 1, "01:00:00", None),  # 设计:空=全放行
    ("whitelist整个缺失", G(whitelist=None), "c13", "任意队", 1,
     "01:00:00", None),  # 同上
    ("max_nodes_字符串'2'", G(whitelist={"queues": ["debug"], "max_nodes": "2",
     "max_walltime_h": 24}), "c14", "debug", 3, "01:00:00", False),
    ("walltime_怪异'1:99:99'", G(), "c15", "debug", 1, "1:99:99", False),
]
for tag, scnet, case, q, n, wt, expect_pass in cases:
    passed, detail = try_submit(tag, scnet, case, q, n, wt)
    if expect_pass is None:
        verdict("ACCEPT", "s9_" + tag,
                "空queues/缺whitelist=全队列放行(设计,tests钉死);但仍受nodes/walltime上限约束: %s"
                % detail)
    elif passed != expect_pass:
        verdict("FINDING", "s9_" + tag, "期望放行=%r 实际=%r %s"
                % (expect_pass, passed, detail))
        findings.append(tag)
    else:
        verdict("PASS", "s9_" + tag, detail)

sys.exit(1 if findings else 0)
