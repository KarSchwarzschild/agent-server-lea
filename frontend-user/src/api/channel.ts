/**
 * 渠道绑定 API 封装
 */
import request from '../utils/request'

// ==================== 类型定义 ====================

export interface ChannelBinding {
  id: number
  user_id: string
  channel: string
  channel_user_id: string
  channel_user_name?: string
  is_active: boolean
  created_at: string
  updated_at?: string
}

export interface BindCodeResult {
  bind_code: string
  expires_in: number
  channel: string
}

// ==================== API 函数 ====================

/**
 * 获取用户渠道绑定列表
 */
export const getChannelBindings = (): Promise<{ items: ChannelBinding[] }> =>
  request.get('/api/user/channels/bindings')

/**
 * 生成绑定码
 */
export const generateBindCode = (channel: string): Promise<BindCodeResult> =>
  request.post('/api/user/channels/bindings/generate-code', { channel })

/**
 * 解除绑定
 */
export const removeBinding = (id: number): Promise<{ message: string }> =>
  request.delete(`/api/user/channels/bindings/${id}`)
