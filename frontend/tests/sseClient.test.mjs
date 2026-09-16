import test from 'node:test'
import assert from 'node:assert/strict'
import { consumeSse } from '../src/services/sseClient.js'

const streamResponse = (chunks) => ({
  ok: true,
  status: 200,
  body: new ReadableStream({
    start(controller) {
      const encoder = new TextEncoder()
      chunks.forEach((chunk) => controller.enqueue(encoder.encode(chunk)))
      controller.close()
    },
  }),
})

test('consumeSse decodes fragmented JSON frames and ignores heartbeats', async (context) => {
  context.mock.method(globalThis, 'fetch', async () => streamResponse([
    'id: 1\nevent: first\nda',
    'ta: {"sequence":1}\n\n: heartbeat\n\n',
    'id: 2\ndata: {"sequence":2}\n\n',
  ]))
  const events = []
  await consumeSse('/api/events', (event) => events.push(event))
  assert.deepEqual(events, [{ sequence: 1 }, { sequence: 2 }])
})

test('consumeSse keeps each service error label', async (context) => {
  context.mock.method(globalThis, 'fetch', async () => ({ ok: false, status: 503, body: null }))
  await assert.rejects(
    consumeSse('/api/events', () => {}, undefined, '执行事件流连接失败'),
    /执行事件流连接失败 \(503\)/,
  )
})