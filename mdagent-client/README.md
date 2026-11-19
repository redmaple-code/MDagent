# mdagent-client · 云端 MD 智能体客户端

像 Codex / Claude Code 一样使用：**装在你自己机器上，本地运行，脑子在云端**。
和你对话的智能体运行在阿里云服务器上（对话、记忆、进化层都在云端你的独立空间里）；
它可以经你批准读写你本机指定目录的文件。

**闭源说明**：本客户端不含任何智能体代码/提示词/模型配置——只有联网、聊天、文件执行器三部分。
你的 token 只能调用 API，碰不到云端代码和模型本体。

**三个版本，选一个**：
- `mdagent_tui.py` —— **终端 TUI 版（推荐）**：终端全屏工作台（顶部状态条 + 左栏对话（markdown 真渲染、流式 ▌）+ 右栏目标面板/文件操作动态 + 底部输入框），写/删批准弹卡片按 y/n；需 `textual`（一键安装脚本会自动装）
- `mdagent_web.py` —— **网页工作台版**：浏览器打开即用，和你本地 workbench 同版式（左对话气泡 + 右本地文件浏览器 + 悬浮写批准卡）
- `mdagent_client.py` —— 终端 REPL 版（无 textual/无浏览器环境用）

## 安装（只需 Python 3.8+；TUI 版另需 textual，脚本自动装）

**一行命令（新机器推荐，无需提前拿 token）**：

```bash
# Linux / macOS —— 现场问用户名密码,登录后自动装 TUI 版并启动
curl -k "https://<SERVER-IP>/mdagent/v1/bootstrap.sh" | bash
```

**一键安装（已有 token 的话）**：

```bash
# Linux / macOS —— 装完自动打开浏览器工作台
curl -k -H "Authorization: Bearer mda_你的token" "https://<SERVER-IP>/mdagent/v1/install.sh" | bash
```

```powershell
# Windows(PowerShell) —— 下载安装脚本再执行
curl.exe -k -H "Authorization: Bearer mda_你的token" "https://<SERVER-IP>/mdagent/v1/install.ps1" -o mdagent_install.ps1
powershell -ExecutionPolicy Bypass -File mdagent_install.ps1
```

装好后：客户端在 `~/.mdagent/`，以后敲 `mdagent` 启动（默认终端 TUI 版；`mdagent --web` 起网页版）。
安装脚本内含你的 token（服务器按你的身份现场生成），**请勿转发给别人**。

**手动安装（不想 curl|bash 的话）**：

```bash
# Linux / macOS 终端 TUI 版
curl -k -H "Authorization: Bearer mda_你的token" "https://<SERVER-IP>/mdagent/v1/tuiclient.py" -o mdagent_tui.py
pip3 install --user textual
python3 mdagent_tui.py --token mda_你的token
```

```powershell
# Windows(PowerShell) 终端 TUI 版
curl.exe -k -H "Authorization: Bearer mda_你的token" "https://<SERVER-IP>/mdagent/v1/tuiclient.py" -o mdagent_tui.py
python -m pip install --user textual
python mdagent_tui.py --token mda_你的token
```

```powershell
# Windows(PowerShell) 网页工作台版
curl.exe -k -H "Authorization: Bearer mda_你的token" "https://<SERVER-IP>/mdagent/v1/webclient.py" -o mdagent_web.py
python mdagent_web.py --token mda_你的token
```

```bash
# Linux / macOS 网页工作台版
curl -k -H "Authorization: Bearer mda_你的token" "https://<SERVER-IP>/mdagent/v1/webclient.py" -o mdagent_web.py
python3 mdagent_web.py --token mda_你的token
```

启动后**浏览器自动打开工作台**（没自动开就复制终端里打印的完整地址，带 `?t=`）。
首次在页面里确认服务器/token/开放目录即可。以后启动直接 `python3 mdagent_web.py`。

**REPL 版**（无 textual 环境）：

```powershell
curl.exe -k -H "Authorization: Bearer mda_你的token" "https://<SERVER-IP>/mdagent/v1/client.py" -o mdagent_client.py
python mdagent_client.py --token mda_你的token
```

没有账号？注册(审批制,需自带大模型 API Key):
```bash
curl -k -X POST "https://<SERVER-IP>/mdagent/v1/apply" \
  -H "Content-Type: application/json" \
  -d '{"username":"你的名字拼音","password":"登录密码(≥8位)","contact":"联系方式","reason":"用途","provider":"kimi-coding","api_key":"sk-你的key"}'
```
审批通过后**不用再找管理员要 token**——直接用注册的用户名+密码登录,token 自动获取:

- **网页版**:打开工作台 → 配置卡里填用户名密码点"登录"(token 自动落配置)
- **TUI/REPL 版**:首次启动选 `1) 账号密码登录`,输用户名密码即可
- **脚本/curl**:`POST /v1/login {"username","password"}` → 返回新 token(旧 token 同时作废)

登录每次都会**轮换 token**(旧 token 立即失效,服务端只存哈希拿不出旧的);怀疑泄露也可随时 `POST /v1/me/token/rotate` 自助换。换 LLM key 不用找管理员:`POST /v1/me/apikey {"provider","api_key"}` 三处(账本/凭据/模型设置)同步生效。

## 使用

