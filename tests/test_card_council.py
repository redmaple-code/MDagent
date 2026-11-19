# -*- coding: utf-8 -*-
"""经验研讨团回归门(evolution/card_council.py):
确定性基线双轴全覆盖恰好一次、LLM 失败降级 llm:false、主席输出覆盖率校验、
原子写无残骸。默认零网络(_chat 只有 --llm 路径才被调,且测试全部 stub)。"""
import atexit
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="cc-t")
sys.path.insert(0, os.path.join(HERE, "..", "evolution"))

import card_council as cc  # noqa: E402

atexit.register(lambda: shutil.rmtree(TMP, ignore_errors=True))


def _cards(tmp, rows):
    os.makedirs(tmp, exist_ok=True)
    path = os.path.join(tmp, "knowledge_cards.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


def _row(cid, kind, facts=("f",), source="atoms"):
    return {"id": cid, "source": source, "kind": kind, "title": kind,
            "category": "其他", "count": 1, "facts": list(facts), "refs": []}


ROWS = [
    _row("kc-flow", "flow-gate-violation", ("用户要状态不许调工具",)),
    _row("kc-build", "build-lesson", ("packmol 建盒容差过小需重打包",)),
    _row("kc-monitor", "monitor-critical", ("温度尖峰 T_SPIKE 触发 CRITICAL",)),
    _row("kc-recovery", "recovery-outcome", ("策略 B 压制 thermal_runaway",)),
    _row("kc-evo", "evolution-proposal-audit", ("门禁红线拒绝路径逃逸",)),
    _row("kc-sop", "sop", ("爆炸处置流程",), source="sop"),
    _row("kc-lost", "monitor-lost-atoms", ("Lost atoms 原子数不守恒",)),
    _row("kc-qeq", "fix-lesson", ("QEq 收敛 tolerance 需放宽",)),
    _row("kc-gov", "friction-tool-error", ("工具错误轮频发需人审",)),
    _row("kc-unknown", "mystery-kind", ("说不清的经验",)),
]


class TestBaseline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cards_path = _cards(os.path.join(TMP, "base"), ROWS)
        cls.tax_path = os.path.join(TMP, "base", "knowledge_taxonomy.json")
        cls.tax = cc.council(cards_path=cls.cards_path,
                             taxonomy_path=cls.tax_path, with_llm=False)

    def test_written_and_atomic(self):
        disk = json.load(open(self.tax_path, encoding="utf-8"))
        self.assertEqual(disk["llm"], False)
        self.assertEqual(disk["cards_total"], len(ROWS))
        leftovers = [n for n in os.listdir(os.path.dirname(self.tax_path))
                     if ".tmp." in n]
        self.assertEqual(leftovers, [])

    def test_full_coverage_exactly_once(self):
        ids = [r["id"] for r in ROWS]
        for axis in ("layers", "physics"):
            flat = [i for g in self.tax[axis] for i in g["card_ids"]]
            self.assertEqual(sorted(flat), sorted(ids), "%s 轴须全覆盖" % axis)
            self.assertEqual(len(flat), len(set(flat)),
                             "%s 轴不得重复归组" % axis)

    def test_expected_layer_buckets(self):
        by_key = {g["key"]: set(g["card_ids"]) for g in self.tax["layers"]}
        self.assertIn("kc-flow", by_key["dialog"])
        self.assertIn("kc-build", by_key["pipeline"])
        self.assertIn("kc-monitor", by_key["monitor"])
        self.assertIn("kc-recovery", by_key["recovery"])
        self.assertIn("kc-evo", by_key["evolution"])
        self.assertIn("kc-sop", by_key["cross"])
        # recovery-outcome 含 "outcome"/"monitor" 双信号:规则序 recovery 先于 monitor
        self.assertNotIn("kc-recovery", by_key["monitor"])

    def test_expected_physics_buckets(self):
        by_key = {g["key"]: set(g["card_ids"]) for g in self.tax["physics"]}
        self.assertIn("kc-monitor", by_key["thermo_energy"])
        self.assertIn("kc-lost", by_key["mass_conservation"])
        self.assertIn("kc-qeq", by_key["charge_convergence"])
        self.assertIn("kc-build", by_key["modeling_setup"])
        self.assertIn("kc-gov", by_key["governance"])
        self.assertIn("kc-unknown", by_key["other"])

    def test_empty_group_dropped(self):
        keys = {g["key"] for g in self.tax["physics"]}
        self.assertNotIn("numerical_stability", keys, "空组不得出现")

    def test_no_llm_by_default(self):
        self.assertEqual(self.tax["llm"], False)
        self.assertEqual(self.tax["roles"], [])


class TestLlmFallback(unittest.TestCase):
    def test_chat_none_degrades_baseline(self):
        old = cc._chat
        cc._chat = lambda *a, **k: None
        try:
            tax = cc.council(cards_path=_cards(os.path.join(TMP, "fb"), ROWS),
                             taxonomy_path=os.path.join(TMP, "fb",
                                                        "knowledge_taxonomy.json"),
                             cache_path=os.path.join(TMP, "fb", "cache.json"),
                             with_llm=True)
        finally:
            cc._chat = old
        self.assertEqual(tax["llm"], False)
        self.assertEqual(tax["mode"], "baseline")
        flat = [i for g in tax["layers"] for i in g["card_ids"]]
        self.assertEqual(sorted(flat), sorted(r["id"] for r in ROWS))
        self.assertTrue(all(r.get("error") for r in tax["roles"]))

    def test_roles_only_when_chair_fails(self):
        # 主席缺席、角色在:层轴按角色发言聚合,物理轴走规则,mode=roles-only

        def fake_chat(system, user, **kw):
            if "主席" in system:
                return None  # 主席坏
            role = next(r for r in cc.ROLES if r["name"] in system)
            sel = {"dialog": [1], "pipeline": [2], "monitor": [3],
                   "recovery": [4], "evolution": [5]}.get(role["key"], [])
            return {"selected": sel, "summary": role["key"] + " 总结"}

        old = cc._chat
        cc._chat = fake_chat
        try:
            tax = cc.council(cards_path=_cards(os.path.join(TMP, "ro"), ROWS),
                             taxonomy_path=os.path.join(TMP, "ro",
                                                        "knowledge_taxonomy.json"),
                             cache_path=os.path.join(TMP, "ro", "cache.json"),
                             with_llm=True)
        finally:
            cc._chat = old
        self.assertTrue(tax["llm"])
        self.assertEqual(tax["mode"], "roles-only")
        by_key = {g["key"]: g for g in tax["layers"]}
        self.assertEqual(by_key["dialog"]["card_ids"], [ROWS[0]["id"]])
        self.assertEqual(by_key["dialog"]["summary"], "dialog 总结")
        self.assertEqual(by_key["cross"]["card_ids"],
                         [r["id"] for r in ROWS[5:]], "未选卡落跨层")
        flat = [i for g in tax["layers"] for i in g["card_ids"]]
        self.assertEqual(sorted(flat), sorted(r["id"] for r in ROWS))
        self.assertTrue(tax["physics"], "物理轴必须来自规则基线")

    def test_cache_reuses_success_zero_calls(self):
        # 成功角色/主席按指纹缓存:第二次重跑零 LLM 调用,产物 mode 一致
        calls = {"n": 0}

        def fake_chat(system, user, **kw):
            calls["n"] += 1
            if "主席" in system:
                return {"l": {"dialog": [1]}, "p": {"other": [10]},
                        "s": {"dialog": "缓存测试"}, "note": "cached"}
            role = next(r for r in cc.ROLES if r["name"] in system)
            return {"selected": [1], "summary": role["key"]}

        cpath = os.path.join(TMP, "cache", "cache.json")
        old = cc._chat
        cc._chat = fake_chat
        try:
            t1 = cc.council(cards_path=_cards(os.path.join(TMP, "ca"), ROWS),
                            taxonomy_path=os.path.join(TMP, "ca",
                                                       "knowledge_taxonomy.json"),
                            cache_path=cpath, with_llm=True)
            n_first = calls["n"]
            self.assertGreater(n_first, 0)
            t2 = cc.council(cards_path=_cards(os.path.join(TMP, "ca"), ROWS),
                            taxonomy_path=os.path.join(TMP, "ca",
                                                       "knowledge_taxonomy.json"),
                            cache_path=cpath, with_llm=True)
        finally:
            cc._chat = old
        self.assertEqual(calls["n"], n_first, "缓存命中不得再调 LLM")
        self.assertEqual(t1["mode"], t2["mode"], "roles+chair")
        flat = [i for g in t2["physics"] for i in g["card_ids"]]
        self.assertEqual(sorted(flat), sorted(r["id"] for r in ROWS),
                         "缓存路径下覆盖仍须完整")


class TestChairMerge(unittest.TestCase):
    """主席输出覆盖率校验:丢卡回填、越界 id 剔除、未知组键拒收、重复归组去重。"""

    def setUp(self):
        self.cards = ROWS
        self.base = cc.baseline_taxonomy(self.cards)

    def _merge(self, chair):
        return cc._merge_chair(chair, self.base, self.cards)

    def test_missing_ids_backfilled_from_baseline(self):
        merged = self._merge({
            "layers": [{"key": "dialog", "summary": "s",
                        "card_ids": ["kc-flow"]}],
            "physics": [{"key": "other", "summary": "s",
                         "card_ids": ["kc-unknown"]}],
            "note": "n"})
        for axis in ("layers", "physics"):
            flat = [i for g in merged[axis] for i in g["card_ids"]]
            self.assertEqual(sorted(flat), sorted(r["id"] for r in self.cards))

    def test_forged_and_unknown_ids_rejected(self):
        merged = self._merge({
            "layers": [{"key": "dialog", "card_ids":
                        ["kc-flow", "kc-forged", 42]}],
            "physics": [{"key": "thermo_energy", "card_ids": ["kc-monitor"]}]})
        flat = [i for g in merged["layers"] for i in g["card_ids"]]
        self.assertNotIn("kc-forged", flat)
        self.assertNotIn(42, flat)

    def test_unknown_group_key_rejected(self):
        merged = self._merge({
            "layers": [{"key": "bogus_layer", "card_ids": ["kc-flow"]}],
            "physics": [{"key": "bogus_phys", "card_ids": ["kc-monitor"]}]})
        self.assertFalse([g for g in merged["layers"] if g["key"] == "bogus_layer"])
        self.assertFalse([g for g in merged["physics"] if g["key"] == "bogus_phys"])
        for axis in ("layers", "physics"):  # 回填后仍全覆盖
            flat = [i for g in merged[axis] for i in g["card_ids"]]
            self.assertEqual(len(flat), len(self.cards))

    def test_duplicate_assignment_kept_once(self):
        merged = self._merge({
            "layers": [{"key": "dialog", "card_ids": ["kc-flow"]},
                       {"key": "monitor", "card_ids": ["kc-flow",
                                                       "kc-monitor"]}],
            "physics": [{"key": "other", "card_ids":
                         ["kc-flow", "kc-unknown"]}]})
        flat = [i for g in merged["layers"] for i in g["card_ids"]]
        self.assertEqual(flat.count("kc-flow"), 1, "跨组重复只留首见")

    def test_chair_group_absent_in_baseline_kept(self):
        # 基线里空的组(如 numerical_stability)被主席用到时不得整组丢弃
        # ——实跑曾因此蒸发 2 张卡(覆盖不全)
        merged = self._merge({
            "layers": [{"key": "dialog", "card_ids": ["kc-flow"]}],
            "physics": [{"key": "numerical_stability",
                         "card_ids": ["kc-unknown"]}]})
        keys = [g["key"] for g in merged["physics"]]
        self.assertIn("numerical_stability", keys)
        flat = [i for g in merged["physics"] for i in g["card_ids"]]
        self.assertIn("kc-unknown", flat)

    def test_role_selected_indices_normalized(self):
        # 角色发言按序号:越界序号/字符串 id 一律剔除,只留合法序号映射
        out = cc._role_call_with(cc.ROLES[0], self.cards,
                                 {"selected": [1, 99, "kc-flow", None],
                                  "summary": "x"})
        self.assertEqual(out["selected"], [self.cards[0]["id"]])


if __name__ == "__main__":
    unittest.main()
