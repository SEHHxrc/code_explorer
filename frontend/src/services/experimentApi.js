import { apiClient } from './httpClient.js'
import { consumeSse } from './sseClient.js'

/** 历史摘要按项目分页；原结果/提示词快照按需读取，完整审计权限不变。 */
export const listExperimentHistory = async (projectId, offset = 0, limit = 20) => (
  (await apiClient.get(`/api/experiments/projects/${encodeURIComponent(projectId)}/comparisons`, {
    params: { offset, limit },
  })).data.data
)

export const readExperimentHistory = async (comparisonId) => (
  (await apiClient.get(`/api/experiments/comparisons/${encodeURIComponent(comparisonId)}/history`)).data.data
)

/** 完整审计仅在管理员显式请求时读取；口令不持久化，不进入模型或 SSE。 */
export const readExperimentAudit = async (comparisonId, lane, token, acknowledgeUnblind = false) => (
  (await apiClient.get(`/api/experiments/comparisons/${comparisonId}/audit/${lane}`, {
    headers: { 'X-Audit-Token': token }, params: { acknowledge_unblind: acknowledgeUnblind },
  })).data.data
)

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
