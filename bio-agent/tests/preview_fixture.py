"""Engineering trajectory, explicitly synthetic: no dynamics or convergence claim."""
from pathlib import Path
from types import SimpleNamespace
import csv
import numpy as np
import MDAnalysis as mda
from bioagent.controller import Controller
from bioagent.errors import atomic_json, sha256_file


def make_preview_fixture(root: Path, *, frames: int, with_protein: bool = True) -> dict:
    output = root / "runtime-attempts/attempt/output"; output.mkdir(parents=True)
    names = ["N", "CA", "C", "C1", "O1", "O", "H1", "H2"] if with_protein else ["O", "H1", "H2"]
    residues = ["ALA", "LIG", "HOH"] if with_protein else ["HOH"]
    mapping = [0, 0, 0, 1, 1, 2, 2, 2] if with_protein else [0, 0, 0]
    u = mda.Universe.empty(len(names), n_residues=len(residues), atom_resindex=mapping, trajectory=True)
    for k, v in dict(names=names, resnames=residues, resids=list(range(1,len(residues)+1)),
                     elements=[n[0] for n in names], chainIDs=["A"]*len(names)).items(): u.add_TopologyAttr(k,v)
    u.atoms.positions = np.arange(len(names)*3, dtype=np.float32).reshape(-1,3)*.31
    u.dimensions = np.array([30,30,30,90,90,90],dtype=np.float32)
    u.atoms.write(str(output / "topology.pdb"))
    with mda.Writer(str(output / "trajectory.dcd"), n_atoms=len(names)) as writer:
        for index in range(frames):
            u.atoms.positions = np.arange(len(names)*3,dtype=np.float32).reshape(-1,3)*.31+index*.0123
            writer.write(u.atoms)
    with (output / "trajectory.times.csv").open('w',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['stageStep','timePs'])
        for i in range(frames):writer.writerow([10*(i+1),1.25+2.5*i])
    report=dict(ligandParticleMap=[dict(particleIndex=i,name=names[i],atomicNumber=6 if names[i][0]=='C' else 8) for i in (3,4)]) if with_protein else {}
    atomic_json(output/'parameterization-report.json',report)
    atomic_json(output/'model.json',dict(particles=len(names),parameterizationSha256=sha256_file(output/'parameterization-report.json'),engineeringFixture=True))
    roles={'topology.pdb':'topology','trajectory.dcd':'trajectory','trajectory.times.csv':'trajectory-times',
           'model.json':'model','parameterization-report.json':'parameterization-report'}
    manifest=dict(schemaVersion='ksh.stage-result.v1',acceptance=dict(engineering=True,scientificConvergence=False),
                  files=[dict(path=n,role=r,sha256=sha256_file(output/n),byteSize=(output/n).stat().st_size) for n,r in roles.items()])
    atomic_json(output/'manifest.json',manifest)
    ref=dict(kind='accepted',sourceId='result-attempt',sourceSha256=sha256_file(output/'manifest.json'),
             workflowId='wf',revision=1,stageId='production',attemptId='attempt')
    value=dict(workflowId='wf',revision=1,attempts=[dict(stageId='production',attemptId='attempt',state='COMPLETED',acceptance=dict(manifestSha256=ref['sourceSha256']))])
    controller=SimpleNamespace(project=root,runtime=SimpleNamespace(root=root/'runtime-attempts'),store=SimpleNamespace(get=lambda *a:value))
    controller.artifacts=lambda *a,**kw:Controller.artifacts(controller,*a,**kw)
    return dict(controller=controller,ref=ref,output=output,universe=u,value=value)


def refresh_fixture(fixture):
    import json
    output=fixture['output']; manifest=json.loads((output/'manifest.json').read_text())
    for file in manifest['files']:file.update(sha256=sha256_file(output/file['path']),byteSize=(output/file['path']).stat().st_size)
    atomic_json(output/'manifest.json',manifest)
    digest=sha256_file(output/'manifest.json');fixture['ref']['sourceSha256']=digest
    fixture['value']['attempts'][0]['acceptance']['manifestSha256']=digest
