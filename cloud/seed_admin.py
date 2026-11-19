#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""播种管理员账号(幂等)。token 仅本次打印,落 /root/.mdagent_admin(600)。
用法:
  ADMIN_PROVIDER=kimi-coding ADMIN_API_KEY=sk-... python3 seed_admin.py [username]
未提供 ADMIN_API_KEY 时管理员只有管理权限,chat 会因缺凭证失败。"""
import hashlib
import os
import secrets
import sys
import time

import accounts
import provision

BOOTSTRAP_FILE = os.environ.get(
    "MDAGENT_ADMIN_BOOTSTRAP_FILE",
    os.path.join(os.environ.get("MDAGENT_HOME", "/opt/mdagent"),
                 "accounts", ".mdagent_admin"))


def seed(username="user"):
    u = accounts.get(username)
    if u and u["status"] == "active" and u["role"] == "admin":
        return None, "管理员已存在(token 见 %s;要重置先删 accounts.json 里该用户)" % BOOTSTRAP_FILE
    provider = os.environ.get("ADMIN_PROVIDER", "kimi-coding")
    api_key = os.environ.get("ADMIN_API_KEY", "") or "placeholder-key"
    token = "mda_" + secrets.token_hex(16)
    db = accounts._load()
    u = accounts._find(db, username)
    if not u:
        u = {"username": username, "created_ts": time.time(),
             "contact": "owner", "reason": "admin bootstrap", "chat_ts": []}
        db["users"].append(u)
    u.update({"role": "admin", "status": "active", "provider": provider,
              "api_key": api_key, "approved_ts": time.time(),
              "token_sha256": hashlib.sha256(token.encode()).hexdigest()})
    accounts._save(db)
    provision.provision(username)
    os.makedirs(os.path.dirname(BOOTSTRAP_FILE), exist_ok=True)
    with open(BOOTSTRAP_FILE, "w", encoding="utf-8") as f:
        f.write("# mdagent-api 管理员账号(token 只存 sha256,此文件是唯一明文)\n")
        f.write("username: %s\ntoken: %s\n" % (username, token))
    os.chmod(BOOTSTRAP_FILE, 0o600)
    return token, None


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "user"
    tok, err = seed(name)
    if err:
        print("FAIL:", err)
        sys.exit(1)
    if tok:
        print("管理员已播种: username=%s" % name)
        print("token(仅本次显示,同时写入 %s): %s" % (BOOTSTRAP_FILE, tok))
