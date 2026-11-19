"""Credential export checks use synthetic data and never print matches."""
from pathlib import Path
import importlib.util
import unittest

MODULE_PATH = Path(__file__).resolve().parents[1] / "build_bundle.py"
SPEC = importlib.util.spec_from_file_location("bio_build_bundle", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CredentialExportTests(unittest.TestCase):
    def test_provenance_word_is_not_a_key(self):
        self.assertFalse(MODULE.contains_credential(b"task-" + b"revision" * 5))

    def test_standalone_key_is_blocked(self):
        synthetic_key = b"sk-" + b"A" * 32
        for prefix in (b"", b'"', b"Bearer ", b"key=", b"api-key-"):
            with self.subTest(prefix=prefix):
                self.assertTrue(MODULE.contains_credential(prefix + synthetic_key))

    def test_private_key_headers_are_blocked(self):
        for key_type in (b"", b"RSA ", b"OPENSSH ", b"EC "):
            self.assertTrue(MODULE.contains_credential(b"-----BEGIN " + key_type + b"PRIVATE KEY-----"))


if __name__ == "__main__":
    unittest.main()
