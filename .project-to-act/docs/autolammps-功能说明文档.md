# autolammps（agent-aura CLI）完整功能说明文档

> **用途**：后续软件开发的参照文档。覆盖 autolammps 仓库全部子系统的功能、接口、数据格式、命令面与已知缺口。
> **来源**：GitHub `redmaplewww/autolammps`（私有）main 分支，commit `feaf7f6`（2026-06-20），2026-10-15 克隆至本工作区 `autolammps/`。
> **方法**：四路并行源码深扫（工作流编排 / 知识系统 / 平台底座 / 工程设施）+ 关键事实人工复核。文中路径均相对仓库根 `autolammps/`。
> **纪律提醒**：按 D-004 口径，本仓库属专有域库——后续二开只借**机制**（架构、状态机、门禁设计、打分算法骨架），不迁移任何专有规则内容（CL 条目、案例数据、代理提示词原文等）。

---

## 1. 产品定位与总体架构

### 1.1 仓库身份

| 项 | 值 |
|---|---|
| 包名 / 版本 | `agent-aura-cli` v1.8.0（bin：`aura` / `aura-bun` / `agent-aura`） |
| 定位 | 基于 Bun + TypeScript 的终端 AI 编码助手，内置多 Agent 协作系统、MCP 协议支持、LAMMPS 模拟工作流知识库 |
| 技术栈 | Bun ≥1.2（构建/运行）+ Node ≥18（产物运行）；TypeScript；React 19 + Ink fork（终端 UI）；SQLite（bun:sqlite，知识索引）；Commander（CLI）；zod（schema）；Biome（lint） |
| 出身 | Claude Code 深度 fork（内部代号 Angsheng，配置目录 `~/.angsheng`，环境变量沿用 `CLAUDE_CODE_*`），其上叠加 LAMMPS 领域层 |
| 工作区 | monorepo：`packages/*`、`packages/@ant/*`、`packages/@anthropic-ai/*` |
| 规模 | 约 3900 文件；src/ 约 2800 个 TS/TSX（utils 849、components 606、commands 279、services 277、hooks 156、cli 137） |

### 1.2 五层架构总览

```
┌─ ① CLI 包装层  src/entrypoints/lammps-cli.ts —— bun run lammps 总入口（子命令路由/复杂度分级/会话日志）
├─ ② 运行时脚本层 scripts/lammps-*.ts ×20 —— 确定性工具（执行/修复环/建模/校验/评测），零 LLM
├─ ③ 代理编排层  .angsheng/{agents,skills,templates} —— 14 个代理定义 + WF-00~WF-05 状态机 + 审查门 + 模板库
├─ ④ 知识层     knowledge/（Markdown 分层账本）+ src/utils/lammpsKnowledge/（SQLite FTS 检索）
│               + src/utils/lammpsKbPipeline/（入库-评审-晋升流水线）
└─ ⑤ 引擎服务层 src/lammps-engine/ —— HTTP 引擎(3847) + MCP stdio 桥（骨架级）
        ↓ 依托
   平台底座（§4）：QueryEngine 代理循环 / 56 内置工具 / 权限模型 / Ink UI / hooks / 插件 / MCP / 多 provider
```

### 1.3 顶层目录地图

| 目录 | 内容 |
|---|---|
| `src/` | 核心源码（代理循环、命令、组件、服务、hooks、入口） |
| `packages/` | 工作区包：Ink fork（@ant/ink）、model-provider、builtin-tools、agent-tools、mcp-client、weixin、remote-control-server、acp-link、5 个 native addon 等 14 个 |
| `knowledge/` | LAMMPS 知识库（cases 118 / memory 11 / papers 10 / reports 23 / rules 19 / templates 7，共 187 文件） |
| `.angsheng/` | Agent 配置：agents（14）/ skills（9）/ templates（66）/ settings / cache（预构建 SQLite 索引 gz 分卷） |
| `skills/lammps-agent-workflow/` | 可移植工作流技能包（模板 + 知识胶囊 + 测试夹具） |
| `scripts/` | 36 个脚本：lammps-* 20 个 + 构建/部署/评测工具 |
| `docs/` | agent-workflow-overview（对外简版架构）、visual-agent-workflow-plan（桌面可视化方案）、implementation、tests（基准报告） |
| `vendor/` | audio-capture 原生插件占位（main 分支已剔除二进制，仅留管线支持） |
| `tests/` | 单测 mock |

### 1.4 命令入口速查

npm scripts（package.json）：

| 命令 | 入口 | 说明 |
|---|---|---|
| `bun run lammps` | `src/entrypoints/lammps-cli.ts` | LAMMPS CLI 总入口（§2.2） |
| `bun run lammps:init-state` | `scripts/lammps-project-state-init.ts` | 初始化 `.lammps-project/` 状态包 |
| `bun run lammps:log-sections` | `scripts/lammps-log-sections.ts` | 大日志分段读取 |
| `bun run lammps:maintain-knowledge` | `scripts/lammps-knowledge-maintenance.ts` | 知识库只读维护报告 |
| `bun run rebuild-knowledge` | `src/utils/lammpsKnowledge/indexer.js` | 全量重建知识索引 |
| `bun run engine:run` / `engine:mcp` | `src/lammps-engine/{entrypoint,mcp-bridge}.ts` | 引擎服务器 / MCP 桥 |
| `bun run dashboard` | `src/entrypoints/dashboard.ts` | Web 仪表板（3760 端口） |
| `bun run rcs` | `scripts/rcs.ts` | 远控服务器（3000 端口） |
| `bun run test:lammps-cli` | `scripts/lammps-cli-test.ts` | LAMMPS CLI 冒烟 11 项 |
| `bun run build` / `build:vite` / `health` / `typecheck` / `test` / `lint` | — | 常规工程命令（§5） |

主 CLI（平台底座）入口：`src/main.tsx`（Commander 全量选项）→ `src/entrypoints/cli.tsx`（分派 print/REPL/MCP）；`--lammps-knowledge-mcp` / `--lammps-kb-pipeline-mcp` 两个隐藏入口以 stdio 方式把知识检索/KB 流水线暴露为内置 MCP server（这是代理 frontmatter `mcpServers:` 声明的来源）。

---

## 2. LAMMPS 多代理工作流子系统（领域层）

### 2.1 工作流状态机（核心）

权威定义在 `knowledge/rules/workflow-stages.md`（V3 Team-Aware）：

```
WF-00 方案设计(architect) ──门──► WF-R 物理合理性(reasoner, advisory 不阻塞)
      ► WF-01 建模(input-writer) ──门──► WF-02 势配置(input-writer) ──门──► WF-03A 输入脚本(input-writer) ──门──►
      │                                                                  ↘ WF-03B 可选 HPC 分支（lammps-hpc-submit skill → hpc-config.json，无门）
      ► execution（脚本执行）→ 失败修复环（auto-repair→repair-loop→[analyst→reasoner]→回滚 WF-01/02/03A）
      → WF-04 数据分析(data-analyst) → WF-05 可视化(post-processor, 可选)
```

- 四个强制门：WF-00 / WF-01 / WF-02 / WF-03A，全部由 `lammps-reviewer` 把关。
- 门禁三值结论：`PASS`（放行）/ `REVISE`（回生产者做**有界**修复，附 `required_fixes` 清单）/ `BLOCKED`（上游问题、强制检查失败、缺工件/缺证据、需用户决策）。
- **3x REVISE 熔断**：同阶段连续 3 次 REVISE → `blocked-by-review-loop`（coordinator 提示词 L129/L165 + state.md 的 REVISE Count 表跟踪）。
- 回滚目标推断（error-summary.ts:72-80 + repair-loop.ts:148-150）：文件缺失/输入语法错 → WF-03A；运行不稳/数值失败 → WF-02；更深设计级问题由 analyst/reasoner 判断。
- MVP 政策：分析-only 请求可直接进 WF-04，但 coordinator 必须显式声明进入部分流程。

### 2.2 LAMMPS CLI（`src/entrypoints/lammps-cli.ts`，约 1000 行）

`bun run lammps` 总入口。把请求分发到子代理（经主 CLI `-p --agent <name>` 模式）或确定性脚本：

| 子命令 | 目标 | 说明 |
|---|---|---|
| `scheme` / `architect` | 代理 lammps-simulation-architect | WF-00 方案设计（effort=high） |
| `reason` | 代理 lammps-simulation-reasoner | WF-R 物理合理性审查（advisory） |
| `build` / `write` | 代理 lammps-input-writer | 一次性输入生成 |
| `review` | 代理 lammps-reviewer | 一次性审查 |
| `analyze` / `debug` | 代理 lammps-data-analyst | 一次性分析/失败排查 |
| `lookup` | 脚本 lammps-lookup.ts | 本地证据优先知识问答（**绕过代理环**） |
| `curate` / `learn` | 代理 lammps-kb-coordinator | 知识入库 |
| `model` / `model-render` / `model-run` / `model-check` | 脚本 ×4 | WF-01 建模路由 / Atomsk 渲染 / 执行 / 结构校验 |
| `execute` | 脚本 lammps-execute.ts | 运行/干跑 LAMMPS |
| `repair` / `loop` / `summarize-error` | 脚本 ×3 | 结果分类 / 修复环规划 / 错误摘要回写 |
| `plot` / `postproc` | 代理 lammps-post-processor | WF-05 图表 |
| `team [prompt]` / `team destroy` | 主 CLI + `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` | 团队模式（team-lead=lammps-coordinator） |
| （无子命令） | `routePrompt()` 关键词正则自动路由（L343-403） | 9 类代理 |
| （交互模式） | `--agent lammps-coordinator` | 默认 |

横切机制：

- **任务复杂度分类** `classifyTaskComplexity`（L517-559）：正则信号分 `simple|complex`；execute/repair/loop/model* 强制 complex。注入 `[Task Complexity: …]` 前导，simple 指示"只查最小证据、必要时自我升级"。
- **effort 选择**（L586-600）：complex→medium；scheme/reason→high；默认 low。
- **进度纪律前导**（L482-492）：强制 todo + 固定 stage 标签（Read references → Inspect case → Plan → Build/repair → Run → Analyze → Write report）+ "失败两次必须换策略"。
- **默认权限**：无显式权限 flag 自动加 `--dangerously-skip-permissions`。
- **会话日志**（L63-134）：在会话工作目录维护 `.lammps-project/work-log.md`，每会话写 `## S-YYYYMMDD-HHMMSS` 块（mode/subcommand/agent/cwd/prompt_summary/complexity + `<!-- work_summary placeholder -->`），结束时补 timestamp_end/exit_code，代理回填 Work Summary。
- **`--debug-routing`**：stderr 输出 `{mode, subcommand, agent, complexity, effort}` JSON。

### 2.3 代理角色（`.angsheng/agents/`，14 个）

每个代理 = YAML frontmatter（name/description/model/effort/color/permissionMode/maxTurns/mcpServers/tools/disallowedTools）+ Markdown 系统提示词。与对外文档 docs/agent-workflow-overview.md 的角色表一一对应：

