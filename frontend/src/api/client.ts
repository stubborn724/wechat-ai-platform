import axios from 'axios'

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

// 响应拦截器：后端登录返回时提取 token 保存至内存
client.interceptors.response.use(
  (response) => response,
  async (error) => {
    if (error.response?.status === 401) {
      // 不清除 localStorage（如果存在其他页面使用的 token）
      // 仅清除内存中的 token（由 auth store 处理）
      window.location.href = '/login'
    }
    return Promise.reject(error)
  }
)

export default client
