"""One project host, one acknowledged granted workflow, one immutable time window."""
from __future__ import annotations
import fcntl
import hashlib
import os
from pathlib import Path
import time
from .errors import BioError, atomic_json, canonical, read_json, require, safe_file
from .store import ACTIVE


class Coordinator:
    def __init__(self, controller):
        self.controller = controller
        self.path = safe_file(controller.project, "chat/coordinator.json")
        self.lock_path = safe_file(controller.project, "chat/host.lock")
        self.active_path = safe_file(controller.project, "chat/active-workflow.json")
        self.submission_path = safe_file(controller.project, "chat/submission-source.json")
        self._descriptor = None
        self._active = None
        self._last_poll = 0
        self._last_state = None
        self._clock = None

    def acquire(self) -> None:
        require(self._descriptor is None, "PROJECT_HOST_ACTIVE")
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.set_inheritable(descriptor, False)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor); raise BioError("PROJECT_HOST_ACTIVE") from None
        self._descriptor = descriptor

    def _other_active(self, workflow_id: str) -> None:
        offset = 0
        while True:
            page = self.controller.store.list(offset, 100)
            for item in page["items"]:
                if item["workflowId"] == workflow_id:
                    continue
                value = self.controller.get(item["workflowId"])
                require(not any(a["state"] in ACTIVE for a in value["attempts"])
                        and value["state"] not in {"RUNNING", "WAITING", "CANCEL_PENDING"}
                        and not value.get("resourceWaiting", False), "PROJECT_WORKFLOW_ACTIVE")
            if page["nextOffset"] is None:
                break
            offset = page["nextOffset"]

    def _window(self, value: dict) -> dict:
        now = time.time()
        window = read_json(self.path) if self.path.exists() else None
        identity = {"workflowId": value["workflowId"], "revision": value["revision"], "specSha256": value["specSha256"]}
        history = safe_file(self.controller.project, "chat/windows/" + hashlib.sha256(canonical(identity)).hexdigest() + ".json")
        # Preserve the legacy current record before switching; accepted evidence stays intact.
        if window is not None:
            old_identity = {key: window[key] for key in identity}
            old_path = safe_file(self.controller.project, "chat/windows/" + hashlib.sha256(canonical(old_identity)).hexdigest() + ".json")
            self._save_window(old_path, window)
        matching = read_json(history) if history.exists() else None
        if matching is not None:
            window = matching
            require(all(window.get(k) == v for k, v in identity.items()), "COORDINATOR_WINDOW_CHANGED")
            require(window["createdWall"] <= now <= window["deadline"], "COORDINATOR_BUDGET_EXCEEDED")
        else:
            if window is not None and self._active is not None:
                require(self._active == identity, "PROJECT_WORKFLOW_ACTIVE")
            budget = sum(stage["resources"]["wallTimeSeconds"] for stage in value["protocol"]["stages"]) + 120
            window = {**identity, "createdWall": now, "deadline": now + budget}
            self._save_window(history, window)
        if self._clock:
            wall, monotonic = self._clock
            require(abs((now - wall) - (time.monotonic() - monotonic)) < 5, "COORDINATOR_CLOCK_CHANGED")
        self._clock = (now, time.monotonic())
        if not self.path.exists() or read_json(self.path) != window:
            atomic_json(self.path, window)
        return window

    @staticmethod
    def _save_window(path: Path, window: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as stream:
                stream.write(canonical(window)); stream.flush(); os.fsync(stream.fileno())
        except FileExistsError:
            require(read_json(path) == window, "COORDINATOR_WINDOW_CHANGED")

    def check_submission(self, workflow_id: str, revision: int) -> None:
        require(self._descriptor is not None, "PROJECT_HOST_REQUIRED")
        value = self.controller.get(workflow_id)
        require(type(revision) is int and value["revision"] == revision, "STALE_REVISION")
        require(value["grant"] is not None, "GRANT_REQUIRED")
        require(value["state"] not in {"FAILED", "PAUSED", "CANCELED", "CANCEL_PENDING"}, "WORKFLOW_NOT_RUNNABLE")
        if self._active is not None and self._active["workflowId"] != workflow_id:
            previous = self.controller.get(self._active["workflowId"])
            require(previous["state"] in {"COMPLETED", "FAILED", "PAUSED", "CANCELED"}
                    and not any(a["state"] in ACTIVE for a in previous["attempts"]), "PROJECT_WORKFLOW_ACTIVE")
            self.stop_advancing()
        self._other_active(workflow_id)
        self._window(value)

    def remember_submission(self, workflow_id: str, revision: int, request_id: str) -> None:
        value = self.controller.get(workflow_id, revision)
        atomic_json(self.submission_path, {"workflowId": workflow_id, "revision": revision,
                                         "specSha256": value["specSha256"], "sourceRequestId": request_id})

    def activate(self, workflow_id: str, revision: int, source_request_id: str) -> dict:
        from .tool_receipts import RequestReceipts
        entry = RequestReceipts(self.controller.project).entry(source_request_id)
        response, request = entry["response"], entry["request"]
        require(request["tool"] == "bio_submit" and request["arguments"] == {"workflowId": workflow_id, "revision": revision}
                and response is not None and response.get("ok") is True
                and response["result"].get("state") not in {"UNKNOWN", "FAILED", "PAUSED", "CANCELED", "CANCEL_PENDING"},
                "SUBMIT_NOT_ACKNOWLEDGED")
        self.check_submission(workflow_id, revision)
        value = self.controller.get(workflow_id)
        require(not any(attempt["state"] == "UNKNOWN" for attempt in value["attempts"]), "SUBMIT_NOT_ACKNOWLEDGED")
        self._active = {key: value[key] for key in ("workflowId", "revision", "specSha256")}
        self._last_poll = 0
        RequestReceipts(self.controller.project).acknowledge(source_request_id)
        atomic_json(self.active_path, {**self._active, "sourceRequestId": source_request_id, "active": True})
        return {**self._active, "advancing": True}

    def resume(self, workflow_id: str, *, human: bool = False) -> dict:
        # Explicit human recovery or an already acknowledged durable activation.
        source = read_json(self.submission_path) if self.submission_path.exists() else None
        if self.active_path.exists():
            recorded = read_json(self.active_path)
            if recorded.get("workflowId") == workflow_id:
                source = recorded
        require(source is not None and source.get("workflowId") == workflow_id, "SUBMISSION_SOURCE_REQUIRED")
        value = self.controller.tick(workflow_id, advance=False)
        require(value["grant"] is not None and value["revision"] == source["revision"]
                and value["specSha256"] == source["specSha256"], "GRANT_CHANGED")
        require(not any(a["state"] == "UNKNOWN" for a in value["attempts"]), "TOOL_RESPONSE_UNKNOWN")
        if value["state"] == "COMPLETED":
            self.stop_advancing()
            return {"workflowId": workflow_id, "state": "COMPLETED", "advancing": False}
        from .tool_receipts import RequestReceipts
        receipts = RequestReceipts(self.controller.project)
        entry = receipts.entry(source["sourceRequestId"])
        if not entry["response"] or entry["response"].get("ok") is not True:
            # Human reconciliation can resume a known existing attempt without replaying submit.
            # The original lost/error receipt remains intact as evidence.
            require((human or source.get("recoveryObserved") is True) and receipts.acknowledged(source["sourceRequestId"])
                    and entry["request"]["tool"] == "bio_submit"
                    and entry["request"]["arguments"] == {"workflowId": workflow_id, "revision": value["revision"]}
                    and bool(value["attempts"]) and value["state"] == "RUNNING", "RECOVERY_NEEDS_EXPLICIT_SUBMIT")
            self.check_submission(workflow_id, value["revision"])
            self._active = {key: value[key] for key in ("workflowId", "revision", "specSha256")}
            self._last_poll = 0
            atomic_json(self.active_path, {**self._active, "sourceRequestId": source["sourceRequestId"],
                                          "active": True, "recoveryObserved": True})
            return {**self._active, "advancing": True, "resumedExistingAttempt": True}
        return self.activate(workflow_id, value["revision"], source["sourceRequestId"])

    def reconcile(self, *, advance: bool) -> dict | None:
        require(type(advance) is bool and self._descriptor is not None, "INVALID_HOST_ARGUMENTS")
        if self._active is None:
            if not self.active_path.exists() or read_json(self.active_path).get("active") is not True:
                return None
            recorded = read_json(self.active_path)
            try:
                self.resume(recorded["workflowId"])
            except BioError as error:
                self.stop_advancing()
                return {"workflowId": recorded.get("workflowId"), "advancing": False,
                        "recoveryRequired": True, "reason": error.code}
            if self._active is None:
                return None
        value = self.controller.get(self._active["workflowId"])
        require(all(value[k] == v for k, v in self._active.items()), "GRANT_CHANGED")
        try:
            self._window(value)
        except BioError:
            self.stop_advancing(); raise
        interval = 5 if self._last_state == value["state"] else 2
        if time.monotonic() - self._last_poll < interval:
            return value
        self._last_poll = time.monotonic()
        if (value["state"] in {"COMPLETED", "FAILED", "PAUSED", "CANCELED", "CANCEL_PENDING", "UNKNOWN"}
                or any(a["state"] == "UNKNOWN" for a in value["attempts"])):
            self.stop_advancing()
            return self.controller.tick(value["workflowId"], advance=False)
        value = self.controller.tick(value["workflowId"], advance=advance)
        self._last_state = value["state"]
        if value["state"] in {"COMPLETED", "FAILED", "PAUSED", "CANCELED", "CANCEL_PENDING", "UNKNOWN"}:
            self.stop_advancing()
        return value

    def close(self) -> None:
        self._active = None
        if self._descriptor is not None:
            os.close(self._descriptor); self._descriptor = None

    def stop_advancing(self) -> None:
        workflow_id = self._active["workflowId"] if self._active else None
        self._active = None
        if self.active_path.exists():
            recorded = read_json(self.active_path)
            workflow_id = workflow_id or recorded.get("workflowId")
            atomic_json(self.active_path, {**recorded, "active": False})
        if workflow_id:
            # Refresh the durable claim before releasing an unbound reservation.
            # An existing/unknown worker always retains its quota.
            try: self.controller.get(workflow_id)
            except BioError: return
            self.controller.resources.cancel_waiting(workflow_id)
