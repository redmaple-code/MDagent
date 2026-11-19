# -*- coding: utf-8 -*-
"""scnet_client 回归测试(2026-09-03):签名算法、JWT 过期解析、token 缓存、
白名单闸门(队列/节点/墙时/双开关)、提交载荷组装、审计落账——全部 mock HTTP,
不打真实开放平台。"""
import base64
import hashlib
import hmac
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="scnet-t")
os.environ["MDSIM_DATA_ROOT"] = TMP
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import scnet_client  # noqa: E402

import atexit  # noqa: E402
atexit.register(lambda: shutil.rmtree(TMP, ignore_errors=True))


class _EnvMixin(unittest.TestCase):
    """MDSIM_DATA_ROOT 必须在 setUp 重指:discover 先导入全部测试模块,
    后导入模块(如 test_workbench_api)的模块级 env 赋值会盖掉本模块的;
    paths.data_root() 每次调用动态读 env,setUp 重指即恢复隔离。"""

    def setUp(self):
        self._old_root = os.environ.get("MDSIM_DATA_ROOT")
        os.environ["MDSIM_DATA_ROOT"] = TMP

    def tearDown(self):
        if self._old_root is None:
            del os.environ["MDSIM_DATA_ROOT"]
        else:
            os.environ["MDSIM_DATA_ROOT"] = self._old_root


def _jwt(expire_ms):
    payload = json.dumps({"expireTime": str(expire_ms)}).encode()
    seg = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    return "h.%s.s" % seg


def _fake_regions():
    return {"0": {"name": "ac", "token": _jwt(9_999_999_999_999), "expire_ms": 9_999_999_999_999},
            "11112": {"name": "计算中心A", "token": _jwt(9_999_999_999_999),
                      "expire_ms": 9_999_999_999_999}}


class TestSign(unittest.TestCase):
    def test_matches_manual_hmac(self):
        sig = scnet_client.sign("AK123", "1764597591", "bob", "SK456")
        expect = hmac.new(
            b"SK456", b'{"accessKey":"AK123","timestamp":"1764597591","user":"bob"}',
            hashlib.sha256).hexdigest()
        self.assertEqual(sig, expect)
        self.assertEqual(len(sig), 64)
        self.assertEqual(sig, sig.lower())

    def test_field_order_is_canonical(self):
        # 字段序必须是 accessKey<timestamp<user(字典序),乱序签名会被平台拒
        sig = scnet_client.sign("a", "1", "u", "s")
        wrong = hmac.new(b"s", b'{"user":"u","accessKey":"a","timestamp":"1"}',
                         hashlib.sha256).hexdigest()
        self.assertNotEqual(sig, wrong)


