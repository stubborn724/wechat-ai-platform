import axios, { type AxiosRequestConfig, type InternalAxiosRequestConfig } from 'axios'

/**
 * 刷新接口的最小返回契约。
 *
 * 认证续期在请求拦截器内部完成，不能依赖 Pinia store，否则会在应用初始化阶段
 * 引入 store 的生命周期与循环依赖问题。因此只保留接口实际需要的 token 字段。
 */
interface TokenRefreshResponse {
  access_token: string
  refresh_token?: string
}

/**
 * 为 Axios 原始请求标记已重试状态，防止刷新成功但原请求仍返回 401 时无限递归。
 */
interface RetryableRequestConfig extends InternalAxiosRequestConfig {
  _authRefreshRetried?: boolean
}

/** 正在进行的刷新请求。多个 API 同时遇到 401 时必须共用它，避免 refresh token 轮换产生竞争。 */
let activeTokenRefresh: Promise<TokenRefreshResponse> | null = null

/**
 * 判断认证接口自身是否应该跳过自动续期。
 *
 * 登录凭据错误需要由登录页展示错误，而刷新凭据失效应直接结束登录态；二者都不能再发起刷新请求。
 */
function isAuthEndpoint(url?: string): boolean {
  return url?.includes('/auth/login') === true || url?.includes('/auth/refresh') === true
}

/**
 * 清理浏览器中的兼容 token，并让应用回到登录页。
 *
 * Cookie 由后端在后续登录时覆盖；这里清理 localStorage 是为了让路由守卫不再把已失效 token 当成登录态。
 */
function endExpiredSession(): void {
  localStorage.removeItem('access_token')
  localStorage.removeItem('refresh_token')

  if (window.location.pathname !== '/login') {
    window.location.assign('/login')
  }
}

/**
 * 使用 refresh token 续期，并写回新 token。
 *
 * 这里直接使用 axios 而不是本文件导出的 client，确保 refresh 接口的 401 不会再次经过响应拦截器，
 * 从而形成无限刷新循环。后端也会从 HttpOnly Cookie 读取 refresh token，localStorage 仅用于兼容旧会话。
 */
async function refreshAccessToken(): Promise<TokenRefreshResponse> {
  if (!activeTokenRefresh) {
    const refreshToken = localStorage.getItem('refresh_token')
    const refreshConfig: AxiosRequestConfig = {
      withCredentials: true,
      params: refreshToken ? { refresh_token: refreshToken } : undefined,
    }

    activeTokenRefresh = axios
      .post<TokenRefreshResponse>('/api/v1/auth/refresh', null, refreshConfig)
      .then(({ data }) => {
        localStorage.setItem('access_token', data.access_token)
        if (data.refresh_token) {
          localStorage.setItem('refresh_token', data.refresh_token)
        }
        return data
      })
      .finally(() => {
        activeTokenRefresh = null
      })
  }

  return activeTokenRefresh
}

const client = axios.create({
  baseURL: '/api/v1',
  timeout: 600000, // 10 分钟（文章自动生成可能较慢）
  // 默认携带 cookie（HttpOnly cookie 自动随请求发送）
  withCredentials: true,
})

// 请求拦截器：优先使用 cookie（HttpOnly，自动发送），
// 同时保留 Authorization header 兼容（用于 SSE token 等场景）
client.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// 响应拦截器：access token 过期时自动续期并重放原请求。
client.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config as RetryableRequestConfig | undefined

    if (error.response?.status !== 401 || !originalRequest) {
      return Promise.reject(error)
    }

    // 登录和刷新接口的 401 是业务结果，不能自动续期，也不能在登录页反复跳转。
    if (isAuthEndpoint(originalRequest.url)) {
      if (originalRequest.url?.includes('/auth/refresh')) {
        endExpiredSession()
      }
      return Promise.reject(error)
    }

    // 原请求已经用新 token 重试过仍无权限，说明不是单纯的 access token 过期。
    if (originalRequest._authRefreshRetried) {
      endExpiredSession()
      return Promise.reject(error)
    }

    originalRequest._authRefreshRetried = true
    try {
      await refreshAccessToken()
      // 请求拦截器会读取刚写入的 access token，因此无需在这里手动拼装 Authorization header。
      return client(originalRequest)
    } catch (refreshError) {
      endExpiredSession()
      return Promise.reject(refreshError)
    }
  }
)

export default client
