/**
 * 超级智能体 API 封装
 * 支持流式对话、Skill执行、多模型辩论
 */
import request from '../utils/request'

// ==================== 类型定义 ====================

/**
 * Skill定义
 */
export interface Skill {
  name: string
  display_name: string
  description: string
  icon: string
  triggers: string[]
  tools: string[]
  enabled: boolean
}

/**
 * 超级智能体会话
 */
export interface SuperAgentSession {
  session_id: string
  user_id: string
  title: string | null
  status: string
  agent_id: number | null
  agent_name: string | null
  created_at: string
  updated_at: string
  message_count: number
  last_intent?: string
}

/**
 * 超级智能体消息
 */
export interface SuperAgentMessage {
  message_id: string
  role: 'user' | 'assistant'
  content: string
  intent?: string
  skill_name?: string
  created_at: string
}

/**
 * 会话详情响应
 */
export interface SessionDetailResponse {
  session_id: string
  title: string | null
  messages: SuperAgentMessage[]
}

/**
 * SSE事件类型
 */
export type SSEEventType =
  | 'thinking'       // 思考中
  | 'intent'         // 意图识别结果
  | 'skill_start'    // Skill开始执行
  | 'tool_call'      // 工具调用
  | 'tool_result'    // 工具执行结果
  | 'action'         // 工具调用开始（增强版）
  | 'observation'    // 工具调用完成（增强版）
  | 'content'        // 内容输出
  | 'answering'      // 开始生成答案
  | 'skill_done'     // Skill执行完成
  | 'debate_created' // 辩论会话创建
  | 'debate_done'    // 辩论完成
  | 'references'     // 学术引用数据
  | 'error'          // 错误
  // 🆕 子代理编排事件
  | 'sub_agent_start'       // 子代理开始
  | 'sub_agent_thinking'    // 子代理思考
  | 'sub_agent_action'      // 子代理工具调用
  | 'sub_agent_observation'  // 子代理工具完成
  | 'sub_agent_content'     // 子代理内容输出
  | 'sub_agent_complete'    // 子代理完成
  | 'sub_agent_error'       // 子代理错误
  | 'sub_agent_tool_call_start'  // 子代理工具调用开始（流式参数）
  | 'sub_agent_tool_call_delta'  // 子代理工具参数增量
  | 'sub_agent_answering'        // 子代理开始生成答案
  // 🆕 Phase 2: 后端事件增强
  | 'iteration_start'       // 迭代开始（含 model 信息）
  | 'llm_usage'             // Token 使用量
  // 🆕 Phase 4: 流式工具参数
  | 'tool_call_start'       // 工具调用开始（流式参数开始）
  | 'tool_call_delta'       // 工具参数增量
  // 会话与控制事件
  | 'session_created'       // 会话创建
  | 'round_start'           // 辩论轮次开始
  | 'round_end'             // 辩论轮次结束
  | 'final_summary'         // 最终总结
  | 'reasoning_chunk'       // 推理过程增量
  | 'reasoning_done'        // 推理过程完成
  | 'done'                  // 流式结束
  | 'heartbeat'             // 心跳保活
  | 'cancelled'             // 用户停止
  // 🆕 Client Tools: 后端请求前端执行工具
  | 'client_tool'           // 客户端工具请求
  // 🆕 Plan Mode: 计划审批
  | 'plan_created'          // 计划创建完成
  | 'plan_submitted'        // 等待用户审批
  | 'plan_approved'         // 用户批准
  | 'plan_rejected'         // 用户拒绝
  | 'plan_step'             // 步骤状态变更
  | 'plan_completed'        // 计划执行完成
  // 并行编排 + 质量验证
  | 'parallel_decompose'    // 并行子任务分解结果
  | 'parallel_progress'     // 并行执行进度
  | 'verification'          // 输出质量验证结果
  | 'parallel_subtask_scope_retry'  // 子任务范围重试
  | 'parallel_subtask_content_reset' // 子任务内容重置（重试前清空旧内容）
  | 'parallel_diagnosing'           // 并行质量诊断中
  | 'diagnostic_escalation'         // 诊断升级（报告）
  | 'peer_review'                   // 同行评审结果
  // PageIndex 索引状态
  | 'pageindex_status'              // 附件索引状态更新
  | 'memory_context'                // 长期记忆上下文
  // Aggregation@N 聚合增强
  | 'aggregation_progress'          // 聚合进度（N路搜索状态 + 合成进度）
  | 'synthesis_content'             // 聚合合成流式内容

