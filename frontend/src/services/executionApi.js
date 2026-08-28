import { API_BASE, apiClient } from './httpClient.js'

const dataOf = (response) => response.data.data

export const getExecutionConfiguration = async () => (
  dataOf(await apiClient.get('/api/executions/configuration'))
)

export const createExecutionTask = async (projectId, payload) => (
  dataOf(await apiClient.post('/api/executions/projects/' + projectId + '/tasks', payload))
)

export const listExecutionTasks = async (projectId) => (
  dataOf(await apiClient.get('/api/executions/projects/' + projectId + '/tasks'))
)

export const getExecutionTask = async (taskId) => (
  dataOf(await apiClient.get('/api/executions/tasks/' + taskId))
)

export const cancelExecutionTask = async (taskId) => (
  dataOf(await apiClient.post('/api/executions/tasks/' + taskId + '/cancel'))
)

/** 消费执行任务审计 SSE；服务端在任务进入终态后主动结束。 */
export const streamExecutionEvents = async (eventsUrl, onEvent, signal) => {
  const response = await fetch(API_BASE + eventsUrl, {
    headers: { Accept: 'text/event-stream' },
    signal,
  })
  if (!response.ok || !response.body) throw new Error('执行事件流连接失败 (' + response.status + ')')
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
