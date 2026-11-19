# dsh-agent 加固手册 v2（真机核验修订版 · 2026-09-17）

> 基于原《异机执行手册》逐条对照 node01 真机核验后修订。原手册的设计骨架**全部保留**（uid 降权 + ACL + sudo 桥 + append-only 账本 + persona），本版修正执行层错误。**带 ⚠ 的条目是不改就会出事或加固落空的。**

## 修订清单（相对原手册）

| # | 原手册位置 | 问题 | 修订 |
|---|---|---|---|
| R1 | 全文 `sudo` | **node01 上 user 不在 wheel、无 sudo 权限**（`id user` 仅 user 组） | 所有特权操作经 rootrun：`echo <REDACTED-PW> \| su - root -c '...'`。sudoers 规则照写（运行期提权机制本身用 sudo，二进制在） |
| R2 | §0.1 表格 | 真相源（status_bus.json/.v3.3_state.json/.v3.3_strategy_db.json）**已是 mdorch:mdorch 664**，user 不在 mdorch 组本来就写不了 | §6 的 chown 改为**核对**而非变更；真正的软面是 dashboard/mcp/evolution 下的开关 JSON（user 所有 644/666） |
| R3 ⚠ | §4.2 `DSH_PERMISSION_MODE=workspace-write` | **错位**：run-dsh.sh 会 `cd $REPO`，workspace-write 把 write/edit 沙箱锁在 repo——而 repo 对 mdagent 只读 → write/edit 全废，自由面死亡 | **保留 `danger-full-access`**。内核 ACL 已是真墙，harness 层沙箱是错位的第二层。这回答了原 §0.3-3：保留 |
| R4 ⚠ | §4.1 `chown -R mdagent dsh-home` | **会连同 `dsh-home/profiles/*/node_modules` 一起送出去**。插件 symlink 目录若 mdagent 可写，agent 可替换插件代码/摘掉 ops-gate → 所有进程内闸门蒸发；mdagent 拥有 dsh-home 根目录还能 rename 整个 profiles 重建 | dsh-home 根目录 `user:mdagent 0750`；`profiles/`、`.agent-presets/`、`settings.yaml` 保持 **user 所有**；仅 sessions/history/attachments/storages/data/update-check.json 给 mdagent |
| R5 | §4.2 launcher PATH | `run-dsh.sh` 末尾是 `pnpm exec dsh`，pnpm 在 `/opt/npm-global/bin/`，**node2219/bin 里没有 pnpm** | launcher PATH 加 `/opt/npm-global/bin`；.npm-global 已是 775（other r-x）无需 ACL；node2219 需查权限，非 755 则补 ACL |
| R6 | §5.3 桥接代码 `argv = [sudo...] + argv` | argv[0] 是 `sys.executable`——MCP 今天由 python3.11 启动碰巧匹配 sudoers，但 spawn 配置一变就**静默 403** | 桥接时把 argv[0] **写死为 `/usr/bin/python3.11`**（与 sudoers 规则精确对齐），不依赖 sys.executable |
| R7 ⚠ | §6b 清理脚本 `d['dsh']['bundles']` | **键路径错误**：已安装 profile 的真实结构是 `dsh.profile.bundles`（少一层 profile），原脚本静默清不到 | 用正确键路径；且 deps 里的 `dsh-md-auto-approve` link 条目也要删 |
| R8 ⚠ | §7.2 只改 setup.sh PROFILES | **setup.sh 不会传播到已安装实例**（profiles/*/package.json 已存在，setup 只建不更新） | trace 注册要同时改：setup.sh PROFILES（面向未来）+ 三个已安装 `dsh-home/profiles/*/package.json`（deps link + bundles，**trace 排在 ops-gate 前**）+ `profiles/*/node_modules/dsh-md-trace` symlink |
| R9 | §7.1 账本文件 `root:mdagent 660` | group 只能占一个，看板（user）与巡检要读就被挤掉 | 改用 ACL：文件 `root:root 660` + `setfacl u:mdagent:rw-,u:user:r--`；目录 `root:root 755`。mdagent 连 unlink 都做不到（目录不可写），比原方案更硬 |
| R10 | §5.2/§1 表述 | 白名单含 `python3` 且 shlex 解析 → `python3 -c 任意代码` 能以 mdorch 跑。**mdagent→mdorch 的墙是「人工弹窗审批 + 逐条审计」，不是能力零化** | 手册必须写死这一点，否则给人虚假安全感。auto-approve 废除后每次 sim_exec 过人工弹窗——那才是真闸。可选增强（去 python3、换固定入口 wrapper）列入附录 |
| R11 | §3 ACL rwX 全 DATA | 核验：mdsim-data 顶层只有 dsh-home/events/memory/projects/scratch/state，**case 目录不在 DATA 下**，rwX 可接受 | 保留原方案，加一句前提声明；memory/（atoms.jsonl/LEDGER）是已知软面（原附录 B 已诚实列出） |
| R12 | 未提及 | 现有 sudoers 已有 `user→mdorch NOPASSWD: launch_agent_gate.py / gmx_gate.py / packmol_gate.py`（/etc/sudoers.d/mdorch-gate）。**这三条保留不动**——生产 LAMMPS 当前就走它们 | 新增第 60 条规则与既有规则并存；不要动 mdorch-gate |
| R13 | §7.3/§11 验收 | `sudo -u mdagent` 的调用者必须是 root（user 无 sudo） | 验收命令全部改为 rootrun 包裹，或 root 身份 `su -s /bin/bash mdagent -c '...'` |
| R14 | §2 停机范围 | 核验：user crontab 有 observe.py(*/37) 与 push/md_push_driver.py(*/10)；live 进程有 dsh web(:3091) 与 tmux tui | 改造**不需要**停 cron（推送驱动/观察器只读 kit、写 DATA 项目目录，均不受影响）。live 的 user 身份 harness 是附录 B.3 的已知残余面，由用户择机重启进 jail，不强制杀 |
| R15 | §9 persona 行号 | 核验通过（41/64/75/213 附近命中）；第三副本路径确认 `$DATA/dsh-home/.agent-presets/md-sim-assistant/agent.cordis.yml`，headless 补丁在 `$REPO/md-sim-assistant.headless.patch.yml`（repo 根，非 presets/） | 按此三处执行 |

