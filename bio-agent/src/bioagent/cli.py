"""Ordinary operations cannot review/approve; human review is interactive."""
from __future__ import annotations

import argparse
import getpass
import json
from pathlib import Path
import sys
import time

from .controller import Controller
from .errors import BioError, canonical, read_json, require, safe_file
from .preflight import preflight
from .schema import protocol_sha256, validate_protocol


def dispatch(controller: Controller, operation: str, value: dict) -> dict:
    fields = {"preflight": {"protocol"}, "plan": {"protocol"}, "get": {"workflowId"}, "list": {"offset", "limit"},
              "submit": {"workflowId", "revision"}, "artifacts": {"workflowId", "stageId", "offset", "limit"},
              "cancel": {"workflowId"}, "continue": {"sourceWorkflowId", "sourceAttemptId", "protocol"}}
    require(operation in fields, "INVALID_OPERATION")
    require(isinstance(value, dict) and set(value) == fields[operation], "INVALID_REQUEST")
    if operation == "preflight": return controller._preflight(value["protocol"])
    if operation == "plan": return controller.plan(value["protocol"])
    if operation == "get": return controller.get(value["workflowId"])
    if operation == "list": return controller.store.list(value["offset"], value["limit"])
    if operation == "submit": return controller.submit(value["workflowId"], value["revision"])
    if operation == "cancel": return controller.cancel(value["workflowId"])
    if operation == "continue": return controller.continue_plan(value["sourceWorkflowId"], value["sourceAttemptId"], value["protocol"])
    return controller.artifacts(value["workflowId"], value["stageId"], value["offset"], value["limit"])


def review(controller: Controller, workflow_id: str, revision: int, input_stream=sys.stdin, output_stream=sys.stdout, *, format: str = "json") -> dict:
    require(input_stream.isatty() and output_stream.isatty(), "INTERACTIVE_REVIEW_REQUIRED")
    value = controller.get(workflow_id, revision)
    bindings = controller.bindings_for(value)
    require(bindings["ready"] and all(bindings[k] == value["bindings"][k] for k in value["bindings"]), "GRANT_CHANGED")
    require(format in {"json", "compact"}, "INVALID_REVIEW_FORMAT")
    if format == "compact":
        from .review_view import review_view
        output_stream.write(review_view(controller, value, bindings)["text"])
    else:
        output_stream.write(json.dumps({"protocol": value["protocol"], "specSha256": value["specSha256"],
                                       "reference": value.get("reference"), "bindings": value["bindings"],
                                       "environment": bindings["environment"]}, ensure_ascii=False, indent=2))
    output_stream.write("\nType the full specSha256 to approve these exact conditions: "); output_stream.flush()
    require(input_stream.readline().strip() == value["specSha256"], "REVIEW_REJECTED")
    return controller.store.approve(workflow_id, revision, value["specSha256"], getpass.getuser())


def main() -> int:
    parser = argparse.ArgumentParser(description="Bio OpenMM compute-side core")
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--execution-target-file", type=Path)
    sub = parser.add_subparsers(dest="operation", required=True)
    for name in ("preflight", "plan"):
        p = sub.add_parser(name); p.add_argument("protocol", type=Path)
    p = sub.add_parser("list"); p.add_argument("--offset", type=int, default=0); p.add_argument("--limit", type=int, default=100)
    for name in ("get", "submit", "review", "tick", "cancel"):
        p = sub.add_parser(name); p.add_argument("workflow_id")
        if name in {"submit", "review"}: p.add_argument("--revision", required=True, type=int)
        if name == "review": p.add_argument("--format", choices=("json", "compact"), default="json")
        if name == "submit": p.add_argument("--wait", action="store_true")
    p = sub.add_parser("artifacts"); p.add_argument("workflow_id"); p.add_argument("stage_id")
    p.add_argument("--offset", type=int, default=0); p.add_argument("--limit", type=int, default=100)
    p = sub.add_parser("continue"); p.add_argument("source_workflow_id"); p.add_argument("source_attempt_id"); p.add_argument("protocol", type=Path)
    args = parser.parse_args()
    try:
        target = read_json(args.execution_target_file) if args.execution_target_file else None
        marker = safe_file(args.project, "execution-target.json")
        if target is None and marker.exists(): target = read_json(marker)["target"]
        if args.operation == "preflight":
            from .execution_target import verify_target, check_resources
            protocol = validate_protocol(read_json(args.protocol))
            if target:
                verify_target(target, args.project, args.python); check_resources(target, protocol)
            result = preflight(args.project, protocol, args.python)
            if target: result["executionTargetSha256"] = verify_target(target, args.project, args.python)["executionTargetSha256"]
        else:
            controller = Controller(args.project, args.python, target)
            if args.operation == "review": result = review(controller, args.workflow_id, args.revision, format=args.format)
            elif args.operation == "tick": result = controller.tick(args.workflow_id)
            else:
                if args.operation == "continue": value = {"sourceWorkflowId": args.source_workflow_id, "sourceAttemptId": args.source_attempt_id, "protocol": read_json(args.protocol)}
                elif args.operation == "plan": value = {"protocol": read_json(args.protocol)}
                elif args.operation == "list": value = {"offset": args.offset, "limit": args.limit}
                else:
                    value = {"workflowId": args.workflow_id}
                    if args.operation == "submit": value["revision"] = args.revision
                    if args.operation == "artifacts": value.update(stageId=args.stage_id, offset=args.offset, limit=args.limit)
                result = dispatch(controller, args.operation, value)
                if args.operation == "submit" and args.wait:
                    budget = sum(s["resources"]["wallTimeSeconds"] for s in result["protocol"]["stages"]) + 120
                    deadline = time.monotonic() + budget
                    while result["state"] in {"RUNNING", "READY"} and time.monotonic() < deadline:
                        time.sleep(.2); result = controller.tick(args.workflow_id)
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
        return 0
    except BioError as error:
        print(json.dumps({"ok": False, "error": error.code})); return 1


if __name__ == "__main__":
    raise SystemExit(main())
