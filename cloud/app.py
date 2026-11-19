#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mdagent-api:多租户 MD 智能体 HTTP API。
认证:Authorization: Bearer <token>(仅头部;?token= 会明文落日志,已禁用)。
启动:uvicorn cloud.app:app --app-dir /opt/mdagent/code --host 127.0.0.1 --port 8090"""
import json
import os
import sys

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import accounts
import bio
import bridge
import gate
import evo_graph
import history
import nodes
import pages
import paths
import provision
import jobqueue as jq
import ledger
import llmproxy
import relay
import runner
import runs
import tenants
import watcher
import webentry

app = FastAPI(title="mdagent-api", docs_url=None, redoc_url=None, openapi_url=None)
webentry.mount(app)
llmproxy.mount(app)  # /llm/<provider>/… 计费代理
if os.environ.get("MDAGENT_HPC_SECRET"):
    import hpc
    hpc.mount_hpc(app)  # Existing SSH/Slurm module; no new credential is invented.


@app.on_event("startup")
def _startup():
    jq.init()
    os.makedirs(paths.jobs_dir(), exist_ok=True)
    provision.ensure_shared_evolution()
    import threading as _th
    def _reaper():
        import time as _t
        while True:
            _t.sleep(60)
            try:
                tenants.reap()
            except Exception:
                pass
    _th.Thread(target=_reaper, daemon=True).start()
    # 清扫历史残留的 per-run patch(含死 token;新版 runner 用后即删,这是兜底,红队ι#4)
    import glob
    for p in glob.glob(os.path.join(paths.users_dir(), "*", "dsh-home",
                                    "localfs.patch.yml")):
        try:
            os.unlink(p)
        except OSError:
            pass
    # 【B-H1】一次性把代理化改造前开通的老用户 credentials 从真 key 迁到代理令牌(真 key 移出租户 DSH_HOME)。
    # 后台线程跑,不阻塞启动;ensure_proxy_credentials 幂等(已迁移的跳过)。
    def _cred_sweep():
        try:
            for u in accounts._load().get("users", []):
                if u.get("status") == "active":
                    try:
                        provision.ensure_proxy_credentials(u["username"])
                    except Exception:
                        pass
        except Exception:
            pass
    _th.Thread(target=_cred_sweep, daemon=True).start()


def _token_from(request: Request):
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    # 不接受 ?token=:query 串会明文落 nginx access.log / systemd journal
    return None


def current_user(request: Request):
    # 【X2】双认:程序化(CLI/TUI)走 Authorization: Bearer <API 令牌 mda_>;
    # 浏览器走 HttpOnly Cookie mdexp_session=<会话令牌 mds_>(与 API 令牌分离,可独立作废)。
    tok = _token_from(request)
    u = accounts.by_token(tok) if tok else None
    if not u:
        sc = request.cookies.get("mdexp_session")
        u = accounts.by_session_token(sc) if sc else None
    if not u:
        raise HTTPException(401, "unauthorized: 需要有效 token")
    return u


def admin_user(u=Depends(current_user)):
    if u.get("role") != "admin":
        raise HTTPException(403, "需要管理员权限")
    return u


def account_user(u=Depends(current_user)):
    """Integration credentials cannot manage the account's primary credentials."""
    if u.get("token_kind") == "external-api":
        raise HTTPException(403, "外部 API 令牌不能管理账号凭据")
    return u


class ApplyBody(BaseModel):
    username: str
    contact: str = ""
    reason: str = ""
    provider: str = ""
    api_key: str = ""
    password: str
    invite_code: str = ""


class ChatBody(BaseModel):
    project: str
    message: str
    policy: str = ""


class LoginBody(BaseModel):
    username: str
    password: str


class ApiKeyBody(BaseModel):
    provider: str
    api_key: str


class PasswdBody(BaseModel):
    password: str



@app.get("/manual", response_class=HTMLResponse)
def manual_page():
    """使用手册(静态 HTML,登录页可点开)。"""
    try:
        with open("/opt/mdagent/static/manual.html", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    except OSError:
        return HTMLResponse("<h1>手册暂不可用</h1>", status_code=404)

@app.post("/v1/apply")
def apply(body: ApplyBody, request: Request):
    import time as _t
    ip = _client_ip(request); now = _t.time()
    _prune_fails(_apply_fails, now)
    af = [t for t in _apply_fails.get(ip, []) if now - t < 600]
    if len(af) >= 10:   # N4/N5/#1:注册 IP 限速,封住 pbkdf2 放大与开放注册 DoS
        raise HTTPException(429, "注册请求过于频繁,请稍后再试")
    af.append(now); _apply_fails[ip] = af
    ok, msg = accounts.apply(body.username.strip(), body.contact,
                             body.reason, body.provider, body.api_key,
                             body.password, body.invite_code.strip())
    if not ok:
        raise HTTPException(400, msg)
    return {"status": "pending", "msg": msg}


_login_fails = {}  # "ip|username" -> [失败 ts](内存级,重启清零;5 次/10 分钟锁)
_apply_fails = {}  # ip -> [ts](注册 IP 限速,10 次/10 分)
_FAILS_MAX = 20000

def _prune_fails(d, now):
    """N1:清掉全过期的键;仍超上限则整表清空(防未认证内存 DoS)。"""
    if len(d) < _FAILS_MAX:
        return
    for k in [k for k, v in list(d.items()) if not any(now - t < 600 for t in v)]:
        d.pop(k, None)
    if len(d) >= _FAILS_MAX:
        d.clear()

def _client_ip(request):
    # N2:只信 nginx 用 $remote_addr 覆盖的 X-Real-IP;不信客户端可伪造的 X-Forwarded-For
    xr = request.headers.get("x-real-ip", "").strip()
    return xr or (request.client.host if request.client else "?")


@app.post("/v1/login")
def login(body: LoginBody, request: Request, response: Response):
    """用户名+密码 → 新 token(旧的立即作废;token 库里只存 sha256,拿不出旧明文)。
    审批通过前/密码错误都 401(不区分,防用户名枚举)。"""
    import time as _time
    name = (body.username or "").strip()
    now = _time.time()
    _prune_fails(_login_fails, now)
    fkey = _client_ip(request) + "|" + name   # F4:按 IP+名 计数,防定向锁死
    fails = [t for t in _login_fails.get(fkey, []) if now - t < 600]
    if len(fails) >= 5:
        raise HTTPException(429, "失败次数过多,10 分钟后再试")
    u = accounts.verify_login(name, body.password or "")
    if not u:
        fails.append(now)
        _login_fails[fkey] = fails
        raise HTTPException(401, "用户名或密码错误(或账号未审批通过)")
    _login_fails.pop(fkey, None)
    # 【X2 凭据拆分】API 令牌(mda_)供装机/CLI(body 返回,install.sh 解析);
    # 浏览器会话令牌(mds_)单独铸,只进 HttpOnly Cookie。二者分离:登录轮换 session 不动 API 令牌
    # (不再踢已装 CLI),Cookie 被盗 ≠ API 令牌被盗。world overlay 用的是 per-instance 短票(见 tenants)。
    token = accounts.rotate_token(name)
    session = accounts.rotate_session_token(name)
    # 生产走 TLS 应 Secure;spike 明文时设 MDAGENT_COOKIE_INSECURE=1。
    response.set_cookie("mdexp_session", session, httponly=True, samesite="lax",
                        secure=(os.environ.get("MDAGENT_COOKIE_INSECURE") != "1"), max_age=7 * 86400, path="/")
    return {"username": name, "token": token,
            "msg": "登录成功(API 令牌已轮换;浏览器会话另发 Cookie)"}


@app.post("/v1/logout")
def logout(request: Request, response: Response):
    """登出:作废浏览器会话令牌 + 清 Cookie(不动 API 令牌,CLI 不受影响)。"""
    sc = request.cookies.get("mdexp_session")
    u = accounts.by_session_token(sc) if sc else None
    if u:
        accounts.clear_session_token(u["username"])
    response.delete_cookie("mdexp_session", path="/")
    return {"ok": True}


@app.post("/v1/me/apikey")
def me_apikey(body: ApiKeyBody, u=Depends(account_user)):
    """自助改 LLM provider/api_key:账本 + .credentials.yaml + settings.yaml 三处同步。"""
    ok, msg = accounts.update_apikey(u["username"], body.provider,
                                     body.api_key.strip())
    if not ok:
        raise HTTPException(400, msg)
    if not provision.refresh_credentials(u["username"]):
        raise HTTPException(500, "凭据文件同步失败,请联系管理员")
    try:
        tenants.reap_user(u["username"])   # A3-1:代理令牌已轮换,下线旧实例,下次 /v1/auth 用新令牌+新 settings 重拉
    except Exception:
        pass
    return {"ok": True, "provider": body.provider}


@app.get("/v1/me")  # BYOK /v1/me
def me(u=Depends(current_user)):
    """当前用户档案(不含真 key,只报是否已配 provider key)。"""
    return {"username": u["username"], "role": u.get("role", "user"),
            "provider": u.get("provider", ""), "has_key": bool(u.get("api_key") or ""),
            "membership": accounts.effective_membership(u)[0],
            "membership_tier": u.get("membership", "free"),
            "membership_expire": u.get("membership_expire"),
            "membership_expired": accounts.effective_membership(u)[2],
            "points": accounts.safe_points(u)}


@app.post("/v1/me/passwd")
def me_passwd(body: PasswdBody, u=Depends(account_user)):
    """token 持有者可设/改密码(老账号无密码的初始设置也走这里)。"""
    ok, msg = accounts.set_password(u["username"], body.password)
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True}