| 代理 | 职责 | 关键设定 |
|---|---|---|
| **lammps-coordinator**（375 行） | 纯委派协调者/项目 owner | model=sonnet effort=low maxTurns=40；**绝对规则**：所有技术工作必须委派、禁读 scratchpad/原始技术文件、只读 `project-memory/{init,state,decisions,review-log}.md`、每阶段后更新项目记忆、阶段报告 ≤300 token；路由表 13 类任务；**Team Mode 协议**（TeamCreate("lammps")/TaskCreate+blockedBy 门同步/shutdown 握手）；**workflowStyle 四模式**（guided/semi/auto+错误恢复，auto 永不问用户、REVISE 自动修复至多 3 次） |
| **lammps-simulation-architect**（257 行） | WF-00 方案设计 | effort=high；**D1-D7 七决策表**：D1 材料/D2 研究目标（D2.1 必备特征清单 required_features）/D3 热力学/D4 边界尺寸/D5 加载/D6 势家族/D7 量化验收标准；证据要求：D1/D2/D4 ≥1 本地案例、D3/D5/D7 ≥1 文献、D6 ≥1 案例或文献；产出 `work/cases/<slug>/SIMULATION_SCHEME.md`，重大修订快照 `versions/vNNN-<label>/` |
| **lammps-simulation-reasoner**（243 行） | WF-R 物理合理性顾问（**advisory 不阻塞**） | 对 D1-D7 逐项对照文献评估；产出 `reasoner-assessment.md`（status: physically-sound / concerns-identified / significant-risks；`design_root_cause` 布尔；`proceed_to_next_stage: recommended|caution|not-recommended`）；修复环中判"设计缺陷 vs 技术错误" |
| **lammps-input-writer**（210 行） | WF-01/02/03A 生产者 | 强制前置阅读 workflow-stages / input-template-families / input-self-check / core-checks；**Goal-Aware Writing**：从 scheme 提取 D2.1 特征逐一确认实现，关键特征无法实现 → BLOCKED 而非静默跳过；**WF-03A 诊断 compute 要求**（pe/atom+coord/atom 最低，FCC/BCC 加 centro/atom，多相/变形加 cna/atom）；目录纪律 `inputs/{structures,potentials,scripts,raw}` |
| **lammps-reviewer**（211 行） | 四门把关 | **强制检查 MB-001~MB-008**（见 §2.4）；强制输出 decision/mandatory_check_results(带证据)/manual_refs/case_refs/confidence/required_next_actor；高风险双证据；布局审查（乱放→REVISE，版本混杂→BLOCKED）；结论追加 review-log.md，阻塞项进 open-issues.md |
| **lammps-data-analyst**（223 行） | WF-04 分析 | 解析 log.lammps → 提取指标（弹性模量/屈服强度/失效应变）→ D7 验证 → 文献对比（偏差 >2x 标记）→ **analysis-report.json**（metrics / d7_validation{criterion, computed_value, status: met|not_met|borderline, deviation} / literature_comparison / rollback_recommendation{recommended, target: WF-01|02|03A|null} / wf05_trigger{generate_plots, quantities_to_plot, ovito_scenes, figure_output_dir}）；大日志用 log-sections 分段读 |
| **lammps-post-processor**（224 行） | WF-05 可视化（独立代理，V2） | **工具边界铁律**：matplotlib 只画曲线、OVITO 画一切含原子图；8 步 pre-flight；仿真类型→必选图集映射；**post-processing-manifest.json**（figures[]{id/type: T-01/tool/path/status: generated|script_only|skipped|failed/file_size_bytes/…}）；生成后校验（存在、>1KB、matplotlib ≥800×600、OVITO ≥1280×720）；log 提取规则（应变=(Lx-Lx0)/Lx0，应力=-(Pxx+Pyy+Pzz)/3/10000 bar→GPa）；OVITO 必须用 templates/ovito/render_engine.py |
| **lammps-case-librarian**（145 行） | 检索专家（只读） | 先 MCP `search_lammps_knowledge`，索引缺失先 `index_lammps_knowledge` 再搜；把检索结果的 queryType/answerStrategy/answerChecklist/answerTemplate/evidenceLines 当指令执行；结论先行 |
| **lammps-paper-researcher**（113 行） | 文献检索 | exa（发现）+ semanticscholar（元数据/PDF 全文提取）；证据分级 pdf/full-text > publisher page > abstract-only；回写 knowledge/papers/ |
| **lammps-kb-coordinator**（109 行） | KB 流水线顶层协调 | 只用隔离 MCP `lammps-kb-pipeline`；ingest → curator → reviewer → apply 晋升 → 报告；无意义内容进隔离区而非硬删 |
| **lammps-kb-curator**（83 行） | 知识候选整理 | 产出最小可复用知识单元（situation→risk/mistake→fix/check）；类型→目的地映射（experience→confirmed-lessons、rule→learned-rules、correction→reference-corpus、case_note→cases/notes、qa/template_snippet→templates/answers、potential_note→potentials）；合并模式 append/replace/new_entry + mergeTarget（如 CL-001） |
| **lammps-kb-reviewer**（64 行） | KB 终审 | 三值决策 confirmed/candidate/quarantined + 7 条评审标准 |
| lammps-analyst（74 行，遗留） | 旧版分析代理 | data-analyst 前身，保留 |
| hello-agent | 测试用 | — |

所有代理共同约定：**RESULT 块强格式**（`## RESULT`(decision/summary/artifacts/issues/confidence) + `## LOG`(key_decisions/errors_fixed) + `## NEXT`(recommended_action)，超约 2000 token 截断）、Team Mode 生命周期（shutdown_request→approve）、Work Summary 回填 work-log.md。

### 2.4 证据系统（Evidence System，审查门禁的核心机制）

> 设计原则（evidence-gate.md 开篇明义）：**reviewer 门不能只靠指令约束——模型可能忘记执行证据规则、也可能把弱审查标成 PASS，因此尽可能使用机械门（mechanical gate）**。整套系统由"规则文件 + 审查工件 schema + 机械校验 + coordinator 验收约束"四层组成。

#### 2.4.1 组成部件与文件定位

| 部件 | 文件 | 作用 |
|---|---|---|
| 证据规则 | `knowledge/rules/review-guidelines.md` | 风险三级处理方式、审查重点、双证据规则、语义命令强制证据、证据记录格式 |
| 强制检查表 | `knowledge/rules/mandatory-checks.md` | MB-001~MB-007 逐条定义（触发条件/检查点/违规处理/证据来源），任何匹配即 BLOCKED |
| 机械门设计 | `skills/lammps-agent-workflow/knowledge-capsule/evidence-gate.md` | 三层验证设计 + 8 条拒绝 PASS 情形 + 人工回退模式 |
| 审查工件模板 | `skills/lammps-agent-workflow/templates/review-result.json` | reviewer 结论的强制 JSON 结构 |
| 证据记录模板 | `skills/lammps-agent-workflow/templates/evidence-record.json` | 单条证据的最小格式 |
| 示例工件 | `templates/review-result.example.json`（REVISE 例）、`review-result.high-risk-pass.example.json`（高风险双证据 PASS 例，含 lineNumber/verified 字段演示） | 正反示例 |
| 执行者提示词 | `.angsheng/agents/lammps-reviewer.md`（MB 检查流程 L33-53、强制字段 L75-97）、`lammps-coordinator.md`（验收约束） | 把上述规则注入代理行为 |
| 验收协议 | `knowledge/rules/workflow-handoffs.md` §四 | coordinator 的 `/evidence-validate` 强制步骤与 fail-closed 语义 |
| 代码层入口（设计） | `src/commands/evidence-validate.ts`、`evidence-check.ts`、`src/evidence/evidence-gate.ts`、`src/evidence/evidence-schema.ts` | **均为设计引用，实际不存在或为 stub**（见 2.4.9） |

#### 2.4.2 证据记录格式（evidence-record.json）

```json
{
  "type": "manual|case|correction|literature|memory|artifact",
  "source": "path or URL",
  "excerpt": "short optional quote",
  "lineNumber": 1,
  "verified": true
}
```

六类证据：manual（LAMMPS 官方手册）、case（本地案例）、correction（纠错语料）、literature（文献）、memory（知识库教训条目）、artifact（工件）。`verified` 标记是否实际核验过，`lineNumber`+`excerpt` 支持精确回溯。

#### 2.4.3 审查结论工件（review-result.json 全字段）

reviewer 必须产出 JSON（coordinator **只接受 JSON，不接受自由文本段落**），推荐路径 `work/cases/<case-slug>/.lammps-project/reviews/<stage>.json`：

```json
{
  "stage": "WF-00|WF-01|WF-02|WF-03A",
  "decision": "PASS|REVISE|BLOCKED",
  "scope": "",
  "risk_level": "low|medium|high",
  "mandatory_check_results": {
    "MB-001": { "triggered": false },
    "MB-002": { "triggered": true, "passed": true,
                "evidence": [{ "type": "case", "source": "…", "verified": true }] },
    "MB-003": { "triggered": true, "passed": false, "issue": "…", "location": "line 36-37" }
  },
  "semantic_commands_touched": [],
  "manual_refs": [],      // manual/correction 证据记录数组
  "case_refs": [],        // 本地 case/memory 证据记录数组
  "memory_refs": [], "artifact_refs": [],
  "fatal_issues": [], "major_issues": [],
  "required_fixes": [],   // REVISE 时给 writer 的有界修复清单
  "acceptance_checks": [],
  "confidence": "high|medium|low",
  "required_next_actor": "lammps-input-writer|lammps-coordinator|blocked-user-decision",
  "summary": ""
}
```

#### 2.4.4 风险分级与证据要求（review-guidelines.md）

**任务风险三级**：
- 低风险（单元素/简单平衡/完全复用已验证模板只改路径步数输出名）→ 快速自检后交付；
- 中风险（多元素标准流程/已知案例有限修改/常规熔凝拉伸剪切）→ 必须自审，存在明显不确定项交 reviewer；
- 高风险（ReaxFF/COMB3/复杂反应力场/新势函数首次使用/复杂多步 workflow/电化学氧化还原沉积耦合/用户明确要求审查）→ 必须过 reviewer。

**双证据规则**：高风险结论必须同时有 ≥1 条 manual/correction 证据 **加** ≥1 条本地 case/memory 证据。

**语义命令强制证据（独立叠加）**：writer 修改以下任一命令时，reviewer 必须引用至少一条 manual 或 correction 证据，**不论风险等级**：`pair_style` / `pair_coeff` / `fix` / `compute` / `delete_atoms` / `atom_style` / `boundary` / `kspace_style` / `timestep`。低/中风险任务触发语义命令只需满足本条；高风险任务则本条与双证据**同时**满足。

**高风险修正上限**：高风险任务最多连续自动修正 3 轮，之后回到用户决策（与 3x REVISE 熔断同源）。

**大文件证据纪律**：超长输入不整体分析，超大日志只抓关键/错误/末尾区段，且分析结果必须说明依据的是哪些区段。

#### 2.4.5 MB 强制检查（mandatory-checks.md 完整表）

任何匹配即 **BLOCKED**，此文件必须始终在 reviewer 审查上下文中。审查前固定流程：读本文档 → 对每个 MB 判断触发 → 触发则执行检查点 → 违规记录并立即 BLOCKED → 全过才继续常规审查。

| ID | 规则（来源） | 触发条件 | 检查点要点 | 证据来源 |
|---|---|---|---|---|
| MB-001 | 工程应变 L0 冻结（CL-007） | tensile/deformation + `fix deform` + NPT 切换 | NPT→deform 转换点用 `${L0}` 立即求值冻结；应变变量引用冻结 L0；禁止 bare `lx`/动态 equal-style 变量追踪应变 | confirmed-lessons |
| MB-002 | 固定原子不得施加初始速度（CL-003，safety-critical） | fixed/frozen/constraint 组 + `velocity` 命令 | velocity 作用域排除固定组；`velocity all` 即违规 | corrections/reference-corpus + confirmed-lessons |
| MB-003 | fix npt 不得与 fix deform 同方向竞争（CL-011） | `fix npt` + `fix deform` 同时存在 | npt 的 couple 参数排除变形方向（safe pattern：npt 仅耦合非变形方向） | cases/raw/拉伸/NPT 案例 + confirmed-lessons |
| MB-004 | ReaxFF 必须有电荷处理（CL-001） | `pair_style reaxff`/`reax/c` | atom_style charge/full + `fix qeq/reaxff` 或 `qeq/shielded`；注意 qeq/point、qeq/dynamic、qeq/fire 是点电荷模型**不适用** | potential-selection + NiC/GaN 案例 + LAMMPS 官方文档 |
| MB-005 | fix print 中 equal-style 变量必须 `$(v_varname)`（PL-008） | 任何 `fix print` | 禁 bare `v_varname`（静默产字面文本）；`${varname}` 除非本地手册验证否则不批 | pending-lessons(PL-008) + confirmed-lessons |
| MB-006 | ReaxFF pair_coeff 元素名与 data atom type 一致（CL-010） | 任何 `pair_coeff`（尤其 ReaxFF） | 元素名列表 ↔ data Masses 顺序 ↔ ffield 文件三方交叉验证；不匹配是静默错误 | potential-selection + AlCuO/NiC 案例 + confirmed-lessons |
| MB-007 | restart/data 来源必须验证（CL-006） | `read_restart` 或 data 续算 | 记录来源与生成时间；验证 box 维度一致；特别关注多层/含真空模型 | confirmed-lessons |

