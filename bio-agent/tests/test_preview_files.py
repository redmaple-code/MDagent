from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import MDAnalysis as mda
import numpy as np
from bioagent.errors import BioError, sha256_file, atomic_json
from bioagent.visualization.sources import resolve_source
from bioagent.visualization.preview_plan import plan_preview
from bioagent.visualization.preview_files import write_preview
from tests.preview_fixture import make_preview_fixture


class PreviewFilesTest(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        self.f=make_preview_fixture(self.root,frames=203)
        self.source=resolve_source(self.f['controller'],self.f['ref'])
        self.plan=plan_preview(self.source,dict(source=self.f['ref'],profile='overview',atomSet='overview',startFrame=0,endFrame=202,stride=1))

    def test_xtc_roundtrip_error_and_times(self):
        before={p:sha256_file(p) for p in self.f['output'].iterdir()}
        dest=self.root/'previews'/self.plan['previewId'];manifest=write_preview(self.source,self.plan,dest)
        atoms=np.fromfile(dest/manifest['atomMapFile'],dtype='<u4');self.assertEqual(atoms.tolist(),[1,3,4])
        original=mda.Universe(str(self.source['topology']),[str(p) for p in self.source['trajectories']])
        max_error=0
        for segment in [f for f in manifest['files'] if f['kind']=='xtc']:
            decoded=mda.Universe(str(dest/manifest['topologyFile']),str(dest/segment['fileId']))
            for i,ts in enumerate(decoded.trajectory):
                frame=manifest['frames'][segment['previewFrameStart']+i];original.trajectory[frame['sourceFrameIndex']]
                max_error=max(max_error,float(np.linalg.norm(ts.positions-original.atoms.positions[atoms],axis=1).max()))
                self.assertEqual(frame['timePs'],self.source['frames'][frame['sourceFrameIndex']]['timePs'])
            decoded.trajectory.close()
        self.assertLessEqual(max_error,.01)
        self.assertEqual(before,{p:sha256_file(p) for p in before})
        self.assertEqual(manifest,write_preview(self.source,self.plan,dest))
        original.trajectory.close()

    def test_budget_refuses_without_deleting(self):
        root=self.root/'previews';root.mkdir();old=root/'old';old.write_bytes(b'keep')
        with self.assertRaisesRegex(BioError,'PREVIEW_DISK_BUDGET_EXCEEDED'):
            write_preview(self.source,self.plan,root/self.plan['previewId'],quota_bytes=4)
        self.assertEqual(old.read_bytes(),b'keep')

    def test_failed_attempt_files_are_preserved_and_a_new_attempt_can_publish(self):
        previews=self.root/'previews';dest=previews/'files'/self.plan['previewId'];first=previews/'jobs/job-first/output'
        with patch('MDAnalysis.Writer',side_effect=RuntimeError('temporary writer failure')):
            with self.assertRaisesRegex(RuntimeError,'temporary writer failure'):
                write_preview(self.source,self.plan,dest,staging_root=first,quota_root=previews)
        partial=first/self.plan['previewId'];before={p.name:sha256_file(p) for p in partial.iterdir()}
        self.assertFalse(dest.exists());self.assertTrue(before)
        manifest=write_preview(self.source,self.plan,dest,staging_root=previews/'jobs/job-retry/output',quota_root=previews)
        self.assertEqual(manifest['previewId'],self.plan['previewId']);self.assertTrue((dest/'manifest.json').is_file())
        self.assertEqual(before,{p.name:sha256_file(p) for p in partial.iterdir()})

    def test_independently_owned_jobs_reuse_a_concurrently_published_preview(self):
        root=self.root/'previews';dest=root/'files'/self.plan['previewId'];published=[]
        first=root/'jobs/job-one/output';second=root/'jobs/job-two/output'
        def interleave(path,value):
            atomic_json(path,value)
            if path==first/self.plan['previewId']/'manifest.json':
                published.append(write_preview(self.source,self.plan,dest,staging_root=second,quota_root=root))
        with patch('bioagent.visualization.preview_files.atomic_json',side_effect=interleave):
            result=write_preview(self.source,self.plan,dest,staging_root=first,quota_root=root)
        self.assertEqual(result,published[0]);self.assertTrue((dest/'manifest.json').is_file())
