"""Independent hash, lineage, XML, numeric and analysis result acceptance."""
from __future__ import annotations

import csv
import hashlib
import math
import struct
from pathlib import Path
import xml.etree.ElementTree as ET

from .errors import BioError, canonical, read_json, require, safe_file, sha256_file
from .schema import valid_hash


def verified_manifest(path: Path, expected_hash: str | None = None, *, recovery: bool = False) -> dict:
    require(path.is_file() and not path.is_symlink(), "RESULT_NOT_ACCEPTED")
    if expected_hash is not None: require(sha256_file(path) == expected_hash, "HASH_MISMATCH")
    value = read_json(path)
    require(value.get("schemaVersion") == "ksh.stage-result.v1", "INVALID_MANIFEST")
    if recovery:
        require(value.get("acceptance") == {"engineering": False, "recovery": True}, "INVALID_RECOVERY")
    else:
        require(value.get("acceptance") == {"engineering": True, "scientificConvergence": False}, "RESULT_NOT_ACCEPTED")
    files = value.get("files")
    require(isinstance(files, list) and 1 <= len(files) <= 100000, "INVALID_MANIFEST")
    seen = set()
    for entry in files:
        require(isinstance(entry, dict) and set(entry) == {"path", "role", "sha256", "byteSize"}, "INVALID_MANIFEST")
        require(valid_hash(entry["sha256"]) and type(entry["byteSize"]) is int and entry["byteSize"] >= 0
                and entry["path"] not in seen, "INVALID_MANIFEST")
        seen.add(entry["path"])
        target = safe_file(path.parent, entry["path"])
        require(target.is_file() and target.stat().st_size == entry["byteSize"] and sha256_file(target) == entry["sha256"], "HASH_MISMATCH")
    return value


def finite(value: object) -> float:
    require(type(value) in (int, float, str), "NONFINITE_STATE")
    try: result = float(value)
    except (ValueError, TypeError): raise BioError("NONFINITE_STATE") from None
    require(math.isfinite(result), "NONFINITE_STATE")
    return result


def state_values(path: Path) -> dict:
    try: state = ET.parse(path).getroot()
    except ET.ParseError: raise BioError("INVALID_STATE") from None
    require(state.tag == "State", "INVALID_STATE")
    time_ps = finite(state.get("time")); require(time_ps >= 0, "INVALID_STATE")
    positions, velocities, box = state.find("Positions"), state.find("Velocities"), state.find("PeriodicBoxVectors")
    require(positions is not None and velocities is not None and box is not None, "INVALID_STATE")
    for entries in (positions, velocities, box):
        for row in entries:
            require(set(row.attrib) == {"x", "y", "z"}, "INVALID_STATE")
            for value in row.attrib.values(): finite(value)
    require(len(positions) == len(velocities) and len(box) == 3, "PARTICLE_COUNT_MISMATCH")
    a, b, c = [[finite(row.get(k)) for k in ("x", "y", "z")] for row in box]
    volume = a[0] * (b[1]*c[2]-b[2]*c[1]) - a[1]*(b[0]*c[2]-b[2]*c[0]) + a[2]*(b[0]*c[1]-b[1]*c[0])
    energy = state.find("Energies"); require(energy is not None and len(energy.attrib) == 2, "INVALID_STATE")
    energies = {key: finite(value) for key, value in energy.attrib.items()}
    require(any("kinetic" in key.lower() for key in energies) and any("potential" in key.lower() for key in energies), "INVALID_STATE")
    try: steps = int(state.get("stepCount"))
    except (ValueError, TypeError): raise BioError("INVALID_STATE") from None
    require(steps >= 0, "INVALID_STATE")
    return {"particles": len(positions), "timePs": time_ps, "stepCount": steps, "volumeNm3": volume,
            "potentialEnergyKjMol": next(v for k,v in energies.items() if "potential" in k.lower()),
            "kineticEnergyKjMol": next(v for k,v in energies.items() if "kinetic" in k.lower())}


def _numeric_tree(value: object) -> None:
    if type(value) in (int, float): finite(value)
    elif isinstance(value, dict):
        for item in value.values(): _numeric_tree(item)
    elif isinstance(value, list):
        for item in value: _numeric_tree(item)


