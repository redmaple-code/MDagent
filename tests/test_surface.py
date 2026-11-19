# -*- coding: utf-8 -*-
"""核查面(surface)+E6 单测：枚举/记录/评估/问题包/去重/沉淀。"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SRC, "evolution"))
sys.path.insert(0, os.path.join(SRC, "mcp"))

import stage_state  # noqa: E402
import stage_gate   # noqa: E402
import surface      # noqa: E402
import memory       # noqa: E402


def _wjson(fp, doc):
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)


class Base(unittest.TestCase):
    def setUp(self):
        self.dr = tempfile.mkdtemp()
        os.environ["MDSIM_DATA_ROOT"] = self.dr
        self.cid = "SURF-1"
        stage_state.init_task(self.cid, risk_level="medium", data_root=self.dr)
        # 沉淀通道重定向到 sandbox
        self._old = (memory.EXP_DIR, memory.ATOMS)
        memory.EXP_DIR = os.path.join(self.dr, "memory")
        memory.ATOMS = os.path.join(self.dr, "memory", "atoms.jsonl")
        os.makedirs(memory.EXP_DIR, exist_ok=True)

    def tearDown(self):
        memory.EXP_DIR, memory.ATOMS = self._old
        os.environ.pop("MDSIM_DATA_ROOT", None)
        shutil.rmtree(self.dr, ignore_errors=True)

    def ctx(self):
        return {"case_id": self.cid, "data_root": self.dr,
                "state": stage_state.read_state(self.cid, self.dr),
                "facts": {"a": 1}, "ledger": [], "atoms": [],
                "exp_index": {}, "cases": {}, "confirmations": [],
                "factchecks": {}, "toolcalls": [], "assessments": {},
                "decisions": {}}

    def design_items(self):
        return [i for i in surface.load_items(self.dr).values()
                if i.get("phase") == "design"]


class TestSurfaceData(Base):
    def test_pack_shape(self):
        items = surface.load_items(self.dr)
        self.assertGreaterEqual(len(items), 25)
        phases = {i.get("phase") for i in items.values()}
        self.assertEqual(phases, {"design", "build", "exec", "analyze"})
        for i in items.values():
            self.assertIn("question", i)
            self.assertIn("min_grade", i)
            self.assertIn("na_allowed", i)


class TestEvaluate(Base):
    def test_missing_all(self):
        ev = surface.evaluate(self.cid, "design", self.ctx())
        self.assertFalse(ev["ok"])
        self.assertTrue(any(r.startswith("E6:surface-missing") for r in ev["reasons"]))
        self.assertTrue(ev["questions"])

    def test_complete_with_evidence(self):
        for it in self.design_items():
            reason = ("静态最小化：无时间步长/无系综/无控温/无预平衡/无初速"
                      if it.get("na_reason_pattern") else "非适用")
            surface.record_coverage(self.cid, it["id"], "na" if it["na_allowed"]
                                    else "evidenced",
                                    evidence=[], reason=reason if it["na_allowed"] else "",
                                    answer="测试确认值 1", data_root=self.dr)
        # 非 na 的项给 B 级论文
        for it in self.design_items():
            if not it["na_allowed"]:
                surface.record_coverage(self.cid, it["id"], "evidenced",
                                        evidence=["paper:10.1234/x"],
                                        answer="测试确认值 1", data_root=self.dr)
            else:
                reason = ("静态最小化：无时间步长/无系综/无控温/无预平衡/无初速"
                          if it.get("na_reason_pattern") else "非适用")
                surface.record_coverage(self.cid, it["id"], "na",
                                        reason=reason, data_root=self.dr)
        ev = surface.evaluate(self.cid, "design", self.ctx())
        self.assertTrue(ev["ok"], ev["reasons"])

    def test_na_without_reason(self):
        items = self.design_items()
        target = [i for i in items if i["na_allowed"]][0]
        surface.record_coverage(self.cid, target["id"], "na", reason="", data_root=self.dr)
        ev = surface.evaluate(self.cid, "design", self.ctx())
        self.assertTrue(any("E6:surface-na-reason" in r for r in ev["reasons"]))

    def test_na_not_allowed(self):
        items = self.design_items()
        target = [i for i in items if not i["na_allowed"]][0]
        surface.record_coverage(self.cid, target["id"], "na", reason="不想查",
                                data_root=self.dr)
        ev = surface.evaluate(self.cid, "design", self.ctx())
        self.assertTrue(any("E6:surface-na-not-allowed" in r for r in ev["reasons"]))

    def test_insufficient_grade(self):
        items = self.design_items()
        target = [i for i in items if not i["na_allowed"]][0]
        # F 级估算不足以满足中等风险门槛
        surface.record_coverage(self.cid, target["id"], "evidenced",
                                evidence=["凭经验估的"], data_root=self.dr)
        ev = surface.evaluate(self.cid, "design", self.ctx())
        self.assertTrue(any("E6:surface-evidence-insufficient" in r for r in ev["reasons"]))

    def test_unknown_item_rejected(self):
        with self.assertRaises(ValueError):
            surface.record_coverage(self.cid, "x", "whatever", data_root=self.dr)


class TestE6Gate(Base):
    def _design_ready(self):
        # E1 所需的决策与评估
        stage_state.lock_decisions(
            self.cid, {"D1_对象与范围": "测试"}, self.dr,
            basis={"D1_对象与范围": {"kind": "literature", "ref": "paper:10.1234/x"}})
        d = os.path.join(stage_state.task_dir(self.cid, self.dr), "assessments")
        _wjson(os.path.join(d, "plan.json"),
               {"case_id": self.cid, "phase": "plan", "findings": [],
                "critical_open": [], "overall": "sound"})

    def test_e6_blocks_plan_when_surface_missing(self):
        self._design_ready()
        r = stage_gate.gate_and_log(
            self.cid, {"gate": "G-PLAN", "stage": "PLAN", "decision": "PASS",
                       "summary": "t"}, data_root=self.dr)
        self.assertEqual(r["result"], "BLOCK")
        self.assertTrue(any(x.startswith("E6:surface-missing") for x in r["reasons"]))
        self.assertTrue(r.get("questions"))

    def test_e6_passes_when_surface_complete(self):
        self._design_ready()
        for it in self.design_items():
            if it["na_allowed"]:
                reason = ("静态最小化：无时间步长/无系综/无控温/无预平衡/无初速"
                          if it.get("na_reason_pattern") else "非适用")
                surface.record_coverage(self.cid, it["id"], "na", reason=reason,
                                        data_root=self.dr)
            else:
                surface.record_coverage(self.cid, it["id"], "evidenced",
                                        evidence=["paper:10.1234/x"],
                                        answer="测试确认值 1", data_root=self.dr)
        r = stage_gate.gate_and_log(
            self.cid, {"gate": "G-PLAN", "stage": "PLAN", "decision": "PASS",
                       "summary": "t"}, data_root=self.dr)
        self.assertEqual(r["result"], "GO", r["reasons"])
        self.assertIn("核查面 design", r["rule_trace"].get("E6", ""))

    def test_question_dedup_escalates(self):
        self._design_ready()
        r1 = stage_gate.gate_and_log(
            self.cid, {"gate": "G-PLAN", "stage": "PLAN", "decision": "PASS",
                       "summary": "t"}, data_root=self.dr)
        self.assertTrue(r1.get("questions"))
        # 第二次仍缺项 → 同项标记 unresolved（不重复问新问题）
        r2 = stage_gate.gate_and_log(
            self.cid, {"gate": "G-PLAN", "stage": "PLAN", "decision": "PASS",
                       "summary": "t"}, data_root=self.dr)
        self.assertTrue(any("surface-unresolved" in x for x in r2["reasons"]),
                        r2["reasons"])


class TestSediment(Base):
    def test_sediment_writes_atom(self):
        surface.record_coverage(self.cid, "sys-potential", "evidenced",
                                evidence=["paper:10.1234/x"],
                                answer="EAM Cu_u3 适用", data_root=self.dr)
        aid = surface.sediment(self.cid, "sys-potential",
                               "势函数适用性：EAM Cu_u3 对该体系已验证",
                               refs=["paper:10.1234/x"], data_root=self.dr)
        self.assertTrue(aid)
        rows = [json.loads(l) for l in open(memory.ATOMS, encoding="utf-8")]
        self.assertTrue(any(r.get("id") == aid for r in rows))
        cov = surface.read_coverage(self.cid, self.dr)
        self.assertTrue(cov["sys-potential"]["sedimented"])
        self.assertEqual(cov["sys-potential"]["atom_id"], aid)


if __name__ == "__main__":
    unittest.main()
