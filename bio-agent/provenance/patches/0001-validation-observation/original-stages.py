"""Real CPU/CUDA stages. All outputs are new immutable lineage artifacts."""
from __future__ import annotations

import csv
import hashlib
import math
from pathlib import Path
import platform as host_platform
import shutil
import socket
import sys
import os
import time
import subprocess
import re

from .common import (WorkflowError, atomic_json, canonical, digest, file_record,
                     read_json, require, safe_path, verify_manifest, output_bytes)
from . import storage


def probe(*, include_analysis: bool = True, include_preparation: bool = True) -> dict:
    import openmm
    platforms = []
    for index in range(openmm.Platform.getNumPlatforms()):
        item = openmm.Platform.getPlatform(index)
        platforms.append({"name": item.getName(), "properties": {
            name: item.getPropertyDefaultValue(name) for name in item.getPropertyNames()}})
    result = {"pythonVersion": host_platform.python_version(), "openmmVersion": openmm.version.full_version,
            "platforms": platforms,
            "system": host_platform.system(), "machine": host_platform.machine(),
            "interpreter": sys.executable, "pluginFailures": openmm.Platform.getPluginLoadFailures()}
    if include_analysis:
        import MDAnalysis
        result["mdanalysisVersion"] = MDAnalysis.__version__
    if include_preparation:
        from .amber_preparation import preparation_profile
        result["preparationProfile"] = preparation_profile()
    return result


def _hardware_identity(selected: str, properties: dict) -> dict:
    if host_platform.system() == "Linux":
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    else:
        boot = subprocess.run(["/usr/sbin/sysctl", "-n", "kern.boottime"], capture_output=True,
                              text=True, timeout=5, check=True).stdout.strip()
    result = {"boot": boot, "kernel": host_platform.release()}
    if selected == "CUDA":
        executable = shutil.which("nvidia-smi")
        require(executable is not None, "CUDA_HARDWARE_IDENTITY_UNAVAILABLE")
        hardware = subprocess.run([executable, "--query-gpu=uuid,driver_version,name", "--format=csv,noheader",
                                   "--id=" + properties["DeviceIndex"]], capture_output=True,
                                  text=True, timeout=10, check=True)
        require(bool(hardware.stdout.strip()), "CUDA_HARDWARE_IDENTITY_UNAVAILABLE")
        result["cuda"] = hardware.stdout.strip()
    return result


def _platform(resources: dict):
    import openmm
    name = resources["platform"]
    require(name in ("CPU", "CUDA"), "INVALID_PLATFORM")
    selected = openmm.Platform.getPlatformByName(name)
    if name == "CPU":
        properties = {"Threads": str(resources["cpuThreads"])}
    else:
        require(host_platform.system() == "Linux", "CUDA_REQUIRES_LINUX")
        require("deviceIndex" in resources and "precision" in resources, "CUDA_PROPERTIES_REQUIRED")
        properties = {"DeviceIndex": str(resources["deviceIndex"]), "Precision": resources["precision"]}
    require(set(properties).issubset(set(selected.getPropertyNames())), "PLATFORM_PROPERTIES_UNAVAILABLE")
    return selected, properties


def _finite_state(state, system) -> dict:
    import numpy as np
    from openmm import unit
    coordinates = state.getPositions(asNumpy=True).value_in_unit(unit.nanometer)
    velocities = state.getVelocities(asNumpy=True).value_in_unit(unit.nanometer / unit.picosecond)
    box = state.getPeriodicBoxVectors(asNumpy=True).value_in_unit(unit.nanometer)
    energy = state.getPotentialEnergy().value_in_unit(unit.kilojoule_per_mole)
    kinetic = state.getKineticEnergy().value_in_unit(unit.kilojoule_per_mole)
    time_ps = state.getTime().value_in_unit(unit.picosecond)
    require(coordinates.shape == (system.getNumParticles(), 3), "PARTICLE_COUNT_MISMATCH")
    require(np.isfinite(coordinates).all() and np.isfinite(velocities).all()
            and np.isfinite(box).all() and all(math.isfinite(v) for v in (energy, kinetic, time_ps)), "NONFINITE_STATE")
    volume = float(np.linalg.det(box))
    if system.usesPeriodicBoundaryConditions():
        require(volume > 0, "INVALID_PERIODIC_BOX")
    require(time_ps >= 0, "INVALID_STATE_TIME")
    return {"particles": system.getNumParticles(), "timePs": time_ps,
            "potentialEnergyKjMol": energy, "kineticEnergyKjMol": kinetic,
            "volumeNm3": volume, "boxVectorsNm": box.tolist(), "finite": True}


def _state(context):
    return context.getState(getPositions=True, getVelocities=True, getEnergy=True, getParameters=True)


def integrator_semantics(kind: str, version: str) -> str:
    require(kind in ("LangevinMiddle", "Langevin", "LangevinLegacy"), "UNSUPPORTED_INTEGRATOR")
    match = re.match(r"^(\d+)\.(\d+)", version)
    require(match is not None, "UNKNOWN_OPENMM_VERSION")
    release = tuple(map(int, match.groups()))
    # OpenMM 8.2 changed Langevin's discretization, not merely its class name.
    if kind == "LangevinLegacy":
        require((7, 0) <= release < (8, 2), "LEGACY_LANGEVIN_REQUIRES_OPENMM_BEFORE_8_2")
    return "LFMiddle" if kind == "LangevinMiddle" or release >= (8, 2) else "legacy-Langevin"


