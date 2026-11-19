# -*- coding: utf-8 -*-
"""攻击7:巨型/畸形 db —— 1MB 字符串条目、10KB 策略名、深层嵌套。
测 observe 内存/耗时与崩溃面;kit recover_agent load_db 对照。"""
import json
import os
import resource
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import Sandbox, attempt

NOW_TS = time.strftime("%Y-%m-%dT%H:%M:%S")


def peak_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


# ---------- 7a: 200 条 history 每条带 1MB blob(~200MB db) + 尾部连败 ----------
sb = Sandbox()
db = sb.ra.load_db()
hist = []
for i in range(198):
    h = attempt("D", "x", "old%d" % i, "success", NOW_TS)
    h["blob"] = "y" * (1024 * 1024)  # 1MB 无关字段(畸形但合法)
    hist.append(h)
hist.append(attempt("A", "atomic_ejection", "f1", "fail", NOW_TS))
hist.append(attempt("A", "atomic_ejection", "f2", "fail", NOW_TS))
db["history"] = hist
t0 = time.time()
sb.write_db(db)
size_mb = os.path.getsize(sb.db_path) / 1024.0 / 1024.0
t1 = time.time()
s = sb.observe.observe(days=7, min_occurrences=3)
t2 = time.time()
print("7a 巨型db %.0fMB: observe 一轮 %.1fs, 峰值内存 %.0fMB, drafted=%d → %s"
      % (size_mb, t2 - t1, peak_mb(), len(s["drafted"]),
         "未崩" if isinstance(s["drafted"], list) else "异常"))
print("   (对照:同规模 json.dump 落盘 %.1fs)" % (t1 - t0))

# ---------- 7b: 10KB 策略名 ----------
sb2 = Sandbox()
db2 = sb2.ra.load_db()
big_sid = "S" * 10000
db2["history"] = [attempt(big_sid, "atomic_ejection", "b1", "fail", NOW_TS),
                  attempt(big_sid, "atomic_ejection", "b2", "fail", NOW_TS)]
sb2.write_db(db2)
t0 = time.time()
s2 = sb2.observe.observe(days=7, min_occurrences=3)
print("7b 10KB策略名: observe %.2fs 未崩, drafted=%d(目录名超限被外层兜底吞掉→该连败永远起不了草案)"
      % (time.time() - t0, len(s2["drafted"])))

# ---------- 7c: 深层嵌套 ----------
sb3 = Sandbox()
deep = {}
cur = deep
for _ in range(5000):
    cur["a"] = {}
    cur = cur["a"]
db3 = sb3.ra.load_db()
h = attempt("A", "atomic_ejection", "d1", "fail", NOW_TS)
h["extra"] = deep
db3["history"] = [h]
try:
    sb3.write_db(db3)  # json.dump 本身可能撞递归限
    writable = True
except RecursionError:
    writable = False
    # 手工拼一个深嵌套文件绕开 dump 限制不现实——直接构造较浅但超 json.load C 扫描器限制的
    print("7c 5000层嵌套: json.dump 即 RecursionError(连畸形库都造不出来,套件 save_db 同样写不进)")
if writable:
    try:
        s3 = sb3.observe.observe(days=7, min_occurrences=3)
        print("7c 深嵌套: observe 未崩 drafted=%s" % s3["drafted"])
    except Exception:
        traceback.print_exc()

# kit 侧对照:深嵌套 db 对 recover_agent.load_db 的影响
if writable:
    try:
        sb3.ra.load_db()
        print("7c kit load_db: 正常")
    except RecursionError as e:
        print("7c kit load_db: RecursionError 穿透(except ValueError 兜不住)→ recover_agent 瘫痪: %s" % e)
    except ValueError as e:
        print("7c kit load_db: ValueError(被容错兜住): %s" % e)

print("攻击7 完成")
