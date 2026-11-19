#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mdagent cloud 侧 HPC(超算)多用户 SSH 模块 —— 契约: mdagent-hpc/CONTRACT.md「Cloud 侧 API」。

功能:
  1. 多用户 SSH 连接管理:凭据 Fernet 加密存储(响应/日志/异常绝不回显凭据原文)
  2. verify 检查链(TCP → host key TOFU pin → 认证 → whoami → 调度器/引擎 → $HOME 可写)
  3. 平台直连超算:sbatch/squeue/sacct/scancel(Slurm 优先;PBS 仅检测)
  4. 作业台账 hpc_jobs.json(沿用 refs/cloud/accounts.py 的 _txn flock+tmp+os.replace 模式)

挂载(cloud/app.py,详见 APP_PATCH.md):
    import hpc
    hpc.mount_hpc(app)

环境变量:
    MDAGENT_HPC_SECRET   Fernet 密钥(必需,导入时校验;缺失/非法 → RuntimeError 带配置说明)
    MDAGENT_HPC_DB       台账路径(默认本文件同目录 hpc_jobs.json;测试用)

安全红线(契约「安全红线」章节):
  - 凭据原文只存在于加密前的内存与解密后的 SSH 短连接调用栈内
  - host key SHA256 指纹 TOFU:首次记录并 pin;不一致拒绝(HOST_KEY_CHANGED),
    verify 携带 trust_new_fingerprint=true 才接受变更
  - 不开放任意 shell:sbatch 脚本由服务端固定模板渲染,
    用户输入仅 文件内容 + cores/minutes/entry 三个受限参数
  - 所有路由校验 owner,非本人一律 404
