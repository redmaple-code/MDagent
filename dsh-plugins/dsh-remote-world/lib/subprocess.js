// dsh-remote-world/lib/subprocess.js — ctx.subprocess 的远端提供者。
// spawn() 按接缝契约必须同步返回句柄：这里先造句柄（pid 未知 = -1），再异步向壳子发 proc.spawn，
// 壳子回 pid 后补上；stdout/stderr 以 proc.out 通知推流，退出以 proc.exit 通知收口。
// 收集模式（SubprocessCollect）的 readFrom(fromByte) 也是同步契约：壳子把原始块推过来，
// 云端句柄本地维护同语义的尾部收集器（整条流字节坐标 / lossy / nextOffset）；spill 文件由壳子
// 写在它的工作区 .mdshell/spill/ 下，spillPath 是壳子路径——与 ctx.fs 同一个世界。
import { PassThrough, Writable } from 'node:stream';
import { SubprocessRuntime } from '@deepseek-ai/dsh-subprocess';
import z from '@deepseek-ai/schemastery';
import { RemoteWorld } from './client.js';
import { b64enc, b64dec } from './protocol.js';

let seq = 0;
const nextId = (prefix) => `${prefix}${++seq}-${Date.now().toString(36)}`;
const isCollect = (m) => typeof m === 'object' && m !== null;

/** 与 dsh-subprocess-local 的 OutputCollector 同语义的尾部窗口（只保留最后 maxBytes 字节）。 */
class TailCollector {
    constructor(maxBytes) {
        this.maxBytes = maxBytes;
        this.chunks = [];
        this.bytes = 0;   // 当前保留的尾部字节数
        this.total = 0;   // 整条流累计字节数
        this.spillPath = undefined;
    }
    push(buf) {
        this.chunks.push(buf);
        this.bytes += buf.length;
        this.total += buf.length;
        while (this.bytes > this.maxBytes) {
            const excess = this.bytes - this.maxBytes;
            const head = this.chunks[0];
            if (head.length <= excess) { this.chunks.shift(); this.bytes -= head.length; }
            else { this.chunks[0] = head.subarray(excess); this.bytes -= excess; }
        }
    }
    readFrom(fromByte) {
        const head = this.total - this.bytes;
        const tail = this.chunks.length === 1 ? this.chunks[0] : Buffer.concat(this.chunks);
        const from = Math.max(0, Number(fromByte) || 0);
        const lossy = from < head;
        const text = lossy ? tail.toString('utf8') : tail.subarray(Math.min(from - head, tail.length)).toString('utf8');
        return { text, nextOffset: this.total, lossy, ...(this.spillPath ? { spillPath: this.spillPath } : {}) };
    }
}

function wireStdio(stdio) {
    const { stdin = 'ignore', stdout = 'pipe', stderr = 'pipe' } = stdio ?? {};
    const out = (m) => (isCollect(m) ? { maxBytes: m.maxBytes, spill: m.spill ? { maxBytes: m.spill.maxBytes } : undefined } : m);
    return {
        stdin: isCollect(stdin) ? { data: stdin.data } : stdin,
        stdout: out(stdout),
        stderr: out(stderr),
    };
}

