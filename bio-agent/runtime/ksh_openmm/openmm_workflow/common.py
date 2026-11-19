"""Small filesystem protocol shared by the detached runner and stage worker."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


class WorkflowError(Exception):
    """An intentionally public, non-sensitive protocol error."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise WorkflowError(code)


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def safe_path(root: Path, value: str) -> Path:
    require(isinstance(value, str) and 0 < len(value) <= 1024, "INVALID_PATH")
    parts = value.split("/")
    require(not any(p in ("", ".", "..") for p in parts)
            and not any(c in value for c in ("\\", ":", "\x00")), "INVALID_PATH")
    target = root.joinpath(*parts)
    require(target.resolve().is_relative_to(root.resolve()), "INVALID_PATH")
    current = root
    for part in parts:
        current = current / part
        require(not current.is_symlink(), "INVALID_PATH")
    return target


def valid_id(value: str) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,127}", value) is not None


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".atomic-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_json(path: Path) -> dict:
    require(path.stat().st_size <= 8 * 1024 * 1024, "METADATA_TOO_LARGE")
    return json.loads(path.read_text())


def file_record(root: Path, path: Path, role: str) -> dict:
    with path.open("rb") as stream:
        os.fsync(stream.fileno())
    return {"path": path.relative_to(root).as_posix(), "sha256": digest(path),
            "byteSize": path.stat().st_size, "role": role}


def verify_manifest(path: Path, expected_hash: str | None = None, *, allow_recovery: bool = False) -> dict:
    if expected_hash is not None:
        require(digest(path) == expected_hash, "HASH_MISMATCH")
    manifest = read_json(path)
    require(manifest.get("schemaVersion") == "ksh.stage-result.v1", "INVALID_MANIFEST")
    acceptance = manifest.get("acceptance", {})
    require(acceptance.get("engineering") is True or
            (allow_recovery and acceptance.get("engineering") is False and acceptance.get("recovery") is True),
            "DEPENDENCY_NOT_ACCEPTED")
    entries = manifest.get("files", [])
    require(isinstance(entries, list) and 0 < len(entries) <= 100000, "INVALID_MANIFEST")
    seen = set()
    for item in entries:
        require(item["path"] not in seen, "INVALID_MANIFEST")
        seen.add(item["path"])
        target = safe_path(path.parent, item["path"])
        require(target.is_file() and target.stat().st_size == item["byteSize"]
                and digest(target) == item["sha256"], "HASH_MISMATCH")
    return manifest


def output_bytes(attempt: Path) -> int:
    """Account immutable hardlinks once, and include all output/log metadata bytes."""
    seen = set()
    total = 0
    paths = list((attempt / "output").rglob("*")) if (attempt / "output").exists() else []
    paths += [attempt / "worker.log", attempt / "supervisor.log", attempt / "watchdog.log"]
    for path in paths:
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        identity = (stat.st_dev, stat.st_ino)
        if path.is_file() and identity not in seen:
            seen.add(identity)
            total += stat.st_size
    return total
