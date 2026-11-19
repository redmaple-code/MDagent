"""Control-plane engineering only: no SSH, package probe, Context or MD."""
import tests
import base64
import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import platform
import socket
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch
from bioagent.bridge import Bridge
from bioagent.controller import Controller
from bioagent.errors import BioError, atomic_json, read_json
from bioagent.execution_target import bind_project, check_resources, local_target, target_sha256, verify_target
from bioagent.hash_cache import FileHashCache
from bioagent.gateway import command as gateway_command
from bioagent.intent import compile_intent
from bioagent.resource_pool import ResourcePool
from bioagent.preflight import cuda_environment
from bioagent.transfers import begin_upload, upload_chunk, finish_upload, begin_download, download_chunk, finish_download
from tests.support import PYTHON, ROOT, fake_bindings, protein_protocol


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bio-engineering-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "projects" / ("a" * 64)
        self.project.mkdir(parents=True)
        self.target = local_target(self.project, PYTHON)


class TargetTests(Fixture):
    def test_host_uid_runtime_and_project_mismatch_are_rejected(self):
        for key, value, code in (("hostIdentitySha256", "f"*64, "COMPUTE_IDENTITY_MISMATCH"),
                ("uid", os.getuid()+1, "COMPUTE_IDENTITY_MISMATCH"), ("python", "/wrong/python", "COMPUTE_RUNTIME_MISMATCH"),
                ("projectsRoot", "/wrong/projects", "COMPUTE_PROJECT_MISMATCH")):
            target = {**self.target, key: value}
            with self.subTest(key=key), self.assertRaisesRegex(BioError, code): verify_target(target, self.project, PYTHON)

    def test_project_binding_is_atomic_and_immutable(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            values = list(pool.map(lambda _: bind_project(self.target, self.project, PYTHON), range(2)))
        self.assertEqual(values[0], values[1])
        self.assertEqual((self.project/"execution-target.json").stat().st_mode & 0o777, 0o600)
        changed = {**self.target, "targetRevision": 2}
        with self.assertRaisesRegex(BioError, "PROJECT_TARGET_CHANGED"): bind_project(changed, self.project, PYTHON)

    def test_legacy_and_changed_target_grants_cannot_claim(self):
        controller = Controller(self.project, PYTHON, self.target)
        legacy = controller.store.plan(protein_protocol(), fake_bindings())
        controller.store.approve("protein-test", 1, legacy["specSha256"], "fixture")
        bound = {**fake_bindings(), "executionTargetSha256": target_sha256(self.target)}
        with self.assertRaisesRegex(BioError, "GRANT_CHANGED"):
            controller.store.claim_next("protein-test", 1, bound)
        self.assertEqual(controller.get("protein-test")["attempts"], [])
        next_protocol = protein_protocol(); next_protocol["revision"] = 2
        value = controller.store.plan(next_protocol, bound)
        controller.store.approve("protein-test", 2, value["specSha256"], "fixture")
        with self.assertRaisesRegex(BioError, "GRANT_CHANGED"):
            controller.store.claim_next("protein-test", 2, {**bound, "executionTargetSha256":"e"*64})

    def test_intent_uses_authorized_cuda_only_for_supported_stages(self):
        intent = read_json(ROOT/"validation/chat-cases/intents/water.json")
        intent["resources"]["execution"] = {"platform":"CUDA", "deviceIndex":2, "precision":"mixed"}
        protocol = compile_intent(self.project, intent)
        self.assertEqual({s["kind"]:s["resources"]["platform"] for s in protocol["stages"]},
                         {"prepare":"CPU", "minimize":"CPU", "production":"CUDA", "validate":"CUDA"})
        minimization = next(s for s in protocol["stages"] if s["kind"] == "minimize")
        self.assertNotIn("deviceIndex", minimization["resources"])
        self.assertNotIn("precision", minimization["resources"])
        with self.assertRaisesRegex(BioError, "GPU_NOT_AUTHORIZED"): check_resources(self.target, protocol)
        self.target["resourcePolicy"]["gpuDevices"] = [2];check_resources(self.target, protocol)

    def test_gateway_uses_fixed_ssh_argv_without_reading_credentials(self):
        config=self.root/"ssh-config";hosts=self.root/"known-hosts"
        config.write_text("Host fixture\n  HostName fixture.invalid\n");config.chmod(0o600)
        hosts.write_text("fixture public-host-key-fixture\n");hosts.chmod(0o600)
        connection={"schemaVersion":"bio.ssh-connection.v1","hostAlias":"fixture","configFile":str(config),"knownHosts":str(hosts),
                    **{key:self.target[key] for key in ("python","projectsRoot","releaseRoot")}}
        argv=gateway_command(connection,self.project,self.target)
        self.assertEqual(argv[0],"/usr/bin/ssh");self.assertIn("BatchMode=yes",argv);self.assertIn("StrictHostKeyChecking=yes",argv)
        self.assertIn("--execution-target",argv[-1]);self.assertIn("run_biobridge.py",argv[-1])
        hosts.chmod(0o666)
        with self.assertRaisesRegex(BioError,"GATEWAY_AUTH_FILE_INVALID"):gateway_command(connection,self.project,self.target)

    def test_cuda_checkpoint_binds_device_precision_and_hardware_without_simulation(self):
        controller=Controller(self.project,PYTHON,self.target)
        from bioagent.schema import load_runner
        load_runner()
        stage=protein_protocol()["stages"][2];stage["resources"].update(platform="CUDA",deviceIndex=2,precision="mixed")
        physics={key:stage["parameters"]["dynamics"][key] for key in ("ensemble","temperatureKelvin","timestepFs","frictionPerPs","seed")}
        hardware={"boot":"fixture-boot","kernel":platform.release(),"cuda":"fixture-uuid,driver,name"}
        compatibility={"platform":"CUDA","openmmVersion":"fixture","system":"Linux","machine":platform.machine(),
                       "host":socket.gethostname(),"hardware":hardware,"requestedProperties":{"DeviceIndex":"2","Precision":"mixed"},"physics":physics}
        with patch("bioagent.controller.platform.system",return_value="Linux"),patch("openmm_workflow.stages._hardware_identity",return_value=hardware):
            controller._check_checkpoint_compatibility(compatibility,stage,{"openmmFullVersion":"fixture"})
            changed={**compatibility,"requestedProperties":{"DeviceIndex":"1","Precision":"mixed"}}
            with self.assertRaisesRegex(BioError,"CHECKPOINT_INCOMPATIBLE"):controller._check_checkpoint_compatibility(changed,stage,{"openmmFullVersion":"fixture"})

    def test_cuda_environment_uses_read_only_hardware_identity_and_fails_closed(self):
        protocol=protein_protocol()
        self.assertEqual(cuda_environment(protocol),([],[]))
        protocol["stages"][2]["resources"].update(platform="CUDA",deviceIndex=0,precision="mixed")
        from bioagent.schema import load_runner
        load_runner()
        with patch("bioagent.preflight.platform.system",return_value="Linux"),patch("openmm_workflow.stages._hardware_identity",return_value={"cuda":"fixture-gpu,driver"}) as probe:
            records,blockers=cuda_environment(protocol);self.assertEqual(blockers,[]);self.assertEqual(records[0]["deviceIndex"],0)
            self.assertEqual(probe.call_count,1)
        with patch("bioagent.preflight.platform.system",return_value="Linux"),patch("openmm_workflow.stages._hardware_identity",side_effect=OSError("fixture unavailable")):
            self.assertEqual(cuda_environment(protocol),([],["CUDA_HARDWARE_IDENTITY_UNAVAILABLE"]))


class ReviewTests(Fixture):
    def setUp(self):
        super().setUp()
        self.bridge = Bridge(self.project, PYTHON, self.target); self.addCleanup(self.bridge.close)
        self.bindings = {**fake_bindings(), "executionTargetSha256":target_sha256(self.target)}
        self.bridge.controller.store.plan(protein_protocol(), self.bindings)

    def call(self, action, arguments):
        return self.bridge.handle_host({"schemaVersion":"bio.host.v1", "requestId":str(uuid.uuid4()),
                                       "tool":action, "arguments":arguments})

    def test_review_then_host_approval_never_submits(self):
        args = {"workflowId":"protein-test", "revision":1}
        with patch.object(self.bridge.controller, "bindings_for", return_value=self.bindings):
            reviewed = self.call("review", args);self.assertTrue(reviewed["ok"])
            self.assertIn(self.target["targetId"], reviewed["result"]["text"])
            self.assertIn("模拟时间 0.02 ps", reviewed["result"]["summaryText"])
            self.assertIn("计算时间上限", reviewed["result"]["summaryText"])
            approved = self.call("approve", {**args,"reviewSha256":reviewed["result"]["reviewSha256"],"actor":"web:fixture"})
            self.assertTrue(approved["ok"]);self.assertEqual(approved["result"]["state"], "READY")
        self.assertEqual(self.bridge.controller.get("protein-test")["attempts"], [])
        self.assertFalse(self.bridge.controller.runtime.root.exists())

    def test_changed_environment_or_review_is_rejected(self):
        args = {"workflowId":"protein-test", "revision":1}
        with patch.object(self.bridge.controller, "bindings_for", return_value=self.bindings):
            reply = self.call("approve", {**args,"reviewSha256":"f"*64,"actor":"web:fixture"})
            self.assertEqual(reply["error"]["code"], "REVIEW_CHANGED")
        with patch.object(self.bridge.controller, "bindings_for", return_value={**self.bindings,"environmentSha256":"d"*64}):
            reply = self.call("review", args);self.assertEqual(reply["error"]["code"], "GRANT_CHANGED")
        self.assertIsNone(self.bridge.controller.get("protein-test")["grant"])

    def test_model_cannot_use_human_approval(self):
        reply=self.bridge.handle({"schemaVersion":"bio.tools.v1","requestId":str(uuid.uuid4()),"tool":"bio_approve","arguments":{}})
        self.assertFalse(reply["ok"]);self.assertIsNone(self.bridge.controller.get("protein-test")["grant"])


class ResourceTests(Fixture):
    def setUp(self):
        super().setUp()
        self.target["resourcePolicy"].update(quotaRoot=str(self.root/"pool"),cpuThreads=4,maxConcurrentJobs=2,gpuDevices=[0])
        self.pool = ResourcePool(self.target, self.project)
        self.other = ResourcePool(self.target, self.root/"other")
        self.stage = {"id":"production", "resources":{"cpuThreads":2,"platform":"CPU"}}

    def test_cpu_quota_and_fifo_waiting(self):
        first=self.pool.reserve("first",1,self.stage);second=self.other.reserve("second",1,self.stage)
        self.assertIsNotNone(first);self.assertIsNotNone(second)
        self.assertIsNone(self.pool.reserve("third",1,self.stage));self.assertTrue(self.pool.waiting("third"))
        self.pool.release(first,reason="FIXTURE_END")
        self.assertIsNotNone(self.pool.reserve("third",1,self.stage));self.assertFalse(self.pool.waiting("third"))

    def test_gpu_is_exclusive_and_unknown_attempt_keeps_lease(self):
        gpu={"id":"production","resources":{"cpuThreads":1,"platform":"CUDA","deviceIndex":0}}
        first=self.pool.reserve("first",1,gpu);self.pool.attach(first,"missing-worker")
        self.assertIsNone(self.other.reserve("second",1,gpu))
        self.pool.cancel_waiting("first")
        self.assertIsNone(self.other.reserve("second",1,gpu))

    def test_live_terminal_worker_is_retained_then_released(self):
        first=self.pool.reserve("first",1,self.stage);self.pool.attach(first,"worker")
        path=self.project/"runtime-attempts/worker/status.json"
        identity={"pid":123,"birth":"fixture"};atomic_json(path,{"state":"COMPLETED","identity":identity})
        with patch("bioagent.schema.load_runner",return_value=SimpleNamespace(process_identity=lambda _:identity)):
            self.pool.waiting("other");self.assertIn(first,read_json(self.pool.root/"pool.json")["leases"])
        with patch("bioagent.schema.load_runner",return_value=SimpleNamespace(process_identity=lambda _:None)):
            self.pool.waiting("other");self.assertNotIn(first,read_json(self.pool.root/"pool.json")["leases"])
        self.assertTrue((self.pool.root/"leases.jsonl").exists())

    def test_crash_between_claim_and_attach_is_reconciled(self):
        key=self.pool.reserve("first",1,self.stage)
        self.pool.sync_claims({"workflowId":"first","revision":1,"attempts":[{"stageId":"production","attemptId":"claimed"}]})
        self.pool.cancel_waiting("first")
        self.assertEqual(read_json(self.pool.root/"pool.json")["leases"][key]["attemptId"],"claimed")

    def test_cancel_unclaimed_reservation_and_changed_policy(self):
        key=self.pool.reserve("first",1,self.stage);self.pool.cancel_waiting("first")
        self.assertNotIn(key,read_json(self.pool.root/"pool.json")["leases"])
        changed=copy.deepcopy(self.target);changed["resourcePolicy"]["cpuThreads"]=3
        with self.assertRaisesRegex(BioError,"RESOURCE_POOL_POLICY_CHANGED"):ResourcePool(changed,self.project).reserve("new",1,self.stage)


class CacheTests(Fixture):
    def test_unchanged_hash_is_reused_and_same_size_edit_invalidates(self):
        path=self.root/"environment.bin";path.write_bytes(b"one");cache_path=self.root/"cache.json"
        cache=FileHashCache(cache_path);old=cache.digest(path);cache.save()
        with patch("bioagent.hash_cache.sha256_file",side_effect=AssertionError("unchanged file rehashed")):
            self.assertEqual(FileHashCache(cache_path).digest(path),old)
        timestamp=path.stat().st_mtime_ns;path.write_bytes(b"two");os.utime(path,ns=(timestamp,timestamp))
        self.assertNotEqual(FileHashCache(cache_path).digest(path),old)

    def test_cache_metadata_is_private_and_symlink_is_rejected(self):
        path=self.root/"file";path.write_bytes(b"data");cache=FileHashCache(self.root/"cache.json")
        cache.digest(path);cache.save();self.assertEqual(cache.path.stat().st_mode&0o777,0o600)
        link=self.root/"link";link.symlink_to(cache.path)
        with self.assertRaisesRegex(BioError,"HASH_CACHE_PATH_INVALID"):FileHashCache(link)


class TransferTests(Fixture):
    def test_chunk_upload_is_idempotent_and_source_preserving(self):
        data=b"HEADER fixture\n"*6000;digest=hashlib.sha256(data).hexdigest()
        started=begin_upload(self.project,"fixture.pdb",len(data),digest);uid=started["uploadId"]
        first=base64.b64encode(data[:65536]).decode();upload_chunk(self.project,uid,0,first);upload_chunk(self.project,uid,0,first)
        with self.assertRaisesRegex(BioError,"UPLOAD_REPLAY_CONFLICT"):upload_chunk(self.project,uid,0,base64.b64encode(b"changed").decode())
        upload_chunk(self.project,uid,65536,base64.b64encode(data[65536:]).decode());record=finish_upload(self.project,uid)
        self.assertEqual(record["sha256"],digest);self.assertEqual((self.project/"inputs/fixture.pdb").read_bytes(),data)
        self.assertEqual((self.project/"chat/uploads"/uid/"fixture.pdb").read_bytes(),data)

    def test_traversal_bad_offset_and_bad_hash_are_rejected(self):
        with self.assertRaisesRegex(BioError,"STRUCTURE_FILENAME_INVALID"):begin_upload(self.project,"../escape.pdb",1,"a"*64)
        started=begin_upload(self.project,"bad.pdb",3,"f"*64);uid=started["uploadId"]
        with self.assertRaisesRegex(BioError,"UPLOAD_OFFSET_MISMATCH"):upload_chunk(self.project,uid,1,base64.b64encode(b"a").decode())
        upload_chunk(self.project,uid,0,base64.b64encode(b"abc").decode())
        with self.assertRaisesRegex(BioError,"UPLOAD_HASH_MISMATCH"):finish_upload(self.project,uid)
        self.assertFalse((self.project/"inputs/bad.pdb").exists())

    def test_download_verifies_chunks_and_detects_changed_files(self):
        data=b"trajectory fixture"*8000;path=self.project/"runtime-attempts/a1/output/trajectory.dcd"
        path.parent.mkdir(parents=True);path.write_bytes(data);digest=hashlib.sha256(data).hexdigest()
        def artifacts(*_):
            return {"items":[{"path":path.name,"absolutePath":str(path),"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"byteSize":path.stat().st_size}],"nextOffset":None}
        controller=SimpleNamespace(project=self.project,runtime=SimpleNamespace(root=self.project/"runtime-attempts"),artifacts=artifacts,
            get=lambda _:{"attempts":[{"attemptId":"a1","stageId":"production","state":"COMPLETED","acceptance":{}}]})
        controller.get=lambda _:{"attempts":[{"attemptId":"a1","stageId":"production","state":"COMPLETED","acceptance":{"verified":True}}]}
        ticket=begin_download(controller,"workflow","production",path.name,digest)
        result=bytearray();offset=0
        while offset<len(data):
            part=download_chunk(controller,ticket["downloadId"],offset,65536);chunk=base64.b64decode(part["data"])
            self.assertEqual(hashlib.sha256(chunk).hexdigest(),part["chunkSha256"]);result.extend(chunk);offset=part["nextOffset"]
        self.assertEqual(bytes(result),data);self.assertTrue(finish_download(controller,ticket["downloadId"])["verified"])
        path.write_bytes(b"different")
        with self.assertRaisesRegex(BioError,"ARTIFACT_CHANGED"):download_chunk(controller,ticket["downloadId"],0,1)
        with self.assertRaisesRegex(BioError,"ARTIFACT_CHANGED"):finish_download(controller,ticket["downloadId"])


if __name__ == "__main__": unittest.main()
