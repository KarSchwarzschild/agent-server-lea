/**
 * 智能订阅 API 封装
 */
import request from '../utils/request'
import type { Reference } from '@/types/super-agent'

// ==================== 类型定义 ====================

export interface Subscription {
  id: string
  user_id: string
  name: string
  description?: string
  category: 'academic' | 'tech' | 'trending' | 'custom'
  config?: SubscriptionConfig
  exec_mode: 'quick' | 'deep' | 'hybrid'
  cron_job_id?: string
  is_enabled: boolean
  notification_channels?: string[]
  has_feed?: boolean
  last_check_at?: string
  created_at?: string
  updated_at?: string
}

export interface SubscriptionConfig {
  tools?: Array<{ name: string; params: Record<string, any> }>
  keywords?: string[]
  sources?: string[]
  [key: string]: any
}

export interface ToolCallDetail {
  name: string
  arguments: Record<string, any>
  duration_ms: number
  error?: string
  result_preview?: string
  output_length?: number
}

export interface SubscriptionResult {
  id: string
  subscription_id: string
  run_at?: string
  exec_mode: string
  items?: Array<Record<string, any>>
  item_count: number
  summary?: string
  report?: string
  snapshot_hash?: string
  created_at?: string
  references_data?: Reference[]
  tool_calls_detail?: ToolCallDetail[]
  execution_time_ms?: number
}

export interface ParseResult {
  name: string
  category: string
  tools: Array<Record<string, any>>
  keywords: string[]
  suggested_schedule?: string
  suggested_time?: string
  suggested_exec_mode: string
}

// ==================== API 函数 ====================

/**
 * 获取订阅列表
 */
export const getSubscriptions = (): Promise<{ items: Subscription[]; total: number }> =>
  request.get('/api/user/subscriptions')

/**
 * 获取订阅详情
 */
export const getSubscription = (id: string): Promise<{ subscription: Subscription }> =>
  request.get(`/api/user/subscriptions/${id}`)

/**
 * 创建订阅（自然语言模式）
 */
export const createSubscription = (data: {
  description: string
  exec_mode?: string
  schedule?: string
  schedule_time?: string
}): Promise<{ message: string; subscription: Subscription }> =>
  request.post('/api/user/subscriptions', data)

/**
 * 创建订阅（已解析配置模式）
 */
export const createSubscriptionFromConfig = (data: {
  name: string
  description?: string
  category?: string
  config: Record<string, any>
  exec_mode?: string
  schedule?: string
  schedule_time?: string
  notification_channels?: string[]
}): Promise<{ message: string; subscription: Subscription }> =>
  request.post('/api/user/subscriptions/from-config', data)

/**
 * 更新订阅
 */
export const updateSubscription = (id: string, data: {
  name?: string
  description?: string
  category?: string
  config?: Record<string, any>
  exec_mode?: string
  is_enabled?: boolean
  schedule?: string
  schedule_time?: string
  notification_channels?: string[]
}): Promise<{ message: string; subscription: Subscription }> =>
  request.put(`/api/user/subscriptions/${id}`, data)

/**
 * 删除订阅
 */
export const deleteSubscription = (id: string): Promise<{ message: string }> =>
  request.delete(`/api/user/subscriptions/${id}`)

/**
 * 手动触发订阅检查
 */
export const triggerSubscription = (id: string): Promise<Record<string, any>> =>
  request.post(`/api/user/subscriptions/${id}/trigger`)

/**
 * AI 解析自然语言（预览）
 */
export const parseSubscription = (description: string): Promise<ParseResult> =>
  request.post('/api/user/subscriptions/parse', { description })

/**
 * 获取订阅结果历史
 */
export const getSubscriptionResults = (
  id: string,
  params?: { page?: number; page_size?: number }
): Promise<{ items: SubscriptionResult[]; total: number }> =>
  request.get(`/api/user/subscriptions/${id}/results`, { params })

/**
 * 获取单次结果详情
 */
export const getSubscriptionResult = (
  subId: string,
  resultId: string
): Promise<{ result: SubscriptionResult }> =>
  request.get(`/api/user/subscriptions/${subId}/results/${resultId}`)

/**
 * 重新生成 Feed Token
 */
export const regenerateFeedToken = (subId: string): Promise<{
  feed_token: string
  rss_url: string
  json_url: string
  message: string
}> => request.post(`/api/user/subscriptions/${subId}/regenerate-feed-token`)

// ==================== 内置数据源 ====================

export interface BuiltinSource {
  id: string
  name: string
  description: string
  icon: string
  category: string
  type: 'fixed' | 'parameterized'
  enabled?: boolean
  param_label?: string
  param_placeholder?: string
  param_key?: string
  validation_regex?: string
  validation_error?: string
  input_type?: 'text' | 'autocomplete'
  options?: Array<{ value: string; label: string }>
  help_url?: string
  help_url_label?: string
  help_text?: string
}

export interface BuiltinSourcesResponse {
  categories: { value: string; label: string }[]
  sources: BuiltinSource[]
}

export interface SourceSelection {
  source_id: string
  params?: Record<string, string>
}

export interface ResolveSourcesResponse {
  tools: Array<{
    name: string
    params: Record<string, any>
    _source_id?: string
    _source_params?: Record<string, string>
  }>
  source_labels: string[]
  suggested_name: string
  suggested_category: string
}

export interface SampleItem {
  title: string
  link?: string
  published?: string
  summary?: string
}

export interface TestSourceResponse {
  ok: boolean
  feed_title?: string
  item_count?: number
  sample_items?: SampleItem[]
  message: string
}

export interface PreviewSourceResponse {
  ok: boolean
  feed_title?: string
  sample_items?: SampleItem[]
  message: string
}

/**
 * 获取内置数据源列表
 */
export const getBuiltinSources = (category?: string): Promise<BuiltinSourcesResponse> =>
  request.get('/api/user/subscriptions/builtin-sources', { params: category ? { category } : undefined })

/**
 * 将已选数据源解析为工具配置
 */
export const resolveBuiltinSources = (
  selections: SourceSelection[],
  keywords?: string[]
): Promise<ResolveSourcesResponse> =>
  request.post('/api/user/subscriptions/resolve-sources', { selections, keywords })

/**
 * 预览 fixed 数据源的最新内容
 */
export const previewBuiltinSource = (
  source_id: string
): Promise<PreviewSourceResponse> =>
  request.get(`/api/user/subscriptions/builtin-sources/${source_id}/preview`)

/**
 * 测试单个数据源可用性
 */
export const testBuiltinSource = (
  source_id: string,
  params?: Record<string, string>
): Promise<TestSourceResponse> =>
  request.post('/api/user/subscriptions/builtin-sources/test', { source_id, params })
