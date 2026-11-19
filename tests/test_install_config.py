"""Installer config tests: no package install, model request, SSH or kit execution."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("install_config", REPO / "scripts" / "install_config.py")
config = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(config)


class InstallConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mdagent-install-config-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo, self.kit, self.data = self.root / "repo", self.root / "kit", self.root / "data"
        (self.repo / "mcp").mkdir(parents=True)
        (self.repo / "scripts").mkdir()
        for name in config.REQUIRED_KIT_FILES:
            file = self.kit / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("# fixture, never executed\n", encoding="utf-8")
        # Deliberately dirty source template: a fresh install must disable these.
        inherited = {"mode": "ssh", "local_base": "/old-machine/kit",
                     "ssh": {"host": "old-machine", "user": "previous-owner"},
                     "hpc": {"enabled": True, "auto_submit": True},
                     "scnet": {"enabled": True, "auto_submit": True},
                     "exec_bridge": {"sudo_user": "mdorch"}, "schedule": [[0, 10, 300, 300]]}
        self.template = self.repo / "mcp" / "md_config.json.example"
        self.template.write_text(json.dumps(inherited), encoding="utf-8")
        self.seed = (REPO / "scripts" / "settings.yaml.example").read_text(encoding="utf-8")
        (self.repo / "scripts" / "settings.yaml.example").write_text(self.seed, encoding="utf-8")
        self.home = self.data / "dsh-home"

    def prepare(self):
        return config.prepare(self.kit, self.data, repo=self.repo)

    def read(self, name):
        return (self.home / name).read_text(encoding="utf-8")

    def test_missing_or_empty_kit_is_rejected_before_writes(self):
        (self.kit / config.REQUIRED_KIT_FILES[0]).write_text("", encoding="utf-8")
        with self.assertRaises(config.ConfigError):
            self.prepare()
        self.assertFalse(self.data.exists())
        self.assertFalse((self.repo / "mcp" / "md_config.json").exists())

    def test_prepare_clears_inherited_machine_settings_and_does_not_touch_kit(self):
        before = {str(p): p.read_bytes() for p in self.kit.rglob("*") if p.is_file()}
        (self.repo / "mcp" / "md_config.json").write_text('{"mode":"ssh"}', encoding="utf-8")
        result = self.prepare()
        self.assertFalse(result["existing"])
        value = json.loads((self.repo / "mcp" / "md_config.json").read_text(encoding="utf-8"))
        self.assertEqual(value["mode"], "local")
        self.assertEqual(value["local_base"], str(self.kit.resolve()))
        self.assertFalse(value["hpc"]["enabled"])
        self.assertFalse(value["hpc"]["auto_submit"])
        self.assertFalse(value["scnet"]["enabled"])
        self.assertFalse(value["scnet"]["auto_submit"])
        self.assertEqual(value["exec_bridge"], {})
        self.assertEqual(value["ssh"]["host"], "")
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.kit.rglob("*") if p.is_file()})

    def test_data_directory_inside_kit_is_rejected(self):
        with self.assertRaises(config.ConfigError):
            config.prepare(self.kit, self.kit / "data", repo=self.repo)
        self.assertFalse((self.kit / "data").exists())

    def test_existing_user_files_and_machine_config_are_preserved(self):
        self.home.mkdir(parents=True)
        existing = {"settings.yaml": "custom-user-setting: true\n",
                    ".credentials.yaml": 'USER_KEY: "fixture-user-key"\n'}
        for name, text in existing.items():
            (self.home / name).write_text(text, encoding="utf-8")
        machine_path = self.repo / "mcp" / "md_config.json"
        machine_path.write_text('{"mode":"local","user_setting":42}', encoding="utf-8")
        self.assertTrue(self.prepare()["existing"])
        for name, text in existing.items():
            self.assertEqual(self.read(name), text)
        self.assertEqual(json.loads(machine_path.read_text())["user_setting"], 42)

    def test_default_model_uses_explicit_supported_schema(self):
        self.prepare()
        result = config.configure_model(self.data, non_interactive=True,
                                        environ={"MDAGENT_MODEL_API_KEY": "fixture-deepseek-key"})
        self.assertFalse(result["preserved"])
        text = self.read("settings.yaml")
        for expected in ("llm-pi-ai:", "api: openai-completions", 'id: "deepseek-flash"',
                         'baseURL: "https://api.deepseek.com"', "apiKeyEnv: DEEPSEEK_API_KEY"):
            self.assertIn(expected, text)
        self.assertNotIn("kimi-coding", text)
        self.assertNotIn("fixture-deepseek-key", text)
        self.assertEqual(config._credential_value(self.read(".credentials.yaml"), "DEEPSEEK_API_KEY"),
                         "fixture-deepseek-key")

    def test_reinstall_never_overwrites_model_or_key_even_with_new_env_input(self):
        self.prepare()
        config.configure_model(self.data, non_interactive=True,
                               environ={"MDAGENT_MODEL_API_KEY": "fixture-original-key"})
        before = {name: self.read(name) for name in ("settings.yaml", ".credentials.yaml")}
        self.assertTrue(self.prepare()["existing"])
        result = config.configure_model(self.data, "custom", "other-model", "https://other.invalid/v1",
                                        non_interactive=True, environ={"MDAGENT_MODEL_API_KEY": "new-fixture-key"})
        self.assertTrue(result["preserved"])
        for name, text in before.items():
            self.assertEqual(self.read(name), text)

    def test_manual_settings_change_after_prepare_is_preserved(self):
        self.prepare()
        changed = self.seed + "custom-setting: 123\n"
        (self.home / "settings.yaml").write_text(changed, encoding="utf-8")
        self.assertTrue(config.configure_model(self.data, non_interactive=True, environ={})["preserved"])
        self.assertEqual(self.read("settings.yaml"), changed)

    def test_manual_key_after_prepare_is_preserved_on_reinstall(self):
        self.prepare()
        credentials = 'DEEPSEEK_API_KEY: "manually-entered-fixture-key"\n'
        (self.home / ".credentials.yaml").write_text(credentials, encoding="utf-8")
        self.assertTrue(self.prepare()["existing"])
        self.assertTrue(config.configure_model(self.data, non_interactive=True,
                        environ={"MDAGENT_MODEL_API_KEY": "replacement-fixture-key"})["preserved"])
        self.assertEqual(self.read(".credentials.yaml"), credentials)

    def test_manual_key_after_prepare_is_preserved_without_another_prepare(self):
        self.prepare()
        credentials = 'DEEPSEEK_API_KEY: "manually-entered-fixture-key"\n'
        (self.home / ".credentials.yaml").write_text(credentials, encoding="utf-8")
        self.assertTrue(config.configure_model(self.data, non_interactive=True,
                        environ={"MDAGENT_MODEL_API_KEY": "replacement-fixture-key"})["preserved"])
        self.assertEqual(self.read(".credentials.yaml"), credentials)

    def test_openai_compatible_requires_model_and_address(self):
        self.prepare()
        with self.assertRaises(config.ConfigError):
            config.configure_model(self.data, "openai-compatible", non_interactive=True,
                                   environ={"MDAGENT_MODEL_API_KEY": "fixture-key"})
        self.assertEqual(self.read("settings.yaml"), self.seed)

    def test_custom_provider_serializes_model_and_preserves_unrelated_sections(self):
        self.prepare()
        # JSON quotes, colon and hash must remain literal YAML scalar content.
        model = 'vendor:custom#model"name'
        config.configure_model(self.data, "custom", model, "http://127.0.0.1:8000/v1",
                               non_interactive=True, context_window=16384, max_tokens=4096,
                               environ={"MDAGENT_MODEL_API_KEY": 'fixture-key"quoted'})
        text = self.read("settings.yaml")
        self.assertIn("mdagent-custom:", text)
        self.assertIn("apiKeyEnv: MDAGENT_MODEL_API_KEY", text)
        self.assertIn("id: " + json.dumps(model), text)
        self.assertIn("contextWindow: 16384", text)
        self.assertIn("ui-theme:\n  preference: dark", text)
        self.assertIn("agent-presets:\n  default: md-sim-assistant", text)
        self.assertEqual(config._credential_value(self.read(".credentials.yaml"), "MDAGENT_MODEL_API_KEY"),
                         'fixture-key"quoted')

    def test_explicit_reconfigure_retains_other_credentials(self):
        self.prepare()
        config.configure_model(self.data, non_interactive=True,
                               environ={"MDAGENT_MODEL_API_KEY": "original-fixture-key"})
        with (self.home / ".credentials.yaml").open("a", encoding="utf-8") as stream:
            stream.write('OTHER_SERVICE_KEY: "another-fixture-key"\n')
        config.configure_model(self.data, "custom", "model", "https://gateway.invalid/v1",
                               non_interactive=True, reconfigure=True,
                               environ={"MDAGENT_MODEL_API_KEY": "replacement-fixture-key"})
        credentials = self.read(".credentials.yaml")
        self.assertIn('OTHER_SERVICE_KEY: "another-fixture-key"', credentials)
        self.assertEqual(config._credential_value(credentials, "DEEPSEEK_API_KEY"), "original-fixture-key")
        self.assertEqual(config._credential_value(credentials, "MDAGENT_MODEL_API_KEY"), "replacement-fixture-key")

    def test_invalid_key_or_url_never_changes_files(self):
        self.prepare()
        before = {name: self.read(name) for name in ("settings.yaml", ".credentials.yaml")}
        for base_url, key in (("https://user:password@gateway.invalid/v1", "fixture-key"),
                              ("https://gateway.invalid/v1", "fixture-key\ninjected")):
            with self.assertRaises(config.ConfigError):
                config.configure_model(self.data, "custom", "model", base_url, non_interactive=True,
                                       environ={"MDAGENT_MODEL_API_KEY": key})
        for name, text in before.items():
            self.assertEqual(self.read(name), text)

    def test_non_interactive_missing_key_never_changes_files(self):
        self.prepare()
        with self.assertRaises(config.ConfigError):
            config.configure_model(self.data, non_interactive=True, environ={})
        self.assertEqual(self.read("settings.yaml"), self.seed)
        self.assertEqual(config._credential_value(self.read(".credentials.yaml"), "DEEPSEEK_API_KEY"), "")

    @unittest.skipUnless(os.name == "posix", "POSIX permission bits verified on Linux")
    def test_credential_is_owner_only(self):
        self.prepare()
        config.configure_model(self.data, non_interactive=True,
                               environ={"MDAGENT_MODEL_API_KEY": "fixture-key"})
        self.assertEqual(stat.S_IMODE((self.home / ".credentials.yaml").stat().st_mode), 0o600)

    def test_cli_has_no_key_argument_and_no_key_in_output(self):
        self.prepare()
        key = "fixture-do-not-log-key"
        captured = io.StringIO()
        with mock.patch.dict(os.environ, {"MDAGENT_MODEL_API_KEY": key}), contextlib.redirect_stdout(captured):
            rc = config.main(["configure-model", "--data-root", str(self.data), "--non-interactive"])
        self.assertEqual(rc, 0)
        self.assertNotIn(key, captured.getvalue())
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            config.main(["configure-model", "--api-key", key])


if __name__ == "__main__":
    unittest.main()
