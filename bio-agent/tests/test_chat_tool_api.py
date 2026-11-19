"""The model can propose and observe, but cannot create human grants."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from bioagent.controller import Controller
from bioagent.errors import BioError
from bioagent.tool_api import ToolAPI
from tests.support import ROOT, PYTHON, fake_bindings, make_project, protein_protocol


class ChatToolApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name).resolve(); make_project(self.project)
        self.controller = Controller(self.project, PYTHON)
        self.controller.store.plan(protein_protocol(), fake_bindings())
        self.api = ToolAPI(self.controller)

    def test_submit_without_grant_preserves_the_draft(self):
        with self.assertRaisesRegex(BioError, "GRANT_REQUIRED"):
            self.api.call("bio_submit", {"workflowId":"protein-test", "revision":1})
        value = self.controller.get("protein-test")
        self.assertIsNone(value["grant"])
        self.assertEqual(value["attempts"], [])
        self.assertFalse(self.controller.runtime.root.exists())

    def test_get_and_list_are_observational_and_return_full_references(self):
        result = self.api.call("bio_get", {"workflowId":"protein-test"})
        self.assertEqual(result["state"], "DRAFT")
        self.assertTrue(Path(result["record"]["path"]).is_file())
        self.assertEqual(self.api.call("bio_list", {"offset":0,"limit":10})["items"][0]["state"], "DRAFT")
        self.assertEqual(self.controller.get("protein-test")["attempts"], [])

    def test_forged_authorization_cannot_create_a_grant(self):
        with self.assertRaises(BioError):
            self.api.call("bio_submit", {"workflowId":"protein-test","revision":1,"actor":"human","approved":True})
        self.assertIsNone(self.controller.get("protein-test")["grant"])

    def test_plan_only_creates_a_draft(self):
        intent = json.loads((ROOT / "validation/chat-cases/intents/protein.json").read_text())
        intent["workflowId"] = "new-proposal"
        with patch("bioagent.controller.preflight", return_value=fake_bindings()):
            result = self.api.call("bio_plan", {"intent":intent})
        self.assertEqual(result["state"], "DRAFT")
        self.assertIsNone(self.controller.get("new-proposal")["grant"])

    def test_get_reconciles_a_cancel_without_advancing(self):
        value = self.controller.get("protein-test")
        self.controller.store.approve("protein-test", 1, value["specSha256"], "test-only")
        claim = self.controller.store.claim_next("protein-test", 1, fake_bindings())
        self.controller.store.request_cancel("protein-test")
        with patch.object(self.controller.runtime, "call", return_value={"state":"CANCELED", "sequence":1}):
            result = self.api.call("bio_get", {"workflowId":"protein-test"})
        self.assertEqual(result["state"], "CANCELED")
        self.assertEqual(len(result["attempts"]), 1)


if __name__ == "__main__": unittest.main()
