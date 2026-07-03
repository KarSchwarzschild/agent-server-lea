import request from '../utils/request'

// ==================== 类型定义 ====================

export interface OverviewData {
  subscriptions: {
    total: number
    enabled: number
    disabled: number
    by_category: Record<string, number>
    by_exec_mode: Record<string, number>
    empty_result_streak: number
  }
  execution: {
    total_runs: number
    runs_today: number
    failures_today: number
    total_items_found: number
  }
  webhooks: {
    total: number
    active: number
    auto_disabled: number
    by_type: Record<string, number>
    recent_push_failures: number
  }
  users_with_subscriptions: number
}

export interface SubscriptionItem {
  id: string
  user_id: string
  username?: string
  name: string
  description?: string
  category?: string
  exec_mode?: string
  is_enabled: boolean
  notification_channels: string[]
  cron_schedule?: string
  last_check_at?: string
  last_result_item_count?: number
  last_result_summary?: string
  total_runs: number
  created_at?: string
}

export interface WebhookItem {
  id: number
  user_id: string
  username?: string
  name: string
  channel_type: string
  webhook_url_masked: string
  has_secret: boolean
  is_active: boolean
  consecutive_failures: number
  last_push_at?: string
  last_push_status?: string
  created_at?: string
  bound_subscriptions_count: number
}

// ==================== API 函数 ====================

export const getOverview = (): Promise<OverviewData> =>
  request.get('/api/admin/subscription-monitor/overview')

export const getSubscriptions = (params: {
  page?: number
  page_size?: number
  user_id?: string
  category?: string
  is_enabled?: boolean
  search?: string
}): Promise<{ total: number; items: SubscriptionItem[] }> =>
  request.get('/api/admin/subscription-monitor/subscriptions', { params })

export const getWebhooks = (params: {
  page?: number
  page_size?: number
  user_id?: string
  channel_type?: string
  status?: string
  search?: string
}): Promise<{ total: number; items: WebhookItem[] }> =>
  request.get('/api/admin/subscription-monitor/webhooks', { params })

// 介入操作
export const disableWebhook = (id: number): Promise<{ message: string }> =>
  request.post(`/api/admin/subscription-monitor/webhooks/${id}/disable`)

export const toggleSubscription = (id: string, enable: boolean): Promise<{ message: string }> =>
  request.patch(`/api/admin/subscription-monitor/subscriptions/${id}/toggle`, null, { params: { enable } })

// RSSHub 状态

/** 单路由探测结果 */
export interface RSSHubProbeResult {
  path: string
  status: number
  available: boolean
  error?: string
}

/** 路由信息（可能含缓存的探测结果） */
export interface RSSHubRoute {
  path: string
  label: string
  category: string
  probe?: RSSHubProbeResult
}

/** 路由列表 + 配置信息 */
export interface RSSHubRoutesResponse {
  configured: boolean
  base_url: string
  has_access_key: boolean
  routes: RSSHubRoute[]
  has_cache: boolean
  probed_at: string | null  // ISO 时间字符串
}

/** 获取 RSSHub 静态路由列表（瞬间返回，不探测） */
export const getRSSHubRoutes = (): Promise<RSSHubRoutesResponse> =>
  request.get('/api/admin/subscription-monitor/rsshub/routes')

/** 探测单个路由的可用性 */
export const probeRSSHubRoute = (path: string): Promise<RSSHubProbeResult> =>
  request.post('/api/admin/subscription-monitor/rsshub/probe-route', { path })

/** 批量保存探测结果到 DB（检测完毕后调一次） */
export const saveProbeResults = (results: Record<string, RSSHubProbeResult>): Promise<{ message: string; count: number }> =>
  request.post('/api/admin/subscription-monitor/rsshub/save-probe-results', { results })
