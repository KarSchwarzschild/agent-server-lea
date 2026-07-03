/**
 * Webhook 群机器人 API 封装
 */
import request from '../utils/request'

// ==================== 类型定义 ====================

export interface Webhook {
  id: number
  name: string
  channel_type: 'feishu_webhook' | 'dingtalk_webhook' | 'wework_webhook' | 'telegram_bot' | 'email'
  webhook_url_masked: string
  has_secret: boolean
  is_active: boolean
  consecutive_failures: number
  extra_config?: Record<string, any>
  last_push_at?: string
  last_push_status?: 'success' | 'failed'
  created_at?: string
  updated_at?: string
}

export interface WebhookCreateData {
  name: string
  channel_type: string
  webhook_url?: string
  webhook_secret?: string
  extra_config?: Record<string, any>
}

export interface WebhookUpdateData {
  name?: string
  webhook_url?: string
  webhook_secret?: string
  extra_config?: Record<string, any>
  is_active?: boolean
}

export interface WebhookTestResult {
  success: boolean
  message: string
}

// ==================== API 函数 ====================

/**
 * 获取用户 Webhook 群机器人列表
 */
export const getWebhooks = (): Promise<{ items: Webhook[]; total: number }> =>
  request.get('/api/user/webhooks')

/**
 * 添加 Webhook 群机器人
 */
export const createWebhook = (data: WebhookCreateData): Promise<Webhook> =>
  request.post('/api/user/webhooks', data)

/**
 * 更新 Webhook 群机器人
 */
export const updateWebhook = (id: number, data: WebhookUpdateData): Promise<Webhook> =>
  request.put(`/api/user/webhooks/${id}`, data)

/**
 * 删除 Webhook 群机器人
 */
export const deleteWebhook = (id: number): Promise<{ message: string }> =>
  request.delete(`/api/user/webhooks/${id}`)

/**
 * 发送测试消息
 */
export const testWebhook = (id: number): Promise<WebhookTestResult> =>
  request.post(`/api/user/webhooks/${id}/test`)
