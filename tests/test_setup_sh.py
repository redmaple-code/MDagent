# -*- coding: utf-8 -*-
"""setup.sh profiles 段回归门(六轮对抗):直接抽取 setup.sh 内嵌 python 块执行,
验证 nodeLinker 多命中删余/裸行改写/幂等,以及 bundles 并集校准永不引入 @vendor/dsh-browser(F2)。"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SETUP = os.path.join(os.path.dirname(HERE), "scripts", "setup.sh")


def _profiles_block():
    text = open(SETUP, encoding="utf-8").read()
    blocks = re.findall(r"<<'PYEOF'\n(.*?)\nPYEOF", text, re.S)
    for b in blocks:
        if "nodeLinker" in b:
            return b
    raise AssertionError("setup.sh 中找不到 profiles/nodeLinker python 块")


class TestSetupProfilesBlock(unittest.TestCase):
    def _run(self, home):
        return subprocess.run([sys.executable, "-", HERE, home],
                              input=_profiles_block(), capture_output=True, text=True)

    def _fixture(self, ws_lines):
        tmp = tempfile.mkdtemp(prefix="setup-sh-t")
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        pdir = os.path.join(tmp, "profiles", "web")
        os.makedirs(pdir)
        with open(os.path.join(pdir, "pnpm-workspace.yaml"), "w") as f:
            f.write("\n".join(ws_lines) + "\n")
        return tmp, pdir

    def _ws(self, pdir):
        return open(os.path.join(pdir, "pnpm-workspace.yaml")).read().splitlines()

    def test_multi_hit_rewrites_first_deletes_rest(self):
        # 裸行 + isolated 行并存:改写首个、删除其余——全改写会产生重复键,
        # pnpm 报 duplicated mapping key 且复跑不自愈(六轮对抗)
        tmp, pdir = self._fixture(["packages:", "  - .", "nodeLinker", "nodeLinker: isolated"])
        r = self._run(tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = self._ws(pdir)
        hits = [l for l in lines if l.split(":")[0].strip() == "nodeLinker"]
        self.assertEqual(hits, ["nodeLinker: hoisted"])

    def test_already_hoisted_multi_hit_deduped(self):
        # 多重命中即使全 hoisted 也是重复键,必须去重
        tmp, pdir = self._fixture(["packages:", "  - .", "nodeLinker: hoisted",
                                   "nodeLinker: hoisted"])
        r = self._run(tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        hits = [l for l in self._ws(pdir) if l.split(":")[0].strip() == "nodeLinker"]
        self.assertEqual(hits, ["nodeLinker: hoisted"])

    def test_single_hoisted_untouched_idempotent(self):
        tmp, pdir = self._fixture(["packages:", "  - .", "nodeLinker: hoisted",
                                   "autoInstallPeers: false"])
        before = self._ws(pdir)
        r = self._run(tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._ws(pdir), before, "已合规的 yaml 不得被改写")

    def test_bundles_union_never_adds_dsh_browser(self):
        # F2:@vendor/dsh-browser 只作 dependency link,永不进 bundles(五轮启动事故定案)
        tmp, pdir = self._fixture(["packages:", "  - .", "nodeLinker: hoisted"])
        pkg_path = os.path.join(pdir, "package.json")
        json.dump({"name": "x", "dependencies": {},
                   "dsh": {"profile": {"bundles": ["@deepseek-ai/dsh-base"]}}},
                  open(pkg_path, "w"))
        r = self._run(tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        pkg = json.load(open(pkg_path))
        bundles = pkg["dsh"]["profile"]["bundles"]
        self.assertIn("dsh-md-flow-gate", bundles, "缺失成员必须补齐")
        self.assertNotIn("@vendor/dsh-browser", bundles)
        self.assertIn("@vendor/dsh-browser", pkg["dependencies"],
                      "dependency link 必须保留")

    def test_tui_profile_generated_with_npm_dep_and_gates(self):
        # tui 终端模式(2026-08-30):deepseek-harness-tui 是 npm 版本号依赖(非 link:),
        # bundles 必须含它和治理插件，免批准插件已停用，不能重新装回。
        tmp = tempfile.mkdtemp(prefix="setup-sh-tui-t")
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        r = self._run(tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        pkg = json.load(open(os.path.join(tmp, "profiles", "tui", "package.json")))
        self.assertEqual(pkg["dependencies"]["deepseek-harness-tui"], "0.3.0")
        self.assertTrue(pkg["dependencies"]["dsh-md-flow-gate"].startswith("link:"))
        bundles = pkg["dsh"]["profile"]["bundles"]
        self.assertEqual(bundles[:2], ["@deepseek-ai/dsh-base", "deepseek-harness-tui"])
        for gate in ("dsh-md-ops-gate", "dsh-md-flow-gate", "dsh-md-friction"):
            self.assertIn(gate, bundles)
        self.assertNotIn("dsh-md-auto-approve", bundles)
        self.assertNotIn("dsh-md-auto-approve", pkg["dependencies"])
        ws = open(os.path.join(tmp, "profiles", "tui", "pnpm-workspace.yaml")).read()
        self.assertIn("nodeLinker: hoisted", ws)


if __name__ == "__main__":
    unittest.main()
