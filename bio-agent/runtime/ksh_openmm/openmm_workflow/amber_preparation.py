"""Fixed AmberTools protein/ligand and independent organic-molecule profiles.

Commands and search roots are owned code. The request never supplies a shell,
executable, LEaP program or force-field path. Original inputs remain immutable.
"""
from __future__ import annotations

from collections import Counter
import io
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from .common import WorkflowError, canonical, digest, require

AMINO = set("ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE LEU LYS MET PHE PRO SER THR TRP TYR VAL HID HIE HIP ASH GLH LYN".split())
PROFILE = "ff14SB-gaff2-tip3p"
SMALL_MOLECULE_PROFILE = "gaff2-tip3p"


def validate_preparation(p: dict) -> None:
    standalone = p.get("mode") == "amber-small-molecule"
    fields = {"mode", "forceField", "seed", "ligand", "solvent", "toolchainSha256"}
    if not standalone:
        fields |= {"proteinChains", "missingResidues", "histidines"}
    require(set(p) == fields and p["mode"] == ("amber-small-molecule" if standalone else "amber-protein-ligand")
            and p["forceField"] == (SMALL_MOLECULE_PROFILE if standalone else PROFILE), "INVALID_PREPARATION")
    require(isinstance(p["toolchainSha256"], str) and re.fullmatch(r"[a-f0-9]{64}", p["toolchainSha256"]) is not None,
            "INVALID_TOOLCHAIN_IDENTITY")
    require(type(p["seed"]) is int and 1 <= p["seed"] <= 2147483647, "INVALID_PREPARATION")
    if not standalone:
        require(isinstance(p["proteinChains"], list) and 1 <= len(p["proteinChains"]) <= 8
                and len(set(p["proteinChains"])) == len(p["proteinChains"])
                and all(isinstance(c, str) and len(c) == 1 and c.isalnum() for c in p["proteinChains"]), "INVALID_PROTEIN_CHAINS")
        require(p["missingResidues"] in ("reject", "complete-standard"), "INVALID_PREPARATION")
        his = p["histidines"]
        require(isinstance(his, list) and len(his) <= 128, "INVALID_HISTIDINES")
        keys = []
        for h in his:
            require(isinstance(h, dict) and set(h) == {"chain", "residueId", "variant"}
                    and h["chain"] in p["proteinChains"] and isinstance(h["residueId"], str)
                    and re.fullmatch(r"-?[0-9]+", h["residueId"]) is not None
                    and h["variant"] in ("HID", "HIE", "HIP"), "INVALID_HISTIDINES")
            keys.append((h["chain"], h["residueId"]))
        require(len(set(keys)) == len(keys), "INVALID_HISTIDINES")
    ligand = p["ligand"]
    ligand_fields = {"smiles", "netCharge", "multiplicity", "chargeMethod", "chargeRationale"}
    if not standalone:
        ligand_fields.add("placement")
    require(isinstance(ligand, dict) and set(ligand) == ligand_fields
            and isinstance(ligand["smiles"], str) and 0 < len(ligand["smiles"]) <= 1024
            and type(ligand["netCharge"]) is int
            and (abs(ligand["netCharge"]) <= 4 if standalone else ligand["netCharge"] == 0)
            and type(ligand["multiplicity"]) is int and ligand["multiplicity"] == 1
            and ligand["chargeMethod"] in ("abcg2", "bcc")
            and isinstance(ligand["chargeRationale"], str) and 0 < len(ligand["chargeRationale"].strip()) <= 2048, "INVALID_LIGAND")
    if not standalone:
        placement = ligand["placement"]
        require(isinstance(placement, dict) and set(placement) == {"mode", "gapAngstrom"}
                and placement["mode"] == "outside-protein-positive-x"
                and type(placement["gapAngstrom"]) in (float, int) and 5 <= placement["gapAngstrom"] <= 30, "INVALID_LIGAND_PLACEMENT")
    water = p["solvent"]
    require(isinstance(water, dict) and set(water) == {"mode", "paddingAngstrom", "ionicStrengthMolar", "neutralize"}
            and water["mode"] == "tip3p-box" and type(water["paddingAngstrom"]) in (int, float)
            and 10 <= water["paddingAngstrom"] <= 30 and water["ionicStrengthMolar"] == 0
            and water["neutralize"] is (not standalone or ligand["netCharge"] != 0), "INVALID_SOLVENT")