/**
 * SSE事件数据
 */
export interface SSEEventData {
  type: SSEEventType
  text?: string
  message?: string
  code?: string
  skill?: string
  display_name?: string
  description?: string
  success?: boolean
  execution_time_ms?: number
  tool?: string
  input?: Record<string, unknown>
  result?: string
  confidence?: number
  skill_name?: string
  debate_session_id?: string
  experts?: Expert[]
  total_rounds?: number
  // 🆕 子代理事件字段
  sub_skill?: string
  task?: string
  depth?: number
  summary?: string
  error?: string
  duration_ms?: number
  // 🆕 Phase 2: model 和 token 字段
  model?: string
  prompt_tokens?: number
  completion_tokens?: number
  total_tokens?: number
  // 🆕 Phase 4: 流式工具参数字段
  arguments_delta?: string
  // 🆕 Phase 4: 取消事件字段
  reason?: string
  // 🆕 Phase 7: Client Tool 字段
  tool_use_id?: string
  tool_name?: string
  tool_label?: string
  tool_input?: Record<string, unknown>
  // 🆕 Phase 8: Plan Mode 字段
  plan_id?: string
  title?: string
  steps?: Array<{ id: string; title: string; description: string; status?: string; output?: string }>
  step_id?: string
  feedback?: string
  // 通用扩展字段
  [key: string]: unknown
}

// ==================== 辩论相关类型 ====================

/**
 * 专家信息
 */
export interface Expert {
  id: string
  name: string
  title: string
  category: string
  perspective: string
  avatar?: string
  reasoning?: string  // 🆕 思考过程（DeepSeek-R1等模型）
}

/**
 * 辩论会话
 */
export interface DebateSession {
  session_id: string
  topic: string
  status: 'created' | 'in_progress' | 'paused' | 'completed'
  experts: Expert[]
  current_round: number
  max_rounds: number
  created_at: string
}

/**
 * 辩论消息
 */
export interface DebateMessage {
  message_id: string
  role: 'coordinator' | 'expert'
  expert_id?: string
  expert_name?: string
  content: string
  round: number
  created_at: string
}

/**
 * 辩论详情响应
 */
export interface DebateDetailResponse {
  session_id: string
  topic: string
  status: string
  experts: Expert[]
  current_round: number
  max_rounds: number
  messages: DebateMessage[]
}

// ==================== API函数 ====================

/**
 * 获取超级智能体流式对话URL
 */
export const getSuperAgentStreamUrl = (): string => {
  const baseURL = import.meta.env.VITE_API_BASE_URL || ''
  return `${baseURL}/api/user/super-agent/chat/stream`
}

/**
 * 获取可用的Skills列表
 * 后端直接返回数组
 */
export const getSkills = (): Promise<Skill[]> => {
  return request.get('/api/user/super-agent/skills')
}

/**
 * 获取会话列表
 * 后端直接返回数组
 *
 * @param agent_id - 助手筛选: 不传=全部, 0=默认助手, N=指定助手
 */
export const getSessions = (params?: {
  status?: string
  agent_id?: number
  limit?: number
  offset?: number
}): Promise<SuperAgentSession[]> => {
  return request.get('/api/user/super-agent/sessions', { params })
}

/**
 * 创建新会话
 */
export const createSession = (data?: {
  title?: string
}): Promise<SuperAgentSession> => {
  return request.post('/api/user/super-agent/sessions', data || {})
}

/**
 * 获取会话详情（含消息历史）
 */
export const getSessionDetail = (sessionId: string): Promise<SessionDetailResponse> => {
  return request.get(`/api/user/super-agent/sessions/${sessionId}`)
}

/**
 * 删除会话
 */
export const deleteSession = (sessionId: string): Promise<{ message: string }> => {
  return request.delete(`/api/user/super-agent/sessions/${sessionId}`)
}

/**
 * 健康检查
 */
export const healthCheck = (): Promise<{
  status: string
  skills_count: number
  version: string
}> => {
  return request.get('/api/user/super-agent/health')
}

/**
 * 停止正在执行的会话
 */
export const stopSession = (sessionId: string): Promise<{ success: boolean; session_id: string }> => {
  return request.post(`/api/user/super-agent/sessions/${sessionId}/stop`)
}

