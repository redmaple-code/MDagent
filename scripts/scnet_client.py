#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SCNet 国家超算互联网开放平台客户端(2026-09-03)
================================================
覆盖开放平台 2.0 计算服务最小闭环:AK/SK 认证 → 区域/集群发现 → 文件上传 →
作业提交(白名单闸门) → 实时作业查询。纯 stdlib(urllib/hmac),dsh 侧 py3.11。

凭证:  <MDSIM_DATA_ROOT>/dsh-home/.credentials.yaml
        SCNET_USER / SCNET_ACCESS_KEY / SCNET_SECRET_KEY(chmod 600,绝不进代码库)
闸门:  mcp/md_config.json["scnet"] —— enabled/auto_submit/whitelist(queues,
        max_nodes,max_walltime_h)/work_root(默认 YMY-AI)/lmp_cmd。
        双 true 才允许提交,且仅限白名单内;越界一律拒绝并记审计。
人工确认(代码强制,2026-09-03 用户拍板,不依赖提示词):
        真提交前必须有真人批准——① 一次性批准文件(人工在交互终端跑
        `approve --plan-hash PH`,哈希绑定提交参数,30 分钟有效,用后销毁);
        ② 或人工直接在 TTY 终端跑 submit 时输入 YES。agent 子进程无 TTY,
        两条路都开不出来,只能 dry-run 出计划给人看。
审计:  每次提交尝试(含被拒/批准/dry-run)追加 <数据根>/events/scnet_submit.jsonl。

CLI:
  scnet_client.py auth                          # 认证并列出区域(token 掩码)
  scnet_client.py regions                       # 授权区域(hpcUrls/efileUrls/homePath)
  scnet_client.py clusters                      # 调度器 ID 列表
  scnet_client.py upload --file F [--file F2] --remote-case CASE
  scnet_client.py submit --case-id C --prod-dir D --input-file production.in \
      --queue Q --nodes 1 --ppn 32 --walltime 24:00:00 [--cmd "..."] [--dry-run]
  scnet_client.py approve --plan-hash PH        # 人工批准(仅交互终端)
  scnet_client.py query [--job-id J]
