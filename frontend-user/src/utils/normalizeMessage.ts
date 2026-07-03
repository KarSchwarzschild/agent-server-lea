/**
 * normalizeMessage adapter
 *
 * 将旧格式消息（msg.content 纯字符串 + HTML 注释标记）转换为 ContentBlock[]。
 * 新格式消息（msg.blocks 已是 ContentBlock[]）直接返回。
 *
 * 用于历史消息兼容 —— 数据库中旧消息不迁移，渲染时由此 adapter 实时转换。
 *
 * 处理的标记类型：
 * 1. <!-- STAGE_CONTENT_START:${stage} -->  → StageOutputBlock
 * 2. <!-- TC:${id} -->                      → ToolAnchorBlock
 * 3. <!-- REASONING_BLOCK_${id} -->         → ReasoningBlock
 * 4. <!-- STAGE_PROGRESS:${stage}:... -->   → StageProgressBlock
 *
 * 创建日期：2026-04-04
 */

import type { ContentBlock } from '../types/content-block'

// 标记正则（不带 /g，避免模块级 lastIndex 状态问题；函数内部用 new RegExp() 加 /g）
const STAGE_START_RE = /<!-- STAGE_CONTENT_START:(\w[\w-]*) -->/
const TC_RE = /<!-- TC:([^\s>]+) -->/
const REASONING_RE = /<!-- REASONING_BLOCK_([^\s>]+) -->/
const STAGE_PROGRESS_RE = /<!-- STAGE_PROGRESS:(\w[\w-]*):(\d+):(\d+):([^>]*) -->/
const DIVIDER_RE = /\n---\n/

interface RawMessage {
  content?: string
  blocks?: ContentBlock[]
  [key: string]: unknown
}

/**
 * 将原始消息转换为 ContentBlock 数组
 *
 * @param msg - 原始消息对象
 * @returns ContentBlock[] - 结构化内容块
 */
export function normalizeMessage(msg: RawMessage): ContentBlock[] {
  // 路径 1：新格式，blocks 已存在
  if (msg.blocks && Array.isArray(msg.blocks) && msg.blocks.length > 0) {
    return msg.blocks
  }

  // 路径 2：旧格式，从 content 字符串解析
  const content = msg.content || ''
  if (!content.trim()) {
    return [{ type: 'text', content: '' }]
  }

  // 检查是否包含任何标记
  const hasMarkers = content.includes('<!-- STAGE_CONTENT_START:')
    || content.includes('<!-- TC:')
    || content.includes('<!-- REASONING_BLOCK_')
    || content.includes('<!-- STAGE_PROGRESS:')

  if (!hasMarkers) {
    // 纯文本，包装为单个 TextBlock
    return [{ type: 'text', content }]
  }

  return parseLegacyContent(content)
}

/**
 * 解析旧格式内容中的 HTML 注释标记，转为 ContentBlock[]
 */
function parseLegacyContent(content: string): ContentBlock[] {
  const blocks: ContentBlock[] = []

  // 收集所有标记的位置
  interface Marker {
    index: number
    length: number
    block: ContentBlock
  }
  const markers: Marker[] = []

  // 1. STAGE_CONTENT_START
  let match: RegExpExecArray | null
  const stageStartRe = new RegExp(STAGE_START_RE.source, 'g')
  while ((match = stageStartRe.exec(content)) !== null) {
    markers.push({
      index: match.index,
      length: match[0].length,
      block: { type: 'stage_output', stageName: match[1], visible: false, content: '' },
    })
  }

  // 2. TC anchors
  const tcRe = new RegExp(TC_RE.source, 'g')
  while ((match = tcRe.exec(content)) !== null) {
    markers.push({
      index: match.index,
      length: match[0].length,
      block: { type: 'tool_anchor', toolCallId: match[1] },
    })
  }

  // 3. REASONING_BLOCK
  const reasoningRe = new RegExp(REASONING_RE.source, 'g')
  while ((match = reasoningRe.exec(content)) !== null) {
    markers.push({
      index: match.index,
      length: match[0].length,
      block: { type: 'reasoning_block', content: '' },
    })
  }

  // 4. STAGE_PROGRESS
  const progressRe = new RegExp(STAGE_PROGRESS_RE.source, 'g')
  while ((match = progressRe.exec(content)) !== null) {
    markers.push({
      index: match.index,
      length: match[0].length,
      block: {
        type: 'stage_progress',
        stageName: match[1],
        index: parseInt(match[2], 10),
        total: parseInt(match[3], 10),
        title: match[4],
      },
    })
  }

  // 按位置排序
  markers.sort((a, b) => a.index - b.index)

  if (markers.length === 0) {
    return [{ type: 'text', content }]
  }

  // 遍历标记，提取间隔文本
  let cursor = 0
  for (const marker of markers) {
    // 标记前的文本
    if (marker.index > cursor) {
      const text = content.slice(cursor, marker.index).trim()
      if (text) {
        blocks.push({ type: 'text', content: text })
      }
    }
    blocks.push(marker.block)
    cursor = marker.index + marker.length
  }

  // 末尾文本
  if (cursor < content.length) {
    const text = content.slice(cursor).trim()
    if (text) {
      blocks.push({ type: 'text', content: text })
    }
  }

  return blocks.length > 0 ? blocks : [{ type: 'text', content }]
}
