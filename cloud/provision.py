#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用户空间开通:unix 用户(mu_<name>,主组 mdshare) + 目录 + 进化库目录软链
 + dsh-home(settings.yaml / .credentials.yaml)。
隔离是内核级的:目录 700 属 mu_<name>,dsh 进程 setpriv 到该 uid 跑,
A 用户进程物理读不到 B 用户目录。共享进化库组 mdshare 可写(setgid)。
MDAGENT_SKIP_UNIX=1 时跳过 useradd/setpriv 相关(本地测试用)。"""
import grp
import os
import pwd
import secrets
import shutil
import stat
import subprocess

import accounts
import paths
import tenant_files

SHARE_GROUP = "mdshare"
USER_PREFIX = "mu_"

PROXY_BASE = os.environ.get("MDAGENT_PROXY_BASE", "http://127.0.0.1:8090")  # 计费代理基址(租户 baseURL 指这)
PROXY_KEY_ENV = "MDEXP_PROXY_KEY"                                            # 租户 .credentials 里的代理令牌变量名

SETTINGS_TEMPLATE = """# dsh 实例配置(云端多租户,provision.py 生成)。真 key 只在计费代理,租户仅持代理令牌 mxp_。
llm-pi-ai:
  providers:
    {provider}:
      apiKeyEnv: {proxy_key_env}
{provider_block}agent-default-model:
  provider: {provider}
  model: {model}
agent-presets:
  default: md-sim-assistant
ui-onboarding:
  welcomeNoticeVersion: 2026-08-13.1
"""

# 各 provider 的 dsh 侧定义:baseURL 一律指计费代理(/llm/<provider>),真 key 在代理注入(BYOK 或厂商共用);
# {proxy} 由 _render_settings 填。新增 provider = 这里加一段 + accounts.PROVIDERS 加条目。
PROVIDER_BLOCKS = {
    "kimi": """      displayName: "Kimi"
      api: openai-completions
      baseURL: {proxy}/llm/kimi
      models:
        - id: kimi-k3
          contextWindow: 1048576
          maxTokens: 262144
        - id: kimi-k2.7-code
          contextWindow: 262144
          maxTokens: 131072
        - id: kimi-k2.7-code-highspeed
          contextWindow: 262144
          maxTokens: 131072
        - id: kimi-k2.6
          contextWindow: 262144
          maxTokens: 131072
""",
    "deepseek": """      displayName: "DeepSeek"
      api: openai-completions
      baseURL: {proxy}/llm/deepseek
      models:
        - id: deepseek-v4-pro
          contextWindow: 1048576
          maxTokens: 393216
        - id: deepseek-flash
          contextWindow: 1048576
          maxTokens: 393216
""",
    "bigmodel-anthropic": """      displayName: "BigModel GLM (Anthropic)"
      api: anthropic-messages
      baseURL: {proxy}/llm/bigmodel-anthropic
      models:
        - id: glm-5.3
          contextWindow: 200000
          maxTokens: 65536
        - id: glm-4.6
          contextWindow: 200000
          maxTokens: 65536
        - id: glm-4.5
          contextWindow: 131072
          maxTokens: 65536
""",
    "kimi-coding": """      displayName: "Kimi (Anthropic 兼容)"
      api: anthropic-messages
      baseURL: {proxy}/llm/kimi-coding
      models:
        - id: kimi-for-coding
          contextWindow: 262144
          maxTokens: 65536
""",
    "zai-coding-cn": """      displayName: "Z.ai GLM (Anthropic 兼容)"
      api: anthropic-messages
      baseURL: {proxy}/llm/zai-coding-cn
      models:
        - id: glm-4.6
          contextWindow: 200000
          maxTokens: 65536
