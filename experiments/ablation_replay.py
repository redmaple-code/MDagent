#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""学习层消融对照回放(2026-09-02)
====================================
同一批合成故障场景回放两遍,量化演化/学习层的净贡献:

  学习臂 ON :复用 kit recover_agent 真实代码——record_attempt/settle_pending
              真结账、Beta(1,1) 后验回写;连败压制按 observe.py 同规则
              (尾部连败≥2 → penalty+0.15 封顶0.6;连胜≥2 → 减压)每 case 末施加。
  对照臂 OFF:同一套选择代码,但每次结账后把 success_rate 重置 0.5、
              清空 strategy_adjustments——选择逻辑的输入被中性化,
              等价于"学习层不存在"。

设计纪律(防"剧本偏心"质疑):
  * 真值矩阵中启发式匹配分(select_strategy 的 for 匹配 +2/因子)认不出
    最优策略:每个根因下最优与陷阱策略匹配分打平,双臂差异只能来自学习。
  * 配对回放:同一 case 序列、同一组均匀随机数按尝试序喂两臂,
    与策略选择无关,方差最小。
  * 每 case 尝试上限 2:现实中连败有资源/耐心约束,状态机连败落 FAILED。

用法: python3 ablation_replay.py [--cases 300] [--seed 42] [--json out.json]
退出码: 0=学习臂显著占优; 1=未达标(供回归门复用)。
"""
import argparse
import json
import os
import random
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "mcp"))


def _kit_base():
    if os.environ.get("MDSIM_KIT_BASE"):
        return os.environ["MDSIM_KIT_BASE"]
    try:
        import paths  # noqa
        cfg = json.load(open(os.path.join(paths.CODE_ROOT, "mcp", "md_config.json"),
                             encoding="utf-8"))
        return cfg.get("local_base", "")
    except Exception:
        return ""


KIT = _kit_base()
sys.path.insert(0, os.path.join(KIT, "agents", "runtime", "v3.3"))
import recover_agent  # noqa: E402

# ---------------- 真值矩阵(地面真值,智能体不可见) ----------------
# 每根因下最优策略与陷阱策略的启发式匹配分相同:
#   QEq_instability  (anomaly QEQ_FAILURE):B/C/F 同 +2;真值 F=.85,B=C=.30
#   thermal_runaway  (TEMPERATURE_SPIKE + rapid_heating):B/E 同 +4;真值 E=.80,B=.35
#   atomic_ejection  (LOST_ATOMS):A/D 同 +2;真值 A=.80,D=.35
TRUE_P = {
    "QEq_instability":  {"A": .10, "B": .30, "C": .30, "D": .10, "E": .10, "F": .85},
    "thermal_runaway":  {"A": .10, "B": .35, "C": .30, "D": .35, "E": .80, "F": .20},
    "atomic_ejection":  {"A": .80, "B": .20, "C": .10, "D": .35, "E": .10, "F": .10},
}
CAUSES = [("QEq_instability", 0.40), ("thermal_runaway", 0.30), ("atomic_ejection", 0.30)]
ANOM = {"QEq_instability": ([{"type": "QEQ_FAILURE"}], []),
        "thermal_runaway": ([{"type": "TEMPERATURE_SPIKE"}], ["rapid_heating"]),
        "atomic_ejection": ([{"type": "LOST_ATOMS"}], [])}
MAX_ATTEMPTS = 2
PENALTY_STEP, PENALTY_MAX = 0.15, 0.6  # 与 evolution/observe.py 同口径
STRATEGIES = ["A", "B", "C", "D", "E", "F"]


def _tail_streak(rows, sid, cause):
    """与 observe._tail_streak 同语义的尾部同向计数(输入已按 ts 排)。"""
    kind, n = None, 0
    for h in reversed(rows):
        if h.get("strategy") != sid or h.get("root_cause") != cause:
            continue
        o = h.get("outcome")
        if kind is None:
            kind = o
        if o != kind:
            break
        n += 1
    return kind, n


def _evolve_penalties():
    """学习臂每 case 末的演化周期(observe.py strategy-weight 规则的最小等价)。"""
    def _upd(d):
        hist = sorted((h for h in d.get("history", [])
                       if isinstance(h, dict) and h.get("outcome") in ("success", "fail")),
                      key=lambda h: str(h.get("ts", "")))
        adj = d.setdefault("strategy_adjustments", {})
        pairs = {(h["strategy"], h["root_cause"]) for h in hist}
        for sid, cause in pairs:
            kind, n = _tail_streak(hist, sid, cause)
            key = "%s:%s" % (sid, cause)
            old = (adj.get(key) or {}).get("penalty", 0.0) if isinstance(adj.get(key), dict) else 0.0
            if kind == "fail" and n >= 2:
                pen = min(old + PENALTY_STEP, PENALTY_MAX)
                adj[key] = {"penalty": round(pen, 2), "reason": "连败%d次" % n,
                            "ts": "replay"}
            elif kind == "success" and n >= 2 and old > 0:
                pen = round(max(old - PENALTY_STEP, 0.0), 2)
                if pen > 0:
                    adj[key] = {"penalty": pen, "reason": "连胜减压", "ts": "replay"}
                else:
                    adj.pop(key, None)
    recover_agent.locked_update(_upd)


def _neutralize():
    """对照臂:学习产物全抹除——不只重置 success_rate/清压制,history 也必须
    清空:否则任何后续 settle 的 _recompute_rates 会从残留历史重建后验,
    学习从后门溜回对照臂(2026-09-02 首轮回放实证:对照臂诡异收敛到 F)。"""
    def _upd(d):
        for s in d.get("strategies", {}).values():
            if isinstance(s, dict):
                s["success_rate"] = 0.5
        d.pop("strategy_adjustments", None)
        d["history"] = []
    recover_agent.locked_update(_upd)


def _run_case(case_id, cause, draws, learning):
    """单 case 回放:选策略→按真值判成败→真实函数结账。返回 (recovered, attempts)。"""
    anomalies, factors = ANOM[cause]
    used_history = []
    attempts = 0
    while attempts < MAX_ATTEMPTS:
        db = recover_agent.load_db()
        sid, _info = recover_agent.select_strategy(db, cause, anomalies, factors,
                                                   used_history)
        if sid is None:
            break
        attempts += 1
        ok = draws[attempts - 1] < TRUE_P[cause][sid]

        def _rec(d, sid=sid):
            d["strategies"][sid]["uses"] = d["strategies"][sid].get("uses", 0) + 1
            recover_agent.record_attempt(d, sid, cause, case_id)
        recover_agent.locked_update(_rec)
        recover_agent.locked_update(
            lambda d: recover_agent.settle_pending(d, "success" if ok else "fail",
                                                   case_id=case_id))
        used_history.append({"strategy": sid})
        if ok:
            return True, attempts
    return False, attempts


def run_arm(cases, learning, tmpdir):
    """跑一臂;cases = [(case_id, cause, [u1, u2])]。返回指标 dict。"""
    recover_agent.STRATEGY_DB = os.path.join(
        tmpdir, "db_%s.json" % ("on" if learning else "off"))
    recovered, attempts_all, first_fix = 0, [], 0
    per_cause = {}
    for case_id, cause, draws in cases:
        ok, att = _run_case(case_id, cause, draws, learning)
        if learning:
            _evolve_penalties()
        else:
            _neutralize()
        recovered += 1 if ok else 0
        first_fix += 1 if (ok and att == 1) else 0
        attempts_all.append(att)
        pc = per_cause.setdefault(cause, [0, 0])
        pc[0] += 1 if ok else 0
        pc[1] += 1
    n = len(cases)
    return {"recovered": recovered, "n": n,
            "recovery_rate": round(recovered / n, 3),
            "mean_attempts": round(sum(attempts_all) / n, 3),
            "first_fix_rate": round(first_fix / n, 3),
            "per_cause": {c: "%d/%d" % tuple(v) for c, v in per_cause.items()}}


def gen_cases(n, seed):
    """配对回放:case 序列与每 case 的均匀随机数一次生成,两臂共享。"""
    rng = random.Random(seed)
    causes, weights = zip(*CAUSES)
    out = []
    for i in range(n):
        cause = rng.choices(causes, weights=weights)[0]
        out.append(("case%04d" % i, cause, [rng.random() for _ in range(MAX_ATTEMPTS)]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=300)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    if not KIT or not os.path.isdir(KIT):
        print("kit 路径解析失败(设 MDSIM_KIT_BASE)", file=sys.stderr)
        return 2

    cases = gen_cases(a.cases, a.seed)
    tmpdir = tempfile.mkdtemp(prefix="ablation-")
    on = run_arm(cases, True, tmpdir)
    off = run_arm(cases, False, tmpdir)

    report = {"seed": a.seed, "cases": a.cases, "max_attempts": MAX_ATTEMPTS,
              "learning_on": on, "learning_off": off,
              "delta": {"recovery_rate": round(on["recovery_rate"] - off["recovery_rate"], 3),
                        "mean_attempts": round(on["mean_attempts"] - off["mean_attempts"], 3),
                        "first_fix_rate": round(on["first_fix_rate"] - off["first_fix_rate"], 3)}}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    ok = (report["delta"]["recovery_rate"] >= 0.10
          and report["delta"]["mean_attempts"] < 0
          and report["delta"]["first_fix_rate"] >= 0.15)
    print("判定: %s(胜率 %+dpp / 均次 %+.2f / 一次修复 %+dpp)" % (
        "学习臂显著占优" if ok else "差异未达标",
        round(report["delta"]["recovery_rate"] * 100),
        report["delta"]["mean_attempts"],
        round(report["delta"]["first_fix_rate"] * 100)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
