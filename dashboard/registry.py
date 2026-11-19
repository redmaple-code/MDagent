# -*- coding: utf-8 -*-
"""
看板作业注册表(2026-08-29 数据根外置)
======================================
替代"目录扫描冒充发现机制":作业(本地或超算)启动时登记,看板 registry 优先、
目录扫描仅作兼容兜底。原子写仿 kit harness/core/bus.py:flock + tmp + os.replace,
加载失败静默降级为空表(看板因此可回退纯扫描模式)。
"""
import copy
import fcntl
import hashlib
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mcp"))
import paths

VALID_LOG_FORMATS = ("lammps-thermo-8col", "lammps-thermo-7col")
VALID_PID_SOURCES = ("proc-cwd", "pids-file", "registered", "none")
VALID_LOCATIONS = ("local", "hpc")
VALID_SCHEDULERS = ("slurm", "pbs")
# cluster/jobid 会拼进远端 shell 命令与本地缓存文件名:白名单字符集,防注入与路径逃逸;
# 首字符禁 '-'——shlex.quote 防 shell 不防 CLI 选项解析(`qstat -a` 会列全部作业毒化状态)
SAFE_NAME_RE = re.compile(r"^[0-9A-Za-z_.][0-9A-Za-z_.-]*$")
CASE_ID_RE = re.compile(r"^[\w.-]+$")  # 与 mcp/pipeline_check.py 同口径(五轮对抗 XSS)
SAFE_NAME_CHARS = re.compile(r"[^0-9A-Za-z_.-]")  # 取反版,供文件名清洗 sub 用


def hpc_cache_basename(cluster, jobid):
    """hpc_cache 文件名(采集器与看板必须同口径):清洗名 + 原名 sha1 短缀。
    纯清洗会碰撞(`.foo`↔`foo`、`("a_b","c")`↔`("a","b_c")` 分隔符歧义),
    碰撞条目互相覆盖缓存、看板张冠李戴(2026-08-29 二轮对抗);hash 取自未清洗原名,确定性去歧。"""
    def _safe(s):
        return SAFE_NAME_CHARS.sub("_", str(s)).lstrip(".") or "x"
    digest = hashlib.sha1(("%s\x00%s" % (cluster, jobid)).encode("utf-8")).hexdigest()[:8]
    return "%s_%s.%s" % (_safe(cluster), _safe(jobid), digest)


def _lock_path():
    return registry_file() + ".lock"


def registry_file():
    return paths.registry_file()


def _locked(fn, exclusive=True):
    os.makedirs(os.path.dirname(_lock_path()), exist_ok=True)
    fd = os.open(_lock_path(), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        return fn()
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _load_raw():
    try:
        with open(registry_file(), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("jobs"), list):
            return data
    except Exception:
        pass
    return {"version": 1, "jobs": []}


def load():
    return _locked(_load_raw, exclusive=False)


def _commit(data):
    data["version"] = 1
    data["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = registry_file() + ".tmp.%d" % os.getpid()
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, registry_file())
    except Exception:
        try:
            os.unlink(tmp)  # 崩溃不留 .tmp 残件
        except OSError:
            pass
        raise


def _validate(entry):
    if not isinstance(entry, dict):
        raise ValueError("registry 条目必须是对象")
    for k in ("case_id", "prod_dir", "log_file"):
        if not entry.get(k) or not isinstance(entry.get(k), str):
            raise ValueError("registry 条目缺必填字段: %s" % k)
    cid = entry["case_id"]
    # 白名单(与 pipeline_check.CASE_ID_RE 同口径):HTML 元字符会经看板 innerHTML
    # 原样渲染成存储型 XSS(五轮对抗),路径分隔符则用于 snap 文件名逃逸
    if not CASE_ID_RE.match(cid) or cid in (".", ".."):
        raise ValueError("case_id 含非法字符(只允许字母数字._-): %r" % cid)
    if entry.get("log_format", "lammps-thermo-8col") not in VALID_LOG_FORMATS:
        raise ValueError("未知 log_format: %r" % entry.get("log_format"))
    if entry.get("pid_source", "proc-cwd") not in VALID_PID_SOURCES:
        raise ValueError("未知 pid_source: %r" % entry.get("pid_source"))
    loc = entry.get("location") or {"type": "local"}
    if not isinstance(loc, dict):
        raise ValueError("location 必须是对象,收到: %r" % type(loc).__name__)
    if loc.get("type") not in VALID_LOCATIONS:
        raise ValueError("未知 location.type: %r" % loc.get("type"))
    if loc["type"] == "hpc":
        if not loc.get("jobid"):
            raise ValueError("hpc 条目必须带 location.jobid")
        for k in ("cluster", "jobid"):
            v = loc.get(k)
            if v is not None and not SAFE_NAME_RE.match(str(v)):
                raise ValueError("location.%s 含非法字符(只许 [0-9A-Za-z_.-]): %r" % (k, v))
            if v is not None and not isinstance(v, str):
                # 数值型归一化为 str:存 int 会让下游清洗/re 匹配 TypeError 连坐(二轮对抗)
                loc[k] = str(v)
        sched = loc.get("scheduler") or "slurm"
        if sched not in VALID_SCHEDULERS:
            raise ValueError("未知 location.scheduler: %r" % loc.get("scheduler"))


def upsert(entry, source="register_job"):
    # 先深拷贝再校验:归一化(数值→str)就地改 location,不得污染调用方的 dict(三轮对抗)
    entry = copy.deepcopy(entry)
    _validate(entry)
    entry.setdefault("project", "default")
    entry.setdefault("log_format", "lammps-thermo-8col")
    entry.setdefault("pid_source", "proc-cwd")
    entry.setdefault("pids", [])
    entry.setdefault("location", {"type": "local"})
    entry["registered_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    entry["registered_by"] = source

    def _do():
        data = _load_raw()
        jobs = [j for j in data["jobs"] if j.get("case_id") != entry["case_id"]]
        jobs.append(entry)
        data["jobs"] = jobs
        _commit(data)
    return _locked(_do)


def remove(case_id):
    removed = []

    def _do():
        data = _load_raw()
        before = data["jobs"]
        data["jobs"] = [j for j in before if j.get("case_id") != case_id]
        removed.append(len(before) - len(data["jobs"]))
        _commit(data)
    _locked(_do)
    return removed[0] > 0
