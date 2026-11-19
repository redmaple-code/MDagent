"""Whole-file structural intake checks; no Context, mutation, or dynamics."""
from collections import Counter
import hashlib
import io
import math
from pathlib import Path
import warnings
import xml.etree.ElementTree as ET

MAX_BYTES = 64 * 1024 * 1024


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def scan_pdb(data: bytes, *, allow_ssbond: bool = False) -> dict:
    """Reject ambiguous records before OpenMM can select/drop them implicitly."""
    if not data or len(data) > MAX_BYTES:
        raise ValueError("PDB_EMPTY_OR_TOO_LARGE")
    atoms, residues, serials, identities, conect = [], {}, set(), set(), []
    segment = models = ends = 0
    box = None
    records = Counter()
    for number, line in enumerate(data.decode("ascii").splitlines(), 1):
        record = line[:6].strip()
        records[record] += 1
        try:
            if record == "MODEL":
                models += 1
                if models > 1 or atoms:
                    raise ValueError("MULTIPLE_MODELS_REQUIRE_SELECTION")
            elif record == "ENDMDL":
                ends += 1
            elif record == "TER":
                segment += 1
            elif record == "CRYST1":
                if box is not None:
                    raise ValueError("MULTIPLE_BOX_RECORDS")
                box = [float(line[a:b]) for a, b in ((6, 15), (15, 24), (24, 33), (33, 40), (40, 47), (47, 54))]
                if not all(math.isfinite(x) for x in box) or min(box[:3]) <= 0 or not all(0 < x < 180 for x in box[3:]):
                    raise ValueError("INVALID_BOX")
            elif record == "CONECT":
                conect.append([int(line[i:i + 5]) for i in range(6, len(line), 5) if line[i:i + 5].strip()])
            elif record in ("ATOM", "HETATM"):
                if ends or records["END"]:
                    raise ValueError("ATOMS_AFTER_MODEL_END")
                if line[16:17].strip():
                    raise ValueError("ALTLOC_REQUIRES_SELECTION")
                serial = int(line[6:11])
                key = (segment, line[21:22], line[22:26].strip(), line[26:27], line[72:76].strip())
                name, residue = line[12:16].strip(), line[17:20].strip()
                identity = (key, name)
                if serial in serials or identity in identities:
                    raise ValueError("DUPLICATE_ATOM_IDENTITY")
                if key in residues and residues[key] != residue:
                    raise ValueError("CONFLICTING_RESIDUE_IDENTITY")
                xyz = [float(line[a:b]) for a, b in ((30, 38), (38, 46), (46, 54))]
                occupancy = float(line[54:60])
                if not all(math.isfinite(x) for x in xyz + [occupancy]):
                    raise ValueError("NONFINITE_ATOM_RECORD")
                if occupancy != 1:
                    raise ValueError("PARTIAL_OCCUPANCY_REQUIRES_REVIEW")
                serials.add(serial)
                identities.add(identity)
                residues[key] = residue
                atoms.append({"serial": serial, "name": name, "residue": residue, "key": key, "xyz": xyz})
        except (ValueError, IndexError) as error:
            raise ValueError(f"PDB_LINE_{number}: {error}") from error
    if not atoms or models != ends:
        raise ValueError("EMPTY_OR_UNBALANCED_MODEL")
    if any(not row or any(serial not in serials for serial in row) for row in conect):
        raise ValueError("CONECT_UNKNOWN_ATOM")
    # SSBOND needs a separate identity-aware check; do not silently accept it.
    if records["SSBOND"] and not allow_ssbond:
        raise ValueError("SSBOND_REQUIRES_EXPLICIT_REVIEW")
    return {"atoms": atoms, "residues": residues, "box": box, "records": records}


def force_field_files(data_root: Path, names: tuple[str, ...]) -> list[dict]:
    """Hash roots and every XML include, restricted to the installed data tree."""
    data_root = data_root.resolve()
    result, seen = [], set()

    def visit(name: str) -> None:
        path = (data_root / name).resolve()
        path.relative_to(data_root.resolve())
        if path in seen:
            return
        seen.add(path)
        data = path.read_bytes()
        result.append({"name": path.relative_to(data_root).as_posix(), "sha256": sha256(data), "byteSize": len(data)})
        for child in ET.fromstring(data).findall("Include"):
            visit(child.attrib["file"])

    for name in names:
        visit(name)
    return result


def load_checked_pdb(source: Path, expected_sha256: str):
    from openmm import app, unit
    with source.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if sha256(data) != expected_sha256:
        raise ValueError("INPUT_HASH_MISMATCH")
    # SSBOND is subsequently checked by the variant/explicit-bond preparation
    # logic, not discarded or silently accepted by this parser.
    raw = scan_pdb(data, allow_ssbond=True)
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always")
        pdb = app.PDBFile(io.StringIO(data.decode("ascii")))
    if emitted:
        raise ValueError("OPENMM_PARSER_WARNING")
    atoms = list(pdb.topology.atoms())
    if pdb.getNumFrames() != 1 or len(atoms) != len(raw["atoms"]) or pdb.topology.getNumResidues() != len(raw["residues"]):
        raise ValueError("PARSER_CHANGED_RECORD_COUNTS")
    positions = pdb.positions.value_in_unit(unit.angstrom)
    renamed = 0
    for original, atom, xyz in zip(raw["atoms"], atoms, positions):
        key = original["key"]
        if (str(original["serial"]) != atom.id or key[2] != atom.residue.id
                or key[1].strip() != atom.residue.chain.id.strip()
                or key[3].strip() != atom.residue.insertionCode.strip()):
            raise ValueError("PARSER_CHANGED_ATOM_ORDER")
        if any(abs(a - b) > 1e-7 for a, b in zip(original["xyz"], xyz)):
            raise ValueError("PARSER_CHANGED_COORDINATES")
        if atom.element is None:
            raise ValueError("UNKNOWN_ELEMENT")
        renamed += original["name"] != atom.name
    return pdb, {"schemaVersion": "ksh.input-preflight.v1", "inputSha256": expected_sha256,
                 "byteSize": len(data), "wholeFileRead": True, "atoms": len(atoms),
                 "residues": pdb.topology.getNumResidues(), "residueNames": dict(Counter(raw["residues"].values())),
                 "atomOrderPreserved": True, "coordinatesPreserved": True, "renamedAtoms": renamed,
                 "cryst1AngstromDegrees": raw["box"], "parserWarnings": 0,
                 "scope": "Static input checks before approved preparation; not chemical validation or scientific convergence."}
