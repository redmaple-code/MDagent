# MDagent (dsh-agent)

**全链分子模拟智能体** —— dsh CLI 对话脑(可插拔 LLM)套壳在领域编排系统(agent_deploy_kit v3.3)之上的物理感知 Agentic AI4Science 系统。

**MDagent** — a full-stack molecular-simulation agent: a physics-aware Agentic AI4Science system built as a dsh CLI conversational brain (pluggable LLM) wrapped around a domain orchestration system (agent_deploy_kit v3.3).

- **版本 / Version**: v1.6.7+20260920(见 `VERSION`;版本历史见 `RELEASE.md`)
- **许可证 / License**:**PolyForm Noncommercial 1.0**(见 `LICENSE`)——开源可见、可修改、可非商用,禁止商业使用,商用须书面授权;第三方声明见 `NOTICE`
- **状态 / Status**:内部私有仓库,自内部部署环境脱敏导出(见 `SANITIZATION.md`)

---

## 本仓库当前用途(二开工作区) / What this fork is for

本仓库在 MDagent v1.6.8 脱敏源码之上,承载两项并行开发成果,并作为**交接载体**:

This fork layers two workstreams on top of the sanitized MDagent v1.6.8 source, and serves as the handover artifact:

1. **前置双门(Pre-simulation dual gate)**:在建模/动力学之前插入"仿真方案设计(C0–C6 状态机)"
   与"完整审阅与补全(A–I 八层 AI 审阅 + 三档严重度 + R-VERIFY 责任闭环)"。
   代码入口:`mcp/md_proj_mcp.py`、`mcp/design_review.py`、`mcp/physics_gate.py`、
   `mcp/review_experience/`;设计规格 `docs/设计规格_仿真方案设计与物理一致性校核.md`。
2. **任务级证据系统(Evidence system)**:阶段状态机 + 机械证据门 R1–R19 + 内容/知识证据层。
   代码入口:`evolution/stage_state.py`、`stage_gate.py`、`project_checks.py`、`assessment.py`、
   `content_check.py`、`deep_review.py`、`config_check.py`。

**从这里开始读 / Start here**

| 材料 | 用途 |
|---|---|
| **[四个子系统的两套可插拔模块包](subsystems/README.md)** | 原生拆分版与标准库自包含版：适用范围、下载、接口、验证和已知限制 |
| **[`HANDOFF.md`](HANDOFF.md)** | **完整交接文档**:架构、状态机、运行/测试、已知问题、硬约束、接手检查清单 |
| [`.project-to-act/`](.project-to-act/) | 项目治理快照:总览/进度/功能/验收/版本五文档 + docs 索引(设计规格、开发手册、功能文档、漏洞审计) |
| [`docs/设计规格_仿真方案设计与物理一致性校核.md`](docs/设计规格_仿真方案设计与物理一致性校核.md) | 双门预期基线(状态机/八层/三档/一致性 schema) |
| `.project-to-act/docs/审计_双门功能事实核查与漏洞清单_20261018.md` | 交付前审计:6 高危 / 9 中危 / 8 低危 + 修复优先级 |

**快速自检 / Quick check**(仓库根,双门部分仅需 Python 标准库):

```bash
python -m unittest tests.test_physics_gate tests.test_design_review \
                     tests.test_md_proj_mcp tests.test_model_research
python tests/e2e_dual_gate_demo.py      # 五场景,含 fail-closed
node tests/test_flow_gate.mjs           # 32/32
```

2026-10-18 基线:双门单测 16/16、16/16、36/36(1 skip)、18/18;e2e 5/5;flow-gate 32/32;
证据系统 84/84。`test_md_mcp` 的 10 个失败为 Windows 环境既有问题,与双门无关。

---

## 介绍 / Introduction

MDagent 解决的核心问题是:**让 LLM 驱动的智能体安全、可审计、可进化地执行真实分子模拟任务**,而不是只会聊模拟。它以水凝胶大气取水(AWH)12 篇文献的完全复现为端到端实证任务,把"物理正确性"和"权限安全"做成代码强制的结构,而非提示词约定:

MDagent's core problem: **let an LLM-driven agent run real molecular-simulation workloads safely, auditably, and with the ability to evolve** — not just talk about simulation. Validated end-to-end by fully reproducing 12 atmospheric-water-harvesting (AWH) hydrogel papers, it enforces physical correctness and permission security as code-level structure, not prompt-level convention:

