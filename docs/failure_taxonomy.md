# 统一故障分类法（v7矛盾7 · 2026-09-15）

> 用途：任何告警先归层、再归类，按表查检测/恢复入口；跨层组合故障单列——
> 单层视角看不出的故障形态都在这里。

## 按层分类

| 层 | 故障模式 | 检测方式 | 恢复方式 |
|---|---|---|---|
| LAMMPS | bondchk / hbondchk / lost_atoms | monitor_agent（致命错误扫描） | checkpoint_restart + 学到的参数（safezone/mincap/tdamp） |
| LAMMPS | 双停滞（墙钟+步数） | monitor 双时钟 check_dual_clock | 杀进程→守护续跑 |
| 编排器 | 死锁 / 状态机错误 | watchdog L1（state mtime>15min） | `systemctl restart mdorch` |
| 守护 | 进程死 / 误杀 | watchdog L3（pgrep auto_followup3） | setsid 重启（md-guard 单元） |
| watchdog | 进程死 / 误判 | 日报缺席（Meta 检查>3 天） | 人工重启 + last_resort_alert 保底 |
| 记忆层 | 矛盾 / 过时 / 噪音 | Meta check_knowledge_health | 蒸馏 / supersede / retract |
| 进化层 | 提案堆积 / 回滚循环 | Meta check_evolution_health（回滚率/连续回滚） | proposal_reviewer 人工审 |
| 进化层 | 方向错误（越修越差） | Meta 适应度趋势（红色计数递增） | 人工审查进化方向 |
| Meta-Observer | 误报 / 漏报 | watchdog L6（报告停更>2h） | 修复判断逻辑 |
| Gate | 放行物理错误输入 | gate_physics_warn.jsonl 留痕 | 警告级（不拒发），人工复核 |
| 资源 | 预算超限 | budget_manager 四层（gate 拒发 black） | 硬停等人类 |

## 跨层组合故障（单层视角无法识别）

| 组合 | 表现 | 根因 | 现在的解 |
|---|---|---|---|
| LAMMPS 崩 + 编排器误判正常 | 温度 0K 但 decision=CONTINUE | 编排器只看进程/步数不看物理量 | v7矛盾4：物理维度一票否决 |
| 守护误杀 + 编排器不知道 | 进程消失无告警 | pkill -f 自匹配误伤 | 守护 v3 用精确 cmdline 匹配 |
| 记忆矛盾 + 进化方向错误 | 策略反复回滚 | 知识库有矛盾原子未 supersede | v6修复八 supersede 链 + v7矛盾2 适应度趋势 |
| Gate 放行 + 物理错误 | 模拟跑完但结果错 | Gate 曾只查安全不查物理 | v6修复七 物理校验警告 |
| 权限墙 + 自动恢复 | 恢复用默认参数再崩循环 | 记忆层知识到不了执行层 | v6修复二 + v7矛盾3 学到的参数 |
| watchdog 死 + 无人发现 | 日报中断，系统裸奔 | "谁看医生"无限递归无终止 | v7矛盾1 last_resort_alert（登录路径保底） |
| IO 慢 + 误判崩溃 | 输出停更但步数在走 | 单时钟监控 | v7矛盾6 双时钟分离判定 |

## 新增故障模式时的纪律

1. 先归层：属于哪一层？该层已有检测覆盖吗？
2. 查组合：会不会和其他层的正常行为叠加出诡异表现？（查上表）
3. 补入口：检测方式写进对应层的检查函数，恢复方式写进策略库或 SOP
4. 更新本表与 feature_interaction_matrix.md
