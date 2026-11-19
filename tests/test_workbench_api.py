# -*- coding: utf-8 -*-
"""web_monitor 工作台/开关端点回归门:
workbench 与 pages 静态服务、auto-approve 开关读写的 Origin 防线(drive-by 不得翻转开关)。"""
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="wb-api-t")
os.environ["MDSIM_DATA_ROOT"] = TMP  # 必须在 import web_monitor 前:AUTO_APPROVE_FLAG 是模块级常量
os.makedirs(os.path.join(TMP, "dsh-home"), exist_ok=True)
sys.path.insert(0, os.path.join(HERE, "..", "dashboard"))

import web_monitor  # noqa: E402

# 多个 _ServerMixin 子类共享 TMP:类级 tearDown 删目录会让后跑类踩空(按字母序
# TestGraphNotes 先于 TestWorkbenchApi),统一退出时清。
import atexit  # noqa: E402
atexit.register(lambda: shutil.rmtree(TMP, ignore_errors=True))


class _ServerMixin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from http.server import HTTPServer
        cls.httpd = HTTPServer(("127.0.0.1", 0), web_monitor.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def _req(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(method, path, body=body, headers=headers or {})
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r.status, data


class TestWorkbenchApi(_ServerMixin):
    def test_workbench_served(self):
        status, data = self._req("GET", "/workbench")
        self.assertEqual(status, 200)
        self.assertIn("工作台".encode("utf-8"), data)

    def test_pages_traversal_blocked(self):
        # 编码穿越与伪装扩展名都不得拿到 200(400/404 均属拦截)
        for p in ("/pages/..%2f..%2fregistry.py",
                  "/pages/..\\..\\registry.py.html",
                  "/pages/registry.py.html"):
            status, _ = self._req("GET", p)
            self.assertIn(status, (400, 404), "%s 未被拦截: %d" % (p, status))
        status, _ = self._req("GET", "/pages/definitely-not-here.html")
        self.assertEqual(status, 404)

    def test_auto_approve_roundtrip_same_origin(self):
        host = "127.0.0.1:%d" % self.port
        status, data = self._req("GET", "/api/auto-approve",
                                 headers={"Origin": "http://" + host, "Host": host})
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(data)["enabled"])
        status, data = self._req(
            "POST", "/api/auto-approve", body=json.dumps({"enabled": True}),
            headers={"Origin": "http://" + host, "Host": host,
                     "Content-Type": "application/json"})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(data)["enabled"])
        status, data = self._req("GET", "/api/auto-approve")
        self.assertTrue(json.loads(data)["enabled"])

    def test_auto_approve_file_origin_null_allowed(self):
        # file:// 打开的 workbench 的 Origin 是 null,必须放行
        status, data = self._req(
            "POST", "/api/auto-approve", body=json.dumps({"enabled": False}),
            headers={"Origin": "null", "Content-Type": "application/json"})
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(data)["enabled"])

    def test_auto_approve_evil_origin_blocked(self):
        # drive-by:任意网页不得跨域翻转开关(ACA O:* 只为 file:// 而开)
        flag = os.path.join(TMP, "dsh-home", "auto-approve.json")
        before = open(flag).read() if os.path.exists(flag) else None
        status, _ = self._req(
            "POST", "/api/auto-approve", body=json.dumps({"enabled": True}),
            headers={"Origin": "https://evil.example.com",
                     "Content-Type": "application/json"})
        self.assertEqual(status, 403)
        after = open(flag).read() if os.path.exists(flag) else None
        self.assertEqual(before, after, "被拒请求不得改动开关文件")

    def test_auto_approve_bad_body_400(self):
        status, _ = self._req("POST", "/api/auto-approve", body="not-json",
                              headers={"Content-Type": "application/json"})
        self.assertEqual(status, 400)

    def test_index_html_escapes_case_id(self):
        # 存储型 XSS 防线(五轮对抗):esc 助手存在,case_id 不再裸拼进 onclick/innerHTML
        status, data = self._req("GET", "/")
        if status != 200:
            status, data = self._req("GET", "/index.html")
        self.assertEqual(status, 200)
        text = data.decode("utf-8")
        self.assertIn("function esc(", text)
        self.assertNotIn("onclick=\"location.href='/?task=${t.case_id}'\"", text)
        self.assertIn("encodeURIComponent(this.dataset.case)", text)

    def test_simple_html_escapes_case_id(self):
        status, data = self._req("GET", "/simple.html")
        self.assertEqual(status, 200)
        text = data.decode("utf-8")
        self.assertIn("function esc(", text)
        self.assertNotIn("'<a onclick=\"loadTask", text)

    def test_index_html_escapes_log_streams(self):
        # B6 收尾(七轮对抗):日志/registry 来源字段全部 esc,无裸拼接 sink 残留
        status, data = self._req("GET", "/")
        if status != 200:
            status, data = self._req("GET", "/index.html")
        self.assertEqual(status, 200)
        text = data.decode("utf-8")
        for raw in ("${t.last_modified}", "${l.message || ''}", "${l.msg || ''}",
                    "${r.root_cause || '-'}", "${l.type || '-'}", "${detailsStr}",
                    "${fromName}", "${l.trigger || ''}", "${l.decision || ''}",
                    "[${level}]", "${time}",
                    # 八轮补扫(主会话穷举):agent 卡/物种/步骤/队列/执行链/模态框/PID
                    "${names[name] || name}", "${info.error.substring(0,40)}",
                    "${s.name}", "${q.trigger_condition || '手动'}",
                    "${a.label || a.name}", "${detailStr}", "${errStr}",
                    "' + d.monitor_daemon_pid + '",
                    # 八轮补扫(注册表面):agent 详情模态框整块裸拼接
                    "' + (data.phase || '--') + '",
                    "' + (data.monitor_subphase || '--') + '",
                    "' + (m.status || '--') + '", "' + (m.step || '--') + '",
                    "' + (m.temp || '--') + '", "' + (m.energy || '--') + '",
                    "' + (m.risk_score || 0) + '", "' + (phase || 'IDLE') + '",
                    "' + (agent.status || agent.success || '--') + '",
                    "' + agent.step + '", "' + agent.error + '"):
            self.assertNotIn(raw, text, "裸 sink 残留: %s" % raw)


