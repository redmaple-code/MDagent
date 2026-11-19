# -*- coding: utf-8 -*-
"""push/md_push_driver.py 对抗修复回归门:
--dry 纯检测不得在数据根建空 state/ 目录(三轮对抗 F4 衍生)。"""
import os
import sys
import shutil
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "mcp"))
sys.path.insert(0, os.path.join(HERE, "..", "push"))


class TestDryRunNoSideEffect(unittest.TestCase):
    def test_load_state_creates_nothing(self):
        tmp = tempfile.mkdtemp(prefix="push-dry-t")
        self.addCleanup(shutil.rmtree, tmp, True)
        with mock.patch.dict(os.environ, {"MDSIM_DATA_ROOT": tmp}):
            import md_push_driver as drv
            st = drv.load_state()
        self.assertEqual(st, {"cases": {}, "phase": None})
        # 只读路径:数据根下不得出现任何目录/文件
        self.assertEqual(os.listdir(tmp), [],
                         "load_state 在数据根留下了痕迹: %r" % os.listdir(tmp))

    def test_state_file_make_flag(self):
        tmp = tempfile.mkdtemp(prefix="push-state-t")
        self.addCleanup(shutil.rmtree, tmp, True)
        with mock.patch.dict(os.environ, {"MDSIM_DATA_ROOT": tmp}):
            import md_push_driver as drv
            drv._state_file(make=False)
            self.assertFalse(os.path.exists(os.path.join(tmp, "state")))
            p = drv._state_file(make=True)
            self.assertTrue(os.path.isdir(os.path.join(tmp, "state")))
            self.assertTrue(p.endswith(os.path.join("state", "push_state.json")))

    def test_save_state_atomic_no_tmp_residue(self):
        # save_state 必须 tmp+os.replace:直写撕裂后 load_state 兜底空基线,
        # 下轮重复推送旧事件(五轮对抗)
        tmp = tempfile.mkdtemp(prefix="push-atomic-t")
        self.addCleanup(shutil.rmtree, tmp, True)
        with mock.patch.dict(os.environ, {"MDSIM_DATA_ROOT": tmp}):
            import md_push_driver as drv
            drv.save_state({"cases": {"c1": {"status": "RUNNING"}}, "phase": "MONITOR"})
            self.assertEqual(drv.load_state()["phase"], "MONITOR")
            state_dir = os.path.join(tmp, "state")
            self.assertEqual(sorted(os.listdir(state_dir)), ["push_state.json"],
                             "state/ 下有 tmp 残件: %r" % os.listdir(state_dir))


class TestGatesReadFailureKeepsBaseline(unittest.TestCase):
    """24 轮审计 D1(高):闸文件读失败(损坏/瞬断)不得清空 known_gates 基线,
    否则下次好读把全部 pending 闸当"新"再爆一轮推送。"""

    def test_corrupt_gates_carries_over_known_gates(self):
        data_root = tempfile.mkdtemp(prefix="push-data-t")
        kit_base = tempfile.mkdtemp(prefix="push-kit-t")
        self.addCleanup(shutil.rmtree, data_root, True)
        self.addCleanup(shutil.rmtree, kit_base, True)
        env = {"MDSIM_DATA_ROOT": data_root, "MDSIM_KIT_BASE": kit_base}
        with mock.patch.dict(os.environ, env):
            import md_push_driver as drv
            # 基线:上轮已知 ug-1/ug-2
            drv.save_state({"cases": {}, "phase": "MONITOR",
                            "known_gates": ["ug-1", "ug-2"]})
            # 本轮闸文件损坏
            with open(os.path.join(kit_base, ".v3.3_user_gates.json"), "w") as f:
                f.write("{corrupt")
            events, cur = drv.detect()
        self.assertEqual(cur["known_gates"], ["ug-1", "ug-2"],
                         "读失败清空基线→下轮假爆发(24 轮 D1 回归)")
        self.assertFalse(any("🚪" in e[0] for e in events),
                         "读失败轮不得产闸事件: %r" % events)


if __name__ == "__main__":
    unittest.main()


