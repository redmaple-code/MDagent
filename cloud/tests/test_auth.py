#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""认证体系单测:密码哈希/登录/限流/token 轮换/apikey 自助更新。
沙箱同 test_cloud.py(MDAGENT_HOME + MDAGENT_SKIP_UNIX=1)。"""
import importlib
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

_tmp = tempfile.mkdtemp(prefix="cloud_auth_test_")
os.environ["MDAGENT_HOME"] = _tmp
os.environ["MDAGENT_SKIP_UNIX"] = "1"
# These legacy password/login checks focus on account behavior in an isolated database.
os.environ["MDAGENT_OPEN_SIGNUP"] = "1"

import accounts
import paths
import provision


def setUpModule():
    importlib.reload(paths)
    for m in (accounts, provision):
        importlib.reload(m)


def tearDownModule():
    shutil.rmtree(_tmp, ignore_errors=True)


def _mkuser(name, password="password123", key="sk-test-key-123456"):
    ok, msg = accounts.apply(name, "", "", "kimi-coding", key, password)
    assert ok, msg
    token, err = accounts.approve(name)
    assert err is None
    return token


class TestPassword(unittest.TestCase):
    def test_hash_format_and_verify(self):
        salt, h = accounts._hash_pwd("password123")
        salt2, h2 = accounts._hash_pwd("password123")
        self.assertNotEqual(salt, salt2)      # 随机盐
        self.assertNotEqual(h, h2)
        _, again = accounts._hash_pwd("password123", salt)
        self.assertEqual(h, again)            # 同盐可复现

    def test_apply_requires_password(self):
        ok, msg = accounts.apply("nopwd", "", "", "kimi-coding",
                                 "sk-x" * 8, "short")
        self.assertFalse(ok)
        ok, msg = accounts.apply("nopwd", "", "", "kimi-coding",
                                 "sk-x" * 8, "")
        self.assertFalse(ok)

    def test_password_not_plaintext_on_disk(self):
        _mkuser("pwuser", password="my-secret-pass")
        raw = open(paths.accounts_file(), encoding="utf-8").read()
        self.assertNotIn("my-secret-pass", raw)

    def test_verify_login(self):
        _mkuser("loginok")
        self.assertIsNotNone(accounts.verify_login("loginok", "password123"))
        self.assertIsNone(accounts.verify_login("loginok", "wrong-pass"))
        self.assertIsNone(accounts.verify_login("loginok", ""))
        self.assertIsNone(accounts.verify_login("nosuch", "password123"))

    def test_login_pending_user_rejected(self):
        ok, _ = accounts.apply("pending1", "", "", "kimi-coding",
                               "sk-x" * 8, "password123")
        self.assertTrue(ok)
        self.assertIsNone(accounts.verify_login("pending1", "password123"))
        accounts.deny("pending1")  # 别留 pending 态污染同进程其他测试模块

    def test_rotate_invalidates_old(self):
        token = _mkuser("rotuser")
        self.assertIsNotNone(accounts.by_token(token))
        new = accounts.rotate_token("rotuser")
        self.assertTrue(new.startswith("mda_"))
        self.assertNotEqual(new, token)
        self.assertIsNone(accounts.by_token(token))
        self.assertIsNotNone(accounts.by_token(new))

    def test_set_password(self):
        _mkuser("setpw")
        ok, _ = accounts.set_password("setpw", "brand-new-pass")
        self.assertTrue(ok)
        self.assertIsNotNone(accounts.verify_login("setpw", "brand-new-pass"))
        self.assertIsNone(accounts.verify_login("setpw", "password123"))
        ok, msg = accounts.set_password("setpw", "short")
        self.assertFalse(ok)


class TestApiKeyUpdate(unittest.TestCase):
    def test_update_apikey_syncs_credentials(self):
        _mkuser("keyuser")
        provision.provision("keyuser")
        ok, msg = accounts.update_apikey("keyuser", "kimi-coding",
                                         "sk-new-key-abcdef")
        self.assertTrue(ok, msg)
        self.assertTrue(provision.refresh_credentials("keyuser"))
        cp = os.path.join(paths.user_dsh_home("keyuser"), ".credentials.yaml")
        content = open(cp, encoding="utf-8").read()
        self.assertNotIn("sk-new-key-abcdef", content)
        self.assertTrue(content.startswith("MDEXP_PROXY_KEY: mxp_"))
        proxy_token = content.split(":", 1)[1].strip()
        self.assertEqual(accounts.by_proxy_token(proxy_token)["username"], "keyuser")
        self.assertEqual(accounts.get("keyuser")["api_key"], "sk-new-key-abcdef")

    def test_update_apikey_bad_provider(self):
        _mkuser("keyuser2")
        ok, _ = accounts.update_apikey("keyuser2", "openai", "sk-x" * 8)
        self.assertFalse(ok)

    def test_update_apikey_provider_switch_rewrites_env(self):
        _mkuser("keyuser3")
        provision.provision("keyuser3")
        ok, _ = accounts.update_apikey("keyuser3", "zai-coding-cn",
                                       "zai-key-abcdefgh")
        self.assertTrue(ok)
        provision.refresh_credentials("keyuser3")
        cp = os.path.join(paths.user_dsh_home("keyuser3"), ".credentials.yaml")
        content = open(cp).read()
        self.assertTrue(content.startswith("MDEXP_PROXY_KEY: mxp_"))
        self.assertNotIn("zai-key-abcdefgh", content)
        sp = os.path.join(paths.user_dsh_home("keyuser3"), "settings.yaml")
        self.assertIn("zai-coding-cn", open(sp).read())


class TestLoginRoute(unittest.TestCase):
    """FastAPI TestClient 级:登录限流 5 次/10 分钟。"""

    def test_login_lockout(self):
        from fastapi.testclient import TestClient
        sys.path.insert(0, os.path.dirname(HERE))
        import app as appmod
        importlib.reload(appmod)
        c = TestClient(appmod.app)
        _mkuser("lockme")
        for i in range(5):
            r = c.post("/v1/login", json={"username": "lockme",
                                          "password": "bad-pass-%d" % i})
            self.assertEqual(r.status_code, 401)
        r = c.post("/v1/login", json={"username": "lockme",
                                      "password": "password123"})
        self.assertEqual(r.status_code, 429)  # 连正确密码也锁
        appmod._login_fails.clear()
        r = c.post("/v1/login", json={"username": "lockme",
                                      "password": "password123"})
        self.assertEqual(r.status_code, 200)
        tok = r.json()["token"]
        self.assertTrue(tok.startswith("mda_"))
        # 登录轮换:再次登录旧 token 失效
        r2 = c.post("/v1/login", json={"username": "lockme",
                                       "password": "password123"})
        self.assertEqual(r2.status_code, 200)
        r3 = c.get("/v1/conversations",
                   headers={"Authorization": "Bearer " + tok})
        self.assertEqual(r3.status_code, 401)


if __name__ == "__main__":
    unittest.main()