reviewer 提示词额外定义两条（**未落进规则文件**）：
- **MB-008 目标-模型一致性**（WF-01 审查）：读 SIMULATION_SCHEME.md 提取 D2.1 必备特征表 → 无 D2.1 表即 REVISE（"Missing D2.1 required_features"）→ 逐项核对建成的模型是否包含关键特征，关键特征缺失 → BLOCKED；依赖 `knowledge/rules/goal-feature-registry.md`（不存在）。
- **MB-009 诊断 compute**（WF-03A 审查）：检查 pe/atom、coord/atom 等诊断 compute 是否包含、dump 频率是否合适；依赖 `knowledge/rules/diagnostic-compute-requirements.md`（不存在）。

**非上诉清单**（非 BLOCKED 但应记录）：CL-008 写高风险输入前必须先搜本地案例（REVISE）、CL-004 delete_atoms overlap 窄阈值（REVISE）、CL-005 完成≠成功需验收标准（REVISE）、CL-009 沉积 deposit region 必须重算（MAJOR）、CL-012 NPT→deform 前跑诊断热力学检查点（REVISE）。

#### 2.4.6 三层机械验证（evidence-gate.md）

- **Layer 1 Schema**：按 review-result.schema.json 校验必填字段与枚举值。
- **Layer 2 LAMMPS 门逻辑——拒绝 PASS 的 8 种情形**：
  1. MB-001~MB-007 任一键缺失；
  2. 任何 triggered 的 MB 检查无证据；
  3. 任何 triggered 的 MB 检查 `passed: false`；
  4. 高风险审查缺 manual/correction 证据；
  5. 高风险审查缺 case/memory/artifact 证据；
  6. 触碰语义命令但无 manual/correction 证据；
  7. 本地证据路径在磁盘上不存在（**所有证据 source 路径必须可达**）；
  8. `confidence` 缺失或不在 high|medium|low。
- **Layer 3 coordinator 验收**：仅当 ①decision=PASS 且 ②机械校验通过 且 ③审查工件路径记入 review-log.md 且 ④阶段状态更新进 state.md，才允许推进。
- **人工回退模式**：无脚本可跑时 coordinator 亲自读 JSON 手工执行 Layer 2；此模式下最终 review confidence 上限 **medium**，除非所有证据路径被独立核验。

#### 2.4.7 coordinator 强制约束（workflow-handoffs.md §四）

- **每次 reviewer 输出后必须跑 `/evidence-validate <stage>.json` 验证证据合规性（设计上由 `src/evidence/evidence-gate.ts` 实现）；证据校验失败视同 BLOCKED，不得推进到下一阶段**——明确的 fail-closed 语义。
- reviewer 返回 REVISE 不得当作已交付；BLOCKED 必须停止推进回到用户决策或上一步修复。
- **自治推进模式**（用户授权"你自己决定/自动推进"后）：仍必须遵守相同阶段顺序与门；每个可审查阶段按 writer → reviewer → REVISE 则 writer 修复 → reviewer 复审 → 仅 PASS 后进入下一阶段；coordinator 调 writer 前冻结本阶段核心设定，reviewer 要求修改须记录 `decision revision`；writer 声称产物已创建时送审前确认产物实际存在；**同阶段连续 3 次 REVISE 停止自动推进并报 `blocked-by-review-loop`**；每次汇总写明阶段/已调代理/状态/下一步。

#### 2.4.8 与知识库的联动

- **CL→MB 的派生关系**：mandatory-checks.md 自述"从 confirmed-lessons.md 中提取 BLOCKED 级别强制规则"——MB 表是知识账本的守门投影，教训晋升（§3.4）会反过来扩充检查面。
- reviewer 的分层读取：必读 core-checks.md（CL-001..019 紧凑索引）+ mandatory-checks.md；CL 详情仅在触发或需要证据时经 MCP `search_lammps_knowledge` 检索，MCP 不可用才按 CL ID 窄读文件——这是"分层知识+证据门"的接合点，也是 reviewer 上下文 -73% 优化（§5.5）的组成部分。
- MCP 工具 `verify_script`（§3.8）的设计意图即"CL-001~CL-019 触发式脚本校验"——把 MB 检查做成可编程服务（当前为 stub）。

#### 2.4.9 实现状态对照（设计 vs 代码）

| 设计承诺 | 代码现状 |
|---|---|
| `/evidence-validate <stage>.json` 代码层校验 | `src/commands/evidence-validate.ts` 为 stub（返回 "not implemented"）；`evidence-check.ts` 同为 stub |
| `src/evidence/evidence-schema.ts` ReviewResult Schema | 文件不存在 |
| `src/evidence/evidence-gate.ts` 校验实现 | 文件不存在（workflow-handoffs.md 引用） |
| 代码级 EvidenceValidator 拒绝不合规输出（reviewer 提示词 L75 断言"will reject it"） | 无此代码；实际约束完全依赖代理提示词遵守 JSON 格式 + coordinator 人工执行 Layer 2 |
| skills 包 `scripts/validate-review-result.js` + `templates/review-result.schema.json` | 仓库中不存在（仅文档提及） |
| Layer 1/2 机械校验 | **全部停留在文档层**；真正可执行的是提示词纪律 + 模板 schema + 人工回退 |

即：这套证据系统的**规则与 schema 设计是完整的**（风险分级/双证据/语义命令清单/MB 表/三层验证/8 条拒绝情形/fail-closed 验收/人工回退降级），但**机械执行层一行都没落地**。这正是 MDagent 侧证据系统（F-009，R1-R19 已代码化、fail-closed 已实现）对它的补齐方向——参照时应取其规则设计，执行架构以 MDagent 实现为准。

### 2.5 交接包与项目状态（持久化布局）

**双轨状态**：

**Standalone 模式**（默认）—— `.lammps-project/`（推荐放 `work/cases/<case-slug>/.lammps-project/`）：

| 文件 | 职责 | 维护者 |
|---|---|---|
| `state.md` | 主跟踪：current_stage/status/last_action/next_action/**REVISE Count 表**/locked_decisions(D1-D7)/Open Issues | coordinator |
| `project.json` | 结构化状态（legacy，脚本用） | init-state 脚本 |
| `decisions.md` | D1-D7 决策日志表 | coordinator |
| `review-log.md` | 审查历史（Date/Stage/Decision/Artifact/Issues/Confidence） | reviewer |
| `open-issues.md` | 阻塞项（severity/owner/next_action） | reviewer + error-summary 脚本 |
| `stage-summary.md` | 阶段摘要 + Error Summary 块 | error-summary 脚本 |
| `error-summary.json` | 最近错误摘要（status/rollback_target/selected_actor/issues/suggested_fixes/confidence） | summarize-error 脚本 |
| `execution.json` / `modeling.json` | LAMMPS/Atomsk 命令配置（local/linux/windows/hpc 段；hpc 默认 `mpirun -np 4` + `lmp`） | 用户/脚本 |
| `wf01/wf02/wf03a.packet.json` | 阶段交接包（见下） | model-route 脚本 / input-writer |
| `work-log.md` | append-only 会话日志 | CLI 包装器 + 代理 |
| `runs/<runId>.*` | 运行记录链（见 §2.6） | 执行/修复脚本 |
| `modeling/<runId>/` | Atomsk 渲染脚本 + render.manifest.json | model-render/run |
| `structure-validation.json` | 结构校验结果 | model-check |
| `reviews/<stage>.review.json` | 审查 JSON（推荐路径） | reviewer |

**Team 模式** —— `scratchpad/<stage>/`（wf00/wfr/wf01/wf02/wf03a/wf04/wf05/review/librarian/papers，**coordinator 禁读**）+ `project-memory/`（coordinator 的 state/decisions/review-log/work-log）+ 任务列表（TaskCreate blockedBy 实现门同步）。

**交接包（handoff packet）schema 要点**：
- `wf01.packet.json`：decision_context{D1, D4} / structure_provenance{route: build|convert|reuse|user-provided, source_files, generated_files, atom_count, box, atom_type_mapping, verified} / validation{checks_performed, known_risks, open_questions} / evidence_refs / handoff_to_wf02{structure_file, required_potential_coverage, notes} / review_status。
- `wf02.packet.json`：decision_context{D6} / selected_potential{family, pair_style, potential_files, pair_coeff, atom_style_requirement, element_mapping} / potential_candidates / known_risks / fallback_options / review_focus(4 项) / review_status。
- `wf03a.packet.json`：input_files / structure_inputs / potential_inputs / source_examples / manual_refs / assumptions / risk_points / self_check_passed_items / thermo_outputs / dump_outputs / restart_plan / d7_support{criteria_supported, missing_outputs} / rollback_targets[WF-02, WF-01] / handoff_to_execute / review_focus / review_status。

**案例产物布局**（README 推荐）：`work/cases/<case-slug>/` 下 `SIMULATION_SCHEME.md`（根级）、`in.*.lmp`、`inputs/{structures,potentials,scripts,raw}/`、`outputs/`、`reports/`、`figures/`、`manifests/`、`versions/vNNN-<label>/`、`reasoner-assessment.md`。

**可恢复性**：文件即状态（所有阶段产物、审查结论、运行记录均为磁盘工件）；运行记录链三级脚本各自"取最新匹配后缀文件"自动接续，无需显式传参；work-log 每 session 块的 next_step 字段供下一会话恢复。

### 2.6 执行与修复循环（脚本链）

```
lammps execute ──► runs/<runId>.{json,stdout.txt,stderr.txt} + log.<input>.<runId>.lammps
      │ (exit_code≠0 或需分析)
      ▼
lammps repair ──► <runId>.repair.json（错误签名分类 + 建议_actor）
      ▼
lammps loop ──► <runId>.next-step.json（next_mode + selected_actor + rollback_target + bounded_task_prompt）
      ▼
coordinator 路由（V2 设计：loop 只产出建议，不直接调代理）
      ▼
lammps summarize-error ──► error-summary.json + stage-summary.md 追加 + open-issues.md 追加
```

- **lammps-execute.ts**：LAMMPS 执行器。命令解析优先级：CLI/env `LAMMPS_COMMAND` > hpc 配置 > `.lammps-project/execution.json` 平台段 > PATH 回退探测（win: lmp.exe/lmp/lammps.exe/lammps）。无可执行文件自动 **dry-run 模式**（只验证接口，run 元数据记 `dry_run: true, executable_available: false`）。
- **lammps-auto-repair.ts**：按 stderr+stdout+log 关键词签名分类：`dry_run_only / completed(→data-analyst + wf05_trigger) / launch_failed / missing_artifact("cannot open","no such file"→input-writer) / input_syntax_failure("unknown command","illegal"→reviewer) / runtime_instability("lost atoms"→analyst) / bonding_failure("bond atoms missing"→analyst) / numerical_failure("non-numeric"→analyst) / runtime_failed / unknown`；附 log_signals（warnings/errors/thermo/loop-time-present）。
- **lammps-repair-loop.ts**：按 run_status 产出 next_mode：completed→`post-run-analysis`（D7 验证）、dry_run_only→`execution-setup`、missing_artifact/input_syntax_failure→`bounded-auto-fix`（auto_advance=true，rollback_target=WF-03A）、runtime/numerical→`failure-analysis`（提示 potential_design_issue，rollback_target=WF-02）。
- **lammps-error-summary.ts**：回滚目标推断 + 汇总回写状态包。
- **lammps-log-sections.ts**：大日志分段读取器——HEAD(100 行)/ERROR-WARNING 窗口(前 12 处±6 行)/THERMO 窗口(前 6 处)/TAIL(150 行)，带行号，供 analyst 分段消费。

