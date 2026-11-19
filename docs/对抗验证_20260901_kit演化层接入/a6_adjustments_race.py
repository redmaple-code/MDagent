# -*- coding: utf-8 -*-
"""攻击6:adjustments 竞态 —— observe 初读 db 与 _strategy_db_diff 生成之间,
另一进程(并发 apply / kit 结账 / 人工)改写 db。验证按新快照或不干净不起草,绝不叠加错误。
 hook 点:猴补丁 observe._strategy_db_diff 包装器,在调原函数前模拟并发写。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import Sandbox, attempt

NOW_TS = time.strftime("%Y-%m-%dT%H:%M:%S")


def fresh_sb_with_2fails():
    sb = Sandbox()
    db = sb.ra.load_db()
    db["history"] = [attempt("A", "atomic_ejection", "c1", "fail", NOW_TS),
                     attempt("A", "atomic_ejection", "c2", "fail", NOW_TS)]
    sb.write_db(db)
    return sb


def observe_with_concurrent_write(sb, writer):
    """writer(sb) 在 observe 初读之后、diff 生成之前执行(模拟并发写盘)。"""
    orig = sb.observe._strategy_db_diff
    state = {"fired": False}
    def wrapper(db_path, sid, cause, direction, streak):
        if not state["fired"]:
            state["fired"] = True
            writer(sb)  # 并发写发生在 diff 生成的磁盘重读之前
        return orig(db_path, sid, cause, direction, streak)
    sb.observe._strategy_db_diff = wrapper
    try:
        return sb.observe.observe(days=7, min_occurrences=3)
    finally:
        sb.observe._strategy_db_diff = orig


def draft_diff_penalty(summary):
    if not summary["drafted"]:
        return None, None
    pdir = summary["drafted"][0]
    return open(os.path.join(pdir, "diff.patch")).read(), pdir


# 竞态1: 并发方把 penalty 从 0 写到 0.15(如昨日提案刚被 apply)
sb1 = fresh_sb_with_2fails()
def w1(sb):
    db = sb.read_db()
    db["strategy_adjustments"] = {"A:atomic_ejection": {
        "penalty": 0.15, "reason": "并发apply", "ts": NOW_TS}}
    sb.write_db(db)
s1 = observe_with_concurrent_write(sb1, w1)
diff1, _ = draft_diff_penalty(s1)
got_new = '"penalty": 0.3' in diff1 if diff1 else False
got_stale = '"penalty": 0.15' in diff1.split("\n+\n")[0] if diff1 else False
plus_lines = [l for l in diff1.splitlines() if l.startswith("+") and "penalty" in l] if diff1 else []
minus_lines = [l for l in diff1.splitlines() if l.startswith("-") and "penalty" in l] if diff1 else []
print("竞态1 并发写penalty 0→0.15: 起草=%s; diff -行=%s +行=%s → %s"
      % (bool(diff1), minus_lines, plus_lines,
         "按新快照步进0.15→0.3(无叠加错误)" if got_new and minus_lines and '"penalty": 0.15' in minus_lines[0]
         else "异常"))

# 竞态2: 并发方已把 penalty 顶到 0.6(满格)
sb2 = fresh_sb_with_2fails()
def w2(sb):
    db = sb.read_db()
    db["strategy_adjustments"] = {"A:atomic_ejection": {
        "penalty": 0.6, "reason": "并发apply满格", "ts": NOW_TS}}
    sb.write_db(db)
s2 = observe_with_concurrent_write(sb2, w2)
diff2, _ = draft_diff_penalty(s2)
print("竞态2 并发写penalty 0→0.6(满格): 起草=%s → %s"
      % (bool(diff2), "不干净不起草(步进无果,放弃本轮)" if not diff2 else "异常:产出了diff"))

# 竞态3: 并发方在 history 尾部结出一笔新 success(套件结账),连败尾巴已被打断
sb3 = fresh_sb_with_2fails()
def w3(sb):
    # 模拟 kit:并发记一笔并成功结账(真实 locked_update 通道)
    sb.ra.locked_update(lambda d: sb.ra.record_attempt(d, "A", "atomic_ejection", "c3"))
    sb.ra.locked_update(lambda d: sb.ra.settle_pending(d, "success", case_id="c3"))
s3 = observe_with_concurrent_write(sb3, w3)
diff3, pdir3 = draft_diff_penalty(s3)
tail = sb3.read_db()["history"][-1]
print("竞态3 并发结出尾部 success: 磁盘尾部=%s:%s; observe 起草=%s → %s"
      % (tail["case_id"], tail["outcome"], bool(diff3),
         "仍按旧快照的连败起草了压制提案(连胜退出被 TOCTOU 绕过)"
         if diff3 else "未起草"))

# 竞态4(对照): 无并发写,正常起草 0→0.15
sb4 = fresh_sb_with_2fails()
s4 = observe_with_concurrent_write(sb4, lambda sb: None)
diff4, _ = draft_diff_penalty(s4)
print("竞态4 对照(无并发): 起草=%s, 含 0.15=%s"
      % (bool(diff4), '"penalty": 0.15' in diff4 if diff4 else False))

print("攻击6 结果: 竞态1=%s 竞态2=%s 竞态3=%s 竞态4=%s"
      % ("PASS" if got_new else "FAIL",
         "PASS" if not diff2 else "FAIL",
         "命中:history 侧 TOCTOU 仍存在(旧连败快照起草案)" if diff3 else "PASS",
         "PASS" if diff4 else "FAIL"))