@app.post("/v1/me/token/rotate")
def me_token_rotate(u=Depends(account_user)):
    """吊销当前 token 并铸新 token(怀疑泄露时用;新 token 仅此一次返回)。"""
    token = accounts.rotate_token(u["username"])
    if not token:
        raise HTTPException(400, "无此活跃账号")
    return {"token": token, "msg": "旧 token 已作废,请立即保存新 token"}


@app.get("/v1/admin/applications")
def list_applications(_=Depends(admin_user)):
    return {"applications": [
        {k: u[k] for k in ("username", "contact", "reason", "provider",
                           "created_ts")}
        for u in accounts.list_applications()]}


@app.get("/v1/admin/users")
def list_users(_=Depends(admin_user)):
    return {"users": [
        dict({k: u.get(k) for k in ("username", "role", "status", "provider",
                                    "created_ts", "approved_ts", "membership_expire")},
             membership=accounts.effective_membership(u)[0],
             membership_tier=u.get("membership", "free"),
             membership_expired=accounts.effective_membership(u)[2],
             points=accounts.safe_points(u))
        for u in accounts.list_users()]}


@app.post("/v1/admin/approve/{username}")
def approve(username: str, _=Depends(admin_user)):
    token, err = accounts.approve(username)
    if err:
        raise HTTPException(400, err)
    try:
        provision.provision(username)
    except Exception as e:
        accounts.revoke(username)
        raise HTTPException(500, "空间开通失败,已回滚: %s" % e)
    return {"username": username, "token": token,
            "msg": "token 仅此一次返回,请立即保存"}


class MembershipBody(BaseModel):
    tier: str
    days: int = 0


class PointsBody(BaseModel):
    delta: float
    reason: str = ""


@app.post("/v1/admin/membership/{username}")
def admin_set_membership(username: str, body: MembershipBody, _=Depends(admin_user)):
    ok, err = accounts.set_membership(username, body.tier, body.days)
    if not ok:
        raise HTTPException(400, err)
    return {"username": username, "membership": body.tier}


@app.post("/v1/admin/points/{username}")
def admin_adjust_points(username: str, body: PointsBody, admin=Depends(admin_user)):
    ok, err, bal = accounts.adjust_points(username, body.delta, body.reason, admin["username"])
    if not ok:
        raise HTTPException(400, err)
    return {"username": username, "points": bal}


class InviteBody(BaseModel):
    days: int = 7
    max_uses: int = 1
    note: str = ""


@app.post("/v1/admin/invites")
def admin_create_invite(body: InviteBody, admin=Depends(admin_user)):
    code = accounts.create_invite(admin["username"], body.days, body.max_uses, body.note)
    return {"code": code}


@app.get("/v1/admin/invites")
def admin_list_invites(_=Depends(admin_user)):
    return {"invites": accounts.list_invites()}


@app.post("/v1/admin/deny/{username}")
def deny(username: str, _=Depends(admin_user)):
    ok, err = accounts.deny(username)
    if not ok:
        raise HTTPException(400, err)
    return {"username": username, "status": "denied"}


@app.post("/v1/admin/revoke/{username}")
def revoke(username: str, _=Depends(admin_user)):
    ok, err = accounts.revoke(username)
    if not ok:
        raise HTTPException(400, err)
    # 【X9】撤销即下线其在跑 web 实例(否则最长 IDLE_SEC≈30min 内仍可用旧实例/旧令牌)
    try:
        tenants.reap_user(username)
    except Exception:
        pass
    return {"username": username, "status": "revoked"}


@app.get("/v1/admin/usage")
def all_usage(_=Depends(admin_user)):
    return {"usage": [accounts.usage(u["username"])
                      for u in accounts.list_users() if u["status"] == "active"]}


@app.post("/v1/admin/refresh_models")
def refresh_models(_=Depends(admin_user)):
    """模型换代:按 PROVIDERS 当前定义重写所有 active 用户的 settings.yaml。
    先 ensure_proxy_credentials(B-H2 修):否则只刷 settings 会让老用户 settings 指代理、credentials 却无
    MDEXP_PROXY_KEY → dsh 取不到令牌 401 被打断,且真 key 仍滞留 DSH_HOME(半迁移)。"""
    done = []
    for u in accounts.list_users():
        if u["status"] != "active":
            continue
        provision.ensure_proxy_credentials(u["username"])       # 幂等:已迁移跳过
        if provision.refresh_model_settings(u["username"]):
            done.append(u["username"])
    return {"refreshed": done}


@app.post("/v1/chat", status_code=202)
async def chat(body: ChatBody, u=Depends(current_user)):
    if not history.valid_project(body.project):
        raise HTTPException(400, "project 名非法(≤64 字符,字母数字-_或中文)")
    if not body.message.strip():
        raise HTTPException(400, "message 不能为空")
    if len(body.message) > 32000:
        raise HTTPException(400, "message 过长(>32K)")
    if jq.active_job_of(u["username"]):
        raise HTTPException(429, "已有进行中的任务,等它完成")
    if jq.free_mb() < jq.MIN_FREE_MB:
        raise HTTPException(503, "服务器内存不足,稍后重试")
    if not accounts.rate_ok(u["username"]):
        raise HTTPException(429, "超出每小时 %d 次限额" % accounts.RATE_LIMIT_PER_HOUR)
    job = await jq.submit(u["username"], body.project, body.message,
                          runner.run_chat, body.policy)
    return {"job_id": job["id"], "position": jq.position(job["id"])}


@app.get("/v1/jobs/{job_id}")
def job_status(job_id: str, u=Depends(current_user)):
    j = jq.get_job(job_id)
    if not j:
        raise HTTPException(404, "无此任务")
    if j["user"] != u["username"] and u.get("role") != "admin":
        raise HTTPException(403, "不能看别人的任务")
    out = {"id": j["id"], "project": j["project"], "status": j["status"],
           "created_ts": j["created_ts"], "finished_ts": j["finished_ts"]}
    if j["status"] == "queued":
        out["position"] = jq.position(j["id"])
    if j["status"] == "done":
        out["reply"] = j["reply"]
    else:
        out["partial"] = j.get("partial", "")
        out["thinking"] = j.get("thinking", "")
        out["activity"] = j.get("activity", "")
    if j["error"]:
        out["error"] = j["error"]
    return out


@app.get("/v1/conversations")
def conversations(u=Depends(current_user)):
    return {"projects": history.list_projects(u["username"]),
            "archived": history.archived(u["username"])}


@app.get("/v1/conversations/{project}")
def conversation(project: str, u=Depends(current_user),
                 turns: int = Query(50, ge=1, le=200)):
    if not history.valid_project(project):
        raise HTTPException(400, "project 名非法")
    return {"project": project,
            "turns": history.recent(u["username"], project, turns)}


