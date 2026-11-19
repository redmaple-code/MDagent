# 特性交互矩阵（v7矛盾5 · 2026-09-15）

> 用途：每加一个新特性，先查本表已有交互规则；每修一对交互，补一行。
> 单测通过 ≠ 组合安全——历史上多数事故发生在两特性交界。

| 特性A | 特性B | 交互规则 | 测试用例 | 现状 |
|---|---|---|---|---|
| 权限墙 | 自动恢复 | 恢复必须用学到的参数（safezone/mincap/tdamp），不是默认值 | 崩→恢复→查 input 的 safezone 是否来自记忆层 | ✅ v6修复二 + v7矛盾3 |
| 蜂群回退 | 策略去重 | restart 类策略不受去重拦截，无限重试 | 连续崩 6 次都能恢复 | ✅ 已修 S1 |
| 蒸馏 | supersession | 蒸馏产出决策树后，旧原子必须标 superseded，查询不可见 | 蒸馏后 load_atoms 不返回旧原子，净减少 | ✅ v6修复十二 |
| 蒸馏 | 幂等 | 已蒸馏 kind 未积累翻倍新证据不重蒸 | 连跑两次 distiller，原子数不变 | ✅ v6修复十二（压测⑤） |
| Meta-Observer | 看板 | 温度异常时看板必须显示 CRITICAL，不许绿色 | 温度 0K 时看板非绿 | ⚠️ 共识检查已加（v7矛盾4），看板侧未测 |
| watchdog | Meta-Observer | Meta 报告 >2h 未更新 = L6 告警 | kill Meta cron→watchdog 报 L6 | ✅ L6 已在线 |
| 守护 | 编排器 | 守护启动的进程，编排器必须领养（adopted_pids） | 守护发车后编排器 phase 变化且不失控 | ✅ adopt 机制在线 |
| 编排器 | 记忆层 | MONITOR 异常/崩溃必须自动写原子，不只进日志 | 制造异常→atoms.jsonl 出现 monitor-anomaly | ✅ v6修复一（重启后生效） |
| Gate | 物理合理性 | Gate 不只查安全还查物理（timestep/nvt温度/力场），警告不拒发 | 5000K nvt 输入 → gate_physics_warn.jsonl 留痕 | ✅ v6修复七 |
| monitor | 温度程序 | 判据随物理阶段浮动（ramp 45%/恒温 30%，极端阈值 max(2000,目标×1.3)） | RUN2500 恒温 2500K 不报 CRITICAL | ✅ v6修复五（压测⑥） |
| 双时钟 | 停滞判定 | 墙钟停滞（log mtime）与模拟停滞（步数）分开判，双停滞才 CRITICAL | IO 慢但步数走 → WARNING 不是 CRITICAL | ✅ v7矛盾6 |
| 共识 | 单组件判断 | 物理维度一票否决多数派的"正常" | 末行温度 0K → Meta 判 RED  regardless of phase | ✅ v7矛盾4 |
