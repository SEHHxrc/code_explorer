import { apiClient, responseData } from './httpClient.js'

/** 格式化公开配置或历史预算；空值不冒充零上限，旧记录不推断为无限。 */
export const formatOutputBudget = (budget = {}) => {
  if (Number.isInteger(budget.max_output_tokens) && budget.max_output_tokens > 0) {
    return `输出上限 ${budget.max_output_tokens.toLocaleString()} tokens`
  }
  if (budget.output_limit_mode !== 'provider_default') return '输出上限未记录'
  const reserve = Number.isInteger(budget.output_reserve_tokens) && budget.output_reserve_tokens > 0
    ? ` · 本地预留 ${budget.output_reserve_tokens.toLocaleString()} tokens（仅规划）` : ''
  return `输出上限未指定（平台默认，非无限）${reserve}`
}

export const getModelStatus = async ({ signal } = {}) => (
  responseData(await apiClient.get('/api/models/status', { signal }))
)

export const probeModelConnection = async (model, { signal } = {}) => (
  responseData(await apiClient.post('/api/models/probe', { model: model || null }, { signal }))
)

export const getAvailableModels = async ({ signal } = {}) => (
  responseData(await apiClient.get('/api/models', { signal }))
)