def toolchain_inventory() -> dict:
    """Content identity, including helper programs and installed parameter data."""
    import hashlib
    prefix = Path(sys.prefix).resolve()
    tools = ("antechamber", "parmchk2", "tleap", "teLeap", "sqm", "am1bcc", "atomtype", "bondtype")
    missing = [name for name in tools if not (prefix / "bin" / name).is_file()]
    require(not missing, "AMBERTOOLS_REQUIRED")
    paths = {prefix / "bin" / name for name in tools}
    for name in ("dat/antechamber", "dat/leap"):
        directory = prefix / name
        require(directory.is_dir(), "AMBER_DATA_REQUIRED")
        paths.update(p for p in directory.rglob("*") if p.is_file())
    records = []
    require(len(paths) <= 10000, "AMBER_INVENTORY_TOO_LARGE")
    for path in sorted(paths):
        require(path.resolve().is_relative_to(prefix) and path.stat().st_size <= 64 * 1024 * 1024, "AMBER_DATA_OUTSIDE_RUNTIME")
        records.append({"path": path.relative_to(prefix).as_posix(), "sha256": digest(path), "byteSize": path.stat().st_size})
    require(sum(r["byteSize"] for r in records) <= 256 * 1024 * 1024, "AMBER_INVENTORY_TOO_LARGE")
    return {"prefix": str(prefix), "files": records, "sha256": hashlib.sha256(canonical(records)).hexdigest()}


def preparation_profile() -> dict:
    import importlib.metadata
    result = {"profileId": PROFILE, "supportedProfiles": [PROFILE, SMALL_MOLECULE_PROFILE], "available": False}
    try:
        inventory = toolchain_inventory()
        versions = {name: importlib.metadata.version(name) for name in ("rdkit", "parmed", "pdbfixer")}
        result.update(available=True, toolchainSha256=inventory["sha256"], versions=versions)
    except WorkflowError as error:
        result["reason"] = str(error)
    except importlib.metadata.PackageNotFoundError:
        result["reason"] = "MODELING_MODULES_REQUIRED"
    return result


def ligand_molecule(p: dict):
    from rdkit import Chem
    from rdkit.Chem import AllChem
    molecule = Chem.MolFromSmiles(p["ligand"]["smiles"])
    require(molecule is not None and len(Chem.GetMolFrags(molecule)) == 1, "INVALID_LIGAND_GRAPH")
    charge = p["ligand"]["netCharge"]
    require(type(charge) is int and (abs(charge) <= 4 if p["mode"] == "amber-small-molecule" else charge == 0)
            and Chem.GetFormalCharge(molecule) == charge
            and not any(a.GetNumRadicalElectrons() or a.GetIsotope() for a in molecule.GetAtoms())
            and all(a.GetAtomicNum() in (1, 6, 7, 8, 9, 15, 16, 17, 35, 53) for a in molecule.GetAtoms()), "UNSUPPORTED_LIGAND_CHEMISTRY")
    require(not any(label == "?" for _, label in Chem.FindMolChiralCenters(molecule, includeUnassigned=True)),
            "LIGAND_STEREOCHEMISTRY_REQUIRED")
    molecule = Chem.AddHs(molecule)
    electrons = sum(a.GetAtomicNum() for a in molecule.GetAtoms()) - charge
    require(1 <= molecule.GetNumAtoms() <= 128 and p["ligand"]["multiplicity"] == 1
            and electrons > 0 and electrons % 2 == 0, "UNSUPPORTED_LIGAND_CHEMISTRY")
    options = AllChem.ETKDGv3()
    options.randomSeed = p["seed"]
    options.numThreads = 1
    require(AllChem.EmbedMolecule(molecule, options) == 0, "LIGAND_EMBEDDING_FAILED")
    require(AllChem.MMFFHasAllMoleculeParams(molecule), "LIGAND_GEOMETRY_PARAMETERS_MISSING")
    require(AllChem.MMFFOptimizeMolecule(molecule, maxIters=1000) == 0, "LIGAND_GEOMETRY_NOT_CONVERGED")
    # MMFF changes aromaticity flags, including fused heterocycles. Restore
    # the RDKit representation used by saved SDF/Mol2 graph checks; this leaves
    # optimized coordinates and the actual Amber parameter files unchanged.
    require(Chem.SanitizeMol(molecule, catchErrors=True) == Chem.SanitizeFlags.SANITIZE_NONE,
            "LIGAND_GRAPH_CHANGED")
    return molecule


def validate_solvent_composition(parm, charge: int) -> dict:
    """One organic solute, TIP3P water and exactly its monovalent counterions."""
    counts = dict(Counter(residue.name for residue in parm.residues))
    expected = {"Na+": max(0, -charge), "Cl-": max(0, charge)}
    require(set(counts) <= {"LIG", "WAT", "Na+", "Cl-"} and counts.get("LIG") == 1
            and counts.get("WAT", 0) > 0
            and all(counts.get(name, 0) == count for name, count in expected.items()),
            "INDEPENDENT_SOLVENT_COMPOSITION_MISMATCH")
    ions = []
    for residue in parm.residues:
        if residue.name == "LIG":
            require(abs(sum(atom.charge for atom in residue.atoms) - charge) < 1e-4,
                    "LIGAND_COORDINATE_OR_CHARGE_MISMATCH")
        elif residue.name == "WAT":
            require(sorted(a.atomic_number for a in residue.atoms) == [1, 1, 8]
                    and abs(sum(a.charge for a in residue.atoms)) < 1e-6, "INDEPENDENT_SOLVENT_COMPOSITION_MISMATCH")
        else:
            number, ion_charge = (11, 1) if residue.name == "Na+" else (17, -1)
            require(len(residue.atoms) == 1 and residue.atoms[0].atomic_number == number
                    and abs(residue.atoms[0].charge - ion_charge) < 1e-6, "COUNTERION_IDENTITY_MISMATCH")
            ions.append({"name": residue.name, "particleIndex": residue.atoms[0].idx,
                         "atomicNumber": number, "charge": residue.atoms[0].charge})
    require(abs(sum(a.charge for a in parm.atoms)) < 1e-3, "SYSTEM_NOT_NEUTRAL")
    return {"counterionCounts": expected, "counterionParticles": ions}


