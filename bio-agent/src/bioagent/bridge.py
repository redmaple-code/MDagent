"""Fixed bounded JSON-line bridge; model tools cannot obtain human grants."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import uuid
from .chat_contracts import load_contracts, validate_call
from .controller import Controller
from .coordinator import Coordinator
from .errors import BioError, canonical, require
from .intake import import_input
from .reporting import write_record
from .tool_api import ToolAPI
from .tool_receipts import RequestReceipts
from .execution_target import verify_target
from .schema import valid_hash
from .errors import atomic_json, read_json, safe_file

MAX_REQUEST = 2 * 1024 * 1024
MAX_RESPONSE = 128 * 1024


def _error(code: str) -> dict:
    messages = {"GRANT_REQUIRED": "请先通过人工审阅入口批准完整方案。",
                "TOOL_REQUEST_CONFLICT": "请求标识已对应其他参数，请核对原收据。",
                "PREFLIGHT_BLOCKED": "输入或环境检查未通过，请检查完整记录。"}
    return {"code": code, "message": messages.get(code, "本次操作未完成；请检查状态与该错误代码后决定下一步。")}


class Bridge:
    def __init__(self, project: Path, python: Path, target: dict | None = None):
        self.controller = Controller(project.resolve(), python, target)
        self.coordinator = Coordinator(self.controller)
        self.coordinator.acquire()
        self.api = ToolAPI(self.controller)
        self.receipts = RequestReceipts(self.controller.project)
        from .visualization.service import PreviewService
        self.previews = PreviewService(self.controller)

    def close(self) -> None:
        self.coordinator.close()

    def _validate(self, request: dict, version: str) -> None:
        require(type(request) is dict and set(request) == {"schemaVersion", "requestId", "tool", "arguments"}, "INVALID_ENVELOPE")
        require(request["schemaVersion"] == version, "INVALID_ENVELOPE")
        identity = request["requestId"]
        require(type(identity) is str and str(uuid.UUID(identity)) == identity, "INVALID_REQUEST_ID")
        require(len(canonical(request)) <= MAX_REQUEST, "REQUEST_TOO_LARGE")

    def _response(self, request: dict, result: dict) -> dict:
        response = {"schemaVersion": "bio.tool-result.v1", "requestId": request.get("requestId"),
                    "tool": request.get("tool"), **result}
        if len(canonical(response)) > MAX_RESPONSE:
            full = write_record(self.controller.project, result)
            summary = {key: result.get("result", {}).get(key) for key in ("workflowId", "revision", "state", "partial", "scientificConvergenceAssessed")
                       if key in result.get("result", {})}
            response = {**response, "result": {**summary, "fullResult": full}}
        require(len(canonical(response)) <= MAX_RESPONSE, "RESPONSE_TOO_LARGE")
        return response

    def handle(self, request: dict) -> dict:
        try:
            self._validate(request, "bio.tools.v1")
            validate_call(request["tool"], request["arguments"])
            receipt = self.receipts.begin(request)
            if receipt["status"] == "COMPLETE": return receipt["response"]
            if receipt["status"] == "PENDING":
                result = self.receipts.recover(request["requestId"], self.api)
            else:
                try:
                    self.receipts.guard(request)
                    if request["tool"] == "bio_submit":
                        args = request["arguments"]
                        self.coordinator.check_submission(args["workflowId"], args["revision"])
                        self.coordinator.remember_submission(args["workflowId"], args["revision"], request["requestId"])
                    result = {"ok": True, "result": self.api.call(request["tool"], request["arguments"])}
                except BioError as error:
                    result = {"ok": False, "error": _error(error.code)}
            response = self._response(request, result)
            self.receipts.complete(request["requestId"], response)
            return response
        except (BioError, ValueError, TypeError, KeyError, RecursionError) as error:
            code = error.code if isinstance(error, BioError) else "INVALID_ENVELOPE"
            metadata = request if type(request) is dict else {}
            identity = metadata.get("requestId"); tool = metadata.get("tool")
            return {"schemaVersion": "bio.tool-result.v1", "requestId": identity if type(identity) is str and len(identity) <= 64 else None,
                    "tool": tool if type(tool) is str and len(tool) <= 128 else None, "ok": False, "error": _error(code)}

    def handle_host(self, request: dict) -> dict:
        try:
            self._validate(request, "bio.host.v1")
            action, args = request["tool"], request["arguments"]
            fields = {"import": {"source"}, "activate": {"workflowId", "revision", "sourceRequestId"},
                      "acknowledge": {"sourceRequestId"}, "recover": {"workflowId"},
                      "reconcile": {"advance"}, "shutdown": set(), "target": set(),
                      "resource-preference": {"execution", "cpuThreads"}, "progress": {"workflowId", "revision"},
                      "record-chunk": {"recordId", "offset", "limit"},
                      "review": {"workflowId", "revision"}, "approve": {"workflowId", "revision", "reviewSha256", "actor"},
                      "resume": {"workflowId"}, "upload-begin": {"name", "byteSize", "sha256"},
                      "upload-chunk": {"uploadId", "offset", "data"}, "upload-finish": {"uploadId"},
                      "download-begin": {"workflowId", "stageId", "path", "sha256"},
                      "download-chunk": {"downloadId", "offset", "limit"}, "download-finish": {"downloadId"},
                      "preview-describe": {"source"}, "preview-prepare": {"request"}, "preview-status": {"jobId"},
                      "preview-window": {"previewId", "start", "end", "stride"},
                      "preview-read": {"previewId", "fileId", "offset", "limit"}, "preview-cancel": {"jobId"},
                      "preview-direct-open": {"binding"},
                      "preview-direct-permit": {"channelId", "fileId", "offset", "limit", "actor"},
                      "preview-direct-close": {"channelId", "actor"}}
            valid=type(action) is str and action in fields and type(args) is dict
            if valid:
                expected=fields[action]
                allowed=[expected]
                if action in {'preview-prepare','preview-window'}:allowed.append(expected|{'operationId'})
                if action=='preview-cancel':allowed.append({'operationId'})
                valid=set(args) in allowed
            require(valid, "INVALID_HOST_ARGUMENTS")
            if action.startswith('preview-'):
                method=getattr(self.previews,action.removeprefix('preview-').replace('-','_'))
                names={'source':'ref','jobId':'job_id','previewId':'preview_id','fileId':'file_id','channelId':'channel_id','operationId':'operation_id'}
                value=method(**{names.get(k,k):v for k,v in args.items()})
            elif action == "progress":
                from .compute_progress import compute_progress
                value = compute_progress(self.controller, args["workflowId"], args["revision"])
            elif action == "target":
                value = verify_target(self.controller.target, self.controller.project, self.controller.python)
                path = safe_file(self.controller.project, "chat/resource-preference.json")
                if path.exists(): value["resourcePreference"] = read_json(path)
            elif action == "resource-preference":
                execution = args["execution"]
                require(type(execution) is dict and execution.get("platform") in {"CPU", "CUDA"}
                        and set(execution) == ({"platform"} if execution["platform"] == "CPU" else {"platform", "deviceIndex", "precision"}), "INVALID_RESOURCES")
                require(type(args["cpuThreads"]) is int and 1 <= args["cpuThreads"] <= min(64, self.controller.target["resourcePolicy"]["cpuThreads"]), "RESOURCE_POLICY_EXCEEDED")
                if execution["platform"] == "CUDA":
                    require(type(execution["deviceIndex"]) is int and execution["deviceIndex"] in self.controller.target["resourcePolicy"]["gpuDevices"]
                            and execution["precision"] in {"single", "mixed", "double"}, "GPU_NOT_AUTHORIZED")
                value = {**args, "appliesToNewDrafts": True}
                atomic_json(safe_file(self.controller.project, "chat/resource-preference.json"), value)
            elif action == "record-chunk":
                from .transfers import record_chunk
                value = record_chunk(self.controller.project, args["recordId"], args["offset"], args["limit"])
            elif action in {"review", "approve"}:
                validate_call("bio_submit", {k: args[k] for k in ("workflowId", "revision")})
                from .review_view import review_view, human_summary
                planned = self.controller.get(args["workflowId"], args["revision"])
                require(planned["revision"] == self.controller.get(args["workflowId"])["revision"], "STALE_REVISION")
                bindings = self.controller.bindings_for(planned)
                require(bindings["ready"] and all(bindings.get(k) == v for k, v in planned["bindings"].items()), "GRANT_CHANGED")
                snapshot = {"workflowId": planned["workflowId"], "revision": planned["revision"],
                            "specSha256": planned["specSha256"], "bindings": planned["bindings"],
                            "executionTarget": self.controller.target}
                import hashlib
                digest = hashlib.sha256(canonical(snapshot)).hexdigest()
                if action == "review":
                    rendered = review_view(self.controller, planned, bindings)
                    value = {**snapshot, "reviewSha256": digest, "text": rendered["text"],
                             "summaryText": human_summary(self.controller, planned), "state": planned["state"]}
                else:
                    require(valid_hash(args["reviewSha256"]) and args["reviewSha256"] == digest, "REVIEW_CHANGED")
                    value = self.api.summary(self.controller.store.approve(planned["workflowId"], planned["revision"],
                                                                          planned["specSha256"], args["actor"]))
            elif action == "resume":
                validate_call("bio_get", args)
                value = self.coordinator.resume(args["workflowId"], human=True)
            elif action.startswith("upload-"):
                from .transfers import begin_upload, upload_chunk, finish_upload
                if action == "upload-begin": value = begin_upload(self.controller.project, args["name"], args["byteSize"], args["sha256"])
                elif action == "upload-chunk": value = upload_chunk(self.controller.project, args["uploadId"], args["offset"], args["data"])
                else: value = finish_upload(self.controller.project, args["uploadId"])
            elif action.startswith("download-"):
                from .transfers import begin_download, download_chunk, finish_download
                if action == "download-begin":
                    validate_call("bio_artifacts", {"workflowId": args["workflowId"], "stageId": args["stageId"], "offset": 0, "limit": 1})
                    value = begin_download(self.controller, args["workflowId"], args["stageId"], args["path"], args["sha256"])
                elif action == "download-chunk": value = download_chunk(self.controller, args["downloadId"], args["offset"], args["limit"])
                else: value = finish_download(self.controller, args["downloadId"])
            elif action == "import":
                require(type(args["source"]) is str and len(args["source"]) <= 4096, "INVALID_HOST_ARGUMENTS")
                value = import_input(self.controller.project, Path(args["source"]))
            elif action == "activate": value = self.coordinator.activate(args["workflowId"], args["revision"], args["sourceRequestId"])
            elif action == "acknowledge":
                entry = self.receipts.entry(args["sourceRequestId"])
                require(entry["request"]["tool"] in {"bio_plan", "bio_continue"}
                        and entry["response"] is not None and entry["response"].get("ok") is True, "REQUEST_NOT_COMPLETE")
                self.receipts.acknowledge(args["sourceRequestId"])
                value = {"acknowledged": True}
            elif action == "recover":
                validate_call("bio_get", args)
                self.coordinator.stop_advancing()
                try:
                    observed = self.api.call("bio_get", args)
                except BioError as error:
                    require(error.code == "WORKFLOW_NOT_FOUND", error.code)
                    observed = {"state": "NOT_CREATED", "attempts": []}
                require(observed["state"] != "UNKNOWN" and not any(a["state"] == "UNKNOWN" for a in observed["attempts"]), "TOOL_RESPONSE_UNKNOWN")
                recovered = []
                for entry in self.receipts.unresolved(args["workflowId"]):
                    if entry["response"] is None:
                        result = self.receipts.recover(entry["requestId"], self.api)
                        self.receipts.complete(entry["requestId"], self._response(entry["request"], result))
                    self.receipts.acknowledge(entry["requestId"])
                    recovered.append(entry["requestId"])
                value = {"workflowId": args["workflowId"], "state": observed["state"], "recoveredRequestIds": recovered,
                         "attemptsStarted": False, "advancing": False}
            elif action == "reconcile":
                self.previews.tick()
                reconciled = self.coordinator.reconcile(advance=args["advance"])
                value = (self.api.summary(reconciled) if "protocol" in reconciled else reconciled) if reconciled else {"advancing": False}
            else:
                self.close(); value = {"closed": True, "workersCanceled": False}
            return self._response(request, {"ok": True, "result": value})
        except Exception as error:
            code = error.code if isinstance(error, BioError) else "INVALID_HOST_ARGUMENTS"
            return {"schemaVersion": "bio.tool-result.v1", "requestId": request.get("requestId"), "tool": request.get("tool"), "ok": False, "error": _error(code)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Fixed local Bio tool bridge")
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--execution-target", type=str)
    args = parser.parse_args()
    try:
        target = json.loads(args.execution_target) if args.execution_target else None
        bridge = Bridge(args.project, args.python, target)
    except (BioError, ValueError, TypeError, KeyError) as error:
        sys.stderr.write((error.code if isinstance(error, BioError) else "EXECUTION_TARGET_INVALID") + "\n"); return 1
    try:
        while True:
            line = sys.stdin.buffer.readline(MAX_REQUEST + 1)
            if not line: break
            try:
                require(len(line) <= MAX_REQUEST, "REQUEST_TOO_LARGE")
                require(line.endswith(b"\n"), "INCOMPLETE_REQUEST")
                request = json.loads(line, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                response = bridge.handle_host(request) if type(request) is dict and request.get("schemaVersion") == "bio.host.v1" else bridge.handle(request)
            except (ValueError, UnicodeError, RecursionError, BioError) as error:
                if not line.endswith(b"\n"):
                    while line and not line.endswith(b"\n"):
                        line = sys.stdin.buffer.readline(MAX_REQUEST + 1)
                code = error.code if isinstance(error, BioError) else "INVALID_JSON"
                response = {"schemaVersion":"bio.tool-result.v1","requestId":None,"tool":None,"ok":False,"error":_error(code)}
            sys.stdout.buffer.write(canonical(response) + b"\n"); sys.stdout.buffer.flush()
    except BrokenPipeError:
        pass
    finally:
        bridge.close()
    return 0
