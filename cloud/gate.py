#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""云端写门裁决 + 物理门（纯函数;规则从 kit 照抄,不碰 agent)。

裁决逻辑照 `kit_deploy_originals/claude_interface.py`:
  ALLOWED_EXECUTABLES(:20) / _check_argv(:117) / _adjudicate(:168) / _gate_hash(:225)
物理门照 `kit_deploy_originals/launch_agent.py`:
  validate_input_security(:17) / _target_temp(:24) / derive_schedule_from_input(:151) / validate_input_physics(:47)

与本地版的唯二偏离(均因计划 §2「裁决在云、执行在壳」,规则本身一字不改):
  ① python3 脚本的 realpath「运行时目录内真身」校验(claude_interface:182-195)移交节点壳子
     mdgate.js(P1 已落地的路径囚禁真墙);云端只按脚本 basename 分区,是否越狱由壳子 realpath 兜底。
  ② 物理门原地读文件(restart 魔数、上一 run 末态 thermo)——云端这些文件在用户节点上,故参数化为
     restart_probe / prev_thermo,由 app.py 经 remote-world(fs.get/tail)从节点读回喂入;gate 本身零 IO,可纯单测。

策略数据(green/yellow 名单)原在 /etc/mdsim/exec-policy.json(root:root、读失败=红 fail-closed;旧系统未留档),
这里内置默认(照 RELEASE.md v1.5.24 §三区 + ALLOWED_EXECUTABLES 划分)+ MDAGENT_EXEC_POLICY 指向的 JSON 可覆盖。
RELEASE.md:215 纪律:白名单不收窄——收窄会把危险命令从「红区排队」降级成「硬拒绝」,失去人工放行通道。"""
import hashlib
import json
import os
import re
import shlex

# ---- 可执行文件白名单(照 claude_interface.py:20;故意不含 bash/sh/nohup/setsid/env——任意命令包装器)----
ALLOWED_EXECUTABLES = {
    "python3", "python3.11", "python", "ls", "cat", "tail", "head", "grep", "egrep",
    "ps", "kill", "pkill", "cp", "mv", "mkdir", "echo", "wc", "find",
    "stat", "df", "du", "touch", "rm", "nvidia-smi", "free", "uptime",
    "packmol", "mpirun", "lmp_mpi", "lmp_serial", "lmp", "sleep",
}

# LAMMPS 引擎名:直调(lmp_serial -in x)或经 mpirun 发射都算"发车",都必须跑物理门/安全闸(A-1 修)。
# "lmp" 是 LAMMPS 官方 cmake 构建的默认二进制名(与传统 make 的 lmp_mpi/lmp_serial 并列),同为合法引擎。
ENGINE_NAMES = ("lmp_mpi", "lmp_serial", "lmp")

# ---- 三区策略数据(green/yellow;红=其余,fail-closed)。逻辑在 adjudicate(),数据在此,可 env 覆盖 ----
DEFAULT_POLICY = {
    "green": {
        # 只读命令(RELEASE.md:213「绿(只读命令/绿名单脚本/packmol)」);find 带危险旗标在 adjudicate 里升红
        "executables": ["ls", "cat", "tail", "head", "grep", "egrep", "ps", "stat",
                        "df", "du", "free", "uptime", "nvidia-smi", "echo", "wc", "find",
                        "packmol"],
        # 只读分析脚本(节点上按 basename 认;越狱由壳子囚禁)
        "scripts": ["monitor_agent.py", "preflight_agent.py"],
    },
    "yellow": {
        # 写/重算力(发车、工作区内写、进程管理)
        "executables": ["cp", "mv", "mkdir", "touch", "rm", "kill", "pkill", "sleep",
                        "lmp_mpi", "lmp_serial", "lmp"],
        # 发射/恢复脚本(RELEASE.md:213「黄(launch/preflight/recover)」)
        "scripts": ["launch_agent.py", "recover_agent.py"],
    },
}


def load_policy():
    """默认策略;MDAGENT_EXEC_POLICY 指向的 JSON 存在且可读则用它(缺/坏 → 默认,不 fail-closed 到红:
    fail-closed 是 adjudicate 里「策略为 None」才触发,这里保证总有一份可用策略)。"""
    p = os.environ.get("MDAGENT_EXEC_POLICY")
    if p and os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                pol = json.load(f)
            if isinstance(pol, dict) and "green" in pol and "yellow" in pol:
                return pol
        except Exception:
            pass
    return DEFAULT_POLICY


# ---- 白名单校验(照 _check_argv:117;mpirun 递归校验发射目标,防 'mpirun -np 1 bash' 绕过)----
def check_argv(argv):
    """返回 (ok, msg)。"""
    if not argv:
        return False, "empty command"
    exe = os.path.basename(argv[0])
    if exe not in ALLOWED_EXECUTABLES:
        return False, ("executable '%s' not in allowlist; refusing. Allowed: %s"
                       % (exe, sorted(ALLOWED_EXECUTABLES)))
    if exe == "mpirun":
        i, launched = 1, None
        while i < len(argv):
            a = argv[i]
            if a in ("-np", "-n"):
                i += 2
                continue
            if a.startswith("-"):
                i += 1
                continue
            launched = a
            break
        if launched is None:
            return False, "mpirun without a launch target; refusing"
        sub = os.path.basename(launched)
        if sub not in ALLOWED_EXECUTABLES:
            return False, ("mpirun launch target '%s' not in allowlist; refusing. Allowed: %s"
                           % (sub, sorted(ALLOWED_EXECUTABLES)))
    return True, "ok"


# ---- 三区裁决(照 _adjudicate:168;偏离①:python3 脚本不做 realpath,移交节点壳子)----
def adjudicate(argv, policy=None):
    """返回 (zone, reason); zone ∈ green/yellow/red。policy=None 走 load_policy()。"""
    pol = policy if policy is not None else load_policy()
    if pol is None:                       # 与本地一致:策略不可读一律红(fail-closed)
        return "red", "policy unreadable (fail-closed)"
    exe = os.path.basename(argv[0]) if argv else ""
    green = pol.get("green", {})
    yellow = pol.get("yellow", {})
    if exe in ("python3", "python3.11"):
        if len(argv) < 2:
            return "red", "python3 without script"
        if argv[1].startswith("-"):
            return "red", "python3 flag form (-c/-m/...)"
        script = os.path.basename(argv[1])
        # 偏离①:本地在此做 realpath「运行时目录内真身」校验防同名伪造提权;云端脚本在节点,
        # 该校验由壳子 mdgate.js 的 realpath 路径囚禁负责,gate 只按 basename 分区。
        if script in green.get("scripts", []):
            return "green", "green script %s (路径囚禁由节点壳子保证)" % script
        if script in yellow.get("scripts", []):
            return "yellow", "yellow script %s (路径囚禁由节点壳子保证)" % script
        return "red", "python3 target not zoned: %s" % script
    if exe in green.get("executables", []):
        # find -exec/-delete 等效任意执行 → 升红(照 _adjudicate:200)
        if exe == "find" and any(a in ("-exec", "-execdir", "-ok", "-okdir",
                                       "-delete", "-fprint", "-fprintf") for a in argv[1:]):
            return "red", "find with dangerous flag"
        return "green", "green executable %s" % exe
    if exe in yellow.get("executables", []):
        return "yellow", "yellow executable %s" % exe
    if exe == "mpirun":
        # 发射目标是 lmp → 黄(留痕不排队);非 lmp → 红(照 _adjudicate:208-222)
        i = 1
        while i < len(argv):
            a = argv[i]
            if a in ("-np", "-n"):
                i += 2
                continue
            if a.startswith("-"):
                i += 1
                continue
            if os.path.basename(a) in ENGINE_NAMES:
                return "yellow", "mpirun+lmp"
            break
        return "red", "mpirun non-lmp target"
    return "red", "default red: %s" % exe


# ---- 命令哈希绑定(照 _gate_hash:225;批准与命令严格绑定,防替换)----
def gate_hash(argv, cwd, description):
    desc = " ".join(str(description).split())        # 折叠空白:换措辞/多空格不算新命令,语义改动才算
    blob = json.dumps({"argv": argv, "cwd": cwd, "desc": desc},
                      ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()


def parse_cmd(cmd):
    """把命令行安全拆成 argv(shell=False 口径:元字符成字面量,不做拼接/展开)。返回 (argv, err)。"""
    try:
        argv = shlex.split(cmd)
    except ValueError as e:
        return None, "命令解析失败: %s" % e
    if not argv:
        return None, "空命令"
    return argv, None


# ==== 物理门(照 launch_agent.py;偏离②:文件读参数化为 restart_probe/prev_thermo)====
# 去行首锚(A-2 修):LAMMPS `if "expr" then "shell …"` 把命令藏在 then/else 子句里,行首锚 ^\s*shell 抓不到;
# include 引入的文件不经本校验,任意位置的 include 都要拦。词边界 \b 避免误伤 my_shell 之类标识符。
_FORBIDDEN_INPUT = [
    (r'\bshell\s', "shell command"),
    (r'\bvariable\s+\S+\s+shell\b', "variable shell"),
    (r'\binclude\s', "include"),
    (r'\bpython\b', "python invocation"),
]


def validate_input_security(content):
    """LAMMPS input 安全闸(照 :17):shell/variable shell/include/python 命中即拒。返回 None 或原因串。"""
    for pat, label in _FORBIDDEN_INPUT:
        m = re.search(pat, content, flags=re.M | re.I)
        if m:
            return "forbidden %s: %s" % (label, m.group(0).strip()[:80])
    return None


def _target_temp(step, schedule):
    """schedule=[[s,e,ts,te],...](照 :24)。"""
    if not schedule:
        return None
    for s, e, ts, te in schedule:
        if s <= step <= e:
            return ts if e == s else ts + (step - s) / (e - s) * (te - ts)
    return schedule[-1][3]


def derive_schedule_from_input(content):
    """重启续跑的有效温度程序(照 :151):read_restart 起步步数 + fix nvt/npt temp + run N 展开。"""
    m = re.search(r'^\s*read_restart\s+(\S+)', content, re.M)
    if not m:
        return None
    ms = re.search(r'\.(\d+)$', m.group(1))
    if not ms:
        return None
    cur = int(ms.group(1))
    segs, cur_t = [], None
    for raw in content.split('\n'):
        line = raw.split('#', 1)[0].strip()
        fm = re.match(r'^fix\s+\S+\s+\S+\s+(?:nvt|npt)\b.*?temp\s+([\d.]+)\s+([\d.]+)', line)
        if fm:
            cur_t = (float(fm.group(1)), float(fm.group(2)))
            continue
        rm = re.match(r'^run\s+(\d+)', line)
        if rm and cur_t:
            n = int(rm.group(1))
            if n > 0:
                segs.append([cur, cur + n, cur_t[0], cur_t[1]])
            cur += n
    return segs or None


def validate_input_physics(content, schedule=None, force_restart=False,
                           restart_probe=None, prev_thermo=None):
    """发车前物理校验(照 :47;偏离②:文件读参数化)。返回 (blockers, warnings)。
    restart_probe: None(未探测该文件,跳过 restart 文件判定并记 warning) 或
                   {"exists":bool, "size":int, "magic":bytes|str(前14字节)} —— app.py 经节点 fs.get 读回。
    prev_thermo:   None(未探测) 或 (step, temp) —— 上一 run 末态,app.py 经节点 tail 读回。"""
    blockers, warnings = [], []

    # 1. 恒温器叠加:新 fix nvt/npt 起用时上一个未 unfix → 双恒温器温度必失控(block)。文末最后一个 fix 无需 unfix。
    open_thermostats = {}
    for raw in content.split('\n'):
        line = raw.split('#', 1)[0].strip()
        m = re.match(r'^fix\s+(\S+)\s+\S+\s+(nvt|npt)\b', line)
        if m:
            if open_thermostats:
                blockers.append(
                    "恒温器叠加: 新 fix %s(%s) 起用时 %s 未 unfix — 双恒温器温度必失控(T2000 第一次失败)"
                    % (m.group(1), m.group(2), list(open_thermostats)))
                break
            open_thermostats[m.group(1)] = m.group(2)
            continue
        m = re.match(r'^unfix\s+(\S+)', line)
        if m:
            open_thermostats.pop(m.group(1), None)

    # 2. tdamp 启发式:ReaxFF 体系 tdamp = 100 × timestep,偏离 >50% → warn
    m_ts = re.search(r'^\s*timestep\s+([\d.]+)', content, re.M)
    if m_ts:
        ts = float(m_ts.group(1))
        expected = ts * 100
        for m in re.finditer(r'\b(?:nvt|npt)\b[^\n#]*?\btemp\s+[\d.]+\s+[\d.]+\s+([\d.]+)', content):
            tdamp = float(m.group(1))
            if abs(tdamp - expected) > expected * 0.5:
                warnings.append("tdamp=%s 偏离标准值 %.0ffs(100×timestep=%s)" % (tdamp, expected, ts))

    # 3. restart 状态校验
    m_rst = re.search(r'^\s*read_restart\s+(\S+)', content, re.M)
    if m_rst:
        rst = m_rst.group(1)
        if restart_probe is None:
            warnings.append("read_restart %s: 未探测节点上的 restart 文件(跳过魔数/大小校验)" % rst)
        else:
            if not restart_probe.get("exists") or int(restart_probe.get("size", 0)) < 1024:
                blockers.append("read_restart 文件缺失或过小: %s" % rst)
            else:
                magic = restart_probe.get("magic")
                if isinstance(magic, str):
                    magic = magic.encode("utf-8", "ignore")
                if magic is not None and magic[:14] != b"LammpS Restart":
                    blockers.append("read_restart 文件损坏(魔数不符): %s" % rst)
        if not force_restart:
            eff = derive_schedule_from_input(content) or schedule
            if eff and prev_thermo:
                tgt = _target_temp(prev_thermo[0], eff)
                if tgt and abs(prev_thermo[1] - tgt) > tgt * 0.5:
                    blockers.append(
                        "restart 状态不合理: 上一 run 末态 step=%s T=%.0fK, 偏离该步目标 %.0fK 超 50%% "
                        "— restart 来自失败工况(T2000 第二次失败)。确认要用可传 force_restart=True"
                        % (prev_thermo[0], prev_thermo[1], tgt))
        # 4. 续跑模板校验(与 recover_agent 同口径;force_restart 不豁免:模板 bug 不是工况判定)
        ms_step = re.search(r'\.(\d+)$', rst)
        if ms_step:
            rs_step = int(ms_step.group(1))
            if re.search(r'^\s*velocity\s+all\s+create\b', content, re.M):
                blockers.append("续跑 input 仍含 velocity all create: 覆盖 restart 保存的速度"
                                "(淬火冲击, T2000 复发放大根因)——续跑重塑应已摘除, 此为漏网")
            segs4 = derive_schedule_from_input(content)
            if segs4 and schedule:
                try:
                    intended_end = max(int(s[1]) for s in schedule if len(s) >= 2)
                    planned_end = int(segs4[-1][1])
                    if planned_end > intended_end * 1.05:
                        blockers.append("续跑跑过头: restart(%s) + 各段 run = %s > 计划终点 %s "
                                        "— run 步数未按 restart 步数折算"
                                        % (rs_step, planned_end, intended_end))
                except (TypeError, ValueError, IndexError):
                    pass
            n_stages = len(re.findall(r'^\s*fix\s+\S+\s+\S+\s+(?:nvt|npt)\b', content, re.M))
            if n_stages > 1:
                warnings.append("续跑 input 含 %d 个恒温阶段, 疑似未按 restart 步数裁段" % n_stages)
    return blockers, warnings


# ---- 便于 app.py 一步裁决:命令 → {zone, reason, hash, argv, allow_ok, allow_msg, is_launch} ----
def classify(cmd, cwd=None, description="", policy=None):
    """把一条命令跑完白名单 + 三区裁决 + 哈希。不含物理门(发射路径由 app.py 另调 validate_input_*)。"""
    argv, err = parse_cmd(cmd)
    if err:
        return {"error": err}
    allow_ok, allow_msg = check_argv(argv)
    zone, reason = adjudicate(argv, policy)
    argv0 = os.path.basename(argv[0]) if argv else ""
    # is_launch:mpirun+lmp 或 lmp 直调都算发车,都必须跑物理门(A-1:直调 lmp_serial -in x 曾命中
    # yellow.executables 而 reason!="mpirun+lmp"→物理门被跳过→含 shell 的 input 绕过安全闸→节点 RCE)。
    is_launch = zone == "yellow" and (reason == "mpirun+lmp" or argv0 in ENGINE_NAMES)
    return {
        "argv": argv,
        "allow_ok": allow_ok, "allow_msg": allow_msg,
        "zone": zone, "reason": reason,
        "hash": gate_hash(argv, cwd, description),
        "is_launch": is_launch,
    }
