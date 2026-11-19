# 3D 窗口计算进度 — 本地实施记录

2026-10-15。工作区 `mdagent-web-visualization`，基线 `2e91527a77056a71dccdf54dac0e30ebc5052515`；本轮未 commit、push 或部署。

已在现有 3D 窗口加入可折叠计算进度摘要与详情。接受结果绑定精确任务版本；原始结构手动关联本会话任务，包含尚无接受结果的任务。当前计算阶段按控制器依赖选择，区别于正在查看的结果阶段。MD 百分比来自已校验工作进程记录；未知步数不伪造进度。任务失败、暂停和断线保留实际记录，步数满格与结果接受分别显示。稳定速度形成后才估算剩余时间，记录过期则撤回。

新增 human-only `progress` 读取复用 `human:get` 权限，经固定 host 接口调用 `tick(advance=False)`；不创建完整工具记录、不推进计算、不返回坐标、采样数组、日志、路径或 nonce。轮询不重叠，关闭、换绑及会话切换取消客户端读取并隔离晚回复。已发往计算端的只读查询可以完成，不中止科学任务。

进度详情放在可滚动内容区，固定摘要不会随展开增加高度。原有移动、调整大小、配色模板、DSH 日夜主题和轨迹控制继续可用。

## 验证

- Node：`node --test bio-agent/web/runtime/tests/*.test.mjs bio-agent/local-connector/test/*.test.mjs bio-agent/agent/tests/*.test.mjs`，200/200。
- Python：`PYTHONPATH=bio-agent:bio-agent/src python -B -m unittest discover -s bio-agent/tests -v`，148/148。
- 源包凭据检查：`python -B -m unittest discover -s bio-agent/deploy/tests -v`，3/3。
- `node scripts/build-bio-ui.mjs` 同步两份客户端；`node scripts/build-bio-visualization.mjs` 构建及资产哈希核验通过。入口 `bio-view-DtAzwyB3.js`，样式 `mdagent-bio-visualization-CIRRpPgF.css`。
- 独立只读审查发现的 RPC 取消、过期 ETA、依赖阶段顺序、暂停状态均以失败用例复现后修复，完整套件重新通过。
- 原生 DSH loopback 页面：输入结构保持未关联，手动关联保留来源与镜头；接受结果自动关联。播放时刷新进度，源帧从 1 前进到 78，来源与镜头一致。浅色、深色和 319×750 边界检查完成；恢复跟随系统。工程页面模型调用与中转坐标字节均为 0。

证据：`.superpowers/sdd/2026-10-15-compute-progress/`，含测试日志、审查修复记录、布局对照、主题及播放回读、截图、源文件清单与清理收据。体验入口为 `http://127.0.0.1:3081/`，明确标注本地工程计算端。

## 限制与收尾

当前接入 Bio/OpenMM，LAMMPS 实际任务进度尚未接入。浏览器验收使用明确标注的工程结构/轨迹及完成记录；运行、失败、断线和 ETA 语义由自动测试覆盖。真实超算 SSH、HTTPS、带宽和科学有效性未验收。未安装依赖，未创建新的发布源码包，既有 PR 不包含本轮未提交修改。

保护了开工时的 16 个并发修改文件，收尾哈希保持一致。自动清理仅涉及本轮一次性测试夹具目录和 6 个已被当前构建替代的前端文件；清理前核验无打开句柄。实测可用空间从 102.932 GiB 到 102.948 GiB，增加约 16.2 MiB。源码、证据、当前体验实例和原有资产均保留。