### 2.7 建模子系统（WF-01 路由）

- **lammps-model-route.ts**（`lammps model`）：候选路由打分——`case-reuse-structure`(1.0，显式 raw case 源) > `atomsk-cif-conversion`(0.95) > `atomsk-polycrystal`(0.92) > `atomsk-structure-edit`(0.88) > `atomsk-crystal-build`(0.84) > `case-first-manual-fallback`(0.4)；内置 5 个高价值本地结构索引；产出 wf01.packet.json（primary_route/alternatives/confidence(≥0.9 为 high)/hard_checks/atomsk_plan/handoff_to_wf02{required_artifacts: 主结构文件 + structure.manifest.json + structure-validation.json}）。
- **lammps-atomsk-render.ts**（`model-render`）：按 primary_route 从 `.angsheng/templates/atomsk/` 生成 `run-atomsk.sh`（cif 路线：orthogonal-cell→alignx→unskew→output.lmp；多晶：seed.xsf+poly.txt；默认 fcc duplicate 超胞）+ render.manifest.json（route/generated_files/expected_outputs/element_type_map/box_mode/notes_for_wf02）。
- **lammps-model-execute.ts**（`model-run`）：执行建模脚本（Atomsk 命令解析：CLI > env ATOMSK_COMMAND > modeling.json 平台段；不可执行自动 dry-run；校验 expected 产物存在性）。
- **lammps-structure-validate.ts**（`model-check`）：解析 LAMMPS data 文本（N atoms / N atom types / 盒边界 / Masses/Atoms 节存在性）+ 配合 manifest 给 advisory（来源/元素映射/box_mode 未记录提醒）。

### 2.8 运行脚本全清单（scripts/lammps-*，20 个）

| 脚本 | 子命令 | 用途 | 关键工件 |
|---|---|---|---|
| lammps-project-state-init.ts | —（npm script） | 从模板复制 9 件套初始化 `.lammps-project/`（已存在跳过） | `.lammps-project/` |
| lammps-execute.ts | execute | 运行/干跑 LAMMPS | runs/ 元数据 |
| lammps-auto-repair.ts | repair | 运行结果分类器 | `<runId>.repair.json` |
| lammps-repair-loop.ts | loop | 修复环规划器 | `<runId>.next-step.json` |
| lammps-error-summary.ts | summarize-error | 错误摘要回写 | error-summary.json 等 |
| lammps-model-route.ts | model | WF-01 建模路由 | wf01.packet.json |
| lammps-atomsk-render.ts | model-render | Atomsk 任务包渲染 | modeling/<runId>/ |
| lammps-model-execute.ts | model-run | 建模执行 | modeling/<runId>.json |
| lammps-structure-validate.ts | model-check | 结构校验 | structure-validation.json |
| lammps-lookup.ts | lookup | 知识直答（search+synthesize，零 LLM） | stdout |
| lammps-log-sections.ts | —（npm script） | 日志分段 | stdout |
| lammps-knowledge-maintenance.ts | —（npm script） | 知识只读维护报告（去重/晋升候选/高危词人工队列） | knowledge/maintenance/*.md |
| lammps-kb-ingest-bob-cases.ts | — | 批量案例入库 | INGESTION_REPORT.json |
| lammps-kb-deep-learn-bob.ts | — | 二阶段批量深度学习+晋升 | DEEP_LEARN_REPORT.json |
| lammps-kb-ingest-atomsk-study.ts | — | Atomsk 教程单条入库 | KB 条目 |
| lammps-kb-eval.ts | — | KB 问答评测（50 题，4 分制） | docs/tests/ 报告 |
| lammps-kb-regression.ts | — | 检索回归（8 题 mustMatch） | stdout |
| lammps-paper-note-init.ts | — | 论文笔记初始化（--slug/--doi/--year… 必填） | papers/notes/<slug>.md + case-paper-links.md |
| lammps-cli-test.ts | —（npm script） | CLI 冒烟 11 项 | stdout PASS/FAIL |
| lammps-lookup-workflow-test.ts | — | lookup 工作流 2 用例回归 | stdout |

### 2.9 模板体系（.angsheng/templates/，66 文件）

| 子目录 | 内容 |
|---|---|
| `lammps/` | 5 个输入模板族：hea-melt-solidify / meam-alloy / reaxff-reactive / restart-continue / tensile-strain |
| `atomsk/` | 9 个建模脚本：cif_to_lammps（-orthogonal-cell + -alignx -unskew）/ create_basic_fcc / create_basic_bcc / polycrystal_basic / point_defects_basic / stacking_fault_basic / twin_boundary_basic / dislocation_inclusion_basic / orientation_alignx_unskew |
| `lammps-project/` | 项目状态 9 件套模板（project.json / execution.json / modeling.json / wf02+wf03a.packet.json / stage-summary.md / review-log.md / open-issues.md / decisions.md） |
| `lammps-team/README.md` | 团队结构定义（team-lead=coordinator 常驻 + 8 个按需 teammates 的 spawn 时机） |
| `ovito/` | 15 个 OVITO Python 脚本（render_engine.py 渲染引擎 / cna_render / rdf_analysis / stress/temperature_field_map / dislocation_tracking）+ pipelines/ 4 场景 + tests/ 24 编号自测 |

`.angsheng/skills/`（9 个项目技能）：lammps-atomsk-modeling、lammps-hpc-submit（Slurm/PBS 提交+checklist）、lammps-paper-research、lammps-visualization、ovito-post-processing、scientific-plotting、lammps-paper-reproduction-auditor（论文复现审计：参数抽取/缺失补全/可信度评分，verdict: ready-for-reproduction | reproducible-with-assumptions | partial-only | blocked）、interview、teach-me。

### 2.10 skills/lammps-agent-workflow/（可移植技能包）

把工作流打包成可复制到其它 Claude Code / Codex 项目的独立技能：INSTALL.md、templates/（state.md、3 个 packet、review-result.schema + 2 个示例、evidence-record、review-log、decisions、result-block、work-log）、knowledge-capsule/（evidence-gate、review-guidelines、failure-patterns-min 7 类失败模式、potential-selection-min 势家族速查）、test-cases/（4 个合成审查夹具：tensile-safe / reaxff-safe / bad-fixed-velocity(MB-002 反例) / bad-reaxff-no-qeq(MB-004 反例)）。
⚠️ README 引用的 `SKILL.md`、`AGENTS.md`、`scripts/validate-review-result.js`、`templates/review-result.schema.json` 在仓库中不存在（缺口，见 §8）。

### 2.11 引擎服务层（src/lammps-engine/，骨架级）

- **engine-server.ts**：Node http 轻量服务器（默认 127.0.0.1:3847，`X-Engine-Key`/Bearer 鉴权）。端点：`GET /health`、`GET|PATCH /api/config`、`GET /api/tools`（9 个工具名）、`POST /api/engine/search`（**唯一接真实后端**，调 lammpsKnowledge/search）、`/api/kb/*`（ingest/queue/status/review，占位）、`/api/gateway/*`（session/job，占位）、`POST /api/mcp/call`（统一 MCP 调用入口，仅 knowledge_search 真实）。**引擎本身不直接调 LAMMPS**——`lammps_workflow_run` 只返回 jobId 占位，真实执行在 scripts/lammps-execute.ts。
- **entrypoint.ts**：CLI 启动器（--port/--host/--api-key/--workspace/--config；优先级 命令行 > 配置文件 > 环境变量 > 默认）。
- **mcp-bridge.ts**：JSON-RPC 2.0 over stdio 的 MCP server（协议 2024-11-05），把引擎 9 个工具转成标准 `tools/list`/`tools/call`。

### 2.12 规则文件全景（knowledge/rules/，19 个）与设计哲学双轨

| 文件 | 内容 | 已在本文 |
|---|---|---|
| workflow-stages.md | 阶段状态机权威定义（V3 Team-Aware） | §2.1 |
| workflow-handoffs.md | 三条标准闭环路径 + coordinator 约束（§四） | §2.4.7 |
| **light-stage-handoffs.md** | **V2 轻量主线交接层**：packets 是"记忆辅助与交接契约"而非硬门；回滚是正常操作应记录非报错；coordinator 保留裁量权；reviewer 仍是四阶段质量门，但 packets 的作用是减少歧义而非刚性阻断。定义 **run-result.json V2 schema**（run_id/launched_at/completed_at/workdir/input/mode: local\|hpc\|dry_run/log_path/stdout_path/stderr_path/exit_code/status: completed\|failed\|dry_run/notes）与 analysis-report.json schema | 本节 |
| **simplifications.md** | **MVP 有意简化决策 10 条**（防设计漂移的活档案）：①不改 Claude Code 核心 agent 加载；②不做 LAMMPS-only 服务模式/内置固定代理；③初版不加自定义检索工具（librarian + Read/Glob/Grep 兜底）；④不迁移完整 OpenClaw 运行时布局；⑤**不在代码中强制 reviewer 门——门只定义在 coordinator/reviewer 提示词中**；⑥不自动 HPC 提交；⑦不整拷案例库进提示词（按需引用）；⑧初始团队仅 5 角色；⑨可视化/HPC 提交为二期；⑩向量库推迟。对比现状可知演进轨迹：③⑩后来被 lammpsKnowledge SQLite FTS 取代、⑧扩到 13 代理、⑨已落地 post-processor 与 hpc-submit skill——**唯独 ⑤ 维持至今，即证据门无代码是有意边界而非烂尾** | 本节 + §8 |
| **execution-workflow.md** | 执行层规则（V2）：稳定执行接口 / Windows dry-run / Linux 迁移路径 / 命令解析顺序 / `--invoke` 已从 loop 移除的显式变更记录 | §2.6 |
| **modeling-workflow.md** | WF-01 建模控制层：5 种路由类型与 Atomsk 使用边界（本地案例优先，仅当任务匹配 Atomsk 强项才用） | §2.7 |
| **wf00-scheme-design.md** | WF-00 阶段定义：何时需要 WF-00（模糊目标/新多阶段项目/参数不全）、何时不需要（纯分析已有 log/纯检索/单次执行现有案例）、D1-D7 决策表与依据要求 | §2.1/2.3 |
| **log-analysis-playbook.md** | 大日志最小分析流程（中文）：初始化区段（units/atom_style/boundary/pair_style/pair_coeff/read_data 优先核）→ 错误警告区段 → 后续区段；避免整体读取 | §2.6 |
| mandatory-checks.md / review-guidelines.md | 证据系统规则 | §2.4 |
| failure-patterns / potential-selection / learned-rules / project-state-management | 失败模式 7 类 / 势家族速查 / Bob 案例模板 T-01~T-08 / 状态包规范 | §2.4/2.5/3.2 |
| matplotlib-standards / ovito-rendering-standards / post-processing-standards | WF-05 三大标准（post-processor 8 步 pre-flight 必读） | §2.3 |
| README.md | 一句话索引 | — |

**设计哲学双轨（对二开最重要的元决策）**：同一仓库里并存两套交接哲学——`workflow-handoffs.md`（硬门派：reviewer gate + fail-closed 证据校验）与 `light-stage-handoffs.md`（软门派：packet 是契约不是枷锁，回滚常态化）。加上 `simplifications.md` ⑤ 的"门不代码化"决策，说明作者刻意把**刚性留给了文档协议、把弹性留给了执行**；MDagent 证据系统（F-009）走的正是把硬门派代码化的相反路线。

---

## 3. 分层知识系统（Tiered Knowledge System）

### 3.1 总览

五层环环相扣：
**Markdown 分层账本（knowledge/）→ SQLite FTS5 索引与分层检索（src/utils/lammpsKnowledge/，约 4300 行）→ KB 入库流水线（src/utils/lammpsKbPipeline/，约 2000 行）→ 多机同步服务（scripts/knowledge-sync-server.ts，端口 3849）→ CLI 通用自动记忆（src/memdir/，约 1850 行，与 LAMMPS 知识相互独立）**。

### 3.2 knowledge/ 账本

| 子目录 | 文件数 | 内容 |
|---|---|---|
| `cases/` | 118 | notes/ 27 个提炼笔记、paper-reproduction/ 87 个论文复现脚本+数据、INDEX.md（15.3KB 案例分类总览）、CASE_METADATA.md（case.metadata.json 侧车规范）、case-family-index.md（10 个归一化 family 映射：compression-deformation / tensile-deformation / shear-and-elastic / melt-solidify / reactive-and-deposition / grinding-machining-interface / shock-cascade-ttm / alloy-and-segregation / gsfe-stacking-fault / simple-molecular-gas，每 family 给 raw 目录映射、代表文件、已知教训引用） |
| `memory/` | 11 | 分层经验记忆（见下） |
| `papers/` | 10 | notes/ 8 + topics/ 2（势函数调研、HEA 策略等）+ case-paper-links.md |
| `reports/` | 23 | 16 波 metal-direction-paper-learning-packet（388 篇论文语料学习包，带 frontmatter：entry_id/decision/reviewer/stage/potential_family）+ 实现记录/测试报告 |
| `rules/` | 19 | 19 个规则文件（全量清单与逐文件定位见 §2.12）：workflow-stages（V3 权威）、mandatory-checks（MB-001~007）、review-guidelines、failure-patterns、potential-selection、learned-rules（Bob 案例模板 T-01~T-08）、light-stage-handoffs、simplifications、execution/modeling-workflow、wf00-scheme-design、log-analysis-playbook、ovito/matplotlib/post-processing 标准等 |
| `templates/` | 7 | 输入模板族、atomsk 命令模板、OVITO Python 模板、input 自检表、answers/ |

**memory/ 分层读取策略**（INDEX.md 路由表明文规定）：

| 文件 | 大小 | 条目 | 读取策略 |
|---|---|---|---|
| core-checks.md | 5.2KB | CL-001..019 紧凑护栏索引（按严重度 BLOCKED/HIGH/STANDARD 三档 + 领域路由） | reviewer / input-writer **每次审查前必读** |
| confirmed-lessons.md | 20.5KB | 20 个 `## [CL-xxx]` 确认教训 | 触发后按 CL ID 精读，优先走 MCP |
| metal-research-insights.md | 48.4KB | 54 个哈希 ID 文献洞见 + 主题索引（晶界/HEA/Ni高温合金/增材/辐照/HCP/摩擦等） | 仅 WF-00/WF-01 检索 |
| pending-lessons.md | 10.1KB | 14 个 `## [PL-xxx]` 待定 | 匹配未解决问题时检索 |
| historical-lessons / session-lessons | 2.3/3.8KB | 历史专题 | 兜底检索 |
| verification-rules.md | 2.7KB | 晋升标准（high=双源或用户确认；medium=单源全量审查；low=可隔离） | KB 流水线读 |
| learning-workflow.md | 3.2KB | raw→curate→review→promote 流程契约 | KB 流水线/coordinator 读 |

**CL 条目格式**（无 frontmatter 的 Markdown 账本式）：
```
## [CL-007] 工程应变必须冻结初始盒长
- status: confirmed
- category: review|input|analysis|retrieval|workflow|potential
- lesson: 一句可复用教训（situation → mistake/risk → fix/check）
- evidence: path1; path2
- note: 晋升条件或警示
```

**学习流程铁律**：工作流 agent 一律不得直写 confirmed/pending-lessons，必须先写 raw 缓冲区（`.angsheng/lammps-kb-pipeline/raw/`，JSON 字段：id(uuid)/timestamp/source/content_class/title/body/evidence_paths/context/confidence）。晋升 confirmed 三判据：用户明确确认 / ≥2 一致本地来源 / 对刚犯错误的直接纠正且有本地证据。

### 3.3 检索引擎（lammpsKnowledge/）

**索引结构**（SQLite，`<workspace>/.angsheng/cache/lammps-knowledge.sqlite`，仓库内以 gz 分卷交付约 17MB）：
- `documents` 表：path/title/content/snippet/source_type/source_tier/family/material_system/potential_family/stage/**case_weight(REAL)/case_reliability/case_usage**/file_type/chunk_index/mtime/sha1/char_count/indexed_at；
- `documents_fts`：FTS5 虚表（path,title,content，tokenize=unicode61）；
- `source_files`：mtime+size+metadata_stamp 三者一致即跳过（增量索引判重）；
- `index_metadata`：last_run_at/last_mode。

