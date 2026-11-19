"""Visualization helper leases must not masquerade as scientific workflow claims."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bioagent.controller import Controller
from bioagent.execution_target import local_target
from bioagent.resource_pool import ResourcePool
from tests.support import PYTHON, fake_bindings, make_project, protein_protocol


class PreviewQueueRevisionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="bio-preview-revision-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        project = self.root / "projects/session"
        make_project(project)
        target = local_target(project, PYTHON)
        target["resourcePolicy"].update(quotaRoot=str(self.root / "quota"), cpuThreads=1,
                                       maxConcurrentJobs=1)
        self.controller = Controller(project, PYTHON, target)
        self.other = ResourcePool(target, self.root / "other-project")
        self.protocol = protein_protocol()

    def assert_plan_keeps_preview_pool(self):
        path = self.root / "quota/pool.json"
        before = path.read_bytes()
        with patch.object(self.controller, "_preflight", return_value=fake_bindings()):
            self.assertEqual(self.controller.plan(self.protocol)["state"], "DRAFT")
        self.assertEqual(path.read_bytes(), before)

    def test_active_preview_does_not_block_a_new_workflow_plan(self):
        self.assertIsNotNone(self.controller.resources.reserve_preview("preview-active", "alice"))
        self.assert_plan_keeps_preview_pool()

    def test_waiting_preview_does_not_block_a_new_workflow_plan(self):
        self.assertIsNotNone(self.other.reserve("occupier", 1, self.protocol["stages"][0]))
        self.assertIsNone(self.controller.resources.reserve_preview("preview-waiting", "alice"))
        self.assert_plan_keeps_preview_pool()


if __name__ == "__main__":
    unittest.main()