def _close(actual: object, expected: object, code: str) -> None:
    a, b = finite(actual), finite(expected)
    require(abs(a-b) <= max(1e-8, abs(b)*1e-8), code)


def _validation_observation(output: Path, files: dict, inherited: dict) -> dict:
    require("validation-state.xml" in files, "VALIDATION_OBSERVATION_MISSING")
    path = output / "validation-state.xml"
    observation = state_values(path)
    for key in ("particles", "stepCount", "timePs", "volumeNm3"):
        _close(observation[key], inherited[key], "VALIDATION_STATE_MISMATCH")
    original = ET.parse(output / "state.xml").getroot(); evaluated = ET.parse(path).getroot()
    for tag in ("Positions", "Velocities", "PeriodicBoxVectors"):
        for first, second in zip(original.find(tag), evaluated.find(tag), strict=True):
            for axis in ("x", "y", "z"):
                _close(first.get(axis), second.get(axis), "VALIDATION_STATE_MISMATCH")
    return observation


def _xml(path: Path, code: str) -> ET.Element:
    try: return ET.parse(path).getroot()
    except (ET.ParseError, OSError): raise BioError(code) from None


def _model_files(output: Path, parent: Path, stage: dict) -> None:
    require(sha256_file(output / "topology.pdb") == sha256_file(parent / "topology.pdb"), "MODEL_FILE_MISMATCH")
    kind = stage["kind"]
    if kind in {"validate", "analysis"}:
        for name in ("system.xml", "integrator.xml"):
            require(sha256_file(output / name) == sha256_file(parent / name), "MODEL_FILE_MISMATCH")
        parent_files = read_json(parent / "manifest.json")["files"]
        current_files = {f["path"]: f for f in read_json(output / "manifest.json")["files"]}
        for entry in parent_files:
            if entry["role"] in {"trajectory", "trajectory-times", "checkpoint"}:
                require(entry["path"] in current_files and current_files[entry["path"]] == entry, "MODEL_FILE_MISMATCH")
        return
    original = _xml(parent / "system.xml", "INVALID_SYSTEM")
    current = _xml(output / "system.xml", "INVALID_SYSTEM")
    if kind in {"production", "equilibrate"}:
        controls = stage["parameters"]["dynamics"]
        forces = current.find("Forces"); require(forces is not None, "INVALID_SYSTEM")
        barostats = [f for f in forces if f.get("type") == "MonteCarloBarostat"]
        require(len(barostats) == (1 if controls["ensemble"] == "NPT" else 0), "MODEL_FILE_MISMATCH")
        if barostats:
            for attribute, expected in (("pressure", controls["pressureBar"]), ("temperature", controls["temperatureKelvin"]), ("frequency", 25), ("randomSeed", controls["seed"])):
                _close(barostats[0].get(attribute), expected, "MODEL_FILE_MISMATCH")
        for tree in (original, current):
            fs = tree.find("Forces")
            if fs is not None:
                for force in list(fs):
                    if force.get("type") == "MonteCarloBarostat": fs.remove(force)
    require(ET.canonicalize(ET.tostring(original, encoding="unicode"), strip_text=True)
            == ET.canonicalize(ET.tostring(current, encoding="unicode"), strip_text=True), "MODEL_FILE_MISMATCH")
    integrator = _xml(output / "integrator.xml", "INVALID_INTEGRATOR")
    require(integrator.tag == "Integrator", "INVALID_INTEGRATOR")
    if kind == "minimize":
        require(integrator.get("type") == "VerletIntegrator", "MODEL_FILE_MISMATCH")
        _close(integrator.get("stepSize"), .001, "MODEL_FILE_MISMATCH")
    else:
        expected_kind = controls.get("integrator", "LangevinMiddle")
        expected_kind = "Langevin" if expected_kind == "LangevinLegacy" else expected_kind
        require(integrator.get("type") == expected_kind + "Integrator", "MODEL_FILE_MISMATCH")
        for attribute, expected in (("stepSize", controls["timestepFs"] / 1000), ("temperature", controls["temperatureKelvin"]), ("friction", controls["frictionPerPs"]), ("randomSeed", controls["seed"])):
            _close(integrator.get(attribute), expected, "MODEL_FILE_MISMATCH")
    _close(integrator.get("integrationForceGroups"), -1, "MODEL_FILE_MISMATCH")
    _close(integrator.get("constraintTolerance"), 1e-5, "MODEL_FILE_MISMATCH")


