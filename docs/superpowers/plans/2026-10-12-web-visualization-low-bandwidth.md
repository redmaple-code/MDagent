# MDagent Web Visualization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 在现有 Bio 网页中查看结构与真实轨迹，并在共享 5 Mbps 出口下实现有界、可追溯的预览、详细窗口和 Agent 显示联动。

**Architecture:** 最终计算节点校验接受产物并派生有界 XTC 预览；控制服务复用租户身份和现有固定 Bridge，提供统一限速的同源二进制出口。浏览器通过独立 Mol* 适配器渲染，提取 KSH 的控件、帧映射和用户干预保护；显示状态不替代科学接受或计算授权。

**Tech Stack:** 已有 Python/MDAnalysis、Node/DSH `0.1.0-rc.6`、FastAPI 和 React 插槽；独立浏览器依赖 Mol* `5.13.1`、构建依赖 Vite `8.2.2`，直接依赖精确锁定。

**Spec:** [已确认设计](../specs/2026-10-12-web-visualization-low-bandwidth-design.md)。状态：`IMPLEMENTED_LOCALLY / NOT_DEPLOYED`；用户已于 2026-10-12 确认本会话实施及计划内依赖安装。

## Global Constraints

- 基线仓库 `/Users/a0000/项目/auto-md/mdagent-pr`，HEAD `b8c2b719c5ccf928a6b63ff29de605f321d0145f`；保护用户现有文件与原始科研数据。
- 所有用户共享 5 Mbps 公网出口；全部署预览聚合最多 4,000,000 bit/s，不能按租户独立领取该预算。
- `overview` 最多 200 个真实源帧，包含首末帧；默认蛋白 Cα 骨架与模型已识别配体重原子。
- XTC `precision=3`，记录 0.001 nm 刻度；往返坐标误差 <=0.01 Å；真实时间使用已验证 sidecar。
- `sourceFrameIndex`、`segmentFrameIndex` 从 0 开始；UI 从 1 开始。重复/倒序时间拒绝，时间间断显示。
- HTTP 块目标 <=256 KiB，硬上限 1 MiB；每视图最多 2 个在途请求。
- 每窗口最多 64 帧，解码坐标 <=32 MiB；当前与预取窗口合计 <=64 MiB。
- 默认播放 10 个真实预览帧/s，可选 1–20；无已确认下一帧时显示缓冲，不补造计算帧。
- 每项目预览存储上限 256 MiB；每租户并发派生最多 1 个；纳入目标资源配额，不使用 GPU。
- 首版每会话仅维护当前视图状态，不追加逐帧历史，不跨租户持久保存坐标缓存。
- 原始 DCD、输入、分析、日志、checkpoint 与已发布预览不自动删除；失败残留保留并计入配额。
- 平台权限、科学审批与显示操作分别校验；原科学工具、合法 Bio 会话和原材料会话继续可用。
- 无任意路径、Shell、Python/JavaScript 或自由形式 MDAnalysis 选择；不修改生产配置、凭据或原冻结 DSH 前端。
- KSH 复用按设计附录的逐文件 SHA-256 记录来源；不复制完整 KSH、Qt/PyMOL/OpenCode 或环境。
- 新增依赖安装单独获授权；不升级 DSH、React、科学环境或原 workspace 的依赖版本。
- 不自动 commit、push、部署或运行生产 MD。每任务末尾只检查 diff 与验证证据；提交需用户另行明确要求。

## Review Focus

1. 单帧、非整除抽样与跨段边界：真实末帧只出现一次，静态结构禁用多帧播放。Task 2/5 明确测试。
2. 权限、会话或源文件在异步取数中变化：迟到块不得进入当前视图，原 Bio 正常权限不因新工具误拒绝。Task 4/6 明确测试。
3. 原节点断开时派生 worker 状态未知：保留 owned lease，重试核对原 job，不重复生成或提前释放资源。Task 3 明确测试。
4. 多用户与静态资源冷加载：使用一个共享出口限速器，记录首次资源加载；不能用各连接 5 Mbps 或已缓存结果代替。Task 4/7 明确测试。
5. 纯水、缺 Cα 或模型未识别配体：显示明确集合或静态周期盒，不猜配体、不随机丢原子。Task 2/5 明确测试。

