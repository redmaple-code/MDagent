"""Deterministic source selection with explicit, identity-bound PBC derivation."""
from __future__ import annotations
import hashlib
from pathlib import Path
from ..errors import canonical, require, sha256_file
from .contracts import LIMITS, validate_preview_request
from .sources import SOLVENT, check_source


def plan_preview(source, request, pbc_runtime=None):
    validate_preview_request(request);check_source(source)
    require(source['ref']==request['source'],'PREVIEW_SOURCE_CHANGED')
    require(request['endFrame']<source['sourceFrameCount'],'PREVIEW_FRAME_OUT_OF_RANGE')
    indices=list(range(request['startFrame'],request['endFrame']+1,request['stride']))
    if request['profile']=='overview':
        if indices[-1]!=request['endFrame']:indices.append(request['endFrame'])
        if len(indices)>LIMITS['overviewFrames']:
            indices=[indices[round(i*(len(indices)-1)/(LIMITS['overviewFrames']-1))] for i in range(LIMITS['overviewFrames'])]
    if request['atomSet']=='overview':
        require(source['overviewReason'] is None,source['overviewReason'] or 'PREVIEW_ATOMS_EMPTY')
        atoms=sorted(set(source['caIndices']+source['ligandIndices']))
    elif request['atomSet']=='solute-heavy':
        atoms=[a['index'] for a in source['atoms'] if a['resname'] not in SOLVENT and a['element'] not in {'H','D'}]
    else:atoms=list(range(len(source['atoms'])))
    require(atoms and len(atoms)*12<=LIMITS['windowBytes'],'PREVIEW_ATOM_BUDGET_EXCEEDED')
    implementation=hashlib.sha256(canonical({p.name:sha256_file(p) for p in Path(__file__).parent.glob('*.py')})).hexdigest()
    identity=dict(source=source['ref'],atomIndices=atoms,sourceFrames=indices,precision=3,implementationSha256=implementation)
    if request.get('pbc','raw')=='unwrap':
        from .pbc import pbc_runtime as inspect_runtime
        pbc_runtime=pbc_runtime or inspect_runtime()
        identity['pbcRuntime']=pbc_runtime
    return dict(previewId='preview-'+hashlib.sha256(canonical(identity)).hexdigest(),identity=identity,
                request=request,atomIndices=atoms,frames=[source['frames'][i] for i in indices],pbcRuntime=pbc_runtime if request.get('pbc')=='unwrap' else None)