def verify_result(attempt: Path, request: dict, status: dict) -> dict:
    require(status.get("state") == "COMPLETED" and request.get("attemptId") == attempt.name, "RESULT_NOT_ACCEPTED")
    output = safe_file(attempt, "output"); path = safe_file(output, "manifest.json")
    manifest = verified_manifest(path)
    stage = request["stage"]
    for key, expected in (("workflowId", request["workflowId"]), ("revision", request["revision"]),
                          ("stageId", stage["id"]), ("attemptId", attempt.name)):
        require(manifest.get(key) == expected, "LINEAGE_MISMATCH")
    files = {entry["path"]: entry for entry in manifest["files"]}
    required = {"model.json", "system.xml", "state.xml", "integrator.xml", "topology.pdb", "view-structure.pdb",
                "protocol.json", "environment.json", "diagnostics.json"}
    require(required <= set(files), "RESULT_NOT_ACCEPTED")
    require(manifest["protocolSha256"] == sha256_file(output / "protocol.json")
            == hashlib.sha256(canonical(stage)).hexdigest() and read_json(output / "protocol.json") == stage, "PROTOCOL_MISMATCH")
    require(manifest["stateId"] == sha256_file(output / "state.xml"), "HASH_MISMATCH")
    require(manifest["parentBundles"] == request["dependencyBundles"], "LINEAGE_MISMATCH")
    model = read_json(output / "model.json")
    require(valid_hash(model.get("modelId")) and model["modelId"] == manifest["modelId"], "MODEL_MISMATCH")
    system = _xml(output / "system.xml", "INVALID_SYSTEM"); particles = system.find("Particles")
    require(system.tag == "System" and particles is not None, "INVALID_SYSTEM")
    atoms = sum(line.startswith(("ATOM  ", "HETATM")) for line in (output / "topology.pdb").read_text().splitlines())
    values = state_values(output / "state.xml")
    require(type(model.get("particles")) is int and model["particles"] == len(particles) == atoms == values["particles"] > 0, "PARTICLE_COUNT_MISMATCH")
    if model.get("system", {}).get("nonbondedMethod") == "PME": require(values["volumeNm3"] > 0, "INVALID_PERIODIC_BOX")
    parents = []
    for entry in request["dependencyBundles"]:
        parent_path = safe_file(attempt / "input", entry["path"])
        parent = verified_manifest(parent_path, entry["sha256"], recovery="recovery" in request)
        parents.append((parent_path, parent))
    kind = stage["kind"]
    if kind == "prepare":
        require(not parents and model.get("minimized") is False, "MODEL_MISMATCH")
        p = stage["parameters"]["preparation"]
        require(model.get("preparation") == p and model.get("system") == stage["parameters"]["system"], "MODEL_MISMATCH")
        identity = {"system": sha256_file(output / "system.xml"), "topology": sha256_file(output / "topology.pdb"), "preparation": p}
        if p.get("mode") == "water-box":
            identity.update(forceFieldFileHashes=model["forceFieldFileHashes"], waterSourceFileHashes=model["waterSourceFileHashes"])
        elif p.get("mode", "").startswith("amber-"):
            report_sha = sha256_file(output / "parameterization-report.json")
            require(model.get("parameterizationSha256") == report_sha
                    and read_json(output / "parameterization-report.json").get("toolchainSha256") == p["toolchainSha256"], "MODEL_MISMATCH")
            identity.update(input=request["inputs"][0]["sha256"] if request["inputs"] else None, parameterization=report_sha)
        else: identity["input"] = request["inputs"][0]["sha256"]
        require(model["modelId"] == hashlib.sha256(canonical(identity)).hexdigest(), "MODEL_MISMATCH")
    else:
        require(len(parents) == 1, "LINEAGE_MISMATCH")
        parent_path, parent = parents[0]
        parent_model = read_json(parent_path.parent / "model.json")
        require(model["modelId"] == parent["modelId"] == parent_model["modelId"], "MODEL_MISMATCH")
        _model_files(output, parent_path.parent, stage)
        if "continuation" not in request and "recovery" not in request:
            require(parent["workflowId"] == request["workflowId"] and parent["revision"] == request["revision"]
                    and parent["stageId"] in stage["dependsOn"], "LINEAGE_MISMATCH")
        if kind in {"minimize", "production", "equilibrate"}:
            require(model.get("parentManifestSha256") == sha256_file(parent_path) and model.get("parentStateId") == parent["stateId"], "MODEL_MISMATCH")
        else: require(model == parent_model, "MODEL_MISMATCH")
        if kind in {"validate", "analysis"}:
            require(sha256_file(output / "state.xml") == parent["stateId"], "LINEAGE_MISMATCH")
        parent_state = state_values(parent_path.parent / "state.xml")
        expected_time = parent_state["timePs"]
        if kind in {"production", "equilibrate"}:
            require(model.get("minimized") is True, "MINIMIZATION_REQUIRED")
            controls = stage["parameters"]["dynamics"]
            steps = controls["steps"]
            if "benchmark" in controls:
                steps = read_json(output / "benchmark.json")["totalIntegratedSteps"]
                require(type(steps) is int and 0 < steps <= controls["steps"], "STEP_COUNT_MISMATCH")
            expected_time += steps * controls["timestepFs"] / 1000
            require(values["stepCount"] == parent_state["stepCount"] + steps, "STEP_COUNT_MISMATCH")
        _close(values["timePs"], expected_time, "SIMULATION_TIME_MISMATCH")
    diagnostics = read_json(output / "diagnostics.json"); _numeric_tree(diagnostics)
    if kind == "analysis":
        _verify_analysis(output, stage, diagnostics)
    else:
        final = diagnostics.get("final", diagnostics)
        require(final.get("particles") == values["particles"] and final.get("finite") is True, "RESULT_NOT_ACCEPTED")
        reported_state = _validation_observation(output, files, values) if kind == "validate" else values
        for key in ("timePs", "potentialEnergyKjMol", "kineticEnergyKjMol", "volumeNm3"):
            _close(final.get(key), reported_state[key], "NUMERICAL_REPORT_MISMATCH")
        if kind in {"production", "equilibrate"}:
            require(diagnostics.get("stageSteps") == steps, "STEP_COUNT_MISMATCH")
    require(read_json(output / "environment.json") == manifest["provenance"], "ENVIRONMENT_MISMATCH")
    return {"schemaVersion": "bio.acceptance.v1", "engineering": True, "numerical": True,
            "scientificConvergenceAssessed": False, "manifestSha256": sha256_file(path), "modelId": model["modelId"],
            "protocolSha256": manifest["protocolSha256"], "particles": values["particles"], "timePs": values["timePs"],
            "stepCount": values["stepCount"], "fileCount": len(files)}


