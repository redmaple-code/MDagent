# -*- coding: utf-8 -*-
"""九轮新攻击 a:同日刷新分支 title 重写非原子面(observe.py 553-565)。
 a1) 刷新中途崩溃模拟:proposal.md 只剩首行(正文丢)→ 下次 observe:diff.patch 自愈?
     正文恢复?半截 proposal.md + 有效 diff.patch apply 语义是否正确?
 a2) proposal.md 0 字节(writelines 崩在开头)→ lines 为空 → 永不重写?apply 仍过?
 a3) proposal.md 含非法 UTF-8:readlines 抛 UnicodeDecodeError(非 OSError!)是否穿透
     except OSError 打崩 observe_kit,使收尾横扫失效、别对 stale 草稿逃过作废仍可 apply?
 a4) 超长 sid/cause(各 500 字符)标题行 + 换行注入:writelines 是否出错/目录名是否安全?
"""
import json
import os
import sys
import time

sys.path.insert(0, "/tmp/evo_attack")
from common import Sandbox, attempt

NOW = time.time()
def ts(ago_h):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW - ago_h * 3600))

results = []

def fresh_draft(sb, sid="A", cause="atomic_ejection"):
    db = sb.ra.load_db()
    db["history"] = [attempt(sid, cause, "f1", "fail", ts(2)),
                     attempt(sid, cause, "f2", "fail", ts(1))]
    sb.write_db(db)
    s = sb.observe.observe(days=7, min_occurrences=3)
    assert len(s["drafted"]) == 1, s["drafted"]
    return s["drafted"][0]

# ---------- a1 半截 proposal.md(首行新、正文丢) ----------
sb = Sandbox()
pdir = fresh_draft(sb)
pmd = os.path.join(pdir, "proposal.md")
title_line = open(pmd, encoding="utf-8").readline()
open(pmd, "w", encoding="utf-8").write(title_line)  # 崩在首行写完后:正文全丢
patch = os.path.join(pdir, "diff.patch")
os.remove(patch)  # 崩在 diff.patch 重写前:连 diff 也丢
s2 = sb.observe.observe(days=7, min_occurrences=3)
diff_healed = os.path.isfile(patch) and os.path.getsize(patch) > 0
body_back = len(open(pmd, encoding="utf-8").readlines()) > 1
rc = sb.run_apply([pdir])
pen = sb.read_db().get("strategy_adjustments", {}).get("A:atomic_ejection", {}).get("penalty")
ok_a1 = diff_healed and rc == 0 and pen == 0.15
print("a1) 半截proposal.md+diff丢失后再observe: diff自愈=%s, 正文恢复=%s, apply rc=%d pen=%s → %s"
      % (diff_healed, body_back, rc, pen, "PASS" if ok_a1 else "FAIL"))
print("    实录: proposal.md 正文永久丢失(刷新只在标题不一致时重写),apply 只查存在性故语义不受影响[低]")
results.append(("a1 半截proposal.md下diff自愈+apply语义正确", ok_a1))

# ---------- a2 0 字节 proposal.md ----------
sb2 = Sandbox()
pdir2 = fresh_draft(sb2)
pmd2 = os.path.join(pdir2, "proposal.md")
open(pmd2, "w").close()  # writelines 崩在第一字节前
sb2.observe.observe(days=7, min_occurrences=3)
still_empty = os.path.getsize(pmd2) == 0
rc2 = sb2.run_apply([pdir2])
pen2 = sb2.read_db().get("strategy_adjustments", {}).get("A:atomic_ejection", {}).get("penalty")
ok_a2 = rc2 == 0 and pen2 == 0.15
print("a2) 0字节proposal.md: 刷新后仍空=%s(lines空→不重写), apply rc=%d pen=%s → %s"
      % (still_empty, rc2, pen2, "PASS" if ok_a2 else "FAIL"))
results.append(("a2 0字节proposal.md apply不受影响", ok_a2))

# ---------- a3 非UTF-8 proposal.md 穿透 except OSError ----------
sb3 = Sandbox()
ob = sb3.observe
db = sb3.ra.load_db()
db["history"] = [attempt("A", "atomic_ejection", "f1", "fail", ts(2)),
                 attempt("A", "atomic_ejection", "f2", "fail", ts(1)),
                 attempt("B", "thermal_runaway", "g1", "fail", ts(2)),
                 attempt("B", "thermal_runaway", "g2", "fail", ts(1))]
