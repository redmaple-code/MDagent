# -*- coding: utf-8 -*-
"""攻击7 补充:手工构造深嵌套 db 文件(绕开 json.dump 递归限,模拟手改/外部工具写入),
打 observe 与 kit load_db 两侧。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import Sandbox

sb = Sandbox()
# 手工拼 3000 层嵌套文本(合法 JSON,任何标准解析器都得递归)
depth = 3000
deep_text = '{"a":' * depth + "1" + "}" * depth
db = sb.ra.load_db()
db["history"] = [{"strategy": "A", "root_cause": "atomic_ejection", "case_id": "d1",
                  "ts": "2026-09-02T00:00:00", "outcome": "fail",
                  "extra": json.loads('{"a":1}')}]  # 占位,下面文本级替换
# 直接文本拼装完整 db
head = '{"strategies": {"A": {"name": "a", "desc": "", "for": ["X"], "success_rate": 0.5, "uses": 1}}, "history": [{"strategy": "A", "root_cause": "atomic_ejection", "case_id": "d1", "ts": "2026-09-02T00:00:00", "outcome": "fail", "extra": '
with open(sb.db_path, "w") as f:
    f.write(head + deep_text + "}]}\n")

# kit 侧
try:
    sb.ra.load_db()
    print("kit load_db: 正常解析 %d 层" % depth)
except RecursionError as e:
    print("kit load_db: RecursionError 穿透(except ValueError 兜不住)→ recover 通道瘫痪: %s" % e)
except ValueError as e:
    print("kit load_db: ValueError 兜住: %s" % e)

# observe 侧
try:
    s = sb.observe.observe(days=7, min_occurrences=3)
    print("observe: 未崩, drafted=%s" % s["drafted"])
except RecursionError as e:
    print("observe: RecursionError 穿透到 observe() 顶层?(无顶层兜底): %s" % e)
except Exception as e:
    print("observe: %r" % e)