def verify_ligand_graph(molecule, mol2: str, amber_bonds: str = "") -> dict:
    """Check GAFF output, including proven carboxylate type-9 bond encoding.

    Amber's Mol2 export can lose the localized order of its carboxylate bonds.
    Recover that representation only for the in-memory RDKit check, after the
    owned AC table proves both delocalized bonds, atom identities and charge.
    The actual Mol2 coordinates, charges and parameter input remain unchanged.
    """
    from rdkit import Chem
    try:
        lines = mol2.splitlines()
        atom_start = lines.index("@<TRIPOS>ATOM") + 1
        bond_start = lines.index("@<TRIPOS>BOND") + 1
        records = [line.split() for line in lines[atom_start:bond_start - 1] if line.strip()]
        require(len(records) == molecule.GetNumAtoms()
                and [int(r[0]) for r in records] == list(range(1, len(records) + 1)), "LIGAND_ATOM_MAPPING_MISMATCH")
        def element(value):
            return {"cl": "Cl", "br": "Br", "f": "F", "i": "I"}.get(value,
                {"c": "C", "h": "H", "n": "N", "o": "O", "s": "S", "p": "P"}.get(value[:1]))
        require([element(r[5]) for r in records] == [a.GetSymbol() for a in molecule.GetAtoms()],
                "LIGAND_ELEMENT_MAPPING_MISMATCH")
        expected = {tuple(sorted((b.GetBeginAtomIdx(), b.GetEndAtomIdx()))): b.GetBondTypeAsDouble()
                    for b in molecule.GetBonds()}
        observed = {}
        for index in range(bond_start, len(lines)):
            if lines[index].startswith("@"):
                break
            if not lines[index].strip():
                continue
            row = lines[index].split()
            edge = tuple(sorted((int(row[1]) - 1, int(row[2]) - 1)))
            require(edge not in observed, "LIGAND_GRAPH_CHANGED")
            observed[edge] = (row[3], index)
        require(set(observed) == set(expected), "LIGAND_GRAPH_CHANGED")
        groups = molecule.GetSubstructMatches(Chem.MolFromSmarts("[C;X3](=[O;X1])[O-;X1]"))
        normalized, normalized_groups = [], 0
        for carbon, first, second in groups:
            edges = [tuple(sorted((carbon, oxygen))) for oxygen in (first, second)]
            if not all(observed[edge][0] == "1" for edge in edges):
                continue
            ac_atoms = [row.split() for row in amber_bonds.splitlines() if row.startswith("ATOM ")]
            ac_rows = [row.split() for row in amber_bonds.splitlines() if row.startswith("BOND ")]
            charges = [row.split() for row in amber_bonds.splitlines() if row.startswith("CHARGE ")]
            require(len(ac_atoms) == len(records) and len(ac_rows) == len(expected) and len(charges) == 1
                    and [int(row[1]) for row in ac_atoms] == list(range(1, len(records) + 1))
                    and [row[2] for row in ac_atoms] == [r[1] for r in records]
                    and abs(float(charges[0][1]) - Chem.GetFormalCharge(molecule)) < 1e-6,
                    "LIGAND_RESONANCE_EVIDENCE_MISMATCH")
            ac = {}
            for row in ac_rows:
                edge = tuple(sorted((int(row[2]) - 1, int(row[3]) - 1)))
                require(edge not in ac and 0 <= edge[0] < edge[1] < len(records)
                        and row[5] == records[int(row[2]) - 1][1] and row[6] == records[int(row[3]) - 1][1],
                        "LIGAND_RESONANCE_EVIDENCE_MISMATCH")
                ac[edge] = row[4]
            require(set(ac) == set(expected) and all(ac[edge] == "9" for edge in edges)
                    and records[carbon][5] == "c" and all(records[i][5] == "o" for i in (first, second)),
                    "LIGAND_RESONANCE_EVIDENCE_MISMATCH")
            for edge in edges:
                row = lines[observed[edge][1]].split()
                row[3] = str(int(expected[edge]))
                lines[observed[edge][1]] = " ".join(row)
                normalized.append({"atomIndices": list(edge), "amberBondType": "9", "mol2BondType": "1",
                                   "checkBondOrder": expected[edge]})
            normalized_groups += 1
        # GAFF atom types are not Sybyl element types. This affects only parsing
        # the check block; chemistry and actual Amber files remain authoritative.
        for i, atom in enumerate(molecule.GetAtoms()):
            row = lines[atom_start + i].split()
            row[5] = atom.GetSymbol() + (".ar" if atom.GetIsAromatic() else ".3" if atom.GetAtomicNum() == 6 else "")
            lines[atom_start + i] = " ".join(row)
        parsed = Chem.MolFromMol2Block("\n".join(lines) + "\n", removeHs=False)
        def graph(mol):
            return sorted((*sorted((b.GetBeginAtomIdx(), b.GetEndAtomIdx())), b.GetBondTypeAsDouble()) for b in mol.GetBonds())
        require(parsed is not None and [a.GetAtomicNum() for a in parsed.GetAtoms()] == [a.GetAtomicNum() for a in molecule.GetAtoms()]
                and Chem.GetFormalCharge(parsed) == Chem.GetFormalCharge(molecule)
                and graph(parsed) == graph(molecule), "LIGAND_GRAPH_CHANGED")
        return {"carboxylateResonanceGroups": normalized_groups, "checkOnlyBondNormalization": normalized}
    except (ValueError, IndexError, KeyError) as error:
        raise WorkflowError("LIGAND_GRAPH_CHANGED") from error


