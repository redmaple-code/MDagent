import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("install_check", Path(__file__).resolve().parents[1] / "scripts/install_check.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class InstallationCheckTests(unittest.TestCase):
    def test_kit_change_breaks_integrity_check(self):
        import hashlib
        with tempfile.TemporaryDirectory() as temporary:
            kit = Path(temporary)
            (kit / "agents").mkdir()
            code = kit / "agents/entry.py"
            code.write_bytes(b"# original code\n")
            (kit / ".mdagent-kit-manifest.json").write_text(json.dumps({
                "format": "mdagent.prepared-kit.v1", "destination": str(kit),
                "files": [{"path": "agents/entry.py", "prepared_sha256": hashlib.sha256(code.read_bytes()).hexdigest()}]}))
            self.assertTrue(check.kit_integrity(kit))
            code.write_bytes(b"# user modified\n")
            self.assertFalse(check.kit_integrity(kit))

    def test_missing_installation_fails_without_running_commands(self):
        with tempfile.TemporaryDirectory() as root, patch.object(check, "command_ok") as commands:
            report = check.inspect(root, services=True)
            commands.assert_not_called()
            self.assertFalse(report["checks"]["installation_metadata"])
            self.assertFalse(report["model_response_verified"])

    def test_configured_key_is_never_reported_as_verified_dialogue(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data, code, kit = root / "data", root / "code", root / "kit"
            for path in (data / "dsh-home", code / "mcp", code / "node_modules/.bin", kit / "agents/runtime/v3.3", data / "runtime/venv/bin"):
                path.mkdir(parents=True)
            (data / "install.json").write_text(json.dumps({"format": 1, "data_root": str(data), "code_root": str(code), "kit": str(kit), "run_case_base": str(root / "cases"), "engine": "none", "services": []}))
            (data / "dsh-home/settings.yaml").write_text("settings")
            secret = "private-fixture-key-do-not-print"
            credentials = data / "dsh-home/.credentials.yaml"
            credentials.write_text("credentials")
            credentials.chmod(0o600)
            (code / "mcp/md_config.json").write_text(json.dumps({"local_base": str(kit), "data_root": str(data)}))
            (code / "node_modules/.bin/dsh").write_text("entry")
            (data / "runtime/venv/bin/moltemplate.sh").write_text("entry")
            (kit / ".mdagent-kit-manifest.json").write_text("{}")
            for name in ("orchestrator", "claude_interface", "monitor_agent"):
                (kit / "agents/runtime/v3.3" / (name + ".py")).write_text("# entry")
            settings = {"agent-default-model": {"provider": "custom", "model": "test"}, "llm-pi-ai": {"providers": {"custom": {"apiKeyEnv": "MD_KEY"}}}}
            yaml = types.SimpleNamespace(safe_load=lambda text: settings if text == "settings" else {"MD_KEY": secret})
            with patch.dict("sys.modules", {"yaml": yaml}), patch.object(check, "command_ok", return_value=True) as commands:
                report = check.inspect(data)
            self.assertTrue(report["checks"]["model_configuration"])
            self.assertNotIn(secret, json.dumps(report))
            self.assertFalse(report["model_response_verified"])
            self.assertFalse(report["scientific_job_verified"])
            self.assertFalse(report["research_council_verified"])
            self.assertFalse(report["ssh_submission_verified"])
            for args, _ in commands.call_args_list:
                self.assertNotIn("exec", args[0])
                self.assertNotIn("sbatch", args[0])


if __name__ == "__main__":
    unittest.main()
