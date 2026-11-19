"""Closed raw tool arguments; no SDK coercion and no approval namespace."""
from __future__ import annotations
import json
from pathlib import Path
from .errors import require, canonical
from .schema import valid_id

CONTRACT_FILE = Path(__file__).resolve().parents[2] / "agent/contracts.json"
INTENT_FIELDS = {"schemaVersion", "domain", "workflowId", "revision", "title", "researchGoal", "profile",
                 "input", "preparation", "system", "minimization", "equilibration", "production", "analysis", "resources"}


def load_contracts() -> dict:
    return json.loads(CONTRACT_FILE.read_text())


def validate_call(name: str, arguments: dict) -> dict:
    contracts = load_contracts()
    require(type(name) is str and name in contracts["tools"], "INVALID_OPERATION")
    require(type(arguments) is dict and set(arguments) == set(contracts["tools"][name]["parameters"]["required"]), "INVALID_ARGUMENTS")
    require(len(canonical(arguments)) <= 2 * 1024 * 1024, "REQUEST_TOO_LARGE")
    for key, value in arguments.items():
        if key in {"workflowId", "sourceWorkflowId", "sourceAttemptId", "stageId"}:
            require(valid_id(value), "INVALID_ARGUMENTS")
        elif key == "revision":
            require(type(value) is int and 1 <= value <= 2147483647, "INVALID_ARGUMENTS")
        elif key == "offset":
            require(type(value) is int and 0 <= value <= 2147483647, "INVALID_ARGUMENTS")
        elif key == "limit":
            require(type(value) is int and 1 <= value <= 100, "INVALID_ARGUMENTS")
        elif key == "path":
            require(type(value) is str and 0 < len(value) <= 1024, "INVALID_ARGUMENTS")
        elif key == "intent":
            require(type(value) is dict and set(value) == INTENT_FIELDS, "SCIENTIFIC_DECISION_REQUIRED")
    return arguments
