# MDagent HPC-to-local Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有DSH网页中通过轻量本地连接器直接读取超算已发布预览，同时保留服务器逐块授权、来源校验和中转回退。

**Architecture:** 同源控制服务经现有固定Bridge登记短时、单次、范围限定的读取许可；计算端固定stdio helper只消费这些许可。本地Node连接器处理配对、两条SSH通道和有界传输，网页通过可替换binary provider取数，继续使用现有Molstar、真实源帧/时间和实际视图回执。

**Tech Stack:** 复用Node `26.7.0`、系统OpenSSH `10.3p1`、modeling Python `3.12.11`、cloud Python `3.12.2`、现有DSH/FastAPI；Molstar `5.13.1` / Vite `8.2.2`保持原版本。新连接器仅用Node内置模块和系统SSH；不新增包或环境。

**Spec:** [已确认直连设计](../specs/2026-10-12-hpc-local-preview-direct-design.md)，用户于2026-10-12批准写计划。计划状态：`LOCAL_IMPLEMENTED_ENGINEERING_VERIFIED / REAL_NETWORK_UNCONFIGURED`。

**Execution method:** 沿用用户已选择的本会话Native顺序实施；计划审阅确认后使用`superpowers:executing-plans`。执行者完成全部任务后安排一次独立整分支审查；计划自审由当前执行者完成。原前端依赖安装授权不扩展到新依赖；本计划无需安装。

## Global Constraints

- 使用当前linked worktree `/Users/a0000/项目/auto-md/mdagent-web-visualization`、分支`codex/bio-web-visualization`，不复制仓库或另建工作区。HEAD仍为`b8c2b719c5ccf928a6b63ff29de605f321d0145f`；现有可视化修改与用户文件保留。
- 首版直接读取**同一已登记执行目标**，核验host/UID/项目/release；不同登录或共享数据节点不自动视为同一目标。不把机器身份SHA当成SSH host-key指纹。
- 服务器继续负责登录、原11个scoped Bio工具资格、Project guard、执行目标、来源接受状态及科学审批；world token、cookie和SSH密钥不转交网页连接器接口。
- 仅READY预览可直读；完整SourceRef、manifest/文件SHA、原子映射、真实源帧与时间保持绑定。原始DCD、checkpoint、输入和日志不参与删除或覆盖。
- 回环只监听`127.0.0.1`，默认端口`18773`；配对码至少128 bit、有效120 s、最多5次错误；本地会话空闲15 min失效，仅存内存。
- 固定Origin/Host，CORS不使用通配；本地fetch使用`credentials: 'omit'`、`redirect: 'error'`。请求体<=32 KiB；配对密钥与许可只在header/body，不进入URL、持久存储、Agent或日志。
- 单次读取许可<=256 KiB、寿命5 s；计算端每个<=64 KiB frame复核。许可仅单次使用，同范围单纯过期最多重新授权一次。
- 每viewport<=2在途请求；连接器全局<=2、最多2条SSH helper通道、待队列<=32；当前帧优先并按视图公平。预览应用缓冲全局<=1 MiB，不新增落盘坐标缓存。
- 许可记录每活动channel两个槽位；本计划落实为项目内最多64个活动channel的有界运行态文件，未知schema拒绝，不覆盖既有科学数据。
- 沿用浏览器64 MiB文件缓存及原解码窗口预算；缓存按授权会话/代际、target、SourceRef、manifest与文件身份绑定。
- 只有可确认的连接故障可回退；权限/身份/来源/完整性错误拒绝两条取数路径。`direct-only`测速失败即失败。
- 中转、服务器静态资源和新增授权JSON继续计入原全局4,000,000 bit/s预算；直连坐标单独计数。默认功能关闭，不修改生产binding/config。
- 保留原Bridge串行请求机制；分别记录授权队列等待、执行、SSH冷连接及数据耗时，不以SSH裸下载代替完整许可链性能。
- 真实测试每批预览<=16 MiB，整轮预览下载<=512 MiB；1/3/5视图各180 s。提速30%是待测目标，未测或未达到不标提速通过。
- 不自动commit、push、部署、运行生产MD、修改凭据或安装依赖；按用户更新的第6节协议登记并清理本次任务独占可重建临时产物，保护既有产物和证据。预计新源码/测试<1 MiB，复用构建后新增证据/候选包约0.03–0.2 GiB；执行中超过0.3 GiB先报告原因，保留成果。

## Review Focus

1. EOF、半个framing header、慢消费者或取消发生在许可消费中：单次许可不重放，内存/队列有界，退出不触发科学工作。Task2/3/5固定测试。
2. 初始握手通过后主机配置、目标、agent/scopes或源改变：旧许可/缓存/迟到响应不能在新会话复活，拒绝不降级。Task2/3/4/6固定测试。
3. 同Origin中的第二账号、第二窗口和刷新：client/session代际独立，原合法Bio会话不因缺display工具误拒绝。Task4/5/6固定测试。
4. HTTPS网页访问回环时权限拒绝、CSP阻断、端口占用或未知SSH错误：有明确状态，未知身份错误保持拒绝，不要求关闭浏览器安全策略。Task3/5/6/7固定测试及待验收项。
5. Bridge串行队列、冷JS/CSS和自动降级污染测速：分别计时/计数，direct-only无中转坐标；缺真实浏览器或SSH证据时报告PENDING。Task4/6/7固定测试。

## 文件结构与共享接口

