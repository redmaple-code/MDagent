"""Small engineering-only published preview and accepted metadata, without MD."""
import hashlib
from pathlib import Path
import sys
from bioagent.errors import atomic_json, canonical
from bioagent.execution_target import bind_project, local_target
from bioagent.store import WorkflowStore
from bioagent.visualization.sources import resolve_source
from bioagent.visualization.preview_plan import plan_preview
from bioagent.visualization.preview_files import write_preview
from tests.preview_fixture import make_preview_fixture


def make_direct_fixture(root: Path, *, frames=3):
    fixture = make_preview_fixture(root, frames=frames)
    target = local_target(root, Path(sys.executable))
    bind_project(target, root, Path(sys.executable))
    source = resolve_source(fixture['controller'], fixture['ref'])
    request = dict(source=fixture['ref'], profile='overview', atomSet='overview', startFrame=0, endFrame=frames - 1, stride=1)
    plan = plan_preview(source, request)
    manifest = write_preview(source, plan, root/'previews/files'/plan['previewId'])
    job = root/'previews/jobs/job-direct-fixture'; job.mkdir(parents=True)
    atomic_json(job/'request.json', dict(request=request))
    atomic_json(job/'status.json', dict(state='READY', previewId=manifest['previewId']))
    store = WorkflowStore(root)
    with store._connection(write=True) as db:
        db.execute('INSERT INTO workflows VALUES (?,?,?,?,?)', ('wf', 1, 'b'*64, 'COMPLETED', canonical(dict(protocol={}, bindings={})).decode()))
        db.execute('INSERT INTO stages VALUES (?,?,?,?,?,?)', ('wf', 1, 'production', 0, '{}', 'COMPLETED'))
        db.execute('INSERT INTO attempts VALUES (?,?,?,?,?,?)', ('attempt', 'wf', 1, 'production', 'COMPLETED', canonical(dict(acceptance=dict(manifestSha256=fixture['ref']['sourceSha256']))).decode()))
    fixture.update(target=target, store=store, manifest=manifest)
    return fixture


def direct_binding(fixture, *, client='client-test'):
    from bioagent.execution_target import target_sha256
    from bioagent.visualization.direct_contracts import manifest_digest
    return dict(tenantId=fixture['target']['tenantId'], nodeId=fixture['target']['nodeId'],
                sessionId='session-test', viewportId='main', agentEpoch='epoch-test', clientId=client,
                pairingId='pair-test', target=fixture['target'], projectKey=fixture['controller'].project.name,
                previewId=fixture['manifest']['previewId'], source=fixture['ref'],
                manifestSha256=manifest_digest(fixture['manifest']),
                transportBinding=dict(ownerBinding=hashlib.sha256(b'local-owner').hexdigest(), sessionEpoch='epoch-test',
                                      executionTargetSha256=target_sha256(fixture['target'])))
