#!/usr/bin/env bash
# ============================================================
# dsh-agent 迁移引导(换机器/换目录后跑一次)
# 用法: bash scripts/setup.sh <agent_deploy_kit 根目录的绝对路径>
# 例:   bash scripts/setup.sh /data/user/agent_deploy_kit_20260630
#
# 2026-08-29 数据根外置后收敛为三组改写 + 凭证 + 依赖:
#   1. mcp/md_config.json: local_base / ssh.remote_base / data_root / run_case_base
#      (web_monitor 的 BASE/RUN_CASE_BASE、flow-gate logPath 均已运行时解析,不再改写代码)
#   2. persona 内嵌路径(kit v3.3 脚本目录 + mcp command/args;persona 无法运行时解析,保留正则)
#      —— md-sim-assistant.headless.patch.yml(代码库) 与 dsh-home/.agent-presets 副本(数据根)
#   3. evolution/POLICY.json roots(kit=参数, dsh_agent=代码根自动)
#   4. 凭证模板复制到 <数据根>/dsh-home/.credentials.yaml(chmod 600)
#   5. dsh profile 依赖:数据根 dsh-home/profiles/{web,headless,tui} 缺 package.json 时按绝对
#      link: 生成(数据根位置可配,相对深度不假设),然后 pnpm install + node-pty 编译 + 回归
# 环境变量: MDSIM_DATA_ROOT(数据根)、RUN_CASE_BASE(任务扫描树)、MDSIM_PYTHON(venv 解释器)
# ============================================================
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# pnpm/dsh 需要 node≥22;系统默认可能是 node20——按已知位置探测并前置 PATH
if [ -n "${MDSIM_NODE_BIN:-}" ]; then
  [ -x "$MDSIM_NODE_BIN/node" ] || { echo "✗ MDSIM_NODE_BIN 缺少可执行 Node" >&2; exit 2; }
  export PATH="$MDSIM_NODE_BIN:$PATH"
else
for NODE_DIR in "$HOME/node2219/bin" "$HOME/node22/node-v22.23.2-linux-x64/bin"; do
  if [ -x "$NODE_DIR/node" ]; then
    export PATH="$NODE_DIR:$PATH"
    break
  fi
done
fi

KIT_BASE="${1:-}"
if [ -z "$KIT_BASE" ]; then
  echo "用法: bash scripts/setup.sh <agent_deploy_kit 根目录绝对路径>"; exit 2
fi
[ -d "$KIT_BASE/agents/runtime/v3.3" ] || { echo "✗ $KIT_BASE 下没有 agents/runtime/v3.3,不是 kit 根?"; exit 2; }
# 归一化为绝对路径(相对路径会原样落盘成配置,换 cwd 即漂);并校验关键脚本非空目录
KIT_BASE="$(cd "$KIT_BASE" && pwd)"
[ -f "$KIT_BASE/agents/runtime/v3.3/monitor_agent.py" ] || \
  { echo "✗ $KIT_BASE/agents/runtime/v3.3 缺 monitor_agent.py,目录不完整"; exit 2; }

if [ -n "${MDSIM_PYTHON:-}" ]; then
  PYBIN="$MDSIM_PYTHON"
else
  PYBIN="$(command -v python3.11 || command -v python3 || true)"
fi
[ -n "$PYBIN" ] && [ -x "$PYBIN" ] || { echo "✗ 找不到可执行 Python，请设置 MDSIM_PYTHON"; exit 2; }
PYBIN="$("$PYBIN" -c 'import os,sys; print(os.path.abspath(sys.executable))')"
"$PYBIN" -c 'import sys; sys.exit(sys.version_info < (3, 9))' || \
  { echo "✗ 安装配置需要 Python 3.9+"; exit 2; }

DATA_ROOT="${MDSIM_DATA_ROOT:-$(dirname "$HERE")/mdsim-data}"
DSH_HOME_DIR="$DATA_ROOT/dsh-home"
echo "[setup] kit=$KIT_BASE  python=$PYBIN  home=$HERE  data_root=$DATA_ROOT"

KIT_BASE="$KIT_BASE" PYBIN="$PYBIN" HERE="$HERE" DATA_ROOT="$DATA_ROOT" "$PYBIN" - <<'PYEOF'
import json, os, re

