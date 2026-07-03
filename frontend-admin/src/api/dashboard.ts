import request from '../utils/request'

export interface DashboardStats {
  total_users: number
  active_users: number
  total_documents: number
  total_sessions: number
  total_messages: number
  tokens_used_today: number
  tokens_used_total: number
}

export interface RecentActivity {
  id: number
  action: string
  username: string
  resource: string
  resource_id: string | null
  created_at: string
}

export interface SystemHealth {
  status: string
  database: string
  uptime_seconds: number
}

export interface CacheInfo {
  cached: boolean
  cached_at: string | null
}

export interface DashboardData {
  stats: DashboardStats
  recent_activities: RecentActivity[]
  evaluation_trend: Record<string, number>
  system_health: SystemHealth
  cache_info: CacheInfo
}

// 获取仪表盘数据
export const getDashboard = (): Promise<DashboardData> => {
  return request.get('/api/admin/dashboard')
}

// 刷新仪表盘数据
export const refreshDashboard = (): Promise<DashboardData> => {
  return request.post('/api/admin/dashboard/refresh')
}
