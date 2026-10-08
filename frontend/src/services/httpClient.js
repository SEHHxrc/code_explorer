import axios from 'axios'

export const API_BASE = import.meta.env?.VITE_API_BASE_URL || 'http://localhost:8000'

export const apiClient = axios.create({
  baseURL: API_BASE,
  timeout: 0,
})

/** Extract a safe user-facing message from an HTTP or application error. */
export const apiErrorMessage = (error, fallback = '请求失败，请稍后重试') => (
  error?.response?.data?.detail
  || error?.response?.data?.message
  || error?.message
  || fallback
)

/** Validate the common application envelope and return its data payload. */
export const responseData = (response) => {
  if (!response?.data || response.data.code >= 400) {
    throw new Error(response?.data?.message || '服务器返回了无效响应')
  }
  return response.data.data
}
