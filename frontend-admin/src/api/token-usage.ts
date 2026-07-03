/**
 * Token 用量分析 API
 */
import request from '../utils/request'

// ==================== 类型定义 ====================

export interface TokenOverview {
  total_tokens: number
  total_calls: number
  active_users: number
  estimated_ratio: { count: number; total: number; pct: number }
  week_over_week: { tokens_pct: number; calls_pct: number; users_pct: number }
}

export interface FeatureRow {
  feature: string
  model: string
  total_tokens: number
  call_count: number
  pct: number
  estimated_count: number
}

export interface TrendRow {
  date: string
  feature: string
  total_tokens: number
  call_count: number
  estimated_count: number
}

export interface UserRow {
  user_id: string
  username: string
  total_tokens: number
  call_count: number
  session_count: number
  top_feature: string
  recent_sessions: { session_id: string; title: string; total_tokens: number; created_at: string }[]
}

export interface SessionRecord {
  feature: string
  model: string
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  is_estimated: number
  extra: string | null
  created_at: string
}

export interface SessionDetail {
  session_id: string
  user_id: string
  title: string
  total_tokens: number
  records: SessionRecord[]
}

// ==================== API 函数 ====================

export function getTokenOverview(params: {
  start_date: string
  end_date: string
  model?: string
  feature?: string
}): Promise<TokenOverview> {
  return request.get('/api/admin/token-usage/overview', { params })
}

export function getByFeature(params: {
  start_date: string
  end_date: string
  model?: string
}): Promise<FeatureRow[]> {
  return request.get('/api/admin/token-usage/by-feature', { params })
}

export function getTrend(params: {
  start_date: string
  end_date: string
  granularity?: string
  model?: string
  feature?: string
}): Promise<TrendRow[]> {
  return request.get('/api/admin/token-usage/trend', { params })
}

export function getByUser(params: {
  start_date: string
  end_date: string
  limit?: number
}): Promise<UserRow[]> {
  return request.get('/api/admin/token-usage/by-user', { params })
}

export function getSessionDetail(sessionId: string): Promise<SessionDetail> {
  return request.get(`/api/admin/token-usage/session/${sessionId}`)
}

// ==================== 工具函数 ====================

export function formatTokens(tokens: number): string {
  if (tokens >= 1000000) return (tokens / 1000000).toFixed(2) + 'M'
  if (tokens >= 1000) return (tokens / 1000).toFixed(1) + 'K'
  return tokens.toString()
}
