# -*- coding: utf-8 -*-
"""GROMACS 看板扫描回归门:monitor_roots.json 驱动的扫描根、日志解析、
/api/gromacs 端点。配置缺席/损坏必须 fail-open 成空列表,不得抛。"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="gmx-mon-t")
# discover 按字母序先导入本模块,web_monitor 的 AUTO_APPROVE_FLAG 模块级常量
# 会锁定本 TMP;test_workbench_api 的 POST 要写 <root>/dsh-home/auto-approve.json,
# 缺目录会让 Handler 崩溃(RemoteDisconnected),故必须预建。
os.environ["MDSIM_DATA_ROOT"] = TMP
os.makedirs(os.path.join(TMP, "dsh-home"), exist_ok=True)
sys.path.insert(0, os.path.join(HERE, "..", "dashboard"))

import gmx_scan  # noqa: E402
import web_monitor  # noqa: E402

import atexit  # noqa: E402
atexit.register(lambda: shutil.rmtree(TMP, ignore_errors=True))

GMX_LOG_TAIL = """\
Some earlier output
   Step           Time         Lambda
      24281000    48562.00000      0.00000

   Energies (kJ/mol)
           Bond          Angle   Proper Dih.
    1.00000e+03 2.00000e+03 3.00000e+03
   Step           Time         Lambda
      24282000    48564.00000      0.00000

   Energies (kJ/mol)
    Kinetic En.   Total Energy  Conserved En.    Temperature Pressure (bar)
    1.23456e+05  -5.67890e+05  -5.00000e+05   3.00000e+02   1.00000e+00
"""

STDOUT_HEAD = """\
starting mdrun 'pnipam water gel'
25000000 steps,  50000.0 ps.
"""

MDP = "integrator = md\nnsteps = 25000000\ndt = 0.002\n"


class TestGmxParse(unittest.TestCase):
    def setUp(self):
        self.run_dir = tempfile.mkdtemp(prefix="gmx-run-", dir=TMP)

    def tearDown(self):
        shutil.rmtree(self.run_dir, ignore_errors=True)

    def test_parse_last_step_and_energy(self):
        p = os.path.join(self.run_dir, "prod.log")
        with open(p, "w", encoding="utf-8") as f:
            f.write(GMX_LOG_TAIL)
        info = web_monitor.parse_gmx_log(p)
        self.assertEqual(info["step"], 24282000)
        self.assertAlmostEqual(info["time_ps"], 48564.0)
        self.assertAlmostEqual(info["temp"], 300.0)
        self.assertAlmostEqual(info["etotal"], -567890.0)

    def test_parse_missing_file_returns_empty(self):
        self.assertEqual(web_monitor.parse_gmx_log(
            os.path.join(self.run_dir, "nope.log")), {})

    # 2018.1 换行排版真字节(paper-07 s8prod.log,2026-09-10 取):
    # 能量表 5 列一组换行,Temperature 与 Kinetic En. 不在同一行组
    WRAPPED_LOG_TAIL = """\
   Energies (kJ/mol)
           Bond          Angle Ryckaert-Bell.        LJ (SR)   Coulomb (SR)
    1.31863e+04    7.02409e+03    5.28498e+02    4.07587e+02   -1.23244e+04
   Coul. recip. Position Rest.      Potential    Kinetic En.   Total Energy
    7.65638e+03    4.32109e+04    5.96892e+04    1.73576e+03    6.14250e+04
  Conserved En.    Temperature Pressure (bar)   Constr. rmsd
    2.56366e+05    2.84614e+02   -2.54853e+00    3.11993e-06

           Step           Time
          54000      108.00000
