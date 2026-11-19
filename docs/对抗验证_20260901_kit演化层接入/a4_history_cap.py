# -*- coding: utf-8 -*-
"""攻击4:history 封顶 200 边界 —— 连败跨驱逐边界时 14 天窗+尾部连败检测行为。
场景:
 A) 恰好200条(198条30天前老success + 尾部2条新fail):窗内连败=2,应起草;再记1笔驱逐最老,连败不丢
 B) 乱序文件(还原/手改后):最新记录在 position 0 —— 位置式驱逐把"最新"账先踢掉(数据丢失)
 C) 乱序文件:被驱逐的是夹在中间的近窗 success —— 制造出本不存在的幻影连败(夸大连败)
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import Sandbox, attempt

NOW = time.time()
def ts(ago_hours):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW - ago_hours * 3600))

# ---------- 场景 A:恰好 200,尾部连败跨驱逐 ----------
sb = Sandbox()
ra, observe = sb.ra, sb.observe
db = ra.load_db()
hist = [attempt("D", "x", "old%d" % i, "success", ts(30 * 24 + i)) for i in range(198)]
hist += [attempt("A", "atomic_ejection", "f1", "fail", ts(2)),
         attempt("A", "atomic_ejection", "f2", "fail", ts(1))]
db["history"] = hist
sb.write_db(db)
s = observe.observe(days=7, min_occurrences=3)
a_drafted = len(s["drafted"])
print("A1) 恰好200条+尾部连败2: drafted=%d (期望1,14天窗正确排除198条老success)" % a_drafted)

# kit 再记一笔 pending → 201 → 驱逐 position0(30天前的老success)
ra.locked_update(lambda d: ra.record_attempt(d, "A", "atomic_ejection", "f3"))
ra.locked_update(lambda d: ra.settle_pending(d, "fail", case_id="f3"))
h2 = sb.read_db()["history"]
s = observe.observe(days=7, min_occurrences=3)
print("A2) 驱逐后: history=%d, 尾部连败=%s, 再起草(同日去重拒重建)=%s; 连败未丢=%s"
      % (len(h2), [h["case_id"] for h in h2[-3:]], len(s["drafted"]),
         [h["case_id"] for h in h2[-3:]] == ["f1", "f2", "f3"]))
a_ok = a_drafted == 1 and len(h2) == 200

# ---------- 场景 B:乱序文件,最新记录在头部,被位置式驱逐 ----------
sb2 = Sandbox()
ra2 = sb2.ra
db2 = ra2.load_db()
# position 0 = 最新 fail(2h前);position 1..199 = 30天前老 success(文件序≠ts序)
hist_b = [attempt("A", "atomic_ejection", "newest-fail", "fail", ts(2))]
hist_b += [attempt("D", "x", "old%d" % i, "success", ts(30 * 24 + i)) for i in range(199)]
db2["history"] = hist_b
sb2.write_db(db2)
ra2.locked_update(lambda d: ra2.record_attempt(d, "A", "atomic_ejection", "appended"))
h_b = sb2.read_db()["history"]
b_lost_newest = h_b[0]["case_id"] != "newest-fail" and all(
    h["case_id"] != "newest-fail" for h in h_b)
print("B) 乱序文件 record_attempt 驱逐 position0: 最新账(newest-fail,2h前)被驱逐=%s,"
      " 而199条30天前老账全保留 → 位置式驱逐不认 ts" % b_lost_newest)

# ---------- 场景 C:乱序 + 驱逐近窗 success → 幻影连败 ----------
sb3 = Sandbox()
ra3, ob3 = sb3.ra, sb3.observe
db3 = ra3.load_db()
# ts 升序口径下尾部是 [fail(3h), success(2h)]:尾部连败=1,不应起草;
# 但 success(2h) 被手塞到 position 0,一次 record_attempt 就把它驱逐 → 尾部变 fail(3h)+新 fail → 幻影连败2
hist_c = [attempt("A", "atomic_ejection", "mid-success", "success", ts(2))]
hist_c += [attempt("D", "x", "old%d" % i, "success", ts(30 * 24 + i)) for i in range(198)]
hist_c += [attempt("A", "atomic_ejection", "tail-fail", "fail", ts(3))]
db3["history"] = hist_c
sb3.write_db(db3)
s_pre = ob3.observe(days=7, min_occurrences=3)
print("C1) 驱逐前(尾部=fail,success 在 ts 序更晚): drafted=%d (期望0)" % len(s_pre["drafted"]))
ra3.locked_update(lambda d: ra3.record_attempt(d, "A", "atomic_ejection", "new-fail"))
ra3.locked_update(lambda d: ra3.settle_pending(d, "fail", case_id="new-fail"))
# 清掉同日草稿目录以观测"会不会起草"(去重只看目录存在)
import shutil
shutil.rmtree(ob3.PROPOSALS, ignore_errors=True)
s_post = ob3.observe(days=7, min_occurrences=3)
h_c = sb3.read_db()["history"]
mid_gone = all(h["case_id"] != "mid-success" for h in h_c)
print("C2) 驱逐后: mid-success 被驱逐=%s, drafted=%d → %s"
      % (mid_gone, len(s_post["drafted"]),
         "幻影连败成立(驱逐抹掉断连败的 success,夸大连败)" if s_post["drafted"] else "未夸大"))

print("攻击4 结果: A正常=%s; B=%s; C=%s"
      % ("是" if a_ok else "否",
         "位置式驱逐丢最新账(乱序时)" if b_lost_newest else "未复现",
         "可制造幻影连败" if s_post["drafted"] else "未复现"))
