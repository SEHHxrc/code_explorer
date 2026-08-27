import { API_BASE, apiClient } from './httpClient.js'

export const createExperimentComparison = async (projectId, payload) => {
  const response = await apiClient.post('/api/experiments/projects/' + projectId + '/comparisons', payload)
  return response.data.data
}

export const reviewExperimentComparison = async (comparisonId, payload) => {
  const response = await apiClient.post('/api/experiments/comparisons/' + comparisonId + '/review', payload)
  return response.data.data
}

/** 消费配对实验快照 SSE；每个快照同时包含左右盲态运行。 */
export const streamExperimentComparison = async (eventsUrl, onSnapshot, signal) => {
  const response = await fetch(API_BASE + eventsUrl, {
    headers: { Accept: 'text/event-stream' },
    signal,
  })
  if (!response.ok || !response.body) throw new Error('实验事件流连接失败 (' + response.status + ')')
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
      if (data) onSnapshot(JSON.parse(data))
    }
    if (done) break
  }
}
