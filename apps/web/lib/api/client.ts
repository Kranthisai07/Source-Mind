import axios, { type InternalAxiosRequestConfig } from 'axios'
import { ApiError } from '@/lib/types'

// Token getter injected at runtime by the auth hook
let _getToken: (() => Promise<string | null>) | null = null

export function setTokenGetter(fn: () => Promise<string | null>) {
  _getToken = fn
}

export const apiClient = axios.create({
  baseURL: typeof window !== 'undefined' ? '' : (process.env.API_URL ?? 'http://localhost:8000'),
  headers: { 'Content-Type': 'application/json' },
  timeout: 30_000,
})

// Request interceptor: attach Clerk JWT + idempotency key
apiClient.interceptors.request.use(async (config: InternalAxiosRequestConfig) => {
  if (_getToken) {
    const token = await _getToken()
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
  }

  // Idempotency key for mutations
  const method = config.method?.toLowerCase()
  if (method && ['post', 'patch', 'delete'].includes(method)) {
    if (!config.headers['Idempotency-Key']) {
      config.headers['Idempotency-Key'] = crypto.randomUUID()
    }
  }

  return config
})

// Response interceptor: unwrap envelope { data: T } or pass through
apiClient.interceptors.response.use(
  (response) => {
    // Backend returns { data: T, meta: {...} } or plain objects
    if (response.data && 'data' in response.data && response.data.data !== undefined) {
      return { ...response, data: response.data.data }
    }
    return response
  },
  (error) => {
    if (error.response) {
      const smError = error.response.data?.error
      const code = smError?.code ?? 'UNKNOWN'
      const message = smError?.message ?? error.message ?? 'An error occurred'
      const status = error.response.status
      throw new ApiError(code, message, status)
    }
    if (error.code === 'ECONNABORTED' || error.code === 'ERR_NETWORK') {
      throw new ApiError('NETWORK_ERROR', 'Connection failed — check your API server')
    }
    throw error
  }
)

export default apiClient
