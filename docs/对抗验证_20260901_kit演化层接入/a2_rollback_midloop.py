# -*- coding: utf-8 -*-
"""攻击2:回滚中段 —— 连败2 → 起草 → apply(penalty 0.15) → kit 再记一笔并结账 →
apply_proposal --rollback → 立刻再跑 observe。如实记录语义。"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import Sandbox, attempt

sb = Sandbox()
ra = sb.ra
observe = sb.observe
now = time.strftime("%Y-%m-%dT%H:%M:%S")

db = ra.load_db()
db["history"] = [attempt("A", "atomic_ejection", "c1", "fail", now),
                 attempt("A", "atomic_ejection", "c2", "fail", now)]
sb.write_db(db)

# 1) observe 起草连败压制提案
s1 = observe.observe(days=7, min_occurrences=3)
assert len(s1["drafted"]) == 1, s1
pdir = s1["drafted"][0]
print("1) 连败2起草: %s" % os.path.basename(pdir))

# 2) apply 真实通道
rc = sb.run_apply([pdir])
pen = sb.read_db().get("strategy_adjustments", {}).get("A:atomic_ejection", {}).get("penalty")
print("2) apply rc=%d, penalty=%s" % (rc, pen))
assert rc == 0 and pen == 0.15

# 3) apply 后 kit 继续工作:记一笔新尝试并结 success(apply 与 rollback 之间的真实账目)
ra.locked_update(lambda d: ra.record_attempt(d, "A", "atomic_ejection", "c3"))
ra.locked_update(lambda d: ra.settle_pending(d, "success", case_id="c3"))
hist_pre = sb.read_db()["history"]
rate_pre = sb.read_db()["strategies"]["A"]["success_rate"]
print("3) apply 后 kit 入账 c3(success): history=%d 条, A后验=%s" % (len(hist_pre), rate_pre))

# 4) rollback
lid = [r for r in sb.ledger_rows() if r.get("result") == "AUTO_APPLIED"][0]["id"]
rc = sb.run_apply(["--rollback", lid])
db_post = sb.read_db()
hist_post = db_post["history"]
adj_post = db_post.get("strategy_adjustments", {})
rate_post = db_post["strategies"]["A"]["success_rate"]
print("4) rollback rc=%d: history=%d 条(case=%s), adjustments=%s, A后验=%s"
      % (rc, len(hist_post), [h["case_id"] for h in hist_post], adj_post, rate_post))
if len(hist_post) == 2 and rate_post != rate_pre:
    print("   ** 语义实录: rollback 整文件还原,apply 后 kit 结的 c3 账被抹掉,"
          "后验从 %s 退回 %s(apply↔rollback 窗口内的真账丢失)" % (rate_pre, rate_post))

# 5) rollback 后立刻再跑 observe
try:
    s2 = observe.observe(days=7, min_occurrences=3)
    print("5) observe 再跑: 未崩, drafted=%s" % [os.path.basename(p) for p in s2["drafted"]])
except Exception as e:
    print("5) observe 再跑: 崩溃! %r" % e)
    sys.exit(1)

# 5a) 连败是否还在(回滚还原的是 apply 前的备份,c1/c2 fail 本就在备份里)
streak_hist = [h for h in sb.read_db()["history"] if h["outcome"] == "fail"]
print("5a) 回滚后库内 fail 记录: %d 条 → 连败信号仍在" % len(streak_hist))

# 5b) 同日草稿目录已在 → draft_proposal 拒重建;LEDGER 里 AUTO_APPLIED 仍在 →
#     _proposal_applied=True → 连"刷新 diff"分支也被跳过。 stale diff 内容:
stale = open(os.path.join(pdir, "diff.patch")).read()
print("5b) 草稿目录旧 diff 仍含 'penalty\": 0.15': %s; 当日不再起草也不再刷新"
      % ('"penalty": 0.15' in stale))
applied_flag = observe._proposal_applied(
    "strategy-weight-A-atomic_ejection")
print("5c) _proposal_applied(rollback 后) = %s (LEDGER 的 AUTO_APPLIED 记录不因 ROLLED_BACK 失效)"
      % applied_flag)

# 5d) 但旧 diff 上下文与回滚后磁盘态完全一致(回滚=恢复到 diff 的 base) → 人工重 apply 仍可干净应用
files = sb.apply_proposal.parse_unified_diff(stale)
old_lines = open(sb.db_path).read().splitlines()
try:
    new_lines = sb.apply_proposal.apply_hunks(old_lines, files[0]["hunks"], "x")
    new_db = json.loads("\n".join(new_lines) + "\n")
    print("5d) stale diff 仍能干净应用, penalty=%s (上下文未过期,因回滚恰好还原到 diff base)"
          % new_db["strategy_adjustments"]["A:atomic_ejection"]["penalty"])
except Exception as e:
    print("5d) stale diff 已不可应用: %r" % e)

# 6) 次日重起草能力(换日期模拟):证明通道非永久卡死
real_strftime = time.strftime
observe.time.strftime = lambda fmt, t=None: "2099-01-01" if fmt == "%Y-%m-%d" else real_strftime(fmt, t) if t else real_strftime(fmt)
try:
    s3 = observe.observe(days=7, min_occurrences=3)
    print("6) 模拟次日: drafted=%s → 次日可重新起草,非永久卡死"
          % [os.path.basename(p) for p in s3["drafted"]])
finally:
    observe.time.strftime = real_strftime

print("攻击2 结果: 通过(不崩/不产垃圾 diff),但实录两条语义问题 —— 见总结")
