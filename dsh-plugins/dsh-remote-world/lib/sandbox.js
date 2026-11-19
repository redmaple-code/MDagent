// dsh-remote-world/lib/sandbox.js — ctx.sandbox 的远端提供者。
// confine() 是同步契约，跨网络做不到；这里只把策略打包成带标记的 argv，真正的包装由壳子在
// spawn 时用自己的 sandbox-local 完成（bwrap → Landlock；不可用即拒，绝不静默放开）。
// enforcement / denialSignatures / runnerFailureRules 来自壳子 hello 里缓存的能力表——
// 壳子探测得到什么就报什么，云端不替它编造 'full'。
import { SandboxProvider, SandboxUnavailableError } from '@deepseek-ai/dsh-sandbox';
import z from '@deepseek-ai/schemastery';
import { RemoteWorld } from './client.js';
import { CONFINE_MARKER } from './protocol.js';

export class RemoteSandboxProvider extends SandboxProvider {
    static Config = z.object({
        url: z.string().required(),
        token: z.string().default(''),
    });

    constructor(ctx, config) {
        super(ctx);
        this.config = config;
        this.world = RemoteWorld.get(config);
        ctx.effect(() => () => this.world.release());
        this.world.connect().catch(() => { /* confine 时按未连接处理 */ });
    }

    confine(argv, policy) {
        const cap = this.world.hello?.sandbox;
        // rc.6 签名：SandboxUnavailableError(mode: ConfinedSandboxMode, detail?: string)
        if (!cap) throw new SandboxUnavailableError(policy.mode, 'remote execution world (mdshell) not connected yet');
        if (!cap.available) throw new SandboxUnavailableError(policy.mode, `remote sandbox unavailable on mdshell: ${cap.error ?? 'unknown'}`);
        const marker = JSON.stringify({
            mode: policy.mode,
            workspaceRoot: policy.workspaceRoot,
            sessionId: policy.sessionId ?? null,
        });
        return {
            argv: [CONFINE_MARKER, marker, '--', ...argv],
            enforcement: cap.enforcement,
            denialSignatures: cap.denialSignatures,
            runnerFailureRules: cap.runnerFailureRules,
        };
    }
}

export default RemoteSandboxProvider;
