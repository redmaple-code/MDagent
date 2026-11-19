// dsh-md-friction 回归门(假 ctx,不依赖 dsh 运行时):
// ① 轮内计数(工具错误/否决/重试)在 turn/end 组装成一行
// ② aborted 轮记 interrupt ③ 干净轮全零 ④ turn/start 重置计数器
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { apply, name, resolveDataRoot } from '../dsh-plugins/dsh-md-friction/lib/index.js';

function makeCtx() {
    const handlers = {};
    const ctx = { on(event, fn) { handlers[event] = fn; } };
    const session = 'sess-1';
    const ev = (type, data = {}) => handlers['session/event'](session, { type, data });
    return {
        ctx, handlers,
        turnStart: (turn) => ev('turn/start', { turn }),
        turnEnd: (turn, kind = 'completed') => ev('turn/end', { turn, reason: { kind } }),
        toolOk: () => ev('tool/result', { turn: 1, step: 1, message: {} }),
        toolErr: (code) => ev('tool/result', { turn: 1, step: 1, message: {}, error: { name: 'ToolError', code } }),
        approval: (outcome) => ev('approval/decided', { id: 'a1', outcome }),
        retry: () => ev('llm/retry', { turn: 1, attempt: 2 }),
    };
}

function readRows(dir) {
    return readFileSync(join(dir, 'log.jsonl'), 'utf8')
        .trim().split('\n').map((l) => JSON.parse(l));
}

test('插件导出 name/apply/resolveDataRoot', () => {
    assert.equal(name, 'dsh-md-friction');
    assert.equal(typeof apply, 'function');
    assert.equal(typeof resolveDataRoot, 'function');
});

test('轮内摩擦在 turn/end 组装成一行,计数齐全', () => {
    const dir = mkdtempSync(join(tmpdir(), 'friction-'));
    try {
        const h = makeCtx();
        apply(h.ctx, { logPath: join(dir, 'log.jsonl') });
        h.turnStart(1);
        h.toolOk();
        h.toolErr('TIMEOUT');
        h.toolErr('EACCES');
        h.approval('rejected');
        h.approval('allowed-once');  // 放行不计否决
        h.retry();
        h.turnEnd(1);
        const rows = readRows(dir);
        assert.equal(rows.length, 1);
        const r = rows[0];
        assert.equal(r.kind, 'turn-friction');
        assert.equal(r.session, 'sess-1');
        assert.equal(r.turn, 1);
        assert.equal(r.reason, 'completed');
        assert.equal(r.interrupts, 0);
        assert.equal(r.tool_errors, 2);
        assert.equal(r.denials, 1);
        assert.equal(r.retries, 1);
        assert.equal(r.tools, 3);
        assert.deepEqual(r.failed_tools, ['TIMEOUT', 'EACCES']);
        assert.equal(typeof r.t, 'number');
    } finally {
        rmSync(dir, { recursive: true, force: true });
    }
});

test('aborted 轮记 interrupt;cancelled 审批计否决', () => {
    const dir = mkdtempSync(join(tmpdir(), 'friction-'));
    try {
        const h = makeCtx();
        apply(h.ctx, { logPath: join(dir, 'log.jsonl') });
        h.turnStart(2);
        h.approval('cancelled');
        h.turnEnd(2, 'aborted');
        const r = readRows(dir)[0];
        assert.equal(r.reason, 'aborted');
        assert.equal(r.interrupts, 1);
        assert.equal(r.denials, 1);
    } finally {
        rmSync(dir, { recursive: true, force: true });
    }
});

test('turn/start 重置计数器:上一轮的错误不泄漏到下一轮', () => {
    const dir = mkdtempSync(join(tmpdir(), 'friction-'));
    try {
        const h = makeCtx();
        apply(h.ctx, { logPath: join(dir, 'log.jsonl') });
        h.turnStart(1);
        h.toolErr('TIMEOUT');
        h.turnEnd(1);
        h.turnStart(2);
        h.toolOk();
        h.turnEnd(2);
        const rows = readRows(dir);
        assert.equal(rows[0].tool_errors, 1);
        assert.equal(rows[1].tool_errors, 0);
        assert.equal(rows[1].tools, 1);
    } finally {
        rmSync(dir, { recursive: true, force: true });
    }
});

test('缺 turn/start 的 crash-tail turn/end 也成行;failed_tools 上限 5', () => {
    const dir = mkdtempSync(join(tmpdir(), 'friction-'));
    try {
        const h = makeCtx();
        apply(h.ctx, { logPath: join(dir, 'log.jsonl') });
        for (let i = 0; i < 8; i++) h.toolErr('E' + i);
        h.turnEnd(9);
        const r = readRows(dir)[0];
        assert.equal(r.turn, 9);
        assert.equal(r.tool_errors, 8);
        assert.equal(r.failed_tools.length, 5);
    } finally {
        rmSync(dir, { recursive: true, force: true });
    }
});
