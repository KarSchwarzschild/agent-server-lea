/**
 * SSE 流解析 composable
 *
 * 从 SuperAgent.vue 的 sendMessage / sendAskExpert / continueDebate / endDebate
 * 中提取的公共 SSE 行解析逻辑，消除重复代码。
 *
 * 职责：
 * - 从 ReadableStreamDefaultReader 读取数据
 * - 解析 SSE 协议行（id: / event: / data:）
 * - 回调分发解析后的事件
 * - 追踪 lastEventId 用于断连重连
 */
import type { SSEEventData } from '../api/superagent'

export interface SSEParseCallbacks {
  /** 收到解析后的 SSE 事件 */
  onEvent: (event: string, data: SSEEventData) => void
  /** 收到 event id（用于断连重连） */
  onEventId?: (id: string) => void
}

/**
 * 从 ReadableStream reader 中解析 SSE 事件并分发
 *
 * @param reader - fetch response body reader
 * @param callbacks - 事件回调
 * @returns 最后收到的 event id
 */
export async function parseSSEStream(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  callbacks: SSEParseCallbacks,
): Promise<string> {
  const decoder = new TextDecoder()
  let buffer = ''
  let currentEvent = ''
  let lastEventId = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''

    for (const line of lines) {
      if (!line.trim()) continue

      if (line.startsWith('id:')) {
        lastEventId = line.slice(3).trim()
        callbacks.onEventId?.(lastEventId)
        continue
      }

      if (line.startsWith('event:')) {
        currentEvent = line.slice(6).trim()
        continue
      }

      if (line.startsWith('data:')) {
        try {
          const data: SSEEventData = JSON.parse(line.slice(5).trim())
          callbacks.onEvent(currentEvent, data)
        } catch {
          // JSON 解析错误，忽略
        }
      }
    }
  }

  return lastEventId
}