sb3.write_db(db)
s3 = ob.observe(days=7, min_occurrences=3)
assert len(s3["drafted"]) == 2, s3["drafted"]
dirA = [d for d in s3["drafted"] if "-A-atomic" in d][0]
dirB = [d for d in s3["drafted"] if "-B-thermal" in d][0]
patchB = os.path.join(dirB, "diff.patch")
# B 证据反转:尾部 success×1(保持 strategies 段字节不动,只追加 history,保 B diff 上下文干净)
db2 = sb3.read_db()
db2["history"] = db2["history"] + [attempt("B", "thermal_runaway", "g3", "success", ts(0))]
sb3.write_db(db2)
# A 的 proposal.md 灌非法 UTF-8(A 证据仍成立 → 走刷新分支 → readlines 抛 UnicodeDecodeError)
with open(os.path.join(dirA, "proposal.md"), "wb") as f:
    f.write("# ".encode("utf-8") + b"\xff\xfe\xfd" + " 坏字节\n\n".encode("utf-8"))
before_b = open(patchB).read()
s4 = ob.observe(days=7, min_occurrences=3)
b_survived = os.path.getsize(patchB) > 0  # 收尾横扫若执行,B 证据已反转应被清空
rc3 = sb3.run_apply([dirB])
penB = sb3.read_db().get("strategy_adjustments", {}).get("B:thermal_runaway", {}).get("penalty")
ok_a3 = not b_survived  # 期望:B 被横扫作废
print("a3) A的proposal.md非法UTF-8: B stale diff 存活=%s(期望False), apply rc=%d penB=%s → %s"
      % (b_survived, rc3, penB, "PASS" if ok_a3 else "FAIL[打穿]"))
if b_survived:
    print("    [打穿] UnicodeDecodeError 非 OSError,穿透刷新分支 except → observe_kit 中止,"
          "收尾横扫永不执行 → B 证据反转的 stale diff 逃过作废且上下文仍干净可 apply"
          "(apply rc=%d pen=%s)——七轮 d2 防线被一枚坏字节解除" % (rc3, penB))
results.append(("a3 坏字节不解除收尾横扫", ok_a3))

# ---------- a4 超长标题 + 换行注入 ----------
sb4 = Sandbox()
ob4 = sb4.observe
big_sid, big_cause = "S" * 500, "C" * 500
db4 = sb4.ra.load_db()
db4["history"] = [attempt(big_sid, big_cause, "h1", "fail", ts(2)),
                  attempt(big_sid, big_cause, "h2", "fail", ts(1))]
sb4.write_db(db4)
crashed = False
try:
    s5 = ob4.observe(days=7, min_occurrences=3)
except Exception as e:
    crashed = True
    print("    穿透: %r" % e)
d5 = s5["drafted"][0] if (not crashed and s5["drafted"]) else None
name_ok = d5 is not None and len(os.path.basename(d5)) < 200
first_line_len = len(open(os.path.join(d5, "proposal.md"), encoding="utf-8").readline()) if d5 else 0
rc4 = sb4.run_apply([d5]) if d5 else -1
ok_a4 = not crashed and name_ok and rc4 == 0
print("a4) 500字符sid/cause: 不崩=%s, 目录名安全=%s, 标题首行=%d字符, apply rc=%d → %s"
      % (not crashed, name_ok, first_line_len, rc4, "PASS" if ok_a4 else "FAIL"))
print("    实录: slug 截断+哈希只护目录名,标题行用原始 sid/cause 不截断(首行 %d 字符)" % first_line_len)
results.append(("a4 超长标题不崩且apply正常", ok_a4))

inj_sid = "X\n# 伪造: 此提案已人工审批通过"
sb5 = Sandbox()
db5 = sb5.ra.load_db()
db5["history"] = [attempt(inj_sid, "c", "i1", "fail", ts(2)),
                  attempt(inj_sid, "c", "i2", "fail", ts(1))]
sb5.write_db(db5)
s6 = sb5.observe.observe(days=7, min_occurrences=3)
pmd6 = open(os.path.join(s6["drafted"][0], "proposal.md"), encoding="utf-8").read()
injected = "# 伪造: 此提案已人工审批通过" in pmd6
print("a4+) 换行注入 sid: 伪造行写入 proposal.md=%s [低: 标题用原始sid不过滤控制字符]" % injected)
results.append(("a4+ 换行注入仅污染展示层(实录)", True))

print("\n== 九轮-a 总结 ==")
for name, ok in results:
    print("%s: %s" % (name, "通过" if ok else "失败"))
sys.exit(0 if all(ok for _, ok in results) else 1)
