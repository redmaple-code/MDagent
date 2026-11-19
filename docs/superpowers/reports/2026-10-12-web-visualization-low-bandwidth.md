# MDagent Web Visualization — Implementation and Acceptance

Status: **IMPLEMENTED_LOCALLY / NOT_DEPLOYED**. 用户于 2026-10-12 确认设计、七项实施计划及独立前端依赖安装；本会话完成实现、回归、浏览器工程验收和候选源包核验。

## Done

- 现有 Bio 页头接入 3D 入口与可收起的 Molstar 视图；结构、真实轨迹、表示、选择、聚焦、播放/暂停、详细窗口和刷新恢复已实现。窄屏 390×844 与正常窗口均保留聊天输入。
- 最终计算节点派生独立 XTC 分段，来源、文件、原子映射和真实帧时间绑定 SHA-256；默认骨架与已识别配体最多 200 个真实帧，首末帧完整，详细窗口最多 64 帧。原始输入/DCD/checkpoint 保留。
- 同源鉴权和私有固定 Bridge 复用现有身份。所有租户共享 4 Mbps 预览发送预算；JS/CSS、预览字节与控制 JSON 均计入。每视图最多两个在途请求。
- `bio_observe` / `bio_view` 等待实际浏览器回执，分别校验 scoped display capability；人工 Bio 操作保留原 11 工具资格。用户镜头/导航变化可使旧命令拒绝；定位保持新镜头。
- 取消/失败允许在原 worker 确认退出后建立新执行 attempt。旧记录及失败目录保留且计入配额；UNKNOWN 继续关联原任务；完整文件才发布。纯水默认显示实际周期盒，无虚拟原子；刷新恢复镜头并禁用不适用控件。

## Changed files

工作区：`/Users/a0000/项目/auto-md/mdagent-web-visualization`；分支 `codex/bio-web-visualization`；HEAD 仍为 `b8c2b719c5ccf928a6b63ff29de605f321d0145f`。未提交。

| 范围 | 主要路径 |
|---|---|
| 计算端派生、任务和资源 | `bio-agent/src/bioagent/visualization/`、`resource_pool.py`、`bridge.py`、`controller.py`、`run_biopreview.py` |
| 预览与显示协议 | `bio-agent/visualization/`、`bio-agent/web/runtime/visualization/`、`bio-runtime.mjs`、`server-config.mjs` |
| 浏览器和入口 | `bio-agent/web/visualization/`、`ui-cards/client.source.js` 及两个生成副本、`dsh-plugins/dsh-bio-ui/` |
| 云端出口 | `cloud/bio_preview.py`、`preview_budget.py`、`bio.py`、`tenants.py`、`app.py` |
| 构建、验收及来源 | `scripts/build-bio-visualization.mjs`、`validate-bio-visualization.mjs`、`bio-agent/deploy/`、`bio-agent/provenance/visualization-*` |
| 测试与文档 | 相关 Python/Node/cloud 测试；`bio-agent/docs/web-visualization.md`、README、remote-workflow、platform-integration、批准的计划与本记录 |

主 checkout `/Users/a0000/项目/auto-md/mdagent-pr` 的分支/HEAD 和原有未跟踪设计文档保持原样。未修改 KSH 原文件、科学 runtime、真实 binding 或生产配置。

## Validation performed

