import tests  # Establish the owned src path for unittest discovery.
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import importlib
import sqlite3
import tempfile
import unittest

from bioagent.errors import BioError
from tests.support import fake_bindings, protein_protocol


class StoreTests(unittest.TestCase):
    def setUp(self):
        try:
            self.module = importlib.import_module("bioagent.store")
        except ModuleNotFoundError:
            self.fail("durable workflow store is not implemented")
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.store = self.module.WorkflowStore(self.project)

    def approve(self):
        value = self.store.plan(protein_protocol(), fake_bindings())
        self.store.approve("protein-test", 1, value["specSha256"], "test-operator")

    def test_revision_conflict_preserves_history(self):
        old = self.store.plan(protein_protocol(), fake_bindings())
        changed = protein_protocol(); changed["title"] = "New conditions"
        with self.assertRaisesRegex(BioError, "CONFLICT"): self.store.plan(changed, fake_bindings())
        changed["revision"] = 2
        self.store.plan(changed, fake_bindings())
        reopened = self.module.WorkflowStore(self.project)
        self.assertEqual(reopened.get("protein-test")["revision"], 2)
        self.assertEqual(reopened.get("protein-test", 1)["specSha256"], old["specSha256"])

    def test_two_controllers_claim_one_attempt(self):
        self.approve()
        stores = [self.module.WorkflowStore(self.project), self.module.WorkflowStore(self.project)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda store: store.claim_next("protein-test", 1, fake_bindings()), stores))
        self.assertEqual(sum(value is not None for value in results), 1)
        self.assertEqual(len(self.store.get("protein-test")["attempts"]), 1)

    def test_duplicate_event_is_idempotent(self):
        self.approve()
        attempt = self.store.claim_next("protein-test", 1, fake_bindings())
        value = {"state": "RUNNING"}
        self.store.record_event(attempt["attemptId"], "event-1", value)
        self.store.record_event(attempt["attemptId"], "event-1", value)
        with self.assertRaisesRegex(BioError, "CONFLICT"):
            self.store.record_event(attempt["attemptId"], "event-1", {"state": "FAILED"})
        result = self.module.WorkflowStore(self.project).get("protein-test")
        self.assertEqual(result["attempts"][0]["eventCount"], 1)
        self.assertEqual(result["state"], "RUNNING")

    def test_unknown_database_version_is_read_only_failure(self):
        with sqlite3.connect(self.project / "workflow.sqlite3") as connection:
            connection.execute("PRAGMA user_version=99")
        before = (self.project / "workflow.sqlite3").read_bytes()
        with self.assertRaisesRegex(BioError, "UNKNOWN_DATABASE_VERSION"):
            self.module.WorkflowStore(self.project)
        self.assertEqual((self.project / "workflow.sqlite3").read_bytes(), before)
