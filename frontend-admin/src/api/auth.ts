import request from '../utils/request'

export interface LoginParams {
  username: string
  password: string
}

export interface LoginResponse {
  access_token: string
  token_type: string
}

export interface UserInfo {
  user_id: string
  username: string
  email: string | null
  avatar: string | null
  roles: string[]
  permissions: string[]
}

export interface ChangePasswordParams {
  old_password: string
  new_password: string
}

// 登录
export const login = (data: LoginParams): Promise<LoginResponse> => {
  return request.post('/api/auth/login', data)
}

// 获取当前用户信息
export const getCurrentUser = (): Promise<UserInfo> => {
  return request.get('/api/auth/me')
}

// 登出
export const logout = (): Promise<{ message: string }> => {
  return request.post('/api/auth/logout')
}

// 修改密码
export const changePassword = (data: ChangePasswordParams): Promise<{ message: string }> => {
  return request.post('/api/auth/change-password', data)
}
