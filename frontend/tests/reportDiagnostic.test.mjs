import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { compileScript, compileTemplate, parse } from '@vue/compiler-sfc'
import { createRenderer, h, nextTick, reactive } from 'vue'
import { apiClient } from '../src/services/httpClient.js'

const file = new URL('../src/features/experiment-comparison/ReportConflictDiagnostic.vue', import.meta.url)
const source = await readFile(file, 'utf8')
const { descriptor } = parse(source)
const script = compileScript(descriptor, { id: 'report-diagnostic' })
const moduleSource = script.content.replace(/from (['"])([^'"]+)\1/g, (_, quote, specifier) =>
  `from '${specifier === 'vue' ? import.meta.resolve('vue') : new URL(specifier, file).href}'`)
const component = (await import('data:text/javascript;base64,' + Buffer.from(moduleSource).toString('base64'))).default
const flush = async () => { await new Promise((resolve) => setImmediate(resolve)); await nextTick() }
const response = (config, data) => ({ config, status: 200, headers: {}, data: { code: 200, data } })

const mount = (initial = {}) => {
  let state
  const props = reactive({ comparisonId: 'pair', lane: 'left', available: true, modelConfigured: true, ...initial })
  const renderer = createRenderer({ createComment: () => ({}), insert: () => {}, remove: () => {},
    parentNode: () => null, nextSibling: () => null })
  const wrapped = { ...component, setup(parameters, context) { state = component.setup(parameters, context); return () => null } }
  const app = renderer.createApp({ render: () => h(wrapped, props) })
  app.mount({})
  return { state, props, unmount: () => app.unmount() }
}

test('diagnosis requires explicit action; duplicate clicks and stale history never overwrite a completed result', async () => {
  assert.deepEqual(compileTemplate({ id: 'report-diagnostic', source: descriptor.template.content,
    compilerOptions: { bindingMetadata: script.bindings } }).errors, [])
  assert.doesNotMatch(source, /v-html/)
  const adapter = apiClient.defaults.adapter
  const calls = []
  apiClient.defaults.adapter = async (config) => {
    calls.push(config)
    return response(config, { id: 'diagnostic-1', status: 'completed', explanation: { classification: 'fields_conflict' } })
  }
  const view = mount()
  try {
    await flush()
    assert.equal(calls.length, 0)
    await Promise.all([view.state.start(), view.state.start()])
    assert.equal(calls.length, 1)
    assert.equal(calls[0].method, 'post')
    assert.equal(calls[0].url, '/api/experiments/comparisons/pair/diagnostic/left')
    view.props.snapshot = { id: 'diagnostic-1', status: 'running' }
    await flush()
    assert.equal(view.state.record.value.status, 'completed')
    await view.state.start()
    assert.equal(calls.length, 1)
  } finally { view.unmount(); apiClient.defaults.adapter = adapter }
})

test('restoring a running diagnostic only reads; switching comparisons aborts and discards a late response', async () => {
  const adapter = apiClient.defaults.adapter
  const pending = []
  apiClient.defaults.adapter = (config) => new Promise((resolve) => { pending.push({ config, resolve }) })
  const view = mount({ snapshot: { id: 'diagnostic-1', status: 'running' } })
  try {
    await flush()
    assert.equal(pending.length, 1)
    assert.equal(pending[0].config.method, 'get')
    view.props.comparisonId = 'another-pair'
    view.props.snapshot = null
    await nextTick()
    assert.equal(pending[0].config.signal.aborted, true)
    pending[0].resolve(response(pending[0].config, { id: 'diagnostic-1', status: 'completed' }))
    await flush()
    assert.equal(view.state.record.value, null)
    assert.equal(view.state.error.value, '')
    assert.equal(pending.length, 1)
  } finally { view.unmount(); apiClient.defaults.adapter = adapter }
})
