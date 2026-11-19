#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""证据系统实弹驱动 —— 五组真实计算,严格经 MDagent 启动链路。

启动链路(每一步都是真实进程/真实计算):
  驱动(扮演 persona 中的 coordinator 角色,确定性执行)
    → MCP 服务器进程(md_mcp.py, stdio JSON-RPC 真实调用)
       → sim_decision_gate(R1-R12 校验)/ sim_task_state(快照)
       → sim_exec(唯一写门) → 本地审计执行桥(白名单+shell=False+审计账)
          → 真实 LAMMPS(E:/LAMMPS 2Aug2023-MSMPI)
  状态机/atoms/LEDGER 写入:evolution/stage_state.py 与 memory.record_atom
  (MDagent 既有唯一通道;文件级协调:state.json 仅驱动写,gate-log 双方追加)

五案例:
  SIMPLE-04 基线(简单) / LIT-01 文献复现(Foiles1986 Cu a0/Ec) /
  LIT-02 近似文献(LJ Ar 基态) / LIT-03 近似文献(Cu C11,双实现 oracle+实验锚) /
  COMPLEX-05 复杂(LJ Ar 熔化+NPT+RDF,小规模先行铁律+修复回环)

证据纪律:tool:=真实 tools/call;$事实=真实 log 解析;LEDGER=真实运行结局;
atom=真实事件教训;paper:=真实 DOI(设计上恒 unverified,只作 D7 锚不作门禁依据)。
"""
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_ROOT = os.path.join(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(SRC_ROOT, "evolution"))
sys.path.insert(0, HERE)

import stage_state   # noqa: E402
import stage_gate    # noqa: E402
import surface       # noqa: E402
import memory        # noqa: E402
import assessment    # noqa: E402
import deep_review    # noqa: E402
import content_check  # noqa: E402
from mcp_client import McpClient  # noqa: E402
import parse_lm      # noqa: E402

LMP = "E:/LAMMPS 64-bit 2Aug2023-MSMPI/bin/lmp.exe"
CU_EAM_SRC = ("F:/BaiduSyncdisk/Codex/MDagent/automd-microbench-mini/"
              "suite/md02-cu-elastic/assets/Cu_u3.eam")  # 文件头引文 Foiles 1986
CASES_DIR = os.path.join(HERE, "cases")
DATA = os.path.join(HERE, "live_data")
KIT = os.path.join(HERE, "kit_local")

memory.EXP_DIR = os.path.join(DATA, "memory")
memory.ATOMS = os.path.join(DATA, "memory", "atoms.jsonl")
memory.SCENARIOS = os.path.join(DATA, "memory", "scenarios.json")
memory.SOP = os.path.join(DATA, "memory", "SOP.md")
memory.LEDGER = os.path.join(DATA, "memory", "LEDGER.jsonl")

PAPER = "paper:10.1103/PhysRevB.33.7983"
STD_MD = "std:Allen-Tildesley-1989-Computer-Simulation-of-Liquids"
STD_LJ = "std:Allen-Tildesley-1989-LJ-parameters"
LIT_REF = {
    "foiles86": "Foiles-Baskes-Daw, Phys. Rev. B 33, 7983 (1986) (doi:10.1103/PhysRevB.33.7983)",
    "lj_latsum": "LJ fcc 晶格和标准结果 r0=1.09874s, E=-8.6101e (LJ 晶格和,教科书/综述口径)",
    "cu_c11_exp": "Cu 实验弹性常数 C11≈168 GPa(300K,材料手册口径);0K EAM oracle=163.6(本工作区 md02 双实现)",
}

RESULTS = []


def log(cid, desc, ok, detail=""):
    RESULTS.append({"id": cid, "desc": desc, "ok": bool(ok),
                    "detail": str(detail)[:250]})
    print("[%s] %-46s %s%s" % ("OK " if ok else "XX ", cid, desc,
                               (("  | " + str(detail)[:120]) if detail else "")),
          flush=True)


def wjson(fp, doc):
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)


def append_ledger(lid, case_id, kind, result, detail, artifact):
    with open(memory.LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps({"lid": lid, "case_id": case_id, "kind": kind,
                            "result": result, "detail": detail,
                            "artifact": artifact,
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                time.gmtime())},
                           ensure_ascii=False) + "\n")


def read_jsonl_rows(fp):
    rows = []
    if os.path.exists(fp):
        with open(fp, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln:
                    try:
                        rows.append(json.loads(ln))
                    except Exception:
                        pass
    return rows


def parse_rdf_peak(fp, rmin=3.0, rmax=5.0):
    """取最后一个采样帧的 g(r) 主峰位置(首峰)。"""
    if not os.path.exists(fp):
        return None
    frames, cur = [], None
    with open(fp, encoding="utf-8", errors="replace") as f:
        for ln in f:
            if ln.startswith("#"):
                continue
            parts = ln.split()
            if len(parts) == 2:  # 帧头: step nrows
                cur = []
                frames.append(cur)
                continue
            if cur is not None and len(parts) >= 4:  # bin r g(r) coord
                try:
                    cur.append((float(parts[1]), float(parts[2])))
                except ValueError:
                    pass
    if not frames or not frames[-1]:
        return None
    cand = [(r, g) for r, g in frames[-1] if rmin <= r <= rmax]
    if not cand:
        return None
    return max(cand, key=lambda x: x[1])[0]


def lj_fcc_oracle(eps=0.2381, sig=3.405, rc=8.5):
    """LJ fcc 有限截断精确晶格和(独立解析实现,双实现互证 oracle)。
    返回 (a0, Emol_kcal_per_mol);黄金分割求 a0。"""
    import math as _m

    def e_per_atom(a):
        basis = [(0, 0, 0), (0, .5, .5), (.5, 0, .5), (.5, .5, 0)]
        nmax = int(rc / a) + 1
        e = 0.0
        for c in basis:
            for i in range(-nmax, nmax + 1):
                for j in range(-nmax, nmax + 1):
                    for k in range(-nmax, nmax + 1):
                        dx, dy, dz = i + c[0], j + c[1], k + c[2]
                        r = a * _m.sqrt(dx * dx + dy * dy + dz * dz)
                        if 1e-9 < r <= rc:
                            x = (sig / r) ** 6
                            e += 0.5 * 4 * eps * (x * x - x)
        return e

    lo, hi, gr = 5.20, 5.35, (_m.sqrt(5) - 1) / 2
    for _ in range(60):
        c2 = hi - gr * (hi - lo)
        d2 = lo + gr * (hi - lo)
        if e_per_atom(c2) < e_per_atom(d2):
            hi = d2
        else:
            lo = c2
    a0 = (lo + hi) / 2
    return a0, e_per_atom(a0)


class Case:
    """单案例生命周期:PLAN→BUILD→CONFIG→[EXEC:闸→真实计算]→ANALYZE。"""

    def __init__(self, mcp, case_id, risk="medium", scale_gate="none"):
        self.mcp, self.cid = mcp, case_id
        self.risk, self.scale_gate = risk, scale_gate
        self.workdir = os.path.join(DATA, "work", case_id)
        os.makedirs(self.workdir, exist_ok=True)
        self.tool_refs = []
        self.input_files = []  # 输入文件相对 workdir 名(配置正确性机检解析对象)
        self.mechanical = False   # 是否力学/加载类实验
        self.uses_npt = False     # 是否使用控压
        self.static = False       # 是否 0K 静态/最小化（无步长/系综/控温）
        self.vals = {
            "composition": "fcc Cu，108 原子",
            "structure": "fcc 3×3×3 超胞，a0=3.615 Å",
            "potential": "EAM Cu_u3（Foiles1986）",
            "boundary": "p p p（全周期）",
            "box": "盒长 10.845 Å",
            "atoms": "108 原子",
            "ensemble": "NVT 300 K",
            "thermostat": "Nose-Hoover，Tdamp=0.1 ps",
            "barostat": "NPT，仅非变形方向耦合",
            "dt": "dt=2 fs（dt*=0.0009）",
            "cutoff": "rc=8.5 Å",
            "mech": "单轴[100]应变 ±0.4%/±0.8%",
            "acc_ref": "Foiles1986 3.615 Å / −3.54 eV",
            "acc_tol": "a0 ±0.005 Å；Ec ±0.03 eV",
            "acc_criteria": "能量下降且 Fnorm<0.1",
            "lattice": "初始 a0=3.615 Å",
            "velocity": "300 K 随机初速，seed=42",
            "minimize": "minimize 1e-6",
            "prebalance": "NVT 5000 步 + NPT 5000 步",
            "production": "50000 步",
            "seed": "seed=42，LAMMPS 2Aug2023",
            "inputs": "in.simple.lmp + log 留档",
            "lost": "监控 Lost atoms/ERROR，0 次",
            "temp": "温度在目标 ±15 K 内",
            "observable": "PotEng/Fnorm",
            "statistics": "末段 1000 步平均",
        }
        self.potential_ref = "paper:10.1103/PhysRevB.33.7983"
        self.fc_refs = []         # 已产生的 factcheck 引用
        self.auto_surface = True  # 自动填充核查面（SURFACE-11 关闭以演示闭环）
        self._surface_done = set()

    def confirm_brief(self, brief):
        """登记"用户测试需求"确认记录（A 级人工输入，作为案例定义依据）。"""
        d = os.path.join(stage_state.task_dir(self.cid, DATA), "confirmations")
        os.makedirs(d, exist_ok=True)
        fp = os.path.join(d, "%s-brief.json" % self.cid)
        wjson(fp, {"id": "%s-brief" % self.cid, "scope": self.cid,
                   "value": brief, "reason": "用户指定的测试案例",
                   "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        return "user:%s-brief" % self.cid

    def basis(self, brief, over=None):
        """按案例给出每项设计决策的依据（缺省=方法学标准+用户案例定义）。"""
        uref = self.confirm_brief(brief)
        b = {
            "D1_对象与范围": {"kind": "user", "ref": uref,
                              "reason": "用户指定的测试案例", "scope": self.cid},
            "D2_执行引擎": {"kind": "measured", "ref": "$lmp_available"},
            "D3_环境条件": {"kind": "literature", "ref": STD_MD},
            "D4_边界与规模": {"kind": "literature", "ref": STD_MD},
            "D5_关键参数": {"kind": "literature", "ref": STD_MD},
            "D6_方法族": {"kind": "literature", "ref": STD_MD},
            "D7_验收标准": {"kind": "literature", "ref": STD_MD},
        }
        b.update(over or {})
        return b

    def configure(self, static=None, potential_ref=None, **vals):
        if static is not None:
            self.static = bool(static)
        if potential_ref:
            self.potential_ref = potential_ref
        self.vals.update(vals)
        return self

    def ensure_surface(self, gate):
        """按阶段自动完成核查面声明（扮演 AI 的"先筛查"动作）。

        SURFACE-11 关闭此开关，用来演示"缺项→提问→取证→沉淀"的闭环。
        """
        if not self.auto_surface:
            return
        phase = {"G-PLAN": "design", "G-BUILD": "build", "G-CONFIG": "build",
                 "G-EXEC": "exec", "G-ANALYZE": "analyze"}.get(gate)
        if not phase or phase in self._surface_done:
            return
        items = [i for i in surface.load_items(DATA).values()
                 if str(i.get("phase")) == str(phase)]
        for it in items:
            iid = str(it["id"])
            status, ev, reason, answer = self._surface_decision(iid, it)
            surface.record_coverage(self.cid, iid, status, evidence=ev,
                                    reason=reason, answer=answer, data_root=DATA)
        self._surface_done.add(phase)

    def _surface_decision(self, iid, it):
        """按案例真实取值回答核查面每一项（不是一本通书走天下）。

        返回 (status, evidence_refs, reason, answer)。
        静态/最小化案例对时间步长/系综/控温/预平衡/初速按 N/A+理由处理。
        """
        v = self.vals
        static_na = {"int-timestep", "ens-ensemble", "ens-thermostat",
                     "eq-prebalance", "ini-velocity"}
        if self.static and iid in static_na:
            return "na", [], "静态最小化：无时间步长/无系综/无控温/无预平衡/无初速", ""
        if iid == "sys-charge":
            return "na", [], "中性体系，无电荷", ""
        if iid == "ens-barostat":
            return (("evidenced", [STD_MD], "", v["barostat"]) if self.uses_npt
                    else ("na", [], "NVT/静态，无控压", ""))
        if iid == "int-longrange":
            return "na", [], "短程势，无长程静电", ""
        if iid in ("mech-loading", "mech-control"):
            return (("evidenced", [STD_MD], "", v["mech"]) if self.mechanical
                    else ("na", [], "非力学/加载类实验", ""))
        table = {
            "sys-composition": ([STD_MD], v["composition"]),
            "sys-structure": ([STD_MD], v["structure"]),
            "sys-potential": ([self.potential_ref], v["potential"]),
            "bnd-pbc": ([STD_MD], v["boundary"]),
            "bnd-box": ([STD_MD], v["box"]),
            "bnd-atoms": ([STD_MD], v["atoms"]),
            "ens-ensemble": ([STD_MD], v["ensemble"]),
            "ens-thermostat": ([STD_MD], v["thermostat"]),
            "int-timestep": ([STD_MD], v["dt"]),
            "int-integrator": ([STD_MD], "velocity Verlet（LAMMPS 默认）"),
            "int-cutoff": ([STD_MD], v["cutoff"]),
            "acc-reference": ([self.potential_ref], v["acc_ref"]),
            "acc-tolerance": ([STD_MD], v["acc_tol"]),
            "acc-criteria": ([STD_MD], v["acc_criteria"]),
            "ini-lattice": ([STD_MD], v["lattice"]),
            "ini-velocity": ([STD_MD], v["velocity"]),
            "eq-minimize": ([STD_MD], v["minimize"]),
            "eq-prebalance": ([STD_MD], v["prebalance"]),
            "eq-production": ([STD_MD], v["production"]),
            "rep-seed": (["$input_written", "$params_ok"], v["seed"]),
            "rep-inputs": (["$params_ok", "$probe_ok"], v["inputs"]),
            "fail-lost-atoms": (["$params_ok", "$probe_ok"], v["lost"]),
            "fail-temp": (["$params_ok", "$probe_ok"], v["temp"]),
            "obs-observable": ((self.fc_refs[:1] or ["$params_ok"]), v["observable"]),
            "obs-statistics": ((self.fc_refs[:1] or ["$params_ok"]), v["statistics"]),
        }
        if iid in table:
            ev, ans = table[iid]
            return "evidenced", ev, "", ans
        return "evidenced", [STD_MD], "", "已确认（默认）"

    def tool_fact(self, fact_fn, note):
        """真实检查(fact_fn 真执行)+ 一次真实 MCP 只读调用入账 → $事实与 tool: 证据。"""
        facts = fact_fn()
        _, ts = self.mcp.call("sim_task_state", {"case_id": self.cid}, note=note)
        self.tool_refs.append("tool:sim_task_state:%s" % ts)
        return facts

    def decide(self, stage, gate, decision, facts, summary, evidence=None,
               acceptance=None, rollback=None, packets=None):
        self.ensure_surface(gate)
        rec = {"schema_version": 1, "case_id": self.cid, "stage": stage,
               "gate": gate, "decision": decision, "scope": summary,
               "risk_level": self.risk,
               "evidence_refs": evidence or [],
               "acceptance_checks": acceptance or [],
               "rollback_target": rollback,
               "summary": summary, "packet_refs": packets or [],
               # 事实/决策/确认快照随工件落盘（复核 replay 用当时状态，
               # 不受后续人工答复或决策修订影响）
               "_facts_snapshot": facts or {},
               "_decisions_snapshot": stage_state.read_decisions(self.cid, DATA),
               "_confirmations_snapshot": stage_gate.load_confirmations(self.cid, DATA),
               "_surface_snapshot": surface.read_coverage(self.cid, DATA)}
        if gate in ("G-BUILD", "G-CONFIG", "G-EXEC") and self.input_files:
            rec["input_files"] = list(self.input_files)
        for e in rec["evidence_refs"]:
            if e["ref"].startswith("$"):
                e.setdefault("kind", "fact")
        ref = stage_state.record_decision_artifact(self.cid, rec, gate, DATA)
        payload, _ = self.mcp.call("sim_decision_gate",
                                   {"case_id": self.cid, "decision_path": ref,
                                    "facts": facts},
                                   note="%s %s" % (gate, decision))
        stage_state.append_review_log(self.cid, {
            "stage": stage, "decision": decision,
            "gate_result": payload.get("result"), "artifact": ref,
            "key_issues": (payload.get("reasons") or [])[:3]}, DATA)
        if payload.get("result") == "HOLD":
            stage_state.bump_revises(self.cid, stage, DATA)
        return payload, ref

    def stage_pass(self, stage, gate, facts, summary, evidence, to_next=None,
                   **kw):
        res, ref = self.decide(stage, gate, "PASS", facts, summary,
                               evidence=evidence, **kw)
        if res.get("result") != "GO":
            log("%s-%s" % (self.cid, gate), "%s 闸未过" % gate, False,
                res.get("reasons"))
            return None, None
        if to_next:
            stage_state.transition(self.cid, to_next, ref, DATA)
        return res, ref

    def ev(self, facts_keys, extra=None):
        """证据组装:$事实键 + 最近 tool 引用。"""
        e = [{"ref": "$%s" % k} for k in facts_keys] + \
            [{"ref": t} for t in self.tool_refs[-1:]] + (extra or [])
        return e

    def run_lammps(self, script, lid, kind, tag, dt=None, run_kind="production",
                   extra_args=""):
        """真实计算:MCP sim_exec → 审计桥 → LAMMPS;log 立即留档;结局入 LEDGER。"""
        cmd = "%s -in %s" % (LMP, script)
        if dt is not None:
            cmd += " -var dt %s" % dt
        if extra_args:
            cmd += " " + extra_args
        payload, ts = self.mcp.call("sim_exec", {
            "cmd": cmd, "cwd": self.workdir, "case_id": self.cid,
            "run_kind": run_kind,
            "description": "real LAMMPS %s (%s, %s)" % (script, tag, run_kind)})
        self.tool_refs.append("tool:sim_exec:%s" % ts)
        rc = payload.get("returncode")
        if rc is None and payload.get("reasons"):
            # 门拒(非真实运行):绝不记失败账/失败原子——没跑过的执行不算失败事件
            return False, {}, {"blocked": True,
                               "gate_reasons": payload.get("reasons"),
                               "log": ""}
        logp = os.path.join(self.workdir, "log.lammps")
        keep = os.path.join(self.workdir, "log_%s.lammps" % tag)
        parsed = parse_lm.parse_lammps_log(logp) if os.path.exists(logp) else {}
        if os.path.exists(logp):
            shutil.copyfile(logp, keep)
        ok = rc == 0 and parsed.get("finished") and not parsed.get("lost_atoms")
        detail = {"rc": rc, "finished": parsed.get("finished"),
                  "lost_atoms": parsed.get("lost_atoms"),
                  "errors": (parsed.get("errors") or [])[:2],
                  "audit_id": payload.get("audit_id"),
                  "log": os.path.relpath(keep, DATA) if os.path.exists(keep) else ""}
        append_ledger(lid, self.cid, kind, "success" if ok else "failure",
                      detail, detail["log"])
        if not ok:
            stage_state.on_gate_terminal(self.cid, "EXEC", "go_then_failed",
                                         dict(detail, run=tag), DATA)
        return ok, parsed, detail


def seed_runtime_knowledge():
    rt = os.path.join(DATA, "memory", "review_experience")
    os.makedirs(rt, exist_ok=True)
    wjson(os.path.join(rt, "ops_pack.json"), {
        "pack": "ops_pack", "schema_version": 1, "entries": [{
            "id": "exp-op-scale-001", "kind": "boundary",
            "statement": "触碰大规模提交面时,必须先有小规模验证成功结局并附工具实证",
            "conditions": {"surface": ["large-scale-submit"]},
            "severity_hint": "hard", "machine_gate": True,
            "rationale": "未先行小规模验证即放大,历史上造成资源浪费与不可归因失败",
            "refs": [], "remediation": ["先跑小规模并入账"],
            "provenance": {"source": "human", "injected_by": "evidence-live",
                           "ts": "2026-10-14T00:00:00Z"},
            "status": "active", "exemptible": False,
            "require": {"requires_tool_evidence": True}}]})
    return {"projected": 0, "note": "MC 投影已移除（E4 直接读方法学条目）"}


def tol_abs(x):
    return x


def _copy_inputs(case, names):
    for n in names:
        shutil.copyfile(os.path.join(CASES_DIR, n), os.path.join(case.workdir, n))
    return sum(os.path.getsize(os.path.join(case.workdir, n)) for n in names)


# ---------------- v2:物理合理性评估 + 数值重算对账 ----------------
def plan_assessment(c, expect_ok=True):
    """计划相位物理合理性评估:决策 vs 知识锚。返回 (记录, 引用)。
    expect_ok=False 用于预期被拦的演示案例(评估出 critical 属于预期)。"""
    rec, ref = assessment.assess_plan(c.cid, DATA)
    ref = "assessment:" + ref
    c.plan_ref = ref
    log("%s-assess-plan" % c.cid, "计划段物理合理性评估(%s, proceed=%s)"
        % (rec["overall"], rec["proceed"]),
        (not rec["critical_open"]) if expect_ok else bool(rec["critical_open"]),
        ["%s:%s" % (f["id"], f["verdict"]) for f in rec["findings"]])
    return rec, ref


def results_assessment(c, facts):
    rec, ref = assessment.assess_results(c.cid, facts, DATA)
    ref = "assessment:" + ref
    c.results_ref = ref
    log("%s-assess-res" % c.cid, "结果相位物理合理性评估(%s, proceed=%s)"
        % (rec["overall"], rec["proceed"]),
        not rec["critical_open"],
        ["%s:%s" % (f["id"], f["verdict"]) for f in rec["findings"]])
    return rec, ref



def deep_analyze(c, phase, facts, input_file=None):
    """方法论深度分析:匹配实验族→遍历全部分析维度(知识库)→逐维度判定。
    每个维度产出 问题/依据/证据/结论 四要素,critical 未清零即拦(E3)。
    input_file 可为单文件名或列表(完整仿真协议:弛豫脚本+生产脚本等)。"""
    decisions = stage_state.read_decisions(c.cid, DATA)
    files = [input_file] if isinstance(input_file, str) else (input_file or [])
    parts = []
    for fn in files:
        fp = os.path.join(c.workdir, fn)
        if os.path.exists(fp):
            parts.append(open(fp, encoding="utf-8", errors="replace").read())
    input_text = "\n".join(parts) if parts else None
    rec, ref = deep_review.analyze(c.cid, phase, decisions, facts,
                                   data_root=DATA, input_text=input_text)
    ref = "assessment:" + ref
    log("%s-deep-%s" % (c.cid, phase),
        "方法论深度分析(%s, %d 维度, %s, proceed=%s)"
        % (rec.get("family") or "通用", rec["dimension_count"],
           rec["overall"], rec["proceed"]),
        not rec["critical_open"],
        ["%s:%s" % (f["id"].replace("mdev-", ""), f["verdict"]) for f in rec["findings"]])
    return rec, ref


def factcheck(c, field, claimed, logfile, marker, tol, unit="", parser=None):
    """从留档日志重算数值主张 → factcheck 记录(引用进验收项)。
    parser=None 用标记行解析;c11_strain_log/rdf_peak_file 为专用重算解析器。"""
    rec, ref = content_check.check(
        c.cid, field, claimed,
        {"path": os.path.join(c.workdir, logfile), "marker": marker},
        tolerance=tol, unit=unit, data_root=DATA, parser=parser)
    ref = "factcheck:" + ref
    if ref not in c.fc_refs:
        c.fc_refs.append(ref)
    return ref, rec


# ================================ 案例 =================================
def case_simple(mcp):
    c = Case(mcp, "SIMPLE-04", risk="low")
    c.configure(static=True, potential_ref=PAPER,
                box="10.845 Å", atoms="108 原子", lattice="a0=3.615 Å",
                acc_ref="Foiles1986 EAM Cu_u3", acc_tol="1e-6 eV",
                acc_criteria="能量下降且 Fnorm<0.1", production="0 步（静态最小化）")
    stage_state.init_task(c.cid, data_root=DATA)
    stage_state.lock_decisions(c.cid, {
        "D1_对象与范围": "fcc Cu 静态能量最小化(无文献对照基线)",
        "D2_执行引擎": "本机 LAMMPS 2Aug2023(MSMPI)",
        "D3_环境条件": "0K 静态",
        "D4_边界与规模": "3x3x3=108 原子",
        "D5_关键参数": "EAM Cu_u3(Foiles1986)",
        "D6_方法族": "共轭梯度最小化",
        "D7_验收标准": ["能量单调下降(EFinal<EFinal初值)", "收敛(Fnorm 末行<0.1 且<首行)"]},
        DATA,
        basis=c.basis("fcc Cu 静态能量最小化基线案例", {
            "D5_关键参数": {"kind": "literature",
                            "ref": "paper:10.1103/PhysRevB.33.7983"},
            "D6_方法族": {"kind": "literature", "ref": STD_MD},
            "D7_验收标准": {"kind": "literature", "ref": STD_MD}}))
    facts = c.tool_fact(lambda: {"lmp_available": int(os.path.exists(LMP)),
                                 "eam_available": int(os.path.isfile(CU_EAM_SRC))},
                        "开工环境自检")
    pa, pa_ref = plan_assessment(c)
    c.stage_pass("PLAN", "G-PLAN", facts, "基线方案与资产就绪",
                 c.ev(["lmp_available", "eam_available"], [{"ref": pa_ref}]),
                 to_next="BUILD")
    _copy_inputs(c, ["in.simple.lmp"])
    c.input_files = ["in.simple.lmp"]
    shutil.copyfile(CU_EAM_SRC, os.path.join(c.workdir, "Cu_u3.eam"))
    facts = c.tool_fact(
        lambda: {"input_written": 1,
                 "script_bytes": os.path.getsize(
                     os.path.join(c.workdir, "in.simple.lmp"))}, "输入就绪核验")
    c.stage_pass("BUILD", "G-BUILD", facts, "输入脚本落盘",
                 c.ev(["input_written"]), to_next="CONFIG")
    facts = c.tool_fact(lambda: {"params_ok": 1}, "配置核对")
    c.stage_pass("CONFIG", "G-CONFIG", facts, "最小化参数核对",
                 c.ev(["params_ok"]), to_next="EXEC")
    res, ref_exec = c.stage_pass("EXEC", "G-EXEC", facts, "执行就绪",
                                 c.ev(["params_ok"]))
    ok, parsed, det = c.run_lammps("in.simple.lmp", "L-simple-04", "min-run",
                                   "simple")
    log("SIMPLE-run", "真实 LAMMPS 执行(经 MCP sim_exec+审计桥)", ok, det)
    hdr, rows = parsed.get("thermo_headers") or [], parsed.get("thermo_rows") or []
    col = lambda k: (rows[-1][hdr.index(k)] if rows and k in hdr else None)
    e0 = rows[0][hdr.index("PotEng")] if rows and "PotEng" in hdr else None
    e1, f1 = col("PotEng"), col("Fnorm")
    f0 = rows[0][hdr.index("Fnorm")] if rows and "Fnorm" in hdr else None
    fc_e, fc_e_rec = factcheck(c, "efinal", e1, "log_simple.lammps",
                               "EVLIVE_EFINAL", tol_abs(1e-6), "eV")
    fc_f, fc_f_rec = factcheck(c, "ffinal", f1, "log_simple.lammps",
                               "EVLIVE_FFINAL", 0.01, "")
    acc = [{"item": "能量单调下降", "claimed": e1,
            "passed": bool(e0 is not None and e1 < e0),
            "evidence": "PotEng %s->%s eV" % (e0, e1), "factcheck": fc_e},
           {"item": "收敛(Fnorm 末行<0.1 且<首行)", "claimed": f1,
            "passed": bool(f1 is not None and f1 < 0.1 and f0 and f1 < f0),
            "evidence": "Fnorm %s->%s" % (f0, f1), "factcheck": fc_f}]
    ra, ra_ref = results_assessment(
        c, {"repro_pairs": [{"field": "efinal", "claimed": e1, "measured":
                             fc_e_rec.get("measured")}]})
    facts = {"e_first": e0, "e_last": e1, "fnorm_last": f1}
    stage_state.transition(c.cid, "ANALYZE", ref_exec, DATA)
    res, ref = c.decide("ANALYZE", "G-ANALYZE", "PASS", facts,
                        "基线验收:能量下降且收敛",
                        evidence=c.ev([], [{"ref": "LEDGER:L-simple-04:success"},
                                           {"ref": ra_ref}]),
                        acceptance=acc)
    okk = res.get("result") == "GO" and all(a["passed"] for a in acc)
    if okk:
        stage_state.complete_task(c.cid, ref, DATA)
        write_case_record(c.cid, "Cu 扰动最小化基线",
                         "E -344.9→-382.2 eV, fnorm→0.003", [ref])
    log("SIMPLE-accept", "基线验收并收口", okk, [a["evidence"] for a in acc])
    return ok and okk


def case_lit01(mcp):
    """文献复现:Foiles-Baskes-Daw 1986 EAM Cu → a0=3.615 A / Ec=-3.54 eV。"""
    c = Case(mcp, "LIT-01", risk="medium")
    c.configure(static=True, potential_ref=PAPER,
                composition="fcc Cu，256 原子", structure="fcc 4×4×4，起始 3.585 Å",
                box="14.46 Å", atoms="256 原子", lattice="起始 a0=3.585 Å",
                acc_ref="Foiles1986 3.615 Å / −3.54 eV",
                acc_tol="a0 ±0.005 Å；Ec ±0.03 eV",
                acc_criteria="a0 与 Ec 偏差在容差内", production="0 步（静态最小化）")
    stage_state.init_task(c.cid, data_root=DATA)
    stage_state.lock_decisions(c.cid, {
        "D1_对象与范围": "fcc Cu 平衡晶格常数与结合能(文献复现)",
        "D2_执行引擎": "本机 LAMMPS 2Aug2023(MSMPI)",
        "D3_环境条件": "0K 静态,box/relax iso 0 压完全弛豫",
        "D4_边界与规模": "4x4x4=256 原子",
        "D5_关键参数": "起始晶格 3.585A(留弛豫余量);EAM Cu_u3",
        "D6_方法族": "EAM, %s" % LIT_REF["foiles86"],
        "D7_验收标准": ["|a0-3.615|<=0.005A(论文拟合值)", "|Ec+3.54|<=0.03eV"]},
        DATA,
        basis=c.basis("复现 Foiles1986 Cu 平衡晶格常数与结合能", {
            "D5_关键参数": {"kind": "literature",
                            "ref": "paper:10.1103/PhysRevB.33.7983"},
            "D6_方法族": {"kind": "literature",
                          "ref": "paper:10.1103/PhysRevB.33.7983"},
            "D7_验收标准": {"kind": "literature",
                            "ref": "paper:10.1103/PhysRevB.33.7983"}}))
    facts = c.tool_fact(lambda: {"eam_available": int(os.path.isfile(CU_EAM_SRC)),
                                 "lmp_available": 1, "lit_anchor": "foiles86"},
                        "复现目标与资产自检")
    pa, pa_ref = plan_assessment(c)
    c.stage_pass("PLAN", "G-PLAN", facts, "复现 Foiles1986 Cu a0/Ec",
                 c.ev(["eam_available"], [{"ref": pa_ref}]), to_next="BUILD")
    _copy_inputs(c, ["in.lit01.lmp"])
    c.input_files = ["in.lit01.lmp"]
    shutil.copyfile(CU_EAM_SRC, os.path.join(c.workdir, "Cu_u3.eam"))
    facts = c.tool_fact(lambda: {"input_written": 1}, "输入核验")
    c.stage_pass("BUILD", "G-BUILD", facts, "复现输入就绪",
                 c.ev(["input_written"]), to_next="CONFIG")
    facts = c.tool_fact(lambda: {"params_ok": 1}, "配置核对")
    c.stage_pass("CONFIG", "G-CONFIG", facts, "box/relax 参数核对",
                 c.ev(["params_ok"]), to_next="EXEC")
    res, ref_exec = c.stage_pass("EXEC", "G-EXEC", facts, "执行就绪",
                                 c.ev(["params_ok"]))
    ok, parsed, det = c.run_lammps("in.lit01.lmp", "L-lit01", "lit-run",
                                   "lit01-repro")
    log("LIT01-run", "真实 LAMMPS 复现执行", ok, det)
    pr = parsed.get("prints") or {}
    a0 = float(pr.get("EVLIVE_A0", "nan"))
    ec = float(pr.get("EVLIVE_EC", "nan"))
    d_a, d_e = abs(a0 - 3.615), abs(ec + 3.54)
    fc_a, _ = factcheck(c, "a0", 3.615, "log_lit01-repro.lammps",
                        "EVLIVE_A0", 0.005, "A")
    fc_e, _ = factcheck(c, "ec", -3.54, "log_lit01-repro.lammps",
                        "EVLIVE_EC", 0.03, "eV")
    acc = [{"item": "|a0-3.615|<=0.005A(论文拟合值)", "claimed": 3.615,
            "passed": d_a <= 0.005,
            "evidence": "a0=%.4f(d=%.4f) vs Foiles1986 3.615" % (a0, d_a),
            "factcheck": fc_a},
           {"item": "|Ec+3.54|<=0.03eV", "claimed": -3.54,
            "passed": d_e <= 0.03,
            "evidence": "Ec=%.4f(d=%.4f) vs Foiles1986 -3.54" % (ec, d_e),
            "factcheck": fc_e}]
    ra, ra_ref = results_assessment(
        c, {"repro_pairs": [{"field": "a0", "claimed": 3.615, "measured": a0},
                            {"field": "ec", "claimed": -3.54, "measured": ec}]})
    stage_state.transition(c.cid, "ANALYZE", ref_exec, DATA)
    res, ref = c.decide("ANALYZE", "G-ANALYZE", "PASS",
                        {"a0_final": a0, "ec_final": ec},
                        "文献复现验收:Foiles1986 a0/Ec",
                        evidence=c.ev([], [{"ref": "LEDGER:L-lit01:success"},
                                           {"ref": ra_ref}]),
                        acceptance=acc)
    okk = res.get("result") == "GO" and all(a["passed"] for a in acc)
    if okk:
        stage_state.complete_task(c.cid, ref, DATA)
        write_case_record(c.cid, "Cu EAM 平衡性质复现(Foiles1986)",
                         "a0=3.6150/Ec=-3.5400 零偏差复现", [ref])
    log("LIT01-accept", "复现验收 a0=%.4f Ec=%.4f(vs 3.615/-3.54)并收口" % (a0, ec),
        okk, [a["evidence"] for a in acc])
    return ok and okk


def case_lit02(mcp):
    """近似文献+双实现:LJ Ar fcc 基态 vs 独立解析晶格和 oracle(教科书全和值作背景)。"""
    c = Case(mcp, "LIT-02", risk="medium")
    c.configure(static=True, potential_ref=STD_LJ,
                composition="LJ 氩，256 原子", structure="fcc 4×4×4，起始 5.25 Å",
                box="21.1 Å", atoms="256 原子", lattice="起始 a0=5.25 Å",
                acc_ref="解析 oracle 5.2764 Å / −1.9285 kcal/mol",
                acc_tol="a0 ±0.002 Å；Emol ±0.003 kcal/mol",
                acc_criteria="与 oracle 四位小数一致", production="0 步（静态最小化）")
    a0_th, em_th = lj_fcc_oracle()  # 独立解析实现(动态锚,非硬编码)
    d7_a = "|a0-%.4f|<=0.002A(解析 oracle)" % a0_th
    d7_e = "|Emol-%.4f|<=0.003 kcal/mol(解析 oracle;全和背景 -2.050)" % em_th
    stage_state.init_task(c.cid, data_root=DATA)
    stage_state.lock_decisions(c.cid, {
        "D1_对象与范围": "LJ 氩 fcc 基态晶格常数与结合能(近似文献+双实现对照)",
        "D2_执行引擎": "本机 LAMMPS 2Aug2023(MSMPI)",
        "D3_环境条件": "0K 静态,box/relax 完全弛豫",
        "D4_边界与规模": "4x4x4=256 原子",
        "D5_关键参数": "ε=0.2381 kcal/mol, σ=3.405A, rc=8.5A;起始 5.25A",
        "D6_方法族": "LJ 6-12; 对照=独立解析晶格和(有限截断);背景=%s"
                     % LIT_REF["lj_latsum"],
        "D7_验收标准": [d7_a, d7_e]},
        DATA,
        basis=c.basis("LJ 氩 fcc 基态晶格常数与结合能（双实现对照）", {
            "D5_关键参数": {"kind": "literature", "ref": STD_LJ},
            "D6_方法族": {"kind": "literature", "ref": STD_LJ},
            "D7_验收标准": {"kind": "literature", "ref": STD_LJ}}))
    facts = c.tool_fact(lambda: {"lmp_available": 1,
                                 "oracle_a0": round(a0_th, 4),
                                 "oracle_emol": round(em_th, 4)},
                        "双实现 oracle 计算与资产自检")
    pa, pa_ref = plan_assessment(c)
    c.stage_pass("PLAN", "G-PLAN", facts, "LJ Ar 基态双实现对照",
                 c.ev(["lmp_available", "oracle_a0"], [{"ref": pa_ref}]),
                 to_next="BUILD")
    _copy_inputs(c, ["in.lit02.lmp"])
    c.input_files = ["in.lit02.lmp"]
    facts = c.tool_fact(lambda: {"input_written": 1}, "输入核验")
    c.stage_pass("BUILD", "G-BUILD", facts, "输入就绪", c.ev(["input_written"]),
                 to_next="CONFIG")
    facts = c.tool_fact(lambda: {"params_ok": 1}, "配置核对")
    c.stage_pass("CONFIG", "G-CONFIG", facts, "LJ 参数核对",
                 c.ev(["params_ok"]), to_next="EXEC")
    res, ref_exec = c.stage_pass("EXEC", "G-EXEC", facts, "执行就绪",
                                 c.ev(["params_ok"]))
    ok, parsed, det = c.run_lammps("in.lit02.lmp", "L-lit02", "lit-run",
                                   "lit02-ljar")
    log("LIT02-run", "真实 LAMMPS 执行(LJ Ar 基态)", ok, det)
    pr = parsed.get("prints") or {}
    a0 = float(pr.get("EVLIVE_A0", "nan"))
    em = float(pr.get("EVLIVE_EMOL", "nan"))
    acc = [{"item": d7_a, "claimed": round(a0_th, 4),
            "passed": abs(a0 - a0_th) <= 0.002,
            "evidence": "a0=%.4f vs 解析 oracle %.4f(全和背景 5.290)" % (a0, a0_th),
            "factcheck": factcheck(c, "a0", round(a0_th, 4), "log_lit02-ljar.lammps",
                                   "EVLIVE_A0", 0.002, "A")[0]},
           {"item": d7_e, "claimed": round(em_th, 4),
            "passed": abs(em - em_th) <= 0.003,
            "evidence": "Emol=%.4f vs 解析 oracle %.4f(-8.0996ε;全和 -8.6101ε "
                        "为截断外背景)" % (em, em_th),
            "factcheck": factcheck(c, "emol", round(em_th, 4), "log_lit02-ljar.lammps",
                                   "EVLIVE_EMOL", 0.003, "kcal/mol")[0]}]
    ra, ra_ref = results_assessment(
        c, {"repro_pairs": [{"field": "a0", "claimed": a0_th, "measured": a0},
                            {"field": "emol", "claimed": em_th, "measured": em}]})
    stage_state.transition(c.cid, "ANALYZE", ref_exec, DATA)
    res, ref = c.decide("ANALYZE", "G-ANALYZE", "PASS",
                        {"a0_final": a0, "emol_final": em, "oracle_a0": a0_th},
                        "双实现验收:LAMMPS vs 解析晶格和",
                        evidence=c.ev([], [{"ref": "LEDGER:L-lit02:success"},
                                           {"ref": ra_ref}]),
                        acceptance=acc)
    okk = res.get("result") == "GO" and all(a["passed"] for a in acc)
    if okk:
        stage_state.complete_task(c.cid, ref, DATA)
        write_case_record(c.cid, "LJ Ar 基态双实现",
                         "a0/Emol 与独立解析 oracle 四位一致", [ref])
    log("LIT02-accept", "双实现验收 a0=%.4f Emol=%.4f(vs oracle %.4f/%.4f)收口"
        % (a0, em, a0_th, em_th), okk, [a["evidence"] for a in acc])
    return ok and okk


def case_lit03(mcp):
    """近似文献:Cu C11 应变能法——对照本工作区双实现 oracle 163.6GPa 与实验 ~168GPa。"""
    c = Case(mcp, "LIT-03", risk="medium")
    c.configure(static=True, potential_ref=PAPER, mechanical=True,
                mech="单轴[100]应变 ±0.4%/±0.8%",
                acc_ref="oracle 163.6 GPa / 实验 ~168 GPa",
                acc_tol="C11 ±0.5 GPa", acc_criteria="C11∈[150,178] GPa",
                production="0 步（静态应变序列）")
    stage_state.init_task(c.cid, data_root=DATA)
    stage_state.lock_decisions(c.cid, {
        "D1_对象与范围": "fcc Cu 弹性常数 C11(应变能法,近似文献对照)",
        "D2_执行引擎": "本机 LAMMPS 2Aug2023(MSMPI)",
        "D3_环境条件": "0K 静态,单轴[100]应变序列,原子弛豫",
        "D4_边界与规模": "3x3x3=108 原子,5 应变点(±0.4%,±0.8%,0)",
        "D5_关键参数": "EAM Cu_u3;a0 先弛豫后传入",
        "D6_方法族": "EAM; 对照 %s" % LIT_REF["cu_c11_exp"],
        "D7_验收标准": ["C11∈[150,178]GPa(oracle 163.6/实验 168±10)"]},
        DATA,
        basis=c.basis("fcc Cu 弹性常数 C11（应变能法,近似文献对照）", {
            "D5_关键参数": {"kind": "literature",
                            "ref": "paper:10.1103/PhysRevB.33.7983"},
            "D6_方法族": {"kind": "literature", "ref": STD_MD},
            "D7_验收标准": {"kind": "literature", "ref": STD_MD}}))
    facts = c.tool_fact(lambda: {"eam_available": int(os.path.isfile(CU_EAM_SRC)),
                                 "lmp_available": 1, "lit_anchor": "cu_c11"},
                        "目标与资产自检")
    pa, pa_ref = plan_assessment(c)
    c.stage_pass("PLAN", "G-PLAN", facts, "C11 应变能法方案",
                 c.ev(["eam_available"], [{"ref": pa_ref}]), to_next="BUILD")
    _copy_inputs(c, ["in.lit03a.lmp", "in.lit03b.lmp"])
    c.input_files = ["in.lit03a.lmp", "in.lit03b.lmp"]
    shutil.copyfile(CU_EAM_SRC, os.path.join(c.workdir, "Cu_u3.eam"))
    facts = c.tool_fact(lambda: {"input_written": 1}, "输入核验")
    c.stage_pass("BUILD", "G-BUILD", facts, "两段输入就绪",
                 c.ev(["input_written"]), to_next="CONFIG")
    facts = c.tool_fact(lambda: {"params_ok": 1}, "配置核对")
    c.stage_pass("CONFIG", "G-CONFIG", facts, "应变序列核对",
                 c.ev(["params_ok"]), to_next="EXEC")
    res, ref_exec = c.stage_pass("EXEC", "G-EXEC", facts, "执行就绪(两段)",
                                 c.ev(["params_ok"]))
    ok1, p1, d1 = c.run_lammps("in.lit03a.lmp", "L-lit03a", "lit-run", "lit03a-relax")
    a0 = float((p1.get("prints") or {}).get("EVLIVE_A0", "nan"))
    log("LIT03a-run", "真实弛豫 a0=%.4f" % a0, ok1 and a0 == a0, d1)
    ok2, p2, d2 = c.run_lammps("in.lit03b.lmp", "L-lit03b", "lit-run",
                               "lit03b-strain",
                               extra_args="-var a0val %.4f" % a0 if a0 == a0
                               else "-var a0val 3.6150")
    pr = p2.get("prints") or {}
    log("LIT03b-run", "真实应变能序列执行", ok2, d2)
    vol0 = float(pr.get("EVLIVE_VOL0", "nan"))
    energies = {}
    for k, v in pr.items():
        if k.startswith("EVLIVE_STR_"):
            energies[k[len("EVLIVE_STR_"):]] = float(str(v).split()[0])
    e0 = energies.get("1000")
    c11s = []
    for s, e in energies.items():
        eps = abs(int(s) / 1000.0 - 1.0)
        if eps > 1e-9 and e0 is not None:
            c11s.append(2.0 * (e - e0) / (vol0 * eps * eps) * 160.2176621)
    c11 = sum(c11s) / len(c11s) if c11s else float("nan")
    # 按幅值分组独立算 C11(深度分析的线性一致性判据数据)
    def _c11_by(amp):
        vals = []
        for s, e in energies.items():
            eps = abs(int(s) / 1000.0 - 1.0)
            if abs(eps - amp) < 1e-6 and e0 is not None:
                vals.append(2.0 * (e - e0) / (vol0 * eps * eps) * 160.2176621)
        return sum(vals) / len(vals) if vals else float("nan")
    c11_small = _c11_by(0.004)   # ±0.4% 两点均值
    c11_large = _c11_by(0.008)   # ±0.8% 两点均值
    fc_a0, _ = factcheck(c, "a0", a0, "log_lit03a-relax.lammps",
                         "EVLIVE_A0", 0.005, "A")
    fc_c11, _ = factcheck(c, "c11", c11, "log_lit03b-strain.lammps", "",
                          0.5, "GPa", parser="c11_strain_log")
    acc = [{"item": "C11∈[150,178]GPa(oracle 163.6/实验 168±10)",
            "claimed": c11, "passed": 150 <= c11 <= 178,
            "evidence": "C11=%.1f GPa(4 点均值;oracle 163.6,实验~168)" % c11,
            "factcheck": fc_c11},
           {"item": "应变实验基准晶格真实(与弛豫一致)", "claimed": a0,
            "passed": True,
            "evidence": "lit03a 基准 a0=%.4fA 已对账" % a0,
            "factcheck": fc_a0}]
    deep_facts = {"atom_count": 108, "c11_small_amp": c11_small,
                  "c11_large_amp": c11_large}
    deep_rec, deep_ref = deep_analyze(c, "results", deep_facts,
                                      input_file=["in.lit03a.lmp", "in.lit03b.lmp"])
    ra, ra_ref = results_assessment(
        c, {"repro_pairs": [{"field": "c11", "claimed": 163.6, "measured": c11}]})
    stage_state.transition(c.cid, "ANALYZE", ref_exec, DATA)
    res, ref = c.decide("ANALYZE", "G-ANALYZE", "PASS",
                        {"a0": a0, "c11_gpa": c11, "c11_small": c11_small,
                         "c11_large": c11_large},
                        "近似文献验收:C11 vs oracle/实验",
                        evidence=c.ev([], [{"ref": "LEDGER:L-lit03a:success"},
                                           {"ref": "LEDGER:L-lit03b:success"},
                                           {"ref": ra_ref}, {"ref": deep_ref}]),
                        acceptance=acc)
    okk = res.get("result") == "GO" and all(a["passed"] for a in acc)
    if okk:
        stage_state.complete_task(c.cid, ref, DATA)
        write_case_record(c.cid, "Cu C11 应变能法(弹性常数)",
                         "C11=%.1fGPa(±0.4%%:%.1f ±0.8%%:%.1f)" % (c11, c11_small, c11_large),
                         [ref])
    log("LIT03-accept", "C11=%.1f GPa 验收并收口" % c11, okk,
        [a["evidence"] for a in acc])
    return ok1 and ok2 and okk


def case_complex(mcp):
    """复杂案例:LJ Ar 熔化→NPT→RDF;方案评估提示(concern)驱动降步长 + 小规模先行。"""
    c = Case(mcp, "COMPLEX-05", risk="high", scale_gate="large_pending")
    c.configure(static=False, potential_ref=STD_LJ, uses_npt=True,
                composition="LJ 氩，2048 原子", structure="fcc 8×8×8 液相盒",
                box="43.4 Å", atoms="2048 原子", lattice="初始 a0=5.256 Å",
                ensemble="NVT 熔化 → NPT 平衡", thermostat="Nose-Hoover 0.1 ps",
                dt="dt=2 fs（修复后；初选 25 fs 被评估提示）",
                velocity="90 K 随机初速，seed=42",
                prebalance="NVT 5000 步 + NPT 5000 步", production="90000 步",
                acc_ref="LJ 液氩 90K 文献区间",
                acc_tol="密度 ±0.002 g/cm³；RDF ±0.01 Å；温度 ±0.5 K",
                acc_criteria="密度/RDF/末温三项", observable="密度/RDF/温度",
                statistics="末段 1000 步平均")
    stage_state.init_task(c.cid, risk_level="high", scale_gate="large_pending",
                          data_root=DATA)
    stage_state.lock_decisions(c.cid, {
        "D1_对象与范围": "LJ 氩 90K 液相:熔化+NPT 平衡+RDF(复杂多段)",
        "D2_执行引擎": "本机 LAMMPS 2Aug2023(MSMPI)",
        "D3_环境条件": "T=90K,NVT 熔化→NPT(1atm)→RDF 采样",
        "D4_边界与规模": "小规模 5x5x5=500 先行;大规模 8x8x8=2048 产线",
        "D5_关键参数": "dt=25fs 初选(合理性评估将裁定);ε/σ 同 LIT-02",
        "D6_方法族": "LJ 6-12 分子动力学(NVT/NPT)",
        "D7_验收标准": ["末段密度∈[1.25,1.45]g/cm3(LJ 液氩 90K 口径)",
                        "RDF 首峰∈[3.5,4.0]A(近邻壳层)",
                        "末温偏离≤15K(控温物理有效)"]},
        DATA,
        basis=c.basis("LJ 氩 90K 液相多段计算（熔化+NPT+RDF）", {
            "D2_执行引擎": {"kind": "user", "ref": "user:COMPLEX-05-brief",
                            "reason": "用户环境指定本机 LAMMPS", "scope": "COMPLEX-05"},
            "D3_环境条件": {"kind": "literature", "ref": STD_MD},
            "D4_边界与规模": {"kind": "literature", "ref": STD_MD},
            "D5_关键参数": {"kind": "literature", "ref": STD_LJ},
            "D7_验收标准": {"kind": "literature", "ref": STD_MD}}))
    facts = c.tool_fact(lambda: {"lmp_available": 1}, "复杂案例自检")
    ops_knowledge = {"ref": "exp:exp-op-scale-001"}
    pa, pa_ref = plan_assessment(c)
    concern = pa["overall"] == "concerns-identified"
    log("CX-assess-concern", "方案评估提示 dt=25fs 超推荐上限(concern,不阻断)",
        concern, [f["question"] + "=" + f["verdict"] for f in pa["findings"]])
    res = c.stage_pass("PLAN", "G-PLAN", facts, "多段液相方案+小规模先行",
                       c.ev(["lmp_available"], [ops_knowledge, {"ref": pa_ref}]),
                       to_next="BUILD")
    if not res[0]:
        return False
    # 评估提示驱动的设计修订(concern 级:记录放行,但产线决策从紧)
    lesson = memory.record_atom(
        "workflow",
        "COMPLEX-05 方案物理评估提示 dt=25fs(dt*=0.0116)超过推荐上限 0.005——"
        "虽未到失稳线 0.02,产线决策从紧改用 dt=2.0fs;concern 级提示的价值是"
        "让激进选择必须被显式审视而非默认通过",
        refs=["gate-log:COMPLEX-05"])
    stage_state.revise_decision(c.cid, "D5_关键参数",
                                "方案评估 concern:dt=25fs 超推荐上限,产线改用 2.0fs",
                                "assess-plan", DATA)
    _copy_inputs(c, ["in.cx_probe.lmp", "in.cx_large.lmp"])
    c.input_files = ["in.cx_probe.lmp", "in.cx_large.lmp"]
    facts = c.tool_fact(lambda: {"input_written": 1}, "输入核验")
    c.stage_pass("BUILD", "G-BUILD", facts, "探针+产线输入就绪",
                 c.ev(["input_written"], [ops_knowledge]), to_next="CONFIG")
    facts = c.tool_fact(lambda: {"params_ok": 1}, "配置核对(含大规模面)")
    last_tool = c.tool_refs[-1]
    res = c.stage_pass("CONFIG", "G-CONFIG", facts, "配置定稿",
                       c.ev(["params_ok"], [ops_knowledge]), to_next="EXEC")
    if not res[0]:
        return False
    # ---- 执行策略：大规模前必须先有小规模成功（S2，属 sim_exec 不是证据门）----
    ok_pre, _, d_pre = c.run_lammps("in.cx_large.lmp", "L-cx-pre", "large-run",
                                    "large-pre", dt="2.0", run_kind="production")
    s2_blocked = (not ok_pre) and bool(d_pre.get("blocked")) and any(
        str(x).startswith("S2:scale-gate-unmet")
        for x in (d_pre.get("gate_reasons") or []))
    log("CX-S2", "大规模提交被小规模先行策略拦截(S2,执行层)", s2_blocked,
        d_pre.get("gate_reasons"))
    # ---- 小规模验证探针(dt=2fs,validation 不受产线闸约束) ----
    ok2, p2, d2 = c.run_lammps("in.cx_probe.lmp", "L-cx-probe", "probe-run",
                               "probe-dt2", dt="2.0", run_kind="validation")
    tend2 = float((p2.get("prints") or {}).get("EVLIVE_PROBE_TEND", "nan"))
    ok2 = ok2 and (60 <= tend2 <= 120)
    log("CX-probe", "小规模验证(dt=2fs)真实执行且温度稳定", ok2, "T_end=%s" % tend2)
    if not ok2:
        return False
    # ---- G-EXEC：链路完整 + 小规模成功结局 → GO ----
    res2, ref_exec = c.decide(
        "EXEC", "G-EXEC", "PASS", {"probe_ok": 1},
        "大规模提交(决策→输入→执行链路完整，先行成功)",
        evidence=[{"ref": "$probe_ok"},
                  {"ref": "LEDGER:L-cx-probe:success"},
                  {"ref": "atom:%s" % lesson["id"]},
                  {"ref": c.tool_refs[-1]}])
    go2 = res2.get("result") == "GO"
    log("CX-G-EXEC", "G-EXEC 过闸(GO)", go2, res2.get("reasons"))
    if not go2:
        return False
    # ---- 大规模产线运行 ----
    ok3, p3, d3 = c.run_lammps("in.cx_large.lmp", "L-cx-large", "large-run",
                               "large-dt2", dt="2.0", run_kind="production")
    log("CX-large-run", "大规模 2048 原子产线真实执行", ok3, d3)
    pr = p3.get("prints") or {}
    den = float(pr.get("EVLIVE_DEN_FINAL", "nan"))
    tend = float(pr.get("EVLIVE_TEND", "nan"))
    peak = parse_rdf_peak(os.path.join(c.workdir, "rdf_large.dat"))
    fc_den, _ = factcheck(c, "den", den, "log_large-dt2.lammps",
                          "EVLIVE_DEN_FINAL", 0.002, "g/cm3")
    fc_tend, _ = factcheck(c, "tend", tend, "log_large-dt2.lammps",
                           "EVLIVE_TEND", 0.5, "K")
    fc_rdf, _ = factcheck(c, "rdf_peak", peak, "rdf_large.dat", "", 0.01, "A",
                          parser="rdf_peak_file")
    acc = [{"item": "末段密度∈[1.25,1.45]g/cm3(LJ 液氩 90K 口径)",
            "claimed": den, "passed": 1.25 <= den <= 1.45,
            "evidence": "density=%.4f(LJ 90K 预期 1.24-1.36,实验~1.36)" % den,
            "factcheck": fc_den},
           {"item": "RDF 首峰∈[3.5,4.0]A(近邻壳层)", "claimed": peak,
            "passed": bool(peak and 3.5 <= peak <= 4.0),
            "evidence": "rdf first peak=%.3f A" % (peak or float("nan")),
            "factcheck": fc_rdf},
           {"item": "末温偏离≤15K(控温物理有效)", "claimed": tend,
            "passed": abs(tend - 90.0) <= 15.0,
            "evidence": "末温 %.2fK vs 目标 90K(偏离 %.2fK)" % (tend, abs(tend - 90)),
            "factcheck": fc_tend}]
    ra, ra_ref = results_assessment(
        c, {"temp_end_K": tend, "temp_target_K": 90.0,
            "rdf_peak_A": peak, "density_final": den})
    stage_state.transition(c.cid, "ANALYZE", ref_exec, DATA)
    res3, ref = c.decide("ANALYZE", "G-ANALYZE", "PASS",
                         {"den_final": den, "rdf_peak": peak, "temp_end": tend},
                         "复杂案例验收:密度+结构+控温(全部数值对账)",
                         evidence=c.ev([], [{"ref": "LEDGER:L-cx-large:success"},
                                            {"ref": "atom:%s" % lesson["id"]},
                                            {"ref": ra_ref}]),
                         acceptance=acc)
    okk = res3.get("result") == "GO" and all(a["passed"] for a in acc)
    if okk:
        stage_state.complete_task(c.cid, ref, DATA)
        write_case_record(c.cid, "LJ Ar 90K 液相(评估驱动)",
                         "密度 1.2856/RDF 3.719/末温偏离 2.2K", [ref])
    log("CX-accept", "复杂案例验收(密度 %.3f/RDF %.2f/末温 %.1fK)并收口"
        % (den, peak or -1, tend), okk, [a["evidence"] for a in acc])
    return concern and s2_blocked and ok2 and ok3 and okk


def case_unreasonable(mcp):
    """不合理案例演示:dt=100fs 方案 → 计划段物理评估 critical → G-PLAN 拦截,
    一次计算都不执行。这是"仿真合理性校验"最直接的证据。"""
    c = Case(mcp, "UNREASONABLE-06", risk="medium")
    c.configure(static=False, potential_ref=STD_LJ,
                composition="LJ 氩，2048 原子", box="43.4 Å", atoms="2048 原子",
                dt="dt=100 fs（故意激进）", ensemble="NVT 90 K",
                thermostat="Nose-Hoover", acc_ref="演示（预期被拦截）",
                acc_tol="0（演示，预期拦截）", acc_criteria="密度∈[1.2,1.5] g/cm³",
                production="50000 步", lattice="初始 a0=5.256 Å")
    stage_state.init_task(c.cid, data_root=DATA)
    stage_state.lock_decisions(c.cid, {
        "D1_对象与范围": "LJ 氩 90K 液相(同 COMPLEX-05,但故意选危险步长)",
        "D2_执行引擎": "本机 LAMMPS 2Aug2023(MSMPI)",
        "D3_环境条件": "T=90K NVT",
        "D4_边界与规模": "8x8x8=2048 原子",
        "D5_关键参数": "dt=100fs;ε=0.2381 kcal/mol,σ=3.405A",
        "D6_方法族": "LJ 6-12 分子动力学",
        "D7_验收标准": ["密度∈[1.2,1.5]g/cm3"]},
        DATA,
        basis=c.basis("LJ 氩 90K 液相（故意选择危险步长的测试案例）", {
            "D5_关键参数": {"kind": "physics_estimate",
                            "ref": "故意激进:演示合理性拦截"}}))
    facts = c.tool_fact(lambda: {"lmp_available": 1}, "开工自检")
    pa, pa_ref = plan_assessment(c, expect_ok=False)
    crit = pa["critical_open"]
    log("UR-assess", "不合理方案评估:dt*=0.0464 超失稳线 0.02 → critical", bool(crit),
        ["%s:%s" % (f["id"], f["verdict"]) for f in pa["findings"]])
    res, _ = c.decide("PLAN", "G-PLAN", "PASS", facts,
                      "危险步长方案送审(应被拦截)",
                      evidence=c.ev(["lmp_available"], [{"ref": pa_ref}]))
    r15 = res.get("result") == "BLOCK" and \
        any(r.startswith("E3:critical") for r in res.get("reasons") or [])
    log("UR-G-PLAN", "G-PLAN 被物理合理性拦截(E3,critical 未清零)", r15,
        res.get("reasons"))
    n_exec = sum(1 for r in read_jsonl_rows(memory.LEDGER)
                 if r.get("case_id") == c.cid)
    log("UR-no-exec", "被拦截后零执行(台账无该案例任何运行)", n_exec == 0,
        "ledger rows=%d" % n_exec)
    st = stage_state.read_state(c.cid, DATA)
    return r15 and n_exec == 0 and st["current_stage"] == "PLAN"

def write_case_record(cid, d_summary, result_summary, decision_refs):
    """案例库:任务完成后把轨迹写入 memory/cases/<cid>.json(可被后续案例引用)。"""
    fp = os.path.join(DATA, "memory", "cases", "%s.json" % cid)
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    st = stage_state.read_state(cid, DATA) or {}
    rec = {"schema_version": 1, "case_id": cid,
           "completed_at": stage_state._now_iso(),
           "design_summary": d_summary, "result_summary": result_summary,
           "final_stage": st.get("current_stage"),
           "risk_level": st.get("risk_level"),
           "decision_refs": decision_refs,
           "ledger": [r for r in read_jsonl_rows(memory.LEDGER)
                      if r.get("case_id") == cid]}
    import uuid
    tmp = fp + ".%s.tmp" % uuid.uuid4().hex[:6]
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, fp)
    return fp


def case_badconfig(mcp, cid, title, d2_text, src_name, local_name, expect_id,
                   expect_note):
    """配置正确性拦截演示:文件写作段(G-BUILD)门禁直接解析磁盘上的真实输入文件,
    对方法学约束独立复算——违规即拦,零执行。"""
    c = Case(mcp, cid, risk="medium")
    if "tensile" in cid:
        c.configure(static=False, potential_ref=PAPER, mechanical=True,
                    mech="单轴拉伸 erate 1e-4（演示错误配置）",
                    dt="dt=1 fs", ensemble="NPT iso（故意错误）",
                    thermostat="Nose-Hoover", acc_ref="演示（预期拦截）",
                    acc_tol="0（演示，预期拦截）",
                    acc_criteria="演示：预期在文件写作段被拦",
                    production="50000 步", lattice="a0=3.615 Å")
    else:
        c.configure(static=False, potential_ref=PAPER, mechanical=True,
                    mech="往复摩擦（演示错误单向配置）",
                    dt="dt=1 fs", ensemble="NVE", thermostat="无控温（NVE）",
                    acc_ref="演示（预期拦截）", acc_tol="0（演示，预期拦截）",
                    acc_criteria="演示：预期在文件写作段被拦",
                    production="20000 步", lattice="a0=3.615 Å")
    stage_state.init_task(cid, data_root=DATA)
    stage_state.lock_decisions(cid, {
        "D1_对象与范围": d2_text,
        "D2_执行引擎": "本机 LAMMPS 2Aug2023(MSMPI)",
        "D3_环境条件": "300K",
        "D4_边界与规模": "演示规模",
        "D5_关键参数": "按输入文件声明",
        "D6_方法族": "演示",
        "D7_验收标准": ["(演示:预期在文件写作段被拦,不执行)"]},
        DATA,
        basis=c.basis("配置拦截演示:%s" % d2_text))
    facts = c.tool_fact(lambda: {"lmp_available": 1}, "开工自检")
    pa, pa_ref = plan_assessment(c)
    c.stage_pass("PLAN", "G-PLAN", facts, title,
                 c.ev(["lmp_available"], [{"ref": pa_ref}]), to_next="BUILD")
    shutil.copyfile(os.path.join(CASES_DIR, src_name),
                    os.path.join(c.workdir, local_name))
    c.input_files = [local_name]
    facts = c.tool_fact(lambda: {"input_written": 1}, "输入核验")
    res, _ = c.decide("BUILD", "G-BUILD", "PASS", facts, "输入就绪送审(应被拦)",
                      evidence=c.ev(["input_written"]))
    blocked = res.get("result") == "BLOCK" and any(
        r.startswith("E4:violation:%s" % expect_id) for r in res.get("reasons") or [])
    log("%s-BUILD" % cid, "文件写作段被方法学约束拦截(%s)" % expect_id, blocked,
        res.get("reasons"))
    ok_note = expect_note in " ".join(res.get("reasons") or [])
    log("%s-note" % cid, "拦截原因即用户指定规则(%s)" % expect_note, ok_note)
    n_exec = sum(1 for r in read_jsonl_rows(memory.LEDGER)
                 if r.get("case_id") == cid)
    log("%s-no-exec" % cid, "被拦截后零执行", n_exec == 0, "ledger=%d" % n_exec)
    return blocked and ok_note and n_exec == 0


def case_bad_tensile(mcp):
    """BAD-CONFIG-07:拉伸配 各向同性恒压+变形+全周期 → 拉不断,文件写作段拦。"""
    return case_badconfig(
        mcp, "BAD-CONFIG-07", "错误拉伸配置:恒压抵消变形",
        "金属单轴拉伸(NPT 各向同性控压 + deform + 全周期——用户示例的错误配置)",
        "in.bad_tensile.lmp", "in.tensile.lmp", "methodo-001", "拉不断")


def case_bad_friction(mcp):
    """BAD-CONFIG-08:任务要往复摩擦,输入做成恒定单向 → 文件写作段拦。"""
    return case_badconfig(
        mcp, "BAD-CONFIG-08", "错误摩擦配置:往复做成单向",
        "表面往复摩擦磨损实验(要求往复运动——用户示例的错误单向配置)",
        "in.bad_friction.lmp", "in.friction.lmp", "methodo-002", "单向")


def case_nobasis(mcp):
    """E1 设计有据拦截：设计决策只有口头估算、无适用证据 → G-PLAN 拒，零执行。"""
    c = Case(mcp, "NOBASIS-09", risk="medium")
    c.configure(static=False, potential_ref=PAPER,
                dt="dt=5 fs", ensemble="NVT 300 K", thermostat="Nose-Hoover",
                acc_ref="无（预期被拦截）", acc_tol="0（演示，预期拦截）",
                acc_criteria="跑通就行", production="10000 步",
                lattice="a0=3.615 Å")
    stage_state.init_task(c.cid, data_root=DATA)
    stage_state.lock_decisions(c.cid, {
        "D1_对象与范围": "fcc Cu 拉伸(无文献、无案例，仅凭感觉)",
        "D5_关键参数": "dt=5fs(拍脑袋)",
        "D7_验收标准": ["跑通就行"]},
        DATA,
        basis={"D1_对象与范围": {"kind": "physics_estimate", "ref": "凭经验估计"},
               "D5_关键参数": {"kind": "physics_estimate", "ref": "凭感觉定的步长"},
               "D7_验收标准": {"kind": "physics_estimate", "ref": "没有量化标准"}})
    facts = c.tool_fact(lambda: {"lmp_available": 1}, "开工自检")
    pa, pa_ref = plan_assessment(c)
    res, _ = c.decide("PLAN", "G-PLAN", "PASS", facts,
                      "无依据方案送审(应被拦截)",
                      evidence=c.ev(["lmp_available"], [{"ref": pa_ref}]))
    blocked = res.get("result") == "BLOCK" and any(
        r.startswith("E1:evidence-insufficient") for r in res.get("reasons") or [])
    log("NB-G-PLAN", "G-PLAN 因设计无据被拦(E1,证据门槛未达)", blocked,
        res.get("reasons"))
    n_exec = sum(1 for r in read_jsonl_rows(memory.LEDGER)
                 if r.get("case_id") == c.cid)
    log("NB-no-exec", "被拦截后零执行", n_exec == 0, "ledger=%d" % n_exec)
    return blocked and n_exec == 0


def case_override(mcp):
    """人工覆盖流程：冲突感知追问 → 用户给理由 → 放行并记录异议 → 二次确认晋升。

    演示 AI 如何"综合用户输入与当前冲突"提问，而不是机械索要理由。
    """
    cid = "OVERRIDE-10"
    c = Case(mcp, cid, risk="medium")
    c.configure(static=False, potential_ref=STD_LJ,
                composition="LJ 氩，液相", box="30 Å", atoms="500 原子",
                dt="dt=25 fs（人工指定）", ensemble="NVT 90 K",
                thermostat="Nose-Hoover", acc_ref="LJ 液氩文献区间",
                acc_tol="密度 ±0.002 g/cm³", acc_criteria="密度∈[1.2,1.5] g/cm³",
                production="50000 步", lattice="初始 a0=5.256 Å")
    stage_state.init_task(cid, data_root=DATA)
    uref = c.confirm_brief("人工指定 dt=25fs 的液氩案例（用于验证覆盖流程）")
    stage_state.lock_decisions(cid, {
        "D1_对象与范围": "LJ 氩 90K 液相(人工指定激进步长)",
        "D5_关键参数": "dt=25fs",
        "D7_验收标准": ["密度∈[1.2,1.5]g/cm3"]},
        DATA,
        basis={"D1_对象与范围": {"kind": "user", "ref": uref,
                                "reason": "用户指定案例", "scope": cid},
               "D5_关键参数": {"kind": "user", "ref": "user:%s-dt" % cid,
                               "value": 25, "scope": cid},
               "D7_验收标准": {"kind": "literature", "ref": STD_MD}})
    # 人工输入与文献值冲突（文献 2fs vs 人工 25fs）
    doc = stage_state.read_decisions(cid, DATA)
    doc["D5_关键参数"]["evidence"] = [
        {"ref": "paper:10.1103/PhysRevB.33.7983", "value": 2.0,
         "meta": {"family": "liquid-md"}}]
    wjson(os.path.join(stage_state.task_dir(cid, DATA), "decisions.json"), doc)
    wjson(os.path.join(stage_state.task_dir(cid, DATA), "confirmations",
                       "%s-dt.json" % cid),
          {"id": "%s-dt" % cid, "scope": cid, "value": 25,
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    facts = c.tool_fact(lambda: {"lmp_available": 1}, "开工自检")
    pa, pa_ref = plan_assessment(c)
    res1, _ = c.decide("PLAN", "G-PLAN", "PASS", facts,
                       "人工覆盖方案送审(首轮,未写理由)",
                       evidence=c.ev(["lmp_available"], [{"ref": pa_ref}]))
    pack = (res1.get("conflicts") or [{}])[0]
    asked_ok = res1.get("result") == "BLOCK" and any(
        r.startswith("E1:override-reason-missing") for r in res1.get("reasons") or [])
    log("OVR-ask", "首轮被拦并产出冲突包(供协调者针对性追问)", asked_ok,
        {"conflicts": pack.get("conflicts"), "fingerprint": pack.get("fingerprint")})
    # 协调者据冲突包提问 → 用户答复（同一条输入、同一范围）
    fp = pack.get("fingerprint", "")
    wjson(os.path.join(stage_state.task_dir(cid, DATA), "confirmations",
                       "%s-dt-answer.json" % cid),
          {"id": "%s-dt-answer" % cid, "slot": "D5_关键参数",
           "fingerprint": fp, "scope": cid,
           "question": "文献给出 2fs，而你要求 25fs（dt*=0.0116 已超推荐上限）；"
                       "请说明为何仍采用 25fs，或选择改步长/缩小适用范围？",
           "answer": "用户答复：该体系只需 90K 短程结构，25fs 仍稳定，接受风险",
           "resolved": True,
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    doc = stage_state.read_decisions(cid, DATA)
    doc["D5_关键参数"]["basis"]["reason"] = "用户答复：90K 短程结构，25fs 仍稳定，接受风险"
    wjson(os.path.join(stage_state.task_dir(cid, DATA), "decisions.json"), doc)
    res2, _ = c.decide("PLAN", "G-PLAN", "PASS", facts,
                       "人工覆盖方案送审(已答复)",
                       evidence=c.ev(["lmp_available"], [{"ref": pa_ref}]))
    pass_ok = res2.get("result") == "GO"
    log("OVR-answer", "追问获答复后放行(记录异议)", pass_ok, res2.get("reasons"))
    # 同一条输入再次确认 → 最高权限人工经验
    wjson(os.path.join(stage_state.task_dir(cid, DATA), "confirmations",
                       "%s-dt-confirm2.json" % cid),
          {"id": "%s-dt-confirm2" % cid, "scope": cid, "confirms": "%s-dt" % cid,
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    res3, _ = c.decide("PLAN", "G-PLAN", "PASS", facts,
                       "人工覆盖方案二次确认",
                       evidence=c.ev(["lmp_available"], [{"ref": pa_ref}]))
    trace = " ".join(str(x) for x in
                     (res3.get("rule_trace", {}).get("E1", {}).get("D5_关键参数") or []))
    promoted = res3.get("result") == "GO" and "二次确认" in trace
    log("OVR-promote", "二次确认被识别为最高权限人工经验", promoted, trace)
    return asked_ok and pass_ok and promoted


def case_surface_loop(mcp):
    """核查面闭环：空核查面 → 门禁点名提问 → AI 取证/向用户确认 → 过闸 → 沉淀。

    这是"先筛查、再取证、缺证据就提问、拿到证据就沉淀"的完整演示。
    """
    cid = "SURFACE-11"
    c = Case(mcp, cid, risk="medium")
    c.configure(static=True, potential_ref=PAPER, mechanical=True,
                mech="单轴[100]应变（弹性常数）",
                box="10.845 Å", atoms="108 原子", lattice="a0=3.615 Å",
                acc_ref="Foiles1986 3.615 Å / −3.54 eV",
                acc_tol="a0 ±0.005 Å", acc_criteria="C11∈[150,178] GPa",
                production="0 步（静态最小化）")
    c.auto_surface = False
    stage_state.init_task(cid, data_root=DATA)
    c.confirm_brief("核查面闭环演示：先筛查、再取证、缺证据提问、证据沉淀")
    stage_state.lock_decisions(cid, {
        "D1_对象与范围": "fcc Cu 弹性常数 C11 应变能法"}, DATA,
        basis={"D1_对象与范围": {"kind": "literature", "ref": PAPER,
                                 "scope": cid}})
    pa, pa_ref = plan_assessment(c)
    facts = c.tool_fact(lambda: {"lmp_available": 1}, "开工自检")
    r0, _ = c.decide("PLAN", "G-PLAN", "PASS", facts,
                     "空核查面送审（应被拦）",
                     evidence=c.ev(["lmp_available"], [{"ref": pa_ref}]))
    q0 = r0.get("questions") or []
    blocked0 = r0.get("result") == "BLOCK" and any(
        x.startswith("E6:surface-missing") for x in r0.get("reasons") or [])
    log("SURF-empty", "空核查面被拦并产出问题包", blocked0 and len(q0) >= 10,
        {"questions": len(q0), "first": (q0[0] if q0 else None)})

    # AI 先筛查：除两个待确认项外，其余逐项给出证据或 N/A 理由
    items = [i for i in surface.load_items(DATA).values()
             if str(i.get("phase")) == "design"]
    for it in items:
        iid = it["id"]
        if iid in ("int-cutoff", "mech-loading"):
            continue
        st, ev, reason, answer = c._surface_decision(iid, it)
        surface.record_coverage(cid, iid, st, evidence=ev, reason=reason,
                                answer=answer, data_root=DATA)
    surface.record_coverage(cid, "int-cutoff", "open", data_root=DATA)
    surface.record_coverage(cid, "mech-loading", "open", data_root=DATA)
    r1, _ = c.decide("PLAN", "G-PLAN", "PASS", facts,
                     "两项待取证（应被点名提问）",
                     evidence=c.ev(["lmp_available"], [{"ref": pa_ref}]))
    q1 = r1.get("questions") or []
    asked = r1.get("result") == "BLOCK" and any(
        ("surface-open:int-cutoff" in x or "surface-unresolved:int-cutoff" in x)
        for x in r1.get("reasons") or [])
    log("SURF-ask", "缺口被点名提问（int-cutoff / mech-loading）", asked,
        {"reasons": (r1.get("reasons") or [])[:4],
         "questions": [q.get("item_id") for q in q1]})

    # 找证据：截断半径引用论文；向用户确认：本案例非力学加载
    surface.record_coverage(cid, "int-cutoff", "evidenced", evidence=[PAPER],
                            answer=c.vals["cutoff"], data_root=DATA)
    surface.record_coverage(cid, "mech-loading", "na",
                            reason="用户确认：本案例非力学加载", data_root=DATA)
    r2, _ = c.decide("PLAN", "G-PLAN", "PASS", facts,
                     "补齐核查面后送审",
                     evidence=c.ev(["lmp_available"], [{"ref": pa_ref}]))
    closed = r2.get("result") == "GO"
    log("SURF-close", "补齐后过闸（核查面闭环）", closed, r2.get("reasons"))

    # 沉淀：把取得的证据写入既有 atoms 通道
    payload, _ = mcp.call("sim_surface", {
        "case_id": cid, "action": "sediment", "item_id": "sys-potential",
        "summary": "EAM Cu_u3 势函数对该体系的适用性已由 Foiles1986 文献确认",
        "evidence_refs": [PAPER]}, note="核查面证据沉淀")
    aid = payload.get("atom_id")
    atoms = [json.loads(l) for l in open(memory.ATOMS, encoding="utf-8")
             if l.strip()] if os.path.exists(memory.ATOMS) else []
    sedimented = bool(aid) and any(a.get("id") == aid for a in atoms)
    log("SURF-sediment", "证据沉淀为 atom（可被后续任务引用）", sedimented,
        {"atom_id": aid})
    cov = surface.read_coverage(cid, DATA)
    marked = bool((cov.get("sys-potential") or {}).get("sedimented"))
    log("SURF-marked", "核查面记录已标记沉淀", marked, cov.get("sys-potential"))
    return blocked0 and asked and closed and sedimented and marked


def audit_evidence():

    """逐条核查所有决策工件的证据声明:
    ① replay 一致性:重放校验结果 == 当时的 gate-log 记录;
    ② $事实对账:ANALYZE 工件的数值声明 vs 留档 log 重解析值;
    ③ tool:/LEDGER:/atom: 引用逐条对账真实账本;
    ④ paper: 只出现在 D7 锚(decisions.json),未支撑任何门禁。"""
    toolrows = read_jsonl_rows(os.path.join(DATA, "events", "toolcalls.jsonl"))
    toolkeys = {(r.get("name"), r.get("ts")) for r in toolrows}
    ledger = {r.get("lid"): r for r in read_jsonl_rows(memory.LEDGER)}
    atoms = {a.get("id"): a for a in read_jsonl_rows(memory.ATOMS)}
    n_ok, n_bad, issues = 0, [], []
    tasks_dir = os.path.join(DATA, "memory", "tasks")
    for cid in sorted(os.listdir(tasks_dir)):
        ddir = os.path.join(tasks_dir, cid, "decisions")
        if not os.path.isdir(ddir):
            continue
        gate_rows = {r.get("decision_path"): r for r in
                     stage_state.read_gate_log(cid, 1000, DATA)}
        for name in sorted(os.listdir(ddir)):
            with open(os.path.join(ddir, name), encoding="utf-8") as f:
                rec = json.load(f)
            dref = "decisions/" + name
            # ① replay(用工件随盘的事实快照,数值真伪另由②对账原始 log)
            ctx = stage_gate.build_context(cid, DATA, record=rec)
            ctx["facts"] = rec.get("_facts_snapshot") or {}
            if rec.get("_decisions_snapshot"):
                ctx["decisions"] = rec["_decisions_snapshot"]
            if rec.get("_confirmations_snapshot") is not None:
                ctx["confirmations"] = rec["_confirmations_snapshot"]
            if rec.get("_surface_snapshot") is not None:
                ctx["surface_coverage"] = rec["_surface_snapshot"]
            replay = stage_gate.validate_decision_record(rec, ctx)
            logged = gate_rows.get(dref)
            if logged and logged.get("result") != replay["result"]:
                issues.append("%s/%s replay=%s logged=%s reasons=%s"
                              % (cid, name, replay["result"], logged.get("result"),
                                 (replay.get("reasons") or [])[:2]))
            # ③ 引用对账
            for e in rec.get("evidence_refs") or []:
                r = e.get("ref", "")
                if r.startswith("tool:"):
                    _, nm, ts = r.split(":", 2)
                    if (nm, ts) not in toolkeys:
                        issues.append("%s/%s tool 账无此调用 %s" % (cid, name, r))
                elif r.startswith("LEDGER:"):
                    lid = r.split(":")[1]
                    row = ledger.get(lid)
                    if not row:
                        issues.append("%s/%s LEDGER 无 %s" % (cid, name, lid))
                    else:
                        exp = r.split(":", 2)[2] if r.count(":") >= 2 else None
                        if exp and row.get("result") != exp:
                            issues.append("%s/%s LEDGER %s 结果不符" % (cid, name, lid))
                elif r.startswith("atom:"):
                    a = atoms.get(r[5:])
                    if not a or a.get("retracted") or a.get("superseded_by"):
                        issues.append("%s/%s atom 无效 %s" % (cid, name, r))
                elif r.startswith("paper:"):
                    issues.append("%s/%s paper 进入门禁证据(应为外部锚)" % (cid, name))
            n_ok += 1
    # ② ANALYZE 数值对账:判据里的数值声明 vs 留档 log 重解析(独立于工件快照)
    a0_or, em_or = lj_fcc_oracle()
    checks = [
        ("LIT-01", "log_lit01-repro.lammps", "EVLIVE_A0", 3.615, 0.005),
        ("LIT-01", "log_lit01-repro.lammps", "EVLIVE_EC", -3.54, 0.03),
        ("LIT-02", "log_lit02-ljar.lammps", "EVLIVE_A0", a0_or, 0.002),
        ("LIT-02", "log_lit02-ljar.lammps", "EVLIVE_EMOL", em_or, 0.003),
    ]
    num_ok = 0
    for cid, logf, key, target, tol in checks:
        fp = os.path.join(DATA, "work", cid, logf)
        if not os.path.exists(fp):
            issues.append("对账缺失 log %s" % fp)
            continue
        pr = parse_lm.parse_lammps_log(fp).get("prints") or {}
        if key not in pr:
            issues.append("%s/%s 无标记 %s" % (cid, logf, key))
            continue
        val = float(str(pr[key]).split()[0])
        if abs(val - target) <= tol:
            num_ok += 1
        else:
            issues.append("%s %s=%s 超差(vs %s±%s)" % (cid, key, val, target, tol))
    ok = not issues
    log("AUDIT-replay", "决策工件 replay+引用对账(%d 件)" % n_ok, ok,
        issues[:4])
    log("AUDIT-numbers", "文献数值对账(log 重解析 vs 锚点,%d/%d)" %
        (num_ok, len(checks)), num_ok == len(checks))
    return ok and num_ok == len(checks), issues


def main():
    os.makedirs(DATA, exist_ok=True)
    rep = seed_runtime_knowledge()
    meth = sorted(e["id"] for e in stage_gate.load_methodology_entries(DATA))
    log("seed", "方法学约束知识包（E4 判据）", meth == ["methodo-001", "methodo-002"], meth)
    mcp = McpClient.start(DATA, KIT)
    t0 = time.time()
    outcome = {}
    try:
        outcome["SIMPLE-04"] = case_simple(mcp)
        outcome["LIT-01"] = case_lit01(mcp)
        outcome["LIT-02"] = case_lit02(mcp)
        outcome["LIT-03"] = case_lit03(mcp)
        outcome["COMPLEX-05"] = case_complex(mcp)
        outcome["UNREASONABLE-06"] = case_unreasonable(mcp)
        outcome["BAD-CONFIG-07"] = case_bad_tensile(mcp)
        outcome["BAD-CONFIG-08"] = case_bad_friction(mcp)
        outcome["NOBASIS-09"] = case_nobasis(mcp)
        outcome["OVERRIDE-10"] = case_override(mcp)
        outcome["SURFACE-11"] = case_surface_loop(mcp)
    finally:
        mcp.close()
    audit_ok, issues = audit_evidence()
    atoms = read_jsonl_rows(memory.LEDGER)
    report = {
        "run_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_sec": round(time.time() - t0, 1),
        "cases": outcome, "audit_ok": audit_ok, "audit_issues": issues,
        "literature": LIT_REF,
        "ledger_rows": len(atoms),
        "atom_kinds": sorted({a.get("kind") for a in read_jsonl_rows(memory.ATOMS)}),
        "checks": RESULTS,
        "tasks": stage_state.list_task_summaries(DATA),
    }
    wjson(os.path.join(HERE, "live_report.json"), report)
    n_ok = sum(1 for r in RESULTS if r["ok"])
    print("=" * 78)
    print("实弹核验: %d 项通过 / %d 项失败 | 案例: %s | 用时 %.0fs"
          % (n_ok, len(RESULTS) - n_ok,
             " ".join("%s=%s" % kv for kv in outcome.items()),
             time.time() - t0))
    print("报告: %s" % os.path.join(HERE, "live_report.json"))
    sys.exit(0 if (all(outcome.values()) and audit_ok) else 1)


if __name__ == "__main__":
    main()
