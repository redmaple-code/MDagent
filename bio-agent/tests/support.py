"""Hand-declared engineering protocols; no production approval utilities."""
from pathlib import Path
import copy
import shutil
import time
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path(os.environ.get("BIO_TEST_PYTHON", sys.executable)).resolve()
FIXTURE = ROOT / "runtime/ksh_openmm/tests/fixtures/leu-ser.pdb"


def stage(kind: str, stage_id: str, depends: list[str]) -> dict:
    parameters = {
        "prepare": {"preparation": {"forceField": "amber14", "addHydrogens": False, "ph": 7,
                                     "solvent": {"mode": "as-is"}},
                    "system": {"nonbondedMethod": "NoCutoff", "nonbondedCutoffNm": 1,
                               "constraints": "HBonds", "rigidWater": True, "removeCMMotion": True}},
        "minimize": {"minimization": {"toleranceKjMolNm": 10, "maxIterations": 100}},
        "production": {"dynamics": {"ensemble": "NVT", "temperatureKelvin": 300, "timestepFs": 1,
                         "frictionPerPs": 1, "steps": 20, "seed": 713, "initializeVelocities": True,
                         "reporterInterval": 5, "checkpointInterval": 10, "segmentFrames": 2}, "restart": "state"},
        "validate": {"validation": {"finiteEnergy": True, "finiteCoordinates": True}},
        "analysis": {"analysis": {"selection": "protein and name CA", "alignmentSelection": "protein and backbone",
                                  "pbc": "none", "stride": 1, "metrics": ["rmsd", "rmsf", "radiusOfGyration"]}},
    }
    budgets = {"prepare": 180, "minimize": 120, "production": 60, "validate": 60, "analysis": 60}
    return {"id": stage_id, "title": stage_id, "kind": kind, "dependsOn": depends,
            "resources": {"location": "local", "platform": "CPU", "cpuThreads": 1,
                          "wallTimeSeconds": budgets[kind], "maxOutputBytes": 67108864},
            "parameters": copy.deepcopy(parameters[kind])}


def protein_protocol() -> dict:
    return {"schemaVersion": "bio.workflow.v1", "domain": "bio-openmm", "workflowId": "protein-test",
            "revision": 1, "title": "LEU-SER engineering fixture",
            "decisionSummary": "Explicit 33-atom Amber14 fixture; NoCutoff; CPU 1 fs 20 steps. Engineering only.",
            "inputs": [{"id": "protein", "role": "structure", "path": "protein.pdb", "byteSize": 2751,
                       "sha256": "c2d834d984b79a27339ea2847cf3b5fe1f0e15ce848f8e2ce4ae73269455cecb"}],
            "stages": [stage("prepare", "prepare", []), stage("minimize", "minimize", ["prepare"]),
                       stage("production", "production", ["minimize"]), stage("validate", "validate", ["production"]),
                       stage("analysis", "analysis", ["validate"])]}


def water_protocol() -> dict:
    value = protein_protocol()
    value.update(workflowId="water-test", title="TIP3P water engineering fixture", inputs=[])
    value["stages"] = value["stages"][:4]
    value["stages"][0]["parameters"]["preparation"] = {"mode": "water-box", "forceField": "tip3p", "boxSizeNm": 2.2}
    value["stages"][0]["parameters"]["system"]["nonbondedMethod"] = "PME"
    return value


def fake_bindings() -> dict:
    return {"ready": True, "runtimeSha256": "a" * 64, "environmentSha256": "b" * 64, "inputSetSha256": "c" * 64}


def make_project(root: Path) -> None:
    (root / "inputs").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURE, root / "inputs/protein.pdb")


def wait_for_owned_exit(controller, workflow_id: str) -> None:
    """Wait for test-owned writers to exit before temporary fixture cleanup."""
    from bioagent.schema import load_runner
    from bioagent.errors import read_json
    runner = load_runner(); deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        live = False
        for attempt in controller.get(workflow_id)["attempts"]:
            status = read_json(controller.runtime.root / attempt["attemptId"] / "status.json")
            for key in ("identity", "stageIdentity", "watchdogIdentity"):
                identity = status.get(key)
                if identity and runner.process_identity(identity["pid"]) == identity:
                    live = True
        if not live: return
        time.sleep(.05)
    raise AssertionError("test-owned process still active; preserve temporary fixture")
