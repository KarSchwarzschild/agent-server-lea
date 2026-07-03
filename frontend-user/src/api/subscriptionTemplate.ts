/**
 * 订阅模板 API 封装
 */
import request from '../utils/request'

// ==================== 类型定义 ====================

export interface SubscriptionTemplate {
  id: string
  name: string
  slug: string
  description?: string
  category: 'academic' | 'tech' | 'trending' | 'custom'
  config_template?: Record<string, any>
  suggested_exec_mode?: string
  suggested_schedule?: string
  icon?: string
  install_count?: number
  is_public?: boolean
  created_at?: string
}

export interface TemplateInstallOptions {
  exec_mode?: string
  schedule?: string
  notification_channels?: string[]
}

export interface TemplatePreviewResult {
  template_name: string
  items: Array<Record<string, any>>
  total_sample: number
  message: string
}

// ==================== API 函数 ====================

/**
 * 获取公开模板列表
 */
export const getTemplates = (category?: string): Promise<{ items: SubscriptionTemplate[]; total: number }> =>
  request.get('/api/user/subscription-templates', { params: category ? { category } : undefined })

/**
 * 获取模板详情
 */
export const getTemplate = (slug: string): Promise<SubscriptionTemplate> =>
  request.get(`/api/user/subscription-templates/${slug}`)

/**
 * 一键安装模板
 */
export const installTemplate = (
  slug: string,
  options?: TemplateInstallOptions
): Promise<{ subscription_id: string; name: string; message: string }> =>
  request.post(`/api/user/subscription-templates/${slug}/install`, options || {})

/**
 * 预览模板（试运行）
 */
export const previewTemplate = (slug: string): Promise<TemplatePreviewResult> =>
  request.post(`/api/user/subscription-templates/${slug}/preview`)
