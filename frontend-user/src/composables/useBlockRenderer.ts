/**
 * useBlockRenderer — ContentBlock 合并与渲染单元生成
 *
 * Phase 2.1b: 将 msg.blocks 中相邻的 TextBlock 合并为 MergedText 渲染单元，
 * 解决 Markdown 跨 block 断裂问题（代码块/列表被工具锚点打断）。
 *
 * 输入: [Text, Text, ToolAnchor, Text, StageProgress, Text]
 * 输出: [MergedText("ab"), ToolAnchor, MergedText("c"), StageProgress, MergedText("d")]
 */

import { computed, type Ref } from 'vue'
import type { ContentBlock } from '../types/content-block'

export interface MergedTextUnit {
  type: 'merged_text'
  /** 合并后的完整 Markdown 内容 */
  content: string
  /** 原始 block 的 _id 组合（用作 Vue key） */
  key: string
  /** 是否是最后一个文本单元（流式中显示光标） */
  isLast: boolean
  /** 是否仍在流式中（最后一个 text block 的 _complete === false） */
  isStreaming: boolean
}

export type RenderUnit = MergedTextUnit | (ContentBlock & { _id?: string; _complete?: boolean })

export function useBlockRenderer(blocks: Ref<ContentBlock[] | undefined>) {
  const renderUnits = computed((): RenderUnit[] => {
    const src = blocks.value
    if (!src || src.length === 0) return []

    const units: RenderUnit[] = []
    let textBuf: Array<ContentBlock & { _id?: string; _complete?: boolean }> = []

    function flushText(isLast: boolean) {
      if (textBuf.length === 0) return
      const content = textBuf.map(b => (b as any).content || '').join('')
        .replace(/<think>[\s\S]*?<\/think>/g, '')
        .replace(/<think>[^\n]*\n?/g, '')
      const key = textBuf.map(b => (b as any)._id || '').join('-')
      const lastBlock = textBuf[textBuf.length - 1]
      units.push({
        type: 'merged_text',
        content,
        key,
        isLast,
        isStreaming: isLast && (lastBlock as any)?._complete === false,
      })
      textBuf = []
    }

    for (const block of src) {
      const b = block as any
      // 跳过 visible=false 的 block（invisible 阶段已隐藏）
      if (b.visible === false) continue

      if (b.type === 'text') {
        textBuf.push(b)
      } else {
        flushText(false)
        units.push(b)
      }
    }
    flushText(true) // 最后一组 text 标记为 isLast

    return units
  })

  return { renderUnits }
}
