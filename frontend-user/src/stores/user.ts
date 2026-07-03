import { defineStore } from 'pinia'
import { ref } from 'vue'
import { login as loginApi, getCurrentUser, type UserInfo, type LoginParams } from '../api/auth'

export const useUserStore = defineStore('user', () => {
  const token = ref<string>(localStorage.getItem('token') || '')
  const user = ref<UserInfo | null>(null)

  // 初始化用户信息
  const initUser = () => {
    const savedUser = localStorage.getItem('user')
    if (savedUser) {
      try {
        user.value = JSON.parse(savedUser)
      } catch {
        user.value = null
      }
    }
  }

  // 登录
  const login = async (params: LoginParams) => {
    const res = await loginApi(params)
    token.value = res.access_token
    localStorage.setItem('token', res.access_token)

    // 获取用户信息
    const userInfo = await getCurrentUser()
    user.value = userInfo
    localStorage.setItem('user', JSON.stringify(userInfo))

    return userInfo
  }

  // 登出
  const logout = () => {
    token.value = ''
    user.value = null
    localStorage.removeItem('token')
    localStorage.removeItem('user')
  }

  // 初始化
  initUser()

  return {
    token,
    user,
    login,
    logout
  }
})
