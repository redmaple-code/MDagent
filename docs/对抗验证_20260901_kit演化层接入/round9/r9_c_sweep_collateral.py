# -*- coding: utf-8 -*-
"""九轮新攻击 c:_sweep_stale_drafts 误伤面(八轮把 early return 全接上 set() 全扫)。
 c1) 同日早些 observe(健康 db)起草合法草稿 → 下一轮 observe 时 db 变 history=null
     → early return 全扫:同轮早些的合法草稿是否被误清?(设计意图 fail-closed 核对)
 c2) touched 时序:同一轮内新起草的 slug 必在 touched → 同轮收尾横扫不误清(对照)
 c3) crontab 重叠并发:健康实例刚写好新 diff,并发实例读 db 瞬态失败(set() 全扫)
     是否把健康实例的合法新 diff 清掉?最终是否自愈?
"""
import os
import sys
import threading
import time

sys.path.insert(0, "/tmp/evo_attack")
from common import Sandbox, attempt

NOW = time.time()
def ts(ago_h):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW - ago_h * 3600))

results = []

def healthy_db(sb):
    db = sb.ra.load_db()
    db["history"] = [attempt("A", "atomic_ejection", "f1", "fail", ts(2)),
                     attempt("A", "atomic_ejection", "f2", "fail", ts(1))]
    sb.write_db(db)

# ---------- c1 db 变 history=null 全扫 ----------
sb = Sandbox()
healthy_db(sb)
s1 = sb.observe.observe(days=7, min_occurrences=3)
assert len(s1["drafted"]) == 1
patch = os.path.join(s1["drafted"][0], "diff.patch")
assert os.path.getsize(patch) > 0
db1 = sb.read_db()
db1["history"] = None  # 跑到一半 db 被改坏
sb.write_db(db1)
sb.observe.observe(days=7, min_occurrences=3)
wiped = os.path.getsize(patch) == 0
print("c1) 合法草稿遇上轮 db history=null: 被全扫清空=%s(fail-closed 设计意图) → %s"
      % (wiped, "PASS" if wiped else "FAIL"))
results.append(("c1 db畸形时全扫合法草稿=设计意图fail-closed", wiped))
# 自愈核对:db 恢复后下一轮应回填
healthy_db(sb)
sb.observe.observe(days=7, min_occurrences=3)
healed = os.path.getsize(patch) > 0
print("c1+) db 恢复后同日刷新回填=%s" % healed)
results.append(("c1+ db恢复后自愈回填", healed))

# ---------- c2 同轮 touched 时序 ----------
sb2 = Sandbox()
db2 = sb2.ra.load_db()
db2["history"] = [attempt("A", "atomic_ejection", "f1", "fail", ts(2)),
                  attempt("A", "atomic_ejection", "f2", "fail", ts(1)),
                  attempt("B", "thermal_runaway", "g1", "fail", ts(2)),
                  attempt("B", "thermal_runaway", "g2", "fail", ts(1))]
sb2.write_db(db2)
s2 = sb2.observe.observe(days=7, min_occurrences=3)
both_alive = (len(s2["drafted"]) == 2 and
              all(os.path.getsize(os.path.join(d, "diff.patch")) > 0 for d in s2["drafted"]))
print("c2) 同轮起草两份: 收尾横扫后两份 diff 均存活=%s → %s"
      % (both_alive, "PASS" if both_alive else "FAIL"))
results.append(("c2 同轮touched不误伤", both_alive))

# ---------- c3 并发重叠互扫 ----------
sb3 = Sandbox()
healthy_db(sb3)
s3 = sb3.observe.observe(days=7, min_occurrences=3)
assert len(s3["drafted"]) == 1
patch3 = os.path.join(s3["drafted"][0], "diff.patch")
stop = threading.Event()
wipe_events = []
errors = []

def healthy_writer():
    while not stop.is_set():
        try:
            sb3.observe.observe(days=7, min_occurrences=3)
            if os.path.getsize(patch3) == 0:
                wipe_events.append(time.time())
        except Exception as e:
            errors.append(repr(e))

def db_flapper():
    bak = sb3.db_path + ".flap"
    while not stop.is_set():
        try:
            os.rename(sb3.db_path, bak)      # db 瞬态消失 → 并发实例 sweep(set())
            time.sleep(0.02)
            os.rename(bak, sb3.db_path)
            time.sleep(0.02)
        except OSError:
            pass

t1 = threading.Thread(target=healthy_writer)
t2 = threading.Thread(target=db_flapper)
t1.start(); t2.start()
time.sleep(2.0)
stop.set(); t1.join(); t2.join()
# 收尾:db 稳定在,最后一次 observe 应回填
sb3.observe.observe(days=7, min_occurrences=3)
healed3 = os.path.getsize(patch3) > 0
no_crash = not errors
print("c3) 并发2s(健康写者×N + db瞬态消失): 被并发实例误清事件=%d 起, 异常=%r,"
      " 最终自愈=%s → %s" % (len(wipe_events), errors[:2], healed3,
                              "PASS" if (no_crash and healed3) else "FAIL"))
if wipe_events:
    print("    实录[低]: 并发实例读 db 瞬态失败 → set() 全扫清掉健康实例刚写的合法 diff"
          "(fail-closed,不崩;下一轮健康 observe 同日刷新回填自愈)")
results.append(("c3 并发互扫不崩且最终自愈", no_crash and healed3))

print("\n== 九轮-c 总结 ==")
for name, ok in results:
    print("%s: %s" % (name, "通过" if ok else "失败"))
sys.exit(0 if all(ok for _, ok in results) else 1)
