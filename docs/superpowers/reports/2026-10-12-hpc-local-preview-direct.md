# MDagent HPC-to-local Preview — implementation report

Status: **LOCAL_IMPLEMENTED_ENGINEERING_VERIFIED / REAL_NETWORK_PENDING**.

本会话按已批准计划，在 `/Users/a0000/项目/auto-md/mdagent-web-visualization` 完成本地实施与工程验收。未新增依赖、提交、推送、部署、修改实际绑定或连接超算。当前源包是未部署候选，cloud 代码仍需沿用独立部署流程。

网页新增本地配对、自动／中转／仅直连选择与链路状态。直连坐标从同一登记执行目标的只读 reader 到本地连接器；服务器继续授权每块数据和发送元数据、Agent 控制及前端资源。默认 `directEnabled=false`。

## Implementation

- Python/JS 闭合协议、语义 manifest SHA、五秒单次范围许可、跨进程原子认领；readonly SQLite/catalog 和固定二进制 stdio reader。
- 受控 SSH 参数、配置/known_hosts pin、host/UID/project/target 握手及逐块复核。两个全局 reader 与两个传输槽位，32 项队列；同项目、相同 owner/agent epoch 的空闲 reader 可重新绑定已登记 channel，避免五窗口反复导入。
- 128-bit／120s 配对码、五次错误限制、256-bit 内存 session、15 分钟闲置、固定 Origin/Host/loopback/CORS。完整许可校验后再发 HTTP 字节，应用坐标缓冲有界。
- 服务器 trusted epoch/owner/binding、原十一工具资格、逐块授权及异步前后复核；撤销时已拥有通道可执行终止清理，不能发新许可。
- 两条网页 provider、64 MiB 缓存、整文件 SHA 与三项来源头校验。明确网络故障回退前重新授权并重取当前文件；来源／身份／权限／未知错误不回退；重授权失败清缓存。配对 key/nonce 不进入保存视图、URL、Agent 或日志。

主要修改位于 `bio-agent/src/bioagent/visualization/`、`bio-agent/local-connector/`、`bio-agent/web/visualization/src/`、`bio-agent/web/runtime/visualization/`、`cloud/bio_preview.py`、打包/验收脚本及对应测试。当前工作树还包含此前可视化移植的未提交成果；本轮相对开始时的文件哈希记录在 ledger 的 `direct-changes.json`，不能将全部 Git diff 都视作本轮新增。

## Validation

最终相关汇总 **264/264 PASS**：Python 113、Node runtime/connector/agent 113、cloud 三个独立 runner 3+15+7、remote-world 13。窄屏修改后相关 UI/生命周期测试另行复核 9/9 PASS，计数不重复并入汇总。实际执行入口：

- modeling Python：`PYTHONPATH=bio-agent/src:bio-agent /opt/anaconda3/envs/modeling/bin/python -B -m unittest discover -s bio-agent/tests -v`；临时夹具使用本轮登记的独占目录。
- Node：`BIO_TEST_PYTHON=/opt/anaconda3/envs/modeling/bin/python node --test bio-agent/web/runtime/tests/*.test.mjs bio-agent/local-connector/test/*.test.mjs bio-agent/agent/tests/*.test.mjs`。
- cloud：以绝对 cloud/Bio `PYTHONPATH` 和 `/opt/anaconda3/bin/python -B -m unittest discover -s cloud/tests -p <file> -v` 独立运行 `test_bio_direct.py`、`test_bio.py`、`test_bio_preview.py`。
- remote-world：`node --test dsh-plugins/dsh-remote-world/test/*.test.mjs`；`git diff --check` 通过。

一次独立整分支审查发现的三项 Important 均完成 RED→GREEN：初始握手后的目标变化、SSH/HTTP 读取中途网络故障分类、冻结播放被错误验收。Minor EOF 空行已修正。完整审查、Ruling、失败复现和回归日志保存在 ledger；未再次审查，也未把审查时未完成的浏览器结果当作通过。

当前构建的规范场景为 **直连/中转交替运行 1、3、5 个视图，每视图缓存播放 180s，共 18 个视图**，全部功能与性能 PASS。由实际 renderer 的首次帧变化测播放启动；180s 内每视图观测到 1,780–1,793 次帧推进、200 个不同预览帧。实际末源帧索引 202（网页 203/203）、时间 507.5 ps；来源 SHA 与原文件哈希保持一致。夹具是 300 残基及配体的工程数据，没有执行 MD。

下表为每组最慢视图。暖结构计时从点击来源到结构可见，JS/CSS、配对和首次来源校验另行计时；冷显示包含这些步骤。中转工程模拟共享 5 Mbps／100ms，其中预览、资源及控制共用 4 Mbps 预算。直连采用实际本地 Python stdio helper；没有使用真实 SSH 网络。

| 路径 | 视图数 | 暖结构最大/s | 冷显示最大/s | 实际播放启动最大/s | 卡顿最大/% | 结果 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 直连 | 1 | 1.246 | 4.403 | 0.133 | 0.000 | PASS |
| 中转 | 1 | 1.159 | 1.693 | 0.133 | 0.000 | PASS |
| 直连 | 3 | 1.388 | 2.263 | 0.150 | 0.690 | PASS |
| 中转 | 3 | 1.639 | 2.843 | 0.227 | 0.000 | PASS |
| 直连 | 5 | 2.168 | 3.836 | 0.251 | 0.309 | PASS |
| 中转 | 5 | 2.127 | 3.463 | 0.177 | 0.000 | PASS |

