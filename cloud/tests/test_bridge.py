#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""桥(bridge/localfs/runner 挂接)与客户端囚禁的单测。
MDAGENT_HOME=临时目录 + MDAGENT_SKIP_UNIX=1,与 test_cloud.py 同沙箱。"""
import asyncio
import importlib
import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))                      # cloud/
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), ".."))  # repo 根(client 包)

_TMP = tempfile.mkdtemp(prefix="mda_bridge_test_")
os.environ["MDAGENT_HOME"] = _TMP
os.environ["MDAGENT_SKIP_UNIX"] = "1"
os.environ["MDAGENT_OPEN_SIGNUP"] = "1"  # Isolated legacy bridge/account fixtures only.

import accounts
import bridge
import paths
import provision
import seed_admin


def setUpModule():
    shutil.rmtree(_TMP, ignore_errors=True)
    os.makedirs(_TMP, exist_ok=True)
    for m in ("paths", "accounts", "bridge", "provision", "history", "jobqueue"):
        if m in sys.modules:
            importlib.reload(sys.modules[m])


class TestBridgeCore(unittest.TestCase):
    def test_run_token_lifecycle(self):
        t = bridge.issue_run_token("alice", "job1")
        self.assertEqual(bridge.run_token_user(t), "alice")
        bridge.revoke_run_token(t)
        self.assertIsNone(bridge.run_token_user(t))
        self.assertIsNone(bridge.run_token_user("nonexistent"))

    def test_poll_call_result_roundtrip(self):
        async def go():
            t = bridge.issue_run_token("alice", "job1")
            async def poller():
                return await bridge.poll("alice", 5)
            pt = asyncio.ensure_future(poller())
            await asyncio.sleep(0.05)  # 让 poller 先挂上(才算在线)
            ct = asyncio.ensure_future(bridge.call(t, "alice", "read", {"path": "a.txt"}))
            await asyncio.sleep(0.05)
            op = await pt
            self.assertEqual(op["op"], "read")
            self.assertEqual(op["args"], {"path": "a.txt"})
            self.assertEqual(op["job"], "job1")
            ok = bridge.submit_result("alice", op["id"],
                                      {"ok": True, "data": {"content": "你好"}, "error": ""})
            self.assertTrue(ok)
            res = await ct
            self.assertTrue(res["ok"])
            self.assertEqual(res["data"]["content"], "你好")
            bridge.revoke_run_token(t)

        asyncio.new_event_loop().run_until_complete(go())

    def test_call_offline_rejected(self):
        async def go():
            # 离线时不扣配额:无效 token 也应先撞"不在线"
            res = await bridge.call("any_tok", "nobody_here", "read", {"path": "x"})
            self.assertFalse(res["ok"])
            self.assertIn("不在线", res["error"])

        asyncio.new_event_loop().run_until_complete(go())

    def test_call_over_limit_rejected(self):
        async def go():
            bridge._last_poll["ol"] = __import__("time").time()
            t = bridge.issue_run_token("ol", "jobX")
            bridge._run_tokens[t]["ops"] = bridge.MAX_OPS_PER_JOB  # 已到顶
            res = await bridge.call(t, "ol", "read", {"path": "x"})
            self.assertFalse(res["ok"])
            self.assertIn("超限", res["error"])
            bridge.revoke_run_token(t)

        asyncio.new_event_loop().run_until_complete(go())

    def test_submit_result_wrong_user(self):
        async def go():
            t = bridge.issue_run_token("bob", "j2")
            async def poller():
                return await bridge.poll("bob", 5)
            pt = asyncio.ensure_future(poller())
            await asyncio.sleep(0.05)
            ct = asyncio.ensure_future(bridge.call(t, "bob", "list", {"path": "."}))
            await asyncio.sleep(0.05)
            op = await pt
            # 别人不能替 bob 交结果
            self.assertFalse(bridge.submit_result("carol", op["id"],
                                                  {"ok": True, "data": {}, "error": ""}))
            self.assertTrue(bridge.submit_result("bob", op["id"],
                                                 {"ok": True, "data": {}, "error": ""}))
            await ct
            bridge.revoke_run_token(t)

        asyncio.new_event_loop().run_until_complete(go())

    def test_revoke_purges_queued_ops(self):
        """job 吊销后,其队列残留 op 被清;别的 job 的 op 保留。"""
        t1 = bridge.issue_run_token("pg", "jobA")
        t2 = bridge.issue_run_token("pg", "jobB")
        q = bridge._queues.setdefault("pg", asyncio.Queue())
        while not q.empty():
            q.get_nowait()
        now = __import__("time").time()
        q.put_nowait({"id": "a1", "op": "read", "args": {}, "job": "jobA", "ts": now})
        q.put_nowait({"id": "b1", "op": "read", "args": {}, "job": "jobB", "ts": now})
        bridge.revoke_run_token(t1)
        self.assertEqual(q.qsize(), 1)
        self.assertEqual(q.get_nowait()["id"], "b1")
        bridge.revoke_run_token(t2)

    def test_stale_op_dropped_at_poll(self):
        """陈旧 op(ts 超 CALL_TIMEOUT)或 job 已死的 op 不投递。"""
        async def go():
            q = bridge._queues.setdefault("st", asyncio.Queue())
            while not q.empty():
                q.get_nowait()
            q.put_nowait({"id": "old", "op": "read", "args": {},
                          "job": "deadjob", "ts": 1})  # 远古 + job 不在册
            op = await bridge.poll("st", 1)
            self.assertIsNone(op)  # 被丢弃,直到超时返 None

        asyncio.new_event_loop().run_until_complete(go())

    def test_online_window(self):
        self.assertFalse(bridge.client_online("ghost"))
        bridge._last_poll["ghost"] = __import__("time").time()
        self.assertTrue(bridge.client_online("ghost"))

    def test_poll_disconnect_returns_fast(self):
        """客户端已断开:poll 不挂满 wait,1s 内返回 None 且释放 _polling。"""
        async def go():
            async def disconnected():
                return True
            t0 = __import__("time").time()
            op = await bridge.poll("dave", 30, disconnected)
            self.assertIsNone(op)
            self.assertLess(__import__("time").time() - t0, 5)
            self.assertNotIn("dave", bridge._polling)  # 重连不被挡

        asyncio.new_event_loop().run_until_complete(go())

    def test_requeue_redelivers(self):
        """op 取出后连接已死 → requeue → 下一次 poll 能再拿到同一 op。"""
        async def go():
            now = __import__("time").time()
            t = bridge.issue_run_token("erin", "jobE")
            q = bridge._queues.setdefault("erin", asyncio.Queue())
            while not q.empty():
                q.get_nowait()
            q.put_nowait({"id": "op1", "op": "read", "args": {"path": "x"},
                          "job": "jobE", "ts": now})
            op = await bridge.poll("erin", 1)
            self.assertEqual(op["id"], "op1")
            bridge.requeue("erin", op)
            op2 = await bridge.poll("erin", 1)
            self.assertEqual(op2["id"], "op1")
            bridge.revoke_run_token(t)

        asyncio.new_event_loop().run_until_complete(go())


class TestBridgeRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        import app as cloud_app
        importlib.reload(cloud_app)
        cls.client = TestClient(cloud_app.app)
        accounts.apply("wanda", "", "", "kimi-coding", "sk-wanda-123456", "password123")
        token, _ = accounts.approve("wanda")
        cls.h = {"Authorization": "Bearer " + token}

    def test_poll_requires_auth(self):
        self.assertEqual(self.client.get("/v1/bridge/poll?wait=1").status_code, 401)

    def test_poll_timeout_returns_null(self):
        r = self.client.get("/v1/bridge/poll?wait=1", headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json()["op"])

    def test_status(self):
        r = self.client.get("/v1/bridge/status", headers=self.h)
        self.assertIn("online", r.json())

    def test_call_rejects_bad_token(self):
        r = self.client.post("/v1/bridge/call",
                             json={"token": "bad", "op": "read", "args": {}})
        self.assertEqual(r.status_code, 403)

    def test_call_rejects_unknown_op(self):
        t = bridge.issue_run_token("wanda", "j1")
        r = self.client.post("/v1/bridge/call",
                             json={"token": t, "op": "rm_rf", "args": {}})
        self.assertEqual(r.status_code, 400)
        bridge.revoke_run_token(t)

    def test_result_unknown_op_404(self):
        r = self.client.post("/v1/bridge/result", headers=self.h,
                             json={"op_id": "nonexistent", "ok": True})
        self.assertEqual(r.status_code, 404)


class TestRunnerPatchAttach(unittest.TestCase):
    def test_extra_patch_appended(self):
        import runner
        argv = runner._build_argv("alice", "p1", "信封", extra_patch="/tmp/x.patch.yml")
        self.assertEqual(argv.count("--patch"), 2)
        self.assertIn("/tmp/x.patch.yml", argv)
        argv2 = runner._build_argv("alice", "p1", "信封")
        self.assertEqual(argv2.count("--patch"), 1)

    def test_write_localfs_patch(self):
        import runner
        import stat
        provision.provision("pete")
        p = runner._write_localfs_patch("pete", "tok123")
        content = open(p, encoding="utf-8").read()
        self.assertIn("mcp-localfs", content)
        self.assertIn("pete", content)
        self.assertIn("tok123", content)          # token 在 env 行,不在 args
        self.assertIn("LOCALFS_RUN_TOKEN", content)
        self.assertIn("localfs_mcp.py", content)
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)

    def test_cumulative_op_limit(self):
        t = bridge.issue_run_token("frank", "j9")
        for _ in range(bridge.MAX_OPS_PER_JOB):
            self.assertTrue(bridge.count_op(t))
        self.assertFalse(bridge.count_op(t))      # 累计超限,与队列深度无关
        bridge.revoke_run_token(t)
        self.assertFalse(bridge.count_op(t))      # 吊销后一律拒


class TestClientJail(unittest.TestCase):
    """客户端目录囚禁:../ / 绝对路径 / 软链逃逸全拒。"""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "..",
                                        "mdagent-client"))
        import mdagent_client
        importlib.reload(mdagent_client)
        cls.mc = mdagent_client

    def test_jail_blocks_escape(self):
        root = tempfile.mkdtemp()
        jail = self.mc.Jail(root)
        self.assertTrue(str(jail.resolve("a/b.txt")).startswith(os.path.realpath(root)))
        for bad in ("../etc/passwd", "..", "/etc/passwd", "a/../../etc"):
            with self.assertRaises(PermissionError, msg=bad):
                jail.resolve(bad)

    def test_jail_blocks_symlink_escape(self):
        root = tempfile.mkdtemp()
        outside = tempfile.mkdtemp()
        os.symlink(outside, os.path.join(root, "link"))
        jail = self.mc.Jail(root)
        with self.assertRaises(PermissionError):
            jail.resolve("link/secret.txt")


if __name__ == "__main__":
    unittest.main()
