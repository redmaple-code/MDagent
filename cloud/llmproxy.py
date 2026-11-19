#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM 计费代理:租户 dsh 的 provider baseURL 指向这里(/llm/<provider>),真 key 只在代理侧。
- 认证:租户 dsh 发 `Authorization: Bearer <代理令牌>`(= 该用户 .credentials.yaml 里的 MDEXP_PROXY_KEY);
  代理据此认出用户,绝不把租户令牌当真 key 转发。
- 注入真 key:用户对该 provider 有 BYOK 则用之(cost=0,订阅计费);否则用厂商共用 key(env MDAGENT_PROVIDER_<P>_KEY)。
- 转发:流式透传到真 provider(MDAGENT_PROVIDER_<P>_BASEURL),同时 tee 出 usage → ledger 记账。
真 key 从不落租户 DSH_HOME(那里 agent 一条 bash 就能 cat);模型下拉 = 代理给该用户的可用列表(后续)。"""
import json
import os

import httpx
from fastapi import Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse

import accounts
import ledger

_PROV_RE = __import__("re").compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def _provider_base(provider):
    return os.environ.get("MDAGENT_PROVIDER_%s_BASEURL" % provider.upper().replace("-", "_"))


def _shared_key(provider):
    return os.environ.get("MDAGENT_PROVIDER_%s_KEY" % provider.upper().replace("-", "_"), "")


def _real_key(user, provider):
    """(key, byok)。用户对该 provider 自带 key → BYOK;否则厂商共用 key。"""
    if user.get("provider") == provider and user.get("api_key"):
        return user["api_key"], True
    return _shared_key(provider), False


def _extract_usage(sse_text):
    """从 SSE 文本里取最后一个带 usage 的 chunk;非流式则整体是一个 JSON。"""
    usage = None
    model = None
    for line in sse_text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line or line == "[DONE]":
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            if obj.get("model"):
                model = obj["model"]
            if isinstance(obj.get("usage"), dict):
                usage = obj["usage"]
    return usage, model


def mount(app):
    @app.post("/llm/{provider}/{path:path}")
    async def llm_proxy(provider: str, path: str, request: Request):
        if not _PROV_RE.match(provider):
            raise HTTPException(400, "bad provider")
        # 认出用户(代理令牌 == 租户 .credentials 里的 MDEXP_PROXY_KEY)
        auth = request.headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        if not token:
            # anthropic 兼容口(BigModel Anthropic / Kimi)客户端把 apiKey 放 x-api-key,不是 Bearer。
            # dsh llm-pi-ai 对 anthropic-messages provider 用 x-api-key 发 MDEXP_PROXY_KEY → 必须在此也认,
            # 否则 token 取空、by_proxy_token(None) 恒 401「代理令牌无效」(x-api-key 已在 _DROP 里剥离转发)。
            token = request.headers.get("x-api-key", "").strip()
        user = accounts.by_proxy_token(token) if token else None
        if not user:
            raise HTTPException(401, "代理令牌无效")
        # M2:只准用自己注册的 provider。否则(provider≠注册)会走 _real_key 的共用 key 分支 + 限额=0,
        # 任意租户可 curl /llm/<任意provider> 无限烧平台共用 key。换 provider 走改注册,不在此放行。
        if provider != user.get("provider"):
            raise HTTPException(403, "provider 与账户注册不一致")
        base = _provider_base(provider)
        if not base:
            raise HTTPException(502, "provider %s 未配置(MDAGENT_PROVIDER_%s_BASEURL)" % (provider, provider.upper()))
        real_key, byok = _real_key(user, provider)
        if not real_key:
            raise HTTPException(402, "未配置 LLM Key(平台也无共用 key),请在控制台『模型配置』填入你自己的 Key")
        body = await request.body()
        try:
            model_hint = json.loads(body).get("model")
        except Exception:
            model_hint = None
        # 组转发头:透传客户端头(除敏感/逐跳头),按 provider 风格注入真 key。
        # anthropic 兼容口(Kimi、BigModel Anthropic)用 x-api-key + anthropic-version;OpenAI 兼容口用 Bearer。
        # strip accept-encoding:让上游回明文,tee 解析 usage 不被 gzip 挡(透传给客户端仍完整)。
        _DROP = {"authorization", "x-api-key", "host", "content-length", "connection",
                 "proxy-authorization", "accept-encoding",
                 "transfer-encoding", "te", "upgrade", "keep-alive", "trailer"}   # L1:剥逐跳头,防 CL/TE 请求走私
        fwd_headers = {k: v for k, v in request.headers.items() if k.lower() not in _DROP}
        fwd_headers.setdefault("content-type", "application/json")
        auth_style = os.environ.get(
            "MDAGENT_PROVIDER_%s_AUTH" % provider.upper().replace("-", "_"), "bearer").lower()
        if real_key:
            if auth_style == "anthropic":
                fwd_headers["x-api-key"] = real_key
                fwd_headers.setdefault("anthropic-version",
                                       os.environ.get("MDAGENT_ANTHROPIC_VERSION", "2023-06-01"))
            else:
                fwd_headers["authorization"] = "Bearer " + real_key
        url = base.rstrip("/") + "/" + path

        # 先建立上游流,拿到真实状态码/content-type 再回传(非流式 JSON 不能被误标成 SSE)
        client = httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=15.0))
        try:
            req = client.build_request("POST", url, content=body, headers=fwd_headers)
            resp = await client.send(req, stream=True)
        except Exception as e:
            await client.aclose()
            raise HTTPException(502, "上游暂不可达")   # 不回显 provider 地址

        async def relay():
            chunks = []
            try:
                async for raw in resp.aiter_bytes():
                    chunks.append(raw)
                    yield raw
            finally:
                await resp.aclose()
                await client.aclose()
                usage, m2 = _extract_usage(b"".join(chunks).decode("utf-8", "replace"))
                if usage is not None:
                    try:
                        ledger.record(user["username"], provider, m2 or model_hint or "?", usage, byok=byok)
                    except Exception:
                        pass

        ct = resp.headers.get("content-type", "application/octet-stream")
        return StreamingResponse(relay(), status_code=resp.status_code, media_type=ct)

    return app