**入库与分块**：允许 21 种扩展名 + `in.*` 前缀；单文件上限 2MB；.md 按标题（#~####）切分；log.lammps 专用切法（>240 行时首 120 行 + 关键行 ±8 行窗口 + 前 4 个 thermo 段 + 末 180 行）；超 1800 字符滑窗（overlap 250）。

**三个分类轴**（路径前缀推断）：source_tier（rule/memory/paper/template/case/manual/correction/example/potential/report/source/archive/other）→ source_type（更细 15 种）→ file_type（log/markdown/metadata/potential/structure/input/text）。

**case.metadata.json 侧车**（向上继承、子目录覆盖）：`caseName, family, materialSystem, potentialFamily, stage, weight(0.1~3 钳制), reliability(raw|usable|reviewed|trusted), usage(reference|template|preferred), tags, aliases, summary`——拼成元数据前奏注入 chunk 使 FTS 可命中；无侧车时启发式推断（family/potential/元素三元组正则）。

**检索 API** `searchKnowledge({query, topK(1~20 默认8), family, materialSystem, potentialFamily, stage, fileType, sourceTypes, autoIndex, includeRemote})` → `{queryType, answerStrategy, answerChecklist[], answerTemplate{lead,support[]}, results[]{path,title,snippet,sourceType,sourceTier,family,…,score,whyRanked[],evidenceLines[]}, remoteInfo?}`。

**queryType 判定**（正则顺序）：`syntax_lookup`（语法/怎么写）→ `case_route`（哪个案例库）→ `workflow_summary`（为什么/机制）→ 默认 `rule_judge`（能不能/必须吗）。查询理解含中英同义词表（FAMILY/POTENTIAL/STAGE_SYNONYMS、材料别名词形归一、命令→手册文件名提示）；CJK 按 2-gram 切分组 FTS OR 查询。

**两阶段打分**：
1. SQL 阶段：`-bm25(fts, 6.0, 4.0, 1.0)`（title 权重 6 > path 4 > content 1）+ 路径层加成（knowledge/ +3、memory/ +30、rules/ +20、cases/raw +2.5）+ 侧车权重（case_weight、trusted +2.5、reviewed +1.5、preferred +2）+ 类型加成（manual_reference +2.5、correction +2）+ 降权（INDEX.md 类 -3.5、log -1.5、knowledge_summary -10）+ 命中轴加成（family +4 / potential +3 / stage +2.5 / 材料 +4.5、路径含材料 +8、命令手册 +10）。
2. 内存 rerank：同 path 重复 chunk -14/块、同案例目录 -9/块、archive -6；**按 queryType 二次分层加成**（rule_judge：core-checks +22、confirmed-lessons +18；syntax_lookup：manual_reference +14；case_route：case-family-index +20；workflow_summary：metal-research-insights +18；CL-xxx 检索：core-checks +24）；**案例分组多样性**（每案例组只放最优块进 primary）。
3. **直连路径候选**：特定高频问题绕过 FTS 直接注入高分行（如路由问题→memory/INDEX.md 150 分）。

**证据行抽取**：命中时回读原文件按 token 逐行匹配输出 ≤4 行 `行号: 内容`；whyRanked 生成人读理由标签。

**合成器** `synthesizeKnowledgeAnswer(result)`：纯模板零 LLM——中文五段式：`结论`（checklist 首项）→`关键点`→`直接证据`（≤2 文件各 ≤4 行）→`优先参考路径`（≤3 条+理由）→`回答策略`。

**分层图谱索引**（layered.sqlite，预构建 463KB 随仓分发）：entities/files/links 表；`find_cases`（实体 label LIKE）可用，get_linked/verify_script 为 stub（设计目标：verify_script 做 CL 触发式脚本校验）。

**双库检索** dualSearch：远程库同构打分 ×0.9 折扣、`[remote]` 前缀，按 path+snippet 去重合并。

### 3.4 KB 入库流水线（lammpsKbPipeline/）

- **状态机**：`candidate | confirmed | pending_review | quarantined`；内容类型 13 种（case/case_note/correction/dialogue/error/experience/input_script/output_log/potential_note/qa/rule/template_snippet/unknown）；质量 4 档（gold_candidate/noise/uncertain/useful）；DB `.angsheng/cache/lammps-kb-pipeline.sqlite`；暂存目录 `.angsheng/lammps-kb-pipeline/{raw,candidate,quarantine,reviews}`。
- **classifyContent 自动分类**：sourceType 关键词优先，其次内容正则（`ERROR:`→error；`pair_style`→input_script；`经验|lesson:`→experience；`必须|review rule`→rule；`纠错|should be`→correction；`user:|assistant:`→dialogue）；LAMMPS 术语命中表 19 词；置信度累加（base 0.3 + 相关 0.25 + 类型 0.15 + …，上限 0.98）；**分流**：无意义（<24 字/纯"ok"）→noise+隔离；experience/correction/qa→gold_candidate+待审；置信 ≥0.8→candidate 免审暂存，否则待审。
- **applyReviewDecision 晋升写账本**（核心机制）：
  1. 目标路由：显式 destination 优先（禁 `..`；.md 结尾=append，目录=新文件）；默认映射 rule→learned-rules.md(append)、correction→corrections/reference-corpus/、experience→confirmed-lessons.md(append)、case_note→cases/notes/、potential_note→potentials/…；
  2. **账本块合并**：按 `## ` 标题切段，mergeTarget/短 ID/标题命中已有块→**增量强化**（evidence 用 `;` 合并去重、note 追加 Reinforcement），否则新建块（confirmed-lessons 自动套 CL 格式）；
  3. 副作用：更新 entries 行 → 重建 entries_fts → 写 reviews/ 审计日志 → **confirmed 时触发主知识索引增量更新** + 向 maintenance/ingested-knowledge-index.md 追加幂等路由行。

### 3.5 多机同步与备份

- **knowledge-sync-server.ts**（端口 3849，Bearer 认证）：数据目录三段式——`current/current-manifest.json` / `versions/vN.json`（版本链清单，checksum=sha256 串联）/ `objects/<sha1前2位>/<sha1>`（内容寻址去重）。协议：`GET /knowledge/status`、`GET /knowledge/pull?since_version=N`（增量：只返回 added+modified 对象）、`POST /knowledge/push`（写对象→与上版 diff→生成新版本→推进 latest）、`GET /knowledge/backup/list`、`GET /health`。冲突策略：同名 SHA1 不一致记入 conflicts 跳过（不自动合并）；多写者为最后写入胜出。
- **客户端**（syncService/backupManager/syncWatcher）：环境变量 `LAMMPS_KNOWLEDGE_{REMOTE_URL,TOKEN,REMOTE_PATH, AUTO_SYNC, SYNC_INTERVAL_MS(默认5分钟), MAX_VERSIONS(默认50)}`；push 前本地建版本快照（objects 内容寻址 + restoreVersion 整树还原 + 超 50 版且超 30 天的同日版本折叠）；fs.watch 防抖触发远程索引增量重建。

### 3.6 memdir（CLI 通用自动记忆，与 knowledge 分域）

