# -*- coding: utf-8 -*-
"""s8 —— symlink 攻击: 批准目录/批准文件被换成 symlink。

攻击假设: 攻击者能写 <数据根>/state/(与能直接写批准文件同权限)。
  a) state/scnet_approve/<ph>.json 是指向攻击者控制文件的 symlink;
  b) state/scnet_approve 整个目录是 symlink。
检查: open()/os.remove() 均跟 symlink(Python 默认),_valid_approval 不设防。
预期: 会被跟随(实录)。但威胁模型: 能写 state 目录者本来就能直接写合法
  批准文件(见 s2),symlink 不扩大能力 —— 预期判 ACCEPT(信任边界内),
  只有出现"跨权限"效果(如读到不该读的路径且被采信)才升级。
附加: symlink 指向不存在目标时,open 抛 FileNotFoundError 被 except 吞 → False
  (fail-closed,通过)。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atk_common import *  # noqa

fake = bootstrap()
f1 = make_file("production.in", "run 100\n")
CMD = "mpirun -np 32 lmp_mpi -in production.in"

# a) 批准文件为 symlink → 攻击者控制文件(合法内容)
ph = scnet_client.plan_hash("s8case", "debug", 1, 32, "12:00:00", CMD, [f1])
ap = scnet_client._approval_path(ph)
evil = make_file("evil_approval.json",
                 '{"plan_hash": "%s", "approved_epoch": %f}' % (ph, time.time()))
os.remove(ap) if os.path.exists(ap) else None
os.symlink(evil, ap)
with no_tty():
    r = scnet_client.submit_job("s8case", [f1], "debug", 1, 32, "12:00:00")
followed = r["submitted"]
print("a) 文件symlink: 被跟随提交=%r" % followed)

# b)  dangling symlink → fail-closed?
reset_state()
fake.calls.clear()
ap2 = scnet_client._approval_path(ph)
os.symlink(os.path.join(TMP, "nonexistent.json"), ap2)
with no_tty():
    r2 = scnet_client.submit_job("s8case", [f1], "debug", 1, 32, "12:00:00")
print("b) dangling symlink: submitted=%r(期望False)" % r2["submitted"])

verdict("PASS" if not r2["submitted"] else "FINDING", "s8b_dangling_failclosed",
        "悬空symlink拒绝=%r" % (not r2["submitted"]))
verdict("ACCEPT", "s8a_symlink_followed",
        "open/remove跟随symlink=%r;但写state目录权限≡直接写批准文件(s2已证),"
        "不扩大攻击面,与基线E2同型(写状态者已在信任边界内)" % followed)
sys.exit(0 if not r2["submitted"] else 1)