class RemoteHandle {
    constructor(world, hid, spec) {
        this.world = world;
        this.hid = hid;
        this.spec = spec;
        this._pid = -1;
        const { stdin = 'ignore', stdout = 'pipe', stderr = 'pipe' } = spec.stdio ?? {};

        this.stdin = stdin === 'pipe'
            ? new Writable({
                write: (chunk, _enc, cb) => { this._sendStdin('proc.stdin', { hid, data: b64enc(chunk) }).then(() => cb(), cb); },
                final: (cb) => { this._sendStdin('proc.stdinEnd', { hid }).then(() => cb(), cb); },
            })
            : undefined;
        // Writable callbacks still receive failures; an unused stdin must not
        // turn a normal disconnect into an unhandled EventEmitter error.
        this.stdin?.on('error', () => {});
        this.stdout = stdout === 'pipe' ? new PassThrough() : undefined;
        this.stderr = stderr === 'pipe' ? new PassThrough() : undefined;

        this._collectors = {
            stdout: isCollect(stdout) ? new TailCollector(stdout.maxBytes) : undefined,
            stderr: isCollect(stderr) ? new TailCollector(stderr.maxBytes) : undefined,
        };
        this.collected = {
            ...(this._collectors.stdout ? { stdout: this._collectors.stdout } : {}),
            ...(this._collectors.stderr ? { stderr: this._collectors.stderr } : {}),
        };

        this.done = new Promise((resolve) => { this._resolveDone = resolve; });
        this._stdinReady = new Promise((resolve, reject) => {
            this._resolveStdin = resolve; this._rejectStdin = reject;
        });
        this._stdinReady.catch(() => {});
        world.handleOpened(); // 句柄在途 → 连接 ref，进程不会因"没别的事"提前退出
        this._offs = [
            world.on('proc.out', (p) => {
                if (p.hid !== hid) return;
                const buf = b64dec(p.data);
                const c = this._collectors[p.fd];
                if (c) c.push(buf);
                else (p.fd === 'stdout' ? this.stdout : this.stderr)?.write(buf);
            }),
            world.on('proc.spill', (p) => {
                if (p.hid !== hid) return;
                const c = this._collectors[p.fd];
                if (c) c.spillPath = p.spillPath ?? undefined;
            }),
            world.on('proc.exit', (p) => {
                if (p.hid !== hid) return;
                this._finish({ exitCode: p.exitCode ?? null, signal: p.signal ?? null });
            }),
            // 到节点的连接断了:进程在那边可能还活着,但这边再也收不到它的输出/退出——诚实收口为"挂断",
            // 让 bash 工具立刻拿到结果而不是等到超时;不伪造退出码
            world.on('$close', (info) => {
                const note = Buffer.from(`[mdshell] connection to node lost (${info?.code ?? '?'}); process outcome unknown\n`);
                if (this._collectors.stderr) this._collectors.stderr.push(note); else this.stderr?.write(note);
                this._finish({ exitCode: null, signal: 'SIGHUP' });
            }),
        ];

        this._spawned = (async () => {
            await world.connect();
            if (this._finished || spec.signal?.aborted) throw new Error('mdshell process closed before spawn');
            // Retain the acknowledgement even if cancellation arrives in flight:
            // terminate must follow creation of this specific remote handle.
            const r = await world.request('proc.spawn', { hid, spec: { argv: [...spec.argv], cwd: spec.cwd, env: spec.env, graceMs: spec.graceMs, stdio: wireStdio(spec.stdio) } });
            this._pid = r.pid;
            this._resolveStdin();
            return r;
        })();
        this._spawned.catch((e) => {
            if (this._finished) return;
            // 带错误码:MD_ENGINE_REJECTED / SANDBOX_UNAVAILABLE / FS_PERMISSION_DENIED … 模型与测试都按码判
            const msg = Buffer.from(`[mdshell] spawn failed (${e.code ?? e.name ?? 'ERROR'}): ${e.message}\n`);
            if (this._collectors.stderr) this._collectors.stderr.push(msg); else this.stderr?.write(msg);
            this._finish({ exitCode: 127, signal: null }, e);
        });
        this._abort = () => {
            this.terminate();
            this._finish({ exitCode: null, signal: 'SIGTERM' }, new Error('mdshell process canceled; outcome unknown'));
        };
        spec.signal?.addEventListener('abort', this._abort, { once: true });
        if (spec.signal?.aborted) this._abort();
    }

    get pid() { return this._pid; }

    async _sendStdin(method, params) {
        await this._stdinReady;
        if (this._finished || !this.world.notify(method, params)) throw new Error('mdshell process stdin unavailable');
    }

    _finish(outcome, error) {
        if (this._finished) return;
        this._finished = true;
        this.stdout?.end();
        this.stderr?.end();
        this._rejectStdin(error ?? new Error('mdshell process closed'));
        if (!this.stdin?.writableFinished) this.stdin?.destroy(error ?? new Error('mdshell process closed'));
        this.spec.signal?.removeEventListener('abort', this._abort);
        for (const off of this._offs) off();
        this.world.handleClosed();
        this._resolveDone(outcome);
    }