def normalize_bcc_charge(molecule, mol2: str, sqm_output: str, pre_bcc: str) -> tuple[str, dict]:
    """Correct only a proven SQM precision residual; preserve the raw Mol2.

    SQM prints Mulliken charges to three decimals. Uniform offsets preserve
    equivalent-atom charges, as in OpenFF's partial-charge normalization.
    Missing evidence or a change larger than that rounding bound stays fatal.
    """
    from rdkit import Chem
    try:
        lines = mol2.splitlines(keepends=True)
        start = next(i for i, line in enumerate(lines) if line.strip() == "@<TRIPOS>ATOM") + 1
        stop = next(i for i in range(start, len(lines)) if lines[i].startswith("@"))
        indices = [i for i in range(start, stop) if lines[i].strip()]
        rows = [lines[i].split() for i in indices]
        count = molecule.GetNumAtoms()
        require(len(rows) == count and [int(row[0]) for row in rows] == list(range(1, count + 1)),
                "LIGAND_ATOM_MAPPING_MISMATCH")
        charges = [float(row[8]) for row in rows]
        require(all(math.isfinite(charge) for charge in charges), "LIGAND_COORDINATE_OR_CHARGE_MISMATCH")
        formal = Chem.GetFormalCharge(molecule)
        original = math.fsum(charges)
        report = {"applied": False, "formalCharge": formal, "originalCharge": original,
                  "normalizedCharge": original, "offsetPerAtom": 0.0}
        if abs(original - formal) < 1e-4:
            return mol2, report
        bound = min(.005, count * (.0005 + .0000005))
        require(abs(original - formal) <= bound, "LIGAND_COORDINATE_OR_CHARGE_MISMATCH")
        blocks = re.findall(r"Atom\s+Element\s+Mulliken Charge\s*\n(.*?)Total Mulliken Charge\s*=\s*([+-]?\d+\.\d+)",
                            sqm_output, re.DOTALL)
        require(blocks and "--------- Calculation Completed ----------" in sqm_output,
                "LIGAND_CHARGE_ROUNDING_EVIDENCE_MISMATCH")
        quantum = [line.split() for line in blocks[-1][0].splitlines() if line.strip()]
        ac = [line.split() for line in pre_bcc.splitlines() if line.startswith("ATOM ")]
        ac_charge = [line.split() for line in pre_bcc.splitlines() if line.startswith("CHARGE ")]
        require(len(quantum) == count and all(len(row) == 3 for row in quantum)
                and [int(row[0]) for row in quantum] == list(range(1, count + 1))
                and [row[1] for row in quantum] == [atom.GetSymbol() for atom in molecule.GetAtoms()]
                and all(re.fullmatch(r"[+-]?\d+\.\d{3}", row[2]) for row in quantum)
                and math.isfinite(float(blocks[-1][1])) and abs(float(blocks[-1][1]) - formal) < 1e-6
                and len(ac) == count and [int(row[1]) for row in ac] == list(range(1, count + 1))
                and [row[2] for row in ac] == [row[1] for row in rows]
                and len(ac_charge) == 1 and abs(float(ac_charge[0][1]) - formal) < 1e-6,
                "LIGAND_CHARGE_ROUNDING_EVIDENCE_MISMATCH")
        pre_charges = [float(row[8]) for row in ac]
        quantum_sum = math.fsum(float(row[2]) for row in quantum)
        pre_sum = math.fsum(pre_charges)
        require(all(math.isfinite(charge) for charge in pre_charges)
                and abs(quantum_sum - formal) <= count * .0005 + 1e-9
                and abs(pre_sum - quantum_sum) <= count * .0000005 + 1e-9
                and abs(original - pre_sum) <= count * .0000005 + 1e-9,
                "LIGAND_CHARGE_ROUNDING_EVIDENCE_MISMATCH")
        offset = (formal - original) / count
        corrected = [float(f"{charge + offset:.12f}") for charge in charges]
        require(abs(math.fsum(corrected) - formal) < 1e-9, "LIGAND_COORDINATE_OR_CHARGE_MISMATCH")
        for index, charge in zip(indices, corrected):
            match = re.fullmatch(r"(\s*(?:\S+\s+){8})(\S+)([^\r\n]*)(\r?\n)?", lines[index])
            require(match is not None, "LIGAND_ATOM_MAPPING_MISMATCH")
            lines[index] = match[1] + f"{charge:.12f}" + match[3] + (match[4] or "")
        report.update(applied=True, normalizedCharge=math.fsum(corrected), offsetPerAtom=offset,
                      roundingBound=bound, method="uniform-offset-verified-sqm-rounding",
                      roundedMullikenCharge=quantum_sum, preBccCharge=pre_sum)
        return "".join(lines), report
    except (ValueError, IndexError, StopIteration) as error:
        raise WorkflowError("LIGAND_CHARGE_ROUNDING_EVIDENCE_MISMATCH") from error


