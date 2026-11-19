"""Revision replacement must not orphan an earlier shared-resource waiter."""
import tests
import copy
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from bioagent.controller import Controller
from bioagent.errors import BioError, atomic_json, read_json
from bioagent.execution_target import local_target, target_sha256
from bioagent.resource_pool import ResourcePool
from tests.artifact_fixture import write_result
from tests.support import PYTHON, fake_bindings, make_project, protein_protocol


class ControllerQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bio-controller-queue-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "projects/session"
        make_project(self.project)
        target = local_target(self.project, PYTHON)
        target["resourcePolicy"].update(quotaRoot=str(self.root / "quota"), cpuThreads=1, maxConcurrentJobs=1)
        self.controller = Controller(self.project, PYTHON, target)
        self.second = Controller(self.project, PYTHON, target)
        self.other = ResourcePool(target, self.root / "other-project")
        self.protocol = protein_protocol()
        self.bindings = {**fake_bindings(), "executionTargetSha256": target_sha256(target)}
        probe = patch.object(Controller, "_preflight", side_effect=lambda _: dict(self.bindings))
        probe.start(); self.addCleanup(probe.stop)
        execution = patch("bioagent.runtime_adapter.LocalRuntime.call", side_effect=AssertionError("unexpected scientific execution"))
        execution.start(); self.addCleanup(execution.stop)
        value = self.controller.plan(self.protocol)
        self.controller.store.approve("protein-test", 1, value["specSha256"], "offline-fixture")

    def newer(self):
        protocol = copy.deepcopy(self.protocol)
        protocol["revision"] = 2
        return protocol

    def queue(self):
        blocker = self.other.reserve("occupier", 1, self.protocol["stages"][0])
        self.assertIsNotNone(blocker)
        queued = self.controller.submit("protein-test", 1)
        self.assertTrue(queued["resourceWaiting"])
        self.assertEqual(queued["attempts"], [])
        return blocker

    def test_queued_revision_cannot_be_superseded(self):
        self.queue()
        with self.assertRaisesRegex(BioError, "WORKFLOW_ACTIVE"):
            self.second.plan(self.newer())
        self.assertEqual(self.controller.get("protein-test")["revision"], 1)

    def test_replaying_a_queued_plan_keeps_the_original_queue(self):
        self.queue()
        path = self.root / "quota/pool.json"
        before = path.read_bytes()
        replay = self.second.plan(self.protocol)
        self.assertEqual(replay["state"], "READY")
        self.assertEqual(replay["attempts"], [])
        self.assertEqual(path.read_bytes(), before)

    def test_canceling_the_queued_revision_allows_a_new_plan(self):
        blocker = self.queue()
        self.assertEqual(self.second.cancel("protein-test")["state"], "CANCELED")
        self.assertEqual(self.second.plan(self.newer())["state"], "DRAFT")
        self.other.release(blocker, reason="OFFLINE_FIXTURE_ENDED")
        self.assertIsNotNone(self.other.reserve("unrelated", 1, self.protocol["stages"][0]))
        self.assertFalse(self.controller.resources.waiting("protein-test"))
        self.assertEqual(self.controller.store.get("protein-test", 1)["state"], "CANCELED")

    def test_an_approved_unsubmitted_revision_can_be_replaced(self):
        self.assertEqual(self.second.plan(self.newer())["state"], "DRAFT")
        self.assertFalse(self.controller.resources.root.exists())

    def test_historical_submit_does_not_add_a_waiter(self):
        self.second.plan(self.newer())
        self.other.reserve("occupier", 1, self.protocol["stages"][0])
        path = self.root / "quota/pool.json"
        before = path.read_bytes()
        with self.assertRaisesRegex(BioError, "STALE_REVISION"):
            self.controller.submit("protein-test", 1)
        self.assertEqual(path.read_bytes(), before)

    def test_an_unattached_reservation_blocks_replacement_until_cancel(self):
        self.controller.resources.reserve("protein-test", 1, self.protocol["stages"][0])
        with self.assertRaisesRegex(BioError, "WORKFLOW_ACTIVE"):
            self.second.plan(self.newer())
        self.controller.cancel("protein-test")
        self.assertEqual(self.second.plan(self.newer())["state"], "DRAFT")

    def test_an_unknown_attempt_keeps_its_lease_when_replacement_is_rejected(self):
        lease = self.controller.resources.reserve("protein-test", 1, self.protocol["stages"][0])
        claim = self.controller.store.claim_next("protein-test", 1, self.bindings)
        self.controller.resources.attach(lease, claim["attemptId"])
        self.controller.store.record_event(claim["attemptId"], "offline-unknown", {"state":"UNKNOWN"})
        with self.assertRaisesRegex(BioError, "WORKFLOW_ACTIVE"):
            self.second.plan(self.newer())
        leases = read_json(self.root / "quota/pool.json")["leases"]
        self.assertEqual(leases[lease]["attemptId"], claim["attemptId"])
        self.assertEqual(self.controller.get("protein-test")["attempts"][0]["state"], "UNKNOWN")

    def test_a_foreign_project_waiter_does_not_block_revision_replacement(self):
        self.other.reserve("occupier", 1, self.protocol["stages"][0])
        self.assertIsNone(self.other.reserve("protein-test", 1, self.protocol["stages"][0]))
        self.assertEqual(self.second.plan(self.newer())["state"], "DRAFT")

    def test_a_queued_continuation_cannot_be_superseded(self):
        claim = self.controller.store.claim_next("protein-test", 1, self.bindings)
        attempt = self.controller.runtime.root / claim["attemptId"]
        request, status = write_result(attempt)
        atomic_json(self.controller._request_path(claim["attemptId"]), request)
        self.controller._record_status(claim["attemptId"], status)
        protocol = copy.deepcopy(self.protocol)
        protocol.update(workflowId="continuation-test", inputs=[])
        protocol["stages"] = [protocol["stages"][1]]
        protocol["stages"][0]["dependsOn"] = []
        value = self.controller.continue_plan("protein-test", claim["attemptId"], protocol)
        self.controller.store.approve("continuation-test", 1, value["specSha256"], "offline-fixture")
        self.other.reserve("occupier", 1, self.protocol["stages"][0])
        self.assertTrue(self.controller.submit("continuation-test", 1)["resourceWaiting"])
        protocol["revision"] = 2
        with self.assertRaisesRegex(BioError, "WORKFLOW_ACTIVE"):
            self.second.continue_plan("protein-test", claim["attemptId"], protocol)
        self.assertEqual(self.controller.get("continuation-test")["revision"], 1)

    def test_cancel_can_complete_while_runtime_submission_is_waiting(self):
        submitting, canceled = Event(), Event()

        def runtime(request):
            if request["action"] == "upload":
                return {"offset":request["totalSize"], "complete":True}
            if request["action"] == "submit":
                submitting.set()
                if not canceled.wait(5):
                    raise AssertionError("cancel was blocked by runtime submission")
            elif request["action"] == "cancel":
                canceled.set()
            else:
                raise AssertionError("unexpected runtime request")
            return {"state":"CANCELED", "sequence":1}

        with patch.object(self.controller.runtime, "call", side_effect=runtime), \
                patch.object(self.second.runtime, "call", side_effect=runtime), \
                ThreadPoolExecutor(max_workers=2) as pool:
            result = pool.submit(self.controller.submit, "protein-test", 1)
            self.assertTrue(submitting.wait(5))
            cancellation = pool.submit(self.second.cancel, "protein-test")
            self.assertEqual(cancellation.result(timeout=5)["state"], "CANCELED")
            self.assertEqual(result.result(timeout=5)["state"], "CANCELED")

    def test_replacement_waits_for_an_inflight_reservation_before_checking_the_queue(self):
        self.other.reserve("occupier", 1, self.protocol["stages"][0])
        reserving, proceed, planning = Event(), Event(), Event()
        reserve = self.controller.resources.reserve

        def blocked_reserve(*args):
            reserving.set()
            if not proceed.wait(5):
                raise AssertionError("reservation fixture was not released")
            return reserve(*args)

        def probe(protocol):
            if protocol["revision"] == 2:
                planning.set()
            return self.bindings

        with patch.object(self.controller.resources, "reserve", side_effect=blocked_reserve), \
                patch.object(Controller, "_preflight", side_effect=probe), \
                ThreadPoolExecutor(max_workers=2) as pool:
            submitting = pool.submit(self.controller.submit, "protein-test", 1)
            replacing = None
            try:
                self.assertTrue(reserving.wait(5))
                replacing = pool.submit(self.second.plan, self.newer())
                self.assertTrue(planning.wait(5))
                with self.assertRaises(TimeoutError):
                    replacing.result(timeout=.1)
            finally:
                proceed.set()
            self.assertTrue(submitting.result(timeout=5)["resourceWaiting"])
            with self.assertRaisesRegex(BioError, "WORKFLOW_ACTIVE"):
                replacing.result(timeout=5)
        self.assertEqual(self.controller.get("protein-test")["revision"], 1)
