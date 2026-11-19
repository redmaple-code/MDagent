# -*- coding: utf-8 -*-
"""攻击3 子进程:在 locked_update 锁内以三种方式死法验证 db/锁完整性。
用法: python3.11 crash_child.py <kit_dir> <raise|kill|killsave>"""


def main():
    import json
    import os
    import sys
    sys.path.insert(0, "/tmp/evo_attack")
    from common import load_recover_agent

    kit_dir, mode = sys.argv[1], sys.argv[2]
    ra = load_recover_agent(kit_dir)

    if mode == "raise":
        # 锁内 fn 抛业务异常
        def fn(d):
            ra.record_attempt(d, "A", "atomic_ejection", "crash-raise")
            raise RuntimeError("boom-in-lock")
        ra.locked_update(fn)
    elif mode == "kill":
        # 锁内 mutate 后、save 前 SIGKILL 自己(模拟 OOM/断电)
        def fn(d):
            ra.record_attempt(d, "A", "atomic_ejection", "crash-kill")
            os.kill(os.getpid(), 9)
        ra.locked_update(fn)
    elif mode == "killsave":
        # save_db 写到一半 SIGKILL(模拟写盘途中断电)
        def killer(db):
            import tempfile
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(ra.STRATEGY_DB) or ".",
                                       suffix=".tmp")
            os.write(fd, b'{"strategies": {"A": {"na')  # 半截 JSON
            os.fsync(fd)
            os.close(fd)
            os.kill(os.getpid(), 9)
        ra.save_db = killer
        ra.locked_update(lambda d: ra.record_attempt(d, "A", "atomic_ejection", "crash-save"))
    else:
        raise SystemExit("unknown mode")


if __name__ == "__main__":
    main()
