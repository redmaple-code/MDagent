import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("install_service", Path(__file__).resolve().parents[1] / "scripts/install_service.py")
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


class InstallServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.code, self.data, self.kit, self.cases, self.units = (root / name for name in ("code with spaces $X%", "data", "kit", "cases", "units"))
        self.code.mkdir()
        (self.code / "run-dsh.sh").write_text("#!/bin/bash\n")
        (self.kit / "agents/runtime/v3.3").mkdir(parents=True)
        (self.kit / "agents/runtime/v3.3/orchestrator.py").write_text("# entry\n")

    def generate(self, **overrides):
        values = dict(code=self.code, data=self.data, kit=self.kit, cases=self.cases, engine="both", unit_dir=self.units)
        values.update(overrides)
        return service.generate(**values)

    def test_existing_runtime_state_is_retained(self):
        self.generate()
        state = self.kit / ".v3.3_state.json"
        state.write_text('{"phase":"MONITOR","task":{"case_id":"own-job"}}')
        before = state.read_bytes()
        self.generate()
        self.assertEqual(state.read_bytes(), before)

    def test_reinstall_changes_fail_before_writes(self):
        self.generate()
        before_env = (self.data / "install.env").read_bytes()
        before_unit = (self.units / "mdagent-web.service").read_bytes()
        before_metadata = (self.data / "install.json").read_bytes()
        for overrides in ({"engine": "none"}, {"cases": self.data / "other-cases"}, {"unit_dir": self.data / "other-units"}):
            with self.assertRaises(ValueError):
                self.generate(**overrides)
            self.assertEqual((self.data / "install.env").read_bytes(), before_env)
            self.assertEqual((self.units / "mdagent-web.service").read_bytes(), before_unit)
            self.assertEqual((self.data / "install.json").read_bytes(), before_metadata)
        self.assertFalse((self.data / "other-cases").exists())

    def test_systemd_literal_paths_and_single_user_environment(self):
        metadata = self.generate()
        unit = (self.units / "mdagent-web.service").read_text()
        # Runner is located under data root; direct quoting covers paths accepted
        # for a custom data root, with specifier and environment expansion chars.
        self.assertEqual(service.systemd_quote('/home/user/a ${B}%/bin'), '"/home/user/a $${B}%%/bin"')
        env = (self.data / "install.env").read_text()
        self.assertIn("DSH_PERMISSION_MODE=workspace-write", env)
        self.assertIn("WEB_HOST=127.0.0.1", env)
        self.assertIn("MDSIM_REGISTER_SCRIPT=", env)
        self.assertIn("AGENTS_DIR=", env)
        self.assertNotIn("API_KEY", env)
        self.assertNotIn("sudo", unit)
        self.assertEqual(json.loads((self.data / "install.json").read_text()), metadata)
        bus = json.loads((self.kit / "status_bus.json").read_text())
        self.assertIn("timestamp", bus)
        self.assertEqual(len(bus["system"]["loadavg"]), 3)
        self.assertGreaterEqual(bus["system"]["total_cores"], 1)

    def test_unmanaged_service_file_is_not_replaced(self):
        self.units.mkdir()
        unit = self.units / "mdagent-web.service"
        unit.write_text("# user-managed service\n")
        with self.assertRaises(ValueError):
            self.generate()
        self.assertEqual(unit.read_text(), "# user-managed service\n")

    def test_check_only_does_not_create_directories(self):
        service.check_previous(self.code, self.data, self.kit, self.cases, "both", self.units)
        self.assertFalse(self.data.exists())
        self.assertFalse(self.cases.exists())
        self.assertFalse(self.units.exists())


if __name__ == "__main__":
    unittest.main()
