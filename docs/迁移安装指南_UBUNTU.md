# 迁移安装指南（Ubuntu）

新机器请优先使用 [Ubuntu 独立安装与交付](UBUNTU_INSTALL.md) 和根目录 `install.sh`。以下保留旧版本的手工迁移步骤；其中 Claude Code 辅助工作台、旧 Kimi 凭据和路径约定不是新安装器的必需条件。

把 dsh-agent（MD Exp 全链分子模拟智能体）迁到一台新 Ubuntu 机器的完整步骤。
按顺序执行，每步末尾有验证命令。**第 0 步先装 Claude Code**——后续装环境、跑回归、排查问题都靠它驱动。

适用版本：v1.5.1+20260904 起。目标系统：Ubuntu 22.04 / 24.04（x86_64）。

---

## 第 0 步 · Claude Code（先装这个）

```bash
# 官方原生安装(自带运行时,不依赖 node)
curl -fsSL https://claude.ai/install.sh | bash
# 重开终端后验证
claude --version
```

登录：`claude` 首次运行按提示完成授权（Anthropic 账号或 API key）。

> 说明：本项目的 agent 本体是 dsh（@deepseek-ai/dsh harness + Kimi K3)，不是 Claude Code;
> Claude Code 是**装机器、迁移、排障的工作台**——本指南后面所有"让 Claude 跑一下"都指它。

## 第 1 步 · 系统依赖（apt)

```bash
sudo apt update
sudo apt install -y git curl wget build-essential cmake gfortran \
    openmpi-bin libopenmpi-dev python3.11 python3.11-venv python3-pip \
    cron rsync jq
```

- `python3.11`：回归测试与看板用（Ubuntu 22.04 默认仓没有的话：`sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt update` 再装）。
- `cron`：演化层 observe 定时驱动要用；装完 `sudo systemctl enable --now cron`。

## 第 2 步 · Node.js ≥ 22 + pnpm

dsh 需要 node ≥ 22（`Promise.withResolvers` / zstd)；系统 apt 的 node 太老，装官方二进制：

```bash
mkdir -p ~/node2219 && cd ~/node2219
wget https://nodejs.org/dist/v22.19.0/node-v22.19.0-linux-x64.tar.xz
tar -xf node-v22.19.0-linux-x64.tar.xz --strip-components=1
echo 'export PATH="$HOME/node2219/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc
node --version   # 应 >= v22
corepack enable && corepack prepare pnpm@latest --activate
pnpm --version
```

> `run-dsh.sh` / `setup.sh` 会探测 `$HOME/node2219/bin`——路径保持这个就不用改任何配置。

## 第 3 步 · LAMMPS（含 ReaxFF）+ Packmol

模拟内核。**必须带 REAXFF 包**（本项目势函数是 ReaxFF/Tersoff),apt 仓的 `lammps`
包可能没编译进去，推荐源码装（约 10-20 分钟）:

```bash
# Packmol(建模装分子,apt 仓就有)
sudo apt install -y packmol
packmol < /dev/null 2>&1 | head -3   # 能跑即装好

# LAMMPS 源码编译(MPI + REAXFF)
cd ~ && git clone -b stable --depth 1 https://github.com/lammps/lammps.git
cd lammps && mkdir -p build && cd build
cmake ../cmake \
  -D BUILD_MPI=yes -D PKG_REAXFF=yes -D PKG_MANYBODY=yes \
  -D PKG_MOLECULE=yes -D PKG_KSPACE=yes -D PKG_RIGID=yes \
  -D PKG_EXTRA-DUMP=yes -D PKG_REPLICA=yes \
  -D CMAKE_INSTALL_PREFIX=$HOME/lammps-install
make -j$(nproc)
make install
echo 'export PATH="$HOME/lammps-install/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc

# 验证:命令存在 + ReaxFF 真的编进去了
which lmp_mpi mpirun
lmp_mpi -h | grep -i reaxff   # 有输出 = ReaxFF 可用,没输出就是没编进去,白装
```

