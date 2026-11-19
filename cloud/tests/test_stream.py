#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""流式 tail 的解析纯函数单测:_extract_text/_activity_of 对 session.jsonl 各行型的取舍。"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault("MDAGENT_SKIP_UNIX", "1")

import runner


class TestExtractText(unittest.TestCase):
    def test_packed_text_chunks(self):
        row = {"type": "text-chunks",
               "data": {"turn": 1, "step": 0, "texts": ["你好", ",世", "界"]}}
        self.assertEqual(runner._extract_text(row), "你好,世界")

    def test_unpacked_text_delta(self):
        row = {"type": "assistant/chunk",
               "data": {"turn": 1, "step": 0,
                        "chunk": {"type": "text-delta", "index": 0,
                                  "text": "局部"}}}
        self.assertEqual(runner._extract_text(row), "局部")

    def test_reasoning_chunks_not_in_text_but_in_thinking(self):
        row = {"type": "reasoning-chunks",
               "data": {"turn": 1, "step": 0, "texts": ["内心", "独白"]}}
        self.assertEqual(runner._extract_text(row), "")
        self.assertEqual(runner._extract_thinking(row), "内心独白")

    def test_unpacked_reasoning_delta(self):
        row = {"type": "assistant/chunk",
               "data": {"chunk": {"type": "reasoning-delta", "text": "想"}}}
        self.assertEqual(runner._extract_text(row), "")
        self.assertEqual(runner._extract_thinking(row), "想")

    def test_tool_call_chunks_not_text(self):
        row = {"type": "tool-call-chunks",
               "data": {"turn": 1, "step": 0, "args": ["{\"a\":", "1}"]}}
        self.assertEqual(runner._extract_text(row), "")

    def test_assistant_message_not_used(self):
        # 拼接全文行,若取它会与 delta 重复
        row = {"type": "assistant/message",
               "data": {"message": {"content": "完整回复"}}}
        self.assertEqual(runner._extract_text(row), "")

    def test_malformed_rows(self):
        self.assertEqual(runner._extract_text({}), "")
        self.assertEqual(runner._extract_text({"type": "text-chunks"}), "")
        self.assertEqual(runner._extract_text(
            {"type": "text-chunks", "data": {"texts": None}}), "")
        self.assertEqual(runner._extract_text(
            {"type": "assistant/chunk", "data": None}), "")

    def test_text_not_in_thinking(self):
        row = {"type": "text-chunks",
               "data": {"turn": 1, "step": 0, "texts": ["正文"]}}
        self.assertEqual(runner._extract_thinking(row), "")
        row = {"type": "assistant/chunk",
               "data": {"chunk": {"type": "text-delta", "index": 0,
                                  "text": "正文"}}}
        self.assertEqual(runner._extract_thinking(row), "")

    def test_malformed_thinking(self):
        self.assertEqual(runner._extract_thinking({}), "")
        self.assertEqual(runner._extract_thinking(
            {"type": "reasoning-chunks"}), "")
        self.assertEqual(runner._extract_thinking(
            {"type": "reasoning-chunks", "data": {"texts": None}}), "")
        self.assertEqual(runner._extract_thinking(
            {"type": "assistant/chunk", "data": None}), "")


class TestActivityOf(unittest.TestCase):
    def test_tool_call(self):
        row = {"type": "tool/call",
               "data": {"turn": 1, "step": 0, "callId": "c1",
                        "name": "localfs_read", "arguments": "{}"}}
        self.assertEqual(runner._activity_of(row), "🔧 localfs_read")

    def test_tool_call_missing_name(self):
        self.assertEqual(runner._activity_of(
            {"type": "tool/call", "data": {}}), "🔧 tool")

    def test_non_tool_rows(self):
        self.assertEqual(runner._activity_of(
            {"type": "text-chunks", "data": {"texts": ["x"]}}), "")
        self.assertEqual(runner._activity_of({"type": "tool/result"}), "")
        self.assertEqual(runner._activity_of({}), "")


if __name__ == "__main__":
    unittest.main()