/**
 * 提交客户端工具执行结果
 */
export const submitToolResult = (data: {
  tool_use_id: string
  content: string
  is_error?: boolean
}): Promise<{ message: string }> => {
  return request.post('/api/user/super-agent/tools/result', data)
}

/**
 * 计划步骤输入（用于编辑后提交）
 */
export interface PlanStepInput {
  title: string
  description: string
}

/**
 * 批准计划（可选：附带修改后的步骤）
 */
export const approvePlan = (
  planId: string,
  steps?: PlanStepInput[]
): Promise<{ message: string; plan_id: string }> => {
  return request.post(`/api/user/super-agent/plans/${planId}/approve`, {
    steps: steps || null
  })
}

/**
 * 拒绝计划
 */
export const rejectPlan = (planId: string, feedback?: string): Promise<{ message: string; plan_id: string }> => {
  return request.post(`/api/user/super-agent/plans/${planId}/reject`, { feedback: feedback || '' })
}

// ==================== 文件管理API（v2.0 - 使用文件ID） ====================

/**
 * 文件大小阈值：10MB
 * 小于此值使用 Blob 下载，大于等于此值使用 Content-Disposition
 */
const FILE_SIZE_THRESHOLD = 10 * 1024 * 1024

/**
 * 生成的文件信息（v2 - 使用文件ID）
 */
export interface GeneratedFileV2 {
  id: string           // 文件ID（格式：f_xxxxxxxxxxxxxxxx）
  filename: string     // 原始文件名
  size: number         // 文件大小（字节）
  file_type: string    // 文件类型（md/pdf/docx等）
  created_at: string   // 创建时间
  download_count: number // 下载次数
}

/**
 * [已废弃] 旧版文件信息（兼容）
 * @deprecated 请使用 GeneratedFileV2
 */
export interface GeneratedFile {
  filename: string
  size: number
  created_at: string
  download_url: string
}

/**
 * 获取生成的文件列表（v2）
 */
export const getGeneratedFiles = (): Promise<GeneratedFileV2[]> => {
  return request.get('/api/user/super-agent/files')
}

/**
 * 获取文件下载URL（v2 - 使用文件ID）
 */
export const getFileDownloadUrl = (fileId: string): string => {
  const baseURL = import.meta.env.VITE_API_BASE_URL || ''
  return `${baseURL}/api/user/super-agent/files/${encodeURIComponent(fileId)}/download`
}

/**
 * 删除生成的文件（v2 - 使用文件ID）
 */
export const deleteGeneratedFile = (fileId: string): Promise<{ message: string }> => {
  return request.delete(`/api/user/super-agent/files/${encodeURIComponent(fileId)}`)
}

/**
 * 下载文件（v2 - 混合策略）
 *
 * - 小于 10MB：使用 Blob 下载（内存中处理）
 * - 大于等于 10MB：使用 Content-Disposition 直接跳转
 *
 * @param file 文件信息
 * @param token 认证令牌
 */
export const downloadFile = async (
  file: GeneratedFileV2,
  token: string
): Promise<void> => {
  const downloadUrl = getFileDownloadUrl(file.id)

  if (file.size < FILE_SIZE_THRESHOLD) {
    // 小文件：Blob 下载
    try {
      const response = await fetch(downloadUrl, {
        method: 'GET',
        headers: {
          'Authorization': `Bearer ${token}`
        }
      })

      if (!response.ok) {
        throw new Error(`下载失败: ${response.status}`)
      }

      const blob = await response.blob()
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = file.filename
      document.body.appendChild(a)
      a.click()
      window.URL.revokeObjectURL(url)
      document.body.removeChild(a)
    } catch (error) {
      console.error('Blob下载失败，降级到直接跳转:', error)
      // 降级到直接跳转
      window.open(downloadUrl, '_blank')
    }
  } else {
    // 大文件：直接跳转（利用 Content-Disposition）
    // 创建隐藏的 iframe 来处理下载
    const iframe = document.createElement('iframe')
    iframe.style.display = 'none'
    iframe.src = downloadUrl
    document.body.appendChild(iframe)

    // 3秒后移除 iframe
    setTimeout(() => {
      document.body.removeChild(iframe)
    }, 3000)
  }
}

/**
 * 从下载链接中提取文件ID
 * 支持格式：/api/user/super-agent/files/{file_id}/download
 */
