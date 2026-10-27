import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import { apiClient } from '../src/services/httpClient.js'
import { listExperimentHistory, readExperimentHistory } from '../src/services/experimentApi.js'
import { historyConditions, promptComparison, reviewPreference } from '../src/features/experiment-comparison/history.js'
import { REVIEW_RUBRIC_VERSION, reviewDimensions, legacyReviewDimensions, emptyReviewScores, savedReviewDimensions } from '../src/features/experiment-comparison/reviewRubric.js'

test('ledger and network accounting are explicit and escaped without rewriting raw provider usage', () => {
  const source = readFileSync(new URL('../src/features/experiment-comparison/ExperimentComparison.vue', import.meta.url), 'utf8')
  assert.ok(source.includes('JSON.stringify(laneMetrics(lane).findings_ledger.ledger, null, 2)'))
  assert.ok(!source.includes('v-html'))
  for (const key of ['duration_ms', 'adjusted_duration_ms', 'excluded_network_ms', 'actual_total_tokens', 'accounted_model_total_tokens']) assert.ok(source.includes(key))
  assert.ok(source.includes('这不是供应商返回的 usage'))
  assert.ok(source.includes('未知成本尝试'))
})

test('saved blind review is visible after reveal with escaped notes and missing scores left unknown', () => {
  assert.equal(reviewPreference('left'), '左侧更好')
  assert.equal(reviewPreference('right'), '右侧更好')
  assert.equal(reviewPreference('tie'), '相当')
  assert.equal(reviewPreference(null), '未评分')
  const source = readFileSync(new URL('../src/features/experiment-comparison/BlindReviewResult.vue', import.meta.url), 'utf8')
  const { descriptor, errors } = parse(source)
  assert.deepEqual(errors, [])
  assert.doesNotThrow(() => compileScript(descriptor, { id: 'review-result' }))
  assert.deepEqual(compileTemplate({ source: descriptor.template.content, filename: 'review-result', id: 'review-result' }).errors, [])
  assert.ok(source.includes("?? '未记录'"))
  assert.ok(source.includes('{{ review.notes'))
  assert.ok(!source.includes('v-html') && !source.includes('v-model'))
  for (const file of ['ExperimentComparison.vue', 'ExperimentHistory.vue']) {
    const component = readFileSync(new URL(`../src/features/experiment-comparison/${file}`, import.meta.url), 'utf8')
    assert.ok(component.includes('<BlindReviewResult'))
  }
})

test('history requests are paginated and contain no audit credential or mutation', async () => {
  const original = apiClient.defaults.adapter
  const requests = []
  apiClient.defaults.adapter = async (config) => {
    requests.push(config)
    assert.equal(config.method, 'get')
    assert.equal(config.headers.get('X-Audit-Token'), undefined)
    return { config, status: 200, headers: {}, data: { data: { items: [], total: 0 } } }
  }
  try {
    await listExperimentHistory('project', 20, 20)
    await readExperimentHistory('pair')
    assert.equal(requests[0].url, '/api/experiments/projects/project/comparisons')
    assert.deepEqual(requests[0].params, { offset: 20, limit: 20 })
    assert.equal(requests[1].url, '/api/experiments/comparisons/pair/history')
  } finally { apiClient.defaults.adapter = original }
})

test('absent history conditions stay unknown and matching prompts do not prove equal conditions', () => {
  assert.equal(historyConditions({}).prompt_sha256, null)
  assert.equal(historyConditions({}).budget, null)
  const trial = (hash) => ({ lanes: { left: { prompt_snapshot: { instructions_sha256: hash } }, right: { prompt_snapshot: { instructions_sha256: hash } } } })
  assert.match(promptComparison([trial('a'), trial('a')]), /相同.*仍需核对/)
  assert.match(promptComparison([trial('a'), trial('b')]), /不同.*不代表/)
  assert.match(promptComparison([trial('a'), {}]), /缺失/)
})

test('history and current experiment components compile and escape reports and prompts', () => {
  for (const filename of ['ExperimentHistory.vue', 'ExperimentComparison.vue']) {
    const source = readFileSync(new URL(`../src/features/experiment-comparison/${filename}`, import.meta.url), 'utf8')
    const { descriptor, errors } = parse(source)
    assert.deepEqual(errors, [])
    assert.doesNotThrow(() => compileScript(descriptor, { id: filename }))
    const result = compileTemplate({ source: descriptor.template.content, filename, id: filename })
    assert.deepEqual(result.errors, [])
    assert.ok(!source.includes('v-html'))
    assert.ok(source.includes('generation'))
  }
})

test('opening history restores saved review and late old snapshots cannot overwrite the selected trial', async () => {
  const source = readFileSync(new URL('../src/features/experiment-comparison/ExperimentComparison.vue', import.meta.url), 'utf8')
  const script = parse(source).descriptor.scriptSetup.content.replace(/^import[\s\S]*?from\s+['"][^'"]+['"]\r?\n/gm, '')
  let callback, signal, finishStream
  const terminal = { id: 'reviewed', status: 'completed', question: 'original question', lanes: {},
    reveal: { left: 'baseline', right: 'security_evidence' },
    review: { preferred_lane: 'right', notes: 'original notes', scores: { left: { correctness: 2 }, right: { correctness: 4 } } } }
  const context = {
    ref: (value) => ({ value }), reactive: (value) => value,
    emptyReviewScores,
    computed: (get) => ({ get value() { return get() } }),
    defineProps: () => ({ projectId: 'project', modelStatus: { configured: true } }),
    watch: () => {}, onUnmounted: () => {}, AbortController,
    ElMessage: { error: () => assert.fail('unexpected error'), success: () => {} },
    readExperimentHistory: async (id) => id === 'running' ? { id, status: 'running', question: 'q', events_url: 'stream' } : terminal,
    streamExperimentComparison: (_, onSnapshot, currentSignal) => {
      callback = onSnapshot; signal = currentSignal
      return new Promise((resolve) => { finishStream = resolve })
    },
  }
  runInNewContext(script + '\nglobalThis.api = { openHistory, comparison, reveal, notes, scores, running, preferredLane };', context)
  const old = context.api.openHistory('running')
  await new Promise((resolve) => setImmediate(resolve))
  await context.api.openHistory('reviewed')
  assert.equal(signal.aborted, true)
  callback({ id: 'late-old-snapshot' })
  finishStream()
  await old
  assert.equal(context.api.comparison.value.id, 'reviewed')
  assert.equal(context.api.notes.value, 'original notes')
  assert.equal(context.api.comparison.value.review.scores.right.correctness, 4)
  assert.equal(context.api.scores.right.conclusion_credibility, 0)
  assert.equal(context.api.scores.right.correctness, undefined)
  assert.equal(context.api.preferredLane.value, 'right')
  assert.equal(context.api.reveal.value.left, 'baseline')
  assert.equal(context.api.running.value, false)
})

test('independent rubric preserves old scores and requires explicit new ratings', () => {
  const old = { scores: { left: { correctness: 2 }, right: { correctness: 4 } } }
  const before = JSON.stringify(old)
  assert.deepEqual(savedReviewDimensions(old), legacyReviewDimensions)
  assert.equal(JSON.stringify(old), before)
  assert.deepEqual(savedReviewDimensions({ scores: { rubric_version: REVIEW_RUBRIC_VERSION } }), reviewDimensions)
  const left = emptyReviewScores(), right = emptyReviewScores()
  left.discovery_breadth = 5
  assert.equal(right.discovery_breadth, 0)
  assert.equal(left.chain_verification, 0)
  assert.equal(left.conclusion_credibility, 0)
  assert.equal(left.total, undefined)
})