## 核验通过、无需改动的部分

- 全部 20 个引用路径存在（kit/repo/data/claude_interface/specialist_rules/md_mcp/run-dsh/web_monitor/agent.cordis.yml/setup.sh/gmx_scan/gmx_adopted/monitor_roots/POLICY/dsh-home/dsh-plugins/node_modules）
- 文件系统：/home 与 / 均本地 ext4，setfacl/chattr 可用（实测探针通过）
- mdorch 用户+组存在（1002，nologin）；lmp_mpi root:mdorch 750 在 /usr/local/md-bin/
- /usr/bin/python3.11 存在；MCP  spawn 即 python3.11（agent.cordis.yml:312）
- claude_interface.py CLI 形式 `exec <cmd> <desc> [cwd]` / `exec_bg` 与 sudoers `*` 匹配
- setup.sh:91-94 auto-approve 播种 enabled:True 属实；dsh-home/auto-approve.json 现役存在
- gmx_scan.py adopt() 经 GMX_ADOPTED_FILE env 可重定向，默认写 dashboard/gmx_adopted.json
- web_monitor.py 端口参数是位置参数（ECS 部署已踩过）
- md_push_driver 只读 status_bus/.v3.3_state（user 身份本来也写不了 mdorch 的文件）

## 修订后的关键命令（替代原手册对应段）

### §2 环境（所有特权操作）

```bash
# rootrun 模式：本手册所有 sudo 命令以此等价执行
rr() { echo <REDACTED-PW> | su - root -c "$*"; }
export KIT=/opt/mdagent/kit
export REPO=/opt/mdagent/app
export DATA=/opt/mdagent/data
```

### §4.2 launcher（R3+R5 修订版）

