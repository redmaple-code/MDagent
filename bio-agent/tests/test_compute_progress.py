"""Progress is bounded metadata, never submission authority or accepted output."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from bioagent.bridge import Bridge
from bioagent.errors import BioError, canonical
from tests.support import PYTHON, fake_bindings, make_project, protein_protocol


class ComputeProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bio-progress-")
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name).resolve()
        make_project(self.project)
        self.bridge = Bridge(self.project, PYTHON)
        self.addCleanup(self.bridge.close)
        self.controller = self.bridge.controller
        self.controller.store.plan(protein_protocol(), fake_bindings())

    def request(self, revision=1, **extra):
        return self.bridge.handle_host({"schemaVersion": "bio.host.v1", "requestId": str(uuid.uuid4()),
            "tool": "progress", "arguments": {"workflowId": "protein-test", "revision": revision, **extra}})

    def running(self):
        value = copy.deepcopy(self.controller.get("protein-test"))
        value["state"] = "RUNNING"
        value["stages"][0]["state"] = value["stages"][1]["state"] = "COMPLETED"
        value["stages"][2]["state"] = "RUNNING"
        value["attempts"] = [{"stageId": "production", "attemptId": "attempt-old", "state": "FAILED"},
                             {"stageId": "production", "attemptId": "attempt-new", "state": "RUNNING"}]
        stage = value["stages"][2]["stage"]
        progress = {"schemaVersion": "ksh.workflow-progress.v1", "workflowId": "protein-test", "revision": 1,
            "stageId": "production", "attemptId": "attempt-new", "workerNonce": "must-not-leak",
            "protocolSha256": hashlib.sha256(canonical(stage)).hexdigest(), "stageStep": 8, "totalSteps": 20,
            "elapsedSeconds": 30.5, "publishedAtEpochMs": 100000, "samples": [{"private": "x" * 4000}] * 96}
        return value, progress

    def write_worker_progress(self, value, progress):
        attempt = self.controller.runtime.root / "attempt-new"
        (attempt / "output").mkdir(parents=True, exist_ok=True)
        stage = value["stages"][2]["stage"]
        request = {"schemaVersion": "ksh.stage-request.v1", "workflowId": "protein-test", "revision": 1,
                   "attemptId": "attempt-new", "stage": stage, "inputs": [], "dependencyBundles": []}
        (attempt / "request.json").write_bytes(canonical(request))
        (attempt / "status.json").write_bytes(canonical({"nonce": "must-not-leak"}))
        (attempt / "output/protocol.json").write_bytes(canonical(stage))
        record = {**progress, "targetTemperatureKelvin": 300, "temperatureDegreesOfFreedom": 93,
                  "samples": [{"stageStep": 8, "timePs": .008, "temperatureKelvin": 300,
                               "potentialEnergyKjMol": -10, "kineticEnergyKjMol": 5, "volumeNm3": 1}]}
        path = attempt / "live-progress.json"
        path.write_bytes(canonical(record))
        return path

    def test_draft_returns_stage_targets_without_launching_or_writing_full_records(self):
        before = sorted(str(p.relative_to(self.project)) for p in self.project.rglob("*"))
        with patch.object(self.controller.runtime, "call", side_effect=AssertionError("runtime launched")):
            reply = self.request()
        self.assertTrue(reply["ok"], reply)
        value = reply["result"]
        self.assertEqual(value["schemaVersion"], "bio.compute-progress.v1")
        self.assertEqual(value["state"], "DRAFT")
        self.assertEqual(value["stages"][2]["totalSteps"], 20)
        self.assertIsNone(value["stages"][2]["progress"])
        self.assertIsNone(value["stages"][1]["totalSteps"])
        self.assertEqual(sorted(str(p.relative_to(self.project)) for p in self.project.rglob("*")), before)

    def test_latest_attempt_is_scoped_and_samples_and_nonce_are_not_transferred(self):
        value, progress = self.running()
        with patch.object(self.controller, "tick", return_value=value) as tick, \
                patch.object(self.controller, "progress", return_value=progress) as read:
            reply = self.request()
        self.assertTrue(reply["ok"], reply)
        tick.assert_called_once_with("protein-test", advance=False)
        read.assert_called_once_with("attempt-new")
        row = reply["result"]["stages"][2]
        self.assertEqual(row["attemptId"], "attempt-new")
        self.assertEqual(row["progress"], {"stageStep": 8, "totalSteps": 20,
            "elapsedSeconds": 30.5, "publishedAtEpochMs": 100000})
        self.assertLess(len(canonical(reply)), 8192)
        self.assertNotIn("must-not-leak", json.dumps(reply))

    def test_revision_and_extra_arguments_are_rejected_before_any_status_refresh(self):
        with patch.object(self.controller, "tick", side_effect=AssertionError("stale refresh")):
            self.assertEqual(self.request(2)["error"]["code"], "STALE_REVISION")
            self.assertEqual(self.request(path="/private/log")["error"]["code"], "INVALID_HOST_ARGUMENTS")

    def test_untrusted_cross_attempt_revision_protocol_or_invalid_steps_are_omitted(self):
        value, progress = self.running()
        for mutation in ({"workflowId": "other"}, {"revision": 2}, {"stageId": "prepare"},
                         {"attemptId": "attempt-old"}, {"protocolSha256": "a" * 64},
                         {"totalSteps": 21}, {"stageStep": -1}, {"stageStep": 21},
                         {"stageStep": True}, {"elapsedSeconds": float("nan")}):
            with self.subTest(mutation=mutation), patch.object(self.controller, "tick", return_value=value), \
                    patch.object(self.controller, "progress", return_value={**progress, **mutation}):
                reply = self.request()
                self.assertTrue(reply["ok"], reply)
                self.assertIsNone(reply["result"]["stages"][2]["progress"])

    def test_out_of_range_worker_timestamps_omit_only_the_observation(self):
        value, progress = self.running()
        self.write_worker_progress(value, progress)
        self.assertEqual(self.controller.progress("attempt-new")["stageStep"], 8)
        for published in (2 ** 53, 10 ** 400):
            with self.subTest(digits=len(str(published))):
                self.write_worker_progress(value, {**progress, "publishedAtEpochMs": published})
                with patch.object(self.controller, "tick", return_value=value):
                    reply = self.request()
                self.assertTrue(reply["ok"], reply)
                self.assertEqual(reply["result"]["state"], "RUNNING")
                self.assertIsNone(reply["result"]["stages"][2]["progress"])

    def test_deeply_nested_worker_json_does_not_hide_workflow_state(self):
        value, progress = self.running()
        path = self.write_worker_progress(value, progress)
        self.assertEqual(self.controller.progress("attempt-new")["stageStep"], 8)
        raw = path.read_text()[:-1] + ',"malformed":' + '[' * 20000 + '0' + ']' * 20000 + '}'
        path.write_text(raw)
        with patch.object(self.controller, "tick", return_value=value):
            reply = self.request()
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["result"]["state"], "RUNNING")
        self.assertIsNone(reply["result"]["stages"][2]["progress"])

    def test_failed_task_preserves_actual_steps_and_does_not_mark_stage_completed(self):
        value, progress = self.running()
        value["state"] = value["stages"][2]["state"] = value["attempts"][-1]["state"] = "FAILED"
        with patch.object(self.controller, "tick", return_value=value), patch.object(self.controller, "progress", return_value=progress):
            reply = self.request()
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["result"]["state"], "FAILED")
        self.assertEqual(reply["result"]["stages"][2]["progress"]["stageStep"], 8)

    def test_queued_stage_respects_dependencies_even_if_protocol_is_not_topologically_sorted(self):
        value = self.controller.get("protein-test")
        value["state"] = "RUNNING"
        value["stages"][0]["state"] = "COMPLETED"
        value["stages"] = [value["stages"][2], value["stages"][0], value["stages"][1], *value["stages"][3:]]
        with patch.object(self.controller, "tick", return_value=value):
            reply = self.request()
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["result"].get("currentStageId"), "minimize")

    def test_accepted_stage_does_not_launch_its_ready_successor_on_progress_read(self):
        value = self.controller.get("protein-test")
        self.controller.store.approve("protein-test", 1, value["specSha256"], "test-fixture")
        claim = self.controller.store.claim_next("protein-test", 1, fake_bindings())
        self.controller.store.record_event(claim["attemptId"], "accepted-fixture",
            {"state": "COMPLETED", "acceptance": {"schemaVersion": "bio.acceptance.v1", "engineering": True}})
        with patch.object(self.controller.runtime, "call", side_effect=AssertionError("runtime launched")):
            reply = self.request()
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["result"]["state"], "RUNNING")
        self.assertEqual(reply["result"]["currentStageId"], "minimize")
        self.assertEqual(len(self.controller.get("protein-test")["attempts"]), 1)

    def test_unreachable_runtime_returns_unknown_process_state_without_submission(self):
        value = self.controller.get("protein-test")
        self.controller.store.approve("protein-test", 1, value["specSha256"], "test-fixture")
        claim = self.controller.store.claim_next("protein-test", 1, fake_bindings())
        with patch.object(self.controller.runtime, "call", side_effect=BioError("TRANSPORT_FAILED")) as runtime:
            reply = self.request()
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["result"]["state"], "WAITING")
        self.assertEqual(reply["result"]["stages"][0]["attemptState"], "UNKNOWN")
        runtime.assert_called_once_with({"action": "status", "attemptId": claim["attemptId"]})

    def test_revision_replaced_during_status_refresh_does_not_return_old_snapshot(self):
        tick = self.controller.tick

        def replaced(workflow_id, *, advance):
            value = tick(workflow_id, advance=advance)
            next_revision = protein_protocol()
            next_revision["revision"] = 2
            self.controller.store.plan(next_revision, fake_bindings())
            return value

        with patch.object(self.controller, "tick", side_effect=replaced):
            reply = self.request()
        self.assertFalse(reply["ok"], reply)
        self.assertEqual(reply["error"]["code"], "STALE_REVISION")
        self.assertEqual(self.controller.get("protein-test")["revision"], 2)


if __name__ == "__main__":
    unittest.main()
