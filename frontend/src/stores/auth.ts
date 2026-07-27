import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import client from '@/api/client'
import type { User, LoginResponse } from '@/api/types'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  // token 仅存内存（不写 localStorage），HttpOnly cookie 由后端自动管理
  const accessToken = ref<string | null>(null)

  const isAuthenticated = computed(() => !!user.value)

  async function login(email: string, password: string) {
    const { data } = await client.post<LoginResponse>('/auth/login', { email, password })
    // 后端同时设置 HttpOnly cookie，这里存内存用于需要 Authorization header 的场景
    accessToken.value = data.access_token
    // 保持向后兼容（部分地方读 localStorage）
    localStorage.setItem('access_token', data.access_token)
    if (data.refresh_token) {
      localStorage.setItem('refresh_token', data.refresh_token)
    }
    user.value = data.user
  }

  async function logout() {
    try {
      await client.post('/auth/logout')
    } catch {
      // Local logout must still succeed when the API is unavailable
    }
    accessToken.value = null
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
    user.value = null
    window.location.href = '/login'
  }

  async function loadUser() {
    try {
      const { data } = await client.get<User>('/auth/me')
      user.value = data
      return data
    } catch {
      accessToken.value = null
      user.value = null
      return null
    }
  }

  return { user, accessToken, isAuthenticated, login, logout, loadUser }
})