    terminate() {
        if (this._finished) return;
        this._spawned.then(() => this.world.notify('proc.terminate', { hid: this.hid })).catch(() => {});
    }

    async waitForExit(signal) {
        try { await this._spawned; } catch { return this.done; }
        if (this._finished) return this.done;
        return this.world.request('proc.waitForExit', { hid: this.hid }, signal);
    }
}

class RemoteTerminalHandle {
    constructor(world, tid, spec) {
        this.world = world;
        this.tid = tid;
        this.spec = spec;
        this._pid = -1;
        this.output = new PassThrough({ encoding: 'utf8' });
        this.done = new Promise((resolve) => { this._resolveDone = resolve; });
        world.handleOpened();
        this._offs = [
            world.on('term.out', (p) => { if (p.tid === tid) this.output.write(p.data); }),
            world.on('term.exit', (p) => {
                if (p.tid !== tid) return;
                this._finish({ exitCode: p.exitCode ?? null, signal: p.signal ?? null });
            }),
            world.on('$close', () => {
                this.output.write('[mdshell] terminal connection lost; process outcome unknown\n');
                this._failure = new Error('mdshell terminal connection lost');
                this._finish({ exitCode: null, signal: 'SIGHUP' });
            }),
        ];
        this._abort = () => {
            this._canceled = true;
            this._failure = new Error('mdshell terminal canceled; outcome unknown');
            if (this._pid >= 0) this.world.request('term.terminate', { tid }).catch(() => {});
            this._finish({ exitCode: null, signal: 'SIGTERM' });
        };
        spec.signal?.addEventListener('abort', this._abort, { once: true });
        if (spec.signal?.aborted) this._abort();
    }
    async start() {
        const { argv, cwd, env, rows, cols, graceMs } = this.spec;
        try {
            if (this._failure) throw this._failure;
            const stopped = this.done.then(() => { if (this._failure) throw this._failure; });
            await Promise.race([this.world.connect(), stopped]);
            if (this._failure) throw this._failure;
            const spawned = this.world.request('term.spawn', { tid: this.tid, spec: { argv: [...argv], cwd, env, rows, cols, graceMs } }).then(r => {
                this._pid = r.pid;
                if (this._canceled) this.world.request('term.terminate', { tid: this.tid }).catch(() => {});
            });
            await Promise.race([spawned, stopped]);
            if (this._failure) throw this._failure;
        } catch (error) {
            this._failure ??= error;
            this._finish({ exitCode: 127, signal: null });
            throw this._failure;
        }
    }
    _finish(outcome) {
        if (this._finished) return;
        this._finished = true;
        this.output.end();
        this.spec.signal?.removeEventListener('abort', this._abort);
        for (const off of this._offs) off();
        this.world.handleClosed();
        this._resolveDone(outcome);
    }
    _request(method, params) {
        if (this._finished) return Promise.reject(this._failure ?? new Error('mdshell terminal closed'));
        return this.world.request(method, { tid: this.tid, ...params });
    }
    get pid() { return this._pid; }
    write(data) { return this._request('term.write', { data }); }
    inspectForeground() { return this._request('term.inspectForeground', {}).then((r) => r ?? undefined); }
    signalForeground(signal) { return this._request('term.signalForeground', { signal }); }
    terminate() { return this._finished ? this.done : this._request('term.terminate', {}); }
}

export class RemoteSubprocessRuntime extends SubprocessRuntime {
    static Config = z.object({
        url: z.string().required(),
        token: z.string().default(''),
    });

    constructor(ctx, config) {
        super(ctx);
        this.config = config;
        this.world = RemoteWorld.get(config);
        ctx.effect(() => () => this.world.release());
        this.world.connect().catch(() => { /* 首次调用时再报错 */ });
    }

    resolveExecutable(command, env, signal) {
        return this.world.request('proc.resolveExecutable', { command, env }, signal);
    }

    spawn(spec) {
        return new RemoteHandle(this.world, nextId('h'), spec);
    }

    async spawnTerminal(spec) {
        const t = new RemoteTerminalHandle(this.world, nextId('t'), spec);
        await t.start();
        return t;
    }
}

export default RemoteSubprocessRuntime;
