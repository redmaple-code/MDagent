# -*- coding: utf-8 -*-
"""收官新攻击 e:坏库(RecursionError 级深嵌套)下各链路的响亮程度。
 e1) kit load_db:必须抛 ValueError(响亮,带原因),绝不静默当空库
 e2) locked_update 链路:ValueError 穿透、锁随异常释放(后续可再获锁)、坏库文件字节不变
 e3) recover main 路径(去掉 research 干扰直打 load_db 行):ValueError 响亮抛出,不写半截 result
 e4) orchestrator._settle_strategy_attempts:按设计只告警不阻断,但必须 warning 留痕且坏库不变
"""
import importlib.util
import json
import logging
import os
import sys

sys.path.insert(0, "/tmp/evo_attack")
from common import Sandbox, KIT_V33

results = []

def make_bad_db(sb):
    depth = 3000
    deep_text = '{"a":' * depth + "1" + "}" * depth
    head = ('{"strategies": {"A": {"name": "a", "desc": "", "for": ["X"], '
            '"success_rate": 0.5, "uses": 1}}, "history": [{"strategy": "A", '
            '"root_cause": "atomic_ejection", "case_id": "d1", '
            '"ts": "2026-09-02T00:00:00", "outcome": "fail", "extra": ')
    with open(sb.db_path, "w") as f:
        f.write(head + deep_text + "}]}\n")

# ---- e1 ----
sb = Sandbox()
ra = sb.ra
make_bad_db(sb)
bad_text = open(sb.db_path).read()
try:
    ra.load_db()
    ok_e1, msg = False, "静默返回(严重:坏库当空库用)"
except ValueError as e:
    ok_e1, msg = "无法解析" in str(e) or "顶层" in str(e), str(e)[:60]
except RecursionError as e:
    ok_e1, msg = False, "RecursionError 穿透(回退!)"
print("e1) load_db 坏库: %s → %s" % (msg, "PASS" if ok_e1 else "FAIL"))
results.append(("e1 load_db坏库响亮抛ValueError", ok_e1))

# ---- e2 ----
try:
    ra.locked_update(lambda d: ra.record_attempt(d, "A", "atomic_ejection", "x"))
    ok_e2a = False
except ValueError:
    ok_e2a = True
except RecursionError:
    ok_e2a = False
db_untouched = open(sb.db_path).read() == bad_text
# 锁是否释放:修复库后立刻再 locked_update,挂死则超时(join 5s 兜底)
with open(sb.db_path, "w") as f:
    json.dump({"strategies": {}, "history": []}, f, indent=2)
import threading, time
done = []
t = threading.Thread(target=lambda: (ra.locked_update(lambda d: d["history"].append({"x": 1})), done.append(1)))
t.start(); t.join(timeout=5)
ok_e2b = not t.is_alive() and done
print("e2) locked_update: ValueError穿透=%s, 坏库字节不变=%s, 锁释放(修库后5s内再获锁)=%s → %s"
      % (ok_e2a, db_untouched, ok_e2b, "PASS" if ok_e2a and db_untouched and ok_e2b else "FAIL"))
results.append(("e2 locked_update穿透/锁释放/坏库不变", ok_e2a and db_untouched and ok_e2b))

# ---- e3: recover main 路径 ----
sb3 = Sandbox()
ra3 = sb3.ra
make_bad_db(sb3)
inp = os.path.join(sb3.tmp, "in.json")
outp = os.path.join(sb3.tmp, "out.json")
json.dump({"prod_dir": sb3.tmp, "anomalies": [], "history": [], "case_id": "e3"}, open(inp, "w"))
ra3.run_research = lambda *a, **k: None  # 去掉联网研究干扰,直打 load_db
old_argv = sys.argv
sys.argv = ["recover_agent.py", inp, outp]
try:
    ra3.main()
    ok_e3, msg3 = False, "main 静默完成(严重)"
except ValueError as e:
    ok_e3, msg3 = True, "ValueError: %s" % str(e)[:50]
except RecursionError:
    ok_e3, msg3 = False, "RecursionError 穿透"
finally:
    sys.argv = old_argv
no_half_result = not os.path.exists(outp)
print("e3) recover main 坏库: %s, 不写半截result=%s → %s"
      % (msg3, no_half_result, "PASS" if ok_e3 and no_half_result else "FAIL"))
results.append(("e3 recover main坏库响亮崩/不写半截", ok_e3 and no_half_result))

# ---- e4: orchestrator._settle_strategy_attempts ----
sb4 = Sandbox()
make_bad_db(sb4)
bad4 = open(sb4.db_path).read()
real_base = os.environ.get("AGENT_BASE")  # common.load_recover_agent 已设为 sb4.kit
# orchestrator 模块级 import 需要真 harness:临时指向真 BASE 导入,再切回沙盒
os.environ["AGENT_BASE"] = "/opt/mdagent/kit"
spec = importlib.util.spec_from_file_location(
    "orchestrator", os.path.join(KIT_V33, "orchestrator.py"))
orch_mod = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(orch_mod)
    orch = orch_mod.OrchestratorAgent.__new__(orch_mod.OrchestratorAgent)  # 跳过 __init__(不碰真状态)
    memlog = logging.getLogger("attack-e4")
    handler = logging.StreamHandler(open(os.path.join(sb4.tmp, "orch_warn.log"), "w"))
    memlog.addHandler(handler)
    orch.logger = memlog
    os.environ["AGENT_BASE"] = sb4.kit  # _settle 内部按文件路径现载 recover_agent,读此刻 env
    orch._settle_strategy_attempts("case-e4", done=True)
    handler.flush()
    warn_text = open(os.path.join(sb4.tmp, "orch_warn.log")).read()
    ok_e4 = "settle skipped" in warn_text and open(sb4.db_path).read() == bad4
    print("e4) orchestrator settle 坏库: 告警留痕=%s, 坏库不变=%s → %s"
          % ("settle skipped" in warn_text, open(sb4.db_path).read() == bad4,
             "PASS" if ok_e4 else "FAIL"))
    results.append(("e4 orchestrator告警留痕/坏库不变", ok_e4))
except Exception as e:
    print("e4) orchestrator 加载/调用异常: %r → FAIL" % e)
    results.append(("e4 orchestrator告警留痕/坏库不变", False))
finally:
    os.environ["AGENT_BASE"] = sb4.kit

print("\n== 收官-e 总结 ==")
for name, ok in results:
    print("%s: %s" % (name, "通过" if ok else "失败"))
sys.exit(0 if all(ok for _, ok in results) else 1)
