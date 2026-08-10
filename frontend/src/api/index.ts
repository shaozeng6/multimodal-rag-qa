import axios from 'axios'

// 创建 axios 实例,统一配置 baseURL 与超时时间
const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

// token 在 localStorage 中的键名
export const TOKEN_KEY = 'rag_token'

/** 读取本地存储的 JWT token */
export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

/** 写入 JWT token */
export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}

/** 清除 JWT token */
export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}

// 请求拦截: 自动注入 Authorization: Bearer {token}
api.interceptors.request.use(
  (config) => {
    const token = getToken()
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => Promise.reject(error),
)

// 响应拦截: 401 时清除凭证并跳转登录页
api.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error?.response?.status
    if (status === 401) {
      clearToken()
      // 避免在登录页重复跳转
      if (!window.location.pathname.startsWith('/login')) {
        window.location.href = '/login'
      }
    }
    return Promise.reject(error)
  },
)

export default api
