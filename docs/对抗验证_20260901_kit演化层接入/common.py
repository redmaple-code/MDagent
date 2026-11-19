# -*- coding: utf-8 -*-
"""对抗沙盒公共件:kit recover_agent + 演化层 observe/memory/apply_proposal 全部重定向到 /tmp。"""
import importlib.util
import json
import os
import sys
import tempfile

EVO = "/opt/mdagent/app/evolution"
KIT_V33 = "/opt/mdagent/kit/agents/runtime/v3.3"
TS_FMT = "%Y-%m-%dT%H:%M:%S"


def load_recover_agent(kit_dir):
    """认 AGENT_BASE env 的模块级路径解析,故在子进程/首导入前设 env。"""
    os.environ["AGENT_BASE"] = kit_dir
    spec = importlib.util.spec_from_file_location(
        "recover_agent", os.path.join(KIT_V33, "recover_agent.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Sandbox(object):
    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="evo_attack_")
        self.kit = os.path.join(self.tmp, "kit")
        os.makedirs(self.kit)
        self.db_path = os.path.join(self.kit, ".v3.3_strategy_db.json")
        self.ra = load_recover_agent(self.kit)
        assert self.ra.STRATEGY_DB == self.db_path
        if EVO not in sys.path:
            sys.path.insert(0, EVO)
        import memory, observe, apply_proposal
        self.memory, self.observe, self.apply_proposal = memory, observe, apply_proposal
        observe.FLOWGATE_LOG = os.path.join(self.tmp, "none.jsonl")
        observe.PIPELINE_DIR = os.path.join(self.tmp, "none-dir")
        observe.PROPOSALS = os.path.join(self.tmp, "proposals")
        observe.LEGACY_MCP_DIR = os.path.join(self.tmp, "none-legacy")
        observe._kit_db_path = lambda: self.db_path
        observe.paths.memory_dir = lambda: self.tmp
        memory.EXP_DIR = os.path.join(self.tmp, "experience")
        memory.ATOMS = os.path.join(memory.EXP_DIR, "atoms.jsonl")
        memory.SCENARIOS = os.path.join(memory.EXP_DIR, "scenarios.json")
        memory.SOP = os.path.join(memory.EXP_DIR, "SOP.md")
        memory.LEDGER = os.path.join(self.tmp, "mem-LEDGER.jsonl")
        apply_proposal.POLICY = {
            "roots": {"kit": {"path": self.kit,
                              "test_commands": [["python3", "-c", "pass"]]}},
            "auto_fix_classes": {"strategy_weight_tuning": {
                "root": "kit", "paths": [".v3.3_strategy_db.json"],
                "max_changed_lines": 12}},
            "human_review_classes": [],
            "forbidden_paths": [],
            "rollback": {"backups_dir": "backups"},
        }
        apply_proposal.LEDGER = os.path.join(self.tmp, "LEDGER.jsonl")
        apply_proposal.BACKUPS_BASE = self.tmp

    def write_db(self, db):
        with open(self.db_path, "w") as f:
            json.dump(db, f, indent=2)

    def read_db(self):
        with open(self.db_path) as f:
            return json.load(f)

    def run_apply(self, argv):
        old = sys.argv
        sys.argv = ["apply_proposal.py"] + list(argv)
        try:
            return self.apply_proposal.main()
        finally:
            sys.argv = old

    def ledger_rows(self):
        if not os.path.exists(self.apply_proposal.LEDGER):
            return []
        return [json.loads(ln) for ln in open(self.apply_proposal.LEDGER) if ln.strip()]


def attempt(sid, cause, case_id, outcome, ts):
    return {"strategy": sid, "root_cause": cause, "case_id": case_id,
            "ts": ts, "outcome": outcome}
