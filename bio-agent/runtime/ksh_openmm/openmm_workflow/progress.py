"""Bounded observations, separate from immutable scientific output and authority."""
from __future__ import annotations

from collections import deque
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import time

from .common import atomic_json, canonical, read_json

MAX_SAMPLES = 96
MAX_BYTES = 48 * 1024
MAX_PROTOCOL_BYTES = 16 * 1024
SAMPLE_FIELDS = ("stageStep", "timePs", "temperatureKelvin", "potentialEnergyKjMol",
                 "kineticEnergyKjMol", "volumeNm3")


def degrees_of_freedom(system) -> int:
    """OpenMM StateDataReporter convention for the supported MD integrators."""
    import openmm
    from openmm import unit
    count = sum(3 for i in range(system.getNumParticles())
                if system.getParticleMass(i) > 0 * unit.dalton)
    for i in range(system.getNumConstraints()):
        first, second, _ = system.getConstraintParameters(i)
        if (system.getParticleMass(first) > 0 * unit.dalton
                or system.getParticleMass(second) > 0 * unit.dalton):
            count -= 1
    if any(type(force) is openmm.CMMotionRemover for force in system.getForces()):
        count -= 3
    return count


def temperature_kelvin(state, degrees: int) -> float | None:
    from openmm import unit
    if degrees <= 0:
        return None
    value = (2 * state.getKineticEnergy() / (degrees * unit.MOLAR_GAS_CONSTANT_R)).value_in_unit(unit.kelvin)
    return value if math.isfinite(value) and value >= 0 else None


class LiveProgress:
    def __init__(self, attempt: Path, request: dict, degrees: int):
        self.attempt = attempt
        self.request = request
        self.degrees = degrees
        self.started = time.monotonic()
        self.published = float("-inf")
        self.samples: deque[dict] = deque(maxlen=MAX_SAMPLES)
        self.nonce = read_json(attempt / "status.json")["nonce"]

    def observe(self, number: int, state, values: dict, force: bool = False) -> None:
        sample = {"stageStep": number, **{key: values[key] for key in SAMPLE_FIELDS[1:] if key != "temperatureKelvin"},
                  "temperatureKelvin": temperature_kelvin(state, self.degrees)}
        if self.samples and self.samples[-1]["stageStep"] == number:
            self.samples[-1] = sample
        else:
            self.samples.append(sample)
        now = time.monotonic()
        if not force and now - self.published < 1:
            return
        stage = self.request["stage"]
        result = {"schemaVersion": "ksh.workflow-progress.v1", "workflowId": self.request["workflowId"],
                  "revision": self.request["revision"], "stageId": stage["id"], "attemptId": self.request["attemptId"],
                  "protocolSha256": hashlib.sha256(canonical(stage)).hexdigest(), "workerNonce": self.nonce,
                  "stageStep": number, "totalSteps": stage["parameters"]["dynamics"]["steps"],
                  "targetTemperatureKelvin": stage["parameters"]["dynamics"]["temperatureKelvin"],
                  "temperatureDegreesOfFreedom": self.degrees, "elapsedSeconds": now - self.started,
                  "publishedAtEpochMs": int(time.time() * 1000), "samples": list(self.samples)}
        # Diagnostic publication must not turn a healthy calculation into a
        # failed task. Disk/physics safeguards are enforced independently.
        try:
            if len(canonical(result)) <= MAX_BYTES:
                atomic_json(self.attempt / "live-progress.json", result)
                self.published = now
        except (OSError, ValueError):
            pass


def read_progress(attempt: Path, record: dict) -> dict | None:
    """Accept only an owned worker observation; never change job state on failure."""
    try:
        path = attempt / "live-progress.json"
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_BYTES:
                return None
            raw = stream.read(MAX_BYTES + 1)
        value = json.loads(raw)
        request = read_json(attempt / "request.json")
        stage = request["stage"]
        if (value["schemaVersion"] != "ksh.workflow-progress.v1"
                or any(value[key] != request[source] for key, source in (("workflowId", "workflowId"),
                       ("revision", "revision"), ("attemptId", "attemptId")))
                or value["stageId"] != stage["id"] or value["attemptId"] != attempt.name
                or value["workerNonce"] != record["nonce"]
                or value["protocolSha256"] != hashlib.sha256(canonical(stage)).hexdigest()
                or value["totalSteps"] != stage["parameters"]["dynamics"]["steps"]
                or value["targetTemperatureKelvin"] != stage["parameters"]["dynamics"]["temperatureKelvin"]):
            return None
        for key in ("stageStep", "totalSteps", "publishedAtEpochMs", "temperatureDegreesOfFreedom"):
            if type(value[key]) is not int or value[key] < 0:
                return None
        if (value["stageStep"] > value["totalSteps"] or not _number(value["elapsedSeconds"], nonnegative=True)
                or not isinstance(value["samples"], list) or not 1 <= len(value["samples"]) <= MAX_SAMPLES):
            return None
        previous = -1
        previous_time = -1
        for row in value["samples"]:
            if set(row) != set(SAMPLE_FIELDS) or type(row["stageStep"]) is not int or not previous < row["stageStep"] <= value["stageStep"]:
                return None
            if any(not _number(row[key], nonnegative=key != "potentialEnergyKjMol") for key in SAMPLE_FIELDS[1:]
                   if key != "temperatureKelvin" or row[key] is not None):
                return None
            if row["timePs"] < previous_time:
                return None
            previous = row["stageStep"]
            previous_time = row["timePs"]
        if previous != value["stageStep"]:
            return None
        return {key: value[key] for key in ("schemaVersion", "workflowId", "revision", "stageId", "attemptId",
                "protocolSha256", "stageStep", "totalSteps", "targetTemperatureKelvin", "temperatureDegreesOfFreedom",
                "elapsedSeconds", "publishedAtEpochMs", "samples")}
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return None


def _number(value, nonnegative=False) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and (not nonnegative or value >= 0)


def read_progress_protocol(attempt: Path, progress: dict) -> str | None:
    """Bind diagnostics to existing protocol bytes, without reserializing JSON."""
    try:
        path = attempt / "output/protocol.json"
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_PROTOCOL_BYTES:
                return None
            raw = stream.read(MAX_PROTOCOL_BYTES + 1)
        if (len(raw) > MAX_PROTOCOL_BYTES or hashlib.sha256(raw).hexdigest() != progress["protocolSha256"]
                or json.loads(raw) != read_json(attempt / "request.json")["stage"]):
            return None
        return raw.decode("utf-8")
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return None
