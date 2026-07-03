import { ref } from 'vue'
import request from '../../utils/request'

const API = '/api/apps/notebook'

export interface Source {
  id: number
  filename: string
  file_type: string
  file_size: number
  total_pages: number
  status: string
  summary: string
  key_topics: string[]
  created_at: string | null
  parsed_content?: string
}

export function useNotebook() {
  const sources = ref<Source[]>([])
  const loading = ref(false)
  const currentSource = ref<Source | null>(null)

  async function fetchSources() {
    loading.value = true
    try {
      const { data } = await request.get(`${API}/sources`)
      sources.value = data
    } finally {
      loading.value = false
    }
  }

  async function fetchSourceDetail(id: number) {
    const { data } = await request.get(`${API}/sources/${id}`)
    currentSource.value = data
    return data
  }

  async function uploadFile(file: File, onProgress?: (pct: number) => void): Promise<Source> {
    const formData = new FormData()
    formData.append('file', file)
    const { data } = await request.post(`${API}/sources/upload`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress(e: any) {
        if (onProgress && e.total) {
          onProgress(Math.round((e.loaded / e.total) * 100))
        }
      },
    })
    return data
  }

  async function pollStatus(id: number, interval = 3000): Promise<string> {
    return new Promise((resolve) => {
      const timer = setInterval(async () => {
        try {
          const { data } = await request.get(`${API}/sources/${id}/status`)
          if (data.status === 'ready' || data.status === 'failed') {
            clearInterval(timer)
            resolve(data.status)
          }
        } catch {
          clearInterval(timer)
          resolve('failed')
        }
      }, interval)
    })
  }

  async function deleteSource(id: number) {
    await request.delete(`${API}/sources/${id}`)
    sources.value = sources.value.filter(s => s.id !== id)
    if (currentSource.value?.id === id) {
      currentSource.value = null
    }
  }

  return {
    sources,
    loading,
    currentSource,
    fetchSources,
    fetchSourceDetail,
    uploadFile,
    pollStatus,
    deleteSource,
  }
}
