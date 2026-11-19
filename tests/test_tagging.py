#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""三轴打标(tagging/backfill/入账接线/提案分流)单测,沙箱 EXP_DIR。
含 2026-09-18 三路红队(μ/ν/ξ)发现的回归项。"""
import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "evolution"))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "mcp"))

_TMP = tempfile.mkdtemp(prefix="tag_test_")

import memory  # noqa: E402
import tagging  # noqa: E402


def setUpModule():
    memory.EXP_DIR = _TMP
    memory.ATOMS = os.path.join(_TMP, "atoms.jsonl")
    memory.SCENARIOS = os.path.join(_TMP, "scenarios.json")
    memory.SOP = os.path.join(_TMP, "SOP.md")
    memory.LEDGER = os.path.join(_TMP, "LEDGER.jsonl")


class TestTagging(unittest.TestCase):
    def setUp(self):
        tagging.reload_registry()

    def test_method_tags(self):
        ax = tagging.tag_atom("recovery-outcome",
                              "E:QEq_instability:fail 已换 NVT tdamp 策略")
        self.assertIn("repair-rescue", ax["method"])
        self.assertIn("dynamics/temp-press", ax["method"])

    def test_ops_tags(self):
        ax = tagging.tag_atom("flow-gate-violation", "现在模拟跑到哪了?直接回答")
        self.assertIn("ops/flow-gate", ax["ops"])
        self.assertEqual(ax["method"], [])

    def test_topic_known_and_caseid_split(self):
        """已知课题进 topic(已认证);case_id 正则命中进 topic_candidates(未过闸)。"""
        ax = tagging.tag_atom("s2-thermal-runaway-deepdive",
                              "S2自热失控深挖: PRJ_SYS-A_pyro_1500K_r1 炭化放热")
        self.assertIn("sys-a-pyrolysis", ax["topic"])
        self.assertEqual(ax["topic_candidates"], ["PRJ_SYS-A_pyro_1500K_r1"])
        self.assertNotIn("PRJ_SYS-A_pyro_1500K_r1", ax["topic"])  # 候选≠认证
        self.assertIn("phys-perception", ax["method"])
        self.assertIn("repair-rescue", ax["method"])

    def test_caseid_hyphens_preserve_identity(self):
        """连字符各段保留完整 ID;不同前缀不合并,原下划线 ID 仍仅作候选。"""
        case_ids = ["Candidate-A_Model-x_Run-1", "Candidate-B_Model-x_Run-1",
                    "Plain_Model_v1"]
        ax = tagging.tag_atom("build-lesson", " ".join(case_ids))
        self.assertEqual(ax["topic_candidates"], case_ids)
        self.assertEqual(ax["topic"], [])

    def test_fake_topic_goes_to_candidates(self):
        """红队ν:伪课题名(MyResearch_Project_v1)只能进 candidates,不污染认证轴。"""
        ax = tagging.tag_atom("build-lesson",
                              "MyResearch_Project_v1 的经验:packmol 要检查密度")
        self.assertIn("MyResearch_Project_v1", ax["topic_candidates"])
        self.assertEqual(ax["topic"], [])

    def test_ops_word_boundary(self):
        """红队ν:ops 纯词关键词词边界——'audit checklist' 不再误标 ops/security。"""
        ax = tagging.tag_atom("build-lesson", "建模后跑一遍 audit checklist 再交付")
        self.assertNotIn("ops/security", ax["ops"])
        ax2 = tagging.tag_atom("bash-bypass-attack-surface", "bash bypass 攻击面四洞")
        self.assertIn("ops/security", ax2["ops"])  # 真安全事件仍命中

    def test_refs_str_wrapped(self):
        """红队μ:str 型 refs 不再逐字符拆分。"""
        ax = tagging.tag_atom("recovery-outcome", "E:QEq:fail",
                              refs="logs/recovery.jsonl:42")
        self.assertTrue(ax["exam_ready"])  # refs 归一后非空

    def test_untagged_ok(self):
        ax = tagging.tag_atom("random-kind", "一句和什么都无关的话")
        self.assertEqual(ax["method"], [])
        self.assertEqual(ax["ops"], [])

    def test_exam_ready_three_elements(self):
        good = {"kind": "recovery-outcome", "fact": "E:QEq:fail",
                "refs": ["logs/x.jsonl:3"]}
        self.assertTrue(tagging.exam_ready(good))
        self.assertFalse(tagging.exam_ready({**good, "refs": []}))
        self.assertFalse(tagging.exam_ready({**good, "kind": "friction-denial"}))
        self.assertFalse(tagging.exam_ready({**good, "fact": "无结局信号"}))

    def test_verify_refs(self):
        """红队ν:考题留出集前的 refs 存在性核验。"""
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "logs"))
        open(os.path.join(d, "logs", "x.jsonl"), "w").write("line\n")
        ok = {"refs": ["logs/x.jsonl:3"]}
        fake = {"refs": ["logs/nonexistent.jsonl:999"]}
        self.assertTrue(tagging.verify_refs(ok, d))
        self.assertFalse(tagging.verify_refs(fake, d))
        self.assertFalse(tagging.verify_refs({"refs": ["session-abc"]}, d))

    def test_registry_missing_degrades(self):
        tagging._REGISTRY = {"method": {}, "ops": {}, "known_topics": {},
                             "case_id_regex": "", "exam_ready_kinds": []}
        ax = tagging.tag_atom("recovery-outcome", "E:x:fail", ["r"])
        self.assertEqual(ax["method"], [])
        self.assertFalse(ax["exam_ready"])
        tagging.reload_registry()

    def test_registry_empty_keyword_rejected(self):
        """红队ξ:空关键词子串匹配=全命中,加载时剔除。"""
        tbl = tagging._validate_table({"evil": ["", "  ", "qeq"],
                                       "empty": [], "badtype": "notalist"})
        self.assertEqual(tbl, {"evil": ["qeq"]})


class TestRecordAtomAxes(unittest.TestCase):
    def setUp(self):
        tagging.reload_registry()
        for p in (memory.ATOMS, memory.SCENARIOS, memory.SOP):
            if os.path.exists(p):
                os.remove(p)

    def test_record_atom_stores_axes(self):
        a = memory.record_atom("recovery-outcome",
                               "E:bondchk:success 55→48.5Å 单级压缩修复",
                               refs=["logs/r.jsonl:9"])
        self.assertIsNotNone(a)
        self.assertIn("repair-rescue", a["axes"]["method"])
        self.assertTrue(a["axes"]["exam_ready"])
        rows = [json.loads(l) for l in open(memory.ATOMS, encoding="utf-8")]
        self.assertEqual(rows[-1]["axes"], a["axes"])

    def test_record_atom_refs_str(self):
        """红队μ:record_atom 收到 str 型 refs 不拆字符。"""
        a = memory.record_atom("build-lesson", "str refs 入账测试案例一条",
                               refs="logs/single.jsonl:7")
        self.assertEqual(a["refs"], ["logs/single.jsonl:7"])

    def test_tagging_failure_does_not_block(self):
        import unittest.mock as mock
        with mock.patch.object(tagging, "tag_atom", side_effect=RuntimeError):
            a = memory.record_atom("build-lesson", "打标器崩了也得入账的教训")
        self.assertIsNotNone(a)
        self.assertNotIn("axes", a)


class TestBackfill(unittest.TestCase):
    def setUp(self):
        tagging.reload_registry()
        for p in (memory.ATOMS,):
            if os.path.exists(p):
                os.remove(p)

    def test_backfill_write_and_idempotent(self):
        import backfill_tags
        memory.record_atom("recovery-outcome", "E:QEq:fail 试 NVT 降温",
                           refs=["l:1"])
        memory.record_atom("build-lesson", "packmol 双 NH 恒温器叠加抽干动能")
        self.assertEqual(backfill_tags.main(["--write"]), 0)
        rows = [json.loads(l) for l in open(memory.ATOMS, encoding="utf-8")]
        self.assertTrue(all("axes" in r for r in rows))
        self.assertIn("modeling/packmol", rows[1]["axes"]["method"])
        self.assertEqual(backfill_tags.main(["--write"]), 0)
        rows2 = [json.loads(l) for l in open(memory.ATOMS, encoding="utf-8")]
        self.assertEqual(rows, rows2)
        self.assertTrue(any(f.startswith("atoms.jsonl.bak_tags_")
                            for f in os.listdir(_TMP)))

    def test_backfill_preserves_fields(self):
        import backfill_tags
        a = memory.record_atom("env-lesson", "LAMMPS 不认中文路径,全程 ASCII",
                               refs=["x:1"])
        backfill_tags.main(["--write"])
        row = json.loads(open(memory.ATOMS, encoding="utf-8").read().strip())
        for k in ("id", "t", "kind", "fact", "refs"):
            self.assertEqual(row[k], a[k])

    def test_backfill_preserves_bad_lines(self):
        """红队μ P1-1:坏行/无 kind 行不再静默丢弃。"""
        import backfill_tags
        with open(memory.ATOMS, "w", encoding="utf-8") as f:
            f.write('{"id":"g1","kind":"build-lesson","fact":"正常原子一条记录"}\n')
            f.write('{broken json\n')
            f.write('{"id":"nokind","fact":"没kind的合法行"}\n')
        self.assertEqual(backfill_tags.main(["--write"]), 0)
        lines = open(memory.ATOMS, encoding="utf-8").read().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[1], "{broken json")
        self.assertIn("nokind", lines[2])
        good = json.loads(lines[0])
        self.assertIn("axes", good)

    def test_backfill_rejects_nan(self):
        """红队μ P1-3:NaN 行按坏行保留,不产非法 JSON。"""
        import backfill_tags
        with open(memory.ATOMS, "w", encoding="utf-8") as f:
            f.write('{"id":"n1","kind":"build-lesson","fact":"含NaN","t":NaN}\n')
        self.assertEqual(backfill_tags.main(["--write"]), 0)
        content = open(memory.ATOMS, encoding="utf-8").read()
        self.assertIn("NaN", content)  # 原行保留
        # 且没有被打标成新 JSON 行
        self.assertEqual(len(content.strip().splitlines()), 1)

    def test_backfill_malformed_axes_retag(self):
        """红队μ P2-5:已有 axes 是字符串等畸形时不崩,重打。"""
        import backfill_tags
        with open(memory.ATOMS, "w", encoding="utf-8") as f:
            f.write('{"id":"m1","kind":"build-lesson",'
                    '"fact":"packmol 密度要检查","axes":"oops","refs":[]}\n')
        self.assertEqual(backfill_tags.main(["--write"]), 0)
        row = json.loads(open(memory.ATOMS, encoding="utf-8").read().strip())
        self.assertIsInstance(row["axes"], dict)
        self.assertIn("modeling/packmol", row["axes"]["method"])

    def test_backfill_symlink_rejected(self):
        """红队ξ P1-2:ATOMS 是软链时拒绝写入。"""
        import backfill_tags
        real = os.path.join(_TMP, "real_atoms.jsonl")
        with open(real, "w") as f:
            f.write('{"id":"s1","kind":"build-lesson","fact":"软链目标原子一条"}\n')
        os.symlink(real, memory.ATOMS)
        with self.assertRaises(SystemExit):
            backfill_tags.main(["--write"])
        os.remove(memory.ATOMS)

    def test_backup_mode_640(self):
        """红队ξ P2-1:备份权限 640,不依赖 umask。"""
        import stat as _stat
        backfill_mod = __import__("backfill_tags")
        memory.record_atom("build-lesson", "备份权限测试原子一条记录")
        backfill_mod.main(["--write"])
        baks = [f for f in os.listdir(_TMP) if "bak_tags_" in f]
        self.assertTrue(baks)
        mode = _stat.S_IMODE(os.stat(os.path.join(_TMP, baks[-1])).st_mode)
        self.assertEqual(mode, 0o640)


if __name__ == "__main__":
    unittest.main()