## 文件结构与共享接口

新增文件按职责落在 `bio-agent/src/bioagent/visualization/`、`bio-agent/web/runtime/visualization/`、`bio-agent/web/visualization/` 与 `cloud/`。现有 `client.source.js` 仅负责挂载，渲染器、状态机和取数各自独立。

统一 JSON 来源字段：

```text
SourceRef = {kind: input|accepted, sourceId, sourceSha256,
             workflowId: string|null, revision: int|null,
             stageId: string|null, attemptId: string|null}
PreviewRequest = {source: SourceRef, profile: overview|detail,
                  atomSet: overview|solute-heavy|all,
                  startFrame: int, endFrame: int, stride: int}
PreviewManifest = {schemaVersion: bio.preview.v1, previewId, source: SourceRef,
                   atomCount, sourceFrameCount, atomMapFile, topologyFile,
                   frames: [{sourceFrameIndex,segmentId,segmentFrameIndex,timePs}],
                   files: [{fileId,byteSize,sha256,kind,previewFrameStart,previewFrameCount}],
                   precision, previewOnly: true}
ViewBinding = {sessionId,viewportId,sourceSha256,sourceRevision,
               visualRevision,frameRevision,cameraRevision}
ViewCommand = {operationId: UUID, expected: ViewBinding,
               kind: openSource|setRepresentation|select|focus|seekTime|play|pause|observe,
               payload: fixed object for the selected kind}
```

`sourceId`/`fileId` 是服务端目录记录引用，不是客户端路径。`PreviewManifest` 的 atom map 单独作为有界文件，不能将大原子列表塞进 128 KiB Bridge 回复；大记录沿用分页/受限 record 分块。`SourceRef.kind=input` 的 workflow/stage 字段必须为 null，并只提供静态结构；轨迹来自同一已接受 attempt。

每个 XTC 文件的 `previewFrameStart` 从 0 开始、`previewFrameCount` 为正整数；其他文件这两个字段为 null。播放帧据此定位独立 XTC 段与 localIndex，不能按 URL 顺序猜测所属文件。

`ViewBinding.sourceSha256` 在尚无来源时为 null。显示 payload 固定为：openSource 的 `{source}`；setRepresentation 的 `{representation}`（cartoon/sticks/spheres/lines）；select 的 `{sourceAtomIndices}`（去重且最多 512 个）；seekTime 的 `{timePs}`；play 的 `{startFrame,endFrame,fps,direction,loop}`；focus/pause/observe 的空对象。seekTime 返回请求时间与实际最近真实帧时间，等距时选较早帧；范围与单帧行为经契约检查。seek 只依赖来源与帧版本并保留最新相机；会改变相机的 focus 另检查相机版本。`frameRevision` 记录导航或播放操作改变，同一播放 operation 的自动推进只更新当前帧/时间；因此模型可以暂停或定位正在播放的视图，用户另行拖动仍会使旧命令冲突。

已核实可复用的云测试解释器为 `/opt/anaconda3/bin/python`，FastAPI `0.115.14`、httpx `0.28.1`；科学/派生测试使用 `/opt/anaconda3/envs/modeling/bin/python`。这些是本地工程环境记录，执行前仍检查状态，不宣称等于生产安装版本。

### Task 1: 闭合预览与显示契约

**Files:** Create `bio-agent/visualization/contracts.json`、`bio-agent/src/bioagent/visualization/__init__.py`、`bio-agent/src/bioagent/visualization/contracts.py`、`bio-agent/web/runtime/visualization/contracts.mjs`; Test `bio-agent/tests/test_preview_contracts.py`、`bio-agent/web/runtime/tests/preview-contracts.test.mjs`。

