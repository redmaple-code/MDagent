# -*- coding: utf-8 -*-
"""gmx_agent —— GROMACS 唯一启动入口(代码强制,2026-09-10 用户拍板
"要写到代码里,不能提示词约束")。

契约(与 v3.3 专科手同构):
    python3 gmx_agent.py input.json result.json

input.json(launch 动作):
    {"action": "launch",
     "dir": "<run 目录,必须在某个 enabled 的 monitor root 下>",
     "deffnm": "prod",           # 可选,默认 prod
     "tpr": "<路径>",            # 可选,默认 <dir>/<deffnm>.tpr
     "extra_args": ["-nt", "8"]  # 可选,追加给 gmx mdrun 的参数(逐个元素,不拼 shell)
    }

硬校验(代码闸,任一不过拒绝启动):
    ① dir 必须落在 enabled monitor root 内(扫不到 = 管不着,不许跑);
    ② tpr 文件存在;
    ③ 该目录无在跑 mdrun(get_gmx_pids 为空);
    ④ 负载余量:loadavg1 < CPU 核数(粗粒度,防把机器打爆)。
启动顺序:先 adopt(原子写注册表)→ 再 spawn —— 不存在"先跑后登记"窗口。
绕过本入口直接起的 mdrun 由推送驱动 auto_kill_wild SIGTERM 处死。

py3.6 兼容(sim_exec 的 python3 = 系统 3.6.8):无 walrus/dataclass/海象。
"""
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gmx_scan  # noqa: E402


def _fail(msg):
    return {"success": False, "error": msg}


def _under_enabled_root(run_dir):
    rp = os.path.realpath(run_dir)
    for root in gmx_scan.monitor_roots():
        if root.get("type") != "gromacs":
            continue
        base = os.path.realpath(root["path"])
        if rp == base or rp.startswith(base + os.sep):
            return True
    return False


def _load_headroom_ok():
    try:
        with open("/proc/loadavg") as f:
            load1 = float(f.read().split()[0])
        cpus = os.cpu_count() or 1
        return load1 < cpus
    except Exception:
        return True  # 读不到不拦(粗粒度闸,误判代价高于放行)


def action_launch(inp):
    run_dir = str(inp.get("dir") or "").strip()
    if not run_dir or not os.path.isdir(run_dir):
        return _fail("dir 不存在: %r" % run_dir)
    if not _under_enabled_root(run_dir):
        return _fail("dir 不在任何 enabled 的 monitor root 下(扫不到=管不着,不许跑): %s" % run_dir)
    deffnm = str(inp.get("deffnm") or "prod").strip() or "prod"
    tpr = str(inp.get("tpr") or "").strip() or os.path.join(run_dir, deffnm + ".tpr")
    if not os.path.isfile(tpr):
        return _fail("tpr 不存在: %s" % tpr)
    if gmx_scan.get_gmx_pids(run_dir):
        return _fail("该目录已有 mdrun 在跑,拒绝重复启动: %s" % run_dir)
    if not _load_headroom_ok():
        return _fail("负载已满(loadavg1 ≥ 核数),稍后再试")
    gmx = str(inp.get("gmx_bin") or "").strip() or shutil.which("gmx") or ""
    if not gmx or not os.path.isfile(gmx):
        return _fail("找不到 gmx 可执行文件(PATH 内无 gmx;可用 gmx_bin 参数显式指定)")

    extra = inp.get("extra_args") or []
    if not isinstance(extra, list) or not all(isinstance(x, str) for x in extra):
        return _fail("extra_args 必须是字符串数组")

    # 启动即接管:先写注册表(原子),再起进程 —— 无"先跑后登记"窗口
    gmx_scan.adopt(run_dir)

    log_path = os.path.join(run_dir, deffnm + "_stdout.log")
    cmd = [gmx, "mdrun", "-deffnm", deffnm, "-s", tpr] + list(extra)
    try:
        out = open(log_path, "ab")
        p = subprocess.Popen(cmd, cwd=run_dir, stdout=out,
                             stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             start_new_session=True)
    except Exception as e:
        return _fail("spawn 失败: %s" % e)
    return {"success": True, "pid": p.pid, "dir": os.path.realpath(run_dir),
            "deffnm": deffnm, "log": log_path, "adopted": True,
            "cmd": " ".join(cmd)}


ACTIONS = {"launch": action_launch}


def main(argv):
    if len(argv) != 3:
        sys.stderr.write("usage: python3 gmx_agent.py input.json result.json\n")
        return 2
    inp_path, out_path = argv[1], argv[2]
    try:
        with open(inp_path, encoding="utf-8") as f:
            inp = json.load(f)
    except Exception as e:
        inp = {}
        result = _fail("input.json 读不了: %s" % e)
    else:
        fn = ACTIONS.get(str(inp.get("action") or ""))
        result = fn(inp) if fn else _fail("未知 action: %r(支持: %s)" % (
            inp.get("action"), ",".join(sorted(ACTIONS))))
    tmp = out_path + ".tmp.%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    os.replace(tmp, out_path)
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
