// dsh-remote-world/lib/client.js — 云端侧到 mdshell 的单条 WebSocket 连接，三个提供者共用。
// 用 `ws` 客户端而不是全局 WebSocket：需要拿到底层 socket 做 ref/unref——
//   · 空闲(无在途请求 / 流 / 进程句柄)时 unref：dsh 自己的活干完,事件循环自然排空,headless 正常退出;
//   · 有在途工作时 ref：远端 bash 跑着的时候进程绝不会因为"没别的事"而提前退出。
// 令牌走子协议(tok.<token>),不进 URL。
import WebSocket from 'ws';
import { FsError } from '@deepseek-ai/dsh-fs';
import { encode, decode, PROTOCOL_VERSION, tokenProtocols } from './protocol.js';

const worlds = new Map();
const DEBUG = !!process.env.MDEXP_REMOTE_DEBUG;
const dbg = (...a) => { if (DEBUG) console.error('[dsh-remote-world]', ...a); };

function mapError(e) {
    if (e?.name === 'FsError' && e.code) return new FsError(e.message, e.code);
    const err = new Error(e?.message ?? 'remote error');
    if (e?.name) err.name = e.name;
    if (e?.code) err.code = e.code;
    return err;
}

export class RemoteWorld {
    /** 同一 url+token 只建一条连接（fs / subprocess / sandbox 三个提供者共用）。 */
    static get(config) {
        const key = `${config.url}|${config.token ?? ''}`;
        let w = worlds.get(key);
        if (!w) { w = new RemoteWorld(config); worlds.set(key, w); }
        w.refs++;
        return w;
    }

    constructor(config) {
        this.config = config;
        this.refs = 0;
        this.closing = false;
        this.ws = null;
        this.socket = null;         // 底层 net.Socket，用于 ref/unref
        this.ready = null;
        this.hello = null;          // 壳子的 hello（含 sandbox 能力），confine() 同步读它
        this.nextId = 1;
        this.pending = new Map();   // id -> {resolve, reject}
        this.listeners = new Map(); // method -> Set<fn>
        this.streams = new Map();   // sid -> queue
        this.liveHandles = 0;       // 远端进程/终端句柄（未收口的）
    }

    /** 提供者 dispose 时调用：三个提供者都放手才真正关连接。 */
    release() {
        this.refs--;
        dbg(`release refs=${this.refs}`);
        if (this.refs > 0) return;
        const key = `${this.config.url}|${this.config.token ?? ''}`;
        if (worlds.get(key) === this) worlds.delete(key);
        this.closing = true;
        try { this.ws?.close(1000, 'dispose'); } catch { /* ignore */ }
    }

    get busy() { return this.pending.size > 0 || this.streams.size > 0 || this.liveHandles > 0; }
    updateRef() {
        if (!this.socket) return;
        if (this.busy) this.socket.ref(); else this.socket.unref();
    }
    handleOpened() { this.liveHandles++; this.updateRef(); }
    handleClosed() { this.liveHandles = Math.max(0, this.liveHandles - 1); this.updateRef(); }

    connect() {
        if (this.closing) return Promise.reject(new Error('mdshell provider disposed'));
        if (this.ready) return this.ready;
        this.ready = new Promise((resolve, reject) => {
            const ws = new WebSocket(this.config.url, tokenProtocols(this.config.token), { handshakeTimeout: 15000 });
            this.ws = ws;
            let settled = false;
            ws.on('open', () => {
                this.socket = ws._socket ?? null;
                this.updateRef();
                dbg(`connected ${this.config.url}`);
            });
            ws.on('message', (data) => {
                let msg;
                try { msg = decode(data.toString('utf8')); } catch { return; }
                if (msg.t === 'ntf' && msg.m === 'hello') {
                    if (msg.p?.protocol !== PROTOCOL_VERSION) {
                        const e = new Error(`mdshell protocol ${msg.p?.protocol} != ${PROTOCOL_VERSION}`);
                        if (!settled) { settled = true; reject(e); }
                        ws.close();
                        return;
                    }
                    this.hello = msg.p;
                    if (!settled) { settled = true; resolve(this.hello); }
                    return;
                }
                this.dispatch(msg);
            });
            ws.on('error', (err) => {
                const e = new Error(`mdshell websocket error (${this.config.url}): ${err?.message ?? err}`);
                if (!settled) { settled = true; reject(e); }
            });
            ws.on('close', (code, reason) => {
                const why = `${code}${reason?.length ? ' ' + reason.toString() : ''}`;
                const e = new Error(`mdshell connection closed (${why})`);
                dbg(`closed ${why}`);
                for (const { reject: rj } of this.pending.values()) rj(e);
                this.pending.clear();
                for (const q of this.streams.values()) q.fail(e);
                this.streams.clear();
                this.hello = null;
                this.ready = null;
                this.ws = null;
                this.socket = null;
                if (!settled) { settled = true; reject(e); }
                // 悬着的进程句柄也要收口（否则 bash 工具一直等到超时）：见 subprocess.js 的 '$close'
                const set = this.listeners.get('$close');
                if (set) for (const fn of [...set]) { try { fn({ code, reason: reason?.toString() }); } catch { /* 隔离 */ } }
                this.liveHandles = 0;
            });
        });
        return this.ready;
    }

