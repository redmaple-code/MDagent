"""Transfer boundaries for the Ubuntu source bundle; no kit execution."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import prepare_kit
import build_install_bundle as bundle


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
        print(json.dumps(execute_shell(sys.argv[2], sys.argv[3], cwd=sys.argv[4] if len(sys.argv)>4 else None)))
    elif cmd == "exec_bg":
        print(json.dumps(execute_bg(sys.argv[2], sys.argv[3], cwd=sys.argv[4] if len(sys.argv)>4 else None, log=sys.argv[5] if len(sys.argv)>5 else None)))
'''


class InstallBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="install-bundle-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repository"
        self.kit = self.root / "source-kit"
        self.output = self.root / "transfer.tar.gz"
        self.repo_paths = sorted(bundle.REQUIRED_REPO_FILES)
        for relative in self.repo_paths:
            data = b"# fixture source\r\n" if relative.endswith(".sh") else b"fixture source\n"
            if relative == "scripts/.credentials.yaml.example":
                data = b'# installation template\nMODEL_KEY: ""\n'
            self.write(self.repo, relative, data)
        for name in prepare_kit.REQUIRED_AGENTS:
            self.write(self.kit, prepare_kit.RUNTIME + name + ".py", b"import os\n")
        self.write(self.kit, prepare_kit.RUNTIME + "claude_interface.py", CLI.encode())
        for name in prepare_kit.REQUIRED_CORE:
            self.write(self.kit, "harness/core/" + name + ".py", b"import os\n")
        self.write(self.kit, "agents/runtime/v3.4/web_monitor.py", b"import os\n")
        for name in prepare_kit.REQUIRED_SCHEMAS:
            self.write(self.kit, "harness/schemas/" + name, b'{"type":"object"}\n')
        for relative in ("agents/runtime/v3.3/static/index.html", "agents/runtime/v3.4/static/index.html",
                         "agents/runtime/v3.4/static/simple.html"):
            self.write(self.kit, relative, b"<!doctype html><title>fixture</title>\n")

    def write(self, base, relative, data):
        path = base / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def build(self, **kwargs):
        with mock.patch.object(bundle, "repository_files", return_value=self.repo_paths):
            return bundle.build(self.repo, self.kit, self.output, **kwargs)

    def test_code_only_bundle_has_safe_members_and_final_byte_hashes(self):
        sensitive_repo = (".credentials.yaml", ".env", ".env.local", "md_config.json", "runtime/install.env",
                          "state/current.json", "logs/model.log", "conversation.txt", "gpt2_transcript.txt",
                          ".git/config", "node_modules/package/index.js", ".ssh/id_ed25519", "client.pem",
                          "keys/service.key", "scripts/config.py.bak", "strategy_db.json", "previous.tar.gz",
                          "external_corpus/forum_answers.jsonl", "memory/atoms.jsonl", "cache.sqlite3")
        sensitive_kit = (".credentials.yaml", "sim_config.json", ".v3.3_state.json", "status_bus.json",
                         "logs/current.jsonl", "conversation/session.jsonl", "gpt2_transcript.txt", "README.md",
                         prepare_kit.RUNTIME + "monitor_agent.py.bak", "strategy_db.json", ".env")
        for relative in sensitive_repo:
            self.write(self.repo, relative, b"DO_NOT_DISTRIBUTE_RUNTIME_MATERIAL")
            self.repo_paths.append(relative)
        for relative in sensitive_kit:
            self.write(self.kit, relative, b"DO_NOT_DISTRIBUTE_RUNTIME_MATERIAL")
        before = (self.kit / (prepare_kit.RUNTIME + "claude_interface.py")).read_bytes()
        self.assertEqual(self.build()["status"], "built")
        with tarfile.open(self.output, "r:gz") as archive:
            files = {member.name: archive.extractfile(member).read() for member in archive.getmembers()}
            for member in archive.getmembers():
                self.assertTrue(member.isfile())
                self.assertEqual(PurePosixPath(member.name).parts[0], "MDagent-Ubuntu")
                self.assertNotIn("..", PurePosixPath(member.name).parts)
                self.assertFalse(PurePosixPath(member.name).is_absolute())
                self.assertEqual(member.mode, 0o755 if member.name.endswith(".sh") else 0o644)
            self.assertEqual(files["MDagent-Ubuntu/MDagent/install.sh"], b"# fixture source\n")
            self.assertIn("MDagent-Ubuntu/MDagent/scripts/read_dsh_defaults.py", files)
            self.assertEqual(files["MDagent-Ubuntu/agent_deploy_kit/" + prepare_kit.RUNTIME + "claude_interface.py"], before)
            manifest = json.loads(files[bundle.MANIFEST_NAME])
            self.assertNotIn("DO_NOT_DISTRIBUTE_RUNTIME_MATERIAL", repr(files))
            for record in manifest["files"]:
                self.assertEqual(record["sha256"], hashlib.sha256(files[record["path"]]).hexdigest())
                self.assertEqual(record["bytes"], len(files[record["path"]]))
            self.assertEqual({record["path"] for record in manifest["files"]}, set(files) - {bundle.MANIFEST_NAME})
        self.assertEqual((self.kit / (prepare_kit.RUNTIME + "claude_interface.py")).read_bytes(), before)

    def test_check_only_does_not_create_output_or_directory(self):
        self.output = self.root / "not-created" / "transfer.tar.gz"
        self.assertEqual(self.build(check_only=True)["status"], "checked")
        self.assertFalse(self.output.parent.exists())

    def test_existing_output_is_preserved(self):
        self.output.write_bytes(b"previous completed archive")
        with self.assertRaisesRegex(bundle.BundleError, "OUTPUT_EXISTS"):
            self.build()
        self.assertEqual(self.output.read_bytes(), b"previous completed archive")

    def test_required_installer_helper_must_be_present(self):
        self.repo_paths.remove("scripts/read_dsh_defaults.py")
        with self.assertRaisesRegex(bundle.BundleError, "MISSING_INSTALLER_SOURCE.*read_dsh_defaults"):
            self.build()
        self.assertFalse(self.output.exists())

    def test_paths_are_rejected_before_filesystem_read(self):
        for relative in ("../outside.py", "/outside.py", "C:/outside.py", "nested/../outside.py",
                         "nested\\outside.py", "nested//outside.py", "./outside.py", "bad\npath.py"):
            with self.subTest(relative=relative):
                with mock.patch.object(bundle, "repository_files", return_value=[relative]):
                    with self.assertRaisesRegex(bundle.BundleError, "UNSAFE_REPOSITORY_PATH"):
                        bundle.build(self.repo, self.kit, self.output)
        self.assertFalse(self.output.exists())

    def test_filled_credential_template_stops_without_echoing_value(self):
        secret = b"synthetic-secret-not-for-distribution"
        self.write(self.repo, "scripts/.credentials.yaml.example", b"MODEL_KEY: " + secret + b"\n")
        with self.assertRaises(bundle.BundleError) as raised:
            self.build()
        self.assertIn("CREDENTIAL_TEMPLATE_NOT_BLANK", str(raised.exception))
        self.assertNotIn(secret.decode(), str(raised.exception))

    def test_static_kit_dependency_is_required_without_executing_entry(self):
        self.write(self.kit, prepare_kit.RUNTIME + "monitor_agent.py", b"import unavailable_kit_dependency\n")
        with self.assertRaisesRegex(prepare_kit.PreparationError, "MODULE_DEPENDENCY"):
            self.build()
        self.assertFalse(self.output.exists())

    def test_changed_kit_after_validation_is_refused(self):
        original_collect = prepare_kit.collect_source
        count = 0

        def change_after_check(source):
            nonlocal count
            count += 1
            result = original_collect(source)
            if count == 2:
                result[prepare_kit.RUNTIME + "monitor_agent.py"] = b"# changed after validation\n"
            return result

        with mock.patch.object(prepare_kit, "collect_source", side_effect=change_after_check):
            with self.assertRaisesRegex(bundle.BundleError, "KIT_SOURCE_CHANGED"):
                self.build()

    def test_git_ignores_local_config_and_includes_untracked_installer(self):
        self.write(self.repo, ".gitignore", b"local-config.json\n")
        self.write(self.repo, "local-config.json", b"ignored runtime settings")
        subprocess.run(["git", "init", "--quiet", str(self.repo)], check=True, capture_output=True)
        files = bundle.repository_files(self.repo)
        self.assertNotIn("local-config.json", files)
        self.assertIn("install.sh", files)
        self.assertIn("scripts/read_dsh_defaults.py", files)
        subprocess.run(["git", "-C", str(self.repo), "add", "install.sh"], check=True, capture_output=True)
        self.assertIn("install.sh", bundle.repository_files(self.repo))

    def test_public_corpus_can_exceed_kit_code_limit_and_preserves_attribution(self):
        relative = "external_corpus/gromacs_answered.jsonl"
        data = b'{"url":"https://example.com/forum","author":"fixture-author","text":"' + b"x" * (11 * 1024 * 1024) + b'"}\n'
        self.write(self.repo, relative, data)
        self.repo_paths.append(relative)
        for name in ("external_corpus/README.md", "external_corpus/LICENSE", "external_corpus/corpus_lookup.py"):
            self.write(self.repo, name, b"fixture public reference documentation\n")
            self.repo_paths.append(name)
        self.assertEqual(self.build()["status"], "built")
        with tarfile.open(self.output, "r:gz") as archive:
            self.assertEqual(archive.extractfile("MDagent-Ubuntu/MDagent/" + relative).read(), data)
            self.assertIn("MDagent-Ubuntu/MDagent/external_corpus/LICENSE", archive.getnames())

    def test_suspected_public_corpus_credential_has_safe_filename_only_error(self):
        relative = "external_corpus/lammps_answered.jsonl"
        secret = "sk-" + "a" * 24
        self.write(self.repo, relative, ('{"text":"' + secret + '"}\n').encode())
        self.repo_paths.append(relative)
        with self.assertRaises(prepare_kit.PreparationError) as raised:
            self.build()
        self.assertIn("SUSPECTED_EMBEDDED_CREDENTIAL: " + relative, str(raised.exception))
        self.assertNotIn(secret, str(raised.exception))

    def test_explicit_corpus_omission_is_documented_and_keeps_query_program(self):
        relative = "external_corpus/lammps_answered.jsonl"
        self.write(self.repo, relative, b'{"secret":"synthetic-sensitive-reference"}\n')
        self.repo_paths.append(relative)
        for relative in ("external_corpus/README.md", "external_corpus/corpus_lookup.py"):
            self.write(self.repo, relative, b"fixture public reference support\n")
            self.repo_paths.append(relative)
        self.assertEqual(self.build(include_public_corpus=False)["status"], "built")
        with tarfile.open(self.output, "r:gz") as archive:
            names = archive.getnames()
            self.assertNotIn("MDagent-Ubuntu/MDagent/external_corpus/lammps_answered.jsonl", names)
            self.assertIn("MDagent-Ubuntu/MDagent/external_corpus/corpus_lookup.py", names)
            self.assertIn("MDagent-Ubuntu/MDagent/external_corpus/README.md", names)
            instructions = archive.extractfile("MDagent-Ubuntu/INSTALL-BUNDLE.md").read().decode()
            self.assertIn("explicitly omitted", instructions)
            self.assertIn("Corpus search is optional", instructions)
            manifest = json.load(archive.extractfile(bundle.MANIFEST_NAME))
            self.assertEqual(manifest["omitted_public_corpus"], sorted(bundle.PUBLIC_CORPUS_FILES))

    def make_symlink(self, path, target, directory=False):
        try:
            path.symlink_to(target, target_is_directory=directory)
        except OSError:
            self.skipTest("symbolic links unavailable for this test account")

    def test_repository_symlink_is_rejected(self):
        path = self.repo / "install.sh"
        path.unlink()
        outside = self.write(self.root, "outside.sh", b"# outside source\n")
        self.make_symlink(path, outside)
        with self.assertRaisesRegex(prepare_kit.PreparationError, "SYMLINK_OR_REPARSE_POINT"):
            self.build()

    def test_kit_symlink_is_rejected(self):
        path = self.kit / (prepare_kit.RUNTIME + "monitor_agent.py")
        path.unlink()
        outside = self.write(self.root, "outside.py", b"import os\n")
        self.make_symlink(path, outside)
        with self.assertRaisesRegex(prepare_kit.PreparationError, "SYMLINK_OR_REPARSE_POINT"):
            self.build()

    def test_output_parent_symlink_is_rejected(self):
        link = self.root / "linked-output"
        self.make_symlink(link, self.root, directory=True)
        self.output = link / "transfer.tar.gz"
        with self.assertRaisesRegex(prepare_kit.PreparationError, "SYMLINK_OR_REPARSE_POINT"):
            self.build()


if __name__ == "__main__":
    unittest.main()
