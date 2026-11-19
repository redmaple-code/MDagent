"""Synthetic integrity fixtures, never scientific acceptance evidence."""
import hashlib
import json
from pathlib import Path

from bioagent.errors import canonical
from tests.support import FIXTURE, protein_protocol


def write_result(attempt: Path) -> tuple[dict, dict]:
    output = attempt / "output"; output.mkdir(parents=True)
    protocol = protein_protocol(); stage = protocol["stages"][0]
    request = {"schemaVersion": "ksh.stage-request.v1", "workflowId": "protein-test", "revision": 1,
               "attemptId": attempt.name, "stage": stage,
               "inputs": [{"role": "structure", "path": "protein.pdb", "sha256": protocol["inputs"][0]["sha256"]}],
               "dependencyBundles": []}
    files = {
        "topology.pdb": FIXTURE.read_bytes(), "view-structure.pdb": FIXTURE.read_bytes(),
        "system.xml": ('<System><Particles>' + '<Particle mass="12"/>' * 33 + '</Particles></System>').encode(),
        "state.xml": ('<State time="0" stepCount="0"><PeriodicBoxVectors><A x="2" y="0" z="0"/>'
                      '<B x="0" y="2" z="0"/><C x="0" y="0" z="2"/></PeriodicBoxVectors><Energies '
                      'PotentialEnergy="-10" KineticEnergy="0"/><Positions>' + '<Position x="0" y="0" z="0"/>' * 33
                      + '</Positions><Velocities>' + '<Velocity x="0" y="0" z="0"/>' * 33 + '</Velocities></State>').encode(),
        "integrator.xml": b'<Integrator type="VerletIntegrator"/>',
        "protocol.json": canonical(stage),
        "environment.json": canonical({"pythonVersion": "3.12.11", "openmmVersion": "8.3.0"}),
        "diagnostics.json": canonical({"particles": 33, "timePs": 0, "potentialEnergyKjMol": -10,
                                       "kineticEnergyKjMol": 0, "volumeNm3": 8, "finite": True}),
    }
    model_id = hashlib.sha256(canonical({"system": hashlib.sha256(files["system.xml"]).hexdigest(),
        "topology": hashlib.sha256(files["topology.pdb"]).hexdigest(),
        "preparation": stage["parameters"]["preparation"], "input": protocol["inputs"][0]["sha256"]})).hexdigest()
    files["model.json"] = canonical({"modelId": model_id, "particles": 33, "minimized": False,
                                    "preparation": stage["parameters"]["preparation"], "system": stage["parameters"]["system"]})
    roles = {"topology.pdb": "topology", "view-structure.pdb": "view-structure", "system.xml": "system",
             "state.xml": "state", "integrator.xml": "integrator", "protocol.json": "protocol",
             "environment.json": "environment", "diagnostics.json": "report", "model.json": "model"}
    records = []
    for name, content in files.items():
        (output / name).write_bytes(content)
        records.append({"path": name, "role": roles[name], "sha256": hashlib.sha256(content).hexdigest(), "byteSize": len(content)})
    manifest = {"schemaVersion": "ksh.stage-result.v1", "workflowId": "protein-test", "revision": 1,
                "stageId": "prepare", "attemptId": attempt.name, "modelId": model_id,
                "stateId": hashlib.sha256(files["state.xml"]).hexdigest(),
                "protocolSha256": hashlib.sha256(files["protocol.json"]).hexdigest(),
                "provenance": {"pythonVersion": "3.12.11", "openmmVersion": "8.3.0"},
                "checkpointCompatibility": None, "parentBundles": [],
                "acceptance": {"engineering": True, "scientificConvergence": False}, "files": records}
    (output / "manifest.json").write_bytes(canonical(manifest))
    return request, {"state": "COMPLETED", "attemptId": attempt.name}


def refresh_file(attempt: Path, name: str) -> None:
    output = attempt / "output"; manifest = json.loads((output / "manifest.json").read_text())
    content = (output / name).read_bytes()
    for record in manifest["files"]:
        if record["path"] == name:
            record.update(sha256=hashlib.sha256(content).hexdigest(), byteSize=len(content))
    if name == "state.xml": manifest["stateId"] = hashlib.sha256(content).hexdigest()
    (output / "manifest.json").write_bytes(canonical(manifest))
