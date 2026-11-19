from pathlib import Path
import tempfile
import time
import unittest
from uuid import uuid4
from unittest.mock import patch
from bioagent.controller import Controller
from bioagent.errors import atomic_json, read_json, sha256_file
from bioagent.intake import import_input
from bioagent.visualization.service import PreviewService
from bioagent.execution_target import local_target
from tests.support import PYTHON


class PreviewJobsTest(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.project=Path(tmp.name)/'projects/session';self.project.mkdir(parents=True)
        self.target=local_target(self.project,PYTHON)
        self.controller=Controller(self.project,PYTHON,self.target);self.service=PreviewService(self.controller)
        self.request=dict(source=dict(kind='input',sourceId='missing',sourceSha256='a'*64,
                                     workflowId=None,revision=None,stageId=None,attemptId=None),
                          profile='overview',atomSet='overview',startFrame=0,endFrame=0,stride=1)

    def test_prepare_returns_job_without_blocking_cancel(self):
        with patch.object(self.service,'_start',return_value=None):
            start=time.monotonic();r=self.service.prepare(self.request)
            self.assertLess(time.monotonic()-start,.5);self.assertEqual(r['state'],'QUEUED')
            self.assertEqual(self.service.cancel(r['jobId'])['state'],'CANCELED')

    def test_same_preview_key_starts_one_worker(self):
        with patch.object(self.service,'_start',return_value=None):
            first=self.service.prepare(self.request);second=self.service.prepare(self.request)
            self.assertEqual(first['jobId'],second['jobId'])
            self.assertEqual(len(list(self.service.jobs.root.glob('job-*'))),1)

    def test_owned_operations_do_not_share_running_jobs_or_cancel_each_other(self):
        with patch.object(self.service,'_start',return_value=None):
            one,two=str(uuid4()),str(uuid4())
            first=self.service.prepare(self.request,operation_id=one)
            second=self.service.prepare(self.request,operation_id=two)
            self.assertNotEqual(first['jobId'],second['jobId'])
            self.assertEqual(self.service.cancel(operation_id=one)['state'],'CANCELED')
            self.assertEqual(self.service.status(second['jobId'])['state'],'QUEUED')
            self.assertEqual(self.service.prepare(self.request,operation_id=one)['state'],'CANCELED')

    def test_cancel_before_prepare_is_retained_and_ready_artifacts_can_be_shared(self):
        with patch.object(self.service,'_start',return_value=None):
            operation=str(uuid4());self.service.cancel(operation_id=operation)
            self.assertEqual(self.service.prepare(self.request,operation_id=operation)['state'],'CANCELED')
            first=self.service.prepare(self.request,operation_id=str(uuid4()))
            path=self.service.jobs.path(first['jobId']);record=read_json(path/'status.json')
            record.update(state='READY',previewId='preview-test');atomic_json(path/'status.json',record)
            reused=self.service.prepare(self.request,operation_id=str(uuid4()))
            self.assertEqual(reused['jobId'],first['jobId'])
            self.assertEqual(reused['state'],'READY')

    def test_operation_identity_cannot_be_rebound_to_a_different_request(self):
        with patch.object(self.service,'_start',return_value=None):
            operation=str(uuid4());self.service.prepare(self.request,operation_id=operation)
            with self.assertRaisesRegex(Exception,'PREVIEW_OPERATION_CHANGED'):
                self.service.prepare({**self.request,'atomSet':'all'},operation_id=operation)

    def test_processing_runtime_changes_queue_identity_and_explicit_raw_reuses_legacy(self):
        with patch.object(self.service,'_start',return_value=None),patch('bioagent.visualization.pbc.pbc_runtime') as runtime:
            raw=self.service.prepare(self.request)
            self.assertEqual(raw['jobId'],self.service.prepare({**self.request,'pbc':'raw'})['jobId'])
            runtime.return_value={'runtimeSha256':'a'*64}
            first=self.service.prepare({**self.request,'pbc':'unwrap'})
            runtime.return_value={'runtimeSha256':'b'*64}
            changed=self.service.prepare({**self.request,'pbc':'unwrap'})
            self.assertEqual(len({raw['jobId'],first['jobId'],changed['jobId']}),3)
            stored=read_json(self.service.jobs.path(changed['jobId'])/'request.json')
            self.assertEqual(stored['pbcRuntime']['runtimeSha256'],'b'*64)

    def test_canceled_and_failed_attempts_retry_without_overwriting_history(self):
        with patch.object(self.service,'_start',return_value=None):
            first=self.service.prepare(self.request);self.service.cancel(first['jobId'])
            second=self.service.prepare(self.request)
            self.assertNotEqual(first['jobId'],second['jobId']);self.assertEqual(second['state'],'QUEUED')
            path=self.service.jobs.path(second['jobId']);record=read_json(path/'status.json')
            record.update(state='FAILED',errorCode='TEMPORARY_FAILURE');atomic_json(path/'status.json',record)
            third=self.service.prepare(self.request)
            self.assertNotEqual(second['jobId'],third['jobId']);self.assertEqual(third['state'],'QUEUED')
            self.assertEqual(read_json(self.service.jobs.path(first['jobId'])/'status.json')['state'],'CANCELED')
            self.assertEqual(read_json(path/'status.json')['errorCode'],'TEMPORARY_FAILURE')

    def test_terminal_attempt_with_live_worker_cannot_retry_until_owned_worker_exits(self):
        with patch.object(self.service,'_start',return_value=None):
            first=self.service.prepare(self.request);path=self.service.jobs.path(first['jobId']);record=read_json(path/'status.json')
            identity={'pid':123,'start':'owned'};record.update(state='CANCELED',identity=identity);atomic_json(path/'status.json',record)
            with patch('bioagent.visualization.service.load_runner') as runner:
                runner.return_value.process_identity.return_value=identity
                self.assertEqual(self.service.prepare(self.request)['jobId'],first['jobId'])
                runner.return_value.process_identity.return_value=None
                second=self.service.prepare(self.request)
                self.assertNotEqual(second['jobId'],first['jobId']);self.assertEqual(second['state'],'QUEUED')

    def test_unknown_attempt_is_reused_without_duplicate_worker(self):
        with patch.object(self.service,'_start',return_value=None):
            first=self.service.prepare(self.request);path=self.service.jobs.path(first['jobId']);record=read_json(path/'status.json')
            record.update(state='UNKNOWN');atomic_json(path/'status.json',record)
            self.assertEqual(self.service.prepare(self.request)['jobId'],first['jobId'])
            self.assertEqual(len(list(self.service.jobs.root.glob('job-*'))),1)

    def test_temporary_missing_input_failure_retries_to_a_real_ready_preview(self):
        source=self.project.parent/'protein.pdb'
        source.write_text('ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00           C\nEND\n')
        request={**self.request,'source':{**self.request['source'],'sourceId':'protein','sourceSha256':sha256_file(source)}}
        def finished(job):
            deadline=time.monotonic()+8
            while time.monotonic()<deadline:
                status=self.service.status(job['jobId'])
                if status['state'] in {'FAILED','READY','CANCELED'}:
                    process=self.service.processes.get(job['jobId'])
                    if process:process.wait(timeout=2);self.service.status(job['jobId'])
                    return status
                time.sleep(.02)
            self.fail('preview worker did not finish')
        with patch.object(self.controller,'submit',side_effect=AssertionError('MD forbidden')):
            first=self.service.prepare(request);self.assertEqual(finished(first)['state'],'FAILED')
            import_input(self.project,source);second=self.service.prepare(request);ready=finished(second)
            self.assertNotEqual(first['jobId'],second['jobId']);self.assertEqual(ready['state'],'READY',ready)
            self.assertEqual(ready['manifest']['frames'][0]['sourceFrameIndex'],0)
            self.assertTrue(self.service.read(ready['previewId'],'topology.pdb',0,1)['data'])

    def test_unknown_worker_keeps_lease(self):
        with patch.object(self.service,'_start',return_value=None):first=self.service.prepare(self.request)
        path=self.service.jobs.path(first['jobId']);record=read_json(path/'status.json')
        lease=self.controller.resources.reserve_preview(first['jobId'],self.target['tenantId'])
        record.update(state='RUNNING',leaseId=lease,identity=None);atomic_json(path/'status.json',record)
        self.assertEqual(self.service.status(first['jobId'])['state'],'UNKNOWN')
        with self.controller.resources.locked() as pool:self.assertIn(lease,pool['leases'])

    def test_preview_and_science_share_cpu_without_gpu(self):
        self.controller.resources.policy['cpuThreads']=1;self.controller.resources.identity['cpuThreads']=1
        preview=self.controller.resources.reserve_preview('preview-job',self.target['tenantId']);self.assertIsNotNone(preview)
        stage=dict(id='production',resources=dict(cpuThreads=1,platform='CPU'))
        self.assertIsNone(self.controller.resources.reserve('workflow',1,stage))
        with self.controller.resources.locked() as pool:
            self.assertEqual(pool['leases'][preview]['cpuThreads'],1);self.assertIsNone(pool['leases'][preview]['deviceIndex'])
            self.assertEqual(pool['leases'][preview]['jobKind'],'preview')

    def test_job_cannot_start_md(self):
        with patch.object(self.controller,'submit',side_effect=AssertionError('MD forbidden')):
            first=self.service.prepare(self.request)
            deadline=time.monotonic()+8
            while time.monotonic()<deadline:
                reply=self.service.status(first['jobId'])
                if reply['state'] in {'FAILED','READY','CANCELED'}:break
                time.sleep(.05)
            self.assertEqual(reply['state'],'FAILED');self.assertEqual(reply['errorCode'],'PREVIEW_SOURCE_CHANGED')
