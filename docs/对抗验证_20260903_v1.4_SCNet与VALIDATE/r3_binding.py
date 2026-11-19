# -*- coding: utf-8 -*-
"""r3 —— 复攻 F3(文件内容调包)/F4(cluster_id 挪用) + 绑定面新攻击。

复攻 F3(s5b): 批准 /good/production.in,提交 /evil/production.in(同 basename
  不同内容)。修复后文件 名+sha256 全进哈希 → ph 变 → 拒。预期零提交。
复攻 F4(s5c): 批准区域A(cluster_id=11112),提交 cluster_id=22223 → ph 变 → 拒。
新攻击:
  B1 work_root 漂移: 批准后把配置 work_root 改掉 → ph 变 → 拒;
  B2 cluster_id 显式==配置值时哈希一致(批准与提交同源,正用不退化);
  B3 失败恢复后 plan_hash 漂移: 批准→上传失败(恢复原批准)→改文件内容→重试
     → ph 变 → 拒,且原批准未被消耗(改回内容后仍可成功);
  B4 文件名同、大小写差异/顺序差异: 两文件顺序调换哈希不变(排序,正用),
     同名不同内容必变。
退出码 0 = 绑定完整。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atk_common import *  # noqa

bad = []
CMD = "mpirun -np 32 lmp_mpi -in production.in"

# ---------------- 复攻 F3: 同 basename 不同内容调包
good = make_file("good/production.in", "run 100  # 人看过的好文件\n")
evil = make_file("evil/production.in", "run 999  # 攻击者换的坏文件\n")
fake = bootstrap()
hg = eff_plan_hash("r3case", "debug", 1, 32, "12:00:00", CMD, [good])
he = eff_plan_hash("r3case", "debug", 1, 32, "12:00:00", CMD, [evil])
write_approval_file(hg, {"plan_hash": hg, "approved_epoch": time.time()})
with no_tty():
    r = scnet_client.submit_job("r3case", [evil], "debug", 1, 32, "12:00:00",
                                cmd=CMD)
f3 = (hg != he) and not r["submitted"] and not fake.submit_calls()
print("F3复攻: 哈希变=%r submitted=%r job=%d" % (hg != he, r["submitted"],
                                                len(fake.submit_calls())))
verdict("PASS" if f3 else "FINDING", "r3a_content_swap",
        "批准good/提交evil: hash变=%r 调包成功=%r" % (hg != he, r["submitted"]))
if not f3:
    bad.append("r3a")

# ---------------- 复攻 F4: cluster_id 挪用他区
regions2 = [
    {"id": 11112, "name": "区域A-已批准",
     "clusterUserInfo": {"userName": "fake", "homePath": "/homeA"},
     "hpcUrls": [{"enable": "true", "url": "https://hpcA.invalid/hpc"}],
     "efileUrls": [{"enable": "true", "url": "https://efileA.invalid/efile"}]},
    {"id": 22223, "name": "区域B-未批准",
     "clusterUserInfo": {"userName": "fake", "homePath": "/homeB"},
     "hpcUrls": [{"enable": "true", "url": "https://hpcB.invalid/hpc"}],
     "efileUrls": [{"enable": "true", "url": "https://efileB.invalid/efile"}]},
]
reset_state()
fake2 = install_http(FakeHTTP(regions=regions2))
patch_config(dict(GOOD_SCNET, cluster_id="11112"))
f3f = make_file("production.in", "run 100\n")
phA = write_eff_approval("r3reg", "debug", 1, 32, "12:00:00", CMD, [f3f])
with no_tty():
    rB = scnet_client.submit_job("r3reg", [f3f], "debug", 1, 32, "12:00:00",
                                 cluster_id="22223")
f4 = not rB["submitted"] and not fake2.submit_calls()
print("F4复攻: 挪区域B submitted=%r job=%d" % (rB["submitted"],
                                              len(fake2.submit_calls())))
verdict("PASS" if f4 else "FINDING", "r3b_cluster_id_move",
        "批准区域A提交区域B: submitted=%r" % rB["submitted"])
if not f4:
    bad.append("r3b")

# B2 正用不退化: 显式 cluster_id == 配置值 → 哈希一致可提交
reset_state()
fake2.calls.clear()
write_eff_approval("r3reg", "debug", 1, 32, "12:00:00", CMD, [f3f])
with no_tty():
    rA = scnet_client.submit_job("r3reg", [f3f], "debug", 1, 32, "12:00:00",
                                 cluster_id="11112")
print("B2 正用(显式同配置cluster_id): submitted=%r region=%r"
      % (rA["submitted"], rA.get("region")))
verdict("PASS" if rA["submitted"] else "FINDING", "r3c_explicit_same_cluster",
        "显式cluster_id==配置值: submitted=%r(正用应放行)" % rA["submitted"])
if not rA["submitted"]:
    bad.append("r3c")

# ---------------- B1 work_root 漂移
reset_state()
patch_config()  # 恢复默认 GOOD_SCNET
fake3 = install_http(FakeHTTP())
write_eff_approval("r3wr", "debug", 1, 32, "12:00:00", CMD, [f3f])
patch_config(dict(GOOD_SCNET, work_root="EVIL-ROOT"))  # 批准后改配置
with no_tty():
    rw = scnet_client.submit_job("r3wr", [f3f], "debug", 1, 32, "12:00:00")
b1 = not rw["submitted"] and not fake3.submit_calls()
print("B1 work_root漂移: submitted=%r job=%d" % (rw["submitted"],
                                                len(fake3.submit_calls())))
verdict("PASS" if b1 else "FINDING", "r3d_work_root_drift",
        "批准后改work_root: submitted=%r" % rw["submitted"])
if not b1:
    bad.append("r3d")

# ---------------- B3 失败恢复后漂移
reset_state()
patch_config()
fake4 = install_http(FakeHTTP(
    fail_on={"/file/upload": scnet_client.ScnetError("network", "断网", "up")}))
drift_f = make_file("drift/production.in", "run 100\n")
ap_ph = write_eff_approval("r3drift", "debug", 1, 32, "12:00:00", CMD, [drift_f])
ap_path = scnet_client._approval_path(ap_ph)
try:
    with no_tty():
        scnet_client.submit_job("r3drift", [drift_f], "debug", 1, 32, "12:00:00")
    raised1 = False
except scnet_client.ScnetError:
    raised1 = True
restored = os.path.exists(ap_path)
orphans = [x for x in os.listdir(scnet_client._approval_dir())
           if ".taken-" in x]
# 改内容后重试 → ph 变 → 拒
with open(drift_f, "w") as fh:
    fh.write("run 999  # 调包\n")
fake4.fail_on.clear()
with no_tty():
    rd = scnet_client.submit_job("r3drift", [drift_f], "debug", 1, 32, "12:00:00")
drift_refused = not rd["submitted"] and not fake4.submit_calls()
still_there = os.path.exists(ap_path)  # 漂移拒绝不得消耗原批准
# 改回原内容 → 应成功(恢复语义+绑定同时成立)
with open(drift_f, "w") as fh:
    fh.write("run 100\n")
with no_tty():
    rb = scnet_client.submit_job("r3drift", [drift_f], "debug", 1, 32, "12:00:00")
b3 = raised1 and restored and not orphans and drift_refused and still_there \
    and rb["submitted"]
print("B3 失败恢复: 首败=%r 恢复=%r 孤儿=%r | 漂移重试拒=%r 原批准在=%r | 改回成功=%r"
      % (raised1, restored, orphans, drift_refused, still_there, rb["submitted"]))
verdict("PASS" if b3 else "FINDING", "r3e_restore_then_drift",
        "失败恢复=%r 孤儿taken=%r 漂移拒=%r 批准保留=%r 改回成功=%r"
        % (restored, orphans, drift_refused, still_there, rb["submitted"]))
if not b3:
    bad.append("r3e")

# ---------------- B4 文件顺序/同名内容
fa = make_file("o/a.data", "AAA\n")
fb = make_file("o/b.data", "BBB\n")
h_order1 = eff_plan_hash("r3o", "debug", 1, 32, "12:00:00", CMD, [fa, fb])
h_order2 = eff_plan_hash("r3o", "debug", 1, 32, "12:00:00", CMD, [fb, fa])
fb2 = make_file("o2/b.data", "BBB-改\n")
h_diff = eff_plan_hash("r3o", "debug", 1, 32, "12:00:00", CMD, [fa, fb2])
b4 = (h_order1 == h_order2) and (h_order1 != h_diff)
print("B4 顺序无关=%r(正用) 同名内容变→hash变=%r" % (h_order1 == h_order2,
                                                     h_order1 != h_diff))
verdict("PASS" if b4 else "FINDING", "r3f_order_and_content",
        "文件顺序哈希不变(排序正用)=%r;同名内容调包hash变=%r"
        % (h_order1 == h_order2, h_order1 != h_diff))
if not b4:
    bad.append("r3f")

print("R3 总不良项: %r" % bad)
sys.exit(1 if bad else 0)
