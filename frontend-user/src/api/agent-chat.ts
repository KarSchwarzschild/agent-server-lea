/**
 * ChatV2 · lea-agent (Go 引擎) 直连 API 层
 *
 * 与 superagent.ts 完全解耦：这份文件只面对 Go 后端已有的 7 种 SSE 事件
 *   start / delta / thinking / tool_event / result / error / done
 * 以及 async POST + EventSource GET 模式（H5 阶段）。
 *
 * 一切路径都走 request.ts 的 baseURL — vite proxy 已把 /api → lea-agent:8100。
 * agents CRUD 通过 lea-agent 反代到 lea-config:8500（proxy.go）。
 */

import request from '../utils/request'

// ── Types ─────────────────────────────────────────────

export interface AgentInfo {
  id: number
  name: string
  description?: string
  avatar?: string
  welcome_message?: string
  soul_prompt?: string
  category?: string
  example_questions?: string[]
  is_system?: boolean
}

export interface SkillInfo {
  id: number
  name: string
  display_name?: string
  description?: string
  icon?: string
}

export interface SessionRow {
  id: string
  title: string
  agent_id: number | null
  message_count: number
  last_active_at: string
  created_at: string
  is_archived: boolean
}

// 时间线片段（前端内存态；不下发到后端，也不从后端读回）
export type TimelineSegment =
  | { kind: 'text'; content: string }
  | { kind: 'thinking'; content: string }
  | { kind: 'tool'; ref: ToolCallRow }
  | { kind: 'sandbox'; ref: SandboxCallRow }

export interface MessageExtra {
  tool_calls?: ToolCallRow[]
  sandbox_calls?: SandboxCallRow[]
  reasoning_blocks?: ReasoningBlock[]
  segments?: TimelineSegment[]
}

export interface ToolCallRow {
  toolCallId: string
  tool: string
  input?: Record<string, unknown>
  status?: 'running' | 'success' | 'error'
  /** 一行摘要（Go 侧 emit 的 200 字截断）—— 时间线上快速看的 */
  result?: string
  /** 结构化完整值（JSON/短 Text）—— 前端展开时 pretty-print 显示。长 Text 或不适合结构化的场景会缺失。 */
  result_full?: unknown
  durationMs?: number
}

export interface SandboxCallRow {
  callId: string
  language?: string
  codePreview?: string
  status?: 'running' | 'success' | 'error'
  stdoutPreview?: string
  stderrPreview?: string
  artifacts?: string[]
  durationMs?: number
}

export interface ReasoningBlock {
  expertId: string
  expertName: string
  reasoning: string
}

export interface MessageRow {
  id: number
  role: 'user' | 'assistant'
  content: string
  created_at: string
  extra?: MessageExtra
}

export interface AttachmentUploaded {
  id: number
  filename: string
  file_type: string
  file_size: number
  media_type: string
  text_preview: string
}

// ── Sessions ─────────────────────────────────────────

export const createSession = (agentId: number): Promise<{ session_id: string }> =>
  request.post('/api/sessions', { agent_id: agentId })

export const listSessions = (): Promise<{ sessions: SessionRow[] }> =>
  request.get('/api/sessions')

export const getMessages = (sessionId: string): Promise<{ session_id: string; messages: MessageRow[] }> =>
  request.get(`/api/sessions/${sessionId}/messages`)

export const deleteSession = (sessionId: string): Promise<{ ok: boolean }> =>
  request.delete(`/api/sessions/${sessionId}`)

// ── Chat ─────────────────────────────────────────────

export interface SendPayload {
  session_id?: string
  agent_id: number
  message: string
  attachment_ids?: number[]
}

export const sendMessage = (payload: SendPayload): Promise<{ session_id: string; status: string }> =>
  request.post('/api/chat/send', payload)

// ── SSE ──────────────────────────────────────────────

// 'session' 只出现在 POST /chat/stream 的链路里，是流的第一个事件，带回本轮的
// session_id（新会话时客户端在流结束前就需要它）。
export type ChatEventType =
  | 'session' | 'start' | 'delta' | 'thinking' | 'tool_event' | 'result' | 'error' | 'done'

export interface ChatEvent {
  type: ChatEventType
  data: unknown
}