"""

    def test_parse_wrapped_energy_table_gmx2018(self):
        p = os.path.join(self.run_dir, "s8prod.log")
        with open(p, "w", encoding="utf-8") as f:
            f.write(self.WRAPPED_LOG_TAIL)
        info = web_monitor.parse_gmx_log(p)
        self.assertEqual(info["step"], 54000)
        self.assertAlmostEqual(info["temp"], 284.614)
        self.assertAlmostEqual(info["etotal"], 61425.0)
        self.assertAlmostEqual(info["ke"], 1735.76)
        self.assertAlmostEqual(info["press"], -2.54853)

    def test_total_steps_stdout_preferred(self):
        with open(os.path.join(self.run_dir, "prod_stdout.log"), "w") as f:
            f.write(STDOUT_HEAD)
        with open(os.path.join(self.run_dir, "prod.mdp"), "w") as f:
            f.write("nsteps = 999\n")
        steps, ps = web_monitor._gmx_total_steps(self.run_dir)
        self.assertEqual(steps, 25000000)
        self.assertAlmostEqual(ps, 50000.0)

    def test_total_steps_mdp_fallback(self):
        with open(os.path.join(self.run_dir, "prod.mdp"), "w") as f:
            f.write(MDP)
        steps, ps = web_monitor._gmx_total_steps(self.run_dir)
        self.assertEqual(steps, 25000000)
        self.assertIsNone(ps)


class TestGmxScan(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="gmx-root-", dir=TMP)
        self.cfg = os.path.join(TMP, "roots.json")
        self._old = os.environ.get("MONITOR_ROOTS_FILE")
        os.environ["MONITOR_ROOTS_FILE"] = self.cfg

    def tearDown(self):
        if self._old is None:
            os.environ.pop("MONITOR_ROOTS_FILE", None)
        else:
            os.environ["MONITOR_ROOTS_FILE"] = self._old
        shutil.rmtree(self.root, ignore_errors=True)

    def _write_cfg(self, roots):
        with open(self.cfg, "w", encoding="utf-8") as f:
            json.dump({"roots": roots}, f)

    def _mk_run(self, rel, log=GMX_LOG_TAIL, stdout=STDOUT_HEAD):
        d = os.path.join(self.root, rel)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "prod.log"), "w") as f:
            f.write(log)
        if stdout is not None:
            with open(os.path.join(d, "prod_stdout.log"), "w") as f:
                f.write(stdout)
        return d

    def test_scan_finds_run_and_fields(self):
        self._mk_run("pnipam/run1")
        self._write_cfg([{"path": self.root, "type": "gromacs",
                          "label": "水凝胶", "enabled": True}])
        runs = web_monitor.scan_gromacs_runs()
        self.assertEqual(len(runs), 1)
        r = runs[0]
        self.assertEqual(r["run_id"], os.path.join("pnipam", "run1"))
        self.assertEqual(r["root_label"], "水凝胶")
        self.assertEqual(r["stage"], "prod")
        self.assertEqual(r["step"], 24282000)
        self.assertEqual(r["total_steps"], 25000000)
        self.assertAlmostEqual(r["pct"], 97.1, places=1)
        self.assertAlmostEqual(r["time_ns"], 48.56, places=2)
        self.assertAlmostEqual(r["temp"], 300.0)
        self.assertFalse(r["alive"])
        self.assertNotIn("_mtime", r)

    def test_missing_cfg_returns_empty(self):
        os.remove(self.cfg) if os.path.exists(self.cfg) else None
        self.assertEqual(web_monitor.scan_gromacs_runs(), [])

    def test_corrupt_cfg_returns_empty(self):
        with open(self.cfg, "w") as f:
            f.write("{not json")
        self.assertEqual(web_monitor.scan_gromacs_runs(), [])

    def test_disabled_and_missing_root_skipped(self):
        self._mk_run("a/run")
        self._write_cfg([
            {"path": self.root, "type": "gromacs", "enabled": False},
            {"path": os.path.join(self.root, "nonexistent"), "enabled": True},
        ])
        self.assertEqual(web_monitor.scan_gromacs_runs(), [])

    def test_depth_limit_and_tilde(self):
        # 深度上限 3(2026-09-10 起:论文根 projects/paper-XX/gromacs_full/build/base
        # 要三级才够到):3 收,4 不收
        self._mk_run(os.path.join("g1", "r1", "too", "deep"))              # 深度 3
        self._mk_run(os.path.join("g1", "r1", "too", "deep", "deeper"))    # 深度 4
        self._write_cfg([{"path": self.root, "type": "gromacs", "enabled": True}])
        runs = web_monitor.scan_gromacs_runs()
        ids = [r["run_id"] for r in runs]
        self.assertIn(os.path.join("g1", "r1", "too", "deep"), ids)
        self.assertNotIn(os.path.join("g1", "r1", "too", "deep", "deeper"), ids)
        shutil.rmtree(os.path.join(self.root, "g1"))

    def test_stage_precedence(self):
        d = self._mk_run("b/run")
        with open(os.path.join(d, "min.log"), "w") as f:
            f.write("empty min\n")
        self._write_cfg([{"path": self.root, "type": "gromacs", "enabled": True}])
        runs = web_monitor.scan_gromacs_runs()
        self.assertEqual(runs[0]["stage"], "prod")  # prod.log 优先于 min.log


GRO_SAMPLE = """\
Gel in water
  15000
    1RES     C1    1   0.100   0.200   0.300
   10.00000   10.00000    8.00000
