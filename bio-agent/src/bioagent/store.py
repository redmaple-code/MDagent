"""Transactional claims and revision-bound grants; no automatic execution."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time
import uuid

from .errors import BioError, canonical, require, safe_file
from .schema import protocol_sha256, valid_hash, valid_id, validate_protocol

BINDING_KEYS = ("runtimeSha256", "environmentSha256", "inputSetSha256")
ACTIVE = {"CLAIMED", "ACCEPTED", "RUNNING", "UNKNOWN", "CANCEL_PENDING"}
STATES = {"ACCEPTED", "RUNNING", "UNKNOWN", "CANCEL_PENDING", "COMPLETED", "FAILED", "CANCELED"}
DDL = """
CREATE TABLE workflows (workflow_id TEXT, revision INTEGER, spec_sha TEXT NOT NULL,
 state TEXT NOT NULL, data_json TEXT NOT NULL, PRIMARY KEY(workflow_id,revision));
CREATE TABLE grants (workflow_id TEXT, revision INTEGER, grant_json TEXT NOT NULL,
 PRIMARY KEY(workflow_id,revision), FOREIGN KEY(workflow_id,revision) REFERENCES workflows);
CREATE TABLE stages (workflow_id TEXT, revision INTEGER, stage_id TEXT, position INTEGER,
 stage_json TEXT NOT NULL, state TEXT NOT NULL, PRIMARY KEY(workflow_id,revision,stage_id),
 FOREIGN KEY(workflow_id,revision) REFERENCES workflows);
CREATE TABLE attempts (attempt_id TEXT PRIMARY KEY, workflow_id TEXT, revision INTEGER,
 stage_id TEXT, state TEXT NOT NULL, data_json TEXT NOT NULL,
 UNIQUE(workflow_id,revision,stage_id), FOREIGN KEY(workflow_id,revision,stage_id) REFERENCES stages);
CREATE TABLE events (attempt_id TEXT, event_id TEXT, value_json TEXT NOT NULL,
 PRIMARY KEY(attempt_id,event_id), FOREIGN KEY(attempt_id) REFERENCES attempts);
