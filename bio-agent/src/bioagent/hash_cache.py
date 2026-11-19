"""Reuses file digests only while complete filesystem identity remains stable."""
from __future__ import annotations
import os
from pathlib import Path
from .errors import atomic_json, read_json, require, sha256_file


class FileHashCache:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        require(path.absolute() == path.resolve() and not path.is_symlink(), "HASH_CACHE_PATH_INVALID")
        if path.exists():
            require(path.stat().st_uid == os.getuid() and path.stat().st_mode & 0o077 == 0, "HASH_CACHE_OWNER_INVALID")
            metadata = read_json(path)
            require(metadata.get("schemaVersion") == "bio.file-hash-cache.v1" and type(metadata.get("files")) is dict,
                    "HASH_CACHE_INVALID")
            self.files = metadata["files"]
        else: self.files = {}
        self.used = {}

    @staticmethod
    def identity(path: Path) -> list[int]:
        stat = path.stat()
        return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_mode]

    def digest(self, path: Path) -> str:
        actual = path.resolve()
        identity = self.identity(actual)
        cached = self.files.get(str(actual))
        if cached is not None and cached.get("identity") == identity:
            digest = cached["sha256"]
            from .schema import valid_hash
            require(valid_hash(digest), "HASH_CACHE_INVALID")
        else:
            digest = sha256_file(actual)
            require(identity == self.identity(actual), "ENVIRONMENT_CHANGED_DURING_PROBE")
        self.used[str(actual)] = {"identity": identity, "sha256": digest}
        return digest

    def save(self) -> None:
        if len(self.used) <= 32000:
            from .errors import canonical
            value = {"schemaVersion": "bio.file-hash-cache.v1", "files": self.used}
            if len(canonical(value)) <= 8 * 1024 * 1024:
                atomic_json(self.path, value)
