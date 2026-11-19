# -*- coding: utf-8 -*-
"""_resolve_log_file 回归门:组分文件冒充热力学日志的场景不许复发。

回归源(2026-09-17 压测抓获):兜底曾按 mtime 最新在 (*.log, *.out, log.*) 里猜,
RUN2000 的 species.out 被当 thermo 解析,第二列当 Temp → 假 1482K CRITICAL。
修复后优先级: production.stdout / log.lammps(非空) > *.log/log.*(mtime);*.out 永不进兜底。

运行(仓库根): python3.11 -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MCP_DIR = os.path.join(os.path.dirname(HERE), "mcp")
if MCP_DIR not in sys.path:
    sys.path.insert(0, MCP_DIR)

import pipeline_check  # noqa: E402

THERMO = "0 300.0 -5000.0 0 0 0 1.0 1000.0\n" * 8
SPECIES = "0 1482.0 C2H4 3\n1000 1482.0 C2H4 5\n"  # 第二列不是温度,但数值像


class TestResolveLogFile(unittest.TestCase):
    def setUp(self):
        self.prod = tempfile.mkdtemp(prefix="pcheck_log_")

    def _write(self, name, content, age_sec=0):
        p = os.path.join(self.prod, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        ts = time.time() - age_sec
        os.utime(p, (ts, ts))
        return p

    def test_species_out_newer_still_picks_log_lammps(self):
        """核心回归:species.out(mtime 新)+ log.lammps(mtime 旧)→ 取 log.lammps。"""
        self._write("log.lammps", THERMO, age_sec=3600)
        self._write("species.out", SPECIES, age_sec=0)
        got = pipeline_check._resolve_log_file(self.prod, None)
        self.assertEqual(got, os.path.join(self.prod, "log.lammps"))

    def test_production_stdout_beats_log_lammps(self):
        self._write("log.lammps", THERMO, age_sec=0)
        self._write("production.stdout", THERMO, age_sec=3600)
        got = pipeline_check._resolve_log_file(self.prod, None)
        self.assertEqual(got, os.path.join(self.prod, "production.stdout"))

    def test_empty_stdout_falls_through_to_lammps(self):
        self._write("production.stdout", "", age_sec=0)  # 空文件不算数
        self._write("log.lammps", THERMO, age_sec=3600)
        got = pipeline_check._resolve_log_file(self.prod, None)
        self.assertEqual(got, os.path.join(self.prod, "log.lammps"))

    def test_out_files_never_in_fallback(self):
        """只有 species.out 时兜底必须空手而归(NO_LOG),绝不猜 *.out。"""
        self._write("species.out", SPECIES, age_sec=0)
        self.assertIsNone(pipeline_check._resolve_log_file(self.prod, None))

    def test_fallback_picks_newest_log(self):
        old = self._write("run.log", THERMO, age_sec=3600)
        new = self._write("log.run", THERMO, age_sec=0)
        self.assertEqual(pipeline_check._resolve_log_file(self.prod, None), new)
        self.assertNotEqual(pipeline_check._resolve_log_file(self.prod, None), old)

    def test_explicit_log_file_respected(self):
        self._write("log.lammps", THERMO, age_sec=3600)
        p = self._write("custom.out", THERMO, age_sec=0)  # 显式指定时 *.out 也可以用
        self.assertEqual(pipeline_check._resolve_log_file(self.prod, "custom.out"), p)

    def test_explicit_missing_returns_none(self):
        self._write("log.lammps", THERMO)
        self.assertIsNone(pipeline_check._resolve_log_file(self.prod, "no_such.log"))


if __name__ == "__main__":
    unittest.main()