以下均为工作区相对路径；任务中列出的新增文件目前不存在。现有文件按职责扩展，不改冻结科学runtime或旧科学工具schema。

| 文件/目录 | 职责 |
| --- | --- |
| `bio-agent/visualization/direct-contracts.json`、`bio-agent/visualization/direct-contract-cases.json` | 两端闭合请求/响应字段、限制、错误码和共同测试向量 |
| `bio-agent/src/bioagent/visualization/direct_contracts.py`、`bio-agent/web/runtime/visualization/direct-contracts.mjs` | Python / 可用于浏览器的纯JS协议验证，无新schema包 |
| `bio-agent/src/bioagent/visualization/published_reader.py` | 从现有service提取READY/source/file校验和有界原始字节读取 |
| `bio-agent/src/bioagent/visualization/direct_permits.py` | 原子登记/认领/逐frame校验/关闭读取许可，不生成预览 |
| `bio-agent/src/bioagent/visualization/direct_reader.py`、`run_biopreview_direct.py` | 固定stdio入口与framing；只读已有项目/数据库 |
| `bio-agent/local-connector/config.mjs`、`ssh-protocol.mjs`、`ssh-reader.mjs` | 本地operator配置、framing、受控SSH子进程/握手 |
| `bio-agent/local-connector/scheduler.mjs`、`pairing.mjs`、`server.mjs`、`run.mjs` | 传输槽位、公平队列、配对、回环HTTP和CLI |
| `bio-agent/web/visualization/src/local-connector.mjs`、`direct-provider.mjs`、`relay-provider.mjs` | 网页配对client、直连和既有中转binary provider |
| `scripts/validate-bio-direct.mjs`、`bio-agent/docs/local-preview-connector.md` | 工程验收/evidence契约、安装配置与待部署说明 |

共享JSON类型由Task1定义，后续同名使用：

- `DirectOpenRequest = {previewId, source: SourceRef, manifestSha256, clientId, pairingId}`；`DirectPermitRequest = {channelId, previewId, fileId, offset, limit, sourceSha256, clientId, pairingId}`；`DirectCloseRequest = {channelId, clientId, pairingId}`。HTTP session/viewport从已有头获取，owner、node、project和agentEpoch由可信服务推导，不接受浏览器自报。
- `TransportBinding = {ownerBinding,sessionEpoch,executionTargetSha256}`，ownerBinding是可信tenant/node归属的公开摘要，sessionEpoch是当前agent实例的opaque代际ID。Task4在同源describe响应追加此字段；它不是凭据，不从计算端回复或浏览器自报中获取。
- `ChannelRef = {schemaVersion: 'bio.direct-channel.v1', channelId, targetId, executionTargetSha256, projectKey, clientId, pairingId, previewId, source, manifestSha256, readerVersion, uid, hostIdentitySha256, transportBinding: TransportBinding}`。`projectKey`为现有`projectForSession`的64位十六进制目录名；不返回本地/远端绝对路径。两处executionTargetSha256必须相同。
- `PermitRef = {schemaVersion: 'bio.direct-permit.v1', channelId, handle, previewId, sourceSha256, manifestSha256, fileId, fileSha256, byteSize, offset, limit, expiresAtMs}`。handle随机256 bit，计算端仅落其SHA-256及私有绑定；过期以计算端时钟判断。
- 私有`ChannelBinding`还含tenant/node、sessionId/viewportId、agentEpoch、完整execution target、projectKey；`agentEpoch`按当前agent实例生成，不能由公开请求提供。ChannelRef/PermitRef都有严格字段表，禁止额外路径、命令、target或owner。
- `manifestSha256`为新协议的跨语言内容摘要，不是manifest.json原文件字节的SHA：先验证闭合manifest，复制所有字段，把每个timePs转换成IEEE754 float64 big-endian的16位hex字符串（零统一为正零），再SHA-256既有canonical JSON `["bio.direct-manifest-identity.v1", normalizedManifest]`。所有其他数字为已验证整数，避免Python `1.0`与JS `1`的序列化差异；source/file SHA仍是原文件字节摘要。
- `ReadBlock = {previewId,fileId,sourceSha256,manifestSha256,fileSha256,byteSize,offset,nextOffset,sha256,data: bytes}`，每块<=65536 bytes。服务旧接口保留字段/限额及Base64，只新增manifestSha256；新helper直接发送字节。
- stdio每record为8-byte prefix（两个uint32 big-endian：JSON长度、binary长度）+UTF-8 JSON+原始binary。JSON<=32768、binary<=65536；请求binary长度必须0。请求仅`hello/read/close`；响应`hello/data/done/error`，均绑定requestId；只有`data`可带binary，byteSize/offset/SHA严格验证。
- `BinaryProvider.readChunk({manifest,fileId,offset,limit,signal,priority}) -> Promise<Response>`；`dispose() -> void`。响应保留`x-bio-source-sha256`、`x-bio-file-sha256`、`x-bio-next-offset`和严格content-length，新增`x-bio-manifest-sha256`供两路径共同核验完整manifest身份。`createPreviewTransport`原`api/getFile/hasFile/dispose`调用方式保留。
- 固定可恢复码仅`CONNECTOR_UNAVAILABLE/LOCAL_NETWORK_DENIED/SSH_NETWORK_UNAVAILABLE`；未配对或`DIRECT_NOT_CONFIGURED`在auto选择阶段使用relay，direct-only报错。`DIRECT_PERMIT_EXPIRED`只允许一次重新授权，随后`DIRECT_TRANSFER_TIMEOUT`停止。`DIRECT_ACCESS_DENIED/DIRECT_BINDING_CHANGED/DIRECT_SOURCE_CHANGED/DIRECT_FILE_CHANGED/DIRECT_PROTOCOL_INVALID/DIRECT_REPLAY/SSH_IDENTITY_MISMATCH/SSH_AUTH_REQUIRED/DIRECT_CONFIG_INVALID`及未知错误均fatal。
- 本地session和许可handle都是敏感值；测试仅用显式工程fixture或注入随机源，不捕获真实CLI配对码/SSH stderr/config到工具输出或日志。

