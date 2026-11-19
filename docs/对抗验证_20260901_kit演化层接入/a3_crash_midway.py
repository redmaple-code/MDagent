# -*- coding: utf-8 -*-
"""攻击3 父进程:崩溃中途 —— 子进程锁内抛异常/SIGKILL/写盘一半SIGKILL,
验证:db 不半截、fcntl 锁随进程死亡释放(父进程立即获锁)、下次 load 正常。"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import Sandbox

results = []
for mode in ("raise", "kill", "killsave"):
    sb = Sandbox()
    ra = sb.ra
    db = ra.load_db()
    db["history"] = [{"strategy": "A", "root_cause": "atomic_ejection",
                      "case_id": "pre", "ts": "2026-09-02T00:00:00", "outcome": "success"}]
    sb.write_db(db)
    before = open(sb.db_path).read()

    t0 = time.time()
    r = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "crash_child.py"),
                        sb.kit, mode], capture_output=True, text=True, timeout=60)
    died = r.returncode != 0

    # 1) db 是否半截:必须能完整解析,且内容 == 崩溃前(账目未丢未改)
    try:
        after_db = json.load(open(sb.db_path))
        intact = open(sb.db_path).read() == before
    except ValueError:
        intact = False
        after_db = None

    # 2) 锁是否残留:父进程立刻 locked_update,若锁泄漏会挂死(用告警计时兜底)
    t1 = time.time()
    ra.locked_update(lambda d: ra.record_attempt(d, "B", "thermal_runaway", "post-%s" % mode))
    lock_wait = time.time() - t1

    # 3) 下次 load 正常 + 新账已入
    final = ra.load_db()
    ids = [h["case_id"] for h in final["history"]]

    tmps = [f for f in os.listdir(sb.kit) if f.endswith(".tmp")]
    ok = died and intact and lock_wait < 1.0 and ("post-%s" % mode) in ids
    results.append(ok)
    print("mode=%-8s 子进程死亡=%s rc=%s | db完整且未被改写=%s | 锁%0.3fs内获得=%s | "
          "崩溃尝试未入账=%s | 后续load+入账正常=%s | 残留tmp=%s"
          % (mode, died, r.returncode, intact, lock_wait, lock_wait < 1.0,
             ("crash-%s" % mode.replace("killsave", "save")) not in ids,
             ("post-%s" % mode) in ids, tmps))

print("攻击3 结果: %s" % ("通过(db不半截/锁随死释放/下次load正常)"
                        if all(results) else "失败"))
sys.exit(0 if all(results) else 1)
