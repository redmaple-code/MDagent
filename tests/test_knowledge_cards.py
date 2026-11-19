# -*- coding: utf-8 -*-
"""知识名片生成器回归门(evolution/knowledge_cards.py):
确定性骨架(分组/去重/上限/分类/SOP/notes)、人工核状态跨重建持久、
默认零网络(蒸馏只由 --llm 显式触发)。"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="kc-t")
# 不在 import 期改 MDSIM_DATA_ROOT(discover 全模块先导入,模块级赋值会clobber
# 其他套件——scnet 先例:env 只在 setUp 重指)。本测试全部走显式路径参数,
# memory 模块常量只作缺省值,永不被本套件触碰。
sys.path.insert(0, os.path.join(HERE, "..", "evolution"))

import knowledge_cards as kc  # noqa: E402

import atexit  # noqa: E402
atexit.register(lambda: shutil.rmtree(TMP, ignore_errors=True))


def _w(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class TestBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = os.path.join(TMP, "mem")
        atoms = [
            {"id": "a1", "t": 100.0, "kind": "build-lesson",
             "fact": "packmol 容差过小导致重叠", "refs": ["case:x1"]},
            {"id": "a2", "t": 200.0, "kind": "build-lesson",
             "fact": "packmol 容差过小导致重叠", "refs": ["case:x1"]},  # 重复事实
            {"id": "a3", "t": 300.0, "kind": "build-lesson",
             "fact": "gro 盒子先 min 再 npt", "refs": ["case:x%d" % i for i in range(15)]},
            {"id": "a4", "t": 400.0, "kind": "flow-gate-violation",
             "fact": "不要调用任何工具", "refs": ["events/flowgate.jsonl#1"]},
            {"id": "a5", "t": 500.0, "kind": "forcefield-verified",
             "fact": "ReaxFF 水 300K 不可信", "refs": []},
        ]
        _w(os.path.join(cls.tmp, "atoms.jsonl"),
           "\n".join(json.dumps(a, ensure_ascii=False) for a in atoms))
        _w(os.path.join(cls.tmp, "SOP.md"),
           "# SOP\n\n## 爆炸处置\n- 先降温\n- 查 QEq\n\n## 断链修复\n- 一次一变量\n")
        _w(os.path.join(cls.tmp, "notes", "20260901_caseA_lesson.md"),
           "<!-- {\"case_id\":\"caseA\"} -->\n标题行\n\n这是正文第一段,应成为事实。\n")
        _w(os.path.join(cls.tmp, "scenarios.json"),
           json.dumps([{"signature": "build-lesson", "count": 3}], ensure_ascii=False))
        cls.cards_path = os.path.join(cls.tmp, "knowledge_cards.jsonl")
        cls.cards = kc.build(cards_path=cls.cards_path,
                             atoms_path=os.path.join(cls.tmp, "atoms.jsonl"),
                             sop_path=os.path.join(cls.tmp, "SOP.md"),
                             notes_dir=os.path.join(cls.tmp, "notes"),
                             scenarios_path=os.path.join(cls.tmp, "scenarios.json"))

    def test_grouping_and_category(self):
        by_kind = {c["kind"]: c for c in self.cards if c["source"] == "atoms"}
        self.assertEqual(by_kind["build-lesson"]["count"], 3)
        self.assertEqual(by_kind["build-lesson"]["category"], "教训")
        self.assertEqual(by_kind["flow-gate-violation"]["category"], "摩擦与纠正")
        self.assertEqual(by_kind["forcefield-verified"]["category"], "已验证结论")

    def test_facts_dedup_refs_cap(self):
        c = [x for x in self.cards if x["kind"] == "build-lesson"][0]
        facts = [f for f in c["facts"] if "packmol" in f]
        self.assertEqual(len(facts), 1, "重复事实只留一条")
        self.assertLessEqual(len(c["refs"]), kc.MAX_REFS, "refs 必须封顶")
        self.assertLessEqual(len(c["atom_ids"]), kc.MAX_ATOM_IDS)

    def test_sop_sections_become_cards(self):
        sop = [c for c in self.cards if c["source"] == "sop"]
        titles = " ".join(c["title"] for c in sop)
        self.assertIn("爆炸处置", titles)
        self.assertIn("断链修复", titles)

    def test_note_cards(self):
        notes = [c for c in self.cards if c["source"] == "note"]
        self.assertEqual(len(notes), 1)
        self.assertIn("正文第一段", notes[0]["facts"][0])
        self.assertTrue(notes[0]["refs"][0].startswith("notes/"))

    def test_no_llm_by_default(self):
        self.assertTrue(all("distill" not in c for c in self.cards),
                        "不带 --llm 不得有任何蒸馏字段/网络调用")

    def test_scenario_count_merged(self):
        c = [x for x in self.cards if x["kind"] == "build-lesson"][0]
        self.assertEqual(c.get("scenario_count"), 3)


class TestReview(unittest.TestCase):
    def test_review_persists_across_rebuild(self):
        tmp = os.path.join(TMP, "rev")
        os.makedirs(tmp, exist_ok=True)
        _w(os.path.join(tmp, "atoms.jsonl"),
           json.dumps({"id": "a1", "t": 1.0, "kind": "env-lesson",
                       "fact": "f", "refs": []}, ensure_ascii=False))
        cards_path = os.path.join(tmp, "knowledge_cards.jsonl")
        reviews_path = os.path.join(tmp, "knowledge_reviews.json")
        kc.build(cards_path=cards_path, atoms_path=os.path.join(tmp, "atoms.jsonl"),
                 sop_path=os.path.join(tmp, "SOP_missing.md"),
                 notes_dir=os.path.join(tmp, "notes_missing"),
                 scenarios_path=os.path.join(tmp, "scenarios_missing.json"))
        cid = "kc-env-lesson"
        rec = kc.review(cid, by="tester", note="ok",
                        cards_path=cards_path, reviews_path=reviews_path)
        self.assertEqual(rec["by"], "tester")
        cards = kc.build(cards_path=cards_path,
                         atoms_path=os.path.join(tmp, "atoms.jsonl"),
                         sop_path=os.path.join(tmp, "SOP_missing.md"),
                         notes_dir=os.path.join(tmp, "notes_missing"),
                         scenarios_path=os.path.join(tmp, "scenarios_missing.json"))
        c = [x for x in cards if x["id"] == cid][0]
        self.assertEqual(c.get("reviewed_by"), "tester", "人工核状态须跨重建保留")

    def test_review_unknown_card_rejected(self):
        tmp = os.path.join(TMP, "rev2")
        os.makedirs(tmp, exist_ok=True)
        cards_path = os.path.join(tmp, "knowledge_cards.jsonl")
        _w(cards_path, json.dumps({"id": "kc-x", "kind": "x"}, ensure_ascii=False) + "\n")
        with self.assertRaises(SystemExit):
            kc.review("kc-not-exist", cards_path=cards_path,
                      reviews_path=os.path.join(tmp, "r.json"))


if __name__ == "__main__":
    unittest.main()
