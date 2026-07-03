/**
 * SSE 调试事件捕获 composable
 *
 * 职责：
 * - 捕获 SSE 事件到内存列表（最多 500 条，FIFO 淘汰）
 * - 实时统计事件数、字节数、耗时
 * - 导出事件日志为 JSON 文件
 * - 非 debug 模式下不捕获，避免内存浪费
 */
import { ref, computed, type Ref, type ComputedRef } from 'vue'

export interface SSEDebugEvent {
  id: number
  /** 相对开始时间（ms） */
  relativeTime: number
  /** 绝对时间戳 */
  timestamp: number
  /** SSE 事件类型 */
  event: string
  /** 事件数据 */
  data: Record<string, unknown>
  /** 该事件 JSON 序列化后的字节数 */
  size: number
}

export interface SSEDebugStats {
  totalEvents: number
  totalBytes: number
  elapsedMs: number
  eventCounts: Record<string, number>
  startTime: number
  /** 最近收到的 model 名称 */
  model: string
  /** 累积 token 使用量 */
  totalTokens: number
  promptTokens: number
  completionTokens: number
}

const MAX_EVENTS = 500

export function useSSEDebug(isDebugMode: Ref<boolean>) {
  const events = ref<SSEDebugEvent[]>([])
  const startTime = ref(0)
  let eventCounter = 0

  // model 和 token 追踪（Phase 2 数据源）
  const modelName = ref('')
  const totalTokens = ref(0)
  const promptTokens = ref(0)
  const completionTokens = ref(0)

  const stats: ComputedRef<SSEDebugStats> = computed(() => {
    const now = Date.now()
    const elapsed = startTime.value > 0 ? now - startTime.value : 0
    const counts: Record<string, number> = {}
    let bytes = 0

    for (const evt of events.value) {
      counts[evt.event] = (counts[evt.event] || 0) + 1
      bytes += evt.size
    }

    return {
      totalEvents: events.value.length,
      totalBytes: bytes,
      elapsedMs: elapsed,
      eventCounts: counts,
      startTime: startTime.value,
      model: modelName.value,
      totalTokens: totalTokens.value,
      promptTokens: promptTokens.value,
      completionTokens: completionTokens.value,
    }
  })

  function captureEvent(event: string, data: Record<string, unknown>) {
    if (!isDebugMode.value) return

    const now = Date.now()
    if (startTime.value === 0) {
      startTime.value = now
    }

    // Phase 2: 捕获 model 和 token 数据
    if (event === 'iteration_start' && data.model) {
      modelName.value = data.model as string
    }
    if (event === 'llm_usage') {
      if (data.model) modelName.value = data.model as string
      promptTokens.value += (data.prompt_tokens as number) || 0
      completionTokens.value += (data.completion_tokens as number) || 0
      totalTokens.value += (data.total_tokens as number) || 0
    }

    const serialized = JSON.stringify(data)
    const debugEvent: SSEDebugEvent = {
      id: ++eventCounter,
      relativeTime: now - startTime.value,
      timestamp: now,
      event,
      data: { ...data },
      size: new Blob([serialized]).size,
    }

    events.value.push(debugEvent)

    // FIFO 淘汰
    if (events.value.length > MAX_EVENTS) {
      events.value = events.value.slice(-MAX_EVENTS)
    }
  }

  function reset() {
    events.value = []
    startTime.value = 0
    eventCounter = 0
    modelName.value = ''
    totalTokens.value = 0
    promptTokens.value = 0
    completionTokens.value = 0
  }

  function exportLog() {
    const logData = {
      exportedAt: new Date().toISOString(),
      stats: stats.value,
      events: events.value,
    }
    const blob = new Blob([JSON.stringify(logData, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `sse-debug-${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.json`
    document.body.appendChild(a)
    a.click()
    URL.revokeObjectURL(url)
    document.body.removeChild(a)
  }

  return {
    events,
    stats,
    captureEvent,
    reset,
    exportLog,
  }
}
