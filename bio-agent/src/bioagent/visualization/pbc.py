"""Known-topology reconstruction and an isolated native PyMOL PBC process."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from ..errors import BioError, canonical, require
from .sources import PROTEIN, check_source

HELPER = Path(__file__).with_name('pymol_pbc_worker.py')
MAX_BYTES = 32 * 1024**2


def _env():
    env = {k: os.environ[k] for k in ('PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR') if k in os.environ}
    env.update(OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1', CUDA_VISIBLE_DEVICES='', PYTHONDONTWRITEBYTECODE='1')
    return env


def pbc_runtime():
    import MDAnalysis
    import openmm
    python = Path(os.environ.get('BIO_PREVIEW_PYMOL_PYTHON', sys.executable))
    require(python.is_absolute() and python.is_file() and os.access(python, os.X_OK), 'PBC_PYMOL_UNAVAILABLE')
    # Keep a virtualenv's original executable path; resolving its symlink can lose the environment.
    try:
        result = subprocess.run([str(python), '-I', '-B', str(HELPER), '--probe'], input=b'',
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=_env(), timeout=8, check=False)
        require(result.returncode == 0 and len(result.stdout) <= 4096, 'PBC_PYMOL_UNAVAILABLE')
        engine = json.loads(result.stdout)
        require(set(engine) == {'engineVersion', 'engineSha256'} and len(engine['engineSha256']) == 64, 'PBC_PYMOL_UNAVAILABLE')
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, KeyError):
        raise BioError('PBC_PYMOL_UNAVAILABLE') from None
    runtime = dict(python=str(python), **engine, mdanalysisVersion=MDAnalysis.__version__, openmmVersion=openmm.__version__)
    runtime['runtimeSha256'] = hashlib.sha256(canonical(runtime)).hexdigest()
    return runtime


def processing_metadata(runtime):
    return dict(mode='unwrap', makeWhole='MDAnalysis', makeWholeVersion=runtime['mdanalysisVersion'],
                unwrap='pymol.cmd.pbc_unwrap', unwrapVersion=runtime['engineVersion'], runtimeSha256=runtime['runtimeSha256'])


def _known_topology(source):
    import MDAnalysis as mda
    from openmm import app
    check_source(source)
    u = None
    try:
        topology = app.PDBFile(str(source['topology'])).topology
        atoms = list(topology.atoms())
        require(len(atoms) == len(source['atoms']), 'PBC_TOPOLOGY_REQUIRED')
        for atom, original in zip(atoms, source['atoms']):
            require(atom.name == original['name'] and atom.element is not None
                    and atom.element.symbol.upper() == original['element'].upper(), 'PBC_TOPOLOGY_REQUIRED')
        bonds = sorted({tuple(sorted((a.index, b.index))) for a, b in topology.bonds()})
        bonded = {i for pair in bonds for i in pair}
        for residue in topology.residues():
            members = list(residue.atoms())
            if len(members) > 1 or residue.name in PROTEIN:
                require(all(a.index in bonded for a in members), 'PBC_TOPOLOGY_REQUIRED')
        u = mda.Universe(str(source['topology']))
        u.add_TopologyAttr('bonds', bonds)
        fragments = u.atoms.fragindices
        for residue in topology.residues():
            members = list(residue.atoms())
            if len(members) > 1 and residue.name not in PROTEIN:
                require(len({int(fragments[a.index]) for a in members}) == 1, 'PBC_TOPOLOGY_REQUIRED')
        check_source(source)
        return u, [a.element.symbol for a in atoms], bonds
    except BioError:
        if u is not None:u.trajectory.close()
        raise
    except Exception:
        if u is not None:u.trajectory.close()
        raise BioError('PBC_TOPOLOGY_REQUIRED') from None


class PbcProcessor:
    """Owns two native states; no original source coordinate array is mutated."""
    def __init__(self, source, runtime):
        self.process = None
        self.universe = None
        self.calls = 0
        require(not source.get('gaps'), 'PBC_TRAJECTORY_GAP')
        try:
            self.universe, symbols, bonds = _known_topology(source)
            self.count = len(symbols)
            self.process = subprocess.Popen([runtime['python'], '-I', '-B', str(HELPER)], stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=_env(), shell=False)
            engine = self._reply()
            require(engine == {k: runtime[k] for k in ('engineVersion', 'engineSha256')}, 'PBC_RUNTIME_CHANGED')
            self.process.stdin.write(canonical(dict(atomCount=self.count, symbols=symbols, bonds=bonds)) + b'\n')
            self.process.stdin.flush()
        except BaseException:
            self.close()
            raise

    def _reply(self):
        try:
            line = self.process.stdout.readline(4097)
            require(0 < len(line) <= 4096 and line.endswith(b'\n'), 'PBC_NATIVE_FAILED')
            value = json.loads(line)
            if isinstance(value, dict) and 'error' in value:
                raise BioError(value['error'])
            return value
        except (OSError, ValueError):
            raise BioError('PBC_NATIVE_FAILED') from None

    def unwrap(self, coordinates, dimensions):
        import numpy as np
        from MDAnalysis.exceptions import NoDataError
        from MDAnalysis.lib.mdamath import triclinic_vectors
        require(dimensions is not None, 'PBC_CELL_REQUIRED')
        box = np.asarray(dimensions, dtype=float)
        require(box.shape == (6,) and np.isfinite(box).all() and (box[:3] > 0).all()
                and (box[3:] > 0).all() and (box[3:] < 180).all()
                and np.linalg.det(triclinic_vectors(box, dtype=np.float64)) > 1e-6, 'PBC_CELL_REQUIRED')
        coords = np.asarray(coordinates, dtype=np.float32)
        require(coords.shape == (self.count, 3) and np.isfinite(coords).all(), 'PREVIEW_NONFINITE_COORDINATES')
        self.universe.atoms.positions = coords
        self.universe.dimensions = box
        try:
            whole = self.universe.atoms.unwrap(compound='fragments', reference=None, inplace=False)
        except (ValueError, NoDataError):
            raise BioError('PBC_TOPOLOGY_REQUIRED') from None
        data = np.asarray(whole, dtype='<f4').tobytes()
        require(len(data) <= MAX_BYTES, 'PREVIEW_MEMORY_BUDGET_EXCEEDED')
        try:
            self.process.stdin.write(canonical(dict(byteSize=len(data), box=box.tolist())) + b'\n')
            self.process.stdin.write(data)
            self.process.stdin.flush()
            reply = self._reply()
            require(reply == dict(byteSize=len(data), nativeCalls=self.calls + 1), 'PBC_NATIVE_PROTOCOL')
            output = self.process.stdout.read(len(data))
            require(len(output) == len(data), 'PBC_NATIVE_PROTOCOL')
        except (OSError, BrokenPipeError):
            raise BioError('PBC_NATIVE_FAILED') from None
        result = np.frombuffer(output, dtype='<f4').reshape(self.count, 3).copy()
        require(np.isfinite(result).all(), 'PREVIEW_NONFINITE_COORDINATES')
        self.calls += 1
        return result

    def close(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill(); self.process.wait(timeout=3)
            for stream in (self.process.stdin, self.process.stdout):
                if stream is not None:
                    try:stream.close()
                    except OSError:pass
        if self.universe is not None:
            self.universe.trajectory.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
