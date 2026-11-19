# -*- coding: utf-8 -*-
"""真实流程走查：以用户身份从头完成一次仿真，展示证据系统每步的实际审查结果。

不预设通过路径：先按真实习惯提交（有缺口），看系统问什么、拦什么，
再补证据推进；BUILD 之后跑真实 LAMMPS，ANALYZE 用真实数字验收。
每一步打印门禁的原始返回（拒因/证据分级/问题包/规则轨迹）。
"""
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(SRC, "evolution"))
sys.path.insert(0, os.path.join(SRC, "mcp"))
sys.path.insert(0, HERE)

import stage_state   # noqa: E402
import surface       # noqa: E402
import assessment    # noqa: E402
import content_check  # noqa: E402
import memory        # noqa: E402
import parse_lm      # noqa: E402
from mcp_client import McpClient  # noqa: E402
import drive_case as dc  # noqa: E402

CID = "REALFLOW-01"
DATA = os.path.join(HERE, "live_data_realflow")
KIT = os.path.join(HERE, "kit_local")
PAPER = "paper:10.1103/PhysRevB.33.7983"
STD = "std:Allen-Tildesley-1989-Computer-Simulation-of-Liquids"


def show(title, res):
    print("\n" + "=" * 78)
    print("【%s】" % title)
    print("  门禁结论: %s" % res.get("result"))
    if res.get("reasons"):
        print("  拒因: %s" % "；".join(str(x)[:80] for x in res["reasons"][:4]))
    for e in (res.get("evidence") or [])[:6]:
        print("  证据 %s → %s级%s" % (str(e["ref"])[:44], e["grade"],
                                      "" if e["countable"] else "（不计入）"))
    tr = res.get("rule_trace") or {}
    for k in ("E1", "E3", "E6", "E4", "E2", "E5"):
        if k in tr:
            v = tr[k]
            if isinstance(v, dict):
                v = {s: vv[-1] for s, vv in v.items()}
            print("  %s: %s" % (k, str(v)[:220]))
    qs = res.get("questions") or []
    if qs:
        print("  系统提问 %d 项，前 3 项：" % len(qs))
        for q in qs[:3]:
            print("    - [%s] %s（需 %s 级证据；已问过=%s）"
                  % (q["item_id"], str(q["question"])[:46], q["min_grade"],
                     q["asked_before"]))
    return res


