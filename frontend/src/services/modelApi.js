import { apiClient, responseData } from './httpClient.js'

export const getModelStatus = async ({ signal } = {}) => (
  responseData(await apiClient.get('/api/models/status', { signal }))
)

export const probeModelConnection = async (model, { signal } = {}) => (
  responseData(await apiClient.post('/api/models/probe', { model: model || null }, { signal }))
)

export const getAvailableModels = async ({ signal } = {}) => (
  responseData(await apiClient.get('/api/models', { signal }))
)