""",
}


# headless profile 挂的通用插件(源在 code/dsh-plugins 下,deploy.sh 单独放行读)
HEADLESS_PLUGINS = ("dsh-web-search-pro", "dsh-files")

# web profile(form B 租户实例)—— 与本地 scripts/setup.sh 的 web bundles 同口径 + dsh-remote-world。
# 业务插件按 profiles/node_modules 绝对软链兜底解析(同 enable_headless_plugins,无需 pnpm install)。
WEB_PLUGINS = ("dsh-web-search-pro", "dsh-files", "dsh-md-trace",
               "dsh-md-ops-gate", "dsh-md-flow-gate", "dsh-md-friction",
               "dsh-remote-world")
WEB_BUNDLES = ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-web-app",
               "dsh-web-search-pro", "dsh-files", "dsh-md-trace",
               "dsh-md-ops-gate", "dsh-md-flow-gate", "dsh-md-friction",
               "dsh-remote-world"]
# dsh-remote-world 是"外部拷进来"的插件(不在 pnpm 依赖图),其运行期依赖要从已装的 .pnpm 里软链过去,
# 否则租户 boot 报 "Cannot find package '@deepseek-ai/dsh-fs'"。
REMOTE_WORLD_DEPS = (("@deepseek-ai", "dsh-fs", "@deepseek-ai+dsh-fs"),
                     ("@deepseek-ai", "dsh-subprocess", "@deepseek-ai+dsh-subprocess"),
                     ("@deepseek-ai", "dsh-sandbox", "@deepseek-ai+dsh-sandbox"),
                     ("@deepseek-ai", "cordis", "@deepseek-ai+cordis"),
                     ("@deepseek-ai", "schemastery", "@deepseek-ai+schemastery"),
                     ("", "ws", "ws"))

HEADLESS_PROFILE_FILES = {
    "cordis.yml": ("# dsh profile root — an empty entry list. The tree is composed as patches:\n"
                   "# each bundle in package.json's dsh.profile.bundles, then cordis.patch.yml, then any\n"
                   "# --patch overlays. Edit cordis.patch.yml, not this file.\n[]\n"),
    "cordis.patch.yml": ("# Your patch layer for this dsh profile, applied after every bundle layer:\n"
                         "# a top-level YAML array of loader patch entries (id-targeted config\n"
                         "# overrides, disables, and insert lists; `!!js` expressions allowed).\n[]\n"),
    "pnpm-workspace.yaml": "packages:\n  - .\n\nnodeLinker: hoisted\nautoInstallPeers: false\n",
}


def _skip_unix():
    return os.environ.get("MDAGENT_SKIP_UNIX") == "1"


def unix_name(username):
    return USER_PREFIX + username


def ensure_share_group():
    if _skip_unix():
        return
    try:
        grp.getgrnam(SHARE_GROUP)
    except KeyError:
        subprocess.run(["groupadd", SHARE_GROUP], check=True)


def ensure_unix_user(username):
    """幂等:存在即返回。主组 mdshare,nologin,无家。"""
    if _skip_unix():
        return
    ensure_share_group()
    un = unix_name(username)
    try:
        pwd.getpwnam(un)
        return
    except KeyError:
        pass
    subprocess.run(["useradd", "-M", "-s", "/sbin/nologin",
                    "-g", SHARE_GROUP, un], check=True)


def _chown_tree(path, username):
    if _skip_unix():
        return
    un = unix_name(username)
    for root, dirs, files in os.walk(path):
        for d in dirs:
            os.lchown(os.path.join(root, d), pwd.getpwnam(un).pw_uid,
                      grp.getgrnam(SHARE_GROUP).gr_gid)
        for f in files:
            os.lchown(os.path.join(root, f), pwd.getpwnam(un).pw_uid,
                      grp.getgrnam(SHARE_GROUP).gr_gid)
    os.lchown(path, pwd.getpwnam(un).pw_uid, grp.getgrnam(SHARE_GROUP).gr_gid)


def ensure_shared_evolution():
    """共享进化库:root:root 755,租户只读。唯一写入方是 root 侧 observe timer。
    红队ζ:mdshare 组可写只靠沙箱兜底,属多余暴露面,收紧 root-only。
    红队κ:目录 755 但文件本体曾 664 root:mdshare,任意租户可投毒全员共享库
    (跨租户 prompt 供应链)——递归收紧:文件 644 root:root,目录 755 root:root。"""
    d = paths.shared_evolution_dir()
    os.makedirs(d, exist_ok=True)
    os.makedirs(os.path.join(d, "proposals"), exist_ok=True)
    if not _skip_unix():
        for root, dirs, files in os.walk(d):
            for name in dirs:
                p = os.path.join(root, name)
                os.chown(p, 0, 0)
                os.chmod(p, 0o755)
            for name in files:
                p = os.path.join(root, name)
                os.chown(p, 0, 0)
                os.chmod(p, 0o644)
        os.chown(d, 0, 0)
        os.chmod(d, 0o755)


def _render_settings(u):
    info = accounts.PROVIDERS[u["provider"]]
    raw = PROVIDER_BLOCKS.get(u["provider"])
    if not raw:
        # M1 fail-closed:provider 无代理 block(缺 baseURL/api)绝不生成半配置——否则 dsh 可能把 mxp_ 令牌
        # 发到某默认端点(令牌外泄+绕计费)或用户开不了工。缺块 = 拒绝开通(apply 已限 provider∈PROVIDERS)。
        raise ValueError("provider %s 无 PROVIDER_BLOCK,拒绝生成 settings" % u["provider"])
    return SETTINGS_TEMPLATE.format(
        provider=u["provider"], proxy_key_env=PROXY_KEY_ENV,
        model=info["model"], provider_block=raw.format(proxy=PROXY_BASE))


def refresh_model_settings(username):
    """模型换代:按 PROVIDERS 当前定义重写 settings.yaml(凭据不动)。
    换模型流程 = 改 accounts.PROVIDERS 的 model → 部署 → 调本函数(或 admin 路由)。"""
    u = accounts.get(username)
    if not u:
        return False
    sp = os.path.join(paths.user_dsh_home(username), "settings.yaml")
    if not os.path.exists(sp):
        return False
    with open(sp, "w", encoding="utf-8") as f:
        f.write(_render_settings(u))
    if not _skip_unix():
        os.chown(sp, pwd.getpwnam(unix_name(username)).pw_uid,
                 grp.getgrnam(SHARE_GROUP).gr_gid)
    return True


def refresh_credentials(username):
    """轮换该用户的计费代理令牌 + 重写 .credentials.yaml(只放代理令牌 mxp_,真 key 在代理)。
    切 provider / 从旧「真 key 落盘」形态迁移到代理形态时调;settings.yaml 一并按当前 provider 重写。
    注意:轮换后正在跑的实例要重启才重读新令牌(reap_user 或下次 ensure)。"""
    u = accounts.get(username)
    if not u:
        return False
    home = paths.user_dsh_home(username)
    cp = os.path.join(home, ".credentials.yaml")
    if not os.path.isdir(home):
        return False
    proxy_tok = accounts.set_proxy_token(username)
    with open(cp, "w", encoding="utf-8") as f:
        f.write("%s: %s\n" % (PROXY_KEY_ENV, proxy_tok))
    os.chmod(cp, 0o600)
    refresh_model_settings(username)
    if not _skip_unix():
        os.chown(cp, pwd.getpwnam(unix_name(username)).pw_uid,
                 grp.getgrnam(SHARE_GROUP).gr_gid)
    return True


def ensure_proxy_credentials(username):
    """幂等迁移到代理形态(B-H1 修):credentials 已含代理令牌则跳过;仍是真 key(代理化改造前开通的老用户)
    则铸令牌重写——把真 key 移出租户 DSH_HOME(BYOK 真 key 仍在 accounts 供 llmproxy 注入,共用 key 在
    mdexp.env)。供 startup 一次性 sweep、/v1/auth 兜底、refresh_models 调用。返回是否发生迁移。"""
    home = paths.user_dsh_home(username)
    cp = os.path.join(home, ".credentials.yaml")
    if not os.path.exists(cp):
        return False
    try:
        with open(cp, encoding="utf-8") as f:
            head = f.read(400)
    except OSError:
        return False
    if PROXY_KEY_ENV in head:
        return False                       # 已是代理令牌,不动
    return refresh_credentials(username)   # 老用户真 key → 铸代理令牌重写(真 key 移出 DSH_HOME)


def enable_headless_plugins(username):
    """headless profile 挂通用插件(web-search-pro 搜索 / dsh-files 文档读取)。
    法门:dsh 的 bundle 解析在安装 node_modules 找不到时会落到 profile 侧
    profiles/node_modules 软链兜底(与 dsh-app-boot healProfilesModuleFallback
    同一机制,且该函数只补不删),所以这里 = bundles 追加 + 建软链,无需 pnpm
    install。normalizeShippedProfile 只归一与出厂元组完全一致的 bundles,
    追加后不命中,不会被写回重置。幂等,返回 profile 目录。"""
    import json
    dsh_home = paths.user_dsh_home(username)
    prof = os.path.join(dsh_home, "profiles", "headless")
    os.makedirs(prof, exist_ok=True)
    for fn, content in HEADLESS_PROFILE_FILES.items():
        p = os.path.join(prof, fn)
        if not os.path.exists(p):
            with open(p, "w", encoding="utf-8") as f:
                f.write(content)
    pj = os.path.join(prof, "package.json")
    pkg = {"name": "dsh-profile-headless", "private": True,
           "dependencies": {},
           "dsh": {"profile": {"bundles": ["@deepseek-ai/dsh-base",
                                           "@deepseek-ai/dsh-headless"]}}}
    if os.path.exists(pj):
        try:
            with open(pj, encoding="utf-8") as f:
                pkg = json.load(f)
        except ValueError:
            pass
    bundles = (pkg.setdefault("dsh", {}).setdefault("profile", {})
               .setdefault("bundles", ["@deepseek-ai/dsh-base",
                                       "@deepseek-ai/dsh-headless"]))
    for name in HEADLESS_PLUGINS:
        if name not in bundles:
            bundles.append(name)
    with open(pj, "w", encoding="utf-8") as f:
        json.dump(pkg, f, ensure_ascii=False, indent=2)
        f.write("\n")
    shared = os.path.join(dsh_home, "profiles", "node_modules")
    os.makedirs(shared, exist_ok=True)
    for name in HEADLESS_PLUGINS:
        src = os.path.join(paths.code_dir(), "dsh-plugins", name)
        if not os.path.isdir(src):
            continue  # 源不在(本地测试环境等),该插件跳过不挂
        link = os.path.join(shared, name)
        if os.path.islink(link) and os.readlink(link) == src:
            continue
        if os.path.lexists(link):
            os.unlink(link)
        os.symlink(src, link)
    if not _skip_unix():
        _chown_tree(prof, username)
        un = unix_name(username)
        os.lchown(shared, pwd.getpwnam(un).pw_uid,
                  grp.getgrnam(SHARE_GROUP).gr_gid)
        for name in HEADLESS_PLUGINS:
            os.lchown(os.path.join(shared, name),
                      pwd.getpwnam(un).pw_uid, grp.getgrnam(SHARE_GROUP).gr_gid)
    return prof


def ensure_web_runtime():
    """一次性代码树准备(幂等,首个 web 用户触发即可):
    ① dsh-remote-world 是"拷进来"的插件(不在 pnpm 依赖图),给它建 node_modules 软链到已装 .pnpm 的运行期依赖,
       否则租户 boot 报 "Cannot find package '@deepseek-ai/dsh-fs'";
    ② 让租户 mu_<user> 能【读】要加载的插件/MCP 源(o+rX)——云端跑 agent 必须能加载它们(persona 正文在
       用户自己 dsh-home、真 key 走代理不落租户,故这里只放行插件/mcp 代码本体,不放 kit/持久策略)。"""
    import glob
    code = paths.code_dir()
    rw = os.path.join(code, "dsh-plugins", "dsh-remote-world")
    if os.path.isdir(rw):
        pnpm = os.path.join(code, "node_modules", ".pnpm")
        for scope, name, full in REMOTE_WORLD_DEPS:
            pat = (os.path.join(pnpm, full + "@*", "node_modules", scope, name)
                   if scope else os.path.join(pnpm, full + "@*", "node_modules", name))
            hits = glob.glob(pat)
            if not hits:
                continue
            dst_dir = os.path.join(rw, "node_modules", scope) if scope else os.path.join(rw, "node_modules")
            os.makedirs(dst_dir, exist_ok=True)
            link = os.path.join(dst_dir, name)
            if os.path.islink(link) and os.readlink(link) == hits[0]:
                continue
            if os.path.lexists(link):
                os.unlink(link)
            os.symlink(hits[0], link)
    if _skip_unix():
        return
    subprocess.run(["chmod", "o+rx", os.path.join(code, "dsh-plugins")], check=False)
    for name in WEB_PLUGINS:
        subprocess.run(["chmod", "-R", "o+rX", os.path.join(code, "dsh-plugins", name)], check=False)
    mcp = os.path.join(code, "mcp")
    if os.path.isdir(mcp):
        subprocess.run(["chmod", "o+rx", mcp], check=False)
        subprocess.run(["chmod", "-R", "o+rX", mcp], check=False)
    gs = os.path.join(code, "dashboard", "gmx_scan.py")
    if os.path.exists(gs):
        subprocess.run(["chmod", "o+rx", os.path.join(code, "dashboard")], check=False)
        subprocess.run(["chmod", "o+r", gs], check=False)


def _rewrite_preset_paths(text):
    """把 preset 里写死的 mcp 脚本路径改成本机路径(部署时改写,不改 presets/ 源)。"""
    import re
    return re.sub(r"/[^'\"]*?/mcp/md_mcp\.py",
                  os.path.join(paths.code_dir(), "mcp", "md_mcp.py"), text)


def _profile_owner(username):
    if _skip_unix():
        return None
    return pwd.getpwnam(unix_name(username)).pw_uid, grp.getgrnam(SHARE_GROUP).gr_gid


def _profile_read(directory, name):
    try:
        return tenant_files.read_private(directory, name)
    except FileNotFoundError:
        return None


def _profile_link_matches(dsh_home, name, source):
    shared = os.path.join(dsh_home, "profiles", "node_modules")
    try:
        with tenant_files.home_directory(shared) as directory:
            target = os.readlink(name, dir_fd=directory)
    except FileNotFoundError:
        return False
    return target == source


def _ensure_profile_links(dsh_home, plugins, owner, *, strict=False):
    shared = os.path.join(dsh_home, "profiles", "node_modules")
    for name, folder in plugins:
        src = os.path.join(paths.code_dir(), "dsh-plugins", folder)
        if not os.path.isdir(src):
            continue
        with tenant_files.home_directory(shared, create=True, owner=owner, owner_root=dsh_home) as directory:
            try:
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if not stat.S_ISLNK(info.st_mode):
                    raise ValueError("tenant plugin path must be a symbolic link")
                if os.readlink(name, dir_fd=directory) == src:
                    continue
                if strict:
                    raise RuntimeError("BIO_PLUGIN_LINK_CONFLICT")
            except FileNotFoundError:
                pass
            temporary = ".mdagent-" + secrets.token_hex(16)
            os.symlink(src, temporary, dir_fd=directory)
            try:
                os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
            finally:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass


def _ensure_bio_links(dsh_home, owner=None):
    """Register optional source packages without following tenant path components."""
    _ensure_profile_links(dsh_home, (("dsh-bio-agent", "dsh-bio-agent"),
                                   ("bio-web-tool-cards", "dsh-bio-ui")), owner, strict=True)


def enable_web_profile(username):
    """开通 web profile(form B 租户实例):真 persona 预设 + 真 bundles + remote-world。
    与 enable_headless_plugins 同法(profiles/node_modules 绝对软链兜底,无需 pnpm install),幂等。"""
    import json
    dsh_home = paths.user_dsh_home(username)
    prof = os.path.join(dsh_home, "profiles", "web")
    owner = _profile_owner(username)
    _ensure_bio_links(dsh_home, owner)
    preset = os.path.join(dsh_home, ".agent-presets", "md-sim-assistant")
    package = _profile_read(prof, "package.json")
    agent = _profile_read(preset, "agent.cordis.yml")
    # C-4:已铺好则快速返回,不在 /v1/auth 热路径(nginx auth_request 每请求触发)重跑 ensure_web_runtime 的
    # chmod 子进程风暴 + 重写 package.json/preset(非原子,还可能撞正在 boot 的租户)。首次(产物缺)才全量铺。
    if (package is not None and agent is not None
            and _profile_link_matches(dsh_home, "dsh-remote-world",
                                     os.path.join(paths.code_dir(), "dsh-plugins", "dsh-remote-world"))):
        return prof
    ensure_web_runtime()
    for fn, content in HEADLESS_PROFILE_FILES.items():
        if _profile_read(prof, fn) is None:
            tenant_files.write_private(prof, fn, content, owner, owner_root=dsh_home)
    pkg = {"name": "dsh-profile-web", "private": True, "dependencies": {},
           "dsh": {"profile": {"bundles": list(WEB_BUNDLES)}}}
    if package is not None:
        try:
            pkg = json.loads(package)
        except ValueError:
            pass
    if not isinstance(pkg, dict):
        raise ValueError("tenant profile package must be an object")
    bundles = pkg.setdefault("dsh", {}).setdefault("profile", {}).setdefault("bundles", list(WEB_BUNDLES))
    for b in WEB_BUNDLES:
        if b not in bundles:
            bundles.append(b)
    tenant_files.write_private(prof, "package.json", json.dumps(pkg, ensure_ascii=False, indent=2) + "\n", owner,
                               owner_root=dsh_home)
    _ensure_profile_links(dsh_home, ((name, name) for name in WEB_PLUGINS), owner)
    presets_src = os.path.join(paths.code_dir(), "presets", "md-sim-assistant")
    if os.path.isdir(presets_src):
        for fn in ("agent.cordis.yml", "preset.yml"):
            sp = os.path.join(presets_src, fn)
            if not os.path.exists(sp):
                continue
            with open(sp, encoding="utf-8") as f:
                txt = f.read()
            if fn == "agent.cordis.yml":
                txt = _rewrite_preset_paths(txt)
            tenant_files.write_private(preset, fn, txt, owner, owner_root=dsh_home)
    return prof


def ensure_node_workspace(username, node_root):
    """工作区对齐:session.create 会在【云端本地】mkdir 项目目录(= 节点根同字符串)。
    节点根如 /home/kkk/mdexp 在 ECS 上 mu_<user> 建不了 → 由控制面(root)预建占位并 chown 给该用户。
    真活儿仍经 remote-world 落到节点;此占位只承接会话工作区元数据。"""
    # 【安全·CRITICAL】node_root 是节点壳子自报字符串(租户可用自定义客户端把 hello 的 root 设成任意值)——
    # 绝不能拿它 root 权 chown 任意已存在目录(否则 root=/etc 或 /opt/mdagent/accounts → root chown 提权/窃 token)。
    # 两道闸:① 规范化 + 黑名单拒系统与本服务目录;② 逐层建,只 chown「本次真新建」的叶子层,已存在目录一律不碰。
    import posixpath
    if not node_root or not node_root.startswith("/"):
        return
    rp = posixpath.normpath(node_root)
    if rp != node_root.rstrip("/") or ".." in rp.split("/"):
        return                                   # 非规范路径/含 .. → 拒
    _BADPFX = ("/etc", "/usr", "/bin", "/sbin", "/lib", "/lib64", "/boot", "/root",
               "/opt", "/var", "/dev", "/proc", "/sys", "/run")
    if rp == "/" or rp in ("/home", "/tmp") or any(rp == b or rp.startswith(b + "/") for b in _BADPFX):
        return                                   # 系统/本服务目录一律不建不 chown
    if _skip_unix():
        os.makedirs(rp, exist_ok=True)
        return
    try:
        uid = pwd.getpwnam(unix_name(username)).pw_uid
        gid = grp.getgrnam(SHARE_GROUP).gr_gid
        cur = ""
        for seg in rp.strip("/").split("/"):
            cur = cur + "/" + seg
            if not os.path.exists(cur):
                os.mkdir(cur)
                os.chown(cur, uid, gid)          # 只 chown 本次真新建的层;已存在(系统/别人)目录绝不碰
    except Exception:
        pass


def provision(username):
    """开通空间(幂等)。返回用户目录。"""
    ensure_shared_evolution()
    ensure_unix_user(username)
    udir = paths.user_dir(username)
    data = paths.user_data_dir(username)
    dsh_home = paths.user_dsh_home(username)
    conv = paths.user_conversations_dir(username)
    proj = paths.user_projects_dir(username)
    for d in (udir, data, dsh_home, conv, proj):
        os.makedirs(d, exist_ok=True)
    # 共享进化层:目录软链(repo 写文件走 tmp+os.replace,文件级软链会被
    # 原子替换悄悄断链,目录软链下 tmp+rename 发生在真实共享目录内)
    mem_link = os.path.join(data, "memory")
    if os.path.islink(mem_link):
        if os.readlink(mem_link) != paths.shared_evolution_dir():
            os.unlink(mem_link)
    elif os.path.isdir(mem_link):
        shutil.rmtree(mem_link)
    if not os.path.exists(mem_link):
        os.symlink(paths.shared_evolution_dir(), mem_link)
    # dsh-home:settings.yaml + .credentials.yaml
    u = accounts.get(username)
    if u:
        info = accounts.PROVIDERS[u["provider"]]
        sp = os.path.join(dsh_home, "settings.yaml")
        if not os.path.exists(sp):
            with open(sp, "w", encoding="utf-8") as f:
                f.write(_render_settings(u))
        cp = os.path.join(dsh_home, ".credentials.yaml")
        if not os.path.exists(cp):
            # 真 key 不落租户 DSH_HOME(agent 一条 bash 就能 cat);只写该用户的计费代理令牌 mxp_,
            # 代理据此认用户并注入真 key(BYOK 或厂商共用)。
            proxy_tok = accounts.set_proxy_token(username)
            with open(cp, "w", encoding="utf-8") as f:
                f.write("%s: %s\n" % (PROXY_KEY_ENV, proxy_tok))
            os.chmod(cp, 0o600)
    # headless profile(API/CI)+ web profile(form B 租户实例,真 persona)
    enable_headless_plugins(username)
    try:
        enable_web_profile(username)
    except Exception:
        pass  # web profile 失败不挡开通(headless 仍可用);/v1/auth 会再兜底建
    for d, mode in ((udir, 0o700), (data, 0o700), (dsh_home, 0o700),
                    (conv, 0o700), (proj, 0o755)):
        os.chmod(d, mode)
    _chown_tree(udir, username)
    return udir
