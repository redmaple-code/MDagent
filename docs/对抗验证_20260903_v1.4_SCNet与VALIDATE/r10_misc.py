# -*- coding: utf-8 -*-
"""r10 —— 杂项修复验证 + 新攻击: 文件权限/上传闸/畸形响应/job_id/delete。

  M1 token 缓存 os.open 600(实证 stat);
  M2 上传文件名白名单: "evil;rm" / "a b" / 首字符"-" → 上传前干净拒、零外发;
  M3 >100MB(sparse) → 干净拒、零上传外发;
  M4 畸形响应防护: auth data 非数组 → ScnetError;query data 非对象 → ScnetError;
     region_token 缓存错位 → 干净 config 错;
  M5 job_id 白名单: "1;rm" / "1 OR 1" / 空以外的脏值拒;"1697599991"/"123_4" 放行;
     query 参数 urlencode(静态: qs 经 urlencode);
  M6 delete: enabled=False 总闸拒+审计;脏 job_id 拒+审计;合法 delete 走通+审计;
     delete 不走人工闸(用户拍板,实录 ACCEPT);
  M7 approve 目录/批准文件权限(与 r1 互补:直接 stat 复查);
退出码 0 = 全部符合。
"""
import os
import stat
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atk_common import *  # noqa

fake = bootstrap()
f1 = make_file("production.in", "run 100\n")
bad = []

# ---------------- M1 token 缓存权限
scnet_client.authenticate()
tc = scnet_client._token_cache_path()
m1 = stat.S_IMODE(os.stat(tc).st_mode) == 0o600
print("M1 token缓存权限=%o" % stat.S_IMODE(os.stat(tc).st_mode))
verdict("PASS" if m1 else "FINDING", "r10a_token_cache_mode", "mode=%o(期望600)"
        % stat.S_IMODE(os.stat(tc).st_mode))
if not m1:
    bad.append("M1")

# ---------------- M2 上传文件名白名单
for name in ["evil;rm", "a b", "-lead", "中文.in", "x`id`"]:
    p = make_file(name, "x\n")
    fake.calls.clear()
    try:
        scnet_client.upload_file("https://efile.invalid/efile", "tok", "/r", p)
        ok, detail = False, "未拒"
    except scnet_client.ScnetError as e:
        ok, detail = e.code == "param" and not fake.calls, "干净拒: %s" % str(e)[:50]
    except Exception as e:
        ok, detail = False, "裸抛 %s" % type(e).__name__
    print("M2 文件名=%-10r %s" % (name, detail))
    verdict("PASS" if ok else "FINDING", "r10b_upload_name_%r" % name, detail)
    if not ok:
        bad.append("M2:%r" % name)

# ---------------- M3 >100MB
big = os.path.join(TMP, "inbox", "big.data")
with open(big, "wb") as fh:
    fh.seek(101 * 1024 * 1024)
    fh.write(b"\0")
fake.calls.clear()
try:
    scnet_client.upload_file("https://efile.invalid/efile", "tok", "/r", big)
    m3 = False
except scnet_client.ScnetError as e:
    m3 = e.code == "param" and not fake.calls
print("M3 >100MB: 干净拒=%r 外发=%d" % (m3, len(fake.calls)))
verdict("PASS" if m3 else "FINDING", "r10c_too_large", "拒=%r" % m3)
if not m3:
    bad.append("M3")
os.remove(big)

# ---------------- M4 畸形响应
def bad_auth(*a, **kw):
    return {"not": "alist"}


orig = scnet_client._http
scnet_client._http = bad_auth
reset_state()
try:
    scnet_client.authenticate()
    m4a = False
except scnet_client.ScnetError as e:
    m4a = "畸形" in str(e)
except Exception:
    m4a = False


class JobsHTTP(FakeHTTP):
    def __call__(self, method, url, token=None, body=None, headers=None,
                 timeout=30):
        if "/openapi/v2/jobs" in url:
            if method == "DELETE":
                self.calls.append((method, url, body))
                return {"ok": 1}
            if self.__dict__.get("bad_query"):
                return ["not-a-dict"]
            return {"list": [{"jobId": "1"}]}
        return super().__call__(method, url, token=token, body=body,
                                headers=headers, timeout=timeout)


scnet_client._http = orig
fake4 = install_http(JobsHTTP())
try:
    fake4.bad_query = True
    scnet_client.query_jobs()
    m4b = False
except scnet_client.ScnetError as e:
    m4b = "畸形" in str(e)
fake4.bad_query = False
# region_token 错位
try:
    scnet_client.region_token({"regions": {}}, {"cluster_id": "99999"})
    m4c = False
