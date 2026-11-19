#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""共享进化层图谱(移植自 dashboard build_graph,面向 /v1/evolution/graph)。
数据源 = paths.shared_evolution_dir()(全员共享、云端持续写入的进化库)。
收录:atoms(L1 摩擦/L2 经验 + distilled 融合核 + 融合边)、scenarios、SOP、
proposals+LEDGER 裁决、notes 研判、knowledge_cards、bstrat 行为策略。
不收录本机专属块(kit 策略结账 DB / case 作业节点 / 回放考场成绩单)。
缺文件/坏行一律降级为空,不打穿接口。"""
import json
import os
import re

import paths


def build():
    mdir = paths.shared_evolution_dir()
    nodes, edges = [], []

    def _atom_level(kind):
        if kind.startswith('friction-') or kind in ('flow-gate-violation', 'user-correction'):
            return 1
        if kind.startswith('distilled-'):
            return 3
        return 2

    atoms_by_id = {}
    atom_node_by_id = {}
    atom_ts = {}
    fused_pairs = {}
    try:
        with open(os.path.join(mdir, 'atoms.jsonl'), encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    a = json.loads(line)
                except ValueError:
                    continue
                aid = str(a.get('id', ''))
                if not aid:
                    continue
                nid = 'atom:' + aid
                atoms_by_id[aid] = nid
                kind = str(a.get('kind', 'atom'))
                ts = a.get('t')
                atom_ts[aid] = ts
                sup_by = str(a.get('superseded_by') or '')
                sup_at = a.get('superseded_at')
                atom_node_by_id[aid] = {
                    'id': nid, 'type': 'atom',
                    'label': kind,
                    'sublabel': str(a.get('fact', ''))[:60],
                    'ts': ts, 'level': _atom_level(kind),
                    'distilled': kind.startswith('distilled-'),
                    'dead_ts': sup_at,
                    'detail': {'fact': str(a.get('fact', '')),
                               'refs': a.get('refs', []),
                               'kind': kind,
                               'axes': a.get('axes') or {},
                               'superseded_by': sup_by,
                               'supersede_reason': str(a.get('supersede_reason') or ''),
                               'superseded_at': sup_at}}
                if sup_by:
                    key = (aid, sup_by)
                    if key not in fused_pairs or (sup_at or 0) < (fused_pairs[key] or 0):
                        fused_pairs[key] = sup_at
    except Exception:
        pass
    for aid in atoms_by_id:
        if aid in atom_node_by_id:
            nodes.append(atom_node_by_id[aid])

    fused_edges = 0
    for (old, new), sup_at in fused_pairs.items():
        if old in atoms_by_id and new in atoms_by_id:
            edges.append({'from': atoms_by_id[old], 'to': atoms_by_id[new],
                          'label': '融合', 'ts': sup_at})
            fused_edges += 1

    signatures = []
    try:
        with open(os.path.join(mdir, 'scenarios.json'), encoding='utf-8') as f:
            for sc in json.load(f):
                sig = str(sc.get('signature', ''))
                if not sig:
                    continue
                signatures.append(sig)
                nid = 'scn:' + sig
                nodes.append({'id': nid, 'type': 'scenario', 'label': sig,
                              'sublabel': '命中 %d 次' % sc.get('count', 0), 'ts': None,
                              'detail': {'examples': sc.get('examples', [])[:5]}})
                for aid in dict.fromkeys(str(x) for x in sc.get('atom_ids', [])):
                    if aid in atoms_by_id:
                        edges.append({'from': nid, 'to': atoms_by_id[aid], 'label': '归纳',
                                      'ts': atom_ts.get(aid)})
    except Exception:
        pass

    if os.path.exists(os.path.join(mdir, 'SOP.md')):
        nodes.append({'id': 'sop', 'type': 'sop', 'label': 'SOP.md',
                      'sublabel': '沉淀的处置手册', 'ts': None, 'detail': {}})
        for sig in signatures:
            edges.append({'from': 'scn:' + sig, 'to': 'sop', 'label': '沉淀'})

    ledger = {}
    try:
        with open(os.path.join(mdir, 'LEDGER.jsonl'), encoding='utf-8', errors='replace') as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get('slug'):
                    ledger[str(e['slug'])] = e
    except Exception:
        pass

    def _tokens(s):
        return set(t for t in re.split(r'[-_\s]+', s.lower()) if len(t) >= 4)

    try:
        pdir = os.path.join(mdir, 'proposals')
        for name in sorted(os.listdir(pdir)):
            meta_fp = os.path.join(pdir, name, 'meta.json')
            if not os.path.isdir(os.path.join(pdir, name)):
                continue
            meta = {}
            try:
                with open(meta_fp, encoding='utf-8') as f:
                    meta = json.load(f)
            except Exception:
                pass
            slug = name[11:] if re.match(r'\d{4}-\d{2}-\d{2}-', name) else name
            led = ledger.get(slug, {})
            title = ''
            try:
                with open(os.path.join(pdir, name, 'proposal.md'), encoding='utf-8',
                          errors='replace') as f:
                    title = f.readline().lstrip('# ').strip()
            except Exception:
                pass
            nid = 'prop:' + slug
            nodes.append({'id': nid, 'type': 'proposal', 'label': slug,
                          'sublabel': title or str(meta.get('class', '')),
                          'ts': led.get('ts') or name[:10],
                          'result': led.get('result', 'DRAFT'),
                          'detail': {'class': str(meta.get('class', '')),
                                     'tier': str(meta.get('tier', '')),
                                     'root': str(meta.get('root', '')),
                                     'reason': str(led.get('reason', ''))}})
            ptoks = _tokens(slug + ' ' + str(meta.get('class', '')))
            for sig in signatures:
                if ptoks & _tokens(sig):
                    edges.append({'from': 'scn:' + sig, 'to': nid, 'label': '催生提案',
                                  'ts': led.get('ts') or name[:10] or None})
    except Exception:
        pass

    # RESEARCH 研判笔记:首行 <!-- JSON --> 机读头
    try:
        ndir = os.path.join(mdir, 'notes')
        for name in sorted(os.listdir(ndir)):
            if not name.endswith('.md'):
                continue
            hdr = {}
            try:
                with open(os.path.join(ndir, name), encoding='utf-8',
                          errors='replace') as f:
                    first = f.readline().strip()
                m = re.match(r'<!--\s*(\{.*\})\s*-->', first)
                if m:
                    hdr = json.loads(m.group(1))
            except Exception:
                continue
            if hdr.get('type') != 'research-note':
                continue
            nid = 'note:' + name
            nodes.append({'id': nid, 'type': 'note',
                          'label': '研判 · %s' % str(hdr.get('root_cause', '')),
                          'sublabel': '%s @ %s' % (hdr.get('outcome', ''),
                                                   hdr.get('case_id', '')),
                          'ts': hdr.get('ts'),
                          'result': str(hdr.get('outcome', '')),
                          'detail': {'file': 'notes/' + name,
                                     'case_id': str(hdr.get('case_id', '')),
                                     'root_cause': str(hdr.get('root_cause', '')),
                                     'strategy': str(hdr.get('strategy', '')),
                                     'outcome': str(hdr.get('outcome', ''))}})
    except Exception:
        pass

    # L3 蒸馏层:知识名片 + 行为策略(atom/scenario →蒸馏→ card →生效→ bstrat)
    try:
        with open(os.path.join(mdir, 'knowledge_cards.jsonl'),
                  encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    c = json.loads(line)
                except ValueError:
                    continue
                cid = str(c.get('id', ''))
                if not cid:
                    continue
                nid = 'card:' + cid
                nodes.append({'id': nid, 'type': 'card',
                              'label': str(c.get('title', cid)),
                              'sublabel': '%s · 事实 %d 条' % (str(c.get('category', '')),
                                                              len(c.get('facts') or [])),
                              'ts': c.get('generated_ts') or c.get('last_ts'),
                              'detail': {'category': str(c.get('category', '')),
                                         'kind': str(c.get('kind', '')),
                                         'facts': [str(x) for x in (c.get('facts') or [])[:8]],
                                         'refs': [str(x) for x in (c.get('refs') or [])[:12]],
                                         'count': c.get('count', 0),
                                         'scenario_count': c.get('scenario_count', 0),
                                         'reviewed': bool(c.get('reviewed'))}})
                cts = c.get('generated_ts') or c.get('last_ts')
                for aid in dict.fromkeys(str(x) for x in (c.get('atom_ids') or [])[:6]):
                    if aid in atoms_by_id:
                        edges.append({'from': atoms_by_id[aid], 'to': nid, 'label': '蒸馏',
                                      'ts': cts})
                ksig = str(c.get('kind', ''))
                if ksig and ksig in signatures:
                    edges.append({'from': 'scn:' + ksig, 'to': nid, 'label': '升华',
                                  'ts': cts})
    except Exception:
        pass

    try:
        with open(os.path.join(mdir, 'behavior_strategies.json'), encoding='utf-8') as f:
            bstrats = json.load(f).get('strategies') or []
        for bs in bstrats:
            if not isinstance(bs, dict):
                continue
            sid = str(bs.get('sid', ''))
            if not sid:
                continue
            evals = bs.get('evals') or []
            last_eval = evals[-1].get('count') if evals and isinstance(evals[-1], dict) else None
            nodes.append({'id': 'bstrat:' + sid, 'type': 'bstrat',
                          'label': str(bs.get('title', sid))[:40],
                          'sublabel': '%s · 后验 %s · 基线 %s' % (
                              str(bs.get('status', '')),
                              last_eval if last_eval is not None else '-',
                              bs.get('baseline_7d', '-')),
                          'ts': bs.get('created_ts'),
                          'result': 'success' if bs.get('status') == 'active' else 'pending',
                          'detail': {'rule': str(bs.get('rule', '')),
                                     'source': str(bs.get('source', '')),
                                     'status': str(bs.get('status', '')),
                                     'baseline_7d': bs.get('baseline_7d'),
                                     'eval_counts': [e.get('count') for e in evals
                                                     if isinstance(e, dict)][-8:]}})
            for ref in bs.get('evidence_refs') or []:
                mslug = re.search(r'proposals/(?:\d{4}-\d{2}-\d{2}-)?([^/\s]+)', str(ref))
                if mslug and 'prop:' + mslug.group(1) in {x['id'] for x in nodes}:
                    edges.append({'from': 'prop:' + mslug.group(1), 'to': 'bstrat:' + sid,
                                  'label': '蒸馏生效', 'ts': bs.get('created_ts')})
    except Exception:
        pass

    return {'nodes': nodes, 'edges': edges,
            'stats': {'atoms': sum(1 for n in nodes if n['type'] == 'atom'),
                      'scenarios': sum(1 for n in nodes if n['type'] == 'scenario'),
                      'proposals': sum(1 for n in nodes if n['type'] == 'proposal'),
                      'notes': sum(1 for n in nodes if n['type'] == 'note'),
                      'cards': sum(1 for n in nodes if n['type'] == 'card'),
                      'bstrats': sum(1 for n in nodes if n['type'] == 'bstrat'),
                      'fused': fused_edges,
                      'l1': sum(1 for n in nodes if n.get('level') == 1),
                      'l2': sum(1 for n in nodes if n.get('level') == 2),
                      'l3': sum(1 for n in nodes if n.get('level') == 3)}}
