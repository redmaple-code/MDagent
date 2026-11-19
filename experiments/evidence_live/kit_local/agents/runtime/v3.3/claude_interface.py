#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""本地审计执行桥（Windows 开发机专用）——契约对齐 agent_deploy_kit 的
claude_interface.py exec 通道,使 MDagent 唯一写门 sim_exec 在本机可用。

契约(md_mcp.sim_exec 调用约定):
  argv = [python, 本文件, exec, <cmd 字符串>, <desc>, [cwd]]
  stdout: [CLAUDE_INTERFACE] 提示行(不含 '{') + 末尾单行 JSON
  JSON: {returncode, stdout, stderr, audit_id, whitelisted}

纪律(与 kit 通道同语义,不弱化):
- 可执行文件白名单(精确路径);白名单外一律拒(returncode=126)但仍入审计账。
- shlex 切分 + subprocess argv 列表(shell=False,防注入;重定向/管道天然失效)。
- 每条执行追加审计 JSONL(本目录 logs/claude_operations.jsonl)。
- 超时 340s(上游 sim_exec 360s 之内)。
"""
import json
import os
import shlex
import subprocess
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))          # .../kit_local/agents/runtime/v3.3
KIT_LOCAL = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))  # .../kit_local
AUDIT_LOG = os.path.join(KIT_LOCAL, "logs", "claude_operations.jsonl")

# 白名单:本机 LAMMPS 与受控解释器(精确到路径)
WHITELIST = [
    r"E:\LAMMPS 64-bit 2Aug2023-MSMPI\bin\lmp.exe",
    r"E:/LAMMPS 64-bit 2Aug2023-MSMPI/bin/lmp.exe",
    sys.executable,
]
TIMEOUT_SEC = 340


def audit(row):
    os.makedirs(os.path.dirname(AUDIT_LOG), exist_ok=True)
    with open(AUDIT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def split_cmd(cmd):
    """白名单可执行文件按最长前缀匹配(路径含空格免引号),余参 shlex 切分。
    返回 argv 列表;无可执行匹配返回 None。"""
    text = cmd.strip()
    best = None
    for w in WHITELIST:
        w_norm = w.replace("\\", "/").lower()
        text_norm = text.replace("\\", "/").lower()
        if text_norm.startswith(w_norm):
            if best is None or len(w) > len(best):
                best = w
    if best is None:
        return None
    rest = text[len(best):].strip()
    return [best] + (shlex.split(rest) if rest else [])


def main(argv):
    # child sys.argv = [本文件, mode, cmd, desc, (cwd)]  —— argv[0] 是脚本路径
    if len(argv) < 4 or argv[1] not in ("exec", "exec_bg"):
        print("[CLAUDE_INTERFACE] usage: exec <cmd> <desc> [cwd]")
        print(json.dumps({"returncode": 2, "stdout": "", "stderr": "bad usage"}))
        return 2
    mode, cmd, desc = argv[1], argv[2], (argv[3] if len(argv) > 3 else "")
    cwd = argv[4] if len(argv) > 4 else None
    audit_id = uuid.uuid4().hex[:12]
    if mode == "exec_bg":
        payload = {"returncode": 125, "stdout": "",
                   "stderr": "exec_bg not supported by local audit bridge",
                   "audit_id": audit_id, "whitelisted": False}
        audit({"ts": time.time(), "audit_id": audit_id, "mode": mode, "cmd": cmd,
               "desc": desc, "rejected": "exec_bg-unsupported"})
        print("[CLAUDE_INTERFACE] rejected exec_bg")
        print(json.dumps(payload, ensure_ascii=False))
        return 125
    parts = split_cmd(cmd)
    if parts is None:
        payload = {"returncode": 126, "stdout": "",
                   "stderr": "not whitelisted: %s" % cmd[:200],
                   "audit_id": audit_id, "whitelisted": False}
        audit({"ts": time.time(), "audit_id": audit_id, "mode": mode, "cmd": cmd,
               "desc": desc, "rejected": "not-whitelisted"})
        print("[CLAUDE_INTERFACE] rejected (whitelist)")
        print(json.dumps(payload, ensure_ascii=False))
        return 126
    t0 = time.time()
    try:
        r = subprocess.run(parts, capture_output=True, text=True,
                           cwd=cwd or None, timeout=TIMEOUT_SEC,
                           encoding="utf-8", errors="replace")
        rc, out, errout = r.returncode, r.stdout or "", r.stderr or ""
    except subprocess.TimeoutExpired:
        rc, out, errout = 124, "", "timeout after %ds" % TIMEOUT_SEC
    except Exception as e:
        rc, out, errout = 1, "", "%s: %s" % (type(e).__name__, e)
    audit({"ts": time.time(), "audit_id": audit_id, "mode": "exec", "cmd": cmd,
           "desc": desc, "cwd": cwd, "argv": parts, "returncode": rc,
           "duration_sec": round(time.time() - t0, 2)})
    payload = {"returncode": rc, "stdout": out[-4000:], "stderr": errout[-1000:],
               "audit_id": audit_id, "whitelisted": True}
    print("[CLAUDE_INTERFACE] exec rc=%s dur=%.1fs audit=%s"
          % (rc, time.time() - t0, audit_id))
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