**Interfaces:** Produces Python `validate_preview_request(value: dict) -> dict`、`validate_manifest(value: dict) -> dict`；JS `validatePreviewRequest(value)`、`validateViewCommand(value)`、`previewLimits`。归一化后的字段严格等于契约，额外字段/布尔冒充整数/非有限数字均拒绝。固定 `precision` 和预算由服务决定，不由模型扩大。

- [x] **Step 1: 写失败测试。** `test_closed_request_rejects_path_and_script` 拒绝额外 `path`/`python`、负帧、布尔帧、缺来源哈希；`test_limits_are_identical` 在 Python/Node 都断言 200、64、32/64 MiB、1 MiB、1–20 的设计值。`test_input_is_static_only` 拒绝 input 与 accepted 字段混用。

```python
with self.assertRaisesRegex(BioError, "INVALID_PREVIEW_REQUEST"):
    validate_preview_request({**valid_request, "path": "/foreign/trajectory.dcd"})
with self.assertRaisesRegex(BioError, "INVALID_PREVIEW_REQUEST"):
    validate_preview_request({**valid_request, "startFrame": True})
```

- [x] **Step 2: 运行并确认因缺少新契约失败。** 在 `bio-agent/` 执行 `PYTHONPATH=src /opt/anaconda3/envs/modeling/bin/python -B -m unittest tests.test_preview_contracts -v`；仓库根执行 `node --test bio-agent/web/runtime/tests/preview-contracts.test.mjs`。
- [x] **Step 3: 实现上述验证接口。** 标准库 Python 与现有 Node JSON 能力读取同一契约，不安装新的 schema 库；request/control 回复仍符合原 Bridge 上限。
- [x] **Step 4: 重跑 Step 2。** 预期全部 PASS；加入 Python 与 Node 共用的合法/非法 JSON 样例，证明两端结论一致。
- [x] **Step 5: 检查 diff。** 新契约独立于现有 `agent/contracts.json` 中 11 个科学工具，不改变原科学意图 schema 或审批接口。

### Task 2: 接受来源、原子映射与固定预览派生

**Files:** Create `bio-agent/src/bioagent/visualization/sources.py`、`bio-agent/src/bioagent/visualization/preview_plan.py`、`bio-agent/src/bioagent/visualization/preview_files.py`; Test `bio-agent/tests/test_preview_sources.py`、`bio-agent/tests/test_preview_files.py`、`bio-agent/tests/preview_fixture.py`。

**Interfaces:** Consumes Task 1；Produces `resolve_source(controller, ref: dict) -> dict`、`plan_preview(source: dict, request: dict) -> dict`、`write_preview(source: dict, plan: dict, destination: Path) -> dict`。测试辅助 `make_preview_fixture(root: Path, *, frames: int, with_protein: bool=True) -> dict` 创建明确标记工程用途的拓扑/DCD/时间与接受记录，不调用 OpenMM 动力学。

- [x] **Step 1: 写失败测试。** `test_accepted_source_hash_and_order` 核对拓扑、分段、时间和原子映射；`test_overview_includes_actual_last_frame` 对 1/2/201/203 帧断言唯一、有序、首末帧和最多 200 帧；`test_duplicate_or_reverse_time_rejected` 与 `test_gap_is_preserved` 固定拒绝/标注行为。

```python
indices = [frame["sourceFrameIndex"] for frame in plan["frames"]]
self.assertEqual((indices[0], indices[-1]), (0, 202))
self.assertLessEqual(len(indices), 200)
self.assertEqual(indices, sorted(set(indices)))
self.assertLessEqual(max_roundtrip_distance_angstrom, 0.01)
```

