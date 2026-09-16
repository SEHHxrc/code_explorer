import { apiClient } from './httpClient.js'
import { consumeSse } from './sseClient.js'

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
export const streamExecutionEvents = (eventsUrl, onEvent, signal) => (
  consumeSse(eventsUrl, onEvent, signal, '执行事件流连接失败')
)