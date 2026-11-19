#!/usr/bin/env bash
# ============================================================
# 本工作区专属 dsh 启动器(Linux/macOS) —— 锁定独立 DSH_HOME
# 通用:这套 agent 在 Windows(run-dsh.ps1)和 Linux(本脚本)行为一致。
# 用法:
#   ./run-dsh.sh web --host 127.0.0.1 --port 3091        # 起 web UI(独立实例)
#   ./run-dsh.sh --profile headless "现在模拟跑到哪了"     # 无头单任务
#   ./run-dsh.sh --profile tui                             # 终端交互模式(2026-08-30 新增)
# 环境:
#   DSH_HOME            = <数据根>/dsh-home  (2026-08-29 阶段5 外置;与其他 dsh 实例彻底隔离)
#   DSH_PERMISSION_MODE 默认 danger-full-access(2026-08-29 用户拍板:全自主决策,不设工作区边界;
#     想回到审批模式可 DSH_PERMISSION_MODE=workspace-write ./run-dsh.sh ...)
# ============================================================
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 数据根外置(2026-08-29):所有机器写的文件(事件流/状态/记忆/临时件)都在数据根,
# JS 侧插件(flow-gate)依赖本 env 锚定 logPath,必须在 dsh 进程树内恒存在。
export MDSIM_DATA_ROOT="${MDSIM_DATA_ROOT:-$HERE/../mdsim-data}"
# DSH_HOME 外置(2026-08-29 阶段5):sessions/凭证属最敏感数据,随数据根走,代码目录纯净化
export DSH_HOME="${DSH_HOME:-$MDSIM_DATA_ROOT/dsh-home}"
export DSH_PERMISSION_MODE="${DSH_PERMISSION_MODE:-danger-full-access}"
cd "$HERE"

# dsh 需要 node≥22(Promise.withResolvers / createZstdDecompress);系统默认 node20 起不来。
# 按已知安装位置探测,命中即前置 PATH;都没有就交给系统 node 报错。
if [ -n "${MDSIM_NODE_BIN:-}" ]; then
  [ -x "$MDSIM_NODE_BIN/node" ] || { echo "[run-dsh] MDSIM_NODE_BIN 缺少可执行 Node" >&2; exit 2; }
  export PATH="$MDSIM_NODE_BIN:$PATH"
else
for NODE_DIR in "$HOME/node2219/bin" "$HOME/node22/node-v22.23.2-linux-x64/bin"; do
  if [ -x "$NODE_DIR/node" ]; then
    export PATH="$NODE_DIR:$PATH"
    break
  fi
done
fi

# 病灶 D-01:headless profile 不装 agent-presets,persona/工具白名单/mcp-mdsim
# 都靠 --patch 叠入;漏传 = 静默退回 stock harness(2026-08-27 端到端实测)。
# 因此:参数含 --profile headless 且未显式给 --patch 时,自动补上本工作区补丁。
ARGS=("$@")
# 两段独立扫描(与 run-dsh.ps1 语义一致):任一位置出现 --patch/--patch=* 即不自动补;
# 未显式给 --patch 且 --profile headless(两 token 或等号形式)才自动叠(漏判=D-01 复发)
HAS_PATCH=0
NEED_PATCH=0
for ((i=0; i<${#ARGS[@]}; i++)); do
  case "${ARGS[i]}" in
    --patch|--patch=*) HAS_PATCH=1 ;;
    --model|--model=*) HAS_MODEL=1 ;;
    --preset|--preset=*) HAS_PRESET=1 ;;
    --profile) [ "${ARGS[i+1]:-}" = "headless" ] && NEED_PATCH=1
               [ "${ARGS[i+1]:-}" = "tui" ] && IS_TUI=1 ;;
    --profile=headless) NEED_PATCH=1 ;;
    --profile=tui) IS_TUI=1 ;;
  esac
done
if [ "$NEED_PATCH" = "1" ] && [ "$HAS_PATCH" = "0" ]; then
  # --patch 是启动器选项,必须放在任务位置参数之前(放在后面会被当成 app 参数报 unknown option)
  ARGS=(--patch "$HERE/md-sim-assistant.headless.patch.yml" "${ARGS[@]}")
  echo "[run-dsh] headless 自动叠加 --patch md-sim-assistant.headless.patch.yml"
fi
# tui 上游缺口(2026-08-30 实测):--model 缺省时 agent.options.model 不落,
# persona 的 {{model}} 变量在首轮装配抛 "has no value for this assembly"。
# TUI 自己读 agentDefaultModel 服务做路由,但不回填 options——此处按 settings.yaml
# 的 agent-default-model 段补一个显式 --model,用户显式给了就不动。
TUI_EXTRA=()
DEFAULTS_PY="${MDSIM_PYTHON:-}"
if [ "${IS_TUI:-0}" = "1" ] && [ -z "$DEFAULTS_PY" ]; then
  DEFAULTS_PY="$(command -v python3.11 || command -v python3 || true)"
fi
if [ "${IS_TUI:-0}" = "1" ] && [ "${HAS_MODEL:-0}" = "0" ]; then
  if [ -n "$DEFAULTS_PY" ] && DEF_ROUTE="$("$DEFAULTS_PY" "$HERE/scripts/read_dsh_defaults.py" "$DSH_HOME/settings.yaml" model)"; then
    TUI_EXTRA=(--model "$DEF_ROUTE")
    echo "[run-dsh] tui 自动补 --model $DEF_ROUTE(源自 settings.yaml agent-default-model)"
  else
    echo "[run-dsh] ⚠️ tui 未能从 settings.yaml 解析 agent-default-model,persona 含 {{model}} 时会启动失败" >&2
  fi
fi
# tui 上游缺口其二:交互路径的 preset 控制器硬默认 "standard",不读 settings 的
# agent-presets.default(--print 路径反而读);roster 没有 standard 时 boot 即败。
if [ "${IS_TUI:-0}" = "1" ] && [ "${HAS_PRESET:-0}" = "0" ]; then
  if [ -n "$DEFAULTS_PY" ] && DEF_PRESET="$("$DEFAULTS_PY" "$HERE/scripts/read_dsh_defaults.py" "$DSH_HOME/settings.yaml" preset)"; then
    TUI_EXTRA+=(--preset "$DEF_PRESET")
    echo "[run-dsh] tui 自动补 --preset $DEF_PRESET(源自 settings.yaml agent-presets.default)"
  fi
fi
if [ "${#TUI_EXTRA[@]}" -gt 0 ]; then
  # 启动器只认队首的启动器选项,--model/--preset 是 app 参数,必须插在 --profile 之后
  NEWARGS=()
  for ((i=0; i<${#ARGS[@]}; i++)); do
    NEWARGS+=("${ARGS[i]}")
    case "${ARGS[i]}" in
      --profile) NEWARGS+=("${ARGS[i+1]}") ; NEWARGS+=("${TUI_EXTRA[@]}") ; i=$((i+1)) ;;
      --profile=tui) NEWARGS+=("${TUI_EXTRA[@]}") ;;
    esac
  done
  ARGS=("${NEWARGS[@]}")
fi

echo "[run-dsh] DSH_HOME=$DSH_HOME  MODE=$DSH_PERMISSION_MODE  node=$(node -v 2>/dev/null || echo '?')"
pnpm exec dsh "${ARGS[@]}"
