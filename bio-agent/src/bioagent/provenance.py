"""Verify the fixed scientific runtime before importing or executing it."""
from __future__ import annotations

import hashlib
import difflib
from pathlib import Path

from .errors import BioError, canonical, read_json, require, safe_file, sha256_file

PRODUCT_ROOT = Path(__file__).resolve().parents[2]


def runtime_root(root: Path = PRODUCT_ROOT) -> Path:
    try:
        return safe_file(root, "runtime/ksh_openmm")
    except BioError:
        raise BioError("INVALID_RUNTIME_PATH") from None


def verify_runtime(root: Path = PRODUCT_ROOT) -> dict:
    runtime = runtime_root(root)
    snapshot = read_json(safe_file(root, "provenance/source-snapshot.json"))
    expected = snapshot["runtimeFiles"]
    require(len(expected) == 9 and len({f["path"] for f in expected}) == 9, "INVALID_RUNTIME_PATH")
    found = {p.relative_to(runtime).as_posix() for p in runtime.rglob("*")
             if "tests" not in p.relative_to(runtime).parts and (p.is_file() or p.is_symlink())}
    require(found == {f["path"] for f in expected}, "INVALID_RUNTIME_PATH")
    records = []
    for entry in sorted(expected, key=lambda f: f["path"]):
        try:
            file = safe_file(runtime, entry["path"])
        except BioError:
            raise BioError("INVALID_RUNTIME_PATH") from None
        require(file.is_file(), "RUNTIME_CHANGED")
        record = {"path": entry["path"], "sha256": sha256_file(file), "byteSize": file.stat().st_size}
        require(record == entry, "RUNTIME_CHANGED")
        records.append(record)
    source = {f["path"].removeprefix("workers/"): f for f in snapshot["files"] if f["role"] == "runtime"}
    patches = {p["path"]: p for p in snapshot["patches"]}
    require(len(patches) == len(snapshot["patches"]) and set(patches) <= set(source), "PROVENANCE_CHANGED")
    for entry in records:
        patch = patches.get(entry["path"])
        if patch is None:
            require(entry["sha256"] == source[entry["path"]]["sha256"], "PROVENANCE_CHANGED")
            continue
        original = safe_file(root, patch["originalPath"]); diff = safe_file(root, patch["patchPath"])
        require(original.is_file() and diff.is_file()
                and sha256_file(original) == patch["originalSha256"] == source[entry["path"]]["sha256"]
                and entry["sha256"] == patch["currentSha256"] and sha256_file(diff) == patch["patchSha256"], "PROVENANCE_CHANGED")
        expected_diff = "".join(difflib.unified_diff(original.read_text().splitlines(True),
            safe_file(runtime, entry["path"]).read_text().splitlines(True),
            fromfile="a/" + entry["path"], tofile="b/" + entry["path"]))
        require(diff.read_text() == expected_diff, "PROVENANCE_CHANGED")
    return {"runtimeSha256": hashlib.sha256(canonical(records)).hexdigest(), "files": records,
            "sourceHead": snapshot["sourceHead"], "sourceState": snapshot["sourceState"]}