"""
import base64
import fcntl
import hashlib
import io
import json
import os
import posixpath
import re
import secrets
import socket
import time
from typing import List, Optional

from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

# 生产环境里 cloud/app.py 顶部已 sys.path.insert(cloud 目录),这里拿到的与主 app
# 是同一个 accounts 模块;独立部署/测试时可为 None(认证路径返回 503,或测试注入替身)。
try:
    import accounts as _accounts
except Exception:  # ImportError 及其连带失败一律降级,不在导入期炸
    _accounts = None

router = APIRouter(prefix="/v1/hpc")

# ---------- 常量 ----------
SSH_TIMEOUT = 25              # 契约:SSH timeout 25s;每次操作新建短连接
TCP_TIMEOUT = 10
MAX_CONNECTIONS_PER_USER = 8  # 连接 ≤8/人
MAX_ACTIVE_JOBS_PER_USER = 3  # 并发未终态作业 ≤3
TERMINAL_STATES = {"succeeded", "failed", "cancelled"}
MAX_INPUT_FILES = 10
MAX_INPUT_FILE_BYTES = 256 * 1024
MAX_INPUT_PATH_LEN = 128
CORES_RANGE = (1, 64)
MINUTES_RANGE = (1, 120)
LOG_BYTES_DEFAULT = 16384
LOG_BYTES_MAX = 65536
AUTH_METHODS = ("private_key", "password")

# verify 检查链用到的固定命令(契约原文;测试按常量脚本化 fake paramiko)
CMD_WHOAMI = "whoami"
CMD_SCHED_SLURM = "command -v sbatch squeue"
CMD_SCHED_PBS = "command -v qsub"
CMD_ENGINES = 'command -v lmp lmp_mpi lammps || ls "$HOME/software/lammps/bin/lmp" 2>/dev/null'
CMD_MODULES = "bash -lc 'module avail 2>&1 | head'"
CMD_HOME = "echo $HOME"
CMD_JOBS_WRITABLE = ('mkdir -p "$HOME/mdagent/jobs" && touch "$HOME/mdagent/jobs/.w" '
                     '&& rm -f "$HOME/mdagent/jobs/.w" && echo ok')
CHECK_ORDER = ["tcp", "host_key", "auth", "whoami", "scheduler",
               "engines", "modules", "home", "jobs_dir"]

# sbatch 脚本固定模板(契约原文)。唯一插值点:short(作业 ID 前 8 hex)、cores、minutes、entry,
# 四者均经服务端校验(整数/安全字符集),无自由文本注入点。
_SBATCH_TMPL = (
    "#!/bin/bash\n"
    "#SBATCH --job-name=mdagent-{short}\n"
    "#SBATCH -N 1\n"
    "#SBATCH -n {cores}\n"
    "#SBATCH -t {minutes}\n"
    "#SBATCH -o slurm-%x.out\n"
    "#SBATCH -e slurm-%x.out\n"
    "cd $SLURM_SUBMIT_DIR\n"
    "export PATH=\"$HOME/software/lammps/bin:$PATH\"\n"
    "set +e\n"
    "MDAGENT_ENGINE_RC=127\n"
    "if lmp -in {entry} -log log.lammps 2>&1; then MDAGENT_ENGINE_RC=0\n"
    "elif lmp_mpi -in {entry} 2>&1; then MDAGENT_ENGINE_RC=0\n"
    "elif module load lammps 2>/dev/null && lmp -in {entry} 2>&1; then MDAGENT_ENGINE_RC=0\n"
    "else MDAGENT_ENGINE_RC=$?; fi\n"
    'echo "MDAGENT_EXIT_CODE=$MDAGENT_ENGINE_RC"\n'
    "exit $MDAGENT_ENGINE_RC\n"
)

_HOST_RE = re.compile(r"^(?:[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?|\d{1,3}(?:\.\d{1,3}){3})$")
_HOME_RE = re.compile(r"^/[A-Za-z0-9][A-Za-z0-9._\-/]*$")   # $HOME 不含空白/引号(防 cd 命令拼接)
# 输入文件相对路径:组件不以 . 开头(天然挡住 ..),无空白/shell 元字符;可含一级或多级子目录
_INPUT_PATH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]*(?:/[A-Za-z0-9][A-Za-z0-9._\-]*)*$")

# ---------- Fernet 密钥(导入时校验,缺失 raise RuntimeError 带配置说明) ----------
_SECRET_ENV = "MDAGENT_HPC_SECRET"
_KEYGEN_CMD = 'python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'


def _fernet_from_env() -> Fernet:
    raw = (os.environ.get(_SECRET_ENV) or "").strip()
    if not raw:
        raise RuntimeError(
            "配置缺失:环境变量 %s 未设置(HPC SSH 凭据 Fernet 加密密钥)。\n"
            "生成密钥: %s\n"
            "配置方式: export %s=<生成的密钥>(写入 systemd 单元 Environment= 或启动脚本后重启服务)"
            % (_SECRET_ENV, _KEYGEN_CMD, _SECRET_ENV))
    try:
        return Fernet(raw.encode())
    except Exception:
        raise RuntimeError(
            "配置错误:%s 不是合法的 Fernet 密钥。\n"
            "重新生成: %s\n"
            "更新后重启服务(旧密钥加密的既有连接将无法解密,需删除重录)"
            % (_SECRET_ENV, _KEYGEN_CMD))


_fernet = _fernet_from_env()


def _paramiko():
    """lazy import:paramiko 仅在真实 SSH 操作时导入(测试注入 fake 模块不受影响)。"""
    import paramiko
    return paramiko


# ---------- 认证(与 refs/cloud/app.py 的 current_user 逻辑等价复刻) ----------
# 不直接 `from app import current_user`:app.py 挂载本模块(import hpc)会构成循环导入,
# 故复刻其 token 解析:Authorization Bearer → accounts.by_token;
# 缺失时回退 HttpOnly Cookie mdexp_session → accounts.by_session_token。
def _token_from(request: Request):
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None   # 不接受 ?token=:query 串会明文落访问日志


def current_user(request: Request):
    if _accounts is None:
        raise HTTPException(503, "账号服务不可用(未加载 accounts 模块)")
    tok = _token_from(request)
    u = _accounts.by_token(tok) if tok else None
    if not u:
        sc = request.cookies.get("mdexp_session")
        u = _accounts.by_session_token(sc) if sc else None
    if not u:
        raise HTTPException(401, "unauthorized: 需要有效 token")
    return u


# ---------- 台账持久化(沿用 refs/cloud/accounts.py 的 _default_db/_load/_write/_txn 模式) ----------
def _default_db():
    return {"version": 1, "connections": [], "jobs": {}}


def _db_path():
    return os.environ.get("MDAGENT_HPC_DB") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "hpc_jobs.json")


def _load():
    p = _db_path()
    if not os.path.exists(p):
        return _default_db()
    try:
        with open(p, encoding="utf-8") as f:
            db = json.load(f)
        if not isinstance(db.get("connections"), list) or not isinstance(db.get("jobs"), dict):
            raise ValueError("bad shape")
        return db
    except Exception:
        os.rename(p, "%s.corrupt.%d" % (p, int(time.time())))   # 损坏隔离,不静默覆盖
        return _default_db()


def _write(db):
    """无锁写(调用方须已持锁)。tmp + os.replace 原子落盘,0600。"""
    p = _db_path()
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    tmp = "%s.tmp.%d" % (p, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=1, allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)
    os.chmod(p, 0o600)


def _txn(mutate):
    """锁内 load→改→写:整个读改写持同一把 flock,杜绝并发 RMW 相互覆盖。"""
    p = _db_path()
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    lock = p + ".lock"
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


# ---------- 通用助手 ----------
def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _safe_err(e):
    """异常文本脱敏:截断,且一旦疑似携带密钥材料(PEM 头等)整体替换。"""
    s = str(e) or e.__class__.__name__
    s = s.replace("\n", " ")[:200]
    if "PRIVATE KEY" in s or "BEGIN " in s or "BEGIN-" in s:
        return "凭据相关错误(已脱敏)"
    return s


def _encrypt_creds(d: dict) -> str:
    return _fernet.encrypt(json.dumps(d, ensure_ascii=False).encode()).decode()


def _decrypt_creds(s: str) -> dict:
    try:
        return json.loads(_fernet.decrypt(s.encode()).decode())
    except Exception:
        raise HTTPException(500, "凭据解密失败(密钥已轮换?请删除连接后重新添加)")


def _find_owned_conn(db, cid, username):
    for c in db["connections"]:
        if c["connection_id"] == cid and c["owner"] == username:
            return c
    return None


def _find_owned_job(db, jid, username):
    j = db["jobs"].get(jid)
    return j if (j and j.get("owner") == username) else None


def _conn_public(c):
    """连接脱敏视图:绝不含 secret_ciphertext / 凭据原文。"""
    out = {
        "connection_id": c["connection_id"], "name": c["name"], "host": c["host"],
        "port": c["port"], "username": c["username"], "auth_method": c["auth_method"],
        "status": c["status"], "host_key_fingerprint": c.get("host_key_fingerprint"),
        "scheduler": c.get("scheduler"), "engines": c.get("engines") or [],
        "home": c.get("home"), "created_at": c["created_at"],
        "last_verified_at": c.get("last_verified_at"),
    }
    if c.get("last_error"):
        out["last_error"] = c["last_error"]
    return out


def _conn_created_view(c):
    """POST /connections 响应:严格契约字段。"""
    return {"connection_id": c["connection_id"], "name": c["name"], "host": c["host"],
            "port": c["port"], "username": c["username"], "auth_method": c["auth_method"],
            "status": c["status"], "host_key_fingerprint": c.get("host_key_fingerprint"),
            "created_at": c["created_at"]}


def _job_public(j):
    out = {
        "job_id": j["job_id"], "owner": j["owner"], "connection_id": j["connection_id"],
        "name": j.get("name") or "", "scheduler_job_id": j.get("scheduler_job_id"),
        "remote_dir": j.get("remote_dir"), "engine": j.get("engine"),
        "entry": j.get("entry"), "cores": j.get("cores"), "minutes": j.get("minutes"),
        "state": j.get("state"), "created_at": j.get("created_at"),
        "updated_at": j.get("updated_at"),
    }
    if j.get("last_error"):
        out["last_error"] = j["last_error"]
    return out


def _valid_host(h):
    if not h or len(h) > 255:
        return False
    # 拒绝 file:// 等任意 scheme / userinfo / 路径 / 空白
    if any(x in h for x in ("://", "/", "@", ":", " ", "\t")):
        return False
    if not _HOST_RE.match(h):
        return False
    # 第一版允许私网 IP(超算可能是内网域名),仅拒绝云元数据地址
    if h == "169.254.169.254" or h.endswith(".169.254.169.254"):
        return False
    return True


# ---------- SSH 层(paramiko,每次操作新建短连接) ----------
class _SSHError(Exception):
    """SSH 失败(消息已脱敏,可安全透出)。"""


class _SSHConnError(_SSHError):
    """TCP 通但 SSH 握手失败(网络/协议层)。"""


class _SSHAuthError(_SSHError):
    """认证失败(用户名/私钥/密码不对)。"""


class _HostKeyChanged(_SSHError):
    def __init__(self, expected, actual):
        super().__init__(
            "HOST_KEY_CHANGED: 服务器主机公钥指纹与已记录的不一致"
            "(期望 %s,实际 %s)。若确认集群变更属实,请携带 trust_new_fingerprint=true 重新验证"
            % (expected, actual))
        self.expected, self.actual = expected, actual


def _fingerprint(key_obj) -> str:
    """OpenSSH 风格 SHA256 base64 指纹。"""
    digest = hashlib.sha256(key_obj.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def _load_pkey(pm, pem, passphrase):
    """逐类尝试解析 PEM 私钥;错误消息不含密钥内容。"""
    buf = io.StringIO(pem or "")
    last_cls = None
    for name in ("RSAKey", "ECDSAKey", "Ed25519Key"):
        cls = getattr(pm, name, None)
        if cls is None:
            continue
        last_cls = name
        try:
            return cls.from_private_key(buf, password=passphrase or None)
        except pm.PasswordRequiredException:
            raise _SSHError("私钥已加密,需提供 passphrase")
        except Exception:
            buf.seek(0)
    raise _SSHError("私钥解析失败(格式不支持%s或口令错误)" % (":" + last_cls if last_cls else ""))


def _open_ssh(host, port, username, creds, pinned_fp):
    """新建 SSH 短连接;TOFU pin 在应用层比对(AutoAddPolicy 只为拿 key)。
    返回 (client, fingerprint)。失败抛 _SSHError 子类(消息脱敏)。"""
    pm = _paramiko()
    cli = pm.SSHClient()
    cli.set_missing_host_key_policy(pm.AutoAddPolicy())
    kw = dict(hostname=host, port=int(port), username=username,
              timeout=SSH_TIMEOUT, banner_timeout=SSH_TIMEOUT, auth_timeout=SSH_TIMEOUT,
              look_for_keys=False, allow_agent=False)
    try:
        if creds.get("auth_method") == "private_key":
            try:
                kw["pkey"] = _load_pkey(pm, creds.get("private_key"), creds.get("passphrase"))
            except _SSHError as e:
                _close_quiet(cli)
                raise _SSHAuthError(str(e))   # 私钥解析/口令问题归入认证失败(消息已脱敏)
        else:
            kw["password"] = creds.get("password") or ""
        cli.connect(**kw)
    except _SSHError:
        _close_quiet(cli)
        raise
    except pm.AuthenticationException:
        _close_quiet(cli)
        raise _SSHAuthError("SSH 认证失败(用户名/私钥/密码不匹配)")
    except Exception as e:
        _close_quiet(cli)
        raise _SSHConnError("SSH 连接失败: %s" % _safe_err(e))
    try:
        t = cli.get_transport()
        hk = t.get_remote_server_key() if t else None
    except Exception as e:
        _close_quiet(cli)
        raise _SSHConnError("获取主机公钥失败: %s" % _safe_err(e))
    if hk is None:
        _close_quiet(cli)
        raise _SSHConnError("获取主机公钥失败(transport 未激活)")
    fp = _fingerprint(hk)
    if pinned_fp and fp != pinned_fp:
        _close_quiet(cli)
        raise _HostKeyChanged(pinned_fp, fp)
    return cli, fp


def _close_quiet(cli):
    try:
        cli.close()
    except Exception:
        pass


def _run(cli, cmd, timeout=SSH_TIMEOUT):
    """执行固定白名单命令;返回 (rc, stdout, stderr),UTF-8 errors=replace。"""
    try:
        _stdin, stdout, stderr = cli.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        rc = stdout.channel.recv_exit_status()
        return rc, out, err
    except Exception as e:
        raise _SSHError("远程命令执行失败: %s" % _safe_err(e))


def _tcp_check(host, port):
    try:
        with socket.create_connection((host, int(port)), timeout=TCP_TIMEOUT):
            return True
    except Exception:
        return False


def _sftp_mkdirs(sftp, path):
    """逐级 mkdir -p(SFTP 无递归 mkdir)。"""
    cur = ""
    for part in path.strip("/").split("/"):
        if not part:
            continue
        cur = cur + "/" + part
        try:
            sftp.stat(cur)
        except IOError:
            try:
                sftp.mkdir(cur)
            except IOError:
                pass   # 竞态下已建


# ---------- 请求体模型(同 refs/cloud/app.py 的 Pydantic body 模式) ----------
class ConnBody(BaseModel):
    name: str
    host: str
    port: int
    username: str
    auth_method: str
    private_key: Optional[str] = None
    passphrase: Optional[str] = None
    password: Optional[str] = None


class VerifyBody(BaseModel):
    trust_new_fingerprint: bool = False


class InputFileBody(BaseModel):
    path: str
    content: str


class JobBody(BaseModel):
    connection_id: str
    name: str = ""
    engine: str = "lammps"
    input_files: List[InputFileBody]
    entry: Optional[str] = "in.lammps"
    cores: int = 4
    minutes: int = 15


# ---------- 连接管理 ----------
@router.post("/connections", status_code=201)
def create_connection(body: ConnBody, u=Depends(current_user)):
    name = (body.name or "").strip()
    if not (1 <= len(name) <= 64):
        raise HTTPException(400, "name 须为 1~64 字符")
    host = (body.host or "").strip()
    if not _valid_host(host):
        raise HTTPException(400, "host 须为域名或 IP(拒绝 file:// 等 scheme;169.254.169.254 禁止)")
    if not (1 <= body.port <= 65535):
        raise HTTPException(400, "port 须在 1~65535")
    username = (body.username or "").strip()
    if not (1 <= len(username) <= 64) or any(c in username for c in " \t@:'\""):
        raise HTTPException(400, "username 须为 1~64 字符,且不含空白与 @:'\" 等特殊字符")
    if body.auth_method not in AUTH_METHODS:
        raise HTTPException(400, "auth_method 须为: %s" % "/".join(AUTH_METHODS))
    if body.auth_method == "private_key":
        if not body.private_key or len(body.private_key) < 64 or "PRIVATE KEY" not in body.private_key:
            raise HTTPException(400, "private_key 认证需提供 PEM 私钥全文(含 PRIVATE KEY 头)")
        if body.password:
            raise HTTPException(400, "private_key 认证不应携带 password")
    else:
        if not body.password:
            raise HTTPException(400, "password 认证需提供 password")
        if body.private_key:
            raise HTTPException(400, "password 认证不应携带 private_key")

    # 凭据整体 JSON 后 Fernet 加密;原文从此只在解密后的短连接调用栈里出现
    creds = {"auth_method": body.auth_method}
    if body.auth_method == "private_key":
        creds["private_key"] = body.private_key
        if body.passphrase:
            creds["passphrase"] = body.passphrase
    else:
        creds["password"] = body.password

    def _m(db):
        mine = [c for c in db["connections"] if c["owner"] == u["username"]]
        if len(mine) >= MAX_CONNECTIONS_PER_USER:
            return None
        rec = {
            "connection_id": "c_" + secrets.token_hex(6),
            "owner": u["username"], "name": name, "host": host, "port": body.port,
            "username": username, "auth_method": body.auth_method,
            "secret_ciphertext": _encrypt_creds(creds),
            "status": "unverified", "host_key_fingerprint": None,
            "scheduler": None, "engines": [], "home": None,
            "created_at": _now(), "last_verified_at": None, "last_error": None,
        }
        db["connections"].append(rec)
        return rec
    rec = _txn(_m)
    if rec is None:
        raise HTTPException(400, "连接数已达上限(%d 条/人),请先删除不再使用的连接" % MAX_CONNECTIONS_PER_USER)
    return _conn_created_view(rec)


@router.get("/connections")
def list_connections(u=Depends(current_user)):
    conns = _txn(lambda db: [c for c in db["connections"] if c["owner"] == u["username"]])
    conns.sort(key=lambda c: c.get("created_at") or "")
    return {"connections": [_conn_public(c) for c in conns]}


@router.get("/connections/{cid}")
def get_connection(cid: str, u=Depends(current_user)):
    c = _txn(lambda db: _find_owned_conn(db, cid, u["username"]))
    if not c:
        raise HTTPException(404, "无此连接")
    return _conn_public(c)


@router.delete("/connections/{cid}")
def delete_connection(cid: str, u=Depends(current_user)):
    def _m(db):
        before = len(db["connections"])
        db["connections"] = [c for c in db["connections"]
                             if not (c["connection_id"] == cid and c["owner"] == u["username"])]
        return len(db["connections"]) < before
    if not _txn(_m):
        raise HTTPException(404, "无此连接")
    # 作业台账保留:连接删除后作业只可查询台账
    return {"ok": True}


# ---------- 验证(同步, SSH timeout 25s) ----------
def _skipped_checks(checks, after):
    """前置失败的检查标记为 failed/skipped,保持 checks 结构完整。"""
    done = {c["name"] for c in checks}
    for name in CHECK_ORDER:
        if name not in done:
            checks.append({"name": name, "status": "failed", "detail": "跳过:前置检查未通过"})


@router.post("/connections/{cid}/verify")
def verify_connection(cid: str, body: VerifyBody, u=Depends(current_user)):
    conn = _txn(lambda db: _find_owned_conn(db, cid, u["username"]))
    if not conn:
        raise HTTPException(404, "无此连接")
    creds = _decrypt_creds(conn["secret_ciphertext"])
    checks = []

    def add(name, status, detail):
        checks.append({"name": name, "status": status, "detail": detail})

    def persist(status, fp, scheduler, engines, home, err_summary):
        def _m(db):
            c = _find_owned_conn(db, cid, u["username"])
            if not c:
                return
            if fp:
                c["host_key_fingerprint"] = fp   # TOFU:首次记录即 pin;trust 时替换
            c["status"] = status
            c["last_verified_at"] = now
            if scheduler is not None:
                c["scheduler"] = scheduler
            if engines is not None:
                c["engines"] = engines
            if home is not None:
                c["home"] = home
            c["last_error"] = err_summary
        _txn(_m)

    now = _now()
    scheduler, engines, home = None, [], None
    fp = None

    # ① TCP 可达
    if _tcp_check(conn["host"], conn["port"]):
        add("tcp", "passed", "TCP %s:%d 可达" % (conn["host"], conn["port"]))
    else:
        add("tcp", "failed", "TCP %s:%d 不可达(超时 %ds)" % (conn["host"], conn["port"], TCP_TIMEOUT))
        _skipped_checks(checks, "tcp")
        persist("error", None, None, None, None, "tcp 不可达")
        return _verify_response("error", checks, now, None, [])

    # ② host key(TOFU pin 比对)+ ③ 认证(paramiko 单次 connect 内完成)
    pinned = conn.get("host_key_fingerprint")
    cli = None
    try:
        try:
            cli, fp = _open_ssh(conn["host"], conn["port"], conn["username"], creds, pinned)
            if pinned:
                add("host_key", "passed", "主机公钥指纹与记录一致(%s)" % fp)
            else:
                add("host_key", "passed", "首次连接,已记录并 pin 主机公钥指纹(%s)" % fp)
            add("auth", "passed", "SSH 认证成功(%s 认证)" % conn["auth_method"])
        except _HostKeyChanged as e:
            if body.trust_new_fingerprint:
                cli, fp = _open_ssh(conn["host"], conn["port"], conn["username"], creds, None)
                add("host_key", "passed", "已接受新指纹(人工确认变更): %s" % fp)
                add("auth", "passed", "SSH 认证成功(%s 认证)" % conn["auth_method"])
            else:
                add("host_key", "failed", str(e))
                add("auth", "failed", "跳过:host key 校验失败")
                _skipped_checks(checks, "auth")
                persist("error", None, None, None, None, "HOST_KEY_CHANGED")
                return _verify_response("error", checks, now, None, [])
        except _SSHAuthError as e:
            add("host_key", "warn", "SSH 握手完成,但认证失败,指纹未能核对")
            add("auth", "failed", str(e))
            _skipped_checks(checks, "auth")
            persist("error", None, None, None, None, "SSH 认证失败")
            return _verify_response("error", checks, now, None, [])
        except _SSHConnError as e:
            add("host_key", "failed", "SSH 握手失败: %s" % e)
            add("auth", "failed", "跳过:SSH 握手失败")
            _skipped_checks(checks, "auth")
            persist("error", None, None, None, None, "SSH 握手失败")
            return _verify_response("error", checks, now, None, [])

        # ④ whoami(SSH 用户名一致性)
        rc, out, _err = _run(cli, CMD_WHOAMI)
        if rc == 0 and out.strip() == conn["username"]:
            add("whoami", "passed", "whoami = %s" % conn["username"])
        else:
            add("whoami", "failed", "whoami 返回 %r(rc=%d),与连接用户名 %r 不一致"
                % (out.strip()[:64], rc, conn["username"]))
            _skipped_checks(checks, "whoami")
            persist("error", fp, None, None, None, "whoami 不一致")
            return _verify_response("error", checks, now, None, [])

        # ⑤ 调度器检测
        rc, out, _err = _run(cli, CMD_SCHED_SLURM)
        if rc == 0:
            scheduler = "slurm"
            add("scheduler", "passed", "Slurm: %s" % " ".join(out.split())[:120])
        else:
            rc2, out2, _err2 = _run(cli, CMD_SCHED_PBS)
            if rc2 == 0:
                scheduler = "pbs"
                add("scheduler", "passed", "PBS: %s" % " ".join(out2.split())[:120])
            else:
                add("scheduler", "failed", "未检测到 Slurm(sbatch/squeue)或 PBS(qsub);无法提交作业")

        # ⑥ 引擎检测
        rc, out, _err = _run(cli, CMD_ENGINES)
        engines = [n for n in ("lmp", "lmp_mpi", "lammps")
                   if any(line.rstrip("/").endswith("/" + n) for line in out.splitlines())]
        if engines:
            add("engines", "passed", "可用引擎: %s" % ", ".join(engines))
        else:
            add("engines", "warn",
                "PATH 未找到 lmp/lmp_mpi/lammps;提交脚本将尝试 'module load lammps' 兜底")
        try:
            rc, mout, merr = _run(cli, CMD_MODULES, timeout=8)
            snippet = " ".join((mout + " " + merr).split())[:200]
            add("modules", "passed" if snippet else "warn", snippet or "module avail 无输出(可能无 module 工具)")
        except _SSHError:
            add("modules", "warn", "module avail 探测超时或失败(不影响连接可用性)")

        # ⑦ $HOME 与作业目录可写
        rc, out, _err = _run(cli, CMD_HOME)
        candidate = out.strip()
        if rc == 0 and _HOME_RE.match(candidate or ""):
            home = candidate
            add("home", "passed", "$HOME = %s" % home)
        else:
            add("home", "failed", "无法获取合法 $HOME(得到 %r,rc=%d;须为不含空白的绝对路径)"
                % (candidate[:64], rc))
            _skipped_checks(checks, "home")
            persist("error", fp, scheduler, engines, None, "$HOME 非法")
            return _verify_response("error", checks, now, scheduler, engines)
        rc, out, _err = _run(cli, CMD_JOBS_WRITABLE)
        if rc == 0 and "ok" in out:
            add("jobs_dir", "passed", "$HOME/mdagent/jobs 存在且可写")
        else:
            add("jobs_dir", "failed", "mkdir/touch $HOME/mdagent/jobs 失败(rc=%d)" % rc)
            persist("error", fp, scheduler, engines, home, "作业目录不可写")
            return _verify_response("error", checks, now, scheduler, engines)
    finally:
        if cli is not None:
            _close_quiet(cli)

    failed = {c["name"] for c in checks if c["status"] == "failed"}
    if failed & {"tcp", "host_key", "auth", "whoami", "home", "jobs_dir"}:
        status = "error"
    elif scheduler is None:
        status = "unsupported"   # 能登录但无调度器
    else:
        status = "ready"
    err_summary = None
    if status != "ready":
        err_summary = next((c["detail"] for c in checks if c["status"] == "failed"), "unsupported")
    persist(status, fp, scheduler, engines, home, err_summary)
    return _verify_response(status, checks, now, scheduler, engines)


def _verify_response(status, checks, verified_at, scheduler, engines):
    return {"status": status, "checks": checks, "verified_at": verified_at,
            "scheduler": scheduler, "engines": engines}


# ---------- 作业 ----------
def _validate_input_files(body: JobBody):
    """校验并归一化输入文件;返回 [(path, content_bytes)]。失败抛 400。"""
    files = body.input_files or []
    if not (1 <= len(files) <= MAX_INPUT_FILES):
        raise HTTPException(400, "input_files 须为 1~%d 个" % MAX_INPUT_FILES)
    seen, out = set(), []
    for f in files:
        p = f.path or ""
        if len(p) > MAX_INPUT_PATH_LEN or not _INPUT_PATH_RE.match(p):
            raise HTTPException(400, "输入路径非法(≤%d 字符的相对路径,禁绝对路径与 ..): %r"
                                % (MAX_INPUT_PATH_LEN, p[:64]))
        n = posixpath.normpath(p)
        if n.startswith("/") or n.startswith("..") or n == ".":
            raise HTTPException(400, "输入路径非法(禁绝对路径与 ..): %r" % p[:64])
        base = posixpath.basename(n)
        if n == "submit.sh" or re.match(r"^slurm-.*\.out$", base):
            raise HTTPException(400, "保留文件名不可用作输入: %r" % base)
        if n in seen:
            raise HTTPException(400, "输入路径重复: %r" % n)
        raw = (f.content or "").encode("utf-8")
        if len(raw) > MAX_INPUT_FILE_BYTES:
            raise HTTPException(400, "单文件超过 256KB 限制: %r" % n)
        seen.add(n)
        out.append((n, raw))
    entry = posixpath.normpath(body.entry or "in.lammps")
    if entry not in seen:
        raise HTTPException(400, "entry 必须是 input_files 之一的相对路径: %r" % entry)
    if not (CORES_RANGE[0] <= body.cores <= CORES_RANGE[1]):
        raise HTTPException(400, "cores 须在 %d~%d" % CORES_RANGE)
    if not (MINUTES_RANGE[0] <= body.minutes <= MINUTES_RANGE[1]):
        raise HTTPException(400, "minutes 须在 %d~%d" % MINUTES_RANGE)
    return out, entry


def _render_script(job_id, entry, cores, minutes):
    return _SBATCH_TMPL.format(short=job_id[2:10], cores=int(cores),
                               minutes=int(minutes), entry=entry)


def _active_jobs(db, username):
    return [j for j in db["jobs"].values()
            if j.get("owner") == username and j.get("state") not in TERMINAL_STATES]


@router.post("/jobs", status_code=202)
def submit_job(body: JobBody, u=Depends(current_user)):
    if body.engine != "lammps":
        raise HTTPException(400, "第一版仅支持 engine=lammps")
    name = (body.name or "").strip()
    if len(name) > 64:
        raise HTTPException(400, "name 须 ≤64 字符")
    files, entry = _validate_input_files(body)
    conn = _txn(lambda db: _find_owned_conn(db, body.connection_id, u["username"]))
    if not conn:
        raise HTTPException(404, "无此连接")
    if conn.get("status") != "ready":
        raise HTTPException(400, "连接未就绪(须先 verify 通过;当前 status=%s)" % conn.get("status"))
    if conn.get("scheduler") != "slurm":
        raise HTTPException(400, "第一版仅支持 Slurm 调度器提交(该连接 scheduler=%r)" % conn.get("scheduler"))
    if not conn.get("home"):
        raise HTTPException(400, "连接缺少 $HOME 信息,请重新 verify")

    job_id = "j_" + secrets.token_hex(8)
    remote_dir = posixpath.join(conn["home"], "mdagent", "jobs", job_id)
    creds = _decrypt_creds(conn["secret_ciphertext"])
    pinned = conn.get("host_key_fingerprint")
    script = _render_script(job_id, entry, body.cores, body.minutes)

    # SSH + SFTP:写输入文件与固定模板脚本,然后 sbatch --parsable
    try:
        cli, _fp = _open_ssh(conn["host"], conn["port"], conn["username"], creds, pinned)
    except _HostKeyChanged as e:
        raise HTTPException(409, str(e))
    except _SSHError as e:
        raise HTTPException(502, str(e))
    try:
        try:
            sftp = cli.open_sftp()
        except Exception as e:
            raise _SSHError("打开 SFTP 失败: %s" % _safe_err(e))
        _sftp_mkdirs(sftp, remote_dir)
        for rel, raw in files:
            target = posixpath.join(remote_dir, rel)
            parent = posixpath.dirname(target)
            if parent and parent != remote_dir:
                _sftp_mkdirs(sftp, parent)
            try:
                with sftp.open(target, "wb") as fh:
                    fh.write(raw)
            except Exception as e:
                raise _SSHError("写入输入文件失败 %r: %s" % (rel, _safe_err(e)))
        try:
            with sftp.open(posixpath.join(remote_dir, "submit.sh"), "wb") as fh:
                fh.write(script.encode("utf-8"))
        except Exception as e:
            raise _SSHError("写入提交脚本失败: %s" % _safe_err(e))
        rc, out, err = _run(cli, "cd %s && sbatch --parsable submit.sh" % remote_dir)
    except _SSHError as e:
        raise HTTPException(502, str(e))
    finally:
        _close_quiet(cli)
    if rc != 0:
        raise HTTPException(502, "sbatch 失败(rc=%d): %s" % (rc, " ".join((out + " " + err).split())[:200]))
    token = (out.strip().splitlines() or [""])[0].split(";")[0].strip()
    if not token.isdigit():
        raise HTTPException(502, "sbatch --parsable 输出无法解析为作业号: %r" % out.strip()[:120])
    scheduler_job_id = token

    now = _now()
    record = {
        "job_id": job_id, "owner": u["username"], "connection_id": conn["connection_id"],
        "name": name, "scheduler_job_id": scheduler_job_id, "remote_dir": remote_dir,
        "engine": "lammps", "entry": entry, "cores": int(body.cores),
        "minutes": int(body.minutes), "state": "queued",
        "created_at": now, "updated_at": now,
    }

    def _m(db):
        if len(_active_jobs(db, u["username"])) >= MAX_ACTIVE_JOBS_PER_USER:
            return None
        db["jobs"][job_id] = record
        return record
    rec = _txn(_m)
    if rec is None:
        # 已在集群提交但超并发限额:尽力取消,台账不落(响应明确告知)
        try:
            cli2, _ = _open_ssh(conn["host"], conn["port"], conn["username"], creds, pinned)
            try:
                _run(cli2, "scancel %s" % scheduler_job_id)
            finally:
                _close_quiet(cli2)
        except Exception:
            pass
        raise HTTPException(429, "并发未终态作业已达上限(%d),等作业结束或取消后再提交"
                            % MAX_ACTIVE_JOBS_PER_USER)
    return {"job_id": job_id, "scheduler_job_id": scheduler_job_id,
            "state": "queued", "remote_dir": remote_dir}


def _map_slurm_state(raw, exit_code=""):
    """Slurm 原始状态 → queued/running/succeeded/failed/cancelled/unknown。
    sacct 的 COMPLETED 仅表示脚本退出;引擎失败时脚本以非零码退出,
    因此 COMPLETED 且 ExitCode 非 0:0 映射为 failed。"""
    s = (raw or "").strip().upper()
    if not s:
        return "unknown"
    if s.startswith("CANCELLED"):
        return "cancelled"
    if s.startswith("PREEMPT"):
        return "cancelled"
    if s in ("PENDING", "CONFIGURING", "REQUEUED", "REQUEUE_HOLD", "RESV_DEL_HOLD",
             "RESIZING", "SIGNALING"):
        return "queued"
    if s in ("RUNNING", "COMPLETING", "SUSPENDED", "STAGE_OUT", "STOPPED"):
        return "running"
    if s == "COMPLETED":
        ec = (exit_code or "").strip()
        return "failed" if ec and ec != "0:0" else "succeeded"
    if s in ("FAILED", "TIMEOUT", "NODE_FAIL", "OUT_OF_MEMORY", "BOOT_FAIL",
             "DEADLINE", "LAUNCH_FAILED", "OUT_OF_NCPUS", "OUT_OF_MEMORY", "REVOKED"):
        return "failed"
    return "unknown"


def _query_live(conn, creds, scheduler_job_id):
    """squeue 查不到再 sacct;返回 (mapped_state, live_detail)。"""
    cli, _fp = _open_ssh(conn["host"], conn["port"], conn["username"], creds,
                         conn.get("host_key_fingerprint"))
    try:
        rc, out, _err = _run(cli, "squeue -j %s -h -o %%T,%%M,%%L,%%R" % scheduler_job_id)
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(",", 3)
            parts += [""] * (4 - len(parts))
            state_raw, used, left, reason = parts
            return _map_slurm_state(state_raw), {
                "source": "squeue", "scheduler_state": state_raw,
                "time_used": used, "time_left": left, "reason": reason,
            }
        rc, out, _err = _run(cli,
                             "sacct -j %s -X -n -o State,ExitCode,Elapsed,End --parsable2"
                             % scheduler_job_id)
        for line in out.splitlines():
            line = line.strip()
            if not line or line.lower().startswith("sacct:"):
                continue
            parts = line.split("|")
            parts += [""] * (4 - len(parts))
            state_raw, exit_code, elapsed, end = parts
            return _map_slurm_state(state_raw, exit_code), {
                "source": "sacct", "scheduler_state": state_raw,
                "exit_code": exit_code, "elapsed": elapsed, "end": end,
            }
        return "unknown", {"source": "scheduler", "scheduler_state": "",
                           "note": "squeue/sacct 均无此作业记录"}
    finally:
        _close_quiet(cli)


@router.get("/jobs")
def list_jobs(u=Depends(current_user)):
    jobs = _txn(lambda db: [j for j in db["jobs"].values() if j.get("owner") == u["username"]])
    jobs.sort(key=lambda j: ((j.get("created_at") or ""), j.get("job_id") or ""), reverse=True)
    return {"jobs": [_job_public(j) for j in jobs]}


@router.get("/jobs/{jid}")
def get_job(jid: str, u=Depends(current_user)):
    job = _txn(lambda db: _find_owned_job(db, jid, u["username"]))
    if not job:
        raise HTTPException(404, "无此作业")
    resp = _job_public(job)
    conn = _txn(lambda db: _find_owned_conn(db, job["connection_id"], u["username"]))
    if not conn:
        resp["live"] = None
        resp["note"] = "连接已删除,仅展示台账状态"
        return resp
    try:
        state, live = _query_live(conn, _decrypt_creds(conn["secret_ciphertext"]),
                                  job["scheduler_job_id"])
    except _SSHError:
        # SSH 失败:保留台账 state,标记 observed_offline,绝不虚构终态
        resp["live"] = None
        resp["observed_offline"] = True
        resp["note"] = "集群连接失败,展示最近一次台账状态(状态未知是否已变化)"
        return resp
    now = _now()

    def _m(db):
        j = _find_owned_job(db, jid, u["username"])
        if j:
            j["state"] = state
            j["updated_at"] = now
        return j
    _txn(_m)
    resp["state"] = state
    resp["updated_at"] = now
    resp["live"] = live
    resp["observed_offline"] = False
    return resp


@router.post("/jobs/{jid}/cancel")
def cancel_job(jid: str, u=Depends(current_user)):
    job = _txn(lambda db: _find_owned_job(db, jid, u["username"]))
    if not job:
        raise HTTPException(404, "无此作业")
    if job.get("state") in TERMINAL_STATES:
        raise HTTPException(409, "作业已处于终态(%s),无需取消" % job["state"])
    conn = _txn(lambda db: _find_owned_conn(db, job["connection_id"], u["username"]))
    if not conn:
        raise HTTPException(409, "连接已删除,无法取消(请联系集群管理员 scancel %s)"
                            % job.get("scheduler_job_id"))
    try:
        cli, _fp = _open_ssh(conn["host"], conn["port"], conn["username"],
                             _decrypt_creds(conn["secret_ciphertext"]),
                             conn.get("host_key_fingerprint"))
    except _HostKeyChanged as e:
        raise HTTPException(409, str(e))
    except _SSHError as e:
        raise HTTPException(502, str(e))
    try:
        rc, out, err = _run(cli, "scancel %s" % job["scheduler_job_id"])
    finally:
        _close_quiet(cli)
    if rc != 0:
        def _merr(db):
            j = _find_owned_job(db, jid, u["username"])
            if j:
                j["last_error"] = "scancel 失败: %s" % " ".join((out + " " + err).split())[:160]
                j["updated_at"] = _now()
        _txn(_merr)
        raise HTTPException(409, "scancel 失败(rc=%d): %s"
                            % (rc, " ".join((out + " " + err).split())[:200]))
    now = _now()

    def _m(db):
        j = _find_owned_job(db, jid, u["username"])
        if j:
            j["state"] = "cancel_requested"
            j["updated_at"] = now
            j.pop("last_error", None)
        return j
    _txn(_m)
    return {"ok": True, "job_id": jid, "state": "cancel_requested",
            "scheduler_job_id": job["scheduler_job_id"]}


@router.get("/jobs/{jid}/logs")
def job_logs(jid: str, u=Depends(current_user),
             bytes: int = Query(LOG_BYTES_DEFAULT, ge=1, le=LOG_BYTES_MAX)):
    job = _txn(lambda db: _find_owned_job(db, jid, u["username"]))
    if not job:
        raise HTTPException(404, "无此作业")
    conn = _txn(lambda db: _find_owned_conn(db, job["connection_id"], u["username"]))
    if not conn:
        raise HTTPException(409, "连接已删除,无法读取日志")
    if job.get("state") == "queued":
        return {"content": "", "bytes_read": 0, "eof": True, "note": "排队中"}
    try:
        cli, _fp = _open_ssh(conn["host"], conn["port"], conn["username"],
                             _decrypt_creds(conn["secret_ciphertext"]),
                             conn.get("host_key_fingerprint"))
    except _HostKeyChanged as e:
        raise HTTPException(409, str(e))
    except _SSHError as e:
        raise HTTPException(502, str(e))
    try:
        try:
            sftp = cli.open_sftp()
        except Exception as e:
            raise _SSHError("打开 SFTP 失败: %s" % _safe_err(e))
        try:
            names = [n for n in sftp.listdir(job["remote_dir"])
                     if re.match(r"^slurm-.+\.out$", n)]
        except IOError as e:
            raise _SSHError("列目录失败 %s: %s" % (job["remote_dir"], _safe_err(e)))
        if not names:
            return {"content": "", "bytes_read": 0, "eof": True, "note": "日志尚未生成"}
        path = posixpath.join(job["remote_dir"], sorted(names)[-1])
        st = sftp.stat(path)
        size = int(getattr(st, "st_size", 0))
        start = max(0, size - bytes)
        with sftp.open(path, "rb") as fh:
            fh.seek(start)
            data = fh.read(bytes)
        return {"content": data.decode("utf-8", "replace"), "bytes_read": len(data),
                "eof": start == 0}
    except _SSHError as e:
        raise HTTPException(502, str(e))
    finally:
        _close_quiet(cli)


# ---------- 挂载 ----------
def mount_hpc(app):
    """在 cloud/app.py 中调用:import hpc; hpc.mount_hpc(app)"""
    os.makedirs(os.path.dirname(_db_path()) or ".", exist_ok=True)

    @app.exception_handler(_SSHError)
    async def _hpc_ssh_error_handler(request, exc):
        # SSH 层异常(连接/认证/命令超时等)统一降级为 502 结构化错误,不泄漏凭据
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=502, content={"detail": _safe_err(exc)[:300]})

    app.include_router(router)
