/**
 * 统计分析 API
 */
import request from '../utils/request'

// ==================== 类型定义 ====================

export interface TimeRange {
  start: string
  end: string
}

export interface DocumentStats {
  total: number
  by_category: Record<string, number>
  by_sub_category: Record<string, number>
  by_methodology: Record<string, number>
  by_year: Record<string, number>
  by_status: Record<string, number>
  recent_uploads: Array<{
    id: number
    title: string
    category: string | null
    created_at: string | null
  }>
  time_range: TimeRange
}

export interface UsageDataItem {
  date: string
  chats: number
  searches: number
  reviews: number
  tokens: number
}

export interface UsageStats {
  period: 'day' | 'week' | 'month'
  data: UsageDataItem[]
  summary: {
    total_chats: number
    total_searches: number
    total_reviews: number
    total_tokens: number
    avg_daily_chats: number
    avg_daily_tokens: number
  }
  time_range: TimeRange
}

export interface HotQuery {
  query: string
  count: number
  last_used: string | null
}

export interface HotQueriesResponse {
  queries: HotQuery[]
  total: number
  time_range: TimeRange
}

export interface TokenStats {
  total_used: number
  by_function: Record<string, number>
  by_user: Array<{
    username: string
    tokens: number
  }>
  daily_trend: Array<{
    date: string
    tokens: number
  }>
  time_range: TimeRange
}

export interface StatisticsOverview {
  users: {
    total: number
    active_this_week: number
  }
  documents: {
    total: number
    this_month: number
  }
  sessions: {
    total: number
    today: number
  }
  reviews: {
    total: number
    completed: number
    completion_rate: number
  }
  tokens: {
    total: number
    today: number
  }
}

// ==================== API 函数 ====================

/**
 * 获取统计概览
 */
export function getStatisticsOverview(): Promise<StatisticsOverview> {
  return request.get('/api/admin/statistics/overview')
}

/**
 * 获取文献详细统计
 */
export function getDocumentStatistics(params?: {
  start_date?: string
  end_date?: string
}): Promise<DocumentStats> {
  return request.get('/api/admin/statistics/documents', { params })
}

/**
 * 获取使用统计
 */
export function getUsageStatistics(params?: {
  start_date?: string
  end_date?: string
  granularity?: 'day' | 'week' | 'month'
}): Promise<UsageStats> {
  return request.get('/api/admin/statistics/usage', { params })
}

/**
 * 获取热门查询词
 */
export function getHotQueries(params?: {
  start_date?: string
  end_date?: string
  limit?: number
}): Promise<HotQueriesResponse> {
  return request.get('/api/admin/statistics/hot-queries', { params })
}

/**
 * 获取Token消耗分析
 */
export function getTokenStatistics(params?: {
  start_date?: string
  end_date?: string
}): Promise<TokenStats> {
  return request.get('/api/admin/statistics/tokens', { params })
}

// ==================== 工具函数 ====================

/**
 * 格式化数字（添加千位分隔符）
 */
export function formatNumber(num: number): string {
  return num.toLocaleString('zh-CN')
}

/**
 * 格式化Token数量
 */
export function formatTokens(tokens: number): string {
  if (tokens >= 1000000) {
    return (tokens / 1000000).toFixed(2) + 'M'
  } else if (tokens >= 1000) {
    return (tokens / 1000).toFixed(1) + 'K'
  }
  return tokens.toString()
}

/**
 * 获取日期范围预设
 */
export function getDateRangePresets(): Array<{
  label: string
  value: [string, string]
}> {
  const today = new Date()
  const formatDate = (d: Date) => d.toISOString().split('T')[0]

  const daysAgo = (days: number) => {
    const d = new Date(today)
    d.setDate(d.getDate() - days)
    return formatDate(d)
  }

  return [
    { label: '最近7天', value: [daysAgo(7), formatDate(today)] as [string, string] },
    { label: '最近30天', value: [daysAgo(30), formatDate(today)] as [string, string] },
    { label: '最近90天', value: [daysAgo(90), formatDate(today)] as [string, string] },
    { label: '最近一年', value: [daysAgo(365), formatDate(today)] as [string, string] }
  ]
}

/**
 * 将对象数据转换为饼图数据格式
 */
export function toPieChartData(data: Record<string, number>): Array<{
  name: string
  value: number
}> {
  return Object.entries(data)
    .filter(([, value]) => value > 0)
    .map(([name, value]) => ({ name, value }))
    .sort((a, b) => b.value - a.value)
}

/**
 * 颜色调色板
 */
export const chartColors = [
  '#5470c6', '#91cc75', '#fac858', '#ee6666', '#73c0de',
  '#3ba272', '#fc8452', '#9a60b4', '#ea7ccc', '#48b8d0'
]