所有命令除注明外在上述工作区执行。Python科学测试使用`PYTHONPATH=bio-agent/src:bio-agent`；cloud测试使用明确的绝对PYTHONPATH，不能把FastAPI等装进modeling。先确认失败来自新增接口，再写最小实现；每任务末尾检查diff与证据，不提交。

---

### Task 1: 固定跨端协议与测试向量

**Files:** Create `bio-agent/visualization/direct-contracts.json`、`bio-agent/visualization/direct-contract-cases.json`、`bio-agent/src/bioagent/visualization/direct_contracts.py`、`bio-agent/web/runtime/visualization/direct-contracts.mjs`；Test `bio-agent/tests/test_direct_contracts.py`、`bio-agent/web/runtime/tests/direct-contracts.test.mjs`。

**Interfaces:** Python `validate_direct_request(action: str, value: dict) -> dict`、`validate_channel(value: dict) -> dict`、`validate_permit(value: dict) -> dict`、`manifest_digest(manifest: dict) -> str`；JS同名camelCase函数，`manifestDigest(manifest) -> Promise<string>`使用已有global WebCrypto。输出`directLimits`及上节固定错误码/可恢复集合，未知错误默认拒绝。

- [x] **Step 1:** 写`test_closed_direct_fields_and_boolean_ranges`、`test_direct_limits_match`、`test_channel_cannot_choose_target_or_path`、`test_manifest_digest_matches_across_integer_and_fractional_times`；两端对共同JSON向量作相同接受/拒绝断言，固定256KiB/5s/64KiB/32KiB/2/32/1MiB/120s/5/15min。时间规范断言0→`0000000000000000`、1→`3ff0000000000000`、1.25→`3ff4000000000000`，并覆盖小数/指数时间、SourceRef/file/帧变更使摘要改变。

```js
assert.equal(directLimits.permitBytes, 262144);
assert.throws(() => validateDirectRequest('direct-permit', {...validRequest, limit: 262145}));
assert.notEqual(await manifestDigest(manifest), await manifestDigest({...manifest, source: {...manifest.source, sourceSha256: 'b'.repeat(64)}}));
```

断言中的validRequest/manifest来自本任务共同工程fixture；后续各节断言中的fixture变量均在该节测试文件建立，不代表新增生产接口。

- [x] **Step 2:** 执行`PYTHONPATH=bio-agent/src:bio-agent /opt/anaconda3/envs/modeling/bin/python -B -m unittest tests.test_direct_contracts -v`与`node --test bio-agent/web/runtime/tests/direct-contracts.test.mjs`；预期先因接口未定义FAIL。
- [x] **Step 3:** 实现上述纯验证器与共同类型；沿用既有SourceRef/canonical函数。JS模块可进入Vite浏览器bundle，不引入`node:fs/crypto`。
- [x] **Step 4:** 重跑Step2，全部PASS；增加offset超总大小、schema混用、过长ID、TransportBinding错配和非有限时间向量，确认两端一致。原始framing的UTF-8验证归Task3。

**Deliverable:** 无副作用的跨端协议，后续不另造字段名或放宽未知输入。

### Task 2: 只读预览目录与原子许可登记

**Files:** Create `bio-agent/src/bioagent/visualization/published_reader.py`、`bio-agent/src/bioagent/visualization/direct_permits.py`；Modify `bio-agent/src/bioagent/store.py`、`bio-agent/src/bioagent/visualization/service.py`、`bio-agent/src/bioagent/bridge.py`；Test `bio-agent/tests/test_direct_permits.py`、`bio-agent/tests/test_preview_reads.py`、`bio-agent/tests/test_store.py`。

**Interfaces:** `WorkflowStore(project: Path, *, read_only: bool=False)`保持旧默认；readonly模式要求已有DB，SQLite以`mode=ro`打开并拒绝write。`PublishedPreviewReader(project: Path, store).resolve(preview_id) -> (Path, dict, dict, dict)`、`.read_bytes(preview_id,file_id,offset,limit) -> ReadBlock`；迁移service当前`_preview/_source_current/read`校验，旧方法代理保留既有字段/Base64/限额，追加Task1的manifestSha256元数据。

`DirectPermitStore(project: Path, *, reader: PublishedPreviewReader, clock=time.time)`提供`open_channel(binding: dict) -> ChannelRef`、`issue(channel_id,file_id,offset,limit) -> PermitRef`、`claim(channel_id,handle) -> dict`、`check(claim: dict, offset: int, size: int) -> None`、`close(channel_id) -> dict`。可信Bridge新增`preview-direct-open/permit/close`分支，固定内部binding参数，不把连字符动作直接交给`getattr`。

- [x] **Step 1:** 写`test_single_claim_across_processes`（两个真实子进程竞争，只有一个成功）、`test_expiry_and_reserved_bytes_cannot_replay`、`test_close_blocks_next_frame`、`test_source_change_blocks_issued_permit`、`test_readonly_missing_db_creates_nothing`、`test_direct_read_never_initializes_resource_pool`。修改已有预览读取测试，证明旧reply既有字段/字节与原文件SHA不变、不重新扫描原DCD。

