# -*- coding: utf-8 -*-
"""s5 —— plan_hash 绑定完整性: 16 hex 截断 + 漏字段审计。

攻击假设: 批准绑定 = sha256(json(参数)).hexdigest()[:16]。
  16 hex = 64 bit,生日碰撞 ~2^32(不可实测,只做理论标注);
  真正可打的是"哪些影响提交结果的输入没进哈希"。
步骤:
  1. 逐字段 diff: 七字段各改一次,hash 必须全变(绑定面确认);
  2. 漏字段实弹 a: files 只哈希 basename —— 批准 /good/production.in,
     提交 /evil/production.in(同 basename 不同目录不同内容),同 ph 必须仍放行
     → 若放行则"批准的A内容,上传的B内容"成立 = 打穿;
  3. 漏字段实弹 b: cluster_id 不进哈希 —— 批准区域A,提交时换 cluster_id=区域B,
     同 ph 若放行 = 批准被挪用到别的区域/集群;
  4. 漏字段静态确认: input_file(显式cmd时)、prod_dir 不进哈希(仅标注)。
预期: 1 全变为通过;2/3 若提交成功 = 打穿(绑定漏洞)。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atk_common import *  # noqa

# ---- 1. 逐字段 diff
f1 = make_file("production.in", "run 100\n")
base = ["case", "debug", 1, 32, "12:00:00", "cmd a", [f1]]
h0 = scnet_client.plan_hash(*base)
diffs = {
    "case_id": ["caseX", "debug", 1, 32, "12:00:00", "cmd a", [f1]],
    "queue": ["case", "normal", 1, 32, "12:00:00", "cmd a", [f1]],
    "nodes": ["case", "debug", 2, 32, "12:00:00", "cmd a", [f1]],
    "ppn": ["case", "debug", 1, 64, "12:00:00", "cmd a", [f1]],
    "walltime": ["case", "debug", 1, 32, "13:00:00", "cmd a", [f1]],
    "cmd": ["case", "debug", 1, 32, "12:00:00", "cmd b", [f1]],
    "files": ["case", "debug", 1, 32, "12:00:00", "cmd a", [f1, f1]],
}
allchg = all(scnet_client.plan_hash(*v) != h0 for v in diffs.values())
verdict("PASS" if allchg else "FINDING", "s5a_all_fields_bound",
        "七字段逐一diff全部改hash=%r; hash长度=%d hex(%d bit)"
        % (allchg, len(h0), len(h0) * 4))

# ---- 2. 同 basename 不同内容/目录 → 同 hash
good = make_file("good/production.in", "run 100  # 人看过的好文件\n")
evil = make_file("evil/production.in", "run 999  # 攻击者换的坏文件\n")
hg = scnet_client.plan_hash("s5case", "debug", 1, 32, "12:00:00", "cmd a", [good])
he = scnet_client.plan_hash("s5case", "debug", 1, 32, "12:00:00", "cmd a", [evil])
same_hash = hg == he

fake = bootstrap()
write_approval_file(hg, {"plan_hash": hg, "approved_epoch": time.time()})
with no_tty():
    r = scnet_client.submit_job("s5case", [evil], "debug", 1, 32, "12:00:00",
                                cmd="cmd a")
swapped = r["submitted"] and len(fake.upload_calls()) == 1
print("内容调包: 同hash=%r submitted=%r 上传次数=%d" % (same_hash, r["submitted"],
                                                     len(fake.upload_calls())))
verdict("FINDING" if swapped else "PASS", "s5b_file_content_swap",
        "files只哈希basename: 批准good/提交evil同hash=%r, 调包提交成功=%r"
        % (same_hash, swapped))

# ---- 3. cluster_id 不进哈希 → 批准挪用他区
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
f3 = make_file("production.in", "run 100\n")
cmd3 = "mpirun -np 32 lmp_mpi -in production.in"
ph3 = scnet_client.plan_hash("s5reg", "debug", 1, 32, "12:00:00", cmd3, [f3])
write_approval_file(ph3, {"plan_hash": ph3, "approved_epoch": time.time()})
with no_tty():
    r3 = scnet_client.submit_job("s5reg", [f3], "debug", 1, 32, "12:00:00",
                                 cluster_id="22223")
moved = r3["submitted"] and r3.get("region") == "区域B-未批准"
print("区域挪用: submitted=%r region=%r remote_dir=%r"
      % (r3["submitted"], r3.get("region"), r3.get("remote_dir")))
verdict("FINDING" if moved else "PASS", "s5c_cluster_id_not_bound",
        "cluster_id不进plan_hash: 批准区域A,提交到区域B成功=%r" % moved)

print("附注: input_file(显式cmd时)/prod_dir 同样不进哈希(静态确认,scnet_client.py:329-334);"
      " 64bit截断生日界~2^32,30min窗内实际不可利用,仅理论标注。")
sys.exit(1 if (swapped or moved) else 0)