| 实际执行 | 结果与证据 |
|---|---|
| `PYTHONPATH=bio-agent/src:bio-agent /opt/anaconda3/envs/modeling/bin/python -B -m unittest discover -s bio-agent/tests` | **88/88**；`python-final-current.log` |
| `BIO_TEST_PYTHON=/opt/anaconda3/envs/modeling/bin/python node --test bio-agent/web/runtime/tests/*.test.mjs` | **64/64**；`node-verified-delivery.log` |
| 绝对 cloud PYTHONPATH + `/opt/anaconda3/bin/python -B -m unittest tests.test_bio tests.test_bio_preview` | **22/22**；`cloud-final.log` |
| `node --test dsh-plugins/dsh-remote-world/test/*.test.mjs`；独立 `tests/test_gate.py` | **13/13**；**57/57** |
| 既有 cloud 各模块和 HPC 的既定 runner | auth11、bridge21、legacycloud26、reviewsecurity11、stream12、HPC28 均 PASS；保留独立日志 |
| `node scripts/build-bio-ui.mjs`；`node scripts/build-bio-visualization.mjs`；`git diff --check` | PASS；独立锁定 Molstar5.13.1 / Vite8.2.2 |
| 300 残基 + 3 个配体重原子工程浏览器 | 详细11–20帧→概览→源第203帧/507.5ps→旋转→Agent定位→刷新，回执/镜头一致；水盒镜头刷新一致；正常/窄屏截图已保存 |
| 既有 MD 接受结果，只读原数据 | **READ_AND_MAPPED**；原文件哈希不变；未新跑生产 MD，未判断收敛 |
| 10 万原子工程压力、XTC 往返与文件预算 | 解码窗口最大1,200,000 bytes；坐标误差测试 <=0.01Å；文件预算、来源变化、错误/取消/权限路径通过 |

首次 Node 汇总误用了非科学解释器，三项科学 preflight 未通过；设置明确的 `BIO_TEST_PYTHON` 后重跑64项全部通过。cloud 混合 unittest discover 与原 runner 不兼容；按独立 unittest / gate脚本 / pytest，以及绝对 PYTHONPATH 验证，未修改原测试来隐藏错误。

### 共享带宽浏览器验收

所有视图使用一个共享出口：5 Mbps 链路、100 ms RTT，预览与冷 renderer 资源共同按4 Mbps发送。每组缓存播放180秒。工程网关预先生成的预览与真实 WebGL 回读均有记录；无实际回读的运行保持 NOT_ACCEPTED。

| 构建证据 | 覆盖 | 首次显示最大 | 暖资源结构最大 | 卡顿最大 |
|---|---|---:|---:|---:|
| acceptance-005 | 1/3/5视图，9/9 PASS | 10.410s | 2.406s | 0.06% |
| acceptance-008，当前交付资源 | 5视图，5/5 PASS | 9.883s | 1.861s | 0.74% |

当前资源播放启动最大 0.193s；五个视图末帧均回读 sourceFrameIndex202 / timePs507.5。暖资源<=4s、播放<=6s、冷显示<=12s、缓存卡顿<=1%目标通过。冷JS/CSS发送共 3,586,155 bytes，未使用缓存结果替代冷资源计数。早期冷加载超标和未启动运行的证据仍保留。

当前 frontend `bio-view-B6pF2rfd.js`，SHA-256 `c87956a6c9a3163a7d355cceb2567a36670d7d39aac6ac977bab03d7de4ea598`，与发布包一致。最终当前版本完整证据目录：`/Users/a0000/项目/auto-md/mdagent-web-visualization/.superpowers/sdd/2026-10-12-web-visualization-low-bandwidth/acceptance-008`。1/3视图来自 acceptance-005；其后仅补齐显示命令/恢复控件，当前资源的5视图重新测量，未将旧资源称为当前资源。

### 审查闭环与候选包

一次独立只读整分支审查返回 With fixes（4 Important、2 Minor、无 Critical）：重试、已取消下载复用、跨会话异步 dock、scoped显示撤销、概览恢复请求、纯水盒恢复。六项均在本会话修复并新增回归；没有遗留审查 Minor，也没有声称获得第二轮独立批准。实际网页又补齐鼠标镜头版本、恢复控件同步与水盒能力控件。闭环记录：`/Users/a0000/项目/auto-md/mdagent-web-visualization/.superpowers/sdd/2026-10-12-web-visualization-low-bandwidth/review-fixes.md`。

