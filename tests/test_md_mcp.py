# -*- coding: utf-8 -*-
"""dsh-agent MCP 桥回归门:sim_pipeline_check 契约 + stdio 冒烟。

运行(仓库根或本目录均可):
    python3.11 -m unittest discover -s tests -v     # 在 dsh-agent/ 下
被测对象:mcp/md_mcp.py(工具注册表)、mcp/pipeline_check.py(确定性巡检流水线)。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MCP_DIR = os.path.join(os.path.dirname(HERE), "mcp")
if MCP_DIR not in sys.path:
    sys.path.insert(0, MCP_DIR)

import md_mcp  # noqa: E402
import pipeline_check  # noqa: E402


def thermo_rows(n, start=0, step=1000, temp=300.0):
    # monitor_agent.parse_thermo_records 要求 ≥8 列
    return "".join("%d %.1f -5000.0 0 0 0 1.0 1000.0\n" % (start + i * step, temp)
                   for i in range(n))


class TestToolRegistry(unittest.TestCase):
    def test_tools_handlers_consistent(self):
        tool_names = {t["name"] for t in md_mcp.TOOLS}
        self.assertEqual(tool_names, set(md_mcp.HANDLERS.keys()),
                         "TOOLS 与 HANDLERS 键不一致")
        for t in md_mcp.TOOLS:
            self.assertIn("description", t)
            self.assertIn("inputSchema", t)

    def test_pipeline_tool_registered(self):
        self.assertIn("sim_pipeline_check", md_mcp.HANDLERS)
        desc = next(t["description"] for t in md_mcp.TOOLS if t["name"] == "sim_pipeline_check")
        self.assertIn("流程硬约束", desc, "状态问题必调的硬约束须写进工具描述")


class TestPipelineCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.prod = os.path.join(self.tmp, "prod")
        os.makedirs(self.prod)
        self.workdir = os.path.join(self.tmp, "pipeline")
        # 事件流重定向到沙盒,绝不写真实 events/pipeline.jsonl
        self.old_events = pipeline_check.PIPELINE_EVENTS_LOG
        pipeline_check.PIPELINE_EVENTS_LOG = os.path.join(self.tmp, "events.jsonl")
        with open(os.path.join(self.prod, "log.run"), "w", encoding="utf-8") as f:
            f.write(thermo_rows(20))

    def tearDown(self):
        pipeline_check.PIPELINE_EVENTS_LOG = self.old_events
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_e2e_and_snapshot_deltas(self):
        r1 = pipeline_check.run(self.prod, "log.run", "case_t", workdir=self.workdir)
        self.assertEqual(r1["status"], "RUNNING")
        self.assertEqual(r1["source"], "pipeline_check")
        self.assertEqual(r1["step"], 19000)
        self.assertIsNone(r1["deltas"], "首次无快照,deltas 应为 None")
        # 推进 log 后再跑,deltas 应反映步数变化
        with open(os.path.join(self.prod, "log.run"), "a", encoding="utf-8") as f:
            f.write(thermo_rows(5, start=20000))
        r2 = pipeline_check.run(self.prod, "log.run", "case_t", workdir=self.workdir)
        self.assertIsNotNone(r2["deltas"])
        self.assertEqual(r2["deltas"]["step"]["delta"], 5000)

    def test_missing_log_returns_no_log_not_raise(self):
        r = pipeline_check.run(self.tmp, None, "case_t", workdir=self.workdir)
        self.assertEqual(r["status"], "NO_LOG")
        self.assertNotIn("risk_score", r, "NO_LOG 不得给出 risk_score=0 的假安全感")

    def test_bad_case_id_rejected(self):
        r = pipeline_check.run(self.prod, "log.run", "../escape", workdir=self.workdir)
        self.assertEqual(r["status"], "PIPELINE_ERROR")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "snap_..json")))

    def test_event_stream_appended_on_success(self):
        # 事件流(2026-09-05):每次调用终态追加一条;snap 覆盖不冲失效史
        pipeline_check.run(self.prod, "log.run", "case_t", workdir=self.workdir)
        pipeline_check.run(self.prod, "log.run", "case_t", workdir=self.workdir)
        rows = [json.loads(ln) for ln in open(pipeline_check.PIPELINE_EVENTS_LOG)]
        self.assertEqual(len(rows), 2, "每次调用追加一条,不覆盖")
        for row in rows:
            self.assertEqual(row["case_id"], "case_t")
            self.assertEqual(row["status"], "RUNNING")
            self.assertIsInstance(row["ts"], float)
            self.assertEqual(row["step"], 19000)

    def test_event_stream_records_pipeline_error(self):
        # PIPELINE_ERROR 从不写 snap(异常/残缺不污染快照),但必须进事件流——
        # 否则 observe 的 pipeline_error 复发统计永远饿着
        r = pipeline_check.run(self.prod, "log.run", "../escape", workdir=self.workdir)
        self.assertEqual(r["status"], "PIPELINE_ERROR")
        rows = [json.loads(ln) for ln in open(pipeline_check.PIPELINE_EVENTS_LOG)]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "PIPELINE_ERROR")
        self.assertIn("error", rows[0])

    def test_event_stream_records_no_log(self):
        r = pipeline_check.run(self.tmp, None, "case_t", workdir=self.workdir)
        self.assertEqual(r["status"], "NO_LOG")
        rows = [json.loads(ln) for ln in open(pipeline_check.PIPELINE_EVENTS_LOG)]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "NO_LOG")

    def test_event_stream_failure_never_blocks(self):
        # 事件流写失败(目录不可写)绝不允许阻断巡检主链
        pipeline_check.PIPELINE_EVENTS_LOG = os.path.join("/proc", "forbidden", "x.jsonl")
        try:
            r = pipeline_check.run(self.prod, "log.run", "case_t", workdir=self.workdir)
            self.assertEqual(r["status"], "RUNNING")
        finally:
            pipeline_check.PIPELINE_EVENTS_LOG = os.path.join(self.tmp, "events.jsonl")

    def test_error_result_does_not_poison_snapshot(self):
        r1 = pipeline_check.run(self.prod, "log.run", "case_t", workdir=self.workdir)
        self.assertEqual(r1["status"], "RUNNING")
        # 制造一次坏结果(log 消失 → NO_LOG),快照不得被覆盖
        os.remove(os.path.join(self.prod, "log.run"))
        r2 = pipeline_check.run(self.prod, None, "case_t", workdir=self.workdir)
        self.assertEqual(r2["status"], "NO_LOG")
        with open(os.path.join(self.prod, "log.run"), "w", encoding="utf-8") as f:
            f.write(thermo_rows(25, start=20000))
        r3 = pipeline_check.run(self.prod, "log.run", "case_t", workdir=self.workdir)
        self.assertEqual(r3["deltas"]["step"]["previous"], 19000,
                         "NO_LOG 不得污染既有快照基线")

    def test_missing_prod_dir_returns_error_not_raise(self):
        r = pipeline_check.run("", None, "case_t", workdir=self.workdir)
        self.assertEqual(r["status"], "PIPELINE_ERROR")

    def test_mcp_handler_falls_back_and_disciplines(self):
        old_workdir = pipeline_check.DEFAULT_WORKDIR
        pipeline_check.DEFAULT_WORKDIR = self.workdir
        try:
            r = md_mcp.sim_pipeline_check(
                {"prod_dir": self.prod, "log_file": "log.run", "case_id": "case_t"})
        finally:
            pipeline_check.DEFAULT_WORKDIR = old_workdir
        self.assertEqual(r["status"], "RUNNING")
        self.assertIn("_discipline", r)

    def test_schedule_injected_to_monitor(self):
        # 按项目统一:注入 1200K 平台,300K 恒温数据持续偏离 → CRITICAL,
        # 证明 pipeline_check → in.json → monitor_agent 链路把配置温度程序带到了判定层
        sentinel = [[0, 100000, 1200.0, 1200.0]]
        old = pipeline_check._resolve_schedule
        pipeline_check._resolve_schedule = lambda case_id: sentinel
        try:
            r = pipeline_check.run(self.prod, "log.run", "case_sched",
                                   workdir=self.workdir)
        finally:
            pipeline_check._resolve_schedule = old
        self.assertEqual(r["status"], "CRITICAL")
        kinds = {a["type"] for a in r["anomalies"]}
        self.assertIn("TEMPERATURE_SUSTAINED", kinds)

    def test_resolve_schedule_falls_back_to_global_config(self):
        # 未登记 case 用全局 md_config.json schedule(canonical ramp,4 元段)
        sched = pipeline_check._resolve_schedule("no-such-case")
        self.assertTrue(sched and all(len(s) == 4 for s in sched))

    def test_concurrent_same_ms_no_tmp_collision(self):
        # 冻结时间戳 + 同进程两线程并发:in/out 临时文件不得互抢/串数据(三轮对抗 R1)
        import threading
        from unittest import mock
        results = {}

        def go(cid):
            results[cid] = pipeline_check.run(self.prod, "log.run", cid,
                                              workdir=self.workdir)
        with mock.patch.object(pipeline_check.time, "time",
                               return_value=1700000000.0):
            threads = [threading.Thread(target=go, args=(c,))
                       for c in ("case_a", "case_b")]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        for cid in ("case_a", "case_b"):
            self.assertEqual(results[cid]["status"], "RUNNING")
            self.assertEqual(results[cid]["case_id"], cid,
                             "并发结果串了 case 数据")
        leftovers = [n for n in os.listdir(self.workdir)
                     if n.startswith(("in_", "out_"))]
        self.assertEqual(leftovers, [], "临时文件泄漏: %r" % leftovers)


class TestStdioSmoke(unittest.TestCase):
    def test_initialize_list_call(self):
        prod = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, prod, True)
        data_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, data_root, True)
        with open(os.path.join(prod, "log.run"), "w", encoding="utf-8") as f:
            f.write(thermo_rows(12))
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "sim_pipeline_check",
                        "arguments": {"prod_dir": prod, "log_file": "log.run",
                                      "case_id": "stdio_t"}}},
        ]
        payload = "\n".join(json.dumps(r) for r in requests) + "\n"
        env = dict(os.environ, MDSIM_DATA_ROOT=data_root)
        proc = subprocess.run(
            [sys.executable, os.path.join(MCP_DIR, "md_mcp.py")],
            input=payload, capture_output=True, text=True, timeout=60, env=env,
        )
        self.assertEqual(proc.returncode, 0, "md_mcp 进程异常退出: %s" % proc.stderr[-400:])
        replies = {}
        for line in proc.stdout.splitlines():
            line = line.strip()
            if line:
                msg = json.loads(line)
                replies[msg["id"]] = msg
        self.assertEqual(replies[1]["result"]["serverInfo"]["name"], "mdsim")
        names = {t["name"] for t in replies[2]["result"]["tools"]}
        self.assertIn("sim_pipeline_check", names)
        content = replies[3]["result"]["structuredContent"]
        self.assertEqual(content["status"], "RUNNING")
        self.assertEqual(content["source"], "pipeline_check")
        # 快照须落在临时数据根,代码目录不得出现新数据文件
        self.assertTrue(os.path.exists(os.path.join(data_root, "scratch", "pipeline", "snap_stdio_t.json")))
        self.assertFalse(os.path.exists(os.path.join(MCP_DIR, "_pipeline", "snap_stdio_t.json")))
        # 事件流(追加式失效史)同样落在临时数据根
        ev_path = os.path.join(data_root, "events", "pipeline.jsonl")
        rows = [json.loads(ln) for ln in open(ev_path)]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["case_id"], "stdio_t")
        self.assertEqual(rows[0]["status"], "RUNNING")


class TestReviewPacket(unittest.TestCase):
    """sim_review_packet(2026-09-07,LoopX review-packet 形状):组合既有真相源,
    分节容错永不抛,只读零写入。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # kit 侧:MDSIM_KIT_BASE 指到沙盒(read_json 每次调用现读 env)
        self.old_kit = os.environ.get("MDSIM_KIT_BASE")
        os.environ["MDSIM_KIT_BASE"] = self.tmp
        self.addCleanup(self._restore_env)
        # dsh 侧:事件流与 memory 目录沙盒,绝不碰真数据根
        self.old_pev = md_mcp.paths.pipeline_events_log
        md_mcp.paths.pipeline_events_log = lambda: os.path.join(self.tmp, "events.jsonl")
        self.addCleanup(setattr, md_mcp.paths, "pipeline_events_log", self.old_pev)
        self.old_memdir = md_mcp.paths.memory_dir
        md_mcp.paths.memory_dir = lambda make=True: self._memdir()
        self.addCleanup(setattr, md_mcp.paths, "memory_dir", self.old_memdir)

    def _restore_env(self):
        if self.old_kit is None:
            os.environ.pop("MDSIM_KIT_BASE", None)
        else:
            os.environ["MDSIM_KIT_BASE"] = self.old_kit

    def _memdir(self):
        d = os.path.join(self.tmp, "memory")
        os.makedirs(d, exist_ok=True)
        return d

    def _seed_kit(self, state=None, gates=None):
        if state is not None:
            with open(os.path.join(self.tmp, ".v3.3_state.json"), "w") as f:
                json.dump(state, f)
        if gates is not None:
            with open(os.path.join(self.tmp, ".v3.3_user_gates.json"), "w") as f:
                json.dump({"version": 1, "seq": len(gates), "gates": gates}, f)

    def test_handler_registered(self):
        self.assertIn("sim_review_packet", md_mcp.HANDLERS)
        names = {t["name"] for t in md_mcp.TOOLS}
        self.assertIn("sim_review_packet", names)

    def test_packet_shape(self):
        self._seed_kit(state={"phase": "MONITOR", "task": {"case_id": "c1"}},
                       gates=[{"id": "ug-1", "kind": "wild_run_adopt", "ref": "/x",
                               "question": "q", "status": "pending"}])
        r = md_mcp.sim_review_packet({})
        for k in ("context", "user_gates", "bus", "pipeline_events",
                  "proposals", "ledger", "_discipline"):
            self.assertIn(k, r)
        self.assertEqual(r["context"]["phase"], "MONITOR")
        self.assertEqual(r["user_gates"]["pending_count"], 1)
        self.assertEqual(r["user_gates"]["pending"][0]["id"], "ug-1")

    def test_missing_files_sections_error_never_raise(self):
        r = md_mcp.sim_review_packet({})  # 沙盒全空
        self.assertIsInstance(r, dict)
        # bus/gates 缺文件 → 空结构而非异常炸穿
        self.assertEqual(r["user_gates"]["pending_count"], 0)
        self.assertEqual(r["pipeline_events"]["tail"], [])

    def test_corrupt_queue_tolerated(self):
        with open(os.path.join(self.tmp, ".v3.3_user_gates.json"), "w") as f:
            f.write("{not json")
        r = md_mcp.sim_review_packet({})
        self.assertEqual(r["user_gates"]["pending_count"], 0)

    def test_proposals_human_pending_count(self):
        pdir = os.path.join(self._memdir(), "proposals", "2026-09-07-x")
        os.makedirs(pdir)
        with open(os.path.join(pdir, "meta.json"), "w") as f:
            json.dump({"tier": "human", "class": "monitor_logic"}, f)
        r = md_mcp.sim_review_packet({})
        self.assertEqual(r["proposals"]["human_pending"], 1)
        self.assertEqual(r["proposals"]["count"], 1)

    def test_readonly_no_new_files_in_kit_base(self):
        before = set()
        for root, _dirs, files in os.walk(self.tmp):
            for fn in files:
                before.add(os.path.join(root, fn))
        md_mcp.sim_review_packet({})
        after = set()
        for root, _dirs, files in os.walk(self.tmp):
            for fn in files:
                after.add(os.path.join(root, fn))
        # 只许 memory/ 沙盒因 proposals 扫描建目录,kit 基目录(junction tmp 根)零新文件
        new = {p for p in after - before if not p.startswith(self._memdir())}
        self.assertEqual(new, set(), "review_packet 不得在 kit 基目录写文件: %s" % new)

    def test_tail_jsonl_negative_n_clamped(self):
        # 24 轮审计(低):n=-1 会把 lines[-n:] 反成"掐头",尾 1 行变头 4999 行
        p = os.path.join(self.tmp, "big.jsonl")
        with open(p, "w") as f:
            for i in range(50):
                f.write(json.dumps({"i": i}) + "\n")
        rows = md_mcp._tail_jsonl(p, -1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["i"], 49, "负 n 反转了 tail 语义(24 轮回归)")
        rows = md_mcp._tail_jsonl(p, 99999)
        self.assertLessEqual(len(rows), 1000, "n 必须钳上限")
        rows = md_mcp._tail_jsonl(p, "abc")
        self.assertEqual(len(rows), 10, "非数值 n 回退默认 10,不得抛")

    def test_packet_bad_n_never_raises(self):
        r = md_mcp.sim_review_packet({"n": "abc"})
        self.assertIsInstance(r, dict)
        self.assertIn("context", r)