/**
 * 发消息并在同一条连接上读 SSE。返回 stop 函数用于中断。
 *
 * 取代 sendMessage + streamChat 两步走。那两步要求 POST 和 GET 落到同一个后端
 * 副本（事件只存在于跑 turn 的那个进程的内存里），多副本部署时 GET 会 404；
 * 而且两步之间 turn 已经在跑，头几个 delta 是丢的。一条连接两个问题都没有。
 *
 * session_id 从流里的第一个 'session' 事件拿，不再有 POST 的 JSON 响应。
 */
export const sendMessageStream = async (
  payload: SendPayload,
  onEvent: (ev: ChatEvent) => void,
): Promise<() => void> => {
  const token = localStorage.getItem('token') || ''
  const baseURL = import.meta.env.VITE_API_BASE_URL || ''
  const controller = new AbortController()

  const resp = await fetch(`${baseURL}/api/chat/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'text/event-stream',
      'Authorization': `Bearer ${token}`,
    },
    body: JSON.stringify(payload),
    signal: controller.signal,
  })
  if (!resp.ok || !resp.body) {
    // 错误在写 SSE 头之前发生，所以仍然是正常的 JSON 错误响应。
    let detail = ''
    try {
      detail = ((await resp.json()) as { error?: string })?.error || ''
    } catch { /* 非 JSON 响应，用状态码说话 */ }
    throw new Error(detail || `stream failed: ${resp.status}`)
  }

  pumpSSE(resp.body, onEvent)
  return () => controller.abort()
}

/**
 * 订阅 SSE 流。返回一个 stop 函数用于中断。
 *
 * @deprecated 用 sendMessageStream。这条要求 send 和 stream 落到同一个副本。
 *
 * fetch + ReadableStream 而不是原生 EventSource，因为 EventSource 无法带
 * Authorization header。参考 superagent.ts:761。
 */
export const streamChat = async (
  sessionId: string,
  onEvent: (ev: ChatEvent) => void,
): Promise<() => void> => {
  const token = localStorage.getItem('token') || ''
  const baseURL = import.meta.env.VITE_API_BASE_URL || ''
  const controller = new AbortController()

  const resp = await fetch(`${baseURL}/api/chat/stream/${sessionId}`, {
    method: 'GET',
    headers: {
      'Accept': 'text/event-stream',
      'Authorization': `Bearer ${token}`,
    },
    signal: controller.signal,
  })
  if (!resp.ok || !resp.body) {
    throw new Error(`stream failed: ${resp.status}`)
  }

  pumpSSE(resp.body, onEvent)
  return () => controller.abort()
}

/**
 * 读 SSE 响应体并把事件逐个交给 onEvent。不 await —— 调用方拿 abort 中断。
 *
 * 中途出错（断网、代理超时）不再让 promise 静默 reject：那样 UI 会永远停在
 * streaming 上转圈。改成合成一对 error + done 事件，让上层能收尾。
 */
const pumpSSE = (body: ReadableStream<Uint8Array>, onEvent: (ev: ChatEvent) => void): void => {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  const pump = async () => {
    try {
      while (true) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        // gin.SSEvent 输出格式：event: <type>\ndata: <json>\n\n
        let sepIdx: number
        while ((sepIdx = buffer.indexOf('\n\n')) >= 0) {
          const chunk = buffer.slice(0, sepIdx)
          buffer = buffer.slice(sepIdx + 2)
          const ev = parseSSEChunk(chunk)
          if (ev) onEvent(ev)
        }
      }
    } catch (e) {
      // 主动中断是正常收场，不当错误报。
      if ((e as { name?: string }).name === 'AbortError') return
      onEvent({ type: 'error', data: (e as Error)?.message || '连接中断' })
      onEvent({ type: 'done', data: null })
    }
  }
  void pump()
}

function parseSSEChunk(chunk: string): ChatEvent | null {
  let type: string | null = null
  const dataLines: string[] = []
  for (const line of chunk.split('\n')) {
    if (line.startsWith('event:')) type = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
  }
  if (!type || dataLines.length === 0) return null
  const raw = dataLines.join('\n')
  let data: unknown = raw
  if (raw && (raw.startsWith('{') || raw.startsWith('[') || raw === 'null')) {
    try {
      data = JSON.parse(raw)
    } catch {
      data = raw
    }
  } else if (raw.startsWith('"')) {
    // gin.SSEvent 对纯字符串会 JSON 编码为 "xxx"
    try {
      data = JSON.parse(raw)
    } catch {
      data = raw
    }
  }
  return { type: type as ChatEventType, data }
}

// ── Attachments ──────────────────────────────────────

export const uploadAttachment = (
  file: File,
  sessionId?: string,
  onProgress?: (percent: number) => void,
): Promise<AttachmentUploaded> => {
  return new Promise((resolve, reject) => {
    const baseURL = import.meta.env.VITE_API_BASE_URL || ''
    const url = `${baseURL}/api/chat/attachments/upload`
    const token = localStorage.getItem('token') || ''

    const formData = new FormData()
    formData.append('file', file)
    if (sessionId) formData.append('session_id', sessionId)

    const xhr = new XMLHttpRequest()
    xhr.open('POST', url)
    xhr.setRequestHeader('Authorization', `Bearer ${token}`)

    if (onProgress) {
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100))
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
        let msg = `上传失败: ${xhr.status}`
        try {
          const err = JSON.parse(xhr.responseText)
          msg = err.error || err.detail || msg
        } catch {
          // ignore
        }
        reject(new Error(msg))
      }
    }
    xhr.onerror = () => reject(new Error('网络错误'))
    xhr.send(formData)
  })
}

// ── Agents (proxy → lea-config) ──────────────────────

export const listAgents = (params?: { scope?: string; category?: string; search?: string }): Promise<AgentInfo[]> =>
  request.get('/api/user/agents', { params })

export const getDefaultAgent = (): Promise<AgentInfo> =>
  request.get('/api/user/agents/default')

export const listAgentSkills = (agentId: number): Promise<SkillInfo[]> =>
  request.get(`/api/user/agents/${agentId}/skills`)

// ── Sandbox workspace files ──────────────────────────

export interface WorkspaceFile {
  /** 相对 /workspace 的路径，如 "output/report.xlsx" */
  path: string
  size: number
  /** unix 秒 */
  mtime: number
  /** 在 output/ 下 —— UI 给"产物"徽章 */
  in_output: boolean
}

export interface WorkspaceListing {
  files: WorkspaceFile[]
  /** 条数超上限被截断 */
  truncated: boolean
  /** nfs = 共享存储（沙箱死后仍可读）；sandbox = envd 读活沙箱 */
  store: string
}

/**
 * 列当前 session 的 workspace 文件树。
 *
 * tenant_id 由 lea-agent 从 JWT 注入，前端不传（传了也不被信任）。
 */
export const listWorkspaceFiles = (
  sessionId: string,
  showHidden = false,
): Promise<WorkspaceListing> =>
  request.get('/api/files/list', {
    params: { session_id: sessionId, ...(showHidden ? { show_hidden: true } : {}) },
  })

// ── Sandbox artifact download ────────────────────────

/**
 * 下载沙箱产物文件。fetch → blob → 触发浏览器 download。
 *
 * 不能用 <a href> 直连，因为下载端点要 JWT header（EventSource 也是同理）。
 * 后端 GET /api/files/download?session_id=X&path=Y → 200 stream +
 * Content-Disposition: attachment; filename*=UTF-8''<encoded>
 */
export const downloadArtifact = async (
  sessionId: string,
  relPath: string,
): Promise<void> => {
  const token = localStorage.getItem('token') || ''
  const baseURL = import.meta.env.VITE_API_BASE_URL || ''
  const url = `${baseURL}/api/files/download?session_id=${encodeURIComponent(sessionId)}&path=${encodeURIComponent(relPath)}`

  const resp = await fetch(url, {
    method: 'GET',
    headers: { 'Authorization': `Bearer ${token}` },
  })
  if (!resp.ok) {
    let msg = `下载失败 (${resp.status})`
    try {
      const err = await resp.json()
      if (err.error) msg = err.error
    } catch { /* ignore */ }
    throw new Error(msg)
  }

  // 从 Content-Disposition 拿文件名，失败就用相对路径的 basename 兜底
  let filename = relPath.split('/').pop() || 'download'
  const disp = resp.headers.get('Content-Disposition') || ''
  const m = disp.match(/filename\*=UTF-8''([^;]+)/i)
  if (m?.[1]) {
    try { filename = decodeURIComponent(m[1]) } catch { /* ignore */ }
  }

  const blob = await resp.blob()
  const objectUrl = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = objectUrl
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(objectUrl)
}
