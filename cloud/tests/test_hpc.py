#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cloud/hpc.py 单元测试 — mock paramiko(注入 fake 模块,绝不触网)。

运行:
    python3 -m venv /tmp/hpc-venv
    /tmp/hpc-venv/bin/pip install fastapi uvicorn paramiko cryptography pytest httpx
    /tmp/hpc-venv/bin/python -m pytest test_hpc.py -v
"""
import io
import json
import os
import re
import subprocess
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# 导入 hpc 前设置 Fernet 密钥(hpc 导入期校验);DB 路径由 fixture 逐测试覆盖
os.environ.setdefault(
    "MDAGENT_HPC_SECRET",
    __import__("cryptography.fernet", fromlist=["Fernet"]).Fernet.generate_key().decode())


# ======================= fake paramiko(注入 sys.modules,覆盖真实包) =======================
STATE = {}


def _reset_state():
    STATE.clear()
    STATE.update({
        "connect_error": None,      # connect() 抛的异常实例
        "pkey_error": None,         # 私钥解析抛的异常实例
        "hostkey": b"fake-host-key-blob-A",   # 服务器公钥 blob(指纹由此派生)
        "exec": {},                 # 精确命令 → (rc, out, err)
        "sbatch": (0, "12345;cluster\n", ""),
        "squeue": (0, "", ""),
        "sacct": (0, "", ""),
        "scancel": (0, "", ""),
        "fs": {},                   # SFTP 内存文件系统 path → bytes
        "dirs": set(),
        "connect_kwargs": None,
    })


_reset_state()


class _FakeSSHException(Exception):
    pass


class _FakeAuthenticationException(_FakeSSHException):
    pass


class _FakePasswordRequiredException(_FakeSSHException):
    pass


class _FakeKey:
    def __init__(self, blob):
        self._blob = blob

    def asbytes(self):
        return self._blob


class _FakeTransport:
    def get_remote_server_key(self):
        return _FakeKey(STATE["hostkey"])


class _FakeChannel:
    def __init__(self, rc):
        self._rc = rc

    def recv_exit_status(self):
        return self._rc


class _FakeStd:
    def __init__(self, data, rc):
        self._buf = io.BytesIO(data.encode() if isinstance(data, str) else data)
        self.channel = _FakeChannel(rc)

    def read(self, n=-1):
        return self._buf.read(n)

    def write(self, b):
        return len(b)

    def close(self):
        pass


class _FakeSSHClient:
    def __init__(self):
        self.closed = False

    def set_missing_host_key_policy(self, p):
        pass

    def connect(self, **kw):
        if STATE["connect_error"] is not None:
            raise STATE["connect_error"]
        STATE["connect_kwargs"] = kw

    def get_transport(self):
        return _FakeTransport()

    def exec_command(self, cmd, timeout=None):
        ex = STATE["exec"]
        if cmd in ex:
            rc, out, err = ex[cmd]
        elif "sbatch --parsable" in cmd:
            rc, out, err = STATE["sbatch"]
        elif cmd.startswith("squeue -j"):
            rc, out, err = STATE["squeue"]
        elif cmd.startswith("sacct -j"):
            rc, out, err = STATE["sacct"]
        elif cmd.startswith("scancel"):
            rc, out, err = STATE["scancel"]
        else:
            rc, out, err = 0, "", ""
        return _FakeStd("", rc), _FakeStd(out, rc), _FakeStd(err, rc)

    def open_sftp(self):
        return _FakeSFTP()

    def close(self):
        self.closed = True


class _FakeSFTPFile:
    def __init__(self, path, mode, data=b""):
        self.path, self.mode = path, mode
        self._buf = io.BytesIO(data)

    def read(self, n=-1):
        return self._buf.read(n)

    def seek(self, n):
        self._buf.seek(n)
        return n

    def write(self, b):
        self._buf.write(b)

    def close(self):
        if self.mode == "wb":
            STATE["fs"][self.path] = self._buf.getvalue()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


class _FakeSFTP:
    def stat(self, path):
        if path in STATE["dirs"] or path in STATE["fs"]:
            return types.SimpleNamespace(st_size=len(STATE["fs"].get(path, b"")),
                                         st_mtime=1700000000.0)
        raise IOError("no such file: %s" % path)

    def mkdir(self, path):
        STATE["dirs"].add(path)

    def listdir(self, path):
        pre = path.rstrip("/") + "/"
        names = set()
        for p in list(STATE["fs"]) + list(STATE["dirs"]):
            if p.startswith(pre) and "/" not in p[len(pre):]:
                names.add(p[len(pre):])
        return sorted(names)

    def open(self, path, mode="r"):
        if "r" in mode:
            data = STATE["fs"].get(path)
            if data is None:
                raise IOError("no such file: %s" % path)
            return _FakeSFTPFile(path, "rb", data)
        return _FakeSFTPFile(path, "wb")

    def close(self):
        pass


class _FakePKey:
    pass


class _FakeRSAKey:
    @staticmethod
    def from_private_key(file_obj, password=None):
        if STATE["pkey_error"] is not None:
            raise STATE["pkey_error"]
        return _FakePKey()


class _FakeNoKey:
    @staticmethod
    def from_private_key(file_obj, password=None):
        raise _FakeSSHException("unsupported key type")


FAKE_PARAMIKO = types.ModuleType("paramiko")
FAKE_PARAMIKO.SSHException = _FakeSSHException
FAKE_PARAMIKO.AuthenticationException = _FakeAuthenticationException
FAKE_PARAMIKO.PasswordRequiredException = _FakePasswordRequiredException
FAKE_PARAMIKO.SSHClient = _FakeSSHClient
FAKE_PARAMIKO.AutoAddPolicy = type("AutoAddPolicy", (), {})
FAKE_PARAMIKO.MissingHostKeyPolicy = type("MissingHostKeyPolicy", (), {})
FAKE_PARAMIKO.RSAKey = _FakeRSAKey
FAKE_PARAMIKO.ECDSAKey = _FakeNoKey
FAKE_PARAMIKO.Ed25519Key = _FakeNoKey
sys.modules["paramiko"] = FAKE_PARAMIKO   # hpc lazy import 拿到的是 fake

import hpc  # noqa: E402  (必须在 fake paramiko 注入与密钥设置之后)


# ======================= fake accounts(等价 app.py 认证依赖) =======================
class _FakeAccounts:
    def __init__(self):
        self.tokens = {
            "tok-alice": {"username": "alice", "role": "user"},
            "tok-bob": {"username": "bob", "role": "user"},
        }
        self.sessions = {"sess-alice": {"username": "alice", "role": "user"}}

    def by_token(self, t):
        return self.tokens.get(t)

    def by_session_token(self, s):
        return self.sessions.get(s)


def _fp_of(blob: bytes) -> str:
    import base64
    import hashlib
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


# ======================= fixtures =======================
@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("MDAGENT_HPC_DB", str(tmp_path / "hpc_jobs.json"))
    _reset_state()
    _scn_ready()
    monkeypatch.setattr(hpc, "_accounts", _FakeAccounts())
    monkeypatch.setattr(hpc, "_tcp_check", lambda host, port: True)  # 测试不触网
    yield


@pytest.fixture()
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    hpc.mount_hpc(app)
    with TestClient(app) as c:
        yield c


ALICE = {"Authorization": "Bearer tok-alice"}
BOB = {"Authorization": "Bearer tok-bob"}


def _scn_ready(username="hpcuser", home="/home/hpcuser"):
    """verify 全绿场景(Slurm + lmp + 可写)。"""
    STATE["exec"] = {
        hpc.CMD_WHOAMI: (0, username + "\n", ""),
        hpc.CMD_SCHED_SLURM: (0, "/usr/bin/sbatch\n/usr/bin/squeue\n", ""),
        hpc.CMD_ENGINES: (0, "/usr/bin/lmp\n", ""),
        hpc.CMD_MODULES: (0, "   lammps/2023.08   gromacs/2024 \n", ""),
        hpc.CMD_HOME: (0, home + "\n", ""),
        hpc.CMD_JOBS_WRITABLE: (0, "ok\n", ""),
    }
    STATE["hostkey"] = b"fake-host-key-blob-A"


def _mk_conn(client, **over):
    body = {"name": "T1", "host": "cloud.phadcloud.com", "port": 22298,
            "username": "hpcuser", "auth_method": "password", "password": "s3cret-pw-xyz"}
    body.update(over)
    return client.post("/v1/hpc/connections", json=body, headers=ALICE)


def _verify_ready(client, cid, **over):
    return client.post("/v1/hpc/connections/%s/verify" % cid,
                       json={"trust_new_fingerprint": False, **over}, headers=ALICE)


def _mk_job(client, cid, **over):
    body = {"connection_id": cid, "name": "demo", "engine": "lammps",
            "input_files": [{"path": "in.lammps", "content": "units lj\nrun 2000\n"}],
            "entry": "in.lammps", "cores": 4, "minutes": 15}
    body.update(over)
    return client.post("/v1/hpc/jobs", json=body, headers=ALICE)


# ======================= 认证 =======================
def test_auth_required(client):
    r = client.get("/v1/hpc/connections")
    assert r.status_code == 401
    r = client.get("/v1/hpc/connections", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_auth_session_cookie(client):
    r = client.get("/v1/hpc/connections", cookies={"mdexp_session": "sess-alice"})
    assert r.status_code == 200
    assert r.json() == {"connections": []}


# ======================= 连接管理 =======================
def test_connection_crud_and_owner_isolation(client):
    r = _mk_conn(client)
    assert r.status_code == 201
    c = r.json()
    assert re.match(r"^c_[0-9a-f]{12}$", c["connection_id"])
    assert c["status"] == "unverified"
    assert c["host_key_fingerprint"] is None
    assert set(c) == {"connection_id", "name", "host", "port", "username",
                      "auth_method", "status", "host_key_fingerprint", "created_at"}
    # 单条 / 列表(脱敏:无凭据字段)
    assert client.get("/v1/hpc/connections/" + c["connection_id"], headers=ALICE).status_code == 200
    lst = client.get("/v1/hpc/connections", headers=ALICE).json()["connections"]
    assert len(lst) == 1 and "secret_ciphertext" not in lst[0] and "password" not in lst[0]
    # 他人/不存在 → 404
    assert client.get("/v1/hpc/connections/" + c["connection_id"], headers=BOB).status_code == 404
    assert client.get("/v1/hpc/connections/c_000000000000", headers=ALICE).status_code == 404
    # 删除
    assert client.delete("/v1/hpc/connections/" + c["connection_id"], headers=BOB).status_code == 404
    assert client.delete("/v1/hpc/connections/" + c["connection_id"], headers=ALICE).json() == {"ok": True}
    assert client.get("/v1/hpc/connections/" + c["connection_id"], headers=ALICE).status_code == 404


def test_connection_validation(client):
    bad = [
        {"host": "file:///etc/passwd"},          # scheme
        {"host": "a b.example.com"},             # 空白
        {"host": "169.254.169.254"},             # 云元数据
        {"host": ""}, {"host": "x" * 256},
        {"port": 0}, {"port": 70000},
        {"username": ""}, {"username": "a b"},
        {"auth_method": "agent"},
        {"auth_method": "password", "password": ""},                     # 缺密码
        {"auth_method": "private_key", "private_key": "short"},         # 缺私钥
    ]
    for over in bad:
        r = _mk_conn(client, **over)
        assert r.status_code == 400, (over, r.text)
    # name 长度
    assert _mk_conn(client, name="x" * 65).status_code == 400


def test_connection_quota_8(client):
    for _ in range(8):
        assert _mk_conn(client).status_code == 201
    assert _mk_conn(client).status_code == 400


def test_credentials_encrypted_at_rest_and_never_leaked(client, tmp_path):
    pem = "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAASECRETMATERIAL1234567890\n-----END OPENSSH PRIVATE KEY-----\n"
    r = _mk_conn(client, auth_method="private_key", private_key=pem,
                 passphrase="pass-phrase-SECRET99", password=None)
    assert r.status_code == 201
    cid = r.json()["connection_id"]
    # 落盘为密文
    db_raw = open(os.environ["MDAGENT_HPC_DB"], encoding="utf-8").read()
    assert "AAAASECRETMATERIAL" not in db_raw
    assert "pass-phrase-SECRET99" not in db_raw and "PRIVATE KEY" not in db_raw
    # 认证失败场景:任何响应不回显凭据
    STATE["connect_error"] = _FakeAuthenticationException("Authentication failed.")
    rv = _verify_ready(client, cid)
    assert rv.status_code == 200 and rv.json()["status"] == "error"
    for text in (rv.text,
                 client.get("/v1/hpc/connections", headers=ALICE).text,
                 client.get("/v1/hpc/connections/" + cid, headers=ALICE).text):
        assert "AAAASECRETMATERIAL" not in text
        assert "pass-phrase-SECRET99" not in text
        assert "PRIVATE KEY" not in text


# ======================= verify =======================
def test_verify_ready_pins_fingerprint(client):
    cid = _mk_conn(client).json()["connection_id"]
    rv = _verify_ready(client, cid).json()
    assert rv["status"] == "ready"
    assert rv["scheduler"] == "slurm"
    assert rv["engines"] == ["lmp"]
    names = [c["name"] for c in rv["checks"]]
    assert names == hpc.CHECK_ORDER
    assert all(c["status"] in ("passed", "warn") for c in rv["checks"])
    # TOFU:指纹已记录 pin
    got = client.get("/v1/hpc/connections/" + cid, headers=ALICE).json()
    assert got["status"] == "ready"
    assert got["host_key_fingerprint"] == _fp_of(b"fake-host-key-blob-A")
    assert got["last_verified_at"] == rv["verified_at"]
    assert got["scheduler"] == "slurm" and got["home"] == "/home/hpcuser"


def test_verify_host_key_changed_and_trust(client):
    cid = _mk_conn(client).json()["connection_id"]
    assert _verify_ready(client, cid).json()["status"] == "ready"
    STATE["hostkey"] = b"another-cluster-key-blob-B"
    rv = _verify_ready(client, cid).json()
    assert rv["status"] == "error"
    hk = next(c for c in rv["checks"] if c["name"] == "host_key")
    assert hk["status"] == "failed" and "HOST_KEY_CHANGED" in hk["detail"]
    # 指纹未被静默替换
    got = client.get("/v1/hpc/connections/" + cid, headers=ALICE).json()
    assert got["host_key_fingerprint"] == _fp_of(b"fake-host-key-blob-A")
    assert got["status"] == "error"
    # trust_new_fingerprint=true 才接受变更
    rv2 = _verify_ready(client, cid, trust_new_fingerprint=True).json()
    assert rv2["status"] == "ready"
    got2 = client.get("/v1/hpc/connections/" + cid, headers=ALICE).json()
    assert got2["host_key_fingerprint"] == _fp_of(b"another-cluster-key-blob-B")


def test_verify_unsupported_no_scheduler(client):
    cid = _mk_conn(client).json()["connection_id"]
    STATE["exec"][hpc.CMD_SCHED_SLURM] = (1, "", "not found")
    STATE["exec"][hpc.CMD_SCHED_PBS] = (1, "", "not found")
    rv = _verify_ready(client, cid).json()
    assert rv["status"] == "unsupported"
    assert rv["scheduler"] is None
    assert client.get("/v1/hpc/connections/" + cid, headers=ALICE).json()["status"] == "unsupported"


def test_verify_auth_failure_and_whoami_mismatch(client):
    cid = _mk_conn(client).json()["connection_id"]
    STATE["connect_error"] = _FakeAuthenticationException("Authentication failed.")
    rv = _verify_ready(client, cid).json()
    assert rv["status"] == "error"
    auth = next(c for c in rv["checks"] if c["name"] == "auth")
    assert auth["status"] == "failed" and "s3cret" not in auth["detail"]
    # whoami 不一致
    STATE["connect_error"] = None
    STATE["exec"][hpc.CMD_WHOAMI] = (0, "someoneelse\n", "")
    rv2 = _verify_ready(client, cid).json()
    assert rv2["status"] == "error"
    assert next(c for c in rv2["checks"] if c["name"] == "whoami")["status"] == "failed"


def test_verify_tcp_unreachable(client, monkeypatch):
    cid = _mk_conn(client).json()["connection_id"]
    monkeypatch.setattr(hpc, "_tcp_check", lambda host, port: False)
    rv = _verify_ready(client, cid).json()
    assert rv["status"] == "error"
    assert next(c for c in rv["checks"] if c["name"] == "tcp")["status"] == "failed"


def test_verify_missing_conn_404(client):
    assert _verify_ready(client, "c_000000000000").status_code == 404


# ======================= 作业 =======================
def _ready_conn(client):
    cid = _mk_conn(client).json()["connection_id"]
    assert _verify_ready(client, cid).json()["status"] == "ready"
    return cid


def test_job_submit_writes_files_and_script(client):
    cid = _ready_conn(client)
    r = _mk_job(client, cid, cores=8, minutes=30)
    assert r.status_code == 202, r.text
    j = r.json()
    assert set(j) == {"job_id", "scheduler_job_id", "state", "remote_dir"}
    assert re.match(r"^j_[0-9a-f]{16}$", j["job_id"])
    assert j["scheduler_job_id"] == "12345"
    assert j["state"] == "queued"
    assert j["remote_dir"] == "/home/hpcuser/mdagent/jobs/" + j["job_id"]
    # SFTP 侧:输入文件 + 固定模板脚本
    assert STATE["fs"][j["remote_dir"] + "/in.lammps"] == b"units lj\nrun 2000\n"
    script = STATE["fs"][j["remote_dir"] + "/submit.sh"].decode()
    assert script == (
        "#!/bin/bash\n"
        "#SBATCH --job-name=mdagent-%s\n" % j["job_id"][2:10] +
        "#SBATCH -N 1\n"
        "#SBATCH -n 8\n"
        "#SBATCH -t 30\n"
        "#SBATCH -o slurm-%x.out\n"
        "#SBATCH -e slurm-%x.out\n"
        "cd $SLURM_SUBMIT_DIR\n"
        'export PATH="$HOME/software/lammps/bin:$PATH"\n'
        "set +e\n"
        "MDAGENT_ENGINE_RC=127\n"
        "if lmp -in in.lammps -log log.lammps 2>&1; then MDAGENT_ENGINE_RC=0\n"
        "elif lmp_mpi -in in.lammps 2>&1; then MDAGENT_ENGINE_RC=0\n"
        "elif module load lammps 2>/dev/null && lmp -in in.lammps 2>&1; then MDAGENT_ENGINE_RC=0\n"
        "else MDAGENT_ENGINE_RC=$?; fi\n"
        'echo "MDAGENT_EXIT_CODE=$MDAGENT_ENGINE_RC"\n'
        "exit $MDAGENT_ENGINE_RC\n")
    # 台账
    lst = client.get("/v1/hpc/jobs", headers=ALICE).json()["jobs"]
    assert len(lst) == 1
    rec = lst[0]
    assert rec["job_id"] == j["job_id"] and rec["owner"] == "alice"
    assert rec["state"] == "queued" and rec["scheduler_job_id"] == "12345"
    assert rec["entry"] == "in.lammps" and rec["cores"] == 8 and rec["minutes"] == 30
    # 他人不可见
    assert client.get("/v1/hpc/jobs/" + j["job_id"], headers=BOB).status_code == 404


def test_job_submit_validation(client):
    cid = _ready_conn(client)
    big = "x" * (256 * 1024 + 1)
    cases = [
        {"input_files": []},                                        # 空文件
        {"input_files": [{"path": "in%d.lammps" % i, "content": "y"} for i in range(11)]},  # >10
        {"input_files": [{"path": "/abs/in.lammps", "content": "y"}]},   # 绝对路径
        {"input_files": [{"path": "../esc.lammps", "content": "y"}]},    # ..
        {"input_files": [{"path": "a/../../esc.lammps", "content": "y"}]},
        {"input_files": [{"path": "in;rm.lammps", "content": "y"}]},     # shell 元字符
        {"input_files": [{"path": "submit.sh", "content": "y"}]},        # 保留名
        {"input_files": [{"path": "in.lammps", "content": big}]},        # >256KB
        {"entry": "other.in"},                                       # entry 不在文件列表
        {"cores": 0}, {"cores": 65}, {"minutes": 0}, {"minutes": 121},
        {"engine": "gromacs"},
    ]
    for over in cases:
        r = _mk_job(client, cid, **over)
        assert r.status_code == 400, (over, r.text)
    # 未验证连接 → 400
    cid2 = _mk_conn(client).json()["connection_id"]
    assert _mk_job(client, cid2).status_code == 400
    # 不存在连接 → 404
    assert _mk_job(client, "c_000000000000").status_code == 404
    # 他人连接 → 404(以 bob 身份提交 alice 的连接)
    r = client.post("/v1/hpc/jobs", json={"connection_id": cid, "engine": "lammps",
                                         "input_files": [{"path": "in.lammps", "content": "x"}],
                                         "entry": "in.lammps"}, headers=BOB)
    assert r.status_code == 404


def test_job_concurrency_limit_3(client):
    cid = _ready_conn(client)
    ids = []
    for _ in range(3):
        r = _mk_job(client, cid)
        assert r.status_code == 202
        ids.append(r.json()["job_id"])
    r = _mk_job(client, cid)
    assert r.status_code == 429
    # 推进一个到终态后可再提交
    STATE["squeue"] = (0, "", "")
    STATE["sacct"] = (0, "COMPLETED|0:0|00:00:05|2026-10-08T10:00:00\n", "")
    assert client.get("/v1/hpc/jobs/" + ids[0], headers=ALICE).json()["state"] == "succeeded"
    assert _mk_job(client, cid).status_code == 202


def test_job_live_state_mapping(client):
    cid = _ready_conn(client)
    jid = _mk_job(client, cid).json()["job_id"]
    # squeue: PENDING → queued;RUNNING → running
    STATE["squeue"] = (0, "PENDING,0:00,15:00,(Priority)\n", "")
    r = client.get("/v1/hpc/jobs/" + jid, headers=ALICE).json()
    assert r["state"] == "queued" and r["live"]["source"] == "squeue"
    STATE["squeue"] = (0, "RUNNING,0:05,14:55,Resources\n", "")
    assert client.get("/v1/hpc/jobs/" + jid, headers=ALICE).json()["state"] == "running"
    # squeue 空 → sacct
    STATE["squeue"] = (0, "", "")
    for raw, expect in [("COMPLETED|0:0|00:00:05|2026-10-08T10:00:00", "succeeded"),
                        ("FAILED|1:0|00:00:02|2026-10-08T10:00:00", "failed"),
                        ("CANCELLED by 999|0:0|00:00:01|2026-10-08T10:00:00", "cancelled"),
                        ("TIMEOUT|0:0|00:15:00|2026-10-08T10:00:00", "failed")]:
        STATE["sacct"] = (0, raw + "\n", "")
        r = client.get("/v1/hpc/jobs/" + jid, headers=ALICE).json()
        assert r["state"] == expect, (raw, r)
        assert r["live"]["source"] == "sacct" and r["live"]["scheduler_state"] == raw.split("|")[0]
    # 双空 → unknown(不虚构)
    STATE["sacct"] = (0, "", "")
    r = client.get("/v1/hpc/jobs/" + jid, headers=ALICE).json()
    assert r["state"] == "unknown"
    # 台账 state 已同步
    rec = client.get("/v1/hpc/jobs", headers=ALICE).json()["jobs"][0]
    assert rec["state"] == "unknown"


def test_job_get_offline_keeps_ledger_state(client):
    cid = _ready_conn(client)
    jid = _mk_job(client, cid).json()["job_id"]
    STATE["connect_error"] = _FakeSSHException("connection reset by peer")
    r = client.get("/v1/hpc/jobs/" + jid, headers=ALICE)
    assert r.status_code == 200
    body = r.json()
    assert body["observed_offline"] is True
    assert body["state"] == "queued"          # 保留台账 state,不虚构终态
    assert body["live"] is None


def test_job_get_after_connection_deleted(client):
    cid = _ready_conn(client)
    jid = _mk_job(client, cid).json()["job_id"]
    assert client.delete("/v1/hpc/connections/" + cid, headers=ALICE).status_code == 200
    body = client.get("/v1/hpc/jobs/" + jid, headers=ALICE).json()
    assert body["job_id"] == jid and body["note"] == "连接已删除,仅展示台账状态"
    assert client.post("/v1/hpc/jobs/" + jid + "/cancel", headers=ALICE).status_code == 409
    assert client.get("/v1/hpc/jobs/" + jid + "/logs", headers=ALICE).status_code == 409


def test_job_cancel(client):
    cid = _ready_conn(client)
    jid = _mk_job(client, cid).json()["job_id"]
    r = client.post("/v1/hpc/jobs/%s/cancel" % jid, headers=ALICE)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "job_id": jid, "state": "cancel_requested",
                        "scheduler_job_id": "12345"}
    rec = client.get("/v1/hpc/jobs", headers=ALICE).json()["jobs"][0]
    assert rec["state"] == "cancel_requested"
    # scancel 失败 → 409 + last_error
    jid2 = _mk_job(client, cid).json()["job_id"]
    STATE["scancel"] = (1, "", "scancel: error parsing job id\n")
    assert client.post("/v1/hpc/jobs/%s/cancel" % jid2, headers=ALICE).status_code == 409
    rec2 = next(j for j in client.get("/v1/hpc/jobs", headers=ALICE).json()["jobs"]
                if j["job_id"] == jid2)
    assert "scancel 失败" in rec2["last_error"]
    # 终态作业不可取消
    STATE["squeue"] = (0, "", "")
    STATE["sacct"] = (0, "COMPLETED|0:0|00:00:05|2026-10-08T10:00:00\n", "")
    jid3 = _mk_job(client, cid).json()["job_id"]
    assert client.get("/v1/hpc/jobs/" + jid3, headers=ALICE).json()["state"] == "succeeded"
    assert client.post("/v1/hpc/jobs/%s/cancel" % jid3, headers=ALICE).status_code == 409


def test_job_logs_tail(client):
    cid = _ready_conn(client)
    jid = _mk_job(client, cid).json()["job_id"]
    remote = "/home/hpcuser/mdagent/jobs/" + jid
    # 排队中:无日志
    assert client.get("/v1/hpc/jobs/%s/logs" % jid, headers=ALICE).json()["note"] == "排队中"
    # running 后读尾部
    STATE["squeue"] = (0, "RUNNING,0:05,14:55,\n", "")
    client.get("/v1/hpc/jobs/" + jid, headers=ALICE)
    data = ("HEAD" + "x" * 20000).encode()
    STATE["fs"][remote + "/slurm-mdagent-" + jid[2:10] + ".out"] = data
    r = client.get("/v1/hpc/jobs/%s/logs?bytes=4096" % jid, headers=ALICE).json()
    assert r["bytes_read"] == 4096 and r["eof"] is False
    assert r["content"] == "x" * 4096            # 尾部,不含 HEAD
    r2 = client.get("/v1/hpc/jobs/%s/logs?bytes=65536" % jid, headers=ALICE).json()
    assert r2["eof"] is True and r2["bytes_read"] == len(data)
    assert r2["content"].startswith("HEAD")
    # 尚未生成日志
    del STATE["fs"][remote + "/slurm-mdagent-" + jid[2:10] + ".out"]
    assert client.get("/v1/hpc/jobs/%s/logs" % jid, headers=ALICE).json()["note"] == "日志尚未生成"
    # bytes 参数边界(FastAPI Query 校验)
    assert client.get("/v1/hpc/jobs/%s/logs?bytes=0" % jid, headers=ALICE).status_code == 422
    assert client.get("/v1/hpc/jobs/%s/logs?bytes=65537" % jid, headers=ALICE).status_code == 422


def test_job_submit_host_key_changed_409(client):
    cid = _ready_conn(client)
    STATE["hostkey"] = b"evil-replacement-key"
    r = _mk_job(client, cid)
    assert r.status_code == 409
    assert "HOST_KEY_CHANGED" in r.json()["detail"]


def test_job_submit_ssh_failure_502(client):
    cid = _ready_conn(client)
    STATE["connect_error"] = _FakeSSHException("timed out")
    assert _mk_job(client, cid).status_code == 502
    # 台账未落
    assert client.get("/v1/hpc/jobs", headers=ALICE).json()["jobs"] == []


def test_private_key_auth_uses_pkey_not_password(client):
    pem = "-----BEGIN OPENSSH PRIVATE KEY-----\n" + "A" * 200 + "\n-----END OPENSSH PRIVATE KEY-----\n"
    cid = _mk_conn(client, auth_method="private_key", private_key=pem,
                   passphrase="phrase", password=None).json()["connection_id"]
    assert _verify_ready(client, cid).json()["status"] == "ready"
    kw = STATE["connect_kwargs"]
    assert "pkey" in kw and "password" not in kw
    assert kw["username"] == "hpcuser" and kw["port"] == 22298
    assert kw["look_for_keys"] is False and kw["allow_agent"] is False
    assert kw["timeout"] == 25
    # 私钥解析失败(口令错)→ verify error,响应不含 PEM
    STATE["pkey_error"] = _FakeSSHException("could not deserialize key data")
    rv = _verify_ready(client, cid).json()
    assert rv["status"] == "error"


def test_ledger_persistence_pattern(client, tmp_path):
    """台账走 accounts.py 的 _txn 模式:tmp+replace 原子写、0600、损坏隔离。"""
    cid = _ready_conn(client)
    _mk_job(client, cid)
    p = os.environ["MDAGENT_HPC_DB"]
    assert oct(os.stat(p).st_mode & 0o777) == "0o600"
    with open(p, encoding="utf-8") as f:
        db = json.load(f)
    assert db["version"] == 1 and len(db["jobs"]) == 1 and len(db["connections"]) == 1
    # 损坏文件隔离:改坏后可继续服务(重命名为 .corrupt.*)
    with open(p, "w", encoding="utf-8") as f:
        f.write("{not json")
    assert client.get("/v1/hpc/connections", headers=ALICE).status_code == 200
    assert any(".corrupt." in fn for fn in os.listdir(os.path.dirname(p)))


def test_missing_secret_runtime_error():
    env = {k: v for k, v in os.environ.items() if k != "MDAGENT_HPC_SECRET"}
    r = subprocess.run([sys.executable, "-c", "import hpc"], cwd=HERE,
                       env=env, capture_output=True, text=True)
    assert r.returncode != 0
    assert "MDAGENT_HPC_SECRET" in r.stderr
    assert "Fernet" in r.stderr


def test_bad_secret_runtime_error():
    env = dict(os.environ)
    env["MDAGENT_HPC_SECRET"] = "not-a-fernet-key"
    r = subprocess.run([sys.executable, "-c", "import hpc"], cwd=HERE,
                       env=env, capture_output=True, text=True)
    assert r.returncode != 0
    assert "Fernet" in r.stderr


def test_no_arbitrary_shell_surface():
    """红线:模块内不存在任意 shell 执行口(os.system/subprocess 等)。"""
    src = open(os.path.join(os.path.dirname(HERE), "hpc.py"), encoding="utf-8").read()
    assert "os.system" not in src
    assert "subprocess" not in src
    assert "shell=True" not in src
    assert "popen" not in src.lower()
    # f-string 命令仅允许 squeue/sacct/scancel/sbatch 固定格式(插值仅为数字 ID 与服务端拼的 remote_dir)
    for m in re.finditer(r'_run\(cli2?,\s*f"([^"]+)"', src):
        assert re.match(r"^(squeue -j|sacct -j|scancel|cd /home)", m.group(1)), m.group(1)


def test_map_slurm_state_exit_code():
    # COMPLETED 仅表示脚本退出;引擎非零退出码必须映射 failed
    assert hpc._map_slurm_state("COMPLETED", "0:0") == "succeeded"
    assert hpc._map_slurm_state("COMPLETED", "127:0") == "failed"
    assert hpc._map_slurm_state("COMPLETED", "1:0") == "failed"
    assert hpc._map_slurm_state("COMPLETED") == "succeeded"
    assert hpc._map_slurm_state("RUNNING") == "running"
    assert hpc._map_slurm_state("CANCELLED by 12345") == "cancelled"
