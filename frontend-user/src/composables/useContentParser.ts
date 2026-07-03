/**
 * useContentParser — 消息内容解析 composable
 *
 * 从 MessageItem.vue 提取的内容解析逻辑（Phase 2.4）。
 * 根据 outputFormat 解析不同格式的内容：动画/交互/内联工具/标准。
 *
 * 职责：
 * - HTML 动画/交互代码提取
 * - Quiz 数据提取
 * - 内容过滤（移除代码块/状态注释/quiz块）
 * - 内联标记拆分（TC 锚点 + STAGE_PROGRESS 占位）
 * - 引导进度提取
 */

import { computed, type Ref } from 'vue'
import type { ChatMessage } from '../types/super-agent'
import { extractHtmlBlock } from './useHtmlSandbox'

export interface QuizData {
  question: string
  answer: string | number
  options?: string[]
  explanation?: string
  [key: string]: unknown
}

export type InlineContentBlock =
  | { type: 'text'; text: string }
  | { type: 'tool'; toolCallId: string }
  | { type: 'progress'; stageName: string; progress: string; title: string }

export function useContentParser(msg: Ref<ChatMessage> | (() => ChatMessage)) {
  const getMsg = typeof msg === 'function' ? msg : () => msg.value

  // ── 动画播放器 ──

  const isHtmlAnimation = computed(() => getMsg().outputFormat === 'html-animation')

  const extractedAnimationHtml = computed(() => {
    if (!isHtmlAnimation.value || !getMsg().content) return null
    return extractHtmlBlock(getMsg().content)
  })

  const animationStreamingCode = computed(() => {
    if (!isHtmlAnimation.value || !getMsg().isStreaming || !getMsg().content) return ''
    const match = getMsg().content.match(/```html\s*\n([\s\S]*)$/)
    return match ? match[1].slice(-800) : ''
  })

  // ── 交互式学习 ──

  const isHtmlInteractive = computed(() => getMsg().outputFormat === 'html-interactive')

  const extractedInteractiveHtml = computed(() => {
    if (!isHtmlInteractive.value || !getMsg().content) return null
    return extractHtmlBlock(getMsg().content)
  })

  const interactiveStreamingCode = computed(() => {
    if (!isHtmlInteractive.value || !getMsg().isStreaming || !getMsg().content) return ''
    const match = getMsg().content.match(/```html\s*\n([\s\S]*)$/)
    return match ? match[1].slice(-800) : ''
  })

  const filteredInteractiveContent = computed(() => {
    if (!isHtmlInteractive.value || !getMsg().content) return getMsg().content
    let result = getMsg().content
    // 移除 ```html 代码块（完整闭合 + 流式未闭合）
    result = result.replace(/```html\s*\n[\s\S]*?```/g, '')
    result = result.replace(/```html\s*\n[\s\S]*$/g, '')
    // 移除直接嵌入的完整 HTML 文档（含可能的孤立语言标记 "html"）
    result = result.replace(/\n*html\s*\n\s*<!DOCTYPE\s+html[\s\S]*<\/html>/gi, '')
    result = result.replace(/<!DOCTYPE\s+html[\s\S]*<\/html>/gi, '')
    // 移除流式场景中未闭合的 HTML 文档片段
    result = result.replace(/\n*html\s*\n\s*<!DOCTYPE\s+html[\s\S]*$/gi, '')
    result = result.replace(/<!DOCTYPE\s+html[\s\S]*$/gi, '')
    result = result.replace(/<!--\w+_STATE:\s*\{[\s\S]*?\}\s*-->/g, '')
    result = result.replace(/:::quiz\s*\n[\s\S]*?\n:::/g, '')
    result = result.replace(/:::quiz\s*\n[\s\S]*$/g, '')
    return result
  })

  const guideProgress = computed(() => {
    if (!isHtmlInteractive.value || !getMsg().content) return null
    const match = getMsg().content.match(/<!--\w+_STATE:\s*(\{[\s\S]*?\})\s*-->/)
    if (!match) return null
    try {
      const state = JSON.parse(match[1])
      return {
        progress: state.total > 0 ? Math.round((state.current_index / state.total) * 100) : 0,
        progressText: `${state.current_index}/${state.total}`,
        currentTopic: state.current_topic || '',
        currentIndex: state.current_index ?? 0,
        total: state.total ?? 0,
        mastery: state.mastery || {},
      }
    } catch { return null }
  })

  // ── Quiz 提取 ──

  const extractedQuizzes = computed<QuizData[]>(() => {
    let content = getMsg().content
    if (!content) return []
    content = content.replace(/\u201c/g, '\u300c').replace(/\u201d/g, '\u300d')
    const quizzes: QuizData[] = []
    const regex = /:::quiz\s*\n([\s\S]*?)\n:::/g
    let match
    while ((match = regex.exec(content)) !== null) {
      try {
        const quiz = JSON.parse(match[1].trim())
        if (quiz.question) quiz.question = quiz.question.replace(/\u300c/g, '\u201c').replace(/\u300d/g, '\u201d')
        if (quiz.explanation) quiz.explanation = quiz.explanation.replace(/\u300c/g, '\u201c').replace(/\u300d/g, '\u201d')
        if (quiz.question && quiz.answer !== undefined) quizzes.push(quiz)
      } catch { /* ignore */ }
    }
    return quizzes
  })

  // ── Slides 在线演示提取 ──

  const extractedSlides = computed<any | null>(() => {
    const content = getMsg().content
    if (!content) return null
    const match = content.match(/:::slides\s*\n([\s\S]*?)\n:::/)
    if (!match) return null
    try {
      return JSON.parse(match[1].trim())
    } catch {
      return null
    }
  })

  // ── 内容过滤 ──

  const contentWithoutQuizAndState = computed(() => {
    const content = isHtmlInteractive.value ? filteredInteractiveContent.value : getMsg().content
    if (!content) return ''
    return content
      .replace(/:::quiz\s*\n[\s\S]*?\n:::/g, '')
      .replace(/:::slides\s*\n[\s\S]*?\n:::/g, '')
      .replace(/:::slides\s*\n[\s\S]*$/g, '')
      .replace(/<!--\w+_STATE:\s*\{[\s\S]*?\}\s*-->/g, '')
      .replace(/<!--STAGE_DETAIL-->[\s\S]*?<!--\/STAGE_DETAIL-->/g, '')
      .replace(/```json\s*\n[\s\S]*?```/g, '')
      .replace(/<think>[^\n]*\n?/g, '')
  })

  // ── 内联标记拆分 ──

  const CONTENT_MARKER_TEST_RE = /<!-- (?:TC:[\w-]+|STAGE_PROGRESS:[\w-]+:\d+\/\d+:[^>]+) -->/

  const hasInlineMarkers = computed(() => {
    return getMsg().executionPlan?.customStages && CONTENT_MARKER_TEST_RE.test(getMsg().content)
  })

  const contentBlocks = computed((): InlineContentBlock[] | null => {
    const content = contentWithoutQuizAndState.value
    if (!content || !hasInlineMarkers.value) return null
    const blocks: InlineContentBlock[] = []
    let lastIndex = 0
    const re = /\n*<!-- (?:TC:([\w-]+)|STAGE_PROGRESS:([\w-]+):(\d+\/\d+):([^>]+)) -->\n*/g
    let match: RegExpExecArray | null
    while ((match = re.exec(content)) !== null) {
      const textBefore = content.slice(lastIndex, match.index)
      if (textBefore.trim()) blocks.push({ type: 'text', text: textBefore })
      if (match[1]) blocks.push({ type: 'tool', toolCallId: match[1] })
      else if (match[2]) blocks.push({ type: 'progress', stageName: match[2], progress: match[3], title: match[4] })
      lastIndex = re.lastIndex
    }
    const trailing = content.slice(lastIndex)
    if (trailing.trim()) blocks.push({ type: 'text', text: trailing })
    return blocks.length > 0 ? blocks : null
  })

  const firstTextBlockIndex = computed(() => {
    if (!contentBlocks.value) return -1
    return contentBlocks.value.findIndex(b => b.type === 'text')
  })

  // ── 降级正文视觉统一 ──
  // 当 build_fallback_content 产出多篇报告拼接时，前端自动 H1→H2 + 分隔线
  const normalizedContent = computed(() => {
    const raw = contentWithoutQuizAndState.value
    if (!raw) return raw

    // 只对降级输出启用（检测后端导语特征，避免误伤其他消息）
    const isFallback = raw.includes('> **提示**：以下包含') && raw.includes('子主题的研究报告')
    if (!isFallback) return raw

    const h1Count = (raw.match(/^# [^\n]+$/gm) || []).length
    if (h1Count < 2) return raw

    // H1 → H2 + 分隔线
    let normalized = raw.replace(/^# ([^\n]+)$/gm, '\n---\n\n## $1')
    // 去掉导语后重复的分隔线
    normalized = normalized.replace(/---\n+\n---\n/g, '---\n')
    return normalized
  })

  return {
    // 动画
    isHtmlAnimation, extractedAnimationHtml, animationStreamingCode,
    // 交互
    isHtmlInteractive, extractedInteractiveHtml, interactiveStreamingCode,
    filteredInteractiveContent, guideProgress,
    // Quiz
    extractedQuizzes,
    // Slides 在线演示
    extractedSlides,
    // 内容
    contentWithoutQuizAndState,
    normalizedContent,
    // 内联标记
    hasInlineMarkers, contentBlocks, firstTextBlockIndex,
  }
}
