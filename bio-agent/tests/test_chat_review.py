"""Compact display keeps exact authorization conditions and the TTY boundary."""
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from bioagent.cli import review
from bioagent.controller import Controller
from bioagent.errors import BioError
from bioagent.review_view import review_view
from tests.support import PYTHON,fake_bindings,make_project,protein_protocol


class Terminal(io.StringIO):
    def isatty(self):return True


class ChatReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.project=Path(self.temp.name).resolve();make_project(self.project)
        self.controller=Controller(self.project,PYTHON)
        self.value=self.controller.store.plan(protein_protocol(),fake_bindings())

    def test_compact_format_preserves_the_tty_gate(self):
        with self.assertRaisesRegex(BioError,"INTERACTIVE_REVIEW_REQUIRED"):
            review(self.controller,"protein-test",1,io.StringIO("approve\n"),io.StringIO(),format="compact")
        self.assertIsNone(self.controller.get("protein-test")["grant"])

    def test_compact_review_displays_all_conditions_and_full_refs(self):
        value=review_view(self.controller,self.value,{**fake_bindings(),"environment":{"files":[{"path":"long-file","sha256":"a"*64}]}})
        text=value["text"]
        for literal in (self.value["specSha256"],"713","timestepFs","protein.pdb","wallTimeSeconds","ps","kJ/mol/nm","analysis","nonbondedMethod"):
            self.assertIn(literal,text)
        self.assertTrue(Path(value["record"]["path"]).exists())

    def test_wrong_full_hash_preserves_the_draft(self):
        output=Terminal()
        with patch.object(self.controller,"bindings_for",return_value={**fake_bindings(),"environment":{}}):
            with self.assertRaisesRegex(BioError,"REVIEW_REJECTED"):
                review(self.controller,"protein-test",1,Terminal("approve\n"),output,format="compact")
        self.assertIsNone(self.controller.get("protein-test")["grant"])

    def test_control_characters_cannot_hide_approval_conditions(self):
        value={**self.value,"protocol":{**self.value["protocol"],"title":"untrusted\x1b[2J\u202e"}}
        text=review_view(self.controller,value,{**fake_bindings(),"environment":{}})["text"]
        self.assertNotIn("\x1b",text);self.assertNotIn("\u202e",text)
        self.assertIn(self.value["specSha256"],text)


if __name__=="__main__":unittest.main()
