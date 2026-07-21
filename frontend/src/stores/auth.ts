import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import client from '@/api/client'
import type { User, LoginResponse } from '@/api/types'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)

  const isAuthenticated = computed(() => !!user.value)

  async function login(email: string, password: string) {
    const { data } = await client.post<LoginResponse>('/auth/login', { email, password })
    localStorage.setItem('access_token', data.access_token)
    if (data.refresh_token) {
      localStorage.setItem('refresh_token', data.refresh_token)
    }
    user.value = data.user
  }

  async function logout() {
    try {
      const refreshToken = localStorage.getItem('refresh_token')
      if (refreshToken) {
        await client.post('/auth/logout', { refresh_token: refreshToken })
      }
    } catch {
      // Local logout must still succeed when the API is unavailable
    }
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
      localStorage.removeItem('access_token')
      localStorage.removeItem('refresh_token')
      user.value = null
      return null
    }
  }

  return { user, isAuthenticated, login, logout, loadUser }
})
