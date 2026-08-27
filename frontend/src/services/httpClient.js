import axios from 'axios'

export const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

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