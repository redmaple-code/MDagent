"""Read-only environment and static input audit in the selected interpreter."""
from __future__ import annotations

from pathlib import Path
import sys

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import subprocess

from bioagent.errors import BioError, canonical, require, safe_file, sha256_file
from bioagent.provenance import PRODUCT_ROOT, runtime_root, verify_runtime
from bioagent.schema import load_runner, validate_protocol

_hash_cache = None


def environment_hash(file: Path) -> str:
    return _hash_cache.digest(file) if _hash_cache is not None else sha256_file(file)


def implementation_files(component: str, package_root: Path) -> list[dict]:
    """Stable installed implementation and data identity, excluding caches."""
    return [{"component": component + "-implementation", "path": str(file.resolve()), "sha256": environment_hash(file)}
            for file in sorted(package_root.rglob("*")) if file.is_file()
            and "__pycache__" not in file.parts and file.suffix not in {".pyc", ".pyo"}]


def cuda_environment(protocol: dict) -> tuple[list[dict], list[str]]:
    selected = sorted({(s["resources"]["deviceIndex"], s["resources"]["precision"])
                       for s in protocol["stages"] if s["resources"]["platform"] == "CUDA"})
    if not selected: return [], []
    if platform.system() != "Linux": return [], ["CUDA_REQUIRES_LINUX"]
    load_runner()
    from openmm_workflow.stages import _hardware_identity
    records, by_device = [], {}
    try:
        for index, precision in selected:
            if index not in by_device:
                by_device[index] = _hardware_identity("CUDA", {"DeviceIndex": str(index), "Precision": precision})
            records.append({"deviceIndex": index, "precision": precision, "hardware": by_device[index]})
    except Exception:
        return [], ["CUDA_HARDWARE_IDENTITY_UNAVAILABLE"]
    return records, []


def _probe(protocol: dict) -> dict:
    runner = load_runner()
    kinds = {s["kind"] for s in protocol["stages"]}
    amber = any(s["kind"] == "prepare" and s["parameters"]["preparation"].get("mode", "").startswith("amber-")
                for s in protocol["stages"])
    packages, files, blockers = {}, [], []
    wanted = {"openmm", "numpy"} | ({"MDAnalysis"} if "analysis" in kinds else set())
    if amber:
        wanted |= {"rdkit", "parmed", "pdbfixer"}
    for name in ("openmm", "numpy", "MDAnalysis", "rdkit", "parmed", "pdbfixer"):
        try:
            specification = importlib.util.find_spec(name)
            version = importlib.metadata.version(name)
        except (ImportError, importlib.metadata.PackageNotFoundError):
            specification, version = None, None
        packages[name] = version
        if specification and specification.origin:
            source = Path(specification.origin)
            if source.is_file():
                files.append({"component": name, "path": str(source), "sha256": environment_hash(source)})
        if name in wanted:
            try:
                importlib.import_module(name)
            except ImportError:
                blockers.append("DEPENDENCY_MISSING_" + name.upper())
            if specification and specification.submodule_search_locations:
                for package_root in specification.submodule_search_locations:
                    files.extend(implementation_files(name, Path(package_root)))
    platforms = []
    if packages["openmm"] is not None:
        import openmm
        platforms = [openmm.Platform.getPlatform(i).getName() for i in range(openmm.Platform.getNumPlatforms())]
        native = sorted(Path(openmm.__file__).parent.glob("*.so"))
        native += sorted((Path(sys.prefix) / "lib").glob("libOpenMM*"))
        native += sorted((Path(sys.prefix) / "lib/plugins").glob("*OpenMM*"))
        for file in native:
            if file.is_file():
                files.append({"component": "openmm-native", "path": str(file), "sha256": environment_hash(file)})
        from openmm import app
        data = Path(app.__file__).parent / "data"
        for file in sorted(data.rglob("*.xml")):
            files.append({"component": "openmm-forcefield", "path": str(file), "sha256": environment_hash(file)})
    if "CPU" not in platforms:
        blockers.append("CPU_PLATFORM_MISSING")
    if any(s["resources"]["platform"] == "CUDA" for s in protocol["stages"]) and "CUDA" not in platforms:
        blockers.append("CUDA_PLATFORM_MISSING")
    tools = all((Path(sys.prefix) / "bin" / name).is_file()
                for name in ("antechamber", "parmchk2", "tleap", "teLeap", "sqm", "am1bcc", "atomtype", "bondtype"))
    toolchain = None
    if amber:
        try:
            from openmm_workflow.amber_preparation import toolchain_inventory
            toolchain = toolchain_inventory()
            for s in protocol["stages"]:
                p = s["parameters"].get("preparation", {})
                if p.get("mode", "").startswith("amber-"):
                    if p["toolchainSha256"] != toolchain["sha256"]:
                        blockers.append("AMBER_TOOLCHAIN_CHANGED")
                    _audit_ligand(p)
        except runner.WorkflowError as error:
            blockers.append(str(error))
        except BioError as error:
            blockers.append(error.code)
        except ImportError:
            blockers.append("MODELING_MODULES_REQUIRED")
    cuda_hardware, cuda_blockers = cuda_environment(protocol)
    blockers.extend(cuda_blockers)
    identity = {"interpreter": sys.executable, "interpreterSha256": sha256_file(Path(sys.executable).resolve()),
                "pythonVersion": platform.python_version(), "system": platform.system(), "machine": platform.machine(),
                "kernel": platform.release(), "packages": packages, "files": files, "platforms": platforms,
                "cudaHardware": cuda_hardware,
                "openmmFullVersion": openmm.version.full_version if packages["openmm"] is not None else None,
                "toolchainSha256": toolchain["sha256"] if toolchain else None}
    return {"environment": identity, "environmentSha256": hashlib.sha256(canonical(identity)).hexdigest(),
            "toolchain": toolchain, "capabilities": {"dynamics": "CPU" in platforms and packages["numpy"] is not None,
            "cuda": "CUDA" in platforms, "availablePlatforms": platforms,
            "analysis": packages["MDAnalysis"] is not None, "amberPreparation": tools and all(packages[n] is not None
            for n in ("rdkit", "parmed", "pdbfixer"))}, "blockers": sorted(set(blockers))}


