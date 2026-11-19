"""Independent request transactions; unfinished effects are observed, never replayed."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from .errors import BioError, canonical, require, safe_file

EFFECTS = {"bio_plan", "bio_continue", "bio_submit"}


def effect_workflow(request: dict) -> str | None:
    args = request["arguments"]
    return args.get("workflowId") or args.get("intent", {}).get("workflowId")


class RequestReceipts:
    def __init__(self, project: Path):
        self.path = safe_file(project, "chat/requests.sqlite")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, sha TEXT NOT NULL, request TEXT NOT NULL, response TEXT, created REAL NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS acknowledged_effects (id TEXT PRIMARY KEY REFERENCES requests(id))")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            require(version in (0, 1), "REQUEST_SCHEMA_UNSUPPORTED")
            connection.execute("PRAGMA user_version=1")

    @contextmanager
    def _connection(self):
        safe_file(self.path.parent, self.path.name)
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback(); raise
        finally:
            connection.close()

    def begin(self, request: dict) -> dict:
        data = canonical(request); sha = hashlib.sha256(data).hexdigest()
        with self._connection() as connection:
            row = connection.execute("SELECT sha,response FROM requests WHERE id=?", (request["requestId"],)).fetchone()
            if row:
                require(row[0] == sha, "TOOL_REQUEST_CONFLICT")
                return {"status": "COMPLETE", "response": json.loads(row[1])} if row[1] else {"status": "PENDING"}
            connection.execute("INSERT INTO requests VALUES (?,?,?,?,?)", (request["requestId"], sha, data.decode(), None, time.time()))
            return {"status": "NEW"}

    def entry(self, request_id: str) -> dict:
        with self._connection() as connection:
            row = connection.execute("SELECT request,response FROM requests WHERE id=?", (request_id,)).fetchone()
            require(row is not None, "REQUEST_NOT_FOUND")
            return {"request": json.loads(row[0]), "response": json.loads(row[1]) if row[1] else None}

    def complete(self, request_id: str, response: dict) -> None:
        data = canonical(response).decode()
        with self._connection() as connection:
            row = connection.execute("SELECT response FROM requests WHERE id=?", (request_id,)).fetchone()
            require(row is not None, "REQUEST_NOT_FOUND")
            require(row[0] is None or row[0] == data, "TOOL_REQUEST_CONFLICT")
            connection.execute("UPDATE requests SET response=? WHERE id=?", (data, request_id))

    def acknowledge(self, request_id: str) -> None:
        entry = self.entry(request_id)
        require(entry["request"]["tool"] in EFFECTS and entry["response"] is not None, "REQUEST_NOT_COMPLETE")
        with self._connection() as connection:
            connection.execute("INSERT OR IGNORE INTO acknowledged_effects VALUES (?)", (request_id,))

    def acknowledged(self, request_id: str) -> bool:
        with self._connection() as connection:
            return connection.execute("SELECT 1 FROM acknowledged_effects WHERE id=?", (request_id,)).fetchone() is not None

    def unresolved(self, workflow_id: str, *, exclude: str | None = None) -> list[dict]:
        with self._connection() as connection:
            rows = connection.execute("SELECT r.id,r.request,r.response FROM requests r LEFT JOIN acknowledged_effects a ON r.id=a.id WHERE a.id IS NULL").fetchall()
        pending = []
        for identity, raw_request, raw_response in rows:
            request = json.loads(raw_request)
            if identity == exclude or request["tool"] not in EFFECTS or effect_workflow(request) != workflow_id:
                continue
            response = json.loads(raw_response) if raw_response else None
            if response is None or response.get("ok") is True or response.get("error", {}).get("code") == "TOOL_RESPONSE_UNKNOWN":
                pending.append({"requestId": identity, "request": request, "response": response})
        return pending

    def guard(self, request: dict) -> None:
        if request["tool"] in EFFECTS:
            require(not self.unresolved(effect_workflow(request), exclude=request["requestId"]), "TOOL_RESPONSE_UNKNOWN")

    def recover(self, request_id: str, api) -> dict:
        entry = self.entry(request_id)
        if entry["response"] is not None:
            return entry["response"]
        request = entry["request"]
        if request["tool"] in {"bio_plan", "bio_continue", "bio_submit", "bio_cancel"}:
            arguments = request["arguments"]
            workflow_id = arguments.get("workflowId") or arguments["intent"]["workflowId"]
            try:
                observed = api.call("bio_get", {"workflowId": workflow_id})
                message = "原请求响应未确认；现有状态 " + observed["state"] + "。请查询原工作流后明确选择下一步。"
            except BioError:
                message = "原请求响应未确认；请查询原工作流，不能自动重试计算。"
            return {"ok": False, "error": {"code": "TOOL_RESPONSE_UNKNOWN", "message": message}}
        return {"ok": True, "result": api.call(request["tool"], request["arguments"])}