export const extractFileIdFromUrl = (url: string): string | null => {
  const match = url.match(/\/files\/(f_[a-f0-9]{16})\/download/)
  return match ? match[1] : null
}

// ==================== 文件预览和转换API（v2.0新增） ====================

/**
 * 文件内容响应
 */
export interface FileContentResponse {
  id: string
  filename: string
  file_type: string
  content: string
  size: number
  encoding: string
}

/**
 * 转PDF响应
 */
export interface ConvertToPdfResponse {
  success: boolean
  pdf_file: GeneratedFileV2
  conversion_time_ms: number
}

/**
 * 获取文件内容（用于预览）
 * 仅支持文本类型文件（md/txt/json/csv），大小限制 5MB
 */
export const getFileContent = (fileId: string): Promise<FileContentResponse> => {
  return request.get(`/api/user/super-agent/files/${encodeURIComponent(fileId)}/content`)
}

/**
 * 将文件转换为PDF
 * 支持的文件类型：md, txt, json
 */
export const convertToPdf = (
  fileId: string,
  options?: { paper_size?: string }
): Promise<ConvertToPdfResponse> => {
  return request.post(
    `/api/user/super-agent/files/${encodeURIComponent(fileId)}/convert/pdf`,
    options || {}
  )
}

/**
 * 获取PDF预览URL（在iframe中直接显示）
 */
export const getPdfPreviewUrl = (fileId: string): string => {
  const baseURL = import.meta.env.VITE_API_BASE_URL || ''
  return `${baseURL}/api/user/super-agent/files/${encodeURIComponent(fileId)}/download`
}

// ==================== 会话导出 ====================

/**
 * 导出超级智能体会话（返回文件 Blob）
 * @param sessionId 会话ID
 * @param format 导出格式: markdown / txt / pdf
 */
export const exportSuperAgentSession = async (
  sessionId: string,
  format: 'markdown' | 'txt' | 'pdf' | 'docx' = 'markdown',
  style?: 'standard' | 'official' | 'internal' | 'report'
): Promise<Blob> => {
  const baseURL = import.meta.env.VITE_API_BASE_URL || ''
  const token = localStorage.getItem('token') || ''
  let url = `${baseURL}/api/user/export/super-agent/${encodeURIComponent(sessionId)}?format=${format}`
  if (style) {
    url += `&style=${style}`
  }
  const resp = await fetch(url, {
    headers: { Authorization: `Bearer ${token}` }
  })
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: '导出失败' }))
    throw new Error(err.detail || `导出失败 (${resp.status})`)
  }
  return resp.blob()
}

// ==================== kkFileView 预览 ====================

export interface PreviewTokenResponse {
  token: string
  preview_url: string
  expires_in: number
}

/**
 * 获取文件预览临时 token（供 kkFileView 使用）
 */
export const getPreviewToken = (fileId: string): Promise<PreviewTokenResponse> => {
  return request.post(`/api/user/super-agent/files/${encodeURIComponent(fileId)}/preview-token`)
}

/**
 * 构建 kkFileView 预览 URL
 * @param previewUrl 后端返回的文件公开下载地址（Docker 内部网络地址）
 */
export const getKkFileViewUrl = (previewUrl: string): string => {
  // btoa 不支持非 Latin1 字符（如中文文件名），需先 UTF-8 编码
  // 缓存破坏由后端在文件名中嵌入 file_id 后缀实现，无需前端追加参数
  const base64 = btoa(unescape(encodeURIComponent(previewUrl)))
  const encoded = encodeURIComponent(base64)
  return `/kkfileview/onlinePreview?url=${encoded}`
}

// ==================== 聊天附件上传 ====================

/**
 * 附件上传响应
 */
export interface AttachmentUploadResponse {
  id: string
  filename: string
  file_category: 'document' | 'image'
  file_ext: string
  size: number
  status: string
  index_status?: string  // PageIndex: building / not_supported
}

/**
 * 上传聊天附件（使用 XMLHttpRequest 支持进度回调）
 */
