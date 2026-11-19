"""Compile explicit decisions to the existing closed scientific protocol."""
from __future__ import annotations
import copy
from decimal import Decimal, InvalidOperation, localcontext
import re
from pathlib import Path
from .chat_contracts import INTENT_FIELDS
from .errors import BioError, require
from .intake import inspect_input
from .schema import valid_hash, validate_protocol

PROFILES = {"protein", "protein-ligand-external", "small-molecule-water", "water-box", "continuation"}


def steps_from_duration(duration: dict, timestep_fs: int | float) -> int:
    require(type(duration) is dict and set(duration) == {"value", "unit"}, "INVALID_DURATION")
    value = duration["value"]
    require(type(value) is str and len(value) <= 64 and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value) is not None,
            "INVALID_DURATION")
    require(duration["unit"] in {"fs", "ps", "ns"} and type(timestep_fs) in (int, float), "INVALID_DURATION")
    try:
        with localcontext() as context:
            context.prec = 128
            timestep = Decimal(str(timestep_fs))
            require(timestep.is_finite() and timestep > 0, "INVALID_DURATION")
            total = Decimal(value) * {"fs": 1, "ps": 1000, "ns": 1000000}[duration["unit"]]
            require(total > 0, "INVALID_DURATION")
            require(total % timestep == 0, "DURATION_NOT_INTEGER_STEPS")
            steps = total / timestep
            require(1 <= steps <= 10000000000, "INVALID_DURATION")
            return int(steps)
    except (InvalidOperation, ValueError, OverflowError):
        raise BioError("INVALID_DURATION") from None


def compile_intent(project: Path, intent: dict, *, toolchain_sha256: str | None = None) -> dict:
    require(type(intent) is dict and set(intent) == INTENT_FIELDS, "SCIENTIFIC_DECISION_REQUIRED")
    value = copy.deepcopy(intent)
    require(value["schemaVersion"] == "bio.intent.v1" and value["domain"] == "bio-openmm", "INVALID_INTENT")
    require(value["profile"] in PROFILES, "UNSUPPORTED_PROFILE")
    profile = value["profile"]
    preparation, system = value["preparation"], value["system"]
    inputs = []
    if value["input"] is not None:
        entry = value["input"]
        require(type(entry) is dict and set(entry) == {"id", "path", "expectedSha256", "expectedByteSize"}, "INVALID_INPUTS")
        inspected = inspect_input(project, entry["path"])
        require(entry["expectedSha256"] == inspected["sha256"] and type(entry["expectedByteSize"]) is int
                and entry["expectedByteSize"] == inspected["byteSize"], "INPUT_CHANGED")
        require(not inspected["blockers"], inspected["blockers"][0] if inspected["blockers"] else "INPUT_BLOCKED")
        inputs = [{"id": entry["id"], **{key: inspected[key] for key in ("role", "path", "sha256", "byteSize")}}]
    require(bool(inputs) == (profile in {"protein", "protein-ligand-external"}), "PREPARATION_INPUT_COUNT")
    if profile == "continuation":
        require(preparation is None and system is None and not inputs, "INVALID_CONTINUATION")
    else:
        require(type(preparation) is dict and type(system) is dict, "SCIENTIFIC_DECISION_REQUIRED")
        expected_mode = {"protein": None, "protein-ligand-external": "amber-protein-ligand",
                         "small-molecule-water": "amber-small-molecule", "water-box": "water-box"}[profile]
        require(preparation.get("mode") == expected_mode, "PROFILE_PREPARATION_MISMATCH")
        if profile in {"protein-ligand-external", "small-molecule-water"}:
            require("toolchainSha256" not in preparation and valid_hash(toolchain_sha256), "AMBER_TOOLCHAIN_REQUIRED")
            preparation["toolchainSha256"] = toolchain_sha256
    resources = value["resources"]
    require(type(resources) is dict and {"cpuThreads", "maxOutputBytes", "wallTimeSeconds"} <= set(resources)
            <= {"cpuThreads", "maxOutputBytes", "wallTimeSeconds", "execution"}
            and type(resources["wallTimeSeconds"]) is dict, "INVALID_RESOURCES")
    execution = resources.get("execution", {"platform": "CPU"})
    require(type(execution) is dict and execution.get("platform") in {"CPU", "CUDA"}, "INVALID_RESOURCES")
    require(set(execution) == ({"platform"} if execution["platform"] == "CPU" else {"platform", "deviceIndex", "precision"}), "INVALID_RESOURCES")
    stages = []
    def add(kind: str, parameters: dict) -> None:
        require(kind in resources["wallTimeSeconds"], "SCIENTIFIC_DECISION_REQUIRED")
        stages.append({"id": kind, "title": kind, "kind": kind,
                       "dependsOn": [stages[-1]["id"]] if stages else [],
                       "resources": {"location": "local", **({"platform": "CPU"} if kind in {"prepare", "minimize", "analysis"} else execution), "cpuThreads": resources["cpuThreads"],
                                     "maxOutputBytes": resources["maxOutputBytes"], "wallTimeSeconds": resources["wallTimeSeconds"][kind]},
                       "parameters": parameters})
    if preparation is not None:
        add("prepare", {"preparation": preparation, "system": system})
    if value["minimization"] is not None:
        add("minimize", {"minimization": value["minimization"]})
    dynamics_count = 0
    for kind in ("equilibration", "production"):
        wrapper = value[kind]
        if wrapper is None:
            continue
        require(type(wrapper) is dict and set(wrapper) == {"duration", "dynamics", "restart"}, "INVALID_DYNAMICS")
        dynamics = wrapper["dynamics"]
        require(type(dynamics) is dict and not ({"steps", "benchmark"} & set(dynamics)), "INVALID_DYNAMICS")
        require(dynamics.get("initializeVelocities") is (profile != "continuation" and dynamics_count == 0),
                "CONTINUATION_VELOCITY_RESET_FORBIDDEN")
        require(profile == "continuation" or value["minimization"] is not None, "MINIMIZATION_REQUIRED")
        require("timestepFs" in dynamics, "SCIENTIFIC_DECISION_REQUIRED")
        dynamics["steps"] = steps_from_duration(wrapper["duration"], dynamics["timestepFs"])
        add("equilibrate" if kind == "equilibration" else kind, {"dynamics": dynamics, "restart": wrapper["restart"]})
        dynamics_count += 1
    add("validate", {"validation": {"finiteEnergy": True, "finiteCoordinates": True}})
    if value["analysis"] is not None:
        add("analysis", {"analysis": value["analysis"]})
    require(set(resources["wallTimeSeconds"]) == {stage["id"] for stage in stages}, "INVALID_RESOURCES")
    protocol = {"schemaVersion": "bio.workflow.v1", "domain": "bio-openmm", "workflowId": value["workflowId"],
                "revision": value["revision"], "title": value["title"], "decisionSummary": value["researchGoal"],
                "inputs": inputs, "stages": stages}
    return validate_protocol(protocol)
