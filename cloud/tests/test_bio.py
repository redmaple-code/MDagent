"""Offline tenant/gate checks; no SSH, model requests, real processes or scientific jobs."""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bio
import paths
import provision
import tenants

FIXTURES = Path(tempfile.mkdtemp(prefix="mdagent-bio-unit-")).resolve()
SOURCE = Path(__file__).resolve().parents[2]


class TestBioPlatform(unittest.TestCase):
    def setUp(self):
        self.home = FIXTURES / self._testMethodName
        self.home.mkdir(mode=0o700)
        self.accounts = self.home / "accounts"
        self.accounts.mkdir(mode=0o700)
        self.registry = self.accounts / "bio-targets"
        self.registry.mkdir(mode=0o700)
        self.env = patch.dict(os.environ, {"MDAGENT_BIO_CONFIG_DIR": str(self.registry), "MDAGENT_SKIP_UNIX": "1"})
        self.env.start()
        self.addCleanup(self.env.stop)
        for name, value in (("accounts_dir", self.accounts), ("code_dir", SOURCE),
                            ("user_data_dir", self.home / "data"), ("user_dsh_home", self.home / "dsh-home")):
            mock = patch.object(paths, name, return_value=str(value))
            mock.start()
            self.addCleanup(mock.stop)
        self.target = json.loads((SOURCE / "bio-agent/deploy/execution-target.example.json").read_text())
        self.target.update(tenantId="alice", nodeId="n_test", projectsRoot="/compute/alice", releaseRoot="/compute/bio", python="/compute/env/bin/python")
        self.config = {"schemaVersion": "bio.platform-binding.v1", "mode": "node-ssh", "executionTarget": self.target,
                       "allowedActions": sorted(bio.ACTIONS), "gatewayPython": "/gateway/python", "gatewayReleaseRoot": "/gateway/bio",
                       "gatewayConnectionFile": "/gateway/alice/connection.json"}
        self.value = {"schemaVersion": "bio.platform-access.v1", "tenantId": "alice", "nodeId": "n_test",
                      "executionTargetSha256": bio.target_sha256(self.target), "sessionId": "my-session", "action": "bio_submit",
                      "workflowId": "my-workflow", "revision": 1}
        self.relay = SimpleNamespace(status=lambda node: {"online": node == "n_test"})
        for object_, name, value in ((bio.accounts, "verify_world_token", {"username": "alice", "node_id": "n_test"}),
                                    (bio.accounts, "get", {"username": "alice", "status": "active"}),
                                    (bio.nodes, "get", {"username": "alice", "revoked": False})):
            mock = patch.object(object_, name, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)
        self.touch = patch.object(bio.accounts, "touch_world_token")
        self.touch.start()
        self.addCleanup(self.touch.stop)

    def save(self):
        file = self.registry / "alice.json"
        file.write_text(json.dumps(self.config))
        file.chmod(0o600)
        return file

    def deny(self, value, status=403):
        with self.assertRaises(HTTPException) as observed:
            bio.authorize(value, "synthetic-instance-token", self.relay)
        self.assertEqual(observed.exception.status_code, status)

    def test_absent_registry_leaves_roster_disabled(self):
        self.assertIsNone(bio.signature("alice"))
        self.assertEqual(bio.overlay_entries("alice", "n_test"), [])
        self.deny(self.value)

    def test_bound_action_authorized_without_any_compute(self):
        self.save()
        with patch("subprocess.Popen") as spawn:
            decision = bio.authorize(self.value, "synthetic-instance-token", self.relay)
        self.assertEqual(decision, {**self.value, "allowed": True})
        spawn.assert_not_called()

    def test_cross_tenant_node_target_and_action_rejected(self):
        self.save()
        for key, value in (("tenantId", "bob"), ("nodeId", "n_other"), ("executionTargetSha256", "0" * 64)):
            self.deny({**self.value, key: value})
        self.config["allowedActions"].remove("bio_submit")
        self.save()
        self.deny(self.value)

    def test_general_commands_and_malformed_requests_rejected(self):
        self.save()
        for value in ({**self.value, "cmd": "python -c x"}, {**self.value, "action": "sim_exec"},
                      {**self.value, "sessionId": "bad\n"}, {**self.value, "revision": True},
                      {**self.value, "workflowId": "../other"}, [self.value]):
            self.deny(value, 400)

    def test_revoked_world_token_account_or_node_rejected(self):
        self.save()
        with patch.object(bio.accounts, "verify_world_token", return_value=None):
            self.deny(self.value, 401)
        with patch.object(bio.accounts, "get", return_value={"status": "revoked"}):
            self.deny(self.value)
        for node in (None, {"username": "bob"}, {"username": "alice", "revoked": True}):
            with patch.object(bio.nodes, "get", return_value=node):
                self.deny(self.value)

    def test_offline_node_and_changed_target_fail_closed(self):
        self.save()
        with patch.object(self.relay, "status", return_value={"online": False}):
            self.deny(self.value, 503)
        self.target["targetRevision"] += 1
        self.save()
        self.deny(self.value)

    def test_writable_or_linked_registry_rejected(self):
        file = self.save()
        file.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "CONFIGURATION_INVALID"):
            bio.load_binding("alice")
        file.chmod(0o600)
        self.registry.chmod(0o777)
        with self.assertRaisesRegex(ValueError, "CONFIGURATION_INVALID"):
            bio.load_binding("alice")

    def test_symlinked_registry_and_unknown_config_fields_rejected(self):
        destination = self.home / "operator-config.json"
        destination.write_text(json.dumps(self.config))
        destination.chmod(0o600)
        (self.registry / "alice.json").symlink_to(destination)
        with self.assertRaisesRegex(ValueError, "CONFIGURATION_INVALID"):
            bio.load_binding("alice")
        self.config["shellCommand"] = "arbitrary-command"
        destination.write_text(json.dumps(self.config))
        with self.assertRaisesRegex(ValueError, "CONFIGURATION_INVALID"):
            bio.load_binding("alice")

    def test_generated_node_ssh_preset_keeps_final_host_and_original_default(self):
        self.save()
        entries = bio.overlay_entries("alice", "n_test")
        self.assertNotIn("default", entries[0]["config"])
        root = Path(entries[0]["config"]["roots"][-1]["path"])
        preset = json.loads((root / "bio/agent.cordis.yml").read_text())[0]
        config = preset["config"]
        self.assertEqual(preset["name"], "dsh-bio-agent")
        self.assertEqual(config["mode"], "node-ssh")
        self.assertEqual(config["gatewayConnectionFile"], "/gateway/alice/connection.json")
        self.assertNotIn("computePython", config)
        self.assertEqual(json.loads(Path(config["executionTargetFile"]).read_text()), self.target)
        self.assertEqual(Path(config["platformGateModule"]), SOURCE / "bio-agent/web/runtime/platform-gate.mjs")
        self.assertEqual(bio.overlay_entries("alice", "n_other"), [])
        self.assertEqual(bio.overlay_entries("alice", "n_test"), entries)

    def test_changed_binding_recreates_tenant_instance(self):
        self.save()
        old = bio.signature("alice")
        self.config["allowedActions"].remove("bio_submit")
        self.save()
        instance = {"proc": SimpleNamespace(poll=lambda: None), "node_id": "n_test", "bio_signature": old,
                    "ready": True, "port": 21001, "last": 0}
        with patch.dict(tenants._inst, {"alice": instance}, clear=True), patch.object(tenants, "_cleanup_inst") as cleanup, \
                patch.object(tenants, "_spawn_and_wait", return_value=21002) as spawn:
            self.assertEqual(tenants.ensure("alice", "n_test"), 21002)
        cleanup.assert_called_once_with(instance)
        spawn.assert_called_once_with("alice", "n_test")

    def test_failed_overlay_revokes_new_short_ticket(self):
        with patch.dict(tenants._inst, {}, clear=True), patch.object(tenants, "_stable_port", return_value=21001), \
                patch.object(tenants.accounts, "mint_world_token", return_value="synthetic-instance-token"), \
                patch.object(tenants.accounts, "revoke_world_token") as revoke, \
                patch.object(tenants, "_write_overlay", side_effect=ValueError("BIO_PROFILE_CONFLICT")), \
                patch.object(tenants.subprocess, "Popen") as spawn:
            with self.assertRaisesRegex(ValueError, "PROFILE_CONFLICT"):
                tenants._spawn_and_wait("alice", "n_test")
        revoke.assert_called_once_with("synthetic-instance-token")
        spawn.assert_not_called()

    def test_existing_profile_adds_links_without_replacing_settings_or_bundle(self):
        home = Path(paths.user_dsh_home("alice"))
        profile = home / "profiles/web"
        profile.mkdir(parents=True)
        package = profile / "package.json"
        package.write_text('{"dsh":{"profile":{"bundles":["original-bundle"]}}}')
        preset = home / ".agent-presets/md-sim-assistant/agent.cordis.yml"
        preset.parent.mkdir(parents=True)
        preset.write_text("original-agent-preset")
        (home / "profiles/node_modules").mkdir()
        (home / "profiles/node_modules/dsh-remote-world").symlink_to(SOURCE / "dsh-plugins/dsh-remote-world")
        before = (package.read_bytes(), preset.read_bytes())
        with patch.object(provision, "ensure_web_runtime") as runtime:
            self.assertEqual(provision.enable_web_profile("alice"), str(profile))
        self.assertEqual((package.read_bytes(), preset.read_bytes()), before)
        self.assertTrue((home / "profiles/node_modules/bio-web-tool-cards").is_symlink())
        runtime.assert_not_called()

    def test_http_gate_enforces_body_limit_and_authentication(self):
        self.save()
        app = FastAPI()
        bio.mount(app, self.relay)
        client = TestClient(app)
        self.assertEqual(client.post("/v1/bio/access", content="x" * 8193).status_code, 413)
        self.assertEqual(client.post("/v1/bio/access", content="{").status_code, 400)
        self.assertEqual(client.post("/v1/bio/access", json=self.value).status_code, 401)
        self.assertEqual(client.post("/v1/bio/access", json=self.value,
                                     headers={"X-World-Token": "synthetic-instance-token"}).json()["allowed"], True)
        with patch.object(bio.accounts, "verify_world_token", return_value=None):
            self.assertEqual(client.post("/v1/bio/access", json=self.value).status_code, 401)

    def test_invite_required_outside_legacy_test_signup_mode(self):
        with patch.dict(os.environ, {"MDAGENT_OPEN_SIGNUP": "0"}):
            ok, _ = bio.accounts.apply("alice", "", "", "deepseek", "", "offline-password")
            self.assertFalse(ok)
            invite = bio.accounts.create_invite(note="offline-gate-fixture")
            ok, reason = bio.accounts.apply("alice", "", "", "deepseek", "", "offline-password", invite_code=invite)
            self.assertTrue(ok, reason)

    def test_account_hyphens_supported_in_binding_filename(self):
        self.target["tenantId"] = "alice-test"
        file = self.registry / "alice-test.json"
        file.write_text(json.dumps(self.config))
        file.chmod(0o600)
        self.assertEqual(bio.load_binding("alice-test")["executionTarget"]["tenantId"], "alice-test")


if __name__ == "__main__":
    print("Engineering fixtures retained at", FIXTURES)
    unittest.main()
