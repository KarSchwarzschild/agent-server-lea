import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { login as loginApi, getCurrentUser, logout as logoutApi, type UserInfo, type LoginParams } from '../api/auth'

export const useUserStore = defineStore('user', () => {
  const token = ref<string>(localStorage.getItem('token') || '')
  const user = ref<UserInfo | null>(null)

  // 计算属性
  const isLoggedIn = computed(() => !!token.value && !!user.value)
  const roles = computed(() => user.value?.roles || [])
  const permissions = computed(() => user.value?.permissions || [])

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

  // 检查是否有某个权限
  const hasPermission = (permission: string): boolean => {
    // 超级管理员拥有所有权限
    if (roles.value.includes('super_admin')) {
      return true
    }
    return permissions.value.includes(permission)
  }

  // 检查是否有任一权限
  const hasAnyPermission = (perms: string[]): boolean => {
    if (roles.value.includes('super_admin')) {
      return true
    }
    return perms.some(p => permissions.value.includes(p))
  }

  // 检查是否有某个角色
  const hasRole = (role: string): boolean => {
    return roles.value.includes(role)
  }

  // 检查是否有任一角色
  const hasAnyRole = (roleList: string[]): boolean => {
    return roleList.some(r => roles.value.includes(r))
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
  const logout = async () => {
    try {
      await logoutApi()
    } catch {
      // 忽略登出API错误
    }
    token.value = ''
    user.value = null
    localStorage.removeItem('token')
    localStorage.removeItem('user')
  }

  // 刷新用户信息
  const refreshUser = async () => {
    if (!token.value) return
    try {
      const userInfo = await getCurrentUser()
      user.value = userInfo
      localStorage.setItem('user', JSON.stringify(userInfo))
    } catch {
      // 如果获取失败，可能token过期
      logout()
    }
  }

  // 初始化
  initUser()

  return {
    token,
    user,
    isLoggedIn,
    roles,
    permissions,
    hasPermission,
    hasAnyPermission,
    hasRole,
    hasAnyRole,
    login,
    logout,
    refreshUser
  }
})