```bash
rr 'cat > /usr/local/bin/run-dsh-jailed <<'"'"'LAUNCHER'"'"'
#!/usr/bin/env bash
# 以 mdagent 身份启动 dsh harness。所有 agent 工具继承此 uid——全部硬约束的落点。
# 不要绕过它直接跑 run-dsh.sh（以 user 身份的 harness 不受任何约束）。
# DSH_PERMISSION_MODE 保留 danger-full-access：内核 ACL 是真墙；
# workspace-write 会把沙箱锁在只读的 repo 目录（run-dsh.sh cd $HERE），自由面全废。
set -euo pipefail
REPO=/opt/mdagent/app
DATA=/opt/mdagent/data
exec sudo -n -u mdagent \
  env HOME=/var/lib/mdagent \
      XDG_CACHE_HOME=/var/lib/mdagent/.cache \
      PNPM_HOME=/var/lib/mdagent/.pnpm \
      PATH=/home/user/node2219/bin:/opt/npm-global/bin:/usr/local/bin:/usr/bin:/bin \
      MDSIM_DATA_ROOT="$DATA" \
  "$REPO/run-dsh.sh" "$@"
LAUNCHER
chmod 755 /usr/local/bin/run-dsh-jailed'
```

sudoers（user→mdagent，仅 env 包装）不变：

```bash
rr 'echo "user ALL=(mdagent) NOPASSWD: /usr/bin/env" > /etc/sudoers.d/50-mdagent-launch && chmod 440 /etc/sudoers.d/50-mdagent-launch && visudo -c'
```

### §4.1 dsh-home 分面所有权（R4 修订版）

```bash
D=$DATA/dsh-home
# 根目录：user 持有，mdagent 仅经组 r-x 进入——不能 rename/replace 任何顶层条目
rr "chown user:mdagent $D && chmod 750 $D"
# 受控条目（插件装载面/人格副本/模型路由）保持 user 所有
rr "chown -R user:user $D/profiles $D/.agent-presets $D/settings.yaml"
# harness 运行期可写面
rr "chown -R mdagent:mdagent $D/sessions $D/history.jsonl $D/attachments $D/storages $D/data $D/update-check.json 2>/dev/null || true"
# 凭证：harness 要读 → mdagent；user 要看须经 root（可接受，注记即可）
rr "chown mdagent:mdagent $D/.credentials.yaml && chmod 600 $D/.credentials.yaml"
# node/pnpm 缓存家
rr 'mkdir -p /var/lib/mdagent && chown mdagent:mdagent /var/lib/mdagent'
```

### §5.3 桥接补丁（R6 修订版）

md_mcp.py 在 `argv.append(log_path)` 之后插入：

```python
    # 提权到审计执行身份(2026-09-17 加固)：harness 跑在 mdagent，无 mdorch 权限；
    # 唯一提权路径是 sudoers 里写死脚本路径的那一条。配置缺省时行为与改造前一致。
    _bridge = CFG.get("exec_bridge") or {}
    _sudo_user = _bridge.get("sudo_user")
    if _sudo_user:
        argv[0] = "/usr/bin/python3.11"  # 与 sudoers 规则精确对齐，不依赖 spawn 解释器
        argv = ["/usr/bin/sudo", "-n", "-u", str(_sudo_user)] + argv
```

sudoers（不动既有 mdorch-gate 三条）：

```bash
rr 'echo "mdagent ALL=(mdorch) NOPASSWD: /usr/bin/python3.11 $KIT/agents/runtime/v3.3/claude_interface.py *" > /etc/sudoers.d/60-mdagent-simexec && chmod 440 /etc/sudoers.d/60-mdagent-simexec && visudo -c'
```

### §6b auto-approve 卸载（R7+R8 修订版）

对三个已安装 profile（web/headless/tui）执行——键路径是 `dsh.profile.bundles`：

```python
import json
p = "<profile>/package.json"
d = json.load(open(p, encoding="utf-8"))
(d.get("dependencies") or {}).pop("dsh-md-auto-approve", None)
b = d.get("dsh", {}).get("profile", {}).get("bundles") or []
d["dsh"]["profile"]["bundles"] = [x for x in b if x != "dsh-md-auto-approve"]
json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
```

