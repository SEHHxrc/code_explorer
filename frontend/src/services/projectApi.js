import { apiClient, responseData } from './httpClient.js'

export const listStoredProjects = async ({ signal } = {}) => (
  responseData(await apiClient.get('/api/projects', { signal }))
)

export const restoreStoredProject = async (projectId, { signal } = {}) => (
  responseData(await apiClient.get(`/api/projects/${projectId}/snapshot`, { signal }))
)

export const createAnalysisProgress = async ({ signal } = {}) => (
  responseData(await apiClient.post('/api/projects/analysis-progress', null, { signal, timeout: 10000 }))
)

export const getAnalysisProgress = async (requestId, { signal } = {}) => (
  responseData(await apiClient.get(`/api/projects/analysis-progress/${requestId}`, { signal, timeout: 10000 }))
)

export const analyzeGitProject = async (repoUrl, { signal, requestId } = {}) => {
  const form = new FormData()
  form.append('repo_url', repoUrl)
  if (requestId) form.append('request_id', requestId)
  return responseData(await apiClient.post('/api/projects/analyze', form, { signal }))
}

export const analyzeZipProject = async (file, { signal, requestId, onUploadProgress } = {}) => {
  const form = new FormData()
  form.append('file', file)
  if (requestId) form.append('request_id', requestId)
  return responseData(await apiClient.post('/api/projects/analyze', form, { signal, onUploadProgress }))
}

export const generateProjectOverview = async (projectId, options, { signal } = {}) => (
  responseData(await apiClient.post(`/api/projects/${projectId}/overview`, options, { signal }))
)

export const deleteProject = async (projectId, { signal } = {}) => (
  responseData(await apiClient.delete(`/api/projects/clear/${projectId}`, { signal }))
)
