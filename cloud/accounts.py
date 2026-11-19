#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""账号账本:申请-审批制,每用户一账号一空间。
存储遵循 repo 惯例:flock + tmp+os.replace + 损坏隔离(不静默覆盖)。
token 只存 sha256;api_key 必须可用故明文存(目录 700 文件 600)。"""
import fcntl
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import threading
import time

import paths

USERNAME_RE = re.compile(r"^[a-z][a-z0-9_-]{2,15}$")
# 与系统账号/服务角色撞名会开出危险 unix 用户(mu_root),一律拒收
RESERVED_NAMES = {"root", "admin", "administrator", "system", "nobody", "daemon",
                  "www", "nginx", "mysql", "postgres", "mdagent", "mdorch", "mdshare"}
PROVIDERS = {
    "kimi": {"env": "KIMI_API_KEY", "model": "kimi-k3"},
    "deepseek": {"env": "DEEPSEEK_API_KEY", "model": "deepseek-v4-pro"},
    "kimi-coding": {"env": "KIMI_API_KEY", "model": "kimi-for-coding"},
    "bigmodel-anthropic": {"env": "BIGMODEL_ANTHROPIC_API_KEY", "model": "glm-5.3"},
    "zai-coding-cn": {"env": "ZAI_CODING_CN_API_KEY", "model": "glm-4.6"},
}
RATE_LIMIT_PER_HOUR = 0   # 0 = 不限量(用户自带 API key,费用自担;仅保留计数)


def _default_db():
    return {"version": 1, "users": []}


def _load():
    p = paths.accounts_file()
    if not os.path.exists(p):
        return _default_db()
    try:
        with open(p, encoding="utf-8") as f:
            db = json.load(f)
        if not isinstance(db.get("users"), list):
            raise ValueError("bad shape")
        return db
    except Exception:
        os.rename(p, "%s.corrupt.%d" % (p, int(time.time())))
        return _default_db()


def _write(db):
    """无锁写(调用方须已持锁)。"""
    os.makedirs(paths.accounts_dir(), exist_ok=True)
    tmp = "%s.tmp.%d" % (paths.accounts_file(), os.getpid())
    for _u in db.get("users", []):   # I1:落盘前全库兜底,任一存量非有限值不再让整库写 500
        _p = _u.get("points")
        if isinstance(_p, float) and not math.isfinite(_p):
            _u["points"] = 0.0
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=1, allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, paths.accounts_file())
    os.chmod(paths.accounts_file(), 0o600)


def _txn(mutate):
    """【安全·F2】锁内 load→改→写:整个读改写持同一把 flock,杜绝并发 RMW 相互覆盖
    (曾:高频 rate_ok 用旧快照把管理员的 revoke/rotate_token 静默回滚)。mutate(db) 的返回即本函数返回。"""
    os.makedirs(paths.accounts_dir(), exist_ok=True)
    lock = paths.accounts_file() + ".lock"
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
    """独立整库写(仍先加锁)。保留给非事务型调用。"""
    lock = paths.accounts_file() + ".lock"
    os.makedirs(paths.accounts_dir(), exist_ok=True)
    fd = os.open(lock, os.O_WRONLY | os.O_CREAT, 0o600)
    with os.fdopen(fd, "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            _write(db)
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


def _find(db, username):
    for u in db["users"]:
        if u["username"] == username:
            return u
    return None


def _hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


PWD_ITER = 200_000


def _hash_pwd(password, salt=None):
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             bytes.fromhex(salt), PWD_ITER)
    return salt, dk.hex()


def apply(username, contact, reason, provider, api_key, password, invite_code=""):
    """公开申请。返回 (ok, msg)。重复申请/重名拒绝。"""
    if not USERNAME_RE.match(username or ""):
        return False, "username 须 ^[a-z][a-z0-9_-]{2,15}$"
    if username in RESERVED_NAMES:
        return False, "该用户名为保留名,不可用"
    if provider and provider not in PROVIDERS:
        return False, "provider 须为: %s" % "/".join(PROVIDERS)
    provider = provider or DEFAULT_PROVIDER   # 注册页不再选服务商:给默认,保证批准时 provision 能渲染 settings;登录后配 key 可改
    if api_key and len(api_key) < 8:
        return False, "api_key 过短(至少 8 位)"
    if not password or len(password) < 8:
        return False, "密码至少 8 位"
    if os.environ.get("MDAGENT_OPEN_SIGNUP") != "1" and not check_invite(invite_code):
        return False, "邀请码无效或已过期,请向管理员索取"
    _pre = _find(_load(), username)   # F1:判重前置到 pbkdf2 之前
    if _pre and _pre["status"] in ("pending", "active", "revoked"):
        return False, "该用户名不可用,请换一个"   # I2:不区分具体状态,防枚举
    salt, pwh = _hash_pwd(password)
    def _m(db):
        ex = _find(db, username)
        if ex and ex["status"] in ("pending", "active", "revoked"):
            return False, "该用户名不可用,请换一个"
        if os.environ.get("MDAGENT_OPEN_SIGNUP") != "1":   # N3:先消费邀请码,失败即返回(不动 denied 记录)
            _iok, _ierr = _consume_invite_locked(db, invite_code, username)
            if not _iok:
                return False, _ierr
        if ex:   # denied(从未开通):消费成功后再移除旧记录
            db["users"] = [x for x in db["users"] if x["username"] != username]
        db["users"].append({
            "username": username, "role": "user", "status": "pending",
            "provider": provider or "", "api_key": api_key or "",
            "contact": contact or "", "reason": reason or "",
            "created_ts": time.time(), "approved_ts": None,
            "token_sha256": None, "chat_ts": [],
            "membership": "free", "membership_expire": None,
            "points": 0, "points_log": [],
            "pwd": "%s$%s" % (salt, pwh),
        })
        return True, "已受理,等管理员审批"
    return _txn(_m)


def verify_login(username, password):
    """用户名+密码 → user dict(active 且密码对)或 None。"""
    u = _find(_load(), username)
    pw = u.get("pwd") if u else None
    if not u or u.get("status") != "active" or not pw or "$" not in pw:
        _hash_pwd(password or "", _DUMMY_SALT)   # F4:常时,抹平时序侧信道
        return None
    salt, hexh = pw.split("$", 1)
    _, cand = _hash_pwd(password or "", salt)
    if not hmac.compare_digest(cand, hexh):
        return None
    return u


def rotate_token(username):
    """铸新 token(旧的立即作废)。返回明文,仅此一次。"""
    token = "mda_" + secrets.token_hex(16)
    def _m(db):
        u = _find(db, username)
        if not u or u["status"] != "active":
            return None
        u["token_sha256"] = _hash(token)
        return token
    return _txn(_m)


def set_password(username, password):
    if not password or len(password) < 8:
        return False, "密码至少 8 位"
    salt, pwh = _hash_pwd(password)
    def _m(db):
        u = _find(db, username)
        if not u or u["status"] != "active":
            return False, "无此活跃账号"
        u["pwd"] = "%s$%s" % (salt, pwh)
        return True, None
    return _txn(_m)


def update_apikey(username, provider, api_key):
    """自助改 LLM 配置。注意:.credentials.yaml 是 provision 时写的快照,
    调用方必须随后调 provision.refresh_credentials 同步,否则运行时仍用旧 key。"""
    if provider not in PROVIDERS:
        return False, "provider 须为: %s" % "/".join(PROVIDERS)
    if not api_key or len(api_key) < 8:
        return False, "api_key 缺失或过短"
    def _m(db):
        u = _find(db, username)
        if not u or u["status"] != "active":
            return False, "无此活跃账号"
        u["provider"] = provider
        u["api_key"] = api_key
        return True, None
    return _txn(_m)


def approve(username):
    """审批通过:铸 token(仅此一次返回明文),置 active。空间开通由调用方做。"""
    token = "mda_" + secrets.token_hex(16)
    def _m(db):
        u = _find(db, username)
        if not u or u["status"] != "pending":
            return None, "无此待批申请"
        u["token_sha256"] = _hash(token)
        u["status"] = "active"
        u["approved_ts"] = time.time()
        return token, None
    return _txn(_m)


def deny(username):
    def _m(db):
        u = _find(db, username)
        if not u or u["status"] != "pending":
            return False, "无此待批申请"
        u["status"] = "denied"
        return True, None
    return _txn(_m)


def revoke(username):
    def _m(db):
        u = _find(db, username)
        if not u or u["status"] != "active":
            return False, "无此活跃账号"
        u["status"] = "revoked"
        u["token_sha256"] = None
        u["external_api_token_sha256"] = None
        u["session_token_sha256"] = None   # 【X2】浏览器会话令牌同步作废
        u["proxy_token_sha256"] = None     # L3:计费代理令牌一并作废(否则账号重激活时旧 mxp_ 复活)
        return True, None
    return _txn(_m)


def set_proxy_token(username):
    """铸/轮换该用户的 LLM 代理令牌(租户 dsh 用它认到计费代理)。返回明文,仅此一次;库存 sha256。"""
    token = "mxp_" + secrets.token_hex(16)
    def _m(db):
        u = _find(db, username)
        if not u:
            return None
        u["proxy_token_sha256"] = _hash(token)
        return token
    return _txn(_m)


def by_proxy_token(token):
    if not token:
        return None
    h = _hash(token)
    for u in _load()["users"]:
        stored = u.get("proxy_token_sha256") or ""
        if stored and hmac.compare_digest(stored, h) and u["status"] == "active":
            return u
    return None


def by_token(token):
    if not token:
        return None
    h = _hash(token)
    for u in _load()["users"]:
        if u["status"] != "active":
            continue
        stored = u.get("token_sha256") or ""
        if stored and hmac.compare_digest(stored, h):
            return u
        # External integrations have a separate token, without admin privileges.
        # Existing CLI/API tokens retain their original behavior.
        external = u.get("external_api_token_sha256") or ""
        if external and hmac.compare_digest(external, h):
            view = dict(u)
            view["role"] = "user"
            view["token_kind"] = "external-api"
            return view
    return None


# ---------- 浏览器会话令牌(与长期 API 令牌分离:X2 凭据拆分) ----------
# 浏览器 Cookie 携带的是 session 令牌(mds_),与 CLI/程序化用的 API 令牌(mda_)分开:
# Cookie 被盗 ≠ API 令牌被盗、可独立作废;登录轮换 session 不动 API 令牌 → 不再踢掉已装 CLI。
def rotate_session_token(username):
    token = "mds_" + secrets.token_hex(16)
    def _m(db):
        u = _find(db, username)
        if not u or u["status"] != "active":
            return None
        u["session_token_sha256"] = _hash(token)
        return token
    return _txn(_m)


def by_session_token(token):
    if not token:
        return None
    h = _hash(token)
    for u in _load()["users"]:
        stored = u.get("session_token_sha256") or ""
        if stored and hmac.compare_digest(stored, h) and u["status"] == "active":
            return u
    return None


def clear_session_token(username):
    """登出:作废浏览器会话令牌(不动 API 令牌)。"""
    def _m(db):
        u = _find(db, username)
        if u:
            u["session_token_sha256"] = None
        return True
    return _txn(_m)


# ---------- 租户 world 通道 per-instance 短票(X2:overlay 不再放账号主令牌) ----------
# 内存态(随进程;单 worker——见部署注记 L1)。实例随进程重启而重拉,短票也随之重铸,无需持久化。
# 绑定 (username, node_id):tenants 拉起实例时铸、拆实例时吊销;relay.world_session 用它认证。
# 关键:agent 以 mu_<user> 能读自己 overlay 里的这枚票,但它只认 world 通道、不认 /v1/*——
# 即便泄漏也拿不到账号主令牌/接管账号(封住 X2 最大爆炸半径:agent→overlay→master token)。
_WORLD_TTL = int(os.environ.get("MDAGENT_WORLD_TOKEN_TTL", "86400"))
_world = {}                      # sha256 -> {username, node_id, exp}
_world_lock = threading.Lock()


def mint_world_token(username, node_id, ttl=None):
    token = "mdw_" + secrets.token_hex(16)
    with _world_lock:
        _world[_hash(token)] = {"username": username, "node_id": node_id or "",
                                "exp": time.time() + (ttl or _WORLD_TTL)}
    return token


def verify_world_token(token):
    if not token:
        return None
    h = _hash(token)
    now = time.time()
    with _world_lock:
        rec = _world.get(h)
        if not rec:
            return None
        if rec["exp"] < now:
            _world.pop(h, None)
            return None
        return {"username": rec["username"], "node_id": rec["node_id"]}


def touch_world_token(token, ttl=None):
    """复用在跑实例时续期(实例活着,票不该过期)。"""
    if not token:
        return
    h = _hash(token)
    with _world_lock:
        rec = _world.get(h)
        if rec:
            rec["exp"] = time.time() + (ttl or _WORLD_TTL)


def revoke_world_token(token):
    if not token:
        return
    with _world_lock:
        _world.pop(_hash(token), None)


def get(username):
    return _find(_load(), username)


def list_users():
    return _load()["users"]


def list_applications():
    return [u for u in _load()["users"] if u["status"] == "pending"]


def rate_ok(username, limit=RATE_LIMIT_PER_HOUR):
    """滑动 1 小时窗。通过则记账。limit<=0 表示不限量(仍记账)。"""
    if limit <= 0:
        # 不限量:不再每次 /v1/chat 写盘(此前是 F2 回滚 revoke 的高频竞争写者)。仅记内存无意义,跳过。
        return _find(_load(), username) is not None
    now = time.time()
    def _m(db):
        u = _find(db, username)
        if not u:
            return False
        ts = [t for t in u.get("chat_ts", []) if now - t < 3600]
        if limit <= len(ts):
            u["chat_ts"] = ts
            return False
        ts.append(now)
        u["chat_ts"] = ts
        return True
    return _txn(_m)


def usage(username):
    u = get(username)
    if not u:
        return None
    now = time.time()
    ts = u.get("chat_ts", [])
    return {"username": username, "chats_total": len(ts),
            "chats_24h": len([t for t in ts if now - t < 86400]),
            "limit_per_hour": RATE_LIMIT_PER_HOUR}


# ===== 会员 + 积分(阶段A:管理端可设/充值 + 展示;计费联动待接) =====
MEMBERSHIP_TIERS = ("free", "pro", "vip")


def set_membership(username, tier, days=0):
    """设会员等级(free/pro/vip)。days>0 从当前到期(或现在)顺延 days 天;
    free 清空到期。仅 active 账号。返回 (ok, err)。"""
    if tier not in MEMBERSHIP_TIERS:
        return False, "会员档位须为: %s" % "/".join(MEMBERSHIP_TIERS)
    try:
        days = int(days or 0)
    except (TypeError, ValueError):
        return False, "days 必须是整数"
    if days < 0 or days > MEMBERSHIP_DAYS_MAX:
        return False, "days 须在 0–%d 之间" % MEMBERSHIP_DAYS_MAX

    def _m(db):
        u = _find(db, username)
        if not u or u.get("status") != "active":
            return False, "无此活跃账号"
        u["membership"] = tier
        if tier == "free":
            u["membership_expire"] = None
        elif days and days > 0:
            base = max(time.time(), u.get("membership_expire") or 0)
            u["membership_expire"] = base + days * 86400
        return True, None
    return _txn(_m)


def adjust_points(username, delta, reason="", by="admin"):
    """给用户加/减积分(delta 正负)。返回 (ok, err, 新余额)。不允许扣成负。"""
    try:
        delta = float(delta)
    except (TypeError, ValueError):
        return False, "delta 必须是数字", None
    if not math.isfinite(delta) or abs(delta) > POINTS_DELTA_MAX:
        return False, "积分变动须为有限数,且绝对值 ≤ %d" % POINTS_DELTA_MAX, None
    reason = (reason or "")[:200]

    def _m(db):
        u = _find(db, username)
        if not u or u.get("status") != "active":
            return False, "无此活跃账号", None
        cur = float(u.get("points") or 0)
        if not math.isfinite(cur):   # 历史脏值自愈(修复前可写入 NaN/Inf)
            cur = 0.0
        bal = cur + delta
        if not math.isfinite(bal) or bal > POINTS_BALANCE_MAX:
            return False, "余额将超出上限 %d" % POINTS_BALANCE_MAX, None
        if bal < 0:
            return False, "扣减后为负(当前 %.2f)" % cur, None
        u["points"] = bal
        log = u.get("points_log") or []
        log.append({"ts": time.time(), "delta": delta, "balance": bal,
                    "reason": reason or "", "by": by})
        u["points_log"] = log[-50:]
        return True, None, bal
    return _txn(_m)


def points_balance(username):
    u = _find(_load(), username)
    return safe_points(u) if u else 0.0


# AUDIT-FIX-R1 对抗审计第一轮修复用到的常量与读路径助手
DEFAULT_PROVIDER = "deepseek" if "deepseek" in PROVIDERS else next(iter(PROVIDERS))
MEMBERSHIP_DAYS_MAX = 3650
POINTS_DELTA_MAX = 10 ** 8
POINTS_BALANCE_MAX = 10 ** 9
_DUMMY_SALT = "00" * 16   # F4:verify_login miss 路径用的固定 salt(常时 pbkdf2)


def safe_points(u):
    """读路径兜底:积分非数字/非有限一律按 0 展示(防 JSONResponse allow_nan=False 整体 500)。"""
    try:
        v = float(u.get("points") or 0)
    except (TypeError, ValueError):
        return 0.0
    return v if math.isfinite(v) else 0.0


def effective_membership(u):
    """(有效档位, 到期时间, 是否已过期)。付费档过期 → 视为 free;存储不动,保留原档位留痕。"""
    tier = u.get("membership") or "free"
    exp = u.get("membership_expire")
    if tier != "free" and exp and exp < time.time():
        return "free", exp, True
    return tier, exp, False


# ===== 邀请码注册闸门(INVITE-R1) =====
def create_invite(by="admin", days=7, max_uses=1, note=""):
    """生成邀请码 inv_xxx。返回明文码。"""
    import secrets as _sec
    code = "inv_" + _sec.token_hex(8)
    try:
        days = max(1, min(int(days), INVITE_DAYS_MAX))
    except (TypeError, ValueError):
        days = 7
    try:
        max_uses = max(1, min(int(max_uses), 1000))
    except (TypeError, ValueError):
        max_uses = 1

    def _m(db):
        db.setdefault("invites", []).append({
            "code": code, "created_ts": time.time(), "created_by": by,
            "expires_ts": time.time() + days * 86400,
            "max_uses": max_uses, "uses": 0, "used_by": [], "note": (note or "")[:100],
        })
        return code
    return _txn(_m)


def check_invite(code):
    """只读预检:未过期且未用尽 → True。"""
    if not code:
        return False
    now = time.time()
    for iv in _load().get("invites", []):
        if hmac.compare_digest(iv.get("code", ""), code):   # #3:常时比较
            return iv.get("expires_ts", 0) > now and iv.get("uses", 0) < iv.get("max_uses", 1)
    return False


def _consume_invite_locked(db, code, username):
    """锁内消费(调用方须在 _txn 内并传入同一 db)。返回 (ok, err)。"""
    now = time.time()
    for iv in db.get("invites", []):
        if hmac.compare_digest(iv.get("code", ""), code):   # #3:常时比较
            if iv.get("expires_ts", 0) <= now:
                return False, "邀请码已过期"
            if iv.get("uses", 0) >= iv.get("max_uses", 1):
                return False, "邀请码已用尽"
            iv["uses"] = iv.get("uses", 0) + 1
            iv.setdefault("used_by", []).append({"user": username, "ts": now})
            return True, None
    return False, "邀请码无效"


def list_invites():
    now = time.time()
    out = []
    for iv in _load().get("invites", []):
        out.append({
            "code": iv.get("code"), "created_by": iv.get("created_by"),
            "created_ts": iv.get("created_ts"), "expires_ts": iv.get("expires_ts"),
            "max_uses": iv.get("max_uses", 1), "uses": iv.get("uses", 0),
            "used_by": [x.get("user") for x in iv.get("used_by", [])],
            "valid": iv.get("expires_ts", 0) > now and iv.get("uses", 0) < iv.get("max_uses", 1),
            "note": iv.get("note", ""),
        })
    return out


# AUDIT-R2
INVITE_DAYS_MAX = 90   # #2:邀请码有效期上限(天)


def revoke_invite(code):
    """作废邀请码(置过期)。返回 (ok, err)。"""
    def _m(db):
        for iv in db.get("invites", []):
            if hmac.compare_digest(iv.get("code", ""), code or ""):
                iv["expires_ts"] = 0
                iv["revoked"] = True
                return True, None
        return False, "邀请码无效"
    return _txn(_m)
