# 证据系统实弹工装（evidence_live）

> **这套东西是干什么的**：用十一个真实算例，验证证据系统 v2.3（含核查面闭环）的
> "证据校核"能力——每一项仿真设计、每一条计算结论，是否都有分级、适用、
> 可重算的证据支撑。不是演示页面，是可重跑的验收工装。

## 为什么在这里

MDagent 的正式证据系统在 `evolution/`（门禁、状态机、评估、对账）与 `mcp/`
（工具面、知识包）。本目录是它的**端到端验收工装**：真实调用 MCP 工具面、
真实启动求解器、真实留账，最后从产物生成 HTML 汇报。

## 目录

| 文件 | 用途 |
|---|---|
| `mcp_client.py` | stdio JSON-RPC 客户端：像 dsh 一样真实调用 md_mcp 进程，双向审计账 |
| `kit_local/agents/runtime/v3.3/claude_interface.py` | 本地审计执行桥：契约对齐 kit 的 `claude_interface.py`（白名单 + shell=False + 逐条审计）；**不改 MDagent 代码**，用 `MDSIM_KIT_BASE` 注入 |
| `drive_case.py` | 十案例驱动（扮演 persona 中的 coordinator 角色）：状态机流转、决策工件、分级证据、门禁调用、冲突追问、评估、对账、案例库 |
| `parse_lm.py` | 求解器日志解析——所有"事实"的唯一来源 |
| `cases/` | 输入脚本（含两个**故意写错**的配置：恒压拉伸、单向摩擦） |
| `make_html_report.py` | 从 `live_data/` 全产物生成汇报（内联片段 + `report.html`），零手工数字 |
| `../../tests/e2e_evidence/run_functional_matrix.py` | **功能矩阵**：规则×知识库×核查面×高危操作逐项验证（含知识注入/撤销、10 个方法论维度正反例、核查面缺项/提问、真实日志重算） |
| `report.html` | 存档版汇报（离线可看） |

## 怎么跑

```bash
# 0) 依赖：本机求解器（默认路径见 drive_case.py 顶部 LMP）；Python 3.11+
# 1) 清空上次运行产物
rm -rf live_data kit_local/logs
# 2) 跑十一案例（约 4-6 分钟；末行应输出 54/54）
python drive_case.py
# 3) 功能矩阵（独立沙箱，不跑求解器；逐项验证设计功能）
python ../../tests/e2e_evidence/run_functional_matrix.py
# 4) 重新生成报告
python make_html_report.py
```

Windows 上审计桥通过 `MDSIM_KIT_BASE` 自动注入（见 `mcp_client.py`）；
Linux 服务器可改用真实 kit（`md_config.json` 的 `exec_bridge.sudo_user` 恢复 `mdorch`）。

## 十一个案例与它证明了什么

| 案例 | 证明了什么 |
|---|---|
| SIMPLE-04 | 基线：扰动晶体最小化，能量下降 + 收敛 |
| LIT-01 | **文献复现**：Foiles 1986 铜 EAM，a0=3.6150 / Ec=-3.5400，零偏差 |
| LIT-02 | 双实现互证：LAMMPS 与独立解析晶格和，四位小数一致 |
| LIT-03 | **弹性模量方法论深度分析**：六维度（方法选型/应变线性/弛豫/尺寸/温度口径/步长），从 ±0.4% 与 ±0.8% 两组实测 C11 判线性区 |
| COMPLEX-05 | 执行策略（S2：大规模前必须有小规模成功）+ 评估提示驱动步长修订 + 全量数值对账 |
| UNREASONABLE-06 | **物理合理性拦截（E3）**：dt*=0.046 超失稳线，计划段拦下，零执行 |
| BAD-CONFIG-07 | **方法学合规（E4）**：恒压 + 变形 = 拉不断，文件写作段被拦，零执行 |
| BAD-CONFIG-08 | **方法学合规（E4）**：往复摩擦做成单向，文件写作段被拦，零执行 |
| NOBASIS-09 | **设计有据（E1）**：三项决策只有口头估算，证据门槛未达，计划段拦下，零执行 |
| OVERRIDE-10 | **人工覆盖协议**：与文献冲突 → 冲突包追问 → 用户答复 → 放行并记录异议 → 二次确认晋升最高权限人工经验 |
| SURFACE-11 | **核查面闭环**：空核查面 → 19 个问题 → 两项待取证被点名 → 补齐过闸 → 证据沉淀为 atom |

## 产物与追溯

- `live_data/tasks/<case>/`：状态机、决策工件（不可变）、门禁账、评估记录（快检/深度）、
  数值对账记录、案例库条目
- `live_data/work/<case>/`：输入脚本与留档日志
- `kit_local/logs/claude_operations.jsonl`：执行审计账（每次真实执行一条）
- `report.html`：人读汇总（系统全景 / 生效点与拦截 / 十案例逐步判断 / 复核与教训）

## 注意

- 本目录是**验收工装**，不是生产依赖；生产系统不需要运行它。
- `live_data/` 是可重生成的运行产物，默认不随仓库提交（见 `.gitignore`）。
- 知识包与判据在 `mcp/review_experience/`：锚、方法学约束、方法论维度都是**数据**，
  新增判据请走人工闸，不改门禁代码。
