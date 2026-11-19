#!/usr/bin/env python3
"""Bounded stdin protocol and durable detached scientific supervisor.

Only this trusted file can be launched. Request JSON never selects code, a shell,
credentials, or executables. Accepted attempts never automatically run twice.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
from functools import lru_cache
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
import re
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time
import threading
import uuid

# -I excludes the script directory; add only this installation's owned directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from openmm_workflow.common import (WorkflowError, atomic_json, canonical, digest, file_record,
                                    read_json, require, safe_path, valid_id, verify_manifest, output_bytes)
from openmm_workflow import storage

MAX_CHUNK = 65536
MAX_REQUEST = 2 * 1024 * 1024
TERMINAL = {"COMPLETED", "FAILED", "CANCELED"}


@contextmanager
def lock(path: Path, blocking: bool = True):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        try:
            yield stream
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@lru_cache(maxsize=1)
def boot_identity() -> str:
    if platform.system() == "Linux":
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    result = subprocess.run(["/usr/sbin/sysctl", "-n", "kern.boottime"], capture_output=True,
                            text=True, timeout=5, check=True)
    return result.stdout.strip()


def process_identity(pid: int) -> dict | None:
    try:
        if platform.system() == "Linux":
            content = Path(f"/proc/{pid}/stat").read_text()
            fields = content[content.rfind(")") + 2:].split()
            if fields[0] == "Z":
                return None
            start = fields[19]
        else:
            # Darwin proc_bsdinfo ABI (sys/proc_info.h): microsecond start identity,
            # unlike ps lstart's one-second resolution. SZOMB is 5.
            class BsdInfo(ctypes.Structure):
                _fields_ = [("prefix", ctypes.c_uint32 * 12), ("comm", ctypes.c_char * 16),
                            ("name", ctypes.c_char * 32), ("middle", ctypes.c_uint32 * 6),
                            ("startSeconds", ctypes.c_uint64), ("startMicroseconds", ctypes.c_uint64)]
            info = BsdInfo()
            libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
            call = libproc.proc_pidinfo
            call.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
            call.restype = ctypes.c_int
            if call(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info)) != ctypes.sizeof(info) or info.prefix[1] == 5:
                return None
            start = f"{info.startSeconds}.{info.startMicroseconds:06d}"
        return {"pid": pid, "start": start, "boot": boot_identity()}
    except (OSError, subprocess.SubprocessError, IndexError):
        return None


def transition(attempt: Path, state: str, **extra) -> dict:
    previous = read_json(attempt / "status.json") if (attempt / "status.json").exists() else {"sequence": 0}
    result = {**previous, "attemptId": attempt.name, "state": state,
              "sequence": previous["sequence"] + 1, "updatedAt": time.time(), **extra}
    atomic_json(attempt / "status.json", result)
    return result


def validate_request(request: dict, attempt_id: str):
    require(isinstance(request, dict) and set(request) in ({"schemaVersion", "workflowId", "revision", "attemptId", "stage", "inputs", "dependencyBundles"},
            {"schemaVersion", "workflowId", "revision", "attemptId", "stage", "inputs", "dependencyBundles", "recovery"},
            {"schemaVersion", "workflowId", "revision", "attemptId", "stage", "inputs", "dependencyBundles", "continuation"}), "INVALID_REQUEST")
    require(request["schemaVersion"] == "ksh.stage-request.v1" and request["attemptId"] == attempt_id
            and valid_id(request["workflowId"]) and type(request["revision"]) is int and request["revision"] >= 1, "INVALID_REQUEST")
    stage = request["stage"]
    require(set(stage) == {"id", "title", "kind", "dependsOn", "resources", "parameters"}
            and valid_id(stage["id"]), "INVALID_STAGE")
    kind = stage["kind"]
    expected = {"prepare": {"preparation", "system"}, "minimize": {"minimization"},
                "equilibrate": {"dynamics", "restart"}, "production": {"dynamics", "restart"},
                "validate": {"validation"}, "analysis": {"analysis"}}
    require(kind in expected and set(stage["parameters"]) == expected[kind], "INVALID_PARAMETERS")
    require(isinstance(stage["dependsOn"], list) and len(stage["dependsOn"]) <= 32
            and all(valid_id(v) for v in stage["dependsOn"])
            and len(set(stage["dependsOn"])) == len(stage["dependsOn"]), "INVALID_DEPENDENCIES")
    resources = stage["resources"]
    require(set(resources) <= {"location", "targetId", "targetRevision", "platform", "cpuThreads", "deviceIndex", "precision", "wallTimeSeconds", "maxOutputBytes", "outputPolicy"}
            and ("outputPolicy" not in resources or resources["outputPolicy"] == "auto"), "INVALID_RESOURCES")
    for key, low, high in (("cpuThreads", 1, 64), ("wallTimeSeconds", 1, 604800)):
        require(type(resources.get(key)) is int and low <= resources[key] <= high, "INVALID_RESOURCES")
    if "maxOutputBytes" in resources:
        require(type(resources["maxOutputBytes"]) is int and 1048576 <= resources["maxOutputBytes"] <= 10995116277760, "INVALID_RESOURCES")
    require(resources.get("platform") in ("CPU", "CUDA") and resources.get("location") in ("local", "remote"), "INVALID_RESOURCES")
    if resources["platform"] == "CUDA":
        require(type(resources.get("deviceIndex")) is int and 0 <= resources["deviceIndex"] <= 63
                and resources.get("precision") in ("single", "mixed", "double"), "INVALID_RESOURCES")
    require(isinstance(request["inputs"], list) and len(request["inputs"]) <= 32
            and isinstance(request["dependencyBundles"], list) and len(request["dependencyBundles"]) <= 32, "INVALID_INPUTS")
    for entry in request["inputs"] + request["dependencyBundles"]:
        require(isinstance(entry, dict) and set(entry) in ({"path", "sha256"}, {"role", "path", "sha256"})
                and isinstance(entry["sha256"], str) and len(entry["sha256"]) == 64
                and all(c in "0123456789abcdef" for c in entry["sha256"]), "INVALID_INPUTS")
    if "recovery" in request:
        recovery = request["recovery"]
        require(isinstance(recovery, dict) and set(recovery) == {"path", "sha256", "sourceWorkflowId", "sourceRevision", "sourceStageId", "sourceAttemptId"}, "INVALID_RECOVERY")
        require(kind in ("equilibrate", "production") and stage["dependsOn"] == []
                and request["dependencyBundles"] == [{"path": recovery["path"], "sha256": recovery["sha256"]}]
                and all(valid_id(recovery[k]) for k in ("sourceWorkflowId", "sourceStageId", "sourceAttemptId"))
                and type(recovery["sourceRevision"]) is int and recovery["sourceRevision"] >= 1, "INVALID_RECOVERY")
    if "continuation" in request:
        reference = request["continuation"]
        require(isinstance(reference, dict) and set(reference) == {"path", "sha256", "sourceWorkflowId", "sourceRevision", "sourceStageId", "sourceAttemptId", "modelId", "minimized"}
                and kind in ("minimize", "equilibrate", "production", "validate", "analysis") and stage["dependsOn"] == []
                and request["dependencyBundles"] == [{"path": reference["path"], "sha256": reference["sha256"]}]
                and type(reference["minimized"]) is bool and request["inputs"] == []
                and reference["sourceWorkflowId"] != request["workflowId"]
                and all(valid_id(reference[k]) for k in ("sourceWorkflowId", "sourceStageId", "sourceAttemptId"))
                and type(reference["sourceRevision"]) is int and reference["sourceRevision"] >= 1
                and all(isinstance(reference[k], str) and re.fullmatch(r"[a-f0-9]{64}", reference[k]) for k in ("sha256", "modelId")), "INVALID_CONTINUATION")
        require(kind not in ("equilibrate", "production") or reference["minimized"], "MINIMIZATION_REQUIRED")
    p = stage["parameters"]
    if kind == "prepare":
        preparation, system = p["preparation"], p["system"]
        water_box = preparation.get("mode") == "water-box"
        if water_box:
            require(set(preparation) == {"mode", "forceField", "boxSizeNm"}
                    and preparation["forceField"] == "tip3p"
                    and type(preparation["boxSizeNm"]) in (int, float)
                    and math.isfinite(preparation["boxSizeNm"]) and 2.2 <= preparation["boxSizeNm"] <= 10,
                    "INVALID_WATER_BOX")
            require(request["inputs"] == [] and request["dependencyBundles"] == [] and stage["dependsOn"] == []
                    and resources["location"] == "local" and resources["platform"] == "CPU", "WATER_BOX_REQUIRES_LOCAL_CPU")
        elif preparation.get("mode") in ("amber-protein-ligand", "amber-small-molecule"):
            from openmm_workflow.amber_preparation import validate_preparation
            validate_preparation(preparation)
            if preparation["mode"] == "amber-small-molecule":
                require(request["inputs"] == [] and request["dependencyBundles"] == [] and stage["dependsOn"] == []
                        and resources["location"] == "local" and resources["platform"] == "CPU"
                        and system.get("nonbondedMethod") == "PME" and system.get("rigidWater") is True,
                        "SMALL_MOLECULE_REQUIRES_LOCAL_CPU_PME")
        else:
            require(set(preparation) == {"forceField", "addHydrogens", "ph", "solvent"}
                    and preparation["forceField"] in ("amber14", "charmm36", "amber99sbildn")
                    and type(preparation["addHydrogens"]) is bool
                    and type(preparation["ph"]) in (int, float) and 0 <= preparation["ph"] <= 14, "INVALID_PREPARATION")
        if not water_box:
            solvent = preparation["solvent"]
            require(solvent.get("mode") in ("as-is", "tip3p-box"), "INVALID_SOLVENT")
            if solvent["mode"] == "as-is":
                require(set(solvent) == {"mode"}, "INVALID_SOLVENT")
            else:
                require(set(solvent) == {"mode", "paddingAngstrom", "ionicStrengthMolar", "neutralize"}
                        and type(solvent["paddingAngstrom"]) in (int, float) and 10 <= solvent["paddingAngstrom"] <= 100
                        and type(solvent["ionicStrengthMolar"]) in (int, float) and 0 <= solvent["ionicStrengthMolar"] <= 1
                        and type(solvent["neutralize"]) is bool, "INVALID_SOLVENT")
        require(set(system) == {"nonbondedMethod", "nonbondedCutoffNm", "constraints", "rigidWater", "removeCMMotion"}
                and system["nonbondedMethod"] in ("PME", "NoCutoff")
                and type(system["nonbondedCutoffNm"]) in (int, float) and 0 < system["nonbondedCutoffNm"] <= 10
                and system["constraints"] in ("HBonds", "AllBonds", "None")
                and type(system["rigidWater"]) is bool and type(system["removeCMMotion"]) is bool, "INVALID_SYSTEM")
        if water_box:
            require(system["nonbondedMethod"] == "PME" and system["rigidWater"]
                    and system["nonbondedCutoffNm"] < preparation["boxSizeNm"] / 2, "INVALID_WATER_SYSTEM")
    elif kind == "validate":
        require(p["validation"] == {"finiteEnergy": True, "finiteCoordinates": True}, "INVALID_VALIDATION")
    if kind in ("equilibrate", "production"):
        dynamics = p["dynamics"]
        fields = {"ensemble", "temperatureKelvin", "timestepFs", "frictionPerPs", "steps", "seed", "initializeVelocities",
                  "reporterInterval", "checkpointInterval", "segmentFrames"}
        require(fields <= set(dynamics) <= fields | {"pressureBar", "integrator", "benchmark"}, "INVALID_DYNAMICS")
        require(dynamics.get("integrator", "LangevinMiddle") in ("LangevinMiddle", "Langevin", "LangevinLegacy"), "INVALID_DYNAMICS")
        require(p["restart"] in ("state", "checkpoint") and dynamics.get("ensemble") in ("NVT", "NPT"), "INVALID_DYNAMICS")
        for key, low, high in (("steps", 1, 10000000000), ("reporterInterval", 1, 10000000),
                               ("checkpointInterval", 1, 10000000), ("segmentFrames", 1, 10000), ("seed", 1, 2147483647)):
            require(type(dynamics.get(key)) is int and low <= dynamics[key] <= high, "INVALID_DYNAMICS")
        for key, low, high in (("temperatureKelvin", 1, 500), ("timestepFs", 1e-9, 2), ("frictionPerPs", 1e-9, 100)):
            require(type(dynamics.get(key)) in (int, float) and low <= dynamics[key] <= high, "INVALID_DYNAMICS")
        require(type(dynamics.get("initializeVelocities")) is bool, "INVALID_DYNAMICS")
        if dynamics["ensemble"] == "NPT":
            require(type(dynamics.get("pressureBar")) in (int, float) and 0.1 <= dynamics["pressureBar"] <= 100, "INVALID_DYNAMICS")
        else:
            require("pressureBar" not in dynamics, "INVALID_DYNAMICS")
        if "benchmark" in dynamics:
            benchmark = dynamics["benchmark"]
            require(isinstance(benchmark, dict) and set(benchmark) == {"warmupSteps", "initialSteps", "targetSeconds", "maxCalibrationRounds", "repetitions"}, "INVALID_BENCHMARK")
            for key, low, high in (("warmupSteps", 0, 1000000), ("initialSteps", 1, 10000000),
                                  ("maxCalibrationRounds", 1, 20), ("repetitions", 1, 30)):
                require(type(benchmark[key]) is int and low <= benchmark[key] <= high, "INVALID_BENCHMARK")
            require(type(benchmark["targetSeconds"]) in (int, float) and 0 < benchmark["targetSeconds"] <= 120
                    and kind == "production" and dynamics["ensemble"] == "NVT" and p["restart"] == "state"
                    and (benchmark["warmupSteps"] + benchmark["initialSteps"]) * (benchmark["repetitions"] + 1) <= dynamics["steps"], "INVALID_BENCHMARK")
    elif kind == "minimize":
        controls = p["minimization"]
        require(set(controls) == {"toleranceKjMolNm", "maxIterations"}, "INVALID_MINIMIZATION")
        require(type(controls.get("maxIterations")) is int and 1 <= controls["maxIterations"] <= 1000000
                and type(controls.get("toleranceKjMolNm")) in (float, int) and 0 < controls["toleranceKjMolNm"] < 1e20, "INVALID_MINIMIZATION")
    elif kind == "analysis":
        controls = p["analysis"]
        require(set(controls) == {"selection", "alignmentSelection", "pbc", "stride", "metrics"}, "INVALID_ANALYSIS")
        require(controls.get("pbc") in ("none", "unwrap") and type(controls.get("stride")) is int
                and 1 <= controls["stride"] <= 100000, "INVALID_ANALYSIS")
        require(all(isinstance(controls.get(k), str) and 0 < len(controls[k]) <= 2048
                    for k in ("selection", "alignmentSelection")), "INVALID_ANALYSIS")
        require(isinstance(controls.get("metrics"), list) and 1 <= len(controls["metrics"]) <= 3
                and set(controls["metrics"]) <= {"rmsd", "rmsf", "radiusOfGyration"}
                and len(set(controls["metrics"])) == len(controls["metrics"]), "INVALID_ANALYSIS")


def budget_reason(attempt: Path, record: dict) -> str | None:
    budget = attempt / "budget.json"
    if budget.exists():
        return read_json(budget)["error"]
    resources = read_json(attempt / "request.json")["stage"]["resources"]
    if storage.automatic(resources):
        reason = storage.reason(attempt)
        if reason:
            return reason
    elif output_bytes(attempt) > resources["maxOutputBytes"]:
        return "OUTPUT_LIMIT_EXCEEDED"
    if time.monotonic() - record.get("stageStartedMono", time.monotonic()) > resources["wallTimeSeconds"]:
        return "WALLTIME_EXCEEDED"
    return None


def reconcile(attempt: Path, record: dict) -> dict:
    """Caller holds state.lock. Terminal evidence belongs to the exact worker."""
    if record["state"] in TERMINAL:
        return record
    evidence_path = attempt / "terminal.json"
    evidence = read_json(evidence_path) if evidence_path.exists() else None
    stage = record.get("stageIdentity")
    if evidence is not None:
        require(evidence.get("identity") == stage and evidence.get("nonce") == record.get("nonce"), "TERMINAL_IDENTITY_MISMATCH")
        state, error = evidence["state"], evidence.get("error")
        if state == "COMPLETED":
            manifest = verify_manifest(attempt / "output" / "manifest.json")
            request = read_json(attempt / "request.json")
            require(manifest["attemptId"] == attempt.name and manifest["stageId"] == request["stage"]["id"]
                    and manifest["workflowId"] == request["workflowId"] and manifest["revision"] == request["revision"], "MANIFEST_IDENTITY_MISMATCH")
        return transition(attempt, state, **({"error": error} if error else {}))
    if stage and process_identity(stage["pid"]) != stage:
        reason_path = attempt / "budget.json"
        error = read_json(reason_path)["error"] if reason_path.exists() else None
        if error:
            return transition(attempt, "FAILED", error=error)
        if (attempt / "cancel.json").exists():
            return transition(attempt, "CANCELED")
        return transition(attempt, "FAILED", error="STAGE_PROCESS_LOST")
    return record


def stop_owned_tool(attempt: Path, record: dict):
    """Stop only the registered tool group with a currently verified leader."""
    path = attempt / "tool-process.json"
    if not path.exists():
        return
    tool = read_json(path)
    identity = tool.get("identity") or {}
    if (tool.get("stageIdentity") == record.get("stageIdentity") and tool.get("nonce") == record.get("nonce")
        and tool.get("processGroup") == identity.get("pid") and process_identity(identity.get("pid", 0)) == identity):
        try:
            if os.getpgid(identity["pid"]) == identity["pid"]:
                os.killpg(identity["pid"], signal.SIGKILL)
        except ProcessLookupError:
            pass


def checkpoint_grace(attempt: Path) -> int:
    budget = attempt / "budget.json"
    code = read_json(budget).get("error") if budget.exists() else None
    return 30 if code in ("DISK_SPACE_LOW", "OUTPUT_GROWTH_ANOMALY") else 5


def watchdog(root: Path, attempt_id: str):
    """Independent fixed-code monitor survives supervisor death and blocking C calls."""
    attempt = Runner(root).attempt(attempt_id)
    try:
        with lock(attempt / "watchdog.lock", blocking=False):
            with lock(attempt / "state.lock"):
                record = read_json(attempt / "status.json")
                monitor_identity = process_identity(os.getpid())
                require(monitor_identity is not None, "PROCESS_IDENTITY_UNAVAILABLE")
                transition(attempt, record["state"], watchdogIdentity=monitor_identity)
                atomic_json(attempt / "watchdog-ready.json", {"identity": monitor_identity, "nonce": record["nonce"]})
            stopped_at = None
            terminated_at = None
            while True:
                with lock(attempt / "state.lock"):
                    record = read_json(attempt / "status.json")
                    stage = record.get("stageIdentity")
                    if ((attempt / "cancel.json").exists() or budget_reason(attempt, record)
                        or (stage and process_identity(stage["pid"]) != stage)):
                        stop_owned_tool(attempt, record)
                    record = reconcile(attempt, record)
                    if record["state"] in TERMINAL:
                        return
                    identity = record.get("stageIdentity")
                    require(identity is not None, "STAGE_IDENTITY_MISSING")
                    reason = budget_reason(attempt, record)
                    if reason:
                        if not (attempt / "budget.json").exists():
                            atomic_json(attempt / "budget.json", {"error": reason})
                        atomic_json(attempt / "cancel.json", {"requested": True})
                    canceled = (attempt / "cancel.json").exists()
                if canceled:
                    now = time.monotonic()
                    if stopped_at is None:
                        stopped_at = now
                    if process_identity(identity["pid"]) == identity:
                        grace = checkpoint_grace(attempt)
                        if terminated_at is None and now - stopped_at >= grace:
                            if os.getpgid(identity["pid"]) == identity["pid"]:
                                os.killpg(identity["pid"], signal.SIGTERM)
                            else:
                                os.kill(identity["pid"], signal.SIGTERM)
                            terminated_at = now
                        elif terminated_at is not None and now - terminated_at >= 3:
                            if os.getpgid(identity["pid"]) == identity["pid"]:
                                os.killpg(identity["pid"], signal.SIGKILL)
                            else:
                                os.kill(identity["pid"], signal.SIGKILL)
                time.sleep(.1)
    except BlockingIOError:
        return
    except ProcessLookupError:
        with lock(attempt / "state.lock"):
            reconcile(attempt, read_json(attempt / "status.json"))


class Runner:
    def __init__(self, root: Path):
        require(not root.is_symlink(), "INVALID_ROOT")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root = root.resolve()

    def attempt(self, value: str) -> Path:
        require(valid_id(value), "INVALID_ATTEMPT")
        attempt = safe_path(self.root, value)
        attempt.mkdir(exist_ok=True, mode=0o700)
        (attempt / "input").mkdir(exist_ok=True)
        return attempt

    def upload(self, attempt: Path, request: dict) -> dict:
        path = safe_path(attempt / "input", request["path"])
        offset, total = request["offset"], request["totalSize"]
        require(type(offset) is int and type(total) is int and 0 <= offset <= total <= 10995116277760, "INVALID_CHUNK")
        sha = request["sha256"]
        require(isinstance(sha, str) and len(sha) == 64 and all(c in "0123456789abcdef" for c in sha), "INVALID_HASH")
        try:
            data = base64.b64decode(request["data"], validate=True)
        except (ValueError, TypeError) as error:
            raise WorkflowError("INVALID_CHUNK") from error
        require(len(data) <= MAX_CHUNK and offset + len(data) <= total and (len(data) > 0 or total == 0), "INVALID_CHUNK")
        token = hashlib.sha256(request["path"].encode()).hexdigest()
        part = attempt / "uploads" / (token + ".part")
        metadata = attempt / "uploads" / (token + ".json")
        with lock(attempt / "state.lock"):
            if path.exists():
                require(path.is_file() and path.stat().st_size == total and digest(path) == sha, "CONFLICT")
                with path.open("rb") as stream:
                    stream.seek(offset)
                    require(stream.read(len(data)) == data, "CONFLICT")
                return {"offset": total, "complete": True}
            require(not (attempt / "request.json").exists(), "INPUTS_FROZEN")
            expected = {"path": request["path"], "totalSize": total, "sha256": sha}
            if metadata.exists():
                require(read_json(metadata) == expected, "CONFLICT")
            else:
                atomic_json(metadata, expected)
            part.parent.mkdir(exist_ok=True)
            size = part.stat().st_size if part.exists() else 0
            require(offset <= size, "OFFSET_MISMATCH")
            with part.open("r+b" if part.exists() else "w+b") as stream:
                if offset < size:
                    stream.seek(offset)
                    overlap = min(len(data), size - offset)
                    require(stream.read(overlap) == data[:overlap], "CONFLICT")
                    if overlap < len(data):
                        stream.seek(size)
                        stream.write(data[overlap:])
                        stream.flush()
                        os.fsync(stream.fileno())
                else:
                    stream.seek(offset)
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
            size = part.stat().st_size
            if size == total:
                require(digest(part) == sha, "HASH_MISMATCH")
                path.parent.mkdir(parents=True, exist_ok=True)
                os.link(part, path)  # no replacement of a finalized input
                directory = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
                part.unlink()
            return {"offset": size, "complete": size == total}

    def submit(self, attempt: Path, request: dict) -> dict:
        validate_request(request, attempt.name)
        with lock(attempt / "state.lock"):
            path = attempt / "request.json"
            if path.exists():
                require(canonical(read_json(path)) == canonical(request), "CONFLICT")
            else:
                for entry in request["inputs"] + request["dependencyBundles"]:
                    source = safe_path(attempt / "input", entry["path"])
                    require(source.is_file() and digest(source) == entry["sha256"], "HASH_MISMATCH")
                for entry in request["dependencyBundles"]:
                    verify_manifest(safe_path(attempt / "input", entry["path"]), entry["sha256"], allow_recovery="recovery" in request)
                atomic_json(path, request)
                if (attempt / "cancel.json").exists():
                    transition(attempt, "CANCELED")
                else:
                    transition(attempt, "ACCEPTED", nonce=uuid.uuid4().hex, acceptedAt=time.time())
                    # Dispatch claim is durable before launch. Crash in this gap is UNKNOWN,
                    # never an automatic second execution of an uncertain attempt.
                    with (attempt / "supervisor.log").open("ab") as log:
                        detached = subprocess.Popen([sys.executable, "-I", "-B", str(Path(__file__).resolve()),
                                          "--root", str(self.root), "--supervise", attempt.name],
                                         stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                         start_new_session=True, close_fds=True)
                        threading.Thread(target=detached.wait, daemon=True).start()
        return self.status(attempt)

    def status(self, attempt: Path, offset: int = 0, inventory_sha256: str | None = None, file_count: int | None = None) -> dict:
        require(type(offset) is int and offset >= 0, "INVALID_CHUNK")
        with lock(attempt / "state.lock"):
            if not (attempt / "status.json").exists():
                return {"attemptId": attempt.name, "state": "UNKNOWN", "sequence": 0}
            record = reconcile(attempt, read_json(attempt / "status.json"))
            if record["state"] not in TERMINAL:
                identity = record.get("identity")
                stage = record.get("stageIdentity")
                if stage and process_identity(stage["pid"]) == stage:
                    pass  # Exact original worker remains monitored independently.
                elif identity and process_identity(identity["pid"]) != identity:
                    if record["state"] != "UNKNOWN":
                        record = transition(attempt, "UNKNOWN", error="PROCESS_IDENTITY_LOST")
                elif not identity and time.time() - record.get("acceptedAt", 0) > 15:
                    if record["state"] != "UNKNOWN":
                        record = transition(attempt, "UNKNOWN", error="LAUNCH_IDENTITY_UNCONFIRMED")
            result = {k: record[k] for k in ("attemptId", "state", "sequence", "error") if k in record}
            manifest = attempt / "output" / "manifest.json"
            if record["state"] == "COMPLETED":
                content = verify_manifest(manifest)
                result["manifest"] = {k: v for k, v in file_record(manifest.parent, manifest, "manifest").items() if k != "role"}
                published = read_json(attempt / "stream.json")["files"] if (attempt / "stream.json").exists() else content["files"]
                by_path = {item["path"]: item for item in published}
                require(all(by_path.get(item["path"]) == item for item in content["files"]), "INVENTORY_MANIFEST_MISMATCH")
                result["files"] = published
            elif (attempt / "stream.json").exists():
                result["files"] = read_json(attempt / "stream.json")["files"]
            files = result.get("files", [])
            # A cursor freezes only a published prefix. Appends and lifecycle
            # transitions do not invalidate pages from that immutable prefix.
            if inventory_sha256 is None:
                require(file_count is None and offset == 0, "INVALID_CURSOR")
                count = len(files)
            else:
                require(type(file_count) is int and 0 <= file_count <= len(files), "INVALID_CURSOR")
                count = file_count
            inventory = hashlib.sha256(canonical(files[:count])).hexdigest()
            require(inventory_sha256 is None or inventory_sha256 == inventory, "CONFLICT")
            require(offset <= count, "INVALID_CHUNK")
            end = min(offset + 100, count)
            result.update(files=files[offset:end], fileCount=count,
                          fileOffset=offset, nextFileOffset=end if end < count else None,
                          inventorySha256=inventory)
            from openmm_workflow.progress import read_progress, read_progress_protocol
            progress = read_progress(attempt, record)
            if progress is not None:
                protocol = read_progress_protocol(attempt, progress)
                if protocol is not None:
                    diagnostic = {**result, "progress": progress, "progressProtocol": protocol}
                    # Optional observations must not overflow the bounded SSH
                    # response or turn a healthy scientific job into a failure.
                    if len(canonical({"ok": True, "result": diagnostic})) <= 60 * 1024:
                        result = diagnostic
            return result

    def cancel(self, attempt: Path) -> dict:
        with lock(attempt / "state.lock"):
            record = read_json(attempt / "status.json") if (attempt / "status.json").exists() else None
            if not record or record["state"] not in TERMINAL:
                atomic_json(attempt / "cancel.json", {"requested": True})
                if not record or not (attempt / "request.json").exists():
                    transition(attempt, "CANCELED")
                elif record["state"] != "CANCEL_PENDING":
                    transition(attempt, "CANCEL_PENDING")
        return self.status(attempt)

    def download(self, attempt: Path, request: dict) -> dict:
        offset, limit = request["offset"], request["limit"]
        require(type(offset) is int and offset >= 0 and type(limit) is int and 1 <= limit <= 4 * 1024 * 1024, "INVALID_CHUNK")
        # Finalized data is immutable. Verify each file once per inode/stat identity,
        # rather than hashing multi-GB files again for every 64 KiB transfer chunk.
        with lock(attempt / "state.lock"):
            record = read_json(attempt / "status.json") if (attempt / "status.json").exists() else {}
            if record.get("state") == "COMPLETED":
                manifest_path = attempt / "output" / "manifest.json"
                content = read_json(manifest_path)
                recovery_files = [f for f in read_json(attempt / "stream.json")["files"]
                                  if f["role"] in ("recovery-file", "recovery-manifest")] if (attempt / "stream.json").exists() else []
                available = content["files"] + recovery_files + [file_record(manifest_path.parent, manifest_path, "manifest")]
            else:
                available = read_json(attempt / "stream.json")["files"] if (attempt / "stream.json").exists() else []
        matches = [f for f in available if f["path"] == request["path"]]
        require(len(matches) == 1, "FILE_NOT_FINALIZED")
        record = matches[0]
        path = safe_path(attempt / "output", request["path"])
        stat = path.stat()
        require(stat.st_size == record["byteSize"], "HASH_MISMATCH")
        identity = {"inode": stat.st_ino, "mtime": stat.st_mtime_ns, "ctime": stat.st_ctime_ns,
                    "size": stat.st_size, "sha256": record["sha256"]}
        cache = attempt / "verified-downloads" / (hashlib.sha256(request["path"].encode()).hexdigest() + ".json")
        if not cache.exists() or read_json(cache) != identity:
            require(digest(path) == record["sha256"], "HASH_MISMATCH")
            atomic_json(cache, identity)
        require(offset <= record["byteSize"], "OFFSET_MISMATCH")
        with path.open("rb") as stream:
            stream.seek(offset)
            data = stream.read(limit)
        return {"data": base64.b64encode(data).decode(), "offset": offset,
                "nextOffset": offset + len(data), "totalSize": record["byteSize"], "sha256": record["sha256"]}

    def dispatch(self, request: dict) -> dict:
        action = request["action"]
        if action == "probe":
            from openmm_workflow.stages import probe
            require(set(request) <= {"action", "scope"}
                    and request.get("scope", "full") in ("full", "dynamics"), "INVALID_REQUEST")
            full = request.get("scope", "full") == "full"
            return probe(include_analysis=full, include_preparation=full)
        require(action in ("upload", "submit", "status", "cancel", "download"), "INVALID_ACTION")
        attempt = self.attempt(request["attemptId"])
        if action == "upload":
            return self.upload(attempt, request)
        if action == "submit":
            return self.submit(attempt, request["request"])
        if action == "download":
            return self.download(attempt, request)
        return self.status(attempt, request.get("fileOffset", 0), request.get("inventorySha256"), request.get("fileCount")) if action == "status" else self.cancel(attempt)


def supervise(root: Path, attempt_id: str):
    runner = Runner(root)
    attempt = runner.attempt(attempt_id)
    try:
        with lock(attempt / "execution.lock", blocking=False) as execution_lock:
            with lock(attempt / "state.lock"):
                record = read_json(attempt / "status.json")
                if record["state"] in TERMINAL | {"UNKNOWN"} or record.get("identity"):
                    return
                if (attempt / "cancel.json").exists():
                    transition(attempt, "CANCELED")
                    return
                identity = process_identity(os.getpid())
                require(identity is not None, "PROCESS_IDENTITY_UNAVAILABLE")
                transition(attempt, "RUNNING", identity=identity)
            request = read_json(attempt / "request.json")
            resources = request["stage"]["resources"]
            gpu_lock = None
            try:
                if resources["platform"] == "CUDA":
                    gpu_path = root / "gpu-locks" / (str(resources["deviceIndex"]) + ".lock")
                    gpu_path.parent.mkdir(exist_ok=True)
                    gpu_lock = gpu_path.open("a+b")
                    try:
                        fcntl.flock(gpu_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError as error:
                        raise WorkflowError("GPU_BUSY") from error
                with (attempt / "worker.log").open("ab") as log:
                    environment = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME", "OPENMM_DEFAULT_PLATFORM", "CUDA_VISIBLE_DEVICES"}}
                    environment.update({"OMP_NUM_THREADS": str(resources["cpuThreads"]),
                                        "OPENMM_CPU_THREADS": str(resources["cpuThreads"]),
                                        "OPENBLAS_NUM_THREADS": str(resources["cpuThreads"])})
                    child = subprocess.Popen([sys.executable, "-I", "-B", str(Path(__file__).resolve()),
                                              "--root", str(root), "--stage", attempt_id],
                                             stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                             close_fds=True, env=environment, start_new_session=True,
                                             pass_fds=(execution_lock.fileno(),) +
                                             ((gpu_lock.fileno(),) if gpu_lock is not None else ()))
                # The stage launches its independent watchdog before scientific imports.
                # This process only reaps its child and reconciles durable evidence.
                child.wait()
                with lock(attempt / "state.lock"):
                    record = reconcile(attempt, read_json(attempt / "status.json"))
                    if record["state"] not in TERMINAL and not record.get("stageIdentity"):
                        transition(attempt, "FAILED", error="STAGE_LAUNCH_FAILED")
            finally:
                if gpu_lock is not None:
                    gpu_lock.close()
    except BlockingIOError:
        return
    except Exception as error:
        with lock(attempt / "state.lock"):
            transition(attempt, "FAILED", error=str(error) if isinstance(error, WorkflowError) else "SUPERVISOR_FAILED")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    internal = parser.add_mutually_exclusive_group()
    internal.add_argument("--supervise")
    internal.add_argument("--stage")
    internal.add_argument("--watchdog")
    args = parser.parse_args()
    if args.supervise:
        supervise(args.root.resolve(), args.supervise)
        return 0
    if args.watchdog:
        watchdog(args.root.resolve(), args.watchdog)
        return 0
    if args.stage:
        attempt = Runner(args.root).attempt(args.stage)
        stage_identity = None
        try:
            with lock(attempt / "state.lock"):
                record = read_json(attempt / "status.json")
                require(record["state"] in ("RUNNING", "CANCEL_PENDING", "UNKNOWN") and record.get("identity"), "SUPERVISOR_IDENTITY_MISSING")
                require("stageIdentity" not in record, "STAGE_ALREADY_LAUNCHED")
                stage_identity = process_identity(os.getpid())
                require(stage_identity is not None, "PROCESS_IDENTITY_UNAVAILABLE")
                record = transition(attempt, "CANCEL_PENDING" if (attempt / "cancel.json").exists() else "RUNNING",
                                    stageIdentity=stage_identity, stageStartedMono=time.monotonic())
            with (attempt / "watchdog.log").open("ab") as log:
                monitor = subprocess.Popen([sys.executable, "-I", "-B", str(Path(__file__).resolve()),
                                            "--root", str(args.root.resolve()), "--watchdog", args.stage],
                                           stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                           start_new_session=True, close_fds=True)
            startup_deadline = time.monotonic() + 5
            while not (attempt / "watchdog-ready.json").exists() and time.monotonic() < startup_deadline:
                require(monitor.poll() is None, "WATCHDOG_STARTUP_FAILED")
                time.sleep(.01)
            require((attempt / "watchdog-ready.json").exists(), "WATCHDOG_STARTUP_FAILED")
            ready = read_json(attempt / "watchdog-ready.json")
            require(ready["nonce"] == record["nonce"] and process_identity(ready["identity"]["pid"]) == ready["identity"], "WATCHDOG_STARTUP_FAILED")
            from openmm_workflow.stages import Stage
            Stage(attempt).run()
            reason = budget_reason(attempt, read_json(attempt / "status.json"))
            if reason:
                raise WorkflowError(reason)
            require(not (attempt / "cancel.json").exists(), "CANCELED")
            terminal = {"state": "COMPLETED"}
            code = 0
        except Exception as error:
            message = str(error) if isinstance(error, WorkflowError) else "SCIENTIFIC_STAGE_FAILED"
            budget = attempt / "budget.json"
            if budget.exists():
                message = read_json(budget)["error"]
            terminal = {"state": "CANCELED"} if message == "CANCELED" else {"state": "FAILED", "error": message}
            atomic_json(attempt / "error.json", {"error": message})
            code = 1
        if stage_identity is not None:
            atomic_json(attempt / "terminal.json", {**terminal, "identity": stage_identity, "nonce": record["nonce"]})
            with lock(attempt / "state.lock"):
                reconcile(attempt, read_json(attempt / "status.json"))
            if "monitor" in locals():
                try:
                    monitor.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        return code
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
        require(len(raw) <= MAX_REQUEST, "REQUEST_TOO_LARGE")
        request = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        result = Runner(args.root).dispatch(request)
        response = {"ok": True, "result": result}
    except Exception as error:
        response = {"ok": False, "error": str(error) if isinstance(error, WorkflowError) else "INVALID_REQUEST"}
    sys.stdout.buffer.write(canonical(response) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