"""
import argparse
import base64
import getpass
import hashlib
import hmac
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "mcp"))

AUTH_URL = "https://api.scnet.cn/api/user/v3/tokens"
CENTER_URL = "https://www.scnet.cn/ac/openapi/v2/center"
RENEW_URL = "https://www.scnet.cn/ac/openapi/v2/tokens/next"
TOKEN_REFRESH_MARGIN = 3600  # 距过期不足 1h 即续约


class ScnetError(Exception):
    def __init__(self, code, msg, url=""):
        super().__init__("scnet %s: %s (%s)" % (code, msg, url))
        self.code, self.msg, self.url = str(code), msg, url


def _data_root():
    try:
        import paths  # dsh-agent/mcp/paths.py 单一解析源(动态读 env,无缓存)
        return paths.data_root()
    except Exception:
        dr = os.environ.get("MDSIM_DATA_ROOT")
        if dr:
            return dr
        raise ScnetError("config", "MDSIM_DATA_ROOT 不可解析")


def _config():
    cfg_path = os.path.join(HERE, "..", "mcp", "md_config.json")
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    scnet = cfg.get("scnet") or {}
    return cfg, scnet


def _credentials():
    """极简 YAML 解析:顶层 `KEY: value`(凭证文件为平铺结构,chmod 600)。"""
    path = os.path.join(_data_root(), "dsh-home", ".credentials.yaml")
    creds = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            k, _, v = line.partition(":")
            creds[k.strip()] = v.strip().strip('"').strip("'")
    user, ak, sk = (creds.get("SCNET_USER", ""), creds.get("SCNET_ACCESS_KEY", ""),
                    creds.get("SCNET_SECRET_KEY", ""))
    if not (user and ak and sk):
        raise ScnetError("config", "凭证不全:需 SCNET_USER/SCNET_ACCESS_KEY/SCNET_SECRET_KEY")
    return {"user": user, "ak": ak, "sk": sk}


def sign(ak, timestamp, user, sk):
    """HMAC-SHA256(SK, '{"accessKey":..,"timestamp":..,"user":..}') 小写十六进制。
    JSON 串按键字典序、无空格,与官方 Java 示例逐字节一致。"""
    msg = '{"accessKey":"%s","timestamp":"%s","user":"%s"}' % (ak, timestamp, user)
    return hmac.new(sk.encode("utf-8"), msg.encode("utf-8"),
                    hashlib.sha256).hexdigest()


def _http(method, url, token=None, body=None, headers=None, timeout=30):
    hdrs = {"Content-Type": "application/json"}
    if token:
        hdrs["token"] = token
    hdrs.update(headers or {})
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raise ScnetError(e.code, e.read().decode("utf-8", errors="replace")[:300], url)
    except urllib.error.URLError as e:
        raise ScnetError("network", str(e.reason), url)
    try:
        resp = json.loads(raw) if raw.strip() else {}
    except ValueError:
        raise ScnetError("bad_json", raw[:300], url)
    code = str(resp.get("code", "0"))
    if code != "0":
        raise ScnetError(code, resp.get("msg", ""), url)
    return resp.get("data")


def _jwt_expire_ms(token):
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
        return int(claims.get("expireTime", 0))
    except Exception:
        return 0


# ---------------------------------------------------------------- token 缓存
def _token_cache_path():
    p = os.path.join(_data_root(), "state")
    os.makedirs(p, exist_ok=True)
    return os.path.join(p, "scnet_tokens.json")


def _load_cached_tokens():
    try:
        with open(_token_cache_path(), encoding="utf-8") as f:
            cache = json.load(f)
        now_ms = time.time() * 1000
        for cid, info in cache.get("regions", {}).items():
            exp = info.get("expire_ms") or _jwt_expire_ms(info.get("token", ""))
            if exp and exp - now_ms < TOKEN_REFRESH_MARGIN * 1000:
                return None  # 有区域临期 → 整体重认证(简单可靠)
        return cache
    except Exception:
        return None


def authenticate():
    """AK/SK → 每区域 token;缓存于 <数据根>/state/scnet_tokens.json(chmod 600)。"""
    cached = _load_cached_tokens()
    if cached:
        return cached
    c = _credentials()
    ts = str(int(time.time()))
    data = _http("POST", AUTH_URL, headers={
        "user": c["user"], "accessKey": c["ak"], "timestamp": ts,
        "signature": sign(c["ak"], ts, c["user"], c["sk"]),
    }, body={})
    if data is not None and not isinstance(data, list):
        raise ScnetError("auth", "tokens 响应畸形(data 非数组)")
    regions = {}
    for item in data or []:
        if not isinstance(item, dict):
            continue
        tok = item.get("token")
        if not tok:
            continue  # 欠费停用的区域 token 为 null
        regions[str(item.get("clusterId"))] = {
            "name": item.get("clusterName", ""), "token": tok,
            "expire_ms": _jwt_expire_ms(tok)}
    if not regions:
        raise ScnetError("auth", "未获得任何区域 token(账户欠费停用?)")
    cache = {"obtained_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "regions": regions}
    path = _token_cache_path()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)  # 已存在文件(旧版 644 残留)也收紧
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)
    return cache


def _platform_token(cache):
    """/center 用区域 token(实测平台 token 报 10001;优先非 ac 区域,回退平台)。"""
    regions = cache.get("regions", {})
    if not regions:
        raise ScnetError("auth", "token 缓存无区域(需重新认证)")
    for cid, info in regions.items():
        if cid != "0" and info.get("name") != "ac":
            return info["token"]
    return next(iter(regions.values()))["token"]


def _svc_url(url, svc):
    """区域返回的服务 url 不统一(实测 hpcUrls 不带 /hpc、efileUrls 自带 /efile),
    归一化为含服务前缀的 base,再拼 /openapi/v2/..."""
    u = url.rstrip("/")
    return u if u.endswith("/" + svc) else u + "/" + svc


def get_center(cache):
    """授权区域 → [{cluster_id, name, hpc_url, efile_url, home_path, user_name}]"""
    data = _http("GET", CENTER_URL, token=_platform_token(cache))
    items = data if isinstance(data, list) else [data]
    out = []
    for it in items or []:
        def _url(lst):
            for u in lst or []:
                if str(u.get("enable")) == "true":
                    return u.get("url", "")
            return ""
        out.append({
            "cluster_id": str(it.get("id", "")),
            "name": it.get("name", ""),
            "user_name": (it.get("clusterUserInfo") or {}).get("userName", ""),
            "home_path": (it.get("clusterUserInfo") or {}).get("homePath", ""),
            "hpc_url": _url(it.get("hpcUrls")),
            "efile_url": _url(it.get("efileUrls")),
        })
    return [r for r in out if r["hpc_url"]]


def list_clusters(region, token):
    data = _http("GET", _svc_url(region["hpc_url"], "hpc") + "/openapi/v2/cluster", token=token)
    return data or []


def region_token(cache, region):
    try:
        return cache["regions"][region["cluster_id"]]["token"]
    except (KeyError, TypeError):
        raise ScnetError("config", "区域 %s 无缓存 token(缓存与 /center 错位,请重认证)"
                         % region.get("cluster_id"))


def _pick_region(cache, cluster_id=""):
    """选计算区域:config cluster_id 指定 > 第一个有 hpc_url 的非平台区域。"""
    _, scnet = _config()
    want = cluster_id or str(scnet.get("cluster_id", ""))
    regions = get_center(cache)
    for r in regions:
        if want and r["cluster_id"] == want:
            return r
    for r in regions:
        if r["cluster_id"] != "0":
            return r
    if regions:
        return regions[0]
    raise ScnetError("config", "无可用计算区域")


# ---------------------------------------------------------------- 文件
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")  # ASCII 白名单(远端路径/shell 安全)
MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 超出走分片接口(本客户端面向输入文件)


def _check_safe_name(name, what="名称"):
    """ASCII 白名单(与 R6/R7 同思路,远端路径更严):禁路径分隔/空白/shell 元字符
    /非 ASCII,单独拒 . 与 ..,首字符禁 -。"""
    if not isinstance(name, str) or not SAFE_NAME_RE.match(name) \
            or name in (".", "..") or name.startswith("-"):
        raise ScnetError("param", "%s 含非法字符(白名单 ^[A-Za-z0-9._-]+$,禁 . .. 与首字符 -): %r"
                         % (what, name))
    return name


def mkdir(efile_url, token, path):
    qs = urllib.parse.urlencode({"path": path, "createParents": "true"})
    return _http("POST", _svc_url(efile_url, "efile") + "/openapi/v2/file/mkdir?" + qs,
                 token=token, body={})


def upload_file(efile_url, token, remote_dir, local_path):
    """普通上传(multipart)。大文件(>100MB)应换分片接口,本客户端面向输入文件。"""
    name = _check_safe_name(os.path.basename(local_path), "上传文件名")
    size = os.path.getsize(local_path)
    if size > MAX_UPLOAD_BYTES:
        raise ScnetError("param", "文件 %s 大小 %d 超 100MB 上限,请改用分片上传接口"
                         % (name, size))
    mkdir(efile_url, token, remote_dir)
    boundary = "----scnet" + uuid.uuid4().hex
    with open(local_path, "rb") as f:
        content = f.read()
    parts = []
    for k, v in (("path", remote_dir.rstrip("/") + "/"), ("cover", "cover")):
        parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                      % (boundary, k, v)).encode("utf-8"))
    parts.append(("--%s\r\nContent-Disposition: form-data; name=\"file\"; "
                  "filename=\"%s\"\r\nContent-Type: application/octet-stream\r\n\r\n"
                  % (boundary, name)).encode("utf-8"))
    parts.append(content)
    parts.append(("\r\n--%s--\r\n" % boundary).encode("utf-8"))
    body = b"".join(parts)
    return _http("POST", _svc_url(efile_url, "efile") + "/openapi/v2/file/upload", token=token,
                 body=body,
                 headers={"Content-Type":
                          "multipart/form-data; boundary=%s" % boundary})


# ---------------------------------------------------------------- 作业
_WALLTIME_RE = re.compile(r"^(\d{1,4}):([0-5]\d):([0-5]\d)$")


def _parse_walltime_h(wt):
    m = isinstance(wt, str) and _WALLTIME_RE.match(wt)
    if not m:
        return -1
    h, mnt, s = (int(x) for x in m.groups())
    return h + mnt / 60.0 + s / 3600.0


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _check_work_root(work_root):
    """work_root(拼 remote_dir 第二段)逐段白名单,禁 .. 逃逸 homePath。"""
    for seg in str(work_root).strip("/").split("/"):
        _check_safe_name(seg, "work_root 段")
    return work_root


def gate_check(scnet, queue, nodes, walltime, ppn=None):
    """白名单闸门(fail-closed:任何配置畸形都拒,不裸抛):返回 None 放行,否则拒绝原因。"""
    if not isinstance(scnet, dict):
        return "md_config scnet 段缺失/畸形(fail-closed 拒绝)"
    if scnet.get("enabled") is not True:
        return "md_config scnet.enabled!=true(超算提交未启用)"
    if scnet.get("auto_submit") is not True:
        return "md_config scnet.auto_submit!=true(只渲染不提交)"
    wl = scnet.get("whitelist")
    if wl is None:
        wl = {}
    if not isinstance(wl, dict):
        return "scnet.whitelist 畸形(需对象): %r" % type(wl).__name__
    queues = wl.get("queues")
    if queues is None:
        queues = []
    if not isinstance(queues, list) or not all(isinstance(q, str) for q in queues):
        return "scnet.whitelist.queues 畸形(需字符串数组)"
    if queues and queue not in queues:
        return "队列 %r 不在白名单 %s" % (queue, queues)
    if not _is_int(nodes) or nodes < 1:
        return "节点数须为正整数: %r" % (nodes,)
    if ppn is not None and (not _is_int(ppn) or ppn < 1):
        return "每节点核数(ppn)须为正整数: %r" % (ppn,)
    max_nodes = wl.get("max_nodes", 1)
    if not _is_int(max_nodes) or max_nodes < 1:
        return "scnet.whitelist.max_nodes 畸形(需正整数): %r" % (max_nodes,)
    if nodes > max_nodes:
        return "节点数 %d 超白名单上限 %d" % (nodes, max_nodes)
    max_wt = wl.get("max_walltime_h", 24)
    if isinstance(max_wt, bool) or not isinstance(max_wt, (int, float)) \
            or not math.isfinite(max_wt) or max_wt <= 0:
        return "scnet.whitelist.max_walltime_h 畸形(需正有限数): %r" % (max_wt,)
    hrs = _parse_walltime_h(walltime)
    if hrs <= 0:
        return "walltime 格式非法(需 H:MM:SS 且 >0,分秒 <60): %r" % (walltime,)
    if hrs > max_wt:
        return "墙时 %.2fh 超白名单上限 %.2fh" % (hrs, max_wt)
    return None


def _audit(entry):
    """审计落盘;写崩不阻断主流程(E7 同思路),降级 stderr 告警。"""
    try:
        path = os.path.join(_data_root(), "events", "scnet_submit.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        entry = dict(entry)
        entry["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        print("警告: scnet 审计写盘失败(%s),动作=%s"
              % (e, entry.get("action")), file=sys.stderr)


# ---------------------------------------------------------------- 人工确认(代码强制,2026-09-03 用户拍板;第 19 轮对抗后加固)
# 提交必须有真人批准,两条通道,都写在代码里(不依赖提示词自觉):
#   1. 批准文件:人工在交互终端跑 `approve --plan-hash PH`(需 TTY + YES + 批准 PIN)
#      → 落一次性批准文件(plan_hash 绑定全量参数,30 分钟有效,原子取走,成功即销毁)。
#   2. TTY 内联:人工直接在终端跑 submit,打印计划后输入 YES + PIN。
# PIN 哈希(PBKDF2 盐化)存 state/scnet_approve/pin.json(chmod 600),由
# `approve --set-pin` 交互设置;agent 不知 PIN,pty 管道也喂不进。
# 威胁模型注记(基线 E2 同型):同 uid 手写伪造批准文件属信任边界内行为,
# 闸的意义是正规代码路径绝不可能无人自批;批准文件另做强 schema 校验防损坏误用。
APPROVAL_MAX_AGE = 1800
PIN_MIN_LEN = 8
PIN_PBKDF2_ROUNDS = 100000


def plan_hash(case_id, queue, nodes, ppn, walltime, cmd, files,
              cluster_id="", work_root=""):
    """绑定全量提交参数:文件按 名+sha256 入哈希(防同名内容调包),
    cluster_id/work_root 入哈希(防批准后配置漂移挪用)。"""
    fents = []
    for f in files:
        try:
            with open(f, "rb") as fh:
                digest = hashlib.sha256(fh.read()).hexdigest()
        except OSError as e:
            raise ScnetError("param", "待上传文件不可读: %s (%s)" % (f, e))
        fents.append({"name": os.path.basename(f), "sha256": digest})
    canon = json.dumps({"case_id": case_id, "cluster_id": str(cluster_id),
                        "work_root": work_root, "queue": queue,
                        "nodes": int(nodes), "ppn": int(ppn),
                        "walltime": walltime, "cmd": cmd,
                        "files": sorted(fents, key=lambda e: e["name"])},
                       ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def _approval_dir():
    d = os.path.join(_data_root(), "state", "scnet_approve")
    os.makedirs(d, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def _approval_path(ph):
    if not re.match(r"^[0-9a-f]{16}$", str(ph)):
        raise ScnetError("param", "plan_hash 格式非法(需 16 位小写 hex): %r" % (ph,))
    return os.path.join(_approval_dir(), ph + ".json")


def _pin_path():
    return os.path.join(_approval_dir(), "pin.json")


def _hash_pin(pin, salt):
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"),
                               bytes.fromhex(salt), PIN_PBKDF2_ROUNDS).hex()


def set_pin(pin, old_pin=""):
    """设置/更换批准 PIN(仅交互终端调用)。已设 PIN 时须先验旧 PIN。"""
    if not isinstance(pin, str) or len(pin) < PIN_MIN_LEN or pin.isdigit():
        raise ScnetError("gate", "PIN 至少 %d 位且不得纯数字" % PIN_MIN_LEN)
    if os.path.exists(_pin_path()):
        if not _verify_pin(old_pin):
            raise ScnetError("gate", "已设过 PIN,更换须验旧 PIN(验旧失败)")
    salt = os.urandom(16).hex()
    fd = os.open(_pin_path(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)  # 已存在文件 mode 不随 O_CREAT 生效,fchmod 兜底
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"salt": salt, "hash": _hash_pin(pin, salt)}, f)
    _audit({"action": "pin_set"})


def _verify_pin(pin):
    try:
        with open(_pin_path(), encoding="utf-8") as f:
            rec = json.load(f)
        return hmac.compare_digest(_hash_pin(pin, rec["salt"]), rec["hash"])
    except Exception:
        return False


def _require_pin():
    """交互输入并校验批准 PIN。未设 PIN 或校验失败都拒。"""
    if not os.path.exists(_pin_path()):
        print("尚未设置批准 PIN,先跑: approve --set-pin", file=sys.stderr)
        return False
    try:
        pin = getpass.getpass("批准 PIN: ")
    except (EOFError, OSError):
        return False
    return _verify_pin(pin)


def _tty_confirm(plan):
    """TTY 内联确认:打印完整计划,输入 YES 且 PIN 正确才放行。无 TTY 一律拒。"""
    if not sys.stdin.isatty():
        return False
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    try:
        ans = input("确认提交该作业到超算?输入 YES 确认: ")
    except EOFError:
        return False
    if ans.strip() != "YES":
        return False
    return _require_pin()


def _valid_approval(ph):
    """一次性批准文件强校验:schema 完整、plan_hash 字段与文件名一致、
    时间戳非未来且 30 分钟内。只读不取走(取走见 _take_approval)。"""
    try:
        with open(_approval_path(ph), encoding="utf-8") as f:
            ap = json.load(f)
        if not isinstance(ap, dict):
            return False
        if ap.get("plan_hash") != ph:
            return False
        epoch = ap.get("approved_epoch")
        if not _is_int(epoch) and not isinstance(epoch, float):
            return False
        age = time.time() - epoch
        return 0 <= age <= APPROVAL_MAX_AGE
    except Exception:
        return False


def _take_approval(ph):
    """原子取走批准文件(rename 到暂存名):并发下仅一个成功者拿到,消灭双花窗口。
    返回暂存路径;取不到返回 None。"""
    src = _approval_path(ph)
    dst = src + ".taken-" + uuid.uuid4().hex
    try:
        os.rename(src, dst)
        return dst
    except OSError:
        return None


def approve(plan_hash_str):
    """人工批准入口(仅交互终端):从审计日志找回该 hash 的 dry-run 计划,展示,
    输入 YES + PIN 后落一次性批准文件。无 TTY 直接拒绝——agent 无法自批。"""
    if not sys.stdin.isatty():
        raise ScnetError("gate", "人工批准必须在交互终端进行(检测到无 TTY,拒绝)")
    audit_path = os.path.join(_data_root(), "events", "scnet_submit.jsonl")
    plan = None
    try:
        with open(audit_path, encoding="utf-8") as f:
            for ln in f:
                try:
                    e = json.loads(ln)
                except ValueError:
                    continue
                if e.get("action") == "dry_run" and e.get("plan_hash") == plan_hash_str:
                    plan = e
    except OSError:
        pass
    if not plan:
        raise ScnetError("gate", "找不到 plan_hash=%s 的 dry-run 记录,先跑 submit --dry-run"
                         % plan_hash_str)
    plan = {k: v for k, v in plan.items() if k not in ("action", "ts")}
    print("待批准提交计划:")
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    try:
        ans = input("确认批准?输入 YES 批准(30 分钟内一次性有效): ")
    except EOFError:
        ans = ""
    if ans.strip() != "YES":
        _audit({"action": "approve_rejected", "plan_hash": plan_hash_str})
        return False
    if not _require_pin():
        _audit({"action": "approve_rejected", "plan_hash": plan_hash_str,
                "reason": "pin"})
        return False
    fd = os.open(_approval_path(plan_hash_str),
                 os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"plan_hash": plan_hash_str, "plan": plan,
                   "approved_epoch": time.time(),
                   "approved_at": time.strftime("%Y-%m-%dT%H:%M:%S")}, f,
                  ensure_ascii=False, indent=2)
    _audit({"action": "approved", "plan_hash": plan_hash_str,
            "case_id": plan.get("case_id")})
    return True


def submit_job(case_id, files, queue, nodes, ppn, walltime,
               input_file="production.in", cmd="", dry_run=False,
               cluster_id=""):
    """提交一个 case:case_id 白名单 → 白名单闸门 → 人工确认(代码强制,先于网络)
    → 上传 → 提交。批准原子取走,提交失败恢复原批准(可重试),成功即销毁。"""
    _, scnet = _config()
    try:
        _check_safe_name(case_id, "case_id")
    except ScnetError as e:
        _audit({"case_id": repr(case_id), "action": "refused", "reason": str(e)})
        return {"submitted": False, "refused": str(e)}
    job_name = case_id
    try:
        nodes = int(nodes)
        ppn = int(ppn)
    except (TypeError, ValueError):
        reason = "nodes/ppn 须为整数: %r/%r" % (nodes, ppn)
        _audit({"case_id": case_id, "action": "refused", "reason": reason})
        return {"submitted": False, "refused": reason}
    refuse = gate_check(scnet, queue, nodes, walltime, ppn=ppn)
    if refuse:
        _audit({"case_id": case_id, "action": "refused", "reason": refuse,
                "queue": queue, "nodes": nodes, "walltime": walltime})
        return {"submitted": False, "refused": refuse}

    work_root = str(scnet.get("work_root") or "YMY-AI")
    try:
        _check_work_root(work_root)
    except ScnetError as e:
        _audit({"case_id": case_id, "action": "refused", "reason": str(e)})
        return {"submitted": False, "refused": str(e)}
    eff_cluster = str(cluster_id or scnet.get("cluster_id") or "")
    if not cmd:
        cmd_t = scnet.get("lmp_cmd", "")
        if not cmd_t:
            return {"submitted": False,
                    "refused": "未给 --cmd 且 md_config scnet.lmp_cmd 为空"}
        cmd = cmd_t.format(nproc=nodes * ppn, input=os.path.basename(input_file))

    try:
        ph = plan_hash(case_id, queue, nodes, ppn, walltime, cmd, files,
                       cluster_id=eff_cluster, work_root=work_root)
    except ScnetError as e:
        _audit({"case_id": case_id, "action": "refused", "reason": str(e)})
        return {"submitted": False, "refused": str(e)}

    # 人工确认闸(代码强制,先于一切网络外发):批准文件 > TTY 内联 YES+PIN
    taken = None
    approved_via = None
    if not dry_run:
        if _valid_approval(ph):
            taken = _take_approval(ph)
            if taken:
                approved_via = "approval_file"
            else:
                reason = "批准文件被并发取走(一次性语义,fail-closed 拒绝)"
                _audit({"case_id": case_id, "action": "refused", "reason": reason,
                        "plan_hash": ph})
                return {"submitted": False, "refused": reason, "plan_hash": ph}
        elif not sys.stdin.isatty():
            reason = ("无人工批准:先 submit --dry-run 拿 plan_hash,再在终端跑 "
                      "approve --plan-hash %s(30 分钟内有效)" % ph)
            if not os.path.exists(_pin_path()):
                reason = "gate_not_armed(批准 PIN 未设置,人工闸未就位) | " + reason
            _audit({"case_id": case_id, "action": "refused",
                    "reason": "no_human_approval"
                              + ("" if os.path.exists(_pin_path())
                                 else "|gate_not_armed"),
                    "plan_hash": ph})
            return {"submitted": False, "refused": reason, "plan_hash": ph}

    try:
        cache = authenticate()
        region = _pick_region(cache, cluster_id)
        token = region_token(cache, region)
        remote_dir = "/".join([region["home_path"].rstrip("/"),
                               work_root.strip("/"), job_name.strip("/")])
        plan = {"case_id": case_id, "region": region["name"], "remote_dir": remote_dir,
                "queue": queue, "nodes": nodes, "ppn": ppn, "walltime": walltime,
                "cmd": cmd, "files": [os.path.basename(f) for f in files],
                "plan_hash": ph}
        if dry_run:
            _audit(dict(plan, action="dry_run"))
            return {"submitted": False, "dry_run": True, "plan": plan}

        if approved_via is None:  # TTY 内联通道:展示完整计划 + YES + PIN
            if _tty_confirm(plan):
                approved_via = "tty"
            else:
                _audit(dict(plan, action="refused", reason="no_human_approval"))
                return {"submitted": False, "refused": "人工确认未通过",
                        "plan_hash": ph}

        clusters = list_clusters(region, token)
        if not clusters:
            raise ScnetError("config", "区域 %s 无集群" % region["name"])
        sched_id = clusters[0]["id"]
        for lf in files:
            upload_file(region["efile_url"], token, remote_dir, lf)

        data = _http("POST", _svc_url(region["hpc_url"], "hpc") + "/openapi/v2/apptemplates/BASIC/BASE/job",
                     token=token, body={
                         "strJobManagerID": str(sched_id),
                         "mapAppJobInfo": {
                             "GAP_CMD_FILE": cmd,
                             "GAP_NNODE": str(nodes),
                             "GAP_NODE_STRING": "",
                             "GAP_SUBMIT_TYPE": "cmd",
                             "GAP_JOB_NAME": job_name,
                             "GAP_WORK_DIR": remote_dir,
                             "GAP_QUEUE": queue,
                             "GAP_NPROC": "",
                             "GAP_PPN": str(ppn),
                             "GAP_NGPU": "",
                             "GAP_NDCU": "",
                             "GAP_WALL_TIME": walltime,
                             "GAP_EXCLUSIVE": "",
                             "GAP_APPNAME": "BASE",
                             "GAP_STD_OUT_FILE": remote_dir + "/std.out.%j",
                             "GAP_STD_ERR_FILE": remote_dir + "/std.err.%j",
                         }})
    except Exception:
        if taken:  # 提交未成,恢复原批准(30 分钟窗内可同参数重试);
            try:      # 若期间人工已重新批准(目标存在),不覆盖新批准
                if os.path.exists(_approval_path(ph)):
                    os.remove(taken)
                else:
                    os.rename(taken, _approval_path(ph))
            except OSError:
                pass
        _audit({"case_id": case_id, "plan_hash": ph,
                "action": "dry_run_failed" if dry_run else "submit_failed"})
        raise
    job_id = ""
    if isinstance(data, dict):
        job_id = str(data.get("jobId") or data.get("jobID") or data.get("id") or "")
    elif data is not None:
        job_id = str(data)
    if taken:  # 一次性:提交成功后销毁,复用需重新批准
        try:
            os.remove(taken)
        except OSError:
            pass
    _audit(dict(plan, action="submitted", job_id=job_id, scheduler_id=sched_id,
                approved_via=approved_via))
    return {"submitted": True, "job_id": job_id, "scheduler_id": sched_id,
            "remote_dir": remote_dir, "region": region["name"],
            "approved_via": approved_via}


_JOB_ID_RE = re.compile(r"^[0-9_]+$")


def _check_job_id(job_id):
    """jobid 白名单(R7 同思路:兼容 slurm array `123_4`),防 query 参数污染/注入。"""
    if not _JOB_ID_RE.match(str(job_id)):
        raise ScnetError("param", "job_id 非法(白名单 ^[0-9_]+$): %r" % (job_id,))
    return str(job_id)


def query_jobs(cluster_id="", job_id=""):
    cache = authenticate()
    region = _pick_region(cache, cluster_id)
    token = region_token(cache, region)
    clusters = list_clusters(region, token)
    if not clusters:
        raise ScnetError("config", "区域 %s 无集群" % region["name"])
    qs = {"strClusterIDList": str(clusters[0]["id"])}
    if job_id:
        qs["strJobId"] = _check_job_id(job_id)
    url = (_svc_url(region["hpc_url"], "hpc") + "/openapi/v2/jobs?"
           + urllib.parse.urlencode(qs))
    data = _http("GET", url, token=token)
    if data is not None and not isinstance(data, dict):
        raise ScnetError("query", "作业查询响应畸形(data 非对象)")
    return (data or {}).get("list") or []


def delete_job(job_id, cluster_id=""):
    """删除(停止)作业:DELETE /jobs,jobMethod=5,调度器ID,用户名:作业号:。
    用户拍板(第 19 轮):删除是安全方向运维操作,不走人工批准;
    但纳入 scnet.enabled 总闸 + job_id 白名单 + 审计。"""
    _, scnet = _config()
    if not isinstance(scnet, dict) or scnet.get("enabled") is not True:
        reason = "md_config scnet.enabled!=true(超算通道未启用,删除亦拒)"
        _audit({"action": "refused", "op": "delete", "job_id": repr(job_id),
                "reason": reason})
        raise ScnetError("gate", reason)
    try:
        job_id = _check_job_id(job_id)
    except ScnetError as e:
        _audit({"action": "refused", "op": "delete", "job_id": repr(job_id),
                "reason": str(e)})
        raise
    cache = authenticate()
    region = _pick_region(cache, cluster_id)
    token = region_token(cache, region)
    clusters = list_clusters(region, token)
    if not clusters:
        raise ScnetError("config", "区域 %s 无集群" % region["name"])
    info_map = "%s,%s:%s:;" % (clusters[0]["id"], region["user_name"], job_id)
    url = (_svc_url(region["hpc_url"], "hpc") + "/openapi/v2/jobs?"
           + urllib.parse.urlencode({"jobMethod": "5", "strJobInfoMap": info_map}))
    data = _http("DELETE", url, token=token)
    _audit({"action": "deleted", "job_id": str(job_id), "region": region["name"]})
    return data


# ---------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser(description="SCNet 开放平台客户端")
    sub = ap.add_subparsers(dest="cmd_name", required=True)
    sub.add_parser("auth")
    sub.add_parser("regions")
    sub.add_parser("clusters")
    up = sub.add_parser("upload")
    up.add_argument("--file", action="append", required=True)
    up.add_argument("--remote-case", required=True)
    sm = sub.add_parser("submit")
    sm.add_argument("--case-id", required=True)
    sm.add_argument("--prod-dir", default="")
    sm.add_argument("--file", action="append", default=[])
    sm.add_argument("--input-file", default="production.in")
    sm.add_argument("--queue", required=True)
    sm.add_argument("--nodes", type=int, default=1)
    sm.add_argument("--ppn", type=int, default=32)
    sm.add_argument("--walltime", default="24:00:00")
    sm.add_argument("--cmd", default="")
    sm.add_argument("--cluster-id", default="")
    sm.add_argument("--dry-run", action="store_true")
    apv = sub.add_parser("approve", help="人工批准一个 dry-run 计划(仅交互终端)")
    apv.add_argument("--plan-hash", default="")
    apv.add_argument("--set-pin", action="store_true",
                     help="设置/更换批准 PIN(仅交互终端)")
    qr = sub.add_parser("query")
    qr.add_argument("--cluster-id", default="")
    qr.add_argument("--job-id", default="")
    dl = sub.add_parser("delete", help="删除(停止)作业")
    dl.add_argument("--job-id", required=True)
    dl.add_argument("--cluster-id", default="")
    a = ap.parse_args()

    if a.cmd_name == "auth":
        cache = authenticate()
        print(json.dumps({cid: {"name": i["name"],
                                "token": i["token"][:20] + "...",
                                "expire_ms": i["expire_ms"]}
                          for cid, i in cache["regions"].items()},
                         ensure_ascii=False, indent=2))
    elif a.cmd_name == "regions":
        cache = authenticate()
        print(json.dumps(get_center(cache), ensure_ascii=False, indent=2))
    elif a.cmd_name == "clusters":
        cache = authenticate()
        region = _pick_region(cache)
        print(json.dumps(list_clusters(region, region_token(cache, region)), ensure_ascii=False, indent=2))
    elif a.cmd_name == "upload":
        _, scnet = _config()
        if not isinstance(scnet, dict) or scnet.get("enabled") is not True:
            reason = "md_config scnet.enabled!=true(超算通道未启用,上传亦拒)"
            _audit({"action": "refused", "op": "upload",
                    "remote_case": repr(a.remote_case), "reason": reason})
            print(reason, file=sys.stderr)
            return 3
        try:
            remote_case = _check_safe_name(a.remote_case, "remote_case")
            _check_work_root(str(scnet.get("work_root") or "YMY-AI"))
        except ScnetError as e:
            _audit({"action": "refused", "op": "upload",
                    "remote_case": repr(a.remote_case), "reason": str(e)})
            print(str(e), file=sys.stderr)
            return 3
        cache = authenticate()
        region = _pick_region(cache)
        remote_dir = "/".join([region["home_path"].rstrip("/"),
                               str(scnet.get("work_root") or "YMY-AI").strip("/"),
                               remote_case.strip("/")])
        for f in a.file:
            upload_file(region["efile_url"], region_token(cache, region), remote_dir, f)
            print("uploaded: %s -> %s" % (f, remote_dir))
        _audit({"action": "upload", "remote_case": remote_case,
                "files": [os.path.basename(f) for f in a.file],
                "remote_dir": remote_dir})
    elif a.cmd_name == "submit":
        files = list(a.file)
        if not files and a.prod_dir:
            for name in os.listdir(a.prod_dir):
                if name.endswith(".data") or name.startswith("ffield") \
                        or name == os.path.basename(a.input_file):
                    files.append(os.path.join(a.prod_dir, name))
        if not files:
            print("没有可上传的文件(给 --file 或 --prod-dir)", file=sys.stderr)
            return 2
        result = submit_job(a.case_id, files, a.queue, a.nodes, a.ppn, a.walltime,
                            input_file=a.input_file, cmd=a.cmd,
                            dry_run=a.dry_run, cluster_id=a.cluster_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("submitted") or result.get("dry_run") else 3
    elif a.cmd_name == "approve":
        if a.set_pin:
            if not sys.stdin.isatty():
                print("设置 PIN 必须在交互终端进行(检测到无 TTY,拒绝)", file=sys.stderr)
                return 3
            old = ""
            if os.path.exists(_pin_path()):
                old = getpass.getpass("旧 PIN: ")
            p1 = getpass.getpass("新批准 PIN(至少 %d 位,不得纯数字): " % PIN_MIN_LEN)
            p2 = getpass.getpass("再输一次: ")
            if p1 != p2:
                print("两次输入不一致", file=sys.stderr)
                return 3
            set_pin(p1, old_pin=old)
            print("PIN 已设置")
            return 0
        if not a.plan_hash:
            print("需 --plan-hash 或 --set-pin", file=sys.stderr)
            return 2
        ok = approve(a.plan_hash)
        print("已批准(30 分钟内一次性有效)" if ok else "未批准")
        return 0 if ok else 3
    elif a.cmd_name == "query":
        jobs = query_jobs(a.cluster_id, a.job_id)
        print(json.dumps(jobs, ensure_ascii=False, indent=2))
    elif a.cmd_name == "delete":
        delete_job(a.job_id, a.cluster_id)
        print("deleted: job %s" % a.job_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
