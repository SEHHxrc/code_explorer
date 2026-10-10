import assert from 'node:assert/strict'
import test from 'node:test'
import { setTimeout as delay } from 'node:timers/promises'
import { fileProgressPercent, pollImportProgress } from '../src/features/project-insight/utils/importProgress.js'

test('file percent is bounded and unknown totals do not claim completion', () => {
  assert.equal(fileProgressPercent({ total_files: 0 }), null)
  assert.equal(fileProgressPercent({ total_files: 20, processed_files: 3 }), 15)
  assert.equal(fileProgressPercent({ total_files: 1, processed_files: 2 }), 100)
})

test('polls serially, retries errors, and stops at a terminal stage', async () => {
  let calls = 0
  let inFlight = 0
  let errors = 0
  const stages = []
  let finish
  const finished = new Promise((resolve) => { finish = resolve })
  const stop = pollImportProgress({
    requestId: 'request-1', interval: 1,
    getProgress: async (id) => {
      assert.equal(id, 'request-1')
      assert.equal(++inFlight, 1)
      await delay(3)
      inFlight--
      if (++calls === 1) throw new Error('temporary connection failure')
      return { status: calls === 3 ? 'completed' : 'running' }
    },
    onError: () => { errors++ },
    onProgress: (progress) => {
      stages.push(progress.status)
      if (progress.status === 'completed') finish()
    },
  })
  try {
    await finished
    await delay(10)
    assert.equal(calls, 3)
    assert.equal(errors, 1)
    assert.deepEqual(stages, ['running', 'completed'])
  } finally { stop() }
})

test('stop aborts only its own GET and suppresses late responses', async () => {
  let resolveRequest
  let pollSignal
  let updates = 0
  const postController = new AbortController()
  const stop = pollImportProgress({
    requestId: 'request-1',
    getProgress: (_, { signal }) => {
      pollSignal = signal
      return new Promise((resolve) => { resolveRequest = resolve })
    },
    onProgress: () => { updates++ }, onError: () => { updates++ },
  })
  stop()
  resolveRequest({ status: 'running' })
  await delay(5)
  assert.equal(pollSignal.aborted, true)
  assert.equal(postController.signal.aborted, false)
  assert.equal(updates, 0)
})
