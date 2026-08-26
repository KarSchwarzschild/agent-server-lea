/**
 * ChatV2 pinia store — 状态机对齐 Go lea-agent 现有的 7 种 SSE 事件。
 *
 * 与 SuperAgent 的旧 store 独立，不共享任何状态；重构失败也不会污染现有页面。
 */

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type {
  AgentInfo,
  AttachmentUploaded,
  ChatEvent,
  MessageExtra,
  MessageRow,
  SessionRow,
  SkillInfo,
  TimelineSegment,
  ToolCallRow,
  SandboxCallRow,
} from '../api/agent-chat'
import * as api from '../api/agent-chat'

// 一次 turn 内的时间线片段。delta / thinking / tool_event 按到达顺序推入 segments，
// 保留 agent 思路的原始交错节奏（"先搜 KB → 观察结果 → 再装依赖 → 再执行"）。
export type Segment = TimelineSegment

interface TurnState {
  userMsg: string
  segments: Segment[]
  // 冗余镜像，方便刷新回填 / 落库时展开（extra.tool_calls, sandbox_calls 需要）
  toolCalls: ToolCallRow[]
  sandboxCalls: SandboxCallRow[]
  status: 'idle' | 'streaming' | 'done' | 'error'
  errorMsg?: string
  iterations?: number
  stopped?: boolean
  // result.text 兜底：如果整个 turn 一个 delta 都没推，用它作为主体
  fallbackText?: string
}

const emptyTurn = (): TurnState => ({
  userMsg: '',
  segments: [],
  toolCalls: [],
  sandboxCalls: [],
  status: 'idle',
})