"""

TOP_MAIN = """\
#include "mol.itp"
[ system ]
; comment
Gel

[ molecules ]
mol 1
"""

ITP = """\
[ moleculetype ]
mol 3

[ bonds ]
1 2 1 0.1 1000 ; comment
2 3 1 0.1 1000

[ angles ]
1 2 3 1 109.5 300

[ dihedrals ]
1 2 3 4 1 180.0 50 1
"""


class TestGmxModeling(unittest.TestCase):
    """建模段上看板:建模产物目录上榜、流水线徽章、模型统计与 spec 对照、
    执法只看 mdrun(packmol 建模进程不触发野跑闸)。"""
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="gmx-m-root-", dir=TMP)
        self.cfg = os.path.join(TMP, "roots-m.json")
        self._old = os.environ.get("MONITOR_ROOTS_FILE")
        os.environ["MONITOR_ROOTS_FILE"] = self.cfg
        with open(self.cfg, "w") as f:
            json.dump({"roots": [{"path": self.root, "type": "gromacs",
                                  "enabled": True}]}, f)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("MONITOR_ROOTS_FILE", None)
        else:
            os.environ["MONITOR_ROOTS_FILE"] = self._old
        shutil.rmtree(self.root, ignore_errors=True)

    def _mk(self, rel, files):
        d = os.path.join(self.root, rel)
        os.makedirs(d, exist_ok=True)
        for name, content in files.items():
            with open(os.path.join(d, name), "w") as f:
                f.write(content)
        return d

    def test_gro_and_top_stats(self):
        d = self._mk("g1/run1", {"conf.gro": GRO_SAMPLE, "topol.top": TOP_MAIN,
                                 "mol.itp": ITP})
        m = gmx_scan.model_stats(d, set(os.listdir(d)))
        self.assertEqual(m["natoms"], 15000)
        self.assertEqual(m["box_nm"], [10.0, 10.0, 8.0])
        self.assertEqual(m["bonds"], 2)
        self.assertEqual(m["angles"], 1)
        self.assertEqual(m["dihedrals"], 1)

    def test_spec_loaded_for_compare(self):
        d = self._mk("g1/run1", {"conf.gro": GRO_SAMPLE,
                                 "model_spec.json": json.dumps(
                                     {"natoms": 15000, "box_nm": [10, 10, 8]})})
        m = gmx_scan.model_stats(d, set(os.listdir(d)))
        self.assertEqual(m["spec"]["natoms"], 15000)

    def test_modeling_only_dir_listed_as_build(self):
        self._mk("g1/building", {"packed.xyz": "x\n", "mol.itp": ITP})
        runs = web_monitor.scan_gromacs_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["stage"], "build")
        self.assertTrue(runs[0]["pipeline"]["build"])
        self.assertFalse(runs[0]["pipeline"]["prod"])
        self.assertIsNone(runs[0]["step"])

    def test_nvt_log_recognized(self):
        self._mk("g1/eq", {"nvt.log": GMX_LOG_TAIL})
        runs = web_monitor.scan_gromacs_runs()
        self.assertEqual(runs[0]["stage"], "nvt")

    def test_mdrun_alive_split(self):
        # 建模进程在跑:alive=True(展示)但 mdrun_alive=False(不触发执法)
        self._mk("g1/building", {"packed.xyz": "x\n"})
        with mock.patch.object(
                gmx_scan, "get_gmx_pids", return_value=[]), \
             mock.patch.object(
                 gmx_scan, "get_gmx_build_pids", return_value=[777]):
            runs = web_monitor.scan_gromacs_runs()
        self.assertTrue(runs[0]["alive"])
        self.assertFalse(runs[0]["mdrun_alive"])
        self.assertEqual(runs[0]["pids_count"], 1)

    def test_mtime_tracks_model_change(self):
        d = self._mk("g1/run1", {"conf.gro": GRO_SAMPLE})
        first = web_monitor.scan_gromacs_runs()[0]["last_modified"]
        import time as _t
        _t.sleep(1.1)
        with open(os.path.join(d, "conf.gro"), "a") as f:
            f.write("\n")
        second = web_monitor.scan_gromacs_runs()[0]["last_modified"]
        self.assertNotEqual(first, second, "模型文件改动必须反映到 last_modified")


SUFFIX_LOG = """\
gmx mdrun header
   nsteps                         = 25000000
