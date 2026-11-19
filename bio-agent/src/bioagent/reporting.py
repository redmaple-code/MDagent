"""Content-addressed records and reports from independently reverified evidence."""
from __future__ import annotations
import csv
import hashlib
import math
import os
from pathlib import Path
from .artifacts import verify_result, verified_manifest
from .errors import canonical, read_json, require, safe_file, sha256_file


def write_record(project: Path, value: object, *, markdown: bool = False) -> dict:
    data = value.encode("utf-8") if markdown else canonical(value)
    digest = hashlib.sha256(data).hexdigest()
    root = safe_file(project, "chat/records")
    root.mkdir(parents=True, exist_ok=True)
    path = safe_file(root, digest + (".md" if markdown else ".json"))
    try:
        with path.open("xb") as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
    except FileExistsError:
        require(sha256_file(path) == digest, "RECORD_CHANGED")
    return {"path": str(path.absolute()), "sha256": digest, "byteSize": len(data)}


def verified_workflow(controller, workflow_id: str) -> dict:
    value = controller.get(workflow_id)
    accepted = []
    for attempt in value["attempts"]:
        if attempt["state"] != "COMPLETED":
            continue
        root = safe_file(controller.runtime.root, attempt["attemptId"])
        request = read_json(controller._request_path(attempt["attemptId"]))
        proof = verify_result(root, request, {"state": "COMPLETED"})
        require(proof == attempt.get("acceptance"), "ACCEPTANCE_CHANGED")
        manifest = verified_manifest(root / "output/manifest.json", proof["manifestSha256"])
        accepted.append({"attempt": attempt, "proof": proof, "manifest": manifest, "root": root})
    return {"value": value, "accepted": accepted}


def _csv_summary(path: Path, fields: tuple[str, ...]) -> dict:
    aggregates, count = {}, 0
    with path.open(newline="") as stream:
        reader = csv.DictReader(stream)
        require(set(fields) <= set(reader.fieldnames or []), "INVALID_ANALYSIS")
        for row in reader:
            count += 1
            for key in fields:
                if row[key] == "":
                    continue
                value = float(row[key]); require(math.isfinite(value), "NONFINITE_REPORT")
                entry = aggregates.setdefault(key, {"min": value, "max": value, "first": value, "last": value})
                entry.update(min=min(entry["min"], value), max=max(entry["max"], value), last=value)
    require(count > 0, "INVALID_ANALYSIS")
    return {"rows": count, "columns": aggregates}


def build_report(controller, workflow_id: str) -> dict:
    verified = verified_workflow(controller, workflow_id)
    value, accepted = verified["value"], verified["accepted"]
    require(bool(accepted), "RESULT_NOT_ACCEPTED")
    facts, sources = [], []
    for item in accepted:
        proof, manifest, root = item["proof"], item["manifest"], item["root"]
        stage_id = item["attempt"]["stageId"]
        request = read_json(controller._request_path(item["attempt"]["attemptId"]))
        stage = request["stage"]
        diagnostics = read_json(root / "output/diagnostics.json")
        fact = {"stageId": stage_id, "kind": stage["kind"], "modelId": proof["modelId"],
                "particles": proof["particles"], "timePs": proof["timePs"], "stepCount": proof["stepCount"]}
        if stage["kind"] == "analysis":
            fact["analysis"] = diagnostics
            records = {entry["path"]: entry for entry in manifest["files"]}
            for name, fields in (("analysis.csv", ("frame", "timePs", "rmsdAngstrom", "radiusOfGyrationAngstrom")),
                                 ("rmsf.csv", ("atomIndex", "rmsfAngstrom"))):
                if name in records:
                    require(records[name]["role"] == "analysis", "INVALID_ANALYSIS")
                    fact[name] = _csv_summary(safe_file(root / "output", name), fields)
        else:
            final = diagnostics.get("final", diagnostics)
            for key in ("potentialEnergyKjMol", "kineticEnergyKjMol", "volumeNm3"):
                if key in final: fact[key] = final[key]
        facts.append(fact)
        sources.append({"stageId": stage_id, "attemptId": item["attempt"]["attemptId"],
                        "path": str(root / "output/manifest.json"), "sha256": proof["manifestSha256"],
                        "files": manifest["files"]})
    external = any(stage["parameters"].get("preparation", {}).get("mode") == "amber-protein-ligand"
                   for stage in value["protocol"]["stages"])
    report = {"schemaVersion": "bio.report.v1", "workflowId": workflow_id, "revision": value["revision"],
              "specSha256": value["specSha256"], "state": value["state"],
              "partial": value["state"] != "COMPLETED" or len(accepted) != len(value["stages"]),
              "scientificConvergenceAssessed": False, "facts": facts, "sources": sources,
              "units": {"time": "ps", "length": "Å", "energy": "kJ/mol", "volume": "nm³"},
              "interpretation": "工程与数值检查通过；采样是否收敛尚未评估。" +
                  (" 配体位于蛋白外部，未进行对接或结合判定。" if external else "")}
    json_ref = write_record(controller.project, report)
    lines = [f"# Bio 结果：{workflow_id}", "", report["interpretation"],
             f"当前状态：{report['state']}；部分结果：{'是' if report['partial'] else '否'}。", ""]
    for fact in facts:
        lines.append(f"- {fact['stageId']}：{fact['particles']} 个粒子，时间 {fact['timePs']} ps，累计 {fact['stepCount']} 步。")
        if "analysis" in fact:
            lines.extend(["", "分析条件与指标：", "```json", canonical({k:v for k,v in fact.items() if k.endswith('.csv') or k=='analysis'}).decode(), "```"])
    lines.extend(["", f"完整事实与逐文件来源：{json_ref['path']}", f"SHA-256：{json_ref['sha256']}"])
    markdown_ref = write_record(controller.project, "\n".join(lines) + "\n", markdown=True)
    return {**report, "reports": {"json": json_ref, "markdown": markdown_ref}}
