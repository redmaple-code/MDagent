# -*- coding: utf-8 -*-
"""演化机制回归门:diff 应用器、POLICY 门禁、自动回滚、观测层复发检测。

沙盒策略:测试用临时 root 与打补丁后的 POLICY 副本,绝不碰真实
agent_deploy_kit / dsh-agent 文件。运行: python3.11 -m unittest discover -s tests -v
"""
import importlib
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
EVO_DIR = os.path.join(os.path.dirname(HERE), "evolution")
if EVO_DIR not in sys.path:
    sys.path.insert(0, EVO_DIR)

import apply_proposal  # noqa: E402
import behavior_strategies as bstrat  # noqa: E402
import memory  # noqa: E402
import observe  # noqa: E402
import specialist_templates  # noqa: E402
import specialist_sandbox  # noqa: E402


def make_proposal(pdir, meta, diff_text):
    os.makedirs(pdir, exist_ok=True)
    json.dump(meta, open(os.path.join(pdir, "meta.json"), "w"))
    for name in ("proposal.md", "evidence.md"):
        open(os.path.join(pdir, name), "w").write("# t\n")
    open(os.path.join(pdir, "diff.patch"), "w").write(diff_text)


DIFF_MODIFY = """--- a/target.py
+++ b/target.py
@@ -1,3 +1,3 @@
 alpha
-beta = 1
+beta = 2
 gamma
"""

DIFF_NEW_FILE = """--- /dev/null
+++ b/created.py
@@ -0,0 +1,2 @@
+one
+two
"""

DIFF_DELETE = """--- a/target.py
+++ /dev/null
@@ -1,3 +1,0 @@
-alpha
-beta = 1
-gamma
"""


class TestUnifiedDiff(unittest.TestCase):
    def test_modify(self):
        files = apply_proposal.parse_unified_diff(DIFF_MODIFY)
        self.assertEqual(files[0]["old"], "target.py")
        out = apply_proposal.apply_hunks(["alpha", "beta = 1", "gamma"], files[0]["hunks"], "t")
        self.assertEqual(out, ["alpha", "beta = 2", "gamma"])

    def test_new_file(self):
        files = apply_proposal.parse_unified_diff(DIFF_NEW_FILE)
        self.assertIsNone(files[0]["old"])
        self.assertEqual(files[0]["new"], "created.py")
        out = apply_proposal.apply_hunks([], files[0]["hunks"], "t")
        self.assertEqual(out, ["one", "two"])

    def test_delete_file(self):
        files = apply_proposal.parse_unified_diff(DIFF_DELETE)
        self.assertIsNone(files[0]["new"])
        out = apply_proposal.apply_hunks(["alpha", "beta = 1", "gamma"], files[0]["hunks"], "t")
        self.assertEqual(out, [])

    def test_context_mismatch_raises(self):
        files = apply_proposal.parse_unified_diff(DIFF_MODIFY)
        with self.assertRaises(ValueError):
            apply_proposal.apply_hunks(["alpha", "WRONG", "gamma"], files[0]["hunks"], "t")


