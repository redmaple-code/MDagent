#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""演化层学习循环 E2E 驱动(全沙盒:一切读写都在 /tmp/evo_training/sandbox 下)。

驱动 3 个完整学习循环:
  循环A: 策略A(delete_overlap) x 根因 atomic_ejection(LOST_ATOMS)
  循环B: 策略B(reduce_timestep) x 根因 thermal_runaway(TEMPERATURE_SPIKE)
  循环C: 策略B x 根因 QEq_instability(QEQ_FAILURE) + 异根因 fail 不连坐老账验证
         + Beta 窗口 20 验证(策略E 23 条已结账)

每循环 4 步:连败2→penalty 0.15;再连败2→0.30;连胜2→0.15;再连胜2→0 摘条目。
每步核对:Beta 后验 (胜+1)/(已结账+2) 窗口20、history 长度、outcome 状态机、
uses 计数、select_strategy 压制/恢复。

沙盒补丁手法复刻 dsh-agent tests/test_evolution.py 的 SandboxCase /
TestObserveKit / TestE2EEvolutionKit;orchestrator 结账口径复刻
kit orchestrator._settle_strategy_attempts(同因7d fail + 年轻<6h fail +
老>6h success)。
"""
import json
import os
import shutil
import sys
import time

BASE = "/tmp/evo_training"
TMP = os.path.join(BASE, "sandbox")
DSH = "/opt/mdagent/app"
KIT = "/opt/mdagent/kit"

sys.path.insert(0, os.path.join(DSH, "evolution"))
sys.path.insert(0, os.path.join(DSH, "mcp"))
sys.path.insert(0, os.path.join(KIT, "agents", "runtime", "v3.3"))

import apply_proposal  # noqa: E402
import memory  # noqa: E402
import observe  # noqa: E402
import recover_agent as ra  # noqa: E402

# ---------------------------------------------------------------- 沙盒搭建
if os.path.exists(TMP):
    shutil.rmtree(TMP)
os.makedirs(TMP)

KIT_DIR = os.path.join(TMP, "kit")
os.makedirs(KIT_DIR)
DB_PATH = os.path.join(KIT_DIR, ".v3.3_strategy_db.json")
MEMROOT = os.path.join(TMP, "memroot")   # paths.memory_dir() 沙盒(LEDGER 所在)
os.makedirs(MEMROOT)
PROD = os.path.join(TMP, "prod")
os.makedirs(PROD)
with open(os.path.join(PROD, "production.in"), "w") as f:
    f.write("fix QEQ all qeq/reax 1 0.0 10.0 1.0e-6\n"
            "timestep 0.25\nrun 50000\n")

# kit recover_agent 补丁(口径同 TestE2EEvolutionKit)
ra.STRATEGY_DB = DB_PATH
ra.run_research = lambda *a, **k: None          # 不联网
ra.restart_production = lambda *a, **k: [4242]  # 假 pid → success

# 初始策略 db:先取内置默认再写(open("w") 先建空文件会让 load_db 崩)
_default = ra.load_db()
with open(DB_PATH, "w") as f:
    json.dump(_default, f, indent=2)

# apply_proposal 补丁(口径同 SandboxCase):POLICY 指向沙盒 kit,回归命令恒绿
apply_proposal.POLICY = {
    "roots": {"kit": {"path": KIT_DIR,
                      "test_commands": [[sys.executable, "-c", "pass"]]}},
    "auto_fix_classes": {
        "strategy_weight_tuning": {"root": "kit",
                                   "paths": [".v3.3_strategy_db.json"],
                                   "max_changed_lines": 12}},
    "human_review_classes": [],
    "forbidden_paths": [],
    "rollback": {"backups_dir": "backups"},
}
apply_proposal.LEDGER = os.path.join(MEMROOT, "LEDGER.jsonl")
apply_proposal.BACKUPS_BASE = TMP

# observe/memory 补丁(口径同 TestObserveKit)
observe.FLOWGATE_LOG = os.path.join(TMP, "none.jsonl")
observe.PIPELINE_DIR = os.path.join(TMP, "none-dir")
observe.PROPOSALS = os.path.join(TMP, "proposals")
observe.LEGACY_MCP_DIR = os.path.join(TMP, "none-legacy")
observe._kit_db_path = lambda: DB_PATH
observe.paths.memory_dir = lambda: MEMROOT   # _proposal_applied 读这里
memory.EXP_DIR = os.path.join(TMP, "experience")
memory.ATOMS = os.path.join(memory.EXP_DIR, "atoms.jsonl")
memory.SCENARIOS = os.path.join(memory.EXP_DIR, "scenarios.json")
memory.SOP = os.path.join(memory.EXP_DIR, "SOP.md")
memory.LEDGER = os.path.join(MEMROOT, "LEDGER.jsonl")

# ---------------------------------------------------------------- 结果表
ROWS = []


def check(loop, step, item, expected, actual, ok=None):
    if ok is None:
        ok = (expected == actual)
    ROWS.append({"loop": loop, "step": step, "item": item,
                 "expected": expected, "actual": actual, "ok": bool(ok)})
    print("[%s] %-42s 期望=%s 实际=%s %s" % (
        step, item, expected, actual, "PASS" if ok else "FAIL"))


def db():
    return json.load(open(DB_PATH))


def fmt(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))


def expected_sr(d, sid):
    """Beta(1,1) 后验手工复算:近 20 条已结账,(胜+1)/(已结账+2)。"""
    settled = [h for h in d["history"]
               if h.get("strategy") == sid and h.get("outcome") in ("success", "fail")]
    settled = settled[-20:]
    if not settled:
        return 0.5
    wins = sum(1 for h in settled if h["outcome"] == "success")
    return round((wins + 1) / (len(settled) + 2), 4)


STATE = {"history_len": 0, "uses": {}}   # 每循环独立 db 的账本


def reset_db():
    """每循环独立策略 db(共享 db 会让 success_rate 跨根因累积——
    后验是按策略全局算的,循环C 的平局对照会被循环B 的胜场污染)。"""
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    d = ra.load_db()
    with open(DB_PATH, "w") as f:
        json.dump(d, f, indent=2)
    STATE["history_len"] = 0
    STATE["uses"] = {}


def verify_db_invariants(loop, step):
    d = db()
    check(loop, step, "history 长度", STATE["history_len"], len(d["history"]))
    for sid, u in STATE["uses"].items():
        check(loop, step, "uses[%s]" % sid, u,
              d["strategies"][sid].get("uses", 0))
    for sid in d["strategies"]:
        check(loop, step, "Beta 后验 %s" % sid, expected_sr(d, sid),
              d["strategies"][sid]["success_rate"])


def run_main(loop, case_id, anomaly_type, expect_sid, used=()):
    # 每轮刷新 production.in:生产上每个 case 都是新算例;复用旧文件会让
    # delete_overlap 幂等空转——20 轮起空转判失败(no-op 不记账),结账序列会断
    with open(os.path.join(PROD, "production.in"), "w") as f:
        f.write("fix QEQ all qeq/reax 1 0.0 10.0 1.0e-6\n"
                "timestep 0.25\nrun 50000\n")
    in_path = os.path.join(TMP, "in_%s.json" % case_id)
    out_path = os.path.join(TMP, "out_%s.json" % case_id)
    json.dump({"prod_dir": PROD,
               "anomalies": [{"type": anomaly_type, "message": anomaly_type}],
               "history": [{"strategy": s} for s in used],
               "risk_score": 5, "risk_factors": [],
               "case_id": case_id, "cores": 4}, open(in_path, "w"))
    pend_before = sum(1 for h in db()["history"] if h["outcome"] == "pending")
    old_argv = sys.argv
    sys.argv = ["recover_agent.py", in_path, out_path]
    try:
        ra.main()
    finally:
        sys.argv = old_argv
    r = json.load(open(out_path))
    check(loop, "main", "recover成功(%s)" % case_id, True, r["success"])
    check(loop, "main", "选中策略(%s)" % case_id, expect_sid, r.get("strategy"))
    d = db()
    STATE["history_len"] += 1
    STATE["uses"][expect_sid] = STATE["uses"].get(expect_sid, 0) + 1
    check(loop, "main", "末条 outcome=pending", "pending",
          d["history"][-1]["outcome"])
    check(loop, "main", "pending +1", pend_before + 1,
          sum(1 for h in d["history"] if h["outcome"] == "pending"))
    return r


def settle(case_id, outcome, cause=None):
    """E2E 口径:success=DONE 全结;fail=同因 7 天窗。"""
    pend_before = sum(1 for h in db()["history"] if h["outcome"] == "pending")
    box = {}
    if outcome == "success":
        ra.locked_update(lambda d: box.__setitem__(
            "n", ra.settle_pending(d, "success", case_id=case_id)))
    else:
        ra.locked_update(lambda d: box.__setitem__(
            "n", ra.settle_pending(d, "fail", case_id=case_id,
                                   root_cause=cause, younger_than_days=7)))
    n = box["n"]
    d = db()
    pend_after = sum(1 for h in d["history"] if h["outcome"] == "pending")
    check("settle", case_id, "结账条数(%s)" % outcome, 1, n)
    check("settle", case_id, "pending -1", pend_before - 1, pend_after)
    return n


def orchestrator_fail_settle(case_id, anomalies, risk_factors=None):
    """复刻 orchestrator._settle_strategy_attempts 的 fail 全规则:
    同因(7d)→fail;年轻(<6h)一律 fail;老(>6h)→success(异根因不连坐)。"""
    current_cause = ra.identify_root_cause(anomalies, risk_factors or [])
    counts = {}

    def _settle(d):
        if current_cause:
            counts["same_cause"] = ra.settle_pending(
                d, "fail", case_id=case_id, root_cause=current_cause,
                younger_than_days=7)
        counts["young"] = ra.settle_pending(d, "fail", case_id=case_id,
                                            younger_than_hours=6)
        counts["old"] = ra.settle_pending(d, "success", case_id=case_id,
                                          older_than_hours=6)
    ra.locked_update(_settle)
    return current_cause, counts


def observe_and_apply(loop, step, expect_n_drafts, expect_key, expect_penalty):
    """observe 起草 → 真实 apply 通道 → 核对 db strategy_adjustments。"""
    summary = observe.observe()
    drafts = [p for p in summary["drafted"]
              if "strategy-weight" in os.path.basename(p)]
    check(loop, step, "起草策略提案数", expect_n_drafts, len(drafts))
    if not expect_n_drafts:
        return
    pdir = drafts[0]
    meta = json.load(open(os.path.join(pdir, "meta.json")))
    check(loop, step, "提案 meta", ("strategy_weight_tuning", "auto", "kit"),
          (meta["class"], meta["tier"], meta["root"]))
    diff_text = open(os.path.join(pdir, "diff.patch")).read()
    if expect_penalty > 0:
        check(loop, step, "diff 含目标 penalty", True,
              '"penalty": %s' % expect_penalty in diff_text)
    old_argv = sys.argv
    sys.argv = ["apply_proposal.py", pdir]
    try:
        rc = apply_proposal.main()
    finally:
        sys.argv = old_argv
    check(loop, step, "apply rc", 0, rc)
    rows = [json.loads(ln) for ln in open(apply_proposal.LEDGER) if ln.strip()]
    check(loop, step, "LEDGER 结果", "AUTO_APPLIED", rows[-1]["result"])
    d = db()
    adj = d.get("strategy_adjustments") or {}
    if expect_penalty > 0:
        check(loop, step, "db penalty[%s]" % expect_key, expect_penalty,
              adj.get(expect_key, {}).get("penalty"))
    else:
        check(loop, step, "条目摘除[%s]" % expect_key, False,
              expect_key in adj)
    # 归档已应用提案目录:同日同 slug 去重是设计节流(每日一档),
    # 驱动多步需让出草稿位;等价于跨日推进。LEDGER 留痕不动。
    suffix = 0
    while os.path.exists("%s.applied%d" % (pdir, suffix)):
        suffix += 1
    os.rename(pdir, "%s.applied%d" % (pdir, suffix))


def score_of(d, sid, cause, anomaly_type):
    """复算 select_strategy 对 sid 的打分:匹配度 + 后验*3 - penalty。"""
    factors = {anomaly_type}
    sinfo = d["strategies"][sid]
    return (sum(2 for f in factors if f in sinfo["for"])
            + sinfo["success_rate"] * 3 - ra._penalty(d, sid, cause))


def select_winner(loop, step, cause, anomaly_type, expect_sid, stripped_sid,
                  penalized_sid=None, penalty=0.0):
    d = db()
    anomalies = [{"type": anomaly_type}]
    sid, _ = ra.select_strategy(d, cause, anomalies, [], [])
    check(loop, step, "select 胜出(含压制)", expect_sid, sid)
    stripped = dict(d)
    stripped.pop("strategy_adjustments", None)
    sid2, _ = ra.select_strategy(stripped, cause, anomalies, [], [])
    check(loop, step, "select 胜出(去压制对照)", stripped_sid, sid2)
    if penalized_sid and penalty > 0:
        s_with = score_of(d, penalized_sid, cause, anomaly_type)
        s_without = score_of(stripped, penalized_sid, cause, anomaly_type)
        check(loop, step, "打分恰减 penalty(%s)" % penalized_sid,
              round(-penalty, 4), round(s_with - s_without, 4))


# ================================================================ 循环 A
# 策略A x atomic_ejection:竞争者 D 同样匹配 LOST_ATOMS,平分局 A 字典序先
print("=" * 70)
print("循环 A: 策略A(delete_overlap) x atomic_ejection(LOST_ATOMS)")
print("=" * 70)
CA = "atomic_ejection"

run_main("A", "a1", "LOST_ATOMS", "A"); settle("a1", "success")
verify_db_invariants("A", "A0 热身1胜")
run_main("A", "a2", "LOST_ATOMS", "A"); settle("a2", "success")
run_main("A", "a3", "LOST_ATOMS", "A"); settle("a3", "fail", CA)
run_main("A", "a4", "LOST_ATOMS", "A"); settle("a4", "fail", CA)
verify_db_invariants("A", "A0 2胜2负")   # sr 应 = 3/6 = 0.5

# 步骤1: 连败2 → 起草 0.15 → 应用 → A 被压制(D 胜出)
observe_and_apply("A", "A1 连败→0.15", 1, "A:atomic_ejection", 0.15)
select_winner("A", "A1 连败→0.15", CA, "LOST_ATOMS", "D", "A", "A", 0.15)

# 步骤2: 再连败2(强制选 A:D 已用过)→ 加压 0.30
run_main("A", "a5", "LOST_ATOMS", "A", used=("D",)); settle("a5", "fail", CA)
run_main("A", "a6", "LOST_ATOMS", "A", used=("D",)); settle("a6", "fail", CA)
verify_db_invariants("A", "A2 连败前")   # sr = 3/8 = 0.375
observe_and_apply("A", "A2 连败→0.30", 1, "A:atomic_ejection", 0.30)
# 步骤2 对照说明:A 后验已跌至 0.375,去压制后 D(0.5)仍胜——
# 此时压制由 Beta 后验+penalty 叠加,故对照期望 D
select_winner("A", "A2 连败→0.30", CA, "LOST_ATOMS", "D", "D", "A", 0.30)

# 步骤3: 连胜2 → 减压 0.30→0.15
run_main("A", "a7", "LOST_ATOMS", "A", used=("D",)); settle("a7", "success")
run_main("A", "a8", "LOST_ATOMS", "A", used=("D",)); settle("a8", "success")
verify_db_invariants("A", "A3 连胜前")   # sr = 5/10 = 0.5
observe_and_apply("A", "A3 连胜→0.15", 1, "A:atomic_ejection", 0.15)
select_winner("A", "A3 连胜→0.15", CA, "LOST_ATOMS", "D", "A", "A", 0.15)

# 步骤4: 再连胜2 → 减压到 0,条目摘除,恢复自由竞争
run_main("A", "a9", "LOST_ATOMS", "A", used=("D",)); settle("a9", "success")
run_main("A", "a10", "LOST_ATOMS", "A", used=("D",)); settle("a10", "success")
verify_db_invariants("A", "A4 连胜前")   # sr = 7/12 = 0.5833
observe_and_apply("A", "A4 连胜→摘除", 1, "A:atomic_ejection", 0)
select_winner("A", "A4 连胜→摘除", CA, "LOST_ATOMS", "A", "A")

# ================================================================ 循环 B
# 策略B x thermal_runaway:竞争者 D/E 同匹配 TEMPERATURE_SPIKE,平局 B 先
print("=" * 70)
print("循环 B: 策略B(reduce_timestep) x thermal_runaway(TEMPERATURE_SPIKE)")
print("=" * 70)
reset_db()
CB = "thermal_runaway"

run_main("B", "b1", "TEMPERATURE_SPIKE", "B"); settle("b1", "success")
run_main("B", "b2", "TEMPERATURE_SPIKE", "B"); settle("b2", "success")
run_main("B", "b3", "TEMPERATURE_SPIKE", "B"); settle("b3", "fail", CB)
run_main("B", "b4", "TEMPERATURE_SPIKE", "B"); settle("b4", "fail", CB)
verify_db_invariants("B", "B0 2胜2负")

observe_and_apply("B", "B1 连败→0.15", 1, "B:thermal_runaway", 0.15)
select_winner("B", "B1 连败→0.15", CB, "TEMPERATURE_SPIKE", "D", "B", "B", 0.15)

run_main("B", "b5", "TEMPERATURE_SPIKE", "B", used=("D", "E"))
settle("b5", "fail", CB)
run_main("B", "b6", "TEMPERATURE_SPIKE", "B", used=("D", "E"))
settle("b6", "fail", CB)
verify_db_invariants("B", "B2 连败前")
observe_and_apply("B", "B2 连败→0.30", 1, "B:thermal_runaway", 0.30)
select_winner("B", "B2 连败→0.30", CB, "TEMPERATURE_SPIKE", "D", "D", "B", 0.30)

run_main("B", "b7", "TEMPERATURE_SPIKE", "B", used=("D", "E"))
settle("b7", "success")
run_main("B", "b8", "TEMPERATURE_SPIKE", "B", used=("D", "E"))
settle("b8", "success")
verify_db_invariants("B", "B3 连胜前")
observe_and_apply("B", "B3 连胜→0.15", 1, "B:thermal_runaway", 0.15)
select_winner("B", "B3 连胜→0.15", CB, "TEMPERATURE_SPIKE", "D", "B", "B", 0.15)

run_main("B", "b9", "TEMPERATURE_SPIKE", "B", used=("D", "E"))
settle("b9", "success")
run_main("B", "b10", "TEMPERATURE_SPIKE", "B", used=("D", "E"))
settle("b10", "success")
verify_db_invariants("B", "B4 连胜前")
observe_and_apply("B", "B4 连胜→摘除", 1, "B:thermal_runaway", 0)
select_winner("B", "B4 连胜→摘除", CB, "TEMPERATURE_SPIKE", "B", "B")

# ================================================================ 循环 C
# 策略B x QEq_instability;混异根因 fail 验证不连坐老账;窗口20验证
print("=" * 70)
print("循环 C: 策略B x QEq_instability(QEQ_FAILURE)+异根因/窗口20")
print("=" * 70)
reset_db()
CC = "QEq_instability"
now = time.time()

# C-pre1: 窗口20验证——策略E 塞 23 条已结账(21负+2胜,近30h内,尾部2胜
# 不触发起草)。期望 E 后验只取近20条:18负2胜 → (2+1)/22 = 0.1364。
def _seed_e(d):
    for i in range(21):
        d["history"].append({"strategy": "E", "root_cause": CC,
                             "case_id": "seedE", "ts": fmt(now - (30 - i) * 3600),
                             "outcome": "fail"})
    for i in range(2):
        d["history"].append({"strategy": "E", "root_cause": CC,
                             "case_id": "seedE", "ts": fmt(now - (2 - i) * 3600),
                             "outcome": "success"})
ra.locked_update(_seed_e)
STATE["history_len"] += 23

# C-pre2: 异根因注入——同 case 两条 pending:D(atomic_ejection,7h 老账)+
# F(QEq_instability,1h 年轻)。随后以 TEMPERATURE_SPIKE(=thermal_runaway)
# 触发 orchestrator fail 结账:老账应判 success(不连坐),年轻应判 fail。
def _inject(d):
    d["history"].append({"strategy": "D", "root_cause": "atomic_ejection",
                         "case_id": "cInj", "ts": fmt(now - 7 * 3600),
                         "outcome": "pending"})
    d["history"].append({"strategy": "F", "root_cause": CC,
                         "case_id": "cInj", "ts": fmt(now - 1 * 3600),
                         "outcome": "pending"})
ra.locked_update(_inject)
STATE["history_len"] += 2

cause, counts = orchestrator_fail_settle("cInj", [{"type": "TEMPERATURE_SPIKE"}])
check("C", "C0 异根因", "触发根因", "thermal_runaway", cause)
check("C", "C0 异根因", "同因结账数(应0,无 thermal pending)", 0,
      counts.get("same_cause", 0))
check("C", "C0 异根因", "年轻<6h 判 fail 数", 1, counts["young"])
check("C", "C0 异根因", "老>6h 判 success 数(不连坐)", 1, counts["old"])
d = db()
inj = {(h["strategy"], h["root_cause"]): h["outcome"]
       for h in d["history"] if h["case_id"] == "cInj"}
check("C", "C0 异根因", "D 老账 outcome", "success", inj[("D", "atomic_ejection")])
check("C", "C0 异根因", "F 年轻账 outcome", "fail", inj[("F", CC)])
# 老账 success 的 ts 在 7h 前,排在 B:QEq 近账之前 → 不打断连败尾部
check("C", "C0 异根因", "E 窗口20 后验", 0.1364, d["strategies"]["E"]["success_rate"])

run_main("C", "c1", "QEQ_FAILURE", "B"); settle("c1", "success")
run_main("C", "c2", "QEQ_FAILURE", "B"); settle("c2", "success")
run_main("C", "c3", "QEQ_FAILURE", "B"); settle("c3", "fail", CC)
run_main("C", "c4", "QEQ_FAILURE", "B"); settle("c4", "fail", CC)
verify_db_invariants("C", "C0 2胜2负")

# 步骤1: 恰好 1 份起草(D 尾success/F 尾1负/E 尾2胜无压制 均不起草)
observe_and_apply("C", "C1 连败→0.15", 1, "B:QEq_instability", 0.15)
select_winner("C", "C1 连败→0.15", CC, "QEQ_FAILURE", "C", "B", "B", 0.15)

run_main("C", "c5", "QEQ_FAILURE", "B", used=("C", "F"))
settle("c5", "fail", CC)
run_main("C", "c6", "QEQ_FAILURE", "B", used=("C", "F"))
settle("c6", "fail", CC)
verify_db_invariants("C", "C2 连败前")
observe_and_apply("C", "C2 连败→0.30", 1, "B:QEq_instability", 0.30)
select_winner("C", "C2 连败→0.30", CC, "QEQ_FAILURE", "C", "C", "B", 0.30)

run_main("C", "c7", "QEQ_FAILURE", "B", used=("C", "F"))
settle("c7", "success")
run_main("C", "c8", "QEQ_FAILURE", "B", used=("C", "F"))
settle("c8", "success")
verify_db_invariants("C", "C3 连胜前")
observe_and_apply("C", "C3 连胜→0.15", 1, "B:QEq_instability", 0.15)
select_winner("C", "C3 连胜→0.15", CC, "QEQ_FAILURE", "C", "B", "B", 0.15)

run_main("C", "c9", "QEQ_FAILURE", "B", used=("C", "F"))
settle("c9", "success")
run_main("C", "c10", "QEQ_FAILURE", "B", used=("C", "F"))
settle("c10", "success")
verify_db_invariants("C", "C4 连胜前")
observe_and_apply("C", "C4 连胜→摘除", 1, "B:QEq_instability", 0)
select_winner("C", "C4 连胜→摘除", CC, "QEQ_FAILURE", "B", "B")

# 终态:strategy_adjustments 应为空(三条目都已摘除/未留)
d = db()
check("ALL", "终态", "adjustments 残留", 0, len(d.get("strategy_adjustments") or {}))
check("ALL", "终态", "history 总长度", STATE["history_len"], len(d["history"]))
check("ALL", "终态", "无 pending 残留", 0,
      sum(1 for h in d["history"] if h["outcome"] == "pending"))

# ---------------------------------------------------------------- 汇总
fails = [r for r in ROWS if not r["ok"]]
print("\n" + "=" * 70)
print("TOTAL: %d/%d PASS" % (len(ROWS) - len(fails), len(ROWS)))
with open(os.path.join(BASE, "results.json"), "w") as f:
    json.dump(ROWS, f, ensure_ascii=False, indent=2)
if fails:
    for r in fails:
        print("FAIL:", json.dumps(r, ensure_ascii=False))
sys.exit(1 if fails else 0)
