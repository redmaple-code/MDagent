import tests  # Establish the owned src path for unittest discovery.
from pathlib import Path
import importlib
import tempfile
import unittest

from bioagent.errors import BioError
from tests.support import fake_bindings, protein_protocol


class GrantTests(unittest.TestCase):
    def setUp(self):
        try:
            self.module = importlib.import_module("bioagent.store")
        except ModuleNotFoundError:
            self.fail("execution grants are not implemented")
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = self.module.WorkflowStore(Path(self.temp.name))
        self.planned = self.store.plan(protein_protocol(), fake_bindings())

    def test_missing_or_stale_grant_cannot_claim(self):
        with self.assertRaisesRegex(BioError, "GRANT_REQUIRED"):
            self.store.claim_next("protein-test", 1, fake_bindings())
        self.store.approve("protein-test", 1, self.planned["specSha256"], "test-operator")
        value = protein_protocol(); value["revision"] = 2
        self.store.plan(value, fake_bindings())
        with self.assertRaisesRegex(BioError, "STALE_REVISION"):
            self.store.claim_next("protein-test", 1, fake_bindings())
        with self.assertRaisesRegex(BioError, "GRANT_REQUIRED"):
            self.store.claim_next("protein-test", 2, fake_bindings())

    def test_changed_bindings_invalidate_grant(self):
        self.store.approve("protein-test", 1, self.planned["specSha256"], "test-operator")
        for key in ("runtimeSha256", "environmentSha256", "inputSetSha256"):
            changed = fake_bindings(); changed[key] = "d" * 64
            with self.subTest(key=key), self.assertRaisesRegex(BioError, "GRANT_CHANGED"):
                self.store.claim_next("protein-test", 1, changed)
        self.assertEqual(self.store.get("protein-test")["attempts"], [])

    def test_unaccepted_parent_cannot_unlock_a_stage(self):
        self.store.approve("protein-test", 1, self.planned["specSha256"], "test-operator")
        first = self.store.claim_next("protein-test", 1, fake_bindings())
        with self.assertRaisesRegex(BioError, "RESULT_NOT_ACCEPTED"):
            self.store.record_event(first["attemptId"], "fake-complete", {"state": "COMPLETED"})
        self.assertIsNone(self.store.claim_next("protein-test", 1, fake_bindings()))

    def test_wrong_digest_cannot_be_approved(self):
        with self.assertRaisesRegex(BioError, "CONFLICT"):
            self.store.approve("protein-test", 1, "f" * 64, "test-operator")
        self.assertEqual(self.store.get("protein-test")["state"], "DRAFT")
