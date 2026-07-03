import { describe, it, expect } from 'vitest'
import { normalizeMessage } from '../normalizeMessage'
import type { ContentBlock } from '../../types/content-block'

describe('normalizeMessage', () => {
  it('新格式：直接返回 blocks', () => {
    const blocks: ContentBlock[] = [{ type: 'text', content: 'hello' }]
    const result = normalizeMessage({ blocks, content: 'ignored' })
    expect(result).toBe(blocks)
  })

  it('纯文本无标记：包装为单个 TextBlock', () => {
    const result = normalizeMessage({ content: '纯文本无标记' })
    expect(result).toHaveLength(1)
    expect(result[0].type).toBe('text')
    expect((result[0] as any).content).toBe('纯文本无标记')
  })

  it('空内容', () => {
    const result = normalizeMessage({ content: '' })
    expect(result).toHaveLength(1)
    expect(result[0].type).toBe('text')
    expect((result[0] as any).content).toBe('')
  })

  it('TC 锚点解析', () => {
    const result = normalizeMessage({ content: 'abc<!-- TC:t1 -->def' })
    expect(result.length).toBeGreaterThanOrEqual(2)

    const types = result.map(b => b.type)
    expect(types).toContain('text')
    expect(types).toContain('tool_anchor')

    const anchor = result.find(b => b.type === 'tool_anchor') as any
    expect(anchor.toolCallId).toBe('t1')
  })

  it('STAGE_CONTENT_START 解析', () => {
    const result = normalizeMessage({
      content: '前文<!-- STAGE_CONTENT_START:analyze -->分析内容',
    })
    const types = result.map(b => b.type)
    expect(types).toContain('stage_output')

    const stage = result.find(b => b.type === 'stage_output') as any
    expect(stage.stageName).toBe('analyze')
  })

  it('STAGE_PROGRESS 解析', () => {
    const result = normalizeMessage({
      content: '<!-- STAGE_PROGRESS:write:2:4:撰写正文 -->',
    })
    const progress = result.find(b => b.type === 'stage_progress') as any
    expect(progress).toBeTruthy()
    expect(progress.stageName).toBe('write')
  })

  it('多种标记混合', () => {
    const result = normalizeMessage({
      content: '开头<!-- TC:tc_1 -->中间<!-- STAGE_CONTENT_START:analyze -->结尾',
    })
    expect(result.length).toBeGreaterThanOrEqual(3)

    const types = result.map(b => b.type)
    expect(types).toContain('tool_anchor')
    expect(types).toContain('stage_output')
    expect(types).toContain('text')
  })

  it('多次调用同一输入结果一致（/g 正则 bug 修复验证）', () => {
    const input = { content: 'abc<!-- TC:t1 -->def' }
    const r1 = normalizeMessage(input)
    const r2 = normalizeMessage(input)
    const r3 = normalizeMessage(input)

    expect(r1.length).toBe(r2.length)
    expect(r2.length).toBe(r3.length)
    expect(r1.map(b => b.type)).toEqual(r2.map(b => b.type))
    expect(r2.map(b => b.type)).toEqual(r3.map(b => b.type))
  })

  it('无 blocks 字段时走旧路径', () => {
    const result = normalizeMessage({ content: 'hello' })
    expect(result).toHaveLength(1)
    expect(result[0].type).toBe('text')
  })

  it('空 blocks 数组走旧路径', () => {
    const result = normalizeMessage({ blocks: [], content: 'fallback' })
    expect(result).toHaveLength(1)
    expect((result[0] as any).content).toBe('fallback')
  })
})