class TestTargetTempFallback(unittest.TestCase):
    """target_temp 越界回退与 monitor_agent 同口径 sched[-1][3](四轮对抗 N1:
    项目短程序越界时两侧曾一个报 CRITICAL 一个正常)。"""

    def test_out_of_range_uses_last_segment_end(self):
        self.assertEqual(web_monitor.target_temp(99999999),
                         web_monitor.SCHEDULE[-1][3])

    def test_injected_short_schedule_out_of_range(self):
        # 短程序末段终温 300K:越界步必须按 300K 而非内置 1200K
        short = [(0.0, 100.0, 300.0, 300.0)]
        self.assertEqual(web_monitor.target_temp(99999999, short), 300.0)


class TestGraphNotes(_ServerMixin):
    """/api/graph 研判笔记节点(CORAL notes 模式,2026-09-02):
    memory/notes/*.md 首行 <!-- JSON --> 机读头 → note 节点 + 连边。"""

    def setUp(self):
        # 策略库沙盒化(2026-09-13):真 kit db 是 mdorch 属主,权限随服务写入浮动
        # (600 时 user 读不到)——测试依赖真实文件既是污染也是抖动源,锚死沙盒。
        self._db = os.path.join(TMP, "strategy_db_sandbox.json")
        with open(self._db, "w", encoding="utf-8") as f:
            json.dump({"strategies": {
                "B": {"name": "降低升温速率", "success_rate": 0.5, "uses": 2}},
                "history": [], "strategy_adjustments": {}}, f)
        self._old_db = web_monitor.DB_FILE
        web_monitor.DB_FILE = self._db

    def tearDown(self):
        web_monitor.DB_FILE = self._old_db

    def _write_note(self, name, header):
        ndir = os.path.join(TMP, "memory", "notes")
        os.makedirs(ndir, exist_ok=True)
        with open(os.path.join(ndir, name), "w", encoding="utf-8") as f:
            f.write("<!-- %s -->\n# 研判笔记\n" % json.dumps(header, ensure_ascii=False))

    def _graph(self):
        status, data = self._req("GET", "/api/graph")
        self.assertEqual(status, 200)
        return json.loads(data.decode("utf-8"))

    def test_note_node_appears(self):
        self._write_note("20990101_000000_caseA_thermal_runaway.md", {
            "type": "research-note", "case_id": "caseA",
            "root_cause": "thermal_runaway", "strategy": "B",
            "outcome": "success", "ts": "2099-01-01T00:00:00"})
        g = self._graph()
        notes = [n for n in g["nodes"] if n["type"] == "note"]
        self.assertTrue(any(n["id"] == "note:20990101_000000_caseA_thermal_runaway.md"
                            for n in notes))
        self.assertGreaterEqual(g["stats"].get("notes", 0), 1)

    def test_note_edges_to_strategy(self):
        # 策略 B 节点来自真实 kit db;笔记 strategy=B → 「采用」边
        self._write_note("20990102_000000_caseB_QEq_instability.md", {
            "type": "research-note", "case_id": "caseB",
            "root_cause": "QEq_instability", "strategy": "B",
            "outcome": "fail", "ts": "2099-01-02T00:00:00"})
        g = self._graph()
        nid = "note:20990102_000000_caseB_QEq_instability.md"
        self.assertTrue(any(e["from"] == nid and e["to"] == "strat:B"
                            and e["label"] == "采用" for e in g["edges"]))

    def test_malformed_note_skipped(self):
        ndir = os.path.join(TMP, "memory", "notes")
        os.makedirs(ndir, exist_ok=True)
        with open(os.path.join(ndir, "20990103_bad.md"), "w", encoding="utf-8") as f:
            f.write("# 没有机读头的笔记\n")
        with open(os.path.join(ndir, "20990104_wrong_type.md"), "w", encoding="utf-8") as f:
            f.write('<!-- {"type": "other"} -->\n')
        g = self._graph()
        ids = {n["id"] for n in g["nodes"]}
        self.assertNotIn("note:20990103_bad.md", ids)
        self.assertNotIn("note:20990104_wrong_type.md", ids)

    def test_no_db_falls_back_to_kit_defaults(self):
        # 打包冒烟抓到(21 轮):全新部署无 .v3.3_strategy_db.json 时图谱无策略节点、
        # 笔记「采用」边断。kit load_db 语义=库缺席即默认六策略在役,图谱须一致。
        orig = web_monitor.DB_FILE
        web_monitor.DB_FILE = os.path.join(TMP, "nonexistent_db.json")
        try:
            self._write_note("20990105_000000_caseC_QEq_instability.md", {
                "type": "research-note", "case_id": "caseC",
                "root_cause": "QEq_instability", "strategy": "B",
                "outcome": "fail", "ts": "2099-01-05T00:00:00"})
            g = self._graph()
        finally:
            web_monitor.DB_FILE = orig
        sids = {n["id"] for n in g["nodes"] if n["type"] == "strategy"}
        self.assertIn("strat:B", sids)  # 默认六策略节点仍在
        nid = "note:20990105_000000_caseC_QEq_instability.md"
        self.assertTrue(any(e["from"] == nid and e["to"] == "strat:B"
                            and e["label"] == "采用" for e in g["edges"]))