def receptor(stage, source: Path, p: dict):
    from openmm import app, unit
    from pdbfixer import PDBFixer
    from .input_audit import scan_pdb
    lines = source.read_text(encoding="ascii").splitlines()
    require(not any(l.startswith(("MODEL ", "SSBOND")) for l in lines), "PROTEIN_MODEL_OR_DISULFIDE_REQUIRES_REVIEW")
    selected, excluded, source_keys = [], Counter(), set()
    for line in lines:
        if line.startswith("SEQRES") and line[11:12] in p["proteinChains"]:
            selected.append(line)
        elif line.startswith(("ATOM  ", "HETATM")):
            if line.startswith("ATOM  ") and line[21:22] in p["proteinChains"]:
                require(line[17:20] in AMINO, "NONSTANDARD_PROTEIN_RESIDUE")
                require(not line[26:27].strip(), "INSERTION_CODE_REQUIRES_REVIEW")
                # Full hydrogens are regenerated under the chosen Amber profile.
                element = line[76:78].strip()
                if element == "H":
                    excluded["source-hydrogen"] += 1
                else:
                    selected.append(line)
                    source_keys.add((line[21:22], line[22:26].strip(), line[12:16].strip()))
            else:
                excluded[line[17:20].strip()] += 1
    selected.append("END")
    data = ("\n".join(selected) + "\n").encode("ascii")
    scan_pdb(data)  # No occupancy/alternate-location rewriting of retained atoms.
    fixer = PDBFixer(pdbfile=io.StringIO(data.decode("ascii")))
    fixer.findMissingResidues()
    missing = [{"chainIndex": k[0], "insertionIndex": k[1], "residues": v} for k, v in fixer.missingResidues.items()]
    require(not missing or p["missingResidues"] == "complete-standard", "MISSING_RESIDUES_REQUIRE_DECISION")
    require(all(name in AMINO for item in missing for name in item["residues"])
            and sum(len(item["residues"]) for item in missing) <= 10, "MISSING_RESIDUES_OUTSIDE_PROFILE")
    fixer.findMissingAtoms()
    added = [{"chain": r.chain.id, "residueId": r.id, "atomNames": [a.name for a in atoms]}
             for r, atoms in fixer.missingAtoms.items()]
    fixer.addMissingAtoms(seed=p["seed"])
    requested = {(h["chain"], h["residueId"]): h["variant"] for h in p["histidines"]}
    used = set()
    for residue in fixer.topology.residues():
        if residue.name == "HIS":
            key = (residue.chain.id, residue.id)
            require(key in requested, "HISTIDINE_VARIANT_REQUIRED")
            residue.name = requested[key]
            used.add(key)
    require(used == set(requested), "HISTIDINE_IDENTITY_MISMATCH")
    residues = list(fixer.topology.residues())
    atoms = list(fixer.topology.atoms())
    require(len(atoms) <= 10000 and {r.chain.id for r in residues} == set(p["proteinChains"]), "PROTEIN_OUTSIDE_PROFILE")
    require(not any(a.name == b.name == "SG" for a, b in fixer.topology.bonds()), "DISULFIDE_REQUIRES_REVIEW")
    rows = [{"chain": a.residue.chain.id, "residueId": a.residue.id, "residueIndex": a.residue.index,
             "name": a.name, "element": a.element.symbol, "source": (a.residue.chain.id, a.residue.id, a.name) in source_keys,
             "xyzAngstrom": list(fixer.positions[a.index].value_in_unit(unit.angstrom))} for a in atoms]
    path = stage.output / "derived-receptor.pdb"
    with path.open("x") as stream:
        app.PDBFile.writeFile(fixer.topology, fixer.positions, stream, keepIds=True)
    stage.roles[path.name] = "derived-structure"
    stage.write_json("structure-cleanup.json", {"inputSha256": digest(source), "proteinChains": p["proteinChains"],
        "excludedAtoms": dict(excluded), "missingResidues": missing, "missingAtoms": added,
        "histidines": p["histidines"], "sourceAtoms": rows, "seed": p["seed"]}, "input-preflight")
    return rows, path


