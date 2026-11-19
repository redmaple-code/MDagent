#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM 用量/费用台账。计费点在这里(不在 harness)——dsh-token-meter 官方明说它只是估算、非计费记录。
每次成功的 provider 调用记一行 <HOME>/ledger/<user>.jsonl:{ts,provider,model,in,out,cache_read,cache_write,cost,byok}。
追加式、每用户一文件;summary() 汇总今日/本周/累计/分模型,供 /v1/usage 与用户面板。"""
import fcntl
import json
import os
import time

import paths

# 单价(元 / 百万 token),占位默认;真单价按 provider 配 MDAGENT_PRICE_<PROVIDER>_<IN|OUT>=元/Mtok。
# BYOK(用户自带 key)成本归用户,cost 记 0,平台只按订阅收费(见计划 D3)。
_DEFAULT_PRICE = {"in": 0.0, "out": 0.0}


def _dir():
    return os.path.join(paths.HOME, "ledger")


def _file(user):
    return os.path.join(_dir(), "%s.jsonl" % user)


def _price(provider):
    up = provider.upper().replace("-", "_")
    def g(k, d):
        try:
            return float(os.environ.get("MDAGENT_PRICE_%s_%s" % (up, k), d))
        except ValueError:
            return d
    return {"in": g("IN", _DEFAULT_PRICE["in"]), "out": g("OUT", _DEFAULT_PRICE["out"])}


def record(user, provider, model, usage, byok=False):
    """usage: dict(可含 prompt_tokens/completion_tokens/total_tokens 或 input/output/cache_*)。
    返回记下的行(含算出的 cost)。BYOK 用户 cost=0(成本自担,平台走订阅)。"""
    u = usage or {}
    def pick(*keys):
        for k in keys:
            if isinstance(u.get(k), (int, float)):
                return int(u[k])
        return 0
    tin = pick("uncachedInputTokens", "prompt_tokens", "input_tokens", "input")
    tout = pick("completion_tokens", "output_tokens", "output")
    cache_r = pick("cacheReadTokens", "cache_read_input_tokens", "cache_read")
    cache_w = pick("cacheWriteTokens", "cache_creation_input_tokens", "cache_write")
    pr = _price(provider)
    cost = 0.0 if byok else round((tin + cache_r + cache_w) / 1e6 * pr["in"] + tout / 1e6 * pr["out"], 6)
    row = {"ts": time.time(), "provider": provider, "model": model,
           "in": tin, "out": tout, "cache_read": cache_r, "cache_write": cache_w,
           "cost": cost, "byok": bool(byok)}
    os.makedirs(_dir(), exist_ok=True)
    lock = _file(user) + ".lock"
    fd = os.open(lock, os.O_WRONLY | os.O_CREAT, 0o600)
    with os.fdopen(fd, "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            with open(_file(user), "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)
    return row


def summary(user):
    p = _file(user)
    now = time.time()
    out = {"today": _z(), "week": _z(), "total": _z(), "by_model": {}}
    if not os.path.exists(p):
        return out
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                _add(out["total"], r)
                if now - r.get("ts", 0) < 86400:
                    _add(out["today"], r)
                if now - r.get("ts", 0) < 7 * 86400:
                    _add(out["week"], r)
                key = "%s/%s" % (r.get("provider", "?"), r.get("model", "?"))
                out["by_model"].setdefault(key, _z())
                _add(out["by_model"][key], r)
    except OSError:
        pass
    return out


def _z():
    return {"calls": 0, "in": 0, "out": 0, "cost": 0.0}


def _add(acc, r):
    acc["calls"] += 1
    acc["in"] += int(r.get("in", 0)) + int(r.get("cache_read", 0)) + int(r.get("cache_write", 0))
    acc["out"] += int(r.get("out", 0))
    acc["cost"] = round(acc["cost"] + float(r.get("cost", 0.0)), 6)