- **物理感知(结构拦截,非提示词)**:模拟数字只来自只读真相源工具;力场文件永不改;爆炸检测解析级不失明;超算提交 PIN 码人工闸;harness 级 flow-gate(意图布防→证据工具销防,豁免白名单消误报)
- **Physics-aware by construction**: numbers only from read-only truth-source tools; force-field files never modified; parse-level explosion detection; PIN-gated HPC submission; harness-level flow gate (intent arming → evidence-tool disarm, with an exemption whitelist)
- **权限墙(OS 层代码强制)**:MD 二进制 750 服务账户属主,经 sudo 门(参数/路径白名单)执行;绕过编排器的野跑起不来;红队 27/27 零绕过
- **OS-enforced permission wall**: MD binaries are 750 under a service account, reachable only through sudo gates with argument/path whitelists; off-orchestrator runs cannot start; red-team 27/27 no bypass
- **主工作流五段**:物理感知 → 建模 → 动力学(力场/系综/时间/续跑)→ 管线监管(遇错蜂群分布式讨论→决策)→ 分析;**小体系验证成功后才大体系运行**(铁律)
- **Five-stage master workflow**: physics perception → modeling → dynamics (force field / ensemble / time / restart) → pipeline supervision (swarm discussion → decision on errors) → analysis; **small-system validation before any large-system run** (hard rule)
- **双引擎**:LAMMPS/ReaxFF 轨(高温炭化/反应)+ GROMACS 经典力场轨(含水体系),按物理选引擎;建模工具只 packmol(构型打包)与 moltemplate(拓扑组装)
- **Two engines**: LAMMPS/ReaxFF (high-T carbonization / reactions) + GROMACS classical force fields (aqueous systems), routed by physics; modeling tools are exactly packmol (packing) and moltemplate (topology)
- **三种工作模式**:Mode A 巡检(分引擎调对应真相源)/ Mode B 文献复现(read_paper→蜂群拆解→建模→动力学→指标对比)/ Mode C 修复(确诊→蜂群搜证→AI 比选→逐项执行)
- **Three modes**: A inspection / B paper reproduction (read_paper → swarm decomposition → modeling → dynamics → metric comparison) / C repair (diagnosis → swarm evidence → comparison → one-variable-at-a-time execution)
- **自进化闭环(L0-L3)**:外部报错语料(LAMMPS/GROMACS 社区 5570 条有解问答)→ friction 采集 → 蜂群起草提案 → **人工闸(绝不自动批准)** → 证据门 → 经验晋升;**回放考场**(对历史事件重放裁决,绝不重跑物理)给出诚实胜率,毕业五条件审查
- **Self-evolution loop (L0-L3)**: external error corpus (5570 answered LAMMPS/GROMACS community Q&A) → friction capture → swarm-drafted proposals → **human gate (nothing auto-approved)** → evidence gate → promotion; a **replay exam** (replays historical events, never re-runs physics) reports honest win rates before anything graduates
- **记忆纪律**:经验按"验证时的条件"定界(APPLY/DON'T APPLY 例对),未入账(atoms+LEDGER)不算数
- **Memory discipline**: experience bounded by its validation conditions (APPLY/DON'T APPLY pairs); nothing counts without a ledger entry
- **全量记录面**:TOKEN 总账 / 五层决策账 / orchestrator tick 日志 / 工具调用账 / 单任务轨迹页
- **Full recording surface**: token ledger / five-layer decision accounts / orchestrator tick logs / tool-call ledger / per-task trajectory pages

## 架构速览 / Architecture at a glance

```
用户入口(workbench 8080 / headless / cron+微信推送)
  → dsh 对话脑(七条红线人格+记忆纪律,服务端注入)
  → 工具层(只读真相源 MCP ×11 | 唯一审计写门 sim_exec | 通用能力 | 联网参考 | 专科手)
  → agent_deploy_kit v3.3(orchestrator 外环+MONITOR 内环,should-run 每 tick 裁决,人工闸队列)
  → 计算层(LAMMPS mpirun | GROMACS gmx_agent 唯一入口 | SCNet 超算 PIN 闸 | OS 权限墙三门)
  ⇅ L2 进化层(observe cron → friction → 提案 → 证据门 → 沉淀)⇅ L0 外部语料(检索缝,经考场入环)

User entry (workbench 8080 / headless / cron + WeChat push)
  → dsh brain (seven red lines + memory discipline, server-injected)
  → Tool layer (11 read-only truth-source MCP tools | sim_exec single audited write gate | general tools | web | specialists)
  → agent_deploy_kit v3.3 (orchestrator outer loop + MONITOR inner loop, per-tick should-run verdict, human-gate queue)
  → Compute (LAMMPS mpirun | GROMACS via gmx_agent single entry | SCNet HPC PIN gate | OS permission wall)
  ⇅ L2 evolution (observe cron → friction → proposals → evidence gate) ⇅ L0 external corpus (retrieval seam, admitted via the replay exam)
```

详细架构图:看板 `:8080/pages/architecture.html`;任务全流程:`:8080/pages/task-flow.html`。
Detailed diagrams: dashboard `:8080/pages/architecture.html`; full task flow: `:8080/pages/task-flow.html`.

## 本仓库内容 / What's in this repo

- 完整智能体代码:MCP 工具面 / 进化层 / 看板 / dsh 插件 / 人格预设 / 回归测试(python 394 + node 35)
- `external_corpus/`:LAMMPS/GROMACS 社区报错问答语料(**CC BY-NC-SA 4.0**,逐条保留上游 URL/作者署名,见 `external_corpus/README.md`,仅限非商用)
- `tools/archify`:第三方 MIT 上游(图表渲染器,版权见其 LICENSE)
- `evolution/` 证据系统(2026-10 新增,2026-10-21 重构为 v2.2):证据分级 `evidence_grade`(A人工/B论文/C实测/D案例/E经验/F估算)、门禁 `stage_gate`(E1设计有据/E2计算有据/E3物理合理/E4方法学合规/E5结论完整)、状态机 `stage_state`、评估 `assessment`/`deep_review`、对账 `content_check`、配置机检 `config_check`;配套 `mcp/review_experience/` 知识包(物理锚/方法学约束/方法论维度)与 `experiments/evidence_live/` 实弹验收工装(十案例、真实求解器、可重跑)
- 不含:知识库/会话存档/运行凭证(导出时已剔除,见 `SANITIZATION.md`);配套编排器 agent_deploy_kit v3.3 为独立部署(作为 kit 根被引用)

The full agent code base (MCP tool surface / evolution layer / dashboard / dsh plugins / persona presets / regression suites), plus the external LAMMPS/GROMACS error-Q&A corpus (CC BY-NC-SA 4.0, per-record attribution preserved, non-commercial) and the third-party MIT `archify` renderer. **Evidence system (added 2026-10, refactored to v2.2 on 2026-10-21):** evidence grading `evidence_grade` (A human / B literature / C measured / D prior case / E experience / F estimate), decision gates `stage_gate` (E1 design basis / E2 computation basis / E3 physical soundness / E4 methodology compliance / E5 conclusion completeness), task state machine `stage_state`, physics assessment `assessment`/`deep_review`, numeric reconciliation `content_check`, config correctness `config_check`; knowledge packs live in `mcp/review_experience/`; an end-to-end live acceptance harness lives in `experiments/evidence_live/` (ten cases, real solver, rerunnable — see its README). Knowledge bases, session archives and runtime credentials were stripped at export (see `SANITIZATION.md`); the companion orchestrator kit (agent_deploy_kit v3.3) is deployed separately and referenced as the kit root.

## 安装与启动 / Install & run

Ubuntu 22.04/24.04 单用户独立部署新增根目录安装入口，支持模型配置、用户服务与可发送的 kit 代码安装包。完整步骤和验收边界见 [Ubuntu 独立安装与交付](docs/UBUNTU_INSTALL.md)。

```bash
bash install.sh --kit /absolute/path/to/agent_deploy_kit --plan
bash install.sh --kit /absolute/path/to/agent_deploy_kit
```

以下为已准备环境时使用的底层 setup/启动入口；新安装优先使用上面的安装器。

```bash
# 依赖:Node ≥22、pnpm；Ubuntu 新安装使用系统 Python ≥3.10 的独立 venv
# Deps: Node >=22, pnpm; the Ubuntu installer creates a Python >=3.10 venv
bash scripts/setup.sh <agent_deploy_kit 根的绝对路径>
# 环境变量:MDSIM_DATA_ROOT(数据根)、RUN_CASE_BASE(任务扫描树)
# Env: MDSIM_DATA_ROOT (data root), RUN_CASE_BASE (task scan tree)

# 凭证:编辑 <数据根>/dsh-home/.credentials.yaml(由 .example 生成,chmod 600)
# Credentials: edit <data-root>/dsh-home/.credentials.yaml (from .example, chmod 600)

./run-dsh.sh                                # web 对话 / web UI
./run-dsh.sh --profile headless             # 无头单任务(写门 fail-closed)
cd dashboard && DSH_HOME=<数据根>/dsh-home MDSIM_DATA_ROOT=<数据根> python3.11 web_monitor.py 8080   # 看板 / dashboard
```

全新克隆先跑 `setup.sh`(播种 md_config.json 等机器配置)再跑回归;或临时用 `MDSIM_KIT_BASE=<kit根>` 越过配置直接跑测试。详见 `docs/迁移安装指南_UBUNTU.md`。
On a fresh clone run `setup.sh` first (seeds md_config.json etc.), then the regression suites — or point `MDSIM_KIT_BASE=<kit root>` at an existing kit to run tests directly. See `docs/迁移安装指南_UBUNTU.md`.

## Bio remote-compute candidate

独立 Bio/OpenMM 预设与现有节点/租户网页的本地集成源码见
[平台集成](docs/bio-platform-integration.md)与 [Bio 工作流](bio-agent/README.md)。
体系构建、拓扑、MD 与分析在最终 SSH 计算主机执行；没有明确绑定配置时
Bio 默认禁用。当前源码准备与离线工程检查不代表部署或科学验收。

## 质量门(当前全绿)/ Quality gates (all green)

| 门 / Gate | 数量 / Count | 命令 / Command |
|---|---|---|
| dsh python | **394** | `python3.11 -m unittest discover -s tests -p "test_*.py"` |
| dsh node | **35** | `node --test tests/*.mjs` |
| kit python | **240** | (kit 仓内 / inside the kit repo) |

发布纪律:重大变更过多智能体对抗审计(`docs/对抗基线_ADVERSARIAL_BASELINE.md` 为唯一裁决标准),红→修→绿,每修复必带回归测试。
Release discipline: major changes go through multi-agent adversarial audits (`docs/对抗基线_ADVERSARIAL_BASELINE.md` is the sole arbiter), red→fix→green, every fix ships with a regression test.

## 目录地图 / Directory map

```
mcp/          MCP 工具面(md_mcp 11 工具/sim_exec 写门/paths/hpc_submit/model_research)
evolution/    进化层(observe/specialist_swarm/replay_eval/POLICY.json)
dashboard/    看板(web_monitor/gmx_scan/gmx_agent/static+pages 架构图/轨迹/流程图)
push/         定时巡检推送驱动(LAMMPS+GROMACS 检测,野跑闸,微信 Server酱)
dsh-plugins/  dsh 插件(md-flow-gate/md-friction/auto-approve/ops-gate/web-search-pro…)
presets/      人格预设(七条红线+记忆纪律,web 与 headless 逐字一致)
external_corpus/  L0 外部报错语料(LAMMPS/GROMACS 问答+检索缝 corpus_lookup)
scripts/      setup.sh 等安装迁移脚本
docs/         交付标准/对抗基线/设计思路/迁移指南
tests/        python 394 + node 35 回归
tools/        分析建模小工具(archify 等)
```

## 红线摘要 / Red lines (summary; full text in presets & baseline)

1. 模拟数字只信只读真相源返回——网页/模型记忆不算数 / Simulation numbers come only from read-only truth-source tools — web pages and model memory don't count
2. 力场文件(`ffield.reax`)永不改 / Force-field files (`ffield.reax`) are never modified
3. 高危/花钱/外部写操作一律人工闸(代码强制,非提示词)/ High-risk / spending / external-write operations always hit a code-enforced human gate
4. 不走编排器不能跑:LAMMPS 经编排器 adopt,GROMACS 经 gmx_agent.py 启动即接管 / No run outside the orchestrator: LAMMPS via orchestrator adoption, GROMACS spawned-and-adopted by gmx_agent.py (and the OS wall enforces it)
5. 指标偏离如实标注转失效样本,不许润色 / Metric deviations are marked as invalidated samples, never polished
6. 修复铁律:一次一变量、先备份、每项判【成/败/不确定】、修复日志记一笔 / Repair discipline: one variable at a time, backup first, verdict per step, logged
7. 分层自主:四条件全满足才自主,否则出建议书等拍板 / Layered autonomy: full autonomy only when all four conditions hold, otherwise a decision memo awaits approval

## 已知限制 / Known limitations

- node ≥ 22(run-dsh.sh 自动探测);pnpm、python3.11 / node ≥ 22, pnpm, python3.11
- 需配套 agent_deploy_kit(v3.3 专科脚本,作为 kit 根被引用,本仓库不含)/ Requires the companion agent_deploy_kit (v3.3 specialists, referenced as the kit root; not in this repo)
- headless 无审批应答者:sim_exec 写门 fail-closed(安全兜底)/ headless has no approval responder: the sim_exec write gate fails closed
- 本机 ReaxFF 液态水 300 K 不可信(实测 665-705 K)——含水生产强制 GROMACS 经典轨 / Local ReaxFF liquid water at 300 K is untrustworthy (measured 665-705 K) — aqueous production runs are forced onto the GROMACS classical track
- moltemplate 未随机预装(纯输入构建器,不执行 MD)/ moltemplate is not preinstalled (pure input builder, executes no MD)
- SCNet 真机作业:批准 PIN 需在真终端设置(`approve --set-pin`)/ SCNet jobs: the approval PIN must be set from a real TTY (`approve --set-pin`)
- lockfile 因发布脱敏做过文本编辑(npm scope 改名),使用前重新 `pnpm install` / lockfiles were text-edited during export sanitization (npm-scope rename) — re-run `pnpm install` before use