def explicit_hydrogen_choices(source: Path, topology, *, require_histidine_variant: bool = True) -> tuple[list, list]:
    """Preserve original PDB variants before PDBFile normalizes residue aliases."""
    from openmm import app
    from openmm.app.internal.pdbstructure import PdbStructure
    with source.open() as stream:
        raw = PdbStructure(stream, load_all_models=False)
    raw_residues = [(chain, residue) for chain in raw.iter_chains() for residue in chain.iter_residues()]
    residues = list(topology.residues())
    require(len(raw_residues) == len(residues), "PROTONATION_RESIDUE_MAPPING_MISMATCH")
    aliases = {"HID": "HIS", "HIE": "HIS", "HIP": "HIS", "ASH": "ASP", "GLH": "GLU", "LYN": "LYS", "CYX": "CYS"}
    variants, provenance, keys = [], [], []
    app.Modeller._loadStandardHydrogenDefinitions()
    for (chain, original), residue in zip(raw_residues, residues):
        name = original.get_name()
        canonical_name = app.PDBFile._residueNameReplacements.get(name, name)
        replacements = app.PDBFile._atomNameReplacements.get(canonical_name, {})
        atoms, seen = [], set()
        for atom in original.atoms_by_name.values():
            if atom.get_name() in seen or atom.residue_name != name:
                continue
            seen.add(atom.get_name())
            atoms.append((replacements.get(atom.get_name(), atom.get_name()).strip(), str(atom.serial_number)))
        require(residue.name == canonical_name and residue.chain.id == chain.chain_id
                and residue.id == str(original.number) and residue.insertionCode == original.insertion_code
                and [(atom.name, atom.id) for atom in residue.atoms()] == atoms, "PROTONATION_RESIDUE_MAPPING_MISMATCH")
        require(not require_histidine_variant or name != "HIS", "HISTIDINE_VARIANT_REQUIRED")
        variant = name if name in aliases else None
        if variant:
            residue.name = aliases[name]
            definition = app.Modeller._residueHydrogens[residue.name]
            require(variant in definition.variants, "UNSUPPORTED_PROTONATION_VARIANT")
            excluded = {h.name for h in definition.hydrogens if h.variants is not None and variant not in h.variants}
            require(not any(atom.element == app.element.hydrogen and atom.name in excluded for atom in residue.atoms()),
                    "PROTONATION_LABEL_CONFLICT")
        variants.append(variant)
        provenance.append({"residueIndex": residue.index, "sourceName": name, "requestedVariant": variant,
                           "source": "explicit-pdb-label" if variant else "approved-pH-rule"})
        keys.append((residue.chain.id, residue.id, residue.insertionCode.strip()))
    if any(record["sourceName"] == "LYN" for record in provenance):
        topology.createStandardBonds()  # LYN is not a PDBFile alias; renamed LYS uses its standard graph.
    explicit_pairs = set()
    with source.open() as stream:
        for line in stream:
            if line.startswith("SSBOND"):
                try:
                    explicit_pairs.add(frozenset(((line[15:16], str(int(line[17:21])), line[21:22].strip()),
                                                  (line[29:30], str(int(line[31:35])), line[35:36].strip()))))
                except ValueError as error:
                    raise WorkflowError("INVALID_DISULFIDE_RECORD") from error
    detected = set()
    explicit_cyx_count = sum(item["sourceName"] == "CYX" for item in provenance)
    for left, right in topology.bonds():
        if left.name == right.name == "SG" and left.residue != right.residue:
            pair = frozenset((keys[left.residue.index], keys[right.residue.index]))
            require(len(pair) == 2, "DISULFIDE_RESIDUE_ID_AMBIGUOUS")
            detected.add(pair)
            labels = provenance[left.residue.index]["sourceName"] == provenance[right.residue.index]["sourceName"] == "CYX"
            require(pair in explicit_pairs or (labels and explicit_cyx_count == 2), "DISULFIDE_CONFIRMATION_REQUIRED")
            for residue in (left.residue, right.residue):
                variants[residue.index] = "CYX"
                if provenance[residue.index]["sourceName"] != "CYX":
                    provenance[residue.index].update(requestedVariant="CYX", source="explicit-SSBOND")
    require(explicit_pairs <= detected, "DISULFIDE_TOPOLOGY_MISMATCH")
    return variants, provenance


def resolved_hydrogen_choices(topology, choices: list, selected: list, ph: float) -> list:
    from openmm import app
    bonded = {atom: [] for atom in topology.atoms()}
    for left, right in topology.bonds():
        bonded[left].append(right)
        bonded[right].append(left)
    for residue, record, actual in zip(topology.residues(), choices, selected):
        atoms = {atom.name: atom for atom in residue.atoms()}
        def hydrogens(names):
            return sum(neighbor.element == app.element.hydrogen for name in names if name in atoms
                       for neighbor in bonded[atoms[name]])
        if residue.name == "ASP":
            actual = "ASH" if hydrogens(("OD1", "OD2")) else "ASP"
        elif residue.name == "GLU":
            actual = "GLH" if hydrogens(("OE1", "OE2")) else "GLU"
        elif residue.name == "LYS":
            actual = "LYS" if hydrogens(("NZ",)) == 3 else "LYN"
        elif residue.name == "CYS":
            actual = "CYS" if hydrogens(("SG",)) else "CYX"
        record.update(resolvedVariant=actual, pH=ph,
                      method="OpenMM explicit variant" if record["requestedVariant"] else
                      "OpenMM pH rule with existing hydrogens preserved; resolved from final bonds")
        require(record["requestedVariant"] is None or actual == record["requestedVariant"], "PROTONATION_RESOLUTION_MISMATCH")
    return choices