- [x] **Step 2: 在 `bio-agent/` 运行。** `PYTHONPATH=src /opt/anaconda3/envs/modeling/bin/python -B -m unittest tests.test_preview_sources tests.test_preview_files -v`；先确认失败来自新接口缺失。
- [x] **Step 3: 实现固定派生。** 复用现有 `Controller.artifacts` 和接受记录校验；读取真实 sidecar；每 XTC 段匹配同一原子映射，含独立解码头及 file hash。不能根据任意 path、文件序号或未知 residue name 猜身份。
- [x] **Step 4: 扩展并重跑测试。** `test_xtc_roundtrip_error_and_times` 断言最大坐标误差 <=0.01 Å、时间精确对应 sidecar、原文件 SHA 不变；`test_missing_ca_and_water_are_explicit` 覆盖纯水与缺 Cα；`test_changed_source_cannot_reuse_preview`、`test_budget_refuses_without_deleting` 覆盖源变化、256 MiB 配额与写入中断。
- [x] **Step 5: 检查派生身份。** `previewId` 覆盖来源、集合、源帧选择、精度与实现 SHA；同 key 复用只验证完整文件，不覆盖既有内容。更新 KSH 提取收据时只保存实际复用的小文件及补丁，科学 runtime 原文件不修改。

### Task 3: 异步派生任务与原资源池共存

**Files:** Create `bio-agent/src/bioagent/visualization/jobs.py`、`bio-agent/src/bioagent/visualization/worker.py`、`bio-agent/src/bioagent/visualization/service.py`、`bio-agent/run_biopreview.py`; Modify `bio-agent/src/bioagent/resource_pool.py`、`bio-agent/src/bioagent/bridge.py`; Test `bio-agent/tests/test_preview_jobs.py`、`bio-agent/tests/test_remote_workflow.py`、`bio-agent/tests/test_transfer_budgets.py`。

**Interfaces:** Produces `PreviewService(controller)`，`describe(ref: dict) -> dict`、`prepare(request: dict) -> dict`、`status(job_id: str) -> dict`、`window(preview_id: str, start: int, end: int, stride: int) -> dict`、`read(preview_id: str, file_id: str, offset: int, limit: int) -> dict`、`cancel(job_id: str) -> dict`。prepare/status/cancel 返回 `{jobId,state,previewId,manifest,errorCode}`，后 3 项无值时为 null，state 为 QUEUED/RUNNING/READY/FAILED/CANCELED/UNKNOWN。`read` 内部 limit <=65536，返回 bytes 的 Base64 和块 SHA，仅内部 Bridge 使用；公网随后解码为二进制。

- [x] **Step 1: 写失败测试。** `test_prepare_returns_job_without_blocking_cancel`、`test_same_preview_key_starts_one_worker`、`test_unknown_worker_keeps_lease`、`test_job_cannot_start_md`；资源测试断言预览与科学阶段争用同一 CPU/并发配额，预览不领取 GPU。

```python
first = service.prepare(valid_request)
second = service.prepare(valid_request)
self.assertEqual(first["jobId"], second["jobId"])
self.assertEqual(recorded_scientific_submissions, [])
self.assertEqual(service.status(disconnected_job_id)["state"], "UNKNOWN")
```

- [x] **Step 2: 在 `bio-agent/` 执行。** `PYTHONPATH=src /opt/anaconda3/envs/modeling/bin/python -B -m unittest tests.test_preview_jobs -v`，确认先失败。
- [x] **Step 3: 实现 job 记录与固定 worker。** `prepare` 只持久化/排队并快速返回；固定 helper argv 使用已绑定 Python 和来源，不使用 shell。每预览 worker 申请 1 CPU、每租户最多 1 个；墙钟上限 120 s，取消和失败保留证据。按预览 job 类型扩展资源池，不能伪造科学 workflow/stage 或改变原 lease 的回收判断。
- [x] **Step 4: 接入 host allowlist。** 增加 `preview-describe/prepare/status/window/read/cancel`，逐项验证 Task 1 字段；保持原 `bio.host.v1` 请求、`bio.tool-result.v1` 回复 envelope 和大小上限。启动前和发布前复核来源、目标和资源。内存逐帧有界并记录峰值，资源预留不宣称 OS 强制限额；已有操作者限额环境优先复用。源不足、科学包缺失或目标不兼容时失败，不回退控制服务器处理。
- [x] **Step 5: 重跑任务测试和相关旧回归。** `PYTHONPATH=src /opt/anaconda3/envs/modeling/bin/python -B -m unittest tests.test_preview_jobs tests.test_remote_workflow tests.test_transfer_budgets -v`。断线恢复只协调原 job，确认进程终止前不释放未知租约；取消不启动后续派生。

