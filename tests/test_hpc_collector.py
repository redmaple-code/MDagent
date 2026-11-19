# -*- coding: utf-8 -*-
"""hpc_collector 二轮对抗修复的回归门:
缓存命名两端一致(采集写/看板读同口径)、pbs/slurm 状态解析、数值型名字归一化。"""
import json
import os
import sys
import shutil
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "dashboard"))
sys.path.insert(0, os.path.join(HERE, "..", "mcp"))

import paths
import registry
import hpc_collector


class TestCacheBasename(unittest.TestCase):
    def test_deterministic_and_collision_safe(self):
        a = registry.hpc_cache_basename("lx", "12345")
        self.assertEqual(a, registry.hpc_cache_basename("lx", "12345"))
        # 清洗碰撞对:lstrip('.') 坍缩 / 下划线分隔歧义,hash 缀必须区分开
        self.assertNotEqual(registry.hpc_cache_basename(".foo", "1"),
                            registry.hpc_cache_basename("foo", "1"))
        self.assertNotEqual(registry.hpc_cache_basename("a_b", "c"),
                            registry.hpc_cache_basename("a", "b_c"))
        self.assertNotEqual(registry.hpc_cache_basename("c", ".."),
                            registry.hpc_cache_basename("c", "."))
        # 文件名成分不许以点开头(隐藏文件)不许含路径分隔符
        for name in (".foo", "..", "a/b"):
            base = registry.hpc_cache_basename(name, "1")
            self.assertFalse(base.startswith("."))
            self.assertNotIn("/", base)

    def test_collect_one_writes_where_board_reads(self):
        """采集端落盘文件名 == 看板端读取文件名(同一 registry.hpc_cache_basename 出处)。"""
        tmp = tempfile.mkdtemp(prefix="hpc-cache-t")
        self.addCleanup(shutil.rmtree, tmp, True)
        entry = {"case_id": "c1", "prod_dir": "/remote/c1", "log_file": "log.x",
                 "location": {"type": "hpc", "cluster": "lx", "jobid": "12345",
                              "scheduler": "slurm"}}
        with mock.patch.dict(os.environ, {"MDSIM_DATA_ROOT": tmp}):
            with mock.patch.object(hpc_collector, "_cluster_conn",
                                   return_value={"host": "dummy", "user": "u"}), \
                 mock.patch.object(hpc_collector, "_ssh_run",
                                   return_value=("RUNNING 10:00 node1\n", "", 0)):
                rec = hpc_collector.collect_one(entry)
        self.assertEqual(rec["state"], "running")
        base = os.path.join(tmp, "state", "hpc_cache",
                            registry.hpc_cache_basename("lx", "12345"))
        self.assertTrue(os.path.exists(base + ".json"))
        self.assertTrue(os.path.exists(base + ".log_tail.txt"))
        data = json.load(open(base + ".json", encoding="utf-8"))
        self.assertEqual(data["state"], "running")


class TestParseState(unittest.TestCase):
    def test_pbs_running_and_pending(self):
        out = ["Job id            Name             User      Time Use S Queue",
               "----------------  ---------------- --------- -------- - -----",
               "123.lxmgr         job              alice     00:12:34 R batch"]
        self.assertEqual(hpc_collector._parse_state("pbs", out), "running")
        out[2] = out[2].replace(" R ", " Q ")
        self.assertEqual(hpc_collector._parse_state("pbs", out), "pending")

    def test_slurm_squeue_and_sacct(self):
        self.assertEqual(hpc_collector._parse_state("slurm", ["RUNNING 5:00 gpu01"]),
                         "running")
        self.assertEqual(hpc_collector._parse_state("slurm", ["COMPLETED|00:05:12"]),
                         "completed")
        # sacct 只取首行(主作业行),.batch/.extern 步不参与判定
        self.assertEqual(hpc_collector._parse_state(
            "slurm", ["BOOT_FAIL+|00:00:01", "CANCELLED|00:00:01"]), "failed")

    def test_empty_is_unknown(self):
        self.assertEqual(hpc_collector._parse_state("slurm", []), "unknown")
        self.assertEqual(hpc_collector._parse_state("pbs", ["---- ----"]), "unknown")

    def test_pbs_finished_states(self):
        # qstat -x 历史态:F=finished X=expired → completed(三轮对抗补)
        for st in ("F", "X"):
            out = ["123.lxmgr         job              alice     00:12:34 %s batch" % st]
            self.assertEqual(hpc_collector._parse_state("pbs", out), "completed")

    def test_pbs_jobid_starting_with_job_not_killed(self):
        # 表头过滤必须精确匹配 "Job id ...";jobid 以 job 开头的数据行不得被误杀(三轮对抗)
        out = ["Job id            Name             User      Time Use S Queue",
               "----------------  ---------------- --------- -------- - -----",
               "job123.lxmgr      melt             alice     00:12:34 R batch"]
        self.assertEqual(hpc_collector._parse_state("pbs", out), "running")


