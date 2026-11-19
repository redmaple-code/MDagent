# MDagent 子系统模块包 / MDagent subsystem packages

本目录提供知识系统、证据系统、门禁系统和物理一致性审查的两种交付方式。两者直接来自 MDagent 子系统交接基线，不依赖 Pi 工程；请根据需要选择，**两套不是可以无条件互换的生产发布版本**。

This directory provides two distributions of MDagent's knowledge, evidence, gate and physical-consistency subsystems. They are independent of the Pi project. Choose the distribution according to the integration scope below; neither is an unconditional production release.

| 交付 / Distribution | 适用范围 / Intended use | 依赖与边界 / Dependencies and boundaries |
|---|---|---|
| [原生拆分版 / Native split v0.1.1](mdagent-modules/README.md) | 需要保留原 UKB、证据 v3、前置/阶段门、物理一致性模块及原 MCP、测试和设计材料的开发者；适合有源码级集成需求的现有 agent 宿主。 | 共享底座和模块间依赖须按清单组合；部分路径需要 NumPy、外部模型、求解器或执行 kit。原始门禁写入、可信审批和审计仍有未修复问题。 |
| [自包含运行版 / Self-contained v0.2.0](mdagent-standalone/README.md) | 希望分别部署四个模块、通过标准输入输出协议接入任意宿主，并优先使用 Python 标准库的开发者。 | 每包独立运行，不要求安装其他包或第三方 Python 库；为保留行为内嵌部分共享/跨模块兼容源码。标准化接口只覆盖各包列出的子集，自动 PDF 解析、模型审阅和真实求解器需要外部能力。 |

## 下载 / Downloads

每套完整包包含源码、说明、测试与许可证；也可按模块下载。Each bundle contains source, documentation, tests and license notices; individual modules are also available.

- **原生拆分版**：[完整包](mdagent-modules/downloads/mdagent-modules-0.1.1.zip) · [知识](mdagent-modules/downloads/mdagent-knowledge-0.1.1.zip) · [证据](mdagent-modules/downloads/mdagent-evidence-0.1.1.zip) · [门禁](mdagent-modules/downloads/mdagent-gate-0.1.1.zip) · [一致性](mdagent-modules/downloads/mdagent-consistency-0.1.1.zip) · [共享底座](mdagent-modules/downloads/mdagent-shared-0.1.1.zip)。各包的必需依赖见[原生组合说明](mdagent-modules/README.md)。
- **自包含运行版**：[完整包](mdagent-standalone/downloads/mdagent-standalone-0.2.0.zip) · [知识](mdagent-standalone/downloads/mdagent-standalone-knowledge-0.2.0.zip) · [证据](mdagent-standalone/downloads/mdagent-standalone-evidence-0.2.0.zip) · [门禁](mdagent-standalone/downloads/mdagent-standalone-gate-0.2.0.zip) · [一致性](mdagent-standalone/downloads/mdagent-standalone-consistency-0.2.0.zip)。每个运行包只需 Python 3.11 标准库，逐行 JSON 接口见[调用协议](mdagent-standalone/docs/MODULE-INTERFACE.md)。

相邻 `.sha256` 文件用于核对下载字节；仓库内各模块还有逐文件清单。哈希清单不是发行者签名。

## 安全与科学边界 / Safety and scientific scope

- 拆分版本保留原有实现：算例前缀定位、门禁方案写入/审计、可信人工确认和审阅截断等已知问题未全部修复；不得仅凭这些入口自动放行生产任务。
- 自包含版默认禁止写操作；审计记录保护不等于宿主认证、资源隔离、人工授权或外部审计锚。标准化门禁接口未开放已知有缺陷的原方案写入口。
- 一致性结果仅反映所检查的声明与输入是否匹配；`PASS` 不代表物理模型正确、数值收敛或具有执行许可。外部模型和求解器不能由标准库取代。
- UKB 的 SQLite 备份不包括原文 blob；恢复必须同时核对所有引用对象。真实生产库、真实 LLM、外部执行 kit、HPC 和 Linux 环境并未因本次测试获得完整验收。

具体测试数、跳过项、复测记录与未验证范围见[拆分版审计](mdagent-modules/AUDIT-AND-VERIFICATION.md)和[自包含版审计](mdagent-standalone/AUDIT-AND-VERIFICATION.md)。随包提供原始 PolyForm Noncommercial 1.0.0 许可证和 NOTICE；适用授权以随包文本为准。