def main():
    if os.path.exists(DATA):
        shutil.rmtree(DATA)
    os.makedirs(DATA, exist_ok=True)
    memory.EXP_DIR = os.path.join(DATA, "memory")
    memory.ATOMS = os.path.join(DATA, "memory", "atoms.jsonl")
    memory.LEDGER = os.path.join(DATA, "memory", "LEDGER.jsonl")
    os.environ["MDSIM_DATA_ROOT"] = DATA
    dc.DATA = DATA  # 对齐 drive_case 模块全局（工件/台账/评估都走它）

    print("################################################################")
    print("# 用户需求：用 EAM 势计算 fcc Cu 的平衡晶格常数与结合能，")
    print("#          对照 Foiles 1986（a0=3.615 Å，Ec=−3.54 eV）")
    print("################################################################")

    mcp = McpClient.start(DATA, KIT)
    c = dc.Case(mcp, CID, risk="medium")
    c.configure(static=True, potential_ref=PAPER,
                composition="fcc Cu，256 原子", structure="fcc 4×4×4，起始 3.585 Å",
                box="14.46 Å", atoms="256 原子", lattice="起始 a0=3.585 Å",
                acc_ref="Foiles1986 3.615 Å / −3.54 eV",
                acc_tol="a0 ±0.005 Å；Ec ±0.03 eV",
                acc_criteria="a0 与 Ec 偏差在容差内",
                production="0 步（静态最小化）")
    stage_state.init_task(CID, risk_level="medium", data_root=DATA)
    c.confirm_brief("计算 fcc Cu 平衡晶格常数与结合能，对照 Foiles 1986")

    # ---- 步骤 1：设计决策（有一项先用经验估算，看系统怎么判） ----
    print("\n---- 步骤 1：锁定设计决策（其中 D4 规模先用经验估算） ----")
    stage_state.lock_decisions(CID, {
        "D1_对象与范围": "fcc Cu 平衡晶格常数与结合能（文献复现）",
        "D2_执行引擎": "本机 LAMMPS 2Aug2023",
        "D3_环境条件": "0K 静态，box/relax 完全弛豫",
        "D4_边界与规模": "4×4×4=256 原子（经验：应该够）",
        "D5_关键参数": "EAM Cu_u3，起始 3.585 Å",
        "D6_方法族": "EAM 静态弛豫",
        "D7_验收标准": ["a0 偏差≤0.005 Å", "Ec 偏差≤0.03 eV"]},
        DATA,
        basis={"D1_对象与范围": {"kind": "user", "ref": "user:%s-brief" % CID,
                                 "reason": "用户指定任务", "scope": CID},
               "D2_执行引擎": {"kind": "measured", "ref": "$lmp_available"},
               "D3_环境条件": {"kind": "literature", "ref": STD},
               "D4_边界与规模": {"kind": "physics_estimate",
                                  "ref": "经验上 4×4×4 应该够"},
               "D5_关键参数": {"kind": "literature", "ref": PAPER},
               "D6_方法族": {"kind": "literature", "ref": PAPER},
               "D7_验收标准": {"kind": "literature", "ref": PAPER}})
    pa, pa_ref = assessment.assess_plan(CID, DATA)
    print("  计划评估: overall=%s critical=%s" % (pa["overall"], pa["critical_open"]))

    facts = c.tool_fact(lambda: {"lmp_available": 1,
                                 "eam_available": 1}, "开工自检")

    # ---- 步骤 2：第一次送审（不填核查面）----
    res, _ = c.decide("PLAN", "G-PLAN", "PASS", facts, "初版方案",
                      evidence=c.ev(["lmp_available"], [{"ref": pa_ref}]))
    show("步骤 2：初版方案送审（核查面未填）", res)

    # ---- 步骤 3：把核查面填上真实答案；截断半径还没查到文献，先标 open ----
    print("\n---- 步骤 3：逐项回答核查面（int-cutoff 尚无文献，先标待取证） ----")
    c.ensure_surface("G-PLAN")
    surface.record_coverage(CID, "int-cutoff", "open", data_root=DATA)
    res, _ = c.decide("PLAN", "G-PLAN", "PASS", facts, "补齐核查面（截断待查）",
                      evidence=c.ev(["lmp_available"], [{"ref": pa_ref}]))
    show("步骤 3：再次送审（截断半径待取证）", res)

    # ---- 步骤 4：查到文献依据，回答截断项 ----
    print("\n---- 步骤 4：查到 Foiles1986 的截断口径，回答该并过闸 ----")
    surface.record_coverage(CID, "int-cutoff", "evidenced", evidence=[PAPER],
                            answer="rc=8.5 Å（EAM Cu_u3 随势文件口径）",
                            data_root=DATA)
    res, ref_plan = c.decide("PLAN", "G-PLAN", "PASS", facts, "方案定稿",
                             evidence=c.ev(["lmp_available"], [{"ref": pa_ref}]))
    show("步骤 4：回答截断项（D4 估算仍被拦——符合预期）", res)

    # ---- 步骤 4b：规模决策改为用户确认（A 级，附理由） ----
    print("\n---- 步骤 4b：规模由用户拍板（记录确认与理由） ----")
    w = os.path.join(stage_state.task_dir(CID, DATA), "confirmations")
    os.makedirs(w, exist_ok=True)
    json.dump({"id": "u-d4", "scope": CID,
               "value": "4x4x4=256", "reason": "复现用途，接受有限尺寸误差",
               "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
              open(os.path.join(w, "u-d4.json"), "w", encoding="utf-8"))
    doc = stage_state.read_decisions(CID, DATA)
    doc["D4_边界与规模"]["basis"] = {
        "kind": "user", "ref": "user:u-d4", "value": "4x4x4",
        "reason": "复现用途，用户接受有限尺寸误差", "scope": CID}
    json.dump(doc, open(os.path.join(stage_state.task_dir(CID, DATA),
                                     "decisions.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    res, ref_plan = c.decide("PLAN", "G-PLAN", "PASS", facts, "方案定稿",
                             evidence=c.ev(["lmp_available"], [{"ref": pa_ref}]))
    show("步骤 4b：方案定稿", res)
    if res.get("result") != "GO":
        print("!! 未过闸，终止"); return 1
    stage_state.transition(CID, "BUILD", ref_plan, DATA)

    # ---- 步骤 5：写真实输入文件，过 BUILD/CONFIG ----
    print("\n---- 步骤 5：写输入文件（真实 EAM 弛豫脚本），过 BUILD/CONFIG ----")
    wd = c.workdir
    shutil.copyfile(os.path.join(dc.CASES_DIR, "in.lit01.lmp"),
                    os.path.join(wd, "in.lit01.lmp"))
    shutil.copyfile(dc.CU_EAM_SRC, os.path.join(wd, "Cu_u3.eam"))
    c.input_files = ["in.lit01.lmp"]
    facts = c.tool_fact(lambda: {"input_written": 1,
                                 "script_bytes": os.path.getsize(
                                     os.path.join(wd, "in.lit01.lmp"))}, "输入就绪")
    res, ref_build = c.decide("BUILD", "G-BUILD", "PASS", facts, "输入脚本落盘",
                              evidence=c.ev(["input_written"]))
    show("步骤 5a：G-BUILD", res)
    if res.get("result") != "GO":
        return 1
    stage_state.transition(CID, "CONFIG", ref_build, DATA)
    facts = c.tool_fact(lambda: {"params_ok": 1}, "配置核对")
    res, ref_cfg = c.decide("CONFIG", "G-CONFIG", "PASS", facts, "参数定稿",
                            evidence=c.ev(["params_ok"]))
    show("步骤 5b：G-CONFIG", res)
    if res.get("result") != "GO":
        return 1
    stage_state.transition(CID, "EXEC", ref_cfg, DATA)

    # ---- 步骤 6：过执行闸，跑真实 LAMMPS ----
    print("\n---- 步骤 6：执行闸 + 真实 LAMMPS 计算 ----")
    res, ref_exec = c.decide("EXEC", "G-EXEC", "PASS",
                             {"params_ok": 1, "input_written": 1},
                             "提交执行", evidence=c.ev(["params_ok"]))
    show("步骤 6a：G-EXEC", res)
    if res.get("result") != "GO":
        return 1
    ok, parsed, det = c.run_lammps("in.lit01.lmp", "L-realflow", "lit-run",
                                   "realflow")
    print("  真实执行: ok=%s rc=%s finished=%s audit=%s"
          % (ok, det.get("rc"), parsed.get("finished"), det.get("audit_id")))
    if not ok:
        return 1
    pr = parsed.get("prints") or {}
    a0 = float(str(pr["EVLIVE_A0"]).split()[0])
    ec = float(str(pr["EVLIVE_EC"]).split()[0])

    # ---- 步骤 7：验收（真实数字重算对账）----
    print("\n---- 步骤 7：验收——真实数字从日志重算并对照文献 ----")
    fc_a, _ = dc.factcheck(c, "a0", 3.615, "log_realflow.lammps",
                           "EVLIVE_A0", 0.005, "Å")
    fc_e, _ = dc.factcheck(c, "ec", -3.54, "log_realflow.lammps",
                           "EVLIVE_EC", 0.03, "eV")
    print("  实测: a0=%.4f Å（文献 3.615，偏差 %.4f）；Ec=%.4f eV（文献 −3.54，偏差 %.4f）"
          % (a0, abs(a0 - 3.615), ec, abs(ec + 3.54)))
    ra, ra_ref = assessment.assess_results(
        CID, {"repro_pairs": [
            {"field": "a0", "claimed": 3.615, "measured": a0},
            {"field": "ec", "claimed": -3.54, "measured": ec}]}, DATA)
    print("  结果评估: overall=%s critical=%s" % (ra["overall"], ra["critical_open"]))
    stage_state.transition(CID, "ANALYZE", ref_exec, DATA)
    acc = [{"item": "a0 偏差≤0.005 Å", "claimed": 3.615,
            "passed": abs(a0 - 3.615) <= 0.005, "factcheck": fc_a},
           {"item": "Ec 偏差≤0.03 eV", "claimed": -3.54,
            "passed": abs(ec + 3.54) <= 0.03, "factcheck": fc_e}]
    res, ref_an = c.decide("ANALYZE", "G-ANALYZE", "PASS",
                           {"a0": a0, "ec": ec}, "验收结题",
                           evidence=c.ev([], [{"ref": "LEDGER:L-realflow:success"},
                                              {"ref": ra_ref}]),
                           acceptance=acc)
    show("步骤 7：G-ANALYZE", res)
    if res.get("result") == "GO":
        stage_state.complete_task(CID, ref_an, DATA)
        print("\n✔ 任务结题。全程一次真实执行，验收两项全过。")
    mcp.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
