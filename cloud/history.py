#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""会话历史:每用户每项目一个 JSONL,追加写。注入最近 N 轮到信封。"""
import json
import os
import re
import threading
import time

import paths

# 归档/恢复/追加同进程串行化(FastAPI 线程池 + runner 同进程):
# 防"预检通过→搬运落地"窗口里 append 重建会话文件被 os.replace 静默覆盖
_io_lock = threading.Lock()

HISTORY_TURNS = 20
# 信封字节预算:只按条数截断会被 20 轮超长消息撑爆模型上下文(红队ζ B7b)
MAX_ENVELOPE_BYTES = 192 * 1024
PROJECT_RE = re.compile(r"^[A-Za-z0-9_一-鿿-]{1,64}$")


def valid_project(name):
    # _archived 是归档保留名(会话/项目空间两侧的归档目录),不许当项目名
    return bool(PROJECT_RE.match(name or "")) and name != "_archived"


def _file(username, project):
    return os.path.join(paths.user_conversations_dir(username),
                        "%s.jsonl" % project)


def append(username, project, role, text):
    os.makedirs(paths.user_conversations_dir(username), exist_ok=True)
    rec = {"ts": time.time(), "role": role, "text": text}
    with _io_lock:
        with open(_file(username, project), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def recent(username, project, turns=HISTORY_TURNS):
    p = _file(username, project)
    rows = []
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):   # 污染行(合法 JSON 非 dict)不炸信封
                    rows.append(rec)
    except OSError:
        return rows   # 归档恰好搬走/权限:当作读到目前所得,不 500
    return rows[-turns * 2:]


def list_projects(username):
    d = paths.user_conversations_dir(username)
    if not os.path.isdir(d):
        return []
    return sorted(f[:-6] for f in os.listdir(d) if f.endswith(".jsonl"))


def _archived_conv_dir(username):
    return os.path.join(paths.user_conversations_dir(username), "_archived")


def _archived_proj_dir(username):
    return os.path.join(paths.user_projects_dir(username), "_archived")


_TS_RE = re.compile(r"\.\d{8}_\d{6}(-\d+)?$")


def _ts_of(name):
    """"proj.20260920_120000-3[.jsonl]" → ("20260920_120000", 3);无时间戳 None。
    排序/取最新一律用这个键:裸字符串字典序会被 "-N" 后缀与 ".jsonl" 后缀
    搅乱(带 -1 的会话档会排到无后缀档前面,restore 取到旧快照)。"""
    if name.endswith(".jsonl"):
        name = name[:-6]
    m = _TS_RE.search(name)
    if not m:
        return None
    ts = m.group(0)[1:]
    if "-" in ts:
        base, _, n = ts.rpartition("-")
        return base, int(n)
    return ts, 0


def _fresh_ts(username, project):
    """该项目的归档时间戳:同秒重复归档时加 -N 后缀,绝不覆盖旧快照
    (对话侧 os.replace 会静默吞掉旧档,项目侧 os.rename 会 500,都要防)。"""
    ts = time.strftime("%Y%m%d_%H%M%S")
    taken = set()
    for d in (_archived_conv_dir(username), _archived_proj_dir(username)):
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            if not f.startswith(project + "."):
                continue
            name = f[:-6] if f.endswith(".jsonl") else f
            m = _TS_RE.search(name)
            if m:
                taken.add(m.group(0)[1:])
    cand, i = ts, 1
    while cand in taken:
        cand = "%s-%d" % (ts, i)
        i += 1
    return cand


def archive(username, project):
    """归档项目:会话 jsonl 与项目空间移入 _archived/(可恢复的移动,不是删除)。
    项目名不含点,归档名 <project>.<ts> 解析无歧义。整段持锁,项目侧失败时
    回滚已搬走的会话,不留半归档态。"""
    if not valid_project(project):
        raise ValueError("project 名非法: %s" % project)
    with _io_lock:
        ts = _fresh_ts(username, project)
        moved = []
        cf = _file(username, project)
        conv_archived = None
        if os.path.exists(cf):
            ad = _archived_conv_dir(username)
            os.makedirs(ad, exist_ok=True)
            conv_archived = os.path.join(ad, "%s.%s.jsonl" % (project, ts))
            os.replace(cf, conv_archived)
            moved.append("conversations")
        pd = os.path.join(paths.user_projects_dir(username), project)
        if os.path.isdir(pd):
            ap = _archived_proj_dir(username)
            os.makedirs(ap, exist_ok=True)
            try:
                os.rename(pd, os.path.join(ap, "%s.%s" % (project, ts)))
            except OSError:
                if conv_archived:   # 回滚:会话搬回原位,不留半归档态
                    os.replace(conv_archived, cf)
                raise
            moved.append("projects")
        if not moved:
            raise FileNotFoundError(
                "项目不存在(无可归档的会话或项目空间): %s" % project)
        return {"project": project, "archived_ts": ts, "moved": moved}


