/**
 * 批量导入API
 */
import request from '../utils/request'

export interface ImportTaskStatus {
  task_id: string
  status: 'pending' | 'processing' | 'completed' | 'failed'
  total_files: number
  processed_files: number
  success_count: number
  failed_count: number
  skipped_count: number
  current_file: string
  error_message: string
}

export interface ImportResult {
  filename: string
  status: 'success' | 'failed' | 'skipped'
  doc_id?: string
  category?: string
  is_relevant?: boolean
  reason?: string
}

export interface ImportResultsResponse {
  task_id: string
  status: string
  total_files: number
  success_count: number
  failed_count: number
  skipped_count: number
  results: ImportResult[]
  created_at: string | null
  completed_at: string | null
}

export interface ImportCreateResponse {
  task_id: string
  message: string
}

/**
 * 创建批量导入任务
 * @param file ZIP文件
 * @param kbId 知识库ID（可选）
 */
export function createBatchImport(file: File, kbId?: string): Promise<ImportCreateResponse> {
  const formData = new FormData()
  formData.append('file', file)
  if (kbId) {
    formData.append('kb_id', kbId)
  }
  return request.post('/api/admin/import/batch', formData, {
    headers: {
      'Content-Type': 'multipart/form-data'
    },
    timeout: 300000 // 5分钟超时（上传大文件）
  })
}

/**
 * 获取导入任务状态
 * @param taskId 任务ID
 */
export function getImportStatus(taskId: string): Promise<ImportTaskStatus> {
  return request.get(`/api/admin/import/batch/${taskId}`)
}

/**
 * 获取导入结果详情
 * @param taskId 任务ID
 */
export function getImportResults(taskId: string): Promise<ImportResultsResponse> {
  return request.get(`/api/admin/import/batch/${taskId}/results`)
}

/**
 * 创建SSE连接获取实时进度
 * @param taskId 任务ID
 * @param onProgress 进度回调
 * @param onComplete 完成回调
 * @param onError 错误回调
 */
export function streamImportProgress(
  taskId: string,
  onProgress: (data: {
    status: string
    total: number
    processed: number
    success: number
    failed: number
    skipped: number
    current_file: string
    progress: number
  }) => void,
  onComplete: (data: {
    status: string
    total: number
    success: number
    failed: number
    skipped: number
    results: ImportResult[]
  }) => void,
  onError: (error: string) => void
): EventSource {
  const baseUrl = import.meta.env.VITE_API_BASE_URL || ''
  const url = `${baseUrl}/api/admin/import/batch/${taskId}/stream`

  // 使用fetch + EventSource polyfill方式（因为原生EventSource不支持自定义header）
  // 这里简化处理，直接轮询
  const eventSource = new EventSource(url)

  eventSource.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)

      if (data.error) {
        onError(data.error)
        eventSource.close()
        return
      }

      if (data.status === 'completed' || data.status === 'failed') {
        onComplete(data)
        eventSource.close()
      } else {
        onProgress(data)
      }
    } catch (e) {
      console.error('解析SSE数据失败:', e)
    }
  }

  eventSource.onerror = (e) => {
    console.error('SSE连接错误:', e)
    onError('连接中断')
    eventSource.close()
  }

  return eventSource
}

/**
 * 轮询方式获取进度（作为SSE的备选方案）
 * @param taskId 任务ID
 * @param onProgress 进度回调
 * @param onComplete 完成回调
 * @param onError 错误回调
 * @param interval 轮询间隔（毫秒）
 */
export function pollImportProgress(
  taskId: string,
  onProgress: (data: ImportTaskStatus) => void,
  onComplete: (data: ImportResultsResponse) => void,
  onError: (error: string) => void,
  interval: number = 1000
): { stop: () => void } {
  let timer: ReturnType<typeof setInterval> | null = null
  let stopped = false

  const poll = async () => {
    if (stopped) return

    try {
      const status = await getImportStatus(taskId)
      onProgress(status)

      if (status.status === 'completed' || status.status === 'failed') {
        if (timer) {
          clearInterval(timer)
          timer = null
        }
        // 获取完整结果
        const results = await getImportResults(taskId)
        onComplete(results)
      }
    } catch (e: any) {
      if (timer) {
        clearInterval(timer)
        timer = null
      }
      onError(e.message || '获取进度失败')
    }
  }

  // 立即执行一次
  poll()

  // 设置定时轮询
  timer = setInterval(poll, interval)

  return {
    stop: () => {
      stopped = true
      if (timer) {
        clearInterval(timer)
        timer = null
      }
    }
  }
}
