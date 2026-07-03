/**
 * 订阅模板管理 API（管理后台）
 */
import request from '../utils/request'

export interface SubscriptionTemplate {
  id: string
  name: string
  slug: string
  description?: string
  category: 'academic' | 'tech' | 'trending' | 'news' | 'industry' | 'custom'
  config_template?: Record<string, any>
  suggested_exec_mode?: string
  suggested_schedule?: string
  icon?: string
  install_count?: number
  is_public?: boolean
  created_by?: string
  created_at?: string
  updated_at?: string
}

export interface TemplateCreateRequest {
  name: string
  slug: string
  description?: string
  category?: string
  config_template: Record<string, any>
  suggested_exec_mode?: string
  suggested_schedule?: string
  icon?: string
  is_public?: boolean
}

// 获取模板列表
export const getTemplates = (params?: {
  category?: string
  search?: string
  page?: number
  page_size?: number
}): Promise<{ items: SubscriptionTemplate[]; total: number }> =>
  request.get('/api/admin/subscription-templates', { params })

// 获取模板详情
export const getTemplate = (id: string): Promise<SubscriptionTemplate> =>
  request.get(`/api/admin/subscription-templates/${id}`)

// 创建模板
export const createTemplate = (data: TemplateCreateRequest): Promise<SubscriptionTemplate> =>
  request.post('/api/admin/subscription-templates', data)

// 更新模板
export const updateTemplate = (id: string, data: Partial<TemplateCreateRequest>): Promise<SubscriptionTemplate> =>
  request.put(`/api/admin/subscription-templates/${id}`, data)

// 删除模板
export const deleteTemplate = (id: string): Promise<{ message: string }> =>
  request.delete(`/api/admin/subscription-templates/${id}`)

// ==================== 内置数据源 ====================

export interface AdminBuiltinSource {
  id: string
  name: string
  description: string
  icon: string
  category: string
  type: 'fixed' | 'parameterized'
  preview_url?: string
  route_template?: string
  param_key?: string
  param_label?: string
  param_placeholder?: string
  validation_regex?: string
  validation_error?: string
  params?: Array<{
    name: string
    label: string
    placeholder: string
    validation_regex: string
    validation_error: string
  }>
  help_url?: string
  help_url_label?: string
  help_text?: string
  input_type?: 'text' | 'autocomplete'
  options?: Array<{ value: string; label: string }>
}

export interface AdminBuiltinSourcesResponse {
  categories: { value: string; label: string }[]
  sources: AdminBuiltinSource[]
  total: number
}

/** 获取内置数据源列表（管理员视图，含路由路径） */
export const getAdminBuiltinSources = (): Promise<AdminBuiltinSourcesResponse> =>
  request.get('/api/admin/subscription-templates/builtin-sources')

// ==================== 预览 & 探测 ====================

export interface SampleItem {
  title: string
  link?: string
  published?: string
  summary?: string
}

export interface PreviewSourceResponse {
  ok: boolean
  feed_title?: string
  sample_items?: SampleItem[]
  message: string
}

export interface TestSourceResponse {
  ok: boolean
  feed_title?: string
  item_count?: number
  sample_items?: SampleItem[]
  message: string
}

/** 预览 fixed 数据源的最新内容 */
export const previewAdminBuiltinSource = (
  sourceId: string
): Promise<PreviewSourceResponse> =>
  request.get(`/api/admin/subscription-templates/builtin-sources/${sourceId}/preview`)

/** 探测参数化数据源的可达性 */
export const testAdminBuiltinSource = (
  sourceId: string,
  params?: Record<string, string>
): Promise<TestSourceResponse> =>
  request.post('/api/admin/subscription-templates/builtin-sources/test', { source_id: sourceId, params })