export const useAgentChatStore = defineStore('agent-chat', () => {
  // ── Agents ──
  const agents = ref<AgentInfo[]>([])
  const defaultAgentId = ref<number | null>(null)
  const activeAgentId = ref<number | null>(null)
  const activeAgentSkills = ref<SkillInfo[]>([])

  const activeAgent = computed<AgentInfo | null>(() => {
    if (activeAgentId.value == null) return null
    return agents.value.find(a => a.id === activeAgentId.value) || null
  })

  // ── Sessions & messages ──
  const sessions = ref<SessionRow[]>([])
  const activeSessionId = ref<string | null>(null)
  const messages = ref<MessageRow[]>([])
  const currentTurn = ref<TurnState>(emptyTurn())

  const pendingAttachments = ref<AttachmentUploaded[]>([])
  const streaming = computed(() => currentTurn.value.status === 'streaming')

  let stopStream: (() => void) | null = null

  // ── Agents / skills loading ──
  const loadAgents = async () => {
    const resp = await api.listAgents({ scope: 'all' })
    // lea-config 返 {items:[...]}，兜底也支持裸数组
    agents.value = Array.isArray(resp) ? resp : (resp?.items || [])
    if (activeAgentId.value == null) {
      try {
        const d = await api.getDefaultAgent()
        defaultAgentId.value = d.id
        activeAgentId.value = d.id
      } catch {
        // 无默认助手时用 super-agent 兜底（agent_id=0，Go 侧 H2 会兜底到 tenant default）
        activeAgentId.value = 0
      }
    }
    if (activeAgentId.value != null) await loadAgentSkills(activeAgentId.value)
  }

  const loadAgentSkills = async (agentId: number) => {
    try {
      const resp = await api.listAgentSkills(agentId)
      // lea-config 返 {agent_id, skills:[...]}，兜底裸数组
      activeAgentSkills.value = Array.isArray(resp) ? resp : (resp?.skills || [])
    } catch {
      activeAgentSkills.value = []
    }
  }

  const switchAgent = async (agentId: number) => {
    activeAgentId.value = agentId
    await loadAgentSkills(agentId)
  }

  // ── Sessions CRUD ──
  const loadSessions = async () => {
    const res = await api.listSessions()
    sessions.value = res.sessions || []
  }

  const openSession = async (sessionId: string) => {
    stopStreamIfAny()
    activeSessionId.value = sessionId
    currentTurn.value = emptyTurn()
    const res = await api.getMessages(sessionId)
    messages.value = (res.messages || []).map(hydrateMessage)
  }

  const newSession = () => {
    stopStreamIfAny()
    activeSessionId.value = null
    messages.value = []
    currentTurn.value = emptyTurn()
    pendingAttachments.value = []
  }

  const archiveSession = async (sessionId: string) => {
    await api.deleteSession(sessionId)
    sessions.value = sessions.value.filter(s => s.id !== sessionId)
    if (activeSessionId.value === sessionId) newSession()
  }

  // ── Attachments ──
  const addAttachment = async (file: File): Promise<AttachmentUploaded> => {
    const uploaded = await api.uploadAttachment(file, activeSessionId.value || undefined)
    pendingAttachments.value.push(uploaded)
    return uploaded
  }

  const removeAttachment = (id: number) => {
    pendingAttachments.value = pendingAttachments.value.filter(a => a.id !== id)
  }

  // ── Send + SSE ──
  const send = async (text: string) => {
    if (!text.trim() && pendingAttachments.value.length === 0) return
    if (activeAgentId.value == null) return
    stopStreamIfAny()

    const agentId = activeAgentId.value
    const attachmentIds = pendingAttachments.value.map(a => a.id)
    const attachmentsSnapshot = [...pendingAttachments.value]

    currentTurn.value = { ...emptyTurn(), userMsg: text, status: 'streaming' }

    // 先把 user 消息 optimistic 加入 messages，让 UI 立刻能看到。
    // 不再需要等 session_id —— 它由流里的第一个 'session' 事件带回来。
    messages.value.push({
      id: Date.now() * -1, // 临时 id，稍后 reload 会替换
      role: 'user',
      content: buildUserContentPreview(text, attachmentsSnapshot),
      created_at: new Date().toISOString(),
    })

    pendingAttachments.value = []

    // 一条连接把消息发出去并读回事件流。拆成 POST + GET 两步的老写法要求两条
    // 请求落到同一个后端副本，多副本下 GET 会 404。
    stopStream = await api.sendMessageStream({
      session_id: activeSessionId.value || undefined,
      agent_id: agentId,
      message: text,
      attachment_ids: attachmentIds.length ? attachmentIds : undefined,
    }, ev => handleEvent(ev))
  }

  const handleEvent = (ev: ChatEvent) => {
    const turn = currentTurn.value
    switch (ev.type) {
      case 'session': {
        // 流的第一个事件。新会话时这是客户端第一次知道自己的 session_id。
        const sid = (ev.data as { session_id?: string })?.session_id
        if (sid && activeSessionId.value !== sid) activeSessionId.value = sid
        break
      }
      case 'start':
        // Server 起手，什么都不做（POST 已把 status 置 streaming）
        break
      case 'delta':
        pushTextSegment(turn, stringOf(ev.data))
        break
      case 'thinking':
        pushThinkingSegment(turn, stringOf(ev.data))
        break
      case 'tool_event':
        applyToolEvent(turn, ev.data)
        break
      case 'result': {
        // Go engine 的 result.text 是「最后一轮 LLM 输出」，不是累计。
        // delta 已经把每一轮都推过一次；这里只当兜底：一个 delta 都没收到时用它。
        const r = ev.data as { text?: string; iterations?: number; stopped?: boolean }
        turn.iterations = r?.iterations
        turn.stopped = r?.stopped
        turn.fallbackText = r?.text
        break
      }
      case 'error':
        turn.status = 'error'
        turn.errorMsg = stringOf(ev.data)
        break
      case 'done':
        turn.status = 'done'
        finalizeTurn()
        break
    }
  }

  // 相邻 delta 合并到同一个 text segment；被 tool/thinking 打断后开新段。
  const pushTextSegment = (turn: TurnState, delta: string) => {
    if (!delta) return
    const last = turn.segments[turn.segments.length - 1]
    if (last && last.kind === 'text') {
      last.content += delta
    } else {
      turn.segments.push({ kind: 'text', content: delta })
    }
  }

  const pushThinkingSegment = (turn: TurnState, delta: string) => {
    if (!delta) return
    const last = turn.segments[turn.segments.length - 1]
    if (last && last.kind === 'thinking') {
      last.content += delta
    } else {
      turn.segments.push({ kind: 'thinking', content: delta })
    }
  }

  const finalizeTurn = () => {
    const turn = currentTurn.value
    const textFromSegments = turn.segments
      .filter((s): s is Extract<Segment, { kind: 'text' }> => s.kind === 'text')
      .map(s => s.content)
      .join('')
    const thinkingText = turn.segments
      .filter((s): s is Extract<Segment, { kind: 'thinking' }> => s.kind === 'thinking')
      .map(s => s.content)
      .join('\n')
    const finalText = textFromSegments || turn.fallbackText || ''

    const extra: MessageExtra = {}
    if (turn.toolCalls.length) extra.tool_calls = turn.toolCalls
    if (turn.sandboxCalls.length) extra.sandbox_calls = turn.sandboxCalls
    if (thinkingText) {
      extra.reasoning_blocks = [
        { expertId: '__model__', expertName: '思考过程', reasoning: thinkingText },
      ]
    }
    extra.segments = turn.segments

    messages.value.push({
      id: Date.now(),
      role: 'assistant',
      content: finalText || (turn.errorMsg ? `[执行失败] ${turn.errorMsg}` : ''),
      created_at: new Date().toISOString(),
      extra: Object.keys(extra).length ? extra : undefined,
    })
    loadSessions().catch(() => { /* silent */ })
    currentTurn.value = emptyTurn()
  }

  const stopStreamIfAny = () => {
    if (stopStream) {
      stopStream()
      stopStream = null
    }
  }

  return {
    // state
    agents, defaultAgentId, activeAgentId, activeAgent, activeAgentSkills,
    sessions, activeSessionId, messages, currentTurn, pendingAttachments,
    streaming,
    // actions
    loadAgents, loadAgentSkills, switchAgent,
    loadSessions, openSession, newSession, archiveSession,
    addAttachment, removeAttachment,
    send,
  }
})