class TestGmxDetect(unittest.TestCase):
    """GROMACS 并入推送检测:跑完(常规)/中途停止(严重)/温度爆炸(严重);
    扫描失败保留基线不清空(同闸 D1 纪律)。"""

    def _run(self, prev_gmx, cur_runs=None, scan_exc=None):
        data_root = tempfile.mkdtemp(prefix="push-gmx-d-")
        kit_base = tempfile.mkdtemp(prefix="push-gmx-k-")
        self.addCleanup(shutil.rmtree, data_root, True)
        self.addCleanup(shutil.rmtree, kit_base, True)
        env = {"MDSIM_DATA_ROOT": data_root, "MDSIM_KIT_BASE": kit_base}
        with mock.patch.dict(os.environ, env):
            import md_push_driver as drv
            drv.save_state({"cases": {}, "phase": "MONITOR", "gmx": prev_gmx})
            target = drv.M.gmx_scan.scan_gromacs_runs
            if scan_exc is not None:
                cm = mock.patch.object(drv.M.gmx_scan, "scan_gromacs_runs",
                                       side_effect=scan_exc)
            else:
                cm = mock.patch.object(drv.M.gmx_scan, "scan_gromacs_runs",
                                       return_value=cur_runs or [])
            # 单测永不开 auto_kill(防摸到真实 gmx_adopted.json 杀真进程)
            with cm, mock.patch.object(drv.M.gmx_scan, "wild_auto_kill",
                                       return_value=False):
                events, cur = drv.detect()
        return events, cur

    @staticmethod
    def _run_row(rid, alive, pct, temp=300.0, ns=50.0):
        return {"run_id": rid, "alive": alive, "pct": pct, "temp": temp,
                "time_ns": ns, "stage": "prod", "step": 1, "total_steps": 1,
                "adopted": True}

    def test_completed_is_routine(self):
        events, cur = self._run(
            {"gel/run1": {"alive": True, "pct": 98.0, "temp": 300.0}},
            [self._run_row("gel/run1", False, 100.0)])
        hit = [e for e in events if "GROMACS" in e[0]]
        self.assertEqual(len(hit), 1)
        self.assertIn("跑完", hit[0][0])
        self.assertFalse(hit[0][1], "跑完是常规事件,不推个人")
        self.assertFalse(cur["gmx"]["gel/run1"]["alive"])

    def test_mid_stop_is_serious(self):
        events, cur = self._run(
            {"gel/run1": {"alive": True, "pct": 60.0, "temp": 300.0}},
            [self._run_row("gel/run1", False, 60.0, ns=30.0)])
        hit = [e for e in events if "GROMACS" in e[0]]
        self.assertEqual(len(hit), 1)
        self.assertIn("中途停止", hit[0][0])
        self.assertTrue(hit[0][1], "进程死是严重事件")

    def test_temp_explosion_is_serious(self):
        events, cur = self._run(
            {"gel/run1": {"alive": True, "pct": 10.0, "temp": 300.0}},
            [self._run_row("gel/run1", True, 11.0, temp=60000.0)])
        hit = [e for e in events if "GROMACS" in e[0]]
        self.assertEqual(len(hit), 1)
        self.assertIn("温度爆炸", hit[0][0])
        self.assertTrue(hit[0][1])

    def test_dead_run_hot_log_no_explosion_event(self):
        # 死 run 的日志尾高温是历史残骸,不是新事件(首轮基线不得轰炸)
        events, cur = self._run(
            {}, [self._run_row("paper04/prod3_pos", False, 100.0, temp=19940.0)])
        self.assertFalse([e for e in events if "温度爆炸" in e[0]])

    def test_steady_state_no_event(self):
        prev = {"gel/run1": {"alive": True, "pct": 50.0, "temp": 300.0}}
        events, cur = self._run(prev, [self._run_row("gel/run1", True, 51.0)])
        self.assertFalse([e for e in events if "GROMACS" in e[0]])

    def test_scan_failure_keeps_baseline(self):
        prev = {"gel/run1": {"alive": True, "pct": 50.0, "temp": 300.0}}
        events, cur = self._run(prev, scan_exc=RuntimeError("boom"))
        self.assertEqual(cur["gmx"], prev, "扫描失败清空基线→下轮假爆发")
        self.assertFalse([e for e in events if "GROMACS" in e[0]])

    def test_wild_run_serious_once(self):
        # 野跑(alive 且 adopted=False):第一次发现 → 严重;已接管 → 不报
        wild = dict(self._run_row("gel/wild", True, 10.0), adopted=False)
        events, cur = self._run({}, [wild])
        hit = [e for e in events if "野跑" in e[0]]
        self.assertEqual(len(hit), 1)
        self.assertTrue(hit[0][1], "野跑是严重事件")
        # 用 cur 作为新基线再跑一轮:同一只野跑不重复报
        data_root = tempfile.mkdtemp(prefix="push-gmx-d2-")
        kit_base = tempfile.mkdtemp(prefix="push-gmx-k2-")
        self.addCleanup(shutil.rmtree, data_root, True)
        self.addCleanup(shutil.rmtree, kit_base, True)
        with mock.patch.dict(os.environ, {"MDSIM_DATA_ROOT": data_root,
                                          "MDSIM_KIT_BASE": kit_base}):
            import md_push_driver as drv
            drv.save_state(cur)
            with mock.patch.object(drv.M.gmx_scan, "scan_gromacs_runs",
                                   return_value=[wild]), \
                 mock.patch.object(drv.M.gmx_scan, "wild_auto_kill",
                                   return_value=False):
                events2, _ = drv.detect()
        self.assertFalse([e for e in events2 if "野跑" in e[0]],
                         "同一只野跑不得重复告警")

    def test_adopted_run_no_wild_event(self):
        adopted = dict(self._run_row("gel/ok", True, 10.0), adopted=True)
        events, cur = self._run({}, [adopted])
        self.assertFalse([e for e in events if "野跑" in e[0]])

    def _run_kill(self, auto_kill, pids):
        """野跑 + auto_kill 开关:命中则 SIGTERM(温和停,gromacs 写终态检查点)。"""
        data_root = tempfile.mkdtemp(prefix="push-gmx-k3-")
        kit_base = tempfile.mkdtemp(prefix="push-gmx-k4-")
        self.addCleanup(shutil.rmtree, data_root, True)
        self.addCleanup(shutil.rmtree, kit_base, True)
        wild = dict(self._run_row("gel/wild", True, 10.0), adopted=False,
                    dir="/nonexistent/gel/wild")
        env = {"MDSIM_DATA_ROOT": data_root, "MDSIM_KIT_BASE": kit_base}
        with mock.patch.dict(os.environ, env):
            import md_push_driver as drv
            drv.save_state({"cases": {}, "phase": "MONITOR", "gmx": {}})
            with mock.patch.object(drv.M.gmx_scan, "scan_gromacs_runs",
                                   return_value=[wild]), \
                 mock.patch.object(drv.M.gmx_scan, "wild_auto_kill",
                                   return_value=auto_kill), \
                 mock.patch.object(drv.M.gmx_scan, "get_gmx_pids",
                                   return_value=pids), \
                 mock.patch("os.kill") as mk:
                events, cur = drv.detect()
        return events, mk

    def test_wild_auto_kill_sigterm(self):
        import signal
        events, mk = self._run_kill(True, [4321, 4322])
        self.assertEqual(mk.call_count, 2)
        for c in mk.call_args_list:
            self.assertEqual(c[0][1], signal.SIGTERM, "必须 SIGTERM 温和停(可续跑)")
        hit = [e for e in events if "已按规停掉" in e[0]]
        self.assertEqual(len(hit), 1)
        self.assertTrue(hit[0][1])

    def test_wild_no_flag_no_kill(self):
        events, mk = self._run_kill(False, [4321])
        mk.assert_not_called()
        hit = [e for e in events if "野跑" in e[0]]
        self.assertEqual(len(hit), 1, "不开关时退回人工闸告警")

    def test_wild_kill_failure_still_alerts(self):
        data_root = tempfile.mkdtemp(prefix="push-gmx-k5-")
        kit_base = tempfile.mkdtemp(prefix="push-gmx-k6-")
        self.addCleanup(shutil.rmtree, data_root, True)
        self.addCleanup(shutil.rmtree, kit_base, True)
        wild = dict(self._run_row("gel/wild", True, 10.0), adopted=False,
                    dir="/nonexistent/gel/wild")
        with mock.patch.dict(os.environ, {"MDSIM_DATA_ROOT": data_root,
                                          "MDSIM_KIT_BASE": kit_base}):
            import md_push_driver as drv
            drv.save_state({"cases": {}, "phase": "MONITOR", "gmx": {}})
            with mock.patch.object(drv.M.gmx_scan, "scan_gromacs_runs",
                                   return_value=[wild]), \
                 mock.patch.object(drv.M.gmx_scan, "wild_auto_kill",
                                   return_value=True), \
                 mock.patch.object(drv.M.gmx_scan, "get_gmx_pids",
                                   return_value=[4321]), \
                 mock.patch("os.kill", side_effect=ProcessLookupError):
                events, cur = drv.detect()
        hit = [e for e in events if "野跑" in e[0] or "停掉" in e[0]]
        self.assertTrue(hit, "杀失败也必须留告警,不得静默")


if __name__ == "__main__":
    unittest.main()