""" + GMX_LOG_TAIL


class TestGmxDetail(unittest.TestCase):
    """点开详情(用户:"不能只显示,绑定和 lammps 一样的功能",2026-09-10):
    gmx_run_detail 返回阶段/日志尾/文件清单/模型统计;越根与不存在目录拒。"""
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="gmx-dt-root-", dir=TMP)
        self.cfg = os.path.join(TMP, "roots-dt.json")
        self._old = os.environ.get("MONITOR_ROOTS_FILE")
        os.environ["MONITOR_ROOTS_FILE"] = self.cfg
        with open(self.cfg, "w") as f:
            json.dump({"roots": [{"path": self.root, "type": "gromacs",
                                  "enabled": True}]}, f)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("MONITOR_ROOTS_FILE", None)
        else:
            os.environ["MONITOR_ROOTS_FILE"] = self._old
        shutil.rmtree(self.root, ignore_errors=True)

    def _mk(self, rel, files):
        d = os.path.join(self.root, rel)
        os.makedirs(d, exist_ok=True)
        for name, content in files.items():
            with open(os.path.join(d, name), "w") as f:
                f.write(content)
        return d

    def test_detail_returns_tail_files_model(self):
        d = self._mk("g1/run", {"s8prod.log": SUFFIX_LOG, "conf.gro": GRO_SAMPLE,
                                "topol.top": TOP_MAIN, "mol.itp": ITP})
        det = gmx_scan.gmx_run_detail(d)
        self.assertEqual(det["stage"], "prod")
        self.assertEqual(det["step"], 24282000)
        self.assertEqual(det["total_steps"], 25000000)
        self.assertIn("24282000", det["log_tail"])
        self.assertEqual(det["log_name"], "s8prod.log")
        names = [f["name"] for f in det["files"]]
        self.assertIn("s8prod.log", names)
        self.assertIn("conf.gro", names)
        self.assertEqual(det["model"]["natoms"], 15000)
        self.assertIn("adopted", det)
        self.assertIn("pipeline", det)

    def test_detail_rejects_escape(self):
        self.assertIsNone(gmx_scan.gmx_run_detail("/etc"))
        self.assertIsNone(gmx_scan.gmx_run_detail(
            os.path.join(self.root, "..")))

    def test_detail_missing_dir(self):
        self.assertIsNone(gmx_scan.gmx_run_detail(os.path.join(self.root, "nope")))


class TestGmxSuffixNaming(unittest.TestCase):
    """自定义前缀阶段日志(2026-09-10 paper-07 实例 s8prod.log/vmin.log):
    后缀识别、总步数取日志自带 nsteps、精确名优先、#备份# 文件不误认。"""
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="gmx-sfx-root-", dir=TMP)
        self.cfg = os.path.join(TMP, "roots-sfx.json")
        self._old = os.environ.get("MONITOR_ROOTS_FILE")
        os.environ["MONITOR_ROOTS_FILE"] = self.cfg
        with open(self.cfg, "w") as f:
            json.dump({"roots": [{"path": self.root, "type": "gromacs",
                                  "enabled": True}]}, f)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("MONITOR_ROOTS_FILE", None)
        else:
            os.environ["MONITOR_ROOTS_FILE"] = self._old
        shutil.rmtree(self.root, ignore_errors=True)

    def _mk(self, rel, files):
        d = os.path.join(self.root, rel)
        os.makedirs(d, exist_ok=True)
        for name, content in files.items():
            with open(os.path.join(d, name), "w") as f:
                f.write(content)
        return d

    def test_suffix_stage_log_recognized(self):
        self._mk("g1/run", {"s8prod.log": SUFFIX_LOG})
        runs = web_monitor.scan_gromacs_runs()
        self.assertEqual(len(runs), 1)
        r = runs[0]
        self.assertEqual(r["stage"], "prod")
        self.assertEqual(r["step"], 24282000)
        self.assertEqual(r["total_steps"], 25000000)  # 日志自带 nsteps(无需 mdp/stdout)
        self.assertAlmostEqual(r["pct"], 97.1, places=1)

    def test_exact_name_preferred_over_suffix(self):
        self._mk("g1/run", {"prod.log": GMX_LOG_TAIL, "s8prod.log": "empty\n"})
        runs = web_monitor.scan_gromacs_runs()
        self.assertEqual(runs[0]["step"], 24282000)  # 解析的是精确名 prod.log

    def test_backup_hash_files_not_runs(self):
        self._mk("g1/run", {"#s8prod.log.1#": GMX_LOG_TAIL})
        self.assertEqual(web_monitor.scan_gromacs_runs(), [])

    def test_log_nsteps_fallback_for_exact_names(self):
        d = self._mk("g1/run", {"prod.log": SUFFIX_LOG})
        steps, ps = web_monitor._gmx_total_steps(d, os.path.join(d, "prod.log"))
        self.assertEqual(steps, 25000000)
        self.assertIsNone(ps)