export const uploadChatAttachment = (
  file: File,
  token: string,
  onProgress?: (percent: number) => void,
): Promise<AttachmentUploadResponse> => {
  return new Promise((resolve, reject) => {
    const baseURL = import.meta.env.VITE_API_BASE_URL || ''
    const url = `${baseURL}/api/user/super-agent/attachments/upload`

    const formData = new FormData()
    formData.append('file', file)

    const xhr = new XMLHttpRequest()
    xhr.open('POST', url)
    xhr.setRequestHeader('Authorization', `Bearer ${token}`)

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress(Math.round((e.loaded / e.total) * 100))
      }
    }

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText))
        } catch {
          reject(new Error('响应解析失败'))
        }
      } else {
        try {
          const err = JSON.parse(xhr.responseText)
          reject(new Error(err.detail || `上传失败: ${xhr.status}`))
        } catch {
          reject(new Error(`上传失败: ${xhr.status}`))
        }
      }
    }

    xhr.onerror = () => reject(new Error('网络错误'))
    xhr.ontimeout = () => reject(new Error('上传超时'))
    xhr.timeout = 120000 // 2 分钟超时

    xhr.send(formData)
  })
}

// ==================== 知识库文档导入 ====================

/**
 * 从个人知识库导入文档作为聊天附件
 */
export const importAttachmentsFromKB = (data: {
  doc_ids: string[]
  session_id?: string
}): Promise<AttachmentUploadResponse[]> => {
  return request.post('/api/user/super-agent/attachments/import-from-kb', data)
}

// ==================== 辩论API ====================

/**
 * 辩论配置
 */
export interface DebateConfig {
  default_rounds: number
  max_rounds: number
  expert_count: number
  timeout_seconds: number
}

/**
 * 获取辩论配置（用户端只读）
 */
export const getDebateConfig = (): Promise<DebateConfig> => {
  return request.get('/api/user/debate/config')
}

/**
 * 获取辩论流式URL
 */
export const getDebateStreamUrl = (sessionId: string): string => {
  const baseURL = import.meta.env.VITE_API_BASE_URL || ''
  return `${baseURL}/api/user/debate/${sessionId}/continue`
}

/**
 * 开始辩论
 */
export const startDebate = (data: {
  topic: string
  expert_count?: number
  max_rounds?: number
  discussion_mode?: string
}): Promise<DebateSession> => {
  return request.post('/api/user/debate/start', data)
}

/**
 * 获取辩论详情
 */
export const getDebateDetail = (sessionId: string): Promise<DebateDetailResponse> => {
  return request.get(`/api/user/debate/${sessionId}`)
}

/**
 * 结束辩论
 */
export const endDebate = (sessionId: string): Promise<{
  message: string
  final_summary?: string
}> => {
  return request.post(`/api/user/debate/${sessionId}/end`)
}

/**
 * 获取专家列表
 */
export const getExperts = (): Promise<{
  experts: Expert[]
  categories: string[]
}> => {
  return request.get('/api/user/debate/experts/list')
}

/**
 * 追问专家请求参数
 */
export interface AskExpertRequest {
  expert_id: string
  question: string
}

/**
 * 获取追问专家流式URL
 */
export const getAskExpertStreamUrl = (sessionId: string): string => {
  const baseURL = import.meta.env.VITE_API_BASE_URL || ''
  return `${baseURL}/api/user/debate/${sessionId}/ask`
}

// ==================== 流式处理工具函数 ====================

/**
 * 解析SSE数据行
 */
export const parseSSELine = (line: string): { event?: string; data?: SSEEventData } | null => {
  if (!line || line.startsWith(':')) return null

  if (line.startsWith('event:')) {
    return { event: line.slice(6).trim() }
  }

  if (line.startsWith('data:')) {
    try {
      const data = JSON.parse(line.slice(5).trim())
      return { data }
    } catch {
      return null
    }
  }

  return null
}

/**
 * 创建流式请求
 */
