"""Resolve catalog identities on the final compute node; no client paths."""
from __future__ import annotations
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics
from ..errors import BioError, canonical, read_json, require, safe_file, sha256_file
from ..artifacts import verified_manifest
from ..intake import list_inputs
from .contracts import HASH, LIMITS, validate_source

PROTEIN = set('ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE LEU LYS MET PHE PRO SER THR TRP TYR VAL HID HIE HIP CYX ASH GLH LYN'.split())
SOLVENT = set('HOH WAT SOL TIP3 TIP4 NA CL Na Cl K MG CA'.split())


def preview_input_id(record):
    """Preview identity binds the exact registered file, without changing intake IDs."""
    value=['bio.preview-input.v1',record['id'],record['path'],record['sha256']]
    digest=hashlib.sha256(json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    return 'input-file-'+digest


def file_identity(path):
    require(not path.is_symlink() and path.is_file(), 'PREVIEW_SOURCE_CHANGED')
    s=path.stat()
    return [s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns]


def _file_fingerprint(path, expected):
    try:
        before=file_identity(path);digest=sha256_file(path);after=file_identity(path)
    except OSError:
        raise BioError('PREVIEW_SOURCE_CHANGED') from None
    require(before==after and digest==expected,'PREVIEW_SOURCE_CHANGED')
    return dict(identity=before,sha256=digest)


def _merge_fingerprints(target, records):
    for path,record in records.items():
        require(path not in target or target[path]==record,'PREVIEW_SOURCE_CHANGED')
        target[path]=record


def check_source(source):
    for path,record in source['fingerprints'].items():
        require(file_identity(Path(path))==record['identity'], 'PREVIEW_SOURCE_CHANGED')


def _ligand_report(controller, ref, files, model):
    """Follow recorded accepted bundles, never infer a report from directory names."""
    digest=model.get('parameterizationSha256');fingerprints={};code='PREVIEW_SOURCE_CHANGED'
    if digest is None:return {},fingerprints
    require(type(digest) is str and HASH.fullmatch(digest),code)
    def load(path, expected=None):
        before=file_identity(path);actual=sha256_file(path)
        require(expected is None or actual==expected,code)
        value=read_json(path);require(file_identity(path)==before,code)
        fingerprints[str(path)]=dict(identity=before,sha256=actual)
        return value
    reports=[f for f in files if f['path']=='parameterization-report.json']
    if reports:
        require(len(reports)==1 and reports[0]['sha256']==digest,code)
        return load(Path(reports[0]['absolutePath']),digest),fingerprints
    try:
        path=safe_file(controller.runtime.root,ref['attemptId']+'/output/manifest.json')
        manifest=load(path,ref['sourceSha256'])
        require(all(manifest[k]==ref[k] for k in ('workflowId','revision','stageId','attemptId')),code)
        value=controller.store.get(ref['workflowId'],ref['revision']);seen=set();accepted=True
        while True:
            attempt_id=manifest['attemptId'];require(attempt_id not in seen,code);seen.add(attempt_id)
            models=[f for f in manifest['files'] if f['role']=='model'];require(len(models)==1,code)
            inherited=load(safe_file(path.parent,models[0]['path']),models[0]['sha256'])
            require(inherited.get('parameterizationSha256')==digest
                    and inherited.get('modelId')==model.get('modelId')==manifest['modelId'],code)
            reports=[f for f in manifest['files'] if f['path']=='parameterization-report.json']
            if reports and accepted:
                require(len(reports)==1 and reports[0]['sha256']==digest,code)
                return load(safe_file(path.parent,reports[0]['path']),digest),fingerprints
            request=load(safe_file(controller.project,'controller-requests/'+attempt_id+'.json'))
            require(request.get('schemaVersion')=='ksh.stage-request.v1'
                    and all(request[k]==manifest[k] for k in ('workflowId','revision','attemptId'))
                    and request['stage']['id']==manifest['stageId']
                    and hashlib.sha256(canonical(request['stage'])).hexdigest()==manifest['protocolSha256'],code)
            bundles=request['dependencyBundles']
            require(type(bundles) is list and len(bundles)==1 and manifest['parentBundles']==bundles,code)
            bundle=bundles[0];require(type(bundle) is dict and set(bundle)=={'path','sha256'},code)
            require(type(bundle['sha256']) is str and HASH.fullmatch(bundle['sha256']),code)
            parent_path=safe_file(safe_file(controller.runtime.root,attempt_id+'/input'),bundle['path'])
            parent=load(parent_path,bundle['sha256']);recovery=request.get('recovery')
            verified_manifest(parent_path,bundle['sha256'],recovery=recovery is not None)
            parent_value,parent_attempt=controller.store.find_attempt(parent['workflowId'],parent['attemptId'])
            require(parent_value['revision']==parent['revision'] and parent_attempt['stageId']==parent['stageId'],code)
            continuation=request.get('continuation')
            require(continuation is None or recovery is None,code)
            if continuation is not None or recovery is not None:
                kind='recovery' if recovery is not None else 'continuation'
                fields=['sourceWorkflowId','sourceRevision','sourceStageId','sourceAttemptId']
                if kind=='continuation':fields+=['modelId','minimized']
                reference=value.get('reference',{})
                require(not request['stage']['dependsOn'] and reference.get('kind')==kind
                        and request[kind]=={**bundle,**{k:reference[k] for k in fields}}
                        and reference['manifestSha256']==bundle['sha256']
                        and all(parent[k]==reference[v] for k,v in (
                            ('workflowId','sourceWorkflowId'),('revision','sourceRevision'),
                            ('stageId','sourceStageId'),('attemptId','sourceAttemptId'),('modelId','modelId'))),code)
                if recovery is not None:
                    registered=parent_attempt.get('recovery',{})
                    require(request['stage']['kind'] in {'equilibrate','production'}
                            and parent_attempt['state'] in {'FAILED','CANCELED'}
                            and registered.get('manifestSha256')==bundle['sha256']
                            and registered.get('manifestPath')==reference['manifestPath'],code)
            else:
                require(parent['workflowId']==manifest['workflowId']
                        and parent['revision']==manifest['revision']
                        and request['stage']['dependsOn']==[parent['stageId']],code)
            accepted=recovery is None
            if accepted:
                require(parent_attempt['state']=='COMPLETED'
                        and parent_attempt.get('acceptance',{}).get('manifestSha256')==bundle['sha256'],code)
            # Recovery supplies provenance only; its unaccepted report is never used.
            path,manifest,value=parent_path,parent,parent_value
    except (KeyError,TypeError,ValueError,OSError,AttributeError):
        raise BioError(code) from None


def resolve_source(controller, ref):
    validate_source(ref)
    if ref['kind']=='input':
        records=list_inputs(controller.project,0,32)['items']
        matches=[v for v in records if preview_input_id(v)==ref['sourceId'] and v['sha256']==ref['sourceSha256']]
        if not matches:
            # Old saved views remain usable only when ID + hash selects exactly one file.
            matches=[v for v in records if v['id']==ref['sourceId'] and v['sha256']==ref['sourceSha256']]
        require(len(matches)==1,'PREVIEW_SOURCE_CHANGED')
        record=matches[0];topology=safe_file(controller.project/'inputs',record['path']);files=[dict(absolutePath=str(topology),**record)]
        trajectories=[];model={};report={};manifest_path=None
    else:
        value=controller.store.get(ref['workflowId'])
        require(value['revision']==ref['revision'] and ref['sourceId']=='result-'+ref['attemptId'],'PREVIEW_SOURCE_CHANGED')
        matches=[a for a in value['attempts'] if a['stageId']==ref['stageId'] and a['attemptId']==ref['attemptId']
                 and a['state']=='COMPLETED' and a.get('acceptance',{}).get('manifestSha256')==ref['sourceSha256']]
        require(len(matches)==1,'RESULT_NOT_ACCEPTED')
        files=[];offset=0
        while True:
            page=controller.artifacts(ref['workflowId'],ref['stageId'],offset,100)
            require(page['attemptId']==ref['attemptId'],'PREVIEW_SOURCE_CHANGED')
            files.extend(page['items'])
            if page['nextOffset'] is None:break
            offset=page['nextOffset']
        topology_files=[Path(f['absolutePath']) for f in files if f['role']=='topology']
        require(len(topology_files)==1,'PREVIEW_TOPOLOGY_REQUIRED');topology=topology_files[0]
        trajectories=[Path(f['absolutePath']) for f in files if f['role']=='trajectory']
        if not trajectories:
            current=[Path(f['absolutePath']) for f in files if f['role']=='view-structure']
            if len(current)==1:topology=current[0]
        models=[Path(f['absolutePath']) for f in files if f['role']=='model']
        require(len(models)==1,'PREVIEW_MODEL_REQUIRED')
        manifest_path=safe_file(controller.runtime.root,ref['attemptId']+'/output/manifest.json')
    # Bind every expected file before parsing any model, report or coordinates.
    fingerprints={}
    if manifest_path:
        _merge_fingerprints(fingerprints,{str(manifest_path):_file_fingerprint(manifest_path,ref['sourceSha256'])})
    for record in files:
        path=Path(record['absolutePath'])
        _merge_fingerprints(fingerprints,{str(path):_file_fingerprint(path,record['sha256'])})
    if manifest_path:
        model=read_json(models[0])
        report,report_fingerprints=_ligand_report(controller,ref,files,model)
        _merge_fingerprints(fingerprints,report_fingerprints)
    import MDAnalysis as mda
    u=mda.Universe(str(topology))
    try:
        require(0<len(u.atoms)<=1000000,'PREVIEW_ATOM_BUDGET_EXCEEDED')
        if model:require(len(u.atoms)==model.get('particles'),'PARTICLE_COUNT_MISMATCH')
        atoms=[dict(index=int(a.index),name=str(a.name),resname=str(a.resname),element=str(getattr(a,'element',''))) for a in u.atoms]
        frames=[];previous_time=-math.inf;previous_step=-1
        if trajectories:
            timings={Path(f['absolutePath']) for f in files if f['role']=='trajectory-times'}
            for segment_index,path in enumerate(trajectories):
                timing=path.with_suffix('.times.csv');require(timing in timings,'TRAJECTORY_TIMES_MISSING')
                local_index=-1
                with timing.open() as stream:
                    reader=csv.DictReader(stream);require(reader.fieldnames==['stageStep','timePs'],'INVALID_TRAJECTORY_TIMES')
                    for local_index,row in enumerate(reader):
                        try:step=int(row['stageStep']);t=float(row['timePs'])
                        except (ValueError,TypeError):raise BioError('INVALID_TRAJECTORY_TIMES') from None
                        require(math.isfinite(t) and t>=0 and t>previous_time and step>previous_step,'INVALID_TRAJECTORY_TIMES')
                        require(len(frames)<LIMITS['maxSourceFrames'],'PREVIEW_FRAME_BUDGET_EXCEEDED')
                        frames.append(dict(sourceFrameIndex=len(frames),sourceSegment=segment_index,sourceLocalIndex=local_index,timePs=t,stageStep=step))
                        previous_time,previous_step=t,step
                # Frame count is checked for each file, not just the concatenation.
                with mda.coordinates.DCD.DCDReader(str(path)) as segment:
                    require(len(segment)==local_index+1 and segment.n_atoms==len(atoms),'INVALID_TRAJECTORY_TIMES')
        else:
            t=0.0
            states=[Path(f['absolutePath']) for f in files if f.get('role')=='state']
            if states:
                from ..artifacts import state_values
                t=state_values(states[0])['timePs']
            frames=[dict(sourceFrameIndex=0,sourceSegment=0,sourceLocalIndex=0,timePs=t,stageStep=0)]
        ligand=[]
        for atom in report.get('ligandParticleMap',[]):
            index=atom.get('particleIndex');require(type(index) is int and 0<=index<len(atoms),'PREVIEW_ATOM_MAPPING_INVALID')
            require(atom.get('name')==atoms[index]['name'],'PREVIEW_ATOM_MAPPING_INVALID')
            if atom.get('atomicNumber',0)>1:ligand.append(index)
        protein=[a['index'] for a in atoms if a['resname'] in PROTEIN]
        ca=[a['index'] for a in atoms if a['resname'] in PROTEIN and a['name']=='CA']
        unknown=[a['index'] for a in atoms if a['resname'] not in PROTEIN|SOLVENT and a['index'] not in ligand and a['element'] not in {'H','D'}]
        reason=('PROTEIN_CA_MISSING' if protein and not ca else 'LIGAND_NOT_IDENTIFIED' if unknown else
                'WATER_BOX_ONLY' if not protein and not ligand else None)
        deltas=[frames[i]['timePs']-frames[i-1]['timePs'] for i in range(1,len(frames))]
        normal=statistics.median(deltas) if deltas else 0
        gaps=[i for i in range(1,len(frames)) if frames[i]['timePs']-frames[i-1]['timePs']>normal*1.5]
        box=u.trajectory[0].triclinic_dimensions
        if trajectories:
            with mda.coordinates.DCD.DCDReader(str(trajectories[0])) as reader:box=reader[0].triclinic_dimensions
        source=dict(ref=dict(ref),topology=topology,trajectories=trajectories,atoms=atoms,frames=frames,
                    sourceFrameCount=len(frames),fingerprints=fingerprints,ligandIndices=ligand,caIndices=ca,
                    overviewReason=reason,gaps=gaps,boxVectorsAngstrom=box.tolist() if box is not None else None)
        check_source(source);return source
    finally:u.trajectory.close()
