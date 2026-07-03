/**
 * 引用渲染工具函数
 *
 * 将 Markdown 文本中的 [ref_XXX] 标记渲染为 HTML 角标。
 * 两阶段 API：
 *   1. protectCitationMarks() — 用占位符保护 [ref_XXX]（避免 Markdown 渲染器误解析）
 *   2. restoreCitationMarks() — 还原占位符为 <span class="citation-mark" data-ref-*>[N]</span>
 *
 * 从 StreamingContent.vue 提取，供 ArtifactEditor / 预览卡片等复用。
 */

import type { Reference } from '@/types/super-agent'

export interface ProtectResult {
  content: string
  placeholders: Map<string, string>  // placeholder → refId
}

/**
 * 阶段一：将 [ref_xxx] 标记替换为占位符，防止 markdown-it 误解析。
 *
 * 支持格式：
 * - [ref_001]         标准格式
 * - [ref:ref_001]     备选格式
 * - [ref_006评论区]    LLM 幻觉容错
 * - [6]               纯数字（排除 Markdown 链接 [n](url)）
 * - *CITATION_ref_001* 星号包裹格式
 */
export function protectCitationMarks(content: string): ProtectResult {
  const placeholders = new Map<string, string>()

  // 占位符不能含下划线，否则相邻占位符的 _ 会被 markdown-it 解析为斜体

  // Tiptap markdown 序列化会将 [ 转义为 \[，先精确还原引用模式的转义
  // 只还原 \[ref_xxx...\] 和 \[数字\] 形式，不影响用户有意书写的其他 \[...\]
  let processed = content
    // 还原 \[ref_xxx\] / \[ref:ref_xxx\] / \[ref_006评论区\] → 去掉反斜杠
    .replace(/\\\[((?:ref:)?ref_\d+(?:[^\\\]]*?))\\\]/g, '[$1]')
    // 还原 \[数字\] → [数字]（排除链接 \[n\](url)）
    .replace(/\\\[(\d{1,3})\\\](?!\()/g, '[$1]')

  processed = processed
    // 标准格式: [ref_001] 或 [ref:ref_001]
    .replace(/\[(?:ref:)?(ref_\d+)\]/g, (_match, refId) => {
      const placeholder = `REFMARK${refId.replace('_', '')}ENDMARK`
      placeholders.set(placeholder, refId)
      return placeholder
    })
    // LLM 幻觉容错: [ref_006评论区] → 提取 ref_006
    .replace(/\[(ref_\d+)[^\]]+\]/g, (_match, refId) => {
      const placeholder = `REFMARK${refId.replace('_', '')}ENDMARK`
      if (!placeholders.has(placeholder)) {
        placeholders.set(placeholder, refId)
      }
      return placeholder
    })
    // 纯数字引用容错: [6] → 映射到 ref_006（排除 markdown 链接 [n](url)）
    .replace(/\[(\d{1,3})\](?!\()/g, (_match, num) => {
      const n = parseInt(num, 10)
      if (n < 1 || n > 999) return _match
      const refId = `ref_${num.padStart(3, '0')}`
      const placeholder = `REFMARK${refId.replace('_', '')}ENDMARK`
      if (!placeholders.has(placeholder)) {
        placeholders.set(placeholder, refId)
      }
      return placeholder
    })
    .replace(/\*{0,2}CITATION_(ref_\d+)\*{0,2}/g, (_match, refId) => {
      const placeholder = `REFMARK${refId.replace('_', '')}ENDMARK`
      placeholders.set(placeholder, refId)
      return placeholder
    })

  return { content: processed, placeholders }
}

/** 查找引用元数据 */
function findReferenceById(refId: string, references: Reference[]): Reference | undefined {
  return references.find(r => r.id === refId)
}

/** HTML 属性转义 */
function escapeAttr(str: unknown): string {
  const s = String(str ?? '')
  return s.replace(/"/g, '&quot;').replace(/'/g, '&#39;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

/**
 * 阶段二：将占位符还原为 HTML 角标 span。
 *
 * 在 markdown-it 渲染后调用，将 REFMARK...ENDMARK 替换为
 * <span class="citation-mark" data-ref-*>[N]</span>
 */
export function restoreCitationMarks(
  html: string,
  placeholders: Map<string, string>,
  references: Reference[],
): string {
  placeholders.forEach((refId, placeholder) => {
    const ref = findReferenceById(refId, references)
    const num = parseInt(refId.replace('ref_', ''), 10)

    if (ref) {
      const authors = ref.authors?.length ? ref.authors.slice(0, 3).join(', ') : ''
      const year = ref.year ? String(ref.year) : ''
      const source = ref.source || ref.data_source || ''
      const url = ref.url || ''
      const abstract = ref.abstract || ''

      const citation = `<span class="citation-mark" data-ref-id="${refId}" data-ref-title="${escapeAttr(ref.title)}" data-ref-authors="${escapeAttr(authors)}" data-ref-year="${escapeAttr(year)}" data-ref-source="${escapeAttr(source)}" data-ref-url="${escapeAttr(url)}" data-ref-abstract="${escapeAttr(abstract)}">[${num}]</span>`
      html = html.split(placeholder).join(citation)
    } else {
      // 引用不存在时渲染为上标样式 [N]（无元数据但视觉一致）
      html = html.split(placeholder).join(`<span class="citation-mark" data-ref-id="${refId}">[${num}]</span>`)
    }
  })
  return html
}

/**
 * 快捷方法：检测内容是否包含引用标记模式
 */
export function hasRefPattern(content: string): boolean {
  return /\\?\[(?:ref:)?ref_\d+\\?\]|\\?\[\d{1,3}\\?\](?!\()/.test(content)
}
