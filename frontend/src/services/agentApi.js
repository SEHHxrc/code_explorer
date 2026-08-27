import { API_BASE, apiClient } from './httpClient.js'

export const createAgentRun = async (projectId, payload) => {
  const response = await apiClient.post(`/api/agent/projects/${projectId}/runs`, payload)
  return response.data.data
}

export const cancelAgentRun = async (runId) => {
  const response = await apiClient.post(`/api/agent/runs/${runId}/cancel`)
  return response.data.data
}

/** Parse bounded JSON SSE frames until the run reaches a terminal state. */
export const streamAgentEvents = async (eventsUrl, onEvent, signal) => {
  const response = await fetch(`${API_BASE}${eventsUrl}`, {
    headers: { Accept: 'text/event-stream' },
    signal,
  })
  if (!response.ok || !response.body) throw new Error(`事件流连接失败 (${response.status})`)
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
      if (data) onEvent(JSON.parse(data))
    }
    if (done) break
  }
}