```python
permit = permits.issue(channel_id, file_id, 0, 1)
claim = permits.claim(channel_id, permit['handle'])
with self.assertRaisesRegex(BioError, 'DIRECT_REPLAY'):
    permits.claim(channel_id, permit['handle'])
clock.advance(5.001)
with self.assertRaisesRegex(BioError, 'DIRECT_PERMIT_EXPIRED'):
    permits.check(claim, 0, 1)
```

- [x] **Step 2:** 执行`PYTHONPATH=bio-agent/src:bio-agent /opt/anaconda3/envs/modeling/bin/python -B -m unittest tests.test_direct_permits tests.test_preview_reads tests.test_store -v`；确认新增测试先FAIL。
- [x] **Step 3:** 提取只读catalog；readonly DB复用现有get查询/接受revision，禁止创建Controller、runtime、资源池或PreviewJobs目录。READY必须经已有job记录和manifest证明，不接受任意同名文件夹。
- [x] **Step 4:** 在独立`previews/direct/runtime.json`与`runtime.lock`中实现许可：目录0700、文件0600、`O_NOFOLLOW`、owner/schema核验、flock+原子替换、最多64活动channel且每channel两槽。关闭/过期槽位可被本模块复用，nonce和agentEpoch变化使旧claim失效；不删除目录或科学记录。byte预算在发送前原子预留，崩溃后的已认领nonce不可再次消费。
- [x] **Step 5:** 实现Bridge可信producer分支：verify_target、READY/sourceCurrent/file条目/range，登记后仅返回PermitRef。plain reader读取过程中只消费已登记许可，不能issue/prepare；预览最初的完整文件校验仍保留。
- [x] **Step 6:** 重跑Step2与`PYTHONPATH=bio-agent/src:bio-agent /opt/anaconda3/envs/modeling/bin/python -B -m unittest tests.test_preview_sources tests.test_preview_files tests.test_remote_workflow -v`；PASS。额外覆盖channel64上限、未知schema、symlink、source revision替换、同range不同handle、EOF/重启后旧claim。

**Deliverable:** 服务器能登记有界许可；独立reader只读已有状态并按frame执行校验。

### Task 3: 固定stdio helper和受控SSH通道

**Files:** Create `bio-agent/src/bioagent/visualization/direct_reader.py`、`bio-agent/run_biopreview_direct.py`、`bio-agent/local-connector/config.mjs`、`bio-agent/local-connector/ssh-protocol.mjs`、`bio-agent/local-connector/ssh-reader.mjs`；Test `bio-agent/tests/test_direct_reader.py`、`bio-agent/local-connector/test/ssh-reader.test.mjs`；参考现有`bio-agent/agent/remote-bridge.mjs`，不改变旧SSH行为。

**Interfaces:** Python `run_reader(projects_root: Path, python: Path, input_stream, output_stream) -> int`，仅使用Task2 readonly catalog/permit consumer；固定CLI只有operator配置的`--projects-root/--python`。Node `loadConnectorConfig(path) -> ConnectorConfig`；`buildReaderCommand(profile) -> {executable,args}`；`createSshReader({profile,channel,spawnChild}) -> {hello(signal),read(permit,signal): AsyncIterable<ReadBlock>,close()}`。`hello(channel)`协议record经stdin选择固定根下的projectKey，读取已有execution-target marker，再verify_target并比对ChannelRef的target hash、host identity、UID、projectKey与readerVersion；动态元数据不进入远程命令。

配置`ConnectorConfig = {schemaVersion:'bio.local-connector.v1',allowedOrigin,port,targets}`；每target是`{targetId,connection: bio.ssh-connection.v1,hostKeyAlias}`，hostKeyAlias必须等于targetId。复用已有connection文件归属/路径/内容pin校验；channel的projectKey只能拼接到该profile固定projectsRoot中。known_hosts须有该hostKeyAlias的预先验证条目，OpenSSH负责拒绝不匹配host key；不得从网页获取/自动信任新key。

- [x] **Step 1:** 写`test_partial_prefix_and_eof_fail_closed`、`test_read_consumes_exact_permit_and_no_extra_stdout`、`test_wrong_host_uid_target_or_project_rejected`、`test_expired_after_first_frame_stops`；Node写`remote command quotes operator paths; browser input stays stdin`、`backpressure caps application buffers`、`unknown SSH failure is fatal`、`cancel owns only its helper`、`config replacement refuses old connection`。

```js
await assert.rejects(wrongIdentityReader.hello(), /SSH_IDENTITY_MISMATCH/);
assert.equal(readCalls, 0);
assert.equal(buildReaderCommand(profile).args.at(-1).includes(channel.projectKey), false);
assert.equal(originalScientificFilesChanged, false);
```