def restore(username, project):
    """恢复该项目最近一次归档(会话/项目空间各取最新一份);同名已存在则拒绝,不覆盖。
    整段持锁 + 预检两侧目标 + 项目侧失败回滚,不产生"会话已回/项目仍归档"的半恢复态。"""
    if not valid_project(project):
        raise ValueError("project 名非法: %s" % project)
    with _io_lock:
        ad = _archived_conv_dir(username)
        conv_src = None
        if os.path.isdir(ad):
            cands = [f for f in os.listdir(ad)
                     if f.startswith(project + ".") and f.endswith(".jsonl")
                     and _ts_of(f)]
            cands.sort(key=_ts_of)
            if cands:
                if os.path.exists(_file(username, project)):
                    raise FileExistsError(
                        "会话已存在: %s(先归档或换名再恢复)" % project)
                conv_src = os.path.join(ad, cands[-1])
        ap = _archived_proj_dir(username)
        proj_src = None
        if os.path.isdir(ap):
            cands = [d for d in os.listdir(ap)
                     if d.startswith(project + ".") and _ts_of(d)
                     and os.path.isdir(os.path.join(ap, d))]
            cands.sort(key=_ts_of)
            if cands:
                dst = os.path.join(paths.user_projects_dir(username), project)
                if os.path.exists(dst):
                    raise FileExistsError("项目空间已存在: %s" % project)
                proj_src = os.path.join(ap, cands[-1])
        if conv_src is None and proj_src is None:
            raise FileNotFoundError("该项目没有归档: %s" % project)
        restored = []
        if conv_src:
            os.replace(conv_src, _file(username, project))
            restored.append("conversations")
        if proj_src:
            try:
                os.rename(proj_src, os.path.join(
                    paths.user_projects_dir(username), project))
            except OSError:
                if conv_src:   # 回滚:会话搬回归档,不留半恢复态
                    os.replace(_file(username, project), conv_src)
                raise
            restored.append("projects")
        return {"project": project, "restored": restored}


def archived(username):
    """归档清单(按项目去重;每侧取 _ts_of 键最大的最新档,listdir 顺序无关)。"""
    seen = {}
    for side, d in (("conv", _archived_conv_dir(username)),
                    ("proj", _archived_proj_dir(username))):
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            name = f[:-6] if f.endswith(".jsonl") else f
            m = _TS_RE.search(name)
            if not m:
                continue
            if side == "proj" and not os.path.isdir(os.path.join(d, f)):
                continue
            k = _ts_of(f)
            ent = seen.setdefault(name[:m.start()], {})
            if side not in ent or k > ent[side][0]:
                ent[side] = (k, m.group(0)[1:])
    out = [{"project": p, "ts": max(v.values())[1],
            "has_conversations": "conv" in v,
            "has_projects": "proj" in v}
           for p, v in seen.items()]
    return sorted(out, key=lambda e: e["project"])


STRAT_BUDGET = 8 * 1024


def _active_strategy_rules():
    """共享进化层里 status=active 的策略规则(全员沉淀,注入每个信封)。"""
    try:
        p = os.path.join(paths.shared_evolution_dir(),
                         "behavior_strategies.json")
        with open(p, encoding="utf-8") as f:
            ss = json.load(f).get("strategies", [])
    except Exception:
        return []
    return [s["rule"].strip() for s in ss
            if s.get("status") == "active" and s.get("rule")]


POLICY_MAX_CHARS = 12000
TASK_DISCIPLINE = (
    "[任务组织纪律·系统内置]\n"
    "接到新的计算/仿真/复现类任务时:先在用户本地开放目录下建独立任务文件夹"
    "(按主题分类命名,如 water-sim),并在文件夹内创建并持续维护 mdagent.md,"
    "记录任务目标、关键参数、文件清单与进度;续做该任务时先读它的 mdagent.md 再继续。"
    "零散问答/闲聊不必建文件夹。\n[/任务组织纪律]"
)


def build_envelope(username, project, message, policy=""):
    hist = recent(username, project)
    parts = ["（API 会话,直接回答本轮问题,不要开场白,不要重复问候。）"]
    if hist:
        # 字节预算:超预算时从最早的开始丢,保最近轮次
        kept, budget = [], MAX_ENVELOPE_BYTES
        for r in reversed(hist):
            size = len(r["text"].encode("utf-8"))
            if size > budget:
                break
            kept.insert(0, r)
            budget -= size
        if kept:
            lines = ["[会话历史·最近%d轮]" % ((len(kept) + 1) // 2)]
            for r in kept:
                who = "用户" if r["role"] == "user" else "助手"
                lines.append("%s: %s" % (who, r["text"]))
            lines.append("[/会话历史]")
            parts.append("\n".join(lines))
    rules = _active_strategy_rules()
    if rules:
        kept, budget = [], STRAT_BUDGET
        for r in rules:
            r = r[:500]
            nb = len(r.encode("utf-8")) + 2
            if nb > budget:
                break
            kept.append("- " + r)
            budget -= nb
        if kept:
            parts.append("[行为策略·共享进化层·全员沉淀]\n遵守以下沉淀规则:\n"
                         + "\n".join(kept) + "\n[/行为策略]")
    policy = (policy or "").strip()
    if policy:
        parts.append(TASK_DISCIPLINE)
        parts.append("[用户本地策略 mdagent.md·每轮自动注入,不属于对话历史]\n"
                     + policy[:POLICY_MAX_CHARS] + "\n[/用户本地策略]")
    else:
        parts.append(TASK_DISCIPLINE)
    parts.append("[本轮用户消息]\n" + message)
    return "\n\n".join(parts)
