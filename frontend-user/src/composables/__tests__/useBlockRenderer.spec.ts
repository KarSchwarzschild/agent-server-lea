import { describe, it, expect } from 'vitest'
import { ref } from 'vue'
import { useBlockRenderer, type RenderUnit, type MergedTextUnit } from '../useBlockRenderer'
import type { ContentBlock } from '../../types/content-block'

function text(content: string, id?: string, complete = true): any {
  return { type: 'text', content, _id: id || `blk_${Math.random().toString(36).slice(2, 6)}`, _complete: complete }
}

function toolAnchor(toolCallId: string, id?: string): any {
  return { type: 'tool_anchor', toolCallId, _id: id || `blk_${Math.random().toString(36).slice(2, 6)}`, _complete: true }
}

function stageProgress(stageName: string, index: number, total: number, title: string): any {
  return { type: 'stage_progress', stageName, index, total, title, _id: `blk_${Math.random().toString(36).slice(2, 6)}`, _complete: true }
}

function stageOutput(stageName: string, content: string, visible = true): any {
  return { type: 'stage_output', stageName, content, visible, _id: `blk_${Math.random().toString(36).slice(2, 6)}`, _complete: true }
}

describe('useBlockRenderer', () => {
  it('合并相邻 TextBlock', () => {
    const blocks = ref<ContentBlock[]>([text('a', 'b1'), text('b', 'b2')])
    const { renderUnits } = useBlockRenderer(blocks)

    expect(renderUnits.value).toHaveLength(1)
    expect(renderUnits.value[0].type).toBe('merged_text')
    expect((renderUnits.value[0] as MergedTextUnit).content).toBe('ab')
    expect((renderUnits.value[0] as MergedTextUnit).key).toContain('b1')
    expect((renderUnits.value[0] as MergedTextUnit).key).toContain('b2')
  })

  it('ToolAnchor 打断 TextBlock 合并', () => {
    const blocks = ref<ContentBlock[]>([
      text('a', 'b1'), toolAnchor('tc_1', 'b2'), text('b', 'b3'),
    ])
    const { renderUnits } = useBlockRenderer(blocks)

    expect(renderUnits.value).toHaveLength(3)
    expect(renderUnits.value[0].type).toBe('merged_text')
    expect((renderUnits.value[0] as MergedTextUnit).content).toBe('a')
    expect(renderUnits.value[1].type).toBe('tool_anchor')
    expect(renderUnits.value[2].type).toBe('merged_text')
    expect((renderUnits.value[2] as MergedTextUnit).content).toBe('b')
  })

  it('ToolAnchor 在开头', () => {
    const blocks = ref<ContentBlock[]>([toolAnchor('tc_1'), text('a')])
    const { renderUnits } = useBlockRenderer(blocks)

    expect(renderUnits.value).toHaveLength(2)
    expect(renderUnits.value[0].type).toBe('tool_anchor')
    expect(renderUnits.value[1].type).toBe('merged_text')
  })

  it('单个 TextBlock 流式中标记 isLast + isStreaming', () => {
    const blocks = ref<ContentBlock[]>([text('streaming...', 'b1', false)])
    const { renderUnits } = useBlockRenderer(blocks)

    expect(renderUnits.value).toHaveLength(1)
    const mt = renderUnits.value[0] as MergedTextUnit
    expect(mt.isLast).toBe(true)
    expect(mt.isStreaming).toBe(true)
  })

  it('空数组返回空', () => {
    const blocks = ref<ContentBlock[]>([])
    const { renderUnits } = useBlockRenderer(blocks)
    expect(renderUnits.value).toHaveLength(0)
  })

  it('undefined 返回空', () => {
    const blocks = ref<ContentBlock[] | undefined>(undefined)
    const { renderUnits } = useBlockRenderer(blocks)
    expect(renderUnits.value).toHaveLength(0)
  })

  it('非 text block 不被合并', () => {
    const blocks = ref<ContentBlock[]>([
      stageProgress('analyze', 1, 4, '分析'),
      stageOutput('analyze', '分析内容'),
      text('a', 'b1'),
      text('b', 'b2'),
    ])
    const { renderUnits } = useBlockRenderer(blocks)

    expect(renderUnits.value).toHaveLength(3)
    expect(renderUnits.value[0].type).toBe('stage_progress')
    expect(renderUnits.value[1].type).toBe('stage_output')
    expect(renderUnits.value[2].type).toBe('merged_text')
    expect((renderUnits.value[2] as MergedTextUnit).content).toBe('ab')
  })

  it('跨 block 代码块合并后 Markdown 连续', () => {
    const blocks = ref<ContentBlock[]>([
      text('```python\ndef f():', 'b1'),
      text('\n  pass\n```', 'b2'),
    ])
    const { renderUnits } = useBlockRenderer(blocks)

    expect(renderUnits.value).toHaveLength(1)
    const merged = (renderUnits.value[0] as MergedTextUnit).content
    expect(merged).toContain('```python')
    expect(merged).toContain('pass')
    expect(merged.match(/```/g)).toHaveLength(2) // 开闭都在
  })

  it('visible=false 的 block 被跳过', () => {
    const blocks = ref<ContentBlock[]>([
      text('可见内容', 'b1'),
      stageOutput('analyze', '不可见', false),
      text('继续', 'b3'),
    ])
    const { renderUnits } = useBlockRenderer(blocks)

    // stageOutput visible=false 被跳过，两个 text 合并
    expect(renderUnits.value).toHaveLength(1)
    expect((renderUnits.value[0] as MergedTextUnit).content).toBe('可见内容继续')
  })

  it('最后一组 text 的 isLast=true，中间的 isLast=false', () => {
    const blocks = ref<ContentBlock[]>([
      text('a'), toolAnchor('tc_1'), text('b'),
    ])
    const { renderUnits } = useBlockRenderer(blocks)

    expect((renderUnits.value[0] as MergedTextUnit).isLast).toBe(false)
    expect((renderUnits.value[2] as MergedTextUnit).isLast).toBe(true)
  })
})
