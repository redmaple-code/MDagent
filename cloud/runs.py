#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发车 run 跟踪(监控 v1):gate/exec 发车即登记,watcher 拉节点 thermo 更新曲线/状态,/v1/runs 读。
v1 纯内存(重启丢跟踪;正在跑的模拟不受影响,节点 keeper 托管,重启后新发车重新登记)——
持久化 + 从节点 hello.runs 恢复留 v1.1。曲线点按 step 去重、上限 MAX_THERMO 防内存膨胀。"""
import threading
import time

_lock = threading.Lock()
_runs = {}   # run_id -> {run_id,user,node,cwd,log,started,state,exit_code,thermo[],last_step,last_seen}
MAX_THERMO = int(__import__("os").environ.get("MDAGENT_RUN_MAX_THERMO", "3000"))   # 每 run 曲线点上限
MAX_RUNS = int(__import__("os").environ.get("MDAGENT_MAX_RUNS", "500"))            # _runs 条目上限(防历史累积无界)


def register(run_id, user, node, cwd, log=None):
    """gate/exec 发车成功后登记。log 缺省 = cwd/log.lammps(LAMMPS 默认 thermo 落点)。"""
    if not run_id:
        return None
    with _lock:
        if len(_runs) >= MAX_RUNS:
            # 超上限:淘汰最老的已结束 run(running 不动),防历史发车累积无界吃内存
            done = sorted((x for x in _runs.values() if x["state"] != "running"),
                          key=lambda x: x["started"])
            for x in done[:len(_runs) - MAX_RUNS + 1]:
                _runs.pop(x["run_id"], None)
        _runs[run_id] = {
            "run_id": run_id, "user": user, "node": node, "cwd": cwd,
            "log": log or (cwd.rstrip("/") + "/log.lammps"),
            "started": time.time(), "state": "running", "exit_code": None,
            "thermo": [], "last_step": -1, "last_seen": time.time(),
        }
        return dict(_runs[run_id])


def active_ids():
    with _lock:
        return [r["run_id"] for r in _runs.values() if r["state"] == "running"]


def get(run_id):
    with _lock:
        r = _runs.get(run_id)
        return dict(r) if r else None


def list_for(user):
    """用户的 run 列表(不含 thermo 大数组,只给点数)。"""
    with _lock:
        out = []
        for r in _runs.values():
            if r["user"] != user:
                continue
            m = {k: v for k, v in r.items() if k != "thermo"}
            m["points"] = len(r["thermo"])
            out.append(m)
        return sorted(out, key=lambda x: x["started"], reverse=True)


def owner(run_id):
    with _lock:
        r = _runs.get(run_id)
        return r["user"] if r else None


def add_thermo(run_id, points):
    """points: [{step,temp,...}];按 step 严格递增去重追加。"""
    with _lock:
        r = _runs.get(run_id)
        if not r:
            return
        for p in points:
            s = p.get("step")
            if isinstance(s, int) and s > r["last_step"]:
                r["thermo"].append(p)
                r["last_step"] = s
        if len(r["thermo"]) > MAX_THERMO:
            r["thermo"] = r["thermo"][-MAX_THERMO:]
        r["last_seen"] = time.time()


def set_state(run_id, state, exit_code=None):
    if state not in ("running", "exited", "lost"):
        state = "lost"                       # LOW-6:状态归一到白名单,杜绝节点回传字符串注入前端
    with _lock:
        r = _runs.get(run_id)
        if not r:
            return
        r["state"] = state
        if exit_code is not None:
            try:
                r["exit_code"] = int(exit_code)   # LOW-6:强制整数,节点回传的 exit_code 不进前端 innerHTML 原样
            except (ValueError, TypeError):
                r["exit_code"] = None
        r["last_seen"] = time.time()
