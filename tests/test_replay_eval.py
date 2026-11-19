#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""回放裁判 replay_eval 单测(沙箱 history/atoms,绝不碰真账本)。"""
import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "evolution"))

import replay_eval  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="replay_test_")


def _history():
    # 10 事件,时序:settled_ts 升序;QEq 有 1 个成功,T* 全无成功
    rows = []
    for i in range(6):
        rows.append({"strategy": "E", "root_cause": "QEq_instability",
                     "case_id": "c1", "ts": "2026-09-01T0%d:00:00" % i,
                     "settled_ts": "2026-09-01T0%d:30:00" % i, "outcome": "fail"})
    rows.append({"strategy": "checkpoint_restart", "root_cause": "QEq_instability",
                 "case_id": "c1", "ts": "2026-09-01T07:00:00",
                 "settled_ts": "2026-09-01T07:30:00", "outcome": "fail"})
    rows.append({"strategy": "checkpoint_restart", "root_cause": "QEq_instability",
                 "case_id": "c1", "ts": "2026-09-01T08:00:00",
                 "settled_ts": "2026-09-01T08:30:00", "outcome": "success"})
    for i in range(2):
        rows.append({"strategy": "checkpoint_restart", "root_cause": "extreme_temp",
                     "case_id": "c2", "ts": "2026-09-01T09:0%d:00" % i,
                     "settled_ts": "2026-09-01T09:3%d:00" % i, "outcome": "fail"})
    return {"strategies": {}, "history": rows}


