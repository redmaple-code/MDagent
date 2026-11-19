"""Only a fixed, source-verified stdin runner can execute scientific stages."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess

from .errors import BioError, canonical, require, safe_file, sha256_file
from .provenance import runtime_root, verify_runtime
from .schema import load_runner, valid_id


class LocalRuntime:
    def __init__(self, project: Path, python: Path, runtime: Path):
        require(runtime.resolve() == runtime_root().resolve(), "INVALID_RUNTIME_PATH")
        require(not project.is_symlink() and python.is_file() and os.access(python, os.X_OK), "INVALID_PROJECT")
        verify_runtime()
        self.root = safe_file(project, "runtime-attempts")
        self.python, self.runtime = python, runtime

    def call(self, request: dict) -> dict:
        require(isinstance(request, dict), "INVALID_REQUEST")
        action = request.get("action")
        fields = {"probe": ({"action"}, {"scope"}), "status": ({"action", "attemptId"}, {"fileOffset", "inventorySha256", "fileCount"}),
                  "upload": ({"action", "attemptId", "path", "offset", "totalSize", "sha256", "data"}, set()),
                  "submit": ({"action", "attemptId", "request"}, set()), "cancel": ({"action", "attemptId"}, set()),
                  "download": ({"action", "attemptId", "path", "offset", "limit"}, set())}
        require(isinstance(action, str) and action in fields, "INVALID_ACTION")
        required, optional = fields[action]
        require(required <= set(request) <= required | optional, "INVALID_REQUEST")
        if action != "probe": require(valid_id(request["attemptId"]), "INVALID_ATTEMPT")
        if action in {"upload", "download"}: safe_file(self.root, request["path"])
        if action == "submit":
            require(isinstance(request["request"], dict), "INVALID_REQUEST")
            stage = request["request"].get("stage", {})
            require(stage.get("resources", {}).get("location") == "local"
                    and stage.get("resources", {}).get("platform") in {"CPU", "CUDA"}, "COMPUTE_RESOURCES_REQUIRED")
            runner = load_runner()
            try: runner.validate_request(request["request"], request["attemptId"])
            except runner.WorkflowError as error: raise BioError(str(error)) from None
        data = canonical(request)
        require(len(data) <= 2 * 1024 * 1024, "INVALID_REQUEST")
        verify_runtime()
        environment = {key: value for key, value in os.environ.items() if key in {"HOME", "TMPDIR", "LANG", "LC_ALL"}}
        environment["PATH"] = str(self.python.parent) + ":/usr/bin:/bin"
        try:
            result = subprocess.run([str(self.python), "-I", "-B", str(self.runtime / "remote_runner.py"),
                                     "--root", str(self.root)], input=data, capture_output=True, timeout=60, env=environment)
        except (subprocess.SubprocessError, OSError):
            raise BioError("TRANSPORT_FAILED") from None
        require(result.returncode == 0 and len(result.stdout) <= 8 * 1024 * 1024, "TRANSPORT_FAILED")
        try: response = json.loads(result.stdout)
        except ValueError: raise BioError("TRANSPORT_FAILED") from None
        require(isinstance(response, dict) and type(response.get("ok")) is bool, "TRANSPORT_FAILED")
        if not response["ok"]: raise BioError(response.get("error", "RUNTIME_FAILED"))
        require(isinstance(response.get("result"), dict), "TRANSPORT_FAILED")
        return response["result"]

    def upload_file(self, attempt_id: str, relative: str, source: Path, expected_hash: str,
                    *, max_bytes: int = 64 * 1024 * 1024) -> None:
        require(type(max_bytes) is int and 0 < max_bytes <= 10 * 1024 ** 4, "INVALID_UPLOAD_BUDGET")
        require(source.is_file() and not source.is_symlink() and sha256_file(source) == expected_hash, "HASH_MISMATCH")
        total = source.stat().st_size
        require(total <= max_bytes, "INPUT_TOO_LARGE")
        offset = 0
        with source.open("rb") as stream:
            while offset < total or total == 0:
                chunk = stream.read(65536)
                result = self.call({"action": "upload", "attemptId": attempt_id, "path": relative, "offset": offset,
                                    "totalSize": total, "sha256": expected_hash, "data": base64.b64encode(chunk).decode()})
                require(result.get("offset") in {offset + len(chunk), total}, "INVALID_UPLOAD_REPLY")
                if result.get("complete") is True: break
                require(bool(chunk), "INVALID_UPLOAD_REPLY")
                offset += len(chunk)
        require(sha256_file(source) == expected_hash, "HASH_MISMATCH")