该轮直连与中转各传输 2,737,620 bytes 坐标；直连坐标经 cloud 为 **0 bytes**。控制、资源和坐标总计 6,816,590 bytes，低于 512 MiB 验收上限。连接器最多两条 helper、两个传输槽位，应用坐标缓冲峰值 178,176 bytes；结束时 active/queued/helpers/buffer 均为零。这些记录证明本地工程路径正确，不证明真实超算已提速。

实际浏览器另行验证 1280×900、390×844、配对码清空、旋转/首末帧定位、播放推进、暂停和断开。390px 面板宽 366、高 572、scrollHeight 572，全部控件可见。性能场景使用 440px 视图；随后仅修改 <=430px CSS，JS SHA 不变，窄屏由独立布局证据确认。测试页已关闭，viewport override 已恢复。

历史诊断保留：初轮计时存在测量缺陷，不能替当前验收；`browser-qa-reviewed` 同时运行 5 直连加 5 中转、共 10 个 renderer，功能 PASS、性能 FAIL。该压力结果没有被改写为五视图通过。当前规范证据为 [browser-qa-alternating/validation.json](/Users/a0000/项目/auto-md/mdagent-web-visualization/.superpowers/sdd/2026-10-12-hpc-local-preview-direct/browser-qa-alternating/validation.json)，布局证据为 [layout-evidence.json](/Users/a0000/项目/auto-md/mdagent-web-visualization/.superpowers/sdd/2026-10-12-hpc-local-preview-direct/browser-qa-responsive/layout-evidence.json)。浏览器回读 Chrome 155.0.0.0，工程宿主 Darwin 27.0.0。

## Verified candidate

当前前端为 `bio-view-BfATNuXW.js`，SHA256 `3c181b587dbcf8d78a8583bc0f31c922ce65f3ab4f8be639598502da979e9cc5`；CSS 为 `mdagent-bio-visualization-BjUFSADF.css`，SHA256 `ba46c9bc545bf11ebac9d5705d54c9b8180be52f2583705892d3eeb5de8ef364`。Molstar/Vite 版本及依赖锁保持不变。

源包 [bio-20261012-385a92c28e14.tar.gz](/Users/a0000/项目/auto-md/mdagent-web-visualization/bio-agent/deploy/dist/bio-20261012-385a92c28e14.tar.gz) 含 139 个条目、2,869,002 bytes；archive SHA256 `5bb4c81b23bccab6e0583466af34a6163668dadcad15c5d11ad61e38ffd4ca07`。已解包执行 `verify_release.verify(extracted_root)`，integrity PASS。冻结科学 runtime SHA 仍为 `2d12f3d6387783f5f28153a020267b65ab97a95c3b16e22a65e07b977d2829c0`；simulationExecuted=false。

## Pending gates

用户尚无实际 SSH/operator 配置或 HTTPS 测试入口。REAL_SSH、REAL_LINK、SCIENTIFIC 均 PENDING。不得把本地 stdio、模拟带宽、原轨迹 hash 不变称为真实超算、已经提速、已部署或科学采样验收。30% 实际提速、真实 HTTPS/CSP/LNA、两条真实链路交替三轮及实际账号登录切换仍待配置后验证。

## Evidence and storage

本轮 [ledger](/Users/a0000/项目/auto-md/mdagent-web-visualization/.superpowers/sdd/2026-10-12-hpc-local-preview-direct/progress.md) 包含 baseline、Ruling、RED/GREEN、完整测试日志、源包校验、截图及逐构建 browser receipts。已停止本轮 QA/reader 服务，核对真实路径、目录边界及无符号链接后，清理 29 个登记路径、名义大小 32.36 MiB：独占 `tmp` 解包树、三份被替代的本轮候选及其收据、未使用的 B72Cpims 中间前端、六轮合成 operator 配置及一张未就绪截图。最终收据与诊断证据保留。

清理前后可用空间 115.128 → 115.161 GiB，实测增加 33.27 MiB；APFS 和并发写入可能使实测与文件大小不同。开始时约 104.671 GiB，任务期间其他磁盘变化不归因于本轮清理。保留当前交付候选、原先 `bio-20261012-c5720c05cab4` 回退候选、所有源码/未提交修改、依赖缓存、科学夹具与必要证据。来源未核实的历史临时目录及既有产物未清理。准确清单见 [cleanup.json](/Users/a0000/项目/auto-md/mdagent-web-visualization/.superpowers/sdd/2026-10-12-hpc-local-preview-direct/cleanup.json)。

启动与配置说明：[local-preview-connector.md](/Users/a0000/项目/auto-md/mdagent-web-visualization/bio-agent/docs/local-preview-connector.md)。后续最必要步骤为提供无凭据的 SSH Host/config 路径与可用 DSH HTTPS 测试预览，经过测试部署授权后完成真实网络门槛。
