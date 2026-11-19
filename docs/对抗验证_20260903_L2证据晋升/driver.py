#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""L2 证据晋升流水线 E2E 驱动(2026-09-03;全沙盒:一切读写都在 /tmp/l2_evidence/sandbox 下)。

环1(晋升环):根因耗尽 → observe 起草 install → 真实证据门安装(沙盒试跑重执行)
  → 4 笔 mock 金丝雀实账(3胜1负)→ observe 起草 promote → 晋升(canary 行摘除)
  → 晋升后 G 作为正式策略赢得 select_strategy。
环2(降级环):换根因换模板再装一只金丝雀 → 3 连败 → observe 起草 demote+墓碑
  → 先应用墓碑(auto 层)再应用 demote(行序纪律)→ 策略消失
  → 墓碑冷却期内同根因不再重起草 install。

全程核对 LEDGER 序列、db 单写者纪律(history/adjustments 谁动谁不动)、
canary 两轮选择语义。沙盒补丁手法复刻 tests/test_evolution.py 的
SandboxCase/TestObserveSpecialist;apply 走真实 apply_proposal 通道
(证据门会真跑 specialist_sandbox 子进程试跑)。
"""
import json
import os
import shutil
import sys
import time

BASE = "/tmp/l2_evidence"
TMP = os.path.join(BASE, "sandbox")
DSH = "/opt/mdagent/app"
KIT = "/opt/mdagent/kit"
V33 = os.path.join(KIT, "agents", "runtime", "v3.3")

sys.path.insert(0, os.path.join(DSH, "evolution"))
sys.path.insert(0, os.path.join(DSH, "mcp"))
sys.path.insert(0, V33)

import apply_proposal  # noqa: E402
import memory  # noqa: E402
import observe  # noqa: E402
import specialist_templates  # noqa: E402
import recover_agent as ra  # noqa: E402

# ---------------------------------------------------------------- 沙盒搭建
if os.path.exists(TMP):
    shutil.rmtree(TMP)
os.makedirs(TMP)

KIT_DIR = os.path.join(TMP, "kit")
SB_V33 = os.path.join(KIT_DIR, "agents", "runtime", "v3.3")
os.makedirs(SB_V33)
for fn in ("recover_agent.py", "specialist_rules.py"):
    shutil.copy2(os.path.join(V33, fn), os.path.join(SB_V33, fn))
DB_PATH = os.path.join(KIT_DIR, ".v3.3_strategy_db.json")
MEMROOT = os.path.join(TMP, "memroot")
os.makedirs(MEMROOT)
PROD = os.path.join(TMP, "prod")
os.makedirs(PROD)

EVIDENCE_SPEC = {
    "root": "kit", "paths": [".v3.3_strategy_db.json"], "max_changed_lines": 40,
    "evidence": {"sandbox_trial": True, "canary_attempts": 4, "canary_min_wins": 3,
                 "min_posterior": 0.5, "demote_fail_streak": 3,
                 "demote_cooldown_days": 7, "canary_max_age_days": 30,
                 "allowed_templates": ["timestep_floor_damp", "qeq_relax_stronger",
                                       "nve_limit_tighten"]}}

POLICY = {
    "roots": {"kit": {"path": KIT_DIR,
                      "test_commands": [[sys.executable, "-c", "pass"]]}},
    "auto_fix_classes": {
        "strategy_weight_tuning": {"root": "kit",
                                   "paths": [".v3.3_strategy_db.json"],
                                   "max_changed_lines": 40}},
    "evidence_classes": {"new_specialist_script": EVIDENCE_SPEC},
    "human_review_classes": ["new_specialist_script"],
    "forbidden_paths": ["agents/runtime/v3.3/specialist_rules.py"],
    "rollback": {"backups_dir": "backups"},
}
apply_proposal.POLICY = POLICY
apply_proposal.LEDGER = os.path.join(MEMROOT, "LEDGER.jsonl")
apply_proposal.BACKUPS_BASE = TMP

POLICY_PATH = os.path.join(TMP, "POLICY.json")
with open(POLICY_PATH, "w") as f:
    json.dump(POLICY, f)

observe.FLOWGATE_LOG = os.path.join(TMP, "none.jsonl")
observe.PIPELINE_DIR = os.path.join(TMP, "none-dir")
observe.PROPOSALS = os.path.join(TMP, "proposals")
observe.LEGACY_MCP_DIR = os.path.join(TMP, "none-legacy")
observe._kit_db_path = lambda: DB_PATH
observe.POLICY_PATH = POLICY_PATH
observe.paths.memory_dir = lambda: MEMROOT
memory.EXP_DIR = os.path.join(TMP, "experience")
memory.ATOMS = os.path.join(memory.EXP_DIR, "atoms.jsonl")
memory.SCENARIOS = os.path.join(memory.EXP_DIR, "scenarios.json")
memory.SOP = os.path.join(memory.EXP_DIR, "SOP.md")
memory.LEDGER = os.path.join(MEMROOT, "LEDGER.jsonl")

ra.STRATEGY_DB = DB_PATH
ra.run_research = lambda *a, **k: None
ra.restart_production = lambda *a, **k: [4242]

LEGACY_NAMES = ["delete_overlap", "reduce_timestep", "langevin_damp",
                "nve_limit", "reduce_heating", "qeq_relax"]

# ---------------------------------------------------------------- 工具
ROWS = []


def check(loop, step, item, expected, actual, ok=None):
    if ok is None:
        ok = (expected == actual)
    ROWS.append({"loop": loop, "step": step, "item": item,
                 "expected": expected, "actual": actual, "ok": bool(ok)})
    print("[%s] %-46s 期望=%s 实际=%s %s" % (
        step, item, expected, actual, "PASS" if ok else "FAIL"))


def db():
    return json.load(open(DB_PATH))


def write_db(d):
    with open(DB_PATH, "w") as f:
        f.write(json.dumps(d, indent=2) + "\n")


def now_ts():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def settled(sid, cause, outcome, i):
    return {"strategy": sid, "root_cause": cause, "case_id": "c%d" % i,
            "ts": now_ts(), "outcome": outcome}


def legacy_db():
    return {"strategies": {sid: {"name": n, "desc": "", "for": [],
                                 "success_rate": 0.5, "uses": 0}
                           for sid, n in zip("ABCDEF", LEGACY_NAMES)},
            "history": [], "strategy_adjustments": {}}


def exhausted_db(sid, cause, anomaly):
    """某根因标准工具箱耗尽:sid×cause 五连败 + penalty 封顶(被压制)。"""
    d = legacy_db()
    d["strategies"][sid]["for"] = [anomaly]
    d["history"] = [settled(sid, cause, "fail", i) for i in range(5)]
    d["strategy_adjustments"] = {
        "%s:%s" % (sid, cause): {"penalty": 0.6, "reason": "连败压制",
                                 "ts": now_ts()}}
    return d


def ledger_rows():
    if not os.path.exists(apply_proposal.LEDGER):
        return []
    return [json.loads(ln) for ln in open(apply_proposal.LEDGER) if ln.strip()]


def observe_drafts(prefix):
    summary = observe.observe(days=7, min_occurrences=3)
    return [p for p in summary["drafted"] if prefix in os.path.basename(p)]


def apply_dir(pdir):
    old_argv = sys.argv
    sys.argv = ["apply_proposal.py", pdir]
    try:
        return apply_proposal.main()
    finally:
        sys.argv = old_argv


def archive(pdir):
    """已应用提案目录让出草稿位(等价跨日推进;LEDGER 留痕不动)。"""
    suffix = 0
    while os.path.exists("%s.applied%d" % (pdir, suffix)):
        suffix += 1
    os.rename(pdir, "%s.applied%d" % (pdir, suffix))


def meta_of(pdir):
    return json.load(open(os.path.join(pdir, "meta.json")))


# ================================================================ 环 1:晋升环
print("=" * 72)
print("环 1: thermal_runaway 耗尽 → install G → 3胜1负 → promote 晋升")
print("=" * 72)
CAUSE1, ANOM1 = "thermal_runaway", "TEMPERATURE_SPIKE"
write_db(exhausted_db("B", CAUSE1, ANOM1))

# 1.1 起草 install
drafts = observe_drafts("specialist-install-")
check("L1", "1.1 起草", "install 草稿数", 1, len(drafts))
pdir = drafts[0]
m = meta_of(pdir)
check("L1", "1.1 起草", "meta(class,tier,op,sid)",
      ("new_specialist_script", "evidence", "install", "G"),
      (m["class"], m["tier"], m.get("op"), m.get("specialist_id")))
hist_len0 = len(db()["history"])

# 1.2 证据门安装(内部真跑沙盒试跑子进程)
rc = apply_dir(pdir)
check("L1", "1.2 安装", "apply rc", 0, rc)
check("L1", "1.2 安装", "LEDGER result", "AUTO_APPLIED",
      ledger_rows()[-1]["result"])
d = db()
expect_entry = specialist_templates.instantiate("timestep_floor_damp", "G",
                                                {"timestep": "0.1"})
check("L1", "1.2 安装", "G == 模板产物", True, d["strategies"].get("G") == expect_entry)
check("L1", "1.2 安装", "canary is True", True,
      d["strategies"]["G"].get("canary") is True)
check("L1", "1.2 安装", "history 不动(单写者)", hist_len0, len(d["history"]))
check("L1", "1.2 安装", "adjustments 不动(单写者)", True,
      set(d["strategy_adjustments"]) == {"B:thermal_runaway"})
archive(pdir)

# 1.3 金丝雀兜底语义:A-F 全用过 → 第二轮才选 G;rule 真能改写 production.in
d = db()
used = [{"strategy": s} for s in "ABCDEF"]
sid, sinfo = ra.select_strategy(d, CAUSE1, [{"type": ANOM1}], [], used)
check("L1", "1.3 兜底", "legacy 耗尽后选中金丝雀", "G", sid)
with open(os.path.join(PROD, "production.in"), "w") as f:
    f.write("units real\nfix QEQ all qeq/reax 1 0.0 10.0 1.0e-6\n"
            "timestep 0.5\nfix 1 all nvt temp 300.0 1200.0 100.0\nrun 50000\n")
ok, msg = ra.apply_strategy(PROD, "G", d["strategies"]["G"])
check("L1", "1.3 兜底", "rule 改写 production.in", True,
      ok and "timestep 0.1" in open(os.path.join(PROD, "production.in")).read())

# 1.4 活金丝雀抑制同根因重起草
drafts = observe_drafts("specialist-install-")
check("L1", "1.4 抑重起草", "活 canary 下 install 草稿数", 0, len(drafts))

# 1.5 4 笔 mock 金丝雀账(3胜1负)→ 起草 promote
d = db()
d["history"] += [settled("G", CAUSE1, "success", 10),
                 settled("G", CAUSE1, "success", 11),
                 settled("G", CAUSE1, "fail", 12),
                 settled("G", CAUSE1, "success", 13)]
write_db(d)
drafts = observe_drafts("specialist-promote-")
check("L1", "1.5 起草晋升", "promote 草稿数", 1, len(drafts))
pdir = drafts[0]
check("L1", "1.5 起草晋升", "meta op", "promote", meta_of(pdir).get("op"))

# 1.6 晋升:apply 后 canary 行消失,其余字段与 history 不动
rc = apply_dir(pdir)
check("L1", "1.6 晋升", "apply rc", 0, rc)
d = db()
check("L1", "1.6 晋升", "canary 行摘除", False, "canary" in d["strategies"]["G"])
check("L1", "1.6 晋升", "策略键集合不变", True,
      set(d["strategies"]) == set("ABCDEFG"))
check("L1", "1.6 晋升", "history 不动", hist_len0 + 4, len(d["history"]))
archive(pdir)

# 1.7 晋升后 G 是正式策略:带压制打分下胜出(B 被 penalty 0.6 压住)
d = db()
sid, _ = ra.select_strategy(d, CAUSE1, [{"type": ANOM1}], [], [])
check("L1", "1.7 正式上岗", "select 胜出", "G", sid)

# ================================================================ 环 2:降级环
print("=" * 72)
print("环 2: QEq_instability 耗尽 → install G → 3 连败 → demote+墓碑 → 抑重起草")
print("=" * 72)
CAUSE2, ANOM2 = "QEq_instability", "QEQ_FAILURE"
write_db(exhausted_db("F", CAUSE2, ANOM2))

# 2.1 再装一只金丝雀(模板 qeq_relax_stronger)
drafts = observe_drafts("specialist-install-")
check("L2", "2.1 起草", "install 草稿数", 1, len(drafts))
pdir = drafts[0]
rc = apply_dir(pdir)
check("L2", "2.1 安装", "apply rc", 0, rc)
d = db()
expect_entry = specialist_templates.instantiate("qeq_relax_stronger", "G",
                                                {"tol": "1.0e-4"})
check("L2", "2.1 安装", "G == 模板产物", True, d["strategies"].get("G") == expect_entry)
archive(pdir)

# 2.2 3 连败 → 起草 demote + 墓碑(配套 auto 层)
d = db()
d["history"] += [settled("G", CAUSE2, "fail", 20),
                 settled("G", CAUSE2, "fail", 21),
                 settled("G", CAUSE2, "fail", 22)]
write_db(d)
summary = observe.observe(days=7, min_occurrences=3)
dem = [p for p in summary["drafted"] if "specialist-demote-" in os.path.basename(p)]
tomb = [p for p in summary["drafted"] if "specialist-tombstone-" in os.path.basename(p)]
check("L2", "2.2 起草降级", "demote 草稿数", 1, len(dem))
check("L2", "2.2 起草降级", "墓碑草稿数", 1, len(tomb))
check("L2", "2.2 起草降级", "墓碑(class,tier)",
      ("strategy_weight_tuning", "auto"),
      (meta_of(tomb[0])["class"], meta_of(tomb[0])["tier"]))

# 2.3 行序纪律:apply_hunks 按绝对行号,墓碑(文件后段)先,demote(前段)后
rc = apply_dir(tomb[0])
check("L2", "2.3 墓碑", "apply rc", 0, rc)
d = db()
t = d["strategy_adjustments"].get("G:QEq_instability", {})
check("L2", "2.3 墓碑", "tombstone 标记", True, t.get("tombstone") is True)
check("L2", "2.3 墓碑", "penalty 封顶", 0.6, t.get("penalty"))
archive(tomb[0])

rc = apply_dir(dem[0])
check("L2", "2.4 降级", "apply rc", 0, rc)
d = db()
check("L2", "2.4 降级", "策略消失", False, "G" in d["strategies"])
check("L2", "2.4 降级", "legacy 六策略完整", True,
      set(d["strategies"]) == set("ABCDEF"))
archive(dem[0])

# 2.5 墓碑冷却期:根因仍耗尽(F 五连败+压制)但不再重起草 install
drafts = observe_drafts("specialist-install-")
check("L2", "2.5 冷却抑起草", "install 草稿数", 0, len(drafts))
drafts = observe_drafts("specialist-demote-")
check("L2", "2.5 冷却抑起草", "demote 草稿数(G 已消失)", 0, len(drafts))

# ---------------------------------------------------------------- 终态汇总
rows = ledger_rows()
seq = [(r.get("result"), r.get("tier"), r.get("class")) for r in rows]
check("ALL", "终态", "LEDGER 序列",
      [("AUTO_APPLIED", "evidence", "new_specialist_script"),
       ("AUTO_APPLIED", "evidence", "new_specialist_script"),
       ("AUTO_APPLIED", "evidence", "new_specialist_script"),
       ("AUTO_APPLIED", "auto", "strategy_weight_tuning"),
       ("AUTO_APPLIED", "evidence", "new_specialist_script")], seq)
d = db()
check("ALL", "终态", "无 pending 残留", 0,
      sum(1 for h in d["history"] if h["outcome"] == "pending"))

fails = [r for r in ROWS if not r["ok"]]
print("\n" + "=" * 72)
print("TOTAL: %d/%d PASS" % (len(ROWS) - len(fails), len(ROWS)))
with open(os.path.join(BASE, "results.json"), "w") as f:
    json.dump(ROWS, f, ensure_ascii=False, indent=2)
if fails:
    for r in fails:
        print("FAIL:", json.dumps(r, ensure_ascii=False))
sys.exit(1 if fails else 0)