except scnet_client.ScnetError as e:
    m4c = e.code == "config"
print("M4 auth畸形拒=%r query畸形拒=%r region错位拒=%r" % (m4a, m4b, m4c))
verdict("PASS" if (m4a and m4b and m4c) else "FINDING", "r10d_malformed_resp",
        "auth=%r query=%r region=%r" % (m4a, m4b, m4c))
if not (m4a and m4b and m4c):
    bad.append("M4")

# ---------------- M5 job_id 白名单
m5 = True
for jid in ["1;rm", "1 OR 1=1", "1&x=2", "中文", "1/2", "1.5"]:
    try:
        scnet_client.query_jobs(job_id=jid)
        m5 = False
        print("M5 脏job_id=%r 未拒!" % jid)
    except scnet_client.ScnetError as e:
        if e.code != "param":
            m5 = False
for jid in ["1697599991", "123_4"]:
    try:
        jobs = scnet_client.query_jobs(job_id=jid)
        print("M5 合法job_id=%r 放行 list=%r" % (jid, jobs))
    except scnet_client.ScnetError as e:
        m5 = False
        print("M5 合法job_id=%r 被误伤: %s" % (jid, e))
verdict("PASS" if m5 else "FINDING", "r10e_job_id_whitelist",
        "脏job_id全拒,合法(含slurm array 123_4)放行")
if not m5:
    bad.append("M5")

# ---------------- M6 delete 闸
reset_state()
# enabled=False 总闸
patch_config(dict(GOOD_SCNET, enabled=False))
try:
    scnet_client.delete_job("123")
    m6a = False
except scnet_client.ScnetError as e:
    m6a = e.code == "gate"
acts = [e["action"] for e in audit_entries()]
m6a = m6a and acts and acts[-1] == "refused"
# 脏 job_id
patch_config()
fake6 = install_http(JobsHTTP())
reset_state()
try:
    scnet_client.delete_job("1;rm -rf")
    m6b = False
except scnet_client.ScnetError as e:
    m6b = e.code == "param"
acts = [e["action"] for e in audit_entries()]
m6b = m6b and acts and acts[-1] == "refused"
# 合法 delete(无人工闸,用户拍板)
reset_state()
with no_tty():
    try:
        scnet_client.delete_job("1697599991")
        m6c = True
    except Exception as e:
        m6c = False
        print("M6 合法delete异常: %r" % e)
acts = [e["action"] for e in audit_entries()]
m6c = m6c and acts and acts[-1] == "deleted"
del_calls = [c for c in fake6.calls if c[0] == "DELETE"]
print("M6 enabled总闸拒+入账=%r 脏id拒+入账=%r 合法delete=%r DELETE调用=%d 审计=%r"
      % (m6a, m6b, m6c, len(del_calls), acts))
verdict("PASS" if (m6a and m6b and m6c) else "FINDING", "r10f_delete_gate",
        "总闸=%r 脏id=%r 合法=%r" % (m6a, m6b, m6c))
verdict("ACCEPT", "r10f2_delete_no_human_gate",
        "delete 不走人工闸=用户拍板(安全方向运维);已纳入 enabled 总闸+job_id 白名单+审计")
if not (m6a and m6b and m6c):
    bad.append("M6")

# ---------------- M7 批准目录/文件权限复查
reset_state()
scnet_client.set_pin("abcd1234")
with no_tty():
    dr = scnet_client.submit_job("r10g", [f1], "debug", 1, 32, "12:00:00",
                                 dry_run=True)
human_approve(dr["plan"]["plan_hash"], pin="abcd1234")
ap = scnet_client._approval_path(dr["plan"]["plan_hash"])
m7 = (stat.S_IMODE(os.stat(ap).st_mode) == 0o600
      and stat.S_IMODE(os.stat(scnet_client._approval_dir()).st_mode) == 0o700
      and stat.S_IMODE(os.stat(scnet_client._pin_path()).st_mode) == 0o600)
print("M7 批准文件=%o 目录=%o pin=%o"
      % (stat.S_IMODE(os.stat(ap).st_mode),
         stat.S_IMODE(os.stat(scnet_client._approval_dir()).st_mode),
         stat.S_IMODE(os.stat(scnet_client._pin_path()).st_mode)))
verdict("PASS" if m7 else "FINDING", "r10g_approval_perms",
        "批准文件600/目录700/pin600 = %r" % m7)
if not m7:
    bad.append("M7")

print("R10 总不良项: %r" % bad)
sys.exit(1 if bad else 0)
