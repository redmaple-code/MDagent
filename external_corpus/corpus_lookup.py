#!/usr/bin/env python3
"""corpus_lookup — 外部报错语料检索(绿区只读, 2026-09-20)。

用法:
  python3 corpus_lookup.py "Lost atoms: original 65536 current 65534"
  python3 corpus_lookup.py "qeq/reax CG convergence failed" --engine lammps --top 3
  echo "<日志错误行>" | python3 corpus_lookup.py -

数据: AI4Science md-error-corpus (CC BY-NC-SA 4.0, 全部 unverified)。
输出 top-N 记录: 签名/中文名/类别/原帖 url + 按分排序的解法摘录。
只读, 永不写任何文件; 找不到就空结果退出 0, 不猜。
"""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCES = [
    ("lammps", os.path.join(HERE, "lammps_answered.jsonl")),
    ("gromacs", os.path.join(HERE, "gromacs_answered.jsonl")),
]
STOP = set("error the a an in of to for with on at is are was be or and it this that file line src using use how can not no do does".split())


def tokens(s):
    return [t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if len(t) > 2 and t not in STOP]


def load(engine):
    out = []
    for eng, path in SOURCES:
        if engine and eng != engine:
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        pass
        except OSError:
            pass
    return out


def score(rec, qtoks, qraw):
    hay_sig = (rec.get("signature") or "").replace(":", " ").replace("_", " ")
    hay = " ".join([
        hay_sig, rec.get("zh") or "", rec.get("category") or "",
        rec.get("fix_hint") or "",
        " ".join(rec.get("problem", {}).get("error_lines") or []),
    ]).lower()
    ht = set(tokens(hay))
    s = sum(1 for t in set(qtoks) if t in ht)
    # 错误行原文连续片段命中加权(报错行是最强指纹)
    for el in rec.get("problem", {}).get("error_lines") or []:
        frag = re.sub(r"\d+", "", el.lower())[:60]
        if len(frag) > 20 and frag in re.sub(r"\d+", "", qraw.lower()):
            s += 3
    best = max((x.get("score") or 0) for x in rec.get("solutions") or [{}])
    return s + best * 0.1


def main():
    args = sys.argv[1:]
    engine, top = "", 3
    if "--engine" in args:
        i = args.index("--engine"); engine = args[i + 1]; del args[i:i + 2]
    if "--top" in args:
        i = args.index("--top"); top = int(args[i + 1]); del args[i:i + 2]
    qraw = args[0] if args else "-"
    if qraw == "-":
        qraw = sys.stdin.read().strip()
    if not qraw:
        print("(空查询)"); return
    qtoks = tokens(qraw)
    recs = load(engine)
    ranked = sorted(((score(r, qtoks, qraw), r) for r in recs), key=lambda x: -x[0])
    shown = 0
    for sc, r in ranked:
        if sc < 1.5 or shown >= top:
            break
        shown += 1
        sols = sorted(r.get("solutions") or [], key=lambda x: -(x.get("score") or 0))
        best = sols[0] if sols else {}
        print("=" * 72)
        print("[%s] %s  (匹配分 %.1f)" % (r.get("engine"), r.get("signature"), sc))
        if r.get("zh"):
            print("  中文: %s" % r["zh"])
        print("  类别: %s | 解法候选 %d 条 | 原帖: %s" % (r.get("category"), len(sols), r.get("url")))
        for el in (r.get("problem", {}).get("error_lines") or [])[:2]:
            print("  报错: %s" % el[:150])
        if best:
            txt = re.sub(r"\s+", " ", best.get("text") or "")[:600]
            print("  最优解(score=%s, 维护者=%s, 采纳=%s):" % (best.get("score"), best.get("is_staff"), best.get("accepted")))
            print("    %s" % txt)
    if not shown:
        print("语料无匹配(%d 条已检索)。外部语料 unverified, 无匹配时按自有 SOP/策略库走。")


if __name__ == "__main__":
    main()
