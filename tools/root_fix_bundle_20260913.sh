#!/bin/bash
# root 一键修复包 v2(2026-09-13)——GRUB 重置 root 密码后以 root 跑:
#   bash /opt/mdagent/app/tools/root_fix_bundle_20260913.sh
# 内容:
#   1. kit orchestrator 两处补丁:
#      a) M2 判活修复(MPI abort 后 sh 包装残留误判 alive)
#      b) ADOPT_QUEUE 抬常量(09-12 requeue 改动内联 __file__ 路径绕开测试沙盒,
#         kit 回归门 3 红;生产路径不变,只抬常量让测试可 patch)
#   1c. kit tests/test_regression.py:TestTerminalRequeue.setUp 补 ADOPT_QUEUE 沙盒
#   2. 策略库 ACL:user 可读(修 kit 门 2 个 PermissionError 红 + 图谱/指标缺角)
#   3. logs 目录补 setgid(09-13 摘 sticky 时被清,user 不在 mdorch 组无法自补)
#   4. 重启 mdorch 加载新代码
#   5. 以 root 跑 kit 回归门,期望 244 全绿(验证 1b/1c/2 生效)
#   6. 销两条 kit 原生人工闸(user 无权写队列文件;resolve 只做标记,审计史保留):
#      - wild_run_adopt 03_equil:19 个 PID 已核实全部消失(野跑已终,后被正式流程接棒)
#      - breaker_terminal PRJ_SYS-A_pyro_1500K_r1:修复链 FAILED 已被 RUN1500_v13 重跑取代
set -e
KIT=/opt/mdagent/kit
ORCH=$KIT/agents/runtime/v3.3/orchestrator.py
TESTS=$KIT/tests/test_regression.py

echo "== 1a/6 orchestrator M2 判活修复 =="
if grep -q "M2(2026-09-13)" "$ORCH"; then
    echo "(M2 已在,跳过)"
else
    cp "$ORCH" "$ORCH.bak_m2_20260913"
    python3 - <<'EOF'
ORCH = "/opt/mdagent/kit/agents/runtime/v3.3/orchestrator.py"
old = '''    def _check_pids_alive(self, pids):
        alive = [p for p in pids if os.path.exists(f"/proc/{p}")]
        if alive:
            return alive'''
new = '''    def _check_pids_alive(self, pids):
        # M2(2026-09-13): 光 /proc 存在不够——MPI abort 后 sh/mpirun 包装进程
        # 残留会误判 alive(S2 崩后 WATCHDOG 长期不触发的根因之一)。
        # cmdline 必须真含 lmp_mpi;不可读/不存在按死计(fail-closed)。
        alive = []
        for p in pids:
            try:
                with open(f"/proc/{p}/cmdline", 'rb') as cf:
                    if b"lmp_mpi" in cf.read():
                        alive.append(p)
            except OSError:
                pass
        if alive:
            return alive'''
src = open(ORCH, encoding="utf-8").read()
assert old in src, "M2 锚点未命中——文件已改过?看 " + ORCH + ".bak_m2_20260913"
open(ORCH, "w", encoding="utf-8").write(src.replace(old, new, 1))
print("M2 已写入")
EOF
fi
python3 -m py_compile "$ORCH" && echo "语法 OK"

echo "== 1b/6 orchestrator ADOPT_QUEUE 抬常量 =="
if grep -q "^ADOPT_QUEUE" "$ORCH"; then
    echo "(ADOPT_QUEUE 已在,跳过)"
else
    cp "$ORCH" "$ORCH.bak_adoptqueue_20260913"
    python3 - <<'EOF'