def _write_db(rows):
    p = os.path.join(_TMP, "db.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(rows, f)
    return p


def _write_atoms(lines):
    p = os.path.join(_TMP, "atoms.jsonl")
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return p


def _atom(aid, ref):
    return json.dumps({"id": aid, "kind": "recovery-outcome",
                       "fact": "E:QEq_instability:fail", "refs": [ref],
                       "axes": {"exam_ready": True}}, ensure_ascii=False)


class TestLoadHistory(unittest.TestCase):
    def setUp(self):
        self.db = _write_db(_history())

    def test_sorted_and_filtered(self):
        ev, st = replay_eval.load_history(self.db)
        self.assertEqual(st["kept"], 10)
        ts = [e["settled_ts"] for e in ev]
        self.assertEqual(ts, sorted(ts))

    def test_bad_rows_skipped(self):
        db = _write_db({"history": [
            {"strategy": "A", "root_cause": "x", "ts": "t", "outcome": "???"},
            {"strategy": "A"},  # 缺字段
            "garbage",
            {"strategy": "A", "root_cause": "x", "ts": "2026-01-01T00:00:00",
             "outcome": "success"}]})
        ev, st = replay_eval.load_history(db)
        self.assertEqual(st["kept"], 1)
        self.assertEqual(st["skipped"], 3)

    def test_missing_db_no_throw(self):
        ev, st = replay_eval.load_history(os.path.join(_TMP, "nope.json"))
        self.assertEqual(ev, [])
        self.assertEqual(st["kept"], 0)


class TestValidateAtoms(unittest.TestCase):
    def setUp(self):
        self.db = _write_db(_history())
        self.events, _ = replay_eval.load_history(self.db)

    def test_resolvable_ref_ok(self):
        p = _write_atoms([_atom("a1", "strategy_db#2026-09-01T00:00:00#E#c1")])
        res, unres, st = replay_eval.validate_atoms(p, self.events)
        self.assertEqual(st["refs_ok"], 1)
        self.assertEqual(res, ["a1"])

    def test_forged_ref_rejected(self):
        """虚构的 ts/strategy 组合(历史里不存在)必须被剔除。"""
        p = _write_atoms([_atom("a2", "strategy_db#2099-01-01T00:00:00#ZZ#QEq_instability")])
        res, unres, st = replay_eval.validate_atoms(p, self.events)
        self.assertEqual(st["refs_ok"], 0)
        self.assertEqual(st["refs_bad"], 1)
        self.assertEqual(unres, ["a2"])

    def test_non_exam_ready_ignored(self):
        a = json.dumps({"id": "a3", "kind": "note", "refs": ["x"],
                        "axes": {"exam_ready": False}})
        p = _write_atoms([a, "not json", ""])
        res, unres, st = replay_eval.validate_atoms(p, self.events)
        self.assertEqual(st["exam_ready"], 0)


class TestJudge(unittest.TestCase):
    def setUp(self):
        self.db = _write_db(_history())
        self.events, _ = replay_eval.load_history(self.db)
        self.cands = {(c["root_cause"], c["strategy"]): c
                      for c in replay_eval.judge(self.events)
                      if c["status"] == "scored"}

    def test_success_coverage(self):
        """checkpoint_restart/QEq: 1 成功 1 失败都在 seen(成功非尾部)→ 命中 1.0,误报 0.5。"""
        c = self.cands[("QEq_instability", "checkpoint_restart")]
        self.assertEqual(c["seen"]["fires"], 2)
        self.assertEqual(c["seen"]["hits"], 1)
        self.assertEqual(c["seen"]["hit_rate"], 1.0)
        self.assertEqual(c["seen"]["fp_rate"], 0.5)
        # holdout 只有 extreme_temp 两条(时序尾部),该考生 holdout 无出手
        self.assertEqual(c["holdout"]["fires"], 0)

    def test_all_fail_no_success(self):
        """E/QEq 全败:根因成功数是全局口径,fires 全 fail → 误报 1.0。"""
        c = self.cands[("QEq_instability", "E")]
        total_fires = c["seen"]["fires"] + c["holdout"]["fires"]
        total_hits = c["seen"]["hits"] + c["holdout"]["hits"]
        self.assertEqual(total_fires, 6)
        self.assertEqual(total_hits, 0)

    def test_cause_without_success_hitrate_none(self):
        """extreme_temp 无成功结局 → hit_rate None(无可证成功,不判)。"""
        c = [x for x in replay_eval.judge(self.events)
             if x["root_cause"] == "extreme_temp"][0]
        self.assertIsNone(c["holdout"]["hit_rate"])
        self.assertFalse(c["graduation_hint"]["hit_ok"])

    def test_holdout_is_chronological_tail(self):
        ev = list(self.events)
        cands = replay_eval.judge(ev)
        # 8 条进 seen(前 80%),extreme_temp 两条(最晚)必在 holdout
        c = [x for x in cands if x["root_cause"] == "extreme_temp"][0]
        self.assertEqual(c["seen"]["fires"], 0)
        self.assertEqual(c["holdout"]["fires"], 2)

    def test_templates_registered_untested(self):
        """模板考生从未真实执行 → untested,不误判分。"""
        cands = replay_eval.judge(self.events)
        untested = [c for c in cands if c["status"] == "untested"]
        self.assertTrue(untested)
        for c in untested:
            self.assertEqual(c["holdout"]["fires"], 0)
            self.assertIsNone(c["holdout"]["fp_rate"])

    def test_empty_events(self):
        self.assertTrue(all(c["status"] == "untested"
                            for c in replay_eval.judge([])))


class TestRun(unittest.TestCase):
    def test_run_writes_scores_and_never_touches_inputs(self):
        db = _write_db(_history())
        atoms = _write_atoms([_atom("a1", "strategy_db#2026-09-01T00:00:00#E#c1")])
        before_db = open(db, "rb").read()
        before_atoms = open(atoms, "rb").read()
        out = os.path.join(_TMP, "scores.json")
        sheet = replay_eval.run(db_path=db, atoms_path=atoms, out_path=out)
        self.assertTrue(os.path.exists(out))
        self.assertEqual(open(db, "rb").read(), before_db)
        self.assertEqual(open(atoms, "rb").read(), before_atoms)
        self.assertEqual(sheet["history"]["kept"], 10)
        self.assertEqual(sheet["atom_ids_verified"], 1)
        # 成绩册可重读且结构完整
        again = json.load(open(out, encoding="utf-8"))
        self.assertEqual(again["candidates"], sheet["candidates"])


if __name__ == "__main__":
    unittest.main()
