# -*- coding: utf-8 -*-
"""s6 —— 批准后 submit 中途失败(upload 抛错),批准文件存废行为实录。

攻击假设: 人批准后,提交链在上传阶段网络故障。
检查: _consume_approval 只在提交成功后调用(scnet_client.py:494-495),
  故中途失败时批准文件应仍在,30 分钟内可同参数重试。
预期: 记录实际行为。保留=设计意图(注释明示"提交成功后才销毁"),接受;
  只有当保留构成绕过(如能换参数重试)才算发现 —— s4 已证换参数 hash 变,
  故此处预期: 批准保留 + 同参数重试成功 + 异参数重试被拒。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atk_common import *  # noqa

f1 = make_file("production.in", "run 100\n")
CMD = "mpirun -np 32 lmp_mpi -in production.in"
fake = bootstrap(fail_on={"/file/upload": scnet_client.ScnetError("network", "模拟上传断网", "up")})
ph = scnet_client.plan_hash("s6case", "debug", 1, 32, "12:00:00", CMD, [f1])
ap_path = write_approval_file(ph, {"plan_hash": ph, "approved_epoch": time.time()})

# 第一次: 上传炸 → submit 抛错
try:
    with no_tty():
        scnet_client.submit_job("s6case", [f1], "debug", 1, 32, "12:00:00")
    raised = False
except scnet_client.ScnetError:
    raised = True
kept = os.path.exists(ap_path)
print("中途失败: 异常=%r 批准文件保留=%r 提交数=%d"
      % (raised, kept, len(fake.submit_calls())))

# 恢复网络,同参数重试 → 应成功且用后销毁
fake.fail_on.clear()
with no_tty():
    r2 = scnet_client.submit_job("s6case", [f1], "debug", 1, 32, "12:00:00")
gone = not os.path.exists(ap_path)
print("同参数重试: submitted=%r 批准后销毁=%r" % (r2["submitted"], gone))

verdict("ACCEPT", "s6_midfail_approval_retained",
        "中途失败批准保留=%r(设计: 注释明示提交成功才销毁,30min内同参数可重试); "
        "重试成功=%r, 用后销毁=%r; 异参数重试由s4证必拒,不构成绕过"
        % (kept, r2["submitted"], gone))
assert kept and r2["submitted"] and gone
sys.exit(0)
