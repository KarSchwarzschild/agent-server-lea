/**
 * 智能体API封装
 * 用户端智能体相关接口
 */
import request from '../utils/request'

/**
 * 智能体公开信息（不含敏感字段）
 */
export interface AgentPublic {
  agent_id: string
  name: string
  icon: string | null
  category: string
  description: string | null
}

/**
 * 智能体列表响应
 */
export interface AgentListResponse {
  items: AgentPublic[]
}

/**
 * 获取已启用的智能体列表
 * @param category 按分类筛选（可选）
 */
export const getAgents = (category?: string): Promise<AgentListResponse> => {
  const params = category ? { category } : undefined
  return request.get('/api/user/agents', { params })
}

/**
 * 获取智能体分类列表
 */
export const getAgentCategories = (): Promise<string[]> => {
  return request.get('/api/user/agents/categories')
}

/**
 * 获取单个智能体详情
 * @param agentId 智能体ID
 */
export const getAgent = (agentId: string): Promise<AgentPublic> => {
  return request.get(`/api/user/agents/${agentId}`)
}
