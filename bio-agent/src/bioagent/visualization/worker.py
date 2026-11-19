"""Fixed preview helper. Runs on bound compute Python, never submits MD."""
from pathlib import Path
import argparse
import os
import resource
import signal
import sys
import time
from ..controller import Controller
from ..errors import BioError, atomic_json, read_json, require
from ..execution_target import verify_target
from ..schema import load_runner
from .jobs import PreviewJobs
from .sources import resolve_source
from .preview_plan import plan_preview
from .preview_files import write_preview
from .contracts import LIMITS


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--project',required=True,type=Path);parser.add_argument('--job-id',required=True)
    args=parser.parse_args();jobs=PreviewJobs(args.project);path=jobs.path(args.job_id)
    def stopped(signum,frame):raise BioError('PREVIEW_CANCELED' if signum==signal.SIGTERM else 'PREVIEW_WALLTIME_EXCEEDED')
    signal.signal(signal.SIGTERM,stopped);signal.signal(signal.SIGALRM,stopped);signal.alarm(LIMITS['helperSeconds'])
    started=time.monotonic()
    with jobs.locked():
        record=read_json(path/'status.json');require(record['state']=='RUNNING','PREVIEW_JOB_CHANGED')
        identity=load_runner().process_identity(os.getpid());require(identity==record.get('identity'),'PREVIEW_JOB_CHANGED')
    try:
        if (path/'cancel.json').exists():raise BioError('PREVIEW_CANCELED')
        spec=read_json(path/'request.json');controller=Controller(args.project,Path(sys.executable).resolve(),spec['target'])
        verify_target(controller.target,controller.project,controller.python)
        runtime=spec.get('pbcRuntime')
        if spec['request'].get('pbc')=='unwrap':
            from .pbc import pbc_runtime
            require(runtime is not None and pbc_runtime()==runtime,'PBC_RUNTIME_CHANGED')
        source=resolve_source(controller,spec['request']['source']);plan=plan_preview(source,spec['request'],pbc_runtime=runtime)
        manifest=write_preview(source,plan,args.project/'previews/files'/plan['previewId'],
                               staging_root=path/'output',quota_root=args.project/'previews')
        verify_target(controller.target,controller.project,controller.python)
        if (path/'cancel.json').exists():raise BioError('PREVIEW_CANCELED')
        result=dict(state='READY',previewId=manifest['previewId'],manifest=manifest,errorCode=None)
    except Exception as error:
        code=error.code if isinstance(error,BioError) else 'PREVIEW_DERIVATION_FAILED'
        result=dict(state='CANCELED' if code=='PREVIEW_CANCELED' else 'FAILED',previewId=None,manifest=None,errorCode=code)
    finally:signal.alarm(0)
    with jobs.locked():
        current=read_json(path/'status.json');require(current.get('identity')==identity,'PREVIEW_JOB_CHANGED')
        peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1024 if sys.platform!='darwin' else 1)
        atomic_json(path/'status.json',{**current,**result,'finishedAt':time.time(),'elapsedSeconds':time.monotonic()-started,
                                      'peakRssBytes':peak,'cpuThreadsRequested':1,'osLimitsEnforced':False})
    return 0 if result['state']=='READY' else 1
