import axios from 'axios'
import type { AxiosInstance, InternalAxiosRequestConfig, AxiosResponse } from 'axios'
import { createDiscreteApi } from 'naive-ui'
import router from '../router'

// 创建独立的 message API（用于非组件上下文）
const { message } = createDiscreteApi(['message'])

const baseURL = import.meta.env.VITE_API_BASE_URL || ''

const request: AxiosInstance = axios.create({
  baseURL,
  timeout: 60000,
  headers: {
    'Content-Type': 'application/json'
  }
})

// 请求拦截器
request.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    const token = localStorage.getItem('token')
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => {
    return Promise.reject(error)
  }
)

// 响应拦截器
request.interceptors.response.use(
  (response: AxiosResponse) => {
    return response.data
  },
  (error) => {
    if (error.response) {
      const { status, data } = error.response

      if (status === 401) {
        localStorage.removeItem('token')
        localStorage.removeItem('user')
        router.push('/login')
        message.error('登录已过期，请重新登录')
      } else if (status === 403) {
        message.error(data.detail || '没有权限')
      } else if (status === 404) {
        message.error(data.detail || '资源不存在')
      } else if (status === 500) {
        message.error(data.detail || '服务器错误')
      } else {
        message.error(data.detail || '请求失败')
      }
    } else {
      message.error('网络错误，请检查连接')
    }
    return Promise.reject(error)
  }
)

export default request
