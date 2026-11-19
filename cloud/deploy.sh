#!/usr/bin/env bash
# ============================================================
# mdagent-api 部署脚本(本地执行)
# 铁律(用户要求):每次更新先 rm -rf 服务器旧代码目录再重铺;
#   用户数据(users/accounts/shared-evolution/jobs)在 code 之外,永不被擦。
# 用法: bash cloud/deploy.sh [服务器]   默认 root@<SERVER-IP>
# ============================================================
set -euo pipefail
SRV="${1:-root@<SERVER-IP>}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$HERE")"
VER="$(cat "$REPO/VERSION")"
PKG="/tmp/dsh-agent_${VER}.tar.gz"

echo "== [1/6] 打包 v$VER(排除 node_modules/日志/对话存档)"
cd "$REPO"
tar --exclude=./node_modules --exclude='./对话存档_*' --exclude=./.git \
    --exclude='*.log' --exclude='./push' --exclude='./experiments' \
    -czf "$PKG" .
ls -lh "$PKG"

echo "== [2/6] 取本地 KIMI key 作管理员凭证(rootrun,仅首次播种用)"
ADMIN_KEY="$(echo <REDACTED-PW> | su - root -c "grep '^KIMI_API_KEY:' /opt/mdagent/data/dsh-home/.credentials.yaml" 2>/dev/null | tail -1 | cut -d' ' -f2- | tr -d '[:space:]' || true)"
[ -n "$ADMIN_KEY" ] && echo "   取到(不明示)" || echo "   ⚠️ 未取到,管理员将无 LLM 凭证(可之后在服务器改 accounts.json)"

echo "== [3/6] 上传"
scp -q "$PKG" "$SRV:/tmp/"

echo "== [4/6] 服务器:wipe 旧版 + 重铺 + 依赖"
ssh "$SRV" ADMIN_KEY="$ADMIN_KEY" VER="$VER" bash -s <<'REMOTE'
set -euo pipefail
cd /tmp
systemctl stop mdagent-api 2>/dev/null || true