class SandboxCase(unittest.TestCase):
    """临时 root + 打补丁 POLICY;每个用例独立沙盒。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.kit = os.path.join(self.tmp, "kit")
        os.makedirs(os.path.join(self.kit, "agents", "runtime", "v3.3"))
        os.makedirs(os.path.join(self.kit, "tests"))
        self.target_rel = "agents/runtime/v3.3/monitor_agent.py"
        with open(os.path.join(self.kit, self.target_rel), "w") as f:
            f.write("SCHEDULE = 300\n")
        # 默认回归命令恒绿;需要变绿的用例自行覆盖
        self.policy = {
            "roots": {"kit": {"path": self.kit,
                              "test_commands": [["python3", "-c", "pass"]]}},
            "auto_fix_classes": {
                "monitor_schedule_temp": {"root": "kit", "paths": [self.target_rel],
                                          "max_changed_lines": 4}},
            "human_review_classes": ["monitor_logic"],
            "forbidden_paths": ["agents/runtime/v3.3/claude_interface.py",
                                "evolution/POLICY.json", "tests/"],
            "rollback": {"backups_dir": "backups"},
        }
        self.old_policy = apply_proposal.POLICY
        self.old_ledger = apply_proposal.LEDGER
        self.old_backups = apply_proposal.BACKUPS_BASE
        apply_proposal.POLICY = self.policy
        apply_proposal.LEDGER = os.path.join(self.tmp, "LEDGER.jsonl")
        apply_proposal.BACKUPS_BASE = self.tmp
        # 批准回执桥沙盒:APPLIED 会投 kit 收件箱销项,绝不碰真 inbox
        self.addCleanup(setattr, apply_proposal, "KIT_GATE_INBOX", apply_proposal.KIT_GATE_INBOX)
        apply_proposal.KIT_GATE_INBOX = os.path.join(self.tmp, "user_gate_requests.jsonl")
        self.addCleanup(self._restore_module)

    def _restore_module(self):
        apply_proposal.POLICY = self.old_policy
        apply_proposal.LEDGER = self.old_ledger
        apply_proposal.BACKUPS_BASE = self.old_backups

    def _run_apply(self, pdir, *extra):
        old_argv = sys.argv
        sys.argv = ["apply_proposal.py", pdir, *extra]
        try:
            return apply_proposal.main()
        finally:
            sys.argv = old_argv

    def _ledger_rows(self):
        if not os.path.exists(apply_proposal.LEDGER):
            return []
        return [json.loads(ln) for ln in open(apply_proposal.LEDGER) if ln.strip()]

    def _write_test_suite(self, expect_content):
        """写一个校验 target 内容的 unittest;patch 后内容不符即红。"""
        test_src = (
            "import unittest, os\n"
            "class T(unittest.TestCase):\n"
            "    def test_content(self):\n"
            "        p = os.path.join(os.path.dirname(__file__), '..', %r)\n"
            "        assert open(p).read() == %r\n" % (self.target_rel, expect_content))
        with open(os.path.join(self.kit, "tests", "test_guard.py"), "w") as f:
            f.write(test_src)
        self.policy["roots"]["kit"]["test_commands"] = [
            [sys.executable, "-m", "unittest", "discover", "-s", "tests"]]


class TestPolicyGate(SandboxCase):
    DIFF = ("--- a/%s\n+++ b/%s\n@@ -1 +1 @@\n-SCHEDULE = 300\n+SCHEDULE = 310\n"
            % ("agents/runtime/v3.3/monitor_agent.py", "agents/runtime/v3.3/monitor_agent.py"))

    def test_forbidden_path_rejected(self):
        diff = ("--- a/agents/runtime/v3.3/claude_interface.py\n"
                "+++ b/agents/runtime/v3.3/claude_interface.py\n@@ -1 +1 @@\n-x\n+y\n")
        pdir = os.path.join(self.tmp, "p1")
        make_proposal(pdir, {"root": "kit", "class": "monitor_schedule_temp", "tier": "auto"}, diff)
        rc = self._run_apply(pdir)
        self.assertEqual(rc, 2)
        self.assertEqual(self._ledger_rows()[-1]["result"], "REJECTED")
        self.assertIn("红线", self._ledger_rows()[-1]["reason"])

    def test_auto_apply_and_rollback(self):
        pdir = os.path.join(self.tmp, "p2")
        make_proposal(pdir, {"root": "kit", "class": "monitor_schedule_temp", "tier": "auto"},
                      self.DIFF)
        rc = self._run_apply(pdir)
        self.assertEqual(rc, 0)
        self.assertEqual(open(os.path.join(self.kit, self.target_rel)).read(), "SCHEDULE = 310\n")
        row = self._ledger_rows()[-1]
        self.assertEqual(row["result"], "AUTO_APPLIED")
        old_argv = sys.argv
        sys.argv = ["apply_proposal.py", "--rollback", row["id"]]
        try:
            rc = apply_proposal.main()
        finally:
            sys.argv = old_argv
        self.assertEqual(rc, 0)
        self.assertEqual(open(os.path.join(self.kit, self.target_rel)).read(), "SCHEDULE = 300\n")

    def test_human_tier_requires_approver(self):
        pdir = os.path.join(self.tmp, "p3")
        make_proposal(pdir, {"root": "kit", "class": "monitor_logic", "tier": "human"}, self.DIFF)
        self.assertEqual(self._run_apply(pdir), 2, "无 --approver 的 Tier2 必须拒")
        pdir4 = os.path.join(self.tmp, "p4")
        make_proposal(pdir4, {"root": "kit", "class": "monitor_logic", "tier": "human"}, self.DIFF)
        self.assertEqual(self._run_apply(pdir4, "--approver", "user"), 0)
        self.assertEqual(self._ledger_rows()[-1]["approver"], "user")

    def test_auto_bounds_enforced(self):
        big = ("--- a/%s\n+++ b/%s\n@@ -1 +1,6 @@\n SCHEDULE = 300\n" % (self.target_rel, self.target_rel)
               + "+a\n+b\n+c\n+d\n+e\n")
        pdir = os.path.join(self.tmp, "p5")
        make_proposal(pdir, {"root": "kit", "class": "monitor_schedule_temp", "tier": "auto"}, big)
        self.assertEqual(self._run_apply(pdir), 2, "超 max_changed_lines 必须拒")

    def test_red_tests_roll_back(self):
        self._write_test_suite(expect_content="SCHEDULE = 300\n")  # patch 后内容变了 → 红
        pdir = os.path.join(self.tmp, "p6")
        make_proposal(pdir, {"root": "kit", "class": "monitor_schedule_temp", "tier": "auto"},
                      self.DIFF)
        rc = self._run_apply(pdir)
        self.assertEqual(rc, 3)
        self.assertEqual(self._ledger_rows()[-1]["result"], "ROLLED_BACK")
        self.assertEqual(open(os.path.join(self.kit, self.target_rel)).read(), "SCHEDULE = 300\n",
                         "回归变红后文件必须还原")

    def test_path_traversal_rejected(self):
        for evil in ("../../etc/passwd", "/etc/passwd"):
            diff = ("--- a/%s\n+++ b/%s\n@@ -1 +1 @@\n-x\n+y\n" % (evil, evil))
            pdir = os.path.join(self.tmp, "evil")
            make_proposal(pdir, {"root": "kit", "class": "monitor_logic", "tier": "human"}, diff)
            rc = self._run_apply(pdir, "--approver", "user")
            self.assertEqual(rc, 2, "路径逃逸必须拒: %s" % evil)
            self.assertIn("越界", self._ledger_rows()[-1]["reason"])

    def test_gnu_diff_tab_header_accepted(self):
        diff = ("--- a/%s\t2026-08-27 01:00:00 +0800\n"
                "+++ b/%s\t2026-08-27 02:00:00 +0800\n"
                "@@ -1 +1 @@\n-SCHEDULE = 300\n+SCHEDULE = 310\n"
                % (self.target_rel, self.target_rel))
        pdir = os.path.join(self.tmp, "p7")
        make_proposal(pdir, {"root": "kit", "class": "monitor_schedule_temp", "tier": "auto"}, diff)
        self.assertEqual(self._run_apply(pdir), 0, "GNU diff -u 的 tab 时间戳头应可用")

    def test_hunk_count_mismatch_rejected(self):
        diff = ("--- a/%s\n+++ b/%s\n@@ -1,3 +1,3 @@\n-SCHEDULE = 300\n+SCHEDULE = 310\n"
                % (self.target_rel, self.target_rel))  # 声明 3 行实际各 1 行
        pdir = os.path.join(self.tmp, "p8")
        make_proposal(pdir, {"root": "kit", "class": "monitor_schedule_temp", "tier": "auto"}, diff)
        rc = self._run_apply(pdir)
        self.assertEqual(rc, 2)
        self.assertIn("不符", self._ledger_rows()[-1]["reason"])

    def test_double_rollback_rejected(self):
        pdir = os.path.join(self.tmp, "p9")
        make_proposal(pdir, {"root": "kit", "class": "monitor_schedule_temp", "tier": "auto"},
                      self.DIFF)
        self.assertEqual(self._run_apply(pdir), 0)
        lid = self._ledger_rows()[-1]["id"]
        old_argv = sys.argv
        try:
            sys.argv = ["apply_proposal.py", "--rollback", lid]
            self.assertEqual(apply_proposal.main(), 0)
            sys.argv = ["apply_proposal.py", "--rollback", lid]
            self.assertEqual(apply_proposal.main(), 2, "同一笔不得重复回滚")
        finally:
            sys.argv = old_argv


class TestObserve(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.old_fg, self.old_pipe, self.old_prop = observe.FLOWGATE_LOG, observe.PIPELINE_DIR, observe.PROPOSALS
        observe.FLOWGATE_LOG = os.path.join(self.tmp, "_flowgate.jsonl")
        observe.PIPELINE_DIR = self.tmp
        observe.PROPOSALS = os.path.join(self.tmp, "proposals")
        # 事件流同样沙盒,否则读到真实 events/pipeline.jsonl
        self.old_pev = observe.PIPELINE_EVENTS
        observe.PIPELINE_EVENTS = os.path.join(self.tmp, "pipeline-events.jsonl")
        # friction 信号流同样沙盒(2026-09-09 经验沉淀)
        self.old_fric = observe.FRICTION_LOG
        observe.FRICTION_LOG = os.path.join(self.tmp, "friction.jsonl")
        # 用户闸桥/observe 闸同样沙盒(2026-09-07 KB_PATH 同型教训)
        self.addCleanup(setattr, observe, "KIT_GATE_INBOX", observe.KIT_GATE_INBOX)
        observe.KIT_GATE_INBOX = os.path.join(self.tmp, "user_gate_requests.jsonl")
        self.addCleanup(setattr, observe, "OBSERVE_GATE_FILE", observe.OBSERVE_GATE_FILE)
        observe.OBSERVE_GATE_FILE = os.path.join(self.tmp, "observe_gate.json")
        # 只读兜底也要指向空目录,否则测试会读到真实 mcp/_pipeline 快照
        self.old_legacy = observe.LEGACY_MCP_DIR
        observe.LEGACY_MCP_DIR = os.path.join(self.tmp, "legacy-empty")
        # kit 策略 db 指向不存在路径:observe_kit 静默跳过,用例不依赖真实 db 状态
        self.old_kdp = observe._kit_db_path
        observe._kit_db_path = lambda: os.path.join(self.tmp, "no-kit-db.json")
        # 训练记忆全部重定向到沙盒,绝不动真实 experience/ 与 LEDGER
        self.old_mem = (memory.EXP_DIR, memory.ATOMS, memory.SCENARIOS,
                        memory.SOP, memory.LEDGER)
        memory.EXP_DIR = os.path.join(self.tmp, "experience")
        memory.ATOMS = os.path.join(memory.EXP_DIR, "atoms.jsonl")
        memory.SCENARIOS = os.path.join(memory.EXP_DIR, "scenarios.json")
        memory.SOP = os.path.join(memory.EXP_DIR, "SOP.md")
        memory.LEDGER = os.path.join(self.tmp, "LEDGER.jsonl")
        # 蜂群 KB 沙盒:canary 横扫会写 canary_outcome 剧集,绝不碰真 repair_kb.jsonl
        self.old_kb = specialist_swarm.KB_PATH
        specialist_swarm.KB_PATH = os.path.join(self.tmp, "repair_kb.jsonl")
        # 行为策略库/persona 同样沙盒(2026-09-17):auto 蒸馏会写 STORE 并渲染
        # persona 两处,绝不碰真实 behavior_strategies.json 与 persona 文件
        self.old_bs = (bstrat.STORE, bstrat.PERSONA_FILES)
        bstrat.STORE = os.path.join(self.tmp, "behavior_strategies.json")
        pa = os.path.join(self.tmp, "pa.yml")
        pb = os.path.join(self.tmp, "pb.yml")
        with open(pa, "w") as f:
            f.write("- id: persona\n  config:\n    text: >-\n      【人格正文】\n- id: next\n")
        with open(pb, "w") as f:
            f.write("- id: system-prompt\n  config:\n    persona: >-\n      【人格正文】\n- id: next\n")
        bstrat.PERSONA_FILES = [(pa, "text"), (pb, "persona")]
        self.addCleanup(self._restore)

    def _restore(self):
        observe.FLOWGATE_LOG, observe.PIPELINE_DIR, observe.PROPOSALS = self.old_fg, self.old_pipe, self.old_prop
        observe.PIPELINE_EVENTS = self.old_pev
        observe.FRICTION_LOG = self.old_fric
        observe.LEGACY_MCP_DIR = self.old_legacy
        specialist_swarm.KB_PATH = self.old_kb
        observe._kit_db_path = self.old_kdp
        bstrat.STORE, bstrat.PERSONA_FILES = self.old_bs
        (memory.EXP_DIR, memory.ATOMS, memory.SCENARIOS,
         memory.SOP, memory.LEDGER) = self.old_mem

    def test_flowgate_recurrence_drafts_proposal(self):
        now_ms = int(__import__("time").time() * 1000)
        with open(os.path.join(self.tmp, "_flowgate.jsonl"), "w") as f:
            for i in range(3):
                f.write(json.dumps({"t": now_ms - i * 1000, "kind": "flow-gate-violation",
                                    "userText": "模拟状态?"}) + "\n")
        summary = observe.observe(days=7, min_occurrences=3)
        self.assertEqual(len(summary["drafted"]), 1)
        self.assertLess(summary["health"], 1.0)
        meta = json.load(open(os.path.join(summary["drafted"][0], "meta.json")))
        self.assertEqual(meta["class"], "flow_gate_intent_terms")
        self.assertEqual(meta["tier"], "auto")
        # RSI 引用与训练记忆溯源应落进 evidence.md
        ev = open(os.path.join(summary["drafted"][0], "evidence.md"), encoding="utf-8").read()
        self.assertIn("RSI 参考", ev)

    def test_below_threshold_no_draft(self):
        now_ms = int(__import__("time").time() * 1000)
        with open(os.path.join(self.tmp, "_flowgate.jsonl"), "w") as f:
            f.write(json.dumps({"t": now_ms, "kind": "flow-gate-violation"}) + "\n")
        summary = observe.observe(days=7, min_occurrences=3)
        self.assertEqual(summary["drafted"], [])
        self.assertEqual(summary["health"], 0.5)  # 1 次违规: 1/(1+1)

    def test_conversation_failure_signals_draft_human_proposal(self):
        now_ms = int(__import__("time").time() * 1000)
        with open(os.path.join(self.tmp, "_flowgate.jsonl"), "w") as f:
            for i in range(3):
                f.write(json.dumps({"t": now_ms - i * 1000, "kind": "repeat-question",
                                    "userText": "到底用什么力场?",
                                    "previousText": "用什么力场?"}) + "\n")
        summary = observe.observe(days=7, min_occurrences=3)
        self.assertEqual(len(summary["drafted"]), 1)
        meta = json.load(open(os.path.join(summary["drafted"][0], "meta.json")))
        self.assertEqual(meta["class"], "persona_wording")
        # 2026-09-17 去人工化:行为复查类 tier=auto,自动蒸馏进行为策略库
        # (规则渲染进 persona 两处),不再开人工闸;后验连升才撤回叫人
        self.assertEqual(meta["tier"], "auto")
        db = bstrat._load()
        strat = [s for s in db["strategies"] if s["signature"] == "repeat-question"]
        self.assertEqual(len(strat), 1, "repeat-question 应自动蒸馏出一条策略")
        self.assertEqual(strat[0]["status"], "active")
        self.assertEqual(strat[0]["source"], "auto-distill")
        self.assertFalse(os.path.exists(observe.KIT_GATE_INBOX),
                         "auto 通道不得再开人工闸")
        kinds = {p["kind"] for p in summary["patterns"]}
        self.assertIn("repeat-question", kinds)

    def test_friction_signals_draft_human_proposals(self):
        # 经验沉淀(2026-09-09):friction.jsonl 轮级软失败信号 ——
        # 打断/工具错误/否决各攒够 min_occurrences 各自晋升提案;
        # 2026-09-17 起 tier=auto 自动蒸馏(去人工化),不再开人工闸
        now_ms = int(__import__("time").time() * 1000)
        with open(os.path.join(self.tmp, "friction.jsonl"), "w") as f:
            for i in range(3):
                f.write(json.dumps({"t": now_ms - i * 1000, "kind": "turn-friction",
                                    "session": "s1", "turn": i + 1, "reason": "aborted",
                                    "interrupts": 1, "tool_errors": 1, "denials": 1,
                                    "retries": 0, "tools": 4,
                                    "failed_tools": ["TIMEOUT"]}) + "\n")
        summary = observe.observe(days=7, min_occurrences=3)
        self.assertEqual(len(summary["drafted"]), 3)
        for d in summary["drafted"]:
            meta = json.load(open(os.path.join(d, "meta.json")))
            self.assertEqual(meta["tier"], "auto")
        db = bstrat._load()
        sigs = {s["signature"] for s in db["strategies"] if s["status"] == "active"}
        self.assertEqual(sigs, {"friction-interrupt", "friction-tool-error",
                                "friction-denial"},
                         "三类软失败应各自动蒸馏出一条 active 策略")
        # persona 两处渲染且逐字一致(程序保证的原人肉红线)
        for path, _key in bstrat.PERSONA_FILES:
            txt = open(path, encoding="utf-8").read()
            self.assertIn(bstrat.MARK_BEGIN, txt)
            self.assertIn("长回复分段", txt)
        self.assertFalse(os.path.exists(observe.KIT_GATE_INBOX),
                         "auto 通道不得再开人工闸")
        kinds = {p["kind"] for p in summary["patterns"]}
        self.assertIn("friction-interrupt", kinds)
        self.assertIn("friction-tool-error", kinds)
        self.assertIn("friction-denial", kinds)

    def test_friction_clean_turns_no_draft(self):
        # 无软失败的干净轮(纯 retries 不计入三类签名)不得触发任何 friction 提案
        now_ms = int(__import__("time").time() * 1000)
        with open(os.path.join(self.tmp, "friction.jsonl"), "w") as f:
            for i in range(5):
                f.write(json.dumps({"t": now_ms - i * 1000, "kind": "turn-friction",
                                    "session": "s1", "turn": i + 1, "reason": "completed",
                                    "interrupts": 0, "tool_errors": 0, "denials": 0,
                                    "retries": 2, "tools": 3, "failed_tools": []}) + "\n")
        summary = observe.observe(days=7, min_occurrences=3)
        self.assertEqual(summary["drafted"], [])
        zero = {p["kind"]: p["count"] for p in summary["patterns"]
                if p["kind"].startswith("friction-")}
        self.assertEqual(zero, {"friction-interrupt": 0, "friction-tool-error": 0,
                                "friction-denial": 0})

    def test_record_atom_concurrent_dedup_same_event(self):
        # 多线程并发录同一逻辑事件(同 kind/fact/refs):文件锁串行化后
        # check-then-act 幂等必须成立,只入账 1 条(五轮对抗:跨进程竞态曾重复入账)
        import threading
        os.makedirs(memory.EXP_DIR, exist_ok=True)
        threads = [threading.Thread(target=memory.record_atom,
                                    args=("dup", "same-fact", ["src-1"]))
                   for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        rows = [json.loads(ln) for ln in open(memory.ATOMS, encoding="utf-8")]
        self.assertEqual(len(rows), 1, "同一事件并发入账必须幂等: %d 条" % len(rows))

    def test_rebuild_coerces_non_str_atom_id(self):
        # atoms.jsonl 里 id 写成数字:下游 ",".join(atom_ids) 会 TypeError(五轮对抗)
        os.makedirs(memory.EXP_DIR, exist_ok=True)
        with open(memory.ATOMS, "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": 123, "t": 1, "kind": "k", "fact": "f"}) + "\n")
        memory.rebuild()
        scenarios = json.load(open(memory.SCENARIOS, encoding="utf-8"))
        self.assertEqual(scenarios[0]["atom_ids"], ["123"])

    def test_observe_tolerates_corrupt_scenarios(self):
        # 损坏的 scenarios.json(缺 atom_ids/count):find_scenario 命中后
        # 旧实现 past["atom_ids"] 直接 KeyError 打崩 observe(五轮对抗)
        now_ms = int(__import__("time").time() * 1000)
        with open(os.path.join(self.tmp, "_flowgate.jsonl"), "w") as f:
            for i in range(3):
                f.write(json.dumps({"t": now_ms - i * 1000, "kind": "flow-gate-violation",
                                    "userText": "模拟状态?"}) + "\n")
        os.makedirs(memory.EXP_DIR, exist_ok=True)
        with open(memory.SCENARIOS, "w", encoding="utf-8") as f:
            json.dump([{"signature": "flow-gate-violation"}], f)  # 缺 atom_ids/count
        summary = observe.observe(days=7, min_occurrences=3)
        self.assertEqual(len(summary["drafted"]), 1)

    def test_observe_days_zero_clamped(self):
        # days=0 窗口为空连当前事件都被排除,与"今天"语义不符:钳位 ≥1(五轮对抗)
        now_ms = int(__import__("time").time() * 1000)
        with open(os.path.join(self.tmp, "_flowgate.jsonl"), "w") as f:
            for i in range(3):
                f.write(json.dumps({"t": now_ms - i * 1000, "kind": "flow-gate-violation",
                                    "userText": "模拟状态?"}) + "\n")
        summary = observe.observe(days=0, min_occurrences=3)
        kinds = {p["kind"] for p in summary["patterns"]}
        self.assertIn("flow-gate-violation", kinds)

    def test_malformed_snapshots_no_crash(self):
        # legacy 兜底快照合法 JSON 但非 dict(字符串/数字):旧实现 .get 直接
        # AttributeError 打崩 observe(四轮对抗);事件流同理(坏行/非 dict/缺 ts)
        legacy_pipe = os.path.join(observe.LEGACY_MCP_DIR, "_pipeline")
        os.makedirs(legacy_pipe)
        for name, payload in (("snap_a.json", '"just a string"'),
                              ("snap_b.json", "123"),
                              ("snap_c.json", json.dumps(
                                  {"status": "PIPELINE_ERROR", "case_id": "c",
                                   "error": 12345}))):
            with open(os.path.join(legacy_pipe, name), "w") as f:
                f.write(payload)
        with open(observe.PIPELINE_EVENTS, "w") as f:
            f.write("not json\n")
            f.write('"bare string"\n')
            f.write(json.dumps({"status": "PIPELINE_ERROR", "case_id": "e1"}) + "\n")  # 缺 ts
            f.write(json.dumps({"ts": "bad", "status": "CRITICAL", "case_id": "e2"}) + "\n")
        summary = observe.observe(days=7, min_occurrences=3)
        self.assertIn("patterns", summary)
        # 缺/坏 ts 的事件被丢弃;legacy snap_c 的 PIPELINE_ERROR 仍兜底入账
        kinds = {p["kind"]: p["count"] for p in summary["patterns"]}
        self.assertEqual(kinds.get("pipeline_error"), 1)

    def test_pipeline_events_overwrite_survival(self):
        # 核心回归锚(2026-09-05):覆盖式快照把 CRITICAL 冲掉后,事件流仍计入失效。
        # 模拟:case_a 先 CRITICAL 后恢复 RUNNING(快照被覆盖),事件流两条都在。
        now = __import__("time").time()
        with open(observe.PIPELINE_EVENTS, "w") as f:
            f.write(json.dumps({"ts": now - 100, "case_id": "case_a", "status": "CRITICAL",
                                "risk_score": 88, "risk_factors": ["T_SPIKE"]}) + "\n")
            f.write(json.dumps({"ts": now - 50, "case_id": "case_a", "status": "RUNNING",
                                "step": 99000}) + "\n")
            for cid in ("case_b", "case_c"):
                f.write(json.dumps({"ts": now - 10, "case_id": cid, "status": "CRITICAL",
                                    "risk_score": 90, "risk_factors": ["T_SPIKE"]}) + "\n")
        summary = observe.observe(days=7, min_occurrences=3)
        kinds = {p["kind"]: p["count"] for p in summary["patterns"]}
        self.assertEqual(kinds.get("monitor_critical"), 3,
                         "被 RUNNING 覆盖的 CRITICAL 也必须从事件流计入")
        metas = [json.load(open(os.path.join(d, "meta.json"))) for d in summary["drafted"]]
        self.assertIn("monitor_logic", {m["class"] for m in metas})

    def test_pipeline_events_distinct_case_semantics(self):
        # 面状语义锚:同 case 窗内反复 CRITICAL 计一例,不算复发次数;
        # 但同 case 的反复 PIPELINE_ERROR 也不堆计数——distinct case 口径不变。
        now = __import__("time").time()
        with open(observe.PIPELINE_EVENTS, "w") as f:
            for i in range(6):
                f.write(json.dumps({"ts": now - i * 60, "case_id": "same_case",
                                    "status": "PIPELINE_ERROR", "error": "boom %d" % i}) + "\n")
        summary = observe.observe(days=7, min_occurrences=3)
        kinds = {p["kind"]: p["count"] for p in summary["patterns"]}
        self.assertEqual(kinds.get("pipeline_error"), 1, "同 case 窗内 N 次错误计一例")
        self.assertEqual(summary["drafted"], [], "单 case 复发不达 ≥3 例阈值不起草")

    def test_pipeline_events_window_expiry(self):
        # 窗口外事件不计入(与 flowgate 同口径)
        now = __import__("time").time()
        old = now - 8 * 86400
        with open(observe.PIPELINE_EVENTS, "w") as f:
            for i, cid in enumerate(("old_a", "old_b", "old_c")):
                f.write(json.dumps({"ts": old - i, "case_id": cid, "status": "CRITICAL",
                                    "risk_score": 90}) + "\n")
        summary = observe.observe(days=7, min_occurrences=3)
        kinds = {p["kind"]: p["count"] for p in summary["patterns"]}
        self.assertEqual(kinds.get("monitor_critical"), 0)

    def test_record_atom_concurrent_unique_ids(self):
        # 30 线程同毫秒写入:id 毫秒撞号曾实测仅 21 唯一(四轮对抗),
        # uuid 短缀后必须 30/30 唯一
        import threading
        os.makedirs(memory.EXP_DIR, exist_ok=True)
        threads = [threading.Thread(target=memory.record_atom,
                                    args=("stress", "fact-%d" % i))
                   for i in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        rows = [json.loads(ln) for ln in open(memory.ATOMS, encoding="utf-8")]
        self.assertEqual(len(rows), 30)
        self.assertEqual(len({r["id"] for r in rows}), 30)

    def test_atoms_and_sop_rebuilt(self):
        now_ms = int(__import__("time").time() * 1000)
        with open(os.path.join(self.tmp, "_flowgate.jsonl"), "w") as f:
            for i in range(2):
                f.write(json.dumps({"t": now_ms - i * 1000, "kind": "user-correction",
                                    "userText": "不对,重答"}) + "\n")
        observe.observe(days=7, min_occurrences=3)
        atoms = [json.loads(ln) for ln in open(memory.ATOMS, encoding="utf-8")]
        self.assertEqual(len(atoms), 2)
        self.assertEqual(atoms[0]["kind"], "user-correction")
        scenarios = json.load(open(memory.SCENARIOS, encoding="utf-8"))
        self.assertEqual(scenarios[0]["count"], 2)
        sop = open(memory.SOP, encoding="utf-8").read()
        self.assertIn("user-correction", sop)
        self.assertIn("尚无生效修复", sop)
        self.assertIsNotNone(memory.find_scenario("user-correction"))

    def test_draft_proposal_existing_dir_returns_none(self):
        # exists 检查与 makedirs 之间的竞态:并发起草同日同 slug 时
        # 旧实现 FileExistsError 打崩调用方;现按"已存在"返回 None(八轮对抗)
        import time as _t
        pdir = os.path.join(observe.PROPOSALS,
                            "%s-race-slug" % _t.strftime("%Y-%m-%d"))
        os.makedirs(pdir)
        self.assertIsNone(observe.draft_proposal(
            "race-slug", "pipeline_steps", "auto", self.tmp, "t", []))

    def test_rebuild_atomic_write_no_tmp_residue(self):
        # rebuild 的 SCENARIOS/SOP 全量写必须 tmp+os.replace:
        # 崩溃/并发读者只见完整旧版或完整新版,且不留 .tmp 残骸(八轮对抗)
        os.makedirs(memory.EXP_DIR, exist_ok=True)
        with open(memory.ATOMS, "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": "a1", "t": 1, "kind": "k", "fact": "f"}) + "\n")
        memory.rebuild()
        leftovers = [n for n in os.listdir(memory.EXP_DIR) if ".tmp." in n]
        self.assertEqual(leftovers, [], "原子写不得留 tmp 残骸: %s" % leftovers)
        scenarios = json.load(open(memory.SCENARIOS, encoding="utf-8"))
        self.assertEqual(scenarios[0]["signature"], "k")
        self.assertIn("## k", open(memory.SOP, encoding="utf-8").read())


class TestObserveKit(unittest.TestCase):
    """kit 策略库接入(2026-08-30):连败检测、atoms 入账、提案 diff 可应用。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # observe/memory 沙盒(口径同 TestObserve)
        self.old = (observe.FLOWGATE_LOG, observe.PIPELINE_DIR, observe.PROPOSALS,
                    observe.LEGACY_MCP_DIR, observe._kit_db_path, observe.PIPELINE_EVENTS)
        observe.FLOWGATE_LOG = os.path.join(self.tmp, "none.jsonl")
        observe.PIPELINE_DIR = os.path.join(self.tmp, "none-dir")
        observe.PIPELINE_EVENTS = os.path.join(self.tmp, "none-pipeline.jsonl")
        # friction 信号流同样沙盒(2026-09-13 补:真实 friction.jsonl 有活会话轮记录,
        # 不隔离会让 observe 额外起草 friction 提案,drafted 计数被真数据污染)
        self.addCleanup(setattr, observe, "FRICTION_LOG", observe.FRICTION_LOG)
        observe.FRICTION_LOG = os.path.join(self.tmp, "friction.jsonl")
        # 用户闸桥/observe 闸同样沙盒:human 提案起草会投 kit 收件箱,绝不碰真 inbox
        # (2026-09-07 KB_PATH 同型教训;addCleanup 注册时捕获旧值,免改 _restore)
        self.addCleanup(setattr, observe, "KIT_GATE_INBOX", observe.KIT_GATE_INBOX)
        observe.KIT_GATE_INBOX = os.path.join(self.tmp, "user_gate_requests.jsonl")
        self.addCleanup(setattr, observe, "OBSERVE_GATE_FILE", observe.OBSERVE_GATE_FILE)
        observe.OBSERVE_GATE_FILE = os.path.join(self.tmp, "observe_gate.json")
        observe.PROPOSALS = os.path.join(self.tmp, "proposals")
        observe.LEGACY_MCP_DIR = os.path.join(self.tmp, "none-legacy")
        self.db_path = os.path.join(self.tmp, ".v3.3_strategy_db.json")
        observe._kit_db_path = lambda: self.db_path
        # _proposal_applied 读 paths.memory_dir()/LEDGER.jsonl:一并沙盒,绝不碰真 LEDGER
        self.old_memory_dir = observe.paths.memory_dir
        observe.paths.memory_dir = lambda: self.tmp
        # 蜂群 KB 沙盒:canary 横扫会写 canary_outcome 剧集,绝不碰真 repair_kb.jsonl
        self.old_kb = specialist_swarm.KB_PATH
        specialist_swarm.KB_PATH = os.path.join(self.tmp, "repair_kb.jsonl")
        self.old_mem = (memory.EXP_DIR, memory.ATOMS, memory.SCENARIOS,
                        memory.SOP, memory.LEDGER)
        memory.EXP_DIR = os.path.join(self.tmp, "experience")
        memory.ATOMS = os.path.join(memory.EXP_DIR, "atoms.jsonl")
        memory.SCENARIOS = os.path.join(memory.EXP_DIR, "scenarios.json")
        memory.SOP = os.path.join(memory.EXP_DIR, "SOP.md")
        memory.LEDGER = os.path.join(self.tmp, "LEDGER.jsonl")
        self.addCleanup(self._restore)

    def _restore(self):
        (observe.FLOWGATE_LOG, observe.PIPELINE_DIR, observe.PROPOSALS,
         observe.LEGACY_MCP_DIR, observe._kit_db_path, observe.PIPELINE_EVENTS) = self.old
        observe.paths.memory_dir = self.old_memory_dir
        specialist_swarm.KB_PATH = self.old_kb
        (memory.EXP_DIR, memory.ATOMS, memory.SCENARIOS,
         memory.SOP, memory.LEDGER) = self.old_mem

    def _write_db(self, history, adjustments=None):
        db = {"strategies": {
                  "B": {"name": "reduce_timestep", "desc": "",
                        "for": ["TEMPERATURE_SPIKE"], "success_rate": 0.5, "uses": 3}},
              "history": history}
        if adjustments is not None:
            db["strategy_adjustments"] = adjustments
        with open(self.db_path, "w") as f:
            json.dump(db, f, indent=2)

    def _attempt(self, sid, cause, outcome, ts=None):
        # ts 默认"现在":连败窗口只收近 14 天,硬编码旧日期会随真实时间腐烂
        import time as _time
        return {"strategy": sid, "root_cause": cause, "case_id": "c1",
                "ts": ts or _time.strftime("%Y-%m-%dT%H:%M:%S"), "outcome": outcome}

    def test_settled_outcomes_record_atoms_idempotent(self):
        self._write_db([self._attempt("B", "thermal_runaway", "fail"),
                        self._attempt("B", "thermal_runaway", "success")])
        observe.observe(days=7, min_occurrences=3)
        observe.observe(days=7, min_occurrences=3)  # 重跑不重复入账
        rows = [json.loads(ln) for ln in open(memory.ATOMS, encoding="utf-8")]
        recov = [r for r in rows if r["kind"] == "recovery-outcome"]
        self.assertEqual(len(recov), 2)
        # fact 带 case_id 尾巴(2026-09-17):近似去重按 fact 判,
        # 不带 case 会同秒异 case 被吞(B2 回潮)
        self.assertIn("B:thermal_runaway:fail@c1", {r["fact"] for r in recov})

    def test_consecutive_fail_drafts_applicable_auto_proposal(self):
        self._write_db([self._attempt("B", "thermal_runaway", "fail"),
                        self._attempt("B", "thermal_runaway", "fail")])
        summary = observe.observe(days=7, min_occurrences=3)
        self.assertEqual(len(summary["drafted"]), 1)
        pdir = summary["drafted"][0]
        meta = json.load(open(os.path.join(pdir, "meta.json")))
        self.assertEqual(meta["class"], "strategy_weight_tuning")
        self.assertEqual(meta["tier"], "auto")
        self.assertEqual(meta["root"], "kit")
        # diff 必须真实可应用:解析 + 打到当前 db 上,出现 penalty 0.15
        diff_text = open(os.path.join(pdir, "diff.patch")).read()
        files = apply_proposal.parse_unified_diff(diff_text)
        self.assertEqual([f["new"] for f in files], [".v3.3_strategy_db.json"])
        old_lines = open(self.db_path).read().splitlines()
        new_lines = apply_proposal.apply_hunks(old_lines, files[0]["hunks"], "x")
        new_db = json.loads("\n".join(new_lines) + "\n")
        adj = new_db["strategy_adjustments"]["B:thermal_runaway"]
        self.assertEqual(adj["penalty"], 0.15)

    def test_no_streak_no_draft(self):
        self._write_db([self._attempt("B", "thermal_runaway", "fail"),
                        self._attempt("B", "thermal_runaway", "success")])
        summary = observe.observe(days=7, min_occurrences=3)
        self.assertEqual(summary["drafted"], [])

    def test_penalty_ratchet_and_clamp(self):
        # 已有 0.55 → 连败再起草压到 0.6(上限);再连败不再起草
        adj = {"B:thermal_runaway": {"penalty": 0.55, "reason": "x", "ts": "t"}}
        self._write_db([self._attempt("B", "thermal_runaway", "fail"),
                        self._attempt("B", "thermal_runaway", "fail")],
                       adjustments=adj)
        summary = observe.observe(days=7, min_occurrences=3)
        self.assertEqual(len(summary["drafted"]), 1)
        diff_text = open(os.path.join(summary["drafted"][0], "diff.patch")).read()
        self.assertIn('"penalty": 0.6', diff_text)

    def test_missing_kit_db_silent_skip(self):
        # db 不存在:不起草、不崩,其他观测源照常
        summary = observe.observe(days=7, min_occurrences=3)
        self.assertEqual(summary["drafted"], [])

    def test_corrupt_but_valid_db_no_crash(self):
        # 损坏但合法 JSON 的 db(history=null / 条目字段非串)不得打崩整轮观测(一轮对抗 B1)
        for variant in ({"strategies": {}, "history": None},
                        {"strategies": {}, "history": "str"},
                        {"strategies": {}, "history": [
                            {"strategy": ["B"], "root_cause": "x", "outcome": "fail"},
                            {"strategy": "B", "root_cause": None, "outcome": "fail"},
                            {"strategy": "B", "root_cause": "x", "outcome": "fail"},
                            {"strategy": "B", "root_cause": "x", "outcome": "fail"}]}):
            with open(self.db_path, "w") as f:
                json.dump(variant, f, indent=2)
            summary = observe.observe(days=7, min_occurrences=3)
            self.assertIsInstance(summary["drafted"], list)

    def test_atom_refs_distinct_cases_same_ts(self):
        # 同秒同策略不同 case:refs 含 case_id,两条都入账不被去重吞掉(一轮对抗 B2)
        import time as _time
        ts = _time.strftime("%Y-%m-%dT%H:%M:%S")
        self._write_db([
            {"strategy": "B", "root_cause": "thermal_runaway", "case_id": "c1",
             "ts": ts, "outcome": "fail"},
            {"strategy": "B", "root_cause": "thermal_runaway", "case_id": "c2",
             "ts": ts, "outcome": "fail"}])
        observe.observe(days=7, min_occurrences=3)
        rows = [json.loads(ln) for ln in open(memory.ATOMS, encoding="utf-8")]
        recov = [r for r in rows if r["kind"] == "recovery-outcome"]
        self.assertEqual(len(recov), 2)

    def test_slug_traversal_sanitized(self):
        # 畸形 strategy 名带 ../:slug 净化后提案目录必须仍在 PROPOSALS 内(一轮对抗 B3)
        self._write_db([
            self._attempt("../../evil", "x", "fail"),
            self._attempt("../../evil", "x", "fail")])
        summary = observe.observe(days=7, min_occurrences=3)
        self.assertEqual(len(summary["drafted"]), 1)
        real_root = os.path.realpath(observe.PROPOSALS)
        self.assertTrue(os.path.realpath(summary["drafted"][0]).startswith(real_root + os.sep))

    def test_noncanonical_db_no_draft(self):
        # 落盘非规范化(indent=4 手改):整体重序列化会带出段外改动,放弃本轮(一轮对抗 B5)
        db = {"strategies": {"B": {"name": "reduce_timestep", "desc": "",
                                   "for": ["TEMPERATURE_SPIKE"],
                                   "success_rate": 0.5, "uses": 3}},
              "history": [self._attempt("B", "thermal_runaway", "fail"),
                          self._attempt("B", "thermal_runaway", "fail")]}
        with open(self.db_path, "w") as f:
            f.write(json.dumps(db, indent=4))
        summary = observe.observe(days=7, min_occurrences=3)
        self.assertEqual(summary["drafted"], [])

    def test_same_strategy_two_causes_both_drafted(self):
        # slug 带 cause:同策略异根因同日各出一份,不互踩去重位(一轮对抗 B4)
        self._write_db([
            self._attempt("B", "thermal_runaway", "fail"),
            self._attempt("B", "thermal_runaway", "fail"),
            self._attempt("B", "general_instability", "fail"),
            self._attempt("B", "general_instability", "fail")])
        summary = observe.observe(days=7, min_occurrences=3)
        self.assertEqual(len(summary["drafted"]), 2)

    def test_stale_snapshot_penalty_rechecked(self):
        # 起草与 diff 生成之间 penalty 已被应用(磁盘新快照):不再退档起草(一轮对抗 C1)
        adj = {"B:thermal_runaway": {"penalty": 0.6, "reason": "x", "ts": "t"}}
        self._write_db([self._attempt("B", "thermal_runaway", "fail"),
                        self._attempt("B", "thermal_runaway", "fail")],
                       adjustments=adj)
        summary = observe.observe(days=7, min_occurrences=3)
        self.assertEqual(summary["drafted"], [])

    def test_streak_window_excludes_old_fails(self):
        # 连败只看近 14 天:20 天前的连败不起草(四轮对抗 M1)
        old = "2026-01-01T10:00:00"
        self._write_db([self._attempt("B", "thermal_runaway", "fail", old),
                        self._attempt("B", "thermal_runaway", "fail", old)])
        summary = observe.observe(days=7, min_occurrences=3)
        self.assertEqual(summary["drafted"], [])

    def test_success_streak_decay_drafts_down(self):
        # 连胜 ≥2 且仍有压制:起草减压提案 0.30→0.15(四轮对抗 M1 退出通道)
        adj = {"B:thermal_runaway": {"penalty": 0.3, "reason": "x", "ts": "t"}}
        self._write_db([self._attempt("B", "thermal_runaway", "success"),
                        self._attempt("B", "thermal_runaway", "success")],
                       adjustments=adj)
        summary = observe.observe(days=7, min_occurrences=3)
        self.assertEqual(len(summary["drafted"]), 1)
        diff_text = open(os.path.join(summary["drafted"][0], "diff.patch")).read()
        self.assertIn('"penalty": 0.15', diff_text)

    def test_decay_to_zero_removes_key(self):
        # 减压到 0:摘除条目恢复自由竞争;应用后 select 不再受压制
        adj = {"B:thermal_runaway": {"penalty": 0.15, "reason": "x", "ts": "t"}}
        self._write_db([self._attempt("B", "thermal_runaway", "success"),
                        self._attempt("B", "thermal_runaway", "success")],
                       adjustments=adj)
        summary = observe.observe(days=7, min_occurrences=3)
        self.assertEqual(len(summary["drafted"]), 1)
        diff_text = open(os.path.join(summary["drafted"][0], "diff.patch")).read()
        files = apply_proposal.parse_unified_diff(diff_text)
        new_lines = apply_proposal.apply_hunks(
            open(self.db_path).read().splitlines(), files[0]["hunks"], "x")
        new_db = json.loads("\n".join(new_lines) + "\n")
        self.assertNotIn("B:thermal_runaway", new_db["strategy_adjustments"])

    def test_same_day_draft_refreshed_when_not_applied(self):
        # 同日草稿未应用:db 变化后重跑 observe,diff.patch 刷新到新快照(四轮对抗 H2)
        self._write_db([self._attempt("B", "thermal_runaway", "fail"),
                        self._attempt("B", "thermal_runaway", "fail")])
        s1 = observe.observe(days=7, min_occurrences=3)
        self.assertEqual(len(s1["drafted"]), 1)
        patch = os.path.join(s1["drafted"][0], "diff.patch")
        first = open(patch).read()
        # 模拟 kit 期间又记了一条(行数变化,旧 diff 上下文过期)
        db = json.load(open(self.db_path))
        db["history"].append(self._attempt("B", "thermal_runaway", "fail"))
        with open(self.db_path, "w") as f:
            json.dump(db, f, indent=2)
        s2 = observe.observe(days=7, min_occurrences=3)
        self.assertEqual(s2["drafted"], [])  # 同日不重复起草
        refreshed = open(patch).read()
        self.assertNotEqual(refreshed, first, "未应用提案的 diff 必须刷新到新快照")
        files = apply_proposal.parse_unified_diff(refreshed)
        apply_proposal.apply_hunks(  # 刷新后的 diff 必须可直接应用
            open(self.db_path).read().splitlines(), files[0]["hunks"], "x")

    def test_direction_flip_rewrites_title(self):
        # 同日方向翻转(连胜减压草稿→连败压制):刷新分支须同步重写 proposal.md
        # 标题,防"标题减压实为压制"误导审计(八轮对抗)
        adj = {"B:thermal_runaway": {"penalty": 0.15, "reason": "x", "ts": "t"}}
        self._write_db([self._attempt("B", "thermal_runaway", "success"),
                        self._attempt("B", "thermal_runaway", "success")],
                       adjustments=adj)
        s1 = observe.observe(days=7, min_occurrences=3)
        self.assertEqual(len(s1["drafted"]), 1)
        pdir = s1["drafted"][0]
        pmd = os.path.join(pdir, "proposal.md")
        self.assertIn("连胜", open(pmd).read().splitlines()[0])
        # 尾部翻转为连败 ×2:刷新为压制 diff,标题必须跟着翻
        db = json.load(open(self.db_path))
        db["history"].append(self._attempt("B", "thermal_runaway", "fail"))
        db["history"].append(self._attempt("B", "thermal_runaway", "fail"))
        with open(self.db_path, "w") as f:
            json.dump(db, f, indent=2)
        s2 = observe.observe(days=7, min_occurrences=3)
        self.assertEqual(s2["drafted"], [])  # 同日不重复起草,走刷新
        title_line = open(pmd).read().splitlines()[0]
        self.assertIn("连败", title_line)
        self.assertIn("上调", title_line)
        self.assertIn('"penalty": 0.3',
                      open(os.path.join(pdir, "diff.patch")).read())

    def test_applied_proposal_never_refreshed(self):
        # 已应用提案是审计留痕:同日重跑 observe 绝不动它的 diff.patch
        self._write_db([self._attempt("B", "thermal_runaway", "fail"),
                        self._attempt("B", "thermal_runaway", "fail")])
        s1 = observe.observe(days=7, min_occurrences=3)
        patch = os.path.join(s1["drafted"][0], "diff.patch")
        first = open(patch).read()
        slug = os.path.basename(s1["drafted"][0])
        with open(memory.LEDGER, "w") as f:  # setUp 已把 paths.memory_dir 沙盒到 tmp
            f.write(json.dumps({"slug": slug, "result": "AUTO_APPLIED"}) + "\n")
        observe.observe(days=7, min_occurrences=3)
        self.assertEqual(open(patch).read(), first, "已应用提案的 diff.patch 不得被刷新")

    def test_future_ts_excluded_from_streak(self):
        # 伪造未来时间戳的连败不得骗出压制(五轮对抗):与 flowgate 路径同口径,
        # >1h 容差之外的未来账不进连败窗口
        import time as _time
        future = _time.strftime("%Y-%m-%dT%H:%M:%S",
                                _time.localtime(_time.time() + 7200))
        self._write_db([self._attempt("B", "thermal_runaway", "fail", future),
                        self._attempt("B", "thermal_runaway", "fail", future)])
        summary = observe.observe(days=7, min_occurrences=3)
        self.assertEqual(summary["drafted"], [])

    def test_bool_penalty_not_laundered(self):
        # penalty:true 经 float(True)=1.0 会被洗成"已有压制",假出减压提案(五轮对抗)
        adj = {"B:thermal_runaway": {"penalty": True, "reason": "x", "ts": "t"}}
        self._write_db([self._attempt("B", "thermal_runaway", "success"),
                        self._attempt("B", "thermal_runaway", "success")],
                       adjustments=adj)
        summary = observe.observe(days=7, min_occurrences=3)
        self.assertEqual(summary["drafted"], [])

    def test_negative_penalty_clamped_zero(self):
        # penalty:-0.5 按 0 计:连败加压从 0.15 起步(而非 -0.35),且不产生减压空间
        adj = {"B:thermal_runaway": {"penalty": -0.5, "reason": "x", "ts": "t"}}
        self._write_db([self._attempt("B", "thermal_runaway", "fail"),
                        self._attempt("B", "thermal_runaway", "fail")],
                       adjustments=adj)
        summary = observe.observe(days=7, min_occurrences=3)
        self.assertEqual(len(summary["drafted"]), 1)
        diff_text = open(os.path.join(summary["drafted"][0], "diff.patch")).read()
        self.assertIn('"penalty": 0.15', diff_text)
        self.assertNotIn("-0.35", diff_text)

    def test_nan_penalty_no_mislabeled_draft(self):
        # penalty:NaN(Python json.loads 默认接受 NaN 字面量)可穿透 max(nan,0)=nan:
        # up 方向比较全 False,最终走出"标题写上调、diff 实为删条目"的误标提案(五轮对抗);
        # 钳 0 后连败起草正常从 0.15 起步
        adj = {"B:thermal_runaway": {"penalty": float("nan"), "reason": "x", "ts": "t"}}
        self._write_db([self._attempt("B", "thermal_runaway", "fail"),
                        self._attempt("B", "thermal_runaway", "fail")],
                       adjustments=adj)
        summary = observe.observe(days=7, min_occurrences=3)
        self.assertEqual(len(summary["drafted"]), 1)
        diff_text = open(os.path.join(summary["drafted"][0], "diff.patch")).read()
        self.assertIn('"penalty": 0.15', diff_text)

    def test_stale_evidence_rechecked_in_diff(self):
        # 初读与落笔之间尾部已转向:diff 生成按磁盘新快照复核连败证据(六轮对抗 TOCTOU)
        self._write_db([self._attempt("B", "thermal_runaway", "fail"),
                        self._attempt("B", "thermal_runaway", "fail"),
                        self._attempt("B", "thermal_runaway", "success")])
        self.assertIsNone(observe._strategy_db_diff(
            self.db_path, "B", "thermal_runaway", "up", 2))  # 尾部已是 success,拒加压
        # 对称:尾部为 fail 且有压制时,direction=down 也须被复核拦下
        adj = {"B:thermal_runaway": {"penalty": 0.3, "reason": "x", "ts": "t"}}
        self._write_db([self._attempt("B", "thermal_runaway", "fail"),
                        self._attempt("B", "thermal_runaway", "fail")],
                       adjustments=adj)
        self.assertIsNone(observe._strategy_db_diff(
            self.db_path, "B", "thermal_runaway", "down", 2))
        # 对照:尾部确为连败时正常出 diff
        self.assertIsNotNone(observe._strategy_db_diff(
            self.db_path, "B", "thermal_runaway", "up", 2))

    def test_long_name_slug_truncated(self):
        # 10KB 策略名:slug 截断+哈希尾巴,提案正常起草、目录名不爆 ENAMETOOLONG
        # 而不再被静默吞掉致连败永久失声(六轮对抗)
        long_sid = "B" + "x" * 10000
        slug = observe._slug_safe(long_sid)
        self.assertLessEqual(len(slug), 61)
        self.assertEqual(slug, observe._slug_safe(long_sid))  # 确定性
        self.assertNotEqual(slug, observe._slug_safe(long_sid + "y"))  # 哈希保唯一
        self._write_db([self._attempt(long_sid, "thermal_runaway", "fail"),
                        self._attempt(long_sid, "thermal_runaway", "fail")])
        summary = observe.observe(days=7, min_occurrences=3)
        self.assertEqual(len(summary["drafted"]), 1)
        real_root = os.path.realpath(observe.PROPOSALS)
        self.assertTrue(os.path.realpath(summary["drafted"][0]).startswith(real_root + os.sep))

    def test_slug_pair_unambiguous(self):
        # 白名单保留 '-'/'_' 使裸拼接有二义:(A,B-C) 与 (A-B,C) 必须不同 slug,
        # 否则后者同日刷新覆盖前者,前者连败压制永不落地(九轮对抗)
        self.assertNotEqual(observe._pair_slug("A", "B-C"),
                            observe._pair_slug("A-B", "C"))
        self._write_db([self._attempt("A", "B-C", "fail"),
                        self._attempt("A", "B-C", "fail"),
                        self._attempt("A-B", "C", "fail"),
                        self._attempt("A-B", "C", "fail")])
        summary = observe.observe(days=7, min_occurrences=3)
        self.assertEqual(len(summary["drafted"]), 2, "两对都必须独立起草")

    def test_stale_draft_invalidated_when_evidence_turns(self):
        # 连败起草后尾部转向成功:同日未应用草稿的 diff 必须作废(清空),
        # 防证据已反转的 stale diff 被 apply 通道误应用(七轮对抗 d2);
        # 证据恢复后经同日刷新通道回填新 diff
        self._write_db([self._attempt("B", "thermal_runaway", "fail"),
                        self._attempt("B", "thermal_runaway", "fail")])
        s1 = observe.observe(days=7, min_occurrences=3)
        patch = os.path.join(s1["drafted"][0], "diff.patch")
        self.assertGreater(os.path.getsize(patch), 0)
        db = json.load(open(self.db_path))
        db["history"] += [self._attempt("B", "thermal_runaway", "success"),
                          self._attempt("B", "thermal_runaway", "success")]
        with open(self.db_path, "w") as f:
            json.dump(db, f, indent=2)
        observe.observe(days=7, min_occurrences=3)
        self.assertEqual(os.path.getsize(patch), 0, "证据反转后旧 diff 必须作废")
        db = json.load(open(self.db_path))
        db["history"] += [self._attempt("B", "thermal_runaway", "fail"),
                          self._attempt("B", "thermal_runaway", "fail")]
        with open(self.db_path, "w") as f:
            json.dump(db, f, indent=2)
        observe.observe(days=7, min_occurrences=3)
        self.assertGreater(os.path.getsize(patch), 0, "证据恢复后 diff 必须刷新回填")

    def test_applied_draft_never_invalidated(self):
        # 已应用草稿是审计留痕:证据反转也不动其 diff.patch(四轮 H2 + 七轮)
        self._write_db([self._attempt("B", "thermal_runaway", "fail"),
                        self._attempt("B", "thermal_runaway", "fail")])
        s1 = observe.observe(days=7, min_occurrences=3)
        patch = os.path.join(s1["drafted"][0], "diff.patch")
        first = open(patch).read()
        slug = os.path.basename(s1["drafted"][0])
        with open(memory.LEDGER, "w") as f:
            f.write(json.dumps({"slug": slug, "result": "AUTO_APPLIED"}) + "\n")
        db = json.load(open(self.db_path))
        db["history"] += [self._attempt("B", "thermal_runaway", "success"),
                          self._attempt("B", "thermal_runaway", "success")]
        with open(self.db_path, "w") as f:
            json.dump(db, f, indent=2)
        observe.observe(days=7, min_occurrences=3)
        self.assertEqual(open(patch).read(), first, "已应用提案绝不被作废")

    def test_corrupt_proposal_md_does_not_skip_sweep(self):
        # 手改成非法 UTF-8 的 proposal.md:UnicodeDecodeError 不得逃逸刷新分支、
        # 跳过收尾横扫(九轮对抗)——stale 草稿照常作废,合法草稿 diff 照常刷新
        self._write_db([self._attempt("B", "thermal_runaway", "fail"),
                        self._attempt("B", "thermal_runaway", "fail")])
        s1 = observe.observe(days=7, min_occurrences=3)
        self.assertEqual(len(s1["drafted"]), 1)
        good = s1["drafted"][0]
        with open(os.path.join(good, "proposal.md"), "wb") as f:
            f.write(b"\xff\xfe corrupted")
        # 另造当日 stale 草稿(无证据),横扫必须清空它的 diff.patch
        stale = os.path.join(observe.PROPOSALS,
                             "%s-strategy-weight-X-y" % time.strftime("%Y-%m-%d"))
        os.makedirs(stale)
        with open(os.path.join(stale, "diff.patch"), "w") as f:
            f.write("stale diff")
        observe.observe(days=7, min_occurrences=3)  # 不得抛异常
        self.assertEqual(os.path.getsize(os.path.join(stale, "diff.patch")), 0,
                         "刷新分支异常不得跳过收尾横扫")
        self.assertGreater(os.path.getsize(os.path.join(good, "diff.patch")), 0,
                           "合法草稿的 diff 必须已刷新回填")

    def test_sweep_invalidates_on_malformed_db(self):
        # db 持续畸形(history=null):证据不可确认,早退路径也必须 fail-closed
        # 作废同日未应用草稿,不得留着 stale diff 等 apply 误应用(八轮对抗)
        self._write_db([self._attempt("B", "thermal_runaway", "fail"),
                        self._attempt("B", "thermal_runaway", "fail")])
        s1 = observe.observe(days=7, min_occurrences=3)
        patch = os.path.join(s1["drafted"][0], "diff.patch")
        self.assertGreater(os.path.getsize(patch), 0)
        with open(self.db_path, "w") as f:
            json.dump({"strategies": {}, "history": None}, f, indent=2)
        observe.observe(days=7, min_occurrences=3)
        self.assertEqual(os.path.getsize(patch), 0, "db 畸形期间 stale diff 必须作废")


