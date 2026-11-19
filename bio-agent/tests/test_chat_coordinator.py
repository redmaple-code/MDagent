"""Only the acknowledged granted workflow advances, within its original window."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from bioagent.controller import Controller
from bioagent.coordinator import Coordinator
from bioagent.errors import BioError
from bioagent.tool_receipts import RequestReceipts
from tests.support import PYTHON, make_project, protein_protocol, fake_bindings


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.project=Path(self.temp.name).resolve();make_project(self.project)
        self.controller=Controller(self.project,PYTHON)
        value=self.controller.store.plan(protein_protocol(),fake_bindings())
        self.controller.store.approve("protein-test",1,value["specSha256"],"test-only")
        self.coordinator=Coordinator(self.controller);self.coordinator.acquire();self.addCleanup(self.coordinator.close)

    def test_second_host_cannot_steal_the_lock(self):
        second=Coordinator(self.controller)
        with self.assertRaisesRegex(BioError,"PROJECT_HOST_ACTIVE"):second.acquire()

    def test_restart_without_activation_never_advances(self):
        self.coordinator.check_submission("protein-test",1)
        self.coordinator.close()
        second=Coordinator(self.controller);second.acquire();self.addCleanup(second.close)
        before=self.controller.get("protein-test")
        self.assertIsNone(second.reconcile(advance=True))
        self.assertEqual(self.controller.get("protein-test"),before)

    def test_restart_cannot_reset_deadline(self):
        with patch("bioagent.coordinator.time.time",return_value=1000):
            self.coordinator.check_submission("protein-test",1)
        self.coordinator.close()
        second=Coordinator(self.controller);second.acquire();self.addCleanup(second.close)
        with patch("bioagent.coordinator.time.time",return_value=2000):
            with self.assertRaisesRegex(BioError,"COORDINATOR_BUDGET_EXCEEDED"):
                second.check_submission("protein-test",1)

    def test_other_active_workflow_blocks_submission(self):
        p=protein_protocol();p["workflowId"]="other-test"
        v=self.controller.store.plan(p,fake_bindings())
        self.controller.store.approve("other-test",1,v["specSha256"],"test-only")
        self.controller.store.claim_next("other-test",1,fake_bindings())
        with self.assertRaisesRegex(BioError,"PROJECT_WORKFLOW_ACTIVE"):
            self.coordinator.check_submission("protein-test",1)

    def test_switching_workflows_and_restarting_keeps_the_original_deadline(self):
        p=protein_protocol();p["workflowId"]="other-test"
        v=self.controller.store.plan(p,fake_bindings())
        self.controller.store.approve("other-test",1,v["specSha256"],"test-only")
        with patch("bioagent.coordinator.time.time",return_value=1000),patch("bioagent.coordinator.time.monotonic",return_value=100):
            self.coordinator.check_submission("protein-test",1)
        with patch("bioagent.coordinator.time.time",return_value=1010),patch("bioagent.coordinator.time.monotonic",return_value=110):
            self.coordinator.check_submission("other-test",1)
        self.coordinator.close()
        second=Coordinator(self.controller);second.acquire();self.addCleanup(second.close)
        with patch("bioagent.coordinator.time.time",return_value=2000):
            with self.assertRaisesRegex(BioError,"COORDINATOR_BUDGET_EXCEEDED"):
                second.check_submission("protein-test",1)

    def source(self, *, successful=True):
        request={"schemaVersion":"bio.tools.v1","requestId":"offline-submit","tool":"bio_submit",
                 "arguments":{"workflowId":"protein-test","revision":1}}
        receipts=RequestReceipts(self.project);receipts.begin(request)
        result={"ok":True,"result":{"state":"READY"}} if successful else {"ok":False,"error":{"code":"TOOL_RESPONSE_UNKNOWN"}}
        receipts.complete(request["requestId"],result)
        self.coordinator.remember_submission("protein-test",1,request["requestId"])
        return receipts,request["requestId"]

    def test_durable_activation_restores_without_submit_or_new_deadline(self):
        _,request_id=self.source()
        with patch("bioagent.coordinator.time.time",return_value=1000):
            self.coordinator.activate("protein-test",1,request_id)
        self.coordinator.close()
        second=Coordinator(self.controller);second.acquire();self.addCleanup(second.close)
        before=self.controller.get("protein-test")
        with patch("bioagent.coordinator.time.time",return_value=1010),patch.object(self.controller,"tick",return_value=before) as tick:
            restored=second.reconcile(advance=False)
        self.assertEqual(restored["attempts"],[]);self.assertTrue(all(call.kwargs["advance"] is False for call in tick.call_args_list))
        from bioagent.errors import read_json
        self.assertEqual(read_json(second.path)["createdWall"],1000)

    def test_human_recovery_resumes_existing_attempt_without_replaying_submit(self):
        receipts,request_id=self.source(successful=False)
        claim=self.controller.store.claim_next("protein-test",1,fake_bindings())
        observed=self.controller.store.record_event(claim["attemptId"],"fixture-running",{"state":"RUNNING"})
        receipts.acknowledge(request_id)
        with patch.object(self.controller,"tick",return_value=observed),patch.object(self.controller,"submit",side_effect=AssertionError("submit replayed")):
            result=self.coordinator.resume("protein-test",human=True)
        self.assertTrue(result["resumedExistingAttempt"])
        self.assertEqual(len(self.controller.get("protein-test")["attempts"]),1)
        self.assertFalse(receipts.entry(request_id)["response"]["ok"])

    def test_lost_submit_without_existing_attempt_requires_a_separate_submit(self):
        receipts,request_id=self.source(successful=False);receipts.acknowledge(request_id)
        with patch.object(self.controller,"tick",return_value=self.controller.get("protein-test")):
            with self.assertRaisesRegex(BioError,"RECOVERY_NEEDS_EXPLICIT_SUBMIT"):
                self.coordinator.resume("protein-test",human=True)
        self.assertEqual(self.controller.get("protein-test")["attempts"],[])

    def test_an_acknowledged_queued_workflow_cannot_be_replaced_by_another_submit(self):
        _,request_id=self.source();self.coordinator.activate("protein-test",1,request_id)
        protocol=protein_protocol();protocol["workflowId"]="other-test"
        value=self.controller.store.plan(protocol,fake_bindings())
        self.controller.store.approve("other-test",1,value["specSha256"],"fixture")
        with self.assertRaisesRegex(BioError,"PROJECT_WORKFLOW_ACTIVE"):self.coordinator.check_submission("other-test",1)

    def test_stopping_advancement_removes_only_unclaimed_queue_entries(self):
        from bioagent.resource_pool import ResourcePool
        from copy import deepcopy
        target=deepcopy(self.controller.target);target["resourcePolicy"]["quotaRoot"]=str(self.project/"fixture-pool")
        self.controller.resources=ResourcePool(target,self.project)
        _,request_id=self.source();self.coordinator.activate("protein-test",1,request_id)
        lease=self.controller.resources.reserve("protein-test",1,protein_protocol()["stages"][0])
        self.coordinator.stop_advancing()
        from bioagent.errors import read_json
        self.assertNotIn(lease,read_json(self.controller.resources.root/"pool.json")["leases"])


if __name__=="__main__":unittest.main()