# ★ 要求1:删旧版代码(users/accounts/shared-evolution/jobs 不动)
rm -rf /opt/mdagent/code
mkdir -p /opt/mdagent/{code,accounts,users,jobs,shared-evolution/proposals,observe-root}
tar --no-same-owner -xzf "/tmp/dsh-agent_${VER}.tar.gz" -C /opt/mdagent/code
rm -f "/tmp/dsh-agent_${VER}.tar.gz"   # 部署包含全源码,不留 /tmp 世界可读
chown -R root:root /opt/mdagent/code
chmod -R go-rwx /opt/mdagent/code/cloud   # 服务代码对租户隐藏(agent 以 mu_ 跑,读不到)
# 非运行时源码同样隐藏(dsh 沙箱不限制读,只能 fs 层收;运行时仅需 node_modules/mcp/dashboard/presets/patch yml)
chmod -R go-rwx /opt/mdagent/code/{docs,scripts,tests,tools,evolution} 2>/dev/null || true
# dsh-plugins:治理插件继续藏;通用插件(web-search-pro/dsh-files)是 headless
# profile 的运行时 bundle,租户进程(mu_)须可读——根目录只放 x(可穿透不可列)
chmod -R go-rwx /opt/mdagent/code/dsh-plugins 2>/dev/null || true
chmod o+x /opt/mdagent/code/dsh-plugins
chmod -R a+rX /opt/mdagent/code/dsh-plugins/dsh-web-search-pro /opt/mdagent/code/dsh-plugins/dsh-files
chmod go-rwx /opt/mdagent/code/*.md /opt/mdagent/code/run-dsh.* 2>/dev/null || true
chmod 700 /opt/mdagent/jobs /opt/mdagent/accounts   # job/账本含对话明文与密钥,仅 root
ln -sfn ../shared-evolution /opt/mdagent/observe-root/memory
echo "$VER" > /opt/mdagent/CLOUD_VERSION

# node22(没有就装到 /opt/node22,走 npmmirror 二进制镜像)
if ! /opt/node22/bin/node -v 2>/dev/null | grep -q '^v2[2-9]'; then
  echo "   安装 node22..."
  curl -fsSL https://registry.npmmirror.com/-/binary/node/v22.23.2/node-v22.23.2-linux-x64.tar.xz -o /tmp/node22.tar.xz
  rm -rf /opt/node22 && mkdir -p /opt/node22
  tar -xJf /tmp/node22.tar.xz -C /opt/node22 --strip-components=1
fi
export PATH=/opt/node22/bin:$PATH
node -v
# pnpm
if ! command -v pnpm >/dev/null; then
  npm install -g pnpm --registry=https://registry.npmmirror.com
fi
# 依赖(EL 系需 --ignore-scripts,与 scripts/setup.sh 同因)
cd /opt/mdagent/code
pnpm install --ignore-scripts --registry=https://registry.npmmirror.com
# node-pty 原生模块:install 钩子被跳过,须显式 rebuild(python3.11, gcc/make 已在)
npm_config_python=/usr/bin/python3.11 pnpm rebuild node-pty 2>&1 | tail -2

# 服务器路径改写:patch 的 mcp 路径 + persona 里的 kit 路径 + POLICY kit root
sed -i 's|/opt/mdagent/app/mcp/md_mcp.py|/opt/mdagent/code/mcp/md_mcp.py|g' md-sim-assistant.headless.patch.yml
sed -i 's|/opt/mdagent/kit|/opt/mdagent/no-kit(云端未挂载)|g' md-sim-assistant.headless.patch.yml
if grep -q '/home/user' md-sim-assistant.headless.patch.yml; then
  echo "FAIL: patch 仍有 /home/user 残留" >&2; exit 1
fi
sed -i 's|/opt/mdagent/kit|/opt/mdagent/no-kit|g' evolution/POLICY.json

# web-search-pro 的 bundle patch 会连挂 @vendor/dsh-browser(本地 kit 全家桶);
# 云端 headless 不上 browser(拍板缓一缓),剥掉 browser 行只留搜索插件
python3.11 - <<'PY'
import re
p = "/opt/mdagent/code/dsh-plugins/dsh-web-search-pro/cordis.patch.yml"
t = open(p).read()
t2 = re.sub(r"    - id: browser\n(?:      .*\n)+", "", t, count=1)
assert "id: browser" not in t2, "browser 行未剥净"
open(p, "w").write(t2)
print("   web-search-pro: browser 行已剥")
PY

# 共享组 + systemd
groupadd -f mdshare
cp cloud/mdagent-api.service cloud/mdagent-observe.service cloud/mdagent-observe.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable mdagent-api mdagent-observe.timer >/dev/null 2>&1 || true

# nginx:443(外网可达 TLS default_server)与 5443(内网)各挂 locations(幂等,先备份)
# 注:阿里云安全组只放 80/443/5174,5443/8081 外网不可达——对外口径走 443
cp cloud/nginx-mdagent-locations.conf /etc/nginx/mdagent-locations.conf
python3.11 - <<'PY'
def patch(path, anchor):
    txt = open(path).read()
    if 'mdagent-locations' in txt:
        print('   已挂:', path)
        return
    import time as _t
    open(path + '.bak_%d' % _t.time(), 'w').write(txt)
    lines = txt.splitlines(keepends=True)
    out, done = [], False
    for ln in lines:
        out.append(ln)
        if not done and anchor in ln:
            out.append('    include /etc/nginx/mdagent-locations.conf;\n')
            done = True
    assert done, '%s 未找到挂点' % path
    open(path, 'w').writelines(out)
    print('   挂载:', path)

patch('/etc/nginx/conf.d/cloud-memory.conf', 'airi-locations.conf')
PY
if ! grep -q mdagent-locations /etc/nginx/conf.d/paper.conf; then
  cp /etc/nginx/conf.d/paper.conf "/etc/nginx/conf.d/paper.conf.bak_$(date +%Y%m%d_%H%M%S)"
  python3.11 - <<'PY'
lines = open('/etc/nginx/conf.d/paper.conf').read().splitlines(keepends=True)
out, in5443, done = [], False, False
for ln in lines:
    out.append(ln)
    if 'listen 5443' in ln:
        in5443 = True
    if in5443 and not done and 'airi-locations.conf' in ln:
        out.append('    include /etc/nginx/mdagent-locations.conf;\n')
        done = True
assert done, '5443 块未找到挂点'
open('/etc/nginx/conf.d/paper.conf', 'w').writelines(out)
PY
fi
nginx -t
systemctl reload nginx

# 管理员播种(幂等;已存在会报"已存在"不重置)
cd /opt/mdagent/code/cloud
ADMIN_PROVIDER=kimi-coding ADMIN_API_KEY="${ADMIN_KEY}" /usr/bin/python3.11 seed_admin.py user || true
REMOTE

echo "== [5/6] 启动 + 健康检查"
ssh "$SRV" bash -s <<'REMOTE'
set -euo pipefail
systemctl start mdagent-api
sleep 2
systemctl is-active mdagent-api
curl -fsS http://127.0.0.1:8090/v1/health
echo
REMOTE

echo "== [6/6] 完成。管理员 token 在服务器 /opt/mdagent/accounts/.mdagent_admin"
