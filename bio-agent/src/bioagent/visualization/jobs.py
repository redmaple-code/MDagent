"""Persistent owned preview job records survive bridge restart."""
from contextlib import contextmanager
import fcntl
import os
from ..errors import require, safe_file
from .contracts import identifier


class PreviewJobs:
    def __init__(self, project):
        self.root=safe_file(project,'previews/jobs');self.root.mkdir(parents=True,exist_ok=True,mode=0o700)

    def path(self,job_id):
        require(identifier(job_id) and job_id.startswith('job-'),'INVALID_PREVIEW_JOB')
        return safe_file(self.root,job_id)

    @contextmanager
    def locked(self):
        descriptor=os.open(safe_file(self.root,'manager.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:fcntl.flock(descriptor,fcntl.LOCK_EX);yield
        finally:fcntl.flock(descriptor,fcntl.LOCK_UN);os.close(descriptor)


def public_job(record):
    return {k:record.get(k) for k in ('jobId','state','previewId','manifest','errorCode')}