PRAGMA user_version=1;
"""


def binding_values(value: dict) -> dict:
    require(isinstance(value, dict) and value.get("ready") is True, "PREFLIGHT_BLOCKED")
    require(all(valid_hash(value.get(key)) for key in BINDING_KEYS), "INVALID_BINDINGS")
    keys = BINDING_KEYS + (("executionTargetSha256",) if "executionTargetSha256" in value else ())
    require(all(valid_hash(value.get(key)) for key in keys), "INVALID_BINDINGS")
    return {key: value[key] for key in keys}


class WorkflowStore:
    def __init__(self, project: Path, *, read_only: bool = False):
        require(not project.is_symlink(), "INVALID_PROJECT")
        self.read_only = read_only
        self.project = project.resolve()
        self.path = safe_file(self.project, "workflow.sqlite3")
        require(not read_only or self.path.is_file(), "STORE_READ_ONLY_UNAVAILABLE")
        if self.path.exists():
            # Inspect before any write-capable connection or pragma. Unknown DBs stay intact.
            with sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True) as connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                require(version == 1, "UNKNOWN_DATABASE_VERSION")
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                require({"workflows", "grants", "stages", "attempts", "events"} <= tables, "INVALID_DATABASE_SCHEMA")
        else:
            self.project.mkdir(parents=True, exist_ok=True, mode=0o700)
            connection = sqlite3.connect(self.path)
            try:
                connection.executescript("BEGIN IMMEDIATE;" + DDL + "COMMIT;")
            finally:
                connection.close()

    @contextmanager
    def _connection(self, *, write: bool = False):
        require(not (write and self.read_only), "STORE_READ_ONLY")
        require(not self.path.is_symlink(), "INVALID_PROJECT")
        connection = sqlite3.connect(self.path.as_uri() + "?mode=ro" if self.read_only else self.path,
                                     uri=self.read_only, isolation_level=None, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            require(connection.execute("PRAGMA user_version").fetchone()[0] == 1, "UNKNOWN_DATABASE_VERSION")
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            if write:
                connection.commit()
        except BaseException:
            if write:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _current(self, connection, workflow_id: str) -> int:
        require(valid_id(workflow_id), "INVALID_WORKFLOW_ID")
        row = connection.execute("SELECT MAX(revision) FROM workflows WHERE workflow_id=?", (workflow_id,)).fetchone()
        require(row[0] is not None, "WORKFLOW_NOT_FOUND")
        return row[0]

    def _get(self, connection, workflow_id: str, revision: int | None = None) -> dict:
        if revision is None:
            revision = self._current(connection, workflow_id)
        row = connection.execute("SELECT * FROM workflows WHERE workflow_id=? AND revision=?", (workflow_id, revision)).fetchone()
        require(row is not None, "WORKFLOW_NOT_FOUND")
        data = json.loads(row["data_json"])
        attempts = []
        for attempt in connection.execute("SELECT * FROM attempts WHERE workflow_id=? AND revision=? ORDER BY rowid", (workflow_id, revision)):
            details = json.loads(attempt["data_json"])
            count = connection.execute("SELECT COUNT(*) FROM events WHERE attempt_id=?", (attempt["attempt_id"],)).fetchone()[0]
            attempts.append({**details, "attemptId": attempt["attempt_id"], "stageId": attempt["stage_id"],
                             "state": attempt["state"], "eventCount": count})
        stages = [{"id": s["stage_id"], "state": s["state"], "stage": json.loads(s["stage_json"])}
                  for s in connection.execute("SELECT * FROM stages WHERE workflow_id=? AND revision=? ORDER BY position", (workflow_id, revision))]
        grant = connection.execute("SELECT grant_json FROM grants WHERE workflow_id=? AND revision=?", (workflow_id, revision)).fetchone()
        return {**data, "workflowId": workflow_id, "revision": revision, "specSha256": row["spec_sha"],
                "state": row["state"], "grant": json.loads(grant[0]) if grant else None, "stages": stages, "attempts": attempts}

    def get(self, workflow_id: str, revision: int | None = None) -> dict:
        with self._connection() as connection:
            return self._get(connection, workflow_id, revision)

    def list(self, offset: int = 0, limit: int = 100) -> dict:
        require(type(offset) is int and offset >= 0 and type(limit) is int and 1 <= limit <= 100, "INVALID_PAGE")
        with self._connection() as connection:
            rows = connection.execute("SELECT workflow_id,MAX(revision) AS revision FROM workflows GROUP BY workflow_id ORDER BY workflow_id LIMIT ? OFFSET ?",
                                      (limit + 1, offset)).fetchall()
            items = []
            for row in rows[:limit]:
                value = self._get(connection, row["workflow_id"], row["revision"])
                items.append({key: value[key] for key in ("workflowId", "revision", "state", "specSha256")})
            return {"items": items, "nextOffset": offset + limit if len(rows) > limit else None}

    def plan(self, protocol: dict, bindings: dict, reference: dict | None = None) -> dict:
        protocol = validate_protocol(protocol)
        bindings = binding_values(bindings)
        digest = protocol_sha256(protocol)
        if reference is not None:
            import hashlib
            digest = hashlib.sha256(canonical({"protocol": protocol, "reference": reference})).hexdigest()
        wid, revision = protocol["workflowId"], protocol["revision"]
        with self._connection(write=True) as connection:
            existing = connection.execute("SELECT spec_sha,data_json FROM workflows WHERE workflow_id=? AND revision=?", (wid, revision)).fetchone()
            if existing:
                require(existing[0] == digest and json.loads(existing[1])["bindings"] == bindings, "CONFLICT")
            else:
                latest = connection.execute("SELECT MAX(revision) FROM workflows WHERE workflow_id=?", (wid,)).fetchone()[0]
                require(latest is None or revision > latest, "STALE_REVISION")
                outstanding = connection.execute("SELECT COUNT(*) FROM workflows WHERE workflow_id=? AND state IN ('RUNNING','WAITING','CANCEL_PENDING')", (wid,)).fetchone()[0]
                outstanding += connection.execute("SELECT COUNT(*) FROM attempts WHERE workflow_id=? AND state IN ('CLAIMED','ACCEPTED','RUNNING','UNKNOWN','CANCEL_PENDING')", (wid,)).fetchone()[0]
                require(outstanding == 0, "WORKFLOW_ACTIVE")
                data = {"protocol": protocol, "bindings": bindings, "createdAt": time.time()}
                if reference is not None: data["reference"] = reference
                connection.execute("INSERT INTO workflows VALUES (?,?,?,?,?)", (wid, revision, digest, "DRAFT", canonical(data).decode()))
                for position, stage in enumerate(protocol["stages"]):
                    connection.execute("INSERT INTO stages VALUES (?,?,?,?,?,?)", (wid, revision, stage["id"], position, canonical(stage).decode(), "PENDING"))
            return self._get(connection, wid, revision)

    def find_attempt(self, workflow_id: str, attempt_id: str) -> tuple[dict, dict]:
        require(valid_id(workflow_id) and valid_id(attempt_id), "INVALID_ATTEMPT")
        with self._connection() as connection:
            row = connection.execute("SELECT revision FROM attempts WHERE workflow_id=? AND attempt_id=?", (workflow_id, attempt_id)).fetchone()
            require(row is not None, "ATTEMPT_NOT_FOUND")
            value = self._get(connection, workflow_id, row[0])
            return value, next(attempt for attempt in value["attempts"] if attempt["attemptId"] == attempt_id)

    def request_cancel(self, workflow_id: str) -> dict:
        with self._connection(write=True) as connection:
            revision = self._current(connection, workflow_id)
            value = self._get(connection, workflow_id, revision)
            if value["state"] in {"COMPLETED", "CANCELED", "FAILED", "PAUSED"}:
                return value
            row = connection.execute("SELECT data_json FROM workflows WHERE workflow_id=? AND revision=?", (workflow_id, revision)).fetchone()
            data = json.loads(row[0]); data["cancellationRequested"] = True
            state = "CANCEL_PENDING" if any(a["state"] in ACTIVE for a in value["attempts"]) else "CANCELED"
            connection.execute("UPDATE workflows SET state=?,data_json=? WHERE workflow_id=? AND revision=?", (state, canonical(data).decode(), workflow_id, revision))
            return self._get(connection, workflow_id, revision)

    def approve(self, workflow_id: str, revision: int, expected_sha256: str, actor: str) -> dict:
        require(isinstance(actor, str) and 0 < len(actor) <= 128 and not any(c in actor for c in ("\n", "\x00")), "INVALID_ACTOR")
        with self._connection(write=True) as connection:
            require(revision == self._current(connection, workflow_id), "STALE_REVISION")
            value = self._get(connection, workflow_id, revision)
            require(value["specSha256"] == expected_sha256, "CONFLICT")
            require(value["state"] in {"DRAFT", "READY"}, "WORKFLOW_NOT_REVIEWABLE")
            if value["grant"] is None:
                grant = {"schemaVersion": "bio.execution-grant.v1", "workflowId": workflow_id, "revision": revision,
                         "specSha256": expected_sha256, **value["bindings"], "actor": actor, "approvedAt": time.time()}
                connection.execute("INSERT INTO grants VALUES (?,?,?)", (workflow_id, revision, canonical(grant).decode()))
            connection.execute("UPDATE workflows SET state='READY' WHERE workflow_id=? AND revision=?", (workflow_id, revision))
            return self._get(connection, workflow_id, revision)

    def claim_next(self, workflow_id: str, revision: int, bindings: dict) -> dict | None:
        bindings = binding_values(bindings)
        with self._connection(write=True) as connection:
            require(revision == self._current(connection, workflow_id), "STALE_REVISION")
            value = self._get(connection, workflow_id, revision)
            grant = value["grant"]
            require(grant is not None, "GRANT_REQUIRED")
            require(grant["specSha256"] == value["specSha256"] and value["bindings"] == bindings
                    and all(grant.get(key) == bindings[key] for key in bindings), "GRANT_CHANGED")
            if value["state"] in {"FAILED", "CANCELED", "CANCEL_PENDING", "PAUSED", "COMPLETED", "NEEDS_REVIEW"}:
                return None
            if any(attempt["state"] in ACTIVE for attempt in value["attempts"]):
                return None
            completed = {stage["id"] for stage in value["stages"] if stage["state"] == "COMPLETED"}
            for stage in value["stages"]:
                if stage["state"] != "PENDING" or not set(stage["stage"]["dependsOn"]) <= completed:
                    continue
                attempt_id = "a-" + uuid.uuid4().hex
                connection.execute("INSERT INTO attempts VALUES (?,?,?,?,?,?)", (attempt_id, workflow_id, revision, stage["id"], "CLAIMED", "{}"))
                connection.execute("UPDATE stages SET state='CLAIMED' WHERE workflow_id=? AND revision=? AND stage_id=?", (workflow_id, revision, stage["id"]))
                connection.execute("UPDATE workflows SET state='RUNNING' WHERE workflow_id=? AND revision=?", (workflow_id, revision))
                return {"workflowId": workflow_id, "revision": revision, "attemptId": attempt_id, "stage": stage["stage"]}
            return None

    def record_event(self, attempt_id: str, event_id: str, value: dict) -> dict:
        require(valid_id(attempt_id) and valid_id(event_id) and isinstance(value, dict), "INVALID_EVENT")
        require(set(value) <= {"state", "error", "sequence", "acceptance", "recovery"} and value.get("state") in STATES, "INVALID_EVENT")
        if "sequence" in value:
            require(type(value["sequence"]) is int and value["sequence"] >= 0, "INVALID_EVENT")
        if value["state"] == "COMPLETED":
            require(isinstance(value.get("acceptance"), dict) and value["acceptance"].get("schemaVersion") == "bio.acceptance.v1"
                    and value["acceptance"].get("engineering") is True, "RESULT_NOT_ACCEPTED")
        encoded = canonical(value).decode()
        require(len(encoded) <= 65536, "INVALID_EVENT")
        with self._connection(write=True) as connection:
            attempt = connection.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            require(attempt is not None, "ATTEMPT_NOT_FOUND")
            existing = connection.execute("SELECT value_json FROM events WHERE attempt_id=? AND event_id=?", (attempt_id, event_id)).fetchone()
            if existing:
                require(existing[0] == encoded, "CONFLICT")
            else:
                previous = json.loads(attempt["data_json"])
                if "sequence" in value and value["sequence"] < previous.get("sequence", 0):
                    return self._get(connection, attempt["workflow_id"], attempt["revision"])
                require(attempt["state"] not in {"COMPLETED", "FAILED", "CANCELED"}, "TERMINAL_ATTEMPT")
                connection.execute("INSERT INTO events VALUES (?,?,?)", (attempt_id, event_id, encoded))
                connection.execute("UPDATE attempts SET state=?,data_json=? WHERE attempt_id=?", (value["state"], encoded, attempt_id))
                connection.execute("UPDATE stages SET state=? WHERE workflow_id=? AND revision=? AND stage_id=?",
                                   (value["state"], attempt["workflow_id"], attempt["revision"], attempt["stage_id"]))
                wid, revision = attempt["workflow_id"], attempt["revision"]
                remaining = connection.execute("SELECT COUNT(*) FROM stages WHERE workflow_id=? AND revision=? AND state!='COMPLETED'", (wid, revision)).fetchone()[0]
                state = {"UNKNOWN": "WAITING", "FAILED": "FAILED", "CANCELED": "CANCELED", "CANCEL_PENDING": "CANCEL_PENDING"}.get(value["state"], "RUNNING")
                if value["state"] == "COMPLETED" and remaining == 0:
                    state = "COMPLETED"
                if value["state"] == "FAILED" and value.get("recovery"):
                    state = "PAUSED"
                workflow = self._get(connection, wid, revision)
                if workflow.get("cancellationRequested"):
                    state = "CANCEL_PENDING" if any(a["state"] in ACTIVE for a in workflow["attempts"]) else "CANCELED"
                connection.execute("UPDATE workflows SET state=? WHERE workflow_id=? AND revision=?", (state, wid, revision))
            return self._get(connection, attempt["workflow_id"], attempt["revision"])
