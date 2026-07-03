/**
 * 意图决策日志 API
 */

import request from '../utils/request'

// 获取意图决策日志列表
export function getIntentLogs(params: {
  session_id?: string
  user_id?: string
  skill_name?: string
  min_confidence?: number
  followup_type?: string
  agent_id?: number
  intercepted_at_stage?: string
  date_from?: string
  date_to?: string
  limit?: number
  offset?: number
}) {
  return request.get('/api/admin/intent-logs', { params })
}

// 获取日志详情
export function getIntentLogDetail(logId: number) {
  return request.get(`/api/admin/intent-logs/${logId}`)
}

// 刷新意图配置缓存
export function refreshIntentConfig() {
  return request.post('/api/admin/intent-config/refresh')
}