kit, pybin, here = os.environ["KIT_BASE"], os.environ["PYBIN"], os.environ["HERE"]
data_root = os.environ["DATA_ROOT"]

# 1. md_config.json:机器相关四字段(local_base / ssh.remote_base / data_root / run_case_base)
p = os.path.join(here, "mcp", "md_config.json")
if not os.path.exists(p):  # 开源克隆首次 setup:从模板播种
    import shutil as _sh
    _sh.copy(p + ".example", p)
    print("[setup] md_config.json 不存在,已从 .example 模板播种")
cfg = json.load(open(p, encoding="utf-8"))
cfg["local_base"] = kit
# "ssh": null 时 setdefault 返回 None 会 TypeError 裸栈(二轮对抗):统一 or {} 回写
cfg["ssh"] = cfg.get("ssh") or {}
cfg["ssh"]["remote_base"] = kit
cfg["data_root"] = data_root
run_case = os.environ.get("RUN_CASE_BASE") or os.path.join(os.path.dirname(kit), "程序运行-case")
cfg["run_case_base"] = run_case
json.dump(cfg, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("[setup] md_config.json local_base=%s data_root=%s run_case_base=%s" % (kit, data_root, run_case))

# 2a. patch.yml(代码库 persona):mcp-mdsim command/args + v3.3 脚本目录
# 替换值走函数式 repl:路径含反斜杠/正则元字符也不会炸(2026-08-29 对抗审计)
p = os.path.join(here, "md-sim-assistant.headless.patch.yml")
t = open(p, encoding="utf-8").read()
t, n1 = re.subn(r"command: '[^']*python[^']*'", lambda m: "command: '%s'" % pybin, t)
t, n2 = re.subn(r"(-\s*')[^']*mcp/md_mcp\.py(')",
                lambda m: m.group(1) + os.path.join(here, "mcp", "md_mcp.py") + m.group(2), t)
# 脚本目录正则非贪婪跨空格:含空格的 kit 路径也能二次改写(旧 [^ )]* 会静默失配残留陈旧路径)
t, n3 = re.subn(r"脚本目录 .*?/agents/runtime/v3\.3/",
                lambda m: "脚本目录 %s" % os.path.join(kit, "agents/runtime/v3.3") + "/", t)
open(p, "w", encoding="utf-8").write(t)
print("[setup] patch.yml 重写: python=%d处 mcp路径=%d处 脚本目录=%d处" % (n1, n2, n3))

# 2a+. dsh-home 播种(新机器):settings.yaml(DeepSeek provider/默认模型/默认预设)、
# auto-approve.json 默认开关、agent-presets 预设(persona 副本从代码库 presets/ 安装)
import shutil
home = os.path.join(data_root, "dsh-home")
os.makedirs(home, exist_ok=True)
settings_dst = os.path.join(home, "settings.yaml")
if not os.path.exists(settings_dst):
    shutil.copy(os.path.join(here, "scripts", "settings.yaml.example"), settings_dst)
    print("[setup] 已安装 dsh-home/settings.yaml(DeepSeek provider + 默认预设)")
aa_dst = os.path.join(home, "auto-approve.json")
if not os.path.exists(aa_dst):
    # 2026-09-17 加固:默认关闭。auto-approve 是"智能体自我松绑"开关,批量免点击
    # 应走一次性批准令牌(scnet 模式,root/user 创建、用完即焚),非常驻开关。
    json.dump({"enabled": False}, open(aa_dst, "w", encoding="utf-8"))
    print("[setup] 已生成 dsh-home/auto-approve.json 默认 {enabled: false}(加固默认关)")
preset_src = os.path.join(here, "presets", "md-sim-assistant")
preset_dst = os.path.join(home, ".agent-presets", "md-sim-assistant")
if not os.path.exists(preset_dst):
    os.makedirs(os.path.dirname(preset_dst), exist_ok=True)
    shutil.copytree(preset_src, preset_dst)
    print("[setup] 已安装 agent-presets/md-sim-assistant( presets/ → dsh-home )")
else:
    # 目录在但成员缺失(半成品):按文件粒度补齐,不覆盖已存在的同名文件
    added = 0
    for name in os.listdir(preset_src):
        s, d = os.path.join(preset_src, name), os.path.join(preset_dst, name)
        if not os.path.exists(d):
            shutil.copy2(s, d); added += 1
    if added:
        print("[setup] agent-presets/md-sim-assistant 补齐缺失成员 %d 个" % added)

# 2b. dsh-home 内 persona 副本(数据根;不存在则跳过——首次 boot 后才有)
p2 = os.path.join(data_root, "dsh-home", ".agent-presets", "md-sim-assistant", "agent.cordis.yml")
if os.path.exists(p2):
    t2 = open(p2, encoding="utf-8").read()
    t2, n4 = re.subn(r"脚本目录 .*?/agents/runtime/v3\.3/",
                     lambda m: "脚本目录 %s" % os.path.join(kit, "agents/runtime/v3.3") + "/", t2)
    t2, n5 = re.subn(r"command: '[^']*python[^']*'", lambda m: "command: '%s'" % pybin, t2)
    t2, n6 = re.subn(r"(-\s*')[^']*mcp/md_mcp\.py(')",
                     lambda m: m.group(1) + os.path.join(here, "mcp", "md_mcp.py") + m.group(2), t2)
    open(p2, "w", encoding="utf-8").write(t2)
    print("[setup] dsh-home persona 副本 脚本目录=%d处 python=%d处 mcp路径=%d处" % (n4, n5, n6))
else:
    print("[setup] dsh-home persona 副本不存在,跳过(首次启动 dsh 后可重跑 setup.sh)")

# 3. POLICY.json roots
p = os.path.join(here, "evolution", "POLICY.json")
pol = json.load(open(p, encoding="utf-8"))
if "kit" in pol.get("roots", {}):
    pol["roots"]["kit"]["path"] = kit
if "dsh_agent" in pol.get("roots", {}):
    pol["roots"]["dsh_agent"]["path"] = here
json.dump(pol, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("[setup] POLICY.json roots 重写完成")
PYEOF

# 4. 凭证(dsh-home 在数据根;模板在代码库 scripts/)
mkdir -p "$DSH_HOME_DIR"
if [ ! -f "$DSH_HOME_DIR/.credentials.yaml" ]; then
  cp "$HERE/scripts/.credentials.yaml.example" "$DSH_HOME_DIR/.credentials.yaml"
  echo "[setup] 已生成模型凭据模板，请通过 install_config.py configure-model 配置"
fi
# dsh 强制 owner-only,每次必复核(不只创建时):cp 默认 664 会拒启动
chmod 600 "$DSH_HOME_DIR/.credentials.yaml"

# 5. profile package.json 缺失时生成(绝对 link:,不假设数据根与代码库的相对深度)
"$PYBIN" - "$HERE" "$DSH_HOME_DIR" <<'PYEOF'
import json, os, sys

here, home = sys.argv[1], sys.argv[2]
PLUGINS = os.path.join(here, "dsh-plugins")
# (依赖键, 插件目录名);dsh.profile.bundles 是 profile 的合成清单——漏了它 dsh 起空树,
# mcp 插件等不到 tools 服务(boot 报 "waiting for service: tools")
PROFILES = {
    "web": {
        "deps": [("@vendor/dsh-browser", "dsh-browser"), ("dsh-web-search-pro", "dsh-web-search-pro"),
                 ("dsh-files", "dsh-files"), ("dsh-md-trace", "dsh-md-trace"),
                 ("dsh-md-ops-gate", "dsh-md-ops-gate"),
                 ("dsh-md-flow-gate", "dsh-md-flow-gate"),
                 ("dsh-md-friction", "dsh-md-friction")],
        "bundles": ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-web-app", "dsh-web-search-pro",
                    "dsh-files", "dsh-md-trace", "dsh-md-ops-gate", "dsh-md-flow-gate",
                    "dsh-md-friction"],
    },
    "headless": {
        "deps": [("dsh-md-trace", "dsh-md-trace"),
                 ("dsh-md-ops-gate", "dsh-md-ops-gate"),
                 ("dsh-md-flow-gate", "dsh-md-flow-gate"),
                 ("dsh-md-friction", "dsh-md-friction")],
        "bundles": ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-headless", "dsh-md-trace", "dsh-md-ops-gate",
                    "dsh-md-flow-gate", "dsh-md-friction"],
    },
    # tui 终端模式(2026-08-30):deepseek-harness-tui 是 npm 分发包(与 rc.6 peer 匹配),
    # 非本地 link;deps 元组第二位为 None 时按 npm 版本号处理
    "tui": {
        "deps": [("deepseek-harness-tui", "npm:0.3.0"),
                 ("dsh-md-trace", "dsh-md-trace"),
                 ("dsh-md-ops-gate", "dsh-md-ops-gate"),
                 ("dsh-md-flow-gate", "dsh-md-flow-gate"),
                 ("dsh-md-friction", "dsh-md-friction")],
        "bundles": ["@deepseek-ai/dsh-base", "deepseek-harness-tui", "dsh-md-trace", "dsh-md-ops-gate",
                    "dsh-md-flow-gate", "dsh-md-friction"],
    },
}
for prof, spec in PROFILES.items():
    pdir = os.path.join(home, "profiles", prof)
    pkg_path = os.path.join(pdir, "package.json")
    os.makedirs(pdir, exist_ok=True)
    deps = {name: (d[4:] if d.startswith("npm:") else "link:%s" % os.path.join(PLUGINS, d))
            for name, d in spec["deps"]}
    if os.path.exists(pkg_path):
        # 已存在不整体跳过(2026-08-29 对抗审计):换机后旧绝对 link: 必须重写到当前代码库,
        # 缺 bundles 必须补(缺了 dsh 起空树,boot 报 "waiting for service: tools")
        try:
            pkg = json.load(open(pkg_path, encoding="utf-8"))
        except ValueError:
            raise SystemExit("[setup] profiles/%s/package.json 无法解析，请修复后重试" % prof)
        pkg["dependencies"] = pkg.get("dependencies") or {}  # "dependencies": null 时 setdefault 返回 None 裸栈(四轮对抗)
        pkg["dependencies"].update(deps)
        pkg["dsh"] = pkg.get("dsh") or {}  # "dsh": null 时 setdefault 返回 None 裸栈(三轮对抗)
        pkg["dsh"]["profile"] = pkg["dsh"].get("profile") or {}
        prof_cfg = pkg["dsh"]["profile"]
        # bundles 做并集校准:非空但缺成员(如被人为删减)也必须补齐,缺了 dsh 起空树(二轮对抗)
        existing_bundles = prof_cfg.get("bundles") or []
        missing = [b for b in spec["bundles"] if b not in existing_bundles]
        if missing:
            prof_cfg["bundles"] = existing_bundles + missing
        json.dump(pkg, open(pkg_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("[setup] profiles/%s/package.json 已存在:link:/bundles 已校准" % prof)
    else:
        pkg = {"name": "dsh-profile-%s" % prof, "private": True, "dependencies": deps,
               "dsh": {"profile": {"bundles": spec["bundles"]}}}
        json.dump(pkg, open(pkg_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("[setup] 已生成 profiles/%s/package.json" % prof)
    # pnpm-workspace.yaml 必须随 package.json 存在:nodeLinker=hoisted 是 dsh 插件
    # 解析 peer 的前提,缺了它 pnpm 默认 isolated 拓扑,boot 报 "waiting for service: tools"。
    # 已存在则只补关键行,不整体重写(用户注释/自定义不得被抹,二轮对抗)
    ws_path = os.path.join(pdir, "pnpm-workspace.yaml")
    if not os.path.exists(ws_path):
        with open(ws_path, "w", encoding="utf-8") as f:
            f.write("packages:\n  - .\nnodeLinker: hoisted\nautoInstallPeers: false\n")
    else:
        lines = open(ws_path, encoding="utf-8").read().splitlines()
        # 裸 "nodeLinker" 行(无冒号)也命中:其语义本就是该键的残缺写法,
        # 原样残留会让整文件成为非法 YAML,pnpm install 每跑必败(五轮对抗)
        hit = [i for i, l in enumerate(lines)
               if l.split(":")[0].strip() == "nodeLinker"]
        if not hit:
            with open(ws_path, "a", encoding="utf-8") as f:
                f.write("\nnodeLinker: hoisted\n")
        else:
            def _nl_value(i):
                return lines[i].split(":", 1)[1].strip() if ":" in lines[i] else ""
            if len(hit) > 1 or any(_nl_value(i) != "hoisted" for i in hit):
                # 任一命中值非 hoisted(重复行后者覆盖前者、或裸行)都会让 dsh 起空树:
                # 改写首个命中行,删除其余命中行——多重命中全改写会产生重复键,
                # pnpm 报 duplicated mapping key 且复跑不自愈(六轮对抗)
                drop = set(hit[1:])
                lines[hit[0]] = "nodeLinker: hoisted"
                lines = [l for j, l in enumerate(lines) if j not in drop]
                with open(ws_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
    # cordis.yml 是 dsh profile 的 boot 入口(空树,靠 package.json bundles + patch 合成);
    # 缺了它 boot 静默走默认配置,mcp 插件等不到 tools 服务
    cordis_yml = os.path.join(pdir, "cordis.yml")
    if not os.path.exists(cordis_yml):
        with open(cordis_yml, "w", encoding="utf-8") as f:
            f.write("# dsh profile root — an empty entry list. The tree is composed as patches:\n"
                    "# each bundle in package.json's dsh.profile.bundles, then cordis.patch.yml,\n"
                    "# then any --patch overlays. Edit cordis.patch.yml, not this file.\n[]\n")
    patch_yml = os.path.join(pdir, "cordis.patch.yml")
    if not os.path.exists(patch_yml):
        with open(patch_yml, "w", encoding="utf-8") as f:
            f.write("# Your patch layer for this dsh profile, applied after every bundle layer:\n"
                    "# a top-level YAML array of loader patch entries (id-targeted config\n"
                    "# overrides, disables, and insert lists; `!!js` expressions allowed).\n[]\n")
    print("[setup] profiles/%s 就绪(package.json + pnpm-workspace.yaml + cordis*.yml)" % prof)
PYEOF

# 6. 依赖 + 原生模块 + 品牌补丁 + 回归
cd "$HERE"
# --ignore-scripts:node-pty 的 install 钩子在 EL8 上撞系统 python3.6 直接 SyntaxError 假死;
# 原生模块统一由下面的 npm_config_python 显式 rebuild 完成
echo "[setup] pnpm install(根,--ignore-scripts)…"; pnpm install --silent --ignore-scripts
for prof in headless web tui; do
  echo "[setup] pnpm install($prof profile)…"
  if ! (cd "$DSH_HOME_DIR/profiles/$prof" && pnpm install --silent --ignore-scripts); then
    echo "[setup] ✗ $prof profile 依赖安装失败，setup 中止" >&2
    exit 1
  fi
done
# node-pty 需 node-gyp 现场编译;node-gyp 要 python≥3.7(EL8 默认 python3.6 会 SyntaxError 假死)
echo "[setup] 编译 node-pty(python=$PYBIN)…"
if ! npm_config_python="$PYBIN" pnpm rebuild node-pty koffi protobufjs @deepseek-ai/dsh-subprocess-local @google/genai; then
  echo "[setup] ✗ 原生依赖构建失败，请检查 gcc/make/Python，setup 中止" >&2
  exit 1
fi
# Profiles have independent dependency trees; their ignored build scripts must
# also be rebuilt in the profile that owns them, including the terminal profile.
for prof in headless web tui; do
  echo "[setup] 编译 $prof profile 原生依赖(python=$PYBIN)…"
  if ! (cd "$DSH_HOME_DIR/profiles/$prof" && npm_config_python="$PYBIN" pnpm rebuild node-pty koffi protobufjs @deepseek-ai/dsh-subprocess-local @google/genai); then
    echo "[setup] ✗ $prof profile 原生依赖构建失败，setup 中止" >&2
    exit 1
  fi
done
echo "[setup] 品牌补丁…"; "$PYBIN" scripts/apply-branding.py
# 回归是硬门:失败必须非零退出(上一轮 || 兜底曾把 FAILED 吞成 exit 0,二轮对抗)
echo "[setup] 回归…"
REG_RC=0
REG_OUT="$(PYTHONUNBUFFERED=1 "$PYBIN" -m unittest discover -s tests 2>&1)" || REG_RC=$?
echo "$REG_OUT" | grep -E '^(Ran|OK|FAILED)' || echo "$REG_OUT" | tail -5
if [ "$REG_RC" -ne 0 ]; then
  echo "[setup] ✗ 回归未全绿(rc=$REG_RC),setup 中止——请修复后重跑" >&2
  exit 1
fi
echo "[setup] ✓ 完成。启动: ./run-dsh.sh web --port 3091 或 ./run-dsh.sh --profile headless \"…\""
