#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cloud 层单测:全部沙箱到 tmp(MDAGENT_HOME + MDAGENT_SKIP_UNIX=1),
不碰 unix 用户、不起真实 dsh(run_fn 用假执行器)。"""
import asyncio
import importlib
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

_tmp = tempfile.mkdtemp(prefix="cloud_test_")
os.environ["MDAGENT_HOME"] = _tmp
os.environ["MDAGENT_SKIP_UNIX"] = "1"
os.environ["MDAGENT_OPEN_SIGNUP"] = "1"  # Isolated legacy account fixtures only.

import accounts
import history
import paths
import provision
import jobqueue as jq
import seed_admin


def setUpModule():
    importlib.reload(paths)
    for m in (accounts, history, provision, jq, seed_admin):
        importlib.reload(m)


def tearDownModule():
    shutil.rmtree(_tmp, ignore_errors=True)


class TestAccounts(unittest.TestCase):
    def test_apply_approve_flow(self):
        ok, msg = accounts.apply("alice", "a@b.c", "做模拟", "kimi-coding",
                                 "sk-test-key-123456", "password123")
        self.assertTrue(ok, msg)
        self.assertEqual(len(accounts.list_applications()), 1)
        token, err = accounts.approve("alice")
        self.assertIsNone(err)
        self.assertTrue(token.startswith("mda_"))
        u = accounts.by_token(token)
        self.assertEqual(u["username"], "alice")
        # token 不明文落盘
        self.assertNotIn(token, open(paths.accounts_file(), encoding="utf-8").read())

    def test_duplicate_apply_rejected(self):
        accounts.apply("bob", "", "", "kimi-coding", "sk-x" * 8, "password123")
        ok, msg = accounts.apply("bob", "", "", "kimi-coding", "sk-y" * 8, "password123")
        self.assertFalse(ok)

    def test_bad_username_rejected(self):
        for bad in ("Alic", "1abc", "root;rm", "", "a" * 20):
            ok, _ = accounts.apply(bad, "", "", "kimi-coding", "sk-x" * 8, "password123")
            self.assertFalse(ok, bad)

    def test_bad_provider_rejected(self):
        ok, _ = accounts.apply("carol", "", "", "openai", "sk-x" * 8, "password123")
        self.assertFalse(ok)

    def test_reserved_names_rejected(self):
        for bad in ("root", "admin", "nobody", "mdagent"):
            ok, _ = accounts.apply(bad, "", "", "kimi-coding", "sk-x" * 8, "password123")
            self.assertFalse(ok, bad)

    def test_revoke_kills_token(self):
        accounts.apply("dave", "", "", "kimi-coding", "sk-x" * 8, "password123")
        token, _ = accounts.approve("dave")
        accounts.revoke("dave")
        self.assertIsNone(accounts.by_token(token))

    def test_rate_limit(self):
        accounts.apply("erin", "", "", "kimi-coding", "sk-x" * 8, "password123")
        accounts.approve("erin")
        # 默认不限量(0):记很多次也不拦
        for _ in range(30):
            self.assertTrue(accounts.rate_ok("erin"))
        # 显式传 limit 仍生效(限流逻辑保留)
        for _ in range(3):
            accounts.rate_ok("erin", limit=3)
        self.assertFalse(accounts.rate_ok("erin", limit=3))


class TestProvision(unittest.TestCase):
    def test_space_layout_and_shared_symlink(self):
        if not accounts.get("alice"):
            accounts.apply("alice", "", "", "kimi-coding", "sk-test-key-123456", "password123")
            accounts.approve("alice")
        provision.provision("alice")
        mem = os.path.join(paths.user_data_dir("alice"), "memory")
        self.assertTrue(os.path.islink(mem))
        self.assertEqual(os.readlink(mem), paths.shared_evolution_dir())
        cred = os.path.join(paths.user_dsh_home("alice"), ".credentials.yaml")
        content = open(cred, encoding="utf-8").read()
        self.assertTrue(content.startswith("MDEXP_PROXY_KEY: mxp_"))
        self.assertNotIn("sk-test-key-123456", content)
        self.assertEqual(os.stat(cred).st_mode & 0o777, 0o600)
        settings = open(os.path.join(paths.user_dsh_home("alice"),
                                     "settings.yaml"), encoding="utf-8").read()
        self.assertIn("provider: kimi-coding", settings)
        self.assertIn("model: kimi-for-coding", settings)
        # 幂等:再跑一遍不炸
        provision.provision("alice")

    def test_seed_admin(self):
        token, err = seed_admin.seed("useradmin")
        self.assertIsNone(err)
        self.assertTrue(token.startswith("mda_"))
        u = accounts.by_token(token)
        self.assertEqual(u["role"], "admin")
        # 幂等:第二次不重置 token
        token2, err2 = seed_admin.seed("useradmin")
        self.assertIsNone(token2)
        self.assertIn("已存在", err2)


class TestHistory(unittest.TestCase):
    def test_envelope_injects_recent_history(self):
        for i in range(30):
            history.append("alice", "proj1", "user", "问%d" % i)
            history.append("alice", "proj1", "assistant", "答%d" % i)
        env = history.build_envelope("alice", "proj1", "新问题")
        self.assertIn("问29", env)
        self.assertIn("答29", env)
        self.assertNotIn("问5", env)  # 只留最近 20 轮
        self.assertIn("[本轮用户消息]\n新问题", env)
        self.assertIn("不要开场白", env)

    def test_project_isolation(self):
        history.append("alice", "projA", "user", "A项目的秘密")
        env = history.build_envelope("alice", "projB", "hi")
        self.assertNotIn("A项目的秘密", env)
        self.assertIn("projA", history.list_projects("alice"))

    def test_envelope_injects_active_strategies(self):
        """共享进化层 active 策略注入信封;非 active/缺文件不注。"""
        sp = os.path.join(paths.shared_evolution_dir(),
                          "behavior_strategies.json")
        os.makedirs(paths.shared_evolution_dir(), exist_ok=True)
        with open(sp, "w", encoding="utf-8") as f:
            json.dump({"strategies": [
                {"sid": "bs-1", "status": "active", "rule": "规则甲:先查本机"},
                {"sid": "bs-2", "status": "revoked", "rule": "规则乙:已废"},
                {"sid": "bs-3", "status": "active"}]}, f)
        env = history.build_envelope("alice", "projS", "hi")
        self.assertIn("规则甲:先查本机", env)
        self.assertIn("[行为策略·共享进化层", env)
        self.assertNotIn("规则乙", env)       # 非 active 不注
        os.unlink(sp)
        env2 = history.build_envelope("alice", "projS", "hi")
        self.assertNotIn("行为策略", env2)     # 缺文件静默跳过

    def test_archive_and_restore(self):
        history.append("zoe", "paper1", "user", "口令Z")
        os.makedirs(os.path.join(paths.user_projects_dir("zoe"), "paper1"))
        open(os.path.join(paths.user_projects_dir("zoe"), "paper1", "GOAL.md"),
             "w", encoding="utf-8").write("# 目标:测试")
        self.assertIn("paper1", history.list_projects("zoe"))

        r = history.archive("zoe", "paper1")
        self.assertEqual(r["moved"], ["conversations", "projects"])
        self.assertNotIn("paper1", history.list_projects("zoe"))
        self.assertEqual(history.recent("zoe", "paper1"), [])
        self.assertFalse(history.valid_project("_archived"))
        lst = history.archived("zoe")
        self.assertEqual([e["project"] for e in lst], ["paper1"])
        self.assertTrue(lst[0]["has_conversations"] and lst[0]["has_projects"])

        # 归档后同项目重新开聊,再恢复须 409 语义(不覆盖)
        history.append("zoe", "paper1", "user", "新一轮")
        with self.assertRaises(FileExistsError):
            history.restore("zoe", "paper1")
        # 清掉新会话后恢复成功,旧内容回来
        os.unlink(history._file("zoe", "paper1"))
        r2 = history.restore("zoe", "paper1")
        self.assertIn("conversations", r2["restored"])
        self.assertIn("口令Z", history.build_envelope("zoe", "paper1", "x"))
        self.assertIn("GOAL.md", os.listdir(
            os.path.join(paths.user_projects_dir("zoe"), "paper1")))
        self.assertEqual(history.archived("zoe"), [])

    def test_archive_missing_project(self):
        with self.assertRaises(FileNotFoundError):
            history.archive("nobody", "ghost")

    def test_same_second_rearchive_no_loss(self):
        """同秒二次归档(时钟冻结逼碰撞):不得 500、不得丢旧快照、恢复取最新。"""
        class _Frozen(object):
            def strftime(self, fmt, *a):
                return "20260920_120000"

            def time(self):
                return 1789000000.0

        real = history.time
        history.time = _Frozen()
        try:
            history.append("zoe", "snap", "user", "第一份")
            os.makedirs(os.path.join(paths.user_projects_dir("zoe"), "snap"))
            open(os.path.join(paths.user_projects_dir("zoe"), "snap",
                              "GOAL.md"), "w").write("v1")
            history.archive("zoe", "snap")
            history.append("zoe", "snap", "user", "第二份")
            os.makedirs(os.path.join(paths.user_projects_dir("zoe"), "snap"))
            open(os.path.join(paths.user_projects_dir("zoe"), "snap",
                              "GOAL.md"), "w").write("v2")
            r = history.archive("zoe", "snap")   # 同秒:ts 撞名
            self.assertTrue(r["archived_ts"] != "20260920_120000")
            ad = history._archived_conv_dir("zoe")
            snaps = [f for f in os.listdir(ad) if f.startswith("snap.")]
            texts = "".join(open(os.path.join(ad, f), encoding="utf-8").read()
                            for f in snaps)
            self.assertEqual(len(snaps), 2)
            self.assertIn("第一份", texts)
            self.assertIn("第二份", texts)
            history.restore("zoe", "snap")       # 取最新
            self.assertIn("第二份", history.build_envelope("zoe", "snap", "x"))
            self.assertNotIn("第一份", history.build_envelope("zoe", "snap", "x"))
        finally:
            history.time = real
            history.archive("zoe", "snap")

    def test_restore_atomic_no_mixed_state(self):
        """恢复预检:会话可回但项目空间被占 → 整体 409,不留半恢复态。"""
        history.append("zoe", "half", "user", "半恢复")
        hp = os.path.join(paths.user_projects_dir("zoe"), "half")
        os.makedirs(hp)
        history.archive("zoe", "half")
        os.makedirs(hp)                            # 只占项目空间
        with self.assertRaises(FileExistsError):
            history.restore("zoe", "half")
        self.assertNotIn("half", history.list_projects("zoe"))  # 会话没被搬回
        ent = [a for a in history.archived("zoe") if a["project"] == "half"]
        self.assertTrue(ent and ent[0]["has_conversations"]
                        and ent[0]["has_projects"])
        # 多条目清单不崩(>=2 个归档项目)
        history.append("zoe", "half2", "user", "第二个归档项目")
        history.archive("zoe", "half2")
        self.assertGreaterEqual(len(history.archived("zoe")), 2)
        history.restore("zoe", "half2")
        shutil.rmtree(hp, ignore_errors=True)
        history.restore("zoe", "half")
        history.archive("zoe", "half")


class TestQueue(unittest.TestCase):
    def test_submit_and_done(self):
        async def go():
            jq.init()

            async def fake_run(job):
                return "回复:" + job["message"]

            job = await jq.submit("alice", "proj1", "你好", fake_run)
            for _ in range(100):
                j = jq.get_job(job["id"])
                if j["status"] != "queued" and j["finished_ts"]:
                    break
                await asyncio.sleep(0.05)
            j = jq.get_job(job["id"])
            self.assertEqual(j["status"], "done")
            self.assertEqual(j["reply"], "回复:你好")

        asyncio.get_event_loop().run_until_complete(go())

    def test_error_captured(self):
        async def go():
            jq.init()

            async def bad_run(job):
                raise RuntimeError("dsh 退出码 1: boom")

            job = await jq.submit("alice", "proj1", "hi", bad_run)
            for _ in range(100):
                if jq.get_job(job["id"])["finished_ts"]:
                    break
                await asyncio.sleep(0.05)
            j = jq.get_job(job["id"])
            self.assertEqual(j["status"], "error")
            self.assertIn("boom", j["error"])

        asyncio.get_event_loop().run_until_complete(go())

    def test_job_file_not_world_readable(self):
        async def go():
            jq.init()

            async def fake_run(job):
                return "ok"

            job = await jq.submit("alice", "proj1", "hi", fake_run)
            for _ in range(100):
                if jq.get_job(job["id"])["finished_ts"]:
                    break
                await asyncio.sleep(0.05)
            mode = os.stat(os.path.join(paths.jobs_dir(), job["id"] + ".json")).st_mode
            self.assertEqual(mode & 0o077, 0, "job 文件不得组/他人可读")

        asyncio.get_event_loop().run_until_complete(go())


class TestAppRoutes(unittest.TestCase):
    """路由级:真实 FastAPI TestClient,runner 替换为假执行器。"""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        import app as cloud_app
        importlib.reload(cloud_app)
        cls.app_mod = cloud_app
        cls.client = TestClient(cloud_app.app)
        token, err = seed_admin.seed("routeadmin")
        if not token:
            u = accounts.get("routeadmin")
            accounts.revoke("routeadmin")
            accounts.apply("routeadmin", "", "", "kimi-coding", "sk-x" * 8, "password123")
            token, _ = accounts.approve("routeadmin")
            db = accounts._load()
            accounts._find(db, "routeadmin")["role"] = "admin"
            accounts._save(db)
        cls.admin_h = {"Authorization": "Bearer " + token}

    def _mkuser(self, name):
        accounts.apply(name, "", "", "kimi-coding", "sk-%s-key-123456" % name, "password123")
        token, err = accounts.approve(name)
        self.assertIsNone(err)
        provision.provision(name)
        return {"Authorization": "Bearer " + token}

    def test_full_flow_and_isolation(self):
        r = self.client.post("/v1/apply", json={
            "username": "frank", "provider": "kimi-coding",
            "api_key": "sk-frank-123456", "reason": "测试", "password": "password123"})
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.get("/v1/admin/applications", headers=self.admin_h)
        self.assertIn("frank", r.text)
        r = self.client.post("/v1/admin/approve/frank", headers=self.admin_h)
        self.assertEqual(r.status_code, 200, r.text)
        frank_token = r.json()["token"]
        fh = {"Authorization": "Bearer " + frank_token}

        async def fake_run(job):
            history.append(job["user"], job["project"], "user", job["message"])
            history.append(job["user"], job["project"], "assistant",
                           "给" + job["user"] + "的回复")
            return "给" + job["user"] + "的回复"

        self.app_mod.jq.init()
        orig = self.app_mod.runner.run_chat
        self.app_mod.runner.run_chat = fake_run
        try:
            r = self.client.post("/v1/chat", headers=fh,
                                 json={"project": "凝胶", "message": "怎么建模"})
            self.assertEqual(r.status_code, 202, r.text)
            jid = r.json()["job_id"]
            import time as _t
            for _ in range(100):
                jr = self.client.get("/v1/jobs/" + jid, headers=fh).json()
                if jr["status"] in ("done", "error"):
                    break
                _t.sleep(0.05)
            self.assertEqual(jr["status"], "done")
            self.assertEqual(jr["reply"], "给frank的回复")
        finally:
            self.app_mod.runner.run_chat = orig

        # 第二用户:看不到 frank 的 job 与会话
        gh = self._mkuser("grace")
        r = self.client.get("/v1/jobs/" + jid, headers=gh)
        self.assertEqual(r.status_code, 403)
        r = self.client.get("/v1/conversations", headers=gh)
        self.assertNotIn("凝胶", r.text)
        r = self.client.get("/v1/conversations", headers=fh)
        self.assertIn("凝胶", r.text)
        # 共享进化层:两人看到同一份
        open(os.path.join(paths.shared_evolution_dir(),
                          "behavior_strategies.json"), "w",
             encoding="utf-8").write('{"strategies":[{"sid":"bs-x"}]}')
        self.assertEqual(self.client.get("/v1/evolution", headers=fh).json(),
                         self.client.get("/v1/evolution", headers=gh).json())

    def test_admin_required(self):
        uh = self._mkuser("henry")
        r = self.client.get("/v1/admin/users", headers=uh)
        self.assertEqual(r.status_code, 403)
        r = self.client.get("/v1/admin/users")
        self.assertEqual(r.status_code, 401)

    def test_archive_endpoints(self):
        uh = self._mkuser("irene")
        history.append("irene", "arch1", "user", "归档前内容")
        r = self.client.get("/v1/conversations", headers=uh)
        self.assertIn("arch1", [p for p in r.json()["projects"]])

        # 非法名 400
        r = self.client.post("/v1/conversations/_arch..ived/archive", headers=uh)
        self.assertEqual(r.status_code, 400)
        # 不存在 404
        r = self.client.post("/v1/conversations/ghost/archive", headers=uh)
        self.assertEqual(r.status_code, 404)
        # 正常归档
        r = self.client.post("/v1/conversations/arch1/archive", headers=uh)
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.get("/v1/conversations", headers=uh)
        self.assertIn("arch1", [a["project"] for a in r.json()["archived"]])
        self.assertNotIn("arch1", r.json()["projects"])
        # 重复归档(已空)404
        r = self.client.post("/v1/conversations/arch1/archive", headers=uh)
        self.assertEqual(r.status_code, 404)
        # 恢复
        r = self.client.post("/v1/conversations/arch1/restore", headers=uh)
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.get("/v1/conversations/arch1", headers=uh)
        self.assertIn("归档前内容", r.text)
        # 没有归档可恢复 404
        r = self.client.post("/v1/conversations/arch1/restore", headers=uh)
        self.assertEqual(r.status_code, 404)

        # 跨用户隔离:别人看不见也归不了
        vh = self._mkuser("judy")
        r = self.client.get("/v1/conversations", headers=vh)
        self.assertNotIn("arch1", r.text)
        r = self.client.post("/v1/conversations/arch1/archive", headers=vh)
        self.assertEqual(r.status_code, 404)

    def test_archive_blocked_while_job_running(self):
        uh = self._mkuser("kate")
        history.append("kate", "busy1", "user", "跑任务中")
        self.app_mod.jq.init()
        self.app_mod.jq._jobs["j-archive-test"] = {
            "id": "j-archive-test", "user": "kate", "project": "busy1",
            "status": "running", "created_ts": 1.0, "finished_ts": None}
        try:
            r = self.client.post("/v1/conversations/busy1/archive", headers=uh)
            self.assertEqual(r.status_code, 409)
        finally:
            del self.app_mod.jq._jobs["j-archive-test"]
        r = self.client.post("/v1/conversations/busy1/archive", headers=uh)
        self.assertEqual(r.status_code, 200, r.text)

    def test_restore_blocked_while_job_running(self):
        uh = self._mkuser("lars")
        history.append("lars", "busy2", "user", "恢复闸")
        r = self.client.post("/v1/conversations/busy2/archive", headers=uh)
        self.assertEqual(r.status_code, 200, r.text)
        self.app_mod.jq.init()
        self.app_mod.jq._jobs["j-restore-test"] = {
            "id": "j-restore-test", "user": "lars", "project": "busy2",
            "status": "running", "created_ts": 1.0, "finished_ts": None}
        try:
            r = self.client.post("/v1/conversations/busy2/restore", headers=uh)
            self.assertEqual(r.status_code, 409)
        finally:
            del self.app_mod.jq._jobs["j-restore-test"]
        r = self.client.post("/v1/conversations/busy2/restore", headers=uh)
        self.assertEqual(r.status_code, 200, r.text)

    def test_health(self):
        r = self.client.get("/v1/health")
        self.assertTrue(r.json()["ok"])

    def test_query_token_disabled(self):
        uh = self._mkuser("ivan")
        token = uh["Authorization"].split(" ", 1)[1]
        r = self.client.get("/v1/usage?token=" + token)
        self.assertEqual(r.status_code, 401, "query 串传 token 必须拒(防日志泄漏)")


if __name__ == "__main__":
    unittest.main()