def _audit_ligand(preparation: dict) -> None:
    """Only inspect a graph; do not embed, optimize, parameterize, or repair it."""
    from rdkit import Chem
    ligand = preparation["ligand"]
    molecule = Chem.MolFromSmiles(ligand["smiles"])
    require(molecule is not None and len(Chem.GetMolFrags(molecule)) == 1, "INVALID_LIGAND_GRAPH")
    require(Chem.GetFormalCharge(molecule) == ligand["netCharge"]
            and not any(a.GetNumRadicalElectrons() or a.GetIsotope() for a in molecule.GetAtoms())
            and all(a.GetAtomicNum() in (1, 6, 7, 8, 9, 15, 16, 17, 35, 53) for a in molecule.GetAtoms()),
            "UNSUPPORTED_LIGAND_CHEMISTRY")
    require(not any(label == "?" for _, label in Chem.FindMolChiralCenters(molecule, includeUnassigned=True)),
            "LIGAND_STEREOCHEMISTRY_REQUIRED")
    require(not any(str(element.specified) == "Unspecified" for element in Chem.FindPotentialStereo(molecule)),
            "LIGAND_STEREOCHEMISTRY_REQUIRED")
    molecule = Chem.AddHs(molecule)
    electrons = sum(a.GetAtomicNum() for a in molecule.GetAtoms()) - ligand["netCharge"]
    require(1 <= molecule.GetNumAtoms() <= 128 and electrons > 0 and electrons % 2 == 0, "UNSUPPORTED_LIGAND_CHEMISTRY")


