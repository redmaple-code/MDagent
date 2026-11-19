"""Raw model values must not acquire authorization through coercion."""
import unittest
from bioagent.errors import BioError
from bioagent.chat_contracts import load_contracts, validate_call


class ChatContractsTests(unittest.TestCase):
    def test_model_tools_cannot_approve(self):
        for name in ("bio_approve", "bio_review", "shell", "read_file"):
            with self.subTest(name=name), self.assertRaisesRegex(BioError, "INVALID_OPERATION"):
                validate_call(name, {"workflowId": "protein-test"})

    def test_tool_set_is_closed(self):
        self.assertEqual(set(load_contracts()["tools"]), {
            "bio_capabilities", "bio_inputs", "bio_inspect", "bio_plan", "bio_get", "bio_list",
            "bio_submit", "bio_cancel", "bio_continue", "bio_artifacts", "bio_report"})
        with self.assertRaises(BioError):
            validate_call("bio_submit", {"workflowId": "protein-test", "revision": 1, "approved": True})

    def test_pagination_and_revision_are_strict(self):
        for value in (True, "1", None, -1):
            with self.subTest(value=value), self.assertRaises(BioError):
                validate_call("bio_submit", {"workflowId": "protein-test", "revision": value})
        for value in (True, 0, 101, "10"):
            with self.subTest(value=value), self.assertRaises(BioError):
                validate_call("bio_inputs", {"offset": 0, "limit": value})
        self.assertEqual(validate_call("bio_inputs", {"offset": 0, "limit": 10}), {"offset": 0, "limit": 10})

    def test_missing_or_foreign_fields_are_not_defaults(self):
        for args in ({}, {"offset": 0}, {"offset": -1, "limit": 10}, {"offset": 0, "limit": 10, "actor": "root"}):
            with self.subTest(args=args), self.assertRaises(BioError):
                validate_call("bio_list", args)


if __name__ == "__main__":
    unittest.main()