def run_tool(stage, inventory: dict, name: str, arguments: list[str]) -> None:
    require(name in ("antechamber", "parmchk2", "tleap"), "INVALID_AMBER_TOOL")
    prefix = Path(inventory["prefix"])
    environment = {key: value for key, value in os.environ.items() if key in ("TMPDIR", "LANG", "LC_ALL", "SYSTEMROOT")}
    environment.update(AMBERHOME=str(prefix), PATH=str(prefix / "bin") + ":/usr/bin:/bin",
                       OMP_NUM_THREADS=str(stage.spec["resources"]["cpuThreads"]), OPENBLAS_NUM_THREADS="1")
    log = stage.output / f"{name}.log"
    command = [str(prefix / "bin" / name), *arguments]
    stage.write_json(f"{name}-command.json", {"arguments": command, "toolchainSha256": inventory["sha256"]}, "parameter-provenance")
    with log.open("xb") as stream:
        child = subprocess.Popen(command, cwd=stage.output, env=environment, stdin=subprocess.DEVNULL,
                                 stdout=stream, stderr=subprocess.STDOUT, close_fds=True, start_new_session=True)
        from remote_runner import process_identity
        try:
            identity = process_identity(child.pid)
            require(identity is not None, "AMBER_TOOL_IDENTITY_UNAVAILABLE")
            # Exact process identity protects cancellation against PID reuse.
            from .common import atomic_json, read_json
            status = read_json(stage.attempt / "status.json")
            atomic_json(stage.attempt / "tool-process.json", {"identity": identity,
                "stageIdentity": status.get("stageIdentity"), "nonce": status.get("nonce"), "processGroup": os.getpgid(child.pid)})
            while child.poll() is None:
                require(not stage.canceled(), "CANCELED")
                time.sleep(.1)
            require(child.returncode == 0, "AMBER_TOOL_FAILED")
        finally:
            if child.poll() is None:
                import signal
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait()
    stage.roles[log.name] = "log"
    text = log.read_text(errors="replace")
    require(not re.search(r"FATAL|ATTN.*revision|Could not open|Unknown residue|No torsion terms|Parameter file was not saved|Error:|ERROR:", text),
            "AMBER_PARAMETERIZATION_REQUIRES_REVIEW")
    if name == "tleap":
        require(re.search(r"Errors = 0", text) is not None, "AMBER_ASSEMBLY_NOT_CONFIRMED")


