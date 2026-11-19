"""Fixed headless native PyMOL worker; bounded data frames, no command input."""
from pathlib import Path
import hashlib
import json
import sys

MAX_BYTES = 32 * 1024**2
NAME = 'bio_pbc'


def header():
    line = sys.stdin.buffer.readline(MAX_BYTES + 1)
    if not line:
        return None
    if len(line) > MAX_BYTES or not line.endswith(b'\n'):
        raise ValueError('PBC_NATIVE_PROTOCOL')
    return json.loads(line)


def reply(value, data=b''):
    sys.stdout.buffer.write(json.dumps(value, separators=(',', ':')).encode() + b'\n')
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def main():
    import numpy as np
    import pymol
    import pymol2
    from chempy import Atom, Bond
    from chempy.models import Indexed
    if not hasattr(pymol._cmd, 'pbc_unwrap'):
        raise ValueError('PBC_PYMOL_UNAVAILABLE')
    with pymol2.PyMOL() as instance:
        cmd = instance.cmd
        cmd.set('auto_zoom', 0)
        cmd.set('auto_show_lines', 0)
        cmd.set('auto_show_nonbonded', 0)
        cmd.set('retain_order', 1)
        files = [Path(pymol._cmd.__file__), Path(pymol.__file__).with_name('editing.py'), Path(__file__)]
        digest = hashlib.sha256()
        for file in files:
            with file.open('rb') as stream:
                for part in iter(lambda: stream.read(1024**2), b''):
                    digest.update(part)
        engine = dict(engineVersion=str(cmd.get_version()[0]), engineSha256=digest.hexdigest())
        reply(engine)
        if sys.argv[1:] == ['--probe']:
            return
        if sys.argv[1:]:
            raise ValueError('PBC_NATIVE_PROTOCOL')
        spec = header()
        if not isinstance(spec, dict) or set(spec) != {'atomCount', 'symbols', 'bonds'}:
            raise ValueError('PBC_NATIVE_PROTOCOL')
        count = spec['atomCount']
        if type(count) is not int or not 0 < count <= 1000000 or len(spec['symbols']) != count:
            raise ValueError('PBC_NATIVE_PROTOCOL')
        size = count * 12
        if size > MAX_BYTES:
            raise ValueError('PBC_NATIVE_PROTOCOL')
        model = Indexed()
        for i, symbol in enumerate(spec['symbols']):
            if not isinstance(symbol, str) or not symbol.isalpha() or not 1 <= len(symbol) <= 3:
                raise ValueError('PBC_NATIVE_PROTOCOL')
            atom = Atom()
            atom.name, atom.symbol, atom.resn, atom.resi = f'a{i:07d}', symbol, 'SYS', '1'
            atom.id, atom.rank = i + 1, i
            model.atom.append(atom)
        expected = set()
        for pair in spec['bonds']:
            if len(pair) != 2 or any(type(v) is not int or not 0 <= v < count for v in pair) or pair[0] == pair[1]:
                raise ValueError('PBC_NATIVE_PROTOCOL')
            edge = tuple(sorted(pair))
            if edge in expected:
                raise ValueError('PBC_NATIVE_PROTOCOL')
            expected.add(edge)
            bond = Bond(); bond.index = list(pair); bond.order = 1
            model.bond.append(bond)
        calls, previous_box = 0, None
        while True:
            frame = header()
            if frame is None:
                return
            if not isinstance(frame, dict) or set(frame) != {'byteSize', 'box'} or frame['byteSize'] != size or len(frame['box']) != 6:
                raise ValueError('PBC_NATIVE_PROTOCOL')
            data = sys.stdin.buffer.read(size)
            if len(data) != size:
                raise ValueError('PBC_NATIVE_PROTOCOL')
            coords = np.frombuffer(data, dtype='<f4').reshape(count, 3)
            if not np.isfinite(coords).all() or not np.isfinite(frame['box']).all():
                raise ValueError('PBC_NATIVE_PROTOCOL')
            state = 1 if calls == 0 else 2
            if calls == 0:
                for atom, xyz in zip(model.atom, coords):
                    atom.coord = xyz.tolist()
                cmd.load_model(model, NAME, state=1, quiet=1)
                actual = {tuple(sorted(b[:2])) for b in cmd.get_bonds(NAME)}
                if cmd.count_atoms(NAME) != count or actual != expected:
                    raise ValueError('PBC_TOPOLOGY_REQUIRED')
            else:
                cmd.load_coordset(coords, NAME, state=2, quiet=1)
                cmd.set_symmetry(NAME, *previous_box, 'P 1', state=1, quiet=1)
            cmd.set_symmetry(NAME, *frame['box'], 'P 1', state=state, quiet=1)
            cmd.pbc_unwrap(NAME, bymol=1)
            result = np.asarray(cmd.get_coordset(NAME, state=state), dtype='<f4')
            if result.shape != (count, 3) or not np.isfinite(result).all():
                raise ValueError('PBC_NATIVE_PROTOCOL')
            calls += 1
            reply(dict(byteSize=size, nativeCalls=calls), result.tobytes())
            # The next call includes this actual unwrapped state as its anchor.
            cmd.load_coordset(result, NAME, state=1, quiet=1)
            previous_box = frame['box']


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        code = str(error)
        reply({'error': code if code.startswith('PBC_') and code.replace('_', '').isalnum() else 'PBC_NATIVE_FAILED'})
        sys.exit(1)
