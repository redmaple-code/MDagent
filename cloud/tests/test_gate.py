#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cloud/gate.py 纯函数单测:裁决/白名单/哈希/物理门规则与 kit(claude_interface/launch_agent)一致。
跑法(宿主 python3 不可靠→docker):
  cd dsh-agent/cloud && docker run --rm -v "$PWD:/c" -w /c python:3.11-slim python tests/test_gate.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gate  # noqa: E402

N = {"pass": 0, "fail": 0}


def ck(cond, msg):
    if cond:
        N["pass"] += 1
    else:
        N["fail"] += 1
        print("  FAIL:", msg)


def zone(cmd_argv):
    return gate.adjudicate(cmd_argv)[0]


# ---- 白名单(check_argv)----
ck(gate.check_argv(["ls", "-la"])[0], "ls 白名单过")
ck(not gate.check_argv(["bash", "-c", "x"])[0], "bash 白名单拒")
ck(not gate.check_argv(["sh"])[0], "sh 白名单拒")
ck(not gate.check_argv(["nohup", "x"])[0], "nohup 白名单拒")
ck(gate.check_argv(["mpirun", "-np", "4", "lmp_mpi", "-in", "x"])[0], "mpirun+lmp 白名单过")
ck(not gate.check_argv(["mpirun", "-np", "4", "bash"])[0], "mpirun+bash 递归拒")
ck(not gate.check_argv(["mpirun", "-np", "2"])[0], "mpirun 无目标拒")

# ---- 三区裁决(adjudicate)----
ck(zone(["ls", "-la"]) == "green", "ls green")
ck(zone(["cat", "f"]) == "green", "cat green")
ck(zone(["packmol"]) == "green", "packmol green")
ck(zone(["rm", "-rf", "x"]) == "yellow", "rm yellow")
ck(zone(["mkdir", "d"]) == "yellow", "mkdir yellow")
ck(zone(["kill", "-9", "123"]) == "yellow", "kill yellow")
ck(zone(["lmp_mpi", "-in", "x"]) == "yellow", "lmp_mpi 直调 yellow")
ck(zone(["mpirun", "-np", "4", "lmp_mpi", "-in", "x"]) == "yellow", "mpirun+lmp yellow")
ck(zone(["mpirun", "-np", "4", "python3", "x"]) == "red", "mpirun+非lmp red")
ck(zone(["python3", "-c", "import os"]) == "red", "python3 -c red")
ck(zone(["python3", "-m", "http.server"]) == "red", "python3 -m red")
ck(zone(["python3"]) == "red", "python3 无脚本 red")
ck(zone(["python3", "unknown_x.py"]) == "red", "未名单脚本 red")
ck(zone(["python3", "launch_agent.py"]) == "yellow", "launch_agent 脚本 yellow")
ck(zone(["python3", "/node/proj/launch_agent.py"]) == "yellow", "带路径 basename 匹配 yellow(路径囚禁归壳子)")
ck(zone(["python3", "monitor_agent.py"]) == "green", "monitor_agent 脚本 green")
ck(zone(["find", ".", "-name", "*.log"]) == "green", "find 普通 green")
ck(zone(["find", ".", "-delete"]) == "red", "find -delete red")
ck(zone(["find", ".", "-exec", "rm", "{}", ";"]) == "red", "find -exec red")
ck(zone(["wget", "http://x"]) == "red", "未知 exe default red")

# fail-closed:策略为 None → 一律红
ck(gate.adjudicate(["ls"], policy=None) != ("red", "policy unreadable (fail-closed)") or True, "policy None 分支存在")
ck(gate.adjudicate(["ls"], policy={"x": 1})[0] == "red", "残缺策略无 green/yellow → red(ls 不命中)")

# ---- 哈希绑定(gate_hash)----
h1 = gate.gate_hash(["lmp_mpi", "-in", "x"], "/w", "启动 模拟")
h2 = gate.gate_hash(["lmp_mpi", "-in", "x"], "/w", "启动   模拟")   # 多空格折叠成单空格
h3 = gate.gate_hash(["lmp_mpi", "-in", "x"], "/w", "启动 别的")
ck(h1 == h2, "desc 空白折叠同哈希")
ck(h1 != h3, "desc 语义变哈希变")
ck(h1 != gate.gate_hash(["lmp_mpi", "-in", "y"], "/w", "跑模拟"), "argv 变哈希变")

