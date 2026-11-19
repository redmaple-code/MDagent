# -*- coding: utf-8 -*-
"""
分子模拟助手 · 主动推送驱动（纯代码，不经 LLM）
=================================================
定时(Task Scheduler/cron 每 N 分钟)跑一次:读模拟真相源 → 检测【新增】该报的事件 → 去重 → 微信。
复用 mcp/md_mcp.py 的双模式读取(local 快照 / ssh 远程 Linux)与 push/wechat_push.mjs(Server酱)。
只读、不改模拟、不下发操作 —— 只把"该你操心的事"推给你。
用法:
  python md_push_driver.py            # 正常:检测+去重+推送
  python md_push_driver.py --dry      # 干跑:只检测打印,不推送、不改 state
  python md_push_driver.py --test     # 发一条测试推送验证通道
"""
import sys, os, io, json, subprocess

# 环境免疫(Windows GBK)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "mcp"))
import md_mcp as M   # 复用双模式 read_json / read_tail（不触发它的 main）
import paths

# 数据根外置(2026-08-29);惰性解析:import 后改 MDSIM_DATA_ROOT 也生效(对抗审计 F4)
def _state_file(make=True):
    # make=False 供只读路径(--dry 纯检测不得在数据根建空 state/ 目录,三轮对抗)
    return os.path.join(paths.state_dir(make=make), "push_state.json")

def _push_log():
    return os.path.join(paths.scratch_dir(), "push.log")

WECHAT_MJS = os.path.join(HERE, "wechat_push.mjs")

EXPLOSION_K = 5000.0   # 温度 > 5000K 视为能量爆炸

def _log(msg):
    try:
        import time
        with io.open(_push_log(), "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass

def load_state():
    try:
        return json.load(io.open(_state_file(make=False), encoding="utf-8"))
    except Exception:
        return {"cases": {}, "phase": None}

def save_state(s):
    # tmp + os.replace:崩溃撕裂后 load_state 兜底空基线会重复推送旧事件(五轮对抗)
    path = _state_file()
    tmp = path + ".tmp.%d" % os.getpid()
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(s, ensure_ascii=False, indent=2))
    os.replace(tmp, path)

def detect():
    """返回 (events:list[str], cur_state:dict)。只报【新增】事件(与上次 state 比)。"""
    prev = load_state()
    bus = M.read_json("status_bus.json") or {}
    st = M.read_json(".v3.3_state.json") or {}
    events = []
    cur = {"cases": {}, "phase": st.get("phase")}
    prev_cases = prev.get("cases", {})
    for cid, cv in (bus.get("cases") or {}).items():
        if not isinstance(cv, dict):
            continue
        status = cv.get("status")
        temp = cv.get("temp_k")
        step = cv.get("step")
        cur["cases"][cid] = {"status": status, "temp": temp}
        pc = prev_cases.get(cid, {})
        # 每个事件带严重度:serious=True 升级给用户，False 只进处理层/日志
        # ① 新变 CRITICAL / FAILED —— 严重
        if status in ("CRITICAL", "FAILED") and pc.get("status") not in ("CRITICAL", "FAILED"):
            events.append(("🔴 %s 变 %s(步 %s，温度 %sK)" % (cid, status, step, temp), True))
        # ② 温度爆炸(新越过阈值) —— 严重
        try:
            if temp is not None and float(temp) > EXPLOSION_K and float(pc.get("temp") or 0) <= EXPLOSION_K:
                events.append(("🔥 %s 温度爆炸 %.0fK(能量失控，轨迹已非物理)" % (cid, float(temp)), True))
        except Exception:
            pass
        # ③ 跑完 —— 常规(好消息，不打扰你)
        if status == "COMPLETED" and pc.get("status") != "COMPLETED":
            events.append(("✅ %s 跑完了(步 %s)" % (cid, step), False))
    # ④ 编排器进入 RECOVER —— 常规(编排器在自愈，进处理层；反复失败才升级)
    if st.get("phase") == "RECOVER" and prev.get("phase") != "RECOVER":
        events.append(("⚠ 编排器进入 RECOVER(自愈循环，编排器在处理)", False))
    # ④.5 GROMACS 运行(2026-09-10,agent loop 双引擎):跑完(常规)/中途停止
    #    (严重,进程死)/温度爆炸(严重)。扫描失败保留 prev 基线不清空(同闸 D1 纪律)。
    try:
        gmx_runs = M.gmx_scan.scan_gromacs_runs()
    except Exception:
        gmx_runs = None
    if gmx_runs is None:
        cur["gmx"] = prev.get("gmx", {})
        cur["gmx_wild"] = prev.get("gmx_wild", [])
    else:
        prev_gmx = prev.get("gmx", {}) if isinstance(prev.get("gmx"), dict) else {}
        prev_wild = set(prev.get("gmx_wild") or [])
        cur["gmx"] = {}
        cur["gmx_wild"] = []
        for r in gmx_runs:
            rid = r.get("run_id")
            if not rid:
                continue
            # 执法与跑完/中途停判定只看 mdrun;packmol/grompp 建模进程只展示
            # (mdrun_alive 缺省回退 alive,兼容旧扫描器)
            ma = r.get("mdrun_alive")
            alive = bool(r.get("alive")) if ma is None else bool(ma)
            pct = r.get("pct")
            temp = r.get("temp")
            cur["gmx"][rid] = {"alive": alive, "pct": pct, "temp": temp}
            pg = prev_gmx.get(rid, {})
            ns = r.get("time_ns")
            # ⓪ 野跑(alive 且未接管):"不走编排器不能跑"。auto_kill_wild 开 →
            #    直接 SIGTERM(温和停,gromacs 写终态检查点可续跑;pid 已按 cwd 锁定
            #    不误杀);关 → 人工闸告警。同一野跑只处理/告警一次。
            if alive and not r.get("adopted"):
                cur["gmx_wild"].append(rid)
                if rid not in prev_wild:
                    if M.gmx_scan.wild_auto_kill():
                        killed, failed = 0, 0
                        for pid in M.gmx_scan.get_gmx_pids(r.get("dir") or ""):
                            try:
                                os.kill(pid, 15)  # SIGTERM:走完当前步写终态检查点再退
                                killed += 1
                            except Exception:
                                failed += 1
                        _log("WILD-KILL %s SIGTERM ok=%d fail=%d" % (rid, killed, failed))
                        if killed:
                            events.append(("🛑 野跑 GROMACS %s 已按规停掉"
                                           "(SIGTERM×%d,%s ns;.cpt 检查点可续跑,"
                                           "要放行请先接管再重启)" % (rid, killed, ns), True))
                        else:
                            events.append(("🚨 野跑 GROMACS %s 停掉失败(%s ns)——"
                                           "请人工处置" % (rid, ns), True))
                    else:
                        events.append(("🚨 野跑 GROMACS %s(%s ns,未经编排器接管)——"
                                       "按规须接管进监控或杀掉,请拍板" % (rid, ns), True))
            if pg.get("alive") and not alive:
                if pct is not None and pct >= 100:
                    # 跑完 —— 常规(好消息,不打扰)
                    events.append(("✅ GROMACS %s 跑完了(%s ns)" % (rid, ns), False))
                else:
                    # 中途停止 —— 严重(进程死)
                    events.append(("🔴 GROMACS %s 中途停止(%s ns,进度 %s%%,进程消失)" % (
                        rid, ns, pct), True))
            try:
                # 只在活着时判爆炸:死 run 日志尾的高温是历史残骸(首轮基线轰炸教训)
                if alive and temp is not None and float(temp) > EXPLOSION_K and \
                        float(pg.get("temp") or 0) <= EXPLOSION_K:
                    events.append(("🔥 GROMACS %s 温度爆炸 %.0fK(能量失控,轨迹已非物理)" % (
                        rid, float(temp)), True))
            except Exception:
                pass
    # ⑤ 新增待人工闸(2026-09-07,借鉴 LoopX user-gate 露出):breaker 终态/野跑待
    #    接管/演化 human_review 提案入队即推——不再只在飞书一闪而过。销项不报。
    gates_doc = M.read_json(".v3.3_user_gates.json")
    if gates_doc is None:
        # 24 轮审计:读失败(损坏/ssh 瞬断)不得清空 known_gates 基线——
        # 否则下次好读会把全部 pending 闸当"新"再爆一轮推送
        cur["known_gates"] = prev.get("known_gates", [])
        return events, cur
    gates = gates_doc.get("gates") or []
    pending_ids = set()
    for g in gates:
        if isinstance(g, dict) and g.get("status") == "pending" and g.get("id"):
            pending_ids.add(g["id"])
            if g["id"] not in prev.get("known_gates", []):
                events.append(("🚪 新待人工闸 %s [%s]: %s" % (
                    g["id"], g.get("kind"), str(g.get("question"))[:120]), True))
    cur["known_gates"] = sorted(pending_ids)
    return events, cur

