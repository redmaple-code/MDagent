"""Browser QA data: labelled engineering pressure plus read-only prior MD artifacts."""
from pathlib import Path
import argparse
import csv
import json
import sqlite3
import sys
import time
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import MDAnalysis as mda
import numpy as np
from bioagent.errors import atomic_json,sha256_file
from bioagent.controller import Controller
from bioagent.store import WorkflowStore
from bioagent.visualization.sources import resolve_source,check_source
from bioagent.visualization.preview_plan import plan_preview
from bioagent.visualization.preview_files import write_preview


def engineering(root, *, atoms=306, frames=203, pressure=False):
    output=root/'runtime-attempts/engineering/output';output.mkdir(parents=True)
    n=atoms;resindex=list(range(300))+[300]*3+[301]*3 if not pressure else list(range(n))
    names=['CA']*300+['C1','O1','N1','O','H1','H2'] if not pressure else ['C']*n
    resnames=['ALA']*300+['LIG','HOH'] if not pressure else ['UNK']*n
    u=mda.Universe.empty(n,n_residues=len(resnames),atom_resindex=resindex,trajectory=True)
    for key,val in dict(names=names,resnames=resnames,resids=list(range(1,len(resnames)+1)),elements=[name[0] for name in names],chainIDs=['A']*n).items():u.add_TopologyAttr(key,val)
    index=np.arange(n)
    if pressure:positions=np.column_stack((index%46,(index//46)%46,index//2116)).astype(np.float32)*2
    else:positions=np.column_stack((12*np.cos(index*.3)+8*np.cos(index*.047),12*np.sin(index*.3),8*np.sin(index*.047))).astype(np.float32)
    u.atoms.positions=positions;u.dimensions=np.array([150,150,150,90,90,90],dtype=np.float32);u.atoms.write(str(output/'topology.pdb'))
    with mda.Writer(str(output/'trajectory.dcd'),n_atoms=n) as writer:
        for frame in range(frames):u.atoms.positions=positions+np.sin(index[:,None]*.1+frame*.08)*.7;writer.write(u.atoms)
    with (output/'trajectory.times.csv').open('w',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['stageStep','timePs']);writer.writerows((10*(i+1),2.5*(i+1)) for i in range(frames))
    report={} if pressure else dict(ligandParticleMap=[dict(particleIndex=i,name=names[i],atomicNumber=z) for i,z in ((300,6),(301,8),(302,7))])
    atomic_json(output/'parameterization-report.json',report)
    atomic_json(output/'model.json',dict(particles=n,engineeringFixture=True,parameterizationSha256=sha256_file(output/'parameterization-report.json')))
    roles={'topology.pdb':'topology','trajectory.dcd':'trajectory','trajectory.times.csv':'trajectory-times','model.json':'model','parameterization-report.json':'parameterization-report'}
    atomic_json(output/'manifest.json',dict(schemaVersion='ksh.stage-result.v1',acceptance=dict(engineering=True,scientificConvergence=False),files=[dict(path=f,role=r,sha256=sha256_file(output/f),byteSize=(output/f).stat().st_size) for f,r in roles.items()]))
    ref=dict(kind='accepted',sourceId='result-engineering',sourceSha256=sha256_file(output/'manifest.json'),workflowId='engineering',revision=1,stageId='production',attemptId='engineering')
    value=dict(workflowId='engineering',revision=1,attempts=[dict(stageId='production',attemptId='engineering',state='COMPLETED',acceptance=dict(manifestSha256=ref['sourceSha256']))])
    controller=SimpleNamespace(project=root,runtime=SimpleNamespace(root=root/'runtime-attempts'),store=SimpleNamespace(get=lambda *a:value))
    controller.artifacts=lambda *a:Controller.artifacts(controller,*a)
    return controller,ref


def derive(controller,ref,destination,atom_set='overview'):
    started=time.monotonic();source=resolve_source(controller,ref)
    request=dict(source=ref,profile='overview',atomSet=atom_set,startFrame=0,endFrame=source['sourceFrameCount']-1,stride=1)
    plan=plan_preview(source,request);manifest=write_preview(source,plan,destination/plan['previewId']);check_source(source)
    atomic_json(destination.parent/'manifest.json',manifest)
    return dict(manifest=manifest,previewDirectory=str(destination/plan['previewId']),deriveSeconds=time.monotonic()-started,
                originalHashesUnchanged=True,originalFiles=len(source['fingerprints']),previewRequest=request,
                decodedMaxBytes=max(f['previewFrameCount']*manifest['atomCount']*12 for f in manifest['files'] if f['kind']=='xtc'))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',required=True,type=Path);parser.add_argument('--real-project',type=Path);args=parser.parse_args()
    c,ref=engineering(args.out/'engineering');benchmark=derive(c,ref,args.out/'engineering/previews')
    c,ref=engineering(args.out/'pressure',atoms=100000,frames=1,pressure=True);pressure=derive(c,ref,args.out/'pressure/previews','all')
    real=dict(status='NOT_ACCEPTED',reason='NO_EXISTING_REAL_PROJECT')
    if args.real_project:
        try:
            project=args.real_project.resolve();db=project/'workflow.sqlite3'
            with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as connection:
                connection.row_factory=sqlite3.Row;store=WorkflowStore.__new__(WorkflowStore)
                rows=connection.execute("SELECT * FROM attempts WHERE state='COMPLETED' AND stage_id='production' ORDER BY workflow_id LIKE '%protein%' DESC,rowid DESC").fetchall()
                for row in rows:
                    value=store._get(connection,row['workflow_id']);attempt=next((a for a in value['attempts'] if a['attemptId']==row['attempt_id']),None)
                    if not attempt or not attempt.get('acceptance'):continue
                    ref=dict(kind='accepted',sourceId='result-'+attempt['attemptId'],sourceSha256=attempt['acceptance']['manifestSha256'],workflowId=value['workflowId'],revision=value['revision'],stageId=attempt['stageId'],attemptId=attempt['attemptId'])
                    c=SimpleNamespace(project=project,runtime=SimpleNamespace(root=project/'runtime-attempts'),store=SimpleNamespace(get=lambda *a:value));c.artifacts=lambda *a:Controller.artifacts(c,*a)
                    try:
                        s=resolve_source(c,ref);atom_set='overview' if s['overviewReason'] is None else 'solute-heavy'
                        real={**derive(c,ref,args.out/'real/previews',atom_set), 'status':'READ_AND_MAPPED','scientificConvergenceAssessed':False};break
                    except Exception:continue
        except Exception as error:real=dict(status='NOT_ACCEPTED',reason=type(error).__name__)
    atomic_json(args.out/'fixtures.json',dict(benchmark=benchmark,pressure=pressure,real=real,engineeringOnly=True))
    return 0

if __name__=='__main__':raise SystemExit(main())