### Task 4: 租户鉴权二进制出口与全局带宽裁决

**Files:** Create `bio-agent/web/runtime/visualization/preview-api.mjs`、`bio-agent/web/runtime/visualization/preview-relay.mjs`、`cloud/bio_preview.py`、`cloud/preview_budget.py`; Modify `bio-agent/web/runtime/human-api.mjs`、`bio-agent/web/runtime/bio-runtime.mjs`、`bio-agent/web/runtime/server-apply.mjs`、`bio-agent/web/runtime/server-config.mjs`、`cloud/bio.py`、`cloud/tenants.py`、`cloud/app.py`; Test `bio-agent/web/runtime/tests/preview-api.test.mjs`、`bio-agent/web/runtime/tests/preview-relay.test.mjs`、`cloud/tests/test_bio_preview.py`。

**Interfaces:** Consumes `PreviewService` Bridge 动作；Produces JS `installPreviewApi(ctx, projects, config) -> PreviewApi`（`dispatch(action,payload,signal)`）、`installPreviewRelay(ctx, projects, config)`；Python `install_preview_routes(app, authenticate, transport_lookup, budget)`、`tenants.preview_transport(username: str, node_id: str) -> dict`、`GlobalPreviewBudget.wait(owner: str, byte_count: int, priority: str) -> awaitable`。transport 仅供服务器内部使用，包含已有 tenant loopback port 与 per-instance world token，不序列化给浏览器。

- [x] **Step 1: 写失败测试。** `test_tenant_source_and_scope_revocation` 在等待 Bridge 后撤销权限，断言不发送块；`test_original_bio_session_still_valid_without_view_tools` 保留合法原会话；`test_private_relay_rejects_wrong_world_token`；`test_three_tenants_share_four_mbps` 用假时钟验证同级公平轮转、聚合速率、取消及前台优先。

```javascript
const pending = api.dispatch('read', validRead);
revokeScopedBioSession();
finishPendingBridgeRead();
await assert.rejects(pending, /BIO_SESSION_ACCESS_DENIED/);
assert.equal(deliveredChunks.length, 0);
```

- [x] **Step 2: 执行新增 Node 与 cloud 测试。** 根目录执行 `node --test bio-agent/web/runtime/tests/preview-api.test.mjs bio-agent/web/runtime/tests/preview-relay.test.mjs` 与 `/opt/anaconda3/bin/python -B -m unittest discover -s cloud/tests -p test_bio_preview.py -v`。执行者先核实解释器版本，不安装云测试依赖到 modeling。
- [x] **Step 3: 实现二进制 façade。** 浏览器同源 `/v1/bio/preview/describe|prepare|status|window|cancel` 控制路由及 `/v1/bio/preview/{previewId}/files/{fileId}` 数据路由使用原鉴权，session/viewport 通过请求头绑定，offset/limit 有界；不带 URL token。cloud 仅转发到该用户已就绪且节点/配置匹配的 tenant，tenant 的私有 loopback relay 再校验原 world token 与 scoped Bio 会话，并复用该记录的 Bridge。
- [x] **Step 4: 实现单一共享发送预算。** cloud 同一裁决器按最多 16 KiB 的发送片段公平排队，聚合速率 4,000,000 bit/s；多 worker 没有共享裁决后端时功能默认拒绝启用。`preview:*` 新 action 显式加入 catalog，旧 operator binding 无这些权限时仅关闭可视化，不关闭科学工具；不改真实生产 binding。
- [x] **Step 5: 重跑新增与旧回归。** 加入超限块、跨租户引用、令牌轮换、取消后的迟到块、源变更与 byte-range 重试测试；执行 `node --test bio-agent/web/runtime/tests/preview-api.test.mjs bio-agent/web/runtime/tests/preview-relay.test.mjs bio-agent/web/runtime/tests/bio-plugin.test.mjs bio-agent/web/runtime/tests/platform-gate.test.mjs` 和 cloud Bio 相关测试。鉴权失败不能读取数据或消耗他人发送预算。

