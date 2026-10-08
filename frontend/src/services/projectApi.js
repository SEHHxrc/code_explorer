import { apiClient, responseData } from './httpClient.js'

export const listStoredProjects = async ({ signal } = {}) => (
  responseData(await apiClient.get('/api/projects', { signal }))
)

export const restoreStoredProject = async (projectId, { signal } = {}) => (
  responseData(await apiClient.get(`/api/projects/${projectId}/snapshot`, { signal }))
)

export const analyzeGitProject = async (repoUrl, { signal } = {}) => {
  const form = new FormData()
  form.append('repo_url', repoUrl)
  return responseData(await apiClient.post('/api/projects/analyze', form, { signal }))
}

export const analyzeZipProject = async (file, { signal } = {}) => {
  const form = new FormData()
  form.append('file', file)
  return responseData(await apiClient.post('/api/projects/analyze', form, { signal }))
}

export const generateProjectOverview = async (projectId, options, { signal } = {}) => (
  responseData(await apiClient.post(`/api/projects/${projectId}/overview`, options, { signal }))
)

export const deleteProject = async (projectId, { signal } = {}) => (
  responseData(await apiClient.delete(`/api/projects/clear/${projectId}`, { signal }))
)
