/**
 * Artifact API 客户端
 *
 * 注意：request 拦截器已返回 response.data，不需要再解构 { data }
 */

import request from '../utils/request'
import type { Artifact, ArtifactVersion, ArtifactTemplate } from '@/types/artifact'
import type { AnchorTriple } from '@/composables/useAnchorLocator'
import { parseSSEStream } from '@/composables/useStreamParser'

const BASE = '/api/user/artifacts'
const TMPL_BASE = '/api/user/templates'

// ==================== Artifact CRUD ====================

export async function getArtifact(artifactId: string): Promise<Artifact> {
  return await request.get(`${BASE}/${artifactId}`)
}

/**
 * 按 artifact_id 从 MinIO 拉原始 payload（用于 store lazy-fetch）。
 * 后端: GET /api/chat/artifacts/{artifact_id} —— slides JSON 全量
 */
export async function fetchArtifactById(artifactId: string): Promise<any> {
  return await request.get(`/api/user/chat/artifacts/${artifactId}`)
}

export async function getSessionArtifacts(sessionId: string, includeVersions = false): Promise<Artifact[]> {
  return await request.get(`${BASE}/session/${sessionId}`, {
    params: includeVersions ? { include_versions: true } : {},
  })
}

export async function updateArtifact(
  artifactId: string,
  updates: {
    title?: string
    currentIndex?: number
    isPinned?: boolean
    isStandalone?: boolean
    docCategory?: string
  },
): Promise<Artifact> {
  return await request.put(`${BASE}/${artifactId}`, {
    title: updates.title,
    current_index: updates.currentIndex,
    is_pinned: updates.isPinned,
    is_standalone: updates.isStandalone,
    doc_category: updates.docCategory,
  })
}

export async function deleteArtifact(artifactId: string): Promise<void> {
  await request.delete(`${BASE}/${artifactId}`)
}

// ==================== 版本管理 ====================

export async function createVersion(
  artifactId: string,
  body: { content: string; source?: string; change_summary?: string },
): Promise<ArtifactVersion> {
  return await request.post(`${BASE}/${artifactId}/versions`, body)
}

export async function getVersion(artifactId: string, versionIndex: number): Promise<ArtifactVersion> {
  return await request.get(`${BASE}/${artifactId}/versions/${versionIndex}`)
}

export async function restoreVersion(artifactId: string, versionIndex: number): Promise<ArtifactVersion> {
  return await request.post(`${BASE}/${artifactId}/restore`, { version_index: versionIndex })
}

export interface DiffOp {
  type: 'equal' | 'insert' | 'delete'
  content: string
}

export async function getVersionDiff(
  artifactId: string,
  fromVersion: number,
  toVersion: number,
): Promise<{ from_version: number; to_version: number; ops: DiffOp[]; stats: { inserts: number; deletes: number; unchanged: number } }> {
  return await request.get(`${BASE}/${artifactId}/diff`, {
    params: { from: fromVersion, to: toVersion },
  })
}

// ==================== 导出 ====================

export async function exportArtifact(
  artifactId: string,
  format: 'docx' | 'pdf',
  style?: string,
): Promise<{ file_id: string; filename: string; note?: string }> {
  return await request.post(
    `${BASE}/${artifactId}/export/${format}`,
    null,
    { params: style ? { style } : {} },
  )
}

// ==================== 文档中心 ====================

export async function listDocuments(params?: {
  category?: string
  limit?: number
  offset?: number
}): Promise<{ items: Artifact[]; total: number }> {
  return await request.get(BASE, { params })
}

// ==================== 模板 ====================

export async function listTemplates(category?: string): Promise<ArtifactTemplate[]> {
  return await request.get(TMPL_BASE, { params: category ? { category } : {} })
}

export async function createTemplate(body: {
  name: string
  doc_category: string
  content: string
  description?: string
  prompt_hint?: string
}): Promise<ArtifactTemplate> {
  return await request.post(TMPL_BASE, body)
}

export async function deleteTemplate(templateId: string): Promise<void> {
  await request.delete(`${TMPL_BASE}/${templateId}`)
}

// ==================== AI 操作（SSE 流式） ====================
//
// 设计：AI 修改 ≠ 保存。
// 后端只返回 AI 修改后的内容（不创建版本），
// 前端进入编辑模式预览，用户确认保存时才调 createVersion。
//

export interface AIEditSSECallbacks {
  onStart?: (data: { artifact_id: string; instruction: string }) => void
  onDelta?: (data: { delta: string }) => void
  onDone?: (data: { artifact_id: string; content: string; change_summary: string }) => void
  onError?: (error: string) => void
}

/**
 * 通用 AI 编辑 SSE 流消费器
 * 事件：ai_start → ai_delta x N → ai_done
 */
async function consumeAIEditSSE(
  url: string,
  body: Record<string, any>,
  callbacks: AIEditSSECallbacks,
): Promise<void> {
  const token = localStorage.getItem('token') || ''
  const baseURL = import.meta.env.VITE_API_BASE_URL || ''

  const response = await fetch(`${baseURL}${url}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`,
      'Accept': 'text/event-stream',
    },
    body: JSON.stringify(body),
  })

  if (!response.ok) {
    const text = await response.text().catch(() => '')
    throw new Error(`HTTP ${response.status}: ${text}`)
  }

  const reader = response.body?.getReader()
  if (!reader) throw new Error('响应体为空')

  await parseSSEStream(reader, {
    onEvent: (event, data) => {
      switch (event) {
        case 'ai_start':
          callbacks.onStart?.(data as any)
          break
        case 'ai_delta':
          callbacks.onDelta?.(data as any)
          break
        case 'ai_done':
          callbacks.onDone?.(data as any)
          break
        case 'error':
          callbacks.onError?.((data as any).message || '未知错误')
          break
      }
    },
  })
}

/**
 * 高亮局部重写：选区 → AI 重写 → 返回新全文（不入库）
 * 前端收到后进入编辑模式，用户确认保存时才创建版本。
 */
export async function highlightRewrite(
  artifactId: string,
  body: { anchor: AnchorTriple; instruction: string },
  callbacks: AIEditSSECallbacks,
): Promise<void> {
  return consumeAIEditSSE(`${BASE}/${artifactId}/highlight`, body, callbacks)
}

/**
 * 面板 AI 指令：根据自然语言指令修改文档（不入库）
 * 前端收到后进入编辑模式，用户确认保存时才创建版本。
 */
export async function aiEdit(
  artifactId: string,
  body: { instruction: string; anchor?: AnchorTriple | null },
  callbacks: AIEditSSECallbacks,
): Promise<void> {
  return consumeAIEditSSE(`${BASE}/${artifactId}/ai-edit`, body, callbacks)
}
