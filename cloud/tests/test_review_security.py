"""Isolated PR security regressions; no Unix users, services or external requests."""
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(tempfile.mkdtemp(prefix="mdagent-review-security-")).resolve()
os.environ.update(MDAGENT_HOME=str(ROOT), MDAGENT_SKIP_UNIX="1", MDAGENT_OPEN_SIGNUP="1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
import accounts
import app
import paths
import provision
import tenants
import tenant_files


class CredentialBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.name = "review" + hashlib.sha256(self.id().encode()).hexdigest()[:8]
        self.assertTrue(accounts.apply(self.name, "", "", "kimi-coding", "fixture-provider-value", "fixture-password123")[0])
        self.main, error = accounts.approve(self.name)
        self.assertIsNone(error)
        self.external = "synthetic-external-credential-" + self.name
        def configure(db):
            user = accounts._find(db, self.name)
            user.update(role="admin", external_api_token_sha256=accounts._hash(self.external))
        accounts._txn(configure)
        self.client = TestClient(app.app)

    def test_external_credentials_cannot_manage_primary_credentials(self):
        before = accounts.get(self.name)
        headers = {"Authorization": "Bearer " + self.external}
        self.assertEqual(self.client.get("/v1/me", headers=headers).json()["role"], "user")
        with patch.object(provision, "refresh_credentials") as refresh:
            for path, body in (("/v1/me/token/rotate", None),
                               ("/v1/me/passwd", {"password": "new-fixture-password"}),
                               ("/v1/me/apikey", {"provider": "kimi-coding", "api_key": "new-fixture-provider"})):
                with self.subTest(path=path):
                    reply = self.client.post(path, headers=headers, json=body)
                    self.assertEqual(reply.status_code, 403)
                    self.assertNotIn("token", reply.json())
            refresh.assert_not_called()
        after = accounts.get(self.name)
        for field in ("pwd", "token_sha256", "provider", "api_key", "external_api_token_sha256"):
            self.assertEqual(after[field], before[field])
        self.assertEqual(accounts.by_token(self.main)["role"], "admin")

    def test_external_bearer_does_not_gain_cookie_authority(self):
        self.client.cookies.set("mdexp_session", accounts.rotate_session_token(self.name))
        reply = self.client.post("/v1/me/token/rotate", headers={"Authorization": "Bearer " + self.external})
        self.assertEqual(reply.status_code, 403)
        self.assertIsNotNone(accounts.by_token(self.main))

    def test_primary_token_and_browser_credentials_keep_management_access(self):
        reply = self.client.post("/v1/me/token/rotate", headers={"Authorization": "Bearer " + self.main})
        self.assertEqual(reply.status_code, 200)
        primary = reply.json()["token"]
        self.assertEqual(accounts.by_token(primary)["role"], "admin")
        self.assertIsNone(accounts.by_token(self.main))
        self.client.cookies.set("mdexp_session", accounts.rotate_session_token(self.name))
        reply = self.client.post("/v1/me/passwd", json={"password": "new-fixture-password"})
        self.assertEqual(reply.status_code, 200)
        self.assertIsNotNone(accounts.verify_login(self.name, "new-fixture-password"))
        with patch.object(provision, "refresh_credentials", return_value=True):
            for headers in ({"Authorization": "Bearer " + primary}, {}):
                reply = self.client.post("/v1/me/apikey", headers=headers,
                                         json={"provider": "kimi-coding", "api_key": "new-fixture-provider"})
                self.assertEqual(reply.status_code, 200)
        self.assertEqual(accounts.get(self.name)["api_key"], "new-fixture-provider")


class TenantFileBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.base = ROOT / self._testMethodName
        self.home = self.base / "users/alice/dsh-home"
        self.home.mkdir(parents=True, mode=0o700)
        self.outside = self.base / "outside"
        self.outside.mkdir()
        self.sentinel = self.outside / "sentinel"
        self.sentinel.write_text("outside sentinel\n")
        for object_, name, value in ((paths, "accounts_dir", self.base / "accounts"),
                                     (paths, "user_dsh_home", self.home)):
            mock = patch.object(object_, name, return_value=str(value))
            mock.start(); self.addCleanup(mock.stop)
        mock = patch.object(tenants.bio, "overlay_entries", return_value=[])
        mock.start(); self.addCleanup(mock.stop)

    def assertOutsideUnchanged(self):
        self.assertEqual(self.sentinel.read_text(), "outside sentinel\n")

    def test_fixed_temporary_symlink_cannot_redirect_overlay_write(self):
        (self.home / "web-overlay.yml.tmp").symlink_to(self.sentinel)
        overlay = Path(tenants._write_overlay("alice", "n_fixture", "synthetic-world-ticket"))
        self.assertOutsideUnchanged()
        self.assertEqual(overlay.stat().st_mode & 0o777, 0o600)
        self.assertIn("n_fixture", overlay.read_text())

    def test_gate_symlink_is_rejected_without_touching_target(self):
        (self.home / ".mdexp-gate.json").symlink_to(self.sentinel)
        with self.assertRaises((OSError, ValueError)):
            tenants._write_gate_file("alice", "synthetic-world-ticket")
        self.assertOutsideUnchanged()

    def test_ancestor_symlink_is_rejected(self):
        original = self.home.parent
        original.rename(original.with_name("original-alice"))
        original.symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises((OSError, ValueError)):
            tenants._write_overlay("alice", "n_fixture", "synthetic-world-ticket")
        self.assertFalse((self.outside / "dsh-home").exists())
        self.assertOutsideUnchanged()

    def assertLogRedirectRejected(self):
        def fake_spawn(*args, **kwargs):
            kwargs["stdout"].write("fixture child output\n")
            kwargs["stdout"].flush()
            raise RuntimeError("FIXTURE_STOP_BEFORE_SERVICE")
        with patch.dict(tenants._inst, {}, clear=True), patch.object(tenants, "_stable_port", return_value=21001), \
             patch.object(tenants.bio, "signature", return_value=None), \
             patch.object(tenants.accounts, "mint_world_token", return_value="synthetic-world-ticket"), \
             patch.object(tenants.accounts, "revoke_world_token"), \
             patch.object(tenants.subprocess, "Popen", side_effect=fake_spawn) as spawn:
            with self.assertRaises((OSError, ValueError, RuntimeError)):
                tenants._spawn_and_wait("alice", "n_fixture")
            spawn.assert_not_called()
        self.assertOutsideUnchanged()

    def test_log_symlink_is_rejected_before_privileged_spawn(self):
        (self.home / "web.log").symlink_to(self.sentinel)
        self.assertLogRedirectRejected()

    def test_log_hardlink_is_rejected_before_privileged_spawn(self):
        os.link(self.sentinel, self.home / "web.log")
        self.assertLogRedirectRejected()

    def test_atomic_publish_never_chowns_a_swapped_destination(self):
        replace = os.replace
        def swap(source, destination, **kwargs):
            (self.home / destination).symlink_to(self.sentinel)
            return replace(source, destination, **kwargs)
        with patch.object(tenants, "_file_owner", return_value=(os.geteuid(), os.getegid())), \
             patch.object(tenant_files.os, "fchown", wraps=os.fchown) as chown_fd, \
             patch.object(tenant_files.os, "chown", side_effect=AssertionError("PATH_CHOWN_FORBIDDEN")), \
             patch.object(tenant_files.os, "replace", side_effect=swap):
            path = Path(tenants._write_gate_file("alice", "synthetic-world-ticket"))
        chown_fd.assert_called_once()
        self.assertFalse(path.is_symlink())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertOutsideUnchanged()
        self.assertEqual(list(self.home.glob(".mdagent-*")), [])

    def test_exit_watcher_uses_owned_log_descriptor_after_name_swap(self):
        from types import SimpleNamespace
        with tenant_files.open_log(self.home) as stream:
            stream.write("EADDRINUSE fixture\n"); stream.flush()
            (self.home / "web.log").rename(self.home / "original.log")
            (self.home / "web.log").symlink_to(self.sentinel)
            self.assertTrue(tenants._log_has_eaddrinuse(stream))
            tenants._watch_exit(SimpleNamespace(pid=42, wait=lambda: 0), stream)
        self.assertIn("pid=42", (self.home / "original.log").read_text())
        self.assertEqual((self.home / "original.log").stat().st_mode & 0o777, 0o600)
        self.assertOutsideUnchanged()

    def test_cleanup_cannot_follow_a_swapped_parent_and_still_revokes_ticket(self):
        original = self.home.parent
        original.rename(original.with_name("original-alice"))
        fake_home = self.outside / "dsh-home"
        fake_home.mkdir(); (fake_home / "web-overlay.yml").write_text("outside overlay")
        original.symlink_to(self.outside, target_is_directory=True)
        with patch.object(tenants.accounts, "revoke_world_token") as revoke:
            tenants._cleanup_inst({"home": str(self.home), "overlay": str(self.home / "web-overlay.yml"),
                                   "world_token": "synthetic-world-ticket"})
        revoke.assert_called_once_with("synthetic-world-ticket")
        self.assertEqual((fake_home / "web-overlay.yml").read_text(), "outside overlay")
        self.assertOutsideUnchanged()


if __name__ == "__main__":
    unittest.main()
