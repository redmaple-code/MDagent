"""Preview derivation must keep the exact bytes accepted before parsing."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import MDAnalysis as mda

from bioagent.errors import BioError, atomic_json, read_json, sha256_file
from bioagent.intake import list_inputs
from bioagent.visualization import sources
from bioagent.visualization.preview_files import write_preview
from bioagent.visualization.preview_plan import plan_preview
from bioagent.visualization.published_reader import PublishedPreviewReader
from tests import test_preview_ligand_lineage as lineage
from tests.preview_fixture import make_preview_fixture


class PreviewSourceSnapshotTests(unittest.TestCase):
    def accepted(self, *, trajectory=False):
        fixture = lineage.PreviewLigandLineageTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        node = fixture.prepared
        if trajectory:
            node = fixture.inherit(node, 'dynamics', 'production', kind='production')
            temporary = tempfile.TemporaryDirectory(prefix='bio-snapshot-trajectory-')
            self.addCleanup(temporary.cleanup)
            generated = make_preview_fixture(Path(temporary.name), frames=3)
            self.addCleanup(generated['universe'].trajectory.close)
            for name, role in [('trajectory.dcd', 'trajectory'), ('trajectory.times.csv', 'trajectory-times')]:
                target = node['output'] / name
                target.write_bytes((generated['output'] / name).read_bytes())
                node['manifest']['files'].append(dict(path=name, role=role, sha256=sha256_file(target),
                                                     byteSize=target.stat().st_size))
            fixture.resign(node)
        return fixture, node

    def input(self):
        temporary = tempfile.TemporaryDirectory(prefix='bio-snapshot-input-')
        self.addCleanup(temporary.cleanup)
        project = Path(temporary.name)
        path = project / 'inputs/protein.pdb'
        path.parent.mkdir()
        path.write_text('ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00           C  \nEND\n')
        record = list_inputs(project, 0, 32)['items'][0]
        ref = dict(kind='input', sourceId=sources.preview_input_id(record), sourceSha256=record['sha256'],
                   workflowId=None, revision=None, stageId=None, attemptId=None)
        return SimpleNamespace(project=project), ref, path

    def change(self, path):
        if path.suffix == '.json':
            atomic_json(path, {**read_json(path), 'changedDuringRead': True})
        elif path.suffix == '.pdb':
            lines = path.read_text().splitlines(keepends=True)
            for index, line in enumerate(lines):
                if line.startswith(('ATOM  ', 'HETATM')):
                    lines[index] = line[:30] + f'{float(line[30:38]) + 7.0:8.3f}' + line[38:]
            path.write_text(''.join(lines))
        elif path.suffix == '.csv':
            path.write_text(path.read_text().replace('6.25', '7.25'))
        else:
            replacement = path.with_name('replacement.dcd')
            universe = mda.Universe(str(path.parent / 'topology.pdb'), str(path))
            try:
                with mda.Writer(str(replacement), n_atoms=len(universe.atoms)) as writer:
                    for _ in universe.trajectory:
                        universe.atoms.positions += 7.0
                        writer.write(universe)
            finally:
                universe.trajectory.close()
            replacement.replace(path)

    def test_input_change_after_catalog_hashing_is_rejected(self):
        controller, ref, path = self.input()
        original = sources.list_inputs

        def changed_after_list(*args):
            page = original(*args)
            self.change(path)
            return page

        with patch.object(sources, 'list_inputs', side_effect=changed_after_list):
            with self.assertRaisesRegex(BioError, 'PREVIEW_SOURCE_CHANGED'):
                sources.resolve_source(controller, ref)

    def test_manifest_change_after_artifact_verification_is_rejected(self):
        fixture, node = self.accepted()
        original = fixture.controller.artifacts

        def changed_after_artifacts(*args):
            page = original(*args)
            self.change(node['output'] / 'manifest.json')
            return page

        with patch.object(fixture.controller, 'artifacts', side_effect=changed_after_artifacts):
            with self.assertRaisesRegex(BioError, 'PREVIEW_SOURCE_CHANGED'):
                sources.resolve_source(fixture.controller, node['ref'])

    def test_each_artifact_change_after_verification_is_rejected_before_parsing(self):
        for name in ('topology.pdb', 'model.json', 'protocol.json', 'parameterization-report.json',
                     'trajectory.dcd', 'trajectory.times.csv'):
            with self.subTest(name=name):
                fixture, node = self.accepted(trajectory=name.startswith('trajectory.'))
                original = fixture.controller.artifacts

                def changed_after_artifacts(*args):
                    page = original(*args)
                    self.change(node['output'] / name)
                    return page

                with patch.object(fixture.controller, 'artifacts', side_effect=changed_after_artifacts):
                    with self.assertRaisesRegex(BioError, 'PREVIEW_SOURCE_CHANGED'):
                        sources.resolve_source(fixture.controller, node['ref'])

    def test_direct_report_read_does_not_rebind_changed_topology_or_manifest(self):
        for name in ('topology.pdb', 'manifest.json'):
            with self.subTest(name=name):
                fixture, node = self.accepted()
                original = sources._ligand_report

                def changed_after_report(*args):
                    value = original(*args)
                    self.change(node['output'] / name)
                    return value

                with patch.object(sources, '_ligand_report', side_effect=changed_after_report):
                    with self.assertRaisesRegex(BioError, 'PREVIEW_SOURCE_CHANGED'):
                        sources.resolve_source(fixture.controller, node['ref'])

    def test_change_during_initial_hash_read_is_rejected(self):
        fixture, node = self.accepted()
        path = node['output'] / 'topology.pdb'
        original = sources.sha256_file

        def changed_after_hash(value):
            digest = original(value)
            if Path(value) == path:
                self.change(path)
            return digest

        with patch.object(sources, 'sha256_file', side_effect=changed_after_hash):
            with self.assertRaisesRegex(BioError, 'PREVIEW_SOURCE_CHANGED'):
                sources.resolve_source(fixture.controller, node['ref'])

    def test_report_fingerprint_cannot_overwrite_the_original_file_identity(self):
        fixture, node = self.accepted()
        original = sources._ligand_report
        path = node['output'] / 'parameterization-report.json'

        def replaced_before_report(*args):
            replacement = path.with_name('replacement.json')
            replacement.write_bytes(path.read_bytes())
            replacement.replace(path)
            return original(*args)

        with patch.object(sources, '_ligand_report', side_effect=replaced_before_report):
            with self.assertRaisesRegex(BioError, 'PREVIEW_SOURCE_CHANGED'):
                sources.resolve_source(fixture.controller, node['ref'])

    def test_changes_after_coordinate_parsing_still_reject_the_original_snapshot(self):
        for kind, name in [('input', 'topology.pdb'), ('accepted', 'topology.pdb'),
                           ('accepted', 'manifest.json')]:
            with self.subTest(kind=kind, name=name):
                if kind == 'input':
                    controller, ref, path = self.input()
                else:
                    fixture, node = self.accepted()
                    controller, ref, path = fixture.controller, node['ref'], node['output'] / name
                original = mda.Universe

                def changed_after_parse(*args, **kwargs):
                    universe = original(*args, **kwargs)
                    self.change(path)
                    return universe

                with patch.object(mda, 'Universe', side_effect=changed_after_parse):
                    with self.assertRaisesRegex(BioError, 'PREVIEW_SOURCE_CHANGED'):
                        sources.resolve_source(controller, ref)

    def test_unchanged_input_and_accepted_sources_keep_publishable_exact_snapshots(self):
        for kind in ('input', 'accepted'):
            with self.subTest(kind=kind):
                if kind == 'input':
                    controller, ref, _ = self.input()
                else:
                    fixture, node = self.accepted()
                    controller, ref = fixture.controller, node['ref']
                source = sources.resolve_source(controller, ref)
                self.assertTrue(source['fingerprints'])
                for path, record in source['fingerprints'].items():
                    self.assertEqual(record['sha256'], sha256_file(Path(path)))
                if kind == 'accepted':
                    manifest = controller.runtime.root / ref['attemptId'] / 'output/manifest.json'
                    self.assertEqual(source['fingerprints'][str(manifest)]['sha256'], ref['sourceSha256'])
                request = dict(source=ref, profile='overview', atomSet='overview', startFrame=0, endFrame=0, stride=1)
                plan = plan_preview(source, request)
                destination = controller.project / 'previews/files' / plan['previewId']
                write_preview(source, plan, destination)
                job = controller.project / 'previews/jobs/job-snapshot'
                job.mkdir(parents=True)
                atomic_json(job / 'status.json', dict(state='READY', previewId=plan['previewId']))
                atomic_json(job / 'request.json', dict(request=request))
                reader = PublishedPreviewReader(controller.project, getattr(controller, 'store', None))
                reply = reader.read_bytes(plan['previewId'], 'topology.pdb', 0, 4096)
                self.assertEqual(reply['sourceSha256'], ref['sourceSha256'])
                self.assertGreater(len(reply['data']), 0)


if __name__ == '__main__':
    unittest.main()
