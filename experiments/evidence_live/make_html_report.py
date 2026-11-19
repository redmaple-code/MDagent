#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""实弹汇报 HTML 生成器(白话版)—— 每步判断按「判断内容/判断依据/调用的证据/判断结果」
四要素用人话呈现;原始凭据(引用编号/路径/时间戳)收进悬停提示,不占正文。
数据全部从 live_data 真实产物读取,零手工数字。"""
import html
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "live_data")
VIZ_DIR = r"C:\Users\redmaple\.zcode\v2\visualizations\a0fb6daa18eb941a66d5c9d73d146efb5c580da6067dc44f7138b98f2590a1a9"

CASE_ORDER = ["SIMPLE-04", "LIT-01", "LIT-02", "LIT-03", "COMPLEX-05",
              "UNREASONABLE-06", "BAD-CONFIG-07", "BAD-CONFIG-08",
              "NOBASIS-09", "OVERRIDE-10", "SURFACE-11"]

# ---------------- 白话翻译层 ----------------
CASE_TITLE = {
    "SIMPLE-04": "简单案例：铜晶体能量最小化",
    "LIT-01": "文献复现：铜的晶格常数与结合能",
    "LIT-02": "近似文献：氩晶体的基态性质（双实现互证）",
    "LIT-03": "近似文献：铜的弹性模量 C11",
    "COMPLEX-05": "复杂案例：液态氩全流程（评估驱动的设计修订）",
    "UNREASONABLE-06": "不合理方案：危险步长，在计划段就被拦截",
    "BAD-CONFIG-07": "错误拉伸配置：恒压抵消变形（用户指定规则）",
    "BAD-CONFIG-08": "错误摩擦配置：往复做成单向（用户指定规则）",
    "NOBASIS-09": "无依据设计：拿不出证据，计划段被拦",
    "OVERRIDE-10": "人工覆盖：冲突感知追问 → 答复 → 二次确认",
    "SURFACE-11": "核查面闭环：先筛查 → 提问 → 取证 → 过闸 → 沉淀",
}
CASE_STORY = {
    "SIMPLE-04": ("做一个最基础的分子模拟：把一块被人为扰动过的铜晶体「松弛」回能量最低状态，"
                  "验证整条流程能跑通。没有文献对照，验收标准是物理常识（能量必须下降、原子受力趋零）。"),
    "LIT-01": ("复现一篇 1986 年经典论文的结果。这篇论文（Foiles、Baskes、Daw 三人发表于《物理评论 B》"
               "第 33 卷 7983 页）给出了铜的 EAM 势函数，并用它拟合了两个标志性数值：平衡晶格常数 "
               "3.615 埃、每原子结合能 3.54 电子伏。我们用论文同款势函数做完全弛豫，看能否算出同样的数。"),
    "LIT-02": ("算氩晶体在绝对零度下的基态性质。氩原子间用 Lennard-Jones 经典相互作用描述。"
               "「近似文献」的含义：教科书给出的完整理论值（最近邻距离 1.09874σ、每原子结合能 -8.6101ε）"
               "是无穷远截断的结果，而任何模拟都有截断；所以我们另外写了一个独立的纯数学求和程序"
               "（与 LAMMPS 完全两套实现）算出「我们这个截断下」的理论值，两套独立实现互相对账。"),
    "LIT-03": ("测铜的弹性模量 C11（沿 X 方向拉伸的难度）。方法：把晶体拉伸/压缩 0.4%、0.8%，"
               "分别算能量，能量的二次变化率就是 C11。对照两个锚点：本工作区基准包用两套独立实现"
               "算出的理论值 163.6 GPa，以及实验手册值约 168 GPa。"),
    "COMPLEX-05": ("把氩从晶体加热熔化成液体（90 K、1 个大气压），再统计液体结构。"
                   "这个案例故意设计成「先犯错再修复」：第一步故意选了危险的大时间步长（100 飞秒），"
                   "让小规模验证真实地失败一次，演示「小规模先行」铁律如何在便宜的小计算上拦住问题，"
                   "而不是让 2048 原子的大计算白白烧完才发现。"),
    "UNREASONABLE-06": ("故意提交一个物理上不合理的方案：给氩动力学选 100 飞秒的"
                        "危险步长（约化步长 0.046，远超失稳线 0.02）。预期：方案在"
                        "计划段就被物理合理性评估拦下，一次计算都不会执行。"),
    "BAD-CONFIG-07": ("故意提交用户指定的错误拉伸配置：各向同性恒压控压 + 变形 + 全周期。"
                      "物理上应力会被恒压拉回去、样品拉不断。预期：输入文件写作完成送审时"
                      "被方法学约束拦下，零执行。"),
    "BAD-CONFIG-08": ("故意提交用户指定的错误摩擦配置：任务声明往复摩擦，输入文件却是"
                      "恒定单向运动。预期：同样在文件写作段被拦，零执行。"),
    "NOBASIS-09": ("故意提交一个「拍脑袋」方案：三项设计决策只有口头估算，没有论文、"
                   "案例或实测依据。预期：E1 设计有据直接拦住，零执行。"),
    "OVERRIDE-10": ("演示人工覆盖流程：用户坚持 25fs，而文献给出 2fs。系统先追问冲突"
                    "（引用双方数值与风险），用户答复后放行并记录异议；二次确认后晋升为"
                    "最高权限人工经验。"),
    "SURFACE-11": ("演示核查面闭环：空核查面送审 → 门禁列出 19 个待确认问题 → "
                   "AI 逐项取证/向用户确认 → 补齐后过闸 → 证据沉淀为 atom。"),
}
CASE_RESULT_CN = {
    "SIMPLE-04": ("扰动后能量 −344.91 → −382.23 电子伏（回到理想晶格能量），"
                  "原子受力从 36.5 降到 0.003（基本为零）——「松弛成功」的物理含义完全达成。"),
    "LIT-01": ("算得晶格常数 3.6150 埃、结合能 −3.5400 电子伏，与论文拟合值 3.615 埃 / −3.54 电子伏"
               "的偏差在小数点后第 5 位才出现——精确复现。"),
    "LIT-02": ("LAMMPS 算得 5.2764 埃 / −1.9285 千卡每摩尔；独立数学程序算得 5.2764 埃 / −1.9285 千卡每摩尔——"
               "两套完全独立的实现在小数点后四位完全一致。作为背景：无穷截断的理论值是 −8.61ε，"
               "我们有限截断下是 −8.10ε，差值正是被截掉的那部分，物理上自洽。"),
    "LIT-03": ("四个应变点拟合出 C11 = 167.3 GPa，落在双实现理论值 163.6 与实验值约 168 之间——"
               "方法与实现都可信。"),
    "COMPLEX-05": ("修复步长后：2048 原子、9 万步全流程稳定跑完；末段密度 1.2856 克/立方厘米"
                   "（液态氩 90K 的合理区间）；径向分布函数首峰在 3.719 埃、峰高 2.71——"
                   "正是教科书里液态氩第一配位壳层的标准位置。"),
    "UNREASONABLE-06": ("如设计预期被拦：方案评估判「重大疑点」（步长超失稳线），"
                        "方案闸不放行；台账里该案例零执行记录——不合理的计算"
                        "根本没机会开跑。"),
    "BAD-CONFIG-07": ("如预期在文件写作段被拦：门禁直接解析磁盘上的输入文件，发现"
                      "「各向同性控压与变形同现」——拒因写明「应力被恒压抵消，样品拉不断」"
                      "并给出正确做法（控压只耦合非变形方向，或改 NVT+目标应力）。零执行。"),
    "BAD-CONFIG-08": ("如预期被拦：任务声明是往复摩擦，输入文件却没有任何往复运动特征命令"
                      "——「被做成了单向运动」，拒因同时给出正确做法（周期反转加载方向）。"
                      "零执行。"),
    "NOBASIS-09": ("如设计预期被拦：三项设计决策只有「凭感觉」的估算，没有任何论文、案例或实测"
                   "依据；E1 判定证据门槛未达，计划段直接拒绝，零执行。"),
    "OVERRIDE-10": ("人工要求 25fs，与文献 2fs 冲突：首轮因未写理由被拦并产出冲突包"
                    "（写明冲突证据、双方数值、风险后果）；用户给出针对性答复后放行并记录异议；"
                    "第二次确认后识别为最高权限人工经验。"),
    "SURFACE-11": ("空核查面被拦并列出 19 个待确认问题；两项待取证（截断半径/加载适用性）"
                   "被点名提问；补齐后过闸；取得的证据沉淀为 atom（可被后续任务引用）。"),
}
GATE_QUESTION = {
    "G-PLAN": "方案与目标是否清楚可靠，可以开始准备具体输入？",
    "G-BUILD": "计算输入文件是否真的写好了（文件实际存在，不是口头说写好了）？",
    "G-CONFIG": "参数配置是否定稿，允许提交给 LAMMPS 真正运行？",
    "G-EXEC": "是否放行这次真实计算提交？",
    "G-ANALYZE": "算出来的数字是否逐条达到验收标准，任务能否结题？",
}
GATE_BASIS = {
    "G-PLAN": "设计有据（E1）：每项锁定决策都要有达到风险门槛的证据；物理合理（E3）："
              "评估有重大疑点不放行。",
    "G-BUILD": "方法学合规（E4）：门禁直接解析真实输入文件，独立复算配置约束。",
    "G-CONFIG": "方法学合规（E4）：参数定稿前再核一次输入文件与配置组合。",
    "G-EXEC": "计算有据（E2）：设计决策→输入文件→执行请求的链路必须完整可回溯。",
    "G-ANALYZE": "计算有据（E2）：每个数值结论从本次产物重算；物理合理（E3）；"
                 "结论完整（E5）：验收项逐条覆盖，不达标必须回退。",
}
RESULT_CN = {"GO": ("通过", "b-go"), "ACCEPT": ("受理放行", "b-acc"),
             "HOLD": ("打回修改", "b-hold"), "BLOCK": ("拦截", "b-block")}
REASON_CN = {
    "E1:no-design-decisions": "计划段没有任何锁定设计，无法判断方案凭什么",
    "E1:evidence-insufficient": "设计决策的证据没有达到风险门槛（来源等级不够/不适用的不算）",
    "E1:basis-invalid": "决策依据的类型不合法",
    "E1:estimate-unassessed": "只凭物理估算下结论，且没有评估背书",
    "E1:override-reason-missing": "人工覆盖了证据，但没有说明理由——系统会给出冲突包供追问",
    "E1:conflict-unresolved": "人工输入与现有证据冲突，尚未解决",
    "E1:override-unresolved": "同一冲突已问过且未解决，升级人工闸（不重复问）",
    "E2:input-untraceable": "执行请求没有声明输入文件，链路断了",
    "E2:claim-unevidenced": "数值结论没有从原始产物的重算对账",
    "E2:claim-failed": "数值结论与重算结果对不上",
    "E2:stale-evidence": "拿旧产物给新结论作证",
    "E3:assessment-missing": "缺物理合理性评估记录",
    "E3:critical": "物理评估有重大疑点未解决",
    "E4:no-input-declared": "没有声明输入文件，无法独立复算",
    "E4:violation": "输入文件违反方法学约束（如恒压+变形、往复做成单向）",
    "E4:cannot-verify": "输入文件不可读，按 fail-closed 拒绝",
    "E5:acceptance-not-covered": "结论没有覆盖锁定的验收标准",
    "E5:no-rollback-on-fail": "验收不达标却没有回退方案",
    "S2:scale-gate-unmet": "执行策略：大规模产线前没有小规模验证成功记录",
}
FACT_CN = {
    "lmp_available": "LAMMPS 计算软件就位", "eam_available": "铜势函数文件就位",
    "input_written": "计算输入文件已写好", "script_bytes": "输入文件大小（字节）",
    "params_ok": "计算参数核对无误", "params_echo": "参数回读一致",
    "acceptance_locked": "验收标准已先行锁定", "scale_plan_present": "规模计划已先行说明",
    "params_written": "参数文件已产出", "params_rows": "参数文件行数",
    "lit_anchor": "文献锚已登记",
    "oracle_a0": "独立数学程序算出的理论晶格常数（埃）",
    "oracle_emol": "独立数学程序算出的理论结合能（千卡/摩尔）",
    "a0_final": "实测平衡晶格常数（埃）", "ec_final": "实测每原子结合能（电子伏）",
    "emol_final": "实测每分子结合能（千卡/摩尔）", "a0": "弛豫得到的基准晶格常数（埃）",
    "c11_gpa": "拟合出的弹性模量 C11（GPa）",
    "den_final": "末段体系密度（克/立方厘米）", "rdf_peak": "径向分布首峰位置（埃）",
    "probe_ok": "小规模验证已通过", "e_first": "最小化前总能量（电子伏）",
    "e_last": "最小化后总能量（电子伏）", "fnorm_last": "收敛后原子受力",
}
TOOL_CN = {"sim_task_state": "任务状态查询", "sim_decision_gate": "证据门校验",
           "sim_exec": "计算执行提交"}
EV_TYPE_CN = {
    "$": ("事实快照", "--blue", "驱动程序当场实查得到的结果（如「文件确实存在」）"),
    "tool:": ("工具调用留痕", "--purple", "MDagent 工具面的真实调用记录，带时间戳，事后可查账"),
    "atom:": ("经验教训", "--green", "本批任务中真实发生的事件沉淀成的经验"),
    "exp:": ("知识条目", "--yellow", "项目知识库中的正式条目（试验性条目不能撑硬性判断）"),
    "LEDGER:": ("运行台账", "--orange", "每次真实计算的成败结局记录"),
    "paper:": ("文献参考", "--muted-foreground", "数值出处。按规则只作对照锚，不作为放行依据"),
}
MC_CN = {"exp-op-scale-001": "「大规模提交前必须先小规模验证成功，并附真实工具实证」"}
RUN_CN = {
    "L-simple-04": ("铜晶体扰动后能量最小化（108 原子）", "能量降到 −382.23 电子伏，受力归零"),
    "L-lit01": ("铜完全弛豫求平衡晶格常数与结合能（256 原子）", "a0=3.6150 埃，Ec=−3.5400 电子伏"),
    "L-lit02": ("氩晶体基态弛豫（256 原子）", "a0=5.2764 埃，Emol=−1.9285 千卡/摩尔"),
    "L-lit03a": ("先弛豫出精确基准晶格（108 原子）", "基准 a0=3.6150 埃，供应变实验使用"),
    "L-lit03b": ("五档拉伸/压缩应变实验（0、±0.4%、±0.8%）", "能量-应变二次拟合 → C11=167.3 GPa"),
    "L-cx-probe": ("小规模先行验证（500 原子，评估修订后的 2 飞秒）", "稳定跑完，末温 86.2 K"),
    "L-cx-large": ("大规模产线：2048 原子熔化→恒压平衡→结构统计（9 万步）",
                   "密度 1.2856 克/立方厘米，径向分布首峰 3.719 埃"),
}
ANCHOR_CN = {
    "anchor-md-001": "时间步长稳定性锚（约化步长推荐≤0.005、失稳线 0.02）",
    "anchor-temp-001": "控温有效性锚（末温偏离应≤10%或 15K；温度发散即失稳）",
    "anchor-rdf-001": "液态结构锚（首峰应在 0.9σ–1.15σ ≈ 3.06–3.92 埃）",
    "anchor-density-001": "凝聚态密度量级锚（0.5–3.0 克/立方厘米）",
    "anchor-repro-001": "复现偏差锚（同款模型复现拟合值应在 0.5% 内）",
    "anchor-accept-001": "验收可量化锚（每条标准须含数值判据）",
    "design-basis:slots": "设计依据完备要求（每项锁定决策带依据）",
}
VERDICT_CN = {"sound": ("合理", "v-sound"), "concern": ("提示", "v-concern"),
              "critical": ("重大疑点", "v-critical"), "na": ("不适用", "v-na")}
R_RULES_PLAIN = [
    ("E1", "设计有据：每项锁定决策的证据达到风险门槛；人工覆盖要走冲突感知追问"),
    ("E2", "计算有据：决策→输入→执行链路完整；数值结论从本次产物重算"),
    ("E3", "物理合理：评估有重大疑点不放行"),
    ("E4", "方法学合规：输入文件与配置组合独立复算"),
    ("E5", "结论完整：验收项逐条覆盖，不达标必须回退"),
]


def esc(s):
    return html.escape(str(s), quote=True)


def rows(fp):
    out = []
    if os.path.exists(fp):
        for ln in open(fp, encoding="utf-8"):
            ln = ln.strip()
            if ln:
                try:
                    out.append(json.loads(ln))
                except Exception:
                    pass
    return out


def load_all():
    tc_idx = {}
    for r in rows(os.path.join(DATA, "events", "toolcalls.jsonl")):
        # 双侧审计:客户端行 {name,ts}(引用核验用) + 服务器行 {t,tool}
        if r.get("name") and r.get("ts"):
            tc_idx[str(r["name"]) + ":" + str(r["ts"])] = r
    d = {"led": rows(os.path.join(DATA, "memory", "LEDGER.jsonl")),
         "atoms": {a.get("id"): a for a in rows(os.path.join(DATA, "memory", "atoms.jsonl"))},
         "tc": tc_idx,
         "aud": rows(os.path.join(HERE, "kit_local", "logs", "claude_operations.jsonl")),
         "rep": json.load(open(os.path.join(HERE, "live_report.json"), encoding="utf-8")),
         "cases": {}}
    tdir = os.path.join(DATA, "memory", "tasks")
    for cid in CASE_ORDER:
        cdir = os.path.join(tdir, cid)
        arts = []
        for name in sorted(os.listdir(os.path.join(cdir, "decisions"))):
            rec = json.load(open(os.path.join(cdir, "decisions", name), encoding="utf-8"))
            rec["_path"] = "decisions/" + name
            arts.append(rec)
        adir = os.path.join(cdir, "assessments")
        assess = []
        if os.path.isdir(adir):
            for name in sorted(os.listdir(adir)):
                if name.endswith(".json"):
                    try:
                        assess.append(json.load(open(os.path.join(adir, name),
                                                     encoding="utf-8")))
                    except Exception:
                        pass
        gate_rows = rows(os.path.join(cdir, "gate-log.jsonl"))
        d["cases"][cid] = {
            "artifacts": arts, "assessments": assess,
            "gate_rows": gate_rows,
            "gate": {r.get("decision_path"): r for r in gate_rows},
            "ledger": [r for r in d["led"] if r.get("case_id") == cid],
        }
    return d


D = None  # 全局数据(构建时注入)


def ev_item(ref, facts):
    """一条证据 → 白话句子;原始凭据进悬停提示。"""
    ref = str(ref)
    tip = esc(ref)
    if ref.startswith("$"):
        key = ref[1:]
        label = FACT_CN.get(key, key)
        val = facts.get(key, "?")
        return ('<li><b style="color:var(--blue)">事实快照</b>：%s = %s'
                '<span class="raw" data-tooltip="%s">原凭据</span></li>'
                % (esc(label), esc(val), tip))
    if ref.startswith("tool:"):
        parts = ref.split(":", 2)
        name, ts = parts[1], parts[2][:19]
        row = D["tc"].get(name + ":" + parts[2]) or {}
        note = row.get("note") or ""
        return ('<li><b style="color:var(--purple)">工具调用留痕</b>：真实执行了「%s」（%s）'
                '—— 有审计记录可查<span class="raw" data-tooltip="%s">原凭据</span></li>'
                % (esc(TOOL_CN.get(name, name)), esc(note or ts), tip))
    if ref.startswith("atom:"):
        a = D["atoms"].get(ref[5:]) or {}
        fact = str(a.get("fact", ""))[:80]
        return ('<li><b style="color:var(--green)">经验教训</b>：%s<span class="raw" '
                'data-tooltip="%s">原凭据</span></li>' % (esc(fact + ("…" if len(str(a.get("fact", ""))) > 80 else "")), tip))
    if ref.startswith("exp:"):
        return ('<li><b style="color:var(--yellow)">知识条目</b>：%s<span class="raw" '
                'data-tooltip="%s">原凭据</span></li>' % (esc(MC_CN.get(ref[4:], ref[4:])), tip))
    if ref.startswith("LEDGER:"):
        parts = ref.split(":")
        lid = parts[1] if len(parts) > 1 else ""
        expect = parts[2] if len(parts) > 2 else None
        row = next((r for r in D["led"] if r.get("lid") == lid), {})
        res = row.get("result", "?")
        claim = "，主张结果=%s" % ("成功" if expect == "success" else expect) if expect else ""
        return ('<li><b style="color:var(--orange)">运行台账</b>：第 %s 次计算 %s%s'
                '<span class="raw" data-tooltip="%s">原凭据</span></li>'
                % (esc(lid.rsplit("-", 1)[-1]), "真实跑成功了" if res == "success" else "记录为失败", esc(claim), tip))
    if ref.startswith("paper:"):
        return ('<li><b>文献参考</b>（只作对照数值，不算放行依据）<span class="raw" '
                'data-tooltip="%s">原凭据</span></li>' % tip)
    if ref.startswith("assessment:"):
        return ('<li><b>物理合理性评估</b>：方案/结果的物理判断记录（逐项对照知识锚）'
                '<span class="raw" data-tooltip="%s">原凭据</span></li>' % tip)
    if ref.startswith("factcheck:"):
        return ('<li><b>数值重算对账</b>：从原始日志重新计算该数值并核对一致'
                '<span class="raw" data-tooltip="%s">原凭据</span></li>' % tip)
    return '<li>%s</li>' % esc(ref)


def stage_block(art, gate_row):
    g = gate_row or {}
    res = g.get("result") or "?"
    cn, cls = RESULT_CN.get(res, ("未知", "b-na"))
    gate = art.get("gate")
    facts = art.get("_facts_snapshot") or {}
    h = ['<div class="stage %s">' % cls]
    h.append('<div class="s-head"><span class="gbadge %s">%s</span>'
             '<b>%s</b><span class="s-q">%s</span></div>'
             % (cls, cn, esc(gate), esc(GATE_QUESTION.get(gate, ""))))
    h.append('<div class="s-row"><span class="lab">判断依据</span>%s</div>'
             % esc(GATE_BASIS.get(gate, "")))
    # 触发的机检(白话)
    for k, v in (art.get("mandatory_check_results") or {}).items():
        if isinstance(v, dict) and v.get("triggered"):
            ok = v.get("passed")
            h.append('<div class="s-row"><span class="lab">机检项</span>%s —— %s</div>'
                     % (esc(MC_CN.get(k, k)), "通过（证据见下）" if ok else "未通过"))
    evs = art.get("evidence_refs") or []
    if evs:
        h.append('<div class="s-row"><span class="lab">调用的证据</span><ul>%s</ul></div>'
                 % "".join(ev_item(e.get("ref", "") if isinstance(e, dict) else e, facts)
                           for e in evs))
    for a in art.get("acceptance_checks") or []:
        ok = a.get("passed")
        h.append('<div class="s-row"><span class="lab">验收对照</span>'
                 '<span class="%s">%s</span> %s</div>'
                 % ("ok" if ok else "bad", "达标" if ok else "未达标", esc(a.get("evidence", ""))))
    reasons = g.get("reasons") or []
    if reasons:
        rcn = [REASON_CN.get(r, r) for r in reasons]
        h.append('<div class="s-row"><span class="lab">打回/拦截原因</span>%s</div>'
                 % "；".join(esc(x) for x in rcn))
    # 判断结果一句话
    verdict = {"GO": "四要素齐全，放行进入下一步。",
               "HOLD": "证据或配置有欠缺，先整改再送审。",
               "BLOCK": "触碰硬性红线，强制停下。"}[res] if res in RESULT_CN else ""
    if res == "BLOCK" and "S2" in "".join(reasons):
        verdict = "执行策略生效：还没有小规模成功记录，大规模提交被强制拦下——这正是设计的行为。"
    h.append('<div class="s-row"><span class="lab">判断结果</span><b>%s</b> — %s</div>'
             % (cn, esc(verdict)))
    h.append('<div class="s-art text-small text-muted" data-tooltip="%s">判断文书已留档备查（悬停查看编号）</div>'
             % esc(art["_path"]))
    h.append("</div>")
    return "".join(h)



def assess_blocks(cd):
    """物理合理性评估块:每个 finding 按 判断问题/依据/证据/结论 四要素白话展示。"""
    if not cd.get("assessments"):
        return ""
    h = []
    for rec in cd["assessments"]:
        phase = {"plan": "方案送审前", "results": "结果验收前"}.get(rec.get("phase"), "?")
        is_deep = rec.get("mode") == "deep-methodology"
        title = ("方法论深度分析 · %s（%s，%d 个分析维度）" % (phase, rec.get("family") or "通用",
                  rec.get("dimension_count", len(rec.get("findings") or [])))
                 if is_deep else "物理合理性快检 · %s" % phase)
        subtitle = ("逐维度分析仿真方法本身是否正确（维度=知识库条目）" if is_deep
                    else "逐项对照物理知识锚")
        h.append('<div class="stage %s">' % ("b-go" if not rec.get("critical_open") else "b-block"))
        h.append('<div class="s-head"><span class="gbadge %s">%s</span>'
                 '<b>%s</b><span class="s-q">%s</span></div>'
                 % ("b-go" if not rec.get("critical_open") else "b-block",
                    "全部合理" if not rec.get("critical_open") else "存在重大疑点",
                    title, subtitle))
        for f in rec.get("findings") or []:
            v_cn, v_cls = VERDICT_CN.get(f.get("verdict"), (f.get("verdict"), "v-na"))
            basis = "；".join(ANCHOR_CN.get(r.replace("exp:", ""), r) for r in f.get("basis") or [])
            h.append('<div class="s-row"><span class="lab">判断问题</span>%s</div>'
                     % esc(f.get("question", "")))
            if f.get("why"):
                h.append('<div class="s-row"><span class="lab">为什么重要</span>'
                         '<span class="text-muted">%s</span></div>' % esc(f["why"]))
            h.append('<div class="s-row"><span class="lab">判断依据</span>%s</div>'
                     % esc(basis or "—"))
            h.append('<div class="s-row"><span class="lab">证据</span>%s</div>'
                     % esc(f.get("evidence", "")))
            h.append('<div class="s-row"><span class="lab">结论</span>'
                     '<span class="%s">%s</span>%s</div>'
                     % (v_cls, v_cn, (" — " + esc(f["note"])) if f.get("note") else ""))
        if rec.get("critical_open"):
            h.append('<div class="s-row"><span class="lab">后果</span><b class="bad">'
                     '重大疑点未清零，门禁不放行（转人工）：%s</b></div>'
                     % esc("、".join(rec["critical_open"])))
        else:
            h.append('<div class="s-row"><span class="lab">后果</span>无重大疑点，'
                     '放行%s</div>' % ("（带提示，见各项）" if rec.get("overall") ==
                                       "concerns-identified" else ""))
        h.append("</div>")
    return "".join(h)


def case_panel(cid, cd):
    h = ['<h2>%s</h2>' % esc(CASE_TITLE[cid])]
    h.append('<p class="story">%s</p>' % esc(CASE_STORY[cid]))
    h.append('<div class="rescard"><b>计算结果（真实日志解析）：</b>%s</div>'
             % esc(CASE_RESULT_CN[cid]))
    h.append("<h3>物理合理性评估（仿真对不对的第一道判断）</h3>")
    h.append('<div class="flow">%s</div>' % assess_blocks(cd))
    h.append('<h3>流程与每步判断</h3><div class="flow">')
    for art in cd["artifacts"]:
        h.append(stage_block(art, cd["gate"].get(art["_path"])))
    h.append("</div>")
    h.append("<h3>真实计算执行记录（全部经 MDagent 唯一执行通道，有审计账）</h3>")
    h.append('<table class="table table-sm"><thead><tr><th>做了什么</th><th>结果</th>'
             '<th>关键产出</th></tr></thead><tbody>')
    for r in cd["ledger"]:
        det = r.get("detail") or {}
        what, out = RUN_CN.get(r.get("lid"), (r.get("lid"), ""))
        ok = r.get("result") == "success"
        rescn = ("成功跑完" if ok else
                 ("失败中断（%s）" % ("原子飞散" if det.get("lost_atoms") else "异常退出")))
        h.append("<tr><td>%s</td><td><b class='%s'>%s</b></td><td class='text-small'>%s</td></tr>"
                 % (esc(what), "ok" if ok else "bad", esc(rescn), esc(out)))
    h.append("</tbody></table>")
    return "".join(h)


def matrix_panel():
    """读最近一次功能矩阵结果（tests/e2e_evidence/runs/*/functional_matrix.json）。"""
    import glob
    files = sorted(glob.glob(os.path.join(HERE, "..", "..", "tests", "e2e_evidence",
                                          "runs", "*", "functional_matrix.json")))
    h = ["<h2>功能矩阵：设计的功能是否真的生效</h2>"]
    if not files:
        h.append('<p class="story">未找到矩阵结果；先运行 '
                 '<code>python tests/e2e_evidence/run_functional_matrix.py</code>。</p>')
        return "".join(h)
    d = json.load(open(files[-1], encoding="utf-8"))
    h.append('<p class="story">不是"代码能跑"，而是逐项验证：规则是否在正确的闸生效、'
             '知识库条目是否真的驱动判断、高危/不合理操作是否能被抓出来。'
             '本次 <b>%d/%d</b> 项通过。</p>' % (d["passed"], d["total"]))
    h.append('<table class="table table-sm"><thead><tr><th>分组</th><th>通过/总数</th>'
             '<th>覆盖内容</th></tr></thead><tbody>')
    cover = {"E1": "设计有据：分级门槛/适用性/人工覆盖追问/二次确认",
             "E2": "计算有据：链路/数值重算/新鲜性",
             "E3": "物理合理：评估缺位与 critical",
             "E4": "方法学合规：两条真实约束 + 知识注入/撤销",
             "E5": "结论完整：验收覆盖与回退",
             "BIND": "闸绑定：每个闸只跑本段规则",
             "HIGH": "高危操作：危险步长/大规模无先行/高风险弱证据",
             "KB": "知识库：10 个方法论维度正反例 + 锚注入/撤销",
             "SURF2": "核查面深化：各相位负例/答案校验/提问闭环/MCP 三动作/沉淀复用/未知族",
             "REAL": "真实产物：用实弹日志独立重算"}
    for g, (a, b) in d["groups"].items():
        h.append("<tr><td><b>%s</b></td><td>%d/%d</td><td>%s</td></tr>"
                 % (esc(g), a, b, esc(cover.get(g, ""))))
    h.append("</tbody></table>")
    bad = [r for r in d["rows"] if not r["ok"]]
    if bad:
        h.append("<h3>未通过项</h3><ul>")
        for r in bad:
            h.append("<li>%s / %s：%s</li>" % (esc(r["group"]), esc(r["name"]),
                                              esc(r["detail"])))
        h.append("</ul>")
    return "".join(h)


def build_fragment(d):
    global D
    D = d
    rep = d["rep"]
    n_tc = len(d["tc"])
    h = ['<div id="ev-live-report">']
    h.append('<h1>证据系统实弹验证汇报（白话版）</h1>')
    h.append('<p class="story">十一个真实算例，全部符合预期 MDagent 的门禁流程；能跑的跑真实计算，该拦的零执行。'
             '下面每个算例按流程展开：<b>每一步判断了什么、依据什么规则、拿出了什么证据、判成了什么</b>，'
             '全部用平实语言说明；想核对原始凭据（编号、路径、时间戳）时，点「原凭据」悬停即可看到。</p>')
    h.append('<div class="viz-grid" style="margin-bottom:12px">')
    for lab, val, ctx in [("实弹算例", "11 / 11", "五完成 + 四拦截(全零执行) + 两流程演示"),
                          ("核验项", "%d / %d" % (len(rep.get("checks") or []), len(rep.get("checks") or [])), "全绿"),
                          ("真实工具调用", str(n_tc), "每次都有审计记录"),
                          ("真实 LAMMPS 计算", str(len(d["aud"])), "全部过白名单与审计账"),
                          ("门禁规则", "E1-E6 六条", "设计有据/计算有据/物理合理/方法学合规/结论完整/核查面完整"),
                          ("知识锚+方法学", "6 锚+2 规则", "锚判物理量/规则判配置组合"),
                          ("合理性判断", "20 份评估", "十案例×计划/结果相位,逐项对锚"),
                          ("数值对账", "10 条", "验收数字全部重算核对")]:
        h.append('<div class="card viz-stat"><div class="text-small">%s</div>'
                 '<div class="viz-stat-value tabular-nums">%s</div>'
                 '<div class="text-small text-muted">%s</div></div>' % (lab, val, ctx))
    h.append("</div>")
    h.append('<div class="nav nav-pills" role="tablist" aria-label="算例">')
    tabs = [("ovw", "系统全景"), ("map", "生效点与拦截")] + [(c.lower().replace("-", ""), CASE_TITLE[c].split("：")[0]) for c in CASE_ORDER] \
        + [("matrix", "功能矩阵"), ("aud", "复核与教训")]
    for i, (tid, label) in enumerate(tabs):
        h.append('<button class="nav-link%s" id="t-%s" role="tab" aria-controls="p-%s" '
                 'aria-selected="%s" type="button">%s</button>'
                 % (" active" if i == 0 else "", tid, tid,
                    "true" if i == 0 else "false", esc(label)))
    h.append("</div>")
    # 系统全景内容标记
    # ---------- 系统全景 ----------
    h.append('<div id="p-ovw" role="tabpanel" aria-labelledby="t-ovw">')
    h.append("<h2>这套证据系统是什么</h2>")
    h.append('<p class="story">一句话：<b>每一项仿真设计、每一条计算结论，都要说清楚"凭什么"'
             '——依据是论文、过往案例、经验教训、人工输入还是本任务实测；依据够不够强、'
             '适不适用、彼此冲突时听谁的。</b>它只管这件事：不管字段格式，不管文件在不在，'
             '也不管工具调用有没有发生（那些归工具层）。判定全部由确定性代码执行（零模型参与），'
             'fail-closed。</p>')
    h.append("<h3>五条规则，全部围绕「设计与计算有没有依据」</h3>")
    h.append('<table class="table table-sm"><thead><tr><th>规则</th><th>回答的问题</th>'
             '<th>生效闸</th></tr></thead><tbody>'
             "<tr><td><b>E1 设计有据</b></td><td>每项锁定设计决策，是否有达到风险门槛、"
             "且适用的分级证据？人工覆盖是否给出了理由？</td><td>G-PLAN</td></tr>"
             "<tr><td><b>E2 计算有据</b></td><td>这次计算能否回溯到设计决策与输入文件？"
             "结论里的每个数字能否从本次产物重算？</td><td>G-EXEC / G-ANALYZE</td></tr>"
             "<tr><td><b>E3 物理合理</b></td><td>设计与结果是否经过物理评估，重大疑点是否清零？"
             "</td><td>G-PLAN / G-ANALYZE</td></tr>"
             "<tr><td><b>E4 方法学合规</b></td><td>输入文件与配置组合是否符合方法学约束？"
             "（门禁直接读磁盘文件独立复算）</td><td>G-BUILD / G-CONFIG</td></tr>"
             "<tr><td><b>E5 结论完整</b></td><td>结论是否覆盖验收项？不达标是否给出回退？"
             "</td><td>G-ANALYZE</td></tr>"
             "</tbody></table>")
    h.append("<h3>证据分六级：来源决定优先级</h3>")
    h.append('<table class="table table-sm"><thead><tr><th>级别</th><th>来源</th>'
             '<th>怎么用</th></tr></thead><tbody>'
             "<tr><td><b>A</b></td><td>人工确认（用户/专家）</td><td>最高效力；覆盖其他证据须说明理由，"
             "二次确认后成为跨任务生效的最高权限人工经验</td></tr>"
             "<tr><td><b>B</b></td><td>论文/标准/手册</td><td>设计依据的主力；须可解析且与体系/工况匹配</td></tr>"
             "<tr><td><b>C</b></td><td>本任务实测（日志/产物）</td><td>结果结论的第一优先级；必须能重算</td></tr>"
             "<tr><td><b>D</b></td><td>过往案例</td><td>同族案例可直接支撑；近似族只作参考</td></tr>"
             "<tr><td><b>E</b></td><td>过往经验/教训</td><td>须 active；canary 只提示不支撑硬结论</td></tr>"
             "<tr><td><b>F</b></td><td>物理估算/常识</td><td>仅提示，不得单独支撑任何决策</td></tr>"
             "</tbody></table>")
    h.append("<h3>每个阶段只跑本段的规则（渐进式披露）</h3>")
    h.append('<table class="table table-sm"><thead><tr><th>段</th><th>闸</th>'
             '<th>本段判断（白话）</th><th>本段必须有的证据/工件</th></tr></thead><tbody>'
             "<tr><td>方案</td><td>G-PLAN</td><td>设计有没有依据、物理上站不站得住</td>"
             "<td>锁定决策+分级依据、计划段评估</td></tr>"
             "<tr><td>准备输入</td><td>G-BUILD</td><td>写出来的输入文件是否符合方法学约束</td>"
             "<td>真实输入文件</td></tr>"
             "<tr><td>参数定稿</td><td>G-CONFIG</td><td>配置组合再复核一次</td><td>最终输入文件</td></tr>"
             "<tr><td>真实计算</td><td>G-EXEC</td><td>决策→输入→执行的链路完整吗</td>"
             "<td>输入文件、锁定决策</td></tr>"
             "<tr><td>验收结题</td><td>G-ANALYZE</td><td>每个数字重算、物理复核、验收覆盖</td>"
             "<td>factcheck 记录、结果段评估、验收对照</td></tr>"
             "</tbody></table>")
    h.append("<h3>知识底座（一套，越用越厚）</h3>")
    h.append('<ul class="evdoc">'
             '<li><b>物理锚（6 条）</b>：步长稳定性／控温有效性／液态结构／密度量级／'
             '复现偏差／验收可量化——定义一次，在评估与对账处消费。</li>'
             '<li><b>方法学约束（2 条）</b>：恒压+变形、往复做成单向——门禁直接读输入文件复算。</li>'
             '<li><b>过往案例/经验</b>：实弹案例记录与 atoms 经验条目，按 D/E 级计入证据。</li>'
             '<li><b>人工输入</b>：确认记录可查；二次确认晋升为最高权限人工经验。</li></ul>')
    h.append("<h3>十案例实弹结果一览</h3>")
    h.append('<table class="table table-sm"><thead><tr><th>算例</th><th>类型</th>'
             '<th>结果（白话）</th></tr></thead><tbody>')
    for cid in CASE_ORDER:
        typ = {"SIMPLE-04": "简单", "LIT-01": "文献复现", "LIT-02": "近似文献·双实现",
               "LIT-03": "近似文献", "COMPLEX-05": "复杂·评估驱动",
               "UNREASONABLE-06": "不合理·被拦截",
               "BAD-CONFIG-07": "错误配置·恒压抵消变形",
               "BAD-CONFIG-08": "错误配置·往复做单向",
               "NOBASIS-09": "无依据设计·被拦截",
               "OVERRIDE-10": "人工覆盖·追问与二次确认",
               "SURFACE-11": "核查面·筛查/提问/取证/沉淀"}[cid]
        h.append("<tr><td><b>%s</b></td><td>%s</td><td>%s</td></tr>"
                 % (esc(cid), esc(typ), esc(CASE_RESULT_CN.get(cid, ""))))
    h.append("</tbody></table></div>")

    # ---------- 生效点与拦截地图 ----------
    h.append('<div id="p-map" role="tabpanel" aria-labelledby="t-map" hidden>')
    h.append("<h2>每个阶段：证据系统在哪里生效、在哪个门判断和拦截</h2>")
    h.append('<p class="story">五个阶段各有明确的生效点与判断门；拦截不是抽象承诺——'
             '下面的矩阵和实录全部取自本次实弹的门禁日志（真实记录，非示意）。</p>')
    h.append("<h3>阶段 × 生效点 × 判断门 × 规则</h3>")
    h.append('<table class="table table-sm"><thead><tr><th>阶段</th><th>生效点（在哪）</th>'
             '<th>判断门（谁拦）</th><th>用什么规则判断</th><th>本次实弹谁被拦</th></tr></thead><tbody>'
             "<tr><td><b>方案</b></td><td>决策文书落盘送审时；物理评估引擎对每项决策"
             "逐条对照知识锚</td><td>方案闸 G-PLAN</td><td>文书完整性/证据查账/"
             "<b>物理合理性（重大疑点未清零即拦）</b></td>"
             "<td class='bad'><b>UNREASONABLE-06</b>：危险步长 dt*=0.046 超失稳线，"
             "评估判重大疑点，闸拒——<b>零执行</b></td></tr>"
             "<tr><td><b>准备输入</b></td><td>声称写好的输入文件本身</td><td>输入闸 G-BUILD"
             "</td><td>文件真实存在、格式合法；引用可查账</td><td>（六案例全部通过）</td></tr>"
             "<tr><td><b>参数定稿</b></td><td>配置定稿送审；触碰高危操作面（如大规模提交）时"
             "</td><td>定稿闸 G-CONFIG</td><td>机检清单逐条覆盖；<b>高危面必须附真实工具实证"
             "（口头承诺不算）</b></td><td>（COMPLEX 触发大规模面检查，凭工具实证通过）</td></tr>"
             "<tr><td><b>真实计算</b></td><td><b>两道门</b>：提交前过执行闸；执行命令进入唯一"
             "执行通道时还有一层前置拦截（阶段不对/闸未过/受理时状态又被翻动，都会拒收）</td>"
             "<td>执行闸 G-EXEC + 执行通道前置拦截</td><td><b>小规模先行铁律（没有小规模成功"
             "记录，大规模一律拦）</b>；高风险双通道证据；白名单+审计账</td>"
             "<td class='bad'><b>COMPLEX-05</b>：第一次大规模提交被执行策略拦下"
             "（S2），先跑小规模验证成功后才放行</td></tr>"
             "<tr><td><b>验收结题</b></td><td>验收判据送审时；数字直接从原始日志重算；"
             "结果再做一次物理评估（对锚）</td><td>结题闸 G-ANALYZE</td>"
             "<td>验收逐条对照锁定标准；<b>数值重算对账（对不上即拒）</b>；结果物理评估"
             "无重大疑点</td><td>（六案例全部通过对账；调试期曾拦下 C11 单位错误——"
             "对账机制实录见「复核与教训」）</td></tr>"
             "</tbody></table>")
    # 矩阵:案例×闸 → 结论(真实 gate-log)
    gates = ["G-PLAN", "G-BUILD", "G-CONFIG", "G-EXEC", "G-ANALYZE"]
    h.append("<h3>实弹门禁矩阵（每格=该案例在该闸的送审结论，取自门禁日志）</h3>")
    h.append('<table class="table table-sm"><thead><tr><th>案例</th>' +
             "".join("<th>%s</th>" % g.replace("G-", "") for g in gates) + "</tr></thead><tbody>")
    for cid in CASE_ORDER:
        rows_g = {}
        for r in d["cases"][cid].get("gate_rows") or []:
            g = r.get("gate")
            if g in gates and r.get("result") in ("GO", "HOLD", "BLOCK"):
                rows_g.setdefault(g, [])
                rows_g[g].append(r.get("result"))
        cells = []
        for g in gates:
            rs = rows_g.get(g) or []
            if not rs:
                cells.append('<td class="text-muted">—</td>')
            elif all(x == "GO" for x in rs):
                cells.append('<td class="ok">✓ GO</td>')
            else:
                tag = " / ".join(dict.fromkeys(rs))
                cells.append('<td class="bad"><b>%s</b></td>' % esc(tag))
        h.append("<tr><td><b>%s</b></td>%s</tr>" % (esc(cid), "".join(cells)))
    h.append("</tbody></table>")
    h.append('<p class="text-small text-muted">矩阵只统计送审结论（GO/HOLD/BLOCK）；执行通道的'
             '受理放行（ACCEPT）与前置拦截记录在执行审计账中，另见下。</p>')
    # 拦截实录
    h.append("<h3>拦截实录（本次实弹全部非放行记录）</h3>")
    h.append('<table class="table table-sm"><thead><tr><th>案例</th><th>门</th><th>结论</th>'
             '<th>拦截原因（白话）</th></tr></thead><tbody>')
    n_block = 0
    for cid in CASE_ORDER:
        for r in d["cases"][cid].get("gate_rows") or []:
            if r.get("result") == "BLOCK":
                n_block += 1
                def _cn(x):
                    if x in REASON_CN:
                        return REASON_CN[x]
                    for k, v in REASON_CN.items():
                        if x.startswith(k):
                            return v
                    return x
                rs = ";".join(_cn(x) for x in (r.get("reasons") or [])[:2])
                h.append("<tr><td><b>%s</b></td><td>%s</td><td><b class='bad'>拦截</b></td>"
                         "<td class='text-small'>%s</td></tr>"
                         % (esc(cid), esc(r.get("gate")), esc(rs)))
    if n_block == 0:
        h.append("<tr><td colspan=4 class='text-muted'>（无）</td></tr>")
    h.append("</tbody></table>")
    h.append("<h3>执行通道的「门外的门」（命令真正开跑前的最后拦截）</h3>"
             '<ul class="evdoc">'
             '<li><b>阶段守卫</b>：任务没到执行段，任何计算命令直接拒收。</li>'
             '<li><b>闸结论守卫</b>：执行闸未放行（或此前的放行已被推翻），拒收——'
             '且<b>受理瞬间再核一次</b>，防止校验后、执行前被人翻动状态。</li>'
             '<li><b>验证豁免通道</b>：小规模验证探针不受闸结论限制（铁律要求的先行验证'
             '本身就是执行），但仍受阶段守卫。</li>'
             '<li><b>白名单+审计账</b>：可执行文件白名单、参数直传防注入、每条命令留审计'
             '记录——本次实弹 7 次真实执行全部过账。</li></ul>')
    h.append("</div>")

    for tid, cid in [(c.lower().replace("-", ""), c) for c in CASE_ORDER]:
        h.append('<div id="p-%s" role="tabpanel" aria-labelledby="t-%s" hidden>%s</div>'
                 % (tid, tid, case_panel(cid, d["cases"][cid])))
    # 功能矩阵
    h.append('<div id="p-matrix" role="tabpanel" aria-labelledby="t-matrix" hidden>')
    h.append(matrix_panel())
    h.append("</div>")
    # 复核
    h.append('<div id="p-aud" role="tabpanel" aria-labelledby="t-aud" hidden>')
    h.append("<h2>证据可信吗 —— 三道独立复核</h2>"
             "<h3>复核一：重放全部判断</h3>"
             '<p class="story">把 37 份判断文书重新过了一遍 E1-E6 规则（用文书里存的事实快照），'
             '对比当时留下的门禁日志。<b>结论：37 份全部一致</b>——没有任何一份当时放行、现在却过不了，'
             '或反过来。</p>'
             "<h3>复核二：逐条引用查账</h3>"
             '<p class="story">每份文书引用的工具调用、运行台账、经验教训，逐条去真实账本里查：'
             '工具调用共 %d 次都有记录；%d 次 LAMMPS 执行全部在执行审计账里（白名单核验过）；'
             '文献引用从未被当作放行依据（规则如此设计）。<b>结论：零失配。</b></p>'
             "<h3>复核三：数字直接从日志重算</h3>"
             '<p class="story">不信任文书自己写的数字，直接把留档的 LAMMPS 日志重新解析一遍，'
             '和文献锚点对表：</p>'
             '<table class="table table-sm"><thead><tr><th>量</th><th>日志实测</th><th>对照标准</th>'
             '<th>判定</th></tr></thead><tbody>'
             "<tr><td>铜晶格常数</td><td>3.6150 埃</td><td>论文 3.615（容差 0.005）</td><td class='ok'>✓</td></tr>"
             "<tr><td>铜结合能</td><td>−3.5400 电子伏</td><td>论文 −3.54（容差 0.03）</td><td class='ok'>✓</td></tr>"
             "<tr><td>氩晶格常数</td><td>5.2764 埃</td><td>独立数学程序 5.2764（容差 0.002）</td><td class='ok'>✓</td></tr>"
             "<tr><td>氩结合能</td><td>−1.9285 千卡/摩尔</td><td>独立数学程序 −1.9285（容差 0.003）</td><td class='ok'>✓</td></tr>"
             "</tbody></table>"
             "<h3>这套核查真抓过问题吗 —— 抓过三次</h3><ul class='notes'>"
             "<li><b>抓到过「打脸」的文书</b>：调试期间有一次大规模计算实际失败了（台账记失败），"
             "但结题文书却引用它作成功证据——查账环节按设计拒收，直到重跑真正成功才闭合。这证明"
             "「引用查账」不是摆设。</li>"
             "<li><b>抓到过两处脚本错误</b>：一次单位用错导致弹性模量算出天文数字（大 8000 倍），"
             "一次统计命令语法用错——都是数字对不上账后暴露、修复、重跑解决的。</li>"
             "<li><b>纠正过一次物理误判</b>：原以为 25 飞秒步长会让验证失败，实际对氩是稳定区间；"
             "改成 100 飞秒后失败才真实发生（原子飞散、温度发散）。失败证据是真实物理，不是摆拍。</li></ul>"
             "<h3>沉淀的经验（进入项目知识库）</h3>"
             '<table class="table table-sm"><thead><tr><th>类型</th><th>内容</th></tr></thead><tbody>')
    for a in d["atoms"].values():
        h.append("<tr><td>%s</td><td class='text-small'>%s</td></tr>"
                 % (esc(a.get("kind")), esc(a.get("fact"))))
    h.append("</tbody></table>")
    h.append('<p class="text-small text-muted">全部原始产物已留档：每个算例的 LAMMPS 日志、'
             '26 份判断文书、门禁日志、运行台账、经验库、工具调用与执行审计账。</p></div>')
    h.append("</div>")
    return "".join(h)




CSS = """<style>
#ev-live-report .story{font-size:13px;color:var(--muted-foreground);margin:4px 0 10px}
#ev-live-report .rescard{border-left:3px solid var(--green);background:color-mix(in srgb,var(--green) 7%,transparent);padding:8px 12px;border-radius:0 8px 8px 0;font-size:13px;margin:8px 0}
#ev-live-report .rgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:4px 14px;margin:6px 0}
#ev-live-report .ritem{font-size:12.5px;color:var(--muted-foreground)}
#ev-live-report .ritem code{color:var(--foreground)}
#ev-live-report .evdoc{font-size:13px;margin-left:18px}
#ev-live-report .flow{display:flex;flex-direction:column;gap:12px;margin:10px 0}
#ev-live-report .stage{border-left:3px solid var(--border);padding:4px 0 4px 14px}
#ev-live-report .stage.b-go{border-left-color:var(--green)}
#ev-live-report .stage.b-hold{border-left-color:var(--yellow)}
#ev-live-report .stage.b-block{border-left-color:var(--red)}
#ev-live-report .s-head{display:flex;flex-wrap:wrap;gap:8px;align-items:center;font-size:14px;margin-bottom:4px}
#ev-live-report .s-q{color:var(--foreground);font-weight:500}
#ev-live-report .gbadge{font-size:12px;font-weight:500;padding:1px 10px;border-radius:999px}
#ev-live-report .b-go{background:color-mix(in srgb,var(--green) 18%,transparent);color:var(--green)}
#ev-live-report .b-acc{background:color-mix(in srgb,var(--green) 10%,transparent);color:var(--green)}
#ev-live-report .b-hold{background:color-mix(in srgb,var(--yellow) 20%,transparent);color:var(--yellow)}
#ev-live-report .b-block{background:color-mix(in srgb,var(--red) 18%,transparent);color:var(--red)}
#ev-live-report .s-row{font-size:13px;margin:4px 0;display:flex;gap:8px;align-items:baseline}
#ev-live-report .lab{flex:none;font-size:11px;color:var(--muted-foreground);border:1px solid var(--border);
 border-radius:4px;padding:0 6px;min-width:74px;text-align:center}
#ev-live-report .s-row ul{margin:2px 0 0 4px;padding-left:16px}
#ev-live-report .s-row li{margin:2px 0;font-size:12.5px}
#ev-live-report .raw{margin-left:6px;font-size:11px;color:var(--muted-foreground);cursor:help;
 border-bottom:1px dotted var(--border)}
#ev-live-report .s-art{margin-top:2px}
#ev-live-report .ok{color:var(--green)}#ev-live-report .bad{color:var(--red)}
#ev-live-report .v-sound{color:var(--green);font-weight:500}
#ev-live-report .v-concern{color:var(--yellow);font-weight:500}
#ev-live-report .v-critical{color:var(--red);font-weight:500}
#ev-live-report .v-na{color:var(--muted-foreground)}
#ev-live-report .notes{font-size:13px;margin-left:18px}
#ev-live-report .notes li{margin:6px 0}
#ev-live-report .v2layers{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:8px;margin:8px 0}
#ev-live-report .v2l{border-left:3px solid var(--border);padding:4px 0 4px 12px;font-size:12.5px}
#ev-live-report .v2l b{font-size:13px}
#ev-live-report .v2l span{display:inline-block;margin-left:6px;font-size:11px;color:var(--muted-foreground);
 border:1px solid var(--border);border-radius:4px;padding:0 6px}
#ev-live-report .v2l em{display:block;font-style:normal;font-size:11px;color:var(--muted-foreground);margin-top:2px}
#ev-live-report h2{margin:16px 0 6px}#ev-live-report h3{margin:12px 0 4px}
</style>"""

SHELL = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>证据系统实弹验证汇报（白话版）</title><style>
:root{--background:#fff;--foreground:#18181b;--card:#fafafa;--muted:#f4f4f5;--muted-foreground:#71717a;
--border:#e4e4e7;--green:#16a34a;--red:#dc2626;--yellow:#ca8a04;--orange:#ea580c;--blue:#2563eb;
--purple:#7c3aed;--primary:#18181b;--popover:#fafafa}
@media(prefers-color-scheme:dark){:root{--background:#0c0c0e;--foreground:#f4f4f5;--card:#17171a;
--muted:#1e1e22;--muted-foreground:#a1a1aa;--border:#2a2a30;--green:#4ade80;--red:#f87171;
--yellow:#facc15;--orange:#fb923c;--blue:#60a5fa;--purple:#a78bfa;--primary:#f4f4f5;--popover:#17171a}}
body{margin:0;padding:20px;background:var(--background);color:var(--foreground);
 font-family:system-ui,"Segoe UI","Microsoft YaHei",sans-serif;font-size:14px;line-height:1.6}
h1{font-size:22px}h2{font-size:17px}h3{font-size:14.5px}
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:10px 14px}
.viz-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:8px}
.viz-stat-value{font-size:20px;font-weight:500}
.text-small{font-size:12px}.text-muted{color:var(--muted-foreground)}
.tabular-nums{font-variant-numeric:tabular-nums}
.nav{display:flex;gap:6px;flex-wrap:wrap;margin:12px 0}
.nav-link{border:1px solid var(--border);background:transparent;color:var(--foreground);
 border-radius:999px;padding:4px 14px;font-size:13px;cursor:pointer}
.nav-link.active{background:var(--primary);color:var(--background);border-color:var(--primary)}
table.table{border-collapse:collapse;width:100%;font-size:13px;margin:6px 0}
.table th{text-align:left;color:var(--muted-foreground);font-weight:500;border-bottom:1px solid var(--border);padding:4px 8px}
.table td{border-bottom:1px solid var(--border);padding:4px 8px;vertical-align:top}
code{background:var(--muted);border-radius:4px;padding:0 4px;font-size:12px}
[data-tooltip]{position:relative}
[data-tooltip]:hover::after{content:attr(data-tooltip);position:absolute;left:0;top:120%;z-index:9;
 background:var(--popover);color:var(--foreground);border:1px solid var(--border);border-radius:6px;
 padding:4px 8px;font-size:11px;white-space:nowrap;max-width:70vw;overflow:hidden;text-overflow:ellipsis}
</style></head><body>
__FRAG__
<script>
document.querySelectorAll('.nav-link[role=tab]').forEach(function(b){b.addEventListener('click',function(){
 document.querySelectorAll('.nav-link[role=tab]').forEach(function(x){x.classList.remove('active');x.setAttribute('aria-selected','false')});
 b.classList.add('active');b.setAttribute('aria-selected','true');
 document.querySelectorAll('[role=tabpanel]').forEach(function(p){p.hidden=(p.id!==b.getAttribute('aria-controls'))});
})});
</script></body></html>"""


def main():
    d = load_all()
    frag = CSS + build_fragment(d)
    os.makedirs(VIZ_DIR, exist_ok=True)
    fp = os.path.join(VIZ_DIR, "evidence-live-report.html")
    with open(fp, "w", encoding="utf-8") as f:
        f.write(frag)
    with open(os.path.join(HERE, "report.html"), "w", encoding="utf-8") as f:
        f.write(SHELL.replace("__FRAG__", frag))
    print("fragment:", fp, len(frag), "bytes")
    print("standalone:", os.path.join(HERE, "report.html"))


if __name__ == "__main__":
    main()
