import { API_BASE } from './httpClient.js'

/** Consume JSON data fields from a bounded server-sent event stream. */
export const consumeSse = async (
  eventsUrl,
  onData,
  signal,
  errorLabel = '事件流连接失败',
) => {
  const response = await fetch(API_BASE + eventsUrl, {
    headers: { Accept: 'text/event-stream' },
    signal,
  })
  if (!response.ok || !response.body) throw new Error(`${errorLabel} (${response.status})`)

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { value, done } = await reader.read()
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
    const frames = buffer.split(/\r?\n\r?\n/)
    buffer = frames.pop() || ''
    for (const frame of frames) {
      if (!frame || frame.startsWith(':')) continue
      const data = frame.split(/\r?\n/)
        .filter((line) => line.startsWith('data:'))
        .map((line) => line.slice(5).trimStart())
        .join('\n')
      if (data) onData(JSON.parse(data))
    }
    if (done) break
  }
}