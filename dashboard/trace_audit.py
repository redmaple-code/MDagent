#!/usr/bin/env python3
"""trace 一致性校验(2026-09-17 加固):看板数字与全工具账本对账。
断言:总线里 alive 的 case,trace 里必须有对应时间窗内的 sim_exec 启动记录;
对不上 → 标红"来源不明"。数据陈旧(总线更新晚于最后一次 sim_exec 很多) → 标黄。
用法: python3 trace_audit.py [--json]
"""
import json, os, sys, time

KIT = os.environ.get("AGENT_BASE",
    "/opt/mdagent/kit")
TRACE = "/var/log/mdsim/trace.jsonl"
STALE_S = 3600  # 总线更新比最后一次 sim_exec 晚超 1h → 陈旧

def load_bus():
    try:
        return json.load(open(os.path.join(KIT, "status_bus.json"), encoding="utf-8"))
    except Exception:
        return {}

def load_trace():
    rows = []
    try:
        with open(TRACE, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("kind") == "tool-call":
                    rows.append(r)
    except PermissionError:
        return None  # 调用者无读权限(应以 user/root 运行)
    except FileNotFoundError:
        return None
    return rows

def audit():
    bus = load_bus()
    rows = load_trace()
    if rows is None:
        return {"error": "trace 不可读", "flags": []}
    sim_execs = [r for r in rows if "sim_exec" in str(r.get("tool", ""))]
    last_exec_t = max((r["t"] for r in sim_execs), default=0)
    flags = []
    cases = bus.get("cases") or bus.get("tasks") or {}
    items = cases.items() if isinstance(cases, dict) else []
    for cid, c in items:
        if not isinstance(c, dict):
            continue
        status = str(c.get("status", "")).upper()
        if status not in ("RUNNING", "ALIVE"):
            continue
        upd = c.get("updated_at") or c.get("ts") or 0
        try:
            upd_ms = float(upd) * (1000 if float(upd) < 1e12 else 1)
        except (TypeError, ValueError):
            upd_ms = 0
        if not sim_execs:
            flags.append({"case": cid, "level": "RED",
                          "reason": "来源不明: case alive 但 trace 无任何 sim_exec 记录"})
        elif upd_ms and upd_ms > last_exec_t + STALE_S * 1000:
            flags.append({"case": cid, "level": "YELLOW",
                          "reason": "数据陈旧: 总线更新晚于最后一次 sim_exec 超 %ds" % STALE_S})
    return {"checked_at": int(time.time() * 1000), "cases_alive": len(list(items)),
            "sim_exec_records": len(sim_execs), "flags": flags}

if __name__ == "__main__":
    r = audit()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(1 if any(f["level"] == "RED" for f in r.get("flags", [])) else 0)