export const createStreamRequest = async (
  url: string,
  body: Record<string, unknown>,
  token: string
): Promise<ReadableStreamDefaultReader<Uint8Array>> => {
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`,
      'Accept': 'text/event-stream'
    },
    body: JSON.stringify(body)
  })

  if (!response.ok) {
    const errorText = await response.text()
    throw new Error(errorText || `请求失败: ${response.status}`)
  }

  if (!response.body) {
    throw new Error('响应体为空')
  }

  return response.body.getReader()
}

// ==================== 长期记忆管理 ====================

import type { MemoryItem } from '../types/super-agent'

/**
 * 获取用户所有长期记忆
 */
export const getUserMemories = (limit: number = 100) =>
  request.get<{ memories: MemoryItem[]; enabled: boolean }>('/api/user/super-agent/memories', { params: { limit } })

/**
 * 删除单条记忆
 */
export const deleteMemory = (memoryId: string) =>
  request.delete(`/api/user/super-agent/memories/${memoryId}`)

/**
 * 清空所有记忆
 */
export const clearAllMemories = () =>
  request.delete('/api/user/super-agent/memories')

// ==================== AI 助手管理 ====================

/**
 * AI 助手信息
 */
export interface AgentInfo {
  id: number
  name: string
  description: string | null
  avatar: string | null
  welcome_message: string | null
  soul_prompt: string | null
  category: string | null
  category_label: string | null
  is_system: boolean
  is_mine: boolean
  is_enabled: boolean
  sort_order: number
  allowed_skills: string[] | null
  example_questions: string[] | null
}

/**
 * 获取可用 AI 助手列表
 */
export const getAgents = (params?: {
  scope?: 'all' | 'system' | 'mine'
  category?: string
  search?: string
}) =>
  request.get<{ items: AgentInfo[] }>('/api/user/agents', { params })

/**
 * 获取助手分类映射 { key: label }
 */
export const getAgentCategories = () =>
  request.get<Record<string, string>>('/api/user/agents/super-agent-categories')

/**
 * 获取单个 AI 助手详情
 */
export const getAgent = (agentId: number) =>
  request.get<AgentInfo>(`/api/user/agents/${agentId}`)

/**
 * 创建个人 AI 助手
 */
export const createAgent = (data: {
  name: string
  description?: string
  soul_prompt?: string
  avatar?: string
  welcome_message?: string
  category?: string | null
  allowed_skills?: string[] | null
  example_questions?: string[] | null
}) =>
  request.post<AgentInfo>('/api/user/agents', data)

/**
 * 更新个人 AI 助手
 */
export const updateAgent = (agentId: number, data: Partial<{
  name: string
  description: string
  soul_prompt: string
  avatar: string
  welcome_message: string
  category: string | null
  allowed_skills: string[] | null
  example_questions: string[] | null
}>) =>
  request.put<AgentInfo>(`/api/user/agents/${agentId}`, data)

/**
 * 删除个人 AI 助手
 */
export const deleteAgent = (agentId: number) =>
  request.delete(`/api/user/agents/${agentId}`)

/**
 * 获取用户默认助手
 */
export const getDefaultAgent = () =>
  request.get<{ agent_id: number | null }>('/api/user/agents/default')

/**
 * 设置用户默认助手
 */
export const setDefaultAgent = (agentId: number) =>
  request.put<{ agent_id: number }>('/api/user/agents/default', { agent_id: agentId })

/**
 * 清除用户默认助手
 */
export const clearDefaultAgent = () =>
  request.delete<{ agent_id: number | null }>('/api/user/agents/default')

/**
 * AI 辅助生成 Agent 配置（用户端）
 */
export const generateAgent = (data: {
  messages: Array<{ role: 'user' | 'assistant'; content: string }>
}): Promise<{
  reply: string
  agent_config?: Record<string, any> | null
  is_final: boolean
}> => {
  return request.post('/api/user/agents/generate', data)
}

// ==================== v5.0 多轮对话版本管理 ====================

/**
 * 重新生成 assistant 消息
 */
export const regenerateMessage = (data: {
  session_id: string
  message_id: string
  force_skill?: string
  agent_id?: number
  revision: number
  mode?: string
}): Promise<{
  session_id: string
  new_message_id: string
  revision: number
}> => {
  return request.post('/api/user/super-agent/chat/regenerate?async=true', data)
}

/**
 * 编辑用户消息并重新生成
 */
export const editMessage = (data: {
  session_id: string
  message_id: string
  new_content: string
  force_skill?: string
  agent_id?: number
  revision: number
  mode?: string
}): Promise<{
  session_id: string
  new_message_id: string
  revision: number
}> => {
  return request.post('/api/user/super-agent/chat/edit?async=true', data)
}

/**
 * 切换消息版本
 */
export const switchVersion = (data: {
  session_id: string
  target_message_id: string
  revision: number
}): Promise<{
  session_id: string
  messages: any[]
  revision: number
}> => {
  return request.post('/api/user/super-agent/chat/switch-version', data)
}
