#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每用户一次性 headless dsh 执行器。
进程身份:setpriv 到 mu_<username>(MDAGENT_SKIP_UNIX=1 时不降权,本地测试);
env 锚定 MDSIM_DATA_ROOT/DSH_HOME 到用户空间,cwd=用户项目目录;
调 <code>/node_modules/@deepseek-ai/dsh/lib/bin.js --profile headless --patch <server patch>。
输出契约:headless 把最终 assistant 消息打到 stdout,原样作 reply。"""
import asyncio
import glob
import json
import os
import pwd
import subprocess
import threading
import time

import bridge
import history
import paths
import provision

NODE_BIN = os.environ.get("MDAGENT_NODE", "/opt/node22/bin/node")
DSH_BIN_REL = os.path.join("node_modules", "@deepseek-ai", "dsh", "lib", "bin.js")
PATCH_REL = "md-sim-assistant.headless.patch.yml"
MAX_REPLY_BYTES = 256 * 1024
# job_id -> 运行中的 agent 子进程。绝不放进 job dict:_persist 对 job 做
# json.dump,Process 不可序列化,任务完成落盘会炸(2026-09-20 实测踩过)。
_PROCS = {}
STREAM_MAX_CHARS = 64 * 1024  # partial 只留尾窗,防爆 job JSON

# per-run patch:仅当用户客户端在线时挂本地文件桥,token 随 job 吊销
# token 走 env 而非 argv:/proc/<pid>/cmdline 全用户可读,environ 仅属主可读(红队η)
LOCALFS_PATCH_TEMPLATE = """# 自动生成 per-run patch(本次 job 专用,勿复用)
- insert:
    - id: mcp-localfs
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: localfs
        transport: stdio
        command: '/usr/bin/python3.11'
        args:
          - '{code}/mcp/localfs_mcp.py'
          - '{username}'
        env:
          LOCALFS_RUN_TOKEN: '{run_token}'
        failOnStartupError: false
        toolCallTimeoutMs: 120000
