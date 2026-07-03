import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import request from '../utils/request'

interface UserInfo {
  id: number
  user_id: string
  username: string
  email: string
  avatar: string
  roles: { code: string; name: string }[]
}

export const useUserStore = defineStore('user', () => {
  const token = ref(localStorage.getItem('lea_token') || '')
  const userInfo = ref<UserInfo | null>(null)

  const isLoggedIn = computed(() => !!token.value)

  async function login(username: string, password: string) {
    const { data } = await request.post('/api/auth/login', { username, password })
    token.value = data.access_token
    localStorage.setItem('lea_token', data.access_token)
    await fetchUserInfo()
  }

  async function register(username: string, password: string, email: string) {
    const { data } = await request.post('/api/auth/register', { username, password, email })
    token.value = data.access_token
    localStorage.setItem('lea_token', data.access_token)
    await fetchUserInfo()
  }

  async function fetchUserInfo() {
    const { data } = await request.get('/api/auth/me')
    userInfo.value = data
  }

  function logout() {
    token.value = ''
    userInfo.value = null
    localStorage.removeItem('lea_token')
  }

  return { token, userInfo, isLoggedIn, login, register, fetchUserInfo, logout }
})
