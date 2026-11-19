# -*- coding: utf-8 -*-
"""s10 —— case_id 注入: `../`、空格、`;`、中文进了 remote_dir / GAP_JOB_NAME。

攻击假设: case_id 由 agent 生成,无任何白名单校验(scnet_client.py:418,440
  只做 strip("/"))。远端 mkdir(createParents=true) 与 GAP_WORK_DIR 都用它。
步骤: 逐个脏 case_id 走全链(带合法批准),抓 fake 收到的 mkdir/upload/job
  载荷,检查 remote_dir 是否逃逸 work_root=YMY-AI。
预期: remote_dir 必须恒在 <home>/YMY-AI/ 之下;逃逸 = 打穿。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atk_common import *  # noqa

fake = bootstrap()
f1 = make_file("production.in", "run 100\n")
CMD = "mpirun -np 32 lmp_mpi -in production.in"
WORK_ROOT_OK_PREFIX = "/public/home/fake/YMY-AI/"

DIRTY = ["../../escape", "..%2f..%2fenc", "a b", "a;rm -rf /", "中文案例",
         "a/b/nested", ".", "case`id`", "case$(id)"]
broke = []
for cid in DIRTY:
    reset_state()
    fake.calls.clear()
    ph = scnet_client.plan_hash(cid, "debug", 1, 32, "12:00:00", CMD, [f1])
    write_approval_file(ph, {"plan_hash": ph, "approved_epoch": time.time()})
    try:
        with no_tty():
            r = scnet_client.submit_job(cid, [f1], "debug", 1, 32, "12:00:00")
        rd = r.get("remote_dir", "")
        escaped = r["submitted"] and not (rd + "/").startswith(WORK_ROOT_OK_PREFIX)
        # 归一化看逃逸: 处理 ../ 后的真实落点
        norm = os.path.normpath(rd)
        norm_ok = (norm + "/").startswith("/public/home/fake/YMY-AI/")
        print("case_id=%-18r submitted=%r remote_dir=%r normpath逃逸=%r"
              % (cid, r["submitted"], rd, not norm_ok))
        # job 载荷里的 GAP_JOB_NAME / GAP_WORK_DIR
        if fake.submit_calls():
            info = fake.submit_calls()[0][2]["mapAppJobInfo"]
            print("    GAP_JOB_NAME=%r GAP_WORK_DIR=%r"
                  % (info["GAP_JOB_NAME"], info["GAP_WORK_DIR"]))
        if r["submitted"] and not norm_ok:
            verdict("FINDING", "s10_traverse_%r" % cid,
                    "remote_dir=%r 归一化后逃逸YMY-AI" % rd)
            broke.append(cid)
        else:
            verdict("PASS" if not r["submitted"] else "ACCEPT", "s10_%r" % cid,
                    "submitted=%r remote_dir=%r(未逃逸work_root,但壳元字符原样进载荷)"
                    % (r["submitted"], rd))
    except Exception as e:
        print("case_id=%-18r 异常 %s: %s" % (cid, type(e).__name__, e))
        verdict("PASS", "s10_%r_exc" % cid, "异常拒绝: %s" % e)

sys.exit(1 if broke else 0)
