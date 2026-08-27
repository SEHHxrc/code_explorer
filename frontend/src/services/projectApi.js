import { apiClient } from './httpClient.js'

const responseData = (response) => {
  if (!response?.data || response.data.code >= 400) {
    throw new Error(response?.data?.message || '服务器返回了无效响应')
  }
  return response.data.data
}

export const getModelStatus = async ({ signal } = {}) => (
  responseData(await apiClient.get('/api/projects/model/status', { signal }))
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