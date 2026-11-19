"""Bounded, source-preserving operator import and static project PDB intake."""
from __future__ import annotations
from collections import Counter
import hashlib
import os
from pathlib import Path
import re
import stat
from .errors import BioError, require, safe_file
from .schema import load_runner

MAX_BYTES = 64 * 1024 * 1024
SUPPORTED = set("ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE LEU LYS MET PHE PRO SER THR TRP TYR VAL HID HIE HIP CYX ASH GLH LYN HOH WAT NA CL Na Cl".split())


def _inputs(project: Path) -> Path:
    project = Path(project).absolute()
    require(not project.is_symlink(), "INVALID_PATH")
    project = project.parent.resolve() / project.name
    return safe_file(project, "inputs")


def _read(path: Path) -> bytes:
    require(not path.is_symlink() and path.is_file(), "INPUT_MISSING")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode), "INVALID_PATH")
            require(before.st_size <= MAX_BYTES, "INPUT_TOO_LARGE")
            data = stream.read(MAX_BYTES + 1)
            after = os.fstat(stream.fileno())
        require(len(data) <= MAX_BYTES, "INPUT_TOO_LARGE")
        identity = lambda value: (value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        require(identity(before) == identity(after) == identity(path.stat()), "INPUT_CHANGED")
        return data
    except OSError:
        raise BioError("INPUT_UNAVAILABLE") from None


def _record(path: Path, data: bytes) -> dict:
    digest = hashlib.sha256(data).hexdigest()
    stem = path.stem.lower()
    identity = stem if re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,127}", stem) else "input-" + digest[:24]
    return {"id": identity, "role": "structure", "path": path.name, "sha256": digest, "byteSize": len(data)}


def import_input(project: Path, source: Path) -> dict:
    source = Path(source).absolute()
    require(not source.is_symlink(), "INVALID_PATH")
    source = source.parent.resolve() / source.name
    data = _read(source)
    root = _inputs(project)
    root.mkdir(parents=True, exist_ok=True)
    destination = safe_file(root, source.name)
    if destination.exists():
        require(_read(destination) == data, "INPUT_CONFLICT")
        return _record(destination, data)
    require(len(list(root.iterdir())) < 32, "INPUT_LIMIT")
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        raise BioError("INPUT_CONFLICT") from None
    require(_read(source) == data and _read(destination) == data, "INPUT_CHANGED")
    return _record(destination, data)


def list_inputs(project: Path, offset: int, limit: int) -> dict:
    require(type(offset) is int and offset >= 0 and type(limit) is int and 1 <= limit <= 100, "INVALID_PAGINATION")
    root = _inputs(project)
    paths = sorted(root.iterdir()) if root.exists() else []
    require(len(paths) <= 32, "INPUT_LIMIT")
    records = [_record(path, _read(safe_file(root, path.name))) for path in paths[offset:offset + limit]]
    return {"items": records, "offset": offset, "limit": limit, "total": len(paths)}


def inspect_input(project: Path, path: str) -> dict:
    root = _inputs(project)
    source = safe_file(root, path)
    require(source.parent == root, "INVALID_PATH")
    data = _read(source)
    load_runner()
    from openmm_workflow.input_audit import scan_pdb
    blockers, summary = [], {}
    try:
        raw = scan_pdb(data, allow_ssbond=True)
        names = dict(Counter(raw["residues"].values()))
        summary = {"atoms": len(raw["atoms"]), "residues": len(raw["residues"]), "residueNames": names,
                   "chains": sorted({atom["key"][1] for atom in raw["atoms"]}), "cryst1AngstromDegrees": raw["box"]}
        if set(names) - SUPPORTED:
            blockers.append("UNSUPPORTED_RESIDUE")
    except (ValueError, UnicodeError):
        blockers.append("PDB_INTAKE_FAILED")
    require(_read(source) == data, "INPUT_CHANGED")
    return {**_record(source, data), "summary": summary, "blockers": blockers}
