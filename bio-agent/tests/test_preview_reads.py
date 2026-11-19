from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from bioagent.errors import atomic_json, BioError
from bioagent.visualization.sources import resolve_source
from bioagent.visualization.preview_plan import plan_preview
from bioagent.visualization.preview_files import write_preview
from bioagent.visualization.service import PreviewService
from tests.preview_fixture import make_preview_fixture


class PreviewReadsTest(unittest.TestCase):
    def test_published_reads_do_not_scan_original_trajectory_again(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);root=Path(tmp.name)
        f=make_preview_fixture(root,frames=3);source=resolve_source(f['controller'],f['ref'])
        request=dict(source=f['ref'],profile='overview',atomSet='overview',startFrame=0,endFrame=2,stride=1)
        plan=plan_preview(source,request);m=write_preview(source,plan,root/'previews/files'/plan['previewId'])
        service=PreviewService(f['controller']);job=service.jobs.path('job-read');job.mkdir()
        atomic_json(job/'request.json',dict(request=request));atomic_json(job/'status.json',dict(state='READY',previewId=m['previewId']))
        with patch('bioagent.visualization.service.resolve_source',side_effect=AssertionError('do not rescan original DCD')):
            first=service.read(m['previewId'],m['topologyFile'],0,64);second=service.read(m['previewId'],m['topologyFile'],0,64)
        self.assertEqual(first,second)
        (f['output']/'trajectory.times.csv').write_text('changed')
        with self.assertRaisesRegex(BioError,'PREVIEW_SOURCE_CHANGED'):service.read(m['previewId'],m['topologyFile'],0,64)
