/**
 * 问答对话API封装
 * 支持多智能体调用
 */
import request from '../utils/request'

/**
 * Chat请求参数
 */
export interface ChatRequest {
  query: string
  session_id?: string
  agent_id?: string  // 智能体ID，不传则使用默认智能体
}

/**
 * Chat响应
 */
export interface ChatResponse {
  session_id: string
  message_id: string
  answer: string
  citations?: Array<{
    document_id: string
    content: string
    relevance: number
  }>
}

/**
 * 会话信息（含智能体）
 */
export interface Session {
  session_id: string
  title: string | null
  session_type: string
  status: string
  message_count: number
  agent_id?: string      // 智能体ID
  agent_name?: string    // 智能体名称
  agent_icon?: string    // 智能体图标
}

/**
 * 消息
 */
export interface Message {
  message_id: string
  role: 'user' | 'assistant'
  content: string
  citations?: Array<unknown>
  created_at: string | null
}

/**
 * 智能体信息（会话详情中返回）
 */
export interface SessionAgent {
  agent_id: string
  name: string
  icon: string | null
  category: string
  description: string | null
}

/**
 * 会话消息响应
 */
export interface SessionMessagesResponse {
  session_id: string
  title: string | null
  agent: SessionAgent | null
  messages: Message[]
}

/**
 * 发送消息（非流式）
 */
export const sendMessage = (data: ChatRequest): Promise<ChatResponse> => {
  return request.post('/api/user/chat', data)
}

/**
 * 获取流式Chat的URL
 */
export const getChatStreamUrl = () => {
  const baseURL = import.meta.env.VITE_API_BASE_URL || ''
  return `${baseURL}/api/user/chat/stream`
}

/**
 * 获取会话列表
 * @param params 分页和筛选参数
 */
export const getSessions = (params?: {
  page?: number
  page_size?: number
  agent_id?: string  // 按智能体筛选
}): Promise<{
  total: number
  sessions: Session[]
}> => {
  return request.get('/api/user/chat/sessions', { params })
}

/**
 * 获取会话消息
 * @param sessionId 会话ID
 */
export const getSessionMessages = (sessionId: string): Promise<SessionMessagesResponse> => {
  return request.get(`/api/user/chat/sessions/${sessionId}/messages`)
}

/**
 * 删除会话
 * @param sessionId 会话ID
 */
export const deleteSession = (sessionId: string): Promise<{ message: string }> => {
  return request.delete(`/api/user/chat/sessions/${sessionId}`)
}
