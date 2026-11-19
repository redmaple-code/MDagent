# Local preview connector

当前为本地实现与工程验收候选。真实 SSH、HTTPS 网页兼容性、真实链路速度及生产启用仍需操作者配置与验收。

DSH 网页继续由服务器提供。授权、manifest 和前端资源仍走服务器；配对后，预览文件字节只从同一登记计算目标经本地 SSH 与回环连接器进入浏览器。原始轨迹留在计算端。

## Operator setup

复用现有 Node，无需安装 connector npm 依赖。把 `local-connector/config.example.json` 复制到一个新建的操作者配置文件，填写实际值并设为 `0600`；不要放密码、token 或私钥。`allowedOrigin` 是准确的 DSH origin，非默认端口必须包含。非回环工程测试须使用 HTTPS。

`targetId` 必须等于已登记 execution target，`hostKeyAlias` 必须与其一致。SSH 必须到达相同 host identity、UID、release 和项目根目录。首版不能把不同登录节点或共享文件系统节点视为相同目标。

`known_hosts` 须有该 alias 的已核验 host key。按机构公布指纹或既有操作者流程核验；连接器不运行 `ssh-keyscan`、不从网页学习 key、不自动接受新 key，也不询问密码。SSH config 必须归属有效且为 `0600`；known_hosts 不能由组或其他用户写入。绝对规范路径与文件内容在进程内固定，修改配置后须重启。

通过既有 release 流程把候选 Bio 源包部署到同一计算目标。`run_biopreview_direct.py` 使用该目标已有的兼容 Python，只读 READY 预览与现有 workflow DB，只更新本模块的许可状态，不构造 Controller、启动科学 worker 或监听计算端端口。

生产启用是单独的部署步骤。cloud binding 需要全部 `PREVIEW_CONTROL_ACTIONS`（describe/prepare/status/window/cancel，不含 read），再增加 `preview:direct-open`、`preview:direct-permit`、`preview:direct-close`。两组权限完整时 overlay 才开启 `directEnabled`，默认关闭。人工浏览仍要求原十一项 Bio 工具资格，不额外要求 display 工具。cloud 代码沿原部署流程发布，不包含在 Bio 源包中。

当前前端的输入 SourceRef 使用绑定文件名及哈希的预览 ID；应把同一候选中的前端与计算端一起发布。旧计算端无法解析新 ID 时会拒绝打开，不自动中转或猜选同名文件。

## Start and pair

在交互终端运行：

```sh
node /absolute/bio-release/local-connector/run.mjs --config /absolute/operator/connector.json
```

默认只监听 `127.0.0.1:18773`。端口占用时明确返回 `DIRECT_PORT_IN_USE`，不扫描或替换其他服务。保持终端开启；启动后显示一次本地配对码，有效两分钟。按 Enter 可给另一窗口生成新码，已有配对保持有效。重启会撤销所有配对。

在 DSH 的“结构与轨迹”中填端口和配对码，点击“连接本地”。若浏览器提示本地网络权限，仅对预期 DSH origin 授权。配对成功后码框清空，session key 只保存在此页面私有内存，不进入视图保存、URL、浏览器存储、Agent 消息或日志。

传输固定为 **仅超算直连**。未配对或断线时停止；服务器的旧预览文件 GET 和私有 read 均返回 `PREVIEW_RELAY_DISABLED`，不进入 Bridge 坐标读取。

“断开”、收起 dock、切换会话、刷新、闲置失效或关闭终端会关闭自己的通道。每个新窗口独立配对。过期许可最多重新授权一次，再过期返回 `DIRECT_TRANSFER_TIMEOUT`。收起面板只停止自身读取/轮询，不自动取消其他窗口可能共享的预览派生任务。

## Limits and deployment requirements

所有目标与窗口共用两个活动传输槽和最多两个 SSH reader，队列最多 32 项，前台客户端公平轮转。空闲 reader 仅在 target/project/host/UID/owner/agent epoch 完全一致时重绑另一个已登记 channel；每个客户端的配对、actor、许可与取消仍独立。发放、认领及逐帧消费都复核目标与 marker；变化会永久关闭旧 channel。

先预留本地槽位，再向服务器领取五秒、单次、最多 256 KiB 的许可。stdio 单帧最多 64 KiB。连接器先校验完整许可，再发 HTTP 字节，使过期能返回明确错误。两个许可 buffer 最多 512 KiB；加上有界 framing，连接器的坐标应用缓冲包络小于 1 MiB。SSH、内核缓冲、Python helper 与总 RSS 单独计算；浏览器编码缓存仍为 64 MiB。

配对码为 128 bit，本地 session 与许可 key 为 256 bit；每码最多五次错误，配对闲置 15 分钟后失效，每进程最多 128 个配对，每计算项目最多 64 个 channel。打开新视图或恢复时重新授权后才复用缓存；授权失败清缓存。已撤销资格仍可终止自己先前登记的传输，这不能发新许可或运行科学工作。

网页 CSP/代理须允许浏览器在自身回环策略下连接所配置的 `http://127.0.0.1:<port>`。客户端使用 `credentials: omit`、`redirect: error`、`targetAddressSpace: loopback`。CSP/LNA 拒绝会显示连接错误并停止；不要关闭证书、mixed-content、CORS 或浏览器安全保护。真实 HTTPS 行为仍是启用门槛。

## QA layers

使用已有科学解释器：

```sh
BIO_TEST_PYTHON=/absolute/modeling/bin/python node --test bio-agent/web/runtime/tests/*.test.mjs bio-agent/local-connector/test/*.test.mjs
PYTHONPATH=bio-agent/src:bio-agent /absolute/modeling/bin/python -B -m unittest discover -s bio-agent/tests -v
```

当前按钮版使用 `scripts/serve-bio-dsh-preview.mjs` 在原生 DSH 中验证结构、配套轨迹、播放与时间条。计算端通过实际只读 Python stdio 运行，非真实 SSH。旧 `qa-bio-direct-browser.mjs` 是 2026-10-12 的 direct/relay 对照工具；旧对照记录只保留为历史证据，不能作为当前唯一直连构建的验收命令。

实际验收记录 renderer 帧推进、首末帧、文件字节、180 秒缓存播放、模型请求及中转坐标计数。seek 正常不能证明 play 正常，卡顿包含没有恢复的尾段。

`scripts/validate-bio-direct.mjs <receipt.json>` 会拒绝 direct-only 混入中转坐标、下载超过 512 MiB、缺身份／帧推进及性能证据等误标。真实 SSH、真实链路与科学有效性分别判断。启用前用同一批 READY 文件在两条实际链路交替三轮，每批最多 16 MiB，总量达到 512 MiB 即停止；验证真实 HTTPS 下的 1/3/5 视图，完整授权／预留／读取链与裸 SSH 吞吐分别报告。本地工程数字不能支持“真实提速至少 30%”。
