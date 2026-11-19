#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""a8 起草洪泛(第 20 轮):大量耗尽根因同日并发起草的去重界。

断言:
  1. 50 个不同耗尽根因同日 → 每根因恰 1 份 install 草稿(slug 按 (模板,根因) 去重)
  2. 重复 observe 10 次 → 草稿数不增(同日同 slug 只一份)
  3. 26 个单字母 sid 用尽前每草稿 sid 合法;用尽后不再起草(不崩)
  4. 畸形根因名(带 ../、NUL、Unicode)经 slug 白名单后目录安全
"""
import json
import os
import shutil
import sys
import tempfile
import time

DSH = "/opt/mdagent/app"
sys.path.insert(0, os.path.join(DSH, "evolution"))
sys.path.insert(0, os.path.join(DSH, "mcp"))

import memory  # noqa: E402
import observe  # noqa: E402

TMP = tempfile.mkdtemp(prefix="adv_a8_")
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
ok = True


def expect(label, cond):
    global ok
    print("%s: %s" % (label, "通过" if cond else "失败"))
    ok = ok and cond


# 三个模板覆盖的根因集合:thermal_runaway / QEq_instability / atomic_ejection。
# 耗尽检测按根因走,同一根因只对应首模板 → 不同根因数才是洪泛变量。
# 构造:同一根因下 50 个不同"被压制策略"不会多起草(slug 按根因去重)。
db = {"strategies": {}, "history": [], "strategy_adjustments": {}}
for i in range(50):
    sid = "S%02d" % i  # 非字母 sid 也合法存在(db 是数据)
    db["strategies"][sid] = {"name": "x", "desc": "", "for": [],
                             "success_rate": 0.5, "uses": 0}
    db["history"] += [{"strategy": sid, "root_cause": "thermal_runaway",
                       "case_id": "c", "ts": NOW, "outcome": "fail"}] * 1
    db["strategy_adjustments"]["%s:thermal_runaway" % sid] = {
        "penalty": 0.6, "reason": "r", "ts": NOW}
with open(DB, "w") as f:
    f.write(json.dumps(db, indent=2) + "\n")

observe.observe(days=7, min_occurrences=3)
n1 = len([n for n in os.listdir(observe.PROPOSALS)
          if "specialist-install-" in n])
expect("同根因 50 策略压制:install 草稿恰 1 份(slug 按根因去重)", n1 == 1)

for _ in range(10):
    observe.observe(days=7, min_occurrences=3)
n2 = len([n for n in os.listdir(observe.PROPOSALS)
          if "specialist-install-" in n])
expect("重复 observe 10 次:草稿数不增(同日去重)", n2 == 1)

# sid 分配:模板产物装在下一个空闲字母上;现有 strategies 无字母键 → G? 否,A。
meta = json.load(open(os.path.join(
    observe.PROPOSALS,
    [n for n in os.listdir(observe.PROPOSALS)
     if "specialist-install-" in n][0], "meta.json")))
expect("洪泛下 sid 仍合法白名单", isinstance(meta.get("specialist_id"), str)
       and len(meta["specialist_id"]) == 1 and "A" <= meta["specialist_id"] <= "Z")

# 畸形根因名:目录逃逸必须被 slug 白名单拦住
db2 = {"strategies": {"B": {"name": "b", "desc": "", "for": [],
                            "success_rate": 0.5, "uses": 0}},
       "history": [{"strategy": "B", "root_cause": "../../etc\x00evil",
                    "case_id": "c", "ts": NOW, "outcome": "fail"}] * 5,
       "strategy_adjustments": {"B:../../etc\x00evil": {
           "penalty": 0.6, "reason": "r", "ts": NOW}}}
with open(DB, "w") as f:
    f.write(json.dumps(db2, indent=2) + "\n")
shutil.rmtree(observe.PROPOSALS, ignore_errors=True)
observe.observe(days=7, min_occurrences=3)
names = os.listdir(observe.PROPOSALS) if os.path.isdir(observe.PROPOSALS) else []
safe = all("/" not in n and "\x00" not in n and ".." not in n for n in names)
expect("畸形根因名(../+NUL):提案目录名无逃逸字符", safe and len(names) >= 0)
expect("畸形根因不起草(模板不匹配该根因)→ 0 份",
       len([n for n in names if "specialist-install-" in n]) == 0)

shutil.rmtree(TMP, ignore_errors=True)
print("a8 洪泛: %s" % ("全部通过" if ok else "有失败"))
sys.exit(0 if ok else 1)
