/**
 * 通知中心 API 封装
 */
import request from '../utils/request'

// ==================== 类型定义 ====================

export interface Notification {
  id: string
  user_id: string
  type: 'subscription_update' | 'report_ready' | 'system'
  title: string
  summary?: string
  data?: Record<string, any>
  source_id?: string
  read_at?: string
  created_at?: string
}

// ==================== API 函数 ====================

/**
 * 获取通知列表
 */
export const getNotifications = (params?: {
  page?: number
  page_size?: number
  unread_only?: boolean
}): Promise<{ items: Notification[]; total: number; unread_count: number }> =>
  request.get('/api/user/notifications', { params })

/**
 * 获取未读通知数
 */
export const getUnreadCount = (): Promise<{ unread_count: number }> =>
  request.get('/api/user/notifications/unread-count')

/**
 * 标记单条通知已读
 */
export const markRead = (id: string): Promise<{ message: string }> =>
  request.post(`/api/user/notifications/${id}/read`)

/**
 * 全部标记已读
 */
export const markAllRead = (): Promise<{ message: string; count: number }> =>
  request.post('/api/user/notifications/read-all')
