/**
 * 综述生成 API
 */
import request from '../utils/request'

export interface ReviewFilters {
  category?: string
  sub_category?: string
  methodology?: string
  year_from?: number
  year_to?: number
}

export interface ReviewCreateParams {
  topic: string
  category?: string
  sub_category?: string
  methodology?: string
  year_from?: number
  year_to?: number
  word_count?: number
  requirements?: string
  scope?: string
}

export interface ReviewTask {
  task_id: string
  topic: string
  status: 'pending' | 'processing' | 'completed' | 'failed'
  filters?: ReviewFilters
  requirements?: string
  review_content?: string
  partial_content?: string  // v2.0 流式生成的部分内容
  citations?: Array<{
    doc_id: string
    title: string
    authors: string
    year: number
  }>
  documents_used: number
  word_count: number
  created_at: string
  completed_at?: string
  // v2.0 新增进度字段（直接从数据库读取）
  progress_step?: string
  progress_percent?: number
  progress_message?: string
  error_type?: string
  error_message?: string
  // 兼容旧格式
  progress?: {
    step: string
    progress: number
    message: string
    data?: any
  }
}

export interface ReviewListResponse {
  total: number
  tasks: ReviewTask[]
}

export interface ReviewCreateResponse {
  task_id: string
  status: string
  message: string
}

/**
 * 创建综述生成任务
 */
export function createReview(params: ReviewCreateParams): Promise<ReviewCreateResponse> {
  return request.post('/api/user/review', params)
}

/**
 * 获取综述任务状态/结果
 */
export function getReviewStatus(taskId: string): Promise<ReviewTask> {
  return request.get(`/api/user/review/${taskId}`)
}

/**
 * 获取综述历史列表
 */
export function getReviewHistory(page: number = 1, pageSize: number = 10): Promise<ReviewListResponse> {
  return request.get('/api/user/review/history/list', {
    params: { page, page_size: pageSize }
  })
}

/**
 * 删除综述任务
 */
export function deleteReview(taskId: string): Promise<{ message: string }> {
  return request.delete(`/api/user/review/${taskId}`)
}

/**
 * 创建SSE连接获取进度
 */
export function createReviewProgressStream(
  taskId: string,
  onProgress: (data: any) => void,
  onError?: (error: any) => void
): () => void {
  const token = localStorage.getItem('token')
  const eventSource = new EventSource(
    `/api/user/review/${taskId}/stream?token=${token}`
  )

  eventSource.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)
      onProgress(data)

      // 如果完成或出错，关闭连接
      if (data.step === 'complete' || data.step === 'error') {
        eventSource.close()
      }
    } catch (e) {
      console.error('解析进度数据失败:', e)
    }
  }

  eventSource.onerror = (error) => {
    console.error('SSE连接错误:', error)
    if (onError) {
      onError(error)
    }
    eventSource.close()
  }

  // 返回关闭函数
  return () => eventSource.close()
}
