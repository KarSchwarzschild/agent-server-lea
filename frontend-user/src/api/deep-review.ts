/**
 * 深度综述 API
 */
import axios from 'axios'

const api = axios.create({ baseURL: '' })

// 请求拦截器：注入 token
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

export interface DeepReviewCreateParams {
  topic: string
  requirements?: string
  report_type?: string
  target_words?: number
  citation_style?: string
  language?: string
  literature_sources?: {
    public_kb: boolean
    user_kb: boolean
    academic_search: boolean
    manual_docs: string[]
  }
}

export interface DeepReviewTask {
  task_id: string
  status: 'pending' | 'running' | 'waiting_outline' | 'completed' | 'failed' | 'cancelled'
  current_stage: string
  progress_percent: number
  progress_message: string
  topic: string
  result_markdown?: string
  abstract?: string
  word_count: number
  chapters_count: number
  references_count: number
  error_message?: string
  duration_seconds: number
  created_at: string
  completed_at?: string
  report_type?: string
  target_words?: number
  citation_style?: string
}

export interface DeepReviewListItem {
  task_id: string
  topic: string
  status: string
  report_type: string
  target_words: number
  word_count: number
  chapters_count: number
  progress_percent: number
  current_stage: string
  created_at: string
  completed_at?: string
}

// 创建深度综述
export async function createDeepReview(params: DeepReviewCreateParams) {
  const { data } = await api.post('/api/user/report/generate', params)
  return data
}

// 获取任务状态
export async function getDeepReviewStatus(taskId: string): Promise<DeepReviewTask> {
  const { data } = await api.get(`/api/user/report/${taskId}`)
  return data
}

// 获取历史列表
export async function getDeepReviewHistory(page = 1, pageSize = 20): Promise<{ items: DeepReviewListItem[], total: number }> {
  const { data } = await api.get('/api/user/report/history/list', { params: { page, page_size: pageSize } })
  return data
}

// 取消任务
export async function cancelDeepReview(taskId: string) {
  const { data } = await api.post(`/api/user/report/${taskId}/cancel`)
  return data
}

// 确认大纲
export async function confirmOutline(taskId: string, outline?: any) {
  const { data } = await api.post(`/api/user/report/${taskId}/outline/confirm`, { outline })
  return data
}

// 删除任务
export async function deleteDeepReview(taskId: string) {
  const { data } = await api.delete(`/api/user/report/${taskId}`)
  return data
}

// SSE 进度流（用 fetch 模拟，支持 Authorization header）
export function createDeepReviewStream(taskId: string): {
  start: (handlers: {
    onProgress?: (data: any) => void
    onChapterDone?: (data: any) => void
    onOutlinePreview?: (data: any) => void
    onComplete?: (data: any) => void
    onError?: (data: any) => void
  }) => Promise<void>
  abort: () => void
} {
  const controller = new AbortController()

  return {
    abort: () => controller.abort(),
    start: async (handlers) => {
      const token = localStorage.getItem('token')
      const url = `/api/user/report/${taskId}/stream?token=${encodeURIComponent(token || '')}`

      const response = await fetch(url, {
        signal: controller.signal,
        headers: { 'Accept': 'text/event-stream' },
      })

      if (!response.ok || !response.body) {
        handlers.onError?.({ message: `连接失败: ${response.status}` })
        return
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        let currentEvent = ''
        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7).trim()
          } else if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6))
              switch (currentEvent) {
                case 'progress':
                  handlers.onProgress?.(data)
                  break
                case 'chapter_done':
                  handlers.onChapterDone?.(data)
                  break
                case 'outline_preview':
                  handlers.onOutlinePreview?.(data)
                  break
                case 'complete':
                  handlers.onComplete?.(data)
                  return
                case 'error':
                  handlers.onError?.(data)
                  return
                case 'cancelled':
                  handlers.onError?.({ message: '任务已取消' })
                  return
              }
            } catch {}
          }
        }
      }
    },
  }
}