> 想让 `lmp_mpi` 不在 PATH 时也能跑：把 `lammps-install/bin` 软链到 `/usr/local/bin` 亦可。
> sim_exec 白名单按命令名放行（`lmp_mpi`/`mpirun`/`packmol`)，不锁路径。

## 第 4 步 · 拉代码（两个仓库）

```bash
mkdir -p ~/AI-MD && cd ~/AI-MD
git clone <dsh-agent 仓库地址> dsh-agent          # 本智能体(注意:不含凭证,见第 6 步)
# kit(执行层状态机)独立目录,与 dsh-agent 平级:
git clone <agent_deploy_kit 仓库地址> agent_deploy_kit
# 或从旧机器整体拷:rsync -av 旧机:~/AI-MD/ ~/AI-MD/
```

目录约定：

```
<某父目录>/
├── dsh-agent/            # 代码根(纯代码,无数据无凭证)
├── agent_deploy_kit/     # 执行层 kit(v3.3)
├── mdsim-data/           # 数据根(第 5 步自动播种;带知识迁移就从旧机拷过来)
└── 程序运行-case/         # 任务扫描树(默认名,可用 RUN_CASE_BASE 改)
```

**带"知识"迁移**（可选，对应多机知识共享）：旧机的 `mdsim-data/memory/` 整个 rsync 过来 + kit 根的 `.v3.3_strategy_db.json` 单个文件——
atoms/场景/SOP/研判笔记/LEDGER/提案/策略学习全在内；不带就是全新大脑，从零开始积累。

### 经验传递：不用新建文件夹，拷这两个就够

智能体学到的全部经验 = **一个目录 + 一个文件**，都在既有位置：

| 内容 | 位置 | 说明 |
|---|---|---|
| 记忆/演化层 | `mdsim-data/memory/` | atoms（观测）、scenarios（场景聚类）、SOP.md（处置手册）、notes/（根因研判笔记，v1.4 起）、LEDGER（提案审计 + L2 晋升/降级记录）、proposals/（演化提案） |
| 恢复策略学习 | `<kit>/.v3.3_strategy_db.json` | 策略 uses/success_rate 后验、history 结账记录、strategy_adjustments 演化压制；v1.5 起还含 **L2 新策略条目**（DSL rule + canary 标记）与**降级墓碑**（带过去才不会在新机把已判死刑的策略再起草一遍） |

```bash
# 从旧机把经验带过来(新机已跑过 setup 后执行;目标目录已存在,逐文件合并不覆盖整机数据):
rsync -av 旧机:~/AI-MD/mdsim-data/memory/ ~/AI-MD/mdsim-data/memory/
scp 旧机:~/AI-MD/agent_deploy_kit/.v3.3_strategy_db.json ~/AI-MD/agent_deploy_kit/
```

- **幂等安全**:atoms 按 id+refs 去重，重复拷不会产生重复记忆；场景/SOP 由 atoms 重建。
- **LEDGER 不只是审计**:L2 用它做僵尸 canary 的饿死计时锚（最早 install 记录）——带 LEDGER 过去，晋升/降级/冷却时间线才连续；不带则新机按全新安装重新计时（不致命，只是冷却/饿死重新起算）。
- **唯一要小心**：如果两台机器**同时**在跑真实恢复（都在写 strategy db 的 success_rate 后验），后拷的会覆盖先考的——一主一从或错开时间拷即可；日常单机使用无此问题。
- 反向同理：新机跑出的经验想回灌旧机，同一行 rsync 换个方向。

### 不该带的（设备专属，带了反而坏事）