### Task 5: 浏览器渲染、缓存与 KSH 控件复用

**Files:** Create `bio-agent/web/visualization/package.json`、`pnpm-lock.yaml`、`vite.config.mjs`、`src/renderer.mjs`、`src/player.mjs`、`src/transport.mjs`、`src/dock.mjs`、`src/index.mjs`、`src/style.css`，上述相对文件均在 `bio-agent/web/visualization/`；Create `scripts/build-bio-visualization.mjs`、`bio-agent/provenance/visualization-source-snapshot.json`；Modify `bio-agent/web/runtime/ui-cards/client.source.js`、`scripts/build-bio-ui.mjs`、`dsh-plugins/dsh-bio-ui/package.json`；Test `bio-agent/web/runtime/tests/preview-player.test.mjs`、`preview-browser-transport.test.mjs`。

**Interfaces:** Produces `createMolstarRenderer(container, onObservation) -> RendererAdapter`，含 `open(manifest, topologyBytes)`、`setFrame(fileBytes, localIndex)`、`setRepresentation(value)`、`select(sourceIndices)`、`focus()`、`observe()`、`dispose()`；`createPreviewPlayer({renderer,transport,onState})` 含 `open(manifest: PreviewManifest)`、`seekTime(timePs: number)`、`play(config: {startFrame,endFrame,fps,direction,loop})`、`pause()`、`dispose()`。`transport.getFile(manifest, fileId, signal) -> Promise<Uint8Array>` 先校验尺寸与 SHA，才能交给解码器。

- [x] **Step 1: 写失败测试。** 假 renderer 验证 0/1 索引换算、200/203 帧末帧、单帧静态、10 fps 调度、内存窗口与无下一帧的缓冲；假网络验证取消、最多 2 请求、损坏块不得渲染、同一文件复用与跨会话缓存隔离。断言 source/time 映射来自 manifest，不由播放速率推算物理时间。

```javascript
await assert.rejects(playerWithChangedFile.open(manifest), /ARTIFACT_CHANGED/);
assert.equal(recordedRendererFrames.length, 0);
assert.ok(recordedInFlightCounts.every(count => count <= 2));
assert.ok(recordedDecodedWindowBytes.every(bytes => bytes <= 32 * 1024 * 1024));
```

- [x] **Step 2: 根目录运行新增 Node 测试并确认失败。** `node --test bio-agent/web/runtime/tests/preview-player.test.mjs bio-agent/web/runtime/tests/preview-browser-transport.test.mjs`。
- [x] **Step 3: 依赖与空间 gate。** 先检查现有兼容工具和 store。已查本地 Node `26.7.0`、pnpm `11.25.0`；Mol* 包自身约 80.5 MiB、Vite 约 2.2 MiB。拟采用独立前端目录与 lock，预计新增依赖及首次构建约 0.2–0.6 GiB，实际预算上限 1 GiB；超预算先说明，不自动清理。取得安装授权后，在新前端目录执行 `pnpm install --ignore-workspace --ignore-scripts --config.auto-install-peers=false`，避免安装 gl/canvas/云存储等服务器 peer；`--ignore-workspace` 已通过本地 CLI 帮助核实，不更改原 workspace lock 或科学环境。后续重复安装加 `--frozen-lockfile`。
- [x] **Step 4: 实现 adapter、player 与 dock。** 按 KSH 文件 SHA 提取首版所需逻辑与样式，记录精确文件/补丁/notices；Mol* 禁用无关远端数据访问。构建输出到本前端目录的 `dist/`，生成 hashed asset manifest；同源 `/v1/bio/visualization/assets/{assetId}` 只服务该构建清单中的发布资源，禁止任意路径、目录浏览和 source map。通过现有公开 header 插槽挂载可折叠 dock，聊天、输入框和原 Bio 任务入口始终可操作；不新加依赖上游哈希类名的 CSS。序列窗口、块校验与渲染分别处理，视图卸载取消取数并释放其内存对象。
- [x] **Step 5: 验证实际 WebGL。** 重跑 Step 2；执行 `node scripts/build-bio-visualization.mjs`、`node scripts/build-bio-ui.mjs`。用已有浏览器能力打开隔离本地候选，验证结构表示、Cα 骨架、纯水盒、拖动时间、播放暂停、鼠标旋转、单帧/末帧与窄/宽窗口。记录真实 renderer 来源及帧观测和截图；DOM 测试不能作为 WebGL 通过证据。记录压缩静态资源大小，不下载新的浏览器。

