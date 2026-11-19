# -*- coding: utf-8 -*-
"""学习层消融对照回归门(2026-09-02):experiments/ablation_replay.py 固定种子
双臂回放,学习臂必须在恢复胜率/尝试次数/一次修复率上同时显著占优。
门槛与脚本退出码同口径:胜率 +10pp / 均次严格更低 / 一次修复 +15pp。"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "experiments"))

import ablation_replay  # noqa: E402


class TestAblationReplay(unittest.TestCase):
    def test_learning_arm_dominates(self):
        if not ablation_replay.KIT or not os.path.isdir(ablation_replay.KIT):
            self.skipTest("kit 路径不可解析(设 MDSIM_KIT_BASE)")
        argv = sys.argv
        sys.argv = ["ablation_replay.py", "--cases", "300", "--seed", "42"]
        try:
            rc = ablation_replay.main()
        finally:
            sys.argv = argv
        self.assertEqual(rc, 0, "学习臂未显著占优——学习层机制回归")


if __name__ == "__main__":
    unittest.main()