def _dcd_frames(path: Path) -> tuple[int, int]:
    """Validate the little-endian OpenMM DCD record layout, including payloads."""
    with path.open("rb") as stream:
        def record() -> bytes | None:
            marker = stream.read(4)
            if not marker: return None
            require(len(marker) == 4, "INVALID_TRAJECTORY")
            size = struct.unpack("<i", marker)[0]
            require(0 < size <= 64 * 1024 * 1024, "INVALID_TRAJECTORY")
            body = stream.read(size)
            require(len(body) == size and stream.read(4) == marker, "INVALID_TRAJECTORY")
            return body
        header = record(); require(header is not None and len(header) == 84 and header[:4] == b"CORD", "INVALID_TRAJECTORY")
        expected = struct.unpack_from("<i", header, 4)[0]
        box = struct.unpack_from("<i", header, 44)[0]
        require(expected > 0 and box in (0, 1), "INVALID_TRAJECTORY")
        comments = record(); atoms = record()
        require(comments is not None and atoms is not None and len(atoms) == 4, "INVALID_TRAJECTORY")
        count = struct.unpack("<i", atoms)[0]; require(count > 0, "INVALID_TRAJECTORY")
        for _ in range(expected):
            if box:
                cell = record(); require(cell is not None and len(cell) == 48, "INVALID_TRAJECTORY")
                for number in struct.unpack("<6d", cell): finite(number)
            for _ in range(3):
                axis = record(); require(axis is not None and len(axis) == count * 4, "INVALID_TRAJECTORY")
                for number in struct.unpack("<" + str(count) + "f", axis): finite(number)
        require(stream.read(1) == b"", "INVALID_TRAJECTORY")
        return expected, count