### Task 6: Agent 显示工具、实际回执与刷新恢复

**Files:** Create `bio-agent/web/runtime/visualization/view-api.mjs`、`bio-agent/web/runtime/visualization/view-runtime.mjs`、`bio-agent/web/runtime/visualization/view-state.mjs`、`bio-agent/web/runtime/visualization/view-contracts.json`; Modify `bio-agent/web/runtime/bio-runtime.mjs`、`bio-agent/web/runtime/ui-cards/client.source.js`、`cloud/bio.py`、`bio-agent/agent/domain-prompt.md`; Test `bio-agent/web/runtime/tests/view-api.test.mjs`、`bio-agent/web/runtime/tests/view-runtime.test.mjs`、`bio-agent/web/runtime/tests/view-state.test.mjs`。

**Interfaces:** Consumes Task 5 adapter；Produces `installViewRuntime(ctx, projects, config)` 与 `ViewStateStore(project)` 的同步 `load(sessionId)`、`save(sessionId,state,expectedRevision)`；新增限定工具 `bio_view({viewportId,commandJson})`、`bio_observe({viewportId})`。`commandJson` 必须经 Task 1 的闭合显示契约验证；处理器返回实际 renderer 的 `ViewBinding`、源帧、时间与选择，不返回坐标大数组。

- [x] **Step 1: 写失败测试。** `test_stale_source_or_frame_command_rejected`、`test_pause_during_playback_remains_possible`、`test_seek_preserves_new_camera`、`test_replaced_session_cannot_receive_observation`、`test_offline_or_ambiguous_view_cannot_succeed`、`test_view_state_is_metadata_only`；证明 Agent 与手动操作执行相同处理器。

```javascript
assert.throws(() => store.save(sessionId, {...viewState, coordinates: [1, 2, 3]}, 0), /INVALID_VIEW_STATE/);
store.save(sessionId, viewState, 0);
assert.equal(store.load(sessionId).sourceSha256, viewState.sourceSha256);
assert.equal(recordedScientificApprovalCalls.length, 0);
```

- [x] **Step 2: 根目录运行。** `node --test bio-agent/web/runtime/tests/view-api.test.mjs bio-agent/web/runtime/tests/view-runtime.test.mjs bio-agent/web/runtime/tests/view-state.test.mjs`；确认先失败。
- [x] **Step 3: 实现 scoped 工具与显示回执。** 科学工具 catalog 保持原 11 项；显示 catalog 独立、启用时加入该 Bio Agent 的 guard。HumanWorkflowApi 的基础 Bio 资格仍检查原科学工具，显示请求另验显示权限；使用 `agent.ctx.tools.schemas(agent)`，不能回到全局 schemas。绑定明确 viewport，等待实际加载完成才回执；延迟响应不能作用于新来源。
- [x] **Step 4: 实现恢复与能力说明。** 原子写入每会话当前元数据，刷新后验证来源再恢复；失效来源、离线页面或 renderer 不支持时返回闭合错误，不报告成功。prompt 说明只能控制显示，不能批准计算、补造帧或将观测变成科学结论。多窗口没有明确 viewport 时要求明确目标。
- [x] **Step 5: 重跑 Step 2 与 Bio scoped-session 旧回归。** 本地真实页面至少走通“显示结构→选择/聚焦→定位真实时间→手动旋转→Agent 再定位→刷新恢复”；保存 request/receipt 和 renderer 回读，不自动调用真实收费模型。

### Task 7: 共享 5 Mbps 验收、发布 allowlist 与交付

