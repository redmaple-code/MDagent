"""Compute-side shared CPU/GPU reservations; uncertain jobs keep their slots."""
from __future__ import annotations
from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import time
from .errors import BioError, atomic_json, canonical, read_json, require, safe_file


class ResourcePool:
    def __init__(self, target: dict, project: Path):
        self.policy = target["resourcePolicy"]
        self.root = Path(self.policy["quotaRoot"])
        self.project = project.resolve()
        self.identity = {"hostIdentitySha256": target["hostIdentitySha256"], "uid": target["uid"],
                         **{k: self.policy[k] for k in ("cpuThreads", "maxConcurrentJobs", "gpuDevices")}}

    @contextmanager
    def locked(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        require(not self.root.is_symlink() and self.root.resolve() == self.root.absolute()
                and self.root.stat().st_uid == os.getuid() and self.root.stat().st_mode & 0o077 == 0, "RESOURCE_POOL_INVALID")
        descriptor = os.open(safe_file(self.root, "pool.lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            path = safe_file(self.root, "pool.json")
            value = read_json(path) if path.exists() else {"identity": self.identity, "leases": {}, "waiting": {}}
            require(value.get("identity") == self.identity and type(value.get("leases")) is dict
                    and type(value.get("waiting")) is dict, "RESOURCE_POOL_POLICY_CHANGED")
            self._collect(value)
            yield value
            atomic_json(path, value)
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN); os.close(descriptor)

    def key(self, workflow_id: str, revision: int, stage_id: str) -> str:
        return hashlib.sha256(canonical([str(self.project), workflow_id, revision, stage_id])).hexdigest()

    def _audit(self, event: dict) -> None:
        descriptor = os.open(safe_file(self.root, "leases.jsonl"), os.O_CREAT | os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "ab") as stream:
            stream.write(canonical(event) + b"\n"); stream.flush(); os.fsync(stream.fileno())

    def sync_claims(self, workflow: dict) -> None:
        if not self.root.exists(): return
        with self.locked() as value:
            for lease in value["leases"].values():
                if (lease.get("jobKind") == "preview" or lease["project"] != str(self.project) or lease["workflowId"] != workflow["workflowId"]
                        or lease["revision"] != workflow["revision"] or lease.get("attemptId")):
                    continue
                matches = [a for a in workflow["attempts"] if a["stageId"] == lease["stageId"]]
                require(len(matches) <= 1, "RESOURCE_LEASE_CHANGED")
                if matches: lease["attemptId"] = matches[0]["attemptId"]

    def _collect(self, value: dict) -> None:
        # Only a terminal, no-longer-live exact worker releases a foreign lease.
        from .schema import load_runner
        runner = None
        for key, lease in list(value["leases"].items()):
            if lease.get("jobKind") == "preview":
                try:
                    path=safe_file(Path(lease['project']), 'previews/jobs/'+lease['jobId']+'/status.json')
                    if not path.exists():continue
                    record=read_json(path)
                    if record.get('state') not in {'READY','FAILED','CANCELED'} or not record.get('identity'):continue
                    runner=runner or load_runner()
                    if runner.process_identity(record['identity']['pid'])==record['identity']:continue
                    self._audit({'leaseId':key,'jobId':lease['jobId'],'state':record['state'],'at':time.time()})
                    del value['leases'][key]
                except (OSError, KeyError, TypeError, BioError):pass
                continue
            if not lease.get("attemptId"):
                continue
            try:
                project = Path(lease["project"])
                status = safe_file(project, "runtime-attempts/" + lease["attemptId"] + "/status.json")
                if not status.exists(): continue
                record = read_json(status)
                if record.get("state") not in {"COMPLETED", "FAILED", "CANCELED"}: continue
                runner = runner or load_runner()
                if any(record.get(k) and runner.process_identity(record[k]["pid"]) == record[k]
                       for k in ("identity", "stageIdentity", "watchdogIdentity")):
                    continue
                self._audit({"leaseId": key, "attemptId": lease["attemptId"], "state": record["state"], "at": time.time()})
                del value["leases"][key]
            except (OSError, KeyError, TypeError):
                continue

    def reserve(self, workflow_id: str, revision: int, stage: dict) -> str | None:
        key = self.key(workflow_id, revision, stage["id"])
        resources = stage["resources"]
        require(type(resources["cpuThreads"]) is int and 1 <= resources["cpuThreads"] <= self.policy["cpuThreads"], "RESOURCE_POLICY_EXCEEDED")
        if resources.get("platform") == "CUDA":
            require(type(resources.get("deviceIndex")) is int and resources["deviceIndex"] in self.policy["gpuDevices"], "GPU_NOT_AUTHORIZED")
        with self.locked() as value:
            if key in value["leases"]: return key
            require(len(value["waiting"]) < 256 or key in value["waiting"], "RESOURCE_QUEUE_FULL")
            entry = {"project": str(self.project), "workflowId": workflow_id, "revision": revision,
                     "stageId": stage["id"], "cpuThreads": resources["cpuThreads"],
                     "deviceIndex": resources.get("deviceIndex")}
            if key not in value["waiting"]: value["waiting"][key] = {**entry, "queuedAt": time.time()}
            oldest = min(value["waiting"], key=lambda item: (value["waiting"][item]["queuedAt"], item))
            leases = list(value["leases"].values())
            busy_gpu = {item["deviceIndex"] for item in leases if item.get("deviceIndex") is not None}
            if (key != oldest or len(leases) >= self.policy["maxConcurrentJobs"]
                    or sum(item["cpuThreads"] for item in leases) + resources["cpuThreads"] > self.policy["cpuThreads"]
                    or entry["deviceIndex"] is not None and entry["deviceIndex"] in busy_gpu):
                return None
            value["leases"][key] = {**entry, "reservedAt": time.time(), "attemptId": None}
            del value["waiting"][key]
            return key

    def attach(self, lease_id: str, attempt_id: str) -> None:
        with self.locked() as value:
            lease = value["leases"].get(lease_id)
            require(lease is not None and lease["project"] == str(self.project)
                    and lease.get("attemptId") in {None, attempt_id}, "RESOURCE_LEASE_CHANGED")
            lease["attemptId"] = attempt_id

    def reserve_preview(self, job_id: str, tenant_id: str) -> str | None:
        """Preview uses the same FIFO/CPU/job pool, plus one helper per tenant."""
        from .schema import valid_id
        require(valid_id(job_id) and isinstance(tenant_id,str), 'INVALID_PREVIEW_JOB')
        key=hashlib.sha256(canonical([str(self.project),'preview',job_id])).hexdigest()
        with self.locked() as value:
            if key in value['leases']:return key
            require(len(value['waiting'])<256 or key in value['waiting'],'RESOURCE_QUEUE_FULL')
            entry=dict(project=str(self.project),jobKind='preview',jobId=job_id,tenantId=tenant_id,cpuThreads=1,deviceIndex=None)
            if key not in value['waiting']:value['waiting'][key]={**entry,'queuedAt':time.time()}
            leases=list(value['leases'].values())
            oldest=min(value['waiting'],key=lambda k:(value['waiting'][k]['queuedAt'],k))
            if (key!=oldest or len(leases)>=self.policy['maxConcurrentJobs']
                or sum(l['cpuThreads'] for l in leases)>=self.policy['cpuThreads']
                or any(l.get('jobKind')=='preview' and l.get('tenantId')==tenant_id for l in leases)):return None
            value['leases'][key]={**entry,'reservedAt':time.time()};del value['waiting'][key];return key

    def cancel_preview_waiting(self, job_id: str) -> None:
        if not self.root.exists():return
        with self.locked() as value:
            for key,entry in list(value['waiting'].items()):
                if entry.get('jobKind')=='preview' and entry['project']==str(self.project) and entry['jobId']==job_id:del value['waiting'][key]

    def release(self, lease_id: str, *, reason: str) -> None:
        with self.locked() as value:
            lease = value["leases"].get(lease_id)
            if lease is not None:
                require(lease["project"] == str(self.project), "RESOURCE_LEASE_OWNER_MISMATCH")
                self._audit({"leaseId": lease_id, "reason": reason, "at": time.time()})
                del value["leases"][lease_id]

    def cancel_waiting(self, workflow_id: str) -> None:
        if not self.root.exists(): return
        with self.locked() as value:
            for key, entry in list(value["waiting"].items()):
                if entry.get('jobKind') != 'preview' and entry["project"] == str(self.project) and entry["workflowId"] == workflow_id:
                    del value["waiting"][key]
            for key, entry in list(value["leases"].items()):
                if entry.get('jobKind') != 'preview' and entry["project"] == str(self.project) and entry["workflowId"] == workflow_id and not entry.get("attemptId"):
                    self._audit({"leaseId": key, "reason": "UNCLAIMED_CANCELLED", "at": time.time()})
                    del value["leases"][key]

    def waiting(self, workflow_id: str) -> bool:
        if not self.root.exists(): return False
        with self.locked() as value:
            return any(v.get('jobKind') != 'preview' and v["project"] == str(self.project) and v["workflowId"] == workflow_id for v in value["waiting"].values())

    def in_use(self, workflow_id: str) -> bool:
        if not self.root.exists(): return False
        with self.locked() as value:
            return any(entry.get('jobKind') != 'preview' and entry["project"] == str(self.project) and entry["workflowId"] == workflow_id
                       for collection in ("waiting", "leases") for entry in value[collection].values())
