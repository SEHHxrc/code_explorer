import { apiClient } from './httpClient.js'
import { consumeSse } from './sseClient.js'

export const createExperimentComparison = async (projectId, payload) => {
  const response = await apiClient.post('/api/experiments/projects/' + projectId + '/comparisons', payload)
  return response.data.data
}

export const reviewExperimentComparison = async (comparisonId, payload) => {
  const response = await apiClient.post('/api/experiments/comparisons/' + comparisonId + '/review', payload)
  return response.data.data
}

/** 消费配对实验快照 SSE；每个快照同时包含左右盲态运行。 */
export const streamExperimentComparison = (eventsUrl, onSnapshot, signal) => (
  consumeSse(eventsUrl, onSnapshot, signal, '实验事件流连接失败')
)