class TestRegistryNameNormalization(unittest.TestCase):
    def test_leading_dash_rejected(self):
        # 首字符 '-' 会穿透 shlex.quote 变成 CLI 选项(qstat -a 毒化输出),必须拒(三轮对抗)
        for loc in ({"type": "hpc", "cluster": "-evil", "jobid": "1"},
                    {"type": "hpc", "cluster": "lx", "jobid": "-1"}):
            with self.assertRaises(ValueError):
                registry.upsert({"case_id": "hx", "prod_dir": "/r", "log_file": "l",
                                 "location": loc})
        for name in ("-a", "--help"):
            self.assertIsNone(registry.SAFE_NAME_RE.match(name))

    def test_upsert_does_not_mutate_caller_entry(self):
        # _validate 会把数值型 cluster/jobid 就地归一化为 str:必须先深拷贝,
        # 调用方的 dict 不得被污染(三轮对抗)
        entry = {"case_id": "hnum2", "prod_dir": "/remote/h2", "log_file": "log.x",
                 "location": {"type": "hpc", "cluster": 123, "jobid": 9}}
        snapshot = json.loads(json.dumps(entry))
        tmp = tempfile.mkdtemp(prefix="reg-deepcopy-t")
        self.addCleanup(shutil.rmtree, tmp, True)
        with mock.patch.dict(os.environ, {"MDSIM_DATA_ROOT": tmp}):
            registry.upsert(entry)
        self.assertEqual(entry, snapshot, "调用方 entry 被就地修改")

    def test_numeric_cluster_jobid_stored_as_str(self):
        tmp = tempfile.mkdtemp(prefix="reg-num-t")
        self.addCleanup(shutil.rmtree, tmp, True)
        with mock.patch.dict(os.environ, {"MDSIM_DATA_ROOT": tmp}):
            registry.upsert({"case_id": "hnum", "prod_dir": "/remote/h",
                             "log_file": "log.x",
                             "location": {"type": "hpc", "cluster": 123, "jobid": 9}})
            jobs = registry.load()["jobs"]
        loc = jobs[0]["location"]
        self.assertEqual(loc["cluster"], "123")
        self.assertEqual(loc["jobid"], "9")

    def test_case_id_html_metachars_rejected(self):
        # case_id 白名单(与 pipeline_check 同口径):<>'"& 经看板 innerHTML
        # 原样渲染即成存储型 XSS(五轮对抗)
        for bad in ("x'><img src=x onerror=alert(1)>", 'a"b', "a&b",
                    "a b", "../x", "a/b", "a\\b", ".", "..", ""):
            with self.assertRaises(ValueError, msg="必须拒: %r" % bad):
                registry.upsert({"case_id": bad, "prod_dir": "/r", "log_file": "l"})
        tmp = tempfile.mkdtemp(prefix="reg-cid-t")
        self.addCleanup(shutil.rmtree, tmp, True)
        with mock.patch.dict(os.environ, {"MDSIM_DATA_ROOT": tmp}):
            registry.upsert({"case_id": "ok_case-1.2", "prod_dir": "/r", "log_file": "l"})
            self.assertEqual(registry.load()["jobs"][0]["case_id"], "ok_case-1.2")

    def test_unreachable_clears_stale_tail(self):
        tmp = tempfile.mkdtemp(prefix="hpc-stale-t")
        self.addCleanup(shutil.rmtree, tmp, True)
        entry = {"case_id": "c2", "prod_dir": "/remote/c2", "log_file": "log.x",
                 "location": {"type": "hpc", "cluster": "lx", "jobid": "77",
                              "scheduler": "slurm"}}
        with mock.patch.dict(os.environ, {"MDSIM_DATA_ROOT": tmp}):
            base = os.path.join(paths.hpc_cache_dir(),
                                registry.hpc_cache_basename("lx", "77"))
            with open(base + ".log_tail.txt", "w") as f:
                f.write("stale")
            with mock.patch.object(hpc_collector, "_ssh_run",
                                   side_effect=OSError("ssh down")):
                rec = hpc_collector.collect_one(entry)
            self.assertEqual(rec["state"], "unreachable")
            self.assertFalse(os.path.exists(base + ".log_tail.txt"))
            self.assertTrue(os.path.exists(base + ".json"))


if __name__ == "__main__":
    unittest.main()