| 内容 | 位置 | 为什么 |
|---|---|---|
| 凭证 | `mdsim-data/dsh-home/`(Kimi key、SCNET AK/SK)、`state/scnet_approve/pin.json` | 每机各自配置（第 6 步）；PIN 哈希随机盐，拷过去也用不了 |
| 在途运行状态 | `<kit>/.v3.3_state.json`、`status_bus.json`、`bus_journal.jsonl`、`mdsim-data/state/`、`projects/`、`scratch/` | 旧机跑到一半的状态机/任务快照，新机无对应作业，带了会误判相位 |
| 事件流 | `mdsim-data/events/` | 纯审计流水，可选；要完整审计链就带，否则新机重新记 |

## 第 5 步 · 一键 setup（路径改写 + 依赖安装）

```bash
cd ~/AI-MD/dsh-agent
bash scripts/setup.sh "$HOME/AI-MD/agent_deploy_kit"
```

它做的事（全部幂等，可重复跑）:
1. `mcp/md_config.json` 四字段（kit 路径/数据根/任务树）——缺文件自动从 `.example` 播种
2. persona 内嵌路径（kit 脚本目录 + mcp 入口）
3. `evolution/POLICY.json` roots
4. dsh-home 播种（settings.yaml / auto-approve / 预设）
5. 三个 profile（web/headless/tui）的 pnpm 依赖安装 + node-pty 编译 + 回归冒烟

## 第 6 步 · 凭证（唯一的手工步骤）

```bash
# setup.sh 已生成模板并 chmod 600,编辑填入 Kimi K3 key:
nano ~/AI-MD/mdsim-data/dsh-home/.credentials.yaml
# 填入: KIMI_API_KEY: sk-...
```

## 第 7 步 · 回归验证（装没装对，跑这个）

```bash
cd ~/AI-MD/dsh-agent
MDSIM_KIT_BASE=$HOME/AI-MD/agent_deploy_kit python3.11 -m unittest discover -s tests   # 151 全绿
node --test tests/test_flow_gate.mjs tests/test_wechat_push.mjs                         # 24 全绿
cd ~/AI-MD/agent_deploy_kit && python3.11 -m unittest discover -s tests                # 84 全绿
```

也可以直接让 Claude Code 跑：`claude` 进仓库目录，说"跑一遍回归"。

## 第 8 步 · 起服务

```bash
cd ~/AI-MD/dsh-agent
./run-dsh.sh --profile tui        # 终端交互(推荐)
./run-dsh.sh web --port 3091      # 或 web UI
# 看板:
python3.11 dashboard/web_monitor.py 8080 &
# 工作台: http://127.0.0.1:8080/workbench
```

## 第 9 步 · cron（演化层定时驱动）

```bash
crontab -e
# 加一行(路径按实际改):
*/37 * * * * cd /home/<你>/AI-MD/dsh-agent/evolution && python3 observe.py >> observe.log 2>&1
```

---

## 常见坑

| 症状 | 原因 | 解法 |
|---|---|---|
| dsh 起不来，报 `Promise.withResolvers` | node < 22 | 第 2 步，确认 `node --version` |
| dsh 拒启动，提 credentials 权限 | `.credentials.yaml` 非 600 | `chmod 600`，setup.sh 每次自动复核 |
| `lmp_mpi` 跑 ReaxFF 报 unknown pair style | LAMMPS 没编 REAXFF | 第 3 步 `lmp_mpi -h \| grep -i reaxff` 验证 |
| 看板作业不显示 | md_config.json 路径是旧机的 | 重跑第 5 步 setup.sh |
| observe.py 没产出 | cron 没装/路径错 | 第 9 步；手动 `cd evolution && python3 observe.py` 验证 |
| 弹性云/无 sudo 机器 | — | LAMMPS 可全装 `$HOME`（第 3 步已是）;cron 不可用就 systemd user timer 或手动跑 |

## 不在本指南范围内

- **HPC 提交永远人工确认**（红线）:hpc_submit 只渲染不提交，新机器上一样。
- 旧机 EL8（py3.6.8 orchestrator）特有的兼容约束只约束旧机，Ubuntu 上不用管。
- 微信推送（md_push_driver）按需另配，不装不影响主功能。
