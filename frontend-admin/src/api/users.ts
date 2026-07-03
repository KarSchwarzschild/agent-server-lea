import request from '../utils/request'

export interface User {
  user_id: string
  username: string
  email: string | null
  phone: string | null
  avatar: string | null
  is_active: boolean
  roles: string[]
  permissions: string[]
  last_login_at: string | null
  created_at: string
}

export interface UserStats {
  total: number
  active: number
  inactive: number
  by_role: Record<string, number>
  recent_logins_24h: number
}

export interface UserQueryParams {
  page?: number
  page_size?: number
  search?: string
  role?: string
  is_active?: boolean
}

export interface CreateUserRequest {
  username: string
  email?: string
  password: string
  phone?: string
  roles?: string[]
  is_active?: boolean
}

export interface UpdateUserRequest {
  email?: string
  phone?: string
  roles?: string[]
  is_active?: boolean
}

// 角色名称映射
export const ROLE_NAME_MAP: Record<string, string> = {
  super_admin: '超级管理员',
  admin: '系统管理员',
  editor: '编辑员',
  user: '普通用户'
}

// 获取用户统计
export const getUserStats = (): Promise<UserStats> => {
  return request.get('/api/admin/users/stats')
}

// 获取用户列表
export const getUsers = (params?: UserQueryParams): Promise<{ total: number; items: User[] }> => {
  return request.get('/api/admin/users', { params })
}

// 获取用户详情
export const getUser = (userId: string): Promise<User> => {
  return request.get(`/api/admin/users/${userId}`)
}

// 创建用户
export const createUser = (data: CreateUserRequest): Promise<User> => {
  return request.post('/api/admin/users', data)
}

// 更新用户
export const updateUser = (userId: string, data: UpdateUserRequest): Promise<User> => {
  return request.put(`/api/admin/users/${userId}`, data)
}

// 删除用户
export const deleteUser = (userId: string): Promise<{ message: string }> => {
  return request.delete(`/api/admin/users/${userId}`)
}

// 重置用户密码
export const resetUserPassword = (userId: string): Promise<{ message: string; new_password: string }> => {
  return request.post(`/api/admin/users/${userId}/reset-password`)
}

// 切换用户状态
export const toggleUserStatus = (userId: string): Promise<{ message: string; is_active: boolean }> => {
  return request.post(`/api/admin/users/${userId}/toggle-status`)
}

// ========== 用户使用记录相关 ==========

export interface UsageRecord {
  id: number
  action_type: string
  action_detail: Record<string, unknown> | null
  tokens_used: number
  created_at: string
}

export interface UserUsageStats {
  total_actions: number
  chat_count: number
  search_count: number
  review_count: number
  import_count: number
  total_tokens: number
  recent_records: UsageRecord[]
}

export interface OperationRecord {
  id: number
  action: string
  resource: string | null
  resource_id: string | null
  detail: Record<string, unknown> | null
  ip_address: string | null
  status: string
  created_at: string
}

export interface UserOperationLogs {
  total: number
  items: OperationRecord[]
}

// 获取用户使用统计
export const getUserUsage = (userId: string, params?: { page?: number; page_size?: number }): Promise<UserUsageStats> => {
  return request.get(`/api/admin/users/${userId}/usage`, { params })
}

// 获取用户操作日志
export const getUserOperations = (userId: string, params?: { page?: number; page_size?: number }): Promise<UserOperationLogs> => {
  return request.get(`/api/admin/users/${userId}/operations`, { params })
}
