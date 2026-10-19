import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { parse } from '@vue/compiler-sfc'
import { baseParse } from '@vue/compiler-dom'
import { apiClient } from '../src/services/httpClient.js'
import { readExperimentAudit } from '../src/services/experimentApi.js'

test('audit credentials are explicit per-request headers, not URL or shared defaults', async () => {
  const originalAdapter = apiClient.defaults.adapter
  const token = 'private-administrator-token'
  apiClient.defaults.adapter = async (config) => {
    assert.equal(config.url, '/api/experiments/comparisons/pair/audit/left')
    assert.equal(config.headers.get('X-Audit-Token'), token)
    assert.deepEqual(config.params, { acknowledge_unblind: true })
    assert.ok(!JSON.stringify(config.params).includes(token))
    return { config, status: 200, headers: {}, data: { data: { status: 'complete', records: [] } } }
  }
  try {
    assert.deepEqual(await readExperimentAudit('pair', 'left', token, true), { status: 'complete', records: [] })
    assert.equal(apiClient.defaults.headers.common['X-Audit-Token'], undefined)
  } finally {
    apiClient.defaults.adapter = originalAdapter
  }
})

test('audit body is escaped text and reading is limited to reviewed or invalid terminal trials', () => {
  const source = readFileSync(new URL('../src/features/experiment-comparison/ExperimentComparison.vue', import.meta.url), 'utf8')
  const { descriptor } = parse(source)
  const nodes = []
  const visit = (node) => {
    nodes.push(node)
    for (const child of node.children || []) visit(child)
  }
  visit(baseParse(descriptor.template.content))
  const body = nodes.find((node) => node.tag === 'pre'
    && node.children.some((child) => child.type === 5 && child.content.content === 'auditText'))
  assert.ok(body)
  assert.ok(body.children.some((node) => node.type === 5 && node.content.content === 'auditText'))
  assert.ok(!body.props.some((prop) => prop.name === 'html'))
  const button = nodes.find((node) => node.tag === 'el-button'
    && node.props.some((prop) => prop.name === 'on' && prop.exp?.content === 'inspectAudit(lane)'))
  assert.equal(button.props.find((prop) => prop.name === 'if').exp.content,
    'reveal || (completed && !comparison.valid_for_review)')
})
