#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dsh-agent 品牌改造 + 内置预设移除(幂等,可重复运行)。

背景:dsh 0.1.0-rc.6 没有品牌/预设的正式配置缝——
  * CLI 的 composeProfile 在启动时强制把 agent-presets 的 roots 指向
    <dsh包>/config/agent-presets(内置 standard/code/cordis/minimal),
    用户层 cordis.patch.yml 的 roots 会被它覆盖,唯一杠杆是清空该目录;
  * Web 前端的 logo/标题是 dsh-web-frontend/dist 里的静态产物与压缩 bundle;
  * TUI(deepseek-harness-tui)的鲸鱼像素画/DEEPSEEK 字标/终端标题硬编码在
    lib/index.js;CLI 帮助/系统提示尾注/设置页公告等 "DeepSeek Harness"
    文案散落在各包 lib。

本脚本直接修补 node_modules 里的被服务文件。pnpm 用硬链接,原地写会污染
全局 store,因此所有修改一律"写临时文件 + os.replace"断链。
pnpm 重装/升级 dsh 后需重跑本脚本。

运行: python3 scripts/apply-branding.py
"""
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # dsh-agent/
PNPM = os.path.join(ROOT, "node_modules", ".pnpm")

BRAND_FULL = "分子模拟专家 MD Exp"
BRAND_SHORT = "MD Exp"
HERO_HEADLINE = "AI4Science 探索未来"

FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="50" height="50" viewBox="0 0 50 50" fill="none">
	<style>
		@media (prefers-color-scheme: dark) {
			.atom { fill: #fff; }
			.bond { stroke: #fff; }
		}
	</style>
	<g class="mol">
		<path class="bond" d="M17 17 L33 17 M18.5 20.5 L23.5 31.5 M31.5 20.5 L26.5 31.5" stroke="#1a1a1a" stroke-width="2.6" fill="none"/>
		<circle class="atom" cx="14" cy="14" r="6.5" fill="#1a1a1a"/>
		<circle class="atom" cx="36" cy="14" r="6.5" fill="#1a1a1a"/>
		<circle class="atom" cx="25" cy="35" r="6.5" fill="#1a1a1a"/>
	</g>
</svg>
"""


def safe_write(path, content):
    """写临时文件再 replace,断开 pnpm 硬链接,不污染全局 store。"""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".brandtmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def find_copies(rel):
    pat = os.path.join(PNPM, "*", "node_modules", rel)
    import glob
    return sorted(glob.glob(pat))


def patch_index_html(dist):
    p = os.path.join(dist, "index.html")
    src = open(p, encoding="utf-8").read()
    if BRAND_FULL in src:
        return "skip"
    new = re.sub(r"<title>[^<]*</title>", "<title>%s</title>" % BRAND_FULL, src)
    if new == src:
        return "NO-TITLE"
    safe_write(p, new)
    return "ok"


