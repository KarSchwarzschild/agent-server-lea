import request from '../utils/request'

// ==================== 请求/响应类型 ====================

export interface SearchRequest {
  query: string
  category?: string
  sub_category?: string
  methodology?: string
  year_from?: number
  year_to?: number
  top_k?: number
  min_relevance?: number
  save_history?: boolean
  scope?: string
}

export interface SearchResult {
  doc_id: string
  filename: string | null
  file_path: string | null   // 文件存储路径
  file_url: string | null    // 文件下载URL
  title: string | null
  authors: string | null
  year: number | null
  category: string | null
  sub_category: string | null
  methodology: string | null
  keywords: string | null
  abstract: string | null
  relevance_score: number
  matched_content: string | null
  is_favorited: boolean
  source_type?: 'public' | 'personal'
}

export interface SearchResponse {
  total: number
  search_id: string | null
  results: SearchResult[]
}

export interface Categories {
  categories: string[]
  sub_categories: string[]
  methodologies: string[]
}

export interface SearchHistoryItem {
  search_id: string
  query: string
  category: string | null
  sub_category: string | null
  methodology: string | null
  year_from: number | null
  year_to: number | null
  results_count: number
  created_at: string
}

export interface SearchHistoryListResponse {
  total: number
  histories: SearchHistoryItem[]
}

export interface SearchHistoryDetail extends SearchHistoryItem {
  results_snapshot: Array<{
    doc_id: string
    title: string | null
    relevance_score: number
  }>
}

export interface FavoriteItem {
  id: number
  doc_id: string
  title: string | null
  authors: string | null
  year: number | null
  category: string | null
  note: string | null
  created_at: string
}

export interface FavoriteListResponse {
  total: number
  favorites: FavoriteItem[]
}

export interface SearchStats {
  total_documents: number
  by_category: Record<string, number>
  by_year: Record<string, number>
  user_favorites_count: number
  user_search_count: number
}

// ==================== 文献检索 ====================

// 文献检索
export const searchDocuments = (data: SearchRequest): Promise<SearchResponse> => {
  return request.post('/api/user/search', data)
}

// 获取分类选项
export const getCategories = (): Promise<Categories> => {
  return request.get('/api/user/search/categories')
}

// 获取统计信息
export const getStats = (): Promise<SearchStats> => {
  return request.get('/api/user/search/stats')
}

// ==================== 检索历史 ====================

// 获取检索历史列表
export const getSearchHistory = (params: {
  page?: number
  page_size?: number
}): Promise<SearchHistoryListResponse> => {
  return request.get('/api/user/search/history', { params })
}

// 获取检索历史详情
export const getSearchHistoryDetail = (searchId: string): Promise<SearchHistoryDetail> => {
  return request.get(`/api/user/search/history/${searchId}`)
}

// 删除单条检索历史
export const deleteSearchHistory = (searchId: string): Promise<{ message: string }> => {
  return request.delete(`/api/user/search/history/${searchId}`)
}

// 清空所有检索历史
export const clearSearchHistory = (): Promise<{ message: string }> => {
  return request.delete('/api/user/search/history')
}

// ==================== 收藏功能 ====================

// 收藏文献
export const addFavorite = (data: {
  doc_id: string
  note?: string
}): Promise<{ message: string }> => {
  return request.post('/api/user/search/favorites', data)
}

// 取消收藏
export const removeFavorite = (docId: string): Promise<{ message: string }> => {
  return request.delete(`/api/user/search/favorites/${docId}`)
}

// 获取收藏列表
export const getFavorites = (params: {
  page?: number
  page_size?: number
}): Promise<FavoriteListResponse> => {
  return request.get('/api/user/search/favorites', { params })
}

// ==================== 导出功能 ====================

// 导出检索结果
export const exportSearchResults = (searchId: string, format: 'csv' | 'json' = 'csv'): void => {
  const token = localStorage.getItem('token')
  const url = `/api/user/search/export?search_id=${searchId}&format=${format}`

  // 使用 fetch 下载文件
  fetch(url, {
    headers: {
      'Authorization': `Bearer ${token}`
    }
  })
    .then(response => response.blob())
    .then(blob => {
      const downloadUrl = window.URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = downloadUrl
      link.download = `search_results_${searchId}.${format}`
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
      window.URL.revokeObjectURL(downloadUrl)
    })
}

// 导出收藏列表
export const exportFavorites = (format: 'csv' | 'json' = 'csv'): void => {
  const token = localStorage.getItem('token')
  const url = `/api/user/search/favorites/export?format=${format}`

  fetch(url, {
    headers: {
      'Authorization': `Bearer ${token}`
    }
  })
    .then(response => response.blob())
    .then(blob => {
      const downloadUrl = window.URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = downloadUrl
      link.download = `favorites.${format}`
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
      window.URL.revokeObjectURL(downloadUrl)
    })
}
