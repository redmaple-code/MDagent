"""Small human-only progress projection; no coordinates, logs, grants or samples."""
from __future__ import annotations

import hashlib
import math
import time

from .chat_contracts import validate_call
from .errors import BioError, canonical, require


def _observation(controller, value: dict, entry: dict, attempt: dict | None) -> dict | None:
    if attempt is None or entry["stage"]["kind"] not in {"equilibrate", "production"}:
        return None
    try:
        progress = controller.progress(attempt["attemptId"])
    except (BioError, OSError, ValueError, RecursionError):
        return None
    if not isinstance(progress, dict):
        return None
    binding = {"schemaVersion": "ksh.workflow-progress.v1", "workflowId": value["workflowId"],
               "revision": value["revision"], "stageId": entry["id"], "attemptId": attempt["attemptId"],
               "protocolSha256": hashlib.sha256(canonical(entry["stage"])).hexdigest()}
    if any(progress.get(key) != expected for key, expected in binding.items()):
        return None
    step, total = progress.get("stageStep"), progress.get("totalSteps")
    elapsed, published = progress.get("elapsedSeconds"), progress.get("publishedAtEpochMs")
    if (type(step) is not int or type(total) is not int or not 0 <= step <= total
            or total != entry["stage"]["parameters"]["dynamics"]["steps"]
            or type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0
            or type(published) is not int or not 0 <= published <= 2 ** 53 - 1):
        return None
    return {"stageStep": step, "totalSteps": total, "elapsedSeconds": elapsed, "publishedAtEpochMs": published}


def compute_progress(controller, workflow_id: str, revision: int) -> dict:
    # Reuse the existing workflow/revision validators without granting submission.
    validate_call("bio_submit", {"workflowId": workflow_id, "revision": revision})
    require(controller.get(workflow_id)["revision"] == revision, "STALE_REVISION")
    value = controller.tick(workflow_id, advance=False)
    require(value["workflowId"] == workflow_id and value["revision"] == revision, "STALE_REVISION")
    stages = []
    for entry in value["stages"]:
        attempt = next((a for a in reversed(value["attempts"]) if a["stageId"] == entry["id"]), None)
        stage = entry["stage"]
        stages.append({"id": entry["id"], "kind": stage["kind"], "state": entry["state"],
                       "attemptId": attempt["attemptId"] if attempt else None,
                       "attemptState": attempt["state"] if attempt else None,
                       "totalSteps": stage["parameters"]["dynamics"]["steps"]
                           if stage["kind"] in {"equilibrate", "production"} else None,
                       "progress": _observation(controller, value, entry, attempt)})
    require(controller.get(workflow_id)["revision"] == revision, "STALE_REVISION")
    completed = {entry["id"] for entry in value["stages"] if entry["state"] == "COMPLETED"}
    active = {"CLAIMED", "ACCEPTED", "RUNNING", "UNKNOWN", "CANCEL_PENDING"}
    current = next((s["id"] for s in stages if s["state"] in active or s["attemptState"] in active), None)
    if current is None and value["state"] in {"PAUSED", "FAILED", "CANCELED"}:
        current = next((s["id"] for s in stages if s["state"] in {"FAILED", "CANCELED"}), None)
    if current is None:
        current = next((s["id"] for s in value["stages"]
                        if s["state"] == "PENDING" and set(s["stage"]["dependsOn"]) <= completed), None)
    if current is None:
        current = value["attempts"][-1]["stageId"] if value["attempts"] else stages[0]["id"]
    return {"schemaVersion": "bio.compute-progress.v1", "workflowId": workflow_id, "revision": revision,
            "specSha256": value["specSha256"], "state": value["state"],
            "currentStageId": current,
            "resourceWaiting": value.get("resourceWaiting", False) or controller.resources.waiting(workflow_id),
            "observedAtEpochMs": int(time.time() * 1000), "stages": stages}