// ── helpers ──

// hydrateMessage 把后端下发的 message 归一化：
// - segments 里的 tool/sandbox 段带 index → 反查 tool_calls/sandbox_calls 取 ref
// 保证模板只处理一种 shape（带 ref 的），不用两个分支。
function hydrateMessage(m: MessageRow): MessageRow {
  const extra = m.extra
  if (!extra?.segments?.length) return m
  const tools = extra.tool_calls || []
  const sandboxes = extra.sandbox_calls || []
  const hydrated: TimelineSegment[] = []
  for (const seg of extra.segments) {
    if (seg.kind === 'text' || seg.kind === 'thinking') {
      hydrated.push(seg)
      continue
    }
    // 后端存的形如 {kind:"tool", index:N}；本地内存态可能已经带 ref
    const withIndex = seg as unknown as { kind: string; index?: number; ref?: ToolCallRow | SandboxCallRow }
    if (withIndex.ref) {
      hydrated.push(seg)
      continue
    }
    const idx = withIndex.index ?? 0
    if (seg.kind === 'tool' && tools[idx]) {
      hydrated.push({ kind: 'tool', ref: tools[idx] })
    } else if (seg.kind === 'sandbox' && sandboxes[idx]) {
      hydrated.push({ kind: 'sandbox', ref: sandboxes[idx] })
    }
    // idx 越界 → 静默丢弃这一段，安全兜底（不阻断整个消息渲染）
  }
  return { ...m, extra: { ...extra, segments: hydrated } }
}

function stringOf(v: unknown): string {
  if (typeof v === 'string') return v
  if (v == null) return ''
  try {
    return JSON.stringify(v)
  } catch {
    return ''
  }
}

function buildUserContentPreview(text: string, atts: AttachmentUploaded[]): string {
  if (!atts.length) return text
  const parts = atts.map(a => `[文件: ${a.filename}]`)
  return parts.join('\n') + '\n\n' + text
}

interface RawToolEvent {
  type: string
  toolName?: string
  toolUseID?: string
  input?: Record<string, unknown>
  message?: string
  data?: unknown
  durationMs?: number
}

function applyToolEvent(turn: TurnState, raw: unknown) {
  const ev = raw as RawToolEvent
  if (!ev || !ev.toolUseID || !ev.toolName) return

  const isSandbox = ev.toolName === 'code_interpreter'
  const bucket = isSandbox ? turn.sandboxCalls : turn.toolCalls
  const idKey = isSandbox ? 'callId' : 'toolCallId'
  const existing = bucket.find(x => (x as { [k: string]: unknown })[idKey] === ev.toolUseID)

  if (ev.type === 'started') {
    if (existing) return
    if (isSandbox) {
      const input = (ev.input || {}) as { code?: string; language?: string }
      const sc: SandboxCallRow = {
        callId: ev.toolUseID,
        language: input.language,
        codePreview: input.code ? (input.code.length > 200 ? input.code.slice(0, 200) + '...' : input.code) : '',
        status: 'running',
      }
      turn.sandboxCalls.push(sc)
      // 同一个引用推进 segments，后续 progress/completed 改 sc 属性 → segment 自动同步
      turn.segments.push({ kind: 'sandbox', ref: sc })
    } else {
      const tc: ToolCallRow = {
        toolCallId: ev.toolUseID,
        tool: ev.toolName,
        input: ev.input,
        status: 'running',
      }
      turn.toolCalls.push(tc)
      turn.segments.push({ kind: 'tool', ref: tc })
    }
    return
  }

  if (ev.type === 'progress' && isSandbox && existing) {
    const payload = ev.data as { artifacts?: string[]; stdout?: string; stderr?: string; duration_ms?: number }
    if (payload) {
      const sc = existing as SandboxCallRow
      if (Array.isArray(payload.artifacts)) sc.artifacts = payload.artifacts
      if (payload.stdout) sc.stdoutPreview = payload.stdout
      if (payload.stderr) sc.stderrPreview = payload.stderr
      if (payload.duration_ms) sc.durationMs = payload.duration_ms
    }
    return
  }

  if (ev.type === 'completed' || ev.type === 'failed') {
    let target = existing as (ToolCallRow | SandboxCallRow) | undefined
    if (!target) {
      // 补一条，防事件乱序丢 started
      if (isSandbox) {
        const sc: SandboxCallRow = { callId: ev.toolUseID }
        turn.sandboxCalls.push(sc)
        turn.segments.push({ kind: 'sandbox', ref: sc })
        target = sc
      } else {
        const tc: ToolCallRow = { toolCallId: ev.toolUseID, tool: ev.toolName, input: ev.input }
        turn.toolCalls.push(tc)
        turn.segments.push({ kind: 'tool', ref: tc })
        target = tc
      }
    }
    target.status = ev.type === 'failed' ? 'error' : 'success'
    if (ev.durationMs) target.durationMs = ev.durationMs
    if (!isSandbox && ev.message) (target as ToolCallRow).result = ev.message
  }
}
