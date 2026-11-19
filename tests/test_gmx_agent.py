# -*- coding: utf-8 -*-
"""gmx_agent 启动闸回归门:代码强制的唯一 GROMACS 启动入口。
根外拒启/缺 tpr 拒启/重复启动拒启/adopt 先于 spawn/契约与 py3.6 语法。"""
import ast
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="gmx-agent-t")
os.environ.setdefault("MDSIM_DATA_ROOT", TMP)
sys.path.insert(0, os.path.join(HERE, "..", "dashboard"))

import gmx_agent  # noqa: E402
import gmx_scan  # noqa: E402

import atexit  # noqa: E402
atexit.register(lambda: shutil.rmtree(TMP, ignore_errors=True))


class TestGmxAgentLaunch(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="gmx-a-root-", dir=TMP)
        self.run_dir = os.path.join(self.root, "gel", "run1")
        os.makedirs(self.run_dir)
        self.tpr = os.path.join(self.run_dir, "prod.tpr")
        with open(self.tpr, "w") as f:
            f.write("fake tpr")
        self.fake_gmx = os.path.join(self.run_dir, "gmx")
        with open(self.fake_gmx, "w") as f:
            f.write("#!/bin/sh\n")
        self.cfg = os.path.join(TMP, "roots-a.json")
        self.adopted = os.path.join(TMP, "adopted-a.json")
        with open(self.cfg, "w") as f:
            json.dump({"roots": [{"path": self.root, "type": "gromacs",
                                  "enabled": True}]}, f)
        with open(self.adopted, "w") as f:
            json.dump({"adopted": []}, f)
        self._env = mock.patch.dict(os.environ, {
            "MONITOR_ROOTS_FILE": self.cfg,
            "GMX_ADOPTED_FILE": self.adopted})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        shutil.rmtree(self.root, ignore_errors=True)

    def _launch(self, **kw):
        inp = {"action": "launch", "dir": self.run_dir}
        inp.update(kw)
        with mock.patch.object(gmx_agent.shutil, "which", return_value=self.fake_gmx), \
             mock.patch.object(gmx_agent.subprocess, "Popen") as mp, \
             mock.patch.object(gmx_scan, "get_gmx_pids", return_value=[]):
            mp.return_value.pid = 9999
            return gmx_agent.action_launch(inp), mp

    def test_happy_path_adopt_before_spawn(self):
        def check_adopted_first(*a, **k):
            # spawn 发生的那一刻,dir 必须已在注册表里(无先跑后登记窗口)
            with open(self.adopted) as f:
                reg = json.load(f)
            assert any(os.path.realpath(p) == os.path.realpath(self.run_dir)
                       for p in reg["adopted"]), "spawn 时尚未接管"
            m = mock.Mock()
            m.pid = 9999
            return m
        inp = {"action": "launch", "dir": self.run_dir}
        with mock.patch.object(gmx_agent.shutil, "which", return_value=self.fake_gmx), \
             mock.patch.object(gmx_agent.subprocess, "Popen",
                               side_effect=check_adopted_first) as mp, \
             mock.patch.object(gmx_scan, "get_gmx_pids", return_value=[]):
            r = gmx_agent.action_launch(inp)
        self.assertTrue(r["success"], r)
        self.assertEqual(r["pid"], 9999)
        self.assertTrue(r["adopted"])
        cmd = mp.call_args[0][0]
        self.assertEqual(cmd[:2], [self.fake_gmx, "mdrun"])
        self.assertIn("-deffnm", cmd)

    def test_refuse_outside_roots(self):
        outside = tempfile.mkdtemp(prefix="gmx-outside-", dir=TMP)
        with open(os.path.join(outside, "prod.tpr"), "w") as f:
            f.write("x")
        with mock.patch.object(gmx_agent.shutil, "which", return_value=self.fake_gmx), \
             mock.patch.object(gmx_agent.subprocess, "Popen") as mp, \
             mock.patch.object(gmx_scan, "get_gmx_pids", return_value=[]):
            r = gmx_agent.action_launch({"action": "launch", "dir": outside})
        self.assertFalse(r["success"])
        self.assertIn("monitor root", r["error"])
        mp.assert_not_called()
        shutil.rmtree(outside, ignore_errors=True)

    def test_refuse_when_already_running(self):
        with mock.patch.object(gmx_agent.shutil, "which", return_value=self.fake_gmx), \
             mock.patch.object(gmx_agent.subprocess, "Popen") as mp, \
             mock.patch.object(gmx_scan, "get_gmx_pids", return_value=[1234]):
            r = gmx_agent.action_launch({"action": "launch", "dir": self.run_dir})
        self.assertFalse(r["success"])
        self.assertIn("在跑", r["error"])
        mp.assert_not_called()

    def test_refuse_missing_tpr(self):
        os.remove(self.tpr)
        r, mp = self._launch()
        self.assertFalse(r["success"])
        self.assertIn("tpr", r["error"])
        mp.assert_not_called()

    def test_main_contract_exit_codes(self):
        inp = os.path.join(TMP, "input.json")
        out = os.path.join(TMP, "result.json")
        with open(inp, "w") as f:
            json.dump({"action": "launch", "dir": "/nonexistent/x"}, f)
        rc = gmx_agent.main(["gmx_agent.py", inp, out])
        self.assertEqual(rc, 1)
        with open(out) as f:
            r = json.load(f)
        self.assertFalse(r["success"])
        # 未知 action
        with open(inp, "w") as f:
            json.dump({"action": "boom"}, f)
        rc = gmx_agent.main(["gmx_agent.py", inp, out])
        self.assertEqual(rc, 1)

    def test_py36_syntax(self):
        # sim_exec 的 python3 = 系统 3.6.8:语法必须 py3.6 可解析
        src = io.open(gmx_agent.__file__, encoding="utf-8").read()
        ast.parse(src, feature_version=(3, 6))

    def test_gmx_bin_param_and_missing_gmx(self):
        # PATH 无 gmx → 拒;gmx_bin 显式指定 → 过
        with mock.patch.object(gmx_agent.shutil, "which", return_value=None), \
             mock.patch.object(gmx_agent.subprocess, "Popen") as mp, \
             mock.patch.object(gmx_scan, "get_gmx_pids", return_value=[]):
            r2 = gmx_agent.action_launch({"action": "launch", "dir": self.run_dir})
            self.assertFalse(r2["success"])
            self.assertIn("gmx_bin", r2["error"])
            mp.assert_not_called()
            r3 = gmx_agent.action_launch({"action": "launch", "dir": self.run_dir,
                                          "gmx_bin": self.fake_gmx})
            self.assertTrue(r3["success"], r3)
            self.assertEqual(mp.call_args[0][0][0], self.fake_gmx)


class TestAdoptHelper(unittest.TestCase):
    def test_adopt_atomic_and_idempotent(self):
        tmp = tempfile.mkdtemp(prefix="gmx-adopt-t", dir=TMP)
        cfg = os.path.join(tmp, "adopted.json")
        with mock.patch.dict(os.environ, {"GMX_ADOPTED_FILE": cfg}):
            s1 = gmx_scan.adopt("/some/dir")
            s2 = gmx_scan.adopt("/some/dir")  # 幂等
            self.assertEqual(len(s2), len(s1))
            with open(cfg) as f:
                reg = json.load(f)
            self.assertIn(os.path.realpath("/some/dir"),
                          [os.path.realpath(p) for p in reg["adopted"]])
            self.assertEqual(os.listdir(tmp), ["adopted.json"],
                             "adopt 留下 tmp 残件")
            gmx_scan.adopt("/other", auto_kill_wild=True)
            with open(cfg) as f:
                self.assertTrue(json.load(f)["auto_kill_wild"])
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
