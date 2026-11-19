// dsh-remote-world/lib/protocol.js — 云端提供者 ↔ mdshell 的线协议（JSON 文本帧 over WebSocket）
//
// 帧形态（t = 类型）：
//   {t:'req', id, m, p}          请求（云端→壳子）
//   {t:'res', id, r}             应答
//   {t:'err', id, e:{name,code,message}}
//   {t:'ntf', m, p}              通知（双向；壳子→云端用于 stdout/stderr/exit/stream 推送）
// 二进制一律 base64；文本流直接传字符串。
// 壳子连接建立后主动发一条 ntf 'hello'，带协议号、平台与沙箱能力——云端 sandbox 提供者
// 的 confine() 是同步方法，只能靠这份缓存的能力表回答 enforcement/denialSignatures。
//
// 生产拓扑（壳子出站 → 云端中继 → 租户 dsh）：壳子与中继之间是一条节点链路，上面跑多个
// 逻辑通道；链路帧 = "<ch>\n<上面的 JSON 帧>"。ch === '$' 是控制通道（hello / channel.open /
// channel.close）；其它 ch 的载荷原样透传，中继不解析 JSON。
//
// 令牌一律走 WebSocket 子协议（['mdexp', 'tok.<token>']），绝不进 URL（nginx access.log 会明文落）。

export const PROTOCOL_VERSION = 1;
export const SUBPROTOCOL = 'mdexp';
export const CONTROL_CH = '$';

// 云端 confine() 返回的 argv 以此标记打头：壳子在 spawn 时识别，就地用自己的 sandbox-local
// 真正包装后再起进程。限制决定永远在进程运行的机器上做；云端只能提出更窄的模式。
export const CONFINE_MARKER = '@mdshell-confine';

export const encode = (msg) => JSON.stringify(msg);
export const decode = (text) => JSON.parse(text);

export const b64enc = (buf) => Buffer.from(buf).toString('base64');
export const b64dec = (s) => Buffer.from(s, 'base64');

export const tokenProtocols = (token) => (token ? [SUBPROTOCOL, `tok.${token}`] : [SUBPROTOCOL]);

export const packEnvelope = (ch, text) => `${ch}\n${text}`;
export function unpackEnvelope(text) {
    const i = text.indexOf('\n');
    if (i < 0) return null;
    return { ch: text.slice(0, i), payload: text.slice(i + 1) };
}

export function serializeError(e) {
    return {
        name: e?.name ?? 'Error',
        code: e?.code,
        message: String(e?.message ?? e),
    };
}

// 从 argv 里解析 confine 标记；不是标记形态则返回 null。
export function parseConfineMarker(argv) {
    if (!Array.isArray(argv) || argv[0] !== CONFINE_MARKER) return null;
    if (argv[2] !== '--') throw new Error('malformed confine marker: expected "--" at argv[2]');
    let policy;
    try { policy = JSON.parse(argv[1]); } catch { throw new Error('malformed confine marker: policy is not JSON'); }
    return { policy, argv: argv.slice(3) };
}