class TestEvolutionMetrics(_ServerMixin):
    """/api/evolution_metrics 进化记分卡(2026-09-02):全部由既有数据算出,
    缺数据降级为「—」不报错。"""

    def test_endpoint_structure(self):
        status, data = self._req("GET", "/api/evolution_metrics")
        self.assertEqual(status, 200)
        d = json.loads(data.decode("utf-8"))
        for k in ("index", "subscores", "cards", "trends", "ts"):
            self.assertIn(k, d)
        self.assertIsInstance(d["cards"], list)
        self.assertTrue(any(c["key"] == "win_rate" for c in d["cards"]))

    def test_fabricated_history_math(self):
        # 2 胜 2 负 → 胜率 0.5;首尝试两 case 一胜一负 → 一次修复率 0.5
        tmpdb = os.path.join(TMP, "fake_strategy_db.json")
        hist = [
            {"strategy": "A", "root_cause": "r1", "case_id": "c1",
             "ts": "2026-09-01T10:00:00", "settled_ts": "2026-09-01T12:00:00",
             "outcome": "success"},
            {"strategy": "A", "root_cause": "r1", "case_id": "c2",
             "ts": "2026-09-01T10:00:00", "settled_ts": "2026-09-01T11:00:00",
             "outcome": "fail"},
            {"strategy": "B", "root_cause": "r1", "case_id": "c2",
             "ts": "2026-09-01T13:00:00", "settled_ts": "2026-09-01T14:00:00",
             "outcome": "fail"},
            {"strategy": "B", "root_cause": "r2", "case_id": "c3",
             "ts": "2026-09-01T15:00:00", "settled_ts": "2026-09-01T16:00:00",
             "outcome": "success"},
        ]
        with open(tmpdb, "w", encoding="utf-8") as f:
            json.dump({"strategies": {
                "A": {"name": "a", "success_rate": 0.5, "uses": 2},
                "B": {"name": "b", "success_rate": 0.5, "uses": 2}},
                "history": hist}, f)
        orig = web_monitor.DB_FILE
        web_monitor.DB_FILE = tmpdb
        try:
            m = web_monitor.build_evolution_metrics()
        finally:
            web_monitor.DB_FILE = orig
        val = {c["key"]: c["value"] for c in m["cards"]}
        self.assertEqual(val["win_rate"], "50%")
        self.assertEqual(val["first_fix"], "67%")  # c1/c3 首试即胜,c2 首试负
        self.assertEqual(val["recover_hours"], "1.0 h")  # [1,1,1,2] 排第 2 位
        self.assertIsNotNone(m["index"])
        self.assertEqual(len(m["trends"]["win_rate"]), 6)

    def test_missing_db_degrades(self):
        orig = web_monitor.DB_FILE
        web_monitor.DB_FILE = os.path.join(TMP, "nonexistent_db.json")
        try:
            m = web_monitor.build_evolution_metrics()
        finally:
            web_monitor.DB_FILE = orig
        val = {c["key"]: c["value"] for c in m["cards"]}
        self.assertEqual(val["win_rate"], "—")
        self.assertEqual(val["recover_hours"], "—")