- [x] **Step 2:** 执行`PYTHONPATH=bio-agent/src:bio-agent /opt/anaconda3/envs/modeling/bin/python -B -m unittest tests.test_direct_reader -v`与`node --test bio-agent/local-connector/test/ssh-reader.test.mjs`；预期新增接口FAIL。
- [x] **Step 3:** 实现Task1 framing，拒绝超长长度、非法UTF-8、未知record/requestId、错offset/SHA、额外stdout和未完成EOF。helper收到hello后先验证固定根下的已有项目/target marker，再verify_target，只读打开数据库/catalog；缺marker/DB不得初始化。只在许可runtime更新认领/预算，既有数据库journal模式不切换，不使用immutable跳过活动revision。
- [x] **Step 4:** 实现固定SSH参数数组和远程静态路径POSIX quoting：`-T`、`BatchMode=yes`、`StrictHostKeyChecking=yes`、固定known_hosts/HostKeyAlias、`ForwardAgent=no`、`ClearAllForwardings=yes`、`ConnectTimeout=8`。本地允许用户已配置SSH agent但不转发；仅复用归属/权限有效的已有ControlMaster。stdout专供协议；stderr有界处理后只保留固定错误码。
- [x] **Step 5:** 握手后复核配置pins与expected channel；每read验证绑定/长度/hash/nonce。read采用async iterator及stdout backpressure；取消先终止自己创建的SSH/helper并等待退出，不触碰MD worker或其他通道。只有明确的连接timeout/refused/unreachable归可恢复；host-key/auth/协议/未知错误均保持拒绝，日志不保留原stderr或命令。
- [x] **Step 6:** 重跑Step2并运行`node --test bio-agent/agent/tests/remote-bridge.test.mjs bio-agent/agent/tests/node-world-bridge.test.mjs`；用实际本地Python子进程做Node framing对照，校验bytes和无科学副作用。此项标`LOCAL_STDIO`，不写真实SSH通过。

**Deliverable:** 无额外网络监听的计算端reader与可取消、身份固定的本地SSH传输。

### Task 4: 同源逐块授权与默认关闭的功能开关

**Files:** Modify `bio-agent/web/runtime/visualization/preview-api.mjs`、`bio-agent/web/runtime/visualization/preview-relay.mjs`、`bio-agent/web/runtime/bio-runtime.mjs`、`bio-agent/web/runtime/server-config.mjs`、`cloud/bio.py`、`cloud/bio_preview.py`；Test Create `bio-agent/web/runtime/tests/direct-api.test.mjs`、`cloud/tests/test_bio_direct.py`；扩展原`bio-agent/web/runtime/tests/preview-api.test.mjs`、`bio-agent/web/runtime/tests/preview-relay.test.mjs`。

**Interfaces:** 现有`PreviewApi.dispatch(action,payload,signal)`增`direct-open/direct-permit/direct-close`，消费Task1 public类型、Task2 Bridge producer。新增`DIRECT_ACTIONS = {'preview:direct-open','preview:direct-permit','preview:direct-close'}`，纳入cloud闭合ACTIONS；`directEnabled`默认false，overlay只在`previewEnabled`且DIRECT_ACTIONS全获授权时开启。保留原private token接口与原11工具资格。

服务器按当前agent对象维护opaque `agentEpoch`及已发channel关系；tenant/node/target从可信executionTarget，projectKey从SessionProjects推导。`agentEpoch`作为公开TransportBinding.sessionEpoch，用于缓存代际；describe与direct-open两者的TransportBinding必须来自同一检查后的可信context。异步guard前后重验同agent、scoped schemas(agent)、target、binding。失败时撤销已登记channel/permit，不返回handle；撤销不可达时保留5s期限。

- [x] **Step 1:** 写`default binding does not enable direct`、`original eleven scopes allow direct without display tools`、`late scope/agent/target replacement yields no permit`、`browser cannot supply owner/project/agentEpoch`、`describe binding and channel epoch agree and change on agent replacement`、`direct JSON uses shared egress budget and carries no coordinates`；覆盖tenant/node撤销、world token轮换、重复请求及control timeout。

```js
const pending = api.dispatch('direct-permit', payload);
await registered; revokeScopes(); finishRegister();
await assert.rejects(pending, /BIO_SESSION_ACCESS_DENIED/);
assert.equal(revokedChannels.has(channelId), true);
assert.equal(coordinateBytesSentByCloud, 0);
```

- [x] **Step 2:** 执行`node --test bio-agent/web/runtime/tests/direct-api.test.mjs bio-agent/web/runtime/tests/preview-api.test.mjs bio-agent/web/runtime/tests/preview-relay.test.mjs`；cloud执行`PYTHONPATH=/Users/a0000/项目/auto-md/mdagent-web-visualization/cloud:/Users/a0000/项目/auto-md/mdagent-web-visualization/bio-agent/src /opt/anaconda3/bin/python -B -m unittest discover -s cloud/tests -p test_bio_direct.py -v`；新增行为先FAIL。
- [x] **Step 3:** 实现固定动作与closed body，不扩大原科学tool schema。复用同源认证、owned ready tenant transport和固定Project guard；runtime通过可信context生成binding后调Bridge。普通预览/人工Bio不增加display资格要求。relay read将计算端manifest摘要加入HTTP头，describe追加可信TransportBinding，测试证明旧字段/字节不变及新绑定不能由Bridge/浏览器伪造。
- [x] **Step 4:** 控制调用加入8s有界等待和AbortSignal；超时/撤销不发新permit，结果迟到后不返回客户端。所有新JSON继续由原全局budget输出；分别记录授权队列等待和Bridge执行，保持BridgeClient原串行机制。清理仅关闭本次可信记录的channel，不能用陌生请求中的channelId关闭他人状态。
- [x] **Step 5:** 重跑Step2及`node --test bio-agent/web/runtime/tests/bio-plugin.test.mjs bio-agent/web/runtime/tests/platform-gate.test.mjs`，再以独立discover运行`test_bio.py`和`test_bio_preview.py`（同Step2绝对PYTHONPATH）。证明原合法/撤销Bio、中转、静态资源预算仍PASS；不修改实际binding。