class TestContextSlim(unittest.TestCase):
    """工具面精简(2026-09-09,用户裁决 13→9):sim_strategy_log/sim_orchestrator_log
    并入 sim_context(section 参数);sim_recent_ops(与 recent_operations 纯重复)/
    open_monitor(看板用户自开)下线。调用证据:events/toolcalls.jsonl 63 次中
    四者各 ≤1 次。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.old_kit = os.environ.get("MDSIM_KIT_BASE")
        os.environ["MDSIM_KIT_BASE"] = self.tmp
        self.addCleanup(self._restore_env)
        logs = os.path.join(self.tmp, "logs")
        os.makedirs(logs)
        with open(os.path.join(self.tmp, ".v3.3_state.json"), "w") as f:
            json.dump({"phase": "MONITOR", "task": {"case_id": "c1"}}, f)
        with open(os.path.join(logs, "strategy_decisions.jsonl"), "w") as f:
            f.write(json.dumps({"ts": 1.0, "phase_from": "MONITOR", "phase_to": "RECOVER",
                                "trigger": "stall", "decision": "retry",
                                "details": {"strategy": "A", "attempt": 1}}) + "\n")
        with open(os.path.join(logs, "orchestrator_v3.3.jsonl"), "w") as f:
            f.write(json.dumps({"ts": 2.0, "level": "INFO", "msg": "OBSERVE tick"}) + "\n")
        with open(os.path.join(logs, "claude_operations.jsonl"), "w") as f:
            f.write(json.dumps({"ts": 3.0, "actor": "x", "action": "exec"}) + "\n")

    def _restore_env(self):
        if self.old_kit is None:
            os.environ.pop("MDSIM_KIT_BASE", None)
        else:
            os.environ["MDSIM_KIT_BASE"] = self.old_kit

    def test_dead_tools_removed(self):
        names = {t["name"] for t in md_mcp.TOOLS}
        for dead in ("sim_recent_ops", "open_monitor",
                     "sim_strategy_log", "sim_orchestrator_log"):
            self.assertNotIn(dead, names, "%s 应从 TOOLS 下线" % dead)
            self.assertNotIn(dead, md_mcp.HANDLERS, "%s 应从 HANDLERS 下线" % dead)

    def test_context_default_state_section(self):
        r = md_mcp.sim_context({})
        self.assertEqual(r["phase"], "MONITOR")
        self.assertEqual(r["task"]["case_id"], "c1")
        self.assertEqual(r["recent_operations"][0]["action"], "exec")

    def test_context_strategy_section(self):
        r = md_mcp.sim_context({"section": "strategy"})
        self.assertEqual(r["decisions"][0]["strategy"], "A")
        self.assertEqual(r["decisions"][0]["phase"], "MONITOR→RECOVER")
        self.assertIn("_discipline", r)

    def test_context_orchestrator_section(self):
        r = md_mcp.sim_context({"section": "orchestrator"})
        self.assertEqual(r["log"][0]["msg"], "OBSERVE tick")
        self.assertIn("_discipline", r)

    def test_context_unknown_section_falls_back_state(self):
        r = md_mcp.sim_context({"section": "bogus"})
        self.assertIn("phase", r)


GMX_TAIL = """\
   Step           Time
      24282000    48564.00000

   Energies (kJ/mol)
    Kinetic En.   Total Energy  Conserved En.    Temperature Pressure (bar)
    1.23456e+05  -5.67890e+05  -5.00000e+05   3.00000e+02   1.00000e+00