class TestObserveKitApply(SandboxCase):
    """strategy_weight_tuning 提案过真实 apply 通道(备份+LEDGER+POLICY 门)。"""

    def test_auto_apply_updates_strategy_db(self):
        db_rel = ".v3.3_strategy_db.json"
        db = {"strategies": {"B": {"name": "reduce_timestep", "desc": "",
                                   "for": ["TEMPERATURE_SPIKE"],
                                   "success_rate": 0.5, "uses": 3}},
              "history": []}
        with open(os.path.join(self.kit, db_rel), "w") as f:
            json.dump(db, f, indent=2)
        self.policy["auto_fix_classes"]["strategy_weight_tuning"] = {
            "root": "kit", "paths": [db_rel], "max_changed_lines": 12}
        new_db = {"strategies": db["strategies"], "history": [],
                  "strategy_adjustments": {
                      "B:thermal_runaway": {"penalty": 0.15, "reason": "连败2次", "ts": "t"}}}
        import difflib
        old_lines = json.dumps(db, indent=2).splitlines()
        new_lines = json.dumps(new_db, ensure_ascii=False, indent=2).splitlines()
        diff = "\n".join(difflib.unified_diff(
            old_lines, new_lines, fromfile="a/" + db_rel, tofile="b/" + db_rel,
            n=3, lineterm="")) + "\n"
        pdir = os.path.join(self.tmp, "pv_strategy")
        make_proposal(pdir, {"root": "kit", "class": "strategy_weight_tuning",
                             "tier": "auto"}, diff)
        rc = self._run_apply(pdir)
        self.assertEqual(rc, 0)
        applied = json.load(open(os.path.join(self.kit, db_rel)))
        self.assertEqual(applied["strategy_adjustments"]["B:thermal_runaway"]["penalty"], 0.15)
        self.assertEqual(self._ledger_rows()[-1]["result"], "AUTO_APPLIED")

    def test_auto_class_rejects_other_files(self):
        # auto 类只许改策略 db:改别的文件必须 REJECTED
        self.policy["auto_fix_classes"]["strategy_weight_tuning"] = {
            "root": "kit", "paths": [".v3.3_strategy_db.json"], "max_changed_lines": 8}
        diff = ("--- a/agents/runtime/v3.3/monitor_agent.py\n"
                "+++ b/agents/runtime/v3.3/monitor_agent.py\n"
                "@@ -1 +1 @@\n-SCHEDULE = 300\n+SCHEDULE = 60\n")
        pdir = os.path.join(self.tmp, "pv_strategy_bad")
        make_proposal(pdir, {"root": "kit", "class": "strategy_weight_tuning",
                             "tier": "auto"}, diff)
        rc = self._run_apply(pdir)
        self.assertEqual(rc, 2)
        self.assertIn("只许改", self._ledger_rows()[-1]["reason"])


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestPolicyGateHardening(SandboxCase):
    """2026-08-29 对抗审计:红线规范化绕过 / 回归 fail-open / rollback 越界。"""

    FORBIDDEN = "agents/runtime/v3.3/claude_interface.py"

    def _forbidden_diff(self, variant):
        return ("--- a/%s\n+++ b/%s\n@@ -1 +1 @@\n-x\n+y\n" % (variant, variant))

    def test_normalized_variants_of_forbidden_rejected(self):
        # ./ 与 // 与 /./ 变体在规范化前会绕过字面比对(审计实证),现在必须统一拒
        for i, variant in enumerate(("./" + self.FORBIDDEN,
                                     "agents//runtime/v3.3/claude_interface.py",
                                     "agents/./runtime/v3.3/claude_interface.py")):
            pdir = os.path.join(self.tmp, "pv%d" % i)
            make_proposal(pdir, {"root": "kit", "class": "monitor_logic",
                                 "tier": "human"}, self._forbidden_diff(variant))
            rc = self._run_apply(pdir, "--approver", "tester")
            self.assertEqual(rc, 2, "变体 %s 必须被拒" % variant)
            self.assertIn("红线", self._ledger_rows()[-1]["reason"])

    def test_rename_into_forbidden_rejected(self):
        # old=允许路径 new=红线路径:两侧都要过红线
        diff = ("--- a/%s\n+++ b/%s\n@@ -1 +1 @@\n-SCHEDULE = 300\n+SCHEDULE = 310\n"
                % (self.target_rel, self.FORBIDDEN))
        pdir = os.path.join(self.tmp, "pv_ren")
        make_proposal(pdir, {"root": "kit", "class": "monitor_logic", "tier": "human"}, diff)
        rc = self._run_apply(pdir, "--approver", "tester")
        self.assertEqual(rc, 2)
        self.assertIn("红线", self._ledger_rows()[-1]["reason"])

    def test_missing_test_command_fails_closed(self):
        # 回归命令不存在 → 按红处理:回滚 + ROLLED_BACK 入账,补丁不得残留
        self.policy["roots"]["kit"]["test_commands"] = [["/nonexistent/definitely-not-a-cmd"]]
        pdir = os.path.join(self.tmp, "p_nc")
        make_proposal(pdir, {"root": "kit", "class": "monitor_schedule_temp",
                             "tier": "auto"}, TestPolicyGate.DIFF)
        rc = self._run_apply(pdir)
        self.assertEqual(rc, 3)
        self.assertEqual(open(os.path.join(self.kit, self.target_rel)).read(),
                         "SCHEDULE = 300\n", "补丁必须已还原")
        self.assertEqual(self._ledger_rows()[-1]["result"], "ROLLED_BACK")

    def test_rollback_rejects_forged_ledger_paths(self):
        victim = os.path.join(self.tmp, "victim.txt")
        open(victim, "w").write("ORIGINAL")
        rel_outside = os.path.relpath(victim, self.kit)  # ../victim.txt
        apply_proposal.ledger({"id": "forge1", "slug": "x", "root": "kit",
                               "files": [rel_outside], "backup": "backups/forge1_x",
                               "result": "APPLIED"})
        rc = apply_proposal.rollback("forge1")
        self.assertEqual(rc, 2)
        self.assertEqual(open(victim).read(), "ORIGINAL", "root 外文件不得被回滚覆写")

    def test_rollback_tolerates_corrupt_ledger(self):
        open(apply_proposal.LEDGER, "w").write('{"id":"ok"}\n这不是json\n{"id":"ok2"}\n')
        rc = apply_proposal.rollback("nonexistent")
        self.assertEqual(rc, 2)  # 不崩溃,正常报未找到

    def test_create_claim_on_existing_file_rejected(self):
        # diff 声称新建(/dev/null)但文件已存在:不备份直接覆盖=原内容不可逆销毁,
        # 回滚还会把既有文件当"新建"删掉——必须在门禁拒(三轮对抗 B1)
        diff = ("--- /dev/null\n+++ b/%s\n@@ -0,0 +1 @@\n+HIJACKED = 1\n" % self.target_rel)
        pdir = os.path.join(self.tmp, "pv_create")
        make_proposal(pdir, {"root": "kit", "class": "monitor_schedule_temp",
                             "tier": "auto"}, diff)
        rc = self._run_apply(pdir)
        self.assertEqual(rc, 2)
        row = self._ledger_rows()[-1]
        self.assertEqual(row["result"], "REJECTED")
        self.assertIn("已存在", row["reason"])
        self.assertEqual(open(os.path.join(self.kit, self.target_rel)).read(),
                         "SCHEDULE = 300\n", "既有文件内容不得被覆盖")

    def test_create_then_rollback_removes_created_file(self):
        # 真新建提案:apply 入账 created 列表;rollback 时备份无此文件,
        # 仅凭 created 语义删除目标(备份丢失≠删文件的护栏不挡真新建)
        new_rel = "agents/runtime/v3.3/new_helper.py"
        self.policy["auto_fix_classes"]["monitor_schedule_temp"]["paths"].append(new_rel)
        diff = ("--- /dev/null\n+++ b/%s\n@@ -0,0 +1 @@\n+NEW = 1\n" % new_rel)
        pdir = os.path.join(self.tmp, "pv_new")
        make_proposal(pdir, {"root": "kit", "class": "monitor_schedule_temp",
                             "tier": "auto"}, diff)
        rc = self._run_apply(pdir)
        self.assertEqual(rc, 0)
        new_abs = os.path.join(self.kit, new_rel)
        self.assertEqual(open(new_abs).read(), "NEW = 1\n")
        row = self._ledger_rows()[-1]
        self.assertIn(new_rel, row.get("created") or [])
        old_argv = sys.argv
        sys.argv = ["apply_proposal.py", "--rollback", row["id"]]
        try:
            rc = apply_proposal.main()
        finally:
            sys.argv = old_argv
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(new_abs), "rollback 必须删除新建文件")

    def test_git_style_create_recorded_and_rollback_deletes(self):
        # git 风格手写新建 diff(--- a/x +++ b/x @@ -0,0):old 非 /dev/null,
        # 旧实现 created 漏记 → rollback 被拒、新文件残留(四轮对抗)
        new_rel = "agents/runtime/v3.3/git_created.py"
        self.policy["auto_fix_classes"]["monitor_schedule_temp"]["paths"].append(new_rel)
        diff = ("--- a/%s\n+++ b/%s\n@@ -0,0 +1 @@\n+MADE = 1\n" % (new_rel, new_rel))
        pdir = os.path.join(self.tmp, "pv_gitnew")
        make_proposal(pdir, {"root": "kit", "class": "monitor_schedule_temp",
                             "tier": "auto"}, diff)
        rc = self._run_apply(pdir)
        self.assertEqual(rc, 0)
        new_abs = os.path.join(self.kit, new_rel)
        self.assertEqual(open(new_abs).read(), "MADE = 1\n")
        row = self._ledger_rows()[-1]
        self.assertIn(new_rel, row.get("created") or [],
                      "git 风格新建必须记入 created,否则回滚无法删除")
        old_argv = sys.argv
        sys.argv = ["apply_proposal.py", "--rollback", row["id"]]
        try:
            rc = apply_proposal.main()
        finally:
            sys.argv = old_argv
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(new_abs), "rollback 必须删除 git 风格新建文件")

    def test_rollback_tolerates_non_dict_ledger_rows(self):
        # LEDGER 混入合法 JSON 但非 dict 的行([1,2]/"str"):旧实现 .get 直接崩(四轮对抗)
        open(apply_proposal.LEDGER, "w").write('{"id":"ok"}\n[1,2]\n"str"\n')
        rc = apply_proposal.rollback("nonexistent")
        self.assertEqual(rc, 2)  # 不崩溃,正常报未找到

    SNEAK_DIFF = ("--- /dev/null\n+++ b/agents/runtime/v3.3/sneak.py\n"
                  "@@ -0,0 +1 @@\n+stageA\n"
                  "--- a/agents/runtime/v3.3/sneak.py\n"
                  "+++ b/agents/runtime/v3.3/sneak.py\n@@ -1 +1 @@\n-stageA\n+stageB\n")

    def _sneak_proposal(self, name):
        # 同文件双条目 diff(先建后改):备份与打补丁交错时会把第一条目写出的
        # 中间态当"原始"备份,还原后文件以中间态残留(五轮对抗)
        rel = "agents/runtime/v3.3/sneak.py"
        if rel not in self.policy["auto_fix_classes"]["monitor_schedule_temp"]["paths"]:
            self.policy["auto_fix_classes"]["monitor_schedule_temp"]["paths"].append(rel)
        pdir = os.path.join(self.tmp, name)
        make_proposal(pdir, {"root": "kit", "class": "monitor_schedule_temp",
                             "tier": "auto"}, self.SNEAK_DIFF)
        return pdir, os.path.join(self.kit, rel)

    def test_same_file_double_entry_red_leaves_nothing(self):
        # 回归红路径:rc=3"已还原"必须字面成立——新建文件不得残留中间态
        self.policy["roots"]["kit"]["test_commands"] = [
            [sys.executable, "-c", "import sys; sys.exit(1)"]]  # 恒红
        pdir, sneak_abs = self._sneak_proposal("pv_sneak_red")
        rc = self._run_apply(pdir)
        self.assertEqual(rc, 3)
        self.assertEqual(self._ledger_rows()[-1]["result"], "ROLLED_BACK")
        self.assertFalse(os.path.exists(sneak_abs),
                         "回滚后 sneak.py 以中间态残留(两阶段备份失效)")

    def test_same_file_double_entry_green_rollback_deletes(self):
        # 回归绿路径:created 去重入账;rollback 必须删除该文件而非还原中间态
        pdir, sneak_abs = self._sneak_proposal("pv_sneak_green")
        rc = self._run_apply(pdir)
        self.assertEqual(rc, 0)
        self.assertEqual(open(sneak_abs).read(), "stageB\n")
        row = self._ledger_rows()[-1]
        self.assertEqual(row.get("created"),
                         ["agents/runtime/v3.3/sneak.py"], "created 必须去重")
        old_argv = sys.argv
        sys.argv = ["apply_proposal.py", "--rollback", row["id"]]
        try:
            rc = apply_proposal.main()
        finally:
            sys.argv = old_argv
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(sneak_abs),
                         "rollback 必须删除同文件双条目新建的文件")

    def test_double_dev_null_entry_rejected(self):
        # 双 /dev/null 畸形条目:无路径可校验,下游会把门禁打崩(二轮对抗)
        diff = "--- /dev/null\n+++ /dev/null\n@@ -0,0 +1 @@\n+x\n"
        pdir = os.path.join(self.tmp, "pv_dd")
        make_proposal(pdir, {"root": "kit", "class": "monitor_schedule_temp",
                             "tier": "auto"}, diff)
        rc = self._run_apply(pdir)
        self.assertEqual(rc, 2)
        self.assertEqual(self._ledger_rows()[-1]["result"], "REJECTED")

    def test_test_results_write_failure_still_rolls_back(self):
        # 提案目录只读:test-results.txt 写崩不得阻断还原与入账,
        # 否则补丁已应用却零入账零还原(七轮对抗 中)
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("root 下 chmod 555 不阻断写入")
        self.policy["roots"]["kit"]["test_commands"] = [
            [sys.executable, "-c", "import sys; sys.exit(1)"]]  # 恒红
        pdir = os.path.join(self.tmp, "pv_ro")
        make_proposal(pdir, {"root": "kit", "class": "monitor_schedule_temp",
                             "tier": "auto"},
                      "--- a/%s\n+++ b/%s\n@@ -1 +1 @@\n-SCHEDULE = 300\n+SCHEDULE = 310\n"
                      % (self.target_rel, self.target_rel))
        os.chmod(pdir, 0o555)
        self.addCleanup(os.chmod, pdir, 0o755)
        rc = self._run_apply(pdir)
        self.assertEqual(rc, 3)
        self.assertEqual(self._ledger_rows()[-1]["result"], "ROLLED_BACK")
        self.assertEqual(open(os.path.join(self.kit, self.target_rel)).read(),
                         "SCHEDULE = 300\n", "test-results 写崩也必须还原")

    def test_backup_phase_failure_preserves_original(self):
        # 备份阶段失败(chmod 000 使 copy2 被拒)时无任何文件被改动,
        # 绝不能走 _restore 把未触碰的原文件当"新建"删掉(六轮对抗 高)
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("root 下 chmod 000 不阻断 copy2")
        target = os.path.join(self.kit, self.target_rel)
        os.chmod(target, 0)
        self.addCleanup(os.chmod, target, 0o644)
        pdir = os.path.join(self.tmp, "pv_bakfail")
        make_proposal(pdir, {"root": "kit", "class": "monitor_schedule_temp",
                             "tier": "auto"},
                      "--- a/%s\n+++ b/%s\n@@ -1 +1 @@\n-SCHEDULE = 300\n+SCHEDULE = 310\n"
                      % (self.target_rel, self.target_rel))
        rc = self._run_apply(pdir)
        self.assertEqual(rc, 2)
        self.assertIn("备份失败", self._ledger_rows()[-1]["reason"])
        os.chmod(target, 0o644)
        self.assertEqual(open(target).read(), "SCHEDULE = 300\n",
                         "备份阶段失败不得改动/删除原文件")

    def test_rollback_removes_empty_parent_dirs(self):
        # 新建文件在深层新目录:rollback 删除后空父目录必须清干净(六轮对抗 低)
        rel = "agents/runtime/v3.3/newdir/deep/created.py"
        self.policy["auto_fix_classes"]["monitor_schedule_temp"]["paths"].append(rel)
        diff = ("--- /dev/null\n+++ b/%s\n@@ -0,0 +1 @@\n+x\n" % rel)
        pdir = os.path.join(self.tmp, "pv_emptydir")
        make_proposal(pdir, {"root": "kit", "class": "monitor_schedule_temp",
                             "tier": "auto"}, diff)
        rc = self._run_apply(pdir)
        self.assertEqual(rc, 0)
        target = os.path.join(self.kit, rel)
        self.assertTrue(os.path.exists(target))
        row = self._ledger_rows()[-1]
        old_argv = sys.argv
        sys.argv = ["apply_proposal.py", "--rollback", row["id"]]
        try:
            rc = apply_proposal.main()
        finally:
            sys.argv = old_argv
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(target))
        self.assertFalse(os.path.exists(os.path.join(self.kit, "agents/runtime/v3.3/newdir")),
                         "rollback 后空父目录不得残留")
        self.assertTrue(os.path.isdir(os.path.join(self.kit, "agents/runtime/v3.3")),
                        "既存非空目录不得被误删")


