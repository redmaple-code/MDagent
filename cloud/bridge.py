#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地文件桥:客户端(mdagent_client.py)长轮询取文件操作,云端 agent 经 localfs_mcp 发起。
方向永远是客户端主动拨出(用户机器无公网 IP)。
注册表全在内存:服务重启 = 桥断开,客户端重连即可,无持久状态。
run token:runner 起 job 时签发,随 job 结束吊销,仅 127.0.0.1 可用。"""
import asyncio
import time
import uuid

POLL_MAX_WAIT = 55        # 客户端长轮询上限(秒),须 < nginx 660s
CALL_TIMEOUT = 110        # localfs_mcp 等客户端执行的上限(须 < toolCallTimeoutMs 120s)
ONLINE_WINDOW = 70        # 距上次 poll 不超过此值算"客户端在线"
MAX_OPS_PER_JOB = 200     # 单 job 文件操作累计上限(run token 维度),防慢速无限写(红队η)

_queues = {}       # username -> asyncio.Queue
_polling = set()   # 正在 poll 的 username(每用户只许 1 个 poller)
_results = {}      # op_id -> {"event": Event, "payload": dict|None, "user": str}
_run_tokens = {}   # token -> {"user": str, "job": str, "ops": int}
_last_poll = {}    # username -> ts


def issue_run_token(username, job_id):
    t = uuid.uuid4().hex
    _run_tokens[t] = {"user": username, "job": job_id, "ops": 0}
    return t


def _drain_queue(q):
    items = []
    while True:
        try:
            items.append(q.get_nowait())
        except asyncio.QueueEmpty:
            return items


def revoke_run_token(token):
    r = _run_tokens.pop(token, None)
    if not r:
        return
    # 清掉该 job 残留在队列里的 op:不清的话,陈旧 op 会活到客户端下次
    # 重连时被投递(甚至跨 job),用户在新会话里批了上个 job 的写(红队ι)
    q = _queues.get(r["user"])
    if q:
        for op in _drain_queue(q):
            if op.get("job") != r["job"] and not _op_stale(op):
                q.put_nowait(op)


def run_token_user(token):
    r = _run_tokens.get(token or "")
    return r["user"] if r else None


def count_op(token):
    """累计计数:超 MAX_OPS_PER_JOB 返回 False(累计,非队列深度——客户端 stall 场景也封顶)。"""
    r = _run_tokens.get(token or "")
    if not r:
        return False
    r["ops"] += 1
    return r["ops"] <= MAX_OPS_PER_JOB


def client_online(username):
    return (time.time() - _last_poll.get(username, 0)) < ONLINE_WINDOW


def _op_stale(op):
    """op 过期(发出超过 CALL_TIMEOUT,客户端再收到也来不及回)或所属 job 已结束。"""
    if time.time() - op.get("ts", 0) > CALL_TIMEOUT:
        return True
    job = op.get("job")
    return not job or all(r["job"] != job for r in _run_tokens.values())


async def poll(username, wait, is_disconnected=None):
    """长轮询:有 op 立即返回,否则等到 wait 秒返回 None。每用户同时只许一个 poller。
    is_disconnected:可选协程(传 request.is_disconnected),客户端断线 1s 内感知,
    避免 op 投进死连接丢失 + _polling 占位挡重连(红队θ)。
    投递前丢陈旧/死 job 的 op(红队ι);重复 poller 不刷 _last_poll 且睡 1s(防空转+在线续命)。"""
    if username in _polling:
        await asyncio.sleep(1)
        return None
    _polling.add(username)
    _last_poll[username] = time.time()
    q = _queues.setdefault(username, asyncio.Queue())
    try:
        deadline = time.time() + min(wait, POLL_MAX_WAIT)
        get_task = asyncio.ensure_future(q.get())
        try:
            while True:
                done, _ = await asyncio.wait({get_task}, timeout=1.0)
                if get_task in done:
                    op = get_task.result()
                    if _op_stale(op):
                        get_task = asyncio.ensure_future(q.get())
                        continue  # 丢弃陈旧 op,继续等到 deadline
                    return op
                if time.time() >= deadline:
                    return None
                if is_disconnected is not None and await is_disconnected():
                    return None
        finally:
            if not get_task.done():
                get_task.cancel()  # Queue.get 取消安全,item 不丢
    finally:
        _polling.discard(username)
        _last_poll[username] = time.time()


def requeue(username, op):
    """op 已取出但连接已死(响应送不到)时放回队列,等客户端重连再取。
    陈旧/死 job 的 op 由 poll 投递前丢弃,这里不拦。"""
    _queues.setdefault(username, asyncio.Queue()).put_nowait(op)


async def call(token, username, op, args):
    """localfs_mcp 经 /v1/bridge/call 调入。返回 {"ok":bool, ...}。
    计数在确认在线后才扣(离线返错不烧配额,红队ι#3)。"""
    if not client_online(username):
        return {"ok": False, "error": "客户端不在线(用户没跑 mdagent_client.py 或刚断开)"}
    if not count_op(token):
        return {"ok": False, "error": "单 job 文件操作数超限(累计 %d 次)" % MAX_OPS_PER_JOB}
    q = _queues.setdefault(username, asyncio.Queue())
    if q.qsize() >= MAX_OPS_PER_JOB:
        return {"ok": False, "error": "单轮文件操作排队超限"}
    op_id = uuid.uuid4().hex
    job = (_run_tokens.get(token) or {}).get("job")
    ev = asyncio.Event()
    _results[op_id] = {"event": ev, "payload": None, "user": username}
    await q.put({"id": op_id, "op": op, "args": args,
                 "job": job, "ts": time.time()})
    try:
        await asyncio.wait_for(ev.wait(), timeout=CALL_TIMEOUT)
    except asyncio.TimeoutError:
        _results.pop(op_id, None)
        return {"ok": False, "error": "客户端 %ds 未响应(可能掉线或用户未批准)" % CALL_TIMEOUT}
    r = _results.pop(op_id, None)
    return (r or {}).get("payload") or {"ok": False, "error": "结果丢失"}


def submit_result(username, op_id, payload):
    r = _results.get(op_id)
    if not r or r["user"] != username:
        return False
    r["payload"] = payload
    r["event"].set()
    return True
