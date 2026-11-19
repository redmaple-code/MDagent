#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""租户 dsh web 实例管理器(form B 核心)。每用户按需拉起一个常驻 `dsh web`:
- 127.0.0.1:<随机端口>,mu_<user> 降权(SKIP_UNIX 时不降),DSH_HOME=用户空间;
- profile=web(用户真实 persona/插件,spike 用最小 web profile)+ per-instance --patch overlay 把
  dsh-remote-world 三提供者指向该用户在线节点的 world 通道(ws://<self>/v1/world/<node>,token=用户 mda_ 令牌);
- LLM 经计费代理(settings.yaml 已指);空闲 MDAGENT_WEB_IDLE_SEC 回收。
nginx auth_request 据 cookie 认用户 → ensure() 拿端口 → 路由浏览器到该实例。

【2026-09-23 审查加固】F1/F3/F5/F6/F8/F9 + X3/X4/X9:
- 生命周期:实例带 ready 标志;ensure 只复用「就绪且端口在听且节点匹配」的实例,否则拆旧拉新
  (挂起/节点变更不再钉死用户 30 分钟);启动失败/超时一律清理(pop+杀+关日志+删 overlay),不留残。
- 收尸:整进程组 killpg SIGTERM→宽限→SIGKILL 并 wait(不再只 SIGTERM 直系子、不再留孤儿孙进程);
  所有 kill/wait 都在 _lock 之外做(F7:不持锁做可能阻塞的收尸)。
- 端口:_free_port 的 TOCTOU 靠「启动失败即换端口重试」兜底,并把 EADDRINUSE 与真·启动即退区分诊断。
- 令牌卫生:overlay 明文含会话令牌,退出/回收即删(不留盘);revoke/rotate 经 reap_user 立即下线该实例。
- 多租户安全:MDAGENT_SPIKE_DSH_HOME 仅限 skip_unix 单用户测试,非 skip_unix 直接拒绝(防共享 home 串号)。"""
import os
import signal
import socket
import subprocess
import threading
import time

import accounts
import bio
import paths
import tenant_files

NODE_BIN = os.environ.get("MDAGENT_NODE", "node")
DSH_DIR = os.environ.get("MDAGENT_DSH_DIR", paths.code_dir())            # 含 node_modules/@deepseek-ai/dsh
DSH_BIN = os.path.join(DSH_DIR, "node_modules", "@deepseek-ai", "dsh", "lib", "bin.js")
SELF_WS = os.environ.get("MDAGENT_SELF_WS", "ws://127.0.0.1:8090")       # 本机 world 通道基址
GATE_URL = os.environ.get("MDAGENT_GATE_URL", "http://127.0.0.1:8090/v1/gate/exec")  # 写门链闸门(md_mcp sim_exec 用)
# 租户实例端口固定区(不用 ephemeral 随机):X1 的 nftables owner-match 靠此区间锁定,禁租户 uid 直连他人实例端口。
PORT_LO = int(os.environ.get("MDAGENT_WEB_PORT_LO", "21000"))
PORT_HI = int(os.environ.get("MDAGENT_WEB_PORT_HI", "21999"))
IDLE_SEC = int(os.environ.get("MDAGENT_WEB_IDLE_SEC", "1800"))
MAX_INSTANCES = int(os.environ.get("MDAGENT_WEB_MAX", "8"))
START_TIMEOUT = int(os.environ.get("MDAGENT_WEB_START_TIMEOUT", "40"))   # 冷启动+插件解析+setpriv,放宽到 40s
SPAWN_RETRIES = int(os.environ.get("MDAGENT_WEB_SPAWN_RETRIES", "2"))    # 端口被抢/启动即退时重试次数
KILL_GRACE = float(os.environ.get("MDAGENT_WEB_KILL_GRACE", "5"))        # SIGTERM 到 SIGKILL 的宽限秒数
TRUSTED_HOSTS = tuple(filter(None, os.environ.get("MDAGENT_WEB_TRUSTED_HOSTS", "127.0.0.1,localhost").split(",")))

_inst = {}   # username -> {proc, port, node_id, last, log, overlay, home, ready}
_lock = threading.Lock()


class _PortInUse(Exception):
    """启动即退且日志含 EADDRINUSE —— 可换端口重试。"""


class _AtCapacity(Exception):
    """已达并发实例上限。"""


def _skip_unix():
    return os.environ.get("MDAGENT_SKIP_UNIX") == "1"


def _dsh_home(username):
    # 【F9/多租户安全】共享 spike home 仅限单用户 skip_unix 测试:多租户(非 skip_unix)下所有用户
    # 会共写同一 overlay/日志且无 uid 隔离 → 令牌串号。fail-closed 拒绝,别让它漏进生产部署。
    sp = os.environ.get("MDAGENT_SPIKE_DSH_HOME")
    if sp:
        if not _skip_unix():
            raise RuntimeError("MDAGENT_SPIKE_DSH_HOME 仅限 skip_unix 单用户测试,禁止用于多租户部署")
        return sp
    return paths.user_dsh_home(username)


def _free_port():
    # 在固定区 [PORT_LO, PORT_HI] 内找空闲端口(X1:nftables 按此区间禁租户 uid 直连实例端口)。
    import random
    for _ in range(60):
        p = random.randint(PORT_LO, PORT_HI)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", p))
            s.close()
            return p
        except OSError:
            s.close()
    raise RuntimeError("实例端口区 %d-%d 无空闲(实例过多?)" % (PORT_LO, PORT_HI))


def _stable_port(username):
    """内测:同用户固定端口(实例重拉端口不变,隧道不跟着断);被占则回退随机。"""
    import zlib
    base = PORT_LO + (zlib.crc32(username.encode()) % (PORT_HI - PORT_LO - 1))
    sk = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sk.bind(("127.0.0.1", base)); sk.close(); return base
    except OSError:
        sk.close(); return _free_port()


def _port_up(port, timeout=0.3):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _write_overlay(username, node_id, world_token):
    # world_token 是 per-instance 短票(mdw_),非账号令牌:agent 读到也只能连自己的 world 通道(X2)。
    home = _dsh_home(username)
    url = "%s/v1/world/%s" % (SELF_WS, node_id or "n_none")
    body = ""
    for pid in ("remote-fs", "remote-subprocess", "remote-sandbox"):
        body += "- id: %s\n  config: {url: '%s', token: '%s'}\n" % (pid, url, world_token)
    # JSON is a YAML subset. Preserve the original remote providers and default agent.
    import json
    for entry in bio.overlay_entries(username, node_id):
        body += "- " + json.dumps(entry, ensure_ascii=False) + "\n"
    return tenant_files.write_private(home, "web-overlay.yml", body, _file_owner(username))


def _file_owner(username):
    if not _skip_unix():
        import grp
        import pwd
        import provision
        return (pwd.getpwnam(provision.unix_name(username)).pw_uid,
                grp.getgrnam(provision.SHARE_GROUP).gr_gid)
    return None


def _argv(username, port, overlay):
    argv = [NODE_BIN, DSH_BIN, "--profile", "web", "--patch", overlay,
            "--host", "127.0.0.1", "--port", str(port)]
    for host in TRUSTED_HOSTS:
        argv += ["--trusted-host", host.strip()]
    if _skip_unix():
        return argv
    import grp
    import pwd
    import provision
    uid = pwd.getpwnam(provision.unix_name(username)).pw_uid
    gid = grp.getgrnam(provision.SHARE_GROUP).gr_gid
    return ["setpriv", "--reuid=%d" % uid, "--regid=%d" % gid, "--clear-groups"] + argv


def _read_proxy_key(home):
    """从 .credentials.yaml 读计费代理令牌(provision.refresh_credentials 写的单行
    'MDEXP_PROXY_KEY: mxp_...')。settings.yaml 的 apiKeyEnv=MDEXP_PROXY_KEY 从进程 env 读它,
    而降权 spawn(env=_env)会替换整份环境 → 必须在此显式回填,否则 LLM 请求带空令牌被代理判 401
    「代理令牌无效」。dsh 起 MCP/subprocess 子进程时 scrubbedParentEnv 会剔除名字含 KEY 的变量,
    故此令牌只在 dsh 主进程可见(供 LLM 路由),不泄漏给 agent 的 bash/MCP。"""
    try:
        for line in tenant_files.read_private(home, ".credentials.yaml").splitlines():
            k, sep, v = line.partition(":")
            if sep and k.strip() == "MDEXP_PROXY_KEY":
                return v.strip()
    except (OSError, ValueError):
        pass
    return None


def _env(username):
    home = _dsh_home(username)
    env = {
        "PATH": os.path.dirname(NODE_BIN) + ":/usr/local/bin:/usr/bin:/bin" if "/" in NODE_BIN else "/usr/local/bin:/usr/bin:/bin",
        "HOME": home,
        "DSH_HOME": home,
        "MDSIM_DATA_ROOT": paths.user_data_dir(username) if not _skip_unix() else home,
        "DSH_PERMISSION_MODE": "workspace-write",
        "LANG": "zh_CN.UTF-8",
    }
    tok = _read_proxy_key(home)
    if tok:
        env["MDEXP_PROXY_KEY"] = tok   # 计费代理令牌(settings apiKeyEnv 从此读);子进程会被 scrub
    return env


def _write_gate_file(username, world_token):
    """写 md_mcp 写门链配置(gate_url + world 短票)到 dsh-home/.mdexp-gate.json(0600)。
    走文件而非 env:dsh 起 MCP 子进程用 dsh-subprocess 的 scrubbedParentEnv 剔除名字含
    KEY/TOKEN/SECRET/PASSWORD 的变量,MDEXP_WORLD_TOKEN 会被剔;md_mcp 从 $HOME 定位本文件读。
    短票不留盘——实例消亡即删(同 overlay,F2)。"""
    import json as _json
    body = _json.dumps({"gate_url": GATE_URL, "world_token": world_token})
    return tenant_files.write_private(_dsh_home(username), ".mdexp-gate.json", body, _file_owner(username))


# ---------- 收尸(全部在 _lock 之外调用:F7 不持锁做可能阻塞的 kill/wait) ----------

def _kill_proc(proc):
    """【F3/F8】整进程组收口:killpg SIGTERM → 宽限 → SIGKILL,并 wait 收尸(避免孤儿孙进程/僵尸)。
    proc 以 start_new_session=True 起,是自身进程组组长 → killpg 一次带走 dsh + 其 mcp/node 子孙。"""
    if proc is None:
        return
    if proc.poll() is not None:
        try:
            proc.wait(timeout=0.5)
        except Exception:
            pass
        return
    try:
        pgid = os.getpgid(proc.pid)
    except Exception:
        pgid = None
    try:
        if pgid is not None:
            os.killpg(pgid, signal.SIGTERM)
        else:
            proc.terminate()
    except Exception:
        pass
    deadline = time.time() + KILL_GRACE
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    if proc.poll() is None:
        try:
            if pgid is not None:
                os.killpg(pgid, signal.SIGKILL)
            else:
                proc.kill()
        except Exception:
            pass
    try:
        proc.wait(timeout=2)
    except Exception:
        pass


def _cleanup_inst(i):
    """杀整树 + 关日志 + 删 overlay(短票明文文件不留盘,F2)+ 吊销 world 短票(X2)。锁外调用。"""
    if not i:
        return
    _kill_proc(i.get("proc"))
    try:
        if i.get("log"):
            i["log"].close()
    except Exception:
        pass
    try:
        ov = i.get("overlay")
        if ov and i.get("home"):
            tenant_files.unlink_private(i["home"], "web-overlay.yml")
    except Exception:
        pass
    try:
        if i.get("home"):
            tenant_files.unlink_private(i["home"], ".mdexp-gate.json")
    except Exception:
        pass
    try:
        accounts.revoke_world_token(i.get("world_token"))   # 【X2】实例消亡即吊销其 world 短票
    except Exception:
        pass


def _drop(username, proc):
    """把 username 名下、且正是 proc 的登记摘掉并收尸;若已被替换,则只收尸这个孤儿 proc。锁外调用。"""
    victim = None
    with _lock:
        i = _inst.get(username)
        if i is not None and i["proc"] is proc:
            victim = _inst.pop(username, None)
    if victim is not None:
        _cleanup_inst(victim)
    else:
        _kill_proc(proc)


def _log_has_eaddrinuse(logf):
    try:
        fd = logf.fileno()
        tail = os.pread(fd, 4096, max(0, os.fstat(fd).st_size - 4096)).decode("utf-8", "ignore").lower()
        return "eaddrinuse" in tail or "address already in use" in tail
    except Exception:
        return False


# ---------- 拉起 / 等就绪 ----------

def _await_ready(username, proc, port, home):
    """锁外等实例监听端口;失败/超时一律 _drop(清理),不留残(F1/X3)。"""
    deadline = time.time() + START_TIMEOUT
    while time.time() < deadline:
        rc = proc.poll()
        if rc is not None:
            with _lock:
                i = _inst.get(username)
                logf = i.get("log") if i and i["proc"] is proc else None
            ead = _log_has_eaddrinuse(logf)
            _drop(username, proc)
            if ead:
                raise _PortInUse("EADDRINUSE port=%d" % port)          # X5:换端口重试
            raise RuntimeError("web 实例启动即退出(rc=%s);看 %s/web.log" % (rc, home))
        if _port_up(port):
            with _lock:
                i = _inst.get(username)
                if i is not None and i["proc"] is proc:
                    i["ready"] = True
                    i["last"] = time.time()
            return port
        time.sleep(0.3)
    _drop(username, proc)                                              # X3:超时清理,不钉死用户
    raise RuntimeError("web 实例 %ds 内未监听端口;看 %s/web.log" % (START_TIMEOUT, home))


def _watch_exit(proc, logf):
    """实例退出即把 rc/signal 写进 web.log —— 定位无 traceback 的崩溃(SIGKILL=-9 / SIGSEGV=-11 / SIGABRT=-6)。"""
    try:
        rc = proc.wait()
        sig = (" SIGNAL=%d" % (-rc)) if rc < 0 else ""
        logf.write("\n[tenants] 实例 pid=%d 退出 rc=%d%s at %s\n" % (proc.pid, rc, sig, time.strftime("%Y-%m-%d %H:%M:%S")))
        logf.flush()
    except Exception:
        pass


def _spawn_and_wait(username, node_id):
    """锁内 spawn(快)+ 注册占位 ready=False,锁外等就绪。并发同用户请求会命中已注册实例并共同等待,
    不会重复 spawn(MAX 检查与注册同处一次持锁,防并发绕过)。"""
    with _lock:
        i = _inst.get(username)
        bio_signature = bio.signature(username)
        if (i is not None and i["proc"].poll() is None and i.get("node_id") == node_id
                and i.get("bio_signature") == bio_signature):
            # 另一并发请求已拉起:复用它,落到锁外一起等就绪
            proc, port, home = i["proc"], i["port"], i.get("home")
        else:
            if i is not None and i["proc"].poll() is None:
                raise RuntimeError("BIO_PROFILE_CHANGED_RETRY")
            alive = sum(1 for x in _inst.values() if x["proc"].poll() is None)
            if alive >= MAX_INSTANCES:
                raise _AtCapacity()
            port = _stable_port(username)
            world_token = accounts.mint_world_token(username, node_id)   # 【X2】per-instance 短票
            home = _dsh_home(username)
            logf, overlay = None, None
            try:
                overlay = _write_overlay(username, node_id, world_token)
                _write_gate_file(username, world_token)   # Existing LAMMPS gate remains separate.
                logf = tenant_files.open_log(home)
                proc = subprocess.Popen(_argv(username, port, overlay), env=_env(username),
                                        cwd=home, stdout=logf, stderr=subprocess.STDOUT,
                                        start_new_session=True)
            except Exception:
                _cleanup_inst({"log": logf, "overlay": overlay, "home": home, "world_token": world_token})
                raise
            _inst[username] = {"proc": proc, "port": port, "node_id": node_id,
                               "last": time.time(), "log": logf, "overlay": overlay,
                               "home": home, "ready": False, "world_token": world_token,
                               "bio_signature": bio_signature}
            threading.Thread(target=_watch_exit, args=(proc, logf), daemon=True).start()
    return _await_ready(username, proc, port, home)


def ensure(username, node_id):
    """拉起或复用该用户的 web 实例,返回端口。world 通道短票在内部铸(X2:不收账号令牌)。"""
    reuse = None
    to_cleanup = []
    with _lock:
        i = _inst.get(username)
        bio_signature = bio.signature(username)
        if (i is not None and i["proc"].poll() is None and i.get("node_id") == node_id
                and i.get("bio_signature") == bio_signature):
            if i.get("ready") and _port_up(i["port"]):
                i["last"] = time.time()
                accounts.touch_world_token(i.get("world_token"))     # 【X2】实例活着,短票续期
                return i["port"]                                      # 复用就绪实例
            reuse = (i["proc"], i["port"], i.get("home"))             # 正在启动:锁外等它就绪
        else:
            if i is not None:
                to_cleanup.append(_inst.pop(username, None))          # 死/节点变(如 n_none→真节点):拆旧(X4)
            to_cleanup += _reap_locked()                             # 顺带摘死/闲(锁外收尸)
    for x in to_cleanup:
        _cleanup_inst(x)                                             # F7:锁外收尸
    if reuse is not None:
        return _await_ready(username, *reuse)
    err = None
    for _try in range(SPAWN_RETRIES + 1):
        try:
            return _spawn_and_wait(username, node_id)
        except _AtCapacity:
            raise RuntimeError("已达并发实例上限(%d)" % MAX_INSTANCES)
        except _PortInUse as e:
            err = e
            continue                                                 # X5:换端口重试
        except Exception as e:
            err = e
            break
    raise RuntimeError("web 实例启动失败: %s" % err)


def _reap_locked():
    """摘除死/闲实例并返回它们(锁内只摘不杀;收尸由调用方在锁外做,F7)。"""
    now = time.time()
    popped = []
    for user, i in list(_inst.items()):
        dead = i["proc"].poll() is not None
        idle = now - i["last"] > IDLE_SEC
        if dead or idle:
            popped.append(_inst.pop(user))
    return popped


def reap():
    with _lock:
        popped = _reap_locked()
    for x in popped:
        _cleanup_inst(x)


def reap_user(username):
    """【X9】revoke/token 轮换联动:立即下线该用户实例(删 overlay 里的失效令牌)。返回是否有实例被拆。"""
    with _lock:
        x = _inst.pop(username, None)
    if x is not None:
        _cleanup_inst(x)
        return True
    return False


def status():
    with _lock:
        return {u: {"port": i["port"], "node_id": i["node_id"],
                    "alive": i["proc"].poll() is None, "ready": i.get("ready", False),
                    "idle_s": round(time.time() - i["last"])}
                for u, i in _inst.items()}


def preview_transport(username, node_id):
    """Private lookup only; never spawn a tenant or return this to a browser."""
    from fastapi import HTTPException
    signature=bio.signature(username)
    with _lock:
        instance=_inst.get(username)
        if (not instance or not instance.get('ready') or instance['proc'].poll() is not None
            or instance['node_id']!=node_id or instance.get('bio_signature')!=signature):
            raise HTTPException(503,'PREVIEW_TENANT_NOT_READY')
        token=instance.get('world_token');record=accounts.verify_world_token(token)
        if not record or record['username']!=username or record['node_id']!=node_id:raise HTTPException(403,'BIO_WORLD_TOKEN_REQUIRED')
        instance['last']=time.time()
        return dict(port=instance['port'],world_token=token,node_id=node_id,bio_signature=signature)


def shutdown_all():
    with _lock:
        items = list(_inst.values())
        _inst.clear()
    for x in items:
        _cleanup_inst(x)