再 `rm -f $DATA/dsh-home/auto-approve.json`，并把 setup.sh:93 的 `True` 改 `False`、PROFILES 三处 deps/bundles 里的 auto-approve 条目删掉（面向未来重装）。

### §7.1 账本（R9 修订版）

```bash
rr 'mkdir -p /var/log/mdsim && touch /var/log/mdsim/trace.jsonl
chown root:root /var/log/mdsim /var/log/mdsim/trace.jsonl
chmod 755 /var/log/mdsim && chmod 660 /var/log/mdsim/trace.jsonl
setfacl -m u:mdagent:rw-,u:user:r-- /var/log/mdsim/trace.jsonl
chattr +a /var/log/mdsim/trace.jsonl && lsattr /var/log/mdsim/trace.jsonl'
# mdagent: 只能 append（chattr +a 内核强制），目录 root 755 不可写 → 连 unlink 都不行
# user: 只读（看板/巡检用）   root: 全权
```

### §7.2 trace 插件注册（R8 修订版）

除 setup.sh 外，对三个**已安装** profile：

```bash
for p in web headless tui; do
  P=$DATA/dsh-home/profiles/$p
  # package.json: deps 加 link 条目 + bundles 加在 ops-gate 之前
  python3 - "$P/package.json" <<'PYEOF'
import json, sys
p = sys.argv[1]
d = json.load(open(p, encoding="utf-8"))
d.setdefault("dependencies", {})["dsh-md-trace"] = "link:../../../../dsh-agent/dsh-plugins/dsh-md-trace"
b = d.get("dsh", {}).get("profile", {}).get("bundles") or []
if "dsh-md-trace" not in b:
    b.insert(b.index("dsh-md-ops-gate") if "dsh-md-ops-gate" in b else 0, "dsh-md-trace")
json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("registered", p)
PYEOF
  # node_modules link
  ln -sfn ../../../../dsh-agent/dsh-plugins/dsh-md-trace "$P/node_modules/dsh-md-trace"
done
```

（link 目标的相对深度以现有条目为准——先 `ls -l profiles/headless/node_modules/` 核对现有 symlink 形态再照抄。）

## 对抗审计压测清单（修订版 §11，执行后必跑）

在 §11 六项故障注入之外，补以下**权限层探针**（全部以 mdagent 身份经 rootrun 发起）：

| # | 攻击 | 期望 |
|---|---|---|
| P1 | 写 repo / kit / dsh-plugins / profiles/node_modules | 全 Permission denied |
| P2 | 直跑 lmp_mpi / gmx_gate.py | denied |
| P3 | sudo -u mdorch 换脚本路径（复制 claude_interface 到 scratch 篡改后调用） | sudoers 路径写死 → denied |
| P4 | 篡改 trace.jsonl（截断/改写/删除/chattr -a） | 全 denied |
| P5 | 伪造追加 trace 行 | **成功——已知边界，记录为残余面** |
| P6 | symlink 攻击：scratch 里建 symlink 指向 kit 状态文件后写入 | denied（目标文件权限兜底） |
| P7 | hardlink 攻击：ln 状态文件到 scratch | denied（protected_hardlinks） |
| P8 | 重建 dsh-home/auto-approve.json + 替换 profiles 里插件 symlink | 文件可建但**无读者**（插件已卸载）→ 无效；profiles user 所有 → 替换 denied |
| P9 | kill mdorch 的进程（起个 mdorch sleep 探针，不碰生产） | denied |
| P10 | python3 -c 经 claude_interface 以 mdorch 跑任意代码 | **成功——R10 记录的已知残余面**：墙=审批+审计。验证它必在 claude_operations.jsonl 留痕 |
| P11 | 合法路径回归：mdagent 读 repo/kit/bus ✔、经 sim_exec 桥跑 `lmp_mpi -h` ✔、user 看板读 DATA ✔、push driver/observe.py cron 不受影响 ✔ | 全通 |