def probe_environment(python: Path, protocol: dict, *, cache_path: Path | None = None) -> dict:
    require(python.is_file() and os.access(python, os.X_OK), "INTERPRETER_MISSING")
    verify_runtime()
    environment = {key: value for key, value in os.environ.items() if key in ("HOME", "TMPDIR", "LANG", "LC_ALL")}
    environment["PATH"] = str(python.parent) + ":/usr/bin:/bin"
    try:
        args = [str(python), "-I", "-B", str(Path(__file__).resolve()), "--probe"]
        if cache_path is not None: args += ["--cache", str(cache_path)]
        result = subprocess.run(args,
                                input=canonical(protocol), capture_output=True, timeout=60, env=environment)
    except subprocess.TimeoutExpired:
        raise BioError("ENVIRONMENT_PROBE_TIMEOUT") from None
    except (OSError, subprocess.SubprocessError):
        raise BioError("ENVIRONMENT_PROBE_FAILED") from None
    require(result.returncode == 0 and len(result.stdout) <= 8 * 1024 * 1024, "ENVIRONMENT_PROBE_FAILED")
    try:
        response = json.loads(result.stdout)
        require(isinstance(response, dict) and isinstance(response.get("blockers"), list), "ENVIRONMENT_PROBE_FAILED")
        require(all(isinstance(code, str) for code in response["blockers"]), "ENVIRONMENT_PROBE_FAILED")
        if not response["blockers"]:
            require(isinstance(response.get("environment"), dict) and isinstance(response.get("capabilities"), dict)
                    and isinstance(response.get("environmentSha256"), str), "ENVIRONMENT_PROBE_FAILED")
        return response
    except ValueError:
        raise BioError("ENVIRONMENT_PROBE_FAILED") from None


def preflight(project: Path, protocol: dict, python: Path) -> dict:
    result = {"ready": False, "blockers": [], "capabilities": {}, "inputSetSha256": None,
              "runtimeSha256": None, "environmentSha256": None}
    try:
        protocol = validate_protocol(protocol)
        result["runtimeSha256"] = verify_runtime()["runtimeSha256"]
        records = []
        for entry in protocol["inputs"]:
            path = safe_file(project / "inputs", entry["path"])
            require(path.is_file(), "INPUT_MISSING")
            require(path.stat().st_size <= 64 * 1024 * 1024, "INPUT_TOO_LARGE")
            record = {"id": entry["id"], "path": entry["path"], "sha256": sha256_file(path), "byteSize": path.stat().st_size}
            records.append(record)
            if record["sha256"] != entry["sha256"] or record["byteSize"] != entry["byteSize"]:
                result["blockers"].append("INPUT_CHANGED")
            elif entry["role"] == "structure":
                from openmm_workflow.input_audit import scan_pdb
                try:
                    audit = scan_pdb(path.read_bytes(), allow_ssbond=True)
                    supported = set("ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE LEU LYS MET PHE PRO SER THR TRP TYR VAL HID HIE HIP CYX ASH GLH LYN HOH WAT NA CL Na Cl".split())
                    if set(audit["residues"].values()) - supported:
                        result["blockers"].append("UNSUPPORTED_RESIDUE")
                except ValueError:
                    result["blockers"].append("PDB_INTAKE_FAILED")
        result["inputSetSha256"] = hashlib.sha256(canonical(sorted(records, key=lambda r: r["id"]))).hexdigest()
        environment = probe_environment(python, protocol, cache_path=safe_file(project, "chat/environment-hashes.json"))
        result.update({key: value for key, value in environment.items() if key != "blockers"})
        result["blockers"].extend(environment["blockers"])
        # Detect an input edited while the environment was being read.
        for record in records:
            if sha256_file(safe_file(project / "inputs", record["path"])) != record["sha256"]:
                result["blockers"].append("INPUT_CHANGED")
    except BioError as error:
        result["blockers"].append(error.code)
    except OSError:
        result["blockers"].append("INPUT_OR_ENVIRONMENT_UNAVAILABLE")
    result["blockers"] = sorted(set(result["blockers"]))
    result["ready"] = not result["blockers"]
    return result


if __name__ == "__main__":
    if sys.argv[1:] != ["--probe"] and not (len(sys.argv) == 4 and sys.argv[1:3] == ["--probe", "--cache"]):
        raise SystemExit(2)
    try:
        if len(sys.argv) == 4:
            from bioagent.hash_cache import FileHashCache
            _hash_cache = FileHashCache(Path(sys.argv[3]))
        data = sys.stdin.buffer.read(2 * 1024 * 1024 + 1)
        require(len(data) <= 2 * 1024 * 1024, "INVALID_PROTOCOL")
        response = _probe(json.loads(data))
        if _hash_cache is not None: _hash_cache.save()
    except Exception:
        response = {"blockers": ["ENVIRONMENT_PROBE_FAILED"], "capabilities": {}}
    sys.stdout.buffer.write(canonical(response))
