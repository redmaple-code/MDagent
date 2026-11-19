"""Bounded asynchronous host service; only fixed compute-node helpers."""
from __future__ import annotations
import base64
import hashlib
import os
from pathlib import Path
import signal
import subprocess
import time
from uuid import UUID
from ..errors import atomic_json, canonical, read_json, require, safe_file, sha256_file
from ..execution_target import verify_target
from ..schema import load_runner
from .contracts import LIMITS, integer, identifier, validate_preview_request, validate_manifest
from .jobs import PreviewJobs, public_job
from .sources import resolve_source, check_source, file_identity
from .preview_plan import plan_preview
from .preview_files import verify_preview
from .published_reader import PublishedPreviewReader
from .direct_permits import DirectPermitStore
from ..execution_target import target_sha256


class PreviewService:
    def __init__(self, controller):
        self.controller=controller;self.jobs=PreviewJobs(controller.project);self.processes={};self.preview_cache={};self.descriptions={}
        self.reader=PublishedPreviewReader(controller.project,controller.store)
        self.direct=None

    def describe(self,ref):
        key=hashlib.sha256(canonical(ref)).hexdigest()
        if key in self.descriptions:
            source,value=self.descriptions[key];self._source_current(source);return value
        source=resolve_source(self.controller,ref)
        value=dict(source=source['ref'],sourceFrameCount=source['sourceFrameCount'],atomCount=len(source['atoms']),
                    timeStartPs=source['frames'][0]['timePs'],timeEndPs=source['frames'][-1]['timePs'],
                    overviewReason=source['overviewReason'],gaps=source['gaps'][:200],gapCount=len(source['gaps']),
                    boxVectorsAngstrom=source['boxVectorsAngstrom'])
        if len(self.descriptions)>=64:self.descriptions.pop(next(iter(self.descriptions)))
        self.descriptions[key]=({'ref':source['ref'],'fingerprints':source['fingerprints']},value)
        return value

    @staticmethod
    def _operation_job(operation_id):
        try:valid=type(operation_id) is str and str(UUID(operation_id))==operation_id
        except (ValueError,TypeError,AttributeError):valid=False
        require(valid,'INVALID_PREVIEW_OPERATION')
        return 'job-op-'+operation_id.replace('-','')

    def prepare(self,request,operation_id=None):
        validate_preview_request(request)
        owned_job=self._operation_job(operation_id) if operation_id is not None else None
        pbc_runtime=None
        if request.get('pbc')=='unwrap':
            from .pbc import pbc_runtime as inspect_runtime
            pbc_runtime=inspect_runtime()
        elif 'pbc' in request:request={k:v for k,v in request.items() if k!='pbc'}
        implementation={p.name:sha256_file(p) for p in Path(__file__).parent.glob('*.py')}
        identity=[request,implementation]+([pbc_runtime] if pbc_runtime else [])
        request_id='job-'+hashlib.sha256(canonical(identity)).hexdigest()
        with self.jobs.locked():
            if owned_job:
                path=self.jobs.path(owned_job)
                if path.exists():
                    record=read_json(path/'status.json')
                    require(record.get('requestId') in {None,request_id},'PREVIEW_OPERATION_CHANGED')
                    return public_job(record)
                # Only completed immutable artifacts are shared between independently cancelable views.
                for candidate in self.jobs.root.glob('job-*'):
                    record=read_json(candidate/'status.json')
                    if record.get('requestId')==request_id and record['state']=='READY':return public_job(record)
            attempts=sorted([p for p in self.jobs.root.glob(request_id+'*') if p.name==request_id or p.name.startswith(request_id+'-attempt-')])
            if owned_job:attempts=[]
            path=self.jobs.path(owned_job) if owned_job else (attempts[-1] if attempts else self.jobs.path(request_id))
            retry=False
            if attempts:
                record=read_json(path/'status.json');identity=record.get('identity')
                # A terminal record can precede process exit. Preserve UNKNOWN and live workers.
                retry=(record['state'] in {'CANCELED','FAILED'} and
                       (not identity or load_runner().process_identity(identity['pid'])!=identity))
                if retry:path=self.jobs.path(request_id+f'-attempt-{len(attempts)+1:06d}')
            job_id=path.name
            if not attempts or retry:
                require(len(list(self.jobs.root.glob('job-*')))<256,'PREVIEW_JOB_LIMIT')
                path.mkdir(mode=0o700)
                atomic_json(path/'request.json',dict(request=request,target=self.controller.target,**({'pbcRuntime':pbc_runtime} if pbc_runtime else {})))
                atomic_json(path/'status.json',dict(jobId=job_id,requestId=request_id,attempt=len(attempts)+1,state='QUEUED',previewId=None,manifest=None,errorCode=None,identity=None,queuedAt=time.time()))
        self._start(job_id);return public_job(read_json(path/'status.json'))

    def _start(self,job_id):
        path=self.jobs.path(job_id)
        with self.jobs.locked():
            record=read_json(path/'status.json')
            if record['state']!='QUEUED':return
            verify_target(self.controller.target,self.controller.project,self.controller.python)
            lease=self.controller.resources.reserve_preview(job_id,self.controller.target['tenantId'])
            if lease is None:return
            record.update(state='RUNNING',leaseId=lease,startedAt=time.time())
            # Persist the claim before spawn; a crash is UNKNOWN, never another spawn.
            atomic_json(path/'status.json',record)
            helper=Path(__file__).resolve().parents[3]/'run_biopreview.py'
            env={k:os.environ[k] for k in ('PATH','HOME','TMPDIR','LANG','LC_ALL') if k in os.environ}
            env.update(OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',CUDA_VISIBLE_DEVICES='',PYTHONDONTWRITEBYTECODE='1')
            if 'BIO_PREVIEW_PYMOL_PYTHON' in os.environ:env['BIO_PREVIEW_PYMOL_PYTHON']=os.environ['BIO_PREVIEW_PYMOL_PYTHON']
            try:
                with (path/'worker.log').open('xb') as log:
                    process=subprocess.Popen([str(self.controller.python),'-B',str(helper),'--project',str(self.controller.project),
                                              '--job-id',job_id],stdin=subprocess.DEVNULL,stdout=log,stderr=log,
                                             env=env,shell=False,start_new_session=True)
                record['identity']=load_runner().process_identity(process.pid)
                require(record['identity'] is not None,'PROCESS_IDENTITY_UNAVAILABLE')
                self.processes[job_id]=process;atomic_json(path/'status.json',record)
            except Exception:
                # No conclusive terminal identity means keep the lease.
                record.update(state='UNKNOWN',errorCode='PREVIEW_WORKER_START_UNKNOWN');atomic_json(path/'status.json',record)

    def status(self,job_id):
        path=self.jobs.path(job_id);require(path.exists(),'PREVIEW_JOB_NOT_FOUND')
        process=self.processes.get(job_id)
        if process and process.poll() is not None:self.processes.pop(job_id,None)
        self._start(job_id)
        with self.jobs.locked():
            record=read_json(path/'status.json')
            if record['state']=='RUNNING' and (not record.get('identity') or load_runner().process_identity(record['identity']['pid'])!=record['identity']):
                record.update(state='UNKNOWN',errorCode='PREVIEW_WORKER_UNKNOWN');atomic_json(path/'status.json',record)
            return public_job(record)

    def cancel(self,job_id=None,operation_id=None):
        require((job_id is None)!=(operation_id is None),'INVALID_PREVIEW_OPERATION')
        if operation_id is not None:job_id=self._operation_job(operation_id)
        path=self.jobs.path(job_id)
        with self.jobs.locked():
            if operation_id is not None and not path.exists():
                # Persist cancellation even if prepare is still in transit. Never start this operation later.
                require(len(list(self.jobs.root.glob('job-*')))<256,'PREVIEW_JOB_LIMIT')
                path.mkdir(mode=0o700)
                record=dict(jobId=job_id,requestId=None,state='CANCELED',previewId=None,manifest=None,errorCode=None,identity=None)
                atomic_json(path/'status.json',record)
                return public_job(record)
            record=read_json(path/'status.json')
            if record['state']=='QUEUED':
                self.controller.resources.cancel_preview_waiting(job_id)
                record.update(state='CANCELED',errorCode=None);atomic_json(path/'status.json',record)
            elif record['state'] in {'RUNNING','UNKNOWN'}:
                identity=record.get('identity')
                if identity and load_runner().process_identity(identity['pid'])==identity:
                    atomic_json(path/'cancel.json',dict(jobId=job_id,at=time.time()))
                    os.kill(identity['pid'],signal.SIGTERM)
                else:
                    record.update(state='UNKNOWN',errorCode='PREVIEW_WORKER_UNKNOWN');atomic_json(path/'status.json',record)
        return public_job(record)

    def _preview(self,preview_id):
        return self.reader.resolve(preview_id)

    def _source_current(self,source):
        self.reader.source_current(source)

    def window(self,preview_id,start,end,stride,operation_id=None):
        _,source,_,request=self._preview(preview_id)
        return self.prepare({**request,'profile':'detail','startFrame':start,'endFrame':end,'stride':stride},operation_id=operation_id)

    def read(self,preview_id,file_id,offset,limit):
        value=self.reader.read_bytes(preview_id,file_id,offset,limit)
        return {**value,'data':base64.b64encode(value['data']).decode()}

    def _direct_store(self):
        if self.direct is None:self.direct=DirectPermitStore(self.controller.project,reader=self.reader)
        return self.direct

    def direct_open(self,binding):
        verify_target(self.controller.target,self.controller.project,self.controller.python)
        require(target_sha256(binding['target'])==target_sha256(self.controller.target),'DIRECT_BINDING_CHANGED')
        return self._direct_store().open_channel(binding)

    def direct_permit(self,channel_id,file_id,offset,limit,actor):
        store=self._direct_store();store.check_actor(channel_id,actor)
        return store.issue(channel_id,file_id,offset,limit)

    def direct_close(self,channel_id,actor):
        store=self._direct_store();store.check_actor(channel_id,actor)
        return store.close(channel_id)

    def tick(self):
        for path in self.jobs.root.glob('job-*'):
            if read_json(path/'status.json')['state']=='QUEUED':self._start(path.name)
