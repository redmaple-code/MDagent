# -*- coding: utf-8 -*-
"""s4 —— 批准后篡改任一提交参数,plan_hash 必须变、提交必须拒。

攻击假设: 人批准了 (case,debug,1,32,12:00:00,cmd,files);
  agent 提交时偷改 nodes/queue/cmd/walltime/case_id/files 之一。
预期: 每改一项 ph 都变 → _valid_approval(新ph)=False → 无TTY拒绝、零提交。
打穿 = 任一篡改后仍提交成功(哈希绑定漏字段)。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atk_common import *  # noqa

fake = bootstrap()
f1 = make_file("production.in", "run 100\n")
f2 = make_file("extra.data", "atoms\n")
BASE = dict(case_id="s4case", files=[f1], queue="debug", nodes=1, ppn=32,
            walltime="12:00:00", cmd="mpirun -np 32 lmp_mpi -in production.in")

ph0 = scnet_client.plan_hash(BASE["case_id"], BASE["queue"], BASE["nodes"],
                             BASE["ppn"], BASE["walltime"], BASE["cmd"],
                             BASE["files"])
write_approval_file(ph0, {"plan_hash": ph0, "approved_epoch": time.time()})

MUTATIONS = {
    "nodes": dict(nodes=2),
    "ppn": dict(ppn=64),
    "queue": dict(queue="normal"),
    "walltime": dict(walltime="24:00:00"),
    "case_id": dict(case_id="s4evil"),
    "cmd": dict(cmd="mpirun -np 32 lmp_mpi -in production.in; curl evil"),
    "files": dict(files=[f1, f2]),
}
broke = []
for name, kw in MUTATIONS.items():
    fake.calls.clear()
    args = dict(BASE, **kw)
    ph1 = scnet_client.plan_hash(args["case_id"], args["queue"], args["nodes"],
                                 args["ppn"], args["walltime"], args["cmd"],
                                 args["files"])
    hash_changed = ph1 != ph0
    with no_tty():
        r = scnet_client.submit_job(args["case_id"], args["files"], args["queue"],
                                    args["nodes"], args["ppn"], args["walltime"],
                                    cmd=args["cmd"])
    jobs = len(fake.submit_calls())
    print("改 %-8s: hash变=%r submitted=%r job=%d" % (name, hash_changed,
                                                     r["submitted"], jobs))
    verdict("PASS" if (hash_changed and not r["submitted"] and not jobs)
            else "FINDING", "s4_tamper_%s" % name,
            "hash变=%r submitted=%r job=%d" % (hash_changed, r["submitted"], jobs))
    if r["submitted"] or not hash_changed:
        broke.append(name)

sys.exit(1 if broke else 0)