**Deliverable:** 浏览器仍经服务器授权，服务器不转发直连坐标，默认配置行为保持中转。

### Task 5: 本地配对、HTTP入口与公平槽位

**Files:** Create `bio-agent/local-connector/pairing.mjs`、`bio-agent/local-connector/scheduler.mjs`、`bio-agent/local-connector/server.mjs`、`bio-agent/local-connector/run.mjs`、`bio-agent/local-connector/config.example.json`；Test `bio-agent/local-connector/test/pairing.test.mjs`、`bio-agent/local-connector/test/server.test.mjs`、`bio-agent/local-connector/test/scheduler.test.mjs`。

**Interfaces:** `createPairing({allowedOrigin,clock=Date.now,randomBytes}).issueCode() -> string`、`.pair({code,clientId,sessionId}) -> {connectorSession,pairingId}`、`.authorize(key,{origin}) -> PairBinding`、`.expireIdle() -> PairBinding[]`、`.close(key)`；`PairBinding = {pairingId,clientId,sessionId,origin,lastSeenAtMs}`。issueCode仅供本地交互终端重新生成一次性码；不经HTTP提供，已有pair不被新码替换，每进程最多128个pair。`createScheduler({activeLimit:2,queueLimit:32}).reserve(context,signal) -> Reservation`、`.release(reservationId)`。

`createConnectorServer({config,pairing,scheduler,readerFactory}) -> {listen(),close(),address,metrics}`；CLI `node bio-agent/local-connector/run.mjs --config <absolute-path>`，仅从用户提供的operator配置启动。health只给协议/ready，不返回target清单或本地路径。

HTTP闭合路由：`GET /v1/health`；`POST /v1/pair`；`POST /v1/open {channel:ChannelRef,priority}`在配对后排队取得槽位/握手并返回`{reservationId}`；`POST /v1/read {reservationId,permit:PermitRef}`消费已有槽位，无二次排队；`POST /v1/close {channelId:null|string,reservationId:null|string}`：非空reservation仅释放该slot；非空channel+空reservation关闭该channel并保留pair；两者空表示断开pair及全部owned channel。pair之后必须有`x-bio-connector-session`头；channel.projectKey必须等于SHA256(PairBinding.sessionId)，clientId/pairingId也必须相同，不能假设ChannelRef包含sessionId。

- [x] **Step 1:** 写`pair code entropy/120s/five attempts/15min idle`、`new local code allows second window without revoking first pair`、`unknown/null Origin and wrong Host rejected before SSH`、`preflight does not authenticate or spawn`、`second account cannot use first pair`、`two active and thirty-two queued with fair foreground rotation`、`close while queued cannot reserve later`、`port occupied gives explicit error`。

```js
const response = await localRequest({origin: 'https://unrelated.invalid', method: 'POST', path: '/v1/open'});
assert.equal(response.status, 403);
assert.equal(sshSpawnCount, 0);
assert.equal(maxActiveReservations, 2);
assert.equal(maxPreviewBufferedBytes <= 1048576, true);
```

- [x] **Step 2:** 执行`node --test bio-agent/local-connector/test/pairing.test.mjs bio-agent/local-connector/test/server.test.mjs bio-agent/local-connector/test/scheduler.test.mjs`；新增接口先FAIL。
- [x] **Step 3:** 实现安全随机配对码128bit、本地session256bit、constant-time比较和内存期限，Origin固定，key仅header/body。真实配对码只向用户交互终端显示一次；本地终端按Enter可issueCode为另一窗口配对，重启才撤销所有pair。工程自动化注入测试随机源，禁止把真实码捕获为ledger/tool输出。
- [x] **Step 4:** 实现HTTP method/content-type/body32KiB/Host/Origin/peer核验、CORS预检及必要expose headers（含manifest摘要）；只bind127.0.0.1，拒绝任意URL/host/path。原始字节使用HTTP backpressure，不做Base64或完整文件缓存。
- [x] **Step 5:** 实现两个全局slot和最多两条owned SSH通道，先reserve后领取permit；单次reservation闲置超过15s释放，取消/断开先终止取数。每pair对应client/session代际；换账号、CLI退出/SIGTERM和peer disconnect终止自己的通道，预览应用buffer<=1MiB。失效运行态不使新请求自动认证。
- [x] **Step 6:** 重跑Step2及全部`node --test bio-agent/local-connector/test/*.test.mjs`，压力验证5视图公平/断开/队列满/慢消费者，计数器和实际进程退出一致；核验并按更新的空间协议清理本轮任务独占临时目录，保护用户文件。

**Deliverable:** 用户可启动并配对的轻量连接器，传输槽位先于许可，后续界面可直接接入。

### Task 6: 网页binary provider、连接状态和跨会话生命周期

**Files:** Create `bio-agent/web/visualization/src/local-connector.mjs`、`bio-agent/web/visualization/src/direct-provider.mjs`、`bio-agent/web/visualization/src/relay-provider.mjs`；Modify `bio-agent/web/visualization/src/transport.mjs`、`bio-agent/web/visualization/src/dock.mjs`、`bio-agent/web/visualization/src/view-bridge.mjs`、`bio-agent/web/visualization/src/style.css`、`bio-agent/web/runtime/ui-cards/client.source.js`；原build脚本更新`bio-agent/web/runtime/ui-cards/client.js`及`dsh-plugins/dsh-bio-ui/client.js`。Test Create `bio-agent/web/runtime/tests/direct-provider.test.mjs`，扩展`bio-agent/web/runtime/tests/preview-browser-transport.test.mjs`、`bio-agent/web/runtime/tests/preview-dock-lifecycle.test.mjs`、`bio-agent/web/runtime/tests/view-bridge.test.mjs`。