KIT_BASE = os.environ.get(
    "MDSIM_KIT_BASE",
    "/opt/mdagent/kit")
KIT_AGENTS = os.path.join(KIT_BASE, "agents", "runtime", "v3.3")


@unittest.skipUnless(os.path.isdir(KIT_AGENTS), "kit runtime 不在,跳过 E2E")
class TestE2EEvolutionKit(SandboxCase):
    """阶段4 E2E(2026-08-30):注故障→recover_agent 记 pending→结账(成功/同因复发
    失败)→后验下降→observe 连败起草提案→apply 真实通道应用→select_strategy 被
    penalty 压制→--rollback 还原。全程沙盒 + 打桩(研究/重启),不碰在役文件。"""

    def setUp(self):
        super().setUp()
        if KIT_AGENTS not in sys.path:
            sys.path.insert(0, KIT_AGENTS)
        import recover_agent
        self.ra = recover_agent
        self.db_path = os.path.join(self.kit, ".v3.3_strategy_db.json")
        self.old_ra = (recover_agent.STRATEGY_DB, recover_agent.run_research,
                       recover_agent.restart_production)
        recover_agent.STRATEGY_DB = self.db_path
        recover_agent.run_research = lambda *a, **k: None      # 不联网
        recover_agent.restart_production = lambda *a, **k: [4242]  # 假 pid
        self.addCleanup(self._restore_extra)
        # 初始策略 db:load_db 内置默认(6 策略,空 history)
        # 注意先取默认再写:open(...,"w") 会先建空文件,load_db 读到即崩
        default_db = recover_agent.load_db()
        with open(self.db_path, "w") as f:
            json.dump(default_db, f, indent=2)
        # fixture prod_dir:production.in 含 fix QEQ(delete_overlap 锚点)
        self.prod_dir = os.path.join(self.tmp, "prod")
        os.makedirs(self.prod_dir)
        with open(os.path.join(self.prod_dir, "production.in"), "w") as f:
            f.write("fix QEQ all qeq/reax 1 0.0 10.0 1.0e-6\n"
                    "timestep 0.25\nrun 50000\n")
        # apply 通道允许改策略 db
        self.policy["auto_fix_classes"]["strategy_weight_tuning"] = {
            "root": "kit", "paths": [".v3.3_strategy_db.json"],
            "max_changed_lines": 12}
        # observe/memory 沙盒(口径同 TestObserveKit)
        self.old_ob = (observe.FLOWGATE_LOG, observe.PIPELINE_DIR, observe.PROPOSALS,
                       observe.LEGACY_MCP_DIR, observe._kit_db_path, observe.PIPELINE_EVENTS)
        observe.FLOWGATE_LOG = os.path.join(self.tmp, "none.jsonl")
        observe.PIPELINE_DIR = os.path.join(self.tmp, "none-dir")
        observe.PIPELINE_EVENTS = os.path.join(self.tmp, "none-pipeline.jsonl")
        # friction 信号流沙盒(2026-09-13 补:真实 friction.jsonl 有活会话轮记录,
        # 不隔离会让 observe 额外起草 friction 提案,drafted 计数被真数据污染)
        self.addCleanup(setattr, observe, "FRICTION_LOG", observe.FRICTION_LOG)
        observe.FRICTION_LOG = os.path.join(self.tmp, "friction.jsonl")
        # 用户闸桥/observe 闸同样沙盒:human 提案起草会投 kit 收件箱,绝不碰真 inbox
        # (2026-09-07 KB_PATH 同型教训;addCleanup 注册时捕获旧值,免改 _restore)
        self.addCleanup(setattr, observe, "KIT_GATE_INBOX", observe.KIT_GATE_INBOX)
        observe.KIT_GATE_INBOX = os.path.join(self.tmp, "user_gate_requests.jsonl")
        self.addCleanup(setattr, observe, "OBSERVE_GATE_FILE", observe.OBSERVE_GATE_FILE)
        observe.OBSERVE_GATE_FILE = os.path.join(self.tmp, "observe_gate.json")
        observe.PROPOSALS = os.path.join(self.tmp, "proposals")
        observe.LEGACY_MCP_DIR = os.path.join(self.tmp, "none-legacy")
        observe._kit_db_path = lambda: self.db_path
        self.old_mem = (memory.EXP_DIR, memory.ATOMS, memory.SCENARIOS,
                        memory.SOP, memory.LEDGER)
        memory.EXP_DIR = os.path.join(self.tmp, "experience")
        memory.ATOMS = os.path.join(memory.EXP_DIR, "atoms.jsonl")
        memory.SCENARIOS = os.path.join(memory.EXP_DIR, "scenarios.json")
        memory.SOP = os.path.join(memory.EXP_DIR, "SOP.md")
        memory.LEDGER = os.path.join(self.tmp, "mem-LEDGER.jsonl")
        # 蜂群 KB 沙盒:promote/demote 横扫写 canary_outcome 剧集,绝不碰真 KB
        self.old_kb = specialist_swarm.KB_PATH
        specialist_swarm.KB_PATH = os.path.join(self.tmp, "repair_kb.jsonl")

    def _restore_extra(self):
        (self.ra.STRATEGY_DB, self.ra.run_research,
         self.ra.restart_production) = self.old_ra
        (observe.FLOWGATE_LOG, observe.PIPELINE_DIR, observe.PROPOSALS,
         observe.LEGACY_MCP_DIR, observe._kit_db_path, observe.PIPELINE_EVENTS) = self.old_ob
        specialist_swarm.KB_PATH = self.old_kb
        (memory.EXP_DIR, memory.ATOMS, memory.SCENARIOS,
         memory.SOP, memory.LEDGER) = self.old_mem

    def _run_main(self, n):
        # 每轮刷新 production.in:生产上每个 case 都是新算例;复用旧文件会让
        # delete_overlap 幂等空转——20 轮起空转判失败(no-op 不记账),结账序列会断
        with open(os.path.join(self.prod_dir, "production.in"), "w") as f:
            f.write("fix QEQ all qeq/reax 1 0.0 10.0 1.0e-6\n"
                    "timestep 0.25\nrun 50000\n")
        in_path = os.path.join(self.tmp, "in%d.json" % n)
        out_path = os.path.join(self.tmp, "out%d.json" % n)
        json.dump({"prod_dir": self.prod_dir,
                   "anomalies": [{"type": "LOST_ATOMS", "message": "Lost atoms"}],
                   "history": [], "risk_score": 5, "risk_factors": [],
                   "case_id": "e2e1", "cores": 4}, open(in_path, "w"))
        old_argv = sys.argv
        sys.argv = ["recover_agent.py", in_path, out_path]
        try:
            self.ra.main()
        finally:
            sys.argv = old_argv
        return json.load(open(out_path))

    def _settle(self, outcome):
        # 复刻 orchestrator 结账口径:DONE→全 success;同因复发→fail(≤7d)
        if outcome == "success":
            self.ra.locked_update(lambda d: self.ra.settle_pending(
                d, "success", case_id="e2e1"))
        else:
            self.ra.locked_update(lambda d: self.ra.settle_pending(
                d, "fail", case_id="e2e1", root_cause="atomic_ejection",
                younger_than_days=7))

    def test_full_loop_fault_to_rollback(self):
        r = self._run_main(1)
        self.assertTrue(r["success"], r.get("error"))
        self.assertEqual((r["strategy"], r["root_cause"]), ("A", "atomic_ejection"))
        db = json.load(open(self.db_path))
        self.assertEqual(len(db["history"]), 1)
        self.assertEqual(db["history"][0]["outcome"], "pending")

        self._settle("success")   # 稳定运行 → DONE 口径
        peak = json.load(open(self.db_path))["strategies"]["A"]["success_rate"]
        self.assertAlmostEqual(peak, round(2 / 3, 4))  # Beta(1,1): 1胜 → 2/3

        self._run_main(2); self._settle("success")
        self._run_main(3); self._settle("fail")   # 同因复发 ×2
        self._run_main(4); self._settle("fail")
        db = json.load(open(self.db_path))
        sr = db["strategies"]["A"]["success_rate"]
        self.assertAlmostEqual(sr, 0.5)           # 2胜2负 → 3/6
        self.assertLess(sr, peak, "同因连败后后验必须下降")

        # observe:尾部连败 2 → 起草 auto 压制提案
        summary = observe.observe(days=7, min_occurrences=3)
        self.assertEqual(len(summary["drafted"]), 1)
        pdir = summary["drafted"][0]
        meta = json.load(open(os.path.join(pdir, "meta.json")))
        self.assertEqual((meta["class"], meta["tier"], meta["root"]),
                         ("strategy_weight_tuning", "auto", "kit"))

        # apply 真实通道:AUTO_APPLIED + penalty 落库
        rc = self._run_apply(pdir)
        self.assertEqual(rc, 0)
        self.assertEqual(self._ledger_rows()[-1]["result"], "AUTO_APPLIED")
        applied = json.load(open(self.db_path))
        adj = applied["strategy_adjustments"]["A:atomic_ejection"]
        self.assertEqual(adj["penalty"], 0.15)

        # 压制生效:A(3.5-0.15) 让位 D(3.5)
        anomalies = [{"type": "LOST_ATOMS"}]
        sid, _ = self.ra.select_strategy(applied, "atomic_ejection", anomalies, [], [])
        self.assertEqual(sid, "D")

        # --rollback 还原:penalty 消失,A(平分局优先)重新胜出
        lid = self._ledger_rows()[-1]["id"]
        old_argv = sys.argv
        sys.argv = ["apply_proposal.py", "--rollback", lid]
        try:
            self.assertEqual(apply_proposal.main(), 0)
        finally:
            sys.argv = old_argv
        restored = json.load(open(self.db_path))
        self.assertNotIn("strategy_adjustments", restored)
        self.assertAlmostEqual(restored["strategies"]["A"]["success_rate"], 0.5,
                               "回滚只动 adjustments,kit 自写的后验不动")
        sid, _ = self.ra.select_strategy(restored, "atomic_ejection", anomalies, [], [])
        self.assertEqual(sid, "A")


