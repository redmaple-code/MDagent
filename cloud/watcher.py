#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""运行看护 v1:每 INTERVAL 秒对活跃 run 经 relay.call_node 拉节点 log.lammps 尾部解析 thermo(step/temp/…)
更新曲线序列,并 md.ps 查存活(exited/lost 即定状态)。只读真日志——不改进程、不碰力场(科研红线)。
v1.1 再接:复用 monitor_agent 的物理异常检测(LOST_ATOMS/温度飙升/冻结)+ CRITICAL/退出唤醒脑子(Mode C)。"""
import asyncio
import math
import os
import re

import runs

INTERVAL = int(os.environ.get("MDAGENT_WATCH_SEC", "30"))
TAIL_BYTES = int(os.environ.get("MDAGENT_WATCH_TAIL", "131072"))

_HDR = re.compile(r'^\s*Step\b', re.I)                 # LAMMPS thermo 表头行
_KEEP = ("temp", "press", "toteng", "e_pair", "pe", "ke", "vol", "density", "enthalpy")


def parse_thermo(text):
    """从 log.lammps 尾部文本解析 thermo 点。多个 run 段的表头会各自重置列名;
    非数字行(Loop time/WARNING/…)结束当前块。返回 [{step,temp,...}]。"""
    cols = None
    pts = []
    for ln in text.splitlines():
        if _HDR.match(ln):
            cols = [c.lower() for c in ln.split()]
            continue
        if cols:
            parts = ln.split()
            if len(parts) != len(cols):
                cols = None
                continue
            try:
                vals = [float(x) for x in parts]
            except ValueError:
                cols = None                    # thermo 块结束
                continue
            row = dict(zip(cols, vals))
            if "step" not in row or not math.isfinite(row["step"]):
                continue
            pt = {"step": int(row["step"])}
            for k in _KEEP:
                # 过滤 nan/inf(LAMMPS 炸机会打印):否则存进 thermo → get_run 序列化(allow_nan=False)500,曲线恰在出事时打不开
                if k in row and math.isfinite(row[k]):
                    pt[k] = round(row[k], 6)
            pts.append(pt)
    return pts


async def _tick(relay):
    for rid in runs.active_ids():
        r = runs.get(rid)
        if not r:
            continue
        node = r["node"]
        # thermo:log.lammps 尾部(相对 cwd,壳子 resolve 后 realpath 囚禁)
        try:
            tgt = await relay.call_node(node, "fs.resolve", {"path": "log.lammps", "cwd": r["cwd"]}, timeout=10)
            tl = await relay.call_node(node, "fs.tail", {"target": tgt, "bytes": TAIL_BYTES}, timeout=10)
            pts = parse_thermo(tl.get("text", "") if isinstance(tl, dict) else "")
            if pts:
                runs.add_thermo(rid, pts)
        except Exception:
            pass
        # 存活:md.ps(keeper 状态文件的三态 running/exited/lost)
        try:
            ps = await relay.call_node(node, "md.ps", {"run_id": rid}, timeout=10)
            st = ps.get("state") if isinstance(ps, dict) else None
            if st in ("exited", "lost"):
                runs.set_state(rid, st, ps.get("exit_code") if isinstance(ps, dict) else None)
        except Exception:
            pass


async def run_forever(relay):
    """app 启动时 create_task 它。单 worker 下与其余 asyncio 端点共用事件循环。"""
    while True:
        try:
            await _tick(relay)
        except Exception:
            pass
        await asyncio.sleep(INTERVAL)
