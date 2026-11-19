// 分子模拟助手 · 微信推送模块
// 通道优先级：① Server酱/方糖 Turbo（push_config.serverchan_key，独立·稳，不依赖 zerocarbon）
//            ② iLink 兜底（借 zerocarbon 微信桥，桥没跑会 "prepare failed"）
// 用法：node wechat_push.mjs "要推送的文字"
import { randomBytes } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { fileURLToPath } from 'node:url'
import { dirname, isAbsolute, join, resolve } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
// 与 mcp/paths.py data_root() 同语义(五轮对抗):~ 展开家目录;相对值锚定
// 代码根父目录(push/ 上两级 = dsh-agent 父目录)而非 cwd,空白视为未设置
export function normalizeEnvPath(raw) {
  let p = (raw ?? '').trim()
  if (!p) return null
  if (p === '~' || p.startsWith('~/')) p = homedir() + p.slice(1)
  if (!isAbsolute(p)) p = resolve(HERE, '..', '..', p)
  return p
}
const resolveDataRoot = () => normalizeEnvPath(process.env.MDSIM_DATA_ROOT)
// 凭证随 DSH_HOME 外置(2026-08-29 阶段5):env > <数据根>/dsh-home > 代码库 .dsh 兜底
// DSH_HOME 过同款归一化(六轮对抗):~/相对值不得相对 cwd 解析
const DATA_ROOT = resolveDataRoot()
const DSH_HOME_NORM = normalizeEnvPath(process.env.DSH_HOME)
const CRED = DSH_HOME_NORM
  ? join(DSH_HOME_NORM, '.credentials.yaml')
  : (DATA_ROOT
    ? join(DATA_ROOT, 'dsh-home', '.credentials.yaml')
    : join(HERE, '..', '.dsh', '.credentials.yaml'))
const CFG = join(HERE, 'push_config.json')

const ILINK_APP_CLIENT_VERSION = (2 << 16) | (2 << 8) | 0 // 131584
const CHANNEL_VERSION = '2.2.0'
const MSG_TYPE_BOT = 2, MSG_STATE_FINISH = 2, ITEM_TEXT = 1
const MAX_CHARS = 1800

function readCreds() {
  try {
    const txt = readFileSync(CRED, 'utf-8')
    const out = {}
    for (const line of txt.split(/\r?\n/)) {
      const m = line.match(/^([A-Za-z0-9_]+):\s*(.+?)\s*$/)
      if (m) out[m[1]] = m[2].replace(/^["']|["']$/g, '')
    }
    return out
  } catch { return {} }
}

// ── 通道 A：Server酱/方糖 Turbo ──
async function sendServerChan(text, cfg) {
  const key = cfg.serverchan_key
  const lines = text.split('\n').filter(s => s.trim())
  const title = (lines[0] || '分子模拟助手提醒').replace(/[#*_`]/g, '').slice(0, 30)
  // desp 用 markdown：每行前留空行更易读
  const desp = text.slice(0, 30000)
  const params = new URLSearchParams({ title, desp })
  const resp = await fetch(`https://sctapi.ftqq.com/${key}.send`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: params.toString(),
  })
  const raw = await resp.text()
  let parsed = null
  try { parsed = JSON.parse(raw) } catch { /* keep raw */ }
  const ok = resp.ok && parsed && parsed.code === 0
  return [{ channel: 'serverchan', ok, status: resp.status, code: parsed?.code,
            msg: parsed?.message || parsed?.data?.error, raw: raw.slice(0, 160) }]
}

// ── 通道 B：iLink 兜底 ──
async function sendILink(text, cfg) {
  const creds = readCreds()
  const token = creds.WEIXIN_BOT_TOKEN
  const baseUrl = (creds.WEIXIN_BASE_URL || 'https://ilinkai.weixin.qq.com').replace(/\/+$/, '')
  const to = cfg.to
  if (!token || !to) throw new Error('缺少 WEIXIN_BOT_TOKEN 或 push_config.to（且未配 serverchan_key）')
  const chunks = []
  for (let i = 0; i < text.length; i += MAX_CHARS) chunks.push(text.slice(i, i + MAX_CHARS))
  const results = []
  for (const chunk of chunks) {
    const body = JSON.stringify({
      msg: {
        from_user_id: '', to_user_id: to,
        client_id: 'dsh-quant-' + randomBytes(6).toString('hex'),
        message_type: MSG_TYPE_BOT, message_state: MSG_STATE_FINISH,
        item_list: [{ type: ITEM_TEXT, text_item: { text: chunk } }],
      },
      base_info: { channel_version: CHANNEL_VERSION },
    })
    const resp = await fetch(baseUrl + '/ilink/bot/sendmessage', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        AuthorizationType: 'ilink_bot_token',
        'Content-Length': String(Buffer.byteLength(body)),
        'X-WECHAT-UIN': randomBytes(4).toString('base64url'),
        'iLink-App-Id': 'bot',
        'iLink-App-ClientVersion': String(ILINK_APP_CLIENT_VERSION),
        Authorization: `Bearer ${token}`,
      },
      body,
    })
    const raw = await resp.text()
    let parsed = null
    try { parsed = JSON.parse(raw) } catch { /* keep raw */ }
    results.push({ channel: 'ilink', ok: resp.ok && (parsed?.ret === 0 || parsed?.ret === undefined),
                   status: resp.status, ret: parsed?.ret, errmsg: parsed?.errmsg, raw: raw.slice(0, 160) })
    if (!resp.ok || (parsed && parsed.ret !== 0 && parsed.ret !== undefined)) break
  }
  return results
}

export async function sendText(text) {
  if (!text || !text.trim()) throw new Error('空消息，拒发')
  const cfg = JSON.parse(readFileSync(CFG, 'utf-8'))
  if (cfg.serverchan_key) return await sendServerChan(text, cfg)
  return await sendILink(text, cfg)
}

// CLI
if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const text = process.argv.slice(2).join(' ')
  sendText(text).then(r => {
    console.log('PUSH_RESULT=' + JSON.stringify(r))
    const good = r.every(x => x.ok)
    process.exit(good ? 0 : 1)
  }).catch(e => { console.error('PUSH_ERR:', e.message); process.exit(2) })
}