"""


def _skip_unix():
    return os.environ.get("MDAGENT_SKIP_UNIX") == "1"


def _extract_text(row):
    """从 session.jsonl 行提取本次新增的 assistant 可见文本(非文本行返 "")。
    text-chunks 是 packChunks 打包行(texts 数组);assistant/chunk 是未打包单 delta。
    assistant/message 是拼接全文,若用于流式会重复,故不取。"""
    t = row.get("type")
    data = row.get("data") or {}
    if t == "text-chunks":
        return "".join(data.get("texts") or [])
    if t == "assistant/chunk":
        chunk = data.get("chunk") or {}
        if chunk.get("type") == "text-delta":
            return chunk.get("text") or ""
    return ""


def _extract_thinking(row):
    """reasoning(思维链)增量:reasoning-chunks 打包行 / reasoning-delta 未打包。
    单独走 job["thinking"],客户端暗色另渲,不与正文 partial 混排。"""
    t = row.get("type")
    data = row.get("data") or {}
    if t == "reasoning-chunks":
        return "".join(data.get("texts") or [])
    if t == "assistant/chunk":
        chunk = data.get("chunk") or {}
        if chunk.get("type") == "reasoning-delta":
            return chunk.get("text") or ""
    return ""


def _activity_of(row):
    """工具调用行 → 状态条活动提示;其余返 ""。"""
    if row.get("type") == "tool/call":
        name = (row.get("data") or {}).get("name") or "tool"
        return "🔧 %s" % name
    return ""


def _mtime(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return 0


def _stream_tail(username, t0, job, stop, holder):
    """tail 本 job 新建的 session.jsonl.zstd,把增量文本写进 job["partial"]、
    工具活动写进 job["activity"]。展示层易失态,权威 reply 仍是 dsh stdout;
    任何失败静默返回(=退化到无流式的旧行为)。"""
    try:
        pat = os.path.join(paths.user_dsh_home(username), "sessions",
                           "*", "*", "session.jsonl.zstd")
        path = None
        for _ in range(100):  # session 文件在 run 启动数秒内出现
            if stop.is_set():
                return
            cands = [p for p in glob.glob(pat) if _mtime(p) >= t0 - 1]
            if cands:
                path = max(cands, key=_mtime)
                break
            time.sleep(0.3)
        if not path:
            return
        proc = subprocess.Popen(
            [NODE_BIN, os.path.join(paths.code_dir(), "cloud", "zstd_tail.cjs"),
             path], stdout=subprocess.PIPE, text=True, errors="replace",
            stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        holder["proc"] = proc
        try:
            for line in proc.stdout:
                if stop.is_set():
                    break
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                text = _extract_text(row)
                if text:
                    job["partial"] = (job.get("partial", "")
                                      + text)[-STREAM_MAX_CHARS:]
                think = _extract_thinking(row)
                if think:
                    job["thinking"] = (job.get("thinking", "")
                                       + think)[-STREAM_MAX_CHARS:]
                act = _activity_of(row)
                if act:
                    job["activity"] = act
        finally:
            holder["proc"] = None
            proc.terminate()
    except Exception:
        pass


def _write_localfs_patch(username, run_token):
    """写 per-run patch 到用户 dsh-home(mu_ 可读,仅本次 job 有效)。
    os.open 带 600 一步成型,无 open→chmod 之间的世界可读微窗口(红队ι#5)。"""
    content = LOCALFS_PATCH_TEMPLATE.format(
        code=paths.code_dir(), username=username, run_token=run_token)
    p = os.path.join(paths.user_dsh_home(username), "localfs.patch.yml")
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    if not _skip_unix():
        # 本进程是 root,dsh 以 mu_ 身份读 patch,必须 chown 给租户
        import grp
        os.chown(p, pwd.getpwnam(provision.unix_name(username)).pw_uid,
                 grp.getgrnam(provision.SHARE_GROUP).gr_gid)
    return p


def _build_argv(username, project, envelope, extra_patch=None):
    code = paths.code_dir()
    argv = [NODE_BIN, os.path.join(code, DSH_BIN_REL),
            "--profile", "headless",
            "--patch", os.path.join(code, PATCH_REL)]
    if extra_patch:
        argv += ["--patch", extra_patch]
    argv.append(envelope)
    if _skip_unix():
        return argv
    uid = pwd.getpwnam(provision.unix_name(username)).pw_uid
    import grp
    gid = grp.getgrnam(provision.SHARE_GROUP).gr_gid
    return ["setpriv", "--reuid=%d" % uid, "--regid=%d" % gid,
            "--clear-groups"] + argv


def _build_env(username):
    dsh_home = paths.user_dsh_home(username)
    return {
        "PATH": os.path.dirname(NODE_BIN) + ":/usr/local/bin:/usr/bin:/bin",
        "HOME": dsh_home,
        "MDSIM_DATA_ROOT": paths.user_data_dir(username),
        "DSH_HOME": dsh_home,
        # 工作区内可写,工作区外升权请求 fail-closed(headless 无审批应答者)
        "DSH_PERMISSION_MODE": "workspace-write",
        "LANG": "zh_CN.UTF-8",
    }


def terminate_job(job_id):
    """取消端点用:SIGTERM 该 job 的 agent 进程(死了/没了则不动)。"""
    p = _PROCS.get(job_id)
    if p is not None and p.returncode is None:
        try:
            p.terminate()
        except Exception:
            pass


async def run_chat(job):
    """queue.submit 的 run_fn。返回 reply 文本,副作用:会话历史落盘。"""
    username, project, message = job["user"], job["project"], job["message"]
    proj_dir = os.path.join(paths.user_projects_dir(username), project)
    os.makedirs(proj_dir, exist_ok=True)
    if not _skip_unix():
        # makedirs 以 root 身份建目录,必须 chown 给租户,否则 agent 在自己工作区写不了
        uid = pwd.getpwnam(provision.unix_name(username)).pw_uid
        import grp
        gid = grp.getgrnam(provision.SHARE_GROUP).gr_gid
        os.chown(proj_dir, uid, gid)
        os.chmod(proj_dir, 0o700)
    envelope = history.build_envelope(username, project, message,
                                   job.get("policy") or "")
    # 客户端在线才挂本地文件桥;run token 随本 job 结束吊销
    extra_patch, run_token = None, None
    if bridge.client_online(username):
        run_token = bridge.issue_run_token(username, job["id"])
        extra_patch = _write_localfs_patch(username, run_token)
    argv = _build_argv(username, project, envelope, extra_patch)
    # asyncio 子进程不支持 umask kwarg,用 bash wrapper 设(共享进化库组写需要 002)
    argv = ["bash", "-c", 'umask 002 && exec "$@"', "bash"] + argv
    t0 = time.time()
    stop_tail = threading.Event()
    tail_holder = {}
    threading.Thread(target=_stream_tail,
                     args=(username, t0, job, stop_tail, tail_holder),
                     daemon=True).start()
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=proj_dir, env=_build_env(username),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        _PROCS[job["id"]] = proc
        if job.get("cancel"):
            proc.terminate()   # 取消信号早于起跑:立即止损
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=870)
        except asyncio.TimeoutError:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 10)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
            raise
    finally:
        _PROCS.pop(job["id"], None)
        stop_tail.set()
        tp = tail_holder.get("proc")
        if tp:
            try:
                tp.terminate()
            except Exception:
                pass
        if run_token:
            bridge.revoke_run_token(run_token)
        if extra_patch:
            try:
                os.unlink(extra_patch)  # 含死 token,job 后不留残(红队η)
            except OSError:
                pass
    if job.get("cancel"):
        history.append(username, project, "user", message)
        history.append(username, project, "assistant", "(此任务已被用户打断)")
        return "\u23f9\ufe0f 已按你的要求打断,执行停止在此步"
    reply = out.decode("utf-8", "replace")[:MAX_REPLY_BYTES].strip()
    if proc.returncode != 0:
        tail = err.decode("utf-8", "replace")[-1500:].strip()
        raise RuntimeError("dsh 退出码 %d: %s" % (proc.returncode, tail or reply[-500:]))
    if not reply:
        raise RuntimeError("dsh 无输出: " + err.decode("utf-8", "replace")[-500:])
    history.append(username, project, "user", message)
    history.append(username, project, "assistant", reply)
    return reply