@app.post("/v1/conversations/{project}/archive")
def conversation_archive(project: str, u=Depends(current_user)):
    """归档项目:会话+项目空间移入 _archived/(可恢复,不是删除)。"""
    if not history.valid_project(project):
        raise HTTPException(400, "project 名非法")
    if jq.active_job_of(u["username"]):
        raise HTTPException(409, "有任务在跑,等它结束再归档(防信封/项目空间中途消失)")
    try:
        return history.archive(u["username"], project)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))


@app.post("/v1/conversations/{project}/restore")
def conversation_restore(project: str, u=Depends(current_user)):
    """恢复该项目最近一次归档;同名已存在时 409(不覆盖)。"""
    if not history.valid_project(project):
        raise HTTPException(400, "project 名非法")
    if jq.active_job_of(u["username"]):
        raise HTTPException(409, "有任务在跑,等它结束再恢复(防任务中途写回旧会话)")
    try:
        return history.restore(u["username"], project)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except FileExistsError as e:
        raise HTTPException(409, str(e))


@app.get("/v1/memory")
def memory(u=Depends(current_user)):
    """用户私有记忆视图:会话存档列表 + 项目空间。原子库是共享进化层,见 /v1/evolution。"""
    return {"projects": history.list_projects(u["username"]),
            "note": "经验原子与行为策略属共享进化层,见 /v1/evolution"}


@app.get("/v1/evolution")
def evolution(u=Depends(current_user)):
    """共享进化层视图(全员同一份)。"""
    d = paths.shared_evolution_dir()
    out = {"strategies": [], "proposals": []}
    bs = os.path.join(d, "behavior_strategies.json")
    if os.path.exists(bs):
        try:
            with open(bs, encoding="utf-8") as f:
                out["strategies"] = json.load(f).get("strategies", [])
        except ValueError:
            pass
    pd = os.path.join(d, "proposals")
    if os.path.isdir(pd):
        out["proposals"] = sorted(os.listdir(pd))[-50:]
    return out


@app.get("/v1/usage")
def usage(u=Depends(current_user)):
    out = accounts.usage(u["username"])
    out["tokens"] = ledger.summary(u["username"])   # 今日/本周/累计/分模型 token 与费用(来自计费代理台账)
    return out


@app.get("/v1/goal")
def goal(project: str = Query("default"), u=Depends(current_user)):
    """目标面板数据源:读该项目的 GOAL.md(agent 按 persona 纪律自维护)。
    文件属 mu_<u>,本进程 root 可读;≤32KB 截断。"""
    if not history.valid_project(project):
        raise HTTPException(400, "project 名非法")
    p = os.path.join(paths.user_projects_dir(u["username"]), project, "GOAL.md")
    if not os.path.isfile(p):
        return {"exists": False}
    with open(p, encoding="utf-8", errors="replace") as f:
        content = f.read(32768)
    return {"exists": True, "content": content,
            "mtime": os.stat(p).st_mtime}


@app.get("/v1/health")
def health():
    return {"ok": True, "free_mb": jq.free_mb(), "protocol": relay.PROTOCOL, "min_protocol": relay.MIN_PROTOCOL}