**Interfaces:** `createLocalConnectorClient({port,sessionId,clientId,fetch}).pair(code,signal) -> pairingId`、`.reserve(channel,priority,signal) -> Reservation`、`.read(reservation,permit,signal) -> Response`、`.release(reservation)`、`.close(channelId)`、`.dispose()`；key仅私有内存。

`createRelayProvider({api,sessionId,viewportId,fetch}) -> BinaryProvider`；`createDirectProvider({api,connector,clientId}) -> BinaryProvider`。`createPreviewTransport({sessionId,viewportId,clientId,fetch,binaryProvider,mode:'auto'|'relay'|'direct-only',onTransport})`保留现有调用与relay默认，增加`.setProvider(provider,mode='auto')`；缺mode时默认auto，未配对即relay。内部`authorizeFile(manifest,signal) -> TransportBinding`在文件用于新窗口/恢复前调用同源describe并比较完整source；默认api由transport创建，provider可接受其api引用。缓存key包含Task4返回的ownerBinding/sessionEpoch；不每播放一帧额外往返。onTransport收到`{mode:'direct'|'relay',reasonCode:null|string}`，不含凭据。

`createDock`生成一次clientId传给transport和`bindView`；`bindView`沿用dock.clientId，原独立调用仍可生成默认UUID。换会话/关闭时clientId和pairing失效，迟到操作必须通过现有generation/AbortSignal检查。保存视图状态不加入key、handle、坐标或operator路径。

- [x] **Step 1:** 写`direct obtains reservation before permit`、`direct file bytes/source/frame match relay`、`expiry retries one authorization only`、`authorization/hash/host mismatch never falls back`、`network fallback restarts current file after same-manifest check`、`direct-only records no relay bytes`、`cached file from old session cannot be reused`、`pair/read response after switch cannot reopen dock`。

```js
await assert.rejects(transport.getFile(manifest, fileId), /DIRECT_ACCESS_DENIED/);
assert.equal(relayReads, 0);
assert.equal(firstOffsetAfterNetworkFallback, 0);
assert.equal(oldSessionFileReachedRenderer, false);
```

- [x] **Step 2:** 执行`node --test bio-agent/web/runtime/tests/direct-provider.test.mjs bio-agent/web/runtime/tests/preview-browser-transport.test.mjs bio-agent/web/runtime/tests/preview-dock-lifecycle.test.mjs bio-agent/web/runtime/tests/view-bridge.test.mjs`；新增行为先FAIL。
- [x] **Step 3:** 提取原relay读块实现；direct按`reserve → api(direct-permit) → read → release`执行，失败也release。以Task1 manifestDigest填direct-open及共同响应校验；两路径的manifest/source/file摘要、长度和完整文件SHA都匹配才接受。最后describe复核再解码。保留两请求上限、64MiB缓存、旧prefetch abort正确性。
- [x] **Step 4:** transport实现`auto/relay/direct-only`选择和白名单fallback，direct provider只返回码、不自行在非零offset换路径。auto取数故障必须丢弃未验证文件parts、重新授权比对manifest并从offset0重取；手动setProvider撤销旧route代际的pending/预取。未知错误fatal。缓存key含授权session/client代际、target、完整source、manifest/file摘要；需要重新授权的缓存命中不能制造新Agent成功回执。会话/来源变化立即取消本视图预取并清除相关缓存。
- [x] **Step 5:** 在dock加“连接本机”“断开”、默认18773端口、配对码密码输入与“超算直连/服务器中转”状态。仅在点击时尝试回环访问；使用固定127.0.0.1 URL、omit/error选项，支持时设置loopback targetAddressSpace。页面/代理CSP或权限不支持则明示状态，不提示关闭安全功能；source/镜头/帧读数照旧。
- [x] **Step 6:** 重跑Step2及`node --test bio-agent/web/runtime/tests/preview-player.test.mjs bio-agent/web/runtime/tests/view-runtime.test.mjs bio-agent/web/runtime/tests/view-api.test.mjs`，然后`node scripts/build-bio-ui.mjs`和`node scripts/build-bio-visualization.mjs`；正常/390px界面、真实末帧/时间、用户旋转后定位、刷新和两账号切换验收。未有HTTPS真实页面时只标本地工程通过。

**Deliverable:** 原网页可显式配对、选择通道并按同一科学来源播放，原显示成功依据不变。

### Task 7: 整体工程验收、候选源包与真实网络门槛

**Files:** Create `scripts/validate-bio-direct.mjs`、`bio-agent/docs/local-preview-connector.md`、`docs/superpowers/reports/2026-10-12-hpc-local-preview-direct.md`；Modify `bio-agent/deploy/build_bundle.py`、`bio-agent/deploy/verify_release.py`、`bio-agent/docs/web-visualization.md`、`bio-agent/README.md`；Test Create `bio-agent/web/runtime/tests/direct-acceptance.test.mjs`；证据放新目录`.superpowers/sdd/2026-10-12-hpc-local-preview-direct/`，不复用旧验收结果为新通道背书。

