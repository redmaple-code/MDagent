# DSH Button Visualization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 在原生 DSH Bio 聊天页提供简单按钮式结构/轨迹查看，使用已登记来源，预览文件只经超算直连。

**Architecture:** 保留已有“3D 预览”入口、human API、Molstar、player 和本地连接器。整理人工选择与基本控件，服务端关闭预览中转，最后用原生 DSH 页面及真实只读工程 helper 验收。

**Tech Stack:** 现有 Node 26.7.0、DSH 0.1.0-rc.6、TypeBox 1.1.38、Schemastery 3.18.4、Molstar 5.13.1、Vite 8.2.2、modeling Python 3.12.11、cloud Python 3.12.2；不安装新依赖。

**Spec:** `/Users/a0000/项目/auto-md/mdagent-web-visualization/docs/superpowers/specs/2026-10-13-dsh-chat-direct-visualization-design.md`，已确认 Revision 2。

**Execution:** 沿用本会话 **Native**；本地实现、最终构建工程验收、一次独立整分支审查及一轮 TDD 修复已完成。未提交、未部署。

## Global Constraints

- 复用当前脏工作树 `/Users/a0000/项目/auto-md/mdagent-web-visualization`，不覆盖既有成果；用户仅明确要求时才 commit/push，不部署或修改生产配置。
- 不新增语言可视化工具、不对话自动打开；按钮不调用模型，原科研工具与审批/提交保持独立。
- 首版仅当前会话登记的输入结构与有效已接受结果；使用 SourceRef 和产物角色，不接收任意路径、不猜配对。
- 预览固定 direct-only：未配对、断线、恢复、预取、缓存和旧入口都不得中转坐标；元数据、授权和前端资源继续经过服务器。
- 保留真实源帧/时间、镜头、64 MiB 缓存、两个传输槽位/reader、单次范围许可及来源/身份复核，不升级科学环境或改变原始数据。
- 所有新测试/体验路径记入本轮 ledger；构建前检查空间，预计新增峰值不超过 100 MiB。只清理任务独占、可重建且非交付/证据的临时物。
- 当前无真实 SSH 配置；本地工程、原生 UI、真实 SSH/HTTPS/提速和科学验收分别报告。既有 2026-10-12 结果不能证明新构建通过。

## Review Focus

1. 旧 binding 仍带 read：不能通过旧 GET/private read 或来源探测获取坐标；Task 1 验证在 Bridge 读取前拒绝。
2. 未配对/断线/恢复或直连资格撤销：不能隐式中转或未经新授权复用缓存；Task 3 验证 provider 与网络调用。
3. 多页任务、重名、多个阶段或过期 revision：选择显示必须对应实际 SourceRef/配套文件；Task 2 验证分页和解析。
4. 快速切来源/会话、迟到的加载结果：不能显示旧数据为新选择，关闭只释放自身读取；Tasks 2–4 验证生命周期。
5. 原生新会话、短窗口和关闭模型联动：无模型请求即可操作按钮、没有联动报错、控件可滚动；Tasks 2/4 用实际 DSH 浏览器验收。

## Files and responsibilities

| 文件 | 职责 |
| --- | --- |
| `cloud/bio.py`、`cloud/bio_preview.py`、runtime `preview-api.mjs` / `view-runtime.mjs` | 控制资格与 read 分离、拒绝中转、元数据核验 |
| 新 `bio-agent/web/visualization/src/source-catalog.mjs` | 人工选择、分页、接受阶段和 SourceRef 解析 |
| `dock.mjs`、`style.css`、`transport.mjs`、`ui-cards/client.source.js` | 简单按钮、稳定选择、唯一直连、原生入口 |
| 新 `scripts/serve-bio-dsh-preview.mjs` / `bio-dsh-preview-plugin.mjs` | 独占原生 DSH 工程体验，复用真实会话和权限 |
| fixture、相关测试、build/verify、用户说明 | 可追踪工程样例、当前构建验收和交付 |

---

### Task 1: 关闭预览中转并拆分资格

**Files:** Modify `cloud/bio.py`、`cloud/bio_preview.py`、`bio-agent/web/runtime/visualization/preview-api.mjs`、`view-runtime.mjs`；Test `cloud/tests/test_bio_direct.py`、`test_bio_preview.py`、runtime `direct-api.test.mjs`、`preview-api.test.mjs`、`view-runtime.test.mjs`。