def _verify_analysis(output: Path, stage: dict, diagnostics: dict) -> None:
    controls = stage["parameters"]["analysis"]
    require(diagnostics.get("configuration") == controls and diagnostics.get("convergenceAssessed") is False, "INVALID_ANALYSIS")
    require(type(diagnostics.get("selectionAtoms")) is int and diagnostics["selectionAtoms"] > 0
            and type(diagnostics.get("alignmentAtoms")) is int and diagnostics["alignmentAtoms"] > 0, "INVALID_ANALYSIS")
    manifest = read_json(output / "manifest.json")
    trajectories = [entry for entry in manifest["files"] if entry["role"] == "trajectory"]
    timing_files = {entry["path"] for entry in manifest["files"] if entry["role"] == "trajectory-times"}
    require(bool(trajectories), "TRAJECTORY_REQUIRED")
    times = []
    for entry in trajectories:
        trajectory = safe_file(output, entry["path"])
        timing = trajectory.with_suffix(".times.csv")
        require(timing.relative_to(output).as_posix() in timing_files, "TRAJECTORY_TIMES_MISSING")
        frames, atoms = _dcd_frames(trajectory)
        if (output / "model.json").is_file():
            require(atoms == read_json(output / "model.json")["particles"], "PARTICLE_COUNT_MISMATCH")
        with timing.open() as stream:
            reader = csv.DictReader(stream)
            require(reader.fieldnames == ["stageStep", "timePs"], "INVALID_TRAJECTORY_TIMES")
            segment = list(reader)
        require(len(segment) == frames, "INVALID_TRAJECTORY_TIMES")
        for row in segment:
            t = finite(row["timePs"])
            require(t >= 0 and (not times or t > times[-1]), "INVALID_TRAJECTORY_TIMES")
            times.append(t)
    selected = [(i, t) for i,t in enumerate(times) if i % controls["stride"] == 0]
    rows, first, last = 0, None, None
    with (output / "analysis.csv").open() as stream:
        reader = csv.DictReader(stream)
        require(reader.fieldnames == ["frame", "timePs", "rmsdAngstrom", "radiusOfGyrationAngstrom"], "INVALID_ANALYSIS_UNITS")
        for row in reader:
            time_ps = finite(row["timePs"])
            require(time_ps >= 0 and (last is None or time_ps > last), "INVALID_TRAJECTORY_TIMES")
            if first is None: first = time_ps
            require(rows < len(selected) and int(row["frame"]) == selected[rows][0], "INVALID_TRAJECTORY_TIMES")
            _close(time_ps, selected[rows][1], "INVALID_TRAJECTORY_TIMES")
            last = time_ps; rows += 1
            for metric, column in (("rmsd", "rmsdAngstrom"), ("radiusOfGyration", "radiusOfGyrationAngstrom")):
                if metric in controls["metrics"]: require(row[column] != "", "INVALID_ANALYSIS")
                if row[column] != "": require(finite(row[column]) >= 0, "INVALID_ANALYSIS")
    require(rows > 0 and rows == diagnostics.get("frames"), "INVALID_ANALYSIS")
    require(rows == len(selected), "INVALID_ANALYSIS")
    _close(times[0], diagnostics.get("coverageStartPs"), "INVALID_TRAJECTORY_TIMES")
    _close(times[-1], diagnostics.get("coverageEndPs"), "INVALID_TRAJECTORY_TIMES")
    if "rmsf" in controls["metrics"]:
        with (output / "rmsf.csv").open() as stream:
            records = list(csv.DictReader(stream))
        require(len(records) == diagnostics["selectionAtoms"], "INVALID_ANALYSIS")
        for row in records: require(finite(row["rmsfAngstrom"]) >= 0, "INVALID_ANALYSIS")
