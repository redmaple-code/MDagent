"""Closed biological workflow protocol; no approval or executable fields."""
from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
import re
import sys

from .errors import BioError, canonical, require, safe_file
from .provenance import runtime_root, verify_runtime


def load_runner():
    verify_runtime()
    directory = str(runtime_root())
    if directory not in sys.path:
        sys.path.insert(0, directory)
    module = importlib.import_module("remote_runner")
    require(Path(module.__file__).resolve() == (runtime_root() / "remote_runner.py").resolve(), "INVALID_RUNTIME_PATH")
    return module


def valid_id(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,127}", value) is not None


def valid_hash(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def validate_protocol(value: dict) -> dict:
    require(isinstance(value, dict) and set(value) == {"schemaVersion", "domain", "workflowId", "revision",
            "title", "decisionSummary", "inputs", "stages"}, "INVALID_PROTOCOL")
    require(value["schemaVersion"] == "bio.workflow.v1" and value["domain"] == "bio-openmm", "INVALID_PROTOCOL")
    require(valid_id(value["workflowId"]) and type(value["revision"]) is int and value["revision"] >= 1, "INVALID_PROTOCOL")
    for key, limit in (("title", 1024), ("decisionSummary", 16384)):
        require(isinstance(value[key], str) and 0 < len(value[key].strip()) <= limit, "SCIENTIFIC_DECISION_REQUIRED")
    data = canonical(value)
    require(len(data) <= 2 * 1024 * 1024, "INVALID_PROTOCOL")
    require(isinstance(value["inputs"], list) and len(value["inputs"]) <= 32, "INVALID_INPUTS")
    seen = set()
    for entry in value["inputs"]:
        require(isinstance(entry, dict) and set(entry) == {"id", "role", "path", "sha256", "byteSize"}, "INVALID_INPUTS")
        require(valid_id(entry["id"]) and entry["id"] not in seen and entry["role"] == "structure"
                and valid_hash(entry["sha256"]) and type(entry["byteSize"]) is int
                and 0 <= entry["byteSize"] <= 64 * 1024 * 1024, "INVALID_INPUTS")
        safe_file(Path("/controlled-inputs"), entry["path"])
        seen.add(entry["id"])
    stages = value["stages"]
    require(isinstance(stages, list) and 1 <= len(stages) <= 64, "INVALID_STAGE")
    runner = load_runner()
    ids = set()
    for stage in stages:
        require(isinstance(stage, dict) and valid_id(stage.get("id")) and stage["id"] not in ids
                and stage["id"] not in seen, "INVALID_STAGE")
        ids.add(stage["id"])
        resources = stage.get("resources", {})
        require(isinstance(resources, dict) and resources.get("location") == "local"
                and resources.get("platform") in {"CPU", "CUDA"}
                and not ({"targetId", "targetRevision"} & set(resources)), "COMPUTE_RESOURCES_REQUIRED")
        if resources["platform"] == "CPU":
            require(not ({"deviceIndex", "precision"} & set(resources)), "INVALID_RESOURCES")
        require(stage.get("kind") not in {"prepare", "analysis"} or resources["platform"] == "CPU", "PREPARATION_ANALYSIS_CPU_REQUIRED")
        request = {"schemaVersion": "ksh.stage-request.v1", "workflowId": value["workflowId"],
                   "revision": value["revision"], "attemptId": "preflight", "stage": stage,
                   "inputs": [{"role": e["role"], "path": e["path"], "sha256": e["sha256"]} for e in value["inputs"]]
                             if stage.get("kind") == "prepare" else [], "dependencyBundles": []}
        try:
            runner.validate_request(request, "preflight")
        except runner.WorkflowError as error:
            raise BioError(str(error)) from None
        except (TypeError, KeyError, AttributeError):
            raise BioError("INVALID_STAGE") from None
        require(isinstance(stage["title"], str) and 0 < len(stage["title"]) <= 2048, "INVALID_STAGE")
    graph = {stage["id"]: stage["dependsOn"] for stage in stages}
    require(all(dep in ids and dep != key for key, deps in graph.items() for dep in deps), "INVALID_DEPENDENCIES")
    active, visited = set(), set()

    def visit(key: str) -> None:
        require(key not in active, "INVALID_DEPENDENCIES")
        if key in visited:
            return
        active.add(key)
        for dep in graph[key]:
            visit(dep)
        active.remove(key)
        visited.add(key)

    for key in graph:
        visit(key)
    by_id = {stage["id"]: stage for stage in stages}

    def lineage_has(key: str, kinds: set[str]) -> bool:
        return by_id[key]["kind"] in kinds or any(lineage_has(parent, kinds) for parent in graph[key])

    for stage in stages:
        parents = stage["dependsOn"]; kind = stage["kind"]
        require(len(parents) <= 1, "SINGLE_MODEL_PARENT_REQUIRED")
        if kind == "prepare":
            require(not parents, "INVALID_DEPENDENCIES")
            mode = stage["parameters"]["preparation"].get("mode")
            expected_inputs = 0 if mode in {"water-box", "amber-small-molecule"} else 1
            require(len(value["inputs"]) == expected_inputs, "PREPARATION_INPUT_COUNT")
        elif parents:
            if kind in {"production", "equilibrate"}:
                require(lineage_has(parents[0], {"minimize", "production", "equilibrate"}), "MINIMIZATION_REQUIRED")
            if kind == "analysis":
                require(lineage_has(parents[0], {"production", "equilibrate", "analysis"}), "TRAJECTORY_REQUIRED")
    return json.loads(data)


def protocol_sha256(value: dict) -> str:
    return hashlib.sha256(canonical(validate_protocol(value))).hexdigest()