# ---------------------------------------------------------------------------
# L2 证据层(2026-09-03):evidence tier 门禁、install/promote/demote 证据门、
# 沙盒试跑、observe 起草/横扫、R6 回滚 db 时限。
# ---------------------------------------------------------------------------
import difflib  # noqa: E402

REAL_KIT = os.environ.get(
    "MDSIM_KIT_BASE",
    "/opt/mdagent/kit")
REAL_V33 = os.path.join(REAL_KIT, "agents", "runtime", "v3.3")

EVIDENCE_SPEC = {
    "root": "kit", "paths": [".v3.3_strategy_db.json"], "max_changed_lines": 40,
    "evidence": {"sandbox_trial": True, "canary_attempts": 4, "canary_min_wins": 3,
                 "min_posterior": 0.5, "demote_fail_streak": 3,
                 "demote_cooldown_days": 7, "canary_max_age_days": 30,
                 "allowed_templates": ["timestep_floor_damp", "qeq_relax_stronger",
                                       "nve_limit_tighten"]}}


def _legacy_db():
    names = ["delete_overlap", "reduce_timestep", "langevin_damp",
             "nve_limit", "reduce_heating", "qeq_relax"]
    return {"strategies": {sid: {"name": n, "desc": "", "for": [],
                                 "success_rate": 0.5, "uses": 0}
                           for sid, n in zip("ABCDEF", names)},
            "history": [], "strategy_adjustments": {}}


def _now_ts():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _settled(sid, cause, outcome, i=0, ts=None):
    return {"strategy": sid, "root_cause": cause, "case_id": "c%d" % i,
            "ts": ts or _now_ts(), "outcome": outcome}


def _exhausted_db(cause="thermal_runaway", sid="B"):
    """install 证据门"仍需要"复核用的耗尽库:sid×cause 4 连败 + penalty 压制
    (与 observe._exhausted_causes 判定口径对齐:14d 窗 fails≥4 且全压制)。"""
    db = _legacy_db()
    db["history"] = [_settled(sid, cause, "fail", i) for i in range(4)]
    db["strategy_adjustments"] = {"%s:%s" % (sid, cause): {
        "penalty": 0.6, "reason": "连败压制", "ts": _now_ts()}}
    return db


class EvidenceCase(SandboxCase):
    """evidence 层沙盒:kit 内含真 recover_agent/specialist_rules 副本 + 策略库。"""

    def setUp(self):
        super().setUp()
        for fn in ("recover_agent.py", "specialist_rules.py"):
            shutil.copy2(os.path.join(REAL_V33, fn),
                         os.path.join(self.kit, "agents", "runtime", "v3.3", fn))
        self.db_path = os.path.join(self.kit, ".v3.3_strategy_db.json")
        self._write_db(_exhausted_db())  # install 门要求根因仍耗尽
        self.policy["evidence_classes"] = {
            "new_specialist_script": json.loads(json.dumps(EVIDENCE_SPEC))}
        self.policy["auto_fix_classes"]["strategy_weight_tuning"] = {
            "root": "kit", "paths": [".v3.3_strategy_db.json"],
            "max_changed_lines": 40}

    def _write_db(self, db):
        with open(self.db_path, "w") as f:
            f.write(json.dumps(db, indent=2) + "\n")

    def _db_diff(self, old_db, new_db):
        old = json.dumps(old_db, indent=2) + "\n"
        new = json.dumps(new_db, indent=2) + "\n"
        return "\n".join(difflib.unified_diff(
            old.splitlines(), new.splitlines(),
            fromfile="a/.v3.3_strategy_db.json",
            tofile="b/.v3.3_strategy_db.json", n=3, lineterm="")) + "\n"

    def _meta(self, op, sid, tier="evidence"):
        return {"root": "kit", "class": "new_specialist_script", "tier": tier,
                "op": op, "specialist_id": sid}

    def _install_proposal(self, sid="G", template_id="timestep_floor_damp",
                          params=None, mutate=None, name="p"):
        params = params if params is not None else {"timestep": "0.1"}
        old = json.load(open(self.db_path))  # 以盘上实况为旧库(防 ts 秒级漂移)
        new = json.loads(json.dumps(old))
        entry = specialist_templates.instantiate(template_id, sid, params)
        if mutate:
            mutate(entry, new)
        new["strategies"][sid] = entry
        pdir = os.path.join(self.tmp, name)
        make_proposal(pdir, self._meta("install", sid), self._db_diff(old, new))
        return pdir

    def _apply_install(self, sid="G"):
        rc = self._run_apply(self._install_proposal(sid))
        self.assertEqual(rc, 0, "install 应过证据门")
        return json.load(open(self.db_path))


class TestEvidencePolicyGate(EvidenceCase):
    """check_policy evidence 分支:类外拒、路径/行数界、forbidden 优先、approver 不豁免。"""

    def test_install_happy_no_approver(self):
        rc = self._run_apply(self._install_proposal())
        self.assertEqual(rc, 0)
        row = self._ledger_rows()[-1]
        self.assertEqual(row["result"], "AUTO_APPLIED")
        self.assertEqual(row["tier"], "evidence")
        db = json.load(open(self.db_path))
        self.assertIs(db["strategies"]["G"]["canary"], True)

    def test_class_not_registered_rejected(self):
        old = json.load(open(self.db_path))
        new = json.loads(json.dumps(old))
        new["strategies"]["G"] = specialist_templates.instantiate(
            "timestep_floor_damp", "G", {"timestep": "0.1"})
        pdir = os.path.join(self.tmp, "p")
        make_proposal(pdir, {"root": "kit", "class": "bogus_class",
                             "tier": "evidence", "op": "install",
                             "specialist_id": "G"}, self._db_diff(old, new))
        self.assertEqual(self._run_apply(pdir), 2)
        self.assertIn("evidence_classes", self._ledger_rows()[-1]["reason"])

    def test_path_outside_declared_rejected(self):
        diff = ("--- a/%s\n+++ b/%s\n@@ -1 +1 @@\n-SCHEDULE = 300\n+SCHEDULE = 310\n"
                % (self.target_rel, self.target_rel))
        pdir = os.path.join(self.tmp, "p")
        make_proposal(pdir, self._meta("install", "G"), diff)
        self.assertEqual(self._run_apply(pdir), 2)
        self.assertIn("只许改", self._ledger_rows()[-1]["reason"])

    def test_max_changed_lines_enforced(self):
        self.policy["evidence_classes"]["new_specialist_script"][
            "max_changed_lines"] = 2
        self.assertEqual(self._run_apply(self._install_proposal()), 2)
        self.assertIn("上界", self._ledger_rows()[-1]["reason"])

    def test_forbidden_still_first(self):
        diff = ("--- a/evolution/POLICY.json\n+++ b/evolution/POLICY.json\n"
                "@@ -1 +1 @@\n-x\n+y\n")
        pdir = os.path.join(self.tmp, "p")
        make_proposal(pdir, self._meta("install", "G"), diff)
        self.assertEqual(self._run_apply(pdir), 2)
        self.assertIn("红线", self._ledger_rows()[-1]["reason"])

    def test_approver_does_not_exempt_evidence(self):
        def tamper(entry, new):
            entry["rule"]["replace"]["template"] = "timestep 0.2"
        pdir = self._install_proposal(mutate=tamper)
        rc = self._run_apply(pdir, "--approver", "boss")
        self.assertEqual(rc, 2)  # approver 只入账,不豁免证据检查


class TestEvidenceInstallGate(EvidenceCase):
    """install 证据门:单写者纪律、canary 必须、模板产物逐字节、沙盒重执行。"""

    def test_history_tamper_rejected(self):
        # 放宽行数界:让证据门(而非行数闸)成为拒绝点
        self.policy["evidence_classes"]["new_specialist_script"][
            "max_changed_lines"] = 200
        def m(entry, new):
            new["history"].append(_settled("A", "x", "fail"))
        self.assertEqual(self._run_apply(self._install_proposal(mutate=m)), 2)
        self.assertIn("history", self._ledger_rows()[-1]["reason"])

    def test_adjustments_tamper_rejected(self):
        def m(entry, new):
            new["strategy_adjustments"]["A:x"] = {"penalty": 0.6}
        self.assertEqual(self._run_apply(self._install_proposal(mutate=m)), 2)
        self.assertIn("strategy_adjustments", self._ledger_rows()[-1]["reason"])

    def test_top_level_key_smuggle_rejected(self):
        # 21 轮对抗 F1:install 顺手夹带/删除顶层键(原只锁三大键)
        # 放宽行数界:让证据门(而非行数闸)成为拒绝点
        self.policy["evidence_classes"]["new_specialist_script"][
            "max_changed_lines"] = 200
        def m(entry, new):
            new["backdoor_top"] = {"x": 1}
        self.assertEqual(self._run_apply(self._install_proposal(mutate=m)), 2)
        self.assertIn("顶层键", self._ledger_rows()[-1]["reason"])

        def m2(entry, new):
            del new["strategy_adjustments"]  # 删既有顶层键同拒
        self.assertEqual(self._run_apply(self._install_proposal(mutate=m2)), 2)
        self.assertIn("顶层键", self._ledger_rows()[-1]["reason"])

    def test_canary_false_rejected(self):
        def m(entry, new):
            entry["canary"] = False
        self.assertEqual(self._run_apply(self._install_proposal(mutate=m)), 2)
        self.assertIn("canary", self._ledger_rows()[-1]["reason"])

    def test_off_template_rejected(self):
        def m(entry, new):
            entry["rule"]["replace"]["template"] = "timestep 0.2"
        self.assertEqual(self._run_apply(self._install_proposal(mutate=m)), 2)
        self.assertIn("模板产物", self._ledger_rows()[-1]["reason"])

    def test_template_not_allowed_rejected(self):
        self.policy["evidence_classes"]["new_specialist_script"]["evidence"][
            "allowed_templates"] = ["nve_limit_tighten"]
        self.assertEqual(self._run_apply(self._install_proposal()), 2)
        self.assertIn("allowed_templates", self._ledger_rows()[-1]["reason"])

    def test_forged_sandbox_report_not_trusted(self):
        # 提案自带"试跑通过"报告,门仍重执行并按模板产物比对 → off-template 照拒
        def m(entry, new):
            entry["params"]["timestep"] = "0.25"  # 与 rule 文本 0.1 不一致
        pdir = self._install_proposal(mutate=m)
        with open(os.path.join(pdir, "sandbox_report.json"), "w") as f:
            json.dump({"ok": True, "checks": {}}, f)
        self.assertEqual(self._run_apply(pdir), 2)

    def test_bad_sid_rejected(self):
        self.assertEqual(
            self._run_apply(self._install_proposal(sid="abc")), 2)

    def test_missing_specialist_rules_failclosed(self):
        os.remove(os.path.join(self.kit, "agents", "runtime", "v3.3",
                               "specialist_rules.py"))
        self.assertEqual(self._run_apply(self._install_proposal()), 2)

    def test_multi_file_rejected(self):
        old = json.load(open(self.db_path))
        new = json.loads(json.dumps(old))
        new["strategies"]["G"] = specialist_templates.instantiate(
            "timestep_floor_damp", "G", {"timestep": "0.1"})
        diff = self._db_diff(old, new) + (
            "--- a/%s\n+++ b/%s\n@@ -1 +1 @@\n-SCHEDULE = 300\n+SCHEDULE = 310\n"
            % (self.target_rel, self.target_rel))
        pdir = os.path.join(self.tmp, "p")
        make_proposal(pdir, self._meta("install", "G"), diff)
        self.assertEqual(self._run_apply(pdir), 2)
        # 多文件提案在 check_policy 路径闸即被拒(只许改策略库),根本到不了证据门
        self.assertIn("只许改", self._ledger_rows()[-1]["reason"])

    def test_invalid_rule_rejected(self):
        def m(entry, new):
            entry["rule"]["replace"]["count"] = 99
        self.assertEqual(self._run_apply(self._install_proposal(mutate=m)), 2)

    # ---- 20 轮对抗 ----
    def test_install_no_longer_needed_rejected(self):
        # 草稿搁置期间根因不再耗尽(尾部胜账破连败 + penalty 解除)→ install 拒
        pdir = self._install_proposal()
        db = json.load(open(self.db_path))
        db["history"].append(_settled("B", "thermal_runaway", "success", 99))
        db["strategy_adjustments"] = {}
        self._write_db(db)
        rc = self._run_apply(pdir)
        self.assertEqual(rc, 2)
        self.assertIn("不再", self._ledger_rows()[-1]["reason"])
        db = json.load(open(self.db_path))
        self.assertNotIn("G", db["strategies"])

    def test_post_apply_recheck_catches_simulate_apply_gap(self):
        # simulate/apply TOCTOU(此处以 mock simulate 撒谎模拟间隙写盘):
        # 落盘后复核必须发现 history 被走私 → ROLLED_BACK + 还原(非 REJECTED)
        old = json.load(open(self.db_path))
        new = json.loads(json.dumps(old))
        new["strategies"]["G"] = specialist_templates.instantiate(
            "timestep_floor_damp", "G", {"timestep": "0.1"})
        new["history"].append(_settled("G", "thermal_runaway", "success", 0))
        pdir = os.path.join(self.tmp, "pt")
        make_proposal(pdir, self._meta("install", "G"), self._db_diff(old, new))
        orig = apply_proposal._simulate_db_patch

        def lying(root_path, changed):
            clean = json.loads(json.dumps(new))
            clean["history"] = old["history"]  # 声称没有走私行
            return (old, clean), None
        apply_proposal._simulate_db_patch = lying
        try:
            rc = self._run_apply(pdir)
        finally:
            apply_proposal._simulate_db_patch = orig
        self.assertEqual(rc, 3)
        row = self._ledger_rows()[-1]
        self.assertEqual(row["result"], "ROLLED_BACK")
        self.assertIn("复核", row["reason"])
        db = json.load(open(self.db_path))
        self.assertNotIn("G", db["strategies"])  # 已还原
        self.assertEqual(len(db["history"]), 4)  # 走私行未留