class TestTrajectoryApi(_ServerMixin):
    """/api/trajectory 每任务/每会话轨迹(2026-09-09 经验沉淀看板面):
    pipeline 按 case_id 过滤、friction 按 session 过滤+总览聚合、缺日志降级空。"""

    def _write(self, name, rows):
        edir = os.path.join(TMP, "events")
        os.makedirs(edir, exist_ok=True)
        with open(os.path.join(edir, name), "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def test_task_trajectory_filters_case(self):
        self._write("pipeline.jsonl", [
            {"ts": "2026-09-09T01:00:00", "case_id": "caseX", "status": "OK"},
            {"ts": "2026-09-09T01:01:00", "case_id": "caseY", "status": "CRITICAL",
             "risk_score": 80},
        ])
        status, data = self._req("GET", "/api/trajectory?task=caseX")
        self.assertEqual(status, 200)
        d = json.loads(data.decode("utf-8"))
        pipe = [it for it in d["items"] if it["src"] == "pipeline"]
        self.assertEqual(len(pipe), 1)
        self.assertIn("OK", pipe[0]["text"])
        self.assertNotIn("caseY", json.dumps(d["items"]))

    def test_session_trajectory_and_overview(self):
        now_ms = int(time.time() * 1000)
        self._write("friction.jsonl", [
            {"t": now_ms, "kind": "turn-friction", "session": "sA", "turn": 1,
             "reason": "aborted", "interrupts": 1, "tool_errors": 2, "denials": 1,
             "retries": 0, "tools": 5, "failed_tools": ["TIMEOUT"]},
            {"t": now_ms, "kind": "turn-friction", "session": "sB", "turn": 1,
             "reason": "completed", "interrupts": 0, "tool_errors": 0, "denials": 0,
             "retries": 1, "tools": 2, "failed_tools": []},
        ])
        status, data = self._req("GET", "/api/trajectory?session=sA")
        self.assertEqual(status, 200)
        d = json.loads(data.decode("utf-8"))
        fr = [it for it in d["items"] if it["src"] == "friction"]
        self.assertEqual(len(fr), 1)
        self.assertIn("interrupt=1", fr[0]["text"])
        self.assertIn("err=2", fr[0]["text"])
        agg = {s["session"]: s for s in d["sessions"]}
        self.assertEqual(agg["sA"]["interrupts"], 1)
        self.assertEqual(agg["sA"]["tool_errors"], 2)
        self.assertEqual(agg["sA"]["denials"], 1)
        self.assertEqual(agg["sB"]["retries"], 1)
        self.assertEqual(agg["sB"]["interrupts"], 0)

    def test_missing_logs_degrade_empty(self):
        # 隔离空数据根:所有源缺席 → 空 items/sessions,永不抛
        empty = tempfile.mkdtemp(prefix="traj-empty-")
        self.addCleanup(shutil.rmtree, empty, True)
        from unittest import mock
        with mock.patch.dict(os.environ, {"MDSIM_DATA_ROOT": empty}):
            d = web_monitor.build_trajectory(task="no-such", session="no-such")
        self.assertEqual(d["items"], [])
        self.assertEqual(d["sessions"], [])


class TestKnowledgeCardsApi(_ServerMixin):
    """/api/knowledge_cards 知识名片 wiki(2026-09-13):
    卡片/人工核状态/待拍板闸/LEDGER 组合只读;缺文件降级空永不抛。"""

    def setUp(self):
        # 路径常量沙盒(KB_PATH 同型教训):discover 按字母序先导入
        # test_gromacs_monitor,web_monitor 的 KNOWLEDGE_*_FILE 模块级常量
        # 在那边就已冻结指向 gromacs 沙盒,本类 env 重指救不回来——必须显式 patch
        self._old = (web_monitor.KNOWLEDGE_CARDS_FILE,
                     web_monitor.KNOWLEDGE_REVIEWS_FILE)
        web_monitor.KNOWLEDGE_CARDS_FILE = os.path.join(
            TMP, "memory", "knowledge_cards.jsonl")
        web_monitor.KNOWLEDGE_REVIEWS_FILE = os.path.join(
            TMP, "memory", "knowledge_reviews.json")
        self.addCleanup(self._restore)

    def _restore(self):
        (web_monitor.KNOWLEDGE_CARDS_FILE,
         web_monitor.KNOWLEDGE_REVIEWS_FILE) = self._old

    def _write_cards(self):
        mdir = os.path.join(TMP, "memory")
        os.makedirs(mdir, exist_ok=True)
        rows = [
            {"id": "kc-build-lesson", "source": "atoms", "kind": "build-lesson",
             "title": "build-lesson", "category": "教训", "count": 2,
             "facts": ["f1"], "refs": ["case:x"], "reviewed_by": "tester"},
            {"id": "kc-sop-a", "source": "sop", "kind": "sop", "title": "SOP: a",
             "category": "SOP", "count": 1, "facts": ["s1"], "refs": [],
             "distill": {"summary": "sm", "apply": [], "dont_apply": [],
                         "conditions": "", "model": "glm-4.6", "ts": 1.0}},
        ]
        with open(os.path.join(mdir, "knowledge_cards.jsonl"), "w",
                  encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def test_cards_shape_and_stats(self):
        self._write_cards()
        status, data = self._req("GET", "/api/knowledge_cards")
        self.assertEqual(status, 200)
        d = json.loads(data.decode("utf-8"))
        self.assertEqual(len(d["cards"]), 2)
        self.assertEqual(d["stats"]["total"], 2)
        self.assertEqual(d["stats"]["reviewed"], 1)
        self.assertEqual(d["stats"]["distilled"], 1)
        self.assertEqual(d["stats"]["by_category"].get("教训"), 1)
        self.assertTrue(any(c["id"] == "kc-build-lesson" and c["reviewed_by"] == "tester"
                            for c in d["cards"]))

    def test_pending_gates_surfaced(self):
        # 待拍板闸必须上桌(用户确认面);闸文件指沙盒,不吃真 kit 状态
        gates = {"version": 1, "gates": [
            {"id": "ug-t-1", "kind": "evolution_proposal", "ref": "p1",
             "question": "Q?", "created_ts": 1.0, "status": "pending", "source": "dsh"},
            {"id": "ug-t-2", "kind": "manual", "ref": "p2", "question": "done",
             "created_ts": 2.0, "status": "resolved", "source": "kit"},
        ]}
        gpath = os.path.join(TMP, "gates_sandbox.json")
        with open(gpath, "w", encoding="utf-8") as f:
            json.dump(gates, f)
        old = web_monitor.USER_GATES_FILE
        web_monitor.USER_GATES_FILE = gpath
        try:
            status, data = self._req("GET", "/api/knowledge_cards")
            self.assertEqual(status, 200)
            d = json.loads(data.decode("utf-8"))
            self.assertEqual(d["stats"]["pending_gates"], 1, "只有 pending 上桌")
            self.assertEqual(d["pending_gates"][0]["id"], "ug-t-1")
        finally:
            web_monitor.USER_GATES_FILE = old

    def test_missing_files_degrade_empty(self):
        # 卡片文件清空后:零卡片零闸零抛
        cpath = os.path.join(TMP, "memory", "knowledge_cards.jsonl")
        if os.path.exists(cpath):
            os.remove(cpath)
        old = web_monitor.USER_GATES_FILE
        web_monitor.USER_GATES_FILE = os.path.join(TMP, "no_such_gates.json")
        try:
            status, data = self._req("GET", "/api/knowledge_cards")
            self.assertEqual(status, 200)
            d = json.loads(data.decode("utf-8"))
            self.assertEqual(d["cards"], [])
            self.assertEqual(d["stats"]["total"], 0)
            self.assertEqual(d["pending_gates"], [])
        finally:
            web_monitor.USER_GATES_FILE = old

    def test_taxonomy_surfaced_when_present(self):
        self._write_cards()
        tax = {"generated_ts": 1.0, "llm": True, "cards_total": 2, "roles": [],
               "layers": [{"key": "dialog", "name": "对话层", "summary": "s",
                           "card_ids": ["kc-build-lesson"]}],
               "physics": [{"key": "other", "name": "其他", "summary": "",
                            "card_ids": ["kc-sop-a"]}],
               "note": "n"}
        with open(web_monitor.KNOWLEDGE_CARDS_FILE.replace(
                "knowledge_cards.jsonl", "knowledge_taxonomy.json"), "w",
                encoding="utf-8") as f:
            json.dump(tax, f)
        status, data = self._req("GET", "/api/knowledge_cards")
        d = json.loads(data.decode("utf-8"))
        self.assertEqual(d["taxonomy"]["layers"][0]["card_ids"],
                         ["kc-build-lesson"])
        self.assertTrue(d["taxonomy"]["llm"])

    def test_taxonomy_absent_degrades_null(self):
        self._write_cards()
        tpath = web_monitor.KNOWLEDGE_CARDS_FILE.replace(
            "knowledge_cards.jsonl", "knowledge_taxonomy.json")
        if os.path.exists(tpath):
            os.remove(tpath)
        status, data = self._req("GET", "/api/knowledge_cards")
        d = json.loads(data.decode("utf-8"))
        self.assertIsNone(d["taxonomy"])

    def test_knowledge_page_served(self):
        status, data = self._req("GET", "/pages/knowledge.html")
        self.assertEqual(status, 200)
        self.assertIn("知识名片".encode("utf-8"), data)
        self.assertIn("按智能体体系".encode("utf-8"), data)


if __name__ == "__main__":
    unittest.main()
