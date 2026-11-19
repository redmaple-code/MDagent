# -*- coding: utf-8 -*-
"""第 19 轮 SCNet 闸实弹攻击 —— 共享沙盒骨架(非攻击项,仅设施)。

铁律落实:
- MDSIM_DATA_ROOT 指向 /tmp 临时目录,假凭证自建,绝不读真实凭证;
- urllib.request.urlopen 硬断:任何绕过 _http 的真实网络调用直接炸;
- scnet_client._http 换为计数 fake:所有"外发"经此计数,拒绝路径断言零/已知次数;
- 不改被测代码,不改真实 md_config.json(用 monkeypatch _config 注入合成配置)。

每个 s*_ 攻击脚本 sys.path 插入本目录后 `from atk_common import *` 即可独立复跑:
    python3.11 s1_no_tty_submit.py
退出码: 0 = 闸有效/行为符合设计; 1 = 打穿(发现)。
"""
import base64
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(AGENT_ROOT, "scripts"))

TMP = tempfile.mkdtemp(prefix="scnet-atk19-")
os.environ["MDSIM_DATA_ROOT"] = TMP

import urllib.request  # noqa: E402

# 硬断真实网络:任何未经 fake 的 urlopen 都是事故
def _no_net(*a, **kw):
    raise AssertionError("REAL NETWORK ACCESS ATTEMPTED: %r" % (a[:2],))

urllib.request.urlopen = _no_net

import scnet_client  # noqa: E402

import atexit  # noqa: E402
atexit.register(lambda: shutil.rmtree(TMP, ignore_errors=True))


# ---------------------------------------------------------------- 假凭证
def write_fake_credentials():
    d = os.path.join(TMP, "dsh-home")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, ".credentials.yaml")
    with open(p, "w") as f:
        f.write("SCNET_USER: fakeuser\nSCNET_ACCESS_KEY: AKFAKE\n"
                "SCNET_SECRET_KEY: SKFAKE\n")
    os.chmod(p, 0o600)
    return p


# ---------------------------------------------------------------- 合成配置(不动真实 md_config.json)
GOOD_SCNET = {"enabled": True, "auto_submit": True, "cluster_id": "11112",
              "work_root": "YMY-AI",
              "lmp_cmd": "mpirun -np {nproc} lmp_mpi -in {input}",
              "whitelist": {"queues": ["debug", "normal"],
                            "max_nodes": 2, "max_walltime_h": 24}}


def patch_config(scnet=None):
    """monkeypatch scnet_client._config,注入合成 scnet 段。"""
    cfg = {"scnet": (scnet if scnet is not None else dict(GOOD_SCNET))}
    scnet_client._config = lambda: (cfg, cfg["scnet"])
    return cfg


# ---------------------------------------------------------------- 计数 fake HTTP
class FakeHTTP:
    """所有外发经此计数。handlers 命中返回数据;未命中 URL 记 500 样式错误。
    calls: [(method, url, body)]。submit_calls() 滤出真正的作业提交。"""

    def __init__(self, regions=None, fail_on=None):
        self.calls = []
        self.fail_on = fail_on or {}  # url 子串 -> 异常
        regs = regions or [
            {"id": 0, "name": "ac",
             "clusterUserInfo": {"userName": "fake", "homePath": "/p"},
             "hpcUrls": [], "efileUrls": []},
            {"id": 11112, "name": "计算中心A",
             "clusterUserInfo": {"userName": "fake", "homePath": "/public/home/fake"},
             "hpcUrls": [{"enable": "true", "url": "https://hpc.invalid/hpc"}],
             "efileUrls": [{"enable": "true", "url": "https://efile.invalid/efile"}]},
        ]
        self._regions = regs

    def __call__(self, method, url, token=None, body=None, headers=None, timeout=30):
        self.calls.append((method, url, body))
        for sub, exc in self.fail_on.items():
            if sub in url:
                raise exc
        if url.endswith("/api/user/v3/tokens"):
            return [{"clusterId": str(r["id"]), "clusterName": r["name"],
                     "token": _jwt(9_999_999_999_999)} for r in self._regions]
        if url.endswith("/ac/openapi/v2/center"):
            return self._regions[1] if len(self._regions) > 1 else self._regions[0]
        if url.endswith("/openapi/v2/cluster"):
            return [{"id": 1638523853, "text": "Cluster", "JobManagerType": "SLURM"}]
        if "/file/mkdir" in url or "/file/upload" in url:
            return {}
        if url.endswith("/apptemplates/BASIC/BASE/job"):
            return {"jobId": "424242"}
        raise scnet_client.ScnetError("fake_404", "unhandled url in fake", url)

    def submit_calls(self):
        return [c for c in self.calls if c[1].endswith("/apptemplates/BASIC/BASE/job")]

    def upload_calls(self):
        return [c for c in self.calls if "/file/upload" in c[1]]


