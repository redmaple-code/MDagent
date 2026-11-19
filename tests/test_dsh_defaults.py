"""Shell defaults must be decoded scalars, with no model or dsh calls."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest


REPO = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("read_dsh_defaults", REPO / "scripts/read_dsh_defaults.py")
defaults = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(defaults)


class DshDefaultTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="dsh-defaults-")
        self.addCleanup(self.temporary.cleanup)
        self.settings = Path(self.temporary.name) / "settings.yaml"

    def write(self, model='"deepseek-flash"', preset="md-sim-assistant"):
        self.settings.write_text("agent-default-model:\n  provider: deepseek\n  model: " + model
                                 + "\nagent-presets:\n  default: " + preset + "\n", encoding="utf-8")

    def test_quoted_model_is_decoded(self):
        self.write()
        self.assertEqual(defaults.default_value(self.settings, "model"), "deepseek/deepseek-flash")
        self.assertEqual(defaults._fallback(self.settings.read_text())["agent-default-model"]["model"],
                         "deepseek-flash")

    def test_custom_model_with_spaces_and_quotes_is_not_truncated(self):
        self.write(model=json.dumps('vendor model "custom"'))
        self.assertEqual(defaults.default_value(self.settings, "model"), 'deepseek/vendor model "custom"')
        self.assertEqual(defaults._fallback(self.settings.read_text())["agent-default-model"]["model"],
                         'vendor model "custom"')

    def test_preset_scalar_is_decoded(self):
        self.write(preset="'my custom preset''s name' # comment")
        self.assertEqual(defaults.default_value(self.settings, "preset"), "my custom preset's name")
        self.assertEqual(defaults._fallback(self.settings.read_text())["agent-presets"]["default"],
                         "my custom preset's name")

    def test_invalid_document_does_not_echo_source(self):
        self.settings.write_text('inline_key: "fixture-secret-value"\nagent-default-model: [broken\n', encoding="utf-8")
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            result = defaults.main([str(self.settings), "model"])
        self.assertEqual(result, 1)
        self.assertNotIn("fixture-secret-value", captured.getvalue())

    def test_newlines_cannot_inject_shell_arguments(self):
        self.write(model='"model\\n--unsafe"')
        with self.assertRaises(ValueError):
            defaults.default_value(self.settings, "model")


if __name__ == "__main__":
    unittest.main()