**TUI 版**(Claude Code 风格:全宽 transcript + 底部输入框/状态条,Ctrl+G 开目标侧坞;logo 与本地 dsh TUI 同款):

```
  ▐▙   ▟▌ █▀▛▖ ▖
  ▐▌▘ ▝▐▌ █  █▞        云端 MD 智能体 · 终端工作台
  ▐▌█ █▐▌ █  █         开放目录 ~/proj · 写/删逐笔批准 · /help 看命令
  ▐▌·▀·▐▌ █  █▚
  ▐▌   ▐▌ █▄▜▘ ▘
      ᴱˣᵖ
❯ 帮我给这个体系建模…
  ✶ 先想体系组成,再想力场参数……(思维链暗色流式,完成后自动折叠)
  好的,步骤如下(markdown 流式 ▌):
  ⏺ 📖 read topol.top  10:24
  ⏺ ✏️ write GOAL.md  10:25
╭ ⚠ 写批准: ~/proj/data.lmp (1234 字节) ──╮(y 批准 / n 拒绝,100s 未答自动拒)
╰ ❯ 输入消息…                            ╯
⠹ 🔧 localfs_write · ⛁ default · ~/proj · 用量 3 ·不限 · 🎯 2/5 · Ctrl+G 目标面板
```

- 启动自动回放当前项目的云端历史(最近 50 条);回复 token 级流式(正文+思维链,末尾 ▌)
- **思维链**暗色斜体流式显示,回答完成后自动折叠
- **⏺ 文件操作行**:智能体每次读/写/建目录/删除你本机文件都在对话流里记一行(红=被拒绝/失败)
- **目标面板**:状态条常显 🎯 进度;Ctrl+G 或 /goal 开右侧坞看完整子目标勾选 + 文件操作历史
- 写和**删除**都要逐笔批准(删除不可恢复,/auto 也不免);批准后 ⏺ 行立即可见
- **⚡ 执行命令**:智能体可以在你本机后台跑 shell 命令(lmp/gmx/python 等,cwd=开放目录)——`/auto` 开时免批准直接跑,关时弹批准卡(完整命令可见,y/n)。命令立即后台化,输出进开放目录下 `.mdagent_run/<id>.log`,智能体自己读日志跟进;默认 7200s 超时强杀(最长 86400s)。网页版不支持执行命令

**网页版**：地址栏打开工作台（书签可用，token 已种 cookie）——界面和你本地 workbench 同版式：**左栏**聊天气泡对话（Enter 发送，`项目` 框换会话分组），**右栏**本地开放目录文件浏览器（智能体读写的文件实时可见）；云端要写文件时右下角弹**写批准卡**（路径+大小+内容预览），点"批准写入"才落盘；顶栏📂换开放目录、⚙改服务器/token，状态条显示桥在线状态和每小时用量。

**REPL 版**：
```
python3 mdagent_client.py            # 首次后免参数
你: 帮我看看当前目录的 topol.top 有没有问题
```

TUI 版与 REPL 版共用同一组命令（TUI 在输入框里打）：

| 命令 | 作用 |
|---|---|
| `/root <目录>` | 切换开放给智能体的目录（默认=启动目录） |
| `/project <名>` | 切换会话项目（不同项目上下文独立） |
| `/auto` | 写操作免确认开关（**默认关**，每次写入都要你批准） |
| `/goal` | 目标面板（当前任务子目标展开，TUI 也可用 Ctrl+G 开关侧坞） |
| `/status` | 桥连接状态 + 每小时用量 |
| `/quit` | 退出 |

## 权限与安全（重要）

- 智能体**只能碰开放目录内**的文件；`../`、绝对路径、软链接逃逸都会被客户端拒绝
- **执行命令 = 本机跑程序**：智能体发起的 shell 命令在你机器上以你的身份后台执行（cwd=开放目录，日志在 `.mdagent_run/`）。`/auto` 关闭时每条命令你都能看到全文并逐笔批准；**`/auto` 开启后写文件和命令执行都免批准**——只在信任当前任务时开。命令权限等同你本人在终端敲命令，可访问 lammps 等系统环境（这是设计），所以批准前看清命令内容
- **写文件逐笔批准**：云端发起写入时，你会看到路径+大小+内容预览（首尾各一段），批准才落盘
- **传输防中间人**：客户端内置服务器证书指纹（pinning），连接时自动比对，指纹不符会拒绝连接——客户端请只从 `/v1/webclient.py`、`/v1/client.py` 下载，下载后可让管理员当面核对文件里印的指纹 `CERT_SHA256`
- 你只能访问**你自己**的云端空间（对话/记忆），看不到其他用户，也改不了云端任何代码
- **同一账号同时在多台机器开客户端时**，文件操作会落到"正在轮询"的那台机器上——同一账号请只在一个客户端里用
- token 即账号全部权限：不要泄露、不要提交进 git；泄露立即 `POST /v1/me/token/rotate` 自助吊销换发（或联系管理员吊销）。
  建议首次用登录或 `--token` 存入配置后，之后直接启动（token 不再出现在命令行，避免进 shell 历史）
- 用量不限（用户自带 API key，费用走自己额度）；同时 1 个任务
- 设计内风险（与 Codex 同类）：开放目录内的文件智能体都可读；把整盘/家目录设为开放目录等于全开放，请只开放项目目录
