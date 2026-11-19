// dsh-md-friction — turn-level friction signal collector for the evolution
// observer (2026-09-09 经验沉淀). Soft failures that today vanish without a
// trace — user aborts, tool errors, approval denials, LLM retries — are
// counted per turn and appended as one JSON row per turn/end to logPath,
// where evolution/observe.py promotes recurring patterns into proposals.
// Same write discipline as dsh-md-flow-gate: append-only, best-effort,
// a logging failure must never affect the main loop.
//
// Signals (all via the session/event firehose, single seam):
//   turn/end reason=aborted        → interrupt (user cancelled the live turn)
//   tool/result with error         → tool error
//   approval/decided rejected|cancelled → denial
//   llm/retry                      → retry
import { appendFileSync, mkdirSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, isAbsolute, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

export const name = 'dsh-md-friction';

// 与 dsh-md-flow-gate 同一定位:插件真实路径上四级 = dsh-agent 父目录
const CODE_PARENT = resolve(dirname(fileURLToPath(import.meta.url)),
    '..', '..', '..', '..');

// 与 mcp/paths.py data_root() 同语义(复制自 flow-gate,保持两插件解析一致):
// 空白视为未设置;~ 展开家目录;相对值锚定代码根父目录而非 cwd
export function resolveDataRoot() {
    let root = (process.env.MDSIM_DATA_ROOT ?? '').trim();
    if (!root) return null;
    if (root === '~' || root.startsWith('~/')) root = homedir() + root.slice(1);
    if (!isAbsolute(root)) root = resolve(CODE_PARENT, root);
    return root;
}

const DEFAULT_LOG = 'events/friction.jsonl';  // 相对数据根(MDSIM_DATA_ROOT)或 cwd
const MAX_FAILED_TOOLS = 5;                    // 每轮最多留证的失败工具错误码数
const DENIAL_OUTCOMES = new Set(['rejected', 'cancelled']);  // dsh-user-approval OUTCOMES

export function apply(ctx, config) {
    // logPath 解析纪律与 flow-gate 一致:绝对值直用;相对值锚 MDSIM_DATA_ROOT,
    // 逃逸(../)回落默认;无 env 退 cwd 并告警一次
    let logPath;
    if (isAbsolute(config?.logPath ?? '')) {
        logPath = config.logPath;
    } else {
        const relLog = config?.logPath ?? DEFAULT_LOG;
        const envRoot = resolveDataRoot();
        const base = envRoot || process.cwd();
        if (!envRoot) {
            console.error('[dsh-md-friction] MDSIM_DATA_ROOT 未设置,friction 日志落 cwd 兜底:',
                base, '(经 run-dsh.sh 启动可消除此警告)');
        }
        const resolved = resolve(base, relLog);
        logPath = resolved.startsWith(resolve(base) + sep)
            ? resolved
            : resolve(base, DEFAULT_LOG);
        if (logPath !== resolved) {
            console.error('[dsh-md-friction] logPath 相对值逃逸基座,已回落默认:', relLog);
        }
    }

    // per-session state: session → 本轮计数器(turn/start 重置)
    const states = new Map();

    const logEvent = (record) => {
        try {
            mkdirSync(dirname(logPath), { recursive: true });
            appendFileSync(logPath, JSON.stringify(record) + '\n');
        } catch {
            // 信号日志失败绝不影响主流程
        }
    };

    const stateOf = (session) => {
        let s = states.get(session);
        if (!s) {
            s = { turn: 0, toolErrors: 0, denials: 0, retries: 0, tools: 0, failedTools: [] };
            states.set(session, s);
        }
        return s;
    };

    // 派发侧首参是 session id 字符串;测试假 ctx 可能传 {id} 对象,两种都接
    const sessionId = (session) =>
        typeof session === 'string' ? session : (session?.id ?? null);

    ctx.on('session/event', (session, event) => {
        const state = stateOf(session);
        if (event.type === 'turn/start') {
            state.turn = event.data?.turn ?? 0;
            state.toolErrors = 0;
            state.denials = 0;
            state.retries = 0;
            state.tools = 0;
            state.failedTools = [];
            return;
        }
        if (event.type === 'tool/result') {
            state.tools++;
            const err = event.data?.error;
            if (err) {
                state.toolErrors++;
                if (state.failedTools.length < MAX_FAILED_TOOLS) {
                    state.failedTools.push(String(err.code || err.name || 'unknown'));
                }
            }
            return;
        }
        if (event.type === 'approval/decided') {
            if (DENIAL_OUTCOMES.has(event.data?.outcome)) state.denials++;
            return;
        }
        if (event.type === 'llm/retry') {
            state.retries++;
            return;
        }
        if (event.type === 'turn/end') {
            const reason = event.data?.reason?.kind ?? 'unknown';
            logEvent({
                t: Date.now(),
                kind: 'turn-friction',
                session: sessionId(session),
                turn: event.data?.turn ?? state.turn,
                reason,
                interrupts: reason === 'aborted' ? 1 : 0,
                tool_errors: state.toolErrors,
                denials: state.denials,
                retries: state.retries,
                tools: state.tools,
                failed_tools: state.failedTools
            });
            states.delete(session); // turn 结束即释放,防 Map 随会话数泄漏
        }
    });
}