def install_http(fake):
    scnet_client._http = fake
    return fake


def _jwt(expire_ms):
    payload = json.dumps({"expireTime": str(expire_ms)}).encode()
    seg = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    return "h.%s.s" % seg


# ---------------------------------------------------------------- 其他小工具
def make_file(name, content):
    p = os.path.join(TMP, "inbox", name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(content)
    return p


def audit_entries():
    p = os.path.join(TMP, "events", "scnet_submit.jsonl")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def reset_state():
    for sub in ("events", "state"):
        shutil.rmtree(os.path.join(TMP, sub), ignore_errors=True)


class NoTTY:
    def isatty(self):
        return False

    def read(self, *a):
        return ""


import contextlib


@contextlib.contextmanager
def no_tty():
    """把 stdin 换成无 TTY 管道样对象;input 被调直接炸(防卡死/防偷读)。"""
    import builtins
    import unittest.mock as mock
    with mock.patch.object(sys, "stdin", NoTTY()), \
            mock.patch.object(builtins, "input",
                              lambda prompt="": (_ for _ in ()).throw(
                                  AssertionError("input() called under NoTTY"))):
        yield


def write_approval_file(ph, payload):
    p = scnet_client._approval_path(ph)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    return p


def verdict(kind, name, detail):
    """kind: PASS(闸有效) / FINDING(打穿) / ACCEPT(设计意图)"""
    print("VERDICT|%s|%s|%s" % (kind, name, detail.replace("\n", " ")))


def bootstrap(scnet=None, regions=None, fail_on=None):
    """一行装好: 假凭证 + 合成配置 + 计数 fake HTTP。返回 FakeHTTP。"""
    write_fake_credentials()
    patch_config(scnet)
    return install_http(FakeHTTP(regions=regions, fail_on=fail_on))


# ---------------------------------------------------------------- 复攻增补(r*): 修复后口径
def eff_plan_hash(case_id, queue, nodes, ppn, walltime, cmd, files,
                  cluster_id="", work_root=None):
    """按 submit_job 生效值口径算 plan_hash(修复后 cluster_id/work_root 全绑定,
    手写批准必须与 submit_job 内部 ph 一致才谈得上测试后续闸)。"""
    _, scnet = scnet_client._config()
    eff_cluster = str(cluster_id or scnet.get("cluster_id") or "")
    wr = str(scnet.get("work_root") or "YMY-AI") if work_root is None else work_root
    return scnet_client.plan_hash(case_id, queue, int(nodes), int(ppn), walltime,
                                  cmd, files, cluster_id=eff_cluster, work_root=wr)


def write_eff_approval(case_id, queue, nodes, ppn, walltime, cmd, files,
                       age=0, cluster_id=""):
    """写一份 schema 完全合法的批准文件(同 uid 信任边界内,E2),返回 ph。"""
    import time as _t
    ph = eff_plan_hash(case_id, queue, nodes, ppn, walltime, cmd, files,
                       cluster_id=cluster_id)
    write_approval_file(ph, {"plan_hash": ph,
                             "approved_epoch": _t.time() - age})
    return ph


class YesTTY:
    def isatty(self):
        return True


@contextlib.contextmanager
def human_tty(pin="", answer="YES"):
    """模拟真人在交互终端: stdin 是 TTY、input 回答 answer、getpass 喂 pin。"""
    import builtins
    import unittest.mock as mock
    with mock.patch.object(sys, "stdin", YesTTY()), \
            mock.patch.object(builtins, "input", lambda prompt="": answer), \
            mock.patch.object(scnet_client.getpass, "getpass",
                              lambda prompt="": pin):
        yield


def human_approve(ph, pin="abcd1234"):  # HD1 后 PIN 须 >=8 位且非纯数字
    """走正规 approve() 人工通道(TTY+YES+PIN 全猴补丁),返回 approve 结果。"""
    with human_tty(pin=pin):
        return scnet_client.approve(ph)