class Stage:
    def __init__(self, attempt: Path):
        self.attempt = attempt
        self.request = read_json(attempt / "request.json")
        self.spec = self.request["stage"]
        self.params = self.spec["parameters"]
        self.output = attempt / "output"
        self.output.mkdir(exist_ok=False)
        self.roles: dict[str, str] = {}
        self.records: dict[str, dict] = {}
        self.inventory: dict[str, dict] = {}
        self.recovery_records: list[dict] = []
        self.recovered_trajectories: set[str] = set()
        self.previous_recovery = None
        self.flush_trajectory = None
        self.dependencies = []
        recovery = self.request.get("recovery")
        continuation = self.request.get("continuation")
        for entry in self.request["dependencyBundles"]:
            path = safe_path(attempt / "input", entry["path"])
            manifest = verify_manifest(path, entry["sha256"], allow_recovery=recovery is not None)
            if recovery is not None:
                require(self.spec["kind"] in ("equilibrate", "production") and not self.spec["dependsOn"]
                        and len(self.request["dependencyBundles"]) == 1
                        and entry == {"path": recovery["path"], "sha256": recovery["sha256"]}
                        and manifest.get("acceptance") == {"engineering": False, "recovery": True}
                        and all(manifest[key] == recovery[source] for key, source in (
                            ("workflowId", "sourceWorkflowId"), ("revision", "sourceRevision"),
                            ("stageId", "sourceStageId"), ("attemptId", "sourceAttemptId"))), "RECOVERY_LINEAGE_MISMATCH")
            elif continuation is not None:
                require(not self.spec["dependsOn"] and len(self.request["dependencyBundles"]) == 1
                        and entry == {"path": continuation["path"], "sha256": continuation["sha256"]}
                        and manifest["acceptance"]["engineering"] is True
                        and manifest["workflowId"] != self.request["workflowId"]
                        and all(manifest[key] == continuation[source] for key, source in (
                            ("workflowId", "sourceWorkflowId"), ("revision", "sourceRevision"),
                            ("stageId", "sourceStageId"), ("attemptId", "sourceAttemptId"), ("modelId", "modelId"))), "CONTINUATION_LINEAGE_MISMATCH")
                model = read_json(path.parent / "model.json")
                require(model["modelId"] == continuation["modelId"] and model["minimized"] == continuation["minimized"], "CONTINUATION_MODEL_CHANGED")
            else:
                require(manifest["workflowId"] == self.request["workflowId"]
                        and manifest["revision"] == self.request["revision"]
                        and manifest["stageId"] in self.spec["dependsOn"], "LINEAGE_MISMATCH")
            self.dependencies.append((path, manifest))
        require(recovery is not None or continuation is not None or {m["stageId"] for _, m in self.dependencies} == set(self.spec["dependsOn"]), "DEPENDENCIES_MISSING")
        for entry in self.request["inputs"]:
            require(digest(safe_path(attempt / "input", entry["path"])) == entry["sha256"], "HASH_MISMATCH")
        storage_estimate = storage.configure(attempt, self.request, self.dependencies)
        if storage_estimate is not None:
            self.write_json("storage-estimate.json", storage_estimate, "report")
        # Dynamics needs OpenMM, while preparation and analysis are local stages
        # with their own dependencies. A CUDA server need not install those tools.
        self.environment = probe(include_analysis=self.spec["kind"] == "analysis",
                                 include_preparation=self.spec["kind"] == "prepare")
        self.write_json("environment.json", self.environment, "environment")
        self.write_json("protocol.json", self.spec, "protocol")
        self.model = None
        self.simulation = None
        self.compatibility = None
        self.diagnostics = {}

    def write_json(self, name: str, data: dict, role: str):
        atomic_json(self.output / name, data)
        self.roles[name] = role

    def write_xml(self, name: str, value, role: str):
        import openmm
        with (self.output / name).open("x") as stream:
            stream.write(openmm.XmlSerializer.serialize(value))
        self.roles[name] = role

    def stream(self):
        for name, role in self.roles.items():
            if name not in self.records:
                self.records[name] = file_record(self.output, self.output / name, role)
        require(len(self.records) + len(self.recovery_records) <= 20000, "TOO_MANY_OUTPUT_FILES")
        for record in list(self.records.values()) + self.recovery_records:
            previous = self.inventory.get(record["path"])
            require(previous is None or previous == record, "PUBLISHED_FILE_CHANGED")
            self.inventory.setdefault(record["path"], record)
        atomic_json(self.attempt / "stream.json", {"files": list(self.inventory.values())})

    def canceled(self) -> bool:
        # Independent of supervisor liveness. The watchdog handles blocking calls.
        if (self.attempt / "budget.json").exists():
            atomic_json(self.attempt / "cancel.json", {"requested": True})
            return True
        if (self.attempt / "cancel.json").exists():
            return True
        status = read_json(self.attempt / "status.json")
        resources = self.spec["resources"]
        reason = None
        if time.monotonic() - status.get("stageStartedMono", time.monotonic()) > resources["wallTimeSeconds"]:
            reason = "WALLTIME_EXCEEDED"
        if storage.automatic(resources):
            reason = storage.reason(self.attempt) or reason
        elif output_bytes(self.attempt) > resources["maxOutputBytes"]:
            reason = "OUTPUT_LIMIT_EXCEEDED"
        if reason:
            atomic_json(self.attempt / "budget.json", {"error": reason})
            atomic_json(self.attempt / "cancel.json", {"requested": True})
        return (self.attempt / "cancel.json").exists()

    def parent(self):
        require(len(self.dependencies) == 1, "ONE_MODEL_DEPENDENCY_REQUIRED")
        path, manifest = self.dependencies[0]
        root = path.parent
        model = read_json(root / "model.json")
        require(model["modelId"] == manifest["modelId"]
                and digest(root / "state.xml") == manifest["stateId"], "MODEL_STATE_IDENTITY_MISMATCH")
        for name in ("system.xml", "state.xml", "topology.pdb", "model.json"):
            require(any(f["path"] == name for f in manifest["files"]), "INCOMPLETE_MODEL_BUNDLE")
        return root, manifest, model

    def prepare(self):
        import openmm
        from openmm import app, unit
        require(self.spec["resources"]["platform"] == "CPU", "PREPARE_REQUIRES_CPU")
        if self.params["preparation"].get("mode") == "water-box":
            return self.prepare_water()
        if self.params["preparation"].get("mode") == "amber-small-molecule":
            require(self.request["inputs"] == [] and not self.dependencies, "SMALL_MOLECULE_REQUIRES_APPROVED_SMILES")
            return self.prepare_amber(None, None)
        sources = [i for i in self.request["inputs"] if i["role"] == "structure"]
        require(len(sources) == 1 and not self.dependencies, "ONE_STRUCTURE_REQUIRED")
        source = safe_path(self.attempt / "input", sources[0]["path"])
        if self.params["preparation"].get("mode") == "amber-protein-ligand":
            return self.prepare_amber(source, sources[0]["sha256"])
        from .input_audit import load_checked_pdb, force_field_files
        try:
            pdb, preflight = load_checked_pdb(source, sources[0]["sha256"])
        except ValueError as error:
            raise WorkflowError(str(error)) from error
        self.write_json("input-preflight.json", preflight, "input-preflight")
        amino = set("ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE LEU LYS MET PHE PRO SER THR TRP TYR VAL HID HIE HIP CYX ASH GLH LYN".split())
        solvent = {"HOH", "WAT", "NA", "CL", "Na+", "Cl-"}
        residues = {r.name for r in pdb.topology.residues()}
        require(bool(residues & amino) and residues <= amino | solvent, "UNSUPPORTED_RESIDUES")
        p = self.params["preparation"]
        force_fields = {"amber14": ["amber14-all.xml", "amber14/tip3p.xml"],
                        "charmm36": ["charmm36.xml", "charmm36/water.xml"],
                        "amber99sbildn": ["amber99sbildn.xml", "tip3p.xml"]}
        data_root = Path(app.__file__).resolve().parent / "data"
        names = tuple(force_fields[p["forceField"]])
        ff_provenance = force_field_files(data_root, names)
        # Resolve exactly the files whose hashes are recorded, never a CWD shadow.
        ff = app.ForceField(*(str(data_root / name) for name in names))
        require(ff_provenance == force_field_files(data_root, names), "FORCE_FIELD_CHANGED_DURING_LOAD")
        modeller = app.Modeller(pdb.topology, pdb.positions)
        cpu, properties = _platform(self.spec["resources"])
        # Hydrogen-placement minimization is also explicitly CPU; no default GPU selection.
        hydrogen_choices = []
        variants, choices = explicit_hydrogen_choices(source, modeller.topology, require_histidine_variant=p["addHydrogens"])
        if p["addHydrogens"]:
            selected = modeller.addHydrogens(ff, pH=p["ph"], variants=variants, platform=cpu)
            hydrogen_choices = resolved_hydrogen_choices(modeller.topology, choices, selected, p["ph"])
        if p["solvent"]["mode"] == "tip3p-box":
            water = p["solvent"]
            modeller.addSolvent(ff, model="tip3p", padding=water["paddingAngstrom"] * unit.angstrom,
                                ionicStrength=water["ionicStrengthMolar"] * unit.molar,
                                neutralize=water["neutralize"])
        configuration = self.params["system"]
        method = {"PME": app.PME, "NoCutoff": app.NoCutoff}[configuration["nonbondedMethod"]]
        require(method != app.PME or modeller.topology.getPeriodicBoxVectors() is not None, "PME_REQUIRES_BOX")
        constraints = {"HBonds": app.HBonds, "AllBonds": app.AllBonds, "None": None}[configuration["constraints"]]
        system = ff.createSystem(modeller.topology, nonbondedMethod=method,
                                 nonbondedCutoff=configuration["nonbondedCutoffNm"] * unit.nanometer,
                                 constraints=constraints, rigidWater=configuration["rigidWater"],
                                 removeCMMotion=configuration["removeCMMotion"])
        integrator = openmm.VerletIntegrator(0.001 * unit.picosecond)
        simulation = app.Simulation(modeller.topology, system, integrator, cpu, properties)
        simulation.context.setPositions(modeller.positions)
        self.diagnostics = _finite_state(_state(simulation.context), system)
        with (self.output / "topology.pdb").open("x") as stream:
            app.PDBFile.writeFile(modeller.topology, modeller.positions, stream)
        self.roles["topology.pdb"] = "topology"
        self.write_xml("system.xml", system, "system")
        self.model = {"modelId": hashlib.sha256(canonical({"system": digest(self.output / "system.xml"),
                      "topology": digest(self.output / "topology.pdb"), "preparation": p,
                      "input": sources[0]["sha256"]})).hexdigest(),
                      "preparation": p, "hydrogenVariants": hydrogen_choices, "system": configuration, "forceFieldFiles": force_fields[p["forceField"]],
                      "forceFieldFileHashes": ff_provenance,
                      "inputSha256": sources[0]["sha256"], "particles": system.getNumParticles(),
                      "bonds": [[a.index, b.index] for a, b in modeller.topology.bonds()], "minimized": False}
        self.simulation = simulation
        self.compatibility = self.make_compatibility(simulation, properties, {"kind": "preparation-state"})

    def prepare_water(self):
        import openmm
        from openmm import app, unit
        from .input_audit import force_field_files
        require(not self.request["inputs"] and not self.dependencies, "WATER_BOX_REQUIRES_NO_INPUTS")
        p, configuration = self.params["preparation"], self.params["system"]
        side = p["boxSizeNm"]
        require(p["forceField"] == "tip3p" and configuration["nonbondedMethod"] == "PME"
                and configuration["rigidWater"] and 0 < configuration["nonbondedCutoffNm"] < side / 2,
                "INVALID_WATER_SYSTEM")
        data_root = Path(app.__file__).resolve().parent / "data"
        names = ("amber14/tip3p.xml",)
        ff_provenance = force_field_files(data_root, names)
        water_source = data_root / "tip3p.pdb"
        water_provenance = [{"name": "tip3p.pdb", "sha256": digest(water_source), "byteSize": water_source.stat().st_size}]
        ff = app.ForceField(*(str(data_root / name) for name in names))
        modeller = app.Modeller(app.Topology(), [] * unit.nanometer)
        modeller.addSolvent(ff, model="tip3p", boxSize=openmm.Vec3(side, side, side) * unit.nanometer,
                            neutralize=False, ionicStrength=0 * unit.molar)
        require(ff_provenance == force_field_files(data_root, names), "FORCE_FIELD_CHANGED_DURING_LOAD")
        require(water_provenance == [{"name": "tip3p.pdb", "sha256": digest(water_source), "byteSize": water_source.stat().st_size}],
                "WATER_SOURCE_CHANGED_DURING_BUILD")
        residues = list(modeller.topology.residues())
        require(bool(residues) and all(residue.name == "HOH"
                and sorted(atom.element.symbol for atom in residue.atoms()) == ["H", "H", "O"] for residue in residues),
                "INVALID_GENERATED_WATER")
        box = modeller.topology.getPeriodicBoxVectors().value_in_unit(unit.nanometer)
        require(min(box[0][0], box[1][1], box[2][2]) > 2 * configuration["nonbondedCutoffNm"], "CUTOFF_EXCEEDS_BOX")
        system = ff.createSystem(modeller.topology, nonbondedMethod=app.PME,
            nonbondedCutoff=configuration["nonbondedCutoffNm"] * unit.nanometer,
            constraints={"HBonds": app.HBonds, "AllBonds": app.AllBonds, "None": None}[configuration["constraints"]],
            rigidWater=True, removeCMMotion=configuration["removeCMMotion"])
        cpu, properties = _platform(self.spec["resources"])
        simulation = app.Simulation(modeller.topology, system, openmm.VerletIntegrator(.001 * unit.picosecond), cpu, properties)
        simulation.context.setPositions(modeller.positions)
        self.diagnostics = _finite_state(_state(simulation.context), system)
        with (self.output / "topology.pdb").open("x") as stream:
            app.PDBFile.writeFile(modeller.topology, modeller.positions, stream)
        self.roles["topology.pdb"] = "topology"
        self.write_xml("system.xml", system, "system")
        density = sum(system.getParticleMass(i).value_in_unit(unit.dalton) for i in range(system.getNumParticles())) / (602.214076 * side**3)
        self.write_json("water-box-report.json", {"builder": "OpenMM Modeller.addSolvent", "waterModel": "tip3p",
            "boxVectorsNm": [list(vector) for vector in box], "waterMolecules": len(residues),
            "particles": system.getNumParticles(), "initialDensityGramPerMl": density, "addedIons": 0,
            "forceFieldFileHashes": ff_provenance, "waterSourceFileHashes": water_provenance,
            "scope": "Generated periodic model before minimization; density is not an equilibrium or convergence assessment."}, "parameter-provenance")
        self.model = {"modelId": hashlib.sha256(canonical({"system": digest(self.output / "system.xml"),
            "topology": digest(self.output / "topology.pdb"), "preparation": p,
            "forceFieldFileHashes": ff_provenance, "waterSourceFileHashes": water_provenance})).hexdigest(),
            "preparation": p, "system": configuration, "forceFieldFiles": list(names), "forceFieldFileHashes": ff_provenance,
            "waterSourceFileHashes": water_provenance, "particles": system.getNumParticles(),
            "bonds": [[a.index, b.index] for a, b in modeller.topology.bonds()], "minimized": False}
        self.simulation = simulation
        self.compatibility = self.make_compatibility(simulation, properties, {"kind": "preparation-state"})

    def prepare_amber(self, source: Path | None, source_hash: str | None):
        import openmm
        from openmm import app, unit
        from .amber_preparation import prepare
        top, coordinates = prepare(self, source, self.params["preparation"])
        configuration = self.params["system"]
        require(configuration["nonbondedMethod"] == "PME", "AMBER_PROFILE_REQUIRES_PME")
        box = coordinates.boxVectors.value_in_unit(unit.nanometer)
        require(min(box[0][0], box[1][1], box[2][2]) > 2 * configuration["nonbondedCutoffNm"], "CUTOFF_EXCEEDS_BOX")
        system = top.createSystem(nonbondedMethod=app.PME,
            nonbondedCutoff=configuration["nonbondedCutoffNm"] * unit.nanometer,
            constraints={"HBonds": app.HBonds, "AllBonds": app.AllBonds, "None": None}[configuration["constraints"]],
            rigidWater=configuration["rigidWater"], removeCMMotion=configuration["removeCMMotion"])
        system.setDefaultPeriodicBoxVectors(*coordinates.boxVectors)
        cpu, properties = _platform(self.spec["resources"])
        simulation = app.Simulation(top.topology, system, openmm.VerletIntegrator(.001 * unit.picosecond), cpu, properties)
        simulation.context.setPositions(coordinates.positions)
        self.diagnostics = _finite_state(_state(simulation.context), system)
        with (self.output / "topology.pdb").open("x") as stream:
            app.PDBFile.writeFile(top.topology, coordinates.positions, stream)
        self.roles["topology.pdb"] = "topology"
        self.write_xml("system.xml", system, "system")
        self.model = {"modelId": hashlib.sha256(canonical({"system": digest(self.output / "system.xml"),
            "topology": digest(self.output / "topology.pdb"), "preparation": self.params["preparation"], "input": source_hash,
            "parameterization": digest(self.output / "parameterization-report.json")})).hexdigest(),
            "preparation": self.params["preparation"], "system": configuration, "inputSha256": source_hash,
            "parameterizationSha256": digest(self.output / "parameterization-report.json"),
            "particles": system.getNumParticles(), "bonds": [[a.index, b.index] for a, b in top.topology.bonds()], "minimized": False}
        self.simulation = simulation
        self.compatibility = self.make_compatibility(simulation, properties, {"kind": "preparation-state"})

    def make_compatibility(self, simulation, properties: dict, physics: dict):
        import openmm
        context = simulation.context
        selected = context.getPlatform()
        actual = {name: selected.getPropertyValue(context, name) for name in selected.getPropertyNames()}
        return {"openmmVersion": openmm.version.full_version, "systemSha256": hashlib.sha256(openmm.XmlSerializer.serialize(simulation.system).encode()).hexdigest(),
                "integratorSha256": hashlib.sha256(openmm.XmlSerializer.serialize(simulation.integrator).encode()).hexdigest(),
                "platform": selected.getName(), "properties": actual, "requestedProperties": properties,
                "system": host_platform.system(), "machine": host_platform.machine(), "host": socket.gethostname(),
                "processor": host_platform.processor(), "hardware": _hardware_identity(selected.getName(), properties), "physics": physics}

    def initialize_parent(self, dynamics: bool):
        import openmm
        from openmm import app, unit
        root, manifest, model = self.parent()
        pdb = app.PDBFile(str(root / "topology.pdb"))
        system = openmm.XmlSerializer.deserialize((root / "system.xml").read_text())
        source_state = openmm.XmlSerializer.deserialize((root / "state.xml").read_text())
        if "recovery" in self.request:
            require(manifest.get("checkpointStep") == source_state.getStepCount()
                    and abs(manifest.get("checkpointTimePs", -1) - source_state.getTime().value_in_unit(unit.picosecond)) < 1e-9,
                    "RECOVERY_STATE_TIME_MISMATCH")
        self.model = dict(model)
        self.model["parentStateId"] = manifest["stateId"]
        self.model["parentManifestSha256"] = digest(root / "manifest.json")
        shutil.copyfile(root / "topology.pdb", self.output / "topology.pdb")
        self.roles["topology.pdb"] = "topology"
        selected, properties = _platform(self.spec["resources"])
        if dynamics:
            require(model["minimized"], "MINIMIZATION_REQUIRED")
            p = self.params["dynamics"]
            require(p["ensemble"] != "NPT" or ("pressureBar" in p and system.usesPeriodicBoundaryConditions()), "NPT_REQUIRES_PERIODIC_PRESSURE")
            physics = {k: p[k] for k in ("ensemble", "temperatureKelvin", "timestepFs", "frictionPerPs", "seed")}
            # Omitted kind preserves legacy checkpoint fingerprints. An explicit
            # kind is a new protocol choice and must participate in compatibility.
            if "integrator" in p:
                physics["integrator"] = p["integrator"]
            if p["ensemble"] == "NPT":
                physics["pressureBar"] = p["pressureBar"]
            if self.params["restart"] == "state":
                for i in reversed(range(system.getNumForces())):
                    if isinstance(system.getForce(i), openmm.MonteCarloBarostat):
                        system.removeForce(i)
                if p["ensemble"] == "NPT":
                    barostat = openmm.MonteCarloBarostat(p["pressureBar"] * unit.bar,
                                                       p["temperatureKelvin"] * unit.kelvin, 25)
                    barostat.setRandomNumberSeed(p["seed"])
                    system.addForce(barostat)
            kind = p.get("integrator", "LangevinMiddle")
            self.diagnostics["integrator"] = {"requested": kind, "discretization": integrator_semantics(kind, openmm.version.full_version),
                                               "openmmVersion": openmm.version.full_version}
            integrators = {"LangevinMiddle": openmm.LangevinMiddleIntegrator, "Langevin": openmm.LangevinIntegrator, "LangevinLegacy": openmm.LangevinIntegrator}
            require(p.get("integrator", "LangevinMiddle") in integrators, "UNSUPPORTED_INTEGRATOR")
            integrator = integrators[p.get("integrator", "LangevinMiddle")](p["temperatureKelvin"] * unit.kelvin,
                         p["frictionPerPs"] / unit.picosecond, p["timestepFs"] * unit.femtosecond)
            integrator.setRandomNumberSeed(p["seed"])
        else:
            require(self.spec["resources"]["platform"] == "CPU", "FIRST_MINIMIZATION_REQUIRES_CPU")
            physics = {"kind": "minimization", **self.params["minimization"]}
            integrator = openmm.VerletIntegrator(0.001 * unit.picosecond)
        simulation = app.Simulation(pdb.topology, system, integrator, selected, properties)
        compatibility = self.make_compatibility(simulation, properties, physics)
        if dynamics and self.params["restart"] == "checkpoint":
            require(not p["initializeVelocities"], "CHECKPOINT_VELOCITY_REINITIALIZATION_FORBIDDEN")
            require(manifest.get("checkpointCompatibility") == compatibility, "CHECKPOINT_INCOMPATIBLE")
            candidates = [f for f in manifest["files"] if f["role"] == "checkpoint"]
            require(bool(candidates), "CHECKPOINT_MISSING")
            simulation.loadCheckpoint(str(safe_path(root, candidates[-1]["path"])))
        else:
            # Portable State handoff must not import obsolete barostat/context parameters.
            simulation.context.setPeriodicBoxVectors(*source_state.getPeriodicBoxVectors())
            simulation.context.setPositions(source_state.getPositions())
            simulation.context.setVelocities(source_state.getVelocities())
            simulation.context.setTime(source_state.getTime())
            simulation.context.setStepCount(source_state.getStepCount())
            if dynamics and p["ensemble"] == "NPT":
                simulation.context.setParameter(openmm.MonteCarloBarostat.Pressure(), p["pressureBar"])
                simulation.context.setParameter(openmm.MonteCarloBarostat.Temperature(), p["temperatureKelvin"])
            if dynamics and p["initializeVelocities"]:
                simulation.context.setVelocitiesToTemperature(p["temperatureKelvin"] * unit.kelvin, p["seed"])
        self.diagnostics["initial"] = _finite_state(_state(simulation.context), system)
        require(abs(self.diagnostics["initial"]["timePs"] - source_state.getTime().value_in_unit(unit.picosecond)) < 1e-6,
                "RESTART_TIME_MISMATCH")
        require(simulation.context.getStepCount() == source_state.getStepCount(), "RESTART_STEP_MISMATCH")
        self.simulation = simulation
        self.compatibility = compatibility
        self.write_xml("system.xml", system, "system")
        if dynamics:
            self.write_json("model.json", self.model, "model")
        return simulation

    def minimize(self):
        from openmm import unit
        simulation = self.initialize_parent(False)
        p = self.params["minimization"]
        self.write_xml("pre-minimization-state.xml", _state(simulation.context), "initial-state")
        simulation.minimizeEnergy(tolerance=p["toleranceKjMolNm"] * unit.kilojoule_per_mole / unit.nanometer,
                                 maxIterations=p["maxIterations"])
        self.diagnostics["final"] = _finite_state(_state(simulation.context), simulation.system)
        self.model["minimized"] = True
        # Finite minimization is an engineering gate, never a convergence assertion.

    def checkpoint(self, number: int):
        path = self.output / f"checkpoint-{number:012d}.chk"
        if path.exists():
            return  # Same completed step on cooperative cancellation; retain immutable file.
        if self.flush_trajectory is not None:
            self.flush_trajectory()
        state = _state(self.simulation.context)
        _finite_state(state, self.simulation.system)
        self.simulation.saveCheckpoint(str(path))
        self.roles[path.name] = "checkpoint"
        self.publish_progress(number, state)
        self.finalize_recovery(number, path, state)
        self.stream()

    def publish_progress(self, number: int, state):
        """A bounded immutable tail, never the live mutable thermodynamics file."""
        from openmm import unit
        header = "stageStep,timePs,potentialEnergyKjMol,kineticEnergyKjMol,volumeNm3,temperatureKelvin"
        tail = b""
        source = self.output / "thermodynamics.csv"
        if source.exists():
            with source.open("rb") as stream:
                size = source.stat().st_size
                start = max(0, size - 12 * 1024)
                stream.seek(start)
                tail = stream.read(12 * 1024)
            if start:
                tail = tail.partition(b"\n")[2]
        rows = [line for line in tail.decode("ascii").splitlines() if line and line != header]
        content = (f"# checkpointStageStep={number} checkpointTimePs={state.getTime().value_in_unit(unit.picosecond):.12g}\n"
                   + header + "\n" + "\n".join(rows) + "\n").encode("ascii")
        require(len(content) <= 16 * 1024, "PROGRESS_LOG_TOO_LARGE")
        destination = self.output / f"progress-{number:012d}.log"
        with destination.open("xb") as stream:
            stream.write(content)
        self.roles[destination.name] = "log"

    def finalize_recovery(self, number: int, checkpoint: Path, state):
        """Publish an independent recovery snapshot, never a completion manifest.

        Static files and closed trajectory segments are hardlinked. Each snapshot
        carries only segments closed since the previous snapshot, avoiding an
        unbounded quadratic collection of historical hardlinks.
        """
        import openmm
        from openmm import unit
        destination = self.output / "recovery" / f"{number:012d}"
        destination.mkdir(parents=True, exist_ok=False)
        bundle_roles = {}
        for name, role in self.roles.items():
            if role in ("system", "topology", "model", "protocol", "environment") or (
                    role in ("trajectory", "trajectory-times") and name not in self.recovered_trajectories):
                os.link(self.output / name, destination / Path(name).name)
                bundle_roles[Path(name).name] = role
                if role in ("trajectory", "trajectory-times"):
                    self.recovered_trajectories.add(name)
        os.link(checkpoint, destination / "checkpoint.chk")
        bundle_roles["checkpoint.chk"] = "checkpoint"
        for name, value, role in (("state.xml", state, "state"),
                                  ("integrator.xml", self.simulation.integrator, "integrator")):
            with (destination / name).open("x") as stream:
                stream.write(openmm.XmlSerializer.serialize(value))
            bundle_roles[name] = role
        times = []
        for name, role in bundle_roles.items():
            if role == "trajectory-times":
                with (destination / name).open(newline="") as stream:
                    for row in csv.DictReader(stream):
                        value = float(row["timePs"])
                        if not times:
                            times.append(value)
                        if len(times) == 1:
                            times.append(value)
                        else:
                            times[1] = value
        manifest = {"schemaVersion": "ksh.stage-result.v1", "workflowId": self.request["workflowId"],
                    "revision": self.request["revision"], "stageId": self.spec["id"], "attemptId": self.request["attemptId"],
                    "modelId": self.model["modelId"], "stateId": digest(destination / "state.xml"),
                    "protocolSha256": digest(destination / "protocol.json"), "provenance": self.environment,
                    "checkpointCompatibility": self.compatibility, "parentBundles": self.request["dependencyBundles"],
                    "acceptance": {"engineering": False, "recovery": True},
                    "checkpointStep": state.getStepCount(), "checkpointTimePs": state.getTime().value_in_unit(unit.picosecond),
                    "trajectoryScope": "since-previous-recovery", "coverageStartPs": times[0] if times else None,
                    "coverageEndPs": times[-1] if times else None, "previousRecovery": self.previous_recovery,
                    "files": [file_record(destination, destination / name, role) for name, role in bundle_roles.items()]}
        atomic_json(destination / "manifest.json", manifest)
        verify_manifest(destination / "manifest.json", allow_recovery=True)
        for item in manifest["files"]:
            self.recovery_records.append({**item, "path": (destination / item["path"]).relative_to(self.output).as_posix(),
                                          "role": "recovery-file"})
        self.recovery_records.append(file_record(self.output, destination / "manifest.json", "recovery-manifest"))
        self.previous_recovery = {"sha256": digest(destination / "manifest.json"), "checkpointStep": state.getStepCount()}

    def dynamics(self):
        from openmm import app, unit
        simulation = self.initialize_parent(True)
        p = self.params["dynamics"]
        if "benchmark" in p:
            from .benchmark import measure
            require(self.spec["kind"] == "production" and self.params["restart"] == "state"
                    and p["ensemble"] == "NVT", "INVALID_BENCHMARK_PROTOCOL")
            def record_sample(index, sample):
                self.write_json(f"benchmark-sample-{index:03d}.json", {
                    "schemaVersion": "ksh.benchmark-sample.v1", "sampleIndex": index,
                    "modelId": self.model["modelId"], "sample": sample,
                }, "benchmark-sample")
                self.stream()

            report = measure(p["benchmark"], p["steps"], p["timestepFs"], simulation.step,
                             lambda: simulation.context.getState(getEnergy=True).getPotentialEnergy().value_in_unit(unit.kilojoule_per_mole),
                             self.canceled, record_sample=record_sample)
            report["environment"] = self.environment
            report["checkpointCompatibility"] = self.compatibility
            report["modelId"] = self.model["modelId"]
            self.write_json("benchmark.json", report, "benchmark")
            self.stream()
            require(report["status"] == "completed", report.get("reason", "BENCHMARK_INCOMPLETE"))
            self.diagnostics["final"] = _finite_state(_state(simulation.context), simulation.system)
            self.diagnostics["stageSteps"] = report["totalIntegratedSteps"]
            self.checkpoint(report["totalIntegratedSteps"])
            return
        # Include recovery manifests, state files, static hardlink records, and
        # segments closed by every checkpoint in the metadata budget.
        checkpoints = math.ceil(p["steps"] / p["checkpointInterval"]) + 1
        segments = math.ceil(p["steps"] / p["reporterInterval"] / p["segmentFrames"]) + checkpoints
        require(13 * checkpoints + 4 * segments + 32 <= 20000,
                "TOO_MANY_OUTPUT_FILES")
        completed, frames, segment = 0, 0, 0
        dcd_file = None
        dcd = None
        times_file = None
        times_writer = None
        def close_segment():
            nonlocal dcd_file, dcd, times_file
            if dcd is not None:
                dcd_file.close()
                times_file.close()
                self.roles[f"trajectory-{segment:06d}.dcd"] = "trajectory"
                self.roles[f"trajectory-{segment:06d}.times.csv"] = "trajectory-times"
                dcd = None
                dcd_file = None
        self.flush_trajectory = close_segment
        from .progress import LiveProgress, degrees_of_freedom, temperature_kelvin
        degrees = degrees_of_freedom(simulation.system)
        live = LiveProgress(self.attempt, self.request, degrees)
        log_path = self.output / "thermodynamics.csv"
        with log_path.open("x", newline="") as log:
            writer = csv.writer(log)
            writer.writerow(["stageStep", "timePs", "potentialEnergyKjMol", "kineticEnergyKjMol", "volumeNm3", "temperatureKelvin"])
            while completed < p["steps"]:
                if self.canceled():
                    if dcd_file:
                        dcd_file.close()
                        times_file.close()
                        self.roles[f"trajectory-{segment:06d}.times.csv"] = "trajectory-times"
                        self.roles[f"trajectory-{segment:06d}.dcd"] = "trajectory"
                    self.checkpoint(completed)
                    state = _state(simulation.context)
                    live.observe(completed, state, _finite_state(state, simulation.system), force=True)
                    self.save_state()
                    self.stream()
                    raise WorkflowError("CANCELED")
                advance = min(p["steps"] - completed, p["reporterInterval"] - completed % p["reporterInterval"],
                              p["checkpointInterval"] - completed % p["checkpointInterval"], 1000)
                simulation.step(advance)
                completed += advance
                if completed % p["reporterInterval"] == 0 or completed == p["steps"]:
                    state = _state(simulation.context)
                    values = _finite_state(state, simulation.system)
                    writer.writerow([completed] + [values[k] for k in ("timePs", "potentialEnergyKjMol", "kineticEnergyKjMol", "volumeNm3")]
                                    + [temperature_kelvin(state, degrees)])
                    log.flush()
                    live.observe(completed, state, values, force=completed == p["steps"])
                    if completed == p["steps"] and completed % p["reporterInterval"] and dcd is not None:
                        dcd_file.close()
                        times_file.close()
                        self.roles[f"trajectory-{segment:06d}.dcd"] = "trajectory"
                        self.roles[f"trajectory-{segment:06d}.times.csv"] = "trajectory-times"
                        dcd = None
                    if dcd is None:
                        segment += 1
                        dcd_file = (self.output / f"trajectory-{segment:06d}.dcd").open("xb")
                        dcd = app.DCDFile(dcd_file, simulation.topology, p["timestepFs"] * unit.femtosecond,
                                          firstStep=round(values["timePs"] * 1000 / p["timestepFs"]), interval=p["reporterInterval"])
                        times_file = (self.output / f"trajectory-{segment:06d}.times.csv").open("x", newline="")
                        times_writer = csv.writer(times_file)
                        times_writer.writerow(["stageStep", "timePs"])
                        frames = 0
                    dcd.writeModel(state.getPositions(), periodicBoxVectors=state.getPeriodicBoxVectors())
                    times_writer.writerow([completed, values["timePs"]])
                    frames += 1
                    if frames >= p["segmentFrames"] or completed == p["steps"]:
                        dcd_file.close()
                        times_file.close()
                        self.roles[f"trajectory-{segment:06d}.times.csv"] = "trajectory-times"
                        self.roles[f"trajectory-{segment:06d}.dcd"] = "trajectory"
                        dcd = None
                        dcd_file = None
                        self.stream()
                if completed % p["checkpointInterval"] == 0 or completed == p["steps"]:
                    self.checkpoint(completed)
        self.roles[log_path.name] = "log"
        self.diagnostics["final"] = _finite_state(_state(simulation.context), simulation.system)
        expected = self.diagnostics["initial"]["timePs"] + p["steps"] * p["timestepFs"] / 1000
        require(abs(self.diagnostics["final"]["timePs"] - expected) < max(1e-5, expected * 1e-8), "SIMULATION_TIME_MISMATCH")
        self.diagnostics["stageSteps"] = completed

    def save_state(self):
        self.write_xml("state.xml", _state(self.simulation.context), "state")
        self.write_xml("integrator.xml", self.simulation.integrator, "integrator")
        if not (self.output / "model.json").exists():
            self.write_json("model.json", self.model, "model")

    def save_view_structure(self):
        """Keep immutable topology identity separate from the stage's visible State."""
        import openmm
        from openmm import app
        pdb = app.PDBFile(str(self.output / "topology.pdb"))
        state = openmm.XmlSerializer.deserialize((self.output / "state.xml").read_text())
        require(pdb.topology.getNumAtoms() == len(state.getPositions()), "PARTICLE_COUNT_MISMATCH")
        pdb.topology.setPeriodicBoxVectors(state.getPeriodicBoxVectors())
        with (self.output / "view-structure.pdb").open("x") as stream:
            app.PDBFile.writeFile(pdb.topology, state.getPositions(), stream)
        self.roles["view-structure.pdb"] = "view-structure"

    def copy_parent(self):
        root, manifest, model = self.parent()
        self.model = model
        self.compatibility = manifest.get("checkpointCompatibility")
        for entry in manifest["files"]:
            if entry["role"] in ("model", "topology", "system", "integrator", "state", "checkpoint", "trajectory", "trajectory-times"):
                destination = safe_path(self.output, entry["path"])
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(safe_path(root, entry["path"]), destination)
                self.roles[entry["path"]] = entry["role"]
        return root, manifest

    def validate(self):
        import openmm
        from openmm import app
        root, _ = self.copy_parent()
        system = openmm.XmlSerializer.deserialize((root / "system.xml").read_text())
        integrator = openmm.XmlSerializer.deserialize((root / "integrator.xml").read_text())
        state = openmm.XmlSerializer.deserialize((root / "state.xml").read_text())
        pdb = app.PDBFile(str(root / "topology.pdb"))
        require(pdb.topology.getNumAtoms() == system.getNumParticles() == self.model["particles"], "PARTICLE_COUNT_MISMATCH")
        selected, properties = _platform(self.spec["resources"])
        simulation = app.Simulation(pdb.topology, system, integrator, selected, properties)
        simulation.context.setState(state)
        self.diagnostics = _finite_state(_state(simulation.context), system)

    def analysis(self):
        import MDAnalysis as mda
        import numpy as np
        from MDAnalysis.lib.mdamath import make_whole
        root, manifest = self.copy_parent()
        p = self.params["analysis"]
        trajectories = [str(safe_path(root, f["path"])) for f in manifest["files"] if f["role"] == "trajectory"]
        require(bool(trajectories), "TRAJECTORY_MISSING")
        universe = mda.Universe(str(root / "topology.pdb"), trajectories)
        bonds = self.model.get("bonds")
        require(isinstance(bonds, list) and universe.atoms.n_atoms == self.model["particles"], "INVALID_MODEL_BONDS")
        require(all(isinstance(pair, list) and len(pair) == 2 and
                    all(type(i) is int and 0 <= i < universe.atoms.n_atoms for i in pair) and pair[0] != pair[1]
                    for pair in bonds), "INVALID_MODEL_BONDS")
        require(len({tuple(sorted(pair)) for pair in bonds}) == len(bonds), "INVALID_MODEL_BONDS")
        universe.add_bonds(bonds)
        atoms = universe.select_atoms(p["selection"])
        alignment = universe.select_atoms(p["alignmentSelection"])
        require(len(atoms) > 0 and len(alignment) >= 3, "INVALID_ANALYSIS_SELECTION")
        require(np.all(np.isfinite(atoms.masses)) and np.all(atoms.masses > 0), "INVALID_ANALYSIS_MASSES")
        selected_fragment = None
        if p["pbc"] == "unwrap":
            fragments = set(atoms.fragindices.tolist()) | set(alignment.fragindices.tolist())
            require(len(fragments) == 1, "MULTI_FRAGMENT_UNWRAP_UNSUPPORTED")
            selected_fragment = universe.atoms.fragments[next(iter(fragments))]
        mean = np.zeros((len(atoms), 3), dtype=np.float64)
        m2 = np.zeros_like(mean)
        reference = None
        reference_align = None
        count = 0
        def actual_times():
            for trajectory in trajectories:
                timing = Path(trajectory).with_suffix(".times.csv")
                require(any(f["path"] == timing.relative_to(root).as_posix() for f in manifest["files"]), "TRAJECTORY_TIMES_MISSING")
                with timing.open(newline="") as source:
                    for row in csv.DictReader(source):
                        yield float(row["timePs"])
        time_source = actual_times()
        previous_time = -1.0
        coverage_start = None
        with (self.output / "analysis.csv").open("x", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["frame", "timePs", "rmsdAngstrom", "radiusOfGyrationAngstrom"])
            for frame_number, ts in enumerate(universe.trajectory):
                require(not self.canceled(), "CANCELED")
                frame_time = next(time_source, None)
                require(frame_time is not None and math.isfinite(frame_time) and frame_time > previous_time, "INVALID_TRAJECTORY_TIMES")
                previous_time = frame_time
                if coverage_start is None:
                    coverage_start = frame_time
                if frame_number % p["stride"]:
                    continue
                if p["pbc"] == "unwrap":
                    require(ts.dimensions is not None and np.all(ts.dimensions[:3] > 0), "UNWRAP_REQUIRES_BOX")
                    try:
                        make_whole(selected_fragment)
                    except (ValueError, mda.exceptions.NoDataError) as error:
                        raise WorkflowError("UNWRAP_REQUIRES_BONDS") from error
                position = atoms.positions.astype(np.float64).copy()
                align_position = alignment.positions.astype(np.float64).copy()
                require(np.isfinite(position).all() and np.isfinite(align_position).all(), "NONFINITE_TRAJECTORY")
                center = align_position.mean(axis=0)
                centered = align_position - center
                if reference is None:
                    reference_align = centered.copy()
                    reference = position - center
                left, _, right = np.linalg.svd(centered.T @ reference_align)
                correction = np.eye(3)
                correction[2, 2] = np.linalg.det(left @ right)
                rotation = left @ correction @ right
                fitted = (position - center) @ rotation
                rmsd = float(np.sqrt(np.mean(np.sum((fitted - reference) ** 2, axis=1))))
                mass_center = np.average(position, axis=0, weights=atoms.masses)
                rg = float(np.sqrt(np.average(np.sum((position - mass_center) ** 2, axis=1), weights=atoms.masses)))
                count += 1
                delta = fitted - mean
                mean += delta / count
                m2 += delta * (fitted - mean)
                writer.writerow([ts.frame, frame_time, rmsd if "rmsd" in p["metrics"] else "",
                                 rg if "radiusOfGyration" in p["metrics"] else ""])
        require(next(time_source, None) is None, "INVALID_TRAJECTORY_TIMES")
        require(count > 0, "EMPTY_TRAJECTORY")
        self.roles["analysis.csv"] = "analysis"
        if "rmsf" in p["metrics"]:
            with (self.output / "rmsf.csv").open("x", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["atomIndex", "rmsfAngstrom", "atomName", "residueName",
                                 "modelResidueIndex", "modelResidueId", "modelChainId", "modelInsertionCode"])
                # Identity belongs to the accepted topology used above. Model
                # numbering may differ from the original downloaded structure.
                for atom, fluctuation in zip(atoms, np.sqrt(m2.sum(axis=1) / count).tolist()):
                    writer.writerow([atom.index, fluctuation, atom.name, atom.resname,
                                     atom.resindex, atom.resid, atom.chainID.strip(), atom.icode.strip()])
            self.roles["rmsf.csv"] = "analysis"
        self.diagnostics = {"frames": count, "selectionAtoms": len(atoms), "alignmentAtoms": len(alignment),
                            "configuration": p, "alignment": "unweighted Kabsch to first selected frame",
                            "rmsfEstimator": "population variance using Welford; aligned coordinates",
                            "pbcTreatment": "per-bonded-fragment make_whole each frame" if p["pbc"] == "unwrap" else "none",
                            "coverageStartPs": coverage_start, "coverageEndPs": previous_time,
                            "trajectoryScope": "only supplied dependency trajectory segments; not full workflow history",
                            "timeSource": "exact per-segment OpenMM State time sidecars; not rounded DCD headers",
                            "convergenceAssessed": False}
        if "radiusOfGyration" in p["metrics"]:
            self.diagnostics["radiusOfGyrationDefinition"] = {
                "weighting": "atomic-mass", "center": "center-of-mass", "unit": "angstrom",
                "massSource": "MDAnalysis topology masses"}
        if "rmsf" in p["metrics"]:
            self.diagnostics["rmsfIdentity"] = {"numbering": "accepted model topology", "indexBase": 0,
                "rmsfUnit": "angstrom", "sourcePdbNumberingInferred": False,
                "residueKey": ["modelChainId", "modelResidueId", "modelInsertionCode"],
                "uniqueResidueIndex": "modelResidueIndex"}

    def run(self):
        kind = self.spec["kind"]
        require(not self.canceled(), "CANCELED")
        if kind in ("equilibrate", "production"):
            self.dynamics()
        else:
            {"prepare": self.prepare, "minimize": self.minimize,
             "validate": self.validate, "analysis": self.analysis}[kind]()
        require(not self.canceled(), "CANCELED")
        if self.simulation is not None:
            self.save_state()
        self.save_view_structure()
        self.write_json("diagnostics.json", self.diagnostics, "report")
        self.stream()
        manifest = {"schemaVersion": "ksh.stage-result.v1", "workflowId": self.request["workflowId"],
                    "revision": self.request["revision"], "stageId": self.spec["id"], "attemptId": self.request["attemptId"],
                    "modelId": self.model["modelId"], "stateId": digest(self.output / "state.xml"),
                    "protocolSha256": digest(self.output / "protocol.json"), "provenance": self.environment,
                    "checkpointCompatibility": self.compatibility,
                    "parentBundles": self.request["dependencyBundles"],
                    "acceptance": {"engineering": True, "scientificConvergence": False},
                    "files": [file_record(self.output, self.output / name, role) for name, role in self.roles.items()]}
        atomic_json(self.output / "manifest.json", manifest)
        verify_manifest(self.output / "manifest.json")
