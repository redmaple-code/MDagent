"""Persist, authorize, claim, execute fixed stages, and independently accept."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import fcntl
import hashlib
import os
import platform
import socket
import uuid

from .artifacts import state_values, verified_manifest, verify_result
from .errors import BioError, atomic_json, canonical, read_json, require, safe_file, sha256_file
from .preflight import preflight
from .provenance import runtime_root
from .runtime_adapter import LocalRuntime
from .store import ACTIVE, STATES, WorkflowStore
from .schema import load_runner, validate_protocol
from .execution_target import bind_project, check_resources, local_target, verify_target
from .resource_pool import ResourcePool


class Controller:
    def __init__(self, project: Path, python: Path, target: dict | None = None):
        self.project, self.python = project, python
        self.target = target or local_target(project, python)
        self.target_binding = bind_project(self.target, project, python)
        self.store = WorkflowStore(project)
        self.runtime = LocalRuntime(project, python, runtime_root())
        self.resources = ResourcePool(self.target, project)

    @contextmanager
    def _orchestration_lock(self):
        # CLI and direct controllers do not acquire the Bridge's lifetime host lock.
        descriptor = os.open(safe_file(self.project, "controller.lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _store_plan(self, protocol: dict, bindings: dict, reference: dict | None = None) -> dict:
        with self._orchestration_lock():
            try:
                self.store.get(protocol["workflowId"], protocol["revision"])
            except BioError as error:
                if error.code != "WORKFLOW_NOT_FOUND": raise
                require(not self.resources.in_use(protocol["workflowId"]), "WORKFLOW_ACTIVE")
            # Existing revisions keep the store's original replay/conflict checks.
            return self.store.plan(protocol, bindings, reference)

    def plan(self, protocol: dict) -> dict:
        bindings = self._preflight(protocol)
        require(bindings["ready"], "PREFLIGHT_BLOCKED")
        roots = [stage for stage in protocol["stages"] if not stage["dependsOn"]]
        require(all(stage["kind"] == "prepare" for stage in roots), "MODEL_REQUIRED")
        return self._store_plan(protocol, bindings)

    def get(self, workflow_id: str, revision: int | None = None) -> dict:
        value = self.store.get(workflow_id, revision)
        self.resources.sync_claims(value)
        return {**value, "resourceWaiting": self.resources.waiting(workflow_id)}

    def _preflight(self, protocol: dict) -> dict:
        protocol = validate_protocol(protocol)
        verified = verify_target(self.target, self.project, self.python)
        check_resources(self.target, protocol)
        result = preflight(self.project, protocol, self.python)
        return {**result, "executionTargetSha256": verified["executionTargetSha256"], "executionTarget": self.target}

    def _request_path(self, attempt_id: str) -> Path:
        return safe_file(self.project, "controller-requests/" + attempt_id + ".json")

    def _upload_dependency(self, attempt_id: str, prefix: str, path: Path,
                           manifest: dict, manifest_hash: str, source_request: dict) -> dict:
        # Verified stage outputs use their recorded production budget, not the
        # external-structure import limit. Check the entire bundle before sending.
        target_limit = self.target["resourcePolicy"]["maxOutputBytes"]
        source_limit = source_request["stage"]["resources"].get("maxOutputBytes", target_limit)
        require(type(source_limit) is int and type(target_limit) is int
                and 0 < source_limit <= 10 * 1024 ** 4 and 0 < target_limit <= 10 * 1024 ** 4,
                "INVALID_UPLOAD_BUDGET")
        budget = min(source_limit, target_limit)
        size = sum(entry["byteSize"] for entry in manifest["files"]) + path.stat().st_size
        require(size <= budget, "DEPENDENCY_OUTPUT_BUDGET_EXCEEDED")
        for entry in manifest["files"]:
            self.runtime.upload_file(attempt_id, prefix + entry["path"],
                                     safe_file(path.parent, entry["path"]), entry["sha256"], max_bytes=budget)
        self.runtime.upload_file(attempt_id, prefix + "manifest.json", path, manifest_hash, max_bytes=budget)
        return {"path": prefix + "manifest.json", "sha256": manifest_hash}

    def _make_request(self, value: dict, claim: dict) -> dict:
        stage, attempt_id = claim["stage"], claim["attemptId"]
        inputs, dependencies = [], []
        if stage["kind"] == "prepare":
            for entry in value["protocol"]["inputs"]:
                source = safe_file(self.project / "inputs", entry["path"])
                relative = "sources/" + entry["path"]
                self.runtime.upload_file(attempt_id, relative, source, entry["sha256"])
                inputs.append({"role": entry["role"], "path": relative, "sha256": entry["sha256"]})
        for parent_id in stage["dependsOn"]:
            matches = [item for item in value["attempts"] if item["stageId"] == parent_id and item["state"] == "COMPLETED"]
            require(len(matches) == 1, "DEPENDENCY_NOT_ACCEPTED")
            parent = matches[0]; parent_attempt = safe_file(self.runtime.root, parent["attemptId"])
            parent_request = read_json(self._request_path(parent["attemptId"]))
            accepted = verify_result(parent_attempt, parent_request, {"state": "COMPLETED"})
            require(accepted == parent["acceptance"], "DEPENDENCY_CHANGED")
            path = parent_attempt / "output/manifest.json"
            manifest = verified_manifest(path, accepted["manifestSha256"])
            prefix = "deps/" + parent_id + "/"
            dependencies.append(self._upload_dependency(attempt_id, prefix, path, manifest,
                                                       accepted["manifestSha256"], parent_request))
        request = {"schemaVersion": "ksh.stage-request.v1", "workflowId": value["workflowId"], "revision": value["revision"],
                   "attemptId": attempt_id, "stage": stage, "inputs": inputs, "dependencyBundles": dependencies}
        if value.get("reference") and not stage["dependsOn"]:
            reference = value["reference"]
            path, manifest = self._reference_manifest(reference)
            prefix = "deps/source/"
            source_request = read_json(self._request_path(reference["sourceAttemptId"]))
            bundle = self._upload_dependency(attempt_id, prefix, path, manifest,
                                             reference["manifestSha256"], source_request)
            request["dependencyBundles"] = [bundle]
            extra = {**bundle, **{key: reference[key] for key in ("sourceWorkflowId", "sourceRevision", "sourceStageId", "sourceAttemptId")}}
            if reference["kind"] == "continuation": extra.update(modelId=reference["modelId"], minimized=reference["minimized"])
            request[reference["kind"]] = extra
        return request

    def _launch_next(self, workflow_id: str, revision: int) -> dict:
        value = self.store.get(workflow_id, revision)
        require(value["grant"] is not None, "GRANT_REQUIRED")
        bindings = self.bindings_for(value)
        require(bindings["ready"], "PREFLIGHT_BLOCKED")
        require(all(bindings.get(k) == v for k, v in value["bindings"].items()), "GRANT_CHANGED")
        with self._orchestration_lock():
            value = self.store.get(workflow_id)
            require(value["revision"] == revision, "STALE_REVISION")
            self.resources.sync_claims(value)
            if value["state"] in {"FAILED", "CANCELED", "CANCEL_PENDING", "PAUSED", "COMPLETED", "NEEDS_REVIEW"} or any(a["state"] in ACTIVE for a in value["attempts"]):
                return value
            completed = {s["id"] for s in value["stages"] if s["state"] == "COMPLETED"}
            candidates = [s["stage"] for s in value["stages"] if s["state"] == "PENDING" and set(s["stage"]["dependsOn"]) <= completed]
            if not candidates: return value
            lease = self.resources.reserve(workflow_id, revision, candidates[0])
            if lease is None: return {**value, "resourceWaiting": True}
            try:
                claim = self.store.claim_next(workflow_id, revision, bindings)
            except BioError:
                # A database claim failure happens before the runner sees a request.
                self.resources.release(lease, reason="CLAIM_REJECTED")
                raise
            if claim is None:
                self.resources.release(lease, reason="NOT_CLAIMED")
                return self.store.get(workflow_id, revision)
            attempt_id = claim["attemptId"]
            self.resources.attach(lease, attempt_id)
        try:
            request = self._make_request(self.store.get(workflow_id, revision), claim)
            atomic_json(self._request_path(attempt_id), request)
        except BioError as error:
            self.resources.release(lease, reason="INPUT_REJECTED")
            return self.store.record_event(attempt_id, "stage-input-failed", {"state": "FAILED", "error": error.code})
        try:
            status = self.runtime.call({"action": "submit", "attemptId": attempt_id, "request": request})
        except BioError as error:
            if error.code == "TRANSPORT_FAILED":
                return self.store.record_event(attempt_id, "submit-reply-lost", {"state": "UNKNOWN", "error": error.code})
            self.resources.release(lease, reason="SUBMIT_REJECTED")
            return self.store.record_event(attempt_id, "submit-rejected", {"state": "FAILED", "error": error.code})
        return self._record_status(attempt_id, status)

    def submit(self, workflow_id: str, revision: int) -> dict:
        value = self.store.get(workflow_id, revision)
        require(value["grant"] is not None, "GRANT_REQUIRED")
        # claim_next checks the current revision and fresh bindings even on replay.
        value = self._launch_next(workflow_id, revision)
        return self.tick(workflow_id, advance=False) if any(a["state"] in ACTIVE for a in value["attempts"]) else value

    def _record_status(self, attempt_id: str, status: dict) -> dict:
        with self.store._connection() as connection:
            row = connection.execute("SELECT workflow_id,revision FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
        require(row is not None, "ATTEMPT_NOT_FOUND")
        value, previous = self.store.find_attempt(row[0], attempt_id)
        if previous["state"] in {"COMPLETED", "FAILED", "CANCELED"}: return value
        if (not isinstance(status, dict) or not isinstance(status.get("state"), str) or status["state"] not in STATES
                or type(status.get("sequence", 0)) is not int or status.get("sequence", 0) < 0):
            status = {"state": "UNKNOWN", "sequence": previous.get("sequence", 0), "error": "TRANSPORT_FAILED"}
        event = {key: status[key] for key in ("state", "sequence", "error") if key in status}
        sequence = status.get("sequence", 0)
        if sequence < previous.get("sequence", 0): return value
        if status["state"] == "COMPLETED":
            try:
                request = read_json(self._request_path(attempt_id))
                event["acceptance"] = verify_result(safe_file(self.runtime.root, attempt_id), request, status)
            except (BioError, OSError, KeyError, ValueError, TypeError, AttributeError, IndexError, UnicodeError) as error:
                event = {"state": "FAILED", "error": error.code if isinstance(error, BioError) else "RESULT_NOT_ACCEPTED", "sequence": sequence}
        if event["state"] in {"FAILED", "CANCELED"}:
            recovery = self._verified_recovery(value["workflowId"], attempt_id)
            if recovery is not None: event["recovery"] = recovery
        if all(previous.get(key) == item for key, item in event.items()): return value
        identity = hashlib.sha256(canonical(event)).hexdigest()[:16]
        return self.store.record_event(attempt_id, "runtime-" + str(sequence) + "-" + identity, event)

    def tick(self, workflow_id: str, *, advance: bool = True) -> dict:
        value = self.store.get(workflow_id)
        for attempt in value["attempts"]:
            if attempt["state"] not in ACTIVE: continue
            try: status = self.runtime.call({"action": "status", "attemptId": attempt["attemptId"]})
            except BioError as error:
                status = {"state": "UNKNOWN", "sequence": attempt.get("sequence", 0), "error": error.code}
            value = self._record_status(attempt["attemptId"], status)
        if advance and value["state"] in {"READY", "RUNNING"} and not any(a["state"] in ACTIVE for a in value["attempts"]):
            value = self._launch_next(workflow_id, value["revision"])
        return value

    def cancel(self, workflow_id: str) -> dict:
        with self._orchestration_lock():
            value = self.store.request_cancel(workflow_id)
            self.resources.sync_claims(value)
            self.resources.cancel_waiting(workflow_id)
            active = [attempt for attempt in value["attempts"] if attempt["state"] in ACTIVE]
        for attempt in active:
            try: status = self.runtime.call({"action": "cancel", "attemptId": attempt["attemptId"]})
            except BioError as error:
                status = {"state": "UNKNOWN", "sequence": attempt.get("sequence", 0), "error": error.code}
            value = self._record_status(attempt["attemptId"], status)
        return value

    def _verified_recovery(self, workflow_id: str, attempt_id: str) -> dict | None:
        value, attempt = self.store.find_attempt(workflow_id, attempt_id)
        root = safe_file(self.runtime.root, attempt_id)
        stream = root / "stream.json"
        if not stream.is_file(): return None
        candidates = []
        for record in read_json(stream).get("files", []):
            if record.get("role") != "recovery-manifest": continue
            try:
                path = safe_file(root / "output", record["path"])
                manifest = verified_manifest(path, record["sha256"], recovery=True)
                require(manifest["workflowId"] == workflow_id and manifest["revision"] == value["revision"]
                        and manifest["stageId"] == attempt["stageId"] and manifest["attemptId"] == attempt_id, "RECOVERY_LINEAGE_MISMATCH")
                required = {"state.xml", "system.xml", "topology.pdb", "model.json", "integrator.xml", "checkpoint.chk", "protocol.json", "environment.json"}
                require(required <= {f["path"] for f in manifest["files"]}, "INVALID_RECOVERY")
                model = read_json(path.parent / "model.json"); state = state_values(path.parent / "state.xml")
                require(model["modelId"] == manifest["modelId"] and model["particles"] == state["particles"]
                        and model.get("minimized") is True and manifest["checkpointStep"] == state["stepCount"]
                        and abs(manifest["checkpointTimePs"] - state["timePs"]) < 1e-9
                        and manifest["stateId"] == sha256_file(path.parent / "state.xml"), "INVALID_RECOVERY")
                candidates.append({"manifestPath": record["path"], "manifestSha256": record["sha256"], "modelId": model["modelId"],
                                   "checkpointStep": state["stepCount"], "checkpointTimePs": state["timePs"], "minimized": True})
            except (BioError, OSError, ValueError, KeyError): continue
        return max(candidates, key=lambda r: r["checkpointStep"]) if candidates else None

    def _reference_manifest(self, reference: dict) -> tuple[Path, dict]:
        value, attempt = self.store.find_attempt(reference["sourceWorkflowId"], reference["sourceAttemptId"])
        require(value["revision"] == reference["sourceRevision"] and attempt["stageId"] == reference["sourceStageId"], "LINEAGE_MISMATCH")
        root = safe_file(self.runtime.root, reference["sourceAttemptId"]) / "output"
        path = safe_file(root, reference["manifestPath"])
        if reference["kind"] == "continuation":
            require(attempt["state"] == "COMPLETED" and attempt.get("acceptance", {}).get("manifestSha256") == reference["manifestSha256"], "DEPENDENCY_NOT_ACCEPTED")
            request = read_json(self._request_path(reference["sourceAttemptId"]))
            accepted = verify_result(root.parent, request, {"state": "COMPLETED"})
            require(accepted == attempt["acceptance"], "DEPENDENCY_CHANGED")
        else:
            recovery = self._verified_recovery(reference["sourceWorkflowId"], reference["sourceAttemptId"])
            require(recovery is not None and recovery["manifestSha256"] == reference["manifestSha256"], "RECOVERY_CHANGED")
        manifest = verified_manifest(path, reference["manifestSha256"], recovery=reference["kind"] == "recovery")
        require(manifest["modelId"] == reference["modelId"], "MODEL_MISMATCH")
        return path, manifest

    def bindings_for(self, value: dict) -> dict:
        bindings = self._preflight(value["protocol"])
        reference = value.get("reference")
        if reference:
            path, manifest = self._reference_manifest(reference)
            bindings["inputSetSha256"] = hashlib.sha256(canonical({"inputs": bindings["inputSetSha256"], "reference": reference})).hexdigest()
            root = next(stage for stage in value["protocol"]["stages"] if not stage["dependsOn"])
            if root["kind"] == "analysis":
                require(any(entry["role"] == "trajectory" for entry in manifest["files"])
                        and any(entry["role"] == "trajectory-times" for entry in manifest["files"]), "TRAJECTORY_REQUIRED")
            if root["kind"] in {"production", "equilibrate"} and root["parameters"]["restart"] == "checkpoint":
                self._check_checkpoint_compatibility(manifest["checkpointCompatibility"], root, bindings.get("environment", {}))
        return bindings

    def _check_checkpoint_compatibility(self, compatibility: dict, stage: dict, environment: dict) -> None:
        selected = stage["resources"]["platform"]
        require(isinstance(compatibility, dict) and selected in {"CPU", "CUDA"}
                and compatibility.get("platform") == selected, "CHECKPOINT_INCOMPATIBLE")
        p = stage["parameters"]["dynamics"]
        physics = {key: p[key] for key in ("ensemble", "temperatureKelvin", "timestepFs", "frictionPerPs", "seed")}
        if "integrator" in p: physics["integrator"] = p["integrator"]
        if p["ensemble"] == "NPT": physics["pressureBar"] = p["pressureBar"]
        runner = load_runner()
        properties = ({"Threads": str(stage["resources"]["cpuThreads"])} if selected == "CPU" else
                      {"DeviceIndex": str(stage["resources"]["deviceIndex"]), "Precision": stage["resources"]["precision"]})
        if selected == "CPU":
            hardware = {"boot": runner.boot_identity(), "kernel": platform.release()}
        else:
            require(platform.system() == "Linux", "CHECKPOINT_INCOMPATIBLE")
            from openmm_workflow.stages import _hardware_identity
            try: hardware = _hardware_identity(selected, properties)
            except Exception: raise BioError("CHECKPOINT_INCOMPATIBLE") from None
        require(compatibility.get("openmmVersion") == environment.get("openmmFullVersion")
                and compatibility.get("system") == platform.system() and compatibility.get("machine") == platform.machine()
                and compatibility.get("host") == socket.gethostname()
                and compatibility.get("hardware") == hardware
                and compatibility.get("requestedProperties") == properties
                and compatibility.get("physics") == physics, "CHECKPOINT_INCOMPATIBLE")

    def continue_plan(self, source_workflow_id: str, source_attempt_id: str, protocol: dict) -> dict:
        protocol = validate_protocol(protocol)
        value, attempt = self.store.find_attempt(source_workflow_id, source_attempt_id)
        require(protocol["workflowId"] != source_workflow_id and not protocol["inputs"], "INVALID_CONTINUATION")
        roots = [stage for stage in protocol["stages"] if not stage["dependsOn"]]
        require(len(roots) == 1 and roots[0]["kind"] in {"minimize", "equilibrate", "production", "validate", "analysis"}, "INVALID_CONTINUATION")
        if attempt["state"] == "COMPLETED":
            root = safe_file(self.runtime.root, source_attempt_id) / "output"
            model = read_json(root / "model.json")
            reference = {"kind": "continuation", "manifestPath": "manifest.json", "manifestSha256": attempt["acceptance"]["manifestSha256"],
                         "modelId": model["modelId"], "minimized": model["minimized"]}
        else:
            reference = self._verified_recovery(source_workflow_id, source_attempt_id)
            require(reference is not None and roots[0]["kind"] in {"production", "equilibrate"}, "RECOVERY_UNAVAILABLE")
            reference = {**reference, "kind": "recovery"}
        reference.update(sourceWorkflowId=source_workflow_id, sourceRevision=value["revision"], sourceStageId=attempt["stageId"], sourceAttemptId=source_attempt_id)
        if roots[0]["kind"] in {"production", "equilibrate"}:
            require(reference["minimized"] is True, "MINIMIZATION_REQUIRED")
            require(roots[0]["parameters"]["dynamics"]["initializeVelocities"] is False, "CONTINUATION_VELOCITY_RESET_FORBIDDEN")
        bindings = self.bindings_for({"protocol": protocol, "reference": reference})
        require(bindings["ready"], "PREFLIGHT_BLOCKED")
        return self._store_plan(protocol, bindings, reference)

    def progress(self, attempt_id: str) -> dict | None:
        root = safe_file(self.runtime.root, attempt_id)
        status = root / "status.json"
        if not status.is_file(): return None
        load_runner()
        from openmm_workflow.progress import read_progress, read_progress_protocol
        value = read_progress(root, read_json(status))
        if value is not None and read_progress_protocol(root, value) is not None: return value
        return None

    def artifacts(self, workflow_id: str, stage_id: str, offset: int = 0, limit: int = 100) -> dict:
        require(type(offset) is int and offset >= 0 and type(limit) is int and 1 <= limit <= 100, "INVALID_PAGE")
        value = self.store.get(workflow_id)
        matches = [a for a in value["attempts"] if a["stageId"] == stage_id and a["state"] == "COMPLETED"]
        require(len(matches) == 1 and matches[0].get("acceptance"), "RESULT_NOT_ACCEPTED")
        attempt = matches[0]; root = safe_file(self.runtime.root, attempt["attemptId"]) / "output"
        manifest = verified_manifest(root / "manifest.json", attempt["acceptance"]["manifestSha256"])
        files = manifest["files"]
        require(offset <= len(files), "INVALID_PAGE")
        end = min(offset + limit, len(files))
        return {"attemptId": attempt["attemptId"], "sourceRef": {"kind":"accepted","sourceId":"result-"+attempt['attemptId'],
                "sourceSha256":attempt['acceptance']['manifestSha256'],"workflowId":value['workflowId'],"revision":value['revision'],
                "stageId":stage_id,"attemptId":attempt['attemptId']}, "items": [{**entry, "absolutePath": str(safe_file(root, entry["path"]))}
                for entry in files[offset:end]], "nextOffset": end if end < len(files) else None, "fileCount": len(files)}