# ---------- 管理员控制台(静态页;数据接口仍走 Bearer,token 只进 sessionStorage) ----------
_ADMIN_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>mdagent 管理台</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 body{margin:0;background:#0F1115;color:#E6EAF2;font:14px/1.6 -apple-system,"Segoe UI",sans-serif}
 header{background:#161B27;padding:10px 18px;display:flex;gap:14px;align-items:baseline}
 h1{font-size:16px;margin:0;color:#C9D1E0}
 .pill{font-size:12px;padding:1px 8px;border-radius:999px;background:#1C2947;color:#5686FE}
 main{padding:16px 18px;display:grid;gap:16px;max-width:1100px}
 section{background:#12151D;border:1px solid #232A3B;border-radius:12px;padding:14px 16px}
 h2{font-size:13px;color:#7C8BA8;margin:0 0 10px;font-weight:600}
 table{width:100%;border-collapse:collapse}
 th,td{text-align:left;padding:5px 8px;border-bottom:1px solid #232A3B;font-size:13px}
 th{color:#5F6B7A;font-weight:500}
 button{background:#2E4A8F;color:#fff;border:0;border-radius:8px;padding:3px 12px;cursor:pointer;margin-right:6px}
 button.no{background:#3A2028}
 .ok{color:#3FB950}.bad{color:#F85149}.dim{color:#5F6B7A}
 #tok{width:100%;box-sizing:border-box;background:#0F1115;border:1px solid #2E4A8F;border-radius:8px;color:#E6EAF2;padding:8px}
</style></head><body>
<header><h1>☁ mdagent 管理台</h1><span class="pill" id="health">…</span>
 <span class="dim" style="margin-left:auto;font-size:12px" id="ts"></span></header>
<main>
 <section id="login"><h2>管理员 token(仅存本页 sessionStorage,不进 URL/日志)</h2>
  <input id="tok" placeholder="mda_..." type="password">
  <p><button onclick="saveTok()">登录</button></p></section>
 <section id="apps" style="display:none"><h2>待审批申请</h2><div id="apps-body"></div></section>
 <section id="invites" style="display:none"><h2>邀请码 <button data-act='newinvite'>+ 生成</button></h2><div id="invites-body"></div></section>
 <section id="users" style="display:none"><h2>用户与用量</h2><div id="users-body"></div></section>
</main>
<script>
let T=sessionStorage.getItem("mda_admin")||"";
function saveTok(){T=document.getElementById("tok").value.trim();
 sessionStorage.setItem("mda_admin",T);tick()}
const ADMIN_P=location.pathname.split('/v1/admin')[0];
async function j(m,url,body){const o={method:m,credentials:'include',headers:{}};if(T)o.headers.Authorization="Bearer "+T;if(body){o.headers['Content-Type']='application/json';o.body=JSON.stringify(body);}const r=await fetch(ADMIN_P+url,o);
 if(r.status===401||r.status===403){throw new Error("token 无效或非管理员")}
 const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.detail||("HTTP "+r.status));return d}
function esc(s){return String(s==null?"":s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
function fmt(ts){return ts?new Date(ts*1000).toLocaleString():"-"}
async function approve(name,ok){try{const r=await j("POST","/v1/admin/"+(ok?"approve/":"deny/")+encodeURIComponent(name));
 if(ok&&r.token)alert("已批准 "+name+"\\ntoken(只显示一次,立即发给用户):\\n"+r.token);tick()}catch(e){alert(e.message)}}
async function revokeU(name){if(!confirm("吊销 "+name+" 的 token?"))return;
 try{await j("POST","/v1/admin/revoke/"+encodeURIComponent(name));tick()}catch(e){alert(e.message)}}
async function tick(){
 try{
  const [h,us,ap,ug,iv]=await Promise.all([j("GET","/v1/health"),j("GET","/v1/admin/users"),
   j("GET","/v1/admin/applications"),j("GET","/v1/admin/usage"),j("GET","/v1/admin/invites")]);
  document.getElementById("login").style.display="none";
  document.getElementById("health").innerHTML='● 服务正常 · 余量 '+h.free_mb+'MB';
  const useMap={};(ug.usage||[]).forEach(x=>useMap[x.username]=x);
  let uh='<table><tr><th>用户</th><th>角色</th><th>状态</th><th>模型</th><th>用量(时)</th><th>会员</th><th>积分</th><th>创建</th><th></th></tr>';
  (us.users||[]).forEach(u=>{const g=useMap[u.username]||{};const TN={free:"免费",pro:"Pro",vip:"VIP"};const tierName=u.membership_expired?((TN[u.membership_tier]||"")+"(已过期)"):(TN[u.membership]||"免费");
   uh+="<tr><td>"+esc(u.username)+"</td><td>"+esc(u.role||"")+"</td><td class='"+(u.status==="active"?"ok":"dim")+"'>"+esc(u.status)+
   "</td><td>"+esc(u.provider||"")+"</td><td>"+(g.chats_24h!=null?g.chats_24h+(g.limit_per_hour?"/"+g.limit_per_hour:" ·不限"):"-")+
   "</td><td>"+tierName+(u.membership_expire&&!u.membership_expired?"<br><span class='dim' style='font-size:11px'>至 "+fmt(u.membership_expire)+"</span>":"")+" <button data-act='member' data-u='"+esc(u.username)+"' style='padding:1px 7px'>设</button></td><td>"+(u.points||0)+" <button data-act='points' data-u='"+esc(u.username)+"' style='padding:1px 7px'>±</button></td><td class='dim'>"+fmt(u.created_ts)+"</td><td>"+(u.status==="active"?
   "<button class='no' onclick=\\"revokeU('"+esc(u.username)+"')\\">吊销</button>":"")+"</td></tr>"});
  document.getElementById("users-body").innerHTML=uh+"</table>";
  document.getElementById("users").style.display="";
  const apps=ap.applications||[];
  let ah=apps.length?"":"<p class='dim'>没有待审批申请</p>";
  apps.forEach(a=>{ah+="<p><b>"+esc(a.username)+"</b> <span class='dim'>"+esc(a.contact||"")+" · "+
   esc(a.provider||"")+" · "+fmt(a.created_ts)+"</span><br><span class='dim'>"+esc(a.reason||"")+
   "</span><br><button onclick=\\"approve('"+esc(a.username)+"',true)\\">批准</button>"+
   "<button class='no' onclick=\\"approve('"+esc(a.username)+"',false)\\">拒绝</button></p>"});
  document.getElementById("apps-body").innerHTML=ah;
  document.getElementById("apps").style.display="";
  const ivs=iv.invites||[];
  let ih=ivs.length?"<table><tr><th>码</th><th>用量</th><th>到期</th><th>状态</th><th>备注</th></tr>":"<p class='dim'>还没有邀请码,点上方生成</p>";
  ivs.forEach(x=>{ih+="<tr><td><code>"+esc(x.code)+"</code></td><td>"+x.uses+"/"+x.max_uses+"</td><td class='dim'>"+fmt(x.expires_ts)+"</td><td class='"+(x.valid?"ok":"dim")+"'>"+(x.valid?"有效":"失效")+"</td><td class='dim'>"+esc(x.note||"")+"</td></tr>"});
  document.getElementById("invites-body").innerHTML=ih+(ivs.length?"</table>":"");
  document.getElementById("invites").style.display="";
  document.getElementById("ts").textContent="刷新于 "+new Date().toLocaleTimeString();
 }catch(e){document.getElementById("login").style.display="";document.getElementById("health").innerHTML='<span class="bad">'+esc(e.message)+"</span>";}}
document.addEventListener('click',function(e){var b=e.target.closest&&e.target.closest('button[data-act]');if(!b)return;var u=b.getAttribute('data-u'),a=b.getAttribute('data-act');if(a==='member')setMember(u);else if(a==='points')addPoints(u);else if(a==='newinvite')newInvite();});
async function newInvite(){var days=prompt('有效天数:','7');if(days==null)return;var mu=prompt('可用次数:','1');if(mu==null)return;var note=prompt('备注(选填):','')||'';try{var r=await j('POST','/v1/admin/invites',{days:parseInt(days)||7,max_uses:parseInt(mu)||1,note:note});prompt('邀请码已生成,复制发给用户:',r.code);tick();}catch(e){alert(e.message);}}
async function setMember(name){var tier=prompt('会员等级 free/pro/vip:','pro');if(!tier)return;var days=prompt('有效天数(free 填 0):','30');try{await j('POST','/v1/admin/membership/'+encodeURIComponent(name),{tier:tier.trim(),days:parseInt(days)||0});tick();}catch(e){alert(e.message);}}
async function addPoints(name){var d=prompt('积分变动(正=充值,负=扣减):','100');if(d==null)return;var reason=prompt('备注(选填):','')||'';try{await j('POST','/v1/admin/points/'+encodeURIComponent(name),{delta:parseFloat(d),reason:reason});tick();}catch(e){alert(e.message);}}
tick();setInterval(tick,5000);
</script></body></html>"""


@app.get("/v1/admin", response_class=HTMLResponse)
def admin_console():
    """管理台静态页(无数据,不鉴权;数据接口仍要 admin Bearer)。
    token 由页面 JS 存 sessionStorage,?token= 永不出现在 URL(防日志明文)。"""
    return HTMLResponse(_ADMIN_HTML)


# ---------- 浏览器上手页(补审查 L5:登录入口)+ 一键装机脚本 ----------

@app.get("/")
def root_redirect():
    """裸根 → 登录页(相对跳转,spike 无前缀与生产 /mdagent 前缀通用)。
    生产里 nginx 的 location / 是租户实例(auth_request),本路由只在直连 app 或 /mdagent/ 时命中。"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="login.html", status_code=302)


@app.get("/login.html", response_class=HTMLResponse)
def login_page():
    """登录 + 注册页(公开)。nginx @weblogin 未登录访问 / 时 302 到此(/mdagent/login.html)。"""
    return HTMLResponse(pages.LOGIN_HTML)


@app.get("/app.html", response_class=HTMLResponse)
def app_page():
    """登录后控制台(公开页;数据接口靠会话 Cookie 鉴权,401 时页面自跳回登录)。"""
    return HTMLResponse(pages.APP_HTML)


@app.get("/get.sh")
def get_sh():
    """一键装机脚本(公开:新机器无凭据也要能拉;注册码才是闸门)。
    curl -fsSL <云端>/get.sh | MDEXP_SERVER=<云端> MDEXP_CODE=<注册码> sh
    路径:优先 code_dir()(生产全代码树),回退到 app.py 同级代码根(镜像内 /app/code)。"""
    from fastapi.responses import PlainTextResponse
    app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for base in (paths.code_dir(), app_root):
        p = os.path.join(base, "mdshell", "deploy", "get.sh")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return PlainTextResponse(f.read(), media_type="text/x-shellscript")
    raise HTTPException(404, "安装脚本未部署")


# ---------- 本地文件桥(mdagent_client.py 长轮询;agent 经 localfs_mcp 发起) ----------

@app.get("/v1/bridge/poll")
async def bridge_poll(request: Request, u=Depends(current_user),
                      wait: int = Query(50, ge=1, le=55)):
    op = await bridge.poll(u["username"], wait, request.is_disconnected)
    if op is not None and await request.is_disconnected():
        bridge.requeue(u["username"], op)  # 响应已送不到,放回队列等重连
        return {"op": None}
    return {"op": op}


class BridgeResultBody(BaseModel):
    op_id: str
    ok: bool
    data: object = None
    error: str = ""


@app.post("/v1/bridge/result")
def bridge_result(body: BridgeResultBody, u=Depends(current_user)):
    if not bridge.submit_result(u["username"], body.op_id,
                                {"ok": body.ok, "data": body.data, "error": body.error}):
        raise HTTPException(404, "无此 op 或已过期")
    return {"ok": True}


class BridgeCallBody(BaseModel):
    token: str
    op: str
    args: dict = {}


@app.post("/v1/bridge/call")
async def bridge_call(body: BridgeCallBody, request: Request):
    # 仅本机(localfs_mcp 与 uvicorn 同机)+ 有效 run token;run token 随 job 结束吊销
    # ("testclient" 仅测试模式放行,MDAGENT_SKIP_UNIX=1 时才会用 TestClient)
    host_ok = request.client.host == "127.0.0.1" or (
        os.environ.get("MDAGENT_SKIP_UNIX") == "1"
        and request.client.host == "testclient")
    if not host_ok:
        raise HTTPException(403, "仅本机可调")
    username = bridge.run_token_user(body.token)
    if not username:
        raise HTTPException(403, "run token 无效或已过期")
    if body.op not in ("list", "read", "write", "mkdir", "delete", "exec",
                      "edit", "glob", "grep"):
        raise HTTPException(400, "未知操作")
    return await bridge.call(body.token, username, body.op, body.args)


@app.get("/v1/bridge/status")
def bridge_status(u=Depends(current_user)):
    return {"online": bridge.client_online(u["username"])}


# ---------- 发布物(壳子自更新):manifest + bundle 下载 ----------
# 目录布局 $MDAGENT_RELEASES_DIR/<channel>/manifest.json 与 .../<channel>/files/<name>。
# manifest 公开(未注册机器 get.sh 要拉);bundle 完整性由 manifest 内 sha256 校验,不靠传输层。

_RELEASES_DIR = os.environ.get("MDAGENT_RELEASES_DIR", os.path.join(paths.HOME, "releases"))
_FNAME_RE = __import__("re").compile(r"^[A-Za-z0-9._-]+$")


@app.get("/v1/releases/{channel}/manifest.json")
def release_manifest(channel: str):
    if not _FNAME_RE.match(channel):
        raise HTTPException(400, "bad channel")
    mp = os.path.join(_RELEASES_DIR, channel, "manifest.json")
    if not os.path.isfile(mp):
        raise HTTPException(404, "无此发布通道")
    with open(mp, encoding="utf-8") as f:
        return json.load(f)


@app.get("/v1/releases/{channel}/files/{fname}")
def release_file(channel: str, fname: str):
    from fastapi.responses import FileResponse
    if not _FNAME_RE.match(channel) or not _FNAME_RE.match(fname) or fname.startswith("."):
        raise HTTPException(400, "bad path")
    fp = os.path.join(_RELEASES_DIR, channel, "files", fname)
    # 双保险:realpath 必须仍在 files 目录内
    root = os.path.realpath(os.path.join(_RELEASES_DIR, channel, "files"))
    if not os.path.realpath(fp).startswith(root + os.sep) or not os.path.isfile(fp):
        raise HTTPException(404, "无此文件")
    return FileResponse(fp, media_type="application/gzip", filename=fname)


# ---------- 租户 web 实例:nginx auth_request 认证 + 路由 ----------

@app.get("/v1/auth")
def web_auth(request: Request):
    """nginx auth_request 子请求:认会话 Cookie → 该用户在线节点 → 拉起/复用其 web 实例 → 回 X-Upstream-Port。"""
    sc = request.cookies.get("mdexp_web_session")
    u = webentry.browser_user(sc)
    if not u:
        sc = request.cookies.get("mdexp_session")
        u = accounts.by_session_token(sc) if sc else None
    if not u:
        raise HTTPException(401, "未登录")
    online = [n["node_id"] for n in nodes.list_for(u["username"])
              if not n.get("revoked") and _relay.status(n["node_id"])["online"]]
    try:
        node_id = bio.preferred_node(u["username"], online) or next(iter(online), None)
    except ValueError:
        raise HTTPException(503, "BIO_CONFIGURATION_INVALID")
    # 兜底:确保 web profile 已铺(老用户/首次登录)+ 工作区占位与节点根对齐(否则 session.create 在云端 mkdir 失败)
    try:
        provision.ensure_proxy_credentials(u["username"])   # B-H1 兜底:确保真 key 已迁出 DSH_HOME(幂等,已迁移仅一次读)
        provision.enable_web_profile(u["username"])
        if node_id:
            _root = _relay.node_root(node_id)
            if _root:
                provision.ensure_node_workspace(u["username"], _root)
    except Exception:
        pass
    try:
        # 【X2】ensure 内部为该实例铸 per-instance world 短票写进 overlay,不再把账号令牌交给租户进程
        port = tenants.ensure(u["username"], node_id)
    except Exception as e:
        raise HTTPException(503, "web 实例不可用: %s" % e)
    return Response(status_code=200, headers={"X-Upstream-Port": str(port),
                                              "X-Mdexp-User": u["username"],
                                              "X-Mdexp-Node": node_id or ""})


@app.get("/v1/web/instances")
def web_instances(_=Depends(admin_user)):
    return {"instances": tenants.status()}


# ---------- 算力节点(mdshell):注册 + 出站 WebSocket 中继 ----------
# 壳子出站连 /v1/node/ws(节点令牌走子协议 tok.<mdn_…>,经 nginx Upgrade);
# 租户 dsh 的 dsh-remote-world 三提供者连 /v1/world/<node_id>(默认仅本机回环)。

_relay = relay.Relay()
bio.mount(app, _relay)
from bio_preview import install_preview_routes, authenticate_browser
from preview_budget import GlobalPreviewBudget
import tempfile
import hashlib
_preview_lock = os.path.join(tempfile.gettempdir(), 'mdagent-preview-' + str(os.getuid()) + '-' + hashlib.sha256(paths.accounts_dir().encode()).hexdigest()[:16] + '.lock')
_preview_budget = GlobalPreviewBudget(lock_path=_preview_lock)
install_preview_routes(app, lambda request: authenticate_browser(request, current_user), tenants.preview_transport, _preview_budget)


@app.on_event("startup")
async def _startup_watcher():
    # 运行看护:异步任务拉活跃 run 的 thermo/状态(与其余端点共用事件循环)。
    import asyncio
    asyncio.create_task(watcher.run_forever(_relay))
_enroll_fails = {}  # client host -> [失败 ts](内存级;注册码 48bit + 10 分钟 + 限速)


class EnrollBody(BaseModel):
    code: str
    name: str = ""
    platform: str = ""


@app.post("/v1/nodes/enroll-code")
def nodes_enroll_code(u=Depends(current_user)):
    """签一枚 10 分钟有效、单次使用的注册码;用户在本机执行 mdshell enroll 换节点令牌。"""
    code, ttl = nodes.new_enroll_code(u["username"])
    return {"code": code, "expires_in": ttl,
            "msg": "在本机执行: mdshell enroll --server <云端地址> --code %s" % code}


@app.post("/v1/nodes/enroll")
def nodes_enroll(body: EnrollBody, request: Request):
    import time as _time
    host = request.client.host if request.client else "?"
    now = _time.time()
    fails = [t for t in _enroll_fails.get(host, []) if now - t < 600]
    if len(fails) >= 10:
        raise HTTPException(429, "失败次数过多,10 分钟后再试")
    node, token, err = nodes.enroll(body.code.strip(), body.name, body.platform)
    if err:
        fails.append(now)
        _enroll_fails[host] = fails
        raise HTTPException(400, err)
    _enroll_fails.pop(host, None)
    return {"node_id": node["node_id"], "node_token": token,
            "ticket_key": node["ticket_key"],
            "msg": "node token / ticket key 仅此一次返回,壳子已自动保存"}


class TicketBody(BaseModel):
    argv: list
    cwd: str = "."
    ttl: int = 600
    env: dict = {}
    label: str = ""


@app.post("/v1/nodes/{node_id}/tickets")
def nodes_ticket(node_id: str, body: TicketBody, request: Request, u=Depends(current_user)):
    """发车票签发。票="经过了云端闸门(三区裁决+物理门+审批)"的凭据,不是租户可随手签的。
    【安全·V5】默认对租户令牌关闭:必须另带闸门专用凭据 X-Gate-Token(== 环境变量 MDAGENT_GATE_TOKEN)。
    P2 的闸门服务持有该密钥并在裁决+审批通过后调本端点;未配置该环境变量时端点整体关闭(返回 403)。"""
    gate = os.environ.get("MDAGENT_GATE_TOKEN") or ""
    presented = request.headers.get("x-gate-token") or ""
    import hmac as _hmac
    if not gate or not _hmac.compare_digest(gate, presented):
        raise HTTPException(403, "票据签发需经云端闸门(缺 X-Gate-Token);租户不可自助签票")
    n = nodes.get(node_id)
    if not n or n["username"] != u["username"] or n.get("revoked"):
        raise HTTPException(404, "无此节点")
    if not body.argv or not all(isinstance(a, str) and a for a in body.argv):
        raise HTTPException(400, "argv 须为非空字符串数组")
    if not (1 <= body.ttl <= 3600):
        raise HTTPException(400, "ttl 须在 1~3600 秒")
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in body.env.items()):
        raise HTTPException(400, "env 须为字符串映射")
    t = nodes.sign_ticket(node_id, body.argv, body.cwd, body.ttl, body.env, body.label)
    if not t:
        raise HTTPException(409, "节点不可签票")
    return {"ticket": t}


class WorldTokenBody(BaseModel):
    node_id: str


@app.post("/v1/world/token")
def world_token(body: WorldTokenBody, u=Depends(current_user)):
    """【X2】世界通道取票口:Bearer/会话认证 + 校验节点属主 → 铸 per-instance world 短票(mdw_),
    写进 overlay 供租户 dsh 连 /v1/world/<node>。web 形态由 /v1/auth 内部铸;headless/CI 显式调本口。
    租户全程不接触账号主令牌(封住 agent→overlay→master 的 X2 爆炸半径)。"""
    n = nodes.get(body.node_id)
    if not n or n["username"] != u["username"] or n.get("revoked"):
        raise HTTPException(404, "无此节点")
    tok = accounts.mint_world_token(u["username"], body.node_id)
    return {"token": tok, "node_id": body.node_id}


@app.get("/v1/nodes")
def nodes_list(u=Depends(current_user)):
    out = []
    for n in nodes.list_for(u["username"]):
        n.update(_relay.status(n["node_id"]))
        out.append(n)
    return {"nodes": out}


@app.post("/v1/nodes/{node_id}/revoke")
async def nodes_revoke(node_id: str, u=Depends(current_user)):
    ok, err = nodes.revoke(u["username"], node_id)
    if not ok:
        raise HTTPException(404, err)
    await _relay.kick(node_id, 4401, "node token revoked")
    return {"node_id": node_id, "revoked": True}


@app.websocket("/v1/node/ws")
async def node_ws(websocket: WebSocket):
    node = nodes.by_token(relay.token_from_ws(websocket))
    if not node:
        await websocket.close(code=4401)  # accept 之前 close = 握手 403;壳子据此停止重试
        return
    await _relay.node_session(websocket, node,
                              on_hello=lambda p: nodes.touch(node["node_id"], p))


@app.websocket("/v1/world/{node_id}")
async def world_ws(websocket: WebSocket, node_id: str):
    # 租户 dsh 与 uvicorn 同机:默认只收回环;MDAGENT_WORLD_ALLOW_REMOTE=1 仅供容器化测试
    host = websocket.client.host if websocket.client else ""
    if host not in ("127.0.0.1", "::1") and os.environ.get("MDAGENT_WORLD_ALLOW_REMOTE") != "1":
        await websocket.close(code=4403)
        return
    # 【X2】租户 dsh 用 overlay 里的 per-instance world 短票(mdw_)连入,不再用账号 API 令牌。
    wt = accounts.verify_world_token(relay.token_from_ws(websocket))
    if not wt or wt.get("node_id") != node_id:
        await websocket.close(code=4401)
        return
    await _relay.world_session(websocket, node_id, wt["username"])


# ---------- 写门链:sim_exec 的云端后端(裁决在云、执行在壳) ----------
# 认 mdw_ 世界令牌 → (user, node)。agent 只能经此发命令,裁决(gate.py)说了算;签票在闸门内部,
# 租户拿不到 X-Gate-Token 故无法自签票(红队 V5)。发射路径(mpirun+lmp)先经节点读 input 跑物理门。

class GateExecBody(BaseModel):
    cmd: str
    description: str = ""
    cwd: str | None = None
    background: bool = False
    log: str | None = None


def _parse_in_file(argv):
    """从 LAMMPS 发射 argv 取 -in/-i <file>(mpirun -np N lmp_mpi -in production.in)。"""
    for i, a in enumerate(argv):
        if a in ("-in", "-i") and i + 1 < len(argv):
            return argv[i + 1]
    return None


async def _probe_restart(node_id, content, cwd):
    """物理门增强:content 含 read_restart 时经节点读 restart 魔数(fs.readBytes 前 1KB)+ 上一 run
    末态 thermo(fs.tail production.stdout)。读不到按「未探测」(None → 物理门记 warning 不 block);
    明确 NOT_FOUND 才当缺失(block)。返回 (restart_probe, prev_thermo)。"""
    import base64
    import re as _re
    m = _re.search(r'^\s*read_restart\s+(\S+)', content, _re.M)
    if not m:
        return None, None
    rst = m.group(1)
    probe = None
    try:
        t = await _relay.call_node(node_id, "fs.resolve", {"path": rst, "cwd": cwd}, timeout=10)
        rb = await _relay.call_node(node_id, "fs.readBytes", {"target": t, "maxBytes": 1024}, timeout=10)
        raw = base64.b64decode(rb.get("data", "")) if isinstance(rb, dict) else b""
        probe = {"exists": True, "size": len(raw), "magic": raw[:14]}   # len==1024 即≥1KB;<1024 即真实小
    except Exception as e:
        probe = {"exists": False, "size": 0} if "NOT_FOUND" in str(e).upper() else None
    prev = None
    try:
        t2 = await _relay.call_node(node_id, "fs.resolve", {"path": "production.stdout", "cwd": cwd}, timeout=10)
        tl = await _relay.call_node(node_id, "fs.tail", {"target": t2, "bytes": 65536}, timeout=10)
        text = tl.get("text", "") if isinstance(tl, dict) else ""
        for line in reversed(text.splitlines()):
            mm = _re.match(r'^\s*(\d+)\s+(-?[\d.eE+]+)\s', line)
            if mm:
                prev = (int(mm.group(1)), float(mm.group(2)))
                break
    except Exception:
        prev = None
    return probe, prev


@app.post("/v1/gate/exec")
async def gate_exec(body: GateExecBody, request: Request):
    """sim_exec 写门链的云端后端。租户 md_mcp 用 mdw_ 世界令牌(X-World-Token)调本口。"""
    rec = accounts.verify_world_token(request.headers.get("x-world-token") or "")
    if not rec:
        raise HTTPException(401, "缺/无效 world 令牌(mdw_)")
    username, node_id = rec["username"], rec["node_id"]
    accounts.touch_world_token(request.headers.get("x-world-token") or "")
    if not _relay.status(node_id)["online"]:
        return {"error": "节点离线,无法执行", "node": node_id}
    cwd = body.cwd or _relay.node_root(node_id) or "."
    c = gate.classify(body.cmd, cwd, body.description)
    if "error" in c:
        return {"error": c["error"]}
    zone, argv = c["zone"], c["argv"]
    # 红区:第一版据实告知阻断(排队/审批 gates 表后续补;绝不静默放行)
    if zone == "red":
        return {"blocked": True, "zone": "red", "reason": c["reason"], "gate_hash": c["hash"],
                "msg": "红区命令:需人工审批(暂未接排队)。请改用工作区内合规命令,或联系管理员。"}
    # 白名单纵深(RELEASE:215 不收窄;真正不在白名单的包装器 bash/sh 等在此硬拒)
    if not c["allow_ok"]:
        return {"blocked": True, "zone": zone, "error": c["allow_msg"]}
    warnings = []
    # 发射路径:先经节点读 input 跑物理门(安全闸 + 恒温器/tdamp/restart/续跑)
    if c["is_launch"]:
        # A-3:云端读首个 -in 而 LAMMPS 取末个 → 多个 -in 会"验 safe.in 却跑 evil.in"。恰好一个才放行。
        ins = [argv[i + 1] for i, a in enumerate(argv) if a in ("-in", "-i") and i + 1 < len(argv)]
        if len(ins) != 1:
            return {"blocked": True, "zone": zone,
                    "error": "发射命令须恰好一个 -in <input>(发现 %d 个);多 -in 云端/LAMMPS 解析不一致,拒发" % len(ins)}
        inp = ins[0]
        try:
            t = await _relay.call_node(node_id, "fs.resolve", {"path": inp, "cwd": cwd}, timeout=15)
            content = await _relay.call_node(node_id, "fs.readText", {"target": t}, timeout=15)
        except Exception as e:
            return {"blocked": True, "zone": zone, "error": "读取节点 input 失败: %s" % e}
        sec = gate.validate_input_security(content)
        if sec:
            return {"blocked": True, "zone": zone, "error": "input 安全闸拒: %s" % sec}
        restart_probe, prev_thermo = await _probe_restart(node_id, content, cwd)
        blockers, warns = gate.validate_input_physics(content, restart_probe=restart_probe, prev_thermo=prev_thermo)
        if blockers:
            return {"blocked": True, "zone": zone, "physics_blockers": blockers, "warnings": warns,
                    "msg": "物理门拒发: " + "; ".join(blockers)}
        warnings = warns
    # 闸门内部签票 → 节点 md.run(keeper 托管;短命令跑完即 exit,长任务后台盯)
    ttl = 24 * 3600 if body.background else 3600
    ticket = nodes.sign_ticket(node_id, argv, cwd, ttl, {}, body.description or body.cmd)
    if not ticket:
        raise HTTPException(500, "签票失败(节点无 ticket_key?请重新 enroll)")
    try:
        r = await _relay.call_node(node_id, "md.run", {"ticket": ticket}, timeout=30)
    except Exception as e:
        return {"error": "发车失败: %s" % e, "zone": zone}
    rr = r if isinstance(r, dict) else {}
    rid = rr.get("run_id") or rr.get("tid")
    if rid:
        runs.register(rid, username, node_id, cwd)   # 登记进运行看护(watcher 拉 thermo/状态)
    return {"ok": True, "zone": zone, "reason": c["reason"],
            "run_id": rid, "pid": rr.get("pid"),
            "warnings": warnings, "gate_hash": c["hash"], "node": node_id}


@app.get("/v1/runs")
def list_runs(u=Depends(current_user)):
    """当前用户的发车 run 列表(状态/起时/曲线点数;运行面板用)。"""
    return {"runs": runs.list_for(u["username"])}


@app.get("/v1/runs/{run_id}")
def get_run(run_id: str, u=Depends(current_user)):
    """单个 run 详情,含 thermo 曲线序列。"""
    r = runs.get(run_id)
    if not r or r.get("user") != u["username"]:
        raise HTTPException(404, "run 不存在或非本人")
    return r


# 探测常见的引擎/建模工具全路径(node01 常把 lmp 装在非 PATH 位置;这里逐个 fs.stat,存在即报);
# PATH 里的(python3/mpirun/packmol)另用 resolveExecutable。合起来给用户一张"发射前工具齐不齐"的清单。
_PREFLIGHT_PATH_TOOLS = ["python3", "mpirun", "packmol", "lmp", "lmp_mpi", "lmp_serial"]
_PREFLIGHT_ENGINE_CANDIDATES = [
    "/home/kkk/lmp_build/src/lmp_serial", "/home/kkk/lmp_build/src/lmp_mpi",
    "/usr/local/md-bin/lmp_mpi", "/usr/local/md-bin/lmp_serial",
    "/opt/software/lammps-2Aug2023/build/lmp", "/usr/bin/packmol",
    "/home/kkk/lmp_build/bin/packmol",
]


@app.get("/v1/nodes/{node_id}/preflight")
async def node_preflight(node_id: str, u=Depends(current_user)):
    """运行前系统检测:node01 的沙箱/工作根/版本 + 模拟工具(lmp/mpirun/packmol/python)在不在、在哪。
    发射前预检——避免像塑料燃烧那样发射后才发现缺工具/权限。"""
    n = nodes.get(node_id)
    if not n or n.get("username") != u["username"]:
        raise HTTPException(404, "节点不存在或非本人")
    if not _relay.status(node_id)["online"]:
        return {"online": False, "msg": "节点离线,无法检测"}
    hello = n.get("hello") or {}
    tools = {}
    # ① PATH 里能否解析(resolveExecutable:找到返回绝对路径,找不到抛错)
    for t in _PREFLIGHT_PATH_TOOLS:
        try:
            p = await _relay.call_node(node_id, "proc.resolveExecutable", {"command": t}, timeout=8)
            tools[t] = {"ok": True, "path": p, "via": "PATH"}
        except Exception:
            tools[t] = {"ok": False, "via": "PATH"}
    # ② PATH 找不到引擎时(node01 常见),逐个探常见全路径,存在且是文件即算可用(发射用全路径)
    found_engine = any(tools.get(k, {}).get("ok") for k in ("lmp", "lmp_mpi", "lmp_serial"))
    found_packmol = tools.get("packmol", {}).get("ok")
    candidates = []
    if not (found_engine and found_packmol):
        for cand in _PREFLIGHT_ENGINE_CANDIDATES:
            try:
                t = await _relay.call_node(node_id, "fs.resolve", {"path": cand}, timeout=6)
                st = await _relay.call_node(node_id, "fs.stat", {"target": t}, timeout=6)
                if st and st.get("type") == "file":
                    candidates.append({"path": cand, "size": st.get("size")})
            except Exception:
                pass
    return {
        "online": True,
        "root": hello.get("root"),
        "version": hello.get("version"),
        "sandbox": hello.get("sandbox"),
        "tools": tools,
        "engine_candidates": candidates,  # 非 PATH 的引擎/packmol 全路径(发射时用)
        "note": "引擎(lmp/mpirun)不在 PATH 属正常——node01 常装在 /home 下,发射时用全路径即可;"
                "REAXFF 包是否编入需真发射才知(pair_style reaxff 报错=没编)。",
    }


@app.get("/v1/nodes/{node_id}/ps")
async def node_ps(node_id: str, u=Depends(current_user)):
    """node01 上正在运行的模拟/程序(md.ps:run_id/pid/state/cwd/label)。看板"运行的程序"面板用。"""
    n = nodes.get(node_id)
    if not n or n.get("username") != u["username"]:
        raise HTTPException(404, "节点不存在或非本人")
    if not _relay.status(node_id)["online"]:
        return {"online": False, "procs": []}
    try:
        ps = await _relay.call_node(node_id, "md.ps", {}, timeout=8)
    except Exception as e:
        return {"online": True, "procs": [], "error": str(e)[:120]}
    return {"online": True, "procs": ps if isinstance(ps, list) else (ps.get("runs") if isinstance(ps, dict) else [])}


@app.get("/v1/client.py")
def download_client(u=Depends(current_user)):
    """分发客户端:登录用户一键下载 mdagent_client.py(装客户端=一行 curl)。"""
    from fastapi.responses import PlainTextResponse
    p = os.path.join(paths.code_dir(), "mdagent-client", "mdagent_client.py")
    if not os.path.exists(p):
        raise HTTPException(404, "客户端文件未部署")
    with open(p, encoding="utf-8") as f:
        return PlainTextResponse(f.read(),
                                 headers={"Content-Disposition":
                                          "attachment; filename=mdagent_client.py"})


@app.get("/v1/webclient.py")
def download_webclient(u=Depends(current_user)):
    """分发 web 客户端:mdagent_web.py(浏览器即工作台,图形界面版)。"""
    from fastapi.responses import PlainTextResponse
    p = os.path.join(paths.code_dir(), "mdagent-client", "mdagent_web.py")
    if not os.path.exists(p):
        raise HTTPException(404, "web 客户端文件未部署")
    with open(p, encoding="utf-8") as f:
        return PlainTextResponse(f.read(),
                                 headers={"Content-Disposition":
                                          "attachment; filename=mdagent_web.py"})


@app.get("/v1/tuiclient.py")
def download_tuiclient(u=Depends(current_user)):
    """分发 TUI 客户端:mdagent_tui.py(终端全屏版,需 textual)。"""
    from fastapi.responses import PlainTextResponse
    p = os.path.join(paths.code_dir(), "mdagent-client", "mdagent_tui.py")
    if not os.path.exists(p):
        raise HTTPException(404, "TUI 客户端文件未部署")
    with open(p, encoding="utf-8") as f:
        return PlainTextResponse(f.read(),
                                 headers={"Content-Disposition":
                                          "attachment; filename=mdagent_tui.py"})


_INSTALL_SH = """#!/bin/sh
# mdagent 一键安装(脚本内含你的 token,请勿分享/提交 git)
set -e
BASE="https://<SERVER-IP>/mdagent"
TOKEN="__TOKEN__"
DIR="$HOME/.mdagent"
PY=""
for c in python3 python3.13 python3.12 python3.11 python3.10 python3.9 python3.8; do
  if command -v "$c" >/dev/null && "$c" -c "import sys;sys.exit(sys.version_info<(3,8))" 2>/dev/null; then
    PY="$c"; break
  fi
done
[ -n "$PY" ] || { echo "需要 Python 3.8+,请先安装"; exit 1; }
echo "使用 $PY($($PY --version 2>&1))"
mkdir -p "$DIR" "$HOME/.local/bin"
echo "[1/4] 下载客户端(终端TUI版+网页版)..."
curl -k -fsSL -H "Authorization: Bearer $TOKEN" "$BASE/v1/tuiclient.py" \\
  -o "$DIR/mdagent_tui.py"
curl -k -fsSL -H "Authorization: Bearer $TOKEN" "$BASE/v1/webclient.py" \\
  -o "$DIR/mdagent_web.py"
echo "[2/4] 安装 TUI 依赖 textual(装不上会自动用网页版)..."
"$PY" -m pip install --user -q textual 2>/dev/null || \\
  "$PY" -m pip install --user -q textual \\
    -i https://pypi.tuna.tsinghua.edu.cn/simple 2>/dev/null || \\
  echo "  textual 未装上,将退回网页版(浏览器打开)"
echo "[3/4] 安装 mdagent 命令..."
cat > "$HOME/.local/bin/mdagent" <<EOF
#!/bin/sh
# mdagent 启动器:默认终端TUI,--web 起网页版;textual 缺失自动退回网页版
if [ "\\$1" = "--web" ]; then shift; exec $PY "\\$HOME/.mdagent/mdagent_web.py" "\\$@"; fi
if $PY -c "import textual" 2>/dev/null; then
  exec $PY "\\$HOME/.mdagent/mdagent_tui.py" "\\$@"
else
  exec $PY "\\$HOME/.mdagent/mdagent_web.py" "\\$@"
fi
EOF
chmod +x "$HOME/.local/bin/mdagent"
echo "[4/4] 首次启动(写入配置)..."
echo "装好了。以后直接敲: mdagent(网页版: mdagent --web)"
echo "(若提示找不到命令: $PY ~/.mdagent/mdagent_tui.py)"
exec "$HOME/.local/bin/mdagent" --token "$TOKEN"
"""

_INSTALL_PS1 = """# mdagent 一键安装(脚本内含你的 token,请勿分享)
$ErrorActionPreference = "Stop"
$base = "https://<SERVER-IP>/mdagent"
$token = "__TOKEN__"
$dir = "$HOME\\.mdagent"
New-Item -ItemType Directory -Force $dir | Out-Null
Write-Host "[1/3] 下载客户端(终端TUI版+网页版)..."
curl.exe -k -fsSL -H "Authorization: Bearer $token" "$base/v1/tuiclient.py" -o "$dir\\mdagent_tui.py"
curl.exe -k -fsSL -H "Authorization: Bearer $token" "$base/v1/webclient.py" -o "$dir\\mdagent_web.py"
Write-Host "[2/3] 安装 TUI 依赖 textual(失败自动用网页版)..."
python -m pip install --user -q textual 2>$null | Out-Null
Write-Host "[3/3] 首次启动(写入配置)..."
python -c "import textual" 2>$null
if ($LASTEXITCODE -eq 0) {
  Write-Host "装好了。以后运行: python `"$dir\\mdagent_tui.py`""
  python "$dir\\mdagent_tui.py" --token $token
} else {
  Write-Host "装好了(网页版)。以后运行: python `"$dir\\mdagent_web.py`""
  python "$dir\\mdagent_web.py" --token $token
}
"""


@app.get("/v1/install.sh")
def install_sh(request: Request, u=Depends(current_user)):
    """一键安装(Linux/macOS):curl .../install.sh | bash —— 按调用者 token 现场生成。"""
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(
        _INSTALL_SH.replace("__TOKEN__", _token_from(request)),
        media_type="text/x-sh")


@app.get("/v1/install.ps1")
def install_ps1(request: Request, u=Depends(current_user)):
    """一键安装(Windows PowerShell)。"""
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(
        _INSTALL_PS1.replace("__TOKEN__", _token_from(request)),
        media_type="text/plain")


_BOOTSTRAP_SH = """#!/bin/sh
# mdagent 免 token 引导安装(默认终端 TUI 版):现场问用户名密码 → 登录拿 token → 装。
# 脚本本身不含任何凭据,可随意转发;密码请勿含双引号/反斜杠/美元符。
# 注意:交互输入一律走 /dev/tty(curl|bash 时 stdin 被脚本占用)。
set -e
BASE="https://<SERVER-IP>/mdagent"
printf "用户名: " > /dev/tty; read USER < /dev/tty
printf "密码(不回显): " > /dev/tty
stty -echo < /dev/tty; read PASS < /dev/tty; stty echo < /dev/tty
printf "\\n" > /dev/tty
RESP=$(curl -k -fsSL -X POST "$BASE/v1/login" \\
  -H "Content-Type: application/json" \\
  -d "{\\"username\\":\\"$USER\\",\\"password\\":\\"$PASS\\"}") \\
  || { echo "登录请求失败(网络/服务器)"; exit 1; }
TOKEN=$(printf '%s' "$RESP" | python3 -c \\
  "import sys,json;print(json.load(sys.stdin).get('token',''))" 2>/dev/null)
[ -n "$TOKEN" ] || { echo "登录失败: $RESP"; exit 1; }
echo "登录成功,开始安装…"
TMP=$(mktemp)
curl -k -fsSL -H "Authorization: Bearer $TOKEN" "$BASE/v1/install.sh" -o "$TMP"
sh "$TMP" < /dev/tty; rm -f "$TMP"
"""


@app.get("/v1/bootstrap.sh")
def bootstrap_sh():
    """免 token 引导安装:curl -k .../bootstrap.sh | bash —— 问账号密码即可装。"""
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(_BOOTSTRAP_SH, media_type="text/x-sh")


@app.on_event("shutdown")
def _shutdown():
    tenants.shutdown_all()


@app.exception_handler(Exception)
async def unhandled(request, exc):
    # 【X14】不把内部异常(路径/栈/内部细节)回显给客户端;服务端记日志,对外只给通用信息。
    import logging
    logging.getLogger("mdagent").exception("unhandled error on %s", getattr(request, "url", "?"))
    return JSONResponse({"detail": "internal server error"}, status_code=500)


# Preserve the GitHub baseline APIs alongside the synchronized node platform.

@app.get("/v1/evolution/graph")
def evolution_graph(u=Depends(current_user)):
    """进化图谱数据(全员共享的进化库, {nodes, edges, stats})。"""
    return evo_graph.build()


@app.post("/v1/jobs/{job_id}/cancel")
def job_cancel(job_id: str, u=Depends(current_user)):
    """用户打断自己的进行中任务:SIGTERM agent 子进程,run_chat 识别取消温和收尾。"""
    j = jq.get_job(job_id)
    if not j:
        raise HTTPException(404, "无此任务")
    if j["user"] != u["username"] and u.get("role") != "admin":
        raise HTTPException(403, "不能取消别人的任务")
    if j["status"] not in ("queued", "running"):
        return {"ok": False, "status": j["status"], "msg": "任务已结束"}
    j["cancel"] = True
    runner.terminate_job(job_id)
    return {"ok": True, "status": j["status"], "msg": "已发停止信号"}


class PdfToolBody(BaseModel):
    content: str  # base64 编码的 PDF 原始字节(≤8MB 解码后)


@app.post("/v1/tools/pdf2text")
def pdf2text(body: PdfToolBody, u=Depends(current_user)):
    """云端 PDF 文字提取(客户端本机无 pdftotext 时的回退)。
    客户端囚禁目录里读到的 PDF 传上来解出纯文本;文件内容本来就要进对话,
    不引入新的暴露面。上限 8MB/30s,产物截 1MB。"""
    import base64
    import subprocess
    import tempfile
    try:
        raw = base64.b64decode(body.content, validate=False)
    except Exception:
        raise HTTPException(400, "content 不是合法 base64")
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(413, "PDF over 8MB unsupported")
    if not raw.startswith(b"%PDF"):
        raise HTTPException(400, "not a PDF file")
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    try:
        tmp.write(raw)
        tmp.close()
        r = subprocess.run(["pdftotext", "-enc", "UTF-8", tmp.name, "-"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           timeout=30)
        return {"text": r.stdout.decode("utf-8", "replace")[:1024 * 1024]}
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "pdftotext timeout")
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
