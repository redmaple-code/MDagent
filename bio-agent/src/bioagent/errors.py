"""Bounded public errors and small deterministic filesystem primitives."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


class BioError(Exception):
    def __init__(self, code: str):
        self.code = code if isinstance(code, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", code) else "INTERNAL_ERROR"
        super().__init__(self.code)


def require(condition: bool, code: str) -> None:
    if not condition:
        raise BioError(code)


def canonical(value: object) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError):
        raise BioError("INVALID_JSON") from None


def sha256_file(file: Path) -> str:
    hasher = hashlib.sha256()
    with file.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def safe_file(root: Path, relative: str) -> Path:
    require(isinstance(relative, str) and 0 < len(relative) <= 1024, "INVALID_PATH")
    require(not any(c in relative for c in ("\\", ":", "\x00")), "INVALID_PATH")
    parts = relative.split("/")
    require(not any(part in ("", ".", "..") for part in parts), "INVALID_PATH")
    require(not root.is_symlink(), "INVALID_PATH")
    candidate = root
    for part in parts:
        candidate = candidate / part
        require(not candidate.is_symlink(), "INVALID_PATH")
    require(candidate.resolve().is_relative_to(root.resolve()), "INVALID_PATH")
    return candidate


def read_json(file: Path) -> dict:
    require(file.is_file() and file.stat().st_size <= 8 * 1024 * 1024, "INVALID_METADATA")
    try:
        value = json.loads(file.read_text(), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        require(isinstance(value, dict), "INVALID_METADATA")
        return value
    except (ValueError, UnicodeError):
        raise BioError("INVALID_METADATA") from None


def atomic_json(file: Path, value: object) -> None:
    data = canonical(value)
    file.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".atomic-", dir=file.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, file)
        directory = os.open(file.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)
