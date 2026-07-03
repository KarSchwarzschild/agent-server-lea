/**
 * Artifact 系统前端类型定义
 *
 * 包含：文档类型、版本、模板、高亮选区、场景元信息
 */

// ==================== 文档类别 ====================

export type DocCategory = 'general' | 'official' | 'academic' | 'teaching' | 'report'

export const DOC_CATEGORY_META: Record<DocCategory, {
  label: string
  icon: string
  highlightActions: HighlightPreset[]
}> = {
  general: {
    label: '通用文档', icon: 'document-text',
    highlightActions: ['polish', 'translate', 'simplify', 'expand', 'custom'],
  },
  official: {
    label: '公文', icon: 'ribbon',
    highlightActions: ['formalize', 'polish', 'simplify', 'check-format', 'custom'],
  },
  academic: {
    label: '学术文档', icon: 'school',
    highlightActions: ['academize', 'translate', 'add-citation', 'deAI', 'custom'],
  },
  teaching: {
    label: '教学文档', icon: 'book',
    highlightActions: ['polish', 'simplify', 'adjust-level', 'custom'],
  },
  report: {
    label: '报告', icon: 'stats-chart',
    highlightActions: ['polish', 'add-data', 'simplify', 'formalize', 'custom'],
  },
}

// ==================== 高亮操作 ====================

export type HighlightPreset =
  | 'polish'
  | 'translate'
  | 'simplify'
  | 'expand'
  | 'formalize'
  | 'academize'
  | 'add-citation'
  | 'deAI'
  | 'check-format'
  | 'adjust-level'
  | 'add-data'
  | 'custom'

export const HIGHLIGHT_PRESET_LABELS: Record<HighlightPreset, string> = {
  polish: '润色',
  translate: '翻译',
  simplify: '精简',
  expand: '扩写',
  formalize: '公文化',
  academize: '学术化',
  'add-citation': '补引用',
  deAI: '降AI痕迹',
  'check-format': '格式检查',
  'adjust-level': '调难度',
  'add-data': '补数据',
  custom: '自定义...',
}

export const HIGHLIGHT_PRESET_PROMPTS: Record<Exclude<HighlightPreset, 'custom'>, string> = {
  polish: '请润色这段文字，使表达更流畅、更专业',
  translate: '请将选中的内容翻译为英文，保持原文风格',
  simplify: '请精简选中的内容，保留核心观点，减少冗余表述',
  expand: '请扩写选中的内容，补充细节和论据，使论述更充分',
  formalize: '请将选中的内容改写为正式公文用语，符合机关公文写作规范',
  academize: '请将选中的内容改写为学术论文风格，使用规范的学术用语',
  'add-citation': '请为选中的论述补充相关学术引用（如有必要可标注[待补充]）',
  deAI: '请改写选中的内容，降低 AI 生成痕迹，使其更像人类自然书写',
  'check-format': '请检查选中内容的格式是否符合公文规范，如有问题请修正',
  'adjust-level': '请将选中内容的难度调整为大学本科水平，确保专业术语有解释',
  'add-data': '请为选中的论述补充具体的数据、案例或实例支撑',
}

// ==================== 版本来源 ====================

export type ArtifactSource = 'ai_generate' | 'ai_rewrite' | 'ai_highlight' | 'user_edit' | 'template' | 'restored'

export const SOURCE_META: Record<ArtifactSource, { label: string; icon: string }> = {
  ai_generate: { label: 'AI 生成', icon: '🤖' },
  ai_rewrite: { label: 'AI 重写', icon: '🔄' },
  ai_highlight: { label: 'AI 高亮', icon: '✨' },
  user_edit: { label: '手动编辑', icon: '✏️' },
  template: { label: '模板', icon: '📋' },
  restored: { label: '已恢复', icon: '⏪' },
}

// ==================== 核心数据结构 ====================

export interface Artifact {
  id: string
  sessionId: string
  userId: string
  title: string
  artifactType: 'markdown' | 'code'
  docCategory: DocCategory
  templateId?: string
  currentIndex: number
  contents: ArtifactVersion[]
  isPinned: boolean
  isStandalone: boolean
  createdAt: string
  updatedAt: string
}

export interface ArtifactVersion {
  id: number
  artifactId: string
  versionIndex: number
  content: string
  title: string
  source: ArtifactSource
  changeSummary?: string
  metadata?: Record<string, any>
  createdAt: string
}

export interface ArtifactTemplate {
  id: string
  name: string
  docCategory: DocCategory
  content: string
  description?: string
  promptHint?: string
  sortOrder: number
  isSystem: boolean
  userId?: string
  createdAt: string
}

// ==================== 选区数据 ====================

export interface TextHighlight {
  fullMarkdown: string
  markdownBlock: string
  selectedText: string
}

// ==================== SSE 事件 payload ====================

export interface ArtifactCreateEvent {
  artifact_id: string
  title: string
  artifact_type: string
  doc_category?: string
  version_index: number
  seq: number
}

export interface ArtifactDeltaEvent {
  artifact_id: string
  delta: string
  version_index: number
  source?: string
  seq: number
}

export interface ArtifactDoneEvent {
  artifact_id: string
  version_index: number
  content?: string
  checksum?: string
  seq: number
}

// ==================== 面板状态 ====================

export type ArtifactPanelMode =
  | 'hidden'        // 面板隐藏
  | 'skeleton'      // 骨架屏（等待 artifact_create）
  | 'streaming'     // 流式渲染中
  | 'readonly'      // 只读预览（生成完成）
  | 'editing'       // 编辑模式（Phase 3）
  | 'version-browse' // 浏览历史版本

// ==================== 工具函数 ====================

export function getArtifactContent(artifact: Artifact): ArtifactVersion | undefined {
  return artifact.contents.find(v => v.versionIndex === artifact.currentIndex)
    ?? artifact.contents[artifact.contents.length - 1]
}

export function getContentWordCount(content: string): number {
  // 中文按字计数 + 英文按词计数
  const chineseChars = (content.match(/[\u4e00-\u9fff]/g) || []).length
  const englishWords = content.replace(/[\u4e00-\u9fff]/g, '').split(/\s+/).filter(Boolean).length
  return chineseChars + englishWords
}
