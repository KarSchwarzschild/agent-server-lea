/**
 * 智能体管理API
 */
import request from '../utils/request'

// ========== 类型定义 ==========

export interface Agent {
  id: number
  agent_id: string
  name: string
  icon: string | null
  category: string
  description: string | null
  system_prompt: string

  // 基座应用信息
  jizuo_app_id: string | null
  jizuo_app_key: string | null
  jizuo_app_status: string | null
  jizuo_created_at: string | null
  jizuo_updated_at: string | null
  jizuo_sync_status: string
  jizuo_sync_error: string | null
  jizuo_last_sync_at: string | null

  // 知识库配置
  kb_ids: string[] | null

  // 其他配置
  status: string
  sort_order: number
  is_system: boolean

  // 时间戳
  created_at: string
  updated_at: string
}

export interface AgentListParams {
  page?: number
  page_size?: number
  category?: string
  status?: string
  search?: string
}

export interface AgentCreateRequest {
  agent_id: string
  name: string
  icon?: string
  category: string
  description?: string
  system_prompt: string
  kb_ids?: string[]
  sort_order?: number
  is_system?: boolean
}

export interface AgentUpdateRequest {
  name?: string
  icon?: string
  category?: string
  description?: string
  system_prompt?: string
  kb_ids?: string[]
  status?: string
  sort_order?: number
}

// 状态名称映射
export const AGENT_STATUS_MAP: Record<string, string> = {
  active: '已启用',
  disabled: '已禁用',
  deleted: '已删除'
}

// 同步状态映射
export const SYNC_STATUS_MAP: Record<string, string> = {
  pending: '待同步',
  synced: '已同步',
  failed: '同步失败'
}

// 同步状态标识
export const SYNC_STATUS_ICON: Record<string, string> = {
  pending: '⏳',
  synced: '✅',
  failed: '❌'
}

// 基座应用状态映射
export const JIZUO_APP_STATUS_MAP: Record<string, string> = {
  ACTIVE: '已激活',
  INACTIVE: '已停用',
  DRAFT: '草稿'
}

// 基座应用状态标识
export const JIZUO_APP_STATUS_ICON: Record<string, string> = {
  ACTIVE: '🟢',
  INACTIVE: '🔴',
  DRAFT: '⚪'
}

// ========== 分类管理 ==========

/**
 * 获取智能体分类列表（从系统配置动态读取）
 * @returns 分类列表，如 ["理论研究", "政策研究", "实践研究", "方法研究"]
 */
export function getAgentCategories(): Promise<string[]> {
  return request.get('/api/admin/agents/categories')
}

// ========== 智能体管理 ==========

/**
 * 获取智能体列表（分页）
 */
export const getAgents = (params?: AgentListParams): Promise<{ total: number; items: Agent[] }> => {
  return request.get('/api/admin/agents', { params })
}

/**
 * 获取智能体详情
 */
export const getAgent = (agentId: string): Promise<Agent> => {
  return request.get(`/api/admin/agents/${agentId}`)
}

/**
 * 创建智能体
 */
export const createAgent = (data: AgentCreateRequest): Promise<Agent> => {
  return request.post('/api/admin/agents', data)
}

/**
 * 更新智能体
 */
export const updateAgent = (agentId: string, data: AgentUpdateRequest): Promise<Agent> => {
  return request.put(`/api/admin/agents/${agentId}`, data)
}

/**
 * 删除智能体
 */
export const deleteAgent = (agentId: string): Promise<{ message: string }> => {
  return request.delete(`/api/admin/agents/${agentId}`)
}

/**
 * 手动同步智能体
 */
export const syncAgent = (agentId: string): Promise<{ message: string; agent: Agent }> => {
  return request.post(`/api/admin/agents/${agentId}/sync`)
}

/**
 * 批量同步结果
 */
export interface BatchSyncResult {
  agent_id: string
  name: string
  jizuo_app_status?: string
  error?: string
  reason?: string
}

/**
 * 批量同步响应
 */
export interface BatchSyncResponse {
  message: string
  results: {
    total: number
    success: BatchSyncResult[]
    failed: BatchSyncResult[]
    skipped: BatchSyncResult[]
  }
}

/**
 * 批量同步所有智能体
 */
export const batchSyncAgents = (): Promise<BatchSyncResponse> => {
  return request.post('/api/admin/agents/batch-sync')
}
