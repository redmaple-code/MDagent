#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""节点中继:壳子(mdshell)出站连入 /v1/node/ws,租户 dsh 的三个远端提供者经 /v1/world/<node_id>
接到自己的节点。中继是哑的——只按通道号转发文本帧,不解析业务 JSON。

链路帧(壳子 ↔ 中继):"<ch>\\n<payload>"。ch == "$" 是控制通道:
  壳子→中继  hello            {t:'ntf',m:'hello',p:{protocol,shell,sandbox}}  连上第一帧,缓存后回放给每个消费者
  中继→壳子  channel.open     {t:'ntf',m:'channel.open',p:{ch}}
  中继→壳子  channel.close    {t:'ntf',m:'channel.close',p:{ch}}
其它 ch 的 payload 原样进出消费者的 WebSocket(消费者那头就是 dsh-remote-world 的线协议)。
令牌走 WebSocket 子协议 tok.<token>(不进 URL);服务端回选 'mdexp'。"""
import asyncio
import json
import time

from fastapi import WebSocket, WebSocketDisconnect

CONTROL = "$"
SUBPROTOCOL = "mdexp"
PROTOCOL = 1                                      # 当前线协议版本(与 dsh-remote-world/lib/protocol.js 一致)
MIN_PROTOCOL = int(__import__("os").environ.get("MDAGENT_MIN_PROTOCOL", "1"))   # 低于此的壳子拒绝接入


def _protocols(ws: WebSocket):
    return list(ws.scope.get("subprotocols") or [])


def token_from_ws(ws: WebSocket):
    for p in _protocols(ws):
        if p.startswith("tok."):
            return p[4:]
    auth = ws.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def _accept_proto(ws: WebSocket):
    return SUBPROTOCOL if SUBPROTOCOL in _protocols(ws) else None


class NodeLink:
    def __init__(self, node_id, username, ws, hello_raw):
        self.node_id = node_id
        self.username = username
        self.ws = ws
        self.hello_raw = hello_raw
        self.channels = {}          # ch -> consumer WebSocket
        self.seq = 0
        self.lock = asyncio.Lock()  # 多个 world 会话并发写同一条节点链路
        self.connected_ts = time.time()

    async def send(self, text):
        async with self.lock:
            await self.ws.send_text(text)

    def new_channel(self, consumer):
        self.seq += 1
        ch = "c%d" % self.seq
        self.channels[ch] = consumer
        return ch


class _InternalConsumer:
    """call_node 用的进程内消费者:冒充一个 world 侧连接,只把壳子回帧塞进队列。
    relay 对消费者只用 send_text(壳子→消费者)与 close(链路断开时收尾),这里实现这两个即可。"""
    def __init__(self):
        self.queue = asyncio.Queue()

    async def send_text(self, payload):
        await self.queue.put(payload)

    async def close(self, code=None, reason=None):
        await self.queue.put(None)   # 哨兵:唤醒等待方,让它知道通道断了


class Relay:
    def __init__(self):
        self.links = {}  # node_id -> NodeLink
        self._rpc_seq = 0

    def status(self, node_id):
        link = self.links.get(node_id)
        return {"online": link is not None,
                "channels": len(link.channels) if link else 0,
                "connected_ts": link.connected_ts if link else None}

    def node_hello(self, node_id):
        """在线节点上报的 hello 载荷(含 root/platform/sandbox 等),离线返回 None。"""
        link = self.links.get(node_id)
        if not link:
            return None
        try:
            return json.loads(link.hello_raw).get("p") or {}
        except Exception:
            return None

    def node_root(self, node_id):
        """节点壳子的工作区根(--root),用于云端工作区对齐占位。"""
        p = self.node_hello(node_id) or {}
        return p.get("root") or (p.get("shell") or {}).get("root")

    async def call_node(self, node_id, method, params=None, timeout=30.0):
        """云端内部消费者:在节点链路上开一条临时通道,发一条 remote-world RPC(req)并等它的 res/err。
        写门链用它——签票后 md.run、物理门读 restart 魔数(fs.readBytes)/末态(fs.tail)、md.ps/md.kill。
        与 world_session 同机制(new_channel+channel.open/close),只是消费者是进程内队列而非 WebSocket。
        节点离线→RuntimeError;超时/通道断→抛错;成功返回壳子 result(res.r)。"""
        link = self.links.get(node_id)
        if link is None:
            raise RuntimeError("node offline: %s" % node_id)
        consumer = _InternalConsumer()
        ch = link.new_channel(consumer)
        self._rpc_seq += 1
        rpc_id = "cloud-%d" % self._rpc_seq
        loop = asyncio.get_event_loop()
        try:
            await link.send("%s\n%s" % (CONTROL, json.dumps({"t": "ntf", "m": "channel.open", "p": {"ch": ch}})))
            await link.send("%s\n%s" % (ch, json.dumps({"t": "req", "id": rpc_id, "m": method, "p": params or {}})))
            deadline = loop.time() + timeout
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimeoutError("node RPC timeout: %s %s" % (node_id, method))
                payload = await asyncio.wait_for(consumer.queue.get(), timeout=remaining)
                if payload is None:
                    raise RuntimeError("node channel closed during RPC: %s" % method)
                try:
                    msg = json.loads(payload)
                except ValueError:
                    continue
                if msg.get("id") != rpc_id:
                    continue  # 壳子的 hello/stdout/exit 等 ntf 或别的帧,忽略
                if msg.get("t") == "res":
                    return msg.get("r")
                if msg.get("t") == "err":
                    e = msg.get("e") or {}
                    raise RuntimeError("node RPC %s error [%s]: %s" % (method, e.get("code"), e.get("message")))
        finally:
            link.channels.pop(ch, None)
            try:
                await link.send("%s\n%s" % (CONTROL, json.dumps({"t": "ntf", "m": "channel.close", "p": {"ch": ch}})))
            except Exception:
                pass

    async def kick(self, node_id, code, reason):
        link = self.links.pop(node_id, None)
        if link:
            await self._drop(link, code, reason)

    async def _drop(self, link, code, reason):
        try:
            await link.ws.close(code=code, reason=reason)
        except Exception:
            pass
        await self._close_consumers(link, code, reason)

    async def _close_consumers(self, link, code, reason):
        consumers = list(link.channels.values())
        link.channels.clear()
        for c in consumers:
            try:
                await c.close(code=code, reason=reason)
            except Exception:
                pass

    async def node_session(self, ws: WebSocket, node, on_hello=None):
        """壳子链路。第一帧必须是控制通道 hello,否则 4400。同一节点新连接顶掉旧连接(4409)。"""
        await ws.accept(subprotocol=_accept_proto(ws))
        try:
            first = await asyncio.wait_for(ws.receive_text(), timeout=15)
        except Exception:
            await ws.close(code=4400, reason="hello timeout")
            return
        ch, _, payload = first.partition("\n")
        msg = None
        if ch == CONTROL:
            try:
                msg = json.loads(payload)
            except ValueError:
                msg = None
        if not isinstance(msg, dict) or msg.get("m") != "hello":
            await ws.close(code=4400, reason="expected hello")
            return
        proto = (msg.get("p") or {}).get("protocol")
        if not isinstance(proto, int) or proto < MIN_PROTOCOL:
            # 壳子太旧:明确拒绝(4426=需升级),壳子据此提示 mdshell update
            await ws.close(code=4426, reason="protocol %s < min %s; run: mdshell update" % (proto, MIN_PROTOCOL))
            return
        link = NodeLink(node["node_id"], node["username"], ws, payload)
        old = self.links.get(node["node_id"])
        self.links[node["node_id"]] = link
        if old is not None:
            await self._drop(old, 4409, "replaced by a newer connection")
        if on_hello:
            try:
                on_hello(msg.get("p") or {})
            except Exception:
                pass
        try:
            while True:
                text = await ws.receive_text()
                ch, _, payload = text.partition("\n")
                if ch == CONTROL:
                    # 壳子在运行集变化(md.run / md.exit)时重发 hello:刷新缓存,后来的消费者拿到的是新鲜的
                    try:
                        m2 = json.loads(payload)
                    except ValueError:
                        continue
                    if isinstance(m2, dict) and m2.get("m") == "hello":
                        link.hello_raw = payload
                        if on_hello:
                            try:
                                on_hello(m2.get("p") or {})
                            except Exception:
                                pass
                    continue
                consumer = link.channels.get(ch)
                if consumer is None:
                    continue
                try:
                    await consumer.send_text(payload)
                except Exception:
                    link.channels.pop(ch, None)
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            if self.links.get(node["node_id"]) is link:
                self.links.pop(node["node_id"], None)
            await self._close_consumers(link, 4410, "node disconnected")

    async def world_session(self, ws: WebSocket, node_id, username):
        """租户 dsh 侧。节点不在线/不属于该用户 → 4404;否则开通道、回放 hello、双向转发。"""
        await ws.accept(subprotocol=_accept_proto(ws))
        link = self.links.get(node_id)
        if link is None or link.username != username:
            await ws.close(code=4404, reason="node offline or not yours")
            return
        ch = link.new_channel(ws)
        try:
            await link.send("%s\n%s" % (CONTROL, json.dumps({"t": "ntf", "m": "channel.open", "p": {"ch": ch}})))
            await ws.send_text(link.hello_raw)
            while True:
                text = await ws.receive_text()
                await link.send("%s\n%s" % (ch, text))
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            if link.channels.get(ch) is ws:
                link.channels.pop(ch, None)
            try:
                await link.send("%s\n%s" % (CONTROL, json.dumps({"t": "ntf", "m": "channel.close", "p": {"ch": ch}})))
            except Exception:
                pass
            try:
                await ws.close()
            except Exception:
                pass