    dispatch(msg) {
        if (msg.t === 'res' || msg.t === 'err') {
            const p = this.pending.get(msg.id);
            if (!p) return;
            this.pending.delete(msg.id);
            this.updateRef();
            if (msg.t === 'res') p.resolve(msg.r);
            else p.reject(mapError(msg.e));
            return;
        }
        if (msg.t === 'ntf') {
            if (msg.m === 'stream.data' || msg.m === 'stream.end' || msg.m === 'stream.error') {
                const q = this.streams.get(msg.p?.sid);
                if (!q) return;
                if (msg.m === 'stream.data') q.push(msg.p.data);
                else if (msg.m === 'stream.end') q.end();
                else q.fail(new Error(msg.p.message ?? 'stream error'));
                return;
            }
            const set = this.listeners.get(msg.m);
            if (set) for (const fn of set) { try { fn(msg.p); } catch { /* 监听器异常隔离 */ } }
        }
    }

    // onResponse runs inside dispatch, before any following notification from the same socket read.
    async request(m, p, signal, onResponse) {
        await this.connect();
        if (signal?.aborted) throw abortedError(m);
        if (this.closing || !this.ws || this.ws.readyState !== WebSocket.OPEN) throw new Error('mdshell connection unavailable');
        const id = this.nextId++;
        const done = new Promise((resolve, reject) => {
            const cleanup = () => signal?.removeEventListener('abort', abort);
            const abort = () => {
                if (this.pending.delete(id)) { this.updateRef(); cleanup(); reject(abortedError(m)); }
            };
            this.pending.set(id, {
                resolve: value => {
                    cleanup();
                    try { resolve(onResponse ? onResponse(value) : value); }
                    catch (error) { reject(error); }
                },
                reject: error => { cleanup(); reject(error); },
            });
            signal?.addEventListener('abort', abort, { once: true });
        });
        this.updateRef();
        try {
            this.ws.send(encode({ t: 'req', id, m, p }));
        } catch (error) {
            const pending = this.pending.get(id);
            this.pending.delete(id);
            this.updateRef();
            pending?.reject(error);
        }
        return done;
    }

    notify(m, p) {
        if (this.closing || !this.ws || this.ws.readyState !== WebSocket.OPEN) return false;
        try { this.ws.send(encode({ t: 'ntf', m, p })); return true; }
        catch { return false; }
    }

    on(m, fn) {
        let set = this.listeners.get(m);
        if (!set) { set = new Set(); this.listeners.set(m, set); }
        set.add(fn);
        return () => {
            set.delete(fn);
            if (!set.size && this.listeners.get(m) === set) this.listeners.delete(m);
        };
    }

    /** 把壳子推送的流（sid）转成 AsyncIterable。 */
    stream(sid, signal) {
        if (typeof sid !== 'string' || !sid) throw new Error('invalid stream id');
        if (this.streams.has(sid)) throw new Error('duplicate stream id');
        const q = new StreamQueue(() => {
            signal?.removeEventListener('abort', abort);
            if (this.streams.get(sid) === q) this.streams.delete(sid);
            this.updateRef();
        });
        const abort = () => q.fail(abortedError('stream'), true);
        this.streams.set(sid, q);
        this.updateRef();
        signal?.addEventListener('abort', abort, { once: true });
        if (signal?.aborted) abort();
        return q.iterate();
    }
}

function abortedError(what) {
    const e = new FsError(`${what} aborted`, 'FS_ABORTED');
    return e;
}

class StreamQueue {
    constructor(cleanup) { this.items = []; this.waiters = []; this.done = false; this.error = null; this.cleanup = cleanup; }
    push(x) { if (this.done) return; const w = this.waiters.shift(); if (w) w.resolve({ value: x, done: false }); else this.items.push(x); }
    end() {
        if (this.done) return;
        this.done = true;
        this.cleanup();
        this.cleanup = null;
        for (const w of this.waiters.splice(0)) {
            if (this.error) w.reject(this.error);
            else w.resolve({ value: undefined, done: true });
        }
    }
    fail(e, discard = false) { if (this.done) return; if (discard) this.items.length = 0; this.error = e; this.end(); }
    iterate() {
        const self = this;
        return {
            [Symbol.asyncIterator]() {
                return {
                    next() {
                        if (self.items.length) return Promise.resolve({ value: self.items.shift(), done: false });
                        if (self.error) return Promise.reject(self.error);
                        if (self.done) return Promise.resolve({ value: undefined, done: true });
                        return new Promise((resolve, reject) => self.waiters.push({ resolve, reject }));
                    },
                    return() { self.items.length = 0; self.error = null; self.end(); return Promise.resolve({ value: undefined, done: true }); },
                };
            },
        };
    }
}
