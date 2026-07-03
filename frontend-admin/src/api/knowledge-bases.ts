/**
 * 知识库API
 */
import request from '../utils/request'

export interface KnowledgeBase {
  kb_id: string
  name: string
  description: string | null
  status: string
}

export interface KnowledgeBaseListResponse {
  items: KnowledgeBase[]
}

/**
 * 获取知识库列表(供智能体配置时选择)
 */
export const getKnowledgeBases = (): Promise<KnowledgeBaseListResponse> => {
  return request.get('/api/admin/knowledge-bases')
}