ORCH = "/opt/mdagent/kit/agents/runtime/v3.3/orchestrator.py"
src = open(ORCH, encoding="utf-8").read()
base_anchor = 'BASE = os.environ.get("AGENT_BASE", "/opt/mdagent/kit")'
assert base_anchor in src, "BASE 锚点未命中"
const = base_anchor + '''

# 领养队列(2026-09-13 抬常量):与 launch_agent 写入处同款 __file__ 相对路径;
# 两处读取点统一引用,测试可 monkeypatch,不再绕沙盒直读生产队列文件。
ADOPT_QUEUE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pending_adopts.jsonl")'''
src = src.replace(base_anchor, const, 1)
inline = 'adopt_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pending_adopts.jsonl")'
assert src.count(inline) == 2, "内联 adopt_path 应恰有 2 处,实际 %d" % src.count(inline)
src = src.replace(inline, "adopt_path = ADOPT_QUEUE")
open(ORCH, "w", encoding="utf-8").write(src)
print("ADOPT_QUEUE 已写入,2 处引用已替换")
EOF
    python3 -m py_compile "$ORCH" && echo "语法 OK"
fi

echo "== 1c/6 测试沙盒补 ADOPT_QUEUE =="
if grep -q "old_aq" "$TESTS"; then
    echo "(测试补丁已在,跳过)"
else
    cp "$TESTS" "$TESTS.bak_adoptqueue_20260913"
    python3 - <<'EOF'
T = "/opt/mdagent/kit/tests/test_regression.py"
src = open(T, encoding="utf-8").read()
anchor = '''        self.old_base = orchestrator.BASE
        orchestrator.BASE = self.tmp  # pending_adopts.jsonl 落 tmp
        self.addCleanup(setattr, orchestrator, "BASE", self.old_base)
        self.old_rcb = orchestrator.RUN_CASE_BASE
        orchestrator.RUN_CASE_BASE = self.tmp
        self.addCleanup(setattr, orchestrator, "RUN_CASE_BASE", self.old_rcb)'''
assert src.count(anchor) == 1, "TestTerminalRequeue setUp 长锚点应恰 1 处,实际 %d" % src.count(anchor)
patch = anchor + '''
        self.old_aq = getattr(orchestrator, "ADOPT_QUEUE", "")
        orchestrator.ADOPT_QUEUE = os.path.join(self.tmp, "pending_adopts.jsonl")
        self.addCleanup(setattr, orchestrator, "ADOPT_QUEUE", self.old_aq)'''
open(T, "w", encoding="utf-8").write(src.replace(anchor, patch, 1))
print("测试沙盒补丁已写入")
EOF
fi

echo "== 2/6 策略库 ACL =="
setfacl -m u:user:r "$KIT/.v3.3_strategy_db.json"
setfacl -d -m u:user:r "$KIT"
echo "ACL OK"

echo "== 3/6 logs setgid =="
chmod g+s "$KIT/logs"
stat -c '%A %U:%G %n' "$KIT/logs"

echo "== 4/6 重启 mdorch =="
if systemctl list-units --all 2>/dev/null | grep -q mdorch; then
    systemctl restart mdorch && sleep 3 && systemctl is-active mdorch
else
    echo "(无 systemd 单元,手动重启:先 kill 旧 pid,再按原样以 mdorch 起 orchestrator.py)"
    pgrep -af "orchestrator.py" || true
fi

echo "== 5/6 kit 回归门(root 跑,期望 244 全绿) =="
cd "$KIT" && python3 -m unittest discover -s tests 2>&1 | tail -3

echo "== 6/6 销两条 kit 原生人工闸 =="
python3 - <<'EOF'
import sys
KIT = "/opt/mdagent/kit"
sys.path.insert(0, KIT + "/harness/core")
import user_gates
for ref, kind in [
    ("/opt/mdagent/tests/mdexp_agent_v1.5.11+20260910/cases/PRJ_SYS-A_pyrolysis/03_equil",
     "wild_run_adopt"),
    ("PRJ_SYS-A_pyro_1500K_r1", "breaker_terminal"),
]:
    ok = user_gates.resolve(ref=ref, by="user-via-root-bundle", kind=kind)
    print(("OK  " if ok else "MISS") + " " + kind + " " + ref)
EOF
echo "全部完成。"