**Interfaces:** 新 `bio.PREVIEW_CONTROL_ACTIONS` 为 `preview:describe/prepare/status/window/cancel`；保留旧 `preview:read` 为可解析的历史 action，但所有预览读取返回 `PREVIEW_RELAY_DISABLED`。`PreviewApi.dispatch()` 签名不变，`describe` 继续返回真实 `transportBinding/directEnabled`。`ViewRuntime.checkSource()` 仅使用 `preview-describe` 及来源 SHA，不使用坐标 read。

- [x] **Step 1:** 添加失败用例 `direct_without_read_permission_is_enabled`、`legacy_read_permission_cannot_emit_coordinates`、`source_verification_uses_metadata_only`：完整控制+direct 权限足够；旧 GET/private read 无 `private_request/preview-read` 调用；元数据来源变化仍拒绝。

  核心断言：`await assert.rejects(api.dispatch('read',legacyPayload),/PREVIEW_RELAY_DISABLED/); assert.equal(bridgeReadCalls,0);`；cloud 对合法旧 read 账号的 GET 断言 `status_code == 403`、`private_calls == []`。
- [x] **Step 2:** 运行 `node --test bio-agent/web/runtime/tests/direct-api.test.mjs bio-agent/web/runtime/tests/preview-api.test.mjs bio-agent/web/runtime/tests/view-runtime.test.mjs`；cloud 使用 `PYTHONPATH=/Users/a0000/项目/auto-md/mdagent-web-visualization/cloud:/Users/a0000/项目/auto-md/mdagent-web-visualization/bio-agent/src /opt/anaconda3/bin/python -B -m unittest discover -s cloud/tests -p test_bio_direct.py -v`，再以同命令单独运行 `test_bio_preview.py`；新增断言先 FAIL。
- [x] **Step 3:** overlay、assets_access 和 direct 授权改用控制 action 集；旧 GET 与 private read 在计算端调用前拒绝。来源核验保留 scoped/前后异步/来源检查，改为元数据。兼容旧 binding 的解析，不能令原科学权限失效。
- [x] **Step 4:** 重跑 Step 2，独立运行 `cloud/tests/test_bio.py` 和 runtime `preview-relay.test.mjs`；旧正向预览 read 测试按新策略改为拒绝断言，科学下载/上传与任务权限回归必须保持 PASS。
- [x] **Step 5:** 记录 RED/GREEN 和相对本轮 baseline 的 diff；不提交。

### Task 2: 简化控件并可靠选择来源

**Files:** Create `bio-agent/web/visualization/src/source-catalog.mjs`；Modify `dock.mjs`、`style.css`；Create runtime `preview-source-catalog.test.mjs`；Modify `preview-dock.test.mjs` / `preview-dock-lifecycle.test.mjs`。

**Interfaces:**
- `loadPreviewChoices(humanApi,{offset=0,signal}={}) -> {choices,nextOffset}`：choice 含 key/kind/label；input 保存登记记录及绑定文件名、登记 ID、SHA 的 previewSourceId，workflow 保存 workflowId/revision。使用既有 human inputs/list，后续页不重复输入项。
- `loadCompletedStages(humanApi,workflowId,revision,{signal}={}) -> [{id,label}]`：通过 get 核对当前 revision 和唯一有效接受 attempt。
- `resolvePreviewChoice(humanApi,choice,stageId,{signal}={}) -> {source,structureName,trajectoryNames}`：输入构造原 SourceRef，结果遍历 artifacts 的有界分页并核对每页相同 SourceRef/attempt；名称只取产物角色与相对文件名。
- 保留 `createDock()`、`openSource()`、`metadata()` 和内部按钮 handler 接口；选择中的来源与已实际显示的来源分开维护。

- [x] **Step 1:** 添加 `catalog_pages_keep_choices`、`multiple_stages_require_selection`、`stale_or_mixed_source_is_rejected`、`late_open_cannot_replace_selected_source`：第一页/下一页不丢任务，同名可区分；多个阶段不猜；旧 revision/混合页拒绝；迟到的 open 不覆盖新选择。

  核心断言：`assert.equal(new Set(choices.map(c=>c.key)).size,choices.length); await assert.rejects(resolvePreviewChoice(api,oldChoice,'production'),/PREVIEW_SOURCE_CHANGED/);`。
