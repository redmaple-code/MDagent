#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任务队列:全局并发信号量(默认可 RAM 实测后调) + FIFO + 每用户 1 活跃 job。
job 落盘 jobs/<id>.json(tmp+os.replace),进程重启后可恢复看到状态。"""
import asyncio
import json
import os
import time
import uuid

import paths

MAX_CONCURRENT = int(os.environ.get("MDAGENT_MAX_CONCURRENT", "2"))
JOB_TIMEOUT_SEC = int(os.environ.get("MDAGENT_JOB_TIMEOUT", "900"))
MIN_FREE_MB = int(os.environ.get("MDAGENT_MIN_FREE_MB", "300"))

_sem = None
_jobs = {}
_lock = None


def init():
    global _sem, _lock
    _sem = asyncio.Semaphore(MAX_CONCURRENT)
    _lock = asyncio.Lock()


def free_mb():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    # Portable existing dependency when preparing the cloud app on macOS.
    try:
        import psutil
        return int(psutil.virtual_memory().available) // (1024 * 1024)
    except ImportError:
        return 0  # Unknown available memory must never authorize another job.


def _job_file(job_id):
    return os.path.join(paths.jobs_dir(), "%s.json" % job_id)


def _persist(job):
    os.makedirs(paths.jobs_dir(), exist_ok=True)
    tmp = "%s.tmp.%d" % (_job_file(job["id"]), os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(job, f, ensure_ascii=False, indent=1)
    os.chmod(tmp, 0o600)  # job 含对话明文,禁世界可读
    os.replace(tmp, _job_file(job["id"]))


def get_job(job_id):
    j = _jobs.get(job_id)
    if j:
        return j
    p = _job_file(job_id)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except ValueError:
            return None
    return None


def active_job_of(username):
    for j in _jobs.values():
        if j["user"] == username and j["status"] in ("queued", "running"):
            return j
    return None


def position(job_id):
    queued = sorted((j for j in _jobs.values() if j["status"] == "queued"),
                    key=lambda j: j["created_ts"])
    for i, j in enumerate(queued):
        if j["id"] == job_id:
            return i + 1
    return 0


async def submit(username, project, message, run_fn, policy=""):
    """run_fn(job) 协程:真正执行并返回 reply 文本。返回 job dict。"""
    job = {"id": uuid.uuid4().hex[:16], "user": username, "project": project,
           "status": "queued", "created_ts": time.time(),
           "started_ts": None, "finished_ts": None, "reply": None,
           "error": None, "message": message, "policy": policy or "",
           "partial": "", "activity": "", "thinking": ""}  # 流式展示易失态,权威 reply 仍是 stdout
    _jobs[job["id"]] = job
    _persist(job)
    asyncio.get_event_loop().create_task(_run(job, run_fn))
    return job


async def _run(job, run_fn):
    try:
        async with _sem:
            job["status"] = "running"
            job["started_ts"] = time.time()
            _persist(job)
            try:
                reply = await asyncio.wait_for(run_fn(job), JOB_TIMEOUT_SEC)
                job["status"] = "done"
                job["reply"] = reply
            except asyncio.TimeoutError:
                job["status"] = "timeout"
                job["error"] = "超过 %ds 未返回" % JOB_TIMEOUT_SEC
            except Exception as e:
                job["status"] = "error"
                job["error"] = str(e)[:2000]
    finally:
        job["finished_ts"] = time.time()
        _persist(job)