候选 Bio server 源包：`/Users/a0000/项目/auto-md/mdagent-web-visualization/bio-agent/deploy/dist/bio-20261012-c5720c05cab4.tar.gz`（2,828,665 bytes，120个allowlisted文件）。SHA-256 `eaf75a08637306f7d8978bc03b2c2a06d1cebcf0d6a77ce04d5c27f1fb20239a`。解包核验 **PASS**；scientific runtime SHA `2d12f3d6387783f5f28153a020267b65ab97a95c3b16e22a65e07b977d2829c0` 不变。包含当前源码、锁、资源、来源及 notices；不含科学环境、凭据、用户轨迹/输入或node_modules。云控制服务代码位于本工作区，另随其既有部署流程更新；此 Bio server 包不声称包含完整云服务部署。

## Important caveats

本地组件、真实 WebGL 和工程带宽验收完成，**未 commit、push、合并或部署**。工程网关不等于生产公网上的鉴权/SSH完整链路；生产多租户、节点延迟、物理设备/GPU/FPS、实际大轨迹及科学采样充分性仍未验收。GUI联调用固定工程来源和本地测试RPC；没有调用真实收费LLM。

单进程全局预算目前通过PID和进程锁拒绝第二个裁决器；无共享后端的多worker预览不支持。helper 使用资源池CPU/槽位预留及120s墙钟限制，尚无OS强制内存/CPU隔离。首次来源验证、大源文件哈希/派生、资源排队及真实SSH逐块复核耗时需部署后单独测量，不能用本地预生成预览的冷资源指标代替。

核心 DSH 依赖复用了已有rc.6环境（Cordis4.0.4 / Schemastery3.18.4；锁中4.0.1 / 3.18.1）；没有升级核心依赖。精确部署环境兼容性仍需在测试实例复核。

### 执行裁决与空间

- 用户选择Native顺序实施；依赖安装只在独立前端目录，科学环境保持原样。
- app worktree工具不能识别外层非Git目录；在实际嵌套仓库建立相邻linked worktree，保护主checkout。
- 用户禁止自动提交、部署和清理，覆盖技能默认提交/清理流程；未执行这些动作。
- 纯水概览使用真实周期盒；显式全部原子才产生原子预览，未造虚拟原子。
- 既有artifacts结果增加经接受校验的sourceRef，原科学契约/人工资格保持独立。
- 既有DSH RPC为unary，使用有界poll及实时browser ack；未发明双向RPC，旧心跳不能替代观察。
- 不每64KiB重新扫描原DCD；已发布来源指纹和当前接受revision复核，完整文件SHA在缓存首次校验。
- 共享预算计入静态资源、二进制与控制JSON；进程锁/PID拒绝无共享协调器的多worker。
- cloud采用原独立runner及绝对PYTHONPATH，科学/预览使用已验证modeling解释器；未接受Xcode许可或改系统环境。

当前前端目录约257MiB（依赖约172MiB），验收/ledger约182MiB，候选包约11MiB。已停止本轮临时QA服务，未删除文件。pnpm共享store约363MiB含既有缓存；不能将目录大小当作本任务新增或物理释放量。安装规模在授权上限内。

仅列清理候选：`/Users/a0000/项目/auto-md/mdagent-web-visualization/bio-agent/web/visualization/dist` 中旧哈希资源约79.3MiB；准确逐文件路径/大小见 `/Users/a0000/项目/auto-md/mdagent-web-visualization/.superpowers/sdd/2026-10-12-web-visualization-low-bandwidth/storage-review.json`。它们对应旧验收，删除会影响重放，建议保留当前和至少一个已验证回退后另行确认。验收记录、当前依赖、原始科学文件、源码及环境均继续保留。

## Recommended next step

在获授权的测试部署中接入本工作区云端路由和候选Bio server包，明确开启preview/display actions，再测真实共享5Mbps与SSH路径、断线恢复及两个独立账号的权限隔离。生产配置与科学运行需另行授权。用户入口/操作说明见 `bio-agent/docs/web-visualization.md`。

![当前网页工程实测](/Users/a0000/项目/auto-md/mdagent-web-visualization/.superpowers/sdd/2026-10-12-web-visualization-low-bandwidth/visualization-delivery.png)

![当前资源五视图完成状态](/Users/a0000/项目/auto-md/mdagent-web-visualization/.superpowers/sdd/2026-10-12-web-visualization-low-bandwidth/bandwidth-verified-delivery.png)
