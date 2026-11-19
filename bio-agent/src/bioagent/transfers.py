"""Human-only bounded byte transfers; scientific interpretation stays remote."""
from __future__ import annotations
import base64
import hashlib
import os
from pathlib import Path
import re
import uuid
from .errors import atomic_json, canonical, read_json, require, safe_file, sha256_file
from .intake import import_input, MAX_BYTES
from .schema import valid_hash

CHUNK_BYTES = 65536


def record_chunk(project: Path, record_id: str, offset: int, limit: int) -> dict:
    require(type(record_id) is str and re.fullmatch(r"[a-f0-9]{64}\.(?:json|md)", record_id) is not None
            and type(offset) is int and offset >= 0 and type(limit) is int and 1 <= limit <= CHUNK_BYTES, "RECORD_REQUEST_INVALID")
    path = safe_file(project, "chat/records/" + record_id)
    require(path.is_file() and path.stat().st_size <= 8 * 1024 * 1024 and offset <= path.stat().st_size
            and sha256_file(path) == record_id.split(".")[0], "RECORD_CHANGED")
    with path.open("rb") as stream:
        stream.seek(offset); data = stream.read(limit)
    return {"offset": offset, "nextOffset": offset + len(data), "data": base64.b64encode(data).decode(),
            "complete": offset + len(data) == path.stat().st_size}


def _upload_directory(project: Path, upload_id: str) -> Path:
    require(type(upload_id) is str and re.fullmatch(r"u_[a-f0-9]{32}", upload_id) is not None, "UPLOAD_ID_INVALID")
    return safe_file(project, "chat/uploads/" + upload_id)


def begin_upload(project: Path, name: str, size: int, digest: str) -> dict:
    require(type(name) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,122}\.(?:pdb|ent)", name, re.I) is not None,
            "STRUCTURE_FILENAME_INVALID")
    require(type(size) is int and 0 < size <= MAX_BYTES and valid_hash(digest), "UPLOAD_SIZE_OR_HASH_INVALID")
    upload_id = "u_" + uuid.uuid4().hex
    directory = _upload_directory(project, upload_id)
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    atomic_json(directory / "metadata.json", {"name": name, "size": size, "sha256": digest})
    with (directory / name).open("xb") as output: output.flush(); os.fsync(output.fileno())
    (directory / name).chmod(0o600)
    return {"uploadId": upload_id, "offset": 0, "chunkBytes": CHUNK_BYTES}