"""


class TestGmxStatus(unittest.TestCase):
    """sim_gmx_status:GROMACS 运行并入 agent loop 的只读面。"""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "mdruns")
        run = os.path.join(self.root, "gel", "run1")
        os.makedirs(run)
        with open(os.path.join(run, "prod.log"), "w") as f:
            f.write(GMX_TAIL)
        with open(os.path.join(run, "prod_stdout.log"), "w") as f:
            f.write("starting mdrun 'x'\n25000000 steps,  50000.0 ps.\n")
        self.cfg = os.path.join(self.tmp, "roots.json")
        with open(self.cfg, "w") as f:
            json.dump({"roots": [{"path": self.root, "type": "gromacs",
                                  "label": "t", "enabled": True}]}, f)
        self._old = os.environ.get("MONITOR_ROOTS_FILE")
        os.environ["MONITOR_ROOTS_FILE"] = self.cfg

    def tearDown(self):
        if self._old is None:
            os.environ.pop("MONITOR_ROOTS_FILE", None)
        else:
            os.environ["MONITOR_ROOTS_FILE"] = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_tool_registered(self):
        names = {t["name"] for t in md_mcp.TOOLS}
        self.assertIn("sim_gmx_status", names)
        self.assertIn("sim_gmx_status", md_mcp.HANDLERS)

    def test_returns_runs(self):
        r = md_mcp.HANDLERS["sim_gmx_status"]({})
        self.assertEqual(r["count"], 1)
        run = r["runs"][0]
        self.assertEqual(run["run_id"], os.path.join("gel", "run1"))
        self.assertEqual(run["step"], 24282000)
        self.assertEqual(run["total_steps"], 25000000)
        self.assertAlmostEqual(run["pct"], 97.1, places=1)
        self.assertIn("_discipline", r)

    def test_missing_config_returns_empty(self):
        os.environ["MONITOR_ROOTS_FILE"] = os.path.join(self.tmp, "nope.json")
        r = md_mcp.HANDLERS["sim_gmx_status"]({})
        self.assertEqual(r["runs"], [])
        self.assertEqual(r["count"], 0)

    def test_readonly_zero_touch(self):
        before = os.stat(self.cfg).st_mtime
        md_mcp.HANDLERS["sim_gmx_status"]({})
        self.assertEqual(os.stat(self.cfg).st_mtime, before, "配置文件被写动")


class TestSearchSessions(unittest.TestCase):
    """sim_search_sessions(2026-09-12,fable-5 conversation_search 借鉴):只读检索
    会话存档,zstd 流式解压,真实用户/助手行才可命中,缺档不抛。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.old_home = md_mcp.paths.dsh_home
        md_mcp.paths.dsh_home = lambda make=True: os.path.join(self.tmp, "dsh-home")
        self.addCleanup(setattr, md_mcp.paths, "dsh_home", self.old_home)

    def _mk_session(self, ws, sid, rows):
        """造一份 zstd 压缩会话档;zstd 缺席则跳过(真机恒有)。"""
        import shutil as _sh
        if not _sh.which("zstd"):
            self.skipTest("zstd 不可用")
        sd = os.path.join(self.tmp, "dsh-home", "sessions", ws, "session-" + sid)
        os.makedirs(sd, exist_ok=True)
        import subprocess
        with open(os.path.join(sd, "session.jsonl.zstd"), "wb") as f:
            subprocess.run(["zstd", "-"], input="\n".join(
                json.dumps(r, ensure_ascii=False) for r in rows).encode("utf-8"),
                stdout=f, check=True)

    def test_handler_registered(self):
        self.assertIn("sim_search_sessions", md_mcp.HANDLERS)
        self.assertIn("sim_search_sessions", {t["name"] for t in md_mcp.TOOLS})

    def test_finds_real_rows_only(self):
        self._mk_session("--ws--", "abc123", [
            {"type": "session", "id": "abc123", "cwd": "/tmp/实验室", "createdAt": 1},
            {"type": "user/message", "seq": 1, "time": 2, "data": {
                "role": "user", "source": {"kind": "user"},
                "content": [{"type": "text", "text": "QEq 不收敛怎么修?"}]}},
            {"type": "user/message", "seq": 2, "time": 3, "data": {  # 插件提醒,须排除
                "role": "user", "source": {"kind": "plugin", "plugin": "x"},
                "content": [{"type": "text", "text": "QEq 不收敛提醒文本"}]}},
            {"type": "assistant/message", "seq": 3, "time": 4, "data": {
                "message": {"role": "assistant", "content": [
                    {"type": "reasoning", "text": "QEq 推理块不该命中"},
                    {"type": "text", "text": "把 QEq 收敛容差放宽后稳定"}]}}},
        ])
        r = md_mcp.sim_search_sessions({"q": "QEq", "limit": 10})
        self.assertEqual(r["match_count"], 2)
        roles = {m["role"] for m in r["matches"]}
        self.assertEqual(roles, {"user", "assistant"})
        self.assertEqual(r["matches"][0]["cwd"], "/tmp/实验室")
        self.assertIn("QEq", r["matches"][0]["snippet"])
        self.assertIn("_discipline", r)

    def test_multi_term_and_and_semantics(self):
        self._mk_session("--ws--", "m1", [
            {"type": "session", "id": "m1", "cwd": "/a"},
            {"type": "user/message", "seq": 1, "time": 2, "data": {
                "role": "user", "source": {"kind": "user"},
                "content": [{"type": "text", "text": "温度 爆炸 了"}]}},
            {"type": "user/message", "seq": 2, "time": 3, "data": {
                "role": "user", "source": {"kind": "user"},
                "content": [{"type": "text", "text": "只有温度正常"}]}},
        ])
        r = md_mcp.sim_search_sessions({"q": "温度 爆炸"})
        self.assertEqual(r["match_count"], 1, "多词=同时命中")
        self.assertEqual(r["sessions_scanned"], 1)

    def test_missing_sessions_dir_empty_no_raise(self):
        r = md_mcp.sim_search_sessions({"q": "任何词"})
        self.assertEqual(r["match_count"], 0)
        self.assertEqual(r["sessions_total"], 0)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "dsh-home", "sessions")),
                         "只读工具不得创建 sessions 目录")

    def test_missing_q_returns_error(self):
        r = md_mcp.sim_search_sessions({})
        self.assertIn("error", r)

    def test_readonly_zero_touch(self):
        self._mk_session("--ws--", "ro1", [
            {"type": "session", "id": "ro1", "cwd": "/a"},
            {"type": "user/message", "seq": 1, "time": 2, "data": {
                "role": "user", "source": {"kind": "user"},
                "content": [{"type": "text", "text": "关键词在此"}]}},
        ])
        target = os.path.join(self.tmp, "dsh-home", "sessions", "--ws--",
                              "session-ro1", "session.jsonl.zstd")
        before = os.stat(target).st_mtime
        md_mcp.sim_search_sessions({"q": "关键词"})
        self.assertEqual(os.stat(target).st_mtime, before, "会话档被写动")


if __name__ == "__main__":
    unittest.main(verbosity=2)
