/**
 * useCitationRenderer — 引用标记 Markdown 渲染 composable
 *
 * 将 [ref_xxx] 引用标记保护后渲染 Markdown，再替换为可交互的 citation span。
 * 统一替代 MessageItem.vue 中 renderBrief/renderOutlineHeadings/renderSubtaskContent
 * 三处重复的引用处理逻辑。
 *
 * 创建日期：2026-04-04
 */

import MarkdownIt from 'markdown-it'
import { sanitizeCitationHtml } from '../utils/sanitize'
import type { Reference } from '../types/super-agent'

const md = new MarkdownIt({ html: true, breaks: true, linkify: true })

function escapeAttr(val: any): string {
  if (val == null) return ''
  const str = String(val)
  return str.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

/**
 * 渲染带引用标记的 Markdown 内容
 *
 * @param content - 原始 Markdown 文本（含 [ref_xxx] 标记）
 * @param references - 引用列表
 * @param prefix - 占位符前缀（避免多次渲染冲突）
 * @returns 渲染后的安全 HTML
 */
export function renderCitationMarkdown(
  content: string,
  references: Reference[],
  prefix: string = 'CITE'
): string {
  if (!content?.trim()) return ''

  const citations = new Map<string, string>()
  let citeIdx = 0

  // 1. 用占位符保护 [ref_xxx] 引用，防止 markdown-it 当作链接引用
  const safe = content.replace(/\[(?:ref:)?(ref_([\w]+))\]/g, (_, refId, num) => {
    citeIdx++
    const displayNum = /^\d+$/.test(num) ? num : citeIdx
    const ph = `${prefix}${citeIdx}END`
    const ref = references.find(r => r.id === refId)
    if (ref) {
      citations.set(ph, `<span class="citation-mark" data-ref-id="${refId}" data-ref-title="${escapeAttr(ref.title)}" data-ref-authors="${escapeAttr(ref.authors?.slice(0, 3).join(', '))}" data-ref-year="${escapeAttr(ref.year)}" data-ref-source="${escapeAttr(ref.source || ref.data_source)}" data-ref-url="${escapeAttr(ref.url)}" data-ref-abstract="${escapeAttr(ref.abstract)}">[${displayNum}]</span>`)
    } else {
      citations.set(ph, `<span class="citation-mark" data-ref-id="${refId}">[${displayNum}]</span>`)
    }
    return ph
  })

  // 2. 修复常见 markdown 格式问题：###标题 缺空格
  const fixed = safe.replace(/^(#{1,4})([^\s#])/gm, '$1 $2')

  // 3. Markdown 渲染
  let html = md.render(fixed)

  // 4. 替换回 citation HTML
  citations.forEach((markup, ph) => {
    html = html.split(ph).join(markup)
  })

  return sanitizeCitationHtml(html)
}

/**
 * 渲染大纲内容（清理 LLM 幻觉 JSON 块 + 提取标题后的内容）
 */
export function renderOutlineMarkdown(
  content: string,
  references: Reference[]
): string {
  // 清理伪 tool call JSON 块
  const cleaned = content
    .replace(/```(?:json)?\s*\{[\s\S]*?\}\s*```/g, '')
    .replace(/\{\s*"tool"\s*:[\s\S]*?\}\s*/g, '')

  const lines = cleaned.split('\n')
  const firstHeadingIdx = lines.findIndex(l => /^#{1,3}\s/.test(l.trim()))
  const body = firstHeadingIdx >= 0 ? lines.slice(firstHeadingIdx).join('\n') : cleaned.trim()

  if (!body) return ''
  return renderCitationMarkdown(body, references, 'OUTLINE')
}

/**
 * 渲染子任务内容（清理 tool_call 标签 + 修复换行）
 */
export function renderSubtaskMarkdown(
  content: string,
  references: Reference[]
): string {
  let cleaned = content
    .replace(/<tool_call>[\s\S]*?<\/tool_call>/g, '')
    .replace(/<tool_call>\s*\{[\s\S]*?\}\s*/g, '')
    .replace(/<\/?tool_call>/g, '')
    .replace(/```(?:json|xml)?\s*\{\s*"(?:name|tool)"[\s\S]*?\}\s*```/g, '')
    .trim()

  if (!cleaned) return ''

  // 修复标题前缺空行
  cleaned = cleaned.replace(/([^\n])(#{1,6}\s)/g, '$1\n\n$2')
  cleaned = cleaned.replace(/([^\n])\n(#{1,6}\s)/g, '$1\n\n$2')

  return renderCitationMarkdown(cleaned, references, 'SUBTASK')
}
