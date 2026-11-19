"""Accepted stage inheritance must retain the model's verified ligand identity."""
from pathlib import Path
import copy
import tempfile
import unittest
from unittest.mock import patch

from bioagent.controller import Controller
from bioagent.errors import BioError, atomic_json, canonical, read_json, sha256_file
from bioagent.schema import load_runner
from bioagent.store import WorkflowStore
from bioagent.visualization.sources import check_source, resolve_source
from bioagent.visualization import sources
from bioagent.visualization.preview_plan import plan_preview
from tests.preview_fixture import make_preview_fixture
from tests.support import stage


class PreviewLigandLineageTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='bio-preview-ligand-lineage-')
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name)
        fixture = make_preview_fixture(self.project, frames=3)
        self.controller = fixture['controller']
        self.controller.store = WorkflowStore(self.project)
        self.controller.artifacts = lambda *args, **kwargs: Controller.artifacts(self.controller, *args, **kwargs)
        self.nodes = {}
        output = fixture['output']
        model = read_json(output / 'model.json')
        model.update(modelId='a' * 64, minimized=False)
        atomic_json(output / 'model.json', model)
        # The preparation report is real metadata; preparation emits no trajectory.
        for name in ('trajectory.dcd', 'trajectory.times.csv'):
            (output / name).unlink()
        roles = {'topology.pdb': 'topology', 'model.json': 'model',
                 'parameterization-report.json': 'parameter-provenance'}
        self.prepared = self.publish('attempt', 'wf', stage('prepare', 'prepare', []), roles, [])

    def publish(self, attempt_id, workflow_id, specification, roles, bundles, *, reference=None):
        output = self.project / 'runtime-attempts' / attempt_id / 'output'
        output.mkdir(parents=True, exist_ok=True)
        atomic_json(output / 'protocol.json', specification)
        roles = {**roles, 'protocol.json': 'protocol'}
        manifest = dict(schemaVersion='ksh.stage-result.v1', workflowId=workflow_id, revision=1,
                        stageId=specification['id'], attemptId=attempt_id, modelId='a' * 64,
                        protocolSha256=sha256_file(output / 'protocol.json'), parentBundles=bundles,
                        acceptance=dict(engineering=True, scientificConvergence=False),
                        files=[dict(path=name, role=role, sha256=sha256_file(output / name),
                                    byteSize=(output / name).stat().st_size) for name, role in roles.items()])
        atomic_json(output / 'manifest.json', manifest)
        request = dict(schemaVersion='ksh.stage-request.v1', workflowId=workflow_id, revision=1,
                       attemptId=attempt_id, stage=specification, inputs=[], dependencyBundles=bundles)
        if reference:
            fields = ['sourceWorkflowId', 'sourceRevision', 'sourceStageId', 'sourceAttemptId']
            if reference['kind'] == 'continuation':
                fields += ['modelId', 'minimized']
            request[reference['kind']] = {**bundles[0], **{key: reference[key] for key in fields}}
        atomic_json(self.project / 'controller-requests' / (attempt_id + '.json'), request)
        acceptance = dict(manifestSha256=sha256_file(output / 'manifest.json'), modelId='a' * 64)
        with self.controller.store._connection(write=True) as connection:
            data = dict(protocol={}, bindings={})
            if reference:
                data['reference'] = reference
            connection.execute('INSERT OR IGNORE INTO workflows VALUES (?,?,?,?,?)',
                               (workflow_id, 1, 'b' * 64, 'COMPLETED', canonical(data).decode()))
            connection.execute('INSERT INTO stages VALUES (?,?,?,?,?,?)',
                               (workflow_id, 1, specification['id'], len(self.nodes), canonical(specification).decode(), 'COMPLETED'))
            connection.execute('INSERT INTO attempts VALUES (?,?,?,?,?,?)',
                               (attempt_id, workflow_id, 1, specification['id'], 'COMPLETED', canonical(dict(acceptance=acceptance)).decode()))
        ref = dict(kind='accepted', sourceId='result-' + attempt_id, sourceSha256=acceptance['manifestSha256'],
                   workflowId=workflow_id, revision=1, stageId=specification['id'], attemptId=attempt_id)
        node = dict(ref=ref, output=output, manifest=manifest, request=request, roles=roles)
        self.nodes[attempt_id] = node
        return node

    def inherit(self, parent, attempt_id='child', stage_id='validate', *, workflow_id='wf',
                kind='validate', reference_kind='continuation'):
        load_runner()
        from openmm_workflow.stages import Stage
        output = self.project / 'runtime-attempts' / attempt_id / 'output'
        output.mkdir(parents=True)
        inherited = object.__new__(Stage)
        inherited.output, inherited.roles = output, {}
        inherited.parent = lambda: (parent['output'], parent['manifest'], read_json(parent['output'] / 'model.json'))
        # This invokes the actual file inheritance boundary, without constructing OpenMM.
        inherited.copy_parent()
        cross_workflow = workflow_id != parent['ref']['workflowId']
        prefix = 'source' if cross_workflow else parent['ref']['stageId']
        root = output.parent / 'input' / 'deps' / prefix
        root.mkdir(parents=True)
        for entry in parent['manifest']['files']:
            (root / entry['path']).write_bytes((parent['output'] / entry['path']).read_bytes())
        (root / 'manifest.json').write_bytes((parent['output'] / 'manifest.json').read_bytes())
        bundle = dict(path='deps/' + prefix + '/manifest.json', sha256=parent['ref']['sourceSha256'])
        specification = stage(kind, stage_id, [] if cross_workflow else [parent['ref']['stageId']])
        reference = None
        if cross_workflow:
            reference = dict(kind=reference_kind, sourceWorkflowId=parent['ref']['workflowId'], sourceRevision=1,
                             sourceStageId=parent['ref']['stageId'], sourceAttemptId=parent['ref']['attemptId'],
                             manifestPath=parent.get('manifestPath', 'manifest.json'), manifestSha256=parent['ref']['sourceSha256'],
                             modelId='a' * 64, minimized=read_json(parent['output'] / 'model.json')['minimized'])
        return self.publish(attempt_id, workflow_id, specification, inherited.roles, [bundle], reference=reference)

    def resign(self, node):
        for entry in node['manifest']['files']:
            path = node['output'] / entry['path']
            entry.update(sha256=sha256_file(path), byteSize=path.stat().st_size)
        atomic_json(node['output'] / 'manifest.json', node['manifest'])
        atomic_json(self.project / 'controller-requests' / (node['ref']['attemptId'] + '.json'), node['request'])
        node['ref']['sourceSha256'] = sha256_file(node['output'] / 'manifest.json')
        with self.controller.store._connection(write=True) as connection:
            connection.execute('UPDATE attempts SET data_json=? WHERE attempt_id=?',
                               (canonical(dict(acceptance=dict(manifestSha256=node['ref']['sourceSha256'], modelId='a' * 64))).decode(),
                                node['ref']['attemptId']))

    def dependency_root(self, node):
        return node['output'].parent / 'input' / Path(node['request']['dependencyBundles'][0]['path']).parent

    def recovered(self, *, include_unaccepted_report=False):
        minimized = self.inherit(self.prepared, 'minimized', 'minimize', kind='minimize')
        model = read_json(minimized['output'] / 'model.json')
        model['minimized'] = True
        atomic_json(minimized['output'] / 'model.json', model)
        self.resign(minimized)
        interrupted = self.inherit(minimized, 'interrupted', 'production', kind='production')
        root = interrupted['output'] / 'recovery/000000000001'
        root.mkdir(parents=True)
        manifest = copy.deepcopy(interrupted['manifest'])
        for entry in manifest['files']:
            (root / entry['path']).write_bytes((interrupted['output'] / entry['path']).read_bytes())
        if include_unaccepted_report:
            report = root / 'parameterization-report.json'
            report.write_bytes((self.prepared['output'] / report.name).read_bytes())
            manifest['files'].append(dict(path=report.name, role='parameter-provenance',
                                          sha256=sha256_file(report), byteSize=report.stat().st_size))
        manifest.update(acceptance=dict(engineering=False, recovery=True), checkpointStep=1, checkpointTimePs=1.0)
        atomic_json(root / 'manifest.json', manifest)
        recovery = dict(manifestPath='recovery/000000000001/manifest.json', manifestSha256=sha256_file(root / 'manifest.json'),
                        modelId='a' * 64, checkpointStep=1, checkpointTimePs=1.0, minimized=True)
        with self.controller.store._connection(write=True) as connection:
            connection.execute("UPDATE attempts SET state='FAILED',data_json=? WHERE attempt_id='interrupted'",
                               (canonical(dict(recovery=recovery)).decode(),))
        recovered_parent = dict(output=root, manifest=manifest,
                                ref={**interrupted['ref'], 'sourceSha256': recovery['manifestSha256']},
                                manifestPath=recovery['manifestPath'])
        resumed = self.inherit(recovered_parent, 'resumed', 'production', workflow_id='resumed-wf',
                               kind='production', reference_kind='recovery')
        return resumed, interrupted, minimized

    def overview(self, node):
        source = resolve_source(self.controller, node['ref'])
        request = dict(source=node['ref'], profile='overview', atomSet='overview',
                       startFrame=0, endFrame=source['sourceFrameCount'] - 1, stride=1)
        return source, plan_preview(source, request)

    def test_inherited_stage_overview_keeps_the_accepted_preparation_ligand(self):
        child = self.inherit(self.prepared)
        self.assertFalse((child['output'] / 'parameterization-report.json').exists())
        source = resolve_source(self.controller, child['ref'])
        self.assertEqual(source['ligandIndices'], [3, 4])
        _, plan = self.overview(child)
        self.assertEqual(plan['atomIndices'], [1, 3, 4])

    def test_multiple_inherited_stages_preserve_the_same_ligand_atom_set(self):
        child = self.inherit(self.inherit(self.prepared), 'grandchild', 'validate-again')
        source = resolve_source(self.controller, child['ref'])
        self.assertEqual(source['ligandIndices'], [3, 4])
        self.assertEqual(self.overview(child)[1]['atomIndices'], [1, 3, 4])

    def test_registered_continuation_can_recover_the_preceding_workflow_ligand(self):
        child = self.inherit(self.prepared, workflow_id='continued-wf')
        self.assertEqual(resolve_source(self.controller, child['ref'])['ligandIndices'], [3, 4])

    def test_registered_recovery_reaches_the_accepted_ancestor_report(self):
        resumed, _, _ = self.recovered()
        try:
            source = resolve_source(self.controller, resumed['ref'])
        except BioError as error:
            self.fail('registered recovery lost its accepted ligand lineage: ' + error.code)
        self.assertEqual(source['ligandIndices'], [3, 4])

    def test_recovery_cannot_supply_a_report_from_an_unaccepted_attempt(self):
        resumed, _, minimized = self.recovered(include_unaccepted_report=True)
        (self.dependency_root(minimized) / 'parameterization-report.json').write_text('{}')
        with self.assertRaises(BioError):
            resolve_source(self.controller, resumed['ref'])

    def test_unaccepted_ancestor_cannot_supply_the_ligand_report(self):
        child = self.inherit(self.prepared)
        with self.controller.store._connection(write=True) as connection:
            connection.execute("UPDATE attempts SET state='FAILED' WHERE attempt_id='attempt'")
        with self.assertRaises(BioError):
            resolve_source(self.controller, child['ref'])

    def test_changed_dependency_report_is_rejected(self):
        child = self.inherit(self.prepared)
        (self.dependency_root(child) / 'parameterization-report.json').write_text('{}')
        with self.assertRaises(BioError):
            resolve_source(self.controller, child['ref'])

    def test_report_hash_must_match_the_inherited_model(self):
        child = self.inherit(self.prepared)
        model = read_json(child['output'] / 'model.json')
        model['parameterizationSha256'] = 'c' * 64
        atomic_json(child['output'] / 'model.json', model)
        self.resign(child)
        with self.assertRaises(BioError):
            resolve_source(self.controller, child['ref'])

    def test_unregistered_cross_workflow_parent_is_rejected(self):
        child = self.inherit(self.prepared)
        parent = read_json(self.dependency_root(child) / 'manifest.json')
        parent['workflowId'] = 'foreign-wf'
        atomic_json(self.dependency_root(child) / 'manifest.json', parent)
        child['request']['dependencyBundles'][0]['sha256'] = sha256_file(self.dependency_root(child) / 'manifest.json')
        self.resign(child)
        with self.assertRaises(BioError):
            resolve_source(self.controller, child['ref'])

    def test_request_cannot_redirect_the_accepted_parent_bundle(self):
        child = self.inherit(self.prepared)
        request = copy.deepcopy(child['request'])
        request['dependencyBundles'][0]['path'] = 'deps/unregistered/manifest.json'
        atomic_json(self.project / 'controller-requests/child.json', request)
        with self.assertRaises(BioError):
            resolve_source(self.controller, child['ref'])

    def test_parent_bundle_cannot_escape_the_attempt_input(self):
        child = self.inherit(self.prepared)
        child['request']['dependencyBundles'][0]['path'] = '../output/manifest.json'
        self.resign(child)
        with self.assertRaises(BioError):
            resolve_source(self.controller, child['ref'])

    def test_multiple_parent_bundles_are_rejected_instead_of_guessing(self):
        child = self.inherit(self.prepared)
        child['request']['dependencyBundles'].append(copy.deepcopy(child['request']['dependencyBundles'][0]))
        self.resign(child)
        with self.assertRaises(BioError):
            resolve_source(self.controller, child['ref'])

    def test_repeated_attempt_identity_is_rejected(self):
        child = self.inherit(self.prepared)
        parent = read_json(self.dependency_root(child) / 'manifest.json')
        parent.update(attemptId='child', stageId='validate')
        atomic_json(self.dependency_root(child) / 'manifest.json', parent)
        child['request']['dependencyBundles'][0]['sha256'] = sha256_file(self.dependency_root(child) / 'manifest.json')
        self.resign(child)
        with self.assertRaises(BioError):
            resolve_source(self.controller, child['ref'])

    def test_cached_source_is_invalidated_when_the_ancestor_report_changes(self):
        child = self.inherit(self.prepared)
        source = resolve_source(self.controller, child['ref'])
        (self.dependency_root(child) / 'parameterization-report.json').write_text('{}')
        with self.assertRaisesRegex(BioError, 'PREVIEW_SOURCE_CHANGED'):
            check_source(source)

    def test_manifest_replaced_during_lineage_resolution_is_rejected(self):
        child = self.inherit(self.prepared)
        resolve_report = sources._ligand_report
        def replace_after_read(*args):
            result = resolve_report(*args)
            atomic_json(child['output'] / 'manifest.json', {**child['manifest'], 'replacedDuringRead': True})
            return result
        with patch.object(sources, '_ligand_report', side_effect=replace_after_read):
            with self.assertRaisesRegex(BioError, 'PREVIEW_SOURCE_CHANGED'):
                resolve_source(self.controller, child['ref'])


if __name__ == '__main__':
    unittest.main()