**Interfaces:** `validateDirectEvidence(receipt) -> {status:'PASS'|'WITH_PENDING'|'FAIL', checks: Record<Layer,'PASS'|'FAIL'|'PENDING'>}`；receipt的`layers`对象分`LOCAL_COMPONENTS/LOCAL_STDIO/BROWSER_LOOPBACK/REAL_SSH/REAL_LINK/SCIENTIFIC`，每项含`status/evidence`，并有source/manifest/file哈希、准确构建与浏览器/OS版本、两路径字节、RTT/队列/吞吐/显示/卡顿、取消及原文件哈希。缺实际HTTPS浏览器回读或真实SSH路由时相应项必须PENDING；原文件哈希不变不能证明采样/科学接受，SCIENTIFIC单独判断。

- [x] **Step 1:** 写`unobserved direct cannot be reported passed`、`relay fallback invalidates direct-only measurement`、`coordinate bytes bypass cloud but controls/assets counted`、`download budget stops at 512MiB`；用确定工程向量测试误标与边界。

```js
const result = validateDirectEvidence(localOnlyReceipt);
assert.equal(result.checks.REAL_SSH, 'PENDING');
assert.equal(result.checks.REAL_LINK, 'PENDING');
assert.equal(result.status, 'WITH_PENDING');
assert.equal(validateDirectEvidence(directOnlyWithRelayBytes).status, 'FAIL');
```

- [x] **Step 2:** 执行`node --test bio-agent/web/runtime/tests/direct-acceptance.test.mjs`；新增report验证器先FAIL。实现validator与有界工程fixture，复用旧300残基/配体和现有预览数据，不复制旧ledger/环境或启动MD。
- [x] **Step 3:** 执行相关汇总：`PYTHONPATH=bio-agent/src:bio-agent /opt/anaconda3/envs/modeling/bin/python -B -m unittest discover -s bio-agent/tests -v`；`BIO_TEST_PYTHON=/opt/anaconda3/envs/modeling/bin/python node --test bio-agent/web/runtime/tests/*.test.mjs bio-agent/local-connector/test/*.test.mjs`；Task4三个独立cloud runner及`node --test dsh-plugins/dsh-remote-world/test/*.test.mjs`。修复新失败后仅重跑相关项，保留实际计数。
- [x] **Step 4:** 在实际浏览器进行配对、deny/revoke/reload、SourceRef/末帧/时间、两会话、慢链路、连接器退出和fallback验收。HTTPS入口/证书与代理策略只使用已有合法测试配置；没有配置则保留该项PENDING，不装根证书或改安全策略。浏览器交互使用当前环境的CUA工具。
- [x] **Step 5:** 写当前构建/许可链的工程报告：1/3/5视图各180s、5Mbps/100ms中转对照，JS/CSS冷/暖分组；完整许可链计时与裸stdio分开；暖结构<=4s、播放<=6s、缓存卡顿<=1%及30%相对改进各报实测/未测。工程网络不标真实SSH/HPC通过。
- [x] **Step 6:** 扩展原source-only bundle的明确allowlist：新协议、helper、connector、provider、文档和当前哈希前端资源；排除实际config、known_hosts、pair/permit状态、数据库、科学环境和数据。执行`/opt/anaconda3/bin/python -B bio-agent/deploy/build_bundle.py`；新候选解包到本轮新evidence目录后以`verify_release.verify(extracted_root)`核验文件、scientific runtime及当前前端hash。保留上一候选；cloud代码仍走原部署流程，不声称Bio包包含完整cloud部署。
- [x] **Step 7:** 完成diff/依赖锁/空间检查和一次独立整分支审查；按Blocker/Important/Nice-to-have闭环必要修正并做相应回归。汇报实际新文件/目录大小、活跃进程和保留产物；停止本轮owned QA服务；清理已登记、任务独占、可重建且非交付/证据的临时路径，保存清理前后空间记录。执行`git diff --check`，记录未提交/未部署边界。
- [ ] **Step 8:** **后续真实验收门槛，当前保持PENDING。** 用户提供配置并授权测试部署后，核验本地SSH路径不经5Mbps服务器、同一target和可读READY预览；同批<=16MiB文件两路径交替3次，完整许可链及1/3/5视图按spec验证。超过512MiB停止；登录节点不同则先另行设计只读出口，不放宽target核验。至少一个实际HTTPS浏览器、真实SSH及链路完整通过后才安排启用。

**Deliverable:** 可核验的本地候选与配置/启动说明；真实部署与测速未完成时显式保留未验收，不把组件通过称为已上线或已经提速。

## 计划自审与执行交接

- Spec目标/现状/分工覆盖Task1/2/4/6；配对/SSH/许可覆盖Task2/3/5；缓存/回退覆盖Task6；性能与实际启用覆盖Task7。五项Review Focus均已对应明确测试或真实验收门槛。
- 共享签名/字段使用Task1定义；旧Base64 Bridge不改成自由二进制RPC，直连另走固定stdio。全局slot先于许可，读取helper不构造Controller，agentEpoch只来自服务器。
- 本轮已完成全部本地实施与工程验收步骤，真实网络 Step 8 持续 PENDING。用户暂缺连接信息不阻止本地工程实施，但真实超算/HTTPS部署与提速结论持续PENDING。
- 计划审阅确认后，按已选择Native在本会话执行Task1–7本地部分；不再次询问旧依赖安装或执行方式。新依赖、生产配置、真实部署或其他破坏性操作若后续需要，单独说明具体对象后再取得授权。
