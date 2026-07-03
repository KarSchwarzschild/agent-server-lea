/**
 * 用户个人知识库 API
 */
import request from '../utils/request'

// ========== 类型定义 ==========

export interface UserKnowledgeBase {
  id: number
  user_id: string
  kb_id: string
  name: string
  description?: string
  document_count: number
  storage_used_mb: number
  quota_mb: number
  max_documents: number
  status: string
  created_at?: string
  updated_at?: string
}

export interface UserDocument {
  doc_id: string
  filename: string
  title?: string
  file_size_kb: number
  file_type?: string
  upload_status: 'processing' | 'completed' | 'failed'
  status: string
  chunk_status?: 'pending' | 'processing' | 'completed' | 'failed'
  error_message?: string
  parent_count?: number
  child_count?: number
  created_at?: string
  updated_at?: string
}

export interface UserDocumentListResponse {
  total: number
  items: UserDocument[]
}

export interface CreateKBParams {
  name?: string
  description?: string
}

export interface UpdateKBParams {
  name?: string
  description?: string
}

// ========== 知识库 API ==========

export function createKnowledgeBase(params: CreateKBParams = {}): Promise<UserKnowledgeBase> {
  return request.post('/api/user/knowledge-bases', params)
}

export function getKnowledgeBase(): Promise<UserKnowledgeBase> {
  return request.get('/api/user/knowledge-bases')
}

export function updateKnowledgeBase(params: UpdateKBParams): Promise<UserKnowledgeBase> {
  return request.patch('/api/user/knowledge-bases', params)
}

export function deleteKnowledgeBase(): Promise<{ message: string }> {
  return request.delete('/api/user/knowledge-bases')
}

// ========== 文档 API ==========

export function uploadDocument(
  file: File,
  onProgress?: (percent: number) => void
): Promise<UserDocument> {
  const formData = new FormData()
  formData.append('file', file)

  return request.post('/api/user/knowledge-bases/documents', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 300000, // 5分钟超时
    onUploadProgress: (e: any) => {
      if (onProgress && e.total) {
        onProgress(Math.round((e.loaded / e.total) * 100))
      }
    },
  })
}

export function getDocuments(params: {
  page?: number
  page_size?: number
  status?: string
  upload_status?: string
} = {}): Promise<UserDocumentListResponse> {
  return request.get('/api/user/knowledge-bases/documents', { params })
}

export function getDocumentDetail(docId: string): Promise<UserDocument> {
  return request.get(`/api/user/knowledge-bases/documents/${docId}`)
}

export function deleteDocument(docId: string): Promise<{ message: string }> {
  return request.delete(`/api/user/knowledge-bases/documents/${docId}`)
}

export function retryChunk(docId: string): Promise<UserDocument> {
  return request.post(`/api/user/knowledge-bases/documents/${docId}/retry-chunk`)
}
