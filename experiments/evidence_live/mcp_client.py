#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""MCP stdio 客户端 —— 以真实 JSON-RPC 进程驱动 md_mcp 服务器。

每次 tools/call 都把 {name, ts(微秒唯一), ok, returncode} 追加进
<数据根>/events/toolcalls.jsonl——这是真实调用事实的审计账,
decision-record 里的 tool:<name>:<ts> 证据即对此核验。
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_ROOT = os.path.join(os.path.dirname(os.path.dirname(HERE)))  # MDagent-src


class McpClient:
    @classmethod
    def start(cls, data_root, kit_base, log_path=None):
        obj = cls.__new__(cls)
        obj.log_path = log_path or os.path.join(data_root, "events",
                                                "toolcalls.jsonl")
        os.makedirs(os.path.dirname(obj.log_path), exist_ok=True)
        env = dict(os.environ)
        env["MDSIM_DATA_ROOT"] = data_root
        env["MDSIM_KIT_BASE"] = kit_base
        env["PYTHONIOENCODING"] = "utf-8"
        obj.proc = subprocess.Popen(
            [sys.executable, os.path.join(SRC_ROOT, "mcp", "md_mcp.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=open(os.path.join(data_root, "mcp_server.stderr.log"),
                        "w", encoding="utf-8"),
            env=env, cwd=SRC_ROOT, text=True, encoding="utf-8")
        obj._id = 0
        obj._rpc("initialize", {"protocolVersion": "2024-11-05",
                                "capabilities": {},
                                "clientInfo": {"name": "evidence-live",
                                               "version": "1.0"}})
        obj._notify("notifications/initialized", {})
        return obj

    def _notify(self, method, params):
        """JSON-RPC 通知:无 id 无应答,只发不等。"""
        self.proc.stdin.write(json.dumps(
            {"jsonrpc": "2.0", "method": method, "params": params},
            ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def _rpc(self, method, params, timeout=420):
        self._id += 1
        rid = self._id
        req = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        self.proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("MCP server closed")
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue
            if msg.get("id") == rid:
                if "error" in msg:
                    raise RuntimeError("rpc error: %s" % msg["error"])
                return msg.get("result")
        raise RuntimeError("rpc timeout: %s" % method)

    def call(self, name, args, note=""):
        """真实 tools/call + 审计入账,返回 content 拼接文本解析后的对象。"""
        ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + \
            ".%06d" % (time.time_ns() % 1_000_000 // 1000)
        t0 = time.time()
        try:
            res = self._rpc("tools/call", {"name": name, "arguments": args})
            ok = True
        except Exception as e:
            res = {"error": str(e)}
            ok = False
        text = ""
        payload = None
        for c in (res or {}).get("content") or []:
            if c.get("type") == "text":
                text += c.get("text", "")
        try:
            payload = json.loads(text)
        except Exception:
            payload = {"raw": text[:2000]}
        row = {"name": name, "ts": ts, "args_digest": json.dumps(
            args, ensure_ascii=False, sort_keys=True)[:300],
            "ok": ok, "duration_ms": int((time.time() - t0) * 1000),
            "returncode": (payload or {}).get("returncode"),
            "note": note}
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return payload, ts

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()