def prepare(stage, source: Path | None, p: dict):
    import numpy as np
    from rdkit import Chem
    from openmm import app, unit
    from parmed.amber import AmberParm
    from parmed.tools.checkvalidity import check_validity
    from parmed.tools.exceptions import WarningList
    validate_preparation(p)
    inventory = toolchain_inventory()
    require(inventory["sha256"] == p["toolchainSha256"], "AMBER_TOOLCHAIN_CHANGED")
    stage.write_json("amber-toolchain.json", inventory, "parameter-provenance")
    standalone = p["mode"] == "amber-small-molecule"
    if standalone:
        require(source is None, "SMALL_MOLECULE_REQUIRES_APPROVED_SMILES")
        rows, protein = [], None
    else:
        require(source is not None, "ONE_STRUCTURE_REQUIRED")
        rows, protein = receptor(stage, source, p)
    molecule = ligand_molecule(p)
    xyz = molecule.GetConformer().GetPositions()
    protein_xyz = np.array([a["xyzAngstrom"] for a in rows])
    translation = -xyz.mean(axis=0) if standalone else np.array([
        protein_xyz[:, 0].max() + p["ligand"]["placement"]["gapAngstrom"] - xyz[:, 0].min(),
        protein_xyz[:, 1].mean() - xyz[:, 1].mean(), protein_xyz[:, 2].mean() - xyz[:, 2].mean()])
    xyz += translation
    for index, position in enumerate(xyz):
        molecule.GetConformer().SetAtomPosition(index, position)
    writer = Chem.SDWriter(str(stage.output / "ligand.sdf"))
    writer.write(molecule)
    writer.close()
    stage.roles["ligand.sdf"] = "ligand-input"
    run_tool(stage, inventory, "antechamber", ["-i", "ligand.sdf", "-fi", "sdf", "-o", "ligand.mol2", "-fo", "mol2",
        "-at", "gaff2", "-c", p["ligand"]["chargeMethod"], "-nc", str(p["ligand"]["netCharge"]), "-m", "1", "-rn", "LIG", "-s", "2"])
    mol2 = (stage.output / "ligand.mol2").read_text()
    section = mol2.split("@<TRIPOS>ATOM\n")[1].split("@<TRIPOS>BOND")[0].strip().splitlines()
    records = [line.split() for line in section]
    require(len(records) == molecule.GetNumAtoms() and len({r[1] for r in records}) == len(records), "LIGAND_ATOM_MAPPING_MISMATCH")
    bond_table = stage.output / "ANTECHAMBER_BOND_TYPE.AC"
    graph_verification = verify_ligand_graph(molecule, mol2, bond_table.read_text() if bond_table.is_file() else "")
    if graph_verification["carboxylateResonanceGroups"]:
        stage.roles[bond_table.name] = "parameter-provenance"
    require(np.allclose(np.array([[float(r[i]) for i in (2, 3, 4)] for r in records]), xyz, atol=.002),
            "LIGAND_COORDINATE_OR_CHARGE_MISMATCH")
    parameter_input = "ligand.mol2"
    charge_normalization = None
    if p["ligand"]["chargeMethod"] == "bcc":
        sqm = stage.output / "sqm.out"
        pre = stage.output / "ANTECHAMBER_AM1BCC_PRE.AC"
        normalized, charge_normalization = normalize_bcc_charge(molecule, mol2,
            sqm.read_text() if sqm.is_file() else "", pre.read_text() if pre.is_file() else "")
        if charge_normalization["applied"]:
            parameter_input = "ligand-charge-normalized.mol2"
            with (stage.output / parameter_input).open("x") as stream:
                stream.write(normalized)
            charge_normalization.update(rawMol2Sha256=digest(stage.output / "ligand.mol2"),
                derivedMol2Sha256=digest(stage.output / parameter_input), sqmSha256=digest(sqm), preBccSha256=digest(pre))
            stage.write_json("ligand-charge-normalization.json", charge_normalization, "parameter-provenance")
            stage.roles[pre.name] = "parameter-provenance"
            mol2 = normalized
            records = [line.split() for line in mol2.split("@<TRIPOS>ATOM\n")[1].split("@<TRIPOS>BOND")[0].strip().splitlines()]
    require(abs(sum(float(r[8]) for r in records) - p["ligand"]["netCharge"]) < 1e-4,
            "LIGAND_COORDINATE_OR_CHARGE_MISMATCH")
    stage.roles["ligand.mol2"] = "parameter-provenance" if parameter_input != "ligand.mol2" else "amber-input"
    stage.roles[parameter_input] = "amber-input"
    run_tool(stage, inventory, "parmchk2", ["-i", parameter_input, "-f", "mol2", "-o", "ligand.frcmod", "-s", "2"])
    require("ATTN" not in (stage.output / "ligand.frcmod").read_text(), "LIGAND_PARAMETERS_NEED_REVISION")
    script_lines = [] if standalone else ["source leaprc.protein.ff14SB"]
    script_lines += ["source leaprc.gaff2", "source leaprc.water.tip3p",
        "loadAmberParams ligand.frcmod", f"lig = loadMol2 {parameter_input}"]
    script_lines += ["unit = lig"] if standalone else [f"rec = loadPdb {protein.name}", "unit = combine { rec lig }"]
    script_lines += ["check unit", "saveAmberParm unit solute.prmtop solute.inpcrd",
        f"solvateBox unit TIP3PBOX {p['solvent']['paddingAngstrom']:.6f}"]
    if p["solvent"]["neutralize"]:
        script_lines += ["addIons unit Na+ 0", "addIons unit Cl- 0"]
    script = "\n".join(script_lines + ["check unit", "saveAmberParm unit prepared.prmtop prepared.inpcrd", "quit", ""])
    (stage.output / "build.leap").write_text(script)
    stage.roles["build.leap"] = "parameter-provenance"
    run_tool(stage, inventory, "tleap", ["-s", "-f", "build.leap"])
    require(toolchain_inventory() == inventory, "AMBER_TOOLCHAIN_CHANGED")
    parm = AmberParm(str(stage.output / "prepared.prmtop"), str(stage.output / "prepared.inpcrd"))
    warnings = WarningList()
    check_validity(parm, warnings)
    require(not warnings, "AMBER_TOPOLOGY_INVALID")
    require(all(item.type is not None for group in (parm.bonds, parm.angles, parm.dihedrals) for item in group), "AMBER_PARAMETERS_MISSING")
    solute = AmberParm(str(stage.output / "solute.prmtop"), str(stage.output / "solute.inpcrd"))
    ligand_index = len(solute.residues) - 1
    require(not standalone or len(solute.residues) == 1, "INDEPENDENT_SOLUTE_IDENTITY_MISMATCH")
    require(solute.residues[ligand_index].name == "LIG", "LIGAND_IDENTITY_MISMATCH")
    mapping = []
    for row in rows:
        matches = [a for a in solute.residues[row["residueIndex"]].atoms if a.name == row["name"]]
        require(len(matches) == 1, "PROTEIN_ATOM_MAPPING_MISMATCH")
        atom = matches[0]
        require(atom.element_name == row["element"] and np.allclose(solute.coordinates[atom.idx], row["xyzAngstrom"], atol=.002),
                "PROTEIN_COORDINATES_CHANGED")
        mapping.append({**row, "particleIndex": atom.idx})
    ligand_atoms = list(solute.residues[ligand_index].atoms)
    require(len(ligand_atoms) == len(records)
            and [a.name for a in ligand_atoms] == [r[1] for r in records]
            and [a.atomic_number for a in ligand_atoms] == [a.GetAtomicNum() for a in molecule.GetAtoms()], "LIGAND_ATOM_MAPPING_MISMATCH")
    require(abs(sum(a.charge for a in ligand_atoms) - p["ligand"]["netCharge"]) < 1e-4,
            "LIGAND_COORDINATE_OR_CHARGE_MISMATCH")
    require(len(parm.atoms) >= len(solute.atoms) and [(a.name, a.atomic_number) for a in parm.atoms[:len(solute.atoms)]]
            == [(a.name, a.atomic_number) for a in solute.atoms], "SOLVATION_ATOM_ORDER_CHANGED")
    shift = parm.coordinates[:len(solute.atoms)] - solute.coordinates
    require(np.allclose(shift, shift[0], atol=.002), "SOLVATION_CHANGED_SOLUTE_GEOMETRY")
    require(abs(sum(a.charge for a in parm.atoms)) < 1e-3, "SYSTEM_NOT_NEUTRAL")
    top = app.AmberPrmtopFile(str(stage.output / "prepared.prmtop"))
    coordinates = app.AmberInpcrdFile(str(stage.output / "prepared.inpcrd"))
    require(coordinates.boxVectors is not None and top.topology.getNumAtoms() == len(parm.atoms), "AMBER_PAIR_MISMATCH")
    top.topology.setPeriodicBoxVectors(coordinates.boxVectors)
    box = np.array(coordinates.boxVectors.value_in_unit(unit.angstrom))
    # LEaP's TIP3P box is orthorhombic. Check the periodic image as well as the
    # displayed coordinates: a ligand outside one face must not overlap the
    # protein through another face of the box.
    require(np.allclose(box, np.diag(np.diag(box)), atol=1e-6), "UNSUPPORTED_AMBER_BOX")
    if not standalone:
        distances = np.linalg.norm(xyz[:, None, :] - protein_xyz[None, :, :], axis=-1)
        ligand_positions = parm.coordinates[[a.idx for a in ligand_atoms]]
        protein_positions = parm.coordinates[[a["particleIndex"] for a in mapping]]
        delta = ligand_positions[:, None, :] - protein_positions[None, :, :]
        delta -= np.round(delta / np.diag(box)) * np.diag(box)
        periodic_distance = float(np.linalg.norm(delta, axis=-1).min())
        require(periodic_distance >= p["ligand"]["placement"]["gapAngstrom"] - .01, "PERIODIC_LIGAND_PLACEMENT_OVERLAP")
    report = {"profileId": p["forceField"], "toolchainSha256": inventory["sha256"],
        "inputSha256": digest(source) if source is not None else None, "smiles": Chem.MolToSmiles(Chem.RemoveHs(molecule)),
        "chargeMethod": p["ligand"]["chargeMethod"], "chargeRationale": p["ligand"]["chargeRationale"],
        "chargeNormalization": charge_normalization,
        "ligandCharge": sum(a.charge for a in ligand_atoms), "netSystemCharge": sum(a.charge for a in parm.atoms),
        "formalCharge": Chem.GetFormalCharge(molecule),
        "electronCount": sum(a.GetAtomicNum() for a in molecule.GetAtoms()) - p["ligand"]["netCharge"],
        "multiplicity": p["ligand"]["multiplicity"],
        "graphVerification": graph_verification,
        "solvationTranslationAngstrom": shift[0].tolist(), "residueCounts": dict(Counter(r.name for r in parm.residues)),
        "topologyValidityWarnings": [], "units": {"amberCoordinates": "angstrom", "openmmCoordinates": "nanometer"},
        "proteinSourceMap": mapping, "ligandParticleMap": [{"graphIndex": i, "particleIndex": a.idx, "name": a.name,
            "atomicNumber": a.atomic_number, "charge": a.charge} for i, a in enumerate(ligand_atoms)]}
    if standalone:
        report.update(validate_solvent_composition(parm, p["ligand"]["netCharge"]))
        report.update(inputKind="approved-smiles", geometryMethod="RDKit ETKDGv3 and MMFF", geometrySeed=p["seed"])
    else:
        report.update(placementTranslationAngstrom=translation.tolist(), minimumProteinLigandDistanceAngstrom=float(distances.min()),
                      minimumPeriodicProteinLigandDistanceAngstrom=periodic_distance)
    stage.write_json("parameterization-report.json", report, "parameter-provenance")
    for name in (parameter_input, "ligand.frcmod", "solute.prmtop", "solute.inpcrd", "prepared.prmtop", "prepared.inpcrd"):
        stage.roles[name] = "amber-input"
    for name, role in (("sqm.in", "parameter-provenance"), ("sqm.out", "log"), ("leap.log", "log")):
        if (stage.output / name).is_file():
            stage.roles[name] = role
    return top, coordinates
