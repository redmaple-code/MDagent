from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from bioagent.errors import BioError
from bioagent.intake import list_inputs
from bioagent.visualization.sources import resolve_source
from bioagent.visualization.preview_plan import plan_preview
from tests.preview_fixture import make_preview_fixture, refresh_fixture


class PreviewSourcesTest(unittest.TestCase):
    def test_legacy_input_identity_requires_id_and_hash_to_be_unambiguous(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        project=Path(tmp.name);(project/'inputs').mkdir()
        data='ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00           C  \nEND\n'
        (project/'inputs'/'Protein.pdb').write_text(data)
        (project/'inputs'/'protein.ent').write_text(data.replace('0.000','1.000'))
        record=list_inputs(project,0,32)['items'][0]
        ref=dict(kind='input',sourceId=record['id'],sourceSha256=record['sha256'],workflowId=None,revision=None,stageId=None,attemptId=None)
        self.assertEqual(resolve_source(SimpleNamespace(project=project),ref)['topology'].name,record['path'])
        (project/'inputs'/'protein.ent').write_text(data)
        with self.assertRaisesRegex(BioError,'PREVIEW_SOURCE_CHANGED'):resolve_source(SimpleNamespace(project=project),ref)

    def fixture(self, frames=203, **kw):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        return make_preview_fixture(Path(tmp.name),frames=frames,**kw)

    def plan(self,fixture,**kw):
        source=resolve_source(fixture['controller'],fixture['ref'])
        request=dict(source=fixture['ref'],profile='overview',atomSet='overview',startFrame=0,endFrame=source['sourceFrameCount']-1,stride=1)
        return source,plan_preview(source,{**request,**kw})

    def test_accepted_source_hash_and_order(self):
        f=self.fixture();s,p=self.plan(f)
        self.assertEqual(s['sourceFrameCount'],203);self.assertEqual(p['atomIndices'],[1,3,4])
        self.assertEqual(p['frames'][0]['timePs'],1.25)
        self.assertEqual(p['frames'][-1]['timePs'],506.25)

    def test_overview_includes_actual_last_frame(self):
        for count in (1,2,201,203):
            f=self.fixture(count);_,p=self.plan(f)
            indices=[v['sourceFrameIndex'] for v in p['frames']]
            self.assertEqual((indices[0],indices[-1]),(0,count-1))
            self.assertLessEqual(len(indices),200);self.assertEqual(indices,sorted(set(indices)))

    def test_duplicate_or_reverse_time_rejected(self):
        for times in ('1,2\n2,2\n','1,3\n2,2\n'):
            f=self.fixture(2);(f['output']/'trajectory.times.csv').write_text('stageStep,timePs\n'+times);refresh_fixture(f)
            with self.assertRaisesRegex(BioError,'INVALID_TRAJECTORY_TIMES'):self.plan(f)

    def test_gap_is_preserved(self):
        f=self.fixture(3);(f['output']/'trajectory.times.csv').write_text('stageStep,timePs\n10,1\n20,3\n100,20\n');refresh_fixture(f)
        s,p=self.plan(f);self.assertEqual(s['gaps'],[2]);self.assertEqual(p['frames'][-1]['timePs'],20)

    def test_missing_ca_and_water_are_explicit(self):
        f=self.fixture(2,with_protein=False);source=resolve_source(f['controller'],f['ref'])
        self.assertEqual(source['overviewReason'],'WATER_BOX_ONLY')
        with self.assertRaisesRegex(BioError,'WATER_BOX_ONLY'):self.plan(f)
        f=self.fixture(2);path=f['output']/'topology.pdb';path.write_text(path.read_text().replace(' CA ',' CB '));refresh_fixture(f)
        with self.assertRaisesRegex(BioError,'PROTEIN_CA_MISSING'):self.plan(f)

    def test_changed_source_cannot_reuse_preview(self):
        f=self.fixture(2);(f['output']/'trajectory.times.csv').write_text('changed')
        with self.assertRaises(BioError):resolve_source(f['controller'],f['ref'])
        f=self.fixture(2);f['value']['revision']=2
        with self.assertRaisesRegex(BioError,'PREVIEW_SOURCE_CHANGED'):resolve_source(f['controller'],f['ref'])
