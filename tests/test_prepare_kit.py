"""Offline preparation boundaries. Fixtures are never imported or executed."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("prepare_kit", ROOT / "scripts/prepare_kit.py")
kit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(kit)

CLI = '''import os, sys, json, subprocess
BASE = "/home/kkk/bak1/Students/Master/2025-PJM/agent_deploy_kit_20260630"
def get_context_for_claude():
    raise AssertionError("KIT_MUST_NOT_EXECUTE")
    return {"current_phase": "IDLE", "task": None, "recent_operations": []}
def execute_shell(cmd, description, cwd=None):
    subprocess.run([cmd], shell=False)
    return {"returncode": 0, "stdout": "", "stderr": ""}
def execute_bg(cmd, description, cwd=None, log=None):
    subprocess.Popen([cmd], shell=False)
    return {"returncode": 0, "background": True, "pid": 42, "log": "fixture.log"}
def main():
    cmd = sys.argv[1]
    if cmd == "context":
        print(json.dumps(get_context_for_claude()))
    elif cmd == "exec":
        r = execute_shell(sys.argv[2], sys.argv[3], cwd=sys.argv[4] if len(sys.argv)>4 else None)
        print(json.dumps(r))
    elif cmd == "exec_bg":
        r = execute_bg(sys.argv[2], sys.argv[3], cwd=sys.argv[4] if len(sys.argv)>4 else None, log=sys.argv[5] if len(sys.argv)>5 else None)
        print(json.dumps(r))
'''


class PrepareKitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="prepare-kit-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.destination = self.root / "prepared"
        self.cases = self.root / "cases"
        for name in kit.REQUIRED_AGENTS:
            self.write(kit.RUNTIME + name + ".py", "import os\n")
        self.write(kit.RUNTIME + "claude_interface.py", CLI)
        for name in kit.REQUIRED_CORE:
            self.write("harness/core/" + name + ".py", "import os\n")
        self.write("agents/runtime/v3.4/web_monitor.py", "import os\n")
        for name in kit.REQUIRED_SCHEMAS:
            self.write("harness/schemas/" + name, '{"type":"object"}\n')
        for path in ("agents/runtime/v3.3/static/index.html", "agents/runtime/v3.4/static/index.html", "agents/runtime/v3.4/static/simple.html"):
            self.write(path, "<!doctype html><title>fixture</title>\n")

    def write(self, relative, text):
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def prepare(self, **kwargs):
        return kit.prepare(self.source, self.destination, self.cases, **kwargs)

    def test_code_only_copy_does_not_read_or_copy_runtime_material(self):
        forbidden = (".v3.3_state.json", ".credentials.yaml", "gpt2_transcript.txt", "README.md", "logs/audit.jsonl", "conversation/transcript.jsonl", kit.RUNTIME + "orchestrator.py.bak_20260826")
        for path in forbidden:
            self.write(path, "fixture runtime material\n")
        self.assertEqual(self.prepare()["status"], "prepared")
        for relative in forbidden:
            self.assertFalse((self.destination / relative).exists())
        manifest = json.loads((self.destination / kit.MANIFEST).read_text(encoding="utf-8"))
        record = next(record for record in manifest["files"] if record["path"] == kit.RUNTIME + "claude_interface.py")
        self.assertEqual(record["source_sha256"], kit.digest((self.source / (kit.RUNTIME + "claude_interface.py")).read_bytes()))
        self.assertNotIn("fixture runtime material", json.dumps(manifest))

    def test_check_only_neither_creates_target_nor_runs_source(self):
        before = (self.source / (kit.RUNTIME + "claude_interface.py")).read_bytes()
        self.assertEqual(self.prepare(check_only=True)["status"], "checked")
        self.assertFalse(self.destination.exists())
        self.assertEqual((self.source / (kit.RUNTIME + "claude_interface.py")).read_bytes(), before)

    def test_reuse_preserves_state_and_modified_source_requires_new_target(self):
        self.prepare()
        state = self.destination / ".v3.3_state.json"
        state.write_bytes(b"fixture-current-state")
        self.assertEqual(self.prepare()["status"], "reused")
        self.write(kit.RUNTIME + "monitor_agent.py", "import os\n# changed source\n")
        with self.assertRaisesRegex(kit.PreparationError, "SOURCE_OR_CONFIGURATION_CHANGED"):
            self.prepare()
        self.assertEqual(state.read_bytes(), b"fixture-current-state")

    def test_existing_unprepared_target_is_refused_without_reading_state(self):
        self.destination.mkdir()
        state = self.destination / ".v3.3_state.json"
        state.write_bytes(b"\xff\xfe")
        with self.assertRaisesRegex(kit.PreparationError, "DESTINATION_NOT_A_PREPARED_KIT"):
            self.prepare()
        self.assertEqual(state.read_bytes(), b"\xff\xfe")

    def test_modified_prepared_code_is_never_replaced(self):
        self.prepare()
        changed = self.destination / (kit.RUNTIME + "monitor_agent.py")
        changed.write_text("# local adjustment\n")
        with self.assertRaisesRegex(kit.PreparationError, "PREPARED_CODE_MODIFIED"):
            self.prepare()
        self.assertEqual(changed.read_text(), "# local adjustment\n")

    def test_paths_use_environment_without_legacy_binary_prefixes(self):
        original = ('import os\n'
                    'BASE = "' + kit.OLD_BASES[1] + '"\n'
                    'RUN_CASE_BASE = "' + kit.OLD_CASES + '"\n'
                    'LMP = os.environ.get("LMP", "' + kit.OLD_LMP_DIR + '/lmp_mpi")\n'
                    'MPIRUN = os.environ.get("MPIRUN", "' + kit.OLD_MPI_DIR + '/mpirun")\n'
                    'VARS = os.environ.get("MPI_VARS", "' + kit.OLD_MPI_VARS + '")\n'
                    'extra = ["' + kit.OLD_LMP_DIR + '", "' + kit.OLD_MPI_DIR + '"]\n')
        self.write(kit.RUNTIME + "launch_agent.py", original)
        self.prepare()
        text = (self.destination / (kit.RUNTIME + "launch_agent.py")).read_text(encoding="utf-8")
        self.assertIn("os.environ.get('AGENT_BASE'", text)
        self.assertIn("os.environ.get('RUN_CASE_BASE'", text)
        self.assertIn("os.path.dirname(os.environ.get('LMP'", text)
        self.assertNotIn("/home/kkk/", text)
        self.assertNotIn("/opt/oneapi/", text)

    def test_missing_transitive_module_and_symbol_are_rejected(self):
        path = kit.RUNTIME + "monitor_daemon.py"
        for statement in ("import missing_kit_module\n", "from monitor_agent import nonexistent_symbol\n"):
            with self.subTest(statement=statement):
                self.write(path, statement)
                with self.assertRaisesRegex(kit.PreparationError, "MODULE_DEPENDENCY|IMPORT_SYMBOL"):
                    self.prepare()
                self.assertFalse(self.destination.exists())

    def test_dynamic_agent_dependency_is_verified(self):
        self.write(kit.RUNTIME + "orchestrator.py", 'import os\nclass Fixture:\n    def run(self):\n        self._run_agent("missing_agent", {})\n')
        with self.assertRaisesRegex(kit.PreparationError, "MISSING_DYNAMIC_AGENT_MODULE"):
            self.prepare()

    def test_local_schema_references_and_assets_require_files(self):
        self.write("harness/schemas/sim_config.schema.json", '{"$ref":"extra.schema.json"}')
        with self.assertRaisesRegex(kit.PreparationError, "CODE_FILE_UNREADABLE"):
            self.prepare()
        self.write("harness/schemas/extra.schema.json", '{"type":"object"}')
        self.write("agents/runtime/v3.4/static/index.html", '<script src="ui.js"></script>')
        with self.assertRaisesRegex(kit.PreparationError, "MISSING_STATIC_ASSET_DEPENDENCY"):
            self.prepare()
        self.write("agents/runtime/v3.4/static/ui.js", 'console.log("fixture")')
        self.assertEqual(self.prepare()["status"], "prepared")
        self.assertTrue((self.destination / "harness/schemas/extra.schema.json").exists())

    def test_cli_shape_and_compile_are_hard_checks(self):
        path = kit.RUNTIME + "claude_interface.py"
        self.write(path, CLI.replace('elif cmd == "exec_bg"', 'elif cmd == "wrong_command"'))
        with self.assertRaisesRegex(kit.PreparationError, "CLAUDE_INTERFACE_CONTRACT_MISMATCH"):
            self.prepare()
        self.write(path, "this is invalid python @@@\n")
        with self.assertRaisesRegex(kit.PreparationError, "PYTHON_COMPILE_FAILED"):
            self.prepare()

    def test_suspected_credential_stops_and_diagnostic_never_contains_value(self):
        secret = "fakebutplausible1234567890"
        self.write(kit.RUNTIME + "launch_agent.py", 'import os\nAPI_KEY = "' + secret + '"\n')
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            status = kit.main(["--source", str(self.source), "--destination", str(self.destination), "--run-case-base", str(self.cases)])
        self.assertEqual(status, 2)
        self.assertIn("SUSPECTED_EMBEDDED_CREDENTIAL", stderr.getvalue())
        self.assertIn("launch_agent.py", stderr.getvalue())
        self.assertNotIn(secret, stderr.getvalue())
        self.assertFalse(self.destination.exists())

    def test_source_and_destination_may_not_overlap(self):
        with self.assertRaisesRegex(kit.PreparationError, "MUST_NOT_OVERLAP"):
            kit.prepare(self.source, self.source / "prepared", self.cases)

    def make_symlink(self, link, target, directory=False):
        try:
            link.symlink_to(target, target_is_directory=directory)
        except OSError:
            self.skipTest("symbolic links unavailable for this test account")

    def test_linked_code_file_is_rejected(self):
        path = self.source / (kit.RUNTIME + "monitor_agent.py")
        path.unlink()
        target = self.root / "outside.py"
        target.write_text("import os\n")
        self.make_symlink(path, target)
        with self.assertRaisesRegex(kit.PreparationError, "SYMLINK_OR_REPARSE_POINT"):
            self.prepare()

    def test_linked_manifest_is_rejected(self):
        self.prepare()
        manifest = self.destination / kit.MANIFEST
        outside = self.root / "outside.json"
        manifest.rename(outside)
        self.make_symlink(manifest, outside)
        with self.assertRaisesRegex(kit.PreparationError, "SYMLINK_OR_REPARSE_POINT"):
            self.prepare()

    def test_ignored_state_link_is_not_followed(self):
        # A dangling link would fail immediately if the preparer tried reading state.
        self.make_symlink(self.source / ".v3.3_state.json", self.root / "nonexistent-state")
        self.assertEqual(self.prepare()["status"], "prepared")
        self.assertFalse((self.destination / ".v3.3_state.json").exists())


if __name__ == "__main__":
    unittest.main()
