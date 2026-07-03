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
  email: string
  role: string
}

// 登录
export const login = (data: LoginParams): Promise<LoginResponse> => {
  return request.post('/api/auth/login', data)
}

// 注册
export const register = (data: { username: string; password: string; email?: string }): Promise<UserInfo> => {
  return request.post('/api/auth/register', data)
}

// 获取当前用户信息
export const getCurrentUser = (): Promise<UserInfo> => {
  return request.get('/api/auth/me')
}
