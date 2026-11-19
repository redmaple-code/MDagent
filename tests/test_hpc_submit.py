# -*- coding: utf-8 -*-
"""hpc_submit 预留接口回归门:slurm/pbs 渲染确定性、入参校验、dry_run/DISABLED 门禁。

运行: python3.11 -m unittest discover -s tests -v (在 dsh-agent/ 下)
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MCP_DIR = os.path.join(os.path.dirname(HERE), "mcp")
if MCP_DIR not in sys.path:
    sys.path.insert(0, MCP_DIR)

import hpc_submit  # noqa: E402


class TestRender(unittest.TestCase):
    def test_slurm_script(self):
        s = hpc_submit.build_script("slurm", "/scratch/case1",
                                    "mpirun -np 32 lmp_mpi -in production.in",
                                    cores=32, nodes=1, walltime="24:00:00",
                                    partition="cpu", account="proj01", job_name="mdsim_run1")
        self.assertIn("#SBATCH --ntasks=32", s)
        self.assertIn("#SBATCH --partition=cpu", s)
        self.assertIn("#SBATCH --account=proj01", s)
        self.assertIn("#SBATCH --output=job_%j.out", s, "作业号占位符必须是单个 %j")
        self.assertNotIn("%%j", s)
        self.assertIn("cd /scratch/case1", s)
        self.assertIn("mpirun -np 32 lmp_mpi -in production.in", s)
        self.assertEqual(hpc_submit.build_submit_cmd("slurm", "/scratch/case1/job_run1.sh"),
                         ["sbatch", "/scratch/case1/job_run1.sh"])

    def test_pbs_script(self):
        s = hpc_submit.build_script("pbs", "/work/case2", "mpirun -np 64 lmp_mpi -in in.prod",
                                    cores=64, nodes=2, walltime="2-12:00:00",
                                    partition="batch", job_name="mdsim_c2")
        self.assertIn("#PBS -l nodes=2:ppn=32", s)
        self.assertIn("#PBS -l walltime=2:12:00:00", s)
        self.assertIn("#PBS -q batch", s)
        self.assertNotIn("account", s.lower())
        self.assertEqual(hpc_submit.build_submit_cmd("pbs", "x.sh"), ["qsub", "x.sh"])
        s2 = hpc_submit.build_script("pbs", "/w", "cmd", 8, 1, "01:00:00",
                                     "", "acct01", "j")
        self.assertIn("#PBS -A acct01", s2)
        self.assertNotIn("#PBS -q ", s2)

    def test_render_deterministic(self):
        a = hpc_submit.build_script("slurm", "/d", "cmd", 8, 1, "01:00:00", "", "", "j")
        b = hpc_submit.build_script("slurm", "/d", "cmd", 8, 1, "01:00:00", "", "", "j")
        self.assertEqual(a, b)


class TestRun(unittest.TestCase):
    def test_dry_run_default_no_side_effect(self):
        r = hpc_submit.run({"workdir": "/tmp/x", "command": "mpirun -np 4 lmp_mpi -in in.x"})
        self.assertEqual(r["status"], "DRY_RUN")
        self.assertIn("sbatch", r["submit_cmd"] if r["scheduler"] == "slurm" else r["submit_cmd"])
        self.assertIn("script", r)

    def test_disabled_when_not_enabled(self):
        r = hpc_submit.run({"workdir": "/tmp/x", "command": "c", "dry_run": False},
                           cfg_hpc={"enabled": False})
        self.assertEqual(r["status"], "DISABLED")
        self.assertIn("script", r, "DISABLED 也要给预览脚本")

    def test_ready_when_enabled_but_never_submits(self):
        r = hpc_submit.run({"workdir": "/tmp/x", "command": "c", "dry_run": False},
                           cfg_hpc={"enabled": True})
        self.assertEqual(r["status"], "READY")
        self.assertIn("sim_exec", r["note"], "启用后也必须经写门提交")

    def test_validation(self):
        self.assertEqual(hpc_submit.run({})["status"], "HPC_ERROR")  # 缺 workdir/command
        self.assertEqual(hpc_submit.run(None)["status"], "HPC_ERROR")  # 异常入参不抛
        r = hpc_submit.run({"workdir": "/tmp", "command": "c", "cores": 0})
        self.assertEqual(r["status"], "HPC_ERROR")
        r = hpc_submit.run({"workdir": "/tmp", "command": "c", "walltime": "999:99:99"})
        self.assertEqual(r["status"], "HPC_ERROR")
        r = hpc_submit.run({"workdir": "/tmp", "command": "c", "walltime": "5-25:00:00"})
        self.assertEqual(r["status"], "HPC_ERROR", "d-hh 的 hh≥24 必须拒")
        r = hpc_submit.run({"workdir": "/tmp", "command": "c", "scheduler": "torque"})
        self.assertEqual(r["status"], "HPC_ERROR")
        r = hpc_submit.run({"workdir": "/tmp", "command": "c", "job_name": "bad name!"})
        self.assertEqual(r["status"], "HPC_ERROR")

    def test_cores_nodes_divisibility(self):
        r = hpc_submit.run({"workdir": "/tmp", "command": "c", "cores": 33, "nodes": 2})
        self.assertEqual(r["status"], "HPC_ERROR", "不整除会静默截断 ppn,必须拒")
        r = hpc_submit.run({"workdir": "/tmp", "command": "c", "cores": 4, "nodes": 8})
        self.assertEqual(r["status"], "HPC_ERROR", "nodes>cores 会渲染 ppn=0,必须拒")

    def test_injection_rejected(self):
        r = hpc_submit.run({"workdir": "/tmp\n#SBATCH --exclusive", "command": "c"})
        self.assertEqual(r["status"], "HPC_ERROR", "workdir 换行注入伪指令必须拒")
        r = hpc_submit.run({"workdir": "/tmp", "command": "c", "partition": "cpu\n--exclusive"})
        self.assertEqual(r["status"], "HPC_ERROR")
        r = hpc_submit.run({"workdir": "/tmp;x", "command": "c"})
        self.assertEqual(r["status"], "HPC_ERROR")

    def test_case_id_names_job_and_script(self):
        r = hpc_submit.run({"workdir": "/tmp/x", "command": "c", "case_id": "run1"})
        self.assertEqual(r["job_name"], "mdsim_run1")
        self.assertEqual(r["script_name"], "job_run1.sh")

    def test_mcp_handler_shape(self):
        sys.path.insert(0, MCP_DIR)
        import md_mcp
        r = md_mcp.sim_hpc_submit({"workdir": "/tmp/x", "command": "mpirun -np 4 lmp_mpi -in in.x"})
        self.assertEqual(r["status"], "DRY_RUN")
        self.assertIn("_discipline", r)
        self.assertIn("sim_hpc_submit", md_mcp.HANDLERS)
        names = {t["name"] for t in md_mcp.TOOLS}
        self.assertIn("sim_hpc_submit", names)


if __name__ == "__main__":
    unittest.main(verbosity=2)