- 存储 `~/.angsheng/projects/<slug>/memory/`，MEMORY.md 索引（200 行/25KB 双上限）；四类记忆分类法：**user（用户画像）/ feedback（纠正与确认，带 Why+How to apply）/ project（进行中工作，相对日期转绝对）/ reference（外部指针）**；frontmatter `{name, description, type}` + 负面清单（可从代码/git 推导的不存）。
- **召回**：memoryScan 扫 frontmatter → findRelevantMemories 用**侧链模型**（JSON-schema 输出 ≤5 个最相关文件）→ query 主循环预取注入；>1 天的记忆注入陈旧提醒。
- **提取**：services/extractMemories——每个完整查询循环结束（stopHooks）用 forked agent（共享父对话 prompt cache）从转录提取记忆；主 agent 本轮已写过则跳过。
- 开关：`CLAUDE_CODE_DISABLE_AUTO_MEMORY` → --bare → CCR 无持久存储 → settings.autoMemoryEnabled（默认开）；提取模式由 GrowthBook 特性门控。
- **与 knowledge/ 分工**：memdir=CLI 通用、自动、跨会话；knowledge/=LAMMPS 领域、评审制、FTS 检索。互不越界。

### 3.7 维护闭环

- **lammps-knowledge-maintenance.ts**（只读不改库）：解析两账本结构化 Lesson → 归一化去重组 → 晋升候选（evidence ≥2 且非高危词）→ **高危词人工队列**（正则 `pair_coeff|reaxff|meam|映射|势函数|atomsk|cif` 命中的 pending 一律禁自动晋升）→ 汇总报告写 knowledge/maintenance/。
- **批量学习流水线**（bob 案例两脚本）：ingest（扫描 case-*.md + 原始目录 → inferTags/inferMaterial/inferPotential → 入流水线）→ deep-learn（读 INGESTION_REPORT → classifyCase 规则分类 + extractModelingPattern 建模套路识别 → 批量 applyReviewDecision(confirmed)）→ 产出 learned-rules.md 的 T-01~T-08 建模模板。

### 3.8 知识 MCP 工具清单（19 个）

**server `lammps-knowledge`**（mcpServer.ts，注册于 src/plugins/bundled/lammpsKnowledgeSearch.ts，CLI 入口 `--lammps-knowledge-mcp`）：
`search_lammps_knowledge` / `index_lammps_knowledge`(incremental|full) / `reindex_lammps_knowledge` / `get_lammps_knowledge_status` / `sync_knowledge_{pull,push,status,backup}` / `index_remote_knowledge` / `find_cases` / `get_entity` / `get_linked`(relType: has_check|applies_to) / `verify_script`(commands[]/scriptContent/scope: full|deformation|potential|minimization|npt)。

**server `lammps-kb-pipeline`**（注册于 src/plugins/bundled/lammpsKbPipeline.ts，CLI 入口 `--lammps-kb-pipeline-mcp`）：
`ingest_lammps_kb_content` / `review_lammps_kb_queue`(limit 1~50) / `get_lammps_kb_entry` / `apply_lammps_kb_review_decision` / `search_lammps_kb_pipeline` / `get_lammps_kb_pipeline_status`。

**消费路径**：case-librarian（检索专家）/ reviewer（按 CL ID 窄读）/ input-writer（模板与已验证案例）/ kb 三件套（流水线）/ lookup CLI（直答）/ engine-server（HTTP 代理）/ 评测脚本。

---

## 4. CLI Agent 平台底座

### 4.1 核心代理循环

分层：`screens/REPL.tsx`（Ink 交互，6408 行）→ `QueryEngine.ts`（会话引擎，1331 行）→ `query.ts`（单轮主循环，1776 行）→ `services/tools/`（工具编排）→ `services/api/anthropic.ts`（模型调用）。

- **QueryEngine**（一会话一实例；`submitMessage()` AsyncGenerator<SDKMessage>）：组装系统提示 → processUserInput（斜杠命令/附件）→ **用户消息先于 API 落盘**（保证 --resume 可用）→ query 循环 → SDK 消息流 → 五种收尾（success / error_max_turns / error_max_budget_usd / error_during_execution / error_max_structured_output_retries）。预算控制：maxTurns / maxBudgetUsd（逐消息检查 USD）/ task_budget / 结构化输出重试上限（默认 5）。
- **query() 主循环**（while(true) 状态机）：skill 预取 → 流式调模型 → tool_use → runTools → tool_result → continue。**终止原因**：completed / aborted_streaming / aborted_tools / model_error / prompt_too_long / image_error / blocking_limit / max_turns / hook_stopped。**续跑原因（transition）**：next_turn / stop_hook_blocking / **token_budget_continuation**（完成度 ≥90% 且收益未递减则注入续跑消息）/ reactive_compact_retry / max_output_tokens_escalate|recovery（截断恢复上限 3 次）。
- **Stop hooks**：assistant end_turn 后执行 Stop 钩子，可阻止停止并注入反馈继续；另处理 TeammateIdle/TaskCompleted 钩子与记忆提取任务分类。
- **thinking 硬约束**：thinking 块必须保留整个 assistant 轨迹（消息规范化的硬规则）。

### 4.2 上下文管理（多级压缩体系，src/services/compact/）

| 层 | 触发 | 行为 |
|---|---|---|
| autoCompact | 上下文窗口 − 预留 20k | 生成摘要、重建消息链、产出 compact_boundary（带熔断计数） |
| microCompact / apiMicrocompact / cachedMicrocompact | 渐进 | 对旧工具结果逐条缩水 |
| reactiveCompact（feature） | prompt_too_long | 被动压缩重试 |
| snipCompact / snipProjection（feature HISTORY_SNIP） | 手动/自动 | 剪除死消息（UI 保留滚动、SDK 端真删） |
| contextCollapse（feature） | — | 上下文折叠 |
| toolResultStorage 预算 | — | 工具结果逐条预算 + 内容替换 |

配套：tokens.ts（估算计数）、context.ts（截断恢复上限）、compact_boundary 后 splice 释放旧消息供 GC。

### 4.3 子代理与任务系统