class TestPromoteGateRecompute(EvidenceCase):
    """promote 证据门:统计一律按当前盘重算(TOCTOU),不信提案自证。"""

    def _promote_proposal(self, sid="G", mutate=None, name="pp"):
        old = json.load(open(self.db_path))
        new = json.loads(json.dumps(old))
        entry = {k: v for k, v in old["strategies"][sid].items() if k != "canary"}
        if mutate:
            mutate(entry, new)
        new["strategies"][sid] = entry
        pdir = os.path.join(self.tmp, name)
        make_proposal(pdir, self._meta("promote", sid), self._db_diff(old, new))
        return pdir

    def _seed_wins(self, sid="G", wins=3, fails=1):
        db = json.load(open(self.db_path))
        db["history"] = [_settled(sid, "thermal_runaway", "success", i)
                         for i in range(wins)] + \
                        [_settled(sid, "thermal_runaway", "fail", 99)]
        if fails == 0:
            db["history"] = db["history"][:wins]
        self._write_db(db)

    def test_promote_happy(self):
        self._apply_install()
        self._seed_wins()
        rc = self._run_apply(self._promote_proposal())
        self.assertEqual(rc, 0)
        db = json.load(open(self.db_path))
        self.assertNotIn("canary", db["strategies"]["G"])
        self.assertEqual(set(db["strategies"]), set("ABCDEFG"))

    def test_insufficient_stats_rejected(self):
        self._apply_install()
        db = json.load(open(self.db_path))
        db["history"] = [_settled("G", "thermal_runaway", "success", i)
                         for i in range(2)]
        self._write_db(db)
        self.assertEqual(self._run_apply(self._promote_proposal()), 2)
        self.assertIn("实账不足", self._ledger_rows()[-1]["reason"])

    def test_history_changed_after_draft_rejected(self):
        self._apply_install()
        self._seed_wins()
        pdir = self._promote_proposal()  # 起草时账够
        db = json.load(open(self.db_path))
        db["history"] = []  # 起草后历史被改(灭证):按当前盘重算必须拒
        self._write_db(db)
        self.assertEqual(self._run_apply(pdir), 2)

    def test_promote_touching_other_keys_rejected(self):
        self._apply_install()
        self._seed_wins()
        def m(entry, new):
            entry["desc"] = "篡改"
        self.assertEqual(self._run_apply(self._promote_proposal(mutate=m)), 2)

    # ---- 20 轮对抗 ----
    def test_promote_reversed_streak_rejected(self):
        # 3 胜后 3 连败:累计账面达标,但尾部连败触降级线 → 不得晋升(统计反转)
        self._apply_install()
        db = json.load(open(self.db_path))
        db["history"] = [_settled("G", "thermal_runaway", "success", i)
                         for i in range(3)] + \
                        [_settled("G", "thermal_runaway", "fail", i)
                         for i in range(3, 6)]
        self._write_db(db)
        rc = self._run_apply(self._promote_proposal())
        self.assertEqual(rc, 2)
        self.assertIn("连败", self._ledger_rows()[-1]["reason"])
        db = json.load(open(self.db_path))
        self.assertIs(db["strategies"]["G"]["canary"], True)  # 未晋升

    def test_promote_tampered_lineage_rejected(self):
        # install 后条目被篡改(rule 换参):血统校验必须拦下晋升(防篡改洗白)
        self._apply_install()
        self._seed_wins()
        db = json.load(open(self.db_path))
        db["strategies"]["G"]["rule"]["replace"]["template"] = "timestep 0.25"
        self._write_db(db)
        rc = self._run_apply(self._promote_proposal())
        self.assertEqual(rc, 2)
        self.assertIn("血统", self._ledger_rows()[-1]["reason"])

    def test_human_tier_still_passes_evidence_gate(self):
        # tier=human + approver 不构成证据门旁路:挂 evidence 类一律过门
        self._apply_install()
        self._seed_wins()
        old = json.load(open(self.db_path))
        new = json.loads(json.dumps(old))
        new["strategies"]["G"] = {k: v for k, v in
                                  old["strategies"]["G"].items() if k != "canary"}
        pdir = os.path.join(self.tmp, "ph")
        make_proposal(pdir, self._meta("promote", "G", tier="human"),
                      self._db_diff(old, new))
        # 账面合法 + approver → 过(approver 入账)
        rc = self._run_apply(pdir, "--approver", "boss")
        self.assertEqual(rc, 0)
        self.assertEqual(self._ledger_rows()[-1]["approver"], "boss")

    def test_human_tier_bypass_rejected(self):
        # tier=human + approver 但证据不成立(账不够)→ 照样拒
        self._apply_install()
        old = json.load(open(self.db_path))
        new = json.loads(json.dumps(old))
        new["strategies"]["G"] = {k: v for k, v in
                                  old["strategies"]["G"].items() if k != "canary"}
        pdir = os.path.join(self.tmp, "ph2")
        make_proposal(pdir, self._meta("promote", "G", tier="human"),
                      self._db_diff(old, new))
        rc = self._run_apply(pdir, "--approver", "boss")
        self.assertEqual(rc, 2)
        self.assertIn("实账不足", self._ledger_rows()[-1]["reason"])


