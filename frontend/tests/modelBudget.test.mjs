import assert from 'node:assert/strict'
import test from 'node:test'
import { formatOutputBudget } from '../src/services/modelApi.js'

test('finite output retains its explicit limit', () => {
  assert.equal(formatOutputBudget({ max_output_tokens: 8192 }), `输出上限 ${(8192).toLocaleString()} tokens`)
})

test('omitted output shows provider default and planning reserve, never zero or infinity', () => {
  const text = formatOutputBudget({ max_output_tokens: null, output_limit_mode: 'provider_default', output_reserve_tokens: 32768 })
  assert.ok(text.includes('平台默认，非无限'))
  assert.ok(text.includes(`本地预留 ${(32768).toLocaleString()} tokens（仅规划）`))
  assert.ok(!text.includes('上限 0 tokens'))
})

test('old absent or null output records stay unknown', () => {
  for (const config of [{}, { max_output_tokens: null }, { max_output_tokens: 0 }]) {
    assert.equal(formatOutputBudget(config), '输出上限未记录')
  }
})