class TestJwtExpire(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(scnet_client._jwt_expire_ms(_jwt(123456789)), 123456789)

    def test_garbage_returns_zero(self):
        self.assertEqual(scnet_client._jwt_expire_ms("not-a-jwt"), 0)


class TestTokenCache(_EnvMixin):
    def setUp(self):
        super().setUp()
        self._tear_env = True
        self.cache_path = os.path.join(TMP, "state", "scnet_tokens.json")
        if os.path.exists(self.cache_path):
            os.remove(self.cache_path)

    def _write_cache(self, expire_ms):
        os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
        with open(self.cache_path, "w") as f:
            json.dump({"obtained_at": "x", "regions": {
                "0": {"name": "ac", "token": _jwt(expire_ms), "expire_ms": expire_ms}}}, f)

    def test_fresh_cache_reused(self):
        self._write_cache(9_999_999_999_999)
        self.assertIsNotNone(scnet_client._load_cached_tokens())

    def test_expiring_cache_rejected(self):
        self._write_cache(1000)  # 1970,早过期
        self.assertIsNone(scnet_client._load_cached_tokens())

    def test_corrupt_cache_rejected(self):
        os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
        with open(self.cache_path, "w") as f:
            f.write("not json")
        self.assertIsNone(scnet_client._load_cached_tokens())

    def tearDown(self):
        super().tearDown()


class TestGate(unittest.TestCase):
    def _scnet(self, **kw):
        base = {"enabled": True, "auto_submit": True,
                "whitelist": {"queues": ["debug", "normal"],
                              "max_nodes": 2, "max_walltime_h": 24}}
        base.update(kw)
        return base

    def test_disabled_refuses(self):
        r = scnet_client.gate_check(self._scnet(enabled=False), "debug", 1, "01:00:00")
        self.assertIn("enabled", r)

    def test_auto_submit_off_refuses(self):
        r = scnet_client.gate_check(self._scnet(auto_submit=False), "debug", 1, "01:00:00")
        self.assertIn("auto_submit", r)

    def test_queue_not_in_whitelist(self):
        r = scnet_client.gate_check(self._scnet(), "bigmem", 1, "01:00:00")
        self.assertIn("白名单", r)

    def test_nodes_over_cap(self):
        self.assertIn("节点数", scnet_client.gate_check(self._scnet(), "debug", 3, "01:00:00"))

    def test_walltime_over_cap(self):
        self.assertIn("墙时", scnet_client.gate_check(self._scnet(), "debug", 1, "48:00:00"))

    def test_walltime_malformed(self):
        self.assertIn("格式", scnet_client.gate_check(self._scnet(), "debug", 1, "一天"))

    def test_within_whitelist_passes(self):
        self.assertIsNone(scnet_client.gate_check(self._scnet(), "debug", 2, "24:00:00"))

    def test_empty_queue_whitelist_means_all(self):
        sc = self._scnet(whitelist={"queues": [], "max_nodes": 1, "max_walltime_h": 24})
        self.assertIsNone(scnet_client.gate_check(sc, "anyqueue", 1, "01:00:00"))


class TestSubmitFlow(_EnvMixin):
    """submit_job 全链 mock:闸门/上传/提交载荷/审计,不发真请求。"""

    def setUp(self):
        super().setUp()
        self.cfg_path = os.path.join(HERE, "..", "mcp", "md_config.json")
        self._orig_cfg = open(self.cfg_path, encoding="utf-8").read()
        cfg = json.loads(self._orig_cfg)
        cfg["scnet"] = {"enabled": True, "auto_submit": True, "cluster_id": "11112",
                        "work_root": "YMY-AI",
                        "lmp_cmd": "mpirun -np {nproc} lmp_mpi -in {input}",
                        "whitelist": {"queues": ["debug"], "max_nodes": 2,
                                      "max_walltime_h": 24}}
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        self.calls = []
        self._orig_http = scnet_client._http
        self._orig_auth = scnet_client.authenticate
        scnet_client.authenticate = lambda: {"regions": _fake_regions()}
        scnet_client._http = self._fake_http
        self.tmpdir = tempfile.mkdtemp()
        self.f1 = os.path.join(self.tmpdir, "production.in")
        with open(self.f1, "w") as f:
            f.write("run 100\n")
        self.audit = os.path.join(TMP, "events", "scnet_submit.jsonl")
        if os.path.exists(self.audit):
            os.remove(self.audit)

    def tearDown(self):
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            f.write(self._orig_cfg)
        scnet_client._http = self._orig_http
        scnet_client.authenticate = self._orig_auth
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        super().tearDown()

    def _fake_http(self, method, url, token=None, body=None, headers=None, timeout=30):
        self.calls.append((method, url, body))
        if url.endswith("/ac/openapi/v2/center"):
            return {"id": 11112, "name": "计算中心A",
                    "clusterUserInfo": {"userName": "test", "homePath": "/public/home/test"},
                    "hpcUrls": [{"enable": "true", "url": "https://hpc.example/hpc"}],
                    "efileUrls": [{"enable": "true", "url": "https://efile.example/efile"}]}
        if url.endswith("/openapi/v2/cluster"):
            return [{"id": 1638523853, "text": "Cluster", "JobManagerType": "SLURM"}]
        if url.endswith("/apptemplates/BASIC/BASE/job"):
            return {"jobId": "424242"}
        return {}

    def _write_approval(self, case_id, queue, nodes, ppn, walltime, cmd,
                        age_s=0, ph_override=None):
        ph = ph_override or scnet_client.plan_hash(
            case_id, queue, nodes, ppn, walltime, cmd, [self.f1],
            cluster_id="11112", work_root="YMY-AI")
        ap = scnet_client._approval_path(ph)
        with open(ap, "w", encoding="utf-8") as f:
            json.dump({"plan_hash": ph,
                       "approved_epoch": __import__("time").time() - age_s}, f)
        return ph

    def _audit_entries(self):
        if not os.path.exists(self.audit):
            return []
        with open(self.audit, encoding="utf-8") as f:
            return [json.loads(ln) for ln in f if ln.strip()]

    def test_submit_happy_path(self):
        cmd = "mpirun -np 32 lmp_mpi -in production.in"
        self._write_approval("caseX", "debug", 1, 32, "12:00:00", cmd)
        r = scnet_client.submit_job("caseX", [self.f1], "debug", 1, 32, "12:00:00")
        self.assertTrue(r["submitted"])
        self.assertEqual(r["approved_via"], "approval_file")
        self.assertEqual(r["job_id"], "424242")
        self.assertEqual(r["remote_dir"], "/public/home/test/YMY-AI/caseX")
        submit = [c for c in self.calls if c[1].endswith("/apptemplates/BASIC/BASE/job")]
        self.assertEqual(len(submit), 1)
        info = submit[0][2]["mapAppJobInfo"]
        self.assertEqual(info["GAP_WORK_DIR"], "/public/home/test/YMY-AI/caseX")
        self.assertEqual(info["GAP_CMD_FILE"], "mpirun -np 32 lmp_mpi -in production.in")
        self.assertEqual(info["GAP_QUEUE"], "debug")
        self.assertEqual(info["GAP_WALL_TIME"], "12:00:00")
        # 上传在提交之前
        kinds = [c[1].split("/openapi/v2/")[-1].split("/")[0] for c in self.calls]
        self.assertLess(kinds.index("file"), kinds.index("apptemplates"))
        entries = self._audit_entries()
        self.assertEqual(entries[-1]["action"], "submitted")
        self.assertEqual(entries[-1]["job_id"], "424242")
        # 一次性:提交成功后批准文件被销毁,复用须重新批准
        ph = scnet_client.plan_hash("caseX", "debug", 1, 32, "12:00:00",
                                    "mpirun -np 32 lmp_mpi -in production.in",
                                    [self.f1], cluster_id="11112",
                                    work_root="YMY-AI")
        self.assertFalse(os.path.exists(scnet_client._approval_path(ph)))
        self.assertFalse(scnet_client._valid_approval(ph))

    def test_gate_refusal_audited_no_http(self):
        r = scnet_client.submit_job("caseY", [self.f1], "bigmem", 1, 32, "12:00:00")
        self.assertFalse(r["submitted"])
        self.assertIn("白名单", r["refused"])
        self.assertEqual(self.calls, [])  # 被拒时零外发请求
        self.assertEqual(self._audit_entries()[-1]["action"], "refused")

    def test_dry_run_renders_without_submit(self):
        r = scnet_client.submit_job("caseZ", [self.f1], "debug", 1, 32, "12:00:00",
                                    dry_run=True)
        self.assertTrue(r["dry_run"])
        self.assertIn("YMY-AI/caseZ", r["plan"]["remote_dir"])
        self.assertFalse(any(c[1].endswith("/apptemplates/BASIC/BASE/job")
                             for c in self.calls))
        self.assertEqual(self._audit_entries()[-1]["action"], "dry_run")

    def test_missing_cmd_refused(self):
        with open(self.cfg_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        cfg["scnet"]["lmp_cmd"] = ""
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        r = scnet_client.submit_job("caseW", [self.f1], "debug", 1, 32, "12:00:00")
        self.assertIn("lmp_cmd", r["refused"])


class TestHumanConfirm(TestSubmitFlow):
    """人工确认闸(代码强制,19 轮加固):无批准且无 TTY 拒;批准文件过期/伪造拒;
    TTY 通道需 YES+PIN(批准 PIN 由 set_pin 设置,PBKDF2 盐化哈希落 pin.json)。

    继承 TestSubmitFlow 复用 mock 环境;_tty_confirm 的 stdin/getpass 一律猴补丁,
    防回归在真 TTY 里跑到 input() 卡死。"""

    PIN = "test-pin-123456"

    class _NoTTY:
        def isatty(self):
            return False

    class _YesTTY:
        def isatty(self):
            return True

    def setUp(self):
        super().setUp()
        pin = scnet_client._pin_path()
        if os.path.exists(pin):
            os.remove(pin)
        scnet_client.set_pin(self.PIN)

    def _patch_tty(self, answer="YES", pin=None):
        import unittest.mock as mock
        return mock.patch.object(sys, "stdin", self._YesTTY()), \
            mock.patch("builtins.input", lambda prompt="": answer), \
            mock.patch.object(scnet_client.getpass, "getpass",
                              lambda prompt="": pin if pin is not None else self.PIN)

    def test_no_approval_no_tty_refused(self):
        import unittest.mock as mock
        with mock.patch.object(sys, "stdin", self._NoTTY()):
            r = scnet_client.submit_job("caseNo", [self.f1], "debug", 1, 32, "12:00:00")
        self.assertFalse(r["submitted"])
        self.assertIn("人工批准", r["refused"])
        self.assertEqual(self.calls, [])  # 人工闸先于网络:拒绝零外发
        self.assertEqual(self._audit_entries()[-1]["action"], "refused")

    def test_expired_approval_refused(self):
        import unittest.mock as mock
        cmd = "mpirun -np 32 lmp_mpi -in production.in"
        self._write_approval("caseOld", "debug", 1, 32, "12:00:00", cmd,
                             age_s=scnet_client.APPROVAL_MAX_AGE + 10)
        with mock.patch.object(sys, "stdin", self._NoTTY()):
            r = scnet_client.submit_job("caseOld", [self.f1], "debug", 1, 32, "12:00:00")
        self.assertFalse(r["submitted"])

    def test_tty_yes_pin_submits(self):
        p1, p2, p3 = self._patch_tty("YES")
        with p1, p2, p3:
            r = scnet_client.submit_job("caseTTY", [self.f1], "debug", 1, 32, "12:00:00")
        self.assertTrue(r["submitted"])
        self.assertEqual(r["approved_via"], "tty")

    def test_tty_yes_wrong_pin_refused(self):
        p1, p2, p3 = self._patch_tty("YES", pin="wrong-pin")
        with p1, p2, p3:
            r = scnet_client.submit_job("caseBadPin", [self.f1], "debug", 1, 32,
                                        "12:00:00")
        self.assertFalse(r["submitted"])
        self.assertFalse(any(c[1].endswith("/apptemplates/BASIC/BASE/job")
                             for c in self.calls))

    def test_tty_no_refuses(self):
        p1, p2, p3 = self._patch_tty("no")
        with p1, p2, p3:
            r = scnet_client.submit_job("caseNo2", [self.f1], "debug", 1, 32, "12:00:00")
        self.assertFalse(r["submitted"])

    def test_approve_requires_tty(self):
        import unittest.mock as mock
        with mock.patch.object(sys, "stdin", self._NoTTY()):
            with self.assertRaises(scnet_client.ScnetError):
                scnet_client.approve("deadbeef")

    def test_approve_flow_writes_one_shot_file(self):
        # 先 dry-run 落审计,再 approve(TTY+YES+PIN),批准文件须出现
        scnet_client.submit_job("caseAp", [self.f1], "debug", 1, 32, "12:00:00",
                                dry_run=True)
        ph = self._audit_entries()[-1]["plan_hash"]
        p1, p2, p3 = self._patch_tty("YES")
        with p1, p2, p3:
            self.assertTrue(scnet_client.approve(ph))
        self.assertTrue(os.path.exists(scnet_client._approval_path(ph)))
        actions = [e["action"] for e in self._audit_entries()]
        self.assertEqual(actions, ["pin_set", "dry_run", "approved"])

    def test_approve_wrong_pin_no_file(self):
        scnet_client.submit_job("caseAp2", [self.f1], "debug", 1, 32, "12:00:00",
                                dry_run=True)
        ph = self._audit_entries()[-1]["plan_hash"]
        p1, p2, p3 = self._patch_tty("YES", pin="wrong-pin")
        with p1, p2, p3:
            self.assertFalse(scnet_client.approve(ph))
        self.assertFalse(os.path.exists(scnet_client._approval_path(ph)))

    def test_plan_hash_binds_params(self):
        cmd = "cmd a"
        h1 = scnet_client.plan_hash("c", "debug", 1, 32, "12:00:00", cmd, [self.f1],
                                    cluster_id="11112", work_root="YMY-AI")
        h2 = scnet_client.plan_hash("c", "debug", 2, 32, "12:00:00", cmd, [self.f1],
                                    cluster_id="11112", work_root="YMY-AI")
        self.assertNotEqual(h1, h2)  # 改任何参数哈希都变,批准不可挪用
        # 19 轮加固:cluster_id / work_root / 文件内容 都进哈希
        h3 = scnet_client.plan_hash("c", "debug", 1, 32, "12:00:00", cmd, [self.f1],
                                    cluster_id="22223", work_root="YMY-AI")
        self.assertNotEqual(h1, h3)
        h4 = scnet_client.plan_hash("c", "debug", 1, 32, "12:00:00", cmd, [self.f1],
                                    cluster_id="11112", work_root="OTHER")
        self.assertNotEqual(h1, h4)
        with open(self.f1, "a") as f:
            f.write("# changed\n")
        h5 = scnet_client.plan_hash("c", "debug", 1, 32, "12:00:00", cmd, [self.f1],
                                    cluster_id="11112", work_root="YMY-AI")
        self.assertNotEqual(h1, h5)  # 同名文件内容调包 → 哈希变

    # ---- 19 轮修复回归:伪造批准/并发双花/失败恢复 ----
    def test_forged_approval_missing_plan_hash_refused(self):
        import unittest.mock as mock
        cmd = "mpirun -np 32 lmp_mpi -in production.in"
        ph = scnet_client.plan_hash("caseForge", "debug", 1, 32, "12:00:00", cmd,
                                    [self.f1], cluster_id="11112", work_root="YMY-AI")
        ap = scnet_client._approval_path(ph)
        with open(ap, "w") as f:  # 手写伪造:只有时间戳,无 plan_hash 字段
            json.dump({"approved_epoch": __import__("time").time()}, f)
        with mock.patch.object(sys, "stdin", self._NoTTY()):
            r = scnet_client.submit_job("caseForge", [self.f1], "debug", 1, 32,
                                        "12:00:00")
        self.assertFalse(r["submitted"])
        self.assertFalse(any(c[1].endswith("/apptemplates/BASIC/BASE/job")
                             for c in self.calls))

    def test_forged_approval_future_ts_refused(self):
        cmd = "mpirun -np 32 lmp_mpi -in production.in"
        ph = self._write_approval("caseFut", "debug", 1, 32, "12:00:00", cmd,
                                  age_s=-3600)  # 未来时间戳
        self.assertFalse(scnet_client._valid_approval(ph))

    def test_approval_plan_hash_field_mismatch_refused(self):
        cmd = "mpirun -np 32 lmp_mpi -in production.in"
        real = scnet_client.plan_hash("caseMM", "debug", 1, 32, "12:00:00", cmd,
                                      [self.f1], cluster_id="11112",
                                      work_root="YMY-AI")
        ap = scnet_client._approval_path(real)
        with open(ap, "w") as f:
            json.dump({"plan_hash": "0" * 16,  # 字段与文件名不一致
                       "approved_epoch": __import__("time").time()}, f)
        self.assertFalse(scnet_client._valid_approval(real))

    def test_concurrent_take_only_one_wins(self):
        cmd = "mpirun -np 32 lmp_mpi -in production.in"
        ph = self._write_approval("caseCC", "debug", 1, 32, "12:00:00", cmd)
        t1 = scnet_client._take_approval(ph)
        t2 = scnet_client._take_approval(ph)
        self.assertIsNotNone(t1)
        self.assertIsNone(t2)  # 原子取走:并发下仅一个成功
        os.remove(t1)

    def test_submit_failure_restores_approval(self):
        cmd = "mpirun -np 32 lmp_mpi -in production.in"
        ph = self._write_approval("caseFail", "debug", 1, 32, "12:00:00", cmd)
        orig = self._fake_http

        def boom(method, url, token=None, body=None, headers=None, timeout=30):
            if url.endswith("/apptemplates/BASIC/BASE/job"):
                raise scnet_client.ScnetError("network", "boom")
            return orig(method, url, token=token, body=body, headers=headers)
        scnet_client._http = boom
        with self.assertRaises(scnet_client.ScnetError):
            scnet_client.submit_job("caseFail", [self.f1], "debug", 1, 32, "12:00:00")
        scnet_client._http = orig
        # 失败恢复原批准:文件回位且仍有效,可重试
        self.assertTrue(scnet_client._valid_approval(ph))
        r = scnet_client.submit_job("caseFail", [self.f1], "debug", 1, 32, "12:00:00")
        self.assertTrue(r["submitted"])
        self.assertEqual(self._audit_entries()[-2]["action"], "submit_failed")

    # ---- 19 轮修复回归:case_id 白名单 / 配置类型混淆 fail-closed ----
    def test_case_id_traversal_refused(self):
        for bad in ("..", "../x", "a/b", "x y", "$(id)", "-lead", "中文"):
            r = scnet_client.submit_job(bad, [self.f1], "debug", 1, 32, "12:00:00")
            self.assertFalse(r["submitted"], bad)
            self.assertIn("非法字符", r["refused"], bad)
        self.assertEqual(self.calls, [])

    def test_gate_type_confusion_fail_closed(self):
        cmd = "mpirun -np 32 lmp_mpi -in production.in"
        clean = {"enabled": True, "auto_submit": True, "cluster_id": "11112",
                 "work_root": "YMY-AI",
                 "lmp_cmd": "mpirun -np {nproc} lmp_mpi -in {input}",
                 "whitelist": {"queues": ["debug"], "max_nodes": 2,
                               "max_walltime_h": 24}}

        def write_scnet(sc):
            with open(self.cfg_path, encoding="utf-8") as fh:
                cfg = json.load(fh)
            cfg["scnet"] = sc
            with open(self.cfg_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)

        import copy
        for mutate in (
                lambda c: c.update(enabled="false"),      # 字符串 truthy
                lambda c: c.update(auto_submit="no"),
                lambda c: c.update(whitelist="debug"),    # whitelist 非对象
                lambda c: c["whitelist"].update(queues="debug"),  # 字符串
                lambda c: c["whitelist"].update(max_nodes=None),
                lambda c: c["whitelist"].update(max_walltime_h=float("nan")),
                lambda c: c["whitelist"].update(max_walltime_h="abc"),
        ):
            sc = copy.deepcopy(clean)
            mutate(sc)
            write_scnet(sc)
            r = scnet_client.submit_job("caseTC", [self.f1], "debug", 1, 32,
                                        "12:00:00", cmd=cmd)
            self.assertFalse(r["submitted"], r)  # 畸形一律干净拒,不裸抛
            self.assertIn("refused", r)
        self.assertEqual(self.calls, [])
        # whitelist 整缺 → 默认严格:队列全放行(有意语义)但 nodes 上限默认 1
        sc = copy.deepcopy(clean)
        del sc["whitelist"]
        write_scnet(sc)
        r = scnet_client.submit_job("caseTC2", [self.f1], "anyq", 5, 32, "12:00:00",
                                    cmd=cmd)
        self.assertIn("节点数", r["refused"])

    def test_gate_lower_bounds(self):
        for nodes in (0, -1):
            r = scnet_client.submit_job("caseLB", [self.f1], "debug", nodes, 32,
                                        "12:00:00")
            self.assertIn("正整数", r["refused"])
        for wt in ("00:00:00", "1:99:99", "-1:00:00"):
            r = scnet_client.submit_job("caseLB", [self.f1], "debug", 1, 32, wt)
            self.assertIn("非法", r["refused"])

    def test_job_id_whitelist(self):
        self.assertEqual(scnet_client._check_job_id("123_4"), "123_4")
        for bad in ("8663185&jobMethod=9", "1;2,victim:99999:;x", "x y", ""):
            with self.assertRaises(scnet_client.ScnetError):
                scnet_client._check_job_id(bad)

    def test_pin_set_and_verify(self):
        self.assertTrue(scnet_client._verify_pin(self.PIN))
        self.assertFalse(scnet_client._verify_pin("nope"))
        # 更换须验旧 PIN
        with self.assertRaises(scnet_client.ScnetError):
            scnet_client.set_pin("new-pin-789", old_pin="wrong")
        scnet_client.set_pin("new-pin-789", old_pin=self.PIN)
        self.assertTrue(scnet_client._verify_pin("new-pin-789"))
        self.assertFalse(scnet_client._verify_pin(self.PIN))
        # PIN 太短拒
        with self.assertRaises(scnet_client.ScnetError):
            scnet_client.set_pin("123")

    def test_upload_filename_checked(self):
        with self.assertRaises(scnet_client.ScnetError):
            scnet_client.upload_file("https://efile.example/efile", "tok",
                                     "/x", "/tmp/evil\nname.txt")

    # ---- 19 轮复审(delta)修复回归 ----
    def test_ppn_lower_bound(self):
        for ppn in (0, -32):
            r = scnet_client.submit_job("casePPN", [self.f1], "debug", 1, ppn,
                                        "12:00:00")
            self.assertIn("ppn", r["refused"])
        self.assertEqual(self.calls, [])

    def test_work_root_traversal_refused(self):
        import copy
        with open(self.cfg_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        cfg["scnet"]["work_root"] = "../../attacker"
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        r = scnet_client.submit_job("caseWR", [self.f1], "debug", 1, 32, "12:00:00")
        self.assertFalse(r["submitted"])
        self.assertIn("work_root", r["refused"])
        self.assertEqual(self.calls, [])

    def test_approval_path_rejects_bad_hash(self):
        with self.assertRaises(scnet_client.ScnetError):
            scnet_client._approval_path("../../tmp/x")
        with self.assertRaises(scnet_client.ScnetError):
            scnet_client._approval_path("XYZ")

    def test_dry_run_failure_audited_as_dry_run_failed(self):
        def boom(method, url, token=None, body=None, headers=None, timeout=30):
            raise scnet_client.ScnetError("network", "offline")
        scnet_client._http = boom
        with self.assertRaises(scnet_client.ScnetError):
            scnet_client.submit_job("caseDry", [self.f1], "debug", 1, 32, "12:00:00",
                                    dry_run=True)
        self.assertEqual(self._audit_entries()[-1]["action"], "dry_run_failed")

    def test_restore_does_not_overwrite_newer_approval(self):
        import time
        cmd = "mpirun -np 32 lmp_mpi -in production.in"
        ph = self._write_approval("caseRe", "debug", 1, 32, "12:00:00", cmd,
                                  age_s=100)
        orig = self._fake_http

        def boom(method, url, token=None, body=None, headers=None, timeout=30):
            if url.endswith("/apptemplates/BASIC/BASE/job"):
                # 提交在途时人工重新批准(刷新 epoch)
                self._write_approval("caseRe", "debug", 1, 32, "12:00:00", cmd,
                                     age_s=0)
                raise scnet_client.ScnetError("network", "boom")
            return orig(method, url, token=token, body=body, headers=headers)
        scnet_client._http = boom
        with self.assertRaises(scnet_client.ScnetError):
            scnet_client.submit_job("caseRe", [self.f1], "debug", 1, 32, "12:00:00")
        scnet_client._http = orig
        with open(scnet_client._approval_path(ph)) as f:
            ap = json.load(f)
        self.assertLess(time.time() - ap["approved_epoch"], 5)  # 新批准未被旧 taken 覆盖


class TestCredentialsParse(_EnvMixin):
    def test_parse_and_missing(self):
        dh = os.path.join(TMP, "dsh-home")
        os.makedirs(dh, exist_ok=True)
        path = os.path.join(dh, ".credentials.yaml")
        with open(path, "w") as f:
            f.write('# c\nSCNET_USER: "bob"\nSCNET_ACCESS_KEY: ak1\nKIMI_API_KEY: x\n')
        with self.assertRaises(scnet_client.ScnetError):  # 缺 SECRET_KEY
            scnet_client._credentials()
        with open(path, "a") as f:
            f.write("SCNET_SECRET_KEY: sk1\n")
        c = scnet_client._credentials()
        self.assertEqual((c["user"], c["ak"], c["sk"]), ("bob", "ak1", "sk1"))
        os.remove(path)


if __name__ == "__main__":
    unittest.main()
