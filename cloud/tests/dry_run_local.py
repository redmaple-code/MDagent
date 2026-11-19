#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地真实 dsh 干跑:runner.run_chat 全链路(真 LLM 调用,约 1-2 分钟)。"""
import asyncio
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

os.environ["MDAGENT_HOME"] = "/tmp/mda_dry"
os.environ["MDAGENT_SKIP_UNIX"] = "1"
os.environ["MDAGENT_NODE"] = os.path.expanduser("~/node2219/bin/node")

import accounts
import paths
import provision
import runner

KEY = subprocess.check_output(
    ["bash", "-c",
     "echo <REDACTED-PW> | su - root -c 'grep ^KIMI_API_KEY "
     "/opt/mdagent/data/dsh-home"
     "/.credentials.yaml' 2>/dev/null"],
    text=True).strip().split(":", 1)[1].strip()

if not accounts.get("dryuser"):
    ok, msg = accounts.apply("dryuser", "", "干跑", "kimi-coding", KEY)
    assert ok, msg
    token, err = accounts.approve("dryuser")
    assert not err, err
provision.provision("dryuser")

job = {"user": "dryuser", "project": "干跑项目",
       "message": "用一句话回答:ReaxFF 力场适合描述什么类型的体系?"}


async def main():
    reply = await runner.run_chat(job)
    print("=== REPLY ===")
    print(reply[:2000])
    print("=== reply bytes:", len(reply))


asyncio.run(main())
