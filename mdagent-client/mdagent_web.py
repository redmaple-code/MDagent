#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mdagent web 客户端:浏览器即工作台,脑子在云端。
单文件、纯标准库(Python 3.8+)、零智能体 IP——只有联网/对话/本地文件执行器。

用法:
  python3 mdagent_web.py --token mda_xxx          # 首次
  python3 mdagent_web.py                          # 之后免参数
启动后自动打开浏览器(127.0.0.1:8321),界面=本机 workbench 同款版式。

安全模型(与 REPL 客户端相同):
  云端证书指纹 pinning(防中间人冒充服务器);本地文件囚禁在开放目录;
  写文件必须网页上点"批准"才落盘(自动审批开关默认关);
  本机页面有启动 token + Origin 防线,其他网页/脚本无法借你浏览器调本地接口。
"""
import argparse
import collections
import hashlib
import http.client
import http.server
import json
import os
import queue
import secrets
import socket
import ssl
import threading
import time
import urllib.request
import urllib.error
import urllib.parse
import webbrowser
from pathlib import Path

DEFAULT_SERVER = "https://<SERVER-IP>/mdagent"
CONFIG = os.path.join(os.path.expanduser("~"), ".mdagent_client.json")  # 与 REPL 客户端共用
MAX_FILE_BYTES = 1024 * 1024
PORT = 8321

# 服务器证书 SHA256 指纹(有效期至 2027-05-01;换证需重新下载本文件)
CERT_SHA256 = ("F1:75:DF:A3:B5:0F:69:7A:BA:88:C8:23:80:5D:44:11:"
               "E2:2C:AE:83:33:C5:99:47:A2:01:60:AB:63:BA:7C:69")

_SSL_CTX = ssl._create_unverified_context()  # 自签跳过 CA 链,认证靠指纹比对


class _PinnedConn(http.client.HTTPSConnection):
    def connect(self):
        super().connect()
        der = self.sock.getpeercert(binary_form=True)
        fp = ":".join("%02X" % b for b in hashlib.sha256(der).digest())
        if fp != CERT_SHA256:
            raise ssl.SSLError("云端证书指纹不匹配(可能中间人)!实得 %s…" % fp[:23])


class _PinnedHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_PinnedConn, req, context=self._context)


_OPENER = urllib.request.build_opener(_PinnedHandler(context=_SSL_CTX))


def _load_cfg():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            c = json.load(f)
        if c.get("token"):
            return c
    except Exception:
        pass
    return None


def _save_cfg(server, token, root):
    c = {"server": (server or DEFAULT_SERVER).strip().rstrip("/"),
         "token": token.strip(),
         "root": (root or "").strip() or os.getcwd()}
    fd = os.open(CONFIG, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=1)
    return c


def _http(method, path, body=None, timeout=70):
    cfg = STATE["cfg"] or _load_cfg()
    if not cfg:
        raise RuntimeError("云端未配置(页面里填 server/token)")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(cfg["server"] + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + cfg["token"])
    with _OPENER.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# ---------- 本地文件执行器 ----------
class Jail:
    """目录囚禁:resolve 静态判定 + 打开后 /proc/self/fd 回验(挡 rename TOCTOU)。"""

    def __init__(self, root):
        self.root = Path(root).resolve()

    def resolve(self, rel):
        p = (self.root / rel).resolve()
        try:
            p.relative_to(self.root)
        except ValueError:
            raise PermissionError("路径越出开放目录: %s" % rel)
        return p

    def fd_in_jail(self, fd):
        if not os.path.exists("/proc/self/fd"):
            return True
        try:
            Path(os.path.realpath("/proc/self/fd/%d" % fd)).relative_to(self.root)
            return True
        except ValueError:
            return False


STATE = {
    "cfg": None,
    "jail": None,
    "seen": collections.OrderedDict(),   # op_id 去重(防 requeue 重放)
    "pending": {},                       # op_id -> 待批准写
    "auto": False,                       # 自动审批(写文件免弹卡,默认关)
    "lock": threading.Lock(),
    "last_poll": 0.0,
    "local_token": secrets.token_urlsafe(16),
}


def _docx_text(raw):
    """docx(zip)→纯文本: word/document.xml 去标签。只解压只读,不执行任何东西,
    无需 exec 批准。xml 读限 4MB(防 zip 炸弹),产出文本由调用方按 1MB 截。"""
    import html as _html
    import io as _io
    import zipfile as _zipfile
    with _zipfile.ZipFile(_io.BytesIO(raw)) as z:
        with z.open("word/document.xml") as f:
            xml = f.read(4 * 1024 * 1024).decode("utf-8", "replace")
    xml = xml.replace("</w:p>", "\n")
    text = _html.unescape(re.sub(r"<[^>]+>", "", xml))
    return re.sub(r"\n{3,}", "\n\n", text).strip()

def _do_op(op):
    oid, name = op.get("id"), op.get("op")
    args = op.get("args") or {}
    with STATE["lock"]:
        if oid in STATE["seen"]:
            return {"op_id": oid, "ok": False, "error": "重复 op,已忽略(防重放)"}
        STATE["seen"][oid] = None
        while len(STATE["seen"]) > 500:
            STATE["seen"].popitem(last=False)
    try:
        jail = STATE["jail"]
        if name == "list":
            d = jail.resolve(args.get("path") or ".")
            if not d.is_dir():
                raise FileNotFoundError("目录不存在: %s" % args.get("path"))
            data = {"entries": sorted("%s%s" % (p.name, "/" if p.is_dir() else "")
                                      for p in d.iterdir())[:500]}
        elif name == "read":
            p = jail.resolve(args.get("path") or "")
            if not p.is_file():
                raise FileNotFoundError("文件不存在: %s" % args.get("path"))
            fd = os.open(str(p), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "rb") as f:
                if not jail.fd_in_jail(f.fileno()):
                    raise PermissionError("路径越出开放目录(打开后回验)")
                limit = (8 if p.suffix.lower() == ".docx" else 1) * MAX_FILE_BYTES
                raw = f.read(limit + 1)  # 流式截断,不信 st_size
            if p.suffix.lower() == ".docx":
                if len(raw) > 8 * MAX_FILE_BYTES:
                    raise ValueError("docx 超过 8MB,不支持")
                content = _docx_text(raw)[:MAX_FILE_BYTES]
            elif p.suffix.lower() == ".pdf":
                import shutil as _shutil
                import subprocess as _sp
                pdft = _shutil.which("pdftotext")
                if not pdft:
                    if len(raw) > MAX_FILE_BYTES:
                        raise ValueError("文件超过 1MB,不支持(本机无 pdftotext)")
                    content = raw.decode("utf-8", errors="replace")
                else:
                    r = _sp.run([pdft, "-enc", "UTF-8", str(p), "-"],
                                stdout=_sp.PIPE, stderr=_sp.DEVNULL, timeout=30)
                    content = r.stdout.decode("utf-8", "replace")[:MAX_FILE_BYTES]
            else:
                if len(raw) > MAX_FILE_BYTES:
                    raise ValueError("文件超过 1MB,不支持")
                content = raw.decode("utf-8", errors="replace")
            data = {"path": str(p.relative_to(jail.root)),
                    "content": content}
        elif name == "mkdir":
            p = jail.resolve(args.get("path") or "")
            p.mkdir(parents=True, exist_ok=True)
            data = {"created": str(p.relative_to(jail.root))}
        elif name == "write":
            data = _do_write(oid, args)
        elif name == "exec":
            return {"op_id": oid, "ok": False,
                    "error": "网页版不支持执行命令;请改用终端 TUI 版(mdagent)或 REPL 版"}
        elif name == "delete":
            return {"op_id": oid, "ok": False,
                    "error": "网页版不支持删除;请改用终端 TUI 版(mdagent)或 REPL 版"}
        elif name in ("edit", "glob", "grep"):
            return {"op_id": oid, "ok": False,
                    "error": "网页版暂不支持 %s;请改用终端 TUI 版(mdagent)"
                             % name}
        else:
            return {"op_id": oid, "ok": False, "error": "未知操作 " + str(name)}
        return {"op_id": oid, "ok": True, "data": data}
    except Exception as ex:
        return {"op_id": oid, "ok": False, "error": str(ex)}


def _do_write(oid, args):
    content = str(args.get("content", ""))
    nb = len(content.encode("utf-8"))
    if nb > MAX_FILE_BYTES:
        raise ValueError("内容超过 1MB,不支持")
    p = STATE["jail"].resolve(args.get("path") or "")
    if len(content) > 300:
        preview = "%s\n……(中间省略 %d 字)……\n%s" % (
            content[:200], len(content) - 300, content[-100:])
    else:
        preview = content
    req = {"path": str(p), "bytes": nb, "preview": preview,
           "event": threading.Event(), "decision": None, "ts": time.time()}
    if STATE["auto"]:
        req["decision"] = "approve"
        req["event"].set()
    else:
        with STATE["lock"]:
            STATE["pending"][oid] = req
    try:
        req["event"].wait(100)  # 须 < 云端 CALL_TIMEOUT 110s
        if req["decision"] != "approve":
            raise PermissionError("用户拒绝了这次写入(或 100s 内未批准)")
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT, 0o644)  # 回验后再 truncate
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            if not STATE["jail"].fd_in_jail(f.fileno()):
                raise PermissionError("路径越出开放目录(打开后回验)")
            f.truncate(0)
            f.write(content)
        return {"written": str(p.relative_to(STATE["jail"].root)), "bytes": nb}
    finally:
        with STATE["lock"]:
            STATE["pending"].pop(oid, None)


def _bridge_loop():
    while True:
        if not STATE["cfg"]:
            time.sleep(3)
            continue
        try:
            r = _http("GET", "/v1/bridge/poll?wait=50", timeout=60)
            STATE["last_poll"] = time.time()
        except Exception:
            time.sleep(3)
            continue
        op = r.get("op")
        if not op:
            continue
        result = _do_op(op)
        try:
            _http("POST", "/v1/bridge/result", result, timeout=15)
        except Exception:
            pass


# ---------- 网页(workbench.html 视觉克隆:顶栏/runbar/分栏/悬浮卡全对齐) ----------
# 主题:light/dark/ocean/purple/forest 五套 CSS 变量,localStorage wb-theme 三页同步
_CSS = """
:root{--bg:#ffffff;--panel:#f5f6f7;--border:rgba(0,0,0,.1);--border-soft:#ebedf0;
--bar-bg:rgba(255,255,255,.85);--text:#0f1115;--text-2:#61666b;--text-3:#81868c;
--brand:#4176e6;--brand-hover:#679efe;--brand-50:#edf3fe;--brand-100:#e4edfd;
--green:#22c55e;--green-100:#e6faed;--shadow-1:0 2px 4px 0 rgba(0,0,0,.05);
--font:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei","Helvetica Neue",Helvetica,Arial,sans-serif}
[data-theme=dark]{--bg:#16181d;--panel:#1c1f26;--border:rgba(255,255,255,.12);--border-soft:#2a2e37;
--bar-bg:rgba(22,24,29,.85);--text:#e6e8eb;--text-2:#9aa0a6;--text-3:#6f747c;
--brand:#679efe;--brand-hover:#86b2ff;--brand-50:#1c2a4a;--brand-100:#22314f;
--green:#34d399;--green-100:#12342a;--shadow-1:0 2px 4px rgba(0,0,0,.4)}
[data-theme=ocean]{--bg:#0c1b2a;--panel:#0f2233;--border:rgba(255,255,255,.1);--border-soft:#173349;
--bar-bg:rgba(12,27,42,.85);--text:#dbeafe;--text-2:#8fb3d9;--text-3:#5d7f9e;
--brand:#38bdf8;--brand-hover:#7dd3fc;--brand-50:#0e2f47;--brand-100:#123a57;
--green:#2dd4bf;--green-100:#0d3330;--shadow-1:0 2px 4px rgba(0,0,0,.4)}
[data-theme=purple]{--bg:#170f24;--panel:#1d1429;--border:rgba(255,255,255,.1);--border-soft:#2c1f42;
--bar-bg:rgba(23,15,36,.85);--text:#ede9fe;--text-2:#b3a4d4;--text-3:#8571ab;
--brand:#a78bfa;--brand-hover:#c4b5fd;--brand-50:#2a1d45;--brand-100:#352557;
--green:#34d399;--green-100:#12342a;--shadow-1:0 2px 4px rgba(0,0,0,.4)}
[data-theme=forest]{--bg:#0e1a13;--panel:#122016;--border:rgba(255,255,255,.1);--border-soft:#1d3524;
--bar-bg:rgba(14,26,19,.85);--text:#dcfce7;--text-2:#94c4a5;--text-3:#64896f;
--brand:#4ade80;--brand-hover:#86efac;--brand-50:#16351f;--brand-100:#1c4428;
--green:#4ade80;--green-100:#14301c;--shadow-1:0 2px 4px rgba(0,0,0,.4)}
html,body{margin:0;height:100%;font-family:var(--font);background:var(--panel);color:var(--text)}
"""
_THEME_JS = ("<script>document.documentElement.dataset.theme="
             "localStorage.getItem('wb-theme')||'light';</script>")

# 外壳:结构与 workbench.html 逐行对齐
PAGE = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>分子模拟助手 · 工作台（左讨论 · 右看板）</title>
__THEME_JS__
<style>__CSS__
html,body{overflow:hidden}
/* ── 顶栏(玻璃拟态) ── */
#bar{height:48px;display:flex;align-items:center;gap:12px;padding:0 16px;background:var(--bar-bg);
     backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);
     border-bottom:1px solid var(--border-soft);box-shadow:0 4px 16px rgba(0,0,0,.06);font-size:13px;position:relative;z-index:10}
#bar .logo{width:10px;height:10px;border-radius:50%;background:var(--brand);display:inline-block;margin-right:8px;
           box-shadow:0 0 8px var(--brand),0 0 0 3px var(--brand-100)}
#bar b{font-size:14px;font-weight:700;
       background:linear-gradient(92deg,var(--brand),#7c3aed 75%);
       -webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
#bar .hint{color:var(--text-3);font-size:12px;margin-left:4px}
#bar .spacer{margin-left:auto}
.navbtn{display:inline-flex;align-items:center;gap:4px;padding:5px 12px;border-radius:8px;border:1px solid transparent;
        background:transparent;color:var(--text-2);font-size:12.5px;text-decoration:none;cursor:pointer;
        transition:background .15s,color .15s;font-family:var(--font)}
.navbtn:hover{background:var(--brand-50);color:var(--brand)}
.navbtn.na{opacity:.4;cursor:default}
.navbtn.na:hover{background:transparent;color:var(--text-2)}
/* ── 自动审批开关(dsh 风格 switch) ── */
#aaWrap{display:inline-flex;align-items:center;gap:8px;cursor:pointer;user-select:none;padding:4px 6px;border-radius:8px}
#aaWrap:hover{background:var(--brand-50)}
#aaWrap .lbl{font-size:12.5px;color:var(--text-2)}
#aaTrack{width:34px;height:19px;border-radius:10px;background:#dcdfe4;position:relative;transition:background .2s;flex-shrink:0}
#aaTrack::after{content:"";position:absolute;top:2px;left:2px;width:15px;height:15px;border-radius:50%;background:#fff;
                box-shadow:0 1px 3px rgba(0,0,0,.2);transition:left .2s}
#aaWrap.on #aaTrack{background:var(--green)}
#aaWrap.on #aaTrack::after{left:17px}
#aaWrap.on .lbl{color:var(--text);font-weight:500}
/* ── 分栏 ── */
#wrap{display:flex;height:calc(100% - 78px)}
.pane{height:100%;position:relative}
#left{flex:0 0 46%}
#right{flex:1 1 auto}
iframe{width:100%;height:100%;border:0;background:var(--bg)}
#drag{flex:0 0 8px;cursor:col-resize;background:transparent;position:relative;transition:background .15s}
#drag::after{content:"";position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
             width:4px;height:28px;border-radius:2px;background:var(--border-soft);transition:background .15s}
#drag:hover{background:var(--brand-50)}
#drag:hover::after,#drag.active::after{background:var(--brand)}
.tag{position:absolute;top:10px;left:10px;z-index:5;background:var(--bar-bg);backdrop-filter:blur(2px);
     color:var(--text-2);font-size:11.5px;padding:3px 10px 3px 8px;border-radius:8px;pointer-events:none;
     border:1px solid var(--border-soft);border-left:3px solid var(--brand);box-shadow:var(--shadow-1)}
.tag.tag-dash{border-left-color:var(--green)}
/* ── 运行进度条(runbar) ── */
#runbar{height:30px;display:flex;align-items:center;gap:14px;padding:0 16px;font-size:12px;
        background:var(--bg);border-bottom:1px solid var(--border-soft);color:var(--text-2);overflow:hidden;white-space:nowrap}
#runbar .dot{width:8px;height:8px;border-radius:50%;flex-shrink:0;background:#9aa0a6}
#runbar .dot.ok{background:var(--green);box-shadow:0 0 6px var(--green)}
#runbar .dot.warn{background:#f59e0b;box-shadow:0 0 6px #f59e0b}
#runbar .case{font-weight:600;color:var(--text);max-width:260px;overflow:hidden;text-overflow:ellipsis}
#runbar .track{width:200px;height:6px;border-radius:3px;background:var(--border-soft);position:relative;flex-shrink:0;overflow:hidden}
#runbar .fill{height:100%;border-radius:3px;background:linear-gradient(90deg,var(--brand),#7c3aed);width:0%;transition:width .6s}
#runbar .fill.warn{background:linear-gradient(90deg,#f59e0b,#f97316)}
#runbar b{color:var(--text);font-weight:600}
#runbar .mini{display:inline-flex;align-items:center;gap:4px}
#runbar .ago{color:var(--text-3);font-size:11px;margin-left:auto;flex-shrink:0}
/* ── 悬浮卡(闸门=写批准,策略=云端进化层) ── */
#gate-card{position:fixed;right:16px;bottom:16px;width:440px;max-height:55vh;overflow:auto;background:var(--bg);border:1px solid var(--border-soft);border-radius:10px;box-shadow:0 8px 30px rgba(0,0,0,.25);z-index:99;font-size:13px}
.gate-head{padding:8px 12px;font-weight:600;border-bottom:1px solid var(--border-soft)}
.gate-item{padding:8px 12px;border-bottom:1px dashed var(--border-soft)}
.gate-cmd{font-family:monospace;word-break:break-all;margin:4px 0}
.gate-preview{font-family:monospace;font-size:12px;color:var(--text-2);white-space:pre-wrap;word-break:break-all;
              background:var(--panel);border-radius:6px;padding:6px;margin:4px 0;max-height:120px;overflow:auto}
.gate-btns button{margin-right:8px;padding:3px 14px;border-radius:6px;border:1px solid;cursor:pointer;background:transparent}
.gate-ok{color:#16a34a;border-color:#16a34a}
.gate-no{color:#dc2626;border-color:#dc2626}
#bstrat-card{position:fixed;left:16px;bottom:16px;width:460px;max-height:55vh;overflow:auto;background:var(--bg);border:1px solid var(--border-soft);border-radius:10px;box-shadow:0 8px 30px rgba(0,0,0,.25);z-index:99;font-size:13px}
#bstrat-card .bs-head{padding:8px 12px;font-weight:600;border-bottom:1px solid var(--border-soft)}
#bstrat-card .bs-item{padding:8px 12px;border-bottom:1px dashed var(--border-soft)}
#bstrat-card .bs-rule{margin:4px 0;color:var(--text-2)}
#bstrat-card .bs-meta{color:var(--text-3);font-size:12px}
#gate-card.folded #gate-list,#bstrat-card.folded #bs-list{display:none}
#gate-card.folded,#bstrat-card.folded{max-height:none;opacity:.85}
#gate-card .gate-head,#bstrat-card .bs-head{user-select:none}
.card-hidden{display:none !important}
/* ── 配置弹窗 ── */
#cfg{position:fixed;inset:0;background:rgba(0,0,0,.35);display:none;align-items:center;justify-content:center;z-index:100}
#cfg .box{background:var(--bg);border-radius:14px;padding:24px;width:420px;display:flex;flex-direction:column;gap:10px}
#cfg input{padding:9px 12px;border:1px solid var(--border-soft);border-radius:8px;font-size:13px;font-family:var(--font);background:var(--panel);color:var(--text)}
#cfg label{font-size:12px;color:var(--text-2)}
#cfg button{align-self:flex-end;padding:7px 20px;border-radius:8px;border:0;background:var(--brand);color:#fff;font-size:13px;cursor:pointer}
#cErr{color:#dc2626;font-size:12px}
</style>
</head>
<body>
  <div id="bar">
    <span style="display:flex;align-items:center"><span class="logo"></span><b>分子模拟助手 · 工作台</b></span>
    <span class="hint">左:对话 / 巡检 / 诊断 · 右:本地开放目录(脑子在云端 ECS)</span>
    <span class="spacer"></span>
    <select id="theme-sel" class="navbtn" title="主题" style="border:1px solid var(--border-soft);background:var(--bg);">
      <option value="light">☀️ 浅色</option>
      <option value="dark">🌙 深色</option>
      <option value="ocean">🌊 深海</option>
      <option value="purple">🔮 暗紫</option>
      <option value="forest">🌲 墨绿</option>
    </select>
    <a class="navbtn" href="/evolv" target="_blank" title="云端进化层:行为策略 + 演化提案(全员共享)">🧬 演化</a>
    <span id="aaWrap" class="off" title="开启后:云端写本地文件不再逐笔弹卡,直接落盘(仍限开放目录内);关闭恢复逐笔批准">
      <span class="lbl">自动审批</span><span id="aaTrack"></span>
    </span>
    <button class="navbtn" id="cloud-toggle" title="左侧面板切换:云端对话 ↔ 进化层视图" style="border:1px solid var(--border-soft);background:var(--bg);cursor:pointer">☁️ 云端</button>
    <button class="navbtn" id="bs-navbtn" title="左下「行为策略」悬浮卡显隐" style="border:1px solid var(--border-soft);background:var(--bg);cursor:pointer">🧠 策略 <span id="bs-nav-count">0</span></button>
    <button class="navbtn" id="gate-navbtn" title="右下「写批准闸门」悬浮卡显隐(有新待批会自动弹出)" style="border:1px solid var(--border-soft);background:var(--bg);cursor:pointer">🛂 闸门 <span id="gate-nav-count">0</span></button>
    <button class="navbtn" id="cfgBtn" title="服务器 / token / 开放目录" style="border:1px solid var(--border-soft);background:var(--bg);cursor:pointer">⚙ 配置</button>
    <a class="navbtn" href="javascript:location.reload()">🔄 重载</a>
  </div>
  <div id="runbar" title="云端连接与用量(数字来自云端 API)">
    <span class="dot" id="rb-dot"></span>
    <span class="case" id="rb-case">连接云端…</span>
    <span id="rb-phase">—</span>
    <span class="track"><span class="fill" id="rb-fill"></span></span>
    <span id="rb-pct">—</span>
    <span id="rb-temp">—</span>
    <span id="rb-risk">—</span>
    <span class="mini" id="rb-score" title="云端共享进化层:生效中的行为策略数">🧬 —</span>
    <span class="mini" id="rb-health" title="文件桥状态(智能体读写你本地开放目录的通道)">🛡 —</span>
    <span class="ago" id="rb-ago"></span>
  </div>
  <div id="wrap">
    <div class="pane" id="left"><div class="tag">☁️ 云端智能体 · 对话</div><iframe id="chat-frame" src="/chat" allow="clipboard-read; clipboard-write"></iframe></div>
    <div id="drag"></div>
    <div class="pane" id="right"><div class="tag tag-dash">📂 本地开放目录</div><iframe id="dash-frame" src="/files"></iframe></div>
  </div>
<div id="gate-card" style="display:none">
  <div class="gate-head">🛂 写批准闸门 · <span id="gate-count">0</span> 条待批(云端智能体请求写本地文件,批准才落盘;100s 不批自动拒绝)</div>
  <div id="gate-list"></div>
</div>
<div id="bstrat-card" style="display:none">
  <div class="bs-head">🧠 行为策略 · 云端共享进化层 · <span id="bs-count">0</span> 条生效中(全员同一份,只读视图)</div>
  <div id="bs-list"></div>
</div>
<div id="cfg"><div class="box">
  <b>云端配置</b>
  <label>服务器</label><input id="cServer" placeholder="https://<SERVER-IP>/mdagent">
  <label style="margin-top:6px;border-top:1px solid var(--border-soft);padding-top:8px">账号密码登录(推荐,审批通过后即可用)</label>
  <div style="display:flex;gap:6px">
    <input id="cUser" placeholder="用户名" style="flex:1">
    <input id="cPass" placeholder="密码" type="password" style="flex:1">
  </div>
  <button id="cLogin" style="background:var(--accent,#2E4A8F)">登录(自动取 token)</button>
  <label style="margin-top:6px;border-top:1px solid var(--border-soft);padding-top:8px">或手动贴 token(mda_ 开头;已存可留空)</label><input id="cToken" placeholder="mda_…" type="password">
  <label>开放给智能体的本地目录(留空=启动目录)</label><input id="cRoot" placeholder="/path/to/project">
  <button id="cSave">保存并连接</button>
  <div id="cErr"></div></div></div>
<script>
(function(){
  const $=function(id){return document.getElementById(id)};
  function esc(s){return String(s==null?"":s).replace(/[&<>"]/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]})}
  async function api(m,p,b){const r=await fetch(p,{method:m,cache:"no-store",
    headers:b?{"Content-Type":"application/json"}:undefined,
    body:b?JSON.stringify(b):undefined});
    const j=await r.json().catch(function(){return {}});
    if(!r.ok)throw new Error(j.detail||j.error||("HTTP "+r.status));return j}

  // ── runbar + 闸门卡(4s) ──
  const gateCard=$("gate-card"),gateList=$("gate-list"),gateCnt=$("gate-count");
  async function tick(){
    let s;
    try{s=await api("GET","/api/state");}catch(e){
      $("rb-dot").className="dot";$("rb-case").textContent="本机服务异常";return}
    const online=s.bridge_online;
    $("rb-dot").className="dot"+(online?" ok":"");
    $("rb-dot").title=online?"文件桥在线":"桥离线";
    $("rb-case").textContent=s.configured?(s.root||"—"):"尚未配置云端";
    $("rb-case").title=s.server||"";
    $("rb-phase").innerHTML="<b>"+(online?"桥在线":"桥离线")+"</b>";
    const u=s.usage,lim=u?(u.limit_per_hour||0):0,pct=u&&lim?Math.min(100,(u.chats_24h||0)/lim*100):0;
    $("rb-fill").className="fill"+(pct>=80?" warn":"");
    $("rb-fill").style.width=pct.toFixed(1)+"%";
    $("rb-pct").textContent=u?((u.chats_24h||0)+(lim?"/"+lim+" 每小时":" ·不限量")):"—";
    $("rb-temp").textContent=s.server?s.server.replace(/^https?:\/\//,""):"—";
    $("rb-risk").textContent=(s.pending||[]).length?("待批 "+s.pending.length):"待批 0";
    $("rb-health").textContent="🛡 "+(online?"桥在线":"桥离线");
    $("rb-ago").textContent="更新于 "+new Date().toLocaleTimeString();
    if(!s.configured)$("cfg").style.display="flex";
    aaRender(!!s.auto);
    const pend=s.pending||[];
    if(!pend.length){gateCard.style.display="none";return}
    gateCard.style.display="block";gateCnt.textContent=pend.length;
    gateList.innerHTML=pend.map(function(i){
      return '<div class="gate-item"><div>云端智能体请求写入:</div>'
        +'<div class="gate-cmd">'+esc(i.path)+" ("+i.bytes+' 字节)</div>'
        +'<div class="gate-preview">'+esc(i.preview||"")+'</div>'
        +'<div class="gate-btns"><button class="gate-ok" data-id="'+esc(i.op_id)+'" data-dec="approve">批准写入</button>'
        +'<button class="gate-no" data-id="'+esc(i.op_id)+'" data-dec="deny">拒绝</button></div></div>'
    }).join("");
  }
  gateList.addEventListener("click",async function(e){
    const b=e.target.closest("button");if(!b)return;b.disabled=true;
    try{await api("POST","/api/resolve",{op_id:b.dataset.id,decision:b.dataset.dec})}
    catch(err){alert("裁决失败: "+err.message)}
    tick();
  });

  // ── 策略卡(云端进化层,60s) ──
  const bsCard=$("bstrat-card"),bsList=$("bs-list"),bsCnt=$("bs-count");
  async function bsTick(){
    let d;
    try{d=await api("GET","/api/evolution");}catch(e){bsCard.style.display="none";return}
    const ss=d.strategies||[];
    const act=ss.filter(function(x){return x.status==="active"});
    bsCnt.textContent=act.length;
    $("rb-score").innerHTML="<b>🧬 "+act.length+"</b> 策略生效";
    if(!ss.length){bsCard.style.display="none";return}
    bsCard.style.display="block";
    bsList.innerHTML=ss.slice().reverse().map(function(s){
      const ev=(s.evals||[]).map(function(e){return e.count}).join("→");
      let st=s.status==="active"?'<span style="color:#15803d">生效中</span>':
        '<span style="color:#6b7280">'+esc(s.status)+"</span>";
      return '<div class="bs-item"><div>'+st+" · <b>"+esc(s.title||s.signature)+"</b></div>"
        +'<div class="bs-rule">'+esc(s.rule||"")+'</div>'
        +'<div class="bs-meta">'+esc(s.sid||"")+" · 基线 "+(s.baseline_7d==null?"?":s.baseline_7d)
        +(ev?" · 后验 "+ev:"")+"</div></div>"
    }).join("");
  }

  // ── 自动审批开关(默认关;开启要确认) ──
  const aaWrap=$("aaWrap");
  function aaRender(on){aaWrap.classList.toggle("on",!!on);aaWrap.classList.toggle("off",!on)}
  aaWrap.addEventListener("click",async function(){
    try{
      const cur=await api("GET","/api/state");
      if(!cur.auto&&!confirm("开启后:云端写本地文件不再逐笔弹卡,直接落盘(仍限开放目录内)。确定开启?"))return;
      const r=await api("POST","/api/auto",{enabled:!cur.auto});
      aaRender(!!r.auto);
    }catch(e){}
  });

  // ── ☁️ 云端钮:左栏 对话 ↔ 进化层 ──
  const frame=$("chat-frame"),cloudBtn=$("cloud-toggle");
  const tag=frame.previousElementSibling;
  let evolv=localStorage.getItem("wb-leftview")==="evolv";
  function renderLeft(){
    frame.src=evolv?"/evolv":"/chat";
    cloudBtn.style.color=evolv?"var(--brand)":"";
    cloudBtn.style.borderColor=evolv?"var(--brand)":"var(--border-soft)";
    if(tag&&tag.classList.contains("tag"))
      tag.textContent=evolv?"🧬 云端进化层 · 全员共享":"☁️ 云端智能体 · 对话";
  }
  cloudBtn.addEventListener("click",function(){
    evolv=!evolv;localStorage.setItem("wb-leftview",evolv?"evolv":"chat");renderLeft();
  });
  renderLeft();

  // ── 主题(五套,localStorage 三页同步) ──
  const sel=$("theme-sel");
  sel.value=localStorage.getItem("wb-theme")||"light";
  sel.addEventListener("change",function(){
    localStorage.setItem("wb-theme",sel.value);
    document.documentElement.dataset.theme=sel.value;
    document.querySelectorAll("iframe").forEach(function(f){f.contentWindow.location.reload()});
  });

  // ── 配置 ──
  $("cfgBtn").addEventListener("click",async function(){
    try{const s=await api("GET","/api/state");$("cServer").value=s.server||"";$("cRoot").value=s.root||""}catch(e){}
    $("cfg").style.display="flex";
  });
  $("cSave").addEventListener("click",async function(){
    try{await api("POST","/api/config",{server:$("cServer").value,token:$("cToken").value,root:$("cRoot").value});
      $("cToken").value="";$("cfg").style.display="none";$("cErr").textContent="";
      document.getElementById("dash-frame").contentWindow.location.reload();tick();
    }catch(e){$("cErr").textContent=e.message}
  });
  $("cLogin").addEventListener("click",async function(){
    try{const r=await api("POST","/api/login",{server:$("cServer").value,username:$("cUser").value,password:$("cPass").value,root:$("cRoot").value});
      $("cPass").value="";$("cfg").style.display="none";$("cErr").textContent="";
      document.getElementById("dash-frame").contentWindow.location.reload();tick();
    }catch(e){$("cErr").textContent=e.message}
  });
  $("cPass").addEventListener("keydown",function(e){if(e.key==="Enter")$("cLogin").click()});

  // ── 悬浮卡显隐/折叠(逻辑照搬 workbench) ──
  function wire(cardId,headSel,navId,navCountId,countSpanId,hideKey,foldKey,autoPop,defaultHidden){
    const card=$(cardId),nav=$(navId),navCnt=$(navCountId),cntSpan=$(countSpanId);
    if(!card||!nav)return;
    const head=card.querySelector(headSel);
    let lastCount=-1;
    function isHidden(){const v=localStorage.getItem(hideKey);return v===null?!!defaultHidden:v==="1"}
    function applyAll(){
      const hidden=isHidden();
      const folded=localStorage.getItem(foldKey)==="1";
      card.classList.toggle("card-hidden",hidden);
      card.classList.toggle("folded",folded);
      nav.style.opacity=hidden?".5":"1";
      if(head)head.title="点击收起/展开正文;顶栏按钮可整卡隐藏";
      const tag=head&&head.querySelector(".fold-tag");
      if(tag)tag.textContent=folded?"▸ 展开":"▾ 收起";
    }
    if(head){
      head.style.cursor="pointer";
      const tag=document.createElement("span");
      tag.className="fold-tag";
      tag.style.cssText="float:right;opacity:.55;font-weight:400";
      head.appendChild(tag);
      head.addEventListener("click",function(){
        localStorage.setItem(foldKey,localStorage.getItem(foldKey)==="1"?"0":"1");applyAll();
      });
    }
    nav.addEventListener("click",function(){
      localStorage.setItem(hideKey,isHidden()?"0":"1");applyAll();
    });
    setInterval(function(){
      const n=cntSpan?parseInt(cntSpan.textContent||"0",10)||0:0;
      if(navCnt)navCnt.textContent=n;
      if(autoPop&&lastCount>=0&&n>lastCount&&isHidden()){
        localStorage.setItem(hideKey,"0");applyAll();
      }
      lastCount=n;
    },2000);
    applyAll();
  }
  wire("bstrat-card",".bs-head","bs-navbtn","bs-nav-count","bs-count","wb-hide-bstrat","wb-fold-bstrat",false,true);
  wire("gate-card",".gate-head","gate-navbtn","gate-nav-count","gate-count","wb-hide-gate","wb-fold-gate",true,false);

  // ── 可拖拽分隔条(位置记忆,同 workbench) ──
  const drag=$("drag"),left=$("left"),wrap=$("wrap");
  const savedPct=+localStorage.getItem("wb-split");
  if(savedPct)left.style.flex="0 0 "+Math.min(75,Math.max(25,savedPct))+"%";
  let dragging=false;
  drag.addEventListener("mousedown",function(){dragging=true;drag.classList.add("active");
    document.body.style.userSelect="none";
    document.querySelectorAll("iframe").forEach(function(f){f.style.pointerEvents="none"})});
  window.addEventListener("mouseup",function(){dragging=false;drag.classList.remove("active");
    document.body.style.userSelect="";
    document.querySelectorAll("iframe").forEach(function(f){f.style.pointerEvents="auto"})});
  window.addEventListener("mousemove",function(e){if(!dragging)return;
    const pct=Math.min(75,Math.max(25,e.clientX/wrap.clientWidth*100));
    left.style.flex="0 0 "+pct+"%";localStorage.setItem("wb-split",pct.toFixed(1))});

  tick();setInterval(tick,4000);
  bsTick();setInterval(bsTick,60000);
})();
</script>
</body>
</html>"""

# 左栏:云端对话(气泡式,Enter 发送)。注意:必须是 raw 字符串——JS 正则里的 \n
# 在普通 Python 字符串里会被吞成真实换行,正则字面量断裂,整页 JS 挂掉
CHAT_PAGE = r"""<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>云端智能体 · 对话</title>
__THEME_JS__
<style>__CSS__
#app{display:flex;flex-direction:column;height:100%}
#head{display:flex;align-items:center;gap:10px;padding:8px 14px;background:var(--bg);
      border-bottom:1px solid var(--border-soft);font-size:12.5px;color:var(--text-2);flex-shrink:0}
#head b{color:var(--text);font-size:13.5px}
#head .dot{width:8px;height:8px;border-radius:50%;background:#9aa0a6;display:inline-block}
#head .dot.on{background:var(--green);box-shadow:0 0 6px var(--green)}
#head input{padding:4px 8px;border:1px solid var(--border-soft);border-radius:6px;font-size:12.5px;font-family:var(--font);background:var(--panel);color:var(--text)}
#log{flex:1;overflow-y:auto;padding:34px 14px 14px;display:flex;flex-direction:column;gap:10px}
.msg{max-width:92%;padding:9px 13px;border-radius:10px;font-size:13.5px;line-height:1.65;
     word-break:break-word;box-shadow:var(--shadow-1)}
.msg.user{align-self:flex-end;background:var(--brand);color:#fff;border-bottom-right-radius:3px;white-space:pre-wrap}
.msg.agent{align-self:flex-start;background:var(--bg);border:1px solid var(--border-soft);border-bottom-left-radius:3px}
.msg.sys{align-self:center;background:transparent;color:var(--text-3);font-size:12px;box-shadow:none;padding:2px}
.msg.agent pre{background:var(--panel);border:1px solid var(--border-soft);border-radius:8px;
               padding:8px 10px;overflow-x:auto;font-size:12.5px;margin:6px 0}
.msg.agent code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px}
.msg.agent p{margin:4px 0}
.msg.agent ul,.msg.agent ol{margin:4px 0;padding-left:20px}
.msg.agent h1,.msg.agent h2,.msg.agent h3{font-size:14.5px;margin:8px 0 4px}
.msg.agent :not(pre)>code{background:var(--panel);border-radius:4px;padding:1px 5px}
#inputbar{display:flex;gap:8px;padding:10px 14px;background:var(--bg);border-top:1px solid var(--border-soft);flex-shrink:0}
#msg{flex:1;padding:9px 12px;border:1px solid var(--border-soft);border-radius:9px;font-size:13.5px;
     font-family:var(--font);resize:none;height:42px;box-sizing:border-box;background:var(--panel);color:var(--text)}
#send{padding:0 20px;border-radius:9px;border:0;background:var(--brand);color:#fff;font-size:14px;cursor:pointer}
#send:disabled{opacity:.5;cursor:default}
</style>
</head>
<body>
<div id="app">
  <div id="head">
    <span class="dot" id="online-dot"></span><b>云端智能体</b>
    <span>项目:</span><input id="project" value="default" style="width:110px" title="同项目内上下文连续">
  </div>
  <div id="log"></div>
  <div id="inputbar">
    <textarea id="msg" placeholder="和云端智能体对话…(Enter 发送,Shift+Enter 换行)"></textarea>
    <button id="send">发送</button>
  </div>
</div>
<script>
(function(){
  const $=function(id){return document.getElementById(id)};
  const log=$("log"),msg=$("msg"),send=$("send"),proj=$("project");
  function esc(s){return String(s==null?"":s).replace(/[&<>"]/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]})}
  // 极简 markdown:代码块/行内码/粗斜体/标题/列表(dsh 回复是 md)
  function md(src){
    let h=esc(src);
    h=h.replace(/```(\w*)\n([\s\S]*?)```/g,function(m,l,c){return "<pre><code>"+c.replace(/\n$/,"")+"</code></pre>"});
    h=h.replace(/`([^`\n]+)`/g,"<code>$1</code>");
    h=h.replace(/^###### (.*)$/gm,"<h3>$1</h3>").replace(/^### (.*)$/gm,"<h3>$1</h3>")
       .replace(/^## (.*)$/gm,"<h2>$1</h2>").replace(/^# (.*)$/gm,"<h2>$1</h2>");
    h=h.replace(/\*\*([^*]+)\*\*/g,"<b>$1</b>").replace(/(^|[^*])\*([^*\n]+)\*/g,"$1<i>$2</i>");
    const lines=h.split("\n");let out=[],inList=false;
    for(const ln of lines){
      const m=ln.match(/^\s*[-*] (.*)$/),n=ln.match(/^\s*\d+\. (.*)$/);
      if(m||n){if(!inList){out.push("<ul>");inList=true}out.push("<li>"+(m?m[1]:n[1])+"</li>")}
      else{if(inList){out.push("</ul>");inList=false}
        out.push(/^\s*(<h\d|<pre)/.test(ln)||ln===""?ln:"<p>"+ln+"</p>")}
    }
    if(inList)out.push("</ul>");
    return out.join("\n");
  }
  function add(cls,text,html){
    const d=document.createElement("div");d.className="msg "+cls;
    if(html)d.innerHTML=html;else d.textContent=text;
    log.appendChild(d);log.scrollTop=log.scrollHeight;return d}
  async function j(m,url,body){const r=await fetch(url,{method:m,cache:"no-store",
    headers:body?{"Content-Type":"application/json"}:undefined,
    body:body?JSON.stringify(body):undefined});
    const d=await r.json().catch(function(){return {error:"HTTP "+r.status}});
    if(!r.ok&&!d.error)d.error=d.detail||("HTTP "+r.status);return d}

  // ── 历史回放(切项目时从云端拉最近 50 轮) ──
  let curProj=null;
  async function loadHistory(){
    const p=proj.value.trim()||"default";
    if(p===curProj)return;curProj=p;
    log.innerHTML="";
    const d=await j("GET","/api/history?project="+encodeURIComponent(p));
    const turns=d.turns||[];
    if(!turns.length){add("sys","对话/记忆/进化都在云端你的独立空间;本地文件写入需在「🛂 闸门」卡逐笔批准。");return}
    add("sys","—— 项目「"+p+"」云端历史 "+turns.length+" 轮 ——");
    turns.forEach(function(t){
      if(t.role==="user")add("user",t.text);
      else if(t.role==="assistant")add("agent","",md(t.text));
    });
  }
  proj.addEventListener("change",loadHistory);
  proj.addEventListener("blur",loadHistory);

  let busy=false;
  async function doSend(){
    const text=msg.value.trim();if(!text||busy)return;
    busy=true;send.disabled=true;
    add("user",text);msg.value="";
    const thinking=add("sys","云端思考中…");
    let live=null;
    try{
      const r=await j("POST","/api/chat",{project:proj.value.trim()||"default",message:text});
      if(r.error||!r.job_id){thinking.textContent="提交失败: "+(r.error||JSON.stringify(r));return}
      for(;;){
        await new Promise(function(rs){setTimeout(rs,1000)});
        const s=await j("GET","/api/job?id="+encodeURIComponent(r.job_id));
        if(s.error){thinking.textContent="查询失败: "+s.error;break}
        if(s.status==="done"){thinking.remove();if(live)live.remove();add("agent","",md(s.reply||"(空回复)"));break}
        if(s.status==="error"){thinking.textContent="云端出错: "+(s.error||"?");if(live){live.remove();live=null}break}
        if(s.thinking){  // 思维链:暗色斜体流,跟在正文气泡上方
          thinking.textContent="✶ "+String(s.thinking).slice(-600).replace(/\n/g," ");
          thinking.style.fontStyle="italic";
        }
        if(s.partial){
          if(!live)live=add("agent","","");
          live.innerHTML=md(s.partial)+'<span style="color:#0e639c">▌</span>';
          log.scrollTop=log.scrollHeight}
        else thinking.textContent=s.thinking?thinking.textContent:(s.activity?("云端: "+s.activity):"云端思考中…("+(s.status||"queued")+")");
      }
    }catch(e){thinking.textContent="网络错误: "+e}
    busy=false;send.disabled=false;msg.focus();
  }
  send.addEventListener("click",doSend);
  msg.addEventListener("keydown",function(e){if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();doSend()}});

  async function tick(){
    const d=await j("GET","/api/state");
    $("online-dot").className="dot"+(d.bridge_online?" on":"");
    $("online-dot").title=d.bridge_online?"文件桥在线":"桥离线";
  }
  loadHistory();
  tick();setInterval(tick,10000);msg.focus();
})();
</script>
</body>
</html>"""

# 右栏:本地开放目录文件浏览器
FILES_PAGE = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>本地开放目录</title>
__THEME_JS__
<style>__CSS__
#app{display:flex;flex-direction:column;height:100%}
#head{display:flex;align-items:center;gap:8px;padding:8px 14px;background:var(--bg);
      border-bottom:1px solid var(--border-soft);font-size:12.5px;color:var(--text-2);flex-shrink:0}
#head .crumb{font-family:monospace;color:var(--text);word-break:break-all}
#head button{margin-left:auto;padding:3px 12px;border-radius:7px;border:1px solid var(--border-soft);
             background:var(--bg);color:var(--text-2);font-size:12px;cursor:pointer;flex-shrink:0}
#head button:hover{background:var(--brand-50);color:var(--brand)}
#up{margin-left:0 !important}
#list{flex:1;overflow-y:auto;padding:24px 8px 8px}
.row{display:flex;align-items:center;gap:8px;padding:5px 12px;border-radius:7px;font-size:13px;cursor:default}
.row.dir{cursor:pointer}
.row.dir:hover{background:var(--brand-50)}
.row .nm{flex:1;word-break:break-all;color:var(--text)}
.row.dir .nm{color:var(--brand)}
.row .sz{color:var(--text-3);font-size:11.5px;font-family:monospace;flex-shrink:0}
.empty{padding:20px;text-align:center;color:var(--text-3);font-size:12.5px}
</style>
</head>
<body>
<div id="app">
  <div id="head">
    <button id="up" title="上一级">⬅</button>
    <span class="crumb" id="crumb">.</span>
    <button id="re">🔄 刷新</button>
  </div>
  <div id="list"></div>
</div>
<script>
(function(){
  const $=function(id){return document.getElementById(id)};
  let cur=".";
  function esc(s){return String(s==null?"":s).replace(/[&<>"]/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]})}
  function fmt(n){if(n<1024)return n+" B";if(n<1048576)return (n/1024).toFixed(1)+" KB";return (n/1048576).toFixed(1)+" MB"}
  async function load(p){
    const r=await fetch("/api/files?path="+encodeURIComponent(p||cur),{cache:"no-store"});
    const d=await r.json().catch(function(){return {error:"HTTP "+r.status}});
    const list=$("list");
    if(d.error){list.innerHTML='<div class="empty">'+esc(d.error)+"</div>";return}
    cur=d.path||".";
    $("crumb").textContent=cur;
    if(!d.entries||!d.entries.length){list.innerHTML='<div class="empty">(空目录)</div>';return}
    list.innerHTML=d.entries.map(function(e){
      return '<div class="row'+(e.dir?" dir":"")+'" data-n="'+esc(e.name)+'">'
        +"<span>"+(e.dir?"📁":"📄")+'</span><span class="nm">'+esc(e.name)+"</span>"
        +'<span class="sz">'+(e.dir?"":fmt(e.size))+"</span></div>"
    }).join("");
  }
  $("list").addEventListener("click",function(e){
    const row=e.target.closest(".row.dir");if(!row)return;
    load((cur==="."?"":cur+"/")+row.dataset.n);
  });
  $("up").addEventListener("click",function(){
    if(cur==="."||cur==="")return;
    const i=cur.lastIndexOf("/");load(i<0?".":cur.slice(0,i));
  });
  $("re").addEventListener("click",function(){load(cur)});
  load(".");setInterval(function(){load(cur)},10000);
})();
</script>
</body>
</html>"""

# 进化层视图(云端 /v1/evolution 只读)
EVOLV_PAGE = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>云端进化层</title>
__THEME_JS__
<style>__CSS__
#app{height:100%;overflow-y:auto;padding:34px 16px 16px}
h3{font-size:13px;color:var(--text-2);margin:14px 0 8px;font-weight:600}
.card{background:var(--bg);border:1px solid var(--border-soft);border-radius:10px;
      padding:10px 14px;margin-bottom:8px;font-size:13px;box-shadow:var(--shadow-1)}
.card .rule{color:var(--text-2);margin:4px 0;white-space:pre-wrap;word-break:break-word}
.card .meta{color:var(--text-3);font-size:12px}
.st-on{color:#15803d;font-weight:600}
.st-off{color:#6b7280}
.mono{font-family:monospace;font-size:12px}
.empty{color:var(--text-3);font-size:12.5px;padding:12px;text-align:center}
</style>
</head>
<body>
<div id="app">
  <h3>🧠 行为策略(全员共享,自动蒸馏)</h3><div id="strategies"></div>
  <h3>🧬 演化提案(最近 50 条)</h3><div id="proposals"></div>
</div>
<script>
(function(){
  const $=function(id){return document.getElementById(id)};
  function esc(s){return String(s==null?"":s).replace(/[&<>"]/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]})}
  async function load(){
    const r=await fetch("/api/evolution",{cache:"no-store"});
    const d=await r.json().catch(function(){return {error:"HTTP "+r.status}});
    if(d.error){$("strategies").innerHTML='<div class="empty">'+esc(d.error)+"</div>";return}
    const ss=d.strategies||[];
    $("strategies").innerHTML=ss.length?ss.slice().reverse().map(function(s){
      const ev=(s.evals||[]).map(function(e){return e.count}).join("→");
      const st=s.status==="active"?'<span class="st-on">生效中</span>':'<span class="st-off">'+esc(s.status)+"</span>";
      return '<div class="card"><div>'+st+" · <b>"+esc(s.title||s.signature)+"</b></div>"
        +'<div class="rule">'+esc(s.rule||"")+'</div>'
        +'<div class="meta">'+esc(s.sid||"")+" · 基线 "+(s.baseline_7d==null?"?":s.baseline_7d)
        +(ev?" · 后验 "+ev:"")+"</div></div>"
    }).join(""):'<div class="empty">(还没有策略)</div>';
    const ps=d.proposals||[];
    $("proposals").innerHTML=ps.length?ps.slice().reverse().map(function(p){
      return '<div class="card mono">'+esc(p)+"</div>"
    }).join(""):'<div class="empty">(还没有提案)</div>';
  }
  load();setInterval(load,60000);
})();
</script>
</body>
</html>"""


# ---------- 本机 HTTP(127.0.0.1 + 启动 token cookie + Origin 防线) ----------
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _authed(self):
        cookie = self.headers.get("Cookie", "")
        if ("mdagent_web=" + STATE["local_token"]) not in cookie:
            return False
        origin = self.headers.get("Origin") or self.headers.get("Referer") or ""
        return not origin or "127.0.0.1" in origin or "localhost" in origin

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, page, plant_cookie=False):
        body = page.replace("__CSS__", _CSS).replace(
            "__THEME_JS__", _THEME_JS).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if plant_cookie:
            self.send_header("Set-Cookie",
                             "mdagent_web=%s; Path=/; HttpOnly; SameSite=Strict"
                             % STATE["local_token"])
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 65536:
            raise ValueError("body 过大")
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        path = self.path.split("?")[0]
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query) \
            if "?" in self.path else {}
        if path == "/" and query.get("t", [""])[0] == STATE["local_token"]:
            # 种 cookie(一次性 URL token → HttpOnly cookie,之后书签免 token)
            self._html(PAGE, plant_cookie=True)
            return
        if not self._authed():
            self._json({"error": "请用终端里打印的完整 URL 打开(带 token)"}, 401)
            return
        if path == "/":
            self._html(PAGE)
            return
        if path == "/chat":
            self._html(CHAT_PAGE)
            return
        if path == "/files":
            self._html(FILES_PAGE)
            return
        if path == "/evolv":
            self._html(EVOLV_PAGE)
            return
        if path == "/api/state":
            cfg = STATE["cfg"] or _load_cfg()
            out = {"configured": bool(cfg), "server": (cfg or {}).get("server", ""),
                   "root": str(STATE["jail"].root) if STATE["jail"] else "",
                   "bridge_online": bool(cfg) and
                   (time.time() - STATE["last_poll"]) < 70,
                   "auto": STATE["auto"],
                   "pending": [], "usage": None}
            with STATE["lock"]:
                out["pending"] = [{"op_id": k, "path": v["path"], "bytes": v["bytes"],
                                   "preview": v["preview"]}
                                  for k, v in STATE["pending"].items()]
            if cfg:
                try:
                    out["usage"] = _http("GET", "/v1/usage", timeout=10)
                except Exception:
                    pass
            self._json(out)
            return
        if path == "/api/job":
            try:
                self._json(_http("GET", "/v1/jobs/" + query.get("id", [""])[0],
                                 timeout=15))
            except urllib.error.HTTPError as ex:
                self._json(json.loads(ex.read().decode("utf-8", "replace")
                                      or '{"detail":"云端错误"}'), ex.code)
            except Exception as e:
                self._json({"error": str(e)}, 502)
            return
        if path == "/api/files":
            rel = query.get("path", ["."])[0] or "."
            try:
                jail = STATE["jail"]
                d = jail.resolve(rel)
                if not d.is_dir():
                    raise FileNotFoundError("目录不存在: %s" % rel)
                entries = []
                for p in sorted(d.iterdir(),
                                key=lambda x: (not x.is_dir(), x.name.lower()))[:500]:
                    try:
                        st = p.stat()
                    except OSError:
                        continue
                    entries.append({"name": p.name, "dir": p.is_dir(),
                                    "size": 0 if p.is_dir() else st.st_size})
                self._json({"path": str(d.relative_to(jail.root)),
                            "entries": entries})
            except Exception as e:
                self._json({"error": str(e)}, 400)
            return
        if path == "/api/evolution":
            try:
                self._json(_http("GET", "/v1/evolution", timeout=15))
            except Exception as e:
                self._json({"error": str(e)}, 502)
            return
        if path == "/api/history":
            proj = query.get("project", ["default"])[0] or "default"
            try:
                self._json(_http("GET", "/v1/conversations/" +
                                 urllib.parse.quote(proj) + "?turns=50",
                                 timeout=15))
            except Exception as e:
                self._json({"turns": [], "error": str(e)})
            return
        self._json({"error": "404"}, 404)

    def do_POST(self):
        path = self.path.split("?")[0]
        if not self._authed():
            self._json({"error": "unauthorized"}, 401)
            return
        try:
            body = self._body()
            if path == "/api/login":
                # 代理云端 /v1/login(免 CORS):账号密码 → token → 直接落配置
                server = str(body.get("server") or "").strip().rstrip("/") \
                    or (STATE["cfg"] or _load_cfg() or {}).get("server") \
                    or DEFAULT_SERVER
                user = str(body.get("username") or "").strip()
                pwd = str(body.get("password") or "")
                req = urllib.request.Request(
                    server + "/v1/login",
                    data=json.dumps({"username": user, "password": pwd}).encode(),
                    method="POST")
                req.add_header("Content-Type", "application/json")
                try:
                    with _OPENER.open(req, timeout=15) as r:
                        d = json.loads(r.read().decode("utf-8"))
                except urllib.error.HTTPError as e:
                    try:
                        detail = json.loads(e.read().decode("utf-8", "replace")).get("detail")
                    except Exception:
                        detail = None
                    raise ValueError(detail or ("HTTP %s" % e.code))
                old = STATE["cfg"] or _load_cfg() or {}
                cfg = _save_cfg(server, d["token"],
                                str(body.get("root") or "") or old.get("root") or "")
                STATE["cfg"] = cfg
                STATE["jail"] = Jail(cfg["root"])
                self._json({"ok": True, "username": d.get("username"),
                            "server": server, "root": cfg["root"]})
                return
            if path == "/api/config":
                token = str(body.get("token") or "").strip()
                old = STATE["cfg"] or _load_cfg()
                if not token and old:
                    token = old["token"]
                if not token.startswith("mda_"):
                    raise ValueError("token 缺失或格式不对(mda_ 开头)")
                cfg = _save_cfg(str(body.get("server") or "") or (old or {}).get("server"),
                                token,
                                str(body.get("root") or "") or (old or {}).get("root"))
                STATE["cfg"] = cfg
                STATE["jail"] = Jail(cfg["root"])
                self._json({"ok": True, "server": cfg["server"], "root": cfg["root"]})
                return
            if path == "/api/auto":
                STATE["auto"] = bool(body.get("enabled"))
                self._json({"ok": True, "auto": STATE["auto"]})
                return
            if not STATE["cfg"]:
                raise ValueError("云端未配置")
            if path == "/api/chat":
                message = str(body.get("message") or "").strip()
                if not message or len(message) > 32000:
                    raise ValueError("message 为空或过长(>32K)")
                r = _http("POST", "/v1/chat", {
                    "project": str(body.get("project") or "default")[:64],
                    "message": message}, timeout=20)
                self._json(r, 202)
                return
            if path == "/api/resolve":
                oid = str(body.get("op_id") or "")
                with STATE["lock"]:
                    req = STATE["pending"].get(oid)
                if not req:
                    self._json({"error": "无此待批准项(可能已超时)"}, 404)
                    return
                req["decision"] = ("approve" if body.get("decision") == "approve"
                                   else "deny")
                req["event"].set()
                self._json({"ok": True})
                return
            self._json({"error": "404"}, 404)
        except urllib.error.HTTPError as ex:
            self._json(json.loads(ex.read().decode("utf-8", "replace")
                                  or '{"detail":"云端错误"}'), ex.code)
        except Exception as e:
            self._json({"error": str(e)}, 400)


def main():
    ap = argparse.ArgumentParser(description="mdagent web 客户端(浏览器即工作台)")
    ap.add_argument("--server", default=None)
    ap.add_argument("--token", default=None)
    ap.add_argument("--root", default=None)
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()

    cfg = _load_cfg() or {}
    if args.token or args.server or args.root:
        cfg = _save_cfg(args.server or cfg.get("server") or DEFAULT_SERVER,
                        args.token or cfg.get("token") or "",
                        args.root or cfg.get("root") or "")
    if not cfg.get("token"):
        cfg = {"server": args.server or DEFAULT_SERVER, "token": "",
               "root": args.root or os.getcwd()}
    STATE["cfg"] = cfg if cfg.get("token") else None
    STATE["jail"] = Jail(cfg.get("root") or os.getcwd())

    threading.Thread(target=_bridge_loop, daemon=True).start()

    sock = socket.socket()
    port = args.port
    try:
        sock.bind(("127.0.0.1", port))
    except OSError:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    sock.close()

    url = "http://127.0.0.1:%d/?t=%s" % (port, STATE["local_token"])
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("云端 MD 智能体工作台已启动")
    print("  地址: %s" % url)
    print("  开放目录: %s" % STATE["jail"].root)
    print("  (浏览器没自动打开就复制上面的完整地址;Ctrl+C 退出)")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
