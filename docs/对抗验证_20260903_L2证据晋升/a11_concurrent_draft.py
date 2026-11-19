#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""a11 并发起草恰一份(第 20 轮):两个线程同时跑 observe 起草 install。

断言:并发结束后 proposals 里 install 草稿恰 1 份(makedirs FileExistsError
去重;两线程内容一致——diff 出自同一快照同一模板,确定性)。
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time

DSH = "/opt/mdagent/app"
sys.path.insert(0, os.path.join(DSH, "evolution"))
sys.path.insert(0, os.path.join(DSH, "mcp"))

import memory  # noqa: E402
import observe  # noqa: E402

TMP = tempfile.mkdtemp(prefix="adv_a11_")
observe.FLOWGATE_LOG = os.path.join(TMP, "none.jsonl")
observe.PIPELINE_DIR = os.path.join(TMP, "none-dir")
observe.PROPOSALS = os.path.join(TMP, "proposals")
observe.LEGACY_MCP_DIR = os.path.join(TMP, "none-legacy")
DB = os.path.join(TMP, ".v3.3_strategy_db.json")
observe._kit_db_path = lambda: DB
POL = os.path.join(TMP, "POLICY.json")
EV = {"sandbox_trial": False, "canary_attempts": 4, "canary_min_wins": 3,
      "min_posterior": 0.5, "demote_fail_streak": 3, "demote_cooldown_days": 7,
      "canary_max_age_days": 30,
      "allowed_templates": ["timestep_floor_damp", "qeq_relax_stronger",
                            "nve_limit_tighten"]}
json.dump({"roots": {"kit": {"path": TMP}},
           "evidence_classes": {"new_specialist_script": {"evidence": EV}}},
          open(POL, "w"))
observe.POLICY_PATH = POL
observe.paths.memory_dir = lambda: TMP
memory.EXP_DIR = os.path.join(TMP, "experience")
memory.ATOMS = os.path.join(memory.EXP_DIR, "atoms.jsonl")
memory.SCENARIOS = os.path.join(memory.EXP_DIR, "scenarios.json")
memory.SOP = os.path.join(memory.EXP_DIR, "SOP.md")
memory.LEDGER = os.path.join(TMP, "LEDGER.jsonl")

NOW = time.strftime("%Y-%m-%dT%H:%M:%S")
db = {"strategies": {"B": {"name": "b", "desc": "", "for": [],
                           "success_rate": 0.5, "uses": 0}},
      "history": [{"strategy": "B", "root_cause": "thermal_runaway",
                   "case_id": "c", "ts": NOW, "outcome": "fail"}] * 5,
      "strategy_adjustments": {"B:thermal_runaway": {
          "penalty": 0.6, "reason": "r", "ts": NOW}}}
with open(DB, "w") as f:
    f.write(json.dumps(db, indent=2) + "\n")

errors = []


def worker():
    try:
        observe.observe(days=7, min_occurrences=3)
    except Exception as e:  # 并发下任何异常都算失败
        errors.append(repr(e))


threads = [threading.Thread(target=worker) for _ in range(2)]
for t in threads:
    t.start()
for t in threads:
    t.join()

names = [n for n in os.listdir(observe.PROPOSALS)
         if "specialist-install-" in n] if os.path.isdir(observe.PROPOSALS) else []
ok = not errors and len(names) == 1
print("并发异常: %s" % (errors or "无"))
print("install 草稿数: %d(期望 1)" % len(names))
print("a11 并发起草: %s" % ("通过" if ok else "失败"))
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(0 if ok else 1)
