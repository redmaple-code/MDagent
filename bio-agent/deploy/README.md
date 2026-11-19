# Bio 远程部署候选

当前为本地源码候选，未上传或启动服务器。此次仅做工程检查；实际 SSH、Linux/CUDA、原登录、平台写门、客户对话和科学计算验收按用户要求推迟。原 LAMMPS 配置、生产账户及数据保持原状。

控制端复用 DSH `0.1.0-rc.6`、TypeBox `1.1.38` 和原网页。科学依赖及全部构建、拓扑、参数化、OpenMM、结果检查与分析部署在最终计算端；不复制 macOS Conda 环境或完整 AutoMD SDK 到网页服务器。

## 三种传输

- `node-world`：原账号的登记节点就是最终计算端。只接受原 `RemoteSubprocessRuntime` 和完全匹配的 `/v1/world/<nodeId>` 绑定。
- `node-ssh`：原登记节点只是 SSH 接入壳。壳上部署标准库 `run_biogateway.py` 和公开连接描述，复用已有私有 SSH config / host key；科学 Bridge 只在 SSH 后的最终计算端启动。
- `ssh`：操作者明确指定的直接 SSH 隔离分支。使用 BatchMode、StrictHostKeyChecking、固定 argv，无代理/端口转发；不接受密码弹窗、不自动信任新 host key。它不替代用户选定的原节点通道。

连接失败不回退本地 Python，不重放未知副作用。原 `md_mcp.py` 的传统 SSH 模式只读，原云端 LAMMPS 写门走另外的受控命令链；不能直接把它当作 Bio 写权限。此处没有读取、复制或生成原平台凭据。

## 最终计算目标

`execution-target.example.json` 只含公开字段，所有占位路径、UID、租户/节点和主机哈希都必须替换。该描述由操作者控制，文件不可被组/其他用户写入；项目首次连接后不可原地换目标。

操作者后续可在最终计算服务器用已有 Python 运行下列只读描述命令，输出 JSON 重定向到新配置文件。该命令不探测科学包、不创建项目、不运行 MD；本轮没有远程执行它。

```bash
/absolute/existing/env/bin/python -I -B /absolute/release/run_biotarget.py \
  --tenant-id actual-tenant --node-id n_actual-node --target-id actual-compute \
  --python /absolute/existing/env/bin/python \
  --projects-root /absolute/compute/projects \
  --quota-root /absolute/compute/bio-resource-pool \
  --cpu-threads 8 --max-concurrent-jobs 1
```

授权 CUDA 时另加如 `--gpu-devices 0`。多个 Bio 项目共享资源池时，必须使用相同账户、私有配额目录和一致的总配额；不要把 Bio 配额当成整台服务器已有作业的总调度器。科学运行要求已有兼容的 Python 3.10+ 环境；实际包版本及 GPU 能力仍需后续核实。

## 原平台权限接口

服务器节点模式必须配置 `platformGateModule`，由原租户权限层提供操作者拥有的本地 `.mjs` 文件。模块不能被组/其他用户写入，且必须导出 `createBioGate({ctx, executionTarget})`，返回具有 `authorize(request, signal)` 的服务。

请求是 `bio.platform-access.v1`，包含 `tenantId/nodeId/executionTargetSha256/sessionId/action`，涉及具体方案时还包含 `workflowId/revision`。模块只有在原账号、节点及操作权限通过后才能返回 `allowed: true`，并回填全部身份、action 及方案字段。任何拒绝、字段不匹配、缺失模块或异常均阻止操作；模型不能访问该接口。后台推进单独请求 `advance` 权限，科学核心仍逐阶段校验目标绑定的人工授权。

当前 GitHub 候选补入 `cloud/bio.py` 的 `/v1/bio/access` 与 `web/runtime/platform-gate.mjs`，复用原登记节点的短期 world 令牌，核对账号、节点、目标哈希及允许的固定动作。该接口只授权平台访问，不签任意命令，也不替代科学计算的人工授权。缺少操作者的 `accounts/bio-targets/<username>.json` 时默认禁用；配置与原租户入口详见[平台集成](../../docs/bio-platform-integration.md)。实际远程兼容性仍属于后续验收。

网页的人类入口复用 Connection RPC `/bio-workflow`，限制当前 Bio 会话，人工 actor 由服务器产生；Host/Origin/CSRF 防护只是浏览器信任边界。生产必须继续经过原来已认证的每租户网关，不得将这个 RPC 裸露为公共接口。

## 生成原租户的附加配置

`tenant-profile.mjs` 接受公开操作者配置，输出到全新的候选目录；不会修改原 profile、SSH 凭据或 LAMMPS preset。保留原 roster 的默认 Agent、发现根及用户设置，仅附加 `bio` preset 和独立 UI 插件。

```bash
node /absolute/release/deploy/tenant-profile.mjs --config /absolute/operator/public-bio-profile.json
```

配置字段为 `tenantId/output/executionTargetFile/compilerRoot/projectsRoot/platformGateModule/mode/rosterConfig`。`projectsRoot` 是控制端该租户的收据目录；计算端路径只来自目标文件。`rosterConfig` 使用原公开 `default/roots/includeUserRoot`。如原 roster 插件 ID 不同，填写 `rosterPluginId`；`node-ssh` 还要求接入节点的 `gatewayPython/gatewayReleaseRoot/gatewayConnectionFile`。脚本输出 `GENERATED_NOT_APPLIED`，不会自动接入原网页。

后续应用时需要复用原 provider 和已认证的租户实例、在现有依赖发现路径注册 `bio-web-tool-cards`；不要替换原节点 provider、默认模型或登录服务。具体租户/节点尚待用户提供，当前不生成真实生产配置。

## 隔离的直接 SSH 入口

此入口仅监听 `127.0.0.1`，使用新 DSH home，不读取生产模型凭据；原 API 的隔离方式与配置均保留。其显式 `bio-grant-only` 策略适用于这个隔离入口，仍要求目标绑定的人工科学授权；节点模式拒绝此策略。

```bash
node /absolute/release/deploy/server-launch.mjs \
  --dsh-root /opt/mdagent/code --state /absolute/new/isolated-state \
  --connection /absolute/operator/connection.json \
  --execution-target /absolute/operator/execution-target.json --port 3081
```

`--dump-config` 核对候选配置；这个入口不代表原账号已接入。`deploy/smoke.mjs` 接受同样的四个路径参数，会实际连接 SSH、探测能力并生成无授权草案；本轮不执行它。不要把草案与状态 readback 当作 MD 或 GPU 验收。

## 源包与数据

```bash
python3 -I -B bio-agent/deploy/build_bundle.py
```

源包只含 allowlist 中的源码、公开示例与 provenance；不含依赖环境、API Key、SSH 密钥、会话、轨迹或执行授权。`verify_release.py` 只验证源包及原科学核心完整性，不进行计算。控制端、接入壳及最终端可使用同一源包的不同固定入口；各落点的实际路径由操作者描述。

已产生的原始结构、数据库、日志、轨迹、checkpoint、历史授权与旧源码包保留。旧的无目标绑定授权不升级；改环境、目标或方案时生成新版本再审阅。用户入口与恢复语义见 [远程工作流](../docs/remote-workflow.md)。
