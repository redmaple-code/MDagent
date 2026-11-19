# -*- coding: utf-8 -*-
"""攻击1:并发双 case —— 4 线程 x 25 轮,record_attempt 与交错 settle(success/fail 交替)。
验证:无丢账、无坏 JSON、无残留 pending、每次 settle 恰好结 1 条、后验与串行口径一致。"""
import json
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import Sandbox

sb = Sandbox()
ra = sb.ra
sb.write_db(ra.load_db())  # 默认 6 策略空 history

THREADS, ROUNDS = 4, 25
errors, settle_counts = [], []


def worker(tid):
    try:
        for r in range(ROUNDS):
            case_id = "case-t%d-r%d" % (tid, r)
            sid = "A" if tid % 2 == 0 else "B"
            ra.locked_update(lambda d: ra.record_attempt(d, sid, "atomic_ejection", case_id))
            outcome = "success" if (r + tid) % 2 == 0 else "fail"
            cnt = []
            def _settle(d, outcome=outcome, case_id=case_id, cnt=cnt):
                cnt.append(ra.settle_pending(d, outcome, case_id=case_id))
            ra.locked_update(_settle)
            settle_counts.extend(cnt)
    except Exception as e:
        errors.append((tid, repr(e)))


threads = [threading.Thread(target=worker, args=(t,)) for t in range(THREADS)]
for t in threads:
    t.start()
for t in threads:
    t.join()

ok = True
if errors:
    ok = False
    print("FAIL: 线程异常: %s" % errors)

# 1) 坏 JSON 检查:原样解析
raw = open(sb.db_path).read()
try:
    db = json.loads(raw)
except ValueError as e:
    ok = False
    print("FAIL: db 坏 JSON: %s" % e)
    sys.exit(1)

hist = db["history"]
expected = {"case-t%d-r%d" % (t, r) for t in range(THREADS) for r in range(ROUNDS)}
got = [h["case_id"] for h in hist]
if len(got) != len(expected) or set(got) != expected:
    ok = False
    print("FAIL: 丢账/重账: 期望 %d 条唯一 case,实得 %d 条/%d 唯一"
          % (len(expected), len(got), len(set(got))))
else:
    print("OK: 100 条尝试全部入账,无丢失无重复")

# 2) 无残留 pending,且 outcome 与预定一致
pend = [h["case_id"] for h in hist if h["outcome"] == "pending"]
mismatch = [h["case_id"] for h in hist
            if h["outcome"] != ("success" if (int(h["case_id"].split("-r")[1])
                                             + int(h["case_id"].split("-t")[1].split("-r")[0])) % 2 == 0
                                else "fail")]
if pend:
    ok = False
    print("FAIL: 残留 pending: %s" % pend[:5])
if mismatch:
    ok = False
    print("FAIL: outcome 被错结: %s" % mismatch[:5])
if not pend and not mismatch:
    print("OK: 无残留 pending,全部 outcome 与预定交错口径一致")

# 3) 每次 settle 恰好结 1 条(不多结别人、不漏结自己)
bad = [c for c in settle_counts if c != 1]
if bad or len(settle_counts) != THREADS * ROUNDS:
    ok = False
    print("FAIL: settle 计数异常: 次数=%d 非1计数=%s" % (len(settle_counts), bad[:10]))
else:
    print("OK: 100 次 settle 每次恰好结 1 条")

# 4) 后验数学与串行口径一致:对最终 history 按 kit 规则重算
for sid, sinfo in db["strategies"].items():
    settled = [h for h in hist if h["strategy"] == sid and h["outcome"] in ("success", "fail")]
    settled = settled[-ra.SETTLE_WINDOW:]
    if settled:
        wins = sum(1 for h in settled if h["outcome"] == "success")
        expect_rate = round((wins + 1) / (len(settled) + 2), 4)
        if sinfo["success_rate"] != expect_rate:
            ok = False
            print("FAIL: 策略 %s 后验不符: 库内 %s vs 串行重算 %s"
                  % (sid, sinfo["success_rate"], expect_rate))
print("OK: 后验 success_rate 与串行口径(Beta(1,1) 近20条)一致")

# 5) 锁文件/临时文件残留
leftover = [f for f in os.listdir(sb.kit) if f.endswith(".tmp")]
print("INFO: 残留 .tmp 文件: %s; .lock 存在: %s"
      % (leftover, os.path.exists(sb.db_path + ".lock")))

print("攻击1 结果: %s" % ("通过(无丢账/无坏JSON/口径一致)" if ok else "失败"))
sys.exit(0 if ok else 1)
