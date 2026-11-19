# Bio remote workflow

本目录提供独立的 Bio/OpenMM 工具与网页任务面板。体系构建、拓扑生成、
参数化、模拟和分析运行在明确绑定的最终计算主机；网页主机负责调度、
权限与状态显示。原 `md-sim-assistant` 保留原有行为和模型配置。

当前本地候选已补入节点注册、remote-world、租户网页源码，并提供固定目标的
平台权限适配器。默认不启用 Bio：只有操作者配置匹配账号/节点的目标与操作
权限后，租户网页才发现独立 Bio 预设。该候选尚未部署，不能作为远程运行或
完整生物科学能力的验收结果。

## Repository layout

- `src/bioagent/`: typed workflow, ownership, grants, queue, recovery and artifacts.
- `runtime/ksh_openmm/`: the nine provenance-verified scientific source files.
- `agent/`: bounded Bridge protocol and remote-world/SSH transports.
- `web/runtime/`: scoped tool catalog, human RPC and task panel.
- `../dsh-plugins/dsh-bio-agent/`: workspace DSH server plugin wrapper.
- `../dsh-plugins/dsh-bio-ui/`: deterministic native web client packaging.
- `tests/`: offline engineering tests. Test inputs are small declared fixtures.

## Prerequisites

Use DSH `0.1.0-rc.6`, TypeBox `1.1.38`, Schemastery `3.18.1`, and an already
compatible compute Python. Package/dependency installation is a separate operator
action; this PR preparation does not install or upgrade environments.

Runtime target, tenant account, SSH identity, private keys and model credentials
stay outside Git. The cloud integration reads an operator-owned binding from
`accounts/bio-targets/<username>.json` and preserves the original default agent.
See [platform integration](../docs/bio-platform-integration.md) for the binding,
existing gateway and permission checks. The independent `deploy/tenant-profile.mjs`
generator remains available for producing an unapplied candidate with
`agentPlugin: "dsh-bio-agent"`.

`../presets/bio/` is a configuration template. Placeholders deliberately prevent
it from launching a compute process without explicit tenant/target configuration.
No automatic local-compute fallback, provider change or default-agent change is
introduced.

Build the web wrapper with `node scripts/build-bio-ui.mjs` from repository root.
Engineering tests accept `BIO_TEST_PYTHON` for an existing Python executable.
Scientific execution, Linux/CUDA and real customer acceptance remain separate.

The optional `agent/sdk.mjs` adapter requires a separately supplied AutoMD SDK.
The DSH plugin and its offline web tests do not load that SDK.

See [remote workflow](docs/remote-workflow.md) and
[deployment prerequisites](deploy/README.md).
# Web visualization

Bio 聊天提供可收起的结构/轨迹预览：进入已有 Bio 任务会话后，点击页头“结构与轨迹”选择登记结构或已完成阶段结果，再用播放/暂停和真实时间条查看。计算端生成有界 XTC，预览字节只经本地 SSH 连接器直取，浏览器本地渲染；服务器负责元数据与授权。按钮不调用模型。安装、权限、构建、恢复和验收见 [网页预览说明](docs/web-visualization.md)。源码候选不自动启用生产配置或部署。
# Optional HPC-to-local preview

The DSH molecular dock supports an optional local SSH preview connector. Authorization stays on the server; registered preview bytes can bypass its bandwidth limit. Direct mode defaults off, needs explicit operator configuration, and is currently verified at the local engineering layer. See [local preview connector](docs/local-preview-connector.md). Real SSH, HTTPS deployment and speed improvement remain separate acceptance gates.
