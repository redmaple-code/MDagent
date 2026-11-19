#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""看板作业登记 CLI(2026-08-29 数据根外置)
================================================
本地或超算作业启动时由启动器/人工调用,把作业写进 mdsim-data/state/registry.json。
看板 registry 优先、目录扫描仅兼容兜底。登记失败只应 warn,绝不阻断作业本身。

用法:
  python3 register_job.py --case-id run1 --prod-dir /abs/path --log-file log.lammps
  python3 register_job.py --case-id run2 ... --log-format lammps-thermo-7col --project langmuir2026
  python3 register_job.py --case-id hpc1 ... --location hpc --cluster lx --scheduler slurm --jobid 12345
  python3 register_job.py --case-id run1 --unregister
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "dashboard"))
sys.path.insert(0, os.path.join(HERE, "..", "mcp"))

import registry


def _build_entry(a):
    entry = {
        "case_id": a.case_id,
        "project": a.project,
        "prod_dir": os.path.abspath(a.prod_dir),
        "log_file": a.log_file,
        "log_format": a.log_format,
        "pid_source": a.pid_source,
        "pids": a.pids or [],
    }
    if a.location == "hpc":
        loc = {"type": "hpc", "jobid": a.jobid}
        if a.cluster:
            loc["cluster"] = a.cluster
        if a.scheduler:
            loc["scheduler"] = a.scheduler
        entry["location"] = loc
    else:
        entry["location"] = {"type": "local"}
    return entry


def main(argv=None):
    p = argparse.ArgumentParser(description="看板作业登记(registry 优先发现机制)")
    p.add_argument("--case-id", required=True)
    p.add_argument("--prod-dir", help="生产目录(本地绝对路径; hpc 条目记远端绝对路径)")
    p.add_argument("--log-file", help="相对 prod_dir 的日志文件名")
    p.add_argument("--log-format", default="lammps-thermo-8col",
                   choices=registry.VALID_LOG_FORMATS)
    p.add_argument("--project", default="default")
    p.add_argument("--location", default="local", choices=registry.VALID_LOCATIONS)
    p.add_argument("--cluster", help="hpc: 集群名(对应 md_config.json hpc.clusters 键)")
    p.add_argument("--scheduler", choices=("slurm", "pbs"), help="hpc: 调度器")
    p.add_argument("--jobid", help="hpc: 调度器作业号")
    p.add_argument("--pid-source", default="proc-cwd", choices=registry.VALID_PID_SOURCES)
    p.add_argument("--pids", type=int, nargs="*", help="pid_source=registered 时的进程号")
    p.add_argument("--source", default="register_job.py", help="登记来源标记")
    p.add_argument("--unregister", action="store_true", help="移除该 case_id 的登记")
    a = p.parse_args(argv)

    if a.unregister:
        if registry.remove(a.case_id):
            print("[register_job] 已移除: %s" % a.case_id)
            return 0
        print("[register_job] 未找到登记条目: %s" % a.case_id, file=sys.stderr)
        return 2

    if not a.prod_dir or not a.log_file:
        p.error("登记必须给 --prod-dir 和 --log-file(--unregister 除外)")
    if a.location == "hpc" and not a.cluster:
        p.error("hpc 条目必须给 --cluster(对应 md_config.json hpc.clusters 键)")
    try:
        # P2 修复(2026-09-11 审计): --pids 过滤已死进程,防止登记瞬间进程退出
        # 留下永久幽灵条目(看板 pid_source=registered 时会一直找不到进程)
        if a.pids:
            alive = [p for p in a.pids if os.path.exists("/proc/%d" % p)]
            if not alive and a.pid_source == "registered":
                print("[register_job] 拒绝: --pids 全部已死且 pid_source=registered", file=sys.stderr)
                return 2
            if len(alive) != len(a.pids):
                print("[register_job] warn: %d/%d 个 pid 已死,仅登记存活者"
                      % (len(a.pids) - len(alive), len(a.pids)), file=sys.stderr)
            a.pids = alive
        registry.upsert(_build_entry(a), source=a.source)
    except ValueError as e:
        print("[register_job] 登记被拒: %s" % e, file=sys.stderr)
        return 2
    except OSError as e:
        # registry.json 被目录顶替等 IO 异常:fail 干净,不裸栈(二轮对抗)
        print("[register_job] 登记失败(IO): %s" % e, file=sys.stderr)
        return 2
    print("[register_job] 已登记: %s -> %s" % (a.case_id, registry.registry_file()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