def push_wechat(text):
    try:
        r = subprocess.run(["node", WECHAT_MJS, text], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=40)
        ok = (r.returncode == 0)
        _log("PUSH %s | out=%s" % ("OK" if ok else "FAIL", (r.stdout or r.stderr or "")[:200].replace("\n", " ")))
        return ok
    except Exception as ex:
        _log("PUSH ERR %s" % str(ex)[:160])
        return False

def route_to_agent(events, src):
    """常规事件进【处理层/号智能体】。当前落地为 _agent_inbox.jsonl 记录——
    号智能体接入方式待用户定(dsh 助手 headless 巡检 / 单独微信 bot 号 / 群),
    接上后改本函数把 events 转给它;严重事件才走 push_wechat 升级给用户。"""
    import time
    try:
        with io.open(os.path.join(paths.events_dir(), "agent_inbox.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "src": src, "events": events},
                               ensure_ascii=False) + "\n")
    except Exception:
        pass

def main():
    dry = "--dry" in sys.argv
    test = "--test" in sys.argv
    if test:
        ok = push_wechat("【分子模拟助手】严重告警通道测试 —— 收到即通道正常。以后只有【严重】情况(温度爆炸/CRITICAL/进程死)才推你个人;常规的进处理层不打扰你。")
        print("测试推送:", "OK" if ok else "FAIL(看 %s)" % _push_log())
        return
    events, cur = detect()
    src = "本地快照" if M.MODE == "local" else "远程Linux实时"
    serious = [e for e, s in events if s]
    routine = [e for e, s in events if not s]
    if not events:
        print("[%s] 无新增事件(%s)" % ("dry" if dry else "run", src))
        if not dry:
            save_state(cur)   # 仍更新基线，避免下次误报旧状态
        return
    # 常规 → 处理层(号智能体);严重 → 你个人微信
    if routine:
        print("→ 处理层/号智能体(常规，不推你):\n  " + "\n  ".join(routine))
        if not dry:
            route_to_agent(routine, src)
    if serious:
        body = "【分子模拟助手 · 严重告警】(%s)\n" % src + "\n".join(serious) + \
               "\n———\n只读检测，未下发操作;处理请走 claude_interface 审计通道并确认。"
        print("→ 你个人微信(严重):\n" + body)
        if not dry:
            push_wechat(body)
    else:
        print("(无严重事件，不打扰你个人)")
    if not dry:
        save_state(cur)

if __name__ == "__main__":
    main()
