import { readFileSync } from 'node:fs';
import { join } from 'node:path';

export const name = 'dsh-md-auto-approve';

function flagPath() {
    const home = process.env.DSH_HOME || join(process.cwd(), '.dsh');
    return join(home, 'auto-approve.json');
}

function isEnabled() {
    try {
        const raw = JSON.parse(readFileSync(flagPath(), 'utf8'));
        return raw.enabled === true;
    } catch {
        return false;
    }
}

export function apply(ctx) {
    ctx.on('approval/request', (req, next) => {
        if (!isEnabled()) return next();
        console.log(`[dsh-md-auto-approve] 自动批准: ${req.toolName ?? '(unknown tool)'}`);
        return 'allowed-once';
    }, true);
}
