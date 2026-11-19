"""Estimate owned MD output and preserve headroom for a cooperative checkpoint."""
from __future__ import annotations

import math
from pathlib import Path
import shutil
import time

from .common import atomic_json, output_bytes, read_json, safe_path

MIB = 1024**2
_checks: dict[str, tuple[float, str | None]] = {}


def automatic(resources: dict) -> bool:
    # Already granted legacy requests retain their explicit budget. New native
    # plans use auto; an omitted quota is also automatic.
    return resources.get("outputPolicy") == "auto" or "maxOutputBytes" not in resources


def estimate(stage: dict, particles: int, inherited_bytes: int = 0, *, exact: bool = True) -> dict:
    dynamics = stage["parameters"].get("dynamics")
    frames = checkpoints = segments = 0
    duration = 0
    if dynamics:
        steps = dynamics["steps"]
        frames = math.ceil(steps / dynamics["reporterInterval"])
        checkpoints = math.ceil(steps / dynamics["checkpointInterval"]) + 1
        segments = math.ceil(frames / dynamics["segmentFrames"]) + checkpoints
        duration = steps * dynamics["timestepFs"] / 1000
    trajectory = frames * (12 * particles + 256) + segments * 4096
    # Hardlinked static model/trajectory files count once. State, integrator,
    # progress tail and manifest are newly written for each recovery point.
    recovery = checkpoints * (512 * particles + 128 * 1024)
    static = inherited_bytes + 4096 * particles + 16 * MIB
    expected = static + trajectory + recovery
    return {"schemaVersion": "ksh.storage-estimate.v1", "policy": "auto",
            "particles": particles, "particleCountExact": exact,
            "simulationTimePs": duration, "trajectoryFrames": frames,
            "reporterIntervalSteps": dynamics["reporterInterval"] if dynamics else None,
            "checkpointCount": checkpoints, "inheritedBytes": inherited_bytes,
            "expectedBytes": expected, "minimumBytes": inherited_bytes + trajectory,
            "checkpointReserveBytes": max(256 * MIB, 1024 * particles + 16 * MIB),
            "abnormalGrowthBytes": 4 * expected + 64 * MIB,
            "accounting": "unique output inodes plus owned logs; static recovery hardlinks counted once"}


def prepare_particles(attempt: Path, stage: dict, inputs: list[dict]) -> int:
    preparation = stage["parameters"].get("preparation", {})
    if preparation.get("mode") == "water-box":
        return math.ceil(128 * preparation["boxSizeNm"]**3)
    if preparation.get("mode") == "amber-small-molecule":
        side = 3 + 2 * preparation["solvent"]["paddingAngstrom"] / 10
        return math.ceil(128 * side**3) + 128
    count = 0
    low, high = [math.inf] * 3, [-math.inf] * 3
    for entry in inputs:
        if entry["role"] != "structure":
            continue
        with safe_path(attempt / "input", entry["path"]).open() as stream:
            for line in stream:
                if not line.startswith(("ATOM  ", "HETATM")):
                    continue
                try:
                    xyz = [float(line[start:start + 8]) for start in (30, 38, 46)]
                except ValueError:
                    continue  # Scientific input validation remains separate.
                if not all(math.isfinite(value) for value in xyz):
                    continue
                count += 1
                low = [min(a, b) for a, b in zip(low, xyz)]
                high = [max(a, b) for a, b in zip(high, xyz)]
    solvent = preparation.get("solvent", {})
    if count and solvent.get("mode") == "tip3p-box":
        padding = solvent["paddingAngstrom"]
        # A conservative sizing hint, not a chemical or solvent parameter.
        volume = math.prod((b - a + 2 * padding + 40) / 10 for a, b in zip(low, high))
        return 4 * count + math.ceil(128 * volume)
    return max(4 * count, 1024)


def configure(attempt: Path, request: dict, dependencies: list[tuple[Path, dict]]) -> dict | None:
    stage = request["stage"]
    if not automatic(stage["resources"]):
        return None
    particles, inherited = 0, 0
    copied_roles = {"model", "topology", "system", "integrator", "state", "checkpoint", "trajectory", "trajectory-times"}
    for manifest_path, manifest in dependencies:
        for entry in manifest["files"]:
            if entry["role"] in copied_roles:
                inherited += entry["byteSize"]
            if entry["role"] == "model":
                particles = read_json(safe_path(manifest_path.parent, entry["path"]))["particles"]
    exact = particles > 0
    if not exact:
        particles = prepare_particles(attempt, stage, request["inputs"])
    policy = estimate(stage, particles, inherited, exact=exact)
    atomic_json(attempt / "storage-policy.json", policy)
    _checks.pop(str(attempt), None)
    return policy


def reason(attempt: Path, *, cached: bool = True) -> str | None:
    key, now = str(attempt), time.monotonic()
    previous = _checks.get(key)
    if cached and previous and now - previous[0] < 1:
        return previous[1]
    path = attempt / "storage-policy.json"
    policy = read_json(path) if path.exists() else None
    free = shutil.disk_usage(attempt).free
    reserve = policy["checkpointReserveBytes"] if policy else 256 * MIB
    result = None
    if free < reserve:
        result = "DISK_SPACE_LOW"
    elif policy:
        used = output_bytes(attempt)
        if used > policy["abnormalGrowthBytes"]:
            result = "OUTPUT_GROWTH_ANOMALY"
        elif policy["particleCountExact"] and policy["minimumBytes"] - used > free - reserve:
            result = "DISK_SPACE_LOW"
    if len(_checks) >= 64:
        _checks.clear()
    _checks[key] = (now, result)
    return result
