import { reactive } from 'vue'
import { useUserStore } from '../../stores/user'

export type NodeStatus = 'pending' | 'running' | 'completed' | 'waiting'

export interface InterruptPayload {
  stage: string
  message: string
  [key: string]: any
}

export interface ReviewSSEState {
  running: boolean
  nodeStatuses: Record<string, NodeStatus>
  currentNode: string
  interruptPayload: InterruptPayload | null
  error: string
  logs: Array<{ node: string; data: any; time: number }>
}

const GRAPH_NODES = [
  'init', 'decompose_topic', 'search', 'hitl_search',
  'analyze', 'hitl_analysis',
  'outline', 'hitl_outline',
  'generate', 'hitl_final',
  'finalize',
]

const ADJACENCY: Record<string, string> = {
  init: 'decompose_topic',
  decompose_topic: 'search',
  search: 'hitl_search',
  hitl_search: 'analyze',
  analyze: 'hitl_analysis',
  hitl_analysis: 'outline',
  outline: 'hitl_outline',
  hitl_outline: 'generate',
  generate: 'hitl_final',
  hitl_final: 'finalize',
}

function createInitialStatuses(): Record<string, NodeStatus> {
  const m: Record<string, NodeStatus> = {}
  for (const n of GRAPH_NODES) m[n] = 'pending'
  return m
}

export function useReviewSSE() {
  const userStore = useUserStore()
  const state = reactive<ReviewSSEState>({
    running: false,
    nodeStatuses: createInitialStatuses(),
    currentNode: '',
    interruptPayload: null,
    error: '',
    logs: [],
  })

  let abortController: AbortController | null = null

  function reset() {
    state.running = false
    state.nodeStatuses = createInitialStatuses()
    state.currentNode = ''
    state.interruptPayload = null
    state.error = ''
    state.logs = []
  }

  async function processStream(response: Response) {
    const reader = response.body?.getReader()
    if (!reader) return
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        try {
          const data = JSON.parse(line.slice(6))
          handleEvent(data)
        } catch { /* skip */ }
      }
    }
  }

  function handleEvent(data: any) {
    if (data.type === 'error') {
      state.error = data.message || '未知错误'
      state.running = false
      return
    }

    if (data.type === 'interrupt') {
      state.interruptPayload = data.payload || data
      const waitingAt = data.waiting_at?.[0]
      if (waitingAt) {
        state.nodeStatuses[waitingAt] = 'waiting'
        state.currentNode = waitingAt
      }
      state.running = false
      return
    }

    if (data.type === 'done') {
      state.running = false
      return
    }

    // LangGraph updates format: {"node_name": {state_update}}
    const nodeName = Object.keys(data).find(k => GRAPH_NODES.includes(k))
    if (nodeName) {
      state.nodeStatuses[nodeName] = 'completed'
      state.currentNode = nodeName
      state.logs.push({ node: nodeName, data: data[nodeName], time: Date.now() })

      const next = ADJACENCY[nodeName]
      if (next && state.nodeStatuses[next] === 'pending') {
        state.nodeStatuses[next] = 'running'
      }
    }
  }

  async function startReview(reviewId: number) {
    state.interruptPayload = null
    state.error = ''
    state.running = true
    state.nodeStatuses = createInitialStatuses()
    state.nodeStatuses['init'] = 'running'

    abortController = new AbortController()
    try {
      const resp = await fetch(`/api/apps/research/reviews/${reviewId}/start`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${userStore.token}`,
        },
        signal: abortController.signal,
      })
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      await processStream(resp)
    } catch (e: any) {
      if (e.name !== 'AbortError') {
        state.error = e.message || '连接失败'
        state.running = false
      }
    }
  }

  async function resumeReview(reviewId: number, approved: boolean, feedback?: string) {
    state.interruptPayload = null
    state.error = ''
    state.running = true

    // Mark waiting node as running again
    for (const [k, v] of Object.entries(state.nodeStatuses)) {
      if (v === 'waiting') state.nodeStatuses[k] = 'running'
    }

    abortController = new AbortController()
    try {
      const resp = await fetch(`/api/apps/research/reviews/${reviewId}/resume`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${userStore.token}`,
        },
        body: JSON.stringify({ approved, feedback: feedback || '' }),
        signal: abortController.signal,
      })
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      await processStream(resp)
    } catch (e: any) {
      if (e.name !== 'AbortError') {
        state.error = e.message || '连接失败'
        state.running = false
      }
    }
  }

  async function restoreState(reviewId: number) {
    try {
      const resp = await fetch(`/api/apps/research/reviews/${reviewId}/checkpoint`, {
        headers: { 'Authorization': `Bearer ${userStore.token}` },
      })
      if (!resp.ok) return false
      const data = await resp.json()
      if (!data.has_state) return false

      state.nodeStatuses = { ...createInitialStatuses(), ...data.node_statuses }
      state.currentNode = data.waiting_at?.[0] || ''
      state.interruptPayload = data.interrupt_payload || null
      state.logs = (data.logs || []).map((l: any) => ({ ...l, time: Date.now() }))
      state.error = ''
      state.running = false
      return true
    } catch {
      return false
    }
  }

  function abort() {
    abortController?.abort()
    state.running = false
  }

  return { state, startReview, resumeReview, restoreState, abort, reset, GRAPH_NODES }
}
