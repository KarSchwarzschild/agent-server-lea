import request from '../utils/request'

export interface Document {
  doc_id: string
  datacollection_id?: string | null
  kb_id: string
  filename: string | null
  title: string | null
  authors: string | null
  year: number | null
  category: string | null
  sub_category: string | null
  methodology: string | null
  keywords: string | null
  abstract: string | null
  status: string
  classification_confirmed: boolean
}

export interface DocumentListParams {
  kb_id?: string
  category?: string
  status?: string
  year_from?: number
  year_to?: number
  page?: number
  page_size?: number
}

export interface DocumentListResponse {
  total: number
  documents: Document[]
}

export interface ClassificationUpdate {
  title?: string
  authors?: string
  year?: number
  category?: string
  sub_category?: string
  methodology?: string
  keywords?: string
}

// 删除结果详情
export interface DeleteResult {
  mysql: boolean
  datacollection: boolean
  local_file: boolean
}

// 单个删除响应
export interface SingleDeleteResponse {
  message: string
  details: DeleteResult
  warnings: string[]
}

// 批量删除请求
export interface BatchDeleteRequest {
  doc_ids: string[]
  hard_delete?: boolean
}

// 批量删除单项结果
export interface BatchDeleteItemResult {
  doc_id: string
  success: boolean
  details: DeleteResult
  warnings: string[]
}

// 批量删除响应
export interface BatchDeleteResponse {
  message: string
  total: number
  success: number
  failed: number
  results: BatchDeleteItemResult[]
}

// 获取文献列表
export const getDocuments = (params: DocumentListParams): Promise<DocumentListResponse> => {
  return request.get('/api/admin/documents/list', { params })
}

// 获取文献详情
export const getDocument = (docId: string): Promise<Document> => {
  return request.get(`/api/admin/documents/${docId}`)
}

// 上传文献
export const uploadDocument = (file: File, kbId?: string): Promise<Document> => {
  const formData = new FormData()
  formData.append('file', file)
  if (kbId) {
    formData.append('kb_id', kbId)
  }
  return request.post('/api/admin/documents/import', formData, {
    headers: {
      'Content-Type': 'multipart/form-data'
    },
    timeout: 120000 // 上传超时2分钟
  })
}

// 更新分类
export const updateClassification = (docId: string, data: ClassificationUpdate): Promise<{ message: string }> => {
  return request.put(`/api/admin/documents/${docId}/classification`, data)
}

// 删除文献（支持软删除和硬删除）
export const deleteDocument = (docId: string, hardDelete: boolean = false): Promise<SingleDeleteResponse> => {
  return request.delete(`/api/admin/documents/${docId}`, {
    params: { hard_delete: hardDelete }
  })
}

// 批量删除文献
export const batchDeleteDocuments = (data: BatchDeleteRequest): Promise<BatchDeleteResponse> => {
  return request.post('/api/admin/documents/batch-delete', data)
}

// 获取分类选项
export const getCategories = (): Promise<{
  categories: string[]
  sub_categories: string[]
  methodologies: string[]
}> => {
  return request.get('/api/user/search/categories')
}

// 触发 PageIndex 深度索引
export const buildPageIndex = (doc_ids: string[]): Promise<{
  message: string
  results: Array<{ doc_id: string; status: string; reason?: string }>
}> => {
  return request.post('/api/admin/documents/pageindex/build', { doc_ids })
}

// 获取统计信息
export const getStats = (): Promise<{
  total_documents: number
  by_category: Record<string, number>
  by_year: Record<string, number>
}> => {
  return request.get('/api/user/search/stats')
}
