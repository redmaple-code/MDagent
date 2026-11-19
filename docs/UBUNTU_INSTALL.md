# Ubuntu 独立安装与交付

本安装入口用于把 MDagent 的对话、领域工具、kit 编排器和看板部署到用户自己的 Ubuntu 机器。安装器准备依赖、模型配置和用户服务；用户提供自己的 API Key、任务输入和力场。安装检查通过表示软件与入口准备完成，真实模型回答和科学计算结果需要另行验收。

## 适用机器

- Ubuntu 22.04 或 24.04，x86_64 或 aarch64；以普通用户运行安装器。
- 能访问 Ubuntu 软件源、nodejs.org、npm 和 PyPI；这是联网安装包，依赖尚未包含在压缩包中。
- 首次准备系统软件需要 sudo；Python 和 Node 运行环境安装到用户数据目录。
- 默认安装 LAMMPS、GROMACS、MPI、Packmol 和 Moltemplate。GPU、商业力场和具体研究任务的数据另行配置。
- kit 为公司独立提供的组件，不在 MDagent GitHub 仓库中。客户端 bootstrap/install 接口只连接云服务，不能替代本安装器。

Claude Code 可用于协助维护，不是本安装器的运行依赖。

## 公司制作可发送的安装包

在包含本安装入口的 MDagent Git checkout 中执行：

```bash
python3 scripts/build_install_bundle.py \
  --kit /absolute/path/to/agent_deploy_kit \
  --output /absolute/path/to/MDagent-Ubuntu.tar.gz \
  --without-public-corpus
```

打包器先核对 kit 的代码、依赖与执行接口，再打包白名单代码。压缩包包含 `MDagent-Ubuntu/MDagent/`、`MDagent-Ubuntu/agent_deploy_kit/` 和文件哈希清单。它排除运行凭据、会话、计算案例、历史状态、策略库、日志、Git 数据和 node_modules，并将 shell 脚本转换为 LF 换行。目标文件已存在时拒绝覆盖。

本次交付包使用 `--without-public-corpus`，省略两份可选的公开错误问答语料，保留查询程序与许可说明。因此这项语料检索暂不可用。经过分发审查的 `gromacs_answered.jsonl` 和 `lammps_answered.jsonl` 可另行放到 `MDagent/external_corpus/`，与 `corpus_lookup.py` 同目录，并保留原帖链接、作者与许可。省略该参数时打包器会检查语料中的疑似凭据；命中时中止并只报告文件名。

安装包可直接发给获准使用公司代码的人员，接收人无须使用公司的 GitHub token 或阿里云账户。公司更新 kit 后应制作新版本安装包，先通过下面的验收步骤，再分发给正式用户。

## 用户安装

```bash
tar -xzf MDagent-Ubuntu.tar.gz
cd MDagent-Ubuntu/MDagent
bash install.sh --plan
bash install.sh
```

`--plan` 只显示步骤，不下载或修改文件。正常安装会依次检查 kit、安装环境、准备独立 kit 副本、配置路径、安装 dsh profiles、构建原生依赖、运行回归、配置模型、生成并启动用户服务。任何必需步骤失败都会以非零状态结束。

首次会询问模型服务、模型 ID、API 地址和 Key。支持 `deepseek`、`openai-compatible` 或 `custom`；自定义服务需要提供真实模型 ID、API 地址及对应协议。Key 隐藏输入，保存于权限为 600 的凭据文件；不要把它放在命令行参数或发送给其他人。DeepSeek 默认模型依据其官方 API 文档设置，用户应按自己的账户权限确认可用型号。

安装后默认访问：

- 工作台：`http://127.0.0.1:8080/workbench`
- 对话界面：`http://127.0.0.1:3091/`

远程 Ubuntu 服务器可从自己的电脑建立 SSH 本地端口转发，再访问上述地址。不要把本机工作台直接暴露到公网；公司官网入口与多用户认证是另外的部署流程。

## 使用已有源码与 kit

```bash
cd /absolute/path/to/MDagent
bash install.sh --kit /absolute/path/to/agent_deploy_kit
```

默认数据根是 MDagent 代码目录旁的 `mdsim-data`，任务目录是数据根中的 `projects`。可指定独立位置：

```bash
bash install.sh --kit /home/user/company-kit \
  --data-root /home/user/mdagent-data \
  --run-case-base /home/user/mdagent-cases
```

安装器不运行 kit 原有 `deploy.sh`，不修改源 kit。代码被准备到数据根的 `runtime/kit`，旧机器路径通过 AST 改写；运行状态从空闲态初始化。重复安装保留现有任务状态、用户模型设置和 Key。准备后的 kit 代码被修改、源 kit 变更或安装目录选择不同，会明确拒绝复用；请为新版本选择新的数据根，并安排数据迁移。

## 可选参数

| 参数 | 用途 |
|---|---|
| `--engine both` | 默认，准备 LAMMPS 与 GROMACS |
| `--engine lammps` | 只准备 LAMMPS 计算引擎 |
| `--engine none` | 准备对话和工具环境，不安装本地计算引擎；不能认定计算可用 |
| `--skip-apt` | 复用已有系统依赖和 MPI；安装器仍核对命令与引擎能力 |
| `--no-start` | 生成启动器和服务，暂不启动 |
| `--check` | 只检查现有安装的依赖、路径、凭据配置和 kit 哈希 |
| `--reconfigure-model` | 显式更换已有模型设置和 Key |

兼容服务示例，API Key 仍由终端隐私输入：

```bash
bash install.sh --kit /home/user/company-kit \
  --provider openai-compatible \
  --model your-model-id --base-url https://your-provider.example/v1
```