class TestGmxAdopted(unittest.TestCase):
    """野跑闸(不走编排器不能跑,2026-09-10 用户拍板:检测+人工闸,不自动杀):
    接管注册表 gmx_adopted.json;alive 且未接管 = 野跑(adopted=False)。"""
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="gmx-ad-root-", dir=TMP)
        self.cfg = os.path.join(TMP, "roots-ad.json")
        self.adopt = os.path.join(TMP, "adopted.json")
        self._old_cfg = os.environ.get("MONITOR_ROOTS_FILE")
        self._old_ad = os.environ.get("GMX_ADOPTED_FILE")
        os.environ["MONITOR_ROOTS_FILE"] = self.cfg
        os.environ["GMX_ADOPTED_FILE"] = self.adopt
        with open(self.cfg, "w") as f:
            json.dump({"roots": [{"path": self.root, "type": "gromacs",
                                  "enabled": True}]}, f)

    def tearDown(self):
        for k, v in (("MONITOR_ROOTS_FILE", self._old_cfg),
                     ("GMX_ADOPTED_FILE", self._old_ad)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.root, ignore_errors=True)

    def _mk_run(self, rel):
        d = os.path.join(self.root, rel)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "prod.log"), "w") as f:
            f.write(GMX_LOG_TAIL)
        return d

    def _write_adopted(self, dirs):
        with open(self.adopt, "w") as f:
            json.dump({"adopted": dirs}, f)

    def test_missing_registry_all_wild(self):
        self._mk_run("g1/run1")
        runs = web_monitor.scan_gromacs_runs()
        self.assertEqual(runs[0]["adopted"], False)

    def test_adopted_dir_flagged_true(self):
        d = self._mk_run("g1/run1")
        self._write_adopted([d])
        runs = web_monitor.scan_gromacs_runs()
        self.assertEqual(runs[0]["adopted"], True)

    def test_corrupt_registry_fail_open_wild(self):
        self._mk_run("g1/run1")
        with open(self.adopt, "w") as f:
            f.write("{corrupt")
        runs = web_monitor.scan_gromacs_runs()
        self.assertEqual(runs[0]["adopted"], False,
                         "注册表损坏按未接管处理(宁多报不漏野跑)")


if __name__ == "__main__":
    unittest.main()
