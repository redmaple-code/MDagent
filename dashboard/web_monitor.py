#!/usr/bin/env python3
"""Web Monitor v3.4 — 全链路状态面板（含 Agent 关系 + 观测日志流）"""
import os, sys, json, time, re, math
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# 看板注册表(2026-08-29 数据根外置):registry 优先发现作业,目录扫描仅兼容兜底
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mcp"))
import paths
import registry

# kit 根与任务扫描树改运行时解析(2026-08-29 阶段6):env > md_config.json > 派生,
# 消灭机器路径硬编码,setup.sh 不再改写本文件
def _md_config():
    try:
        with open(os.path.join(paths.CODE_ROOT, "mcp", "md_config.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

_MDCFG = _md_config()
BASE = os.environ.get("MDSIM_KIT_BASE") or _MDCFG.get("local_base", "")
STATE_FILE = os.path.join(BASE, ".v3.3_state.json")
RUN_CASE_BASE = (os.environ.get("RUN_CASE_BASE") or _MDCFG.get("run_case_base")
                 or os.path.join(os.path.dirname(BASE), "程序运行-case"))
DB_FILE = os.path.join(BASE, ".v3.3_strategy_db.json")


def _kit_default_db():
    """kit 策略库文件不存在时的默认库:调 kit recover_agent.load_db()(单一事实源,
    库缺席即返回默认六策略);任何失败返回 None,图谱降级为无策略节点。"""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "recover_agent",
            os.path.join(BASE, "agents", "runtime", "v3.3", "recover_agent.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.STRATEGY_DB = DB_FILE  # 显式对齐本面板数据源,不吃 AGENT_BASE 环境
        return mod.load_db()
    except Exception:
        return None

# 主题同步:跟随 dsh 侧 dsh-home/settings.yaml 的 ui-theme.preference(dark 时对页面做调色板替换)
DSH_SETTINGS = os.path.join(paths.dsh_home(make=False), 'settings.yaml')

# ── 多主题系统 ─────────────────────────────────────────────
# 浅色系源码 hex(顺序固定,各调色板按同序给出替换目标;顺序敏感:6 位 hex 先于 3 位短形式)
_LIGHT_SRC = [
    '#ffffff', '#f5f6f7', '#0f1115', '#353638', '#61666b', '#81868c', '#43454a',
    '#e1e5ee', '#ebedf0', '#4176e6', '#679efe', '#22c55e', '#f59e0b', '#ef4444',
    '#e6faed', '#fef2f2', '#fef5e7', '#edf3fe', '#e4edfd', '#dcdfe4', '#e5e5e5',
    '#d4d4d4', 'rgba(255,255,255,.92)', 'rgba(255,255,255,.85)', '#7c3aed',
    'rgba(0,0,0,.05)', '#fff}',
]

def _mk_palette(targets):
    return list(zip(_LIGHT_SRC, targets))

_PALETTES = {
    # 深色:对齐 DSH 暗色(中性灰 #151517 底 / #1b1b1c 面板 / rgba(255,255,255,.06) 边)
    'dark': _mk_palette([
        '#1b1b1c', '#151517', '#f9fafb', '#d6d6da', '#a3a3ab', '#7a7a82', '#3a3a3e',
        '#2c2c2e', '#262628', '#6b8afd', '#93adff', '#3fe0a0', '#ffce6b', '#ff6b6b',
        '#12251d', '#2a1616', '#2a2210', '#1a2030', '#232c42', '#3a3a3e', '#3a3a3e',
        '#4a4a50', 'rgba(21,21,23,.92)', 'rgba(27,27,28,.85)', '#a78bfa',
        'rgba(0,0,0,.40)', '#1b1b1c}',
    ]),
    # 深海:深灰蓝(对齐《AI+MD智能体平台_投资介绍》暗色变量)
    'ocean': _mk_palette([
        '#141b2d', '#05070f', '#eaf0fb', '#c6d2e8', '#9aa8c4', '#7d8cab', '#3a4a74',
        '#263152', '#1e2740', '#5ea8ff', '#8fc2ff', '#3fe0a0', '#ffce6b', '#ff6b6b',
        '#0d2b22', '#2e1618', '#2e2410', '#14203c', '#1b2a4e', '#3a4a74', '#3a4a74',
        '#4a5d8f', 'rgba(5,7,15,.92)', 'rgba(20,27,45,.85)', '#b490ff',
        'rgba(0,0,0,.40)', '#141b2d}',
    ]),
    # 暗紫
    'purple': _mk_palette([
        '#1a1426', '#100c1a', '#efe9fa', '#cfc3e4', '#a394c2', '#7f6f9e', '#4a3d6e',
        '#2d2342', '#251c38', '#a78bfa', '#c4b5fd', '#3fe0a0', '#ffce6b', '#ff6b6b',
        '#14251f', '#2b161c', '#2a2113', '#241a3d', '#2f2350', '#4a3d6e', '#4a3d6e',
        '#5d4e85', 'rgba(16,12,26,.92)', 'rgba(26,20,38,.85)', '#c4b5fd',
        'rgba(0,0,0,.40)', '#1a1426}',
    ]),
    # 墨绿
    'forest': _mk_palette([
        '#12211b', '#0a1410', '#e8f4ee', '#c2d8cd', '#93b0a3', '#749184', '#35594a',
        '#1e352b', '#1a2d24', '#34d399', '#6ee7b7', '#3fe0a0', '#ffce6b', '#ff6b6b',
        '#10281e', '#2a1616', '#2a2110', '#122a20', '#18382a', '#35594a', '#35594a',
        '#47735f', 'rgba(10,20,16,.92)', 'rgba(18,33,27,.85)', '#6ee7b7',
        'rgba(0,0,0,.40)', '#12211b}',
    ]),
}

_WAVE_CSS = """body::before,body::after{content:"";position:fixed;left:0;right:0;bottom:0;height:@H@;pointer-events:none;z-index:-1;background-repeat:repeat-x;background-position:bottom;background-size:1440px 100%;}
body::before{background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1440 120'%3E%3Cpath fill='%23@C1@' fill-opacity='@O1@' d='M0,64 C240,96 480,32 720,64 C960,96 1200,32 1440,64 L1440,120 L0,120 Z'/%3E%3C/svg%3E");animation:dsh-wave 24s linear infinite;}
body::after{background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1440 120'%3E%3Cpath fill='%23@C2@' fill-opacity='@O2@' d='M0,72 C260,44 520,100 780,72 C1040,44 1240,96 1440,72 L1440,120 L0,120 Z'/%3E%3C/svg%3E");animation:dsh-wave 34s linear infinite reverse;}
@keyframes dsh-wave{from{background-position-x:0}to{background-position-x:1440px}}
"""

def _waves(c1, o1, c2, o2, h):
    return (_WAVE_CSS.replace('@C1@', c1).replace('@O1@', o1)
                     .replace('@C2@', c2).replace('@O2@', o2).replace('@H@', h))

def _wrap(body_css, wave_css):
    return '<style id="dsh-glass">' + body_css + wave_css + '</style>'

_THEME_CSS = {
    'light': _wrap(
        'html{background:#f4f6fb;}'
        'body{background:radial-gradient(1200px 800px at 20% -10%,#ffffff 0%,#f4f6fb 60%) fixed;}'
        '.card,.progress-wrap,.chart-wrap,.log-wrap,.agent-wrap{background:rgba(255,255,255,.9);'
        'backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);border-radius:10px;'
        'border-top:1px solid rgba(30,60,120,.12);border-right:1px solid rgba(30,60,120,.12);'
        'border-bottom:1px solid rgba(30,60,120,.12);box-shadow:0 6px 20px rgba(30,60,120,.08);}'
        '.agent-card,.recovery-card{background:rgba(255,255,255,.72);border:1px solid rgba(30,60,120,.10);border-radius:8px;}'
        'h1{background:linear-gradient(92deg,#2563eb,#7c3aed 75%);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;}'
        '.progress-fill{box-shadow:0 0 8px rgba(37,99,235,.30);}',
        _waves('2563eb', '.13', '7c3aed', '.10', '40vh')),
    'dark': _wrap(
        'html{background:#151517;}'
        'body{background:radial-gradient(1200px 800px at 20% -10%,#1a1a1d 0%,#151517 55%) fixed;}'
        '.card,.progress-wrap,.chart-wrap,.log-wrap,.agent-wrap{background:rgba(27,27,28,.85);'
        'backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);'
        'border-top:1px solid rgba(255,255,255,.08);border-right:1px solid rgba(255,255,255,.08);'
        'border-bottom:1px solid rgba(255,255,255,.08);box-shadow:0 8px 24px rgba(0,0,0,.40);}'
        '.agent-card,.recovery-card{background:rgba(18,18,20,.6);border:1px solid rgba(255,255,255,.06);}'
        'h1{background:linear-gradient(92deg,#8fa8ff,#a78bfa 70%);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;}',
        _waves('6b8afd', '.14', 'a78bfa', '.10', '40vh')),
    'ocean': _wrap(
        'html{background:#05070f;}'
        'body{background:radial-gradient(1200px 800px at 20% -10%,#0d1526 0%,#05070f 55%) fixed;}'
        '.card,.progress-wrap,.chart-wrap,.log-wrap,.agent-wrap{background:rgba(20,27,45,.72);'
        'backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);'
        'border-top:1px solid rgba(90,120,200,.22);border-right:1px solid rgba(90,120,200,.22);'
        'border-bottom:1px solid rgba(90,120,200,.22);box-shadow:0 8px 24px rgba(0,0,0,.35);}'
        '.agent-card,.recovery-card{background:rgba(14,20,36,.6);border:1px solid rgba(90,120,200,.16);}'
        'h1{background:linear-gradient(92deg,#7db9ff,#b490ff 70%);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;}',
        _waves('5ea8ff', '.20', 'b490ff', '.15', '44vh')),
    'purple': _wrap(
        'html{background:#100c1a;}'
        'body{background:radial-gradient(1200px 800px at 20% -10%,#1c1430 0%,#100c1a 55%) fixed;}'
        '.card,.progress-wrap,.chart-wrap,.log-wrap,.agent-wrap{background:rgba(26,20,38,.72);'
        'backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);'
        'border-top:1px solid rgba(167,139,250,.20);border-right:1px solid rgba(167,139,250,.20);'
        'border-bottom:1px solid rgba(167,139,250,.20);box-shadow:0 8px 24px rgba(0,0,0,.35);}'
        '.agent-card,.recovery-card{background:rgba(18,13,28,.6);border:1px solid rgba(167,139,250,.14);}'
        'h1{background:linear-gradient(92deg,#c4b5fd,#f0abfc 70%);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;}',
        _waves('a78bfa', '.18', 'f0abfc', '.13', '42vh')),
    'forest': _wrap(
        'html{background:#0a1410;}'
        'body{background:radial-gradient(1200px 800px at 20% -10%,#10231a 0%,#0a1410 55%) fixed;}'
        '.card,.progress-wrap,.chart-wrap,.log-wrap,.agent-wrap{background:rgba(18,33,27,.72);'
        'backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);'
        'border-top:1px solid rgba(52,211,153,.18);border-right:1px solid rgba(52,211,153,.18);'
        'border-bottom:1px solid rgba(52,211,153,.18);box-shadow:0 8px 24px rgba(0,0,0,.35);}'
        '.agent-card,.recovery-card{background:rgba(12,24,18,.6);border:1px solid rgba(52,211,153,.13);}'
        'h1{background:linear-gradient(92deg,#6ee7b7,#3fd4dc 70%);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;}',
        _waves('34d399', '.16', '3fd4dc', '.12', '42vh')),
}

def _dsh_theme():
    try:
        with open(DSH_SETTINGS, encoding='utf-8') as f:
            m = re.search(r'ui-theme:\s*\n\s*preference:\s*(\w+)', f.read())
            return m.group(1).lower() if m else 'light'
    except Exception:
        return 'light'

def _effective_theme(cookie_header=''):
    pref = 'auto'
    if cookie_header:
        m = re.search(r'dash-theme=([\w-]+)', cookie_header)
        if m:
            pref = m.group(1)
    if pref in _THEME_CSS:
        return pref
    return 'dark' if _dsh_theme() == 'dark' else 'light'

def _apply_theme(html, cookie_header=''):
    theme = _effective_theme(cookie_header)
    if '<meta name="dsh-theme"' not in html and '</head>' in html:
        html = html.replace('</head>', '<meta name="dsh-theme" content="%s"></head>' % theme, 1)
    for old, new in _PALETTES.get(theme, []):
        html = html.replace(old, new)
    css = _THEME_CSS.get(theme)
    if css and '</head>' in html and 'id="dsh-glass"' not in html:
        html = html.replace('</head>', css + '</head>', 1)
    return html

def _current_prod_dir():
    """每次从 state 读取当前任务的 prod_dir，实现看板与工作区解耦。"""
    try:
        with open(STATE_FILE) as f:
            return json.load(f).get("task", {}).get("prod_dir", os.path.join(BASE, "production/small_verify_2600"))
    except Exception:
        return os.path.join(BASE, "production/small_verify_2600")

SCHEDULE = [
    (0,       100000,  300.0, 300.0),
    (100000,  400000,  300.0, 600.0),
    (400000,  700000,  600.0, 900.0),
    (700000,  1000000, 900.0, 1200.0),
    (1000000, 1350000, 1200.0, 1200.0),
]
TOTAL_STEPS = 1350000

def _effective_schedule(prod_dir=None):
    """温度程序按项目取(paths.load_schedule 读 md_config.json);配置缺失/写坏回退本模块
    内置斜坡(与 canonical 同值,仅作断配置兜底)。prod_dir → project 经 registry 反查,
    两侧 realpath 归一(symlink/尾斜杠别名不得漏项目,四轮对抗 N2)。"""
    project = None
    if prod_dir:
        rp = os.path.realpath(prod_dir)
        try:
            for j in registry.load()["jobs"]:
                jp = j.get("prod_dir")
                if jp and os.path.realpath(jp) == rp:
                    project = j.get("project")
                    break
        except Exception:
            pass
    return paths.load_schedule(project) or SCHEDULE

def parse_kinetic_stages(prod_dir):
    """解析 production.in / production_slow.in 中的阶段注释 (# Stage X: ...) 和对应的 run 步长"""
    for inp_name in ("production_slow.in", "production.in"):
        inp_path = os.path.join(prod_dir, inp_name)
        if os.path.exists(inp_path):
            break
    else:
        return []
    try:
        with open(inp_path) as f:
            lines = f.readlines()
        stages = []
        current_stage = None
        for line in lines:
            line_stripped = line.strip()
            # 匹配阶段注释，如 # Stage 1: NVT equilibration 或 # ==================== Stage 2: NVT hold at 1200K ====================
            m = re.match(r'#+\s*(Stage\s*\d+.*?)(?:\s*=+\s*)?$', line_stripped, re.I)
            if m:
                current_stage = m.group(1).strip()
            if line_stripped.lower().startswith('run ') and current_stage:
                try:
                    nsteps = int(line_stripped.split()[1])
                    stages.append((current_stage, nsteps))
                    current_stage = None
                except Exception:
                    pass
        return stages
    except Exception:
        return []

def current_kinetic_stage(prod_dir, step):
    """根据当前 step 判断处于 production.in 的哪个动力学阶段"""
    stages = parse_kinetic_stages(prod_dir)
    if not stages:
        return {"stage": "UNKNOWN", "stage_desc": "", "stage_progress": "0/0", "stage_target": ""}
    cumulative = 0
    for idx, (name, nsteps) in enumerate(stages):
        cumulative += nsteps
        if step <= cumulative:
            progress = f"{step}/{cumulative}"
            return {
                "stage": name,
                "stage_desc": f"阶段 {idx+1}/{len(stages)}",
                "stage_progress": progress,
                "stage_target": f"run {nsteps}"
            }
    # 超出最后阶段
    return {
        "stage": stages[-1][0],
        "stage_desc": f"阶段 {len(stages)}/{len(stages)}",
        "stage_progress": f"{step}/{cumulative}",
        "stage_target": "completed"
    }

def target_temp(step, schedule=None):
    sched = schedule or SCHEDULE
    for s, e, ts, te in sched:
        if s <= step <= e:
            return ts if e == s else ts + (step - s) / (e - s) * (te - ts)
    return sched[-1][3]  # 越界按末段终温(与 monitor_agent 同口径,四轮对抗 N1)

_THERMO_COL_MAP = {'Step': 'step', 'Temp': 'temp', 'TotEng': 'etotal', 'PotEng': 'pe',
                   'KinEng': 'ke', 'Press': 'press', 'Volume': 'vol', 'Density': 'density'}


def parse_log_tail(log_path, n=40, log_format="lammps-thermo-8col"):
    """解析热力学日志尾部。优先表头自适应:在读取窗口内找最后一个 'Step Temp ...' 表头行,
    按列名映射(列序无关,兼容 cpcm 的 PotEng/KinEng 与 Density/Volume 换序);
    窗口内无表头时按 log_format 位置兜底:
    lammps-thermo-8col: Step Temp TotEng PotEng KinEng Press Volume Density
    lammps-thermo-7col: Step Temp TotEng PotEng KinEng Press Volume(board_feed 转换格式,
      无 Density 列;board_feed.py 已被 agent 自行删除,列序按"8col 去末尾 Density"推断)"""
    if not log_path or not os.path.exists(log_path):
        return []
    try:
        with open(log_path, 'rb') as f:
            f.seek(0, 2); size = f.tell()
            buf = b''
            # 读够 n+5 行后继续往回读找热力学表头(列序自适应);
            # 上限 3000 行:已完成的作业日志尾部有长收尾段,表头可能在数百行之前
            while size > 0:
                chunk = min(4096, size)
                f.seek(size - chunk); buf = f.read(chunk) + buf; size -= chunk
                nl = buf.count(b'\n')
                if nl > 3000 or (nl > n + 5 and b'Step' in buf and
                                 re.search(rb'^\s*Step\s+Temp', buf, re.M)):
                    break
            lines = buf.decode('utf-8', errors='ignore').split('\n')
            # 表头自适应·分段映射:每遇到热力学表头就更新列映射,其后的数据行用该映射——
            # 重启续跑列序变更时,表头前后的行各用各的映射,不错列(2026-08-29 对抗审计 M1)
            col_idx = None  # 当前段的列名映射;窗口内从未见表头则按 log_format 位置兜底
            rows = []
            for line in lines:
                parts = line.strip().split()
                if len(parts) >= 7 and parts[0] == 'Step' and 'Temp' in parts:
                    col_idx = {i: _THERMO_COL_MAP[p] for i, p in enumerate(parts)
                               if p in _THERMO_COL_MAP}
                    continue
                if not parts or not parts[0].isdigit():
                    continue
                try:
                    if col_idx:
                        if len(parts) <= max(col_idx):
                            continue
                        row = {k: (int(parts[i]) if k == 'step' else float(parts[i]))
                               for i, k in col_idx.items()}
                        row.setdefault('density', 0.0)
                        rows.append(row)
                    elif log_format == "lammps-thermo-7col" and len(parts) >= 7:
                        rows.append({'step': int(parts[0]), 'temp': float(parts[1]),
                                     'etotal': float(parts[2]), 'pe': float(parts[3]),
                                     'ke': float(parts[4]), 'press': float(parts[5]),
                                     'vol': float(parts[6]), 'density': 0.0})
                    elif log_format == "lammps-thermo-8col" and len(parts) >= 8:
                        rows.append({'step': int(parts[0]), 'temp': float(parts[1]),
                                     'etotal': float(parts[2]), 'pe': float(parts[3]),
                                     'ke': float(parts[4]), 'press': float(parts[5]),
                                     'vol': float(parts[6]), 'density': float(parts[7])})
                except (ValueError, IndexError):
                    pass
            # nan/inf 防护:跑飞的日志会带非法浮点,进 JSON 响应会击穿前端 JSON.parse
            rows = [r for r in rows
                    if all(math.isfinite(v) for v in r.values() if isinstance(v, float))]
            return rows[-n:]
    except Exception:
        return []

def parse_species(species_path):
    """解析 ReaxFF species 文件，返回最新一帧的物种统计。"""
    if not species_path or not os.path.exists(species_path):
        return {}
    try:
        with open(species_path, 'r', errors='ignore') as f:
            lines = f.readlines()
        if not lines:
            return {}
        last_header = None
        last_data = None
        for i, line in enumerate(lines):
            if line.strip().startswith('#'):
                last_header = line.strip()
                if i + 1 < len(lines):
                    last_data = lines[i + 1].strip()
        if not last_header or not last_data:
            return {}
        header_parts = last_header.lstrip('#').strip().split()
        data_parts = last_data.split()
        if len(header_parts) != len(data_parts):
            return {}
        result = {}
        for h, d in zip(header_parts, data_parts):
            try:
                result[h] = int(d)
            except Exception:
                try:
                    result[h] = float(d)
                except Exception:
                    result[h] = d
        species_counts = {}
        for k, v in result.items():
            if k not in ('Timestep', 'No_Moles', 'No_Specs') and isinstance(v, int):
                species_counts[k] = v
        top_species = sorted(species_counts.items(), key=lambda x: x[1], reverse=True)[:15]
        return {
            'timestep': result.get('Timestep', 0),
            'no_moles': result.get('No_Moles', 0),
            'no_specs': result.get('No_Specs', 0),
            'top_species': [{'name': k, 'count': v} for k, v in top_species],
        }
    except Exception as e:
        return {'error': str(e)}

def parse_atom_count(log_path):
    """从日志开头解析实际运行原子数（如 'with 2576 atoms'）"""
    if not log_path or not os.path.exists(log_path):
        return 0
    try:
        with open(log_path, 'rb') as f:
            # 只读前 8192 字节找原子数
            buf = f.read(8192).decode('utf-8', errors='ignore')
        # 优先匹配 Loop time 行中的 with X atoms
        m = re.search(r'with\s+(\d+)\s+atoms', buf)
        if m:
            return int(m.group(1))
        # 回退到 reading atoms ...\n  X atoms
        m = re.search(r'reading atoms\s*\.\.\.\s*\n\s*(\d+)\s+atoms', buf)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return 0

def _pids_from_pidfile(prod_dir):
    """任务目录下 .pids 文件登记的外部启动 lmp 进程(仍校验进程真实存活且为 lmp_mpi)"""
    pids = []
    aux = os.path.join(prod_dir, ".pids") if prod_dir else None
    if aux and os.path.exists(aux):
        try:
            with open(aux) as f:
                for tok in f.read().split():
                    if tok.isdigit() and os.path.exists("/proc/%s" % tok):
                        try:
                            with open("/proc/%s/cmdline" % tok, 'rb') as cf:
                                if b"lmp_mpi" in cf.read():
                                    pids.append(int(tok))
                        except (PermissionError, FileNotFoundError, OSError):
                            pass
        except Exception:
            pass
    return pids


def get_lmp_pids(prod_dir):
    pids = []
    if not prod_dir:
        return pids
    target = os.path.abspath(prod_dir)
    try:
        for pid_dir in os.listdir("/proc"):
            if not pid_dir.isdigit():
                continue
            try:
                with open(f"/proc/{pid_dir}/cmdline", 'rb') as f:
                    cmdline = f.read().replace(b'\x00', b' ').decode('utf-8', errors='ignore')
                if "lmp_mpi" not in cmdline:
                    continue
                cwd = os.readlink(f"/proc/{pid_dir}/cwd")
                if os.path.abspath(cwd) == target:
                    pids.append(int(pid_dir))
            except (PermissionError, FileNotFoundError, OSError):
                pass
    except Exception:
        pass
    # 附加回退: 任务目录下 .pids 文件登记的外部启动 lmp 进程
    # (用于 cwd 不在 RUN_CASE_BASE 扫描树内的手工/复现任务)
    pids.extend(_pids_from_pidfile(prod_dir))
    return pids


# mdorch 服务态发车的 lmp 进程对 user 的 /proc/PID/cwd 不可读(ptrace 限制),
# cwd 匹配法对这些进程全盲 —— 记录 cwd 不可读但 cmdline 命中的进程作归属候选
def blind_lmp_pids():
    pids = []
    try:
        for pid_dir in os.listdir("/proc"):
            if not pid_dir.isdigit():
                continue
            try:
                with open(f"/proc/{pid_dir}/cmdline", 'rb') as f:
                    cmdline = f.read().replace(b'\x00', b' ').decode('utf-8', errors='ignore')
                if "lmp_mpi" not in cmdline:
                    continue
                try:
                    os.readlink(f"/proc/{pid_dir}/cwd")
                except PermissionError:
                    pids.append(int(pid_dir))
            except (PermissionError, FileNotFoundError, OSError):
                pass
    except Exception:
        pass
    return pids


# 判活兜底缓存: {prod_dir: [last_step, last_change_ts]}。cwd 扫描为盲时,
# 看 step 是否还在变(真相源是日志本身,不经过任何模型)。thermo 每 ~2.5min
# 才落一行,而看板 3s 一刷,单看"两次 poll 间推进"只会亮一个周期——改记
# 「step 最近一次变化时刻」,15 分钟内动过即认活(6 个 thermo 间隔无变化=死)
_LIVENESS_CACHE = {}


def _liveness_step_fallback(key, step, pids):
    """cwd 匹配为盲(mdorch 属主)时按日志 step 变化判活;返回 (alive, pids)。"""
    now = time.time()
    if pids:
        _LIVENESS_CACHE[key] = [step, now]
        return True, pids
    ent = _LIVENESS_CACHE.get(key)
    if ent is None:
        # 首见不宽限:变化时刻记 0。否则重启后 15 分钟内全部死案误亮
        # (2026-09-15 实测 48/48 全绿)。真跑最迟一个 thermo 间隔内 step
        # 变化即转活,代价只是监控重启后 ≤2.5min 的保守红灯。
        _LIVENESS_CACHE[key] = [step, 0]
        return False, pids
    if step != ent[0]:
        ent[0] = step
        ent[1] = now
    if ent[1] and now - ent[1] < 900:
        return True, blind_lmp_pids()
    return False, pids


def pids_for_entry(entry):
    """按 registry 条目的 pid_source 分派判活:hpc 条目永不走 /proc。"""
    src = entry.get("pid_source", "proc-cwd")
    prod = entry.get("prod_dir")
    if src == "proc-cwd":
        found = get_lmp_pids(prod)
        if found:
            return found
        # 2026-09-12 修复: 权限墙下 mdorch 进程的 /proc/<pid>/cwd 对 user
        # Permission denied(扫描全盲, S2 曾被误判已停止)。回退: 用 launch_agent
        # 登记的 pids + cmdline 校验(cmdline 跨 uid 可读)判活。
        out = []
        for p in entry.get("pids") or []:
            try:
                p = int(p)
            except (TypeError, ValueError):
                continue
            try:
                with open("/proc/%d/cmdline" % p, 'rb') as cf:
                    if b"lmp_mpi" in cf.read():
                        out.append(p)
            except (PermissionError, FileNotFoundError, OSError):
                pass
        return out
    if src == "pids-file":
        return _pids_from_pidfile(prod)
    if src == "registered":
        out = []
        for p in entry.get("pids") or []:
            try:
                p = int(p)
            except (TypeError, ValueError):
                continue
            # pid 复用防御:光 /proc 存在不够,cmdline 必须仍是 lmp_mpi
            try:
                with open("/proc/%d/cmdline" % p, 'rb') as cf:
                    if b"lmp_mpi" in cf.read():
                        out.append(p)
            except (PermissionError, FileNotFoundError, OSError):
                pass
        return out
    return []


# ── GROMACS 运行扫描(2026-09-10):唯一实现已抽到 gmx_scan.py(与 MCP 工具
# sim_gmx_status 共享);此处 re-export 保持既有调用面不变。 ──
from gmx_scan import (  # noqa: E402,F401
    _GMX_STAGE_LOGS, _gmx_total_steps, get_gmx_pids, gmx_run_detail,
    parse_gmx_log, scan_gromacs_runs)
from gmx_scan import monitor_roots as _monitor_roots  # noqa: E402,F401

def parse_orchestrator_log(n=30):
    log_path = os.path.join(BASE, "logs", "orchestrator_v3.3.jsonl")
    if not os.path.exists(log_path):
        return []
    try:
        with open(log_path, 'rb') as f:
            f.seek(0, 2)
            size = f.tell()
            buf = b''
            while size > 0 and len(buf.split(b'\n')) <= n + 5:
                chunk = min(8192, size)
                f.seek(size - chunk)
                buf = f.read(chunk) + buf
                size -= chunk
            lines = buf.decode('utf-8', errors='ignore').strip().split('\n')
            logs = []
            for line in lines[-n:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    logs.append({
                        'time': entry.get('ts', ''),
                        'level': entry.get('level', 'INFO'),
                        'msg': entry.get('msg', '')
                    })
                except (json.JSONDecodeError, Exception):
                    pass
            return logs
    except Exception:
        return []

def parse_strategy_log(n=20):
    log_path = os.path.join(BASE, "logs", "strategy_decisions.jsonl")
    if not os.path.exists(log_path):
        return []
    try:
        with open(log_path, 'rb') as f:
            f.seek(0, 2)
            size = f.tell()
            buf = b''
            while size > 0 and len(buf.split(b'\n')) <= n + 5:
                chunk = min(8192, size)
                f.seek(size - chunk)
                buf = f.read(chunk) + buf
                size -= chunk
            lines = buf.decode('utf-8', errors='ignore').strip().split('\n')
            logs = []
            for line in lines[-n:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    logs.append({
                        'time': entry.get('ts', ''),
                        'phase_from': entry.get('phase_from', ''),
                        'phase_to': entry.get('phase_to', ''),
                        'trigger': entry.get('trigger', ''),
                        'decision': entry.get('decision', ''),
                        'details': entry.get('details', {})
                    })
                except (json.JSONDecodeError, Exception):
                    pass
            return logs
    except Exception:
        return []

def _parse_any_ts(v):
    """epoch 秒(float/int/数字串)或 ISO 'YYYY-MM-DDTHH:MM:SS[.ffffff]' → epoch;失败 None。"""
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v or '').strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        pass
    for fmt in ('%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S'):
        try:
            return time.mktime(time.strptime(s, fmt))
        except Exception:
            continue
    return None


def _scan_jsonl_tail(path, max_bytes=2 * 1024 * 1024):
    """读 jsonl 尾部 ≤max_bytes 逐行容错解析(截断点首行可能是半行,丢弃);
    缺文件/坏行返回已解析部分,永不抛。"""
    rows = []
    try:
        size = os.path.getsize(path)
        with open(path, 'rb') as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                f.readline()
            data = f.read().decode('utf-8', errors='replace')
    except Exception:
        return rows
    for line in data.split('\n'):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _friction_ts(row):
    """friction.jsonl 的 t 是毫秒时间戳;坏值返回 None。"""
    try:
        return float(row.get('t')) / 1000.0
    except (TypeError, ValueError):
        return None


def build_trajectory(task=None, session=None, limit=300):
    """每任务/每会话轨迹时间线(2026-09-09 经验沉淀看板面)。
    任务轨迹 = pipeline 事件流(按 case_id)+ orchestrator 日志(消息含 case_id)
              + 策略决策(整行含 case_id);会话轨迹 = friction.jsonl 轮级行。
    只读组合既有日志,一个源缺席不影响其它源,缺文件/坏行降级为空。"""
    items = []
    if task:
        for ev in _scan_jsonl_tail(paths.pipeline_events_log()):
            if str(ev.get('case_id') or '') != task:
                continue
            items.append({
                'ts': _parse_any_ts(ev.get('ts')),
                'time': str(ev.get('ts') or ''),
                'src': 'pipeline',
                'text': '%s %s' % (ev.get('status', ''),
                                   str(ev.get('error') or ev.get('reason') or '')),
                'extra': {k: ev.get(k) for k in ('status', 'step', 'risk_score', 'verdict')
                          if ev.get(k) is not None},
            })
        for line in _scan_jsonl_tail(os.path.join(BASE, 'logs', 'orchestrator_v3.3.jsonl')):
            msg = str(line.get('msg') or '')
            if task not in msg:
                continue
            items.append({'ts': _parse_any_ts(line.get('ts')),
                          'time': str(line.get('ts') or ''),
                          'src': 'orchestrator',
                          'text': '[%s] %s' % (line.get('level', 'INFO'), msg)})
        for line in _scan_jsonl_tail(os.path.join(BASE, 'logs', 'strategy_decisions.jsonl')):
            if task not in json.dumps(line, ensure_ascii=False):
                continue
            items.append({'ts': _parse_any_ts(line.get('ts')),
                          'time': str(line.get('ts') or ''),
                          'src': 'strategy',
                          'text': '%s→%s %s %s' % (line.get('phase_from', '?'),
                                                   line.get('phase_to', '?'),
                                                   line.get('trigger', ''),
                                                   line.get('decision', ''))})
    if session:
        for row in _scan_jsonl_tail(paths.friction_log()):
            if row.get('kind') != 'turn-friction' or str(row.get('session') or '') != session:
                continue
            t = _friction_ts(row)
            items.append({
                'ts': t,
                'time': time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(t)) if t else '',
                'src': 'friction',
                'text': 'turn %s %s interrupt=%s err=%s deny=%s retry=%s tools=%s' % (
                    row.get('turn'), row.get('reason', '?'),
                    row.get('interrupts', 0), row.get('tool_errors', 0),
                    row.get('denials', 0), row.get('retries', 0), row.get('tools', 0)),
                'extra': {'failed_tools': row.get('failed_tools') or []},
            })
    items.sort(key=lambda it: (it['ts'] is None, it['ts'] or 0))
    items = items[-limit:]

    # 会话摩擦总览:friction.jsonl 按 session 聚合(无参调用时的入口面板)
    sessions = {}
    for row in _scan_jsonl_tail(paths.friction_log()):
        if row.get('kind') != 'turn-friction':
            continue
        sid = str(row.get('session') or '')
        if not sid:
            continue
        agg = sessions.setdefault(sid, {'session': sid, 'turns': 0, 'interrupts': 0,
                                        'tool_errors': 0, 'denials': 0, 'retries': 0,
                                        'first': None, 'last': None})
        agg['turns'] += 1
        for k in ('interrupts', 'tool_errors', 'denials', 'retries'):
            try:
                agg[k] += int(row.get(k) or 0)
            except (TypeError, ValueError):
                pass
        t = _friction_ts(row)
        if t is not None:
            agg['first'] = t if agg['first'] is None else min(agg['first'], t)
            agg['last'] = t if agg['last'] is None else max(agg['last'], t)
    overview = sorted(sessions.values(),
                      key=lambda a: (a['last'] or 0), reverse=True)[:50]

    return {'task': task or '', 'session': session or '',
            'items': items, 'sessions': overview, 'ts': time.time()}


def _resolve_prod_dir(task_param):
    """按 case_id 找 prod_dir:先扫任务列表(目录名索引);查不到时兜底——
    adopt 接管的 case 目录名≠case_id,扫描列表永远查不到,state 里有真 prod_dir
    (24 轮看板白屏:查不到 → 404 → 前端 d.step 抛异常整屏留白)。"""
    all_tasks = scan_tasks()
    for group in ['active', 'today_stopped', 'history']:
        for t in all_tasks.get(group, []):
            if t['case_id'] == task_param:
                return t['prod_dir']
    st_task = (get_state().get('task') or {})
    if task_param == st_task.get('case_id') and st_task.get('prod_dir'):
        return st_task['prod_dir']
    return None


def get_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def scan_tasks():
    """扫描程序运行-case目录下的所有任务，按时间分组"""
    today = time.strftime('%Y%m%d')
    active = []
    today_stopped = []
    history = []
    if not os.path.exists(RUN_CASE_BASE):
        result = {"active": active, "today_stopped": today_stopped, "history": history,
                  "current_case_id": ""}
        _merge_registry(result, today, "")
        return result

    # 读取当前 orchestrator 管理的任务
    current_case_id = ""
    current_prod_dir = ""
    try:
        state = get_state()
        current_case_id = state.get("task", {}).get("case_id", "")
        current_prod_dir = state.get("task", {}).get("prod_dir", "") or ""
    except Exception:
        pass

    try:
        for date_dir in sorted(os.listdir(RUN_CASE_BASE), reverse=True):
            date_path = os.path.join(RUN_CASE_BASE, date_dir)
            if not os.path.isdir(date_path):
                continue
            for task_dir in sorted(os.listdir(date_path), reverse=True):
                task_path = os.path.join(date_path, task_dir)
                if not os.path.isdir(task_path):
                    continue
                # 查找活跃日志
                log_candidates = [
                    os.path.join(task_path, "production.stdout"),
                    os.path.join(task_path, "production_slow.stdout"),
                    os.path.join(task_path, "log.lammps"),
                    os.path.join(task_path, "log_production_slow.lammps"),
                    os.path.join(task_path, "production.log")
                ]
                active_log = None
                for lc in log_candidates:
                    if os.path.exists(lc) and os.path.getsize(lc) > 0:
                        active_log = lc
                        break
                if not active_log:
                    continue
                rows = parse_log_tail(active_log, 5)
                latest = rows[-1] if rows else {}
                pids = get_lmp_pids(task_path)
                _alive_now, pids = _liveness_step_fallback(task_path, latest.get('step', 0), pids)
                task = {
                    'case_id': task_dir,
                    'prod_dir': task_path,
                    'date': date_dir,
                    'step': latest.get('step', 0),
                    'temp': round(latest.get('temp', 0), 1),
                    'etotal': round(latest.get('etotal', 0), 1),
                    'alive': len(pids) > 0,
                    'pids_count': len(pids),
                    'completed': os.path.exists(os.path.join(task_path, 'final.data')),
                    'last_modified': time.strftime('%H:%M:%S', time.localtime(os.path.getmtime(active_log))),
                    # adopt 接管的 case:目录名≠case_id,按 state prod_dir 认亲
                    'is_current': (task_dir == current_case_id
                                   or (current_prod_dir and os.path.realpath(task_path) == os.path.realpath(current_prod_dir)))
                }
                if task['alive']:
                    active.append(task)
                elif date_dir == today:
                    today_stopped.append(task)
                else:
                    history.append(task)
    except Exception:
        pass
    # 编排器当前任务兜底:守护重启到 RUN_CASE_BASE 之外的目录(如 cases/ 树里的
    # RUN2000_rN)时目录扫描看不到,但 state.task.prod_dir 已被领养链更新——按它补一条
    if current_prod_dir and os.path.isdir(current_prod_dir):
        # FIX-DC(2026-09-18): recover 迁入 _rN 后 state 的 prod_dir 仍是 base,
        # 兜底条目读 base 残骸(run-0 step=160000)被列进"今日已停止"——真身在 _current。
        _cur = current_prod_dir + "_current"
        if os.path.islink(_cur) and os.path.isdir(os.path.realpath(_cur)):
            current_prod_dir = os.path.realpath(_cur)
        rp = os.path.realpath(current_prod_dir)
        listed = any(os.path.realpath(t.get('prod_dir') or '') == rp
                     for t in active + today_stopped + history)
        if not listed:
            cur_log = None
            for lc in (os.path.join(current_prod_dir, n) for n in
                       ("production.stdout", "production_slow.stdout", "log.lammps",
                        "log_production_slow.lammps", "production.log")):
                if os.path.exists(lc) and os.path.getsize(lc) > 0:
                    cur_log = lc
                    break
            if cur_log:
                rows = parse_log_tail(cur_log, 5)
                latest = rows[-1] if rows else {}
                pids = get_lmp_pids(current_prod_dir)
                _alive_now, pids = _liveness_step_fallback(current_prod_dir, latest.get('step', 0), pids)
                entry = {
                    'case_id': current_case_id or os.path.basename(current_prod_dir),
                    'prod_dir': current_prod_dir,
                    'date': today,
                    'step': latest.get('step', 0),
                    'temp': round(latest.get('temp', 0), 1),
                    'etotal': round(latest.get('etotal', 0), 1),
                    'alive': len(pids) > 0,
                    'pids_count': len(pids),
                    'completed': os.path.exists(os.path.join(current_prod_dir, 'final.data')),
                    'last_modified': time.strftime('%H:%M:%S', time.localtime(os.path.getmtime(cur_log))),
                    'is_current': True,
                }
                (active if entry['alive'] else today_stopped).append(entry)
    result = {"active": active, "today_stopped": today_stopped, "history": history,
              "current_case_id": current_case_id}
    _merge_registry(result, today, current_case_id)
    return result


def _registry_task_from_entry(entry, current_case_id):
    """registry 条目 → 看板任务 dict。本地条目按 pid_source 判活;
    hpc 条目读 hpc_cache(采集器落地),永不本地 /proc 探测。"""
    loc = entry.get("location") or {"type": "local"}
    if not isinstance(loc, dict):
        loc = {"type": "local"}
    fmt = entry.get("log_format", "lammps-thermo-8col")
    reg_at = str(entry.get("registered_at") or "")
    date = reg_at[:10].replace("-", "") if reg_at else time.strftime('%Y%m%d')
    task = {
        'case_id': entry.get("case_id", ""),
        'prod_dir': entry.get("prod_dir", ""),
        'date': date,
        'project': entry.get("project", "default"),
        'registered': True,
        'is_current': entry.get("case_id") == current_case_id,
    }
    if loc.get("type") == "hpc":
        # 缓存文件名与采集器同口径(清洗+hash 防碰撞,见 registry.hpc_cache_basename)
        cluster = loc.get("cluster") or "default"
        jobid = str(loc.get("jobid") or "")
        cache_base = os.path.join(paths.hpc_cache_dir(), registry.hpc_cache_basename(cluster, jobid))
        cache = {}
        try:
            with open(cache_base + ".json", encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            pass
        state = cache.get("state", "no-data")
        rows = parse_log_tail(cache_base + ".log_tail.txt", 5, fmt)
        latest = rows[-1] if rows else {}
        task.update({
            'remote': True, 'cluster': cluster, 'jobid': jobid,
            'scheduler_state': state,
            'step': latest.get('step', 0),
            'temp': round(latest.get('temp', 0), 1),
            'etotal': round(latest.get('etotal', 0), 1),
            'alive': state in ("running", "pending", "completing", "configuring"),
            'pids_count': 0,
            'last_modified': cache.get("collected_at", reg_at)[11:19] or reg_at,
        })
        return task
    _pd = entry["prod_dir"]
    _cur = _pd + "_current"  # FIX-DC: 跟随 recover 迁入指针(见 scan_tasks 兜底注释)
    if os.path.islink(_cur) and os.path.isdir(os.path.realpath(_cur)):
        _pd = os.path.realpath(_cur)
    log_path = os.path.join(_pd, entry["log_file"])
    rows = parse_log_tail(log_path, 5, fmt)
    latest = rows[-1] if rows else {}
    pids = pids_for_entry(entry)
    pids = _liveness_step_fallback(_pd, latest.get('step', 0), pids)[1]
    try:
        mtime = time.strftime('%H:%M:%S', time.localtime(os.path.getmtime(log_path)))
    except Exception:
        mtime = reg_at[11:19] or ""
    task.update({
        'remote': False,
        'step': latest.get('step', 0),
        'temp': round(latest.get('temp', 0), 1),
        'etotal': round(latest.get('etotal', 0), 1),
        'alive': len(pids) > 0,
        'pids_count': len(pids),
        'completed': os.path.exists(os.path.join(_pd, 'final.data')),
        'last_modified': mtime,
    })
    return task


def _merge_registry(result, today, current_case_id):
    """registry 优先:已登记但未被目录扫描覆盖的作业补入;按 prod_dir(本地)/case_id 去重。
    registry 损坏/为空时 load() 静默降级为空表,等价纯扫描模式。"""
    try:
        jobs = registry.load().get("jobs", [])
    except Exception:
        return
    if not jobs:
        return
    seen_dirs = set()
    seen_ids = set()
    seen_hpc = set()
    for group in ("active", "today_stopped", "history"):
        for t in result[group]:
            seen_ids.add(t.get("case_id"))
            if t.get("prod_dir"):
                seen_dirs.add(os.path.realpath(t["prod_dir"]))
    for entry in jobs:
        try:
            if not isinstance(entry, dict):
                continue  # 畸形条目跳过,不得拖垮整个 /api/tasks
            loc = entry.get("location") or {"type": "local"}
            if not isinstance(loc, dict):
                loc = {"type": "local"}
            if entry.get("case_id") in seen_ids:
                continue
            if loc.get("type") == "hpc":
                # hpc 条目无本地 prod_dir 去重键:同 cluster+jobid 的重复登记只许显示一次(三轮对抗)
                hpc_key = (str(loc.get("cluster") or "default"), str(loc.get("jobid") or ""))
                if hpc_key in seen_hpc:
                    continue
            elif entry.get("prod_dir") \
                    and os.path.realpath(entry["prod_dir"]) in seen_dirs:
                continue
            task = _registry_task_from_entry(entry, current_case_id)
        except Exception:
            continue
        # 采纳即回写 seen:registry 内部互异 case_id 但同 realpath 的条目也不许双双显示(二轮对抗)
        seen_ids.add(entry.get("case_id"))
        if loc.get("type") == "hpc":
            seen_hpc.add(hpc_key)
        elif entry.get("prod_dir"):
            seen_dirs.add(os.path.realpath(entry["prod_dir"]))
        if task['alive']:
            result["active"].append(task)
        elif task['date'] == today:
            result["today_stopped"].append(task)
        else:
            result["history"].append(task)

def get_orchestrator_pid():
    """扫描当前运行的 orchestrator 进程"""
    try:
        for pid_dir in os.listdir("/proc"):
            if not pid_dir.isdigit():
                continue
            try:
                with open(f"/proc/{pid_dir}/cmdline", 'rb') as f:
                    cmdline = f.read().replace(b'\x00', b' ').decode('utf-8', errors='ignore')
                if "orchestrator.py" in cmdline and "v3.3" in cmdline:
                    return int(pid_dir)
            except (PermissionError, FileNotFoundError, OSError):
                pass
    except Exception:
        pass
    return None

def get_monitor_daemon_health(daemon_pid, prod_dir):
    """检查 monitor daemon 的健康状态"""
    result = {"pid": daemon_pid, "alive": False, "last_write_sec": None, "healthy": False}
    if daemon_pid:
        result["alive"] = os.path.exists(f"/proc/{daemon_pid}")
    if prod_dir:
        live_file = os.path.join(prod_dir, ".monitor_live.json")
        if os.path.exists(live_file):
            result["last_write_sec"] = int(time.time() - os.path.getmtime(live_file))
            result["healthy"] = result["last_write_sec"] < 120  # 2 min threshold
    return result

def get_dsh_layer_status():
    """真实架构状态:dsh 裁决层(对话服务+审计通道) 与 监控通道(monitor_agent/定时巡检)。
    旧 orchestrator 进程已于 2026-06-30 停心跳,判断职责由 dsh 接管,看板改显此数据。"""
    import socket
    res = {
        'dsh_web_alive': False,
        'dsh_web_port': 3091,
        'last_audit_sec': None,
        'last_monitor_agent_sec': None,
        'push_cron': False,
        'orchestrator_note': '原 orchestrator 已于 2026-06-30 停心跳,判断职责由 dsh 裁决层接管',
    }
    try:
        s = socket.create_connection(('127.0.0.1', 3091), timeout=1)
        s.close()
        res['dsh_web_alive'] = True
    except Exception:
        pass
    audit = os.path.join(BASE, 'logs', 'claude_operations.jsonl')
    if os.path.exists(audit):
        now = time.time()
        res['last_audit_sec'] = int(now - os.path.getmtime(audit))
        try:
            from datetime import datetime
            last_ts = None
            with open(audit, encoding='utf-8', errors='replace') as f:
                for line in f:
                    if 'monitor_agent' in line:
                        try:
                            ts = json.loads(line).get('ts')
                            if ts:
                                last_ts = ts
                        except Exception:
                            pass
            if last_ts:
                s = str(last_ts)
                for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                    try:
                        dt = datetime.strptime(s[:26], fmt)
                        res['last_monitor_agent_sec'] = int((datetime.now() - dt).total_seconds())
                        break
                    except ValueError:
                        continue
        except Exception:
            pass
    try:
        import subprocess
        out = subprocess.check_output(['crontab', '-l'], stderr=subprocess.DEVNULL, timeout=3).decode('utf-8', 'replace')
        res['push_cron'] = 'md_push_driver' in out
    except Exception:
        pass
    return res

def get_agent_chain(state):
    """构建 agent 执行链，展示每个子智能体的最近运行详情"""
    chain = []
    agent_names = {
        "preflight": "预检 Agent",
        "launch": "启动 Agent",
        "monitor": "监控 Agent",
        "recover": "修复 Agent",
        "preemptive_recover": "预防修复 Agent",
        "analyze": "分析 Agent"
    }
    agents = state.get("agents", {})
    for key, label in agent_names.items():
        data = agents.get(key, {})
        if not data:
            continue
        entry = {
            "name": key,
            "label": label,
            "status": data.get("status", data.get("success", "N/A")),
            "step": data.get("step", "N/A"),
            "error": data.get("error", ""),
            "details": {}
        }
        if key == "preflight":
            entry["details"]["issues"] = data.get("issues", [])
            entry["details"]["fixes"] = data.get("fixes_applied", [])
            entry["details"]["pass"] = data.get("pass", False)
        elif key == "launch":
            entry["details"]["pids"] = data.get("pids", [])
            entry["details"]["prod_dir"] = data.get("prod_dir", "")
        elif key in ("recover", "preemptive_recover"):
            entry["details"]["strategy"] = data.get("strategy", "")
            entry["details"]["strategy_name"] = data.get("strategy_name", "")
            entry["details"]["changes"] = data.get("changes", "")
            entry["details"]["root_cause"] = data.get("root_cause", "")
        elif key == "analyze":
            entry["details"]["char_yield"] = data.get("char_yield", 0)
            entry["details"]["p_retention"] = data.get("p_retention", 0)
        chain.append(entry)
    return chain

def get_status(prod_dir=None):
    state = get_state()
    monitor = state.get('agents', {}).get('monitor', {})
    phase = state.get('phase', 'UNKNOWN')
    analyze_result = state.get('agents', {}).get('analyze', {})

    # 动态读取当前任务工作区，实现 Agent 与 Case 分离
    if prod_dir is None:
        prod_dir = state.get('task', {}).get('prod_dir', _current_prod_dir())
    else:
        # FIX-PS(2026-09-18): 详情页查非当前任务时不能借用编排器当前相位
        # (RUN2500 在跑时 RUN2000 详情曾显示 MONITOR)——按本地产物推断
        _cur = (state.get('task') or {}).get('prod_dir') or ''
        try:
            _same = _cur and os.path.realpath(prod_dir) == os.path.realpath(_cur)
        except OSError:
            _same = False
        if not _same:
            phase = 'DONE' if os.path.exists(os.path.join(prod_dir, 'final.data')) else 'HISTORY'
            monitor = {}         # 非当前任务: state 里的 monitor/analyze 节是别人的
            analyze_result = {}  # 一律禁用, 只信本地产物与日志
    log_candidates = [
        os.path.join(prod_dir, "production.stdout"),
        os.path.join(prod_dir, "production_slow.stdout"),
        os.path.join(prod_dir, "log.lammps"),
        os.path.join(prod_dir, "log_production_slow.lammps"),
        os.path.join(prod_dir, "production.log")
    ]
    active_log = None
    for lc in log_candidates:
        if os.path.exists(lc) and os.path.getsize(lc) > 0:
            active_log = lc
            break

    rows = parse_log_tail(active_log, 40) if active_log else []
    latest = rows[-1] if rows else {}
    atom_count = parse_atom_count(active_log) if active_log else 0

    # 动力学阶段解析与总步数(配置温度程序末段结束步为总步数兜底,按项目生效)
    sched = _effective_schedule(prod_dir)
    sched_total = sched[-1][1] if sched else TOTAL_STEPS
    kinetic = current_kinetic_stage(prod_dir, latest.get('step', 0))
    stage_total = sum(n for _, n in parse_kinetic_stages(prod_dir))
    total_steps = stage_total if stage_total > 0 else sched_total
    # 领养案(无 Stage 注释/无温度程序, 如 equil): 以编排器 task.total_steps 为准,
    # 否则全局 SCHEDULE 兜底(1350000)会把进度算错(2026-09-19 equil 实测)
    _task_total = state.get('task', {}).get('total_steps')
    if stage_total <= 0 and _task_total and prod_dir == state.get('task', {}).get('prod_dir'):
        total_steps = _task_total

    # Completion override: ignore stale/overwritten log when finished
    if phase in ('DONE', 'FAILED'):
        step = max(sched_total, latest.get('step', 0) or 0)  # FIX-PS: 续跑目录的 sched_total 可能只是当段 run 值, 真步数取日志尾
        progress_pct = 100.0
        remaining_sec = 0
        alive = False
        pids = []
        speed = 0
        # Use last known monitor values if log is stale/overwritten
        temp = monitor.get('temp', latest.get('temp', 0.0))
        density = monitor.get('density', latest.get('density', 0.0))
    else:
        step = latest.get('step', 0)
        temp = latest.get('temp', 0.0)
        density = latest.get('density', 0.0)
        pids = get_lmp_pids(prod_dir)
        alive, pids = _liveness_step_fallback(prod_dir, step, pids)

        speed = 0
        remaining_sec = 0
        if len(rows) >= 2 and active_log:
            dt = rows[-1]['step'] - rows[0]['step']
            try:
                stat = os.stat(active_log)
                age = time.time() - stat.st_mtime
                speed = dt / max(age, 1) if age > 0 else 0
            except Exception:
                speed = 0
        if speed > 0 and step < total_steps:
            remaining_sec = (total_steps - step) / speed
        progress_pct = round(step / total_steps * 100, 1)
    
    # 物种分析
    species_file = state.get('task', {}).get('species_file', '')
    species_path = os.path.join(prod_dir, species_file) if species_file else None
    species_data = parse_species(species_path)

    # Agent 全链路状态
    agents = state.get('agents', {})
    agent_states = {}
    for name, data in agents.items():
        agent_states[name] = {
            'success': data.get('success', 'N/A') if isinstance(data, dict) else 'N/A',
            'status': data.get('status', 'N/A') if isinstance(data, dict) else 'N/A',
            'step': data.get('step', 'N/A') if isinstance(data, dict) else 'N/A',
            'error': data.get('error', '') if isinstance(data, dict) else '',
        }
    
    # 观测日志流（最近 monitor 的 anomalies）
    monitor_log = []
    for a in monitor.get('anomalies', []):
        monitor_log.append({
            'time': monitor.get('timestamp', ''),
            'type': a.get('type', ''),
            'severity': a.get('severity', ''),
            'message': a.get('message', '')
        })
    
    # 策略库状态
    db = {}
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE) as f:
                db = json.load(f)
        except Exception:
            pass
    
    # Token usage billing
    token_usage = {}
    token_path = os.path.join(BASE, '.token_usage.json')
    if os.path.exists(token_path):
        try:
            with open(token_path, 'r') as f:
                token_usage = json.load(f)
        except Exception:
            pass

    # Routine / Pipeline status
    routine_data = {}
    routine_path = os.path.join(BASE, '.routine_state.json')
    if os.path.exists(routine_path):
        try:
            with open(routine_path, 'r') as f:
                routine_data = json.load(f)
        except Exception:
            pass

    # Task Queue (upcoming tasks)
    task_queue = []
    queue_path = os.path.join(BASE, '.task_queue.json')
    if os.path.exists(queue_path):
        try:
            with open(queue_path, 'r') as f:
                q = json.load(f)
                task_queue = q.get('queue', [])
        except Exception:
            pass

    # Orchestrator & Daemon health
    orch_pid = get_orchestrator_pid()
    last_tick = state.get('last_tick')
    orch_healthy = False
    if last_tick:
        try:
            from datetime import datetime
            # Python 3.6 compatible parse (no fromisoformat)
            if '.' in last_tick:
                dt = datetime.strptime(last_tick, "%Y-%m-%dT%H:%M:%S.%f")
            else:
                dt = datetime.strptime(last_tick, "%Y-%m-%dT%H:%M:%S")
            orch_healthy = (datetime.now() - dt).total_seconds() < 120
        except Exception:
            pass
    daemon_pid = state.get('_monitor_daemon_pid')
    daemon_health = get_monitor_daemon_health(daemon_pid, prod_dir)
    agent_chain = get_agent_chain(state)

    return {
        'step': step,
        'atom_count': atom_count,
        'temp': round(temp, 1),
        'target_temp': round(target_temp(step, sched), 1),
        'density': round(density, 6),
        'etotal': round(latest.get('etotal', 0), 1),
        'pe': round(latest.get('pe', 0), 1),
        'ke': round(latest.get('ke', 0), 1),
        'press': round(latest.get('press', 0), 1),
        'vol': round(latest.get('vol', 0), 1),
        'phase': phase,
        'kinetic_stage': kinetic['stage'],
        'kinetic_stage_desc': kinetic['stage_desc'],
        'kinetic_stage_progress': kinetic['stage_progress'],
        'kinetic_stage_target': kinetic['stage_target'],
        'monitor_subphase': state.get('monitor_subphase', 'OBSERVE'),
        'monitor_subphase_history': state.get('monitor_subphase_history', [])[-20:],
        'monitor_status': monitor.get('status', 'UNKNOWN'),
        'risk_score': monitor.get('risk_score', 0),
        'anomalies_count': len(monitor.get('anomalies', [])),
        'trends': monitor.get('trends', {}),
        'alive': alive,
        'pids_count': len(pids),
        'monitor_daemon_pid': daemon_pid,
        'total_steps': total_steps,
        'progress_pct': progress_pct,
        'speed': round(speed, 1),
        'remaining_sec': int(remaining_sec),
        'char_yield': round(analyze_result.get('char_yield', 0), 2),
        'p_retention': round(analyze_result.get('p_retention', 0), 2),
        'history': [{'step': r['step'], 'temp': r['temp'], 'etotal': r['etotal'],
                     'pe': r['pe'], 'ke': r.get('ke', 0), 'vol': r['vol'], 'press': r['press']} for r in rows],
        'timestamp': time.strftime('%H:%M:%S'),
        'agents': agent_states,
        'monitor_log': monitor_log,
        'recovery_history': state.get('recovery_history', []),
        'strategy_db': db.get('strategies', {}),
        'system_log': parse_orchestrator_log(30),
        'strategy_log': parse_strategy_log(20),
        'task': {
            'case_id': state.get('task', {}).get('case_id', ''),
            'prod_dir': state.get('task', {}).get('prod_dir', ''),
            'data_file': state.get('task', {}).get('data_file', ''),
            'ff_file': state.get('task', {}).get('ff_file', ''),
            'cores': state.get('task', {}).get('cores', 0),
            'species_file': state.get('task', {}).get('species_file', ''),
        },
        'species': species_data,
        'token_usage': token_usage,
        'routine': routine_data,
        'task_queue': task_queue,
        # New agent runtime fields
        'orchestrator_pid': orch_pid,
        'orchestrator_last_tick': last_tick,
        'orchestrator_healthy': orch_healthy,
        'monitor_daemon_healthy': daemon_health.get('healthy', False),
        'monitor_daemon_alive': daemon_health.get('alive', False),
        'monitor_daemon_last_write_sec': daemon_health.get('last_write_sec'),
        'agent_chain': agent_chain,
        'dsh_layer': get_dsh_layer_status(),
    }

# dsh 自动审批开关文件(dsh-md-auto-approve 插件每次审批请求时读它;随 DSH_HOME 外置)
AUTO_APPROVE_FLAG = os.path.join(paths.dsh_home(make=False), 'auto-approve.json')

# 人工闸销项收件箱(2026-09-14): 看板只追加请求行, 编排器 tick 经
# user_gates.sweep_requests 单写者落账——看板无 gates 文件直写权, 审计链不破
GATE_INBOX = os.path.join(paths.data_root(), '..', '..',
                          'agent_deploy_kit_20260630', 'logs', 'user_gate_requests.jsonl')

def _append_gate_resolve(ref, by, note=''):
    line = json.dumps({'op': 'resolve', 'ref': str(ref)[:160],
                       'by': str(by or 'dashboard-user')[:120],
                       'note': str(note)[:200]}, ensure_ascii=False)
    with open(GATE_INBOX, 'a', encoding='utf-8') as f:
        f.write(line + '\n')
    return True


# ---- 红区执行闸门(2026-09-17 三区改造):claude_interface 红区调用在此排队,
# 本看板是唯一人工放行口。批准文件与命令 sha256 全等绑定,请求方原样重发才执行。
EXEC_GATE_DIR = '/var/lib/mdsim/gate_inbox'

# 行为策略·自动蒸馏(2026-09-17 去人工化):周报复核视图数据源。
# revoke 走 evolution/behavior_strategies.py 落账(单写者语义不破);
# 懒导入——演化层异常不得拖死看板。
EVOLUTION_DIR = os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', 'evolution'))

def _bstrat():
    import importlib
    if EVOLUTION_DIR not in sys.path:
        sys.path.insert(0, EVOLUTION_DIR)
    return importlib.import_module('behavior_strategies')

def _list_exec_gate():
    items = []
    try:
        names = sorted(os.listdir(EXEC_GATE_DIR))
    except OSError:
        return items
    for n in names:
        if not n.endswith('.json') or n.endswith('.decision.json'):
            continue
        try:
            with open(os.path.join(EXEC_GATE_DIR, n)) as f:
                req = json.load(f)
            if not isinstance(req, dict):
                continue
        except Exception:
            continue
        gid = n[:-5]
        resolution = None
        for suffix, consumed in (('.decision.json', False),
                                 ('.decision.consumed', True)):
            dp = os.path.join(EXEC_GATE_DIR, gid + suffix)
            if os.path.exists(dp):
                try:
                    with open(dp) as f:
                        d = json.load(f)
                    resolution = {'decision': d.get('decision'),
                                  'ts': d.get('ts'), 'consumed': consumed}
                except Exception:
                    pass
                break
        req['gate_id'] = gid
        req['resolution'] = resolution
        items.append(req)
    items.sort(key=lambda r: r.get('ts', ''), reverse=True)
    return items[:100]

def _resolve_exec_gate(gid, decision):
    if not isinstance(gid, str) or not re.fullmatch(r'[0-9a-f]{16}', gid):
        return False, 'bad gate id (want 16 hex chars)'
    if decision not in ('approve', 'deny'):
        return False, 'decision must be approve|deny'
    req_path = os.path.join(EXEC_GATE_DIR, gid + '.json')
    try:
        with open(req_path) as f:
            req = json.load(f)
    except Exception:
        return False, 'unknown gate id'
    h = req.get('hash')
    if not isinstance(h, str) or not h.startswith(gid):
        return False, 'request hash mismatch'
    if os.path.exists(os.path.join(EXEC_GATE_DIR, gid + '.decision.json')) or \
            os.path.exists(os.path.join(EXEC_GATE_DIR, gid + '.decision.consumed')):
        return False, 'already resolved'
    dec_path = os.path.join(EXEC_GATE_DIR, gid + '.decision.json')
    tmp = dec_path + '.tmp.%d' % os.getpid()
    from datetime import datetime
    with open(tmp, 'w') as f:
        json.dump({'decision': decision, 'hash': h,
                   'ts': datetime.now().isoformat(), 'by': 'board'}, f)
    os.replace(tmp, dec_path)
    return True, 'ok'


# 批准唤醒闭环(2026-09-20): 看板批卡只落 .decision.json,但 dsh 里的 PA 已结束
# 回合在等人工回话——批了没人接,用户还得回会话手喊。落裁决后自动把「已批准,
# 请原样重发」注入最近的 dsh 会话(session.prompt queue),PA 下回合重发即命中
# 哈希执行。唤醒是尽力而为:dsh 不在/无会话/接口变形时静默失败,绝不拖垮看板。
DSH_WEB_URL = os.environ.get('DSH_WEB_URL', 'http://127.0.0.1:3091')
GATE_WAKE_ENABLED = os.environ.get('MD_GATE_WAKE', '1') != '0'


def _dsh_rpc(method, payload, timeout=8):
    body = {'type': 'client-request', 'rpcId': 'gatewake-%d' % int(time.time() * 1000),
            'method': method, 'payload': payload}
    req = _ureq.Request(DSH_WEB_URL + '/api/' + method,
                        data=json.dumps(body).encode('utf-8'), method='POST')
    req.add_header('Content-Type', 'application/json')
    with _ureq.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def _gate_wake(gid, decision):
    """裁决落账后唤醒最近的主会话(非 subagent/非 blank)。后台线程执行。"""
    if not GATE_WAKE_ENABLED or not isinstance(gid, str):
        return

    def run():
        try:
            with open(os.path.join(EXEC_GATE_DIR, gid + '.json')) as f:
                cmd = str(json.load(f).get('cmd') or '')
        except Exception:
            cmd = ''
        try:
            r = _dsh_rpc('session.list', {})
            res = r.get('result') or {}
            items = (res.get('value') or {}).get('items') or res.get('items') or []
            cands = [i for i in items if not i.get('origin') and not i.get('blank')]
            if not cands:
                return
            sid = max(cands, key=lambda i: i.get('updatedAt') or 0)['sessionId']
            if decision == 'approve':
                text = ('[看板自动通知] 红区闸卡 %s 已被用户批准(一次性,绑定命令哈希)。'
                        '请立即原样重发该命令执行,不要改动任何参数:\n%s\n'
                        '(本消息由看板在用户点批准时自动注入)' % (gid, cmd))
            else:
                text = ('[看板自动通知] 红区闸卡 %s 已被用户拒绝。'
                        '不要重发该命令;请换方案或向用户说明。' % gid)
            _dsh_rpc('session.prompt', {'sessionId': sid, 'mode': 'queue',
                                        'content': [{'type': 'text', 'text': text}]})
        except Exception:
            pass

    threading.Thread(target=run, daemon=True).start()


# 守护与自省层状态(2026-09-15): systemd is-active 无特权可查; Meta 报告读
# mdsim-data/memory/meta_health_report.json; 守护日志取末行做"最近动作"证据
GUARD_LOG = '/opt/mdagent/deploy/spool/auto_followup3.log'
META_REPORT = os.path.join(paths.data_root(), 'memory', 'meta_health_report.json')

def _svc_active(name):
    import subprocess
    try:
        r = subprocess.run(['systemctl', 'is-active', name],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           timeout=5)
        return r.stdout.decode().strip()
    except Exception:
        return 'unknown'

def _guard_status():
    out = {'mdorch': _svc_active('mdorch'),
           'md_guard': _svc_active('md-guard'),
           'md_watchdog': _svc_active('md-watchdog')}
    try:
        lines = open(GUARD_LOG, encoding='utf-8', errors='replace')\
            .read().strip().splitlines()
        out['guard_last_line'] = lines[-1][:160] if lines else ''
        out['guard_log_age_sec'] = int(time.time() - os.path.getmtime(GUARD_LOG))
        # 守护只在告警时落行,静默即正常;但看板显示"最后一行"导致历史告警永久挂屏
        # (09-18:r35 09:41 起跑后 guard 安静,09:40 的"需人工"却挂了一整天)
        if out['guard_log_age_sec'] > 1800:
            out['guard_last_line'] = ''
    except OSError:
        out['guard_last_line'] = ''
        out['guard_log_age_sec'] = None
    try:
        m = json.load(open(META_REPORT, encoding='utf-8'))
        out['meta_overall'] = m.get('overall', '?')
        out['budget_tier'] = (m.get('budget') or {}).get('tier', '')
        out['meta_fresh'] = (time.time() - os.path.getmtime(META_REPORT)) < 7200
    except Exception:
        out['meta_overall'] = ''
        out['budget_tier'] = ''
        out['meta_fresh'] = False
    out['cron_meta'] = os.path.exists('/etc/cron.d/md-meta')
    out['cron_distill'] = os.path.exists('/etc/cron.d/md-distill')
    return out



def _read_auto_approve():
    try:
        with open(AUTO_APPROVE_FLAG, encoding='utf-8') as f:
            return json.load(f).get('enabled') is True
    except Exception:
        return False

def _write_auto_approve(enabled):
    with open(AUTO_APPROVE_FLAG, 'w', encoding='utf-8') as f:
        json.dump({'enabled': bool(enabled)}, f)


def _case_brief(prod_dir):
    """从 prod_dir 提取"这个 case 跑的什么":data 文件、力场文件、原子数、阶段/步数、最新进度。
    全部读失败静默降级,只返回能拿到的字段。"""
    brief = {}
    try:
        stages = parse_kinetic_stages(prod_dir)
        if stages:
            brief['stages'] = [name for name, _ in stages]
            brief['total_steps'] = sum(n for _, n in stages)
        inp = None
        try:
            names = set(os.listdir(prod_dir))
        except Exception:
            names = set()
        for name in ('production_slow.in', 'production.in'):
            if name in names:
                inp = os.path.join(prod_dir, name)
                break
        if not inp:
            # 兜底:case 目录输入文件名不统一(in.meltquench / *.in),取最新修改的一个
            cands = [n for n in names
                     if n.startswith('in.') or (n.endswith('.in') and not n.endswith('.in.json'))]
            if cands:
                cands.sort(key=lambda n: os.path.getmtime(os.path.join(prod_dir, n)),
                           reverse=True)
                inp = os.path.join(prod_dir, cands[0])
        if inp:
            brief['input_file'] = os.path.basename(inp)
            with open(inp, errors='ignore') as f:
                text = f.read(65536)
            m = re.search(r'^\s*read_data\s+(\S+)', text, re.M)
            if m:
                brief['data_file'] = m.group(1)
            m = re.search(r'pair_coeff[^\n]*?(\S*(?:ffield\S*|tersoff|reax\S*|\.ff)\S*)',
                          text, re.I)
            if m:
                brief['ff_file'] = m.group(1)
        if 'data_file' not in brief:
            datas = sorted(n for n in names if n.endswith('.data'))
            if datas:
                brief['data_file'] = datas[0] + ('(+%d)' % (len(datas) - 1) if len(datas) > 1 else '')
        for lc in ('production.stdout', 'production_slow.stdout', 'log.lammps',
                   'log_production_slow.lammps', 'production.log'):
            lp = os.path.join(prod_dir, lc)
            if os.path.exists(lp) and os.path.getsize(lp) > 0:
                brief['log_file'] = lc
                n = parse_atom_count(lp)
                if n:
                    brief['atoms'] = n
                rows = parse_log_tail(lp, 3)
                if rows:
                    brief['step'] = rows[-1].get('step', 0)
                    brief['temp'] = round(rows[-1].get('temp', 0), 1)
                break
    except Exception:
        pass
    return brief


def build_graph():
    """知识关系图:记忆层(atoms/scenarios/SOP)+演化层(提案/LEDGER)+kit 策略学习
    合并为 {nodes, edges}。节点带 ts 供前端按时间回放进化过程;
    2026-09-19 进化语义接入:atoms 去重并带 level(L1 摩擦/L2 经验)/distilled(融合核)/
    dead_ts(superseded_at=被吸收时刻);融合边(旧原子→蒸馏核)+ 各边出生时间戳;
    L3 回放考场(replay_scores.json)裁决节点。缺文件/坏行一律降级为空,不打穿看板。"""
    mdir = paths.memory_dir(make=False)
    nodes, edges = [], []

    # L1=摩擦观测原子(observe 采集面),L2=其余经验原子;distilled-*=蒸馏融合核(L3)
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
                # 同 id 多行(重复 supersede 记账)以后行为准,只留一个节点
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

    # 融合边:旧原子 --融合(被吸收)--> 蒸馏核;出生=superseded_at
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

    # kit 策略学习:策略节点(大小=uses,色=success_rate)+ 结账历史 + 演化压制
    try:
        if os.path.exists(DB_FILE):
            with open(DB_FILE, encoding='utf-8') as f:
                db = json.load(f)
        else:
            # 无 db 文件=全新大脑:kit load_db 语义是默认六策略在役,
            # 图谱应与之一致(否则新机首次部署图上无策略节点,笔记连边断)
            db = _kit_default_db()
        if db:
            for sid, s in (db.get('strategies') or {}).items():
                if not isinstance(s, dict):
                    continue
                nid = 'strat:' + str(sid)
                nodes.append({'id': nid, 'type': 'strategy',
                              'label': '%s · %s' % (sid, s.get('name', '')),
                              'sublabel': '胜率 %.2f · 用 %d 次' % (s.get('success_rate', 0.5),
                                                                  s.get('uses', 0)),
                              'ts': None,
                              'detail': {'success_rate': s.get('success_rate', 0.5),
                                         'uses': s.get('uses', 0),
                                         'root_causes': s.get('root_causes', [])}})
            for i, h in enumerate(db.get('history') or []):
                if not isinstance(h, dict):
                    continue
                sid = str(h.get('strategy', ''))
                nid = 'att:%d' % i
                nodes.append({'id': nid, 'type': 'attempt',
                              'label': str(h.get('outcome', 'pending')),
                              'sublabel': '%s @ %s' % (h.get('root_cause', ''),
                                                       h.get('case_id', '')),
                              'ts': h.get('ts'),
                              'result': str(h.get('outcome', 'pending')),
                              'detail': dict(h)})
                if 'strat:' + sid in {n['id'] for n in nodes}:
                    edges.append({'from': nid, 'to': 'strat:' + sid,
                                  'label': str(h.get('root_cause', '')),
                                  'ts': h.get('ts')})
            for key, adj in (db.get('strategy_adjustments') or {}).items():
                sid = str(key).split(':', 1)[0]
                pen = adj.get('penalty', 0) if isinstance(adj, dict) else adj
                nid = 'adj:' + str(key)
                nodes.append({'id': nid, 'type': 'adjustment',
                              'label': '压制 %.2f' % (pen if isinstance(pen, (int, float)) else 0),
                              'sublabel': str(key), 'ts': None,
                              'detail': {'key': str(key),
                                         'penalty': pen}})
                edges.append({'from': nid, 'to': 'strat:' + sid, 'label': '演化压制'})
                # 压制提案 ↔ 压制项:slug 以 strategy-weight-<sid> 开头的提案
                for n in nodes:
                    if n['type'] == 'proposal' and n['label'].startswith('strategy-weight-' + sid):
                        edges.append({'from': n['id'], 'to': nid, 'label': '生效'})
    except Exception:
        pass

    # RESEARCH 研判笔记(CORAL notes 模式,2026-09-02):首行 <!-- JSON --> 机读头
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

    # L3 蒸馏层(2026-09-18 图谱接入):知识名片 + 行为策略——
    # 图谱上要能看到"蒸馏过程":atom/scenario →(蒸馏)→ card →(生效)→ bstrat
    card_ids = set()
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
                card_ids.add(cid)
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
                # 蒸馏来源边:卡片 ← 其组成原子(最多 6 条,防边爆炸)
                cts = c.get('generated_ts') or c.get('last_ts')
                for aid in dict.fromkeys(str(x) for x in (c.get('atom_ids') or [])[:6]):
                    if aid in atoms_by_id:
                        edges.append({'from': atoms_by_id[aid], 'to': nid, 'label': '蒸馏',
                                      'ts': cts})
                # 卡片 kind ≈ 场景签名:命中即连"升华"边
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
            # 生效来源边:evidence_refs 里的提案目录 → prop 节点
            for ref in bs.get('evidence_refs') or []:
                mslug = re.search(r'proposals/(?:\d{4}-\d{2}-\d{2}-)?([^/\s]+)', str(ref))
                if mslug and 'prop:' + mslug.group(1) in {x['id'] for x in nodes}:
                    edges.append({'from': 'prop:' + mslug.group(1), 'to': 'bstrat:' + sid,
                                  'label': '蒸馏生效', 'ts': bs.get('created_ts')})
    except Exception:
        pass

    # case 作业节点:registry 登记 + 当前 state 任务;读 prod_dir 补齐"跑的什么"
    # (data 文件 / 力场 / 原子数 / 阶段步数 / 最新进度)
    case_ids = set()

    def _add_case(case_id, project='', prod_dir='', location='local', ts=None, extra=None):
        if not case_id or case_id in case_ids:
            return
        case_ids.add(case_id)
        detail = {'project': project, 'prod_dir': prod_dir, 'location': location}
        if extra:
            detail.update(extra)
        sub = project or prod_dir
        if prod_dir and os.path.isdir(prod_dir):
            brief = _case_brief(prod_dir)
            detail.update(brief)
            bits = [b for b in (project, brief.get('data_file', ''),
                                brief.get('ff_file', ''),
                                '%s 原子' % brief['atoms'] if brief.get('atoms') else '')
                    if b]
            if brief.get('stages'):
                bits.append('%d 阶段/%s 步' % (len(brief['stages']),
                                               brief.get('total_steps', '?')))
            if brief.get('step'):
                bits.append('跑到 %s' % brief['step'])
            sub = ' · '.join(str(b) for b in bits) or prod_dir
        nodes.append({'id': 'case:' + case_id, 'type': 'case', 'label': case_id,
                      'sublabel': sub, 'ts': ts, 'detail': detail})

    try:
        for j in registry.load().get('jobs', []):
            if not isinstance(j, dict):
                continue
            loc = j.get('location') or {}
            _add_case(str(j.get('case_id', '')), str(j.get('project', '')),
                      str(j.get('prod_dir', '')), str(loc.get('type', 'local')),
                      j.get('registered_at'),
                      {'scheduler': loc.get('cluster', ''), 'jobid': str(loc.get('jobid', '')),
                       'registered_by': str(j.get('registered_by', ''))})
    except Exception:
        pass
    try:
        st = get_state().get('task', {})
        if st.get('case_id'):
            _add_case(str(st['case_id']), '', str(st.get('prod_dir', '')), 'local', None,
                      {'data_file': str(st.get('data_file', '')),
                       'ff_file': str(st.get('ff_file', '')),
                       'cores': st.get('cores', 0)})
    except Exception:
        pass

    # case ↔ 记忆/学习:atom refs、history case_id 命中即连边
    for n in list(nodes):
        if n['type'] == 'atom':
            for cid in case_ids:
                if any(cid in str(r) for r in (n['detail'].get('refs') or [])):
                    edges.append({'from': n['id'], 'to': 'case:' + cid, 'label': '观测自',
                                  'ts': n.get('ts')})
        elif n['type'] == 'attempt':
            cid = str(n['detail'].get('case_id', ''))
            if cid in case_ids:
                edges.append({'from': n['id'], 'to': 'case:' + cid, 'label': '恢复于',
                              'ts': n.get('ts')})
        elif n['type'] == 'note':
            nts = n.get('ts')
            cid = str(n['detail'].get('case_id', ''))
            if cid in case_ids:
                edges.append({'from': n['id'], 'to': 'case:' + cid, 'label': '研判于',
                              'ts': nts})
            sid = str(n['detail'].get('strategy', ''))
            if sid and 'strat:' + sid in {x['id'] for x in nodes}:
                edges.append({'from': n['id'], 'to': 'strat:' + sid, 'label': '采用',
                              'ts': nts})

    # L3 回放考场(2026-09-19 接入):replay_eval.py 的成绩单 → 考试裁决节点;
    # 候选 (root_cause, strategy) 从 kit 策略节点连"赴考"边,裁决色由 result 承载
    try:
        rp = os.path.join(paths.CODE_ROOT, 'evolution', 'replay_scores.json')
        with open(rp, encoding='utf-8') as f:
            rs = json.load(f)
        node_ids = {n['id'] for n in nodes}
        gen_ts = rs.get('generated_ts')
        for c in rs.get('candidates') or []:
            rc = str(c.get('root_cause', ''))
            sid = str(c.get('strategy', ''))
            if not rc or not sid:
                continue
            nid = 'exam:%s:%s' % (rc, sid)
            seen = c.get('seen') or {}
            hold = c.get('holdout') or {}
            hint = c.get('graduation_hint') or {}
            status = str(c.get('status', ''))
            passed = bool(hint.get('hit_ok') and hint.get('fp_ok'))
            result = 'success' if passed else ('fail' if status == 'scored' else 'pending')
            nodes.append({'id': nid, 'type': 'exam', 'level': 3,
                          'label': '考场 %s·%s' % (rc, sid),
                          'sublabel': '%s · 命中 %s · 误报 %s · 开火 %d 次' % (
                              status if status else 'untested',
                              seen.get('hit_rate') if seen.get('hit_rate') is not None else '-',
                              seen.get('fp_rate') if seen.get('fp_rate') is not None else '-',
                              seen.get('fires') or 0),
                          'ts': gen_ts, 'result': result,
                          'detail': {'root_cause': rc, 'strategy': sid, 'status': status,
                                     'seen': seen, 'holdout': hold,
                                     'graduation_hint': hint,
                                     'history_rows': (rs.get('history') or {}).get('rows'),
                                     'exam_ready': (rs.get('atoms') or {}).get('exam_ready')}})
            if 'strat:' + sid in node_ids:
                edges.append({'from': 'strat:' + sid, 'to': nid, 'label': '赴考',
                              'ts': gen_ts})
    except Exception:
        pass

    # L0 外部语料层(2026-09-20 接入): AI4Science md-error-corpus 落地 external_corpus/,
    # 图上呈现为「语料源 + 12 类别」节点;疼痛类别连到对应根因的考场节点(供弹边)——
    # 外部知识经考场入环的可视化。语料静态,聚合按进程单例缓存(mtime 失效)。
    try:
        ext_dir = os.path.join(paths.data_root(), 'external_corpus')
        cache_fp = os.path.join(ext_dir, '.graph_cache.json')
        agg = None
        src_mt = max((os.path.getmtime(os.path.join(ext_dir, f))
                      for f in ('lammps_answered.jsonl', 'gromacs_answered.jsonl')
                      if os.path.exists(os.path.join(ext_dir, f))), default=0)
        try:
            if os.path.exists(cache_fp) and os.path.getmtime(cache_fp) >= src_mt:
                with open(cache_fp, encoding='utf-8') as f:
                    agg = json.load(f)
        except Exception:
            agg = None
        if agg is None:
            from collections import Counter
            cats, total = Counter(), 0
            for fn in ('lammps_answered.jsonl', 'gromacs_answered.jsonl'):
                fp = os.path.join(ext_dir, fn)
                if not os.path.exists(fp):
                    continue
                with open(fp, encoding='utf-8', errors='replace') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            r = json.loads(line)
                        except ValueError:
                            continue
                        total += 1
                        cats[str(r.get('category') or '其他')] += 1
            agg = {'total': total, 'cats': dict(cats), 'ts': src_mt or time.time()}
            try:
                tmp = cache_fp + '.tmp'
                with open(tmp, 'w', encoding='utf-8') as f:
                    json.dump(agg, f, ensure_ascii=False)
                os.replace(tmp, cache_fp)
            except OSError:
                pass
        if agg.get('total'):
            land_ts = agg.get('ts') or time.time()
            nodes.append({'id': 'corpus:ai4science', 'type': 'corpus',
                          'label': '外部语料 · AI4Science',
                          'sublabel': '%d 条 · %d 类别 · CC BY-NC-SA(unverified)'
                                      % (agg['total'], len(agg['cats'])),
                          'ts': land_ts, 'level': 0,
                          'detail': {'records': agg['total'],
                                     'categories': agg['cats'],
                                     'license': 'CC BY-NC-SA 4.0(代码 MIT)',
                                     'path': 'mdsim-data/external_corpus/',
                                     'tool': 'corpus_lookup.py "错误行" [--engine] [--top]',
                                     'verified': False}})
            # 类别→三大根因映射(疼痛直连): 供弹边指向对应考场节点
            PAIN_MAP = {'体系爆炸': ('general_instability', 'atomic_ejection'),
                        '温控与压控': ('extreme_temp',),
                        '电荷与自由能': ('QEq_instability',)}
            node_ids = {n['id'] for n in nodes}
            for cat, cnt in sorted(agg['cats'].items(), key=lambda x: -x[1]):
                nid = 'extcat:' + cat
                nodes.append({'id': nid, 'type': 'extcat', 'label': cat,
                              'sublabel': '%d 条' % cnt, 'ts': land_ts, 'level': 0,
                              'detail': {'count': cnt, 'corpus': 'ai4science'}})
                edges.append({'from': 'corpus:ai4science', 'to': nid,
                              'label': '收录', 'ts': land_ts})
                for rc in PAIN_MAP.get(cat, ()):
                    for on in list(nodes):
                        if on['type'] == 'exam' and on['id'].startswith('exam:%s:' % rc):
                            edges.append({'from': nid, 'to': on['id'],
                                          'label': '供弹', 'ts': land_ts})
    except Exception:
        pass

    return {'nodes': nodes, 'edges': edges,
            'stats': {'atoms': sum(1 for n in nodes if n['type'] == 'atom'),
                      'scenarios': sum(1 for n in nodes if n['type'] == 'scenario'),
                      'proposals': sum(1 for n in nodes if n['type'] == 'proposal'),
                      'strategies': sum(1 for n in nodes if n['type'] == 'strategy'),
                      'notes': sum(1 for n in nodes if n['type'] == 'note'),
                      'cards': sum(1 for n in nodes if n['type'] == 'card'),
                      'bstrats': sum(1 for n in nodes if n['type'] == 'bstrat'),
                      'cases': sum(1 for n in nodes if n['type'] == 'case'),
                      'exams': sum(1 for n in nodes if n['type'] == 'exam'),
                      'fused': fused_edges,
                      'l1': sum(1 for n in nodes if n.get('level') == 1),
                      'l2': sum(1 for n in nodes if n.get('level') == 2),
                      'l3': sum(1 for n in nodes if n.get('level') == 3)},
            'memory_dir': mdir}


KNOWLEDGE_CARDS_FILE = os.path.join(paths.memory_dir(False), 'knowledge_cards.jsonl')
KNOWLEDGE_REVIEWS_FILE = os.path.join(paths.memory_dir(False), 'knowledge_reviews.json')
USER_GATES_FILE = os.path.join(BASE, '.v3.3_user_gates.json')


def build_knowledge_cards():
    """知识名片(LLM+wiki,2026-09-13): 只读组合 knowledge_cards.jsonl + 人工核状态
    + kit 用户闸 pending(待拍板清单) + LEDGER 账。缺文件降级空,永不抛。"""
    cards = []
    try:
        with open(KNOWLEDGE_CARDS_FILE, encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    c = json.loads(line)
                except Exception:
                    continue
                if isinstance(c, dict) and c.get('id'):
                    cards.append(c)
    except OSError:
        pass

    pending_gates = []
    try:
        g = json.load(open(USER_GATES_FILE, encoding='utf-8', errors='replace'))
        for x in (g.get('gates') or []):
            if x.get('status') == 'pending':
                pending_gates.append({
                    'id': x.get('id', ''), 'kind': x.get('kind', ''),
                    'question': str(x.get('question', ''))[:200],
                    'ref': str(x.get('ref', ''))[:160],
                    'created_ts': x.get('created_ts'),
                    'source': x.get('source', ''),
                })
    except Exception:
        pass

    ledger = []
    try:
        with open(os.path.join(paths.memory_dir(False), 'LEDGER.jsonl'),
                  encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        ledger.append(json.loads(line))
                    except Exception:
                        pass
    except OSError:
        pass

    n_reviewed = sum(1 for c in cards if c.get('reviewed_by'))
    n_distilled = sum(1 for c in cards if c.get('distill'))
    by_cat = {}
    for c in cards:
        by_cat[c.get('category', '其他')] = by_cat.get(c.get('category', '其他'), 0) + 1

    taxonomy = None
    try:
        taxonomy = json.load(open(
            os.path.join(os.path.dirname(KNOWLEDGE_CARDS_FILE),
                         'knowledge_taxonomy.json'),
            encoding='utf-8', errors='replace'))
    except Exception:
        pass
    return {'cards': cards, 'pending_gates': pending_gates, 'ledger': ledger[-20:],
            'taxonomy': taxonomy,
            'stats': {'total': len(cards), 'distilled': n_distilled,
                      'reviewed': n_reviewed, 'pending_gates': len(pending_gates),
                      'by_category': by_cat}}


def _parse_hms(ts):
    """'YYYY-MM-DDTHH:MM:SS' → epoch;不可解析返回 None。"""
    try:
        return time.mktime(time.strptime(str(ts), '%Y-%m-%dT%H:%M:%S'))
    except Exception:
        return None


# 深挖修复(2026-09-15): 重端点 5s TTL 缓存——前端轮询密集而数据秒级不变,
# 每次请求全量重算(graph/evolution/tasks)白烧 CPU 还拖慢响应
_API_CACHE = {}
_API_CACHE_LOCK = threading.Lock()


def _cached(key, builder, ttl=5):
    now = time.time()
    with _API_CACHE_LOCK:
        ent = _API_CACHE.get(key)
        if ent and now - ent[0] < ttl:
            return ent[1]
    data = builder()
    with _API_CACHE_LOCK:
        _API_CACHE[key] = (now, data)
    return data


def build_evolution_metrics():
    """进化程度量化记分卡(2026-09-02)。全部由既有数据算出,不改写入侧:
    kit db history/settled_ts + memory 层 atoms/scenarios/notes/SOP + LEDGER。
    坏文件/缺字段一律降级为 None,前端显示「—」。"""
    now = time.time()
    BUCKETS = 6
    SPAN = 5 * 86400  # 6 桶 × 5 天 = 近 30 天
    cards, trends = [], {}

    def _bucket(t):
        if t is None or t > now + 3600:
            return None
        i = int((now - t) // SPAN)
        return BUCKETS - 1 - i if 0 <= i < BUCKETS else None

    def _trend(points):  # buckets → 保留 None 的序列,前端断线处理
        return points

    # ---------- kit 策略学习 ----------
    db = {}
    try:
        with open(DB_FILE, encoding='utf-8') as f:
            db = json.load(f)
        if not isinstance(db, dict):
            db = {}
    except Exception:
        pass
    history = [h for h in (db.get('history') or []) if isinstance(h, dict)]
    settled = [h for h in history if h.get('outcome') in ('success', 'fail')]

    # 1) 恢复胜率:全部 + 分桶趋势(按结账时刻,无 settled_ts 退 attempt ts)
    wins = sum(1 for h in settled if h['outcome'] == 'success')
    win_rate = round(wins / len(settled), 3) if settled else None
    wr_buckets = [[] for _ in range(BUCKETS)]
    for h in settled:
        b = _bucket(_parse_hms(h.get('settled_ts')) or _parse_hms(h.get('ts')))
        if b is not None:
            wr_buckets[b].append(1 if h['outcome'] == 'success' else 0)
    trends['win_rate'] = _trend([round(sum(x) / len(x), 2) if x else None
                                 for x in wr_buckets])
    cards.append({'key': 'win_rate', 'name': '恢复胜率',
                  'value': '%.0f%%' % (win_rate * 100) if win_rate is not None else '—',
                  'hint': '已结账尝试 %d 条' % len(settled)})

    # 2) 一次修复率:每 case 首条已结账尝试即成功的比例(学习越早选对策略越高)
    by_case = {}
    for h in settled:
        t = _parse_hms(h.get('ts'))
        by_case.setdefault(str(h.get('case_id', '')), []).append((t or 0, h))
    first_fix, case_total = 0, 0
    ff_buckets = [[] for _ in range(BUCKETS)]
    for cid, rows in by_case.items():
        rows.sort(key=lambda x: x[0])
        if not cid:
            continue
        case_total += 1
        ok = rows[0][1]['outcome'] == 'success'
        first_fix += 1 if ok else 0
        b = _bucket(rows[0][0] or None)
        if b is not None:
            ff_buckets[b].append(1 if ok else 0)
    first_fix_rate = round(first_fix / case_total, 3) if case_total else None
    trends['first_fix'] = _trend([round(sum(x) / len(x), 2) if x else None
                                  for x in ff_buckets])
    cards.append({'key': 'first_fix', 'name': '一次修复率',
                  'value': '%.0f%%' % (first_fix_rate * 100) if first_fix_rate is not None else '—',
                  'hint': '覆盖 %d 个 case' % case_total})

    # 3) 同因复发率:fail 后 7 天内同 root_cause 再 fail 的比例(应随学习下降)
    fails = sorted((h for h in settled if h['outcome'] == 'fail'),
                   key=lambda h: str(h.get('ts', '')))
    recurred, fail_total = 0, 0
    for i, h in enumerate(fails):
        t0 = _parse_hms(h.get('ts'))
        if t0 is None:
            continue
        fail_total += 1
        for h2 in fails[i + 1:]:
            t1 = _parse_hms(h2.get('ts'))
            if t1 is None:
                continue
            if t1 - t0 > 7 * 86400:
                break
            if h2.get('root_cause') == h.get('root_cause'):
                recurred += 1
                break
    recurrence = round(recurred / fail_total, 3) if fail_total else None
    cards.append({'key': 'recurrence', 'name': '同因复发率',
                  'value': '%.0f%%' % (recurrence * 100) if recurrence is not None else '—',
                  'hint': 'fail 样本 %d 条 · 越低越好' % fail_total})

    # 4) 恢复耗时:settled_ts - ts 的中位小时数(2026-09-02 起新账才有)
    durations = []
    dur_buckets = [[] for _ in range(BUCKETS)]
    for h in settled:
        t0, t1 = _parse_hms(h.get('ts')), _parse_hms(h.get('settled_ts'))
        if t0 is None or t1 is None or t1 < t0:
            continue
        dur = (t1 - t0) / 3600.0
        durations.append(dur)
        b = _bucket(t1)
        if b is not None:
            dur_buckets[b].append(dur)
    durations.sort()
    med_dur = durations[len(durations) // 2] if durations else None
    trends['recover_hours'] = _trend([
        round(sorted(x)[len(x) // 2], 1) if x else None for x in dur_buckets])
    cards.append({'key': 'recover_hours', 'name': '恢复耗时(中位)',
                  'value': '%.1f h' % med_dur if med_dur is not None else '—',
                  'hint': '含 settled_ts 的账 %d 条 · 越低越好' % len(durations)})

    # 5) 后验置信度 + 偏离先验度
    strategies = db.get('strategies') or {}
    per_strat = {sid: sum(1 for h in settled if h.get('strategy') == sid)
                 for sid in strategies}
    total_settled = len(settled)
    dev = sum(abs(float(s.get('success_rate', 0.5)) - 0.5)
              for s in strategies.values() if isinstance(s, dict))
    mean_dev = round(dev / len(strategies), 3) if strategies else None
    cards.append({'key': 'posterior', 'name': '后验置信度',
                  'value': '%d 样本' % total_settled,
                  'hint': '40 条已结账≈充分训练'})
    cards.append({'key': 'deviation', 'name': '偏离先验度',
                  'value': '%.2f' % mean_dev if mean_dev is not None else '—',
                  'hint': 'Σ|胜率-0.5|/策略数 · 越大学得越多'})

    # ---------- 演化提案(LEDGER)----------
    mdir = paths.memory_dir(make=False)
    led_counts = {}
    try:
        with open(os.path.join(mdir, 'LEDGER.jsonl'), encoding='utf-8',
                  errors='replace') as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                r = str(e.get('result', ''))
                if r:
                    led_counts[r] = led_counts.get(r, 0) + 1
    except Exception:
        pass
    applied = led_counts.get('APPLIED', 0) + led_counts.get('AUTO_APPLIED', 0)
    decided = applied + led_counts.get('REJECTED', 0) + led_counts.get('ROLLED_BACK', 0)
    apply_rate = round(applied / decided, 3) if decided else None
    cards.append({'key': 'proposals', 'name': '提案采纳率',
                  'value': '%.0f%%' % (apply_rate * 100) if apply_rate is not None else '—',
                  'hint': '应用 %d / 裁决 %d · 回滚 %d' % (
                      applied, decided, led_counts.get('ROLLED_BACK', 0))})

    # ---------- 知识规模与增长 ----------
    atoms = []
    try:
        with open(os.path.join(mdir, 'atoms.jsonl'), encoding='utf-8',
                  errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    a = json.loads(line)
                except ValueError:
                    continue
                if isinstance(a, dict):
                    atoms.append(a)
    except Exception:
        pass
    atom_buckets = [0] * BUCKETS
    for a in atoms:
        b = _bucket(a.get('t') if isinstance(a.get('t'), (int, float)) else None)
        if b is not None:
            atom_buckets[b] += 1
    # 累计曲线比增量更直观(知识只增不减)
    acc, cum = 0, []
    for x in atom_buckets:
        acc += x
        cum.append(acc)
    trends['atoms'] = cum
    scenarios = 0
    try:
        scenarios = len(json.load(open(os.path.join(mdir, 'scenarios.json'),
                                       encoding='utf-8')))
    except Exception:
        pass
    notes = 0
    try:
        notes = sum(1 for n in os.listdir(os.path.join(mdir, 'notes'))
                    if n.endswith('.md'))
    except Exception:
        pass
    sop_rules = 0
    try:
        with open(os.path.join(mdir, 'SOP.md'), encoding='utf-8') as f:
            sop_rules = sum(1 for ln in f if ln.startswith('## '))
    except Exception:
        pass
    cards.append({'key': 'knowledge', 'name': '知识规模',
                  'value': '%d atoms' % len(atoms),
                  'hint': '场景 %d · SOP 规则 %d · 笔记 %d' % (scenarios, sop_rules, notes)})

    # ---------- 合成进化指数(各维归一化,等权)----------
    subs = {}
    if win_rate is not None:
        subs['胜率'] = win_rate
    if first_fix_rate is not None:
        subs['一次修复'] = first_fix_rate
    if recurrence is not None:
        subs['低复发'] = 1.0 - recurrence
    subs['训练量'] = min(1.0, total_settled / 40.0)
    if mean_dev is not None:
        subs['先验偏离'] = min(1.0, mean_dev / 0.25)
    if apply_rate is not None:
        subs['演化吞吐'] = apply_rate
    subs['知识覆盖'] = min(1.0, len(atoms) / 50.0)
    index = round(100 * sum(subs.values()) / len(subs), 1) if subs else None
    trends['index'] = None  # 指数是当下截面,不造伪历史

    return {'index': index, 'subscores': subs, 'cards': cards, 'trends': trends,
            'ts': now}


def _sanitize(obj):
    """nan/inf 不是合法 JSON:递归替换为 None,防跑飞的日志数据击穿前端 JSON.parse。"""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    return obj

# flow-agent.html 实时相位联动脚本(服务端注入:archify 重渲染后依然生效)。
# 父页面 postMessage({source:'md-dash',type:'live-phase',phase}) → 对应节点辉光 + 「▶ 当前」徽标
_FLOW_AGENT_LIVE_SCRIPT = """<script>
(function(){
  var MAP={PREFLIGHT:'preflight',LAUNCH:'launch',MONITOR:'monitor',ANALYZE:'analyze',DONE:'done',
    PREEMPTIVE_RECOVER:'preemptive',RESEARCH:'research',RECOVER:'recover',FAILED:'failed'};
  var st=document.createElement('style');
  st.textContent='.live-current{filter:drop-shadow(0 0 10px #22c55e)}'
    +'.live-current rect,.live-current path{stroke:#22c55e!important;stroke-width:3px!important}'
    +'.live-current.live-fail{filter:drop-shadow(0 0 10px #ef4444)}'
    +'.live-current.live-fail rect,.live-current.live-fail path{stroke:#ef4444!important}';
  document.head.appendChild(st);
  var cur=null,badgeEls=[];
  window.addEventListener('message',function(ev){
    var d=ev.data||{};
    if(d.source!=='md-dash'||d.type!=='live-phase')return;
    if(cur){cur.classList.remove('live-current','live-fail');cur=null;}
    badgeEls.forEach(function(e){e.remove();});badgeEls=[];
    var id=MAP[d.phase];if(!id)return;
    var g=document.getElementById('node-'+id);if(!g)return;
    g.classList.add('live-current');if(d.phase==='FAILED')g.classList.add('live-fail');
    cur=g;
    try{
      var svg=g.ownerSVGElement,bb=g.getBBox();
      var color=d.phase==='FAILED'?'#ef4444':'#22c55e';
      var label=d.phase==='FAILED'?'✖ 失败':'▶ 当前';
      var r=document.createElementNS('http://www.w3.org/2000/svg','rect');
      r.setAttribute('x',bb.x+bb.width/2-36);r.setAttribute('y',bb.y-28);
      r.setAttribute('width',72);r.setAttribute('height',20);r.setAttribute('rx',10);
      r.setAttribute('fill',color);
      var t=document.createElementNS('http://www.w3.org/2000/svg','text');
      t.setAttribute('x',bb.x+bb.width/2);t.setAttribute('y',bb.y-14);
      t.setAttribute('text-anchor','middle');
      t.setAttribute('style','font:700 12px sans-serif;fill:#fff');
      t.textContent=label;
      svg.appendChild(r);svg.appendChild(t);badgeEls=[r,t];
    }catch(e){}
  });
  window.addEventListener('load',function(){
    if(window.parent!==window)window.parent.postMessage({source:'md-flow-agent',type:'ready'},'*');
  });
})();
</script>"""


# flow-decision.html:AI 决策环实时子相位联动。优先看子相位(OBSERVE..DECIDE);
# 相位升到 PREEMPTIVE_RECOVER/RESEARCH 时改亮行动泳道对应节点
_FLOW_DECISION_LIVE_SCRIPT = """<script>
(function(){
  var SUB={OBSERVE:'observe',ANALYZE:'analyze',DETECT:'detect',EVALUATE:'evaluate',DECIDE:'decide'};
  var ESC={PREEMPTIVE_RECOVER:'preempt',RESEARCH:'research'};
  var st=document.createElement('style');
  st.textContent='.live-current{filter:drop-shadow(0 0 10px #22c55e)}'
    +'.live-current rect,.live-current path{stroke:#22c55e!important;stroke-width:3px!important}'
    +'.live-current.live-fail{filter:drop-shadow(0 0 10px #ef4444)}'
    +'.live-current.live-fail rect,.live-current.live-fail path{stroke:#ef4444!important}';
  document.head.appendChild(st);
  var cur=null,badgeEls=[];
  window.addEventListener('message',function(ev){
    var d=ev.data||{};
    if(d.source!=='md-dash'||d.type!=='live-phase')return;
    if(cur){cur.classList.remove('live-current','live-fail');cur=null;}
    badgeEls.forEach(function(e){e.remove();});badgeEls=[];
    var id=ESC[d.phase]||SUB[d.subphase];if(!id)return;
    var g=document.getElementById('node-'+id);if(!g)return;
    var esc=!!ESC[d.phase];
    g.classList.add('live-current');if(esc)g.classList.add('live-fail');
    cur=g;
    try{
      var svg=g.ownerSVGElement,bb=g.getBBox();
      var color=esc?'#ef4444':'#22c55e';
      var label=esc?'⚠ 升级':'▶ 当前';
      var r=document.createElementNS('http://www.w3.org/2000/svg','rect');
      r.setAttribute('x',bb.x+bb.width/2-36);r.setAttribute('y',bb.y-28);
      r.setAttribute('width',72);r.setAttribute('height',20);r.setAttribute('rx',10);
      r.setAttribute('fill',color);
      var t=document.createElementNS('http://www.w3.org/2000/svg','text');
      t.setAttribute('x',bb.x+bb.width/2);t.setAttribute('y',bb.y-14);
      t.setAttribute('text-anchor','middle');
      t.setAttribute('style','font:700 12px sans-serif;fill:#fff');
      t.textContent=label;
      svg.appendChild(r);svg.appendChild(t);badgeEls=[r,t];
    }catch(e){}
  });
  window.addEventListener('load',function(){
    if(window.parent!==window)window.parent.postMessage({source:'md-flow-agent',type:'ready'},'*');
  });
})();
</script>"""


# /api/gromacs 全扫 141 run 要 ~4s,页面 3s 一刷;短 TTL 缓存防扫描堆积
_GMX_CACHE = {'ts': 0.0, 'data': None}
_GMX_CACHE_LOCK = threading.Lock()
_GMX_CACHE_TTL = 5.0


def _gromacs_runs_cached():
    now = time.time()
    with _GMX_CACHE_LOCK:
        if _GMX_CACHE['data'] is not None and now - _GMX_CACHE['ts'] < _GMX_CACHE_TTL:
            return _GMX_CACHE['data']
        data = scan_gromacs_runs()
        _GMX_CACHE['ts'] = time.time()
        _GMX_CACHE['data'] = data
        return data


# ===== 云端智能体模式(2026-09-18):benchwork 作为云端 UI =====
# 脑子在 ECS(对话/记忆/进化),本机只是 UI + 本地文件执行器(写需 UI 批准)。
# 与 mdagent-client 走同一 API;token 存 dashboard/cloud_client.json(600),任何端点不回显。
import hashlib, ssl, collections
import urllib.request as _ureq
import urllib.error as _uerr
import http.client as _httpclient
from pathlib import Path as _Path

CLOUD_CFG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cloud_client.json')
CLOUD_DEFAULT_SERVER = 'https://<SERVER-IP>/mdagent'
CLOUD_CERT_SHA256 = ('F1:75:DF:A3:B5:0F:69:7A:BA:88:C8:23:80:5D:44:11:'
                     'E2:2C:AE:83:33:C5:99:47:A2:01:60:AB:63:BA:7C:69')
CLOUD_MAX_FILE = 1024 * 1024


class _CloudPinnedConn(_httpclient.HTTPSConnection):
    """自签证书跳过 CA 链,真正的认证靠证书指纹 pinning(防中间人冒充云端)。"""
    def connect(self):
        super().connect()
        der = self.sock.getpeercert(binary_form=True)
        fp = ':'.join('%02X' % b for b in hashlib.sha256(der).digest())
        if fp != CLOUD_CERT_SHA256:
            raise ssl.SSLError('云端证书指纹不匹配(可能中间人): %s…' % fp[:23])


class _CloudPinnedHandler(_ureq.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_CloudPinnedConn, req, context=self._context)


_CLOUD_OPENER = _ureq.build_opener(
    _CloudPinnedHandler(context=ssl._create_unverified_context()))

_cloud_state = {
    'cfg': None,       # {'server','token','root'}
    'thread': None,    # 桥长轮询线程
    'pending': {},     # op_id -> {'path','bytes','preview','event','decision','ts'} 待批准写
    'seen': collections.OrderedDict(),  # op_id 去重(防 requeue 重放)
    'last_poll': 0.0,
    'lock': threading.Lock(),
}


def _cloud_cfg():
    with _cloud_state['lock']:
        if _cloud_state['cfg'] is not None:
            return dict(_cloud_state['cfg'])
    try:
        with open(CLOUD_CFG_FILE, encoding='utf-8') as f:
            cfg = json.load(f)
        if cfg.get('token'):
            with _cloud_state['lock']:
                _cloud_state['cfg'] = cfg
            return cfg
    except Exception:
        pass
    return None


def _cloud_save_cfg(server, token, root):
    cfg = {'server': (server or CLOUD_DEFAULT_SERVER).strip().rstrip('/'),
           'token': token.strip(), 'root': (root or '').strip() or os.getcwd()}
    fd = os.open(CLOUD_CFG_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    with _cloud_state['lock']:
        _cloud_state['cfg'] = cfg
    _cloud_start_bridge()
    return cfg


def _cloud_http(method, path, body=None, timeout=70):
    cfg = _cloud_cfg()
    if not cfg:
        raise RuntimeError('云端未配置(先在页面填 server/token)')
    data = json.dumps(body).encode() if body is not None else None
    req = _ureq.Request(cfg['server'] + path, data=data, method=method)
    req.add_header('Content-Type', 'application/json')
    req.add_header('Authorization', 'Bearer ' + cfg['token'])
    with _CLOUD_OPENER.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def _cloud_jail(rel):
    root = _Path(_cloud_cfg()['root']).resolve()
    p = (root / rel).resolve()
    try:
        p.relative_to(root)
    except ValueError:
        raise PermissionError('路径越出开放目录: %s' % rel)
    return p


def _cloud_do_op(op):
    """执行一个本地文件 op;write 进 pending 等 UI 批准(≤100s)。"""
    oid, name = op.get('id'), op.get('op')
    args = op.get('args') or {}
    with _cloud_state['lock']:
        if oid in _cloud_state['seen']:
            return {'op_id': oid, 'ok': False, 'error': '重复 op,已忽略(防重放)'}
        _cloud_state['seen'][oid] = None
        while len(_cloud_state['seen']) > 500:
            _cloud_state['seen'].popitem(last=False)
    try:
        if name == 'list':
            d = _cloud_jail(args.get('path') or '.')
            if not d.is_dir():
                raise FileNotFoundError('目录不存在: %s' % args.get('path'))
            data = {'entries': sorted('%s%s' % (p.name, '/' if p.is_dir() else '')
                                      for p in d.iterdir())[:500]}
        elif name == 'read':
            p = _cloud_jail(args.get('path') or '')
            if not p.is_file():
                raise FileNotFoundError('文件不存在: %s' % args.get('path'))
            fd = os.open(str(p), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
            with os.fdopen(fd, 'rb') as f:
                raw = f.read(CLOUD_MAX_FILE + 1)  # 流式截断,不信 st_size
            if len(raw) > CLOUD_MAX_FILE:
                raise ValueError('文件超过 1MB,不支持')
            data = {'path': str(p), 'content': raw.decode('utf-8', errors='replace')}
        elif name == 'mkdir':
            p = _cloud_jail(args.get('path') or '')
            p.mkdir(parents=True, exist_ok=True)
            data = {'created': str(p)}
        elif name == 'write':
            data = _cloud_do_write(oid, args)
        else:
            return {'op_id': oid, 'ok': False, 'error': '未知操作 ' + str(name)}
        return {'op_id': oid, 'ok': True, 'data': data}
    except Exception as ex:
        return {'op_id': oid, 'ok': False, 'error': str(ex)}


def _cloud_do_write(oid, args):
    content = str(args.get('content', ''))
    if len(content.encode('utf-8')) > CLOUD_MAX_FILE:
        raise ValueError('内容超过 1MB,不支持')
    p = _cloud_jail(args.get('path') or '')
    req = {'path': str(p), 'bytes': len(content.encode('utf-8')),
           'preview': content[:200] + ('……' if len(content) > 200 else ''),
           'event': threading.Event(), 'decision': None, 'ts': time.time()}
    with _cloud_state['lock']:
        _cloud_state['pending'][oid] = req
    try:
        req['event'].wait(100)  # 须 < 云端 CALL_TIMEOUT 110s
        if req['decision'] != 'approve':
            raise PermissionError('用户拒绝了这次写入(或 100s 内未批准)')
        p.parent.mkdir(parents=True, exist_ok=True)
        # 先不带 TRUNC 打开,回验通过再 truncate
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT, 0o644)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.truncate(0)
            f.write(content)
        return {'written': str(p), 'bytes': req['bytes']}
    finally:
        with _cloud_state['lock']:
            _cloud_state['pending'].pop(oid, None)


def _cloud_bridge_loop():
    while True:
        if not _cloud_cfg():
            time.sleep(5)
            continue
        try:
            r = _cloud_http('GET', '/v1/bridge/poll?wait=50', timeout=60)
            _cloud_state['last_poll'] = time.time()
        except Exception:
            time.sleep(3)
            continue
        op = r.get('op')
        if not op:
            continue
        result = _cloud_do_op(op)
        try:
            _cloud_http('POST', '/v1/bridge/result', result, timeout=15)
        except Exception:
            pass


def _cloud_start_bridge():
    with _cloud_state['lock']:
        t = _cloud_state['thread']
        if t and t.is_alive():
            return
        t = threading.Thread(target=_cloud_bridge_loop, daemon=True)
        _cloud_state['thread'] = t
        t.start()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # 2026-09-13 临诊:来客登记(路径+UA)落 /tmp/web_monitor_access.log,
        # 排查"用户打不开但本机正常"类通路问题;稳定后可撤。
        # F3(2026-09-19): 只记 path 剥 query——self.path 原文会把首访
        # ?token=<令牌> 写进全局可读日志(探测实证千余行泄漏);>50MB 单档轮转;
        # 新建文件 0600(历史文件已脱敏+收紧)。
        try:
            _log = "/tmp/web_monitor_access.log"
            try:
                if os.path.getsize(_log) > 50 * 1024 * 1024:
                    os.replace(_log, _log + ".1")
            except OSError:
                pass
            _fd = os.open(_log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(_fd, "a", encoding="utf-8") as _lf:
                _lf.write("%s %s %s | UA=%s\n" % (
                    __import__("time").strftime("%m-%d %H:%M:%S"),
                    self.command, urlparse(self.path).path,
                    (self.headers.get("User-Agent") or "?")[:120]
                    if self.headers else "?"))
        except Exception:
            pass

    def _json_response(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        # S4: no wildcard CORS — dashboard is same-origin; internal state stays
        # unreadable cross-origin.
        self.end_headers()
        self.wfile.write(json.dumps(_sanitize(data), ensure_ascii=False).encode('utf-8'))

    def _auto_approve_response(self, data, status=200):
        # 例外:本机开关,允许 file:// 打开的 workbench 跨域读写
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
        self.wfile.write(json.dumps(_sanitize(data), ensure_ascii=False).encode('utf-8'))

    def do_OPTIONS(self):
        if not self._auth_gate():
            return
        if urlparse(self.path).path == '/api/auto-approve':
            self._auto_approve_response({})
        else:
            self.send_response(404); self.end_headers()

    def _origin_allowed(self):
        """drive-by 防线:ACAO:* 是为 file:// workbench 开的,不能顺便让任意网页跨域
        翻转自动审批开关。放行:无 Origin(curl/同源旧浏览器)、null(file://)、
        或与请求 Host 同源(看板自身页面)。DNS 重绑定因 Host 不符同样被挡。"""
        origin = self.headers.get('Origin')
        if not origin or origin == 'null':
            return True
        return urlparse(origin).netloc == self.headers.get('Host', '')

    def _auth_gate(self):
        """外网暴露前置门禁(2026-09-15, ECS 反代方案): 设了 MD_BOARD_TOKEN 才启用——
        不设则保持本机零配置用法。三种合法凭证: Cookie(浏览器一次种入) /
        Authorization: Bearer(API 脚本) / ?token=(首访或 curl)。
        页面请求带对 token → 302 种 Cookie 跳无参 URL(地址栏不留令牌, 后续 XHR 自动带);
        不设 localhost 豁免——frp 反代的外部流量在本机看也是 127.0.0.1。"""
        tok = (os.environ.get('MD_BOARD_TOKEN') or '').strip()
        if not tok:
            return True
        parsed = urlparse(self.path)
        cookie = self.headers.get('Cookie') or ''
        if ('md_board_token=' + tok) in cookie:
            return True
        if (self.headers.get('Authorization') or '') == 'Bearer ' + tok:
            return True
        qs = parse_qs(parsed.query)
        if qs.get('token', [''])[0] == tok:
            if parsed.path.startswith('/api/'):
                return True
            self.send_response(302)
            self.send_header('Set-Cookie',
                             'md_board_token=%s; Path=/; HttpOnly; SameSite=Lax' % tok)
            self.send_header('Location', parsed.path or '/')
            self.end_headers()
            return False
        if parsed.path.startswith('/api/'):
            self._json_response({'error': 'unauthorized: 需要 token'}, 401)
        else:
            body = ('<!doctype html><meta charset="utf-8"><title>需要访问令牌</title>'
                    '<body style="font-family:sans-serif;max-width:480px;margin:80px auto">'
                    '<h3>MD 看板 · 需要访问令牌</h3>'
                    '<p>在 URL 后加 <code>?token=你的令牌</code> 访问一次即可'
                    '(浏览器种 Cookie 记住, 之后不用再带)。</p></body>').encode('utf-8')
            self.send_response(401)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(body)
        return False

    def do_POST(self):
        if not self._auth_gate():
            return
        parsed = urlparse(self.path)
        if parsed.path == '/api/auto-approve':
            if not self._origin_allowed():
                self._auto_approve_response({'error': 'origin not allowed'}, 403)
                return
            try:
                n = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(n) or b'{}')
                enabled = body.get('enabled') is True
            except Exception:
                self._auto_approve_response({'error': 'bad body, want {"enabled": true|false}'}, 400)
                return
            _write_auto_approve(enabled)
            self._auto_approve_response({'enabled': enabled})
            return
        if parsed.path == '/api/gate_resolve':
            if not self._origin_allowed():
                self._json_response({'error': 'origin not allowed'}, 403)
                return
            try:
                n = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(n) or b'{}')
                ref = str(body.get('ref') or '').strip()
                if not ref or len(ref) > 160:
                    raise ValueError('ref required (1-160 chars)')
                _append_gate_resolve(ref, body.get('by'), body.get('note'))
            except Exception as e:
                self._json_response({'error': 'bad request: %s' % e}, 400)
                return
            self._json_response({'ok': True, 'queued': ref,
                                 'note': '已入收件箱, 编排器下个 tick 落账(≤30s)'})
            return
        if parsed.path == '/api/exec_gate_resolve':
            if not self._origin_allowed():
                self._json_response({'error': 'origin not allowed'}, 403)
                return
            try:
                n = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(n) or b'{}')
                ok, msg = _resolve_exec_gate(body.get('id'), body.get('decision'))
            except Exception as e:
                self._json_response({'error': 'bad request: %s' % e}, 400)
                return
            if not ok:
                self._json_response({'error': msg}, 400)
                return
            _gate_wake(body.get('id'), body.get('decision'))
            self._json_response({'ok': True, 'id': body.get('id'),
                                 'decision': body.get('decision'),
                                 'note': '已裁决并自动通知会话重发(批准一次性)'})
            return
        if parsed.path == '/api/behavior_strategy_revoke':
            if not self._origin_allowed():
                self._json_response({'error': 'origin not allowed'}, 403)
                return
            try:
                n = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(n) or b'{}')
                ok, msg = _bstrat().revoke(body.get('sid'), by='dashboard-user')
            except Exception as e:
                self._json_response({'error': 'bad request: %s' % e}, 400)
                return
            if not ok:
                self._json_response({'error': msg}, 400)
                return
            self._json_response({'ok': True, 'sid': body.get('sid'),
                                 'note': '已翻案:策略撤下,该信号类永久回到人工闸'})
            return
        if parsed.path == '/api/cloud/config':
            if not self._origin_allowed():
                self._json_response({'error': 'origin not allowed'}, 403)
                return
            try:
                n = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(n) or b'{}')
                token = str(body.get('token') or '').strip()
                old = _cloud_cfg()
                if not token and old:
                    token = old['token']  # 只改 root/server 时不必重填 token
                if not token.startswith('mda_'):
                    raise ValueError('token 缺失或格式不对(mda_ 开头)')
                cfg = _cloud_save_cfg(str(body.get('server') or ''),
                                      token, str(body.get('root') or ''))
            except Exception as e:
                self._json_response({'error': 'bad request: %s' % e}, 400)
                return
            self._json_response({'ok': True, 'server': cfg['server'],
                                 'root': cfg['root'], 'note': 'token 已保存(600),不回显'})
            return
        if parsed.path == '/api/cloud/chat':
            if not self._origin_allowed():
                self._json_response({'error': 'origin not allowed'}, 403)
                return
            try:
                n = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(n) or b'{}')
                project = str(body.get('project') or 'default').strip()[:64]
                message = str(body.get('message') or '').strip()
                if not message or len(message) > 32000:
                    raise ValueError('message 为空或过长(>32K)')
                r = _cloud_http('POST', '/v1/chat',
                                {'project': project, 'message': message}, timeout=20)
            except _uerr.HTTPError as ex:
                self._json_response(json.loads(ex.read().decode('utf-8', 'replace')
                                               or '{"detail":"云端错误"}'),
                                    ex.code)
                return
            except Exception as e:
                self._json_response({'error': str(e)}, 502)
                return
            self._json_response(r, 202)
            return
        if parsed.path == '/api/cloud/write_resolve':
            if not self._origin_allowed():
                self._json_response({'error': 'origin not allowed'}, 403)
                return
            try:
                n = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(n) or b'{}')
                oid = str(body.get('op_id') or '')
                decision = 'approve' if body.get('decision') == 'approve' else 'deny'
                with _cloud_state['lock']:
                    req = _cloud_state['pending'].get(oid)
                if not req:
                    self._json_response({'error': '无此待批项(可能已超时)'}, 404)
                    return
                req['decision'] = decision
                req['event'].set()
            except Exception as e:
                self._json_response({'error': 'bad request: %s' % e}, 400)
                return
            self._json_response({'ok': True, 'op_id': oid, 'decision': decision})
            return
        self._json_response({'error': 'not found'}, 404)

    def do_GET(self):
        if not self._auth_gate():
            return
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == '/api/auto-approve':
            self._auto_approve_response({'enabled': _read_auto_approve()})
            return

        if path == '/api/exec_gate':
            self._json_response({'items': _list_exec_gate(),
                                 'pending': sum(1 for i in _list_exec_gate()
                                                if not i.get('resolution'))})
            return

        # 重发免重复弹窗(2026-09-20): dsh-md-ops-gate 在弹窗前查这里——
        # 该 cmd 若有人工批准且未消费的闸卡,放行去 kit 闸(kit 侧哈希绑定才是真闸)
        if path == '/api/exec_gate_lookup':
            cmd = (qs.get('cmd', [''])[0] or '')[:2000]
            approved = False
            try:
                for n in sorted(os.listdir(EXEC_GATE_DIR)):
                    if not n.endswith('.json') or n.endswith('.decision.json'):
                        continue
                    try:
                        with open(os.path.join(EXEC_GATE_DIR, n)) as f:
                            req = json.load(f)
                    except Exception:
                        continue
                    if req.get('cmd') != cmd:
                        continue
                    gid = n[:-5]
                    dp = os.path.join(EXEC_GATE_DIR, gid + '.decision.json')
                    cp = os.path.join(EXEC_GATE_DIR, gid + '.decision.consumed')
                    if os.path.exists(dp) and not os.path.exists(cp):
                        try:
                            with open(dp) as f:
                                approved = json.load(f).get('decision') == 'approve'
                        except Exception:
                            approved = False
                    break
            except OSError:
                pass
            self._json_response({'approved_unconsumed': approved})
            return

        if path == '/api/behavior_strategies':
            # 周报复核视图:自动蒸馏的行为策略全量(active/reverted/revoked)
            try:
                self._json_response(_bstrat().list_all())
            except Exception as e:
                self._json_response({'error': 'bstrat unavailable: %s' % e}, 500)
            return

        if path == '/api/task_stages':
            # 证据门任务阶段卡(2026-10-13 P2):任务级状态机一屏聚合,只读
            def _ts():
                sys.path.insert(0, EVOLUTION_DIR)
                import stage_state
                return {'tasks': stage_state.list_task_summaries(),
                        'generated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ',
                                                      time.gmtime())}
            try:
                self._json_response(_cached('task_stages', _ts, ttl=5))
            except Exception as e:
                self._json_response({'error': 'task_stages unavailable: %s' % e}, 500)
            return

        if path in ('/cloud', '/cloud.html'):
            pg = os.path.join(os.path.dirname(__file__), 'cloud_chat.html')
            if os.path.exists(pg):
                with open(pg, encoding='utf-8') as f:
                    html = _apply_theme(f.read(), self.headers.get('Cookie', ''))
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
                self.end_headers()
                self.wfile.write(html.encode('utf-8'))
            else:
                self._json_response({'error': 'cloud_chat.html not found'}, 404)
            return

        if path == '/api/cloud/config':
            cfg = _cloud_cfg()
            self._json_response({
                'configured': bool(cfg),
                'server': (cfg or {}).get('server', CLOUD_DEFAULT_SERVER),
                'root': (cfg or {}).get('root', os.getcwd()),
                'online': (time.time() - _cloud_state['last_poll']) < 70})
            return

        if path == '/api/cloud/job':
            jid = qs.get('id', [''])[0]
            if not re.fullmatch(r'[0-9a-zA-Z_-]{1,64}', jid):
                self._json_response({'error': 'bad job id'}, 400)
                return
            try:
                self._json_response(_cloud_http('GET', '/v1/jobs/' + jid, timeout=15))
            except _uerr.HTTPError as ex:
                self._json_response(json.loads(ex.read().decode('utf-8', 'replace')
                                               or '{"detail":"云端错误"}'), ex.code)
            except Exception as e:
                self._json_response({'error': str(e)}, 502)
            return

        if path == '/api/cloud/usage':
            try:
                u = _cloud_http('GET', '/v1/usage', timeout=15)
                s = _cloud_http('GET', '/v1/bridge/status', timeout=15)
                u['bridge_online'] = s.get('online')
                self._json_response(u)
            except _uerr.HTTPError as ex:
                self._json_response(json.loads(ex.read().decode('utf-8', 'replace')
                                               or '{"detail":"云端错误"}'), ex.code)
            except Exception as e:
                self._json_response({'error': str(e)}, 502)
            return

        if path == '/api/cloud/pending_writes':
            with _cloud_state['lock']:
                items = [{'op_id': k, 'path': v['path'], 'bytes': v['bytes'],
                          'preview': v['preview'], 'ts': v['ts']}
                         for k, v in _cloud_state['pending'].items()]
            self._json_response({'items': items})
            return

        if path in ('/workbench', '/workbench.html'):
            wb = os.path.join(os.path.dirname(__file__), 'workbench.html')
            if os.path.exists(wb):
                with open(wb, encoding='utf-8') as f:
                    html = _apply_theme(f.read(), self.headers.get('Cookie', ''))
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
                self.end_headers()
                self.wfile.write(html.encode('utf-8'))
            else:
                self._json_response({'error': 'workbench.html not found'}, 404)
            return

        # 静态说明页:/pages/<name>.html(仅限 pages 目录下的 .html,防路径穿越)
        if path.startswith('/pages/'):
            name = path[len('/pages/'):]
            if re.fullmatch(r'[A-Za-z0-9_-]+\.html', name):
                fp = os.path.join(os.path.dirname(__file__), 'pages', name)
                if os.path.exists(fp):
                    with open(fp, encoding='utf-8') as f:
                        html = f.read()
                    # flow-agent/flow-decision 实时相位联动:注入监听脚本(服务端注入,archify 重渲染不丢)
                    if name == 'flow-agent.html' and '</body>' in html:
                        html = html.replace('</body>', _FLOW_AGENT_LIVE_SCRIPT + '</body>', 1)
                    elif name == 'flow-decision.html' and '</body>' in html:
                        html = html.replace('</body>', _FLOW_DECISION_LIVE_SCRIPT + '</body>', 1)
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html; charset=utf-8')
                    self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
                    self.end_headers()
                    self.wfile.write(html.encode('utf-8'))
                else:
                    self._json_response({'error': 'page not found'}, 404)
            else:
                self._json_response({'error': 'invalid page name'}, 400)
            return

        if path == '/api/tasks':
            self._json_response(_cached('tasks', scan_tasks))
            return

        if path == '/api/gromacs':
            self._json_response({'runs': _gromacs_runs_cached()})
            return

        if path == '/api/gromacs_detail':
            qs = parse_qs(urlparse(self.path).query)
            det = gmx_run_detail((qs.get('dir') or [''])[0])
            if det is None:
                self._json_response({'error': 'run 不存在或不在监控根内'}, 404)
            else:
                self._json_response(det)
            return

        if path == '/api/graph':
            self._json_response(_cached('graph', build_graph))
            return

        if path == '/api/knowledge_cards':
            self._json_response(_cached('cards', build_knowledge_cards))
            return

        if path == '/api/evolution_metrics':
            self._json_response(_cached('evolution', build_evolution_metrics))
            return

        if path == '/api/trajectory':
            self._json_response(_cached('traj:%s:%s' % (qs.get('task', [''])[0], qs.get('session', [''])[0]),
                lambda: build_trajectory(
                    task=qs.get('task', [''])[0] or None,
                    session=qs.get('session', [''])[0] or None)))
            return

        if path == '/api/theme':
            self._json_response({'theme': _effective_theme(self.headers.get('Cookie', ''))})
            return

        if path == '/api/guard_status':
            self._json_response(_cached('guard', _guard_status))
            return

        if path == '/api/status':
            task_param = qs.get('task', [''])[0]
            if task_param:
                prod_dir = _resolve_prod_dir(task_param)
                if prod_dir:
                    self._json_response(_cached('status:%s' % task_param,
                                                lambda: get_status(prod_dir=prod_dir), ttl=3))
                else:
                    self._json_response({'error': 'task not found'}, 404)
            else:
                self._json_response(_cached('status', get_status, ttl=3))
            return

        html_name = 'simple.html' if path in ('/simple', '/simple.html') else 'index.html'
        html_path = os.path.join(os.path.dirname(__file__), 'static', html_name)
        if os.path.exists(html_path):
            with open(html_path, encoding='utf-8') as f:
                html = _apply_theme(f.read(), self.headers.get('Cookie', ''))
        else:
            html = "<h1>" + html_name + " not found</h1>"
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))

if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    # S4: bind localhost by default (set WEB_HOST=0.0.0.0 to expose on a LAN).
    host = os.environ.get("WEB_HOST", "127.0.0.1")
    addr = (host, port)
    if _cloud_cfg():
        _cloud_start_bridge()  # 已配置云端:恢复桥长轮询(重启后续传)
    # 多线程:/api/gromacs 全扫要数秒,单线程会让并发的详情/状态请求排队卡死
    httpd = ThreadingHTTPServer(addr, Handler)
    httpd.daemon_threads = True
    print(f"[WEB] Dashboard at http://{host}:{port}/ (set WEB_HOST=0.0.0.0 to expose on LAN)")
    httpd.serve_forever()