- **TaskType 8 类**：`local_bash | local_agent | remote_agent | in_process_teammate | local_workflow | monitor_mcp | dream`（+ LocalMainSessionTask 主会话后台化）；ID 前缀 b/a/r/t/w/m/d + 8 位 base36；输出落盘（outputFile/outputOffset）。
- **AgentTool**（子代理入口）：定义来源 4 类——built-in（general-purpose/explore/plan/verification 等）、`.angsheng/agents/*.md`（frontmatter: tools/disallowedTools/skills/mcpServers/hooks/model/effort/permissionMode/maxTurns/memory/background/**isolation[worktree|remote]**）、agents.json、插件。支持同步/异步（background）、worktree 隔离、teleport 远程、fork 子代理、in-process teammate（团队/swarm）。
- **团队模式**：TeamCreate/SendMessage/TaskCreate(blockedBy) 工具 + scratchpad 共享目录 + UDS 对等信箱（LAN_PIPES）。

### 4.4 工具集（56 个内置）与权限模型

工具注册表 `src/tools.ts:getAllBaseTools()`（唯一事实来源，条件装配）；装配 `assembleToolPool()` 合并内置+MCP（内置名优先、分区排序保 prompt cache 稳定）。要点清单：Agent(Task)/TaskOutput/TaskStop、Bash/PowerShell、FileRead/FileEdit/FileWrite/NotebookEdit、Glob/Grep、WebFetch/WebSearch/WebBrowser、AskUserQuestion、TodoWrite v1 + TaskCreate/Get/Update/List v2、EnterPlanMode/ExitPlanModeV2、EnterWorktree/ExitWorktree、Skill/DiscoverSkills、MCP 系列（ListMcpResources/ReadMcpResource/MCPTool/McpAuth）、SendMessage/TeamCreate/Delete/ListPeers、**Workflow 系列 8 个**（Create/Start/StageSwitch/Status/List/Resume/Stop + WorkflowTool）、ToolSearch（工具过多时延迟注入）、Sleep（主动模式自控唤醒）、Cron 系列、Monitor/RemoteTrigger、REPL（Bash/Read/Edit 包进持久 VM）、Snip、SyntheticOutput（SDK 结构化输出收口）等。

**权限模型**：
- 模式：`default | acceptEdits | plan | dontAsk | bypassPermissions`（+内部 auto/bubble）；叠加 **WorkflowStyle 六档**（guided / autonomous / autonomous-escalation / workflow-guided / workflow-semi / workflow-auto）控制自主升级策略。
- 规则三态 allow/deny/ask，按来源分层；Bash 命令级匹配（shellRuleMatching）+ 危险模式检测 + LLM 分类器（yoloClassifier/bashClassifier）。
- 入口 `hasPermissionsToUseTool()`；REPL 侧分派 interactive/coordinator/swarmWorker 三种 handler 弹权限 UI；工作区信任（trust dialog）是 hook 执行前置。
- **设置源优先级**：userSettings → projectSettings → localSettings → flagSettings → policySettings（managed/远程策略最高）。

### 4.5 命令体系（斜杠命令）

`getCommands()` 合并 6 来源：bundled skills → 内置插件 skills → skill 目录（policy/user/project `.angsheng/skills/`）→ workflow 命令 → 插件 commands/skills → 内置 COMMANDS。分类概览（138 个命令目录）：
- 会话/历史：resume、session、rename、rewind（回滚到某消息+文件快照）、export、summary、clear、compact、tag、thinkback（回放）。
- 模型/成本：model、provider、effort、fast、cost、usage、stats、insights、statusline。
- 文件/上下文：files、diff、add-dir、memory（编辑记忆文件）、context。
- 权限/安全：permissions、sandbox-toggle、privacy-settings、security-review、evidence-check/**evidence-validate（stub）**、doctor。
- 配置：config、env、theme、vim、keybindings、output-style、upgrade、install（Shell 集成）。
- 集成：login、install-github-app、install-slack-app、ide、chrome、mcp、plugin、skills。
- Git：branch、pr_comments、review、autofix-pr、issue。
- **代理编排**：agents、plan（计划模式）、tasks、fork、**workflow 系列**、ultraplan、coordinator、monitor、daemon、job、buddy、proactive、assistant、voice、bridge、remoteControlServer、attach/detach/send/peers/pipes（多进程邮箱）、teleport（会话迁远程）。workflow 系列细节：`/workflow [guided|auto|escalate|workflow-guided|workflow-semi|workflow-auto|status]`（别名 wg/ws/wa，escalate/managed/auto-safe 均映射 autonomous-escalation）是六档 WorkflowStyle 的用户入口，同时写 bootstrap state、AppState 与 toolPermissionContext；**`/paper-reproduce-auto [DOI|PDF 路径|URL|标题]`** = 设 workflow-auto 模式 + 自动链式提交 `/lammps-paper-reproduction-auditor <task>`（nextInput submitNextInput: true）——平台自主模式与领域论文复现审计技能的接缝入口。
- ant 内部命令（commit/bughunter/share 等）在外部构建剔除。

### 4.6 UI 层

- **Ink fork**（packages/@ant/ink，包名 @anthropic/ink）：React 19 + react-reconciler 终端渲染器 fork（超链接/宽字符/性能定制）。
- **screens/**：REPL（主界面：消息列表+输入+权限对话框+任务面板+远程状态）、Doctor（体检向导）、ResumeConversation。
- **components/**（606 文件）：消息渲染（Markdown/高亮/结构化 diff）、输入（PromptInput/Vim 文本框/Typeahead）、权限、MCP 对话框、任务/团队面板、远控/桥、设置向导、设计系统、Shell 面板、沙箱等。
- **状态**：AppStateStore（70+ 字段全局状态：messages/settings/mainLoopModel/toolPermissionContext/tasks/agentNameRegistry/mcp/plugins/speculation…）；AppState.tsx selector 式订阅；无 React 的 createStore 供 headless 共用。hooks/ 94 个 use* 钩子。

### 4.7 扩展机制

- **Hook 系统**：**27 个生命周期事件点**（PreToolUse/PostToolUse/PostToolUseFailure/Notification/UserPromptSubmit/SessionStart/SessionEnd/Stop/StopFailure/SubagentStart/SubagentStop/PreCompact/PostCompact/PermissionRequest/PermissionDenied/Setup/TeammateIdle/TaskCreated/TaskCompleted/Elicitation/ElicitationResult/ConfigChange/WorktreeCreate/WorktreeRemove/InstructionsLoaded/CwdChanged/FileChanged）；三种钩子体：**shell 命令 / HTTP 调用 / LLM prompt**（utils/hooks/）；配置来自 settings 各层+插件/agent/skill frontmatter；执行需工作区信任；有 SSRF 防护。
- **插件系统**（utils/plugins/，46 文件）：完整 marketplace 体系（官方 GCS marketplace/zip 缓存/版本管理与自动更新/blocklist/依赖解析/mcpb 包格式）；插件可贡献 commands、agents、skills、hooks、MCP 服务器、LSP、outputStyles。
- **Skills 三级来源**：编译期 registerBundledSkill（20 余个：batch/debug/dream/loop/remember/simplify/skillify/stuck/verify/cronManage…）→ 磁盘 skill 目录（policy→user→project，frontmatter: allowedTools/model/agent/hooks/context:inline|fork）→ 插件 skills + **MCP prompts 映射为 skill**；EXPERIMENTAL_SKILL_SEARCH 本地索引检索。
- **Output Styles**：`.angsheng/output-styles/*.md`（文件名即样式名），影响系统提示输出风格段。
- **skill-mine 技能挖掘（本仓库自有功能，commit 0542b79，src/skills/skillMine/ 5 文件约 1157 行 + 命令 /skill-mine、/skill-mine-review）**——从使用历史自动发现可复用技能的完整流水线，**纯手动触发、永不自动安装**（与自我进化基线同哲学：人工审查是发布门）：
  - **数据采集**（historyStore.ts）双通道：`~/.angsheng/history.jsonl`（全局提示历史，轻量无工具上下文）+ `~/.angsheng/projects/<proj>/*.jsonl`（完整会话转录，可检测多步工作流）；按 mtime 窗口过滤（默认 7 天、下限 1 小时，config 存 `~/.angsheng/skill-mine/config.json`）。
  - **数据集**：PromptSample（≤500 字符）+ SessionSample（首提示、≤30 交替轮次每轮 ≤300 字符、userMessageCount、toolCounts 频次表）+ **topPromptShapes**（lowercase+trim 归一化的高频提示形态 top-N，高信号低 token）。
  - **模式挖掘**（patternMiner.ts）：Sonnet 一次性非交互查询（maxOutputTokens 8192），结构化输出 0-5 个候选；六类技能原型（重复命令序列/多步验证工作流/领域代码生成/调查流/样板生成/提示模板）；纪律：频率 ≥2 优先、slug 3-40 kebab、不与内置命令重复、数据稀疏返回空、body 必须完整可运行（# Title/## Goal/## Steps/每步 Success criteria）；候选必带 evidence{source: prompt|session, snippet ≤400 字}+rationale+frequency+proposed_scope。
  - **暂存**：`~/.angsheng/skills-staging/`，状态 pending/accepted/rejected；对已 pending 的同 slug 去重（用户没处理的不再重复塞）。
  - **安装/拒绝/卸载**：installCandidate 只写 SKILL.md（frontmatter: name/description/allowed-tools/when_to_use/argument-hint/arguments + body），user 级 `~/.angsheng/skills/<slug>` 或 project 级 `.angsheng/skills/<slug>`，已存在目录保留；拒绝保留历史（除非 purge）；卸载 best-effort。
  - **启动横幅提醒**：getStagedPendingSummary 输出 "N skills ready to review (run /skill-mine-review)"。

### 4.8 模型接入层

- **Provider 7 类**：`firstParty | bedrock | vertex | foundry | openai | gemini | grok`（settings.modelType 或 `CLAUDE_CODE_USE_*` 环境变量）；`ANTHROPIC_BASE_URL` 指向非官方即视为网关/中继（本仓库实际配置 DeepSeek anthropic 兼容端点）。
- **@ant/model-provider 抽象**：注册 clientFactories + hooks（analytics/成本/日志 DI）；openai/grok/gemini 各自消息/工具转换 + 流适配为 Anthropic 事件流——**以 Anthropic Messages 协议为内部通用语**。
- 模型元数据：aliases/configs/上下文窗口升级检查/deprecation/modelCapabilities/allowlist；thinking 默认 adaptive；effort 档位。
- **观测**：Langfuse trace/span 嵌套、OpenTelemetry 全家桶、Sentry、Statsig/GrowthBook 特性门、cost-tracker（USD 计价、会话成本落盘）。

### 4.9 周边系统

| 系统 | 说明 |
|---|---|
| src/server/ + entrypoints/dashboard.ts | Web 仪表板（Bun.serve，3760）：会话/任务扫描（3s 轮询）、网页聊天（spawn CLI 子进程 -p --output-format=stream-json，解析 thinking/text/tool_use/question 事件，stdin 答题）、WebSocket 广播、中文单页前端 |
| src/daemon/ | 守护进程：主管 spawn daemon-worker 子进程（指数退避重启），容量/权限/沙箱可配，支撑后台会话 |
| src/remote/ | CCR 容器化远程会话客户端（RemoteSessionManager/权限桥） |
| src/ssh/ | SSH 远端会话（agent 跑在 SSH 远端） |
| src/bridge/（41 文件） | 手机/桌面 App 遥控桥（repl-bridge）：JWT 会话、云端 API、消息/附件/权限回调转发、容量唤醒 |
| src/coordinator/ | 协调者模式：coordinator 主线程 + worker 子代理 + scratchpad 共享 |
| src/buddy/ | 终端桌宠通知 UI（feature） |
| src/proactive/ | 主动模式状态机：REPL 周期注入 `<tick>` 让模型空闲时继续工作 + SleepTool 自控唤醒 |
| src/assistant/ | KAIROS 助手模式会话发现/选择器 |
| src/jobs/ | 模板任务（输入分类路由到模板） |
| src/voice/ + packages/audio-capture-napi | 语音模式（原生采集/回放，多平台权限查询） |
| src/vim/ + keybindings/ | Vim 模拟（motions/operators/textObjects）+ 可配置键位（schema/冲突检测） |
| src/memdir/ | 自动记忆（§3.6） |
| src/migrations/ | 12 个设置迁移脚本 |
| src/bootstrap/ | 进程级单例状态（sessionId/stats/turn token budget/已注册 hooks） |
| src/upstreamproxy/ | CCR 容器侧上游代理（token/CA 注入/CONNECT→WebSocket relay，fail-open） |
| environment-runner / self-hosted-runner / moreright | 外部构建 stub（ant 内部功能的编译期剔除产物） |

**次要服务族**（src/services/ 下未单列的目录，一句话定位）：lsp（语言服务器协议集成，配合插件 LSP）、autoDream（闲置会话整合"做梦"，/dream）、awaySummary（离开时段摘要）、AgentSummary / toolUseSummary / SessionMemory / sessionTranscript（会话/工具使用的摘要与转录服务）、teamMemorySync（团队共享记忆同步）、settingsSync（设置同步）、MagicDocs、PromptSuggestion（提示建议）、skillSearch（技能检索索引）、tips、notifier、preventSleep、tokenEstimation、vcr（会话录制/回放）、mockRateLimits / rateLimit*（限流模拟与消息）、policyLimits / remoteManagedSettings（企业策略）、subscriptionLimits（订阅限额）、voice / voiceKeyterms / voiceStreamSTT（语音三件套）。

### 4.10 packages/ 工作区包（14 个）

| 包 | 说明 |
|---|---|
| @ant/ink | Ink fork（终端渲染器） |
| @ant/model-provider | 模型提供方抽象层 |
| @ant/chrome-mcp-bridge | Chrome 扩展 MCP 桥（/chrome 命令） |
| @ant/computer-use-{input,mcp,swift} | 计算机使用（鼠标键盘控制）输入捕获/MCP/Swift 侧 |
| agent-tools | 工具核心接口包（CoreTool 类型 + 注册匹配） |
| builtin-tools | 全部 56 内置工具实现 |
| mcp-client | MCP 协议客户端严格层 |
| acp-link | ACP（Agent Client Protocol）代理服务器桥（对接 RCS） |
| remote-control-server | 自托管远控服务器（Hono + web 前端 + 二维码配对） |
| weixin | 微信通道集成（收发消息/媒体/监控，把 agent 接到微信） |
| audio-capture-napi / color-diff-napi / image-processor-napi / modifiers-napi / url-handler-napi | 5 个原生 addon（音频/彩色 diff/图像/修饰键/URL scheme） |

### 4.11 MCP 支持

- **传输**：`stdio | sse | http | ws`（+宿主专用 sse-ide/ws-ide/sdk/claudeai-proxy），基于官方 SDK。
- **能力**：工具（发现/调用/schema 缓存/LRU）、资源（枚举/读取）、**prompts 映射为 skills**、elicitation（反向请求）、OAuth（含内部 IdP）、roots。
- **宿主集成**：MCPConnectionManager（生命周期/重连/健康）、配置分层（settings.json/.mcp.json/项目/用户/策略）、进程内 server（InProcessTransport）、官方 registry、通道 allowlist、服务器审批 UI。
- **反向出口**：`src/entrypoints/mcp.ts` 把 CLI 内置工具以 stdio MCP server 暴露给外部宿主；引擎 mcp-bridge 同理。
- 工具命名 `mcp__<server>__<tool>`；deny 规则可按服务器前缀整体禁用；工具过多走 ToolSearch 延迟注入。

---

## 5. 工程设施

### 5.1 构建体系（双路径 + 编译期 feature 裁剪）

- **build.ts（Bun.build 路径）**：入口 cli.tsx → dist/；`splitting: true` + `define: getMacroDefines()`（MACRO.VERSION/BUILD_TIME 等）+ `features`（**Bun 官方 `feature()` 编译期 DCE**）；两步 Node 兼容后处理（`import.meta.require`→createRequire 兜底；裸解构 `globalThis.Bun` 加 typeof 守卫）；拷贝原生插件；生成双入口 `dist/cli-bun.js` / `dist/cli-node.js`。
- **vite.config.ts（第二路径）**：SSR node target、noExternal、rawAssetPlugin（.md/.txt 以字符串内联）、两个自研插件复刻上述机制；post-build.ts 等价后处理。
- **feature flags（核心亮点）**：`scripts/defines.ts` 的 DEFAULT_BUILD_FEATURES（默认开约 35 个：BUDDY/VOICE_MODE/DAEMON/ACP/AGENT_TEAMS/ULTRAPLAN/COORDINATOR_MODE/HISTORY_SNIP/CONTEXT_COLLAPSE/BG_SESSIONS/TEMPLATES…）+ 运行时 `FEATURE_<NAME>=1` 追加；vite-plugin-feature-flags 在 Rollup transform 阶段把 `feature('FLAG')` 替换为 true/false 字面量（**必须在 resolve 之前**，因为被门控代码块可能 require 不存在的文件）+ 把 `bun:bundle` 虚拟模块 resolve 成 stub + `using` 声明降级。
- **check-bundle-integrity.ts**：构建产物 5 类检查（断裂 chunk 引用/`__require` 引第三方/动态 import 第三方/nodeRequire 绕过/bun 专用模块警告）。
- **postinstall.cjs**：下载 riprep v15.0.1 平台二进制（镜像回退/代理感知/幂等/**失败永不阻断 install**）；setup-chrome-mcp.mjs 统一 Chrome MCP 安装。
- tsconfig：base（strict/bundler resolution）+ 根 paths 映射（src/* 与 workspace 包及旧名兼容 `@claude-code-best/*`）。

### 5.2 部署

- **deploy.sh / deploy.ps1**（一键部署）：依赖检查（bun 必需；lmp/python3 警告）→ bun install → **解压知识索引**（.angsheng/cache 三套 sqlite.gz；主库为 4+1 分卷需先 cat 重组）→ 无压缩包则调 buildKnowledgeIndex() 重建（indexedFiles<100 判失败）→ MCP 冒烟（timeout 5）→ API key 配置检查（grep settings，≥20 位才算配置）。
- **rcs.ts + rcs-aura.sh**：远控服务器启动（RCS_PORT 默认 3000，Hono HTTP+WS）；acp-link 把 `aura-bun --acp` 连到 RCS（本地或线上 remote-control.claude-code-best.win）。

### 5.3 Web 仪表板（3760）

REST：`/api/sessions(/:id)`、`/api/tasks`、`/api/refresh`、`/api/health`、`/api/chat/sessions`（GET/POST 列表/新建）、`.../input`（回答 CLI 提问→写子进程 stdin）、`.../message`、`.../resume`（绑定已有 CLI 会话续聊）、`.../cancel`（SIGTERM）。WebSocket：全量快照 + `sessions_updated` / `chat:stream_event`（thinking/text/tool_use/tool_result/question/error/done）/ `chat:done` 等。前端：中文单页（Agent Aura Console，GitHub Dark 配色，marked 渲染，斜杠命令提示）。
另含 DirectConnect 会话管理器（WebSocket 直连 + Bearer 鉴权 + can_use_tool 权限请求/中断）。

### 5.4 测试与基准

| 层 | 内容 |
|---|---|
| 单元 | bun test：src/__tests__ 6 文件（Tool 构建/上下文基线/提示提交/历史与粘贴引用/命令桥安全）；typecheck + biome lint + knip |
| CLI 冒烟 | lammps-cli-test.ts 11 项（help/route-map/lookup 真实子进程/execute dry-run/repair/loop/error-summary/model 三件套），劫持 argv+stdout 或 spawn |
| KB 评测 | lammps-kb-eval.ts：**50 道中文题**（输入规则/势函数/命令语法/案例检索/工作流认知），每题 4 分制（命令成功+引用命中+主断言+次断言），120s 超时归零，分 category 汇总 |
| 检索回归 | lammps-kb-regression.ts：8 题直接跑 searchKnowledge，mustMatch 正则 + minScore 门限，失败 exit 1 |
| Provider 基准 | provider-benchmark.ts：spawn agent loop（`--agent lammps-case-librarian --max-turns 8`）跑 50 题 3 分制 |

**基准结果**（docs/tests/）：glm-5-turbo **144/150（96.0%）** > GPT-5.5 132/150（88.0%）> glm-5.1 122/150（81.3%）；对照 **Codex CLI gpt-5.5 full-auto 直接跑同一套仅 86/150（57.3%）**（KB22 起大量命令级失败）——自研 CLI agent loop 显著优于通用 CLI 外壳。

### 5.5 Agent 自我进化基线（docs/implementation/agent-self-evolution-baseline.md v0.2）

- **目标**：在既有记忆/工作日志之上加"自我进化"层——检视历史工作、识别行为问题、生成改进提案、测试候选代理定义。首选**上下文优化**而非加规则。
- **不可协商安全规则**：绝不自动修改活跃代理定义/核心 prompt/工作流规则/生产知识；允许自动：读 transcripts、生成诊断报告/提案 JSON/候选副本/派生索引/离线回放。**人类审查是发布门**。
- **六层架构**：证据收集 → 行为与上下文评估（PASS/REVISE/BLOCKED 计数、重复问题模式、token 热点）→ 提案生成（`agent-improvement-proposals/<ts>/`，必含 human_review_required: true / auto_apply_allowed: false）→ 候选副本（candidate-agents/，活跃文件不动）→ 离线验证（replay/benchmark 对比，拒过拟合）→ 人类门控 apply（diff/changelog/回滚/测试）。
- **Agent 专属策略**：input-writer→context-pack（top 3-5 lesson 卡+1 案例，勿整读 78KB 账本）；reviewer→触发式门控证据（必读 mandatory-check 索引节选）；coordinator→只传 RESULT 块与摘要、长证据落盘传路径；architect/reasoner→WF-00 分阶段加载。
- **实证案例**（feature 分支 17953c8）：reviewer 每会话整读 78KB confirmed-lessons 吃 ~90K token → 拆分为 core-checks 5KB + metal-research-insights 48KB + 瘦身 20KB + INDEX 路由 + MCP 激活 → **会话上下文 ~117KB→~31KB（-73%）**。

### 5.6 git 历史与分支

- **main**（12 commits，2026-06-17~06-20）："干净重发布"——initial（tiered knowledge system）后连串 fix 补回丢失内容（.angsheng agents/templates、packages 源 335 文件、layeredIndexer/layeredSearch 重建、构建入口、vendor 占位），最后加 README/docs；新增 skill-mine 命令。
- **feature/knowledge-tiered-system**（8 commits，2026-05-20~06-16，与 main **无共同祖先**）：最小发布→vendor 化插件市场→**分层知识重构 17953c8**（上文 -73% 案例）。
- 差异：feature 比 main 多 645 文件 +92,800 行（vendored 官方插件市场 + 4 平台 .node 二进制 + 旧形态知识文件）；main = 轻量干净可部署版（索引走 gz 分卷压缩包）。

---

## 6. 关键数据流汇总

1. **正常流**：用户 prompt → lammps-cli 路由 → coordinator（读 state）→ architect 产 SIMULATION_SCHEME(D1-D7+D2.1) → reviewer 门（MB 检查+双证据 → review JSON + review-log）→ reasoner advisory（reasoner-assessment.md）→ [model 路由脚本或 writer] 产 wf01.packet+结构 → model-check 校验 → WF-02 势配置（wf02.packet）→ WF-03A 输入脚本（wf03a.packet+诊断 computes）→ execute 脚本跑 LAMMPS（runs/ 元数据）→ WF-04 analyst 解析 log（log-sections 分段）→ analysis-report.json（D7 验证+wf05_trigger）→ WF-05 post-processor 出图（figures/ + manifest）。
2. **失败流**：exit≠0 → auto-repair 分类 → repair-loop 规划 → coordinator 路由 → 疑似设计级 → reasoner（design_root_cause）→ architect 修订 → 回滚阶段重做 → summarize-error 落账。
3. **知识流**：会话经验 → kb-coordinator（ingest）→ curator 分类提案 → kb-reviewer 确认 → apply 晋升写 knowledge/ 账本 → 增量索引 → searchKnowledge/librarian/lookup 检索（闭环）。
4. **同步流**：knowledge/ 全树 SHA1 清单 → 与上版 diff → push 变更对象 → 服务器版本链 → 他机 pull（since_version 增量）→ 远程索引重建。

---

## 7. 规模统计

| 项 | 数值 |
|---|---|
| src/ TS 文件 | 约 2800（utils 849 / components 606 / commands 279 / services 277 / hooks 156 / cli 137） |
| knowledge/ | 187 文件（cases 118 / reports 23 / rules 19（含 README） / memory 11 / papers 10 / templates 7） |
| 确认/待定教训 | CL 20 / PL 14；文献洞见 54 条（48.4KB） |
| 代理定义 | 14 个（13 LAMMPS + hello-agent）；项目技能 9 个；模板 66 文件 |
| 知识代码量 | lammpsKnowledge ~4300 行 / lammpsKbPipeline ~2020 行 / memdir ~1850 行 |
| 知识 MCP 工具 | 19 个（lammps-knowledge 13 + lammps-kb-pipeline 6） |
| 主索引产物 | ~17MB（gz 4+1 分卷）+ layered 463KB + pipeline 0.9KB |
| 内置工具 | 56 个；生命周期 hook 事件点 27 个；默认 feature ~35 个 |
| 端口约定 | 引擎 3847 / 知识同步 3849 / 仪表板 3760 / RCS 3000 |

---

## 8. 已知缺口与开发参照要点

**实现缺口（prompt/文档引用了但代码不存在）**：
1. **证据系统机械执行层未代码化**（完整设计见 §2.4.9 对照表）：`/evidence-validate` 与 `/evidence-check` 命令均为 stub；`src/evidence/evidence-schema.ts`、`src/evidence/evidence-gate.ts` 不存在；skills 包的 `validate-review-result.js` / `review-result.schema.json` 不在仓库。三层验证（schema/8 条拒绝 PASS 情形/coordinator 验收）与 fail-closed 语义全部停留在规则文件与提示词层。**注意：这是 `simplifications.md` 第 5 条的显式 MVP 决策（"不在代码中强制 reviewer 门"），是有意边界而非烂尾**；对二开的启示是取舍要显式记录（该文件即"防漂移决策日志"的好范式）。**这是与 MDagent 证据系统（F-009，已代码化 R1-R19、fail-closed 已实现）差距最大的点——规则设计可参照 autolammps，执行架构以 MDagent 实现为准。**
2. MB-008（目标-模型一致性）/MB-009（诊断 compute）在 reviewer prompt 中引用但未落进 mandatory-checks.md；其依赖的 `knowledge/rules/goal-feature-registry.md`、`diagnostic-compute-requirements.md` 不存在。
3. `knowledge/manuals/`、`knowledge/corrections/`、`knowledge/cases/raw/`、`knowledge/potentials/` 被 prompt 大量引用但目录不存在（由流水线按需创建或经历过迁移）。
4. 引擎（lammps-engine）是骨架：除 `/api/engine/search` 外全部占位，未被主 CLI 集成；layered 索引的 get_linked/verify_script 为 stub。
5. 双状态目录约定并存（standalone `.lammps-project/` vs team `project-memory/`+`scratchpad/`），一致性靠约定维持。
6. settings.json（6 个 server stub 文件同）为骨架；dashboard 的 chat 会话依赖 spawn CLI 子进程而非 daemon。

**安全提示**：`.angsheng/settings.json` 含**真实 DeepSeek API key**（sk-75d7…，指向 api.deepseek.com/anthropic，模型 deepseek-v4-flash[1m]）已提交进 git。仓库虽为私有，任何共享/开源动作前必须先清除并轮换该 key。

**对二开（sim-agent-runtime / 证据系统）最有参照价值的机制**（均只借机制不借内容）：
- 分阶段状态机 + 文件即状态 + 运行记录链自动接续（§2.1/2.5/2.6）；
- 门禁三值 + 3x REVISE 熔断 + 有界修复 + 回滚目标推断（§2.1/2.6）；
- MB 强制检查表 + 双证据规则 + 语义命令清单（§2.4）——MDagent 证据系统 R1-R19 已覆盖并超出其文档化部分；
- 交接包（packet）schema 与阶段产物契约（§2.5）；
- 分层知识账本 + 触发式窄读（core-checks 必读、详录按 ID 检索）——**-73% 上下文的实证**（§5.5）；
- KB 入库状态机 + 自动分类置信度 + 幂等账本块合并（强化而非堆重复）（§3.4）；
- FTS 两阶段打分（SQL bm25 加权 + queryType 分层 rerank + 案例分组多样性）（§3.3）；
- 高危词人工队列（禁自动晋升）与晋升三判据（§3.2/3.7）；
- **skill-mine 技能挖掘**（§4.7）：使用历史→LLM 结构化候选→暂存→人工审查安装，全程手动、证据化的候选格式（evidence+rationale+frequency）；
- **simplifications.md 式"有意简化决策日志"**（§2.12）：把"不做什么"显式记录防漂移；
- 内容寻址对象库 + 版本链的多机同步协议（§3.5）；
- 编译期 feature 裁剪三端一致机制（§5.1）；
- 自我进化安全基线（提案/候选副本/离线验证/人审 apply，绝不自动改生产）（§5.5）。

---

*文档版本 v1.2 · 2026-10-15 · 由四路源码深扫合成；对应仓库快照 commit feaf7f6。v1.1 修订（用户指正）：新增 §2.4 证据系统完整专节。v1.2 修订（二轮复查）：新增 §2.12 规则文件全景与设计哲学双轨（含 simplifications.md 十条 MVP 决策——证据门不代码化系第 5 条有意边界）、§4.7 skill-mine 技能挖掘子系统、/workflow 与 /paper-reproduce-auto 命令细节、次要服务族清单。*