- [x] **Step 2:** 运行 `node --test bio-agent/web/runtime/tests/preview-source-catalog.test.mjs bio-agent/web/runtime/tests/preview-dock.test.mjs bio-agent/web/runtime/tests/preview-dock-lifecycle.test.mjs`；新增行为先 FAIL。
- [x] **Step 3:** 接入上述解析器，添加明确“请选择”、输入/任务分组及“更多任务”；唯一接受阶段可直接使用，多个阶段必须选择。打开成功后才更新实际来源标签，显示结构/配套轨迹/版本/时间范围，空列表给上传或等待提示。
- [x] **Step 4:** 基本区保留打开、播放/暂停、时间条、表示/居中、收起；原子集合、速度与详细窗口放“更多设置”。静态结构禁用播放；收起取消自身下载/轮询，不自动取消其他窗口可能共享的派生任务。短窗口/窄屏可滚动，实际布局验证放 Task 4，不写镜像 CSS 测试。
- [x] **Step 5:** 重跑 Step 2 与 `preview-player.test.mjs`；核对真实时间、镜头、源替换和水盒分支，记录 PASS/diff。

### Task 3: 浏览器固定直连并关闭模型显示联动

**Files:** Modify `bio-agent/web/visualization/src/transport.mjs`、`dock.mjs`、`bio-agent/web/runtime/ui-cards/client.source.js`；Test runtime `direct-provider.test.mjs`、`preview-browser-transport.test.mjs`、`preview-dock-lifecycle.test.mjs`；两份 `client.js` 和前端 dist 由现有脚本生成。

**Interfaces:** `createPreviewTransport()` 只使用 direct provider；`setProvider(provider|null)` 不再选择模式，非 direct provider 返回 `DIRECT_CONFIG_INVALID`。未连接返回 `CONNECTOR_UNAVAILABLE`，服务未配置返回 `DIRECT_NOT_CONFIGURED`；`api/getFile/beginView/hasFile/dispose` 及 direct provider 许可协议不变。

- [x] **Step 1:** 添加失败断言：未连接、HTTP/SSH 断线、恢复和直连资格撤销时不请求预览文件 GET、不进入 relay；缓存需当前授权。点击 3D 预览不调用 `/bio-view` 或模型接口，也不显示未启用 Agent 联动错误；会话切换继续释放迟到 dock。

  用例名 `direct_required_never_requests_relay` / `manual_open_has_no_view_or_model_rpc`；核心断言：`assert.equal(requests.filter(r=>r.method==='GET'&&r.url.includes('/files/')).length,0); assert.equal(viewRpcCalls,0); assert.equal(modelCalls,0);`。
- [x] **Step 2:** 运行 `node --test bio-agent/web/runtime/tests/direct-provider.test.mjs bio-agent/web/runtime/tests/preview-browser-transport.test.mjs bio-agent/web/runtime/tests/preview-dock-lifecycle.test.mjs`；新增行为先 FAIL。
- [x] **Step 3:** 移除 transport 的 relay 导入/回退和模式参数；保留哈希、两槽位、缓存隔离、取消及重授权。dock 配对固定设置 direct，移除模式选择，仅展示连接状态/断开和明确重试提示。
- [x] **Step 4:** 原生 BioAction 仍由按钮创建面板，不执行 `bindView()`；保留现有 session generation、abort 和资源释放。旧显示代码无需删除，测试 profile 明确 `viewEnabled=false`，不新增语言工具。
- [x] **Step 5:** 重跑 Step 2 和 UI/科学任务面板相关 `bio-plugin.test.mjs`，再运行 `node scripts/build-bio-ui.mjs`、`node scripts/build-bio-visualization.mjs`；确认两份 client 与当前 asset-manifest 对应，依赖锁不变。

### Task 4: 原生 DSH 体验、验收与候选交付

**Files:** Create `scripts/serve-bio-dsh-preview.mjs`、`scripts/bio-dsh-preview-plugin.mjs`、runtime `native-preview.test.mjs`、`docs/superpowers/reports/2026-10-13-dsh-button-visualization.md`；Modify `bio-agent/tests/direct_browser_fixture.py`、runtime `bio-runtime.mjs`、`bio-plugin.test.mjs`、deploy `build_bundle.py` / `verify_release.py`、`bio-agent/docs/web-visualization.md` / `local-preview-connector.md`、`bio-agent/README.md`。

**Interfaces:**
- `installBio(ctx,config,Compile,{projects}={})` 增加仅内部可用的 SessionProjects 注入；默认仍构造原对象，原三参数调用不变，HTTP/config schema 不暴露此注入。
- `create_native_preview_fixture(out:Path,session_id:str) -> fixture`：工程目录按实际 DSH session SHA 建立，提供蛋白静态输入、带配套拓扑/时间的已接受轨迹；现有 fixture CLI 增加 `--session-id`，其 session-test 默认保留给旧工程测试。
- `startNativePreview({stateRoot,repoRoot,python,port=3081,connectorPort=18773,modelHome}) -> Promise<{url,close}>`；新 CLI `node scripts/serve-bio-dsh-preview.mjs --state <new-owned-dir> --port 3081`，公开 receipt 只保存 URL/PID/build/工程标记，不保存 env 或密钥。

