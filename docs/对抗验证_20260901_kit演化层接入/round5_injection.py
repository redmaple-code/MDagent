#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""第五轮修复实弹注射(全沙盒,绝不触生产):
a 未来2h fail x2 -> 零起草
b penalty=true(bool) + success x2 -> 零起草(不得假减压)
c penalty=-0.5 + fail x2 -> 起草 penalty=0.15(非 -0.35)
d penalty="garbage" + fail x2 -> 不崩,起草 0.15
e ts=now+3599 计入 / now+3601 不计(1h 容差边界)
f kit recover_agent._penalty 同口径:bool/负/垃圾 -> 0.0
"""
import json, os, shutil, sys, tempfile, time

DSH = "/opt/mdagent/app"
KIT = "/opt/mdagent/kit"
sys.path.insert(0, os.path.join(DSH, "evolution"))
sys.path.insert(0, os.path.join(DSH, "mcp"))
import observe, memory  # noqa: E402

def fmt(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))

def attempt(sid, cause, outcome, ts):
    return {"strategy": sid, "root_cause": cause, "case_id": "c1",
            "ts": fmt(ts), "outcome": outcome}

def run_case(history, adjustments=None):
    tmp = tempfile.mkdtemp(prefix="r5inj_")
    old = (observe.FLOWGATE_LOG, observe.PIPELINE_DIR, observe.PROPOSALS,
           observe.LEGACY_MCP_DIR, observe._kit_db_path)
    old_md = observe.paths.memory_dir
    old_mem = (memory.EXP_DIR, memory.ATOMS, memory.SCENARIOS, memory.SOP, memory.LEDGER)
    try:
        observe.FLOWGATE_LOG = os.path.join(tmp, "none.jsonl")
        observe.PIPELINE_DIR = os.path.join(tmp, "none-dir")
        observe.LEGACY_MCP_DIR = os.path.join(tmp, "none-legacy")
        observe.PROPOSALS = os.path.join(tmp, "proposals")
        db_path = os.path.join(tmp, ".v3.3_strategy_db.json")
        observe._kit_db_path = lambda: db_path
        observe.paths.memory_dir = lambda: tmp
        memory.EXP_DIR = os.path.join(tmp, "experience")
        memory.ATOMS = os.path.join(memory.EXP_DIR, "atoms.jsonl")
        memory.SCENARIOS = os.path.join(memory.EXP_DIR, "scenarios.json")
        memory.SOP = os.path.join(memory.EXP_DIR, "SOP.md")
        memory.LEDGER = os.path.join(tmp, "LEDGER.jsonl")
        db = {"strategies": {"B": {"name": "reduce_timestep", "desc": "",
                                   "for": ["TEMPERATURE_SPIKE"],
                                   "success_rate": 0.5, "uses": 3}},
              "history": history}
        if adjustments is not None:
            db["strategy_adjustments"] = adjustments
        with open(db_path, "w") as f:
            json.dump(db, f, indent=2)  # 规范化落盘,保证 diff 通道可起草
        summary = observe.observe()
        diffs = {}
        for pdir in summary["drafted"]:
            slug = os.path.basename(pdir)
            dp = os.path.join(pdir, "diff.patch")
            diffs[slug] = open(dp, encoding="utf-8").read() if os.path.exists(dp) else ""
        return summary, diffs
    finally:
        (observe.FLOWGATE_LOG, observe.PIPELINE_DIR, observe.PROPOSALS,
         observe.LEGACY_MCP_DIR, observe._kit_db_path) = old
        observe.paths.memory_dir = old_md
        (memory.EXP_DIR, memory.ATOMS, memory.SCENARIOS,
         memory.SOP, memory.LEDGER) = old_mem
        shutil.rmtree(tmp, True)

results = []
def check(name, ok, evidence):
    results.append((name, ok, evidence))
    print("%s %s | %s" % ("PASS" if ok else "FAIL", name, evidence))

# a. 未来 2h 的 fail x2 -> 容差外,零起草
now = time.time()
s, d = run_case([attempt("B", "fut", "fail", now + 7200),
                 attempt("B", "fut", "fail", now + 7201)])
check("a_future2h_fail", len(s["drafted"]) == 0,
      "drafted=%d (期望0)" % len(s["drafted"]))

# b. penalty=true(bool) + success x2 -> 零起草
s, d = run_case([attempt("B", "boolpen", "success", now - 10),
                 attempt("B", "boolpen", "success", now - 5)],
                {"B:boolpen": {"penalty": True, "reason": "x", "ts": "t"}})
check("b_bool_penalty_success", len(s["drafted"]) == 0,
      "drafted=%d (期望0,bool 不算压制故无减压)" % len(s["drafted"]))

# c. penalty=-0.5 + fail x2 -> 0.15 而非 -0.35
s, d = run_case([attempt("B", "negpen", "fail", now - 10),
                 attempt("B", "negpen", "fail", now - 5)],
                {"B:negpen": {"penalty": -0.5, "reason": "x", "ts": "t"}})
diff = "\n".join(d.values())
check("c_negative_penalty_fail",
      len(s["drafted"]) == 1 and '"penalty": 0.15' in diff and "-0.35" not in diff,
      "drafted=%d, diff含0.15=%s, 含-0.35=%s" % (
          len(s["drafted"]), '"penalty": 0.15' in diff, "-0.35" in diff))

# d. penalty="garbage" + fail x2 -> 不崩,起草 0.15
try:
    s, d = run_case([attempt("B", "garbage", "fail", now - 10),
                     attempt("B", "garbage", "fail", now - 5)],
                    {"B:garbage": {"penalty": "garbage", "reason": "x", "ts": "t"}})
    diff = "\n".join(d.values())
    check("d_garbage_penalty_fail",
          len(s["drafted"]) == 1 and '"penalty": 0.15' in diff,
          "drafted=%d, diff含0.15=%s, 无异常" % (
              len(s["drafted"]), '"penalty": 0.15' in diff))
except Exception as e:
    check("d_garbage_penalty_fail", False, "抛异常: %r" % e)

# e. 容差边界:+3599 计入(起草), +3601 不计(零起草)
base = time.time()
s, d = run_case([attempt("B", "inTol", "fail", base + 3599),
                 attempt("B", "inTol", "fail", base + 3599.4),
                 attempt("B", "outTol", "fail", base + 3601),
                 attempt("B", "outTol", "fail", base + 3601.4)])
slugs = sorted(os.path.basename(p) for p in s["drafted"])
in_ok = any("inTol" in x for x in slugs)
out_ok = not any("outTol" in x for x in slugs)
check("e_tolerance_boundary", in_ok and out_ok,
      "drafted=%s (期望仅 inTol)" % slugs)

# f. kit recover_agent._penalty 同口径(纯函数直测,不触 db 文件)
sys.path.insert(0, os.path.join(KIT, "agents", "runtime", "v3.3"))
import recover_agent  # noqa: E402
def _pen(v):
    return recover_agent._penalty(
        {"strategy_adjustments": {"B:x": {"penalty": v}}}, "B", "x")
f_ok = (_pen(True) == 0.0 and _pen(-0.5) == 0.0
        and _pen("garbage") == 0.0 and abs(_pen(0.3) - 0.3) < 1e-9)
check("f_kit_penalty_guard", f_ok,
      "True->%s -0.5->%s 'garbage'->%s 0.3->%s" % (
          _pen(True), _pen(-0.5), _pen("garbage"), _pen(0.3)))

bad = [r for r in results if not r[1]]
print("TOTAL: %d/%d PASS" % (len(results) - len(bad), len(results)))
sys.exit(1 if bad else 0)
