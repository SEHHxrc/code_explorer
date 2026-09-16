import { apiClient } from './httpClient.js'
import { consumeSse } from './sseClient.js'

export const createAgentRun = async (projectId, payload) => {
  const response = await apiClient.post(`/api/agent/projects/${projectId}/runs`, payload)
  return response.data.data
}

export const cancelAgentRun = async (runId) => {
  const response = await apiClient.post(`/api/agent/runs/${runId}/cancel`)
  return response.data.data
}

/** Parse bounded JSON SSE frames until the run reaches a terminal state. */
export const streamAgentEvents = (eventsUrl, onEvent, signal) => (
  consumeSse(eventsUrl, onEvent, signal)
)