class TestDemoteGate(EvidenceCase):
    """demote 证据门:恰删该键、只适用金丝雀、条件按盘重算。"""

    def _demote_proposal(self, sid="G", name="pd"):
        old = json.load(open(self.db_path))
        new = json.loads(json.dumps(old))
        del new["strategies"][sid]
        pdir = os.path.join(self.tmp, name)
        make_proposal(pdir, self._meta("demote", sid), self._db_diff(old, new))
        return pdir

    def test_demote_happy_fail_streak(self):
        self._apply_install()
        db = json.load(open(self.db_path))
        db["history"] = [_settled("G", "thermal_runaway", "fail", i)
                         for i in range(3)]
        self._write_db(db)
        self.assertEqual(self._run_apply(self._demote_proposal()), 0)
        db = json.load(open(self.db_path))
        self.assertNotIn("G", db["strategies"])

    def test_demote_condition_unmet_rejected(self):
        self._apply_install()
        db = json.load(open(self.db_path))
        db["history"] = [_settled("G", "thermal_runaway", "fail", 0)]
        self._write_db(db)
        self.assertEqual(self._run_apply(self._demote_proposal()), 2)
        self.assertIn("降级条件不成立", self._ledger_rows()[-1]["reason"])

    def test_demote_non_canary_rejected(self):
        self.assertEqual(self._run_apply(self._demote_proposal(sid="A")), 2)
        self.assertIn("金丝雀", self._ledger_rows()[-1]["reason"])

    def test_demote_starved(self):
        self._apply_install()
        old_ts = time.strftime("%Y-%m-%dT%H:%M:%S",
                               time.localtime(time.time() - 40 * 86400))
        db = json.load(open(self.db_path))
        db["history"] = [_settled("G", "thermal_runaway", "success", 0, ts=old_ts)]
        self._write_db(db)
        self.assertEqual(self._run_apply(self._demote_proposal()), 0)

    # ---- 20 轮对抗 ----
    def test_demote_ghost_sid_rejected(self):
        # 幽灵降级:meta 指向不存在的策略——此前 old_s[sid] KeyError 崩门,现显式拒
        old = json.load(open(self.db_path))
        new = json.loads(json.dumps(old))
        del new["strategies"]["B"]  # diff 真删了东西,但 meta 说的是另一个 sid
        pdir = os.path.join(self.tmp, "pdg")
        make_proposal(pdir, self._meta("demote", "Q"), self._db_diff(old, new))
        rc = self._run_apply(pdir)
        self.assertEqual(rc, 2)
        self.assertIn("不存在", self._ledger_rows()[-1]["reason"])

    def test_demote_zombie_via_ledger_install_ts(self):
        # 僵尸金丝雀(零实账):history 无锚,以 LEDGER install ts 计龄,超 30 天饿死降级
        self._apply_install()
        rows = self._ledger_rows()
        for r in rows:  # 把 install 入账时间改到 40 天前
            r["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S",
                                    time.localtime(time.time() - 40 * 86400))
        with open(apply_proposal.LEDGER, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        db = json.load(open(self.db_path))
        db["history"] = []  # 零实账(原耗尽史清空,排除连败路径)
        db["strategy_adjustments"] = {}
        self._write_db(db)
        self.assertEqual(self._run_apply(self._demote_proposal()), 0)
        db = json.load(open(self.db_path))
        self.assertNotIn("G", db["strategies"])

    def test_demote_young_zombie_rejected(self):
        # 年轻僵尸(装好没多久、零实账):既不连败也不饿死 → 拒
        self._apply_install()
        db = json.load(open(self.db_path))
        db["history"] = []
        db["strategy_adjustments"] = {}
        self._write_db(db)
        self.assertEqual(self._run_apply(self._demote_proposal()), 2)
        self.assertIn("降级条件不成立", self._ledger_rows()[-1]["reason"])

    def test_demote_tampered_lineage_rejected(self):
        self._apply_install()
        db = json.load(open(self.db_path))
        db["strategies"]["G"]["rule"]["replace"]["template"] = "timestep 0.25"
        db["history"] = [_settled("G", "thermal_runaway", "fail", i)
                         for i in range(3)]
        self._write_db(db)
        rc = self._run_apply(self._demote_proposal())
        self.assertEqual(rc, 2)
        self.assertIn("血统", self._ledger_rows()[-1]["reason"])


class TestAutoDbConfinement(EvidenceCase):
    """auto 内容闸(20 轮对抗):auto 提案碰策略库只许动 strategy_adjustments,
    strategies/history 逐字节不动——此前 auto 类只校验路径/行数,内容可走私。"""

    def _auto_proposal(self, mutate, name="pa"):
        old = json.load(open(self.db_path))
        new = json.loads(json.dumps(old))
        mutate(new)
        pdir = os.path.join(self.tmp, name)
        make_proposal(pdir, {"root": "kit", "class": "strategy_weight_tuning",
                             "tier": "auto"}, self._db_diff(old, new))
        return pdir

    def test_adjustments_only_passes(self):
        def m(new):
            new["strategy_adjustments"]["A:x"] = {"penalty": 0.15, "reason": "t",
                                                  "ts": _now_ts()}
        self.assertEqual(self._run_apply(self._auto_proposal(m)), 0)
        db = json.load(open(self.db_path))
        self.assertIn("A:x", db["strategy_adjustments"])

    def test_strategies_smuggle_rejected(self):
        def m(new):
            new["strategies"]["G"] = {"name": "evil", "desc": "", "for": [],
                                      "success_rate": 0.99, "uses": 0}
        rc = self._run_apply(self._auto_proposal(m))
        self.assertEqual(rc, 2)
        self.assertIn("strategies", self._ledger_rows()[-1]["reason"])
        self.assertNotIn("G", json.load(open(self.db_path))["strategies"])

    def test_history_smuggle_rejected(self):
        def m(new):
            new["history"].append(_settled("A", "x", "success"))
        rc = self._run_apply(self._auto_proposal(m))
        self.assertEqual(rc, 2)
        self.assertIn("history", self._ledger_rows()[-1]["reason"])
        self.assertEqual(len(json.load(open(self.db_path))["history"]), 4)

    def test_adjustments_container_swap_rejected(self):
        def m(new):
            new["strategy_adjustments"] = ["not-a-dict"]
        rc = self._run_apply(self._auto_proposal(m))
        self.assertEqual(rc, 2)

    def test_top_level_key_smuggle_rejected(self):
        # 21 轮对抗 F1:auto 提案顺手增/删策略库顶层键
        def m(new):
            new["backdoor_top"] = {"x": 1}
        rc = self._run_apply(self._auto_proposal(m))
        self.assertEqual(rc, 2)
        self.assertIn("顶层键", self._ledger_rows()[-1]["reason"])

        old = json.load(open(self.db_path))
        old["meta_key"] = {"v": 1}
        self._write_db(old)

        def m2(new):
            del new["meta_key"]
        rc = self._run_apply(self._auto_proposal(m2))
        self.assertEqual(rc, 2)
        self.assertIn("meta_key", json.load(open(self.db_path)))

    def test_bool_int_confusion_rejected(self):
        # 21 轮对抗 F2:dict== 下 True==1 穿透"逐字节";canon 文本比对必须拦
        def m(new):
            new["strategies"]["A"]["success_rate"] = True
        rc = self._run_apply(self._auto_proposal(m))
        self.assertEqual(rc, 2)
        db = json.load(open(self.db_path))
        self.assertEqual(db["strategies"]["A"]["success_rate"], 0.5)


class TestRollbackDbAgeGuard(EvidenceCase):
    """R6(用户拍板):策略库条目 >24h 拒绝整文件回滚,走正向修正提案。"""

    def _apply_auto_db_change(self):
        old = json.load(open(self.db_path))
        new = json.loads(json.dumps(old))
        new["strategy_adjustments"]["A:x"] = {"penalty": 0.15, "reason": "t",
                                              "ts": _now_ts()}
        pdir = os.path.join(self.tmp, "pa")
        make_proposal(pdir, {"root": "kit", "class": "strategy_weight_tuning",
                             "tier": "auto"}, self._db_diff(old, new))
        self.assertEqual(self._run_apply(pdir), 0)
        return self._ledger_rows()[-1]["id"]

    def _rollback(self, lid):
        old_argv = sys.argv
        sys.argv = ["apply_proposal.py", "--rollback", lid]
        try:
            return apply_proposal.main()
        finally:
            sys.argv = old_argv

    def test_within_24h_rollback_ok(self):
        lid = self._apply_auto_db_change()
        self.assertEqual(self._rollback(lid), 0)
        db = json.load(open(self.db_path))
        # 回滚只摘除本笔 auto 改动,耗尽种子的压制条目不动
        self.assertNotIn("A:x", db["strategy_adjustments"])
        self.assertIn("B:thermal_runaway", db["strategy_adjustments"])

    def test_older_than_24h_refused(self):
        lid = self._apply_auto_db_change()
        rows = self._ledger_rows()
        rows[0]["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S",
                                      time.localtime(time.time() - 2 * 86400))
        with open(apply_proposal.LEDGER, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        self.assertEqual(self._rollback(lid), 2)
        db = json.load(open(self.db_path))  # 未动
        self.assertIn("A:x", db["strategy_adjustments"])

    def test_unparseable_ts_failclosed(self):
        lid = self._apply_auto_db_change()
        rows = self._ledger_rows()
        rows[0]["ts"] = "garbage"
        with open(apply_proposal.LEDGER, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        self.assertEqual(self._rollback(lid), 2)

    def test_future_ts_failclosed(self):
        # 21 轮对抗 F3:ts 在未来(时钟回拨/伪造)时 age<0 绕过 24h 闸——fail-closed
        lid = self._apply_auto_db_change()
        rows = self._ledger_rows()
        rows[0]["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S",
                                      time.localtime(time.time() + 86400))
        with open(apply_proposal.LEDGER, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        self.assertEqual(self._rollback(lid), 2)
        db = json.load(open(self.db_path))  # 未回滚
        self.assertIn("A:x", db["strategy_adjustments"])


class TestSpecialistSandbox(unittest.TestCase):
    """沙盒试跑:三模板全过;网格外参数/未知模板拒。"""

    def test_all_templates_pass(self):
        for tid in specialist_templates.TEMPLATES:
            ok, report = specialist_sandbox.run_trial(
                tid, "G", specialist_templates.first_params(tid))
            self.assertTrue(ok, "%s: %s" % (tid, json.dumps(
                report.get("checks", {}), ensure_ascii=False)[:400]))

    def test_unknown_template_rejected(self):
        ok, report = specialist_sandbox.run_trial("nope", "G", {})
        self.assertFalse(ok)

    def test_off_grid_params_rejected(self):
        self.assertIsNone(specialist_templates.instantiate(
            "timestep_floor_damp", "G", {"timestep": "0.3"}))

    def test_param_coverage_exact(self):
        self.assertIsNone(specialist_templates.instantiate(
            "timestep_floor_damp", "G", {}))  # 缺
        self.assertIsNone(specialist_templates.instantiate(
            "timestep_floor_damp", "G", {"timestep": "0.1", "x": "1"}))  # 多

    def test_instantiate_deterministic(self):
        a = specialist_templates.instantiate("timestep_floor_damp", "G",
                                             {"timestep": "0.1"})
        b = specialist_templates.instantiate("timestep_floor_damp", "G",
                                             {"timestep": "0.1"})
        self.assertEqual(a, b)  # 无时间戳,apply 门逐字节比对依赖此


class TestObserveSpecialist(unittest.TestCase):
    """observe L2:耗尽起草、活 canary 抑起草、墓碑冷却、promote/demote/饿死、横扫。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.old = (observe.FLOWGATE_LOG, observe.PIPELINE_DIR, observe.PROPOSALS,
                    observe.LEGACY_MCP_DIR, observe._kit_db_path,
                    observe.POLICY_PATH, observe.PIPELINE_EVENTS)
        observe.FLOWGATE_LOG = os.path.join(self.tmp, "none.jsonl")
        observe.PIPELINE_DIR = os.path.join(self.tmp, "none-dir")
        observe.PIPELINE_EVENTS = os.path.join(self.tmp, "none-pipeline.jsonl")
        # friction 信号流沙盒(2026-09-13 补:真实 friction.jsonl 有活会话轮记录,
        # 不隔离会让 observe 额外起草 friction 提案,drafted 计数被真数据污染)
        self.addCleanup(setattr, observe, "FRICTION_LOG", observe.FRICTION_LOG)
        observe.FRICTION_LOG = os.path.join(self.tmp, "friction.jsonl")
        # 用户闸桥/observe 闸同样沙盒:human 提案起草会投 kit 收件箱,绝不碰真 inbox
        # (2026-09-07 KB_PATH 同型教训;addCleanup 注册时捕获旧值,免改 _restore)
        self.addCleanup(setattr, observe, "KIT_GATE_INBOX", observe.KIT_GATE_INBOX)
        observe.KIT_GATE_INBOX = os.path.join(self.tmp, "user_gate_requests.jsonl")
        self.addCleanup(setattr, observe, "OBSERVE_GATE_FILE", observe.OBSERVE_GATE_FILE)
        observe.OBSERVE_GATE_FILE = os.path.join(self.tmp, "observe_gate.json")
        observe.PROPOSALS = os.path.join(self.tmp, "proposals")
        observe.LEGACY_MCP_DIR = os.path.join(self.tmp, "none-legacy")
        self.db_path = os.path.join(self.tmp, ".v3.3_strategy_db.json")
        observe._kit_db_path = lambda: self.db_path
        pol = {"roots": {"kit": {"path": self.tmp}},
               "evidence_classes": {"new_specialist_script":
                                    json.loads(json.dumps(EVIDENCE_SPEC))}}
        self.pol_path = os.path.join(self.tmp, "POLICY.json")
        with open(self.pol_path, "w") as f:
            json.dump(pol, f)
        observe.POLICY_PATH = self.pol_path
        self.old_memory_dir = observe.paths.memory_dir
        observe.paths.memory_dir = lambda: self.tmp
        self.old_mem = (memory.EXP_DIR, memory.ATOMS, memory.SCENARIOS,
                        memory.SOP, memory.LEDGER)
        memory.EXP_DIR = os.path.join(self.tmp, "experience")
        memory.ATOMS = os.path.join(memory.EXP_DIR, "atoms.jsonl")
        memory.SCENARIOS = os.path.join(memory.EXP_DIR, "scenarios.json")
        memory.SOP = os.path.join(memory.EXP_DIR, "SOP.md")
        memory.LEDGER = os.path.join(self.tmp, "LEDGER.jsonl")
        # 蜂群 KB 沙盒:canary 横扫会写 canary_outcome 剧集,绝不碰真 repair_kb.jsonl
        self.old_kb = specialist_swarm.KB_PATH
        specialist_swarm.KB_PATH = os.path.join(self.tmp, "repair_kb.jsonl")
        self.addCleanup(self._restore)

    def _restore(self):
        (observe.FLOWGATE_LOG, observe.PIPELINE_DIR, observe.PROPOSALS,
         observe.LEGACY_MCP_DIR, observe._kit_db_path,
         observe.POLICY_PATH, observe.PIPELINE_EVENTS) = self.old
        observe.paths.memory_dir = self.old_memory_dir
        specialist_swarm.KB_PATH = self.old_kb
        (memory.EXP_DIR, memory.ATOMS, memory.SCENARIOS,
         memory.SOP, memory.LEDGER) = self.old_mem

    def _write_db(self, db):
        with open(self.db_path, "w") as f:
            f.write(json.dumps(db, indent=2) + "\n")

    def _exhausted_db(self, cause="thermal_runaway", fails=5):
        db = _legacy_db()
        db["history"] = [_settled("B", cause, "fail", i) for i in range(fails)]
        db["strategy_adjustments"] = {
            "B:%s" % cause: {"penalty": 0.6, "reason": "r", "ts": _now_ts()}}
        return db

    def _drafts(self):
        if not os.path.isdir(observe.PROPOSALS):
            return {}
        out = {}
        for name in sorted(os.listdir(observe.PROPOSALS)):
            pdir = os.path.join(observe.PROPOSALS, name)
            out[name] = {
                "meta": json.load(open(os.path.join(pdir, "meta.json"))),
                "diff": open(os.path.join(pdir, "diff.patch")).read(),
            }
        return out

    def test_exhausted_cause_drafts_install(self):
        self._write_db(self._exhausted_db())
        observe.observe(days=7, min_occurrences=3)
        drafts = self._drafts()
        inst = [d for n, d in drafts.items() if "specialist-install-" in n]
        self.assertEqual(len(inst), 1)
        meta = inst[0]["meta"]
        self.assertEqual((meta["class"], meta["tier"], meta["op"]),
                         ("new_specialist_script", "evidence", "install"))
        self.assertRegexpMatches(meta["specialist_id"], r"^[A-Z]$")
        files = apply_proposal.parse_unified_diff(inst[0]["diff"])
        self.assertEqual([f["new"] for f in files], [".v3.3_strategy_db.json"])

    def test_below_threshold_no_draft(self):
        self._write_db(self._exhausted_db(fails=3))
        observe.observe(days=7, min_occurrences=3)
        self.assertFalse([n for n in self._drafts() if "specialist-" in n])

    def test_recent_success_breaks_exhaustion(self):
        # B 尾部连胜(无 penalty 且连败尾 <2)→ 工具箱未耗尽,不起草
        db = self._exhausted_db()
        db["strategy_adjustments"] = {}
        db["history"] += [_settled("B", "thermal_runaway", "success", 90),
                          _settled("B", "thermal_runaway", "success", 91)]
        self._write_db(db)
        observe.observe(days=7, min_occurrences=3)
        self.assertFalse([n for n in self._drafts() if "specialist-install" in n])

    def test_live_canary_suppresses_draft(self):
        db = self._exhausted_db()
        db["strategies"]["G"] = specialist_templates.instantiate(
            "timestep_floor_damp", "G", {"timestep": "0.1"})
        self._write_db(db)
        observe.observe(days=7, min_occurrences=3)
        self.assertFalse([n for n in self._drafts() if "specialist-install" in n])

    def test_fresh_tombstone_suppresses_draft(self):
        db = self._exhausted_db()
        db["strategy_adjustments"]["G:thermal_runaway"] = {
            "penalty": 0.6, "reason": "金丝雀降级墓碑", "tombstone": True,
            "ts": _now_ts()}
        self._write_db(db)
        observe.observe(days=7, min_occurrences=3)
        self.assertFalse([n for n in self._drafts() if "specialist-install" in n])

    def test_expired_tombstone_allows_draft(self):
        db = self._exhausted_db()
        old_ts = time.strftime("%Y-%m-%dT%H:%M:%S",
                               time.localtime(time.time() - 30 * 86400))
        db["strategy_adjustments"]["G:thermal_runaway"] = {
            "penalty": 0.6, "reason": "金丝雀降级墓碑", "tombstone": True,
            "ts": old_ts}
        self._write_db(db)
        observe.observe(days=7, min_occurrences=3)
        self.assertTrue([n for n in self._drafts() if "specialist-install" in n])

    def test_promote_drafted_at_threshold(self):
        db = _legacy_db()
        db["strategies"]["G"] = specialist_templates.instantiate(
            "timestep_floor_damp", "G", {"timestep": "0.1"})
        db["history"] = [_settled("G", "thermal_runaway", "success", i)
                         for i in range(3)] + [
                         _settled("G", "thermal_runaway", "fail", 9)]
        self._write_db(db)
        observe.observe(days=7, min_occurrences=3)
        prom = [d for n, d in self._drafts().items()
                if "specialist-promote-G" in n]
        self.assertEqual(len(prom), 1)
        self.assertEqual(prom[0]["meta"]["op"], "promote")

    def test_demote_and_tombstone_drafted_on_streak(self):
        db = _legacy_db()
        db["strategies"]["G"] = specialist_templates.instantiate(
            "timestep_floor_damp", "G", {"timestep": "0.1"})
        db["history"] = [_settled("G", "thermal_runaway", "fail", i)
                         for i in range(3)]
        self._write_db(db)
        observe.observe(days=7, min_occurrences=3)
        drafts = self._drafts()
        dem = [d for n, d in drafts.items() if "specialist-demote-G" in n]
        tomb = [d for n, d in drafts.items() if "specialist-tombstone-G" in n]
        self.assertEqual(len(dem), 1)
        self.assertEqual(dem[0]["meta"]["op"], "demote")
        self.assertEqual(len(tomb), 1)
        self.assertEqual((tomb[0]["meta"]["class"], tomb[0]["meta"]["tier"]),
                         ("strategy_weight_tuning", "auto"))
        self.assertIn('"tombstone": true', tomb[0]["diff"])

    def test_starved_canary_demoted(self):
        db = _legacy_db()
        db["strategies"]["G"] = specialist_templates.instantiate(
            "timestep_floor_damp", "G", {"timestep": "0.1"})
        old_ts = time.strftime("%Y-%m-%dT%H:%M:%S",
                               time.localtime(time.time() - 40 * 86400))
        db["history"] = [_settled("G", "thermal_runaway", "success", 0, ts=old_ts)]
        self._write_db(db)
        observe.observe(days=7, min_occurrences=3)
        self.assertTrue([n for n in self._drafts()
                         if "specialist-demote-G" in n])

    def test_canary_excluded_from_legacy_weight_loop(self):
        db = _legacy_db()
        db["strategies"]["G"] = specialist_templates.instantiate(
            "timestep_floor_damp", "G", {"timestep": "0.1"})
        db["history"] = [_settled("G", "thermal_runaway", "fail", i)
                         for i in range(3)]
        self._write_db(db)
        observe.observe(days=7, min_occurrences=3)
        self.assertFalse([n for n in self._drafts()
                          if n.split("-", 1)[1].startswith("strategy-weight-G-")])

    def test_stale_install_draft_invalidated_when_evidence_turns(self):
        self._write_db(self._exhausted_db())
        observe.observe(days=7, min_occurrences=3)
        name = [n for n in self._drafts() if "specialist-install-" in n][0]
        # 证据反转:压制的 B 连胜 → 工具箱不再耗尽 → 旧草稿作废
        db = self._exhausted_db()
        db["strategy_adjustments"] = {}
        db["history"] += [_settled("B", "thermal_runaway", "success", 90),
                          _settled("B", "thermal_runaway", "success", 91)]
        self._write_db(db)
        observe.observe(days=7, min_occurrences=3)
        patch = os.path.join(observe.PROPOSALS, name, "diff.patch")
        self.assertEqual(os.path.getsize(patch), 0)

    def test_sweep_on_malformed_db(self):
        self._write_db(self._exhausted_db())
        observe.observe(days=7, min_occurrences=3)
        name = [n for n in self._drafts() if "specialist-install-" in n][0]
        with open(self.db_path, "w") as f:
            f.write('{"strategies": {}, "history": null}')  # 畸形 history
        observe.observe(days=7, min_occurrences=3)
        patch = os.path.join(observe.PROPOSALS, name, "diff.patch")
        self.assertEqual(os.path.getsize(patch), 0)  # fail-closed 作废

    def test_no_evidence_class_channel_closed(self):
        with open(self.pol_path, "w") as f:
            json.dump({"roots": {"kit": {"path": self.tmp}}}, f)
        self._write_db(self._exhausted_db())
        observe.observe(days=7, min_occurrences=3)
        self.assertFalse([n for n in self._drafts() if "specialist-" in n])

    # ---- 20 轮对抗 ----
    def _canary_db(self):
        db = _legacy_db()
        db["strategies"]["G"] = specialist_templates.instantiate(
            "timestep_floor_damp", "G", {"timestep": "0.1"})
        return db

    def test_demote_first_on_reversed_sequence(self):
        # 3 胜后 3 连败:累计账面达晋升线,但连败尾触降级线 → 只起草 demote,
        # 绝不起草 promote(反转序列不得洗白晋升)
        db = self._canary_db()
        db["history"] = [_settled("G", "thermal_runaway", "success", i)
                         for i in range(3)] + \
                        [_settled("G", "thermal_runaway", "fail", i)
                         for i in range(3, 6)]
        self._write_db(db)
        observe.observe(days=7, min_occurrences=3)
        drafts = self._drafts()
        self.assertFalse([n for n in drafts if "specialist-promote-G" in n])
        self.assertTrue([n for n in drafts if "specialist-demote-G" in n])

    def test_tampered_lineage_no_draft(self):
        # 条目被篡改(rule 换参):血统不正,promote/demote 都不起草
        db = self._canary_db()
        db["strategies"]["G"]["rule"]["replace"]["template"] = "timestep 0.25"
        db["history"] = [_settled("G", "thermal_runaway", "fail", i)
                         for i in range(3)]
        self._write_db(db)
        observe.observe(days=7, min_occurrences=3)
        self.assertFalse([n for n in self._drafts()
                          if "specialist-demote-G" in n
                          or "specialist-promote-G" in n])

    def test_zombie_canary_starved_via_ledger(self):
        # 僵尸金丝雀(零实账):history 无锚,以 LEDGER install ts 计龄 → 饿死降级
        db = self._canary_db()
        self._write_db(db)
        old_ts = time.strftime("%Y-%m-%dT%H:%M:%S",
                               time.localtime(time.time() - 40 * 86400))
        with open(memory.LEDGER, "w") as f:
            f.write(json.dumps({"result": "AUTO_APPLIED", "op": "install",
                                "specialist_id": "G", "ts": old_ts}) + "\n")
        observe.observe(days=7, min_occurrences=3)
        self.assertTrue([n for n in self._drafts()
                         if "specialist-demote-G" in n])

    def test_young_zombie_no_demote(self):
        # 年轻僵尸(装好没多久、零实账):不降级
        db = self._canary_db()
        self._write_db(db)
        with open(memory.LEDGER, "w") as f:
            f.write(json.dumps({"result": "AUTO_APPLIED", "op": "install",
                                "specialist_id": "G", "ts": _now_ts()}) + "\n")
        observe.observe(days=7, min_occurrences=3)
        self.assertFalse([n for n in self._drafts()
                          if "specialist-demote-G" in n])

    def test_tombstone_bad_ts_failclosed(self):
        # 墓碑 ts 解析失败:按未过期处理(fail-closed),抑制重起草
        db = self._exhausted_db()
        db["strategy_adjustments"]["G:thermal_runaway"] = {
            "penalty": 0.6, "reason": "金丝雀降级墓碑", "tombstone": True,
            "ts": "garbage"}
        self._write_db(db)
        observe.observe(days=7, min_occurrences=3)
        self.assertFalse([n for n in self._drafts() if "specialist-install" in n])

    def test_tombstone_future_ts_failclosed(self):
        # 墓碑 ts 在未来:同样按未过期处理
        db = self._exhausted_db()
        fut = time.strftime("%Y-%m-%dT%H:%M:%S",
                            time.localtime(time.time() + 86400))
        db["strategy_adjustments"]["G:thermal_runaway"] = {
            "penalty": 0.6, "reason": "金丝雀降级墓碑", "tombstone": True,
            "ts": fut}
        self._write_db(db)
        observe.observe(days=7, min_occurrences=3)
        self.assertFalse([n for n in self._drafts() if "specialist-install" in n])

    def test_old_date_draft_swept(self):
        # 隔夜未应用的 specialist 草稿:本轮横扫一律作废(过期快照不得滞留)
        yesterday = time.strftime("%Y-%m-%d",
                                  time.localtime(time.time() - 86400))
        stale = os.path.join(observe.PROPOSALS,
                             "%s-specialist-install-thermal_runaway-deadbeef"
                             % yesterday)
        os.makedirs(stale)
        with open(os.path.join(stale, "diff.patch"), "w") as f:
            f.write("stale diff content\n")
        self._write_db(self._exhausted_db())
        observe.observe(days=7, min_occurrences=3)
        self.assertEqual(os.path.getsize(
            os.path.join(stale, "diff.patch")), 0)

    def test_multi_cause_distinct_sids(self):
        # 一轮多根因耗尽:sid 记账占位,两份 install 草稿分不同 sid
        db = _legacy_db()
        db["history"] = [_settled("B", "thermal_runaway", "fail", i)
                         for i in range(5)] + \
                        [_settled("F", "QEq_instability", "fail", i)
                         for i in range(5)]
        db["strategy_adjustments"] = {
            "B:thermal_runaway": {"penalty": 0.6, "reason": "r", "ts": _now_ts()},
            "F:QEq_instability": {"penalty": 0.6, "reason": "r", "ts": _now_ts()}}
        self._write_db(db)
        observe.observe(days=7, min_occurrences=3)
        inst = [d for n, d in self._drafts().items()
                if "specialist-install-" in n]
        self.assertEqual(len(inst), 2)
        sids = {d["meta"]["specialist_id"] for d in inst}
        self.assertEqual(len(sids), 2)  # 不再是同一 sid 撞车


# ======================================================================
# 蜂群研究驱动起草(2026-09-05):耗尽 → 联网蜂群 → 确定性映射到模板空间。
# 红线锚:picked params 永远在网格内,证据门 instantiate==entry 不松动;
# 蜂群失败/节流 → 盲选首参,与接入前逐字节等价;蜂群绝不抛进起草流程。
# ======================================================================
import specialist_swarm  # noqa: E402


def _qeq_research(tol_text="1e-3"):
    """罐头蜂群产物(repair 模式 OK):QEq 放宽到指定档位的文本。"""
    return {
        "status": "OK",
        "question": "QEq 耗尽",
        "verdict": "建议放宽 QEq 收敛 tolerance 至 %s(社区同症状案例)" % tol_text,
        "strategies": [
            {"name": "放宽 QEq 收敛阈值 tolerance 到 %s" % tol_text,
             "score": 85, "verified": True,
             "evidence": ["https://docs.lammps.org/fix_qeq_reaxff.html"],
             "risks": ["精度略降"], "when_to_use": "qeq 不收敛时"},
            {"name": "换用更小 timestep", "score": 40, "verified": False,
             "evidence": [], "risks": [], "when_to_use": ""},
        ],
        "sources": [{"url": "https://docs.lammps.org/fix_qeq_reaxff.html",
                     "title": "fix qeq/reaxff", "published": ""}],
        "cost": {"llm_calls": 5, "total_tokens": 8000}, "elapsed_sec": 62.0,
    }


class TestSwarmPickTemplate(unittest.TestCase):
    """pick_template 纯函数映射:计分/选参/回退/新机制。"""

    def test_qeq_text_picks_template_and_param(self):
        tid, params, meta = specialist_swarm.pick_template(
            "QEq_instability", _qeq_research("1e-3"), specialist_templates,
            ["qeq_relax_stronger", "timestep_floor_damp"])
        self.assertEqual(tid, "qeq_relax_stronger")
        self.assertEqual(params, {"tol": "1.0e-3"},
                         "文本里的 1e-3 必须映射到网格值,而非首参 1.0e-4")
        self.assertFalse(meta["fallback"])
        self.assertFalse(meta["novel"])

    def test_picked_params_always_in_grid(self):
        # 红线锚:无论蜂群文本写什么,产出必须过 instantiate(网格内)
        for text in ("1e-4", "1e-3", "0.0001", "9e-9 不存在档", "1e-5"):
            tid, params, meta = specialist_swarm.pick_template(
                "QEq_instability", _qeq_research(text), specialist_templates,
                ["qeq_relax_stronger"])
            entry = specialist_templates.instantiate(tid, "Z", params)
            self.assertIsNotNone(entry,
                                 "off-grid 参数不许逃出来: %r → %r" % (text, params))

    def test_no_match_falls_back_and_flags_novel(self):
        research = {"status": "OK", "verdict": "考虑更换力场为 ReaxFF/c-2019",
                    "strategies": [{"name": "更换力场参数集", "score": 70,
                                    "verified": True, "evidence": ["http://x"],
                                    "when_to_use": "力场不适用时", "risks": []}],
                    "sources": []}
        tid, params, meta = specialist_swarm.pick_template(
            "QEq_instability", research, specialist_templates,
            ["qeq_relax_stronger"])
        self.assertEqual((tid, params),
                         ("qeq_relax_stronger", {"tol": "1.0e-4"}),
                         "零命中回退首模板首参")
        self.assertTrue(meta["fallback"])
        self.assertTrue(meta["novel"], "有结论但零命中=新机制,须转人工")

    def test_degraded_research_falls_back_not_novel(self):
        for bad in (None, {"status": "DEGRADED"}, {"status": "RESEARCH_ERROR"},
                    {"status": "OK", "strategies": []}):
            tid, params, meta = specialist_swarm.pick_template(
                "QEq_instability", bad, specialist_templates,
                ["qeq_relax_stronger"])
            self.assertEqual(params, {"tol": "1.0e-4"})
            self.assertTrue(meta["fallback"])
            self.assertFalse(meta["novel"], "研究不可用不算新机制: %r" % (bad,))

    def test_cause_without_template_returns_none(self):
        self.assertIsNone(specialist_swarm.pick_template(
            "structural_collapse", _qeq_research(), specialist_templates,
            ["qeq_relax_stronger"]))


class TestSwarmThrottleAndKb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.old_kb = specialist_swarm.KB_PATH
        specialist_swarm.KB_PATH = os.path.join(self.tmp, "repair_kb.jsonl")
        self.addCleanup(setattr, specialist_swarm, "KB_PATH", self.old_kb)
        self.ev = {"swarm_research": True, "swarm_cooldown_hours": 24,
                   "swarm_max_per_day": 4}

    def test_cooldown_throttles_same_cause(self):
        specialist_swarm.append_episode({"type": "swarm_research",
                                         "root_cause": "QEq_instability"})
        self.assertTrue(specialist_swarm._throttled("QEq_instability", self.ev))
        self.assertFalse(specialist_swarm._throttled("thermal_runaway", self.ev),
                         "冷却只压同根因,其它根因不受影响")

    def test_daily_cap_throttles_all(self):
        for i in range(4):
            specialist_swarm.append_episode(
                {"type": "swarm_research", "root_cause": "c%d" % i})
        self.assertTrue(specialist_swarm._throttled("new_cause", self.ev))

    def test_canary_outcome_episodes_do_not_count_toward_cap(self):
        for i in range(10):
            specialist_swarm.append_episode(
                {"type": "canary_outcome", "sid": "G", "op": "promote",
                 "root_cause": "QEq_instability"})
        self.assertFalse(specialist_swarm._throttled("QEq_instability", self.ev),
                         "结局剧集不占蜂群额度")

    def test_knob_off_and_missing_kb_fail_closed(self):
        self.assertIsNone(specialist_swarm.research_cause(
            "QEq_instability", {"swarm_research": False}))
        # KB 不存在=从未跑过,不节流,但模型导入可被 stub 验证不抛
        old = specialist_swarm._load_model_research
        specialist_swarm._load_model_research = lambda: None
        try:
            self.assertIsNone(specialist_swarm.research_cause(
                "QEq_instability", self.ev))
        finally:
            specialist_swarm._load_model_research = old

    def test_never_raises_on_pipeline_exception(self):
        class _Boom:
            def run(self, *a, **k):
                raise RuntimeError("network down")
        old = specialist_swarm._load_model_research
        specialist_swarm._load_model_research = lambda: _Boom()
        try:
            self.assertIsNone(specialist_swarm.research_cause(
                "QEq_instability", self.ev))
            self.assertIsNone(specialist_swarm.swarm_pick(
                "QEq_instability", self.ev, specialist_templates,
                ["qeq_relax_stronger"]))
        finally:
            specialist_swarm._load_model_research = old

    def test_kb_episode_schema_and_corrupt_line_tolerance(self):
        specialist_swarm.append_episode({"type": "swarm_research",
                                         "root_cause": "QEq_instability",
                                         "verdict": "v"})
        with open(specialist_swarm.KB_PATH, "a") as f:
            f.write("{corrupt line\n")  # 截断半行是追加日志的常态
        eps = specialist_swarm.kb_episodes(cause="QEq_instability")
        self.assertEqual(len(eps), 1)
        self.assertEqual(eps[0]["verdict"], "v")
        self.assertIn("ts", eps[0])


class TestObserveSwarmIntegration(unittest.TestCase):
    """observe 起草段接蜂群:stub 研究产物,验证草稿用 picked 参数+写 KB;
    蜂群 None 时与盲选逐字节等价;新机制出 human_review 草稿。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.old = (observe.FLOWGATE_LOG, observe.PIPELINE_DIR, observe.PROPOSALS,
                    observe.LEGACY_MCP_DIR, observe._kit_db_path,
                    observe.POLICY_PATH, observe.PIPELINE_EVENTS)
        observe.FLOWGATE_LOG = os.path.join(self.tmp, "none.jsonl")
        observe.PIPELINE_DIR = os.path.join(self.tmp, "none-dir")
        observe.PIPELINE_EVENTS = os.path.join(self.tmp, "none-pipeline.jsonl")
        # friction 信号流沙盒(2026-09-13 补:真实 friction.jsonl 有活会话轮记录,
        # 不隔离会让 observe 额外起草 friction 提案,drafted 计数被真数据污染)
        self.addCleanup(setattr, observe, "FRICTION_LOG", observe.FRICTION_LOG)
        observe.FRICTION_LOG = os.path.join(self.tmp, "friction.jsonl")
        # 用户闸桥/observe 闸同样沙盒:human 提案起草会投 kit 收件箱,绝不碰真 inbox
        # (2026-09-07 KB_PATH 同型教训;addCleanup 注册时捕获旧值,免改 _restore)
        self.addCleanup(setattr, observe, "KIT_GATE_INBOX", observe.KIT_GATE_INBOX)
        observe.KIT_GATE_INBOX = os.path.join(self.tmp, "user_gate_requests.jsonl")
        self.addCleanup(setattr, observe, "OBSERVE_GATE_FILE", observe.OBSERVE_GATE_FILE)
        observe.OBSERVE_GATE_FILE = os.path.join(self.tmp, "observe_gate.json")
        observe.PROPOSALS = os.path.join(self.tmp, "proposals")
        observe.LEGACY_MCP_DIR = os.path.join(self.tmp, "none-legacy")
        self.db_path = os.path.join(self.tmp, ".v3.3_strategy_db.json")
        observe._kit_db_path = lambda: self.db_path
        spec = json.loads(json.dumps(EVIDENCE_SPEC))
        spec["evidence"].update({"swarm_research": True,
                                 "swarm_cooldown_hours": 24,
                                 "swarm_max_per_day": 4})
        pol = {"roots": {"kit": {"path": self.tmp}},
               "evidence_classes": {"new_specialist_script": spec}}
        self.pol_path = os.path.join(self.tmp, "POLICY.json")
        with open(self.pol_path, "w") as f:
            json.dump(pol, f)
        observe.POLICY_PATH = self.pol_path
        self.old_memory_dir = observe.paths.memory_dir
        observe.paths.memory_dir = lambda: self.tmp
        self.old_mem = (memory.EXP_DIR, memory.ATOMS, memory.SCENARIOS,
                        memory.SOP, memory.LEDGER)
        memory.EXP_DIR = os.path.join(self.tmp, "experience")
        memory.ATOMS = os.path.join(memory.EXP_DIR, "atoms.jsonl")
        memory.SCENARIOS = os.path.join(memory.EXP_DIR, "scenarios.json")
        memory.SOP = os.path.join(memory.EXP_DIR, "SOP.md")
        memory.LEDGER = os.path.join(self.tmp, "LEDGER.jsonl")
        self.old_kb = specialist_swarm.KB_PATH
        specialist_swarm.KB_PATH = os.path.join(self.tmp, "repair_kb.jsonl")
        self.addCleanup(self._restore)

    def _restore(self):
        (observe.FLOWGATE_LOG, observe.PIPELINE_DIR, observe.PROPOSALS,
         observe.LEGACY_MCP_DIR, observe._kit_db_path,
         observe.POLICY_PATH, observe.PIPELINE_EVENTS) = self.old
        observe.paths.memory_dir = self.old_memory_dir
        (memory.EXP_DIR, memory.ATOMS, memory.SCENARIOS,
         memory.SOP, memory.LEDGER) = self.old_mem
        specialist_swarm.KB_PATH = self.old_kb

    def _write_db(self, db):
        with open(self.db_path, "w") as f:
            f.write(json.dumps(db, indent=2) + "\n")

    def _qeq_exhausted_db(self):
        db = _legacy_db()
        db["history"] = [_settled("F", "QEq_instability", "fail", i)
                         for i in range(5)]
        db["strategy_adjustments"] = {
            "F:QEq_instability": {"penalty": 0.6, "reason": "r", "ts": _now_ts()}}
        return db

    def _drafts(self):
        if not os.path.isdir(observe.PROPOSALS):
            return {}
        out = {}
        for name in sorted(os.listdir(observe.PROPOSALS)):
            pdir = os.path.join(observe.PROPOSALS, name)
            out[name] = {
                "meta": json.load(open(os.path.join(pdir, "meta.json"))),
                "diff": open(os.path.join(pdir, "diff.patch")).read(),
                "evidence": open(os.path.join(pdir, "evidence.md")).read(),
            }
        return out

    def test_swarm_pick_lands_in_draft_and_kb(self):
        self._write_db(self._qeq_exhausted_db())
        old = specialist_swarm.research_cause
        specialist_swarm.research_cause = lambda cause, ev: _qeq_research("1e-3")
        try:
            observe.observe(days=7, min_occurrences=3)
        finally:
            specialist_swarm.research_cause = old
        inst = [d for n, d in self._drafts().items()
                if "specialist-install-" in n]
        self.assertEqual(len(inst), 1)
        self.assertIn("1.0e-3", inst[0]["diff"],
                      "草稿必须带蜂群选中的参数而非首参 1.0e-4")
        self.assertIn("qeq_relax_stronger", inst[0]["diff"])
        self.assertIn("蜂群研判", inst[0]["evidence"])
        eps = [e for e in specialist_swarm.kb_episodes()
               if e.get("type") == "swarm_research"]
        self.assertEqual(len(eps), 1, "蜂群剧集必须落知识库")
        self.assertEqual(eps[0]["picked"],
                         {"template": "qeq_relax_stronger",
                          "params": {"tol": "1.0e-3"}})
        self.assertFalse(eps[0]["fallback"])

    def test_swarm_none_byte_identical_blind_anchor(self):
        # 蜂群缺席(节流/失败/关闭):草稿与接入前盲选逐字节等价
        self._write_db(self._qeq_exhausted_db())
        old = specialist_swarm.swarm_pick
        specialist_swarm.swarm_pick = lambda *a, **k: None
        try:
            observe.observe(days=7, min_occurrences=3)
        finally:
            specialist_swarm.swarm_pick = old
        inst = [d for n, d in self._drafts().items()
                if "specialist-install-" in n]
        self.assertEqual(len(inst), 1)
        self.assertIn("1.0e-4", inst[0]["diff"], "盲选锚:首参 1.0e-4")
        self.assertNotIn("蜂群研判", inst[0]["evidence"])
        self.assertFalse([n for n in self._drafts() if "swarm-research-" in n])

    def test_novel_mechanism_drafts_human_review(self):
        self._write_db(self._qeq_exhausted_db())
        novel = {"status": "OK", "verdict": "更换力场参数集",
                 "strategies": [{"name": "换力场", "score": 70,
                                 "verified": True, "evidence": ["http://x"],
                                 "when_to_use": "力场不适用", "risks": []}],
                 "sources": [{"url": "http://x", "title": "t"}]}
        old = specialist_swarm.research_cause
        specialist_swarm.research_cause = lambda cause, ev: novel
        try:
            observe.observe(days=7, min_occurrences=3)
        finally:
            specialist_swarm.research_cause = old
        drafts = self._drafts()
        hr = [d for n, d in drafts.items() if "swarm-research-" in n]
        self.assertEqual(len(hr), 1, "新机制必须出 human_review 草稿")
        self.assertEqual(hr[0]["meta"]["tier"], "human")
        self.assertIn("换力场", hr[0]["evidence"])
        # install 草稿仍发(回退首参):新机制不阻塞既有兜底通道
        inst = [d for n, d in drafts.items() if "specialist-install-" in n]
        self.assertEqual(len(inst), 1)
        self.assertIn("1.0e-4", inst[0]["diff"])


class TestSwarmPickedInstallGate(EvidenceCase):
    """蜂群选参草稿过真实证据门(含沙盒试跑):红线全链证明+越狱反例。"""

    def test_swarm_picked_qeq_install_passes_gate(self):
        # 蜂群文本指向 1e-3 → picked params(网格内)→ 草稿 → 门必须收
        tid, params, meta = specialist_swarm.pick_template(
            "QEq_instability", _qeq_research("1e-3"), specialist_templates,
            ["qeq_relax_stronger"])
        self.assertEqual(params, {"tol": "1.0e-3"})
        self.assertFalse(meta["fallback"])
        # install 门要求根因仍耗尽:换 QEq_instability 耗尽库(F×5 连败+压制)
        self._write_db(_exhausted_db(cause="QEq_instability", sid="F"))
        pdir = self._install_proposal(sid="G", template_id=tid, params=params)
        rc = self._run_apply(pdir)
        self.assertEqual(rc, 0, "蜂群选参(网格内)必须过证据门: %s"
                         % (self._ledger_rows()[-1],))
        db = json.load(open(self.db_path))
        self.assertEqual(db["strategies"]["G"]["params"], {"tol": "1.0e-3"})
        self.assertIs(db["strategies"]["G"]["canary"], True)
        self.assertEqual(self._ledger_rows()[-1]["result"], "AUTO_APPLIED")

    def test_off_grid_jailbreak_rejected(self):
        # 反向锚:网格外参数(模拟蜂群越狱)必须被"条目==模板产物"拒
        self._write_db(_exhausted_db(cause="QEq_instability", sid="F"))
        entry = specialist_templates.instantiate("qeq_relax_stronger", "G",
                                                 {"tol": "1.0e-4"})
        entry["params"] = {"tol": "9.9e-9"}  # 越狱:网格外值
        old = json.load(open(self.db_path))
        new = json.loads(json.dumps(old))
        new["strategies"]["G"] = entry
        pdir = os.path.join(self.tmp, "p_jailbreak")
        make_proposal(pdir, self._meta("install", "G"), self._db_diff(old, new))
        rc = self._run_apply(pdir)
        self.assertEqual(rc, 2)
        self.assertIn("模板产物", self._ledger_rows()[-1]["reason"])


class TestObserveGate(unittest.TestCase):
    """observe 全局 should-run 闸 + 提案→kit 用户闸桥(2026-09-07,LoopX 形状)。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.kit = os.path.join(self.tmp, "kit")
        os.makedirs(self.kit)
        # POLICY 沙盒:roots.kit.path 指向假 kit
        self.old_pol = observe.POLICY_PATH
        self.pol_path = os.path.join(self.tmp, "POLICY.json")
        with open(self.pol_path, "w") as f:
            json.dump({"roots": {"kit": {"path": self.kit}}}, f)
        observe.POLICY_PATH = self.pol_path
        self.addCleanup(setattr, observe, "POLICY_PATH", self.old_pol)
        # 闸文件与收件箱沙盒,绝不碰真 state_dir/真 inbox
        self.addCleanup(setattr, observe, "OBSERVE_GATE_FILE", observe.OBSERVE_GATE_FILE)
        observe.OBSERVE_GATE_FILE = os.path.join(self.tmp, "observe_gate.json")
        self.addCleanup(setattr, observe, "KIT_GATE_INBOX", observe.KIT_GATE_INBOX)
        observe.KIT_GATE_INBOX = os.path.join(self.tmp, "user_gate_requests.jsonl")
        self.addCleanup(setattr, observe, "PIPELINE_EVENTS", observe.PIPELINE_EVENTS)
        observe.PIPELINE_EVENTS = os.path.join(self.tmp, "pipeline.jsonl")

    def _write_gate_file(self, ts):
        with open(observe.OBSERVE_GATE_FILE, "w") as f:
            json.dump({"last_run_ts": ts}, f)

    def _write_kit(self, state=None, gates=None):
        if state is not None:
            with open(os.path.join(self.kit, ".v3.3_state.json"), "w") as f:
                json.dump(state, f)
        if gates is not None:
            with open(os.path.join(self.kit, ".v3.3_user_gates.json"), "w") as f:
                json.dump({"version": 1, "seq": len(gates), "gates": gates}, f)

    def test_too_soon_skips(self):
        self._write_gate_file(time.time())
        ok, v = observe._should_observe()
        self.assertFalse(ok)
        self.assertEqual(v["reason"], "too_soon")

    def test_old_enough_runs(self):
        self._write_gate_file(time.time() - 7200)
        ok, v = observe._should_observe()
        self.assertTrue(ok)
        self.assertEqual(v["verdict"], "RUN")

    def test_kit_failed_with_pending_gates_skips(self):
        self._write_kit(state={"phase": "FAILED"},
                        gates=[{"id": "ug-1", "status": "pending"}])
        ok, v = observe._should_observe()
        self.assertFalse(ok)
        self.assertEqual(v["reason"], "kit_failed_pending_gates")

    def test_kit_failed_without_gates_runs(self):
        self._write_kit(state={"phase": "FAILED"}, gates=[])
        ok, _v = observe._should_observe()
        self.assertTrue(ok)

    def test_kit_state_missing_runs(self):
        # kit 缺席=无闸可守(observe_kit 本就容忍),放行
        ok, _v = observe._should_observe()
        self.assertTrue(ok)

    def test_kit_state_corrupt_fail_closed(self):
        with open(os.path.join(self.kit, ".v3.3_state.json"), "w") as f:
            f.write("{broken")
        ok, v = observe._should_observe()
        self.assertFalse(ok)
        self.assertEqual(v["reason"], "kit_state_unreadable")

    # ---- 24 轮审计修复回归 ----
    def test_kit_gates_corrupt_fail_closed_when_failed(self):
        # 审计 D3(中):FAILED 态下闸文件损坏不得按 pending=0 放行空转
        self._write_kit(state={"phase": "FAILED"})
        with open(os.path.join(self.kit, ".v3.3_user_gates.json"), "w") as f:
            f.write("{broken")
        ok, v = observe._should_observe()
        self.assertFalse(ok)
        self.assertEqual(v["reason"], "kit_gates_unreadable")

    def test_kit_gates_corrupt_runs_when_not_failed(self):
        # 非 FAILED 态:闸损坏不阻塞(observe 主链优先)
        self._write_kit(state={"phase": "MONITOR"})
        with open(os.path.join(self.kit, ".v3.3_user_gates.json"), "w") as f:
            f.write("{broken")
        ok, _v = observe._should_observe()
        self.assertTrue(ok)

    def test_min_interval_float_not_truncated(self):
        # 审计 D7(低):int(0.5)→0 会静默关停节流
        with open(self.pol_path, "w") as f:
            json.dump({"roots": {"kit": {"path": self.kit}},
                       "observe_gate": {"min_interval_minutes": 0.5}}, f)
        cfg = observe._observe_gate_cfg()
        self.assertEqual(cfg["min_interval_minutes"], 0.5)
        self._write_gate_file(time.time() - 10)  # 10s 前 < 0.5min
        ok, v = observe._should_observe()
        self.assertFalse(ok, "0.5 分钟间隔被 int() 截成 0(24 轮 D7 回归)")
        self.assertEqual(v["reason"], "too_soon")

    def test_gate_event_ts_numeric(self):
        # 审计 D6(低):与 pipeline_check 事件流同 schema,ts 必须数值型
        observe._emit_gate_event({"verdict": "RUN", "reason": "ok"})
        row = [json.loads(ln) for ln in open(observe.PIPELINE_EVENTS)][0]
        self.assertIsInstance(row["ts"], float,
                              "OBSERVE_GATE 行 ts 字符串化会破坏同文件 schema(24 轮 D6 回归)")

    def test_crash_emits_fail_event_and_keeps_throttle_window(self):
        # 审计 D4(中):observe 崩溃必须留 FAIL 裁决,且不得白占节流窗
        old_observe = observe.observe
        observe.observe = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            with self.assertRaises(RuntimeError):
                observe.main()
        finally:
            observe.observe = old_observe
        rows = [json.loads(ln) for ln in open(observe.PIPELINE_EVENTS)]
        verdicts = [r.get("verdict") for r in rows if r.get("status") == "OBSERVE_GATE"]
        self.assertIn("FAIL", verdicts, "崩溃必须留 FAIL 裁决行")
        self.assertFalse(os.path.exists(observe.OBSERVE_GATE_FILE),
                         "崩溃不得盖 last_run_ts 章白占节流窗(24 轮 D4 回归)")

    def test_success_stamps_gate_file_atomically(self):
        old_observe = observe.observe
        observe.observe = lambda *a, **k: {"ok": True}
        try:
            observe.main()
        finally:
            observe.observe = old_observe
        with open(observe.OBSERVE_GATE_FILE) as f:
            self.assertGreater(json.load(f)["last_run_ts"], 0)
        leftovers = [n for n in os.listdir(self.tmp) if ".tmp." in n]
        self.assertEqual(leftovers, [], "闸文件原子写不得留临时文件")


class TestGateBridge(unittest.TestCase):
    """draft_proposal tier=human 投 kit 收件箱;apply 成功投销项行。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, observe, "PROPOSALS", observe.PROPOSALS)
        observe.PROPOSALS = os.path.join(self.tmp, "proposals")
        self.addCleanup(setattr, observe, "KIT_GATE_INBOX", observe.KIT_GATE_INBOX)
        observe.KIT_GATE_INBOX = os.path.join(self.tmp, "inbox.jsonl")

    def _inbox_lines(self):
        if not os.path.exists(observe.KIT_GATE_INBOX):
            return []
        return [json.loads(ln) for ln in open(observe.KIT_GATE_INBOX) if ln.strip()]

    def test_human_tier_enqueues_request(self):
        p = observe.draft_proposal("x-human", "monitor_logic", "human", "kit",
                                   "测试标题", ["证据1"])
        self.assertIsNotNone(p)
        rows = self._inbox_lines()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["op"], "enqueue")
        self.assertEqual(rows[0]["kind"], "evolution_proposal")
        self.assertEqual(rows[0]["ref"], os.path.basename(p))
        self.assertEqual(rows[0]["source"], "dsh")

    def test_non_human_tier_silent(self):
        p = observe.draft_proposal("x-auto", "monitor_logic", "auto", "kit",
                                   "自动类", ["e"])
        self.assertIsNotNone(p)
        self.assertEqual(self._inbox_lines(), [])

    def test_bridge_failure_never_blocks_draft(self):
        observe.KIT_GATE_INBOX = "/proc/forbidden/inbox.jsonl"
        p = observe.draft_proposal("x-norise", "monitor_logic", "human", "kit",
                                   "桥断也要起草", ["e"])
        self.assertIsNotNone(p, "桥失败不得阻塞提案起草")

    def test_apply_success_emits_resolve(self):
        self.addCleanup(setattr, apply_proposal, "KIT_GATE_INBOX",
                        apply_proposal.KIT_GATE_INBOX)
        inbox = os.path.join(self.tmp, "apply-inbox.jsonl")
        apply_proposal.KIT_GATE_INBOX = inbox
        apply_proposal._append_gate_resolve("2026-09-07-x", "tester")
        rows = [json.loads(ln) for ln in open(inbox)]
        self.assertEqual(rows[0]["op"], "resolve")
        self.assertEqual(rows[0]["ref"], "2026-09-07-x")
        self.assertEqual(rows[0]["by"], "tester")

    def test_resolve_bridge_gated_on_human_tier_source_anchor(self):
        # 审计 D8(低):auto/evidence 应用从未入队,投 resolve 只是收件箱噪音
        with open(apply_proposal.__file__, encoding="utf-8") as f:
            src = f.read()
        self.assertIn('if meta.get("tier") == "human":\n        _append_gate_resolve',
                      src, "resolve 桥必须仅在 human tier 投(24 轮 D8 回归)")