自定义协议使用 `--provider custom --api openai-completions`，也可按服务要求选择 `openai-responses` 或 `anthropic-messages`。需确认服务实际支持所选协议；配置可保存不代表联网调用成功。

自动化部署可用 `--non-interactive`，通过受控进程环境变量 `MDAGENT_MODEL_API_KEY` 提供 Key，结束后清理该变量。重装默认保留现有配置，只有显式指定 `--reconfigure-model` 才更换。

首次安装的非交互配置示例，输入的 Key 不进入命令历史：

```bash
read -rs -p 'API Key: ' MDAGENT_MODEL_API_KEY; printf '\n'
export MDAGENT_MODEL_API_KEY
bash install.sh --provider deepseek --non-interactive
unset MDAGENT_MODEL_API_KEY
```

上述兼容服务示例同样用于首次安装；更换已配置的服务需要追加 `--reconfigure-model`。

## 启动与维护

安装器生成数据根中的 `bin/mdagent-local` 和 `install.env`，持续绑定 kit、案例目录、MPI、注册脚本、Python 与 Node。新安装采用 `workspace-write` 权限模式，自动批准开关关闭；这是单用户安装，不创建旧机器的 `mdorch` 账号或 sudoers。

```bash
systemctl --user status mdagent-web mdagent-dashboard mdagent-orchestrator
systemctl --user status mdagent-evolution.timer
journalctl --user -u mdagent-web -u mdagent-dashboard -u mdagent-orchestrator
```

用户服务通常随用户会话运行。如需退出 SSH 或尚未登录时持续运行，请由机器管理员确认并设置该用户的 systemd linger 策略。

没有用户 systemd 会话时使用 `--no-start`，分别在终端运行以下入口，把 `DATA` 替换为实际数据根：

```bash
DATA/bin/mdagent-local web
DATA/bin/mdagent-local dashboard
DATA/bin/mdagent-local orchestrator
```

终端交互使用 `DATA/bin/mdagent-local tui`；检查使用 `DATA/bin/mdagent-local check`。直接运行原始 `run-dsh.sh` 不会自动读取新安装的环境文件，优先使用生成的启动器。

停止安装生成的服务不会删除数据：

```bash
systemctl --user stop mdagent-web mdagent-dashboard mdagent-orchestrator mdagent-evolution.timer
```

## 安装检查与交付验收

默认安装会核对 Node、pnpm、Python 虚拟环境与解析库、Moltemplate、dsh CLI 和 profiles、模型凭据是否填写、本地执行路径、kit 文件哈希，以及所选计算引擎的功能。启动服务时另外检查用户服务状态、两个本地 HTTP 入口和状态总线 schema。

LAMMPS 必须实际报告 `reaxff` 与 `qeq/reaxff`；Ubuntu 软件包缺这些功能时安装中止，按旧迁移指南编译支持 REAXFF 的 MPI 版本后，用 `--skip-apt` 重试。原 kit 的输入语法、MPI ABI、力场文件和任务参数仍需计算验收。

正式交付前在一台干净 Ubuntu 机器完成：

1. 安装成功并能重启、停止和再次启动服务。
2. 使用用户自己的 Key 发起一次真实对话，确认回答与错误提示。
3. 用指定的小规模案例完成编排器接管、日志更新、产物生成和错误回读。
4. 重新运行安装器，确认原 Key、案例和在途任务状态保留。

此安装器不自动发送模型请求或启动科学任务。`model_response_verified`、`scientific_job_verified`、`ssh_submission_verified` 和 `research_council_verified` 默认为 false；不能把 HTTP 200 当作上述验收通过。

领域研究/模型研讨工具目前有独立的供应商与搜索协议；聊天改用新模型不会自动改写所有研讨调用，默认研讨链未配置、未验证。浏览器自动化所需 Chromium 及系统库也需按插件说明另行准备。本地安装不包含公司官网登录、阿里云部署或已经接通的多用户 SSH 提交接口。

## 开发与协作

前端人员可修改图片、Logo、页面布局及模型配置交互；模型配置字段应保持 `apiKeyEnv/api/baseURL/models` 与默认路由一致，不把用户 Key放入前端包。安装器在 `scripts/install_config.py` 管理配置，在 `scripts/install_service.py` 管理运行环境和用户服务。

超算负责人另行对接身份、SSH 连接、调度器、作业提交、取消和结果回读。当前 main 的独立 SSH/Slurm 模块尚未挂载到 cloud 应用，旧 MCP `sim_hpc_submit` 主要渲染脚本；本地安装成功不代表公司多用户超算链已经可用。

## 本次验证记录（2026-10-12）

在 Windows 开发机完成 76 项定向测试，其中 69 项通过、7 项因 POSIX 权限或符号链接条件跳过。Shell 语法和只读 `--plan` 检查通过；公司 kit 的 32 个代码文件完成静态准备检查，生成的初始状态总线通过实际 JSON Schema 校验。安装包另核对文件哈希、解压路径和 shell 的 LF 换行。

Ubuntu 22.04/24.04 的源码检查工作流模板位于 `docs/ci/ubuntu-installer.yml.example`，由维护者复制到 `.github/workflows/ubuntu-installer.yml` 后启用；目前尚未运行该 CI。尚不能认定干净 Ubuntu 的全量安装、真实模型对话、科学计算或 SSH 提交通过。Windows 全量仓库测试存在 `fcntl` 和 POSIX 权限等平台错误，没有判为通过。目标 Ubuntu 上的安装器仍执行全量测试硬门，失败即中止。
