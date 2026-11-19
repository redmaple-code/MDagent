#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""算力节点(mdshell)登记簿:一次性注册码 → 节点令牌(mdn_,只存 sha256,可吊销)。
存储与 accounts.py 同纪律:flock + tmp+os.replace + 损坏隔离 + 600。
节点令牌是身份凭证,不是 IP——壳子零 IP 的定义是"不含任何 IP 性质的密钥"。"""
import fcntl
import hashlib
import hmac
import json
import os
import secrets
import time

import paths

CODE_TTL = 600          # 注册码 10 分钟有效,单次使用
NODE_NAME_MAX = 64


def _file():
    return os.path.join(paths.accounts_dir(), "nodes.json")


def _default_db():
    return {"version": 1, "nodes": [], "codes": []}


def _load():
    p = _file()
    if not os.path.exists(p):
        return _default_db()
    try:
        with open(p, encoding="utf-8") as f:
            db = json.load(f)
        if not isinstance(db.get("nodes"), list) or not isinstance(db.get("codes"), list):
            raise ValueError("bad shape")
        return db
    except Exception:
        os.rename(p, "%s.corrupt.%d" % (p, int(time.time())))
        return _default_db()


def _write(db):
    """无锁写(调用方须已持锁)。"""
    os.makedirs(paths.accounts_dir(), exist_ok=True)
    tmp = "%s.tmp.%d" % (_file(), os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, _file())
    os.chmod(_file(), 0o600)


def _txn(mutate):
    """【安全·F2】锁内 load→改→写:每次节点 hello 的 touch 与管理员 revoke 不再相互回滚。"""
    os.makedirs(paths.accounts_dir(), exist_ok=True)
    lock = _file() + ".lock"
    fd = os.open(lock, os.O_WRONLY | os.O_CREAT, 0o600)
    with os.fdopen(fd, "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            db = _load()
            result = mutate(db)
            _write(db)
            return result
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


def _save(db):
    lock = _file() + ".lock"
    os.makedirs(paths.accounts_dir(), exist_ok=True)
    fd = os.open(lock, os.O_WRONLY | os.O_CREAT, 0o600)
    with os.fdopen(fd, "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            _write(db)
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


def _hash(s):
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


def _find(db, node_id):
    for n in db["nodes"]:
        if n["node_id"] == node_id:
            return n
    return None


def new_enroll_code(username):
    """给已登录用户签一枚注册码(明文仅此一次返回;库里只存 sha256)。"""
    code = secrets.token_hex(6)
    def _m(db):
        now = time.time()
        db["codes"] = [c for c in db["codes"] if c["expires_ts"] > now and not c["used"]]
        db["codes"].append({"code_sha256": _hash(code), "username": username,
                            "expires_ts": now + CODE_TTL, "used": False})
        return code, CODE_TTL
    return _txn(_m)


def enroll(code, name, platform):
    """壳子凭注册码换节点令牌。返回 (node, token, err)。"""
    h = _hash(code)
    def _m(db):
      now = time.time()
      for c in db["codes"]:
        if c["used"] or c["expires_ts"] <= now:
            continue
        if not hmac.compare_digest(c["code_sha256"], h):
            continue
        c["used"] = True
        node_id = "n_" + secrets.token_hex(4)
        token = "mdn_" + secrets.token_hex(16)
        # 票钥:云端签发车票用的每节点 HMAC 密钥。云端必须持明文才能签,故明文存(600,与 api_key 同待遇);
        # 壳子侧同样只在 node.json(0600)。它不是 IP——只是"这张票确实是云端开的"的凭据。
        ticket_key = secrets.token_hex(32)
        node = {"node_id": node_id, "username": c["username"],
                "name": (name or "")[:NODE_NAME_MAX], "platform": (platform or "")[:32],
                "token_sha256": _hash(token), "ticket_key": ticket_key,
                "created_ts": now, "revoked": False, "last_seen": None, "hello": None}
        db["nodes"].append(node)
        return node, token, None
      return None, None, "注册码无效或已过期"
    return _txn(_m)


def canonical(obj):
    """票据规范化 JSON:键排序、无空白、非 ASCII 原样——壳子侧 tickets.js 的 canonical() 逐字节一致。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sign_ticket(node_id, argv, cwd=".", ttl=600, env=None, label="", extra=None):
    """签一张发车票:绑节点/argv/cwd/时窗,单次使用(由壳子台账保证)。返回 dict(含 sig)或 None。
    P2 起唯一合法调用方是云端闸门(三区裁决+物理门+审批之后);这里只负责签名。"""
    n = _find(_load(), node_id)
    if not n or n.get("revoked") or not n.get("ticket_key"):
        return None
    now = int(time.time())
    body = {"tid": "t_" + secrets.token_hex(8), "node": node_id,
            "argv": [str(a) for a in argv], "cwd": str(cwd or "."),
            "iat": now, "exp": now + int(ttl), "env": dict(env or {}),
            "label": str(label or "")}
    if extra:
        body.update(extra)
    sig = hmac.new(n["ticket_key"].encode("utf-8"), canonical(body).encode("utf-8"),
                   hashlib.sha256).hexdigest()
    body["sig"] = sig
    return body


def by_token(token):
    if not token:
        return None
    h = _hash(token)
    for n in _load()["nodes"]:
        stored = n.get("token_sha256") or ""
        if stored and hmac.compare_digest(stored, h) and not n.get("revoked"):
            return n
    return None


def get(node_id):
    return _find(_load(), node_id)


def _public(n):
    return {k: n.get(k) for k in ("node_id", "name", "platform", "created_ts",
                                  "last_seen", "revoked", "hello")}


def list_for(username):
    return [_public(n) for n in _load()["nodes"] if n["username"] == username]


def revoke(username, node_id):
    def _m(db):
        n = _find(db, node_id)
        if not n or n["username"] != username:
            return False, "无此节点"
        if n.get("revoked"):
            return False, "节点已吊销"
        n["revoked"] = True
        n["token_sha256"] = None
        return True, None
    return _txn(_m)


def touch(node_id, hello):
    """节点连上(hello)时记 last_seen 与能力摘要;只留小字段,不存整份 hello。"""
    shell = (hello or {}).get("shell") or {}
    sandbox = (hello or {}).get("sandbox") or {}
    def _m(db):
        n = _find(db, node_id)
        if not n:
            return
        n["last_seen"] = time.time()
        n["hello"] = {"version": shell.get("version"), "platform": shell.get("platform"),
                      "root": shell.get("root"), "maxMode": shell.get("maxMode"),
                      "sandbox": {"available": sandbox.get("available"),
                                  "enforcement": sandbox.get("enforcement"),
                                  "backend": sandbox.get("backend"),
                                  "error": sandbox.get("error")}}
    _txn(_m)