- [x] **Step 1:** `native-preview.test.mjs` 先验证：非新建/越界状态根拒绝；普通三参数 installBio 兼容；真实 SessionProjects/scoped Agent 来源隔离；测试 gate 拒绝科学提交/审批/advance；helper 使用既有 `scientificEnv()`，不继承模型凭据。执行 `node --test bio-agent/web/runtime/tests/native-preview.test.mjs`，新增接口先 FAIL。

  用例名 `native_profile_is_owned_and_readonly` / `native_fixture_matches_actual_session`；核心断言：`assert.equal(profile.viewEnabled,false); assert.equal(computeEnv.BIO_WEB_TEST_API_KEY,undefined); assert.notEqual(projectForSession(root,'a'),projectForSession(root,'b'));`。
- [x] **Step 2:** 启动原 DSH CLI、原前端/会话/Bio 预设及当前 UI client，复用已有 profile/frozen-web 机制和依赖。工程插件注入真实 SessionProjects、BridgeClient、HumanWorkflowApi、PreviewApi；metadata facade 复核实际 scoped Agent、会话和精确 Origin，assets 限当前 manifest allowlist/hash；不能用假聊天页、假 Agent 对象或跳过 scope。计算端只在独占工程目录，工程会话最多 4 个；SSH spawn 替换为真实本地只读 Python stdio，严格标注“本地工程计算端，非真实超算”。
- [x] **Step 3:** 配对继续使用现有安全流程。工程配对辅助仅在此独占测试插件、明确工程标记和精确 Origin 下可用，可在密码输入框填入演示码；不写入生产代码/receipt/截图/模型。普通聊天沿用原模型配置，按需仅引用受保护凭据到 DSH 子进程环境；不复制原 home，不自动请求模型。
- [x] **Step 4:** 用 CUA 打开原生新 Bio 会话，在没有发送模型消息时点击 3D 预览，选择静态结构/轨迹并完成旋转、缩放、居中、播放/暂停、首末时间定位、源切换和关页/会话隔离。桌面 1280×900、1280×720 和 390×844 全部基本控件可见/可滚动；记录实际源帧/时间、当前 build、预览 relay=0 和模型请求=0。当前构建完成一次 180s 实际缓存帧推进，卡顿/冻结尾段不能由 seek 假通过；保存就绪截图，保留体验页供用户操作。
- [x] **Step 5:** 执行相关汇总：modeling Python 的 `unittest discover -s bio-agent/tests -v`；`BIO_TEST_PYTHON=/opt/anaconda3/envs/modeling/bin/python node --test bio-agent/web/runtime/tests/*.test.mjs bio-agent/local-connector/test/*.test.mjs bio-agent/agent/tests/*.test.mjs`；三个 cloud runner 独立运行及 remote-world/plugin 回归。新临时夹具用本轮登记父目录并清理；不批量清理历史 `/private/tmp`。
- [x] **Step 6:** 文档改成按钮/唯一直连说明，bundle allowlist 加入 source-catalog；用 `/opt/anaconda3/bin/python -B bio-agent/deploy/build_bundle.py` 生成新候选，在本轮独占解包目录调用 `verify_release.verify(extracted_root)`。核对冻结科学 runtime、当前前端、依赖锁与 `git diff --check`；记录实测结果和未验收项。
- [x] **Step 7:** Native 模式按 executing-plans 做一次新整分支审查及一轮必要修复/相关回归；保存未提交源码、当前/回退候选、报告和必要证据。停止不用的 owned 服务、核验路径/符号链接并清理登记过的临时产物；用户正在体验的服务/状态保留，报告空间变化。

## Self-review and handoff

- Spec 六节均对应 Tasks 1–4；五项 Review Focus 均有明确断言或实际浏览器验收。
- 新选择器复用 human API；直连 provider 协议不改；原生测试通过内部 SessionProjects 注入，生产科学执行仍使用固定 remote target。
- 产品实现已执行。用户已选择 Native，不重复询问执行方式、安装依赖或模型密钥。
- 最后审查和收尾完成后交付。真实 SSH/HTTPS/实际提速及科学生产验收不由本地工程结果替代。
