"""Stage-dependency transport budgets; sparse fixtures, no simulation or runner launch."""
import tests
import base64
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bioagent.controller import Controller
from bioagent.errors import BioError, atomic_json, sha256_file
from bioagent.execution_target import local_target
from tests.support import PYTHON, protein_protocol

MIB = 1024 * 1024


class TransferBudgetTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="bio-transfer-budget-")).resolve()
        self.project = self.root / "projects/session"
        self.project.mkdir(parents=True)
        self.target = local_target(self.project, PYTHON)
        self.target["resourcePolicy"]["maxOutputBytes"] = 128 * MIB
        self.controller = Controller(self.project, PYTHON, self.target)
        self.output = self.controller.runtime.root / "parent/output"
        self.output.mkdir(parents=True)
        self.source = self.output / "trajectory.dcd"
        with self.source.open("wb") as stream:
            stream.truncate(64 * MIB + 1)
        self.digest = sha256_file(self.source)
        self.sent = 0
        def receive(request):
            self.assertEqual(request["action"], "upload")
            chunk = base64.b64decode(request["data"], validate=True)
            self.assertLessEqual(len(chunk), 65536)
            self.sent += len(chunk)
            end = request["offset"] + len(chunk)
            return {"offset": end, "complete": end == request["totalSize"]}
        mock = patch.object(self.controller.runtime, "call", side_effect=receive)
        self.receive = mock.start(); self.addCleanup(mock.stop)
        self.protocol = protein_protocol()
        self.protocol["stages"][2]["resources"]["maxOutputBytes"] = 128 * MIB
        self.request = {"stage": self.protocol["stages"][2]}
        atomic_json(self.controller._request_path("parent"), self.request)
        self.manifest = {"schemaVersion": "ksh.stage-result.v1", "acceptance": {"engineering": True, "scientificConvergence": False},
                         "modelId": "fixture-model", "files": [{"path": self.source.name, "role": "trajectory",
                         "sha256": self.digest, "byteSize": self.source.stat().st_size}]}
        self.path = self.output / "manifest.json"
        atomic_json(self.path, self.manifest)
        self.acceptance = {"manifestSha256": sha256_file(self.path)}

    def value(self):
        return {"workflowId": self.protocol["workflowId"], "revision": 1, "protocol": self.protocol,
                "attempts": [{"stageId": "production", "attemptId": "parent", "state": "COMPLETED", "acceptance": self.acceptance}]}

    def make_request(self, value):
        # Independent scientific acceptance is a sentinel; real manifest/hash/transport code is used.
        with patch("bioagent.controller.verify_result", return_value=self.acceptance):
            return self.controller._make_request(value, {"attemptId": "next", "stage": self.protocol["stages"][3]})

    def test_large_verified_parent_artifact_reaches_validation_transport(self):
        request = self.make_request(self.value())
        self.assertEqual(request["dependencyBundles"][0]["path"], "deps/production/manifest.json")
        self.assertEqual(self.sent, self.source.stat().st_size + self.path.stat().st_size)
        self.assertGreater(self.receive.call_count, 1024)

    def test_external_structure_upload_limit_remains_64_mib(self):
        with self.assertRaisesRegex(BioError, "INPUT_TOO_LARGE"):
            self.controller.runtime.upload_file("next", "sources/protein.pdb", self.source, self.digest)
        self.receive.assert_not_called()

    def test_source_and_target_budgets_both_limit_dependency_transfer(self):
        for source_limit, target_limit in ((64 * MIB, 128 * MIB), (128 * MIB, 64 * MIB)):
            with self.subTest(source_limit=source_limit, target_limit=target_limit):
                self.request["stage"]["resources"]["maxOutputBytes"] = source_limit
                atomic_json(self.controller._request_path("parent"), self.request)
                self.target["resourcePolicy"]["maxOutputBytes"] = target_limit
                with self.assertRaisesRegex(BioError, "DEPENDENCY_OUTPUT_BUDGET_EXCEEDED"):
                    self.make_request(self.value())
                self.receive.assert_not_called()

    def test_continuation_and_recovery_use_source_output_budget(self):
        value = self.value(); value["protocol"]["inputs"] = []
        stage = {**self.protocol["stages"][2], "dependsOn": []}
        for kind in ("continuation", "recovery"):
            with self.subTest(kind=kind):
                value["reference"] = {"kind": kind, "sourceWorkflowId": "protein-test", "sourceRevision": 1,
                                      "sourceStageId": "production", "sourceAttemptId": "parent",
                                      "manifestSha256": self.acceptance["manifestSha256"], "modelId": "fixture-model", "minimized": True}
                before = self.sent
                with patch.object(self.controller, "_reference_manifest", return_value=(self.path, self.manifest)):
                    result = self.controller._make_request(value, {"attemptId": "next", "stage": stage})
                self.assertEqual(result[kind]["sourceAttemptId"], "parent")
                self.assertEqual(self.sent - before, self.source.stat().st_size + self.path.stat().st_size)

    def test_aggregate_bundle_is_rejected_before_any_upload(self):
        second = self.output / "checkpoint.chk"
        with second.open("wb") as stream:
            stream.truncate(64 * MIB + 1)
        self.manifest["files"].append({"path": second.name, "role": "checkpoint",
                                      "sha256": sha256_file(second), "byteSize": second.stat().st_size})
        atomic_json(self.path, self.manifest)
        self.acceptance["manifestSha256"] = sha256_file(self.path)
        with self.assertRaisesRegex(BioError, "DEPENDENCY_OUTPUT_BUDGET_EXCEEDED"):
            self.make_request(self.value())
        self.receive.assert_not_called()

    def test_internal_budget_override_still_requires_matching_artifact_hash(self):
        with self.assertRaisesRegex(BioError, "HASH_MISMATCH"):
            self.controller.runtime.upload_file("next", "deps/source/trajectory.dcd", self.source,
                                                "a" * 64, max_bytes=128 * MIB)
        self.receive.assert_not_called()

    def test_automatic_source_budget_uses_finite_operator_target_limit(self):
        self.request["stage"]["resources"].pop("maxOutputBytes")
        self.request["stage"]["resources"]["outputPolicy"] = "auto"
        atomic_json(self.controller._request_path("parent"), self.request)
        self.make_request(self.value())
        self.assertEqual(self.sent, self.source.stat().st_size + self.path.stat().st_size)


if __name__ == "__main__":
    unittest.main()
