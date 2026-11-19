// dsh-remote-world/lib/directorypicker.js — ctx.directoryPicker 的远端提供者。
// 默认 browse picker(dsh-host-directory-picker-browse)用 node:fs 直接读「运行 dsh 的机器」(云端实例)的目录,
// 于是「选择工作区」列的是云端实例目录,而不是用户的算力机器 node01。本提供者改用 ctx.fs——在租户实例里
// ctx.fs 已被 dsh-remote-world/fs 覆盖、指向 node01 的 world 通道,所以列出来、能新建的就是 node01 的目录。
// 返回结构与 browse 逐字段对齐:{ path, home, crumbs:[{name,path,hidden}], entries:[{name,path,hidden}], truncated }。
import path from 'node:path';
import z from '@deepseek-ai/schemastery';
import { DirectoryPicker, DirectoryPickerError } from '@deepseek-ai/dsh-host-directory-picker';

/** 从文件系统根到 target 的祖先链(面包屑)。node01 是 Linux → posix 语义。 */
function ancestryCrumbs(target, P) {
    const crumbs = [];
    let current = target;
    for (;;) {
        const parent = P.dirname(current);
        crumbs.unshift({ name: parent === current ? current : P.basename(current), path: current, hidden: false });
        if (parent === current) return crumbs;
        current = parent;
    }
}

export class RemoteDirectoryPicker extends DirectoryPicker {
    // 消费 ctx.fs(已被 remote-fs 覆盖指向 node01);cordis 要求显式声明依赖注入才能访问。
    static inject = ['fs'];

    static Config = z.object({
        maxEntries: z.number().default(1000),
    });

    constructor(ctx, config) {
        super(ctx);
        this.maxEntries = config?.maxEntries ?? 1000;
        // capability 对象必须在 service 生命周期内稳定(消费者可跨调用捕获)。
        this.browseCapability = {
            kind: 'browse',
            list: (p, signal) => this.list(p, signal),
            createDirectory: (p, name) => this.createDirectory(p, name),
        };
    }

    capability() {
        return this.browseCapability;
    }

    // node01(主目标)是 Linux → posix。若壳子上报 win32 再切,与 remote-fs 同口径。
    #pathlib() {
        return path.posix;
    }

    /** node01 的默认起点:remote-fs 的 cwd(= 壳子 --root，即 node01 工作根)。 */
    async #home() {
        const t = await this.ctx.fs.resolve('.');
        return this.ctx.fs.processPath(t);
    }

    async list(p, signal) {
        const P = this.#pathlib();
        const home = await this.#home();
        const target = p ?? home;
        let dirents;
        try {
            const t = await this.ctx.fs.resolve(target, { signal });
            dirents = await this.ctx.fs.listDir(t, signal);
        } catch (error) {
            signal?.throwIfAborted?.();
            throw new DirectoryPickerError('directory-unreadable', target,
                `cannot list ${target}: ${error instanceof Error ? error.message : String(error)}`);
        }
        const entries = [];
        let truncated = false;
        for (const d of dirents) {
            // 只列可进入的目录(与 browse 的 enterable 语义一致);符号链接指向目录的也放行。
            if (d.type !== 'directory' && d.type !== 'symlink') continue;
            if (d.type === 'symlink') {
                // 软链需确认指向目录才可进入;stat 跟随末段软链。
                try {
                    const st = await this.ctx.fs.stat(d.target, signal);
                    if (!st || st.type !== 'directory') continue;
                } catch { continue; }
            }
            if (entries.length >= this.maxEntries) { truncated = true; break; }
            entries.push({ name: d.name, path: P.join(target, d.name), hidden: d.name.startsWith('.') });
        }
        return { path: target, home, crumbs: ancestryCrumbs(target, P), entries, truncated };
    }

    async createDirectory(p, name) {
        const P = this.#pathlib();
        if (name.trim() === '' || name === '.' || name === '..' || /[/\\]/.test(name))
            throw new DirectoryPickerError('directory-create-failed', P.join(p, name),
                `"${name}" is not a single path segment`);
        const target = P.join(p, name);
        // mdshell 无独立 fs.mkdir:借 writeText 建目录内的占位文件,LocalFileSystem 的原子写会先建父目录,
        // 从而落地这个新目录(占位文件 .dsh-workspace 标记它是用户从 picker 新建的工作区)。
        try {
            const keep = await this.ctx.fs.resolve(P.join(target, '.dsh-workspace'));
            await this.ctx.fs.writeText(keep, '', { kind: 'createIfAbsent' });
            return target;
        } catch (error) {
            const msg = error instanceof Error ? error.message : String(error);
            if (/exist/i.test(msg)) throw new DirectoryPickerError('directory-exists', target, `${target} already exists`);
            throw new DirectoryPickerError('directory-create-failed', target, `cannot create ${target}: ${msg}`);
        }
    }
}

export default RemoteDirectoryPicker;
