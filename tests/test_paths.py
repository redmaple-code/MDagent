# -*- coding: utf-8 -*-
"""paths.py 三级解析与子路径布局的契约测试"""
import os, sys, json, tempfile, shutil, unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mcp"))
import paths


class TestDataRootPriority(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="paths-t")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_env_wins_over_config(self):
        env_root = os.path.join(self.tmp, "env-root")
        with mock.patch.dict(os.environ, {"MDSIM_DATA_ROOT": env_root}):
            self.assertEqual(paths.data_root(), env_root)

    def test_config_used_when_no_env(self):
        cfg_root = os.path.join(self.tmp, "cfg-root")
        real_cfg = paths._cfg
        paths._cfg = lambda: {"data_root": cfg_root}
        try:
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("MDSIM_DATA_ROOT", None)
                self.assertEqual(paths.data_root(), cfg_root)
        finally:
            paths._cfg = real_cfg

    def test_default_is_sibling_of_code_root(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MDSIM_DATA_ROOT", None)
            real_cfg = paths._cfg
            paths._cfg = lambda: {}
            try:
                self.assertEqual(paths.data_root(),
                                 os.path.join(os.path.dirname(paths.CODE_ROOT), "mdsim-data"))
            finally:
                paths._cfg = real_cfg


class TestSubdirs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="paths-t")
        self._env = mock.patch.dict(os.environ, {"MDSIM_DATA_ROOT": self.tmp})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_subdir_layout(self):
        self.assertEqual(paths.events_dir(), os.path.join(self.tmp, "events"))
        self.assertEqual(paths.state_dir(), os.path.join(self.tmp, "state"))
        self.assertEqual(paths.memory_dir(), os.path.join(self.tmp, "memory"))
        self.assertEqual(paths.pipeline_scratch_dir(), os.path.join(self.tmp, "scratch", "pipeline"))
        self.assertEqual(paths.dsh_home(), os.path.join(self.tmp, "dsh-home"))
        self.assertEqual(paths.registry_file(), os.path.join(self.tmp, "state", "registry.json"))
        self.assertEqual(paths.flowgate_log(), os.path.join(self.tmp, "events", "flowgate.jsonl"))

    def test_dirs_created_lazily(self):
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "events")))
        paths.events_dir()
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, "events")))

    def test_dynamic_env_read_no_cache(self):
        paths.data_root()
        other = os.path.join(self.tmp, "switched")
        with mock.patch.dict(os.environ, {"MDSIM_DATA_ROOT": other}):
            self.assertEqual(paths.data_root(), other)

    def test_tilde_expanded(self):
        with mock.patch.dict(os.environ, {"MDSIM_DATA_ROOT": "~/mdsim-x"}):
            self.assertEqual(paths.data_root(),
                             os.path.join(os.path.expanduser("~"), "mdsim-x"))

    def test_whitespace_env_treated_as_unset(self):
        cfg_root = os.path.join(self.tmp, "cfg-root")
        real_cfg = paths._cfg
        paths._cfg = lambda: {"data_root": cfg_root}
        try:
            with mock.patch.dict(os.environ, {"MDSIM_DATA_ROOT": "   "}):
                self.assertEqual(paths.data_root(), cfg_root)
        finally:
            paths._cfg = real_cfg

    def test_relative_env_anchored_not_cwd(self):
        with mock.patch.dict(os.environ, {"MDSIM_DATA_ROOT": "rel-root"}):
            self.assertEqual(paths.data_root(),
                             os.path.join(os.path.dirname(paths.CODE_ROOT), "rel-root"))

    def test_non_str_values_rejected_with_warning(self):
        # 配置/env 写成数字等非 str:静默透传会在 join 时 TypeError 连坐,必须拒绝并告警
        for bad in (123, {"x": 1}, ["a"]):
            self.assertIsNone(paths._pick(bad))
        self.assertIsNone(paths._pick(""))
        self.assertIsNone(paths._pick(None))
        cfg_root = os.path.join(self.tmp, "cfg-root")
        real_cfg = paths._cfg
        paths._cfg = lambda: {"data_root": 123}
        try:
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("MDSIM_DATA_ROOT", None)
                self.assertEqual(paths.data_root(),
                                 os.path.join(os.path.dirname(paths.CODE_ROOT), "mdsim-data"))
        finally:
            paths._cfg = real_cfg


class TestSchedule(unittest.TestCase):
    """温度程序唯一出处:schedules[project] > schedule;坏配置回退 None(调用方用内置)。"""
    def setUp(self):
        self.real_cfg = paths._cfg

    def tearDown(self):
        paths._cfg = self.real_cfg

    def test_default_and_project_override(self):
        paths._cfg = lambda: {"schedule": [[0, 100, 300, 300]],
                              "schedules": {"proj-a": [[0, 50, 600, 900]]}}
        self.assertEqual(paths.load_schedule(), [(0.0, 100.0, 300.0, 300.0)])
        self.assertEqual(paths.load_schedule("proj-a"), [(0.0, 50.0, 600.0, 900.0)])
        self.assertEqual(paths.load_schedule("ghost"), [(0.0, 100.0, 300.0, 300.0)])

    def test_broken_config_returns_none(self):
        for bad in ({"schedule": "junk"}, {"schedule": [[1, 2, 3]]},
                    {"schedule": []}, {}):
            paths._cfg = (lambda b: (lambda: b))(bad)
            self.assertIsNone(paths.load_schedule(), "坏配置必须回退 None: %r" % (bad,))

    def test_schedules_non_dict_no_crash(self):
        # schedules 写坏成 list/str/int 不得 AttributeError 打崩看板(四轮对抗)
        for bad in ([["x"]], "oops", 42):
            paths._cfg = (lambda b: (lambda: {"schedule": [[0, 1, 300, 300]],
                                              "schedules": b}))(bad)
            self.assertEqual(paths.load_schedule("p"), [(0.0, 1.0, 300.0, 300.0)])

    def test_segment_type_and_finiteness(self):
        # 字符串段会被逐字符 float 化成合法假象;NaN/inf 段让温度比较全 False(四轮对抗)
        paths._cfg = lambda: {"schedule": ["1234"]}
        self.assertIsNone(paths.load_schedule())
        paths._cfg = lambda: {"schedule": [[0, 100, float("nan"), 300]]}
        self.assertIsNone(paths.load_schedule())
        paths._cfg = lambda: {"schedule": [[0, 100, float("inf"), 300]]}
        self.assertIsNone(paths.load_schedule())


if __name__ == "__main__":
    unittest.main()
