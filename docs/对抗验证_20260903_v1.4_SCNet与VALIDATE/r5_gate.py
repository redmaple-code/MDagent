# -*- coding: utf-8 -*-
"""r5 —— 复攻 F6(类型混淆 fail-open)/F7(无下界) + gate 新擦边。

复攻 F6/F7(s9): 修复后 enabled/auto_submit 必须 is True;whitelist 必须 dict;
  queues 必须字符串数组;max_nodes 正整数;max_walltime_h 正有限数;
  nodes/ppn 正整数;walltime 严格 H{1,4}:MM:SS 分秒<60 且 >0。
  原 s9 全案用修复后口径(合法批准用 eff ph)重打。
新擦边:
  G1 walltime "12345:00:00"(5 位小时) / "1:2:03"(非两位分秒) / "24:00:00.5" 拒;
  G2 max_walltime_h=True(bool) / max_nodes=True(bool) / max_nodes=2.0(float) 拒;
  G3 queues=[1,"debug"](混入非字符串) 拒;
  G4 gate_check 直接喂 nodes=True(bool) 拒(submit_job 会先 int() 收敛,静态注记);
  G5 ppn=0 / ppn="x" 经 submit_job 干净拒并入账;
  G6 queues=None 与 whitelist 整缺 = 全队列放行(设计,tests 钉死)ACCEPT,
     但 nodes/walltime 上限仍约束(实证)。
退出码 0 = gate 语义全部钉死。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atk_common import *  # noqa

write_fake_credentials()
f1 = make_file("production.in", "run 100\n")
CMD = "mpirun -np 32 lmp_mpi -in production.in"
bad = []


def try_submit(tag, scnet, case, queue, nodes, walltime, cmd=CMD, ppn=32,
               expect_pass=False, accept=None):
    patch_config(scnet)
    fake = install_http(FakeHTTP())
    reset_state()
    try:  # 能算出 ph 就写合法批准(让 gate 成为唯一变量)
        ph = eff_plan_hash(case, queue, nodes, ppn, walltime, cmd, [f1])
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
        passed, detail = False, "EXCEPTION %s: %s" % (type(e).__name__,
                                                      str(e)[:70])
    print("%-30s 放行=%r %s" % (tag, passed, detail))
    if accept:
        verdict("ACCEPT", "r5_" + tag, accept + " | " + detail)
        return
    if passed != expect_pass:
        verdict("FINDING", "r5_" + tag, "期望放行=%r 实际=%r %s"
                % (expect_pass, passed, detail))
        bad.append(tag)
    else:
        verdict("PASS", "r5_" + tag, detail)


G = lambda **kw: dict(GOOD_SCNET, **kw)  # noqa: E731

# ---- 原 s9 全案(修复后口径)
try_submit("nodes_str_'1'", G(), "g1", "debug", "1", "01:00:00", expect_pass=True)
try_submit("nodes_0", G(), "g2", "debug", 0, "01:00:00")
try_submit("nodes_-1", G(), "g3", "debug", -1, "01:00:00")
try_submit("walltime_48:00:01", G(), "g4", "debug", 1, "48:00:01")
try_submit("walltime_24:00:00_eq", G(), "g5", "debug", 1, "24:00:00",
           expect_pass=True)
try_submit("walltime_00:00:00", G(), "g6", "debug", 1, "00:00:00")
try_submit("queue_Debug大小写", G(), "g7", "Debug", 1, "01:00:00")
try_submit("queue_前后空格", G(), "g8", " debug", 1, "01:00:00")
try_submit("enabled_字符串'false'", G(enabled="false"), "g9", "debug", 1, "01:00:00")
try_submit("auto_submit_字符串'no'", G(auto_submit="no"), "g10", "debug", 1,
           "01:00:00")
try_submit("queues_字符串'debug'",
           G(whitelist={"queues": "debug", "max_nodes": 2, "max_walltime_h": 24}),
           "g11", "bug", 1, "01:00:00")
try_submit("queues_None", G(whitelist={"queues": None, "max_nodes": 2,
           "max_walltime_h": 24}), "g12", "任意队", 1, "01:00:00",
           accept="queues=None=全队列放行(设计,tests钉死)")
try_submit("whitelist整个缺失", G(whitelist=None), "g13", "任意队", 1, "01:00:00",
           accept="whitelist缺失=全队列放行(设计,tests钉死)")
try_submit("max_nodes_字符串'2'",
           G(whitelist={"queues": ["debug"], "max_nodes": "2",
                        "max_walltime_h": 24}), "g14", "debug", 3, "01:00:00")
try_submit("walltime_'1:99:99'", G(), "g15", "debug", 1, "1:99:99")

# ---- 新擦边
try_submit("walltime_'12345:00:00'", G(), "g16", "debug", 1, "12345:00:00")
try_submit("walltime_'1:2:03'", G(), "g17", "debug", 1, "1:2:03")
try_submit("walltime_'24:00:00.5'", G(), "g18", "debug", 1, "24:00:00.5")
try_submit("walltime_'00:00:01'1秒", G(), "g19", "debug", 1, "00:00:01",
           expect_pass=True)
try_submit("max_walltime_h_True",
           G(whitelist={"queues": ["debug"], "max_nodes": 2,
                        "max_walltime_h": True}), "g20", "debug", 1, "01:00:00")
try_submit("max_nodes_True",
           G(whitelist={"queues": ["debug"], "max_nodes": True,
                        "max_walltime_h": 24}), "g21", "debug", 1, "01:00:00")
try_submit("max_nodes_2.0float",
           G(whitelist={"queues": ["debug"], "max_nodes": 2.0,
                        "max_walltime_h": 24}), "g22", "debug", 1, "01:00:00")
try_submit("queues_混入非字符串",
           G(whitelist={"queues": [1, "debug"], "max_nodes": 2,
                        "max_walltime_h": 24}), "g23", "debug", 1, "01:00:00")
try_submit("max_walltime_h_inf",
           G(whitelist={"queues": ["debug"], "max_nodes": 2,
                        "max_walltime_h": float("inf")}), "g24", "debug", 1,
           "01:00:00")
try_submit("ppn_0", G(), "g25", "debug", 1, "01:00:00", ppn=0)
try_submit("ppn_'x'", G(), "g26", "debug", 1, "01:00:00", ppn="x")

# ---- G4 gate_check 直接喂 bool(nodes)
r_bool = scnet_client.gate_check(GOOD_SCNET, "debug", True, "01:00:00")
print("G4 gate_check(nodes=True) -> %r" % r_bool)
verdict("PASS" if r_bool else "FINDING", "r5_gate_nodes_bool",
        "gate_check 直接拒 bool nodes=%r(注:submit_job 先 int() 收敛,True→1)"
        % r_bool)
if not r_bool:
    bad.append("G4")

# ---- G6 queues=None 时 nodes/walltime 上限仍约束
try_submit("queues_None但nodes越界",
           G(whitelist={"queues": None, "max_nodes": 2, "max_walltime_h": 24}),
           "g27", "任意队", 5, "01:00:00")
try_submit("queues_None但walltime越界",
           G(whitelist={"queues": None, "max_nodes": 2, "max_walltime_h": 24}),
           "g28", "任意队", 1, "48:00:00")

print("R5 总不良项: %r" % bad)
sys.exit(1 if bad else 0)
