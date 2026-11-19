"""Engineering-only READY preview for browser/stdio QA; never runs MD."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from preview_acceptance_fixture import engineering,derive
from bioagent.store import WorkflowStore
from bioagent.execution_target import local_target,bind_project
from bioagent.errors import canonical,atomic_json,sha256_file
from bioagent.visualization.direct_contracts import manifest_digest

def create_native_preview_fixture(out: Path, session_id: str) -> dict:
    project=out/'compute'/hashlib.sha256(session_id.encode()).hexdigest()
    c,ref=engineering(project);preview=derive(c,ref,project/'previews/files')
    target=local_target(project,Path(sys.executable));bind_project(target,project,Path(sys.executable))
    store=WorkflowStore(project)
    with store._connection(write=True) as db:
        db.execute('INSERT INTO workflows VALUES (?,?,?,?,?)',('engineering',1,'b'*64,'COMPLETED',canonical(dict(protocol={},bindings={})).decode()))
        stage=dict(id='production',kind='production',parameters=dict(dynamics=dict(steps=2030)))
        db.execute('INSERT INTO stages VALUES (?,?,?,?,?,?)',('engineering',1,'production',0,canonical(stage).decode(),'COMPLETED'))
        db.execute('INSERT INTO attempts VALUES (?,?,?,?,?,?)',('engineering','engineering',1,'production','COMPLETED',canonical(dict(acceptance=dict(manifestSha256=ref['sourceSha256']))).decode()))
    # A registered protein-only input provides the static structure choice.
    inputs=project/'inputs';inputs.mkdir(mode=0o700,exist_ok=True)
    topology=project/'runtime-attempts/engineering/output/topology.pdb'
    protein=''.join(line+'\n' for line in topology.read_text().splitlines() if line.startswith('ATOM') and line[17:20]=='ALA')+'END\n'
    (inputs/'engineering-protein.pdb').write_text(protein)
    job=project/'previews/jobs/job-engineering';job.mkdir(parents=True)
    atomic_json(job/'request.json',dict(request=preview['previewRequest']))
    atomic_json(job/'status.json',dict(state='READY',previewId=preview['manifest']['previewId']))
    original=[dict(path=str(f),sha256=sha256_file(f)) for f in (project/'runtime-attempts/engineering/output').iterdir() if f.is_file()]
    fixture={**preview,'engineeringOnly':True,'scientificConvergence':False,'sessionId':session_id,'project':str(project),'target':target,'originalFiles':original,'manifestSha256':manifest_digest(preview['manifest'])}
    atomic_json(out/'fixture.json',fixture)
    return fixture

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True);parser.add_argument('--session-id',default='session-test');args=parser.parse_args()
    create_native_preview_fixture(args.out,args.session_id)
    return 0
if __name__=='__main__':raise SystemExit(main())