**Files:** Create `scripts/validate-bio-visualization.mjs`、`bio-agent/docs/web-visualization.md`；Modify `bio-agent/deploy/build_bundle.py`、`bio-agent/deploy/verify_release.py`、`bio-agent/README.md`、`bio-agent/docs/remote-workflow.md`、`docs/bio-platform-integration.md`；Test `bio-agent/web/runtime/tests/preview-bandwidth.test.mjs`、`cloud/tests/test_bio_preview.py`。

**Interfaces:** Produces验收入口 `node scripts/validate-bio-visualization.mjs --out <new-absolute-directory> --shared-mbps 5 --rtt-ms 100 --viewers 1,3,5 --duration-seconds 180`。输出新目录的 `manifest.json`、`network.json`、`views.json`、`memory.json`、`storage.json`、`summary.md` 与必要截图；目录已存在则拒绝覆盖。使用一个共享限速的本地验证网关，不改变 macOS 全局网络或生产 nginx。

- [x] **Step 1: 写失败测试。** `test_shared_proxy_limits_all_viewers_together` 断言聚合不是各自 5 Mbps；`test_cold_assets_are_counted` 单独计入首次 renderer JS/CSS；`test_report_cannot_mark_unobserved_view_passed` 拒绝缺失实际帧/来源回读的成功结论。

```javascript
assert.ok(measuredIntervals.every(item => item.aggregateBits <= 5_000_000 * item.seconds + 16_384 * 8));
assert.ok(coldRendererAssetBytes > 0);
assert.equal(unobservedView.status, 'NOT_ACCEPTED');
```

- [x] **Step 2: 运行新增测试。** `node --test bio-agent/web/runtime/tests/preview-bandwidth.test.mjs` 与 cloud 预览相关测试，确认先失败。
- [x] **Step 3: 实现验收及包装边界。** 1/3/5 个视图共享 5 Mbps、100 ms RTT 各测 180 s；分别统计派生冷启动、已生成预览、renderer 冷资源及缓存播放。参考蛋白约 300 残基加小配体，目标为暖资源结构 <=4 s、播放 <=6 s、renderer 冷资源显示 <=12 s、预览缓存后卡顿 <=1%。原文件摘要、真实末帧与错误路径单独核对；约 10 万原子压力案例明确标为工程数据，原始大轨迹不复制到证据目录。
- [x] **Step 4: 运行并审阅结果。** 在新证据目录执行上述入口，用已有真实轨迹补充科学数据读取/映射验收；无真实输入或 GPU/WebGL/远程证据时相应项标为未验收，不能用合成数据替代。重跑各任务聚焦测试与原 Bio/cloud/remote 生命周期回归，失败先定位，不机械扩大到在线/生产测试。
- [x] **Step 5: 更新用户文档与源包 allowlist。** 明确入口、抽样/精度、来源、缓冲、详细窗口、恢复及局限；附带独立前端版本/notices 与构建摘要，不打包环境、凭据、用户轨迹或输入。只有完成相关代码后再更新状态，不预先宣称功能可用。
- [x] **Step 6: 完成 diff 与空间审阅。** 检查仅必要文件变更、锁版本、第三方收据、输入前后哈希、测试/浏览器证据及新增产物大小。提交部署均等用户明确指令，失败残留只列清理候选，不自动删除。

## 执行与审阅交接

建议 **Native**：在当前会话按 Task 1–7 顺序实施，每个交付完成后检查接口、错误路径和测试；实现结束再做独立审查。数据生成、鉴权、带宽调度和浏览器状态彼此依赖，先稳定接口能减少重复适配。

用户也可选择 **Subagent-driven**，按同样任务边界交付并逐项独立审查；未选择前不派发代理。用户批准本计划并选择执行方式后才开始产品代码。Task 5 的新增依赖安装须单独或与本计划明确一起授权；生产部署和科研计算另行安排。

实施及相关本地验证完成，详见 [交付与验收记录](../reports/2026-10-12-web-visualization-low-bandwidth.md)。生产/SSH/科学验收边界保留，不将工程数据当作科学接受。
