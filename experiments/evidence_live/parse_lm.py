#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""LAMMPS 输出解析 —— 事实采集(facts)的唯一来源。
从真实 log 解析:thermo 表(变量名+末行)、run 完成/中止证据、print 标记行。
数字只从产物解析,不从模型/记忆来(设计 C1 的决策域延伸)。"""
import json
import re

ERROR_MARKS = re.compile(r"Lost atoms|^ERROR|ERROR \(")


def parse_lammps_log(path):
    """返回 {thermo_headers, thermo_last, thermo_rows, finished, lost_atoms,
    prints:{...}, errors:[...]}。容错:坏文件返回空结构,绝不抛。"""
    out = {"thermo_headers": [], "thermo_rows": [], "thermo_last": {},
           "finished": False, "lost_atoms": False, "prints": {}, "errors": []}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception:
        return out
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.strip().startswith("Step") and "TotEng" in ln:
            headers = ln.split()
            rows = []
            j = i + 1
            while j < len(lines):
                vals = lines[j].split()
                if len(vals) == len(headers):
                    try:
                        rows.append([float(v) for v in vals])
                        j += 1
                        continue
                    except ValueError:
                        pass
                break
            if rows:
                out["thermo_headers"] = headers
                out["thermo_rows"] = rows
                last = rows[-1]
                out["thermo_last"] = dict(zip(headers, [str(x) for x in last]))
            i = j
            continue
        if ERROR_MARKS.search(ln):  # 只认 LAMMPS 真实报错,脚本注释回显不算
            out["errors"].append(ln.strip())
            if "Lost atoms" in ln:
                out["lost_atoms"] = True
        if ln.startswith("Loop time of "):
            out["finished"] = True
        m = re.match(r'^print\s+"?"?(.+?)"?"?\s*$', ln)  # 脚本 print 命令回显
        if m:
            out["prints"].setdefault("_raw", []).append(m.group(1))
        m2 = re.match(r"^(EVLIVE_[A-Z0-9_]+)\s+(.+)$", ln.strip())
        if m2:
            out["prints"][m2.group(1)] = m2.group(2).strip()
        i += 1
    return out


def fact_value(raw):
    """EVLIVE_ 标记值转 float(bool 语义除外)。"""
    s = str(raw).split()[0]
    try:
        return float(s)
    except ValueError:
        return raw
