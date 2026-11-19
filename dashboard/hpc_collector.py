# -*- coding: utf-8 -*-
"""HPC 远端作业采集器(2026-08-29 数据根外置)
================================================
对 registry 中 location.type=hpc 的条目,经 ssh 周期采集:
  1) 调度器状态(slurm: squeue/sacct; pbs: qstat) → state/hpc_cache/<cluster>_<jobid>.json
  2) 远端日志尾部 tail -c 64K → state/hpc_cache/<cluster>_<jobid>.log_tail.txt(供看板解析 step/temp)

红线:
  - hpc.enabled=false 时整体不运行(启用属人工决策,POLICY 红线不变)
  - 只读:远端只执行查询/tail,永不写、永不提交作业(hpc_submit.py 仍只做渲染)
  - ssh 失败静默降级为 unreachable,绝不影响本地条目
集群连接:hpc.clusters.<name>{host,user,key_file,password};缺省时退回顶层 ssh 段(单集群)。
"""
import json
import os
import shlex
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "mcp"))

import paths
import registry

TAIL_BYTES = 65536


def _cfg():
    try:
        with open(os.path.join(paths.CODE_ROOT, "mcp", "md_config.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def enabled():
    # 必须显式为 true:字符串 "false" 等 truthy 值不得误启动(人工闸语义)
    return _cfg().get("hpc", {}).get("enabled") is True


_warned_clusters = set()
_hostkey_warned = False


def _cluster_conn(cluster):
    cfg = _cfg()
    clusters = cfg.get("hpc", {}).get("clusters") or {}
    c = clusters.get(cluster or "") or {}
    if not c:
        if clusters and cluster not in _warned_clusters:
            # clusters 段存在但无此集群:回退顶层 ssh 可能张冠李戴,告警一次
            _warned_clusters.add(cluster)
            print("[hpc_collector] 警告: hpc.clusters 无集群 %r,回退顶层 ssh 段" % cluster,
                  file=sys.stderr)
        c = cfg.get("ssh", {}) or {}
    return {"host": c.get("host"), "user": c.get("user"),
            "key_file": c.get("key_file"), "password": c.get("password")}


def _ssh_run(conn, cmd, timeout=45):
    """返回 (stdout, stderr, rc);rc!=0 不抛,由调用方决定语义。"""
    import paramiko
    cli = paramiko.SSHClient()
    try:
        cli.load_system_host_keys()  # 已知主机先核验(二轮对抗:裸 AutoAddPolicy 无 MITM 防护)
    except Exception:
        pass
    global _hostkey_warned
    if not _hostkey_warned:
        _hostkey_warned = True
        print("[hpc_collector] 提示: 未知主机密钥按 TOFU 接受;建议在 ~/.ssh/known_hosts 预置集群指纹",
              file=sys.stderr)
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kw = {"hostname": conn["host"], "username": conn["user"], "timeout": 20,
          # banner/auth 阶段也要限时:对端不回 banner 会让该轮永久挂起(四轮对抗)
          "banner_timeout": 20, "auth_timeout": 20}
    if conn.get("key_file"):
        kw["key_filename"] = conn["key_file"]
    elif conn.get("password"):
        kw["password"] = conn["password"]
    cli.connect(**kw)
    try:
        _in, out, err = cli.exec_command(cmd, timeout=timeout)
        stdout = out.read().decode("utf-8", "replace")
        stderr = err.read().decode("utf-8", "replace")
        return stdout, stderr, out.channel.recv_exit_status()
    finally:
        cli.close()


def _sched_cmd(scheduler, jobid):
    # jobid 来自 registry(外部输入):shlex.quote 防远端 shell 注入;
    # registry._validate 另有白名单,这里是纵深防御。
    # 不加 || true / 管道:rc 必须真实传回(调度器宕机 ≠ 查无作业);表头/多行由 Python 侧过滤。
    q = shlex.quote(str(jobid))
    if scheduler == "pbs":
        # qstat 查在队;作业退出队列后 qstat 报错,退到 qstat -x 查历史(F/X 完成态)——
        # 否则 pbs 完成/失败永远与调度器宕机同形(unknown),slurm 有 sacct 兜底而 pbs 没有
        return "(qstat %s 2>/dev/null || qstat -x %s 2>/dev/null)" % (q, q)
    # slurm: 在队列查 squeue,已退出查 sacct(首行=主作业行,后续 .batch/.extern 步由解析侧忽略)
    return "(squeue -j %s -h -o '%%T %%M %%N' 2>/dev/null; sacct -j %s -n -o State,Elapsed -P 2>/dev/null)" % (q, q)


def _write_atomic(path, text):
    # 写前断言目标仍在 hpc_cache_dir 内:防 cluster/jobid 路径逃逸覆盖本地文件
    base_dir = os.path.realpath(paths.hpc_cache_dir())
    real = os.path.realpath(path)
    if not real.startswith(base_dir + os.sep):
        raise ValueError("hpc_cache 路径逃逸: %r" % path)
    tmp = "%s.tmp.%d" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def collect_one(entry):
    """采集单个 hpc 条目;任何失败都落成 unreachable 记录,不抛异常。"""
    loc = entry.get("location")
    if not isinstance(loc, dict):
        # 手工写坏的 registry(location 是字符串等)不得连坐整轮采集(四轮对抗)
        loc = {}
    cluster = str(loc.get("cluster") or "default")
    jobid = str(loc.get("jobid") or "")
    record = {"case_id": entry.get("case_id"), "cluster": cluster, "jobid": jobid,
              "scheduler": loc.get("scheduler") or "slurm",
              "collected_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    # 文件名走 registry.hpc_cache_basename(清洗+hash 防碰撞,与看板同口径);
    # 远端命令用的是原名+shlex.quote
    base = os.path.join(paths.hpc_cache_dir(), registry.hpc_cache_basename(cluster, jobid))
    try:
        # 白名单复核(registry 写入时已校验,这里纵深防御;坏名字直接落 unreachable)
        for name in (cluster, jobid):
            if not registry.SAFE_NAME_RE.match(name):
                raise ValueError("cluster/jobid 含非法字符: %r" % name)
        conn = _cluster_conn(cluster)
        if not conn.get("host"):
            raise RuntimeError("集群 %s 无 ssh 连接配置" % cluster)
        out, serr, rc = _ssh_run(conn, _sched_cmd(record["scheduler"], jobid))
        lines = [l.strip() for l in out.splitlines() if l.strip()]
        record["scheduler_raw"] = lines[:3]
        record["state"] = _parse_state(record["scheduler"], lines)
        if rc != 0 and not lines:
            # 调度器命令本身失败(宕机/无权限),与"查无此作业"区分开;不再被 || true 吞掉
            record["scheduler_error"] = (serr.strip() or "rc=%d" % rc)[:200]
            record["state"] = "unknown"
        log_path = "%s/%s" % (entry["prod_dir"].rstrip("/"), entry["log_file"])
        tail, terr, trc = _ssh_run(
            conn, "tail -c %d %s 2>/dev/null" % (TAIL_BYTES, shlex.quote(log_path)), timeout=60)
        if trc != 0:
            record["log_tail_error"] = (terr.strip() or "rc=%d" % trc)[:200]
        if tail:
            _write_atomic(base + ".log_tail.txt", tail)
            record["log_tail_bytes"] = len(tail)
    except Exception as e:
        record["state"] = "unreachable"
        record["error"] = str(e)[:200]
        try:
            # 不可达时清旧 tail:看板不得拿陈旧 step/temp 配 unreachable 状态(二轮对抗)
            os.unlink(base + ".log_tail.txt")
        except OSError:
            pass
    try:
        _write_atomic(base + ".json", json.dumps(record, ensure_ascii=False, indent=2))
    except Exception as e:
        # 缓存写失败(含路径逃逸拦截)只影响本条目,绝不连坐整个采集循环
        record["state"] = "unreachable"
        record["error"] = "cache write failed: %s" % str(e)[:160]
        print("[hpc_collector] %s 缓存写失败: %s" % (record.get("case_id"), e), file=sys.stderr)
    return record


def _parse_state(scheduler, lines):
    """只按状态字段判定(节点名等其余字段不参与,防子串误判)。
    slurm: 无 '|' 行=squeue '%T %M %N' 首 token;有 '|' 行=sacct -P,只取首行(主作业行,
    后续 .batch/.extern 步状态不代表作业级状态)。sacct 后缀 '+' 剥掉。
    pbs: qstat 全量输出,表头(Job id 行/虚线行)跳过,数据行状态在倒数第 2 列(S 列)——
    首 token 是 Job ID 不是状态(二轮对抗:token 化曾把 pbs 改废)。"""
    states = []
    if scheduler == "pbs":
        for l in lines:
            parts = l.split()
            if not parts or l.startswith("-"):
                continue  # 虚线分隔行
            # 表头精确匹配("Job id ..."):startswith("job") 会误杀 jobid 以 job 开头的数据行(三轮对抗)
            if parts[0] == "Job" and len(parts) > 1 and parts[1].lower().startswith("id"):
                continue
            if len(parts) >= 2:
                states.append(parts[-2].upper())
    else:
        for l in lines:
            if "|" in l:
                continue
            parts = l.split()
            if parts:
                states.append(parts[0].upper().rstrip("+"))
        sacct_lines = [l for l in lines if "|" in l]
        if sacct_lines:
            # 老式 slurm 的 State 带空格描述("CANCELLED by 0"):只取首词再比对(四轮对抗)
            first = sacct_lines[0].split("|")[0].strip().upper().rstrip("+")
            if first:
                states.append(first.split()[0])
    states = [s for s in states if s]
    if not states:
        return "unknown"
    state_set = set(states)
    for key in ("RUNNING", "PENDING", "COMPLETING", "CONFIGURING"):
        if key in state_set:
            return key.lower()
    for key in ("FAILED", "TIMEOUT", "CANCELLED", "NODE_FAIL", "OUT_OF_MEMORY", "BOOT_FAIL"):
        if key in state_set:
            return "failed"
    if "COMPLETED" in state_set:
        return "completed"
    if scheduler == "pbs":
        if "R" in state_set or "E" in state_set:  # E=exiting(收尾中,仍占用)
            return "running"
        if state_set & {"Q", "H", "W", "T"}:  # 排队/挂起/等待
            return "pending"
        if state_set & {"F", "X"}:  # qstat -x 历史态:F=finished X=expired(成败需 exit code,超出只读采集范围)
            return "completed"
    return "unknown"


def _gc_cache(active):
    """清扫 hpc_cache 里不在活跃集合的缓存(改名迁移孤儿/已 unregister 条目残留)。
    active 是 basename 集合;文件名形态 <base>.json 与 <base>.log_tail.txt。失败静默。"""
    try:
        d = paths.hpc_cache_dir()
        for name in os.listdir(d):
            if name.endswith(".json"):
                base = name[:-5]
            elif name.endswith(".log_tail.txt"):
                base = name[:-13]
            else:
                continue
            if base not in active:
                os.unlink(os.path.join(d, name))
    except Exception:
        pass


def collect_all(entries=None):
    if not enabled():
        return []
    if entries is None:
        entries = [j for j in registry.load()["jobs"]
                   if (j.get("location") or {}).get("type") == "hpc"]
        full_sweep = True
    else:
        # 子集采集不做 GC:active 集合不完整会把别的活跃条目缓存误删(四轮对抗)
        full_sweep = False
    recs = [collect_one(e) for e in entries]
    if full_sweep:
        active = set()
        for e in entries:
            loc = e.get("location") or {}
            if not isinstance(loc, dict):
                continue
            active.add(registry.hpc_cache_basename(str(loc.get("cluster") or "default"),
                                                   str(loc.get("jobid") or "")))
        _gc_cache(active)
    return recs


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description="HPC 远端作业采集(只读)")
    p.add_argument("--loop", type=int, default=0, metavar="SEC",
                   help="常驻轮询间隔(默认 0 = 跑一次就退出)")
    a = p.parse_args(argv)
    if not enabled():
        print("[hpc_collector] hpc.enabled=false,采集器不启动(启用属人工决策)")
        return 0
    while True:
        try:
            recs = collect_all()
        except Exception as e:
            # 单轮采集整体异常(如 registry 读取故障)不终止常驻循环
            print("[hpc_collector] 本轮采集异常: %s" % e, file=sys.stderr)
            recs = []
        for r in recs:
            print("[hpc_collector] %s %s/%s -> %s" % (r["case_id"], r["cluster"], r["jobid"], r["state"]))
        if a.loop <= 0:
            break
        time.sleep(a.loop)
    return 0


if __name__ == "__main__":
    sys.exit(main())
