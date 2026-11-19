// dsh-md-trace — 全工具账本(2026-09-17 加固)。与 flow-gate/friction 的区别：
// 不过滤、不判断、不参与决策，只做一件事——每个工具调用记一行，落到 agent
// 无法改写的 append-only 文件(chattr +a, root:root, ACL 放行 mdagent 追加)。
// 看板的一致性校验以本文件为准；数据根内的 toolcalls.jsonl 属 agent 可写面，不作数。
// 写纪律与 flow-gate/friction 相同：append-only、best-effort、失败绝不影响主流程。
import { appendFileSync } from 'node:fs';

export const name = 'dsh-md-trace';
const DEFAULT_LOG = '/var/log/mdsim/trace.jsonl';
const MAX_ARG = 600;

function brief(args) {
    try {
        const s = JSON.stringify(args ?? {});
        return s.length > MAX_ARG ? s.slice(0, MAX_ARG) + '…<truncated>' : s;
    } catch {
        return '<unserializable>';
    }
}

function sid(exec) {
    // session 对象是整个事件日志(数百 KB)——只取标识,绝不整体落盘
    const s = exec?.agent?.session;
    return (s && typeof s === 'object') ? (s.id ?? null) : (s ?? null);
}

export function apply(ctx, config) {
    const logPath = config?.logPath || DEFAULT_LOG;
    const write = (row) => {
        try {
            appendFileSync(logPath, JSON.stringify(row) + '\n');
        } catch {
            // 账本写失败绝不影响主流程(与 flow-gate/friction 同纪律)
        }
    };
    write({ t: Date.now(), kind: 'trace-start', pid: process.pid });

    ctx.on('tools/pre-execute', (exec, next) => {
        write({
            t: Date.now(), kind: 'tool-call',
            session: sid(exec),
            tool: exec?.name ?? '(unknown)',
            args: brief(exec?.arguments)
        });
        return next();
    });

    ctx.on('tools/post-execute', (exec, result, next) => {
        let err = null;
        try {
            err = (result && result.isError) ? String(result.error ?? 'error').slice(0, 200) : null;
        } catch {
            err = '<unreadable>';
        }
        write({
            t: Date.now(), kind: 'tool-result',
            session: sid(exec),
            tool: exec?.name ?? '(unknown)',
            error: err
        });
        return next();
    });
}