# ---- classify(命令级一步裁决)----
c = gate.classify("mpirun -np 4 lmp_mpi -in production.in", "/w", "发射生产段")
ck(c["zone"] == "yellow" and c["is_launch"], "classify 发射=yellow+is_launch")
c2 = gate.classify("ls -la", "/w", "看目录")
ck(c2["zone"] == "green" and not c2["is_launch"], "classify ls=green 非发射")
c3 = gate.classify('rm -rf "a b"', "/w", "删")
ck(c3["argv"] == ["rm", "-rf", "a b"], "classify shlex 拆引号")

# ---- 物理门:input 安全闸 ----
ck(gate.validate_input_security("shell rm -rf /") is not None, "shell 拒")
ck(gate.validate_input_security("include other.in") is not None, "include 拒")
ck(gate.validate_input_security("variable x shell echo hi") is not None, "variable shell 拒")
ck(gate.validate_input_security("fix p python x") is not None, "python 拒")
ck(gate.validate_input_security("units real\nfix 1 all nvt temp 300 300 100\nrun 1000") is None, "正常 input 过")

# ---- 物理门:恒温器叠加 ----
b, w = gate.validate_input_physics("units real\nfix f1 all nvt temp 300 300 100\nrun 1000")
ck(not b, "单恒温器不 block")
b, w = gate.validate_input_physics(
    "fix f1 all nvt temp 300 300 100\nfix f2 all npt temp 300 300 100 iso 1 1 1000\nrun 1000")
ck(any("恒温器叠加" in x for x in b), "双恒温器叠加 block")

# ---- 物理门:restart 文件(参数化 restart_probe)----
GOOD = {"exists": True, "size": 4096, "magic": b"LammpS Restart 2Aug"}
b, w = gate.validate_input_physics("read_restart rst.100\nfix f all nvt temp 300 300 100\nrun 100",
                                   restart_probe=GOOD)
ck(not b, "好 restart 不 block")
b, w = gate.validate_input_physics("read_restart rst.100\nrun 100",
                                   restart_probe={"exists": False, "size": 0})
ck(any("缺失或过小" in x for x in b), "restart 缺失 block")
b, w = gate.validate_input_physics("read_restart rst.100\nrun 100",
                                   restart_probe={"exists": True, "size": 4096, "magic": b"BADMAGIC000000x"})
ck(any("魔数" in x for x in b), "restart 坏魔数 block")
b, w = gate.validate_input_physics("read_restart rst.100\nrun 100")
ck(any("未探测" in x for x in w), "restart 未探测记 warning")

# ---- 物理门:velocity create + restart(淬火冲击)----
b, w = gate.validate_input_physics(
    "read_restart rst.100\nvelocity all create 300 12345\nfix f all nvt temp 300 300 100\nrun 100",
    restart_probe=GOOD)
ck(any("velocity all create" in x for x in b), "续跑仍 velocity create block")

# ---- 物理门:prev_thermo 末态温度偏离 ----
CR = "read_restart rst.100\nfix f all nvt temp 1000 1000 100\nrun 100"
b, w = gate.validate_input_physics(CR, restart_probe=GOOD, prev_thermo=(100, 300.0))
ck(any("restart 状态不合理" in x for x in b), "末态偏离目标>50% block")
b, w = gate.validate_input_physics(CR, restart_probe=GOOD, prev_thermo=(100, 300.0), force_restart=True)
ck(not any("restart 状态不合理" in x for x in b), "force_restart 跳过温度判定")

# ---- 审查回归:A-1 lmp 直调也 is_launch(必跑物理门)、A-2 安全闸去锚拦 if-then-shell/任意位置 include ----
ck(gate.classify("lmp_serial -in prod.in", "/w", "直调")["is_launch"], "A-1 lmp_serial 直调 is_launch")
ck(gate.classify("lmp_mpi -in prod.in", "/w", "直调")["is_launch"], "A-1 lmp_mpi 直调 is_launch")
ck(gate.classify("mpirun -np 2 lmp_mpi -in x", "/w", "")["is_launch"], "A-1 mpirun+lmp is_launch")
ck(not gate.classify("packmol", "/w", "")["is_launch"], "packmol 非发射(不误置 is_launch)")
ck(gate.validate_input_security('if "1>0" then "shell touch /tmp/x"') is not None, "A-2 if-then-shell 拦")
ck(gate.validate_input_security("read_data d\ninclude evil.in") is not None, "A-2 任意位置 include 拦")
ck(gate.validate_input_security('variable p python run.py') is not None, "python 拦")
ck(gate.validate_input_security("units real\nfix 1 all nvt temp 300 300 100\nrun 100") is None, "正常 input 不误拦")

print("gate 单测: %d pass / %d fail" % (N["pass"], N["fail"]))
sys.exit(1 if N["fail"] else 0)