def upload_chunk(project: Path, upload_id: str, offset: int, encoded: str) -> dict:
    directory = _upload_directory(project, upload_id)
    metadata = read_json(directory / "metadata.json")
    require(type(offset) is int and 0 <= offset <= metadata["size"] and type(encoded) is str
            and len(encoded) <= 4 * ((CHUNK_BYTES + 2) // 3), "UPLOAD_CHUNK_INVALID")
    try: data = base64.b64decode(encoded, validate=True)
    except (ValueError, UnicodeError):
        require(False, "UPLOAD_CHUNK_INVALID")
    require(0 < len(data) <= CHUNK_BYTES and offset + len(data) <= metadata["size"], "UPLOAD_CHUNK_INVALID")
    path = safe_file(directory, metadata["name"])
    current = path.stat().st_size
    with path.open("r+b") as output:
        if offset < current:
            output.seek(offset)
            require(offset + len(data) <= current and output.read(len(data)) == data, "UPLOAD_REPLAY_CONFLICT")
        else:
            require(offset == current, "UPLOAD_OFFSET_MISMATCH")
            output.seek(current); output.write(data); output.flush(); os.fsync(output.fileno())
    return {"uploadId": upload_id, "offset": path.stat().st_size}


def finish_upload(project: Path, upload_id: str) -> dict:
    directory = _upload_directory(project, upload_id)
    metadata = read_json(directory / "metadata.json")
    path = safe_file(directory, metadata["name"])
    require(path.stat().st_size == metadata["size"] and sha256_file(path) == metadata["sha256"], "UPLOAD_HASH_MISMATCH")
    return import_input(project, path)


def artifact_descriptor(controller, workflow_id: str, stage_id: str, name: str, expected_hash: str) -> dict:
    require(valid_hash(expected_hash), "ARTIFACT_HASH_INVALID")
    offset = 0
    while True:
        page = controller.artifacts(workflow_id, stage_id, offset, 100)
        for record in page["items"]:
            if record["path"] == name:
                require(record["sha256"] == expected_hash, "ARTIFACT_CHANGED")
                path = Path(record["absolutePath"])
                stat = path.stat()
                return {"workflowId": workflow_id, "stageId": stage_id, "path": name,
                    "sha256": record["sha256"], "byteSize": record["byteSize"],
                    "fileIdentity": [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]}
        if page["nextOffset"] is None: break
        offset = page["nextOffset"]
    require(False, "ARTIFACT_NOT_FOUND")


def begin_download(controller, workflow_id: str, stage_id: str, name: str, expected_hash: str) -> dict:
    descriptor = artifact_descriptor(controller, workflow_id, stage_id, name, expected_hash)
    ticket = hashlib.sha256(canonical(descriptor)).hexdigest()
    path = safe_file(controller.project, "chat/downloads/" + ticket + ".json")
    if path.exists(): require(read_json(path) == descriptor, "ARTIFACT_CHANGED")
    else: atomic_json(path, descriptor)
    return {"downloadId": ticket, "name": Path(name).name, "byteSize": descriptor["byteSize"],
            "sha256": descriptor["sha256"], "chunkBytes": CHUNK_BYTES}


def download_chunk(controller, download_id: str, offset: int, limit: int) -> dict:
    require(valid_hash(download_id) and type(offset) is int and offset >= 0
            and type(limit) is int and 1 <= limit <= CHUNK_BYTES, "DOWNLOAD_CHUNK_INVALID")
    record = read_json(safe_file(controller.project, "chat/downloads/" + download_id + ".json"))
    require(hashlib.sha256(canonical(record)).hexdigest() == download_id and offset <= record["byteSize"], "ARTIFACT_CHANGED")
    value = controller.get(record["workflowId"])
    attempts = [a for a in value["attempts"] if a["stageId"] == record["stageId"] and a["state"] == "COMPLETED" and a.get("acceptance")]
    require(len(attempts) == 1, "RESULT_NOT_ACCEPTED")
    path = safe_file(safe_file(controller.runtime.root, attempts[0]["attemptId"]) / "output", record["path"])
    stat = path.stat()
    identity = lambda item: [item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns]
    require(identity(stat) == record["fileIdentity"], "ARTIFACT_CHANGED")
    with path.open("rb") as stream:
        stream.seek(offset); data = stream.read(min(limit, record["byteSize"] - offset))
        require(identity(os.fstat(stream.fileno())) == record["fileIdentity"] and identity(path.stat()) == record["fileIdentity"], "ARTIFACT_CHANGED")
    return {"downloadId": download_id, "offset": offset, "nextOffset": offset + len(data),
            "data": base64.b64encode(data).decode(), "chunkSha256": hashlib.sha256(data).hexdigest(),
            "complete": offset + len(data) == record["byteSize"]}


def finish_download(controller, download_id: str) -> dict:
    require(valid_hash(download_id), "DOWNLOAD_CHUNK_INVALID")
    record = read_json(safe_file(controller.project, "chat/downloads/" + download_id + ".json"))
    require(hashlib.sha256(canonical(record)).hexdigest() == download_id, "ARTIFACT_CHANGED")
    # Re-read the accepted manifest and full file hash after the ordered chunk stream.
    current = artifact_descriptor(controller, record["workflowId"], record["stageId"], record["path"], record["sha256"])
    require(current == record, "ARTIFACT_CHANGED")
    return {"downloadId": download_id, "sha256": record["sha256"], "byteSize": record["byteSize"], "verified": True}
