// dsh-goal-sync — GoalBar 自动刷新(2026-09-18)。
// 背景:goal round 续行不持久化(session-start 即 disarm),09-14 起自动轮死透,
// 进度条靠模型手调 update_goal,冻成化石。本插件取而代之:定时 + turn/end 双触发,
// 从看板 API 推导进度条,内容变化才 ctx.goals.edit。goal 变更不注入模型上下文,零 token。
// 纪律:只 edit objective(phase/blockedReason 动不了,edit 语义本身禁止);
// 任何失败(看板挂/会话未加载/CAS 冲突)静默跳过本轮,绝不影响主流程。
export const name = 'dsh-goal-sync';
// cordis 服务访问必须显式 inject(dsh-browser/dsh-files 同此约定)
export const inject = ['agents', 'goals'];

const DEFAULTS = {
    marker: '标定收官',
    statusUrl: 'http://127.0.0.1:8080/api/status',
    intervalMs: 120000,
    minEditIntervalMs: 90000,
    fetchTimeoutMs: 3000,
    suffix: '',
    slots: [],
};

async function fetchStatus(url, timeoutMs) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
        const res = await fetch(url, { signal: ctrl.signal });
        if (!res.ok) return null;
        return await res.json();
    } catch {
        return null;
    } finally {
        clearTimeout(timer);
    }
}

function renderBar(slots, status, suffix) {
    const task = status?.task ?? {};
    const caseId = String(task.case_id ?? '');
    const prodBase = String(task.prod_dir ?? '').replace(/\/+$/, '').split('/').pop()
        .replace(/_r\d+$/, '');
    const caseSlots = slots.map((s, i) => ({ s, i })).filter(({ s }) => s.case);
    const cur = caseSlots.find(({ s }) =>
        (caseId && caseId.includes(s.case)) || (prodBase && prodBase.includes(s.case)));
    if (!cur) return null; // 编排器空闲/case 对不上——本轮不动,防误刷
    const curOrder = caseSlots.indexOf(cur);
    const step = Number(status.step ?? 0);
    const temp = Number(status.temp ?? 0);
    const phase = String(status.phase ?? '');
    const alive = status.alive !== false;

    const parts = slots.map((s) => {
        if (!s.case) return `${s.n}${s.state}${s.name}`;
        const order = caseSlots.findIndex((c) => c.s === s);
        if (order < curOrder) return `${s.n}✅${s.name}`;
        if (order > curOrder) return `${s.n}⬜${s.name}`;
        // 完成判定优先看步数:模拟 DONE 后分析步骤失败会把 phase 打成 FAILED
        // (11-18 实锤:r35 真完成,species.out 改名致 analyze 报 not found → FAILED)
        const finished = phase === 'DONE' || (s.plannedEnd && step >= s.plannedEnd);
        if (finished) return `${s.n}✅${s.name}`;
        const pct = s.plannedEnd ? Math.min(step / s.plannedEnd * 100, 100) : null;
        const detail = [pct != null ? `${pct.toFixed(1)}%` : '',
            temp ? `${Math.round(temp)}K` : ''].filter(Boolean).join(' ');
        const icon = (!alive || phase === 'FAILED' || phase === 'RECOVER') ? '⚠️' : '🔄';
        return `${s.n}${icon}${s.name}${detail ? `(${detail})` : ''}`;
    });
    return parts.join(' ') + (suffix ? ' ' + suffix : '');
}

export function apply(ctx, config) {
    const cfg = { ...DEFAULTS, ...(config ?? {}) };
    if (!cfg.slots.length) return;
    const lastEditAt = new Map(); // agent.id -> ts
    let inFlight = false;

    async function tick() {
        if (inFlight) return;
        inFlight = true;
        try {
            const status = await fetchStatus(cfg.statusUrl, cfg.fetchTimeoutMs);
            if (!status) { if (cfg.debug) console.log('[dsh-goal-sync] tick: board unreachable'); return; }
            const bar = renderBar(cfg.slots, status, cfg.suffix);
            if (!bar) { if (cfg.debug) console.log('[dsh-goal-sync] tick: no case match', status?.task?.case_id); return; }
            let agents = [];
            try { agents = ctx.agents.list(); } catch (e) {
                console.log('[dsh-goal-sync] tick: agents.list failed', e?.message); return;
            }
            if (cfg.debug) console.log(`[dsh-goal-sync] tick: ${agents.length} live agents, bar="${bar.slice(0, 60)}..."`);
            for (const agent of agents) {
                try {
                    const view = ctx.goals.get(agent);
                    if (!view) continue;
                    if (view.phase !== 'active' || !view.objective.includes(cfg.marker)) continue;
                    if (view.objective === bar) continue;
                    const last = lastEditAt.get(agent.id) ?? 0;
                    if (Date.now() - last < cfg.minEditIntervalMs) continue;
                    try {
                        ctx.goals.edit(agent, { id: view.id, revision: view.revision }, { objective: bar });
                    } catch (e) {
                        // CAS 冲突:重取最新 revision 再试一次,仍败则等下轮
                        if (String(e?.code ?? e?.message ?? '').includes('STALE_REVISION')) {
                            const fresh = ctx.goals.get(agent);
                            if (!fresh || fresh.objective === bar) continue;
                            ctx.goals.edit(agent, { id: fresh.id, revision: fresh.revision }, { objective: bar });
                        } else {
                            console.log('[dsh-goal-sync] edit rejected:', e?.message ?? e);
                            continue;
                        }
                    }
                    lastEditAt.set(agent.id, Date.now());
                    console.log(`[dsh-goal-sync] goal ${view.id} -> ${bar}`);
                } catch (e) {
                    console.log('[dsh-goal-sync] agent scan error:', e?.message ?? e);
                }
            }
        } finally {
            inFlight = false;
        }
    }

    const timer = setInterval(tick, cfg.intervalMs);
    if (typeof timer.unref === 'function') timer.unref();
    ctx.on('session/event', (_session, event) => {
        if (event?.type === 'turn/end') tick();
    });
    ctx.on('agent/session-start', () => { tick(); });
    ctx.on('dispose', () => clearInterval(timer));
    console.log(`[dsh-goal-sync] armed (interval ${cfg.intervalMs}ms, marker "${cfg.marker}")`);
}
