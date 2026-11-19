// wechat_push 数据根/DSH_HOME 归一化回归门(六轮对抗):
// DSH_HOME 与 MDSIM_DATA_ROOT 必须过同款 normalizeEnvPath——~/相对值不得相对 cwd 解析
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { homedir } from 'node:os'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { normalizeEnvPath } from '../push/wechat_push.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const CODE_PARENT = resolve(HERE, '..', '..') // tests/ 上两级 = dsh-agent 父目录

test('normalizeEnvPath: ~ 展开家目录', () => {
  assert.equal(normalizeEnvPath('~/x'), resolve(homedir(), 'x'))
  assert.equal(normalizeEnvPath('~'), homedir())
})

test('normalizeEnvPath: 相对值锚定代码父目录而非 cwd', () => {
  assert.equal(normalizeEnvPath('mdsim-data'), resolve(CODE_PARENT, 'mdsim-data'))
})

test('normalizeEnvPath: 空白/未设置 → null,绝对值原样', () => {
  assert.equal(normalizeEnvPath('   '), null)
  assert.equal(normalizeEnvPath(''), null)
  assert.equal(normalizeEnvPath(undefined), null)
  assert.equal(normalizeEnvPath('/abs/path'), '/abs/path')
})
