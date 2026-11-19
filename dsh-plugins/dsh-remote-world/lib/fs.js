// dsh-remote-world/lib/fs.js — ctx.fs 的远端提供者：每个方法原样转发给 mdshell 上的 LocalFileSystem。
// FsTargetKey / FsVersion 底层都是 string，FsTarget/FsInfo/FsDirEntry 都是纯数据，可以 JSON 直传。
import path from 'node:path';
import { FileSystem } from '@deepseek-ai/dsh-fs';
import z from '@deepseek-ai/schemastery';
import { RemoteWorld } from './client.js';
import { b64dec } from './protocol.js';

export class RemoteFileSystem extends FileSystem {
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

    // 壳子是 Linux（主目标）时路径按 posix 语义处理，与云端进程所在平台无关。
    #pathlib() { return this.world.hello?.shell?.pathStyle === 'win32' ? path.win32 : path.posix; }

    resolve(p, opts) {
        return this.world.request('fs.resolve', { path: p, cwd: opts?.cwd }, opts?.signal);
    }

    processPath(target) { return String(target.targetKey); }

    fileUrl(target) {
        const p = this.processPath(target);
        if (this.#pathlib() === path.win32) return 'file:///' + p.replace(/\\/g, '/').split('/').map(encodeURIComponent).join('/');
        return 'file://' + p.split('/').map(encodeURIComponent).join('/');
    }

    contains(parent, child) {
        const P = this.#pathlib();
        const rel = P.relative(this.processPath(parent), this.processPath(child));
        return rel === '' || (!rel.startsWith('..') && !P.isAbsolute(rel));
    }

    async stat(target, signal) {
        return (await this.world.request('fs.stat', { target }, signal)) ?? undefined;
    }

    async lstat(p, opts, signal) {
        return (await this.world.request('fs.lstat', { path: p, cwd: opts?.cwd }, signal ?? opts?.signal)) ?? undefined;
    }

    readText(target, signal) {
        return this.world.request('fs.readText', { target }, signal);
    }

    async streamText(target, signal) {
        return this.world.request('fs.streamText', { target }, signal, ({ sid }) => this.world.stream(sid, signal));
    }

    async readBytes(target, signal, maxBytes) {
        const r = await this.world.request('fs.readBytes', { target, maxBytes }, signal);
        return new Uint8Array(b64dec(r.data));
    }

    listDir(target, signal) {
        return this.world.request('fs.listDir', { target }, signal);
    }

    writeText(target, content, expected, signal, sandboxPolicy) {
        return this.world.request('fs.writeText', { target, content, expected, sandboxPolicy }, signal);
    }

    editText(target, edit, expected, signal, sandboxPolicy) {
        return this.world.request('fs.editText', { target, edit, expected, sandboxPolicy }, signal);
    }
}

export default RemoteFileSystem;