def patch_manifest(dist):
    p = os.path.join(dist, "manifest.webmanifest")
    data = json.load(open(p, encoding="utf-8"))
    if data.get("name") == BRAND_FULL:
        return "skip"
    data["name"] = BRAND_FULL
    data["short_name"] = BRAND_SHORT
    safe_write(p, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return "ok"


def patch_favicon(dist):
    p = os.path.join(dist, "favicon.svg")
    if os.path.exists(p) and 'class="mol"' in open(p, encoding="utf-8").read():
        return "skip"
    safe_write(p, FAVICON_SVG)
    return "ok"


def _brace_match(src, open_idx):
    """从 src[open_idx]=='{' 起做括号配对,返回闭合 '}' 的下标。字符串/模板字面量按单字符跳过。"""
    depth = 0
    i = open_idx
    quote = None
    while i < len(src):
        c = src[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "'\"`":
            quote = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("unbalanced braces")


# 分子图标:替换 DeepSeek 鱼标(FishLogo)。保留原函数签名(参数名随压缩而变,
# 从原文本提取),jsx 运行时别名同理。
FISH_BODY = (
    "{{return {j}.jsxs(\"svg\",{{width:{size},height:{size},className:{cls},"
    "viewBox:\"0 0 24 24\",fill:\"none\",\"aria-hidden\":\"true\",children:["
    "{j}.jsx(\"path\",{{d:\"M8.2 8.2 L15.8 8.2 M9.3 10.6 L11.3 15.4 M14.7 10.6 L12.7 15.4\","
    "stroke:\"currentColor\",strokeWidth:1.7}}),"
    "{j}.jsx(\"circle\",{{cx:7,cy:7,r:3.1,fill:\"currentColor\"}}),"
    "{j}.jsx(\"circle\",{{cx:17,cy:7,r:3.1,fill:\"currentColor\"}}),"
    "{j}.jsx(\"circle\",{{cx:12,cy:17,r:3.1,fill:\"currentColor\"}})"
    "]}})}}"
)

# 文字标:替换 DeepSeek 字标(BrandWordmark),宽度比保持 182:24 不变。
WORDMARK_BODY = (
    "{{return {j}.jsxs(\"svg\",{{width:{size}*182/24,height:{size},className:{cls},"
    "viewBox:\"0 0 182 24\",fill:\"none\",\"aria-hidden\":\"true\",children:["
    "{j}.jsx(\"text\",{{x:2,y:17,fontSize:14,fontWeight:700,fill:\"currentColor\","
    "children:\"分子模拟专家\"}}),"
    "{j}.jsx(\"text\",{{x:90,y:16,fontSize:11,fill:\"currentColor\",opacity:.7,"
    "children:\"MD Exp\"}})"
    "]}})}}"
)


def _body_brace(src, func_start):
    """函数体 '{' 的下标:跳过参数解构里的 '{',定位 '){' 序列。"""
    return src.index("){", func_start) + 1


def _parse_sig(sig):
    """从 'function gf({size:n=24,className:r})' 提取 (参数名size, 参数名className)。"""
    m = re.search(r"\(\{size:([A-Za-z_$][\w$]*)=[^,}]*,className:([A-Za-z_$][\w$]*)\}\)", sig)
    if not m:
        raise ValueError("无法解析签名: %r" % sig)
    return m.group(1), m.group(2)


def patch_shell_bundle(dist):
    assets = os.path.join(dist, "assets")
    hits = [f for f in os.listdir(assets)
            if f.startswith("index-") and f.endswith(".js")]
    if len(hits) != 1:
        return "AMBIGUOUS:%r" % hits
    p = os.path.join(assets, hits[0])
    src = open(p, encoding="utf-8").read()
    if "分子模拟专家" in src:
        return "skip"

    # jsx 运行时别名:从 FishLogo 原体的 f.jsx / f.jsxs 提取
    m = re.search(r"function ([A-Za-z_$][\w$]*)\(\{size:[A-Za-z_$][\w$]*=24,className:[A-Za-z_$][\w$]*\}\)\{return ([A-Za-z_$][\w$]*)\.jsx\(\"svg\",\{width:[^,]+,height:[^*]*\*17\.04/23\.16", src)
    if not m:
        return "FISHLOGO-NOT-FOUND"
    fish_name, j = m.group(1), m.group(2)
    size_p, cls_p = _parse_sig(src[m.start():_body_brace(src, m.start())])
    fish_new = ("function %s({size:%s=24,className:%s})" % (fish_name, size_p, cls_p)
                + FISH_BODY.format(j=j, size=size_p, cls=cls_p))
    src = src[:m.start()] + fish_new + src[_brace_match(src, _body_brace(src, m.start())) + 1:]

    m2 = re.search(r"BrandWordmark:([A-Za-z_$][\w$]*)", src)
    if not m2:
        return "WORDMARK-ALIAS-NOT-FOUND"
    wm_name = m2.group(1)
    wm_start = src.find("function %s(" % wm_name)
    if wm_start < 0:
        return "WORDMARK-FUNC-NOT-FOUND"
    sig = src[wm_start:_body_brace(src, wm_start)]
    size_p, cls_p = _parse_sig(sig)
    wm_new = sig + WORDMARK_BODY.format(j=j, size=size_p, cls=cls_p)
    src = src[:wm_start] + wm_new + src[_brace_match(src, _body_brace(src, wm_start)) + 1:]

    safe_write(p, src)
    return "ok"


def disable_shipped_presets(dsh_pkg):
    src_dir = os.path.join(dsh_pkg, "config", "agent-presets")
    dst_dir = os.path.join(dsh_pkg, "config", "agent-presets.shipped-off")
    if not os.path.isdir(src_dir):
        return "skip" if os.path.isdir(dst_dir) else "MISSING"
    if os.path.isdir(dst_dir):
        # 已移出过:清掉 src 里可能残留/被重装恢复的预设目录
        import shutil
        for child in os.listdir(src_dir):
            p = os.path.join(src_dir, child)
            if os.path.isdir(p):
                shutil.rmtree(p)
        return "skip"
    os.rename(src_dir, dst_dir)
    os.makedirs(src_dir)
    with open(os.path.join(src_dir, "README.off.txt"), "w", encoding="utf-8") as f:
        f.write("内置预设已被 scripts/apply-branding.py 移出到 ../agent-presets.shipped-off。\n"
                "本实例只使用 $DSH_HOME/.agent-presets 下的 md-sim-assistant。\n")
    return "ok"


def patch_hero_text(conv_pkg):
    """欢迎页主标语/预览徽章(dsh-client-ui-conversation 的 i18n 表)。"""
    p = os.path.join(conv_pkg, "lib", "client.js")
    if not os.path.exists(p):
        return "MISSING"
    src = open(p, encoding="utf-8").read()
    if HERO_HEADLINE in src:
        return "skip"
    new = src.replace('"hero.headline": "探索未至之境"',
                      '"hero.headline": "%s"' % HERO_HEADLINE)
    new = new.replace('"hero.preview": "预览版"',
                      '"hero.preview": "%s"' % BRAND_SHORT)
    if new == src:
        return "HERO-NOT-FOUND"
    safe_write(p, new)
    return "ok"


def patch_surface_prompt(webapp_lib):
    """模型可见的朝向文案里的品牌名(非 UI,顺手统一)。"""
    p = os.path.join(webapp_lib, "lib", "index.js")
    src = open(p, encoding="utf-8").read()
    if "DeepSeek Harness Web GUI" not in src:
        return "skip"
    safe_write(p, src.replace("DeepSeek Harness Web GUI", "MD Exp Web GUI"))
    return "ok"


# ---- 运行时 "DeepSeek Harness" 文案 → MD Exp(2026-08-30 用户拍板:全品牌统一)----
# 只动用户/模型可见字符串;npm 包名、DEEPSEEK_API_KEY、update-check 包名等
# 功能标识符一律不碰。纯注释里的品牌名不改(零用户收益,徒增漂移面)。
STRING_PATCHES = [
    ("@deepseek-ai/dsh-system-prompt/lib/index.js", [
        ("You are an AI agent powered by DeepSeek Harness.",
         "You are an AI agent powered by MD Exp.")]),
    ("@deepseek-ai/dsh/lib/bin.js", [
        ("dsh: boot a DeepSeek Harness profile", "dsh: boot an MD Exp profile")]),
    ("@deepseek-ai/dsh-web-app/lib/startup.js", [
        ("Serve the DeepSeek Harness browser UI.", "Serve the MD Exp browser UI.")]),
    ("@deepseek-ai/dsh-skill-badge/lib/index.js", [
        ("produced with DeepSeek Harness", "produced with MD Exp")]),
    ("@deepseek-ai/dsh-app-boot/lib/index.js", [
        ("The DeepSeek Harness implementation checkout is at",
         "The MD Exp implementation checkout is at")]),
    ("@deepseek-ai/dsh-client-connection/lib/client.js", [
        ("DeepSeek Harness is a plugin-based agent harness",
         "MD Exp is a plugin-based agent harness"),
        ("DeepSeek Harness — plugin-based agent harness",
         "MD Exp — plugin-based agent harness")]),
    ("@deepseek-ai/dsh-client-ui-settings-models/lib/client.js", [
        ("DeepSeek Harness", "MD Exp")]),
]


def patch_strings(p, pairs):
    src = open(p, encoding="utf-8").read()
    new = src
    for old, rep in pairs:
        new = new.replace(old, rep)
    if new == src:
        return "skip" if all(rep in src for _, rep in pairs) else "NOT-FOUND"
    safe_write(p, new)
    return "ok"


# TUI 欢迎屏:DeepSeek 鲸鱼象素画 → "MD 小精灵"象素画(网格宽不变,
# 小 4x12→5x12 / 大 5x14→6x14,多一行给 MD 下方的小标 ᴱˣᵖ;布局常量
# WHALE_WIDTH 等不动,渲染按数组长度自适应)。粗体 MD 字母:
# M 凹槽里大方块眼 + ·小腮红点 + ▀小嘴,D 右侧两条游动摆动姿态
# 小尾巴;字标 "MD"+下方上标小字 ᴱˣᵖ。(横幅为静态渲染,动作感靠姿势表达)
TUI_ART_SMALL = [
    "▐▙  ▟▌▐▀▀▙ ▖",
    "▐▌▄▄▟▌▐▌ ▐▛ ",
    "▐▌▚▞▐▌▐▌ ▐▙ ",
    "▐▌  ▐▌▐▄▄▛ ▘",
    "   ᴱˣᵖ      ",
]
TUI_ART_LARGE = [
    "▐▙   ▟▌ █▀▛▖ ▖",
    "▐▌▘ ▝▐▌ █  █▞ ",
    "▐▌█ █▐▌ █  █  ",
    "▐▌·▀·▐▌ █  █▚ ",
    "▐▌   ▐▌ █▄▜▘ ▘",
    "    ᴱˣᵖ       ",
]


def _tui_bundle():
    """tui profile 的 deepseek-harness-tui bundle($DSH_HOME/profiles/tui,
    不在 dsh-agent 仓库的 .pnpm 下)。DSH_HOME 缺失时按 P1 默认数据根兜底。"""
    home = os.environ.get("DSH_HOME")
    if not home:
        root = os.environ.get("MDSIM_DATA_ROOT") or os.path.join(os.path.dirname(ROOT), "mdsim-data")
        home = os.path.join(root, "dsh-home")
    p = os.path.join(home, "profiles", "tui", "node_modules",
                     "deepseek-harness-tui", "lib", "index.js")
    return p if os.path.exists(p) else None


def patch_tui_bundle(p):
    src = open(p, encoding="utf-8").read()
    if 'gradientText("MD")' in src and 'bold("MOL SIM")' in src and '\t"    ᴱˣᵖ       "' in src:
        return "skip"

    def art_block(name, rows):
        return "const %s = [\n%s\n]" % (name, ",\n".join('\t"%s"' % r for r in rows))

    new = re.sub(r"const WHALE_ART = \[[^\]]*\]",
                 lambda m: art_block("WHALE_ART", TUI_ART_SMALL), src)
    new = re.sub(r"const WHALE_ART_LARGE = \[[^\]]*\]",
                 lambda m: art_block("WHALE_ART_LARGE", TUI_ART_LARGE), new)
    new = new.replace('gradientText("DEEPSEEK")', 'gradientText("MD")')
    new = new.replace('this.palette.accent("DEEPSEEK")', 'this.palette.accent("MD")')
    new = new.replace('gradientText("MD EXP")', 'gradientText("MD")')          # 迁移旧补丁
    new = new.replace('this.palette.accent("MD EXP")', 'this.palette.accent("MD")')  # 迁移旧补丁
    new = new.replace('this.palette.bold("HARNESS")', 'this.palette.bold("MOL SIM")')
    new = new.replace('this.palette.bold("分子模拟")', 'this.palette.bold("MOL SIM")')  # 迁移旧补丁
    new = new.replace('"DeepSeek Harness"', '"MD Exp"')   # 终端窗口标题两处
    new = new.replace("DEEPSEEK HARNESS", "MD EXP")        # 横幅注释
    new = new.replace("DeepSeek Harness", "MD Exp")        # 其余注释
    if 'gradientText("MD")' not in new or 'bold("MOL SIM")' not in new:
        return "WORDMARK-NOT-FOUND"
    if '\t"    ᴱˣᵖ       "' not in new:
        return "ART-NOT-FOUND"
    safe_write(p, new)
    return "ok"


def main():
    if not os.path.isdir(PNPM):
        print("!! 未找到 %s —— 在 dsh-agent 仓库根先 pnpm install" % PNPM)
        return 1
    ok = True
    for dist in find_copies("@deepseek-ai/dsh-web-frontend/dist"):
        rel = os.path.relpath(dist, ROOT)
        for label, fn in (("index.html", patch_index_html),
                          ("manifest", patch_manifest),
                          ("favicon", patch_favicon),
                          ("shell-bundle", patch_shell_bundle)):
            try:
                r = fn(dist)
            except Exception as e:  # noqa: BLE001
                r = "ERROR:%s" % e
                ok = False
            print("[brand] %-12s %-40s %s" % (label, rel, r))
    for pkg in find_copies("@deepseek-ai/dsh"):
        if not os.path.isdir(os.path.join(pkg, "config")):
            continue
        r = disable_shipped_presets(pkg)
        print("[preset] shipped-off   %-40s %s" % (os.path.relpath(pkg, ROOT), r))
    for lib in find_copies("@deepseek-ai/dsh-web-app"):
        r = patch_surface_prompt(lib)
        print("[brand] surface-prompt %-40s %s" % (os.path.relpath(lib, ROOT), r))
    for pkg in find_copies("@deepseek-ai/dsh-client-ui-conversation"):
        r = patch_hero_text(pkg)
        print("[brand] hero-text     %-40s %s" % (os.path.relpath(pkg, ROOT), r))
    for rel, pairs in STRING_PATCHES:
        for lib in find_copies(rel):
            try:
                r = patch_strings(lib, pairs)
            except Exception as e:  # noqa: BLE001
                r = "ERROR:%s" % e
                ok = False
            print("[brand] strings      %-40s %s" % (os.path.relpath(lib, ROOT), r))
    tui = _tui_bundle()
    if tui:
        try:
            r = patch_tui_bundle(tui)
        except Exception as e:  # noqa: BLE001
            r = "ERROR:%s" % e
            ok = False
        print("[brand] tui-bundle   %-40s %s" % (os.path.relpath(tui, ROOT), r))
    else:
        print("[brand] tui-bundle   %-40s %s" % ("(tui profile 未安装)", "skip"))
    print("done" if ok else "done (有 ERROR,见上)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
