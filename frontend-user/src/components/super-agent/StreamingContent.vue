<script setup lang="ts">
/**
 * 流式内容渲染组件
 *
 * 职责：
 * - Markdown 渲染（MarkdownIt + highlight.js + KaTeX）
 * - 学术引用标记渲染（[ref_N] → 可点击脚注）
 * - 思考块（ReasoningBlock）内联渲染
 * - CSS 动画光标（替代 ▋ 字符）
 * - XSS 防护（DOMPurify）
 *
 * 导出共享的 md 实例供其他组件复用
 */
import { computed, ref, toRef, onMounted, onUnmounted, watch, nextTick } from 'vue'
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'
import 'highlight.js/styles/github.css'
import katex from 'katex'
import 'katex/dist/katex.min.css'
import type { Reference, ReasoningBlock } from '../../types/super-agent'
import { sanitizeWithKatex, CITATION_SANITIZE_CONFIG } from '../../utils/sanitize'
import { protectCitationMarks, restoreCitationMarks, hasRefPattern as detectRefPattern } from '../../utils/citationRenderer'
import { useMermaidChart } from '../../composables/useMermaidChart'
import { useEChartsChart } from '../../composables/useEChartsChart'
import { quickDetectECharts } from '../../utils/echartsDetector'

const props = defineProps<{
  /** 原始文本内容 */
  content: string
  /** 是否正在流式输出 */
  isStreaming?: boolean
  /** 隐藏光标（有 pendingClientTool 时） */
  hideCursor?: boolean
  /** 学术引用列表 */
  references?: Reference[]
  /** 思考块列表 */
  reasoningBlocks?: ReasoningBlock[]
  /** 消息 ID（用于思考块交互） */
  msgId?: string
  /** 当前正在流式输出的专家 ID */
  streamingExpertId?: string
}>()

const emit = defineEmits<{
  (e: 'citation-click', event: MouseEvent, references: Reference[]): void
  (e: 'citation-mousemove', event: MouseEvent): void
  (e: 'toggle-reasoning', msgId: string, expertId: string): void
}>()

// ==================== Mermaid 图表渲染 ====================

const contentRef = ref<HTMLElement | null>(null)
const isStreamingRef = toRef(props, 'isStreaming')

// 使用 Mermaid 图表渲染 composable
const { isRendering: isMermaidRendering, errors: mermaidErrors } = useMermaidChart({
  containerRef: contentRef,
  isStreaming: isStreamingRef,
  debounceMs: 500,
  timeout: 10000,
})

// 使用 ECharts 图表渲染 composable
const { isRendering: isEChartsRendering, errors: echartsErrors, chartCount } = useEChartsChart({
  containerRef: contentRef,
  isStreaming: isStreamingRef,
  debounceMs: 500,
})
// ==================== Markdown 配置 ====================

/**
 * HTML 转义（用于代码块）
 */
function escapeCodeHtml(str: string): string {
  return str.replace(/[&<>"']/g, (m) => {
    const map: Record<string, string> = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }
    return map[m] || m
  })
}

function highlightCode(str: string, lang: string): string {
  // Mermaid 代码块：返回占位容器，由 useMermaidChart 处理
  if (lang === 'mermaid') {
    return `<pre class="language-mermaid mermaid-placeholder"><code>${escapeCodeHtml(str)}</code></pre>`
  }

  // ECharts 代码块：返回占位容器，由 useEChartsChart 处理
  if (lang === 'echarts') {
    return `<pre class="language-echarts echarts-placeholder"><code>${escapeCodeHtml(str)}</code></pre>`
  }

  // JSON / JavaScript 代码块：智能检测是否为 ECharts 配置
  if (lang === 'json' || lang === 'javascript' || lang === 'js') {
    if (quickDetectECharts(str)) {
      return `<pre class="language-echarts echarts-placeholder"><code>${escapeCodeHtml(str)}</code></pre>`
    }
  }

  // 其他语言：使用 highlight.js
  if (lang && hljs.getLanguage(lang)) {
    try {
      return '<pre class="hljs"><code>' +
        hljs.highlight(str, { language: lang, ignoreIllegals: true }).value +
        '</code></pre>'
    } catch {
      // 忽略高亮错误
    }
  }
  return '<pre class="hljs"><code>' + escapeCodeHtml(str) + '</code></pre>'
}

const md: MarkdownIt = new MarkdownIt({
  html: true,
  linkify: true,
  typographer: true,
  breaks: true,
  highlight: highlightCode,
})

// 思考过程专用 Markdown 渲染器（轻量级：不做代码高亮、不开 html）
const reasoningMd = new MarkdownIt({ html: false, linkify: false, breaks: true })

// 链接在新标签页打开
const defaultRender = md.renderer.rules.link_open || function(tokens, idx, options, _env, self) {
  return self.renderToken(tokens, idx, options)
}

md.renderer.rules.link_open = function(tokens, idx, options, env, self) {
  const token = tokens[idx]
  const targetIndex = token.attrIndex('target')
  const relIndex = token.attrIndex('rel')

  if (targetIndex < 0) token.attrPush(['target', '_blank'])
  else token.attrs![targetIndex][1] = '_blank'

  if (relIndex < 0) token.attrPush(['rel', 'noopener noreferrer'])
  else token.attrs![relIndex][1] = 'noopener noreferrer'

  return defaultRender(tokens, idx, options, env, self)
}

// ==================== 渲染函数 ====================

function renderMathInText(text: string): string {
  if (!text) return ''
  text = text.replace(/\$\$([\s\S]+?)\$\$/g, (_, formula) => {
    try {
      return katex.renderToString(formula.trim(), { displayMode: true, throwOnError: false })
    } catch {
      return `<span class="katex-error">$$${formula}$$</span>`
    }
  })
  text = text.replace(/\$([^\$\n]+?)\$/g, (_, formula) => {
    try {
      return katex.renderToString(formula.trim(), { displayMode: false, throwOnError: false })
    } catch {
      return `<span class="katex-error">$${formula}$</span>`
    }
  })
  return text
}

/** 修复 Markdown 标题和表格前缺少空行的问题（LLM 常遗漏） */
function fixMarkdownSpacing(text: string): string {
  // Step 1: 分离代码块，只处理非代码块部分
  const parts = text.split(/(```[\s\S]*?```)/g)
  for (let i = 0; i < parts.length; i += 2) {
    if (!parts[i]) continue
    // 在行内 ##{1,5}+空格 前插入换行（只处理 ##+ 避免误伤 URL 锚点 #）
    parts[i] = parts[i].replace(/(?<=[^\n#])(#{2,6}\s)/g, '\n$1')
    // ##紧跟中文/字母（无空格）— 插入换行+空格
    parts[i] = parts[i].replace(/(?<=[^\n#])(#{2,6})(?=[^\s#])/g, '\n$1 ')
    // 行内表格拆分：非 | 开头的行中出现 |col| 模式（后跟 |---| 分隔行）
    parts[i] = parts[i].replace(/(^|\n)([^|\n]+)(\|[^|\n]+\|[^\n]*\n\|[-|: ]+\|)/g, '$1$2\n$3')
  }
  text = parts.join('')

  // Step 2: 行级处理（跳过代码块内的行）
  const lines = text.split('\n')
  const fixed: string[] = []
  let inCodeBlock = false
  for (let line of lines) {
    const stripped = line.trim()

    // 代码块边界检测
    if (stripped.startsWith('```')) {
      inCodeBlock = !inCodeBlock
      fixed.push(line)
      continue
    }
    if (inCodeBlock) {
      fixed.push(line)
      continue
    }

    // ##标题 → ## 标题（补空格）
    const hm = stripped.match(/^(#{2,6})([^\s#])/)
    if (hm) {
      line = hm[1] + ' ' + stripped.slice(hm[1].length)
    }
    const normalizedStripped = line.trim()

    // 标题前确保空行
    if (/^#{2,6}\s/.test(normalizedStripped) && fixed.length > 0) {
      const prev = fixed[fixed.length - 1].trim()
      if (prev) fixed.push('')
    }

    // 表格首行前确保空行
    if (normalizedStripped.startsWith('|') && fixed.length > 0) {
      const prev = fixed[fixed.length - 1].trim()
      if (prev && !prev.startsWith('|') && !prev.startsWith('---')) {
        fixed.push('')
      }
    }

    fixed.push(line)
  }
  return fixed.join('\n')
}

/**
 * 将 [audio:file_id] 标记替换为内联 HTML 音频播放器
 *
 * 降级路径：当 [audio:xxx] 出现在 Markdown 文本流中（而非 ContentBlock 路由）时，
 * 使用原生 <audio> 标签渲染。主路径是 AudioBlock → ContentRenderer → AudioPlayer 组件。
 */
function replaceAudioMarkers(text: string): string {
  return text.replace(
    /\[audio:([a-zA-Z0-9_-]+)\]/g,
    (_match, fileId) =>
      `<div class="inline-audio-player" data-audio-file-id="${fileId}">` +
      `<audio controls preload="metadata" src="/api/user/files/${fileId}/download" style="height:36px;width:100%;max-width:400px;border-radius:8px;" />` +
      `</div>`,
  )
}

/** 基础 Markdown 渲染（含数学公式 + XSS 防护） */
function renderMarkdown(text: string): string {
  if (!text) return ''
  const fixed = fixMarkdownSpacing(text)
  const withAudio = replaceAudioMarkers(fixed)
  const withMath = renderMathInText(withAudio)
  const html = md.render(withMath)
  // 使用统一的 sanitize 配置，支持 KaTeX 保护 + SVG 白名单
  return sanitizeWithKatex(html, CITATION_SANITIZE_CONFIG)
}

function escapeHtml(text: string): string {
  const div = document.createElement('div')
  div.textContent = text
  return div.innerHTML
}

function escapeJsAttr(str: string): string {
  return str.replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '&quot;')
}

function removeReasoningPlaceholders(content: string): string {
  if (!content) return ''
  return content.replace(/\n?<!-- REASONING_BLOCK_[^>]+ -->\n?/g, '')
}

/**
 * 清洗 LLM 幻觉的虚假文件链接
 * 覆盖：sandbox:、file:///、/mnt/data/、attachment:、(#) 占位链接等格式
 */
function sanitizeFileLinks(content: string): string {
  if (!content) return ''

  const fakeSchemes = 'sandbox|file|attachment'

  // 1. 移除 Markdown 图片链接 ![alt](fake-scheme:...)
  content = content.replace(
    new RegExp(`!\\[([^\\]]*)\\]\\((?:${fakeSchemes}):[^)]*\\)`, 'gi'),
    '$1'
  )
  // 2. 移除 Markdown 普通链接 [text](fake-scheme:...)
  content = content.replace(
    new RegExp(`\\[([^\\]]*)\\]\\((?:${fakeSchemes}):[^)]*\\)`, 'gi'),
    '$1'
  )
  // 3. 移除指向 /mnt/data/ 的 Markdown 链接
  content = content.replace(/!\[([^\]]*)\]\(\/mnt\/[^)]*\)/gi, '$1')
  content = content.replace(/\[([^\]]*)\]\(\/mnt\/[^)]*\)/gi, '$1')
  // 4. 移除裸 sandbox: URL
  content = content.replace(/\bsandbox:(?:\/\/|\/)?[^\s)>\]]+/gi, '')
  // 5. 移除裸 file:/// URL
  content = content.replace(/\bfile:\/\/\/[^\s)>\]]+/gi, '')
  // 6. 移除指向 # 的假下载链接 [下载xxx](#) 或 [点击下载xxx](#)
  content = content.replace(/\[([^\]]*)\]\(#\)/gi, '$1')

  return content
}

/** 渲染带引用标记的 Markdown 内容 + 思考块内联 */
function renderMarkdownWithReferences(
  content: string,
  references: Reference[] = [],
  reasoningBlocks: ReasoningBlock[] = [],
  msgId: string = '',
  streamingExpertId?: string,
): string {
  if (!content) return ''

  let processedContent = removeReasoningPlaceholders(content)
  processedContent = sanitizeFileLinks(processedContent)
  let html: string

  // 使用提取的 citationRenderer 两阶段 API
  const refs = references || []
  if (refs.length === 0 && !detectRefPattern(processedContent)) {
    html = renderMarkdown(processedContent)
  } else {
    const { content: protectedContent, placeholders } = protectCitationMarks(processedContent)
    html = renderMarkdown(protectedContent)
    html = restoreCitationMarks(html, placeholders, refs)
  }

  // 动态插入思考块
  if (reasoningBlocks && reasoningBlocks.length > 0) {
    for (const block of reasoningBlocks) {
      const isCollapsed = block.isCollapsed
      const isStreaming = streamingExpertId === block.expertId
      const toggleText = isCollapsed ? '展开' : '收起'
      const arrowClass = isCollapsed ? '' : ' rotated'
      const bodyDisplay = isCollapsed ? 'none' : 'block'
      const statusText = isStreaming ? ' (思考中...)' : ''

      const blockHtml = `<div class="reasoning-block${isCollapsed ? ' collapsed' : ''}${isStreaming ? ' streaming' : ''}" data-msg-id="${escapeJsAttr(msgId)}" data-expert-id="${escapeJsAttr(block.expertId)}">
<div class="reasoning-header" onclick="window.__toggleReasoningBlock && window.__toggleReasoningBlock('${escapeJsAttr(msgId)}', '${escapeJsAttr(block.expertId)}')">
<span class="reasoning-icon">${isStreaming ? '💭' : '🧠'}</span>
<span class="reasoning-title">${block.expertId === '__model__' ? '思考过程' : escapeHtml(block.expertName) + ' 的思考过程'}${statusText}</span>
<span class="reasoning-toggle">${toggleText} <span class="toggle-arrow${arrowClass}">▶</span></span>
</div>
<div class="reasoning-body" style="display: ${bodyDisplay};">
<div class="reasoning-text">${sanitizeWithKatex(reasoningMd.render(block.reasoning))}</div>
</div>
</div>`

      let inserted = false

      if (block.phase === 'opening') {
        const patterns = [
          /<strong>【主持人】<\/strong>：?\s*<\/p>/i,
          /【主持人】：?\s*<\/p>/i,
        ]
        const firstRoundH2 = /<h2[^>]*>第1轮辩论<\/h2>/i.exec(html)
        const searchEnd = firstRoundH2 ? firstRoundH2.index : html.length
        const searchArea = html.slice(0, searchEnd)

        for (const pattern of patterns) {
          const match = pattern.exec(searchArea)
          if (match) {
            const insertPos = match.index + match[0].length
            html = html.slice(0, insertPos) + '\n' + blockHtml + '\n' + html.slice(insertPos)
            inserted = true
            break
          }
        }
      } else if (block.phase === 'final_summary') {
        const finalSummaryH2 = /<h2[^>]*>最终总结<\/h2>/gi
        const h2Match = finalSummaryH2.exec(html)
        if (h2Match) {
          const afterH2 = html.slice(h2Match.index)
          const patterns = [
            /<strong>【主持人总结】<\/strong>：?\s*<\/p>/i,
            /<strong>【主持人】<\/strong>：?\s*<\/p>/i,
            /【主持人总结】：?\s*<\/p>/i,
            /【主持人】：?\s*<\/p>/i,
          ]
          for (const pattern of patterns) {
            const match = pattern.exec(afterH2)
            if (match) {
              const insertPos = h2Match.index + match.index + match[0].length
              html = html.slice(0, insertPos) + '\n' + blockHtml + '\n' + html.slice(insertPos)
              inserted = true
              break
            }
          }
        }
      } else if (block.phase === 'round_summary' && block.round) {
        const roundH2Pattern = new RegExp(`<h2[^>]*>第${block.round}轮辩论<\\/h2>`, 'gi')
        const h2Match = roundH2Pattern.exec(html)
        if (h2Match) {
          const afterH2Start = h2Match.index + h2Match[0].length
          const nextH2Match = /<h2[^>]*>/gi.exec(html.slice(afterH2Start))
          const searchEnd = nextH2Match ? afterH2Start + nextH2Match.index : html.length
          const searchArea = html.slice(afterH2Start, searchEnd)

          const patterns = [
            /<strong>【主持人总结】<\/strong>：?\s*<\/p>/i,
            /【主持人总结】：?\s*<\/p>/i,
          ]
          for (const pattern of patterns) {
            const match = pattern.exec(searchArea)
            if (match) {
              const insertPos = afterH2Start + match.index + match[0].length
              html = html.slice(0, insertPos) + '\n' + blockHtml + '\n' + html.slice(insertPos)
              inserted = true
              break
            }
          }
        }
      } else if (block.phase === 'speech' && block.round && block.expertName) {
        const roundH2Pattern = new RegExp(`<h2[^>]*>第${block.round}轮辩论<\\/h2>`, 'gi')
        const h2Match = roundH2Pattern.exec(html)
        if (h2Match) {
          const afterH2Start = h2Match.index + h2Match[0].length
          const nextH2Match = /<h2[^>]*>/gi.exec(html.slice(afterH2Start))
          const searchEnd = nextH2Match ? afterH2Start + nextH2Match.index : html.length
          const searchArea = html.slice(afterH2Start, searchEnd)

          const escapedName = block.expertName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
          const expertPattern = new RegExp(`<strong>【${escapedName}】<\\/strong>[^<]*<\\/p>`, 'i')
          const match = expertPattern.exec(searchArea)
          if (match) {
            const insertPos = afterH2Start + match.index + match[0].length
            html = html.slice(0, insertPos) + '\n' + blockHtml + '\n' + html.slice(insertPos)
            inserted = true
          }
        }
      } else if (block.expertId.startsWith('coordinator')) {
        const patterns = [
          /<strong>【主持人总结】<\/strong>：?\s*<\/p>/gi,
          /<strong>【主持人】<\/strong>：?\s*<\/p>/gi,
          /【主持人总结】：?\s*<\/p>/gi,
          /【主持人】：?\s*<\/p>/gi,
        ]
        for (const pattern of patterns) {
          const matches = [...html.matchAll(pattern)]
          if (matches.length > 0) {
            const lastMatch = matches[matches.length - 1]
            const insertPos = (lastMatch.index || 0) + lastMatch[0].length
            html = html.slice(0, insertPos) + '\n' + blockHtml + '\n' + html.slice(insertPos)
            inserted = true
            break
          }
        }
      } else {
        const expertPattern = new RegExp(`<strong>【${block.expertName}】<\\/strong>[^<]*<\\/p>`, 'gi')
        const matches = [...html.matchAll(expertPattern)]
        if (matches.length > 0) {
          const lastMatch = matches[matches.length - 1]
          const insertPos = (lastMatch.index || 0) + lastMatch[0].length
          html = html.slice(0, insertPos) + '\n' + blockHtml + '\n' + html.slice(insertPos)
          inserted = true
        }
      }

      if (!inserted) {
        html = html + '\n' + blockHtml
      }
    }
  }

  return html
}

// ==================== 计算属性 ====================

// __model__ 思考块：用独立 ref 驱动流式渲染（避免 computed 缓存导致不更新）
const modelReasoningText = ref('')
const modelReasoningVisible = ref(false)

watch(() => props.reasoningBlocks, () => {
  const block = props.reasoningBlocks?.find(b => b.expertId === '__model__')
  if (block) {
    modelReasoningText.value = block.reasoning
    modelReasoningVisible.value = true
  } else {
    modelReasoningVisible.value = false
  }
}, { deep: true, immediate: true })

// 过滤掉 __model__ 的辩论模式思考块（继续走 v-html 插入）
const debateReasoningBlocks = computed(() => {
  if (!props.reasoningBlocks) return []
  return props.reasoningBlocks.filter(b => b.expertId !== '__model__')
})

const renderedHtml = computed(() =>
  renderMarkdownWithReferences(
    props.content,
    props.references,
    debateReasoningBlocks.value.length > 0 ? debateReasoningBlocks.value : undefined,
    props.msgId || '',
    props.streamingExpertId,
  )
)

// ==================== __model__ 思考块展开/收起 ====================

const modelReasoningCollapsed = ref(false)

// 历史加载时同步折叠状态（immediate），流式过程中不再自动折叠
watch(() => props.reasoningBlocks?.find(b => b.expertId === '__model__')?.isCollapsed, (val, oldVal) => {
  // 仅初始加载时同步（oldVal === undefined 表示 immediate 首次触发）
  if (oldVal === undefined && val === true) modelReasoningCollapsed.value = true
}, { deep: true, immediate: true })

const reasoningTextRef = ref<HTMLElement>()

function toggleModelReasoning() {
  modelReasoningCollapsed.value = !modelReasoningCollapsed.value
}

// 思考内容流式输出时自动滚到底部
watch(modelReasoningText, () => {
  if (!modelReasoningCollapsed.value) {
    nextTick(() => {
      if (reasoningTextRef.value) {
        reasoningTextRef.value.scrollTop = reasoningTextRef.value.scrollHeight
      }
    })
  }
})

const isModelReasoningStreaming = computed(() =>
  props.streamingExpertId === '__model__'
)

// 思考过程 Markdown 渲染（不做引用替换）
const renderedReasoningHtml = computed(() => {
  if (!modelReasoningText.value) return ''
  return sanitizeWithKatex(reasoningMd.render(modelReasoningText.value))
})

// 辩论模式思考块的全局 toggle（v-html 中 onclick 用）
function setupToggleReasoningBlock() {
  ;(window as any).__toggleReasoningBlock = (msgId: string, expertId: string) => {
    const blockEl = document.querySelector(
      `.reasoning-block[data-msg-id="${msgId}"][data-expert-id="${expertId}"]`
    )
    if (!blockEl) return
    const body = blockEl.querySelector('.reasoning-body') as HTMLElement
    const toggleText = blockEl.querySelector('.reasoning-toggle') as HTMLElement
    if (!body) return
    const isCollapsed = body.style.display === 'none'
    body.style.display = isCollapsed ? 'block' : 'none'
    blockEl.classList.toggle('collapsed', !isCollapsed)
    if (toggleText) {
      toggleText.innerHTML = isCollapsed
        ? '收起 <span class="toggle-arrow rotated">▶</span>'
        : '展开 <span class="toggle-arrow">▶</span>'
    }
  }
}

onMounted(() => {
  setupToggleReasoningBlock()
})

onUnmounted(() => {
  delete (window as any).__toggleReasoningBlock
})

// ==================== 事件处理 ====================

function handleClick(e: MouseEvent) {
  emit('citation-click', e, props.references || [])
}

function handleMouseMove(e: MouseEvent) {
  emit('citation-mousemove', e)
}
</script>

<template>
  <div class="streaming-content">
    <!-- v8.0: 思考模型 reasoning 块（固定在正文前，Vue 组件渲染） -->
    <div
      v-if="modelReasoningVisible"
      class="reasoning-block"
      :class="{ collapsed: modelReasoningCollapsed, streaming: isModelReasoningStreaming }"
    >
      <div class="reasoning-header" @click="toggleModelReasoning">
        <span class="reasoning-icon">{{ isModelReasoningStreaming ? '💭' : '🧠' }}</span>
        <span class="reasoning-title">思考过程{{ isModelReasoningStreaming ? ' (思考中...)' : '' }}</span>
        <span class="reasoning-toggle">
          {{ modelReasoningCollapsed ? '展开' : '收起' }}
          <span class="toggle-arrow" :class="{ rotated: !modelReasoningCollapsed }">▶</span>
        </span>
      </div>
      <div v-show="!modelReasoningCollapsed" class="reasoning-body">
        <div ref="reasoningTextRef" class="reasoning-text" v-html="renderedReasoningHtml"></div>
      </div>
    </div>

    <div
      v-if="content"
      ref="contentRef"
      class="markdown-content"
      v-html="renderedHtml"
      @click="handleClick"
      @mousemove="handleMouseMove"
    />
    <span v-if="isStreaming && !hideCursor && content" class="streaming-cursor" />
  </div>
</template>

<style scoped>
.streaming-content {
  position: relative;
}

/* CSS 动画光标（替代 ▋ 字符） */
.streaming-cursor {
  display: inline-block;
  width: 2px;
  height: 1.2em;
  background: #3b82f6;
  margin-left: 2px;
  vertical-align: text-bottom;
  animation: cursor-blink 1s step-end infinite;
}

@keyframes cursor-blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}
</style>

<style>
/* 非 scoped 样式：markdown-content 内部的样式需要穿透 v-html */
.streaming-content .markdown-content {
  line-height: 1.7;
  word-break: break-word;
}

.streaming-content .markdown-content p {
  margin: 0 0 8px;
}

.streaming-content .markdown-content p:last-child {
  margin-bottom: 0;
}

.streaming-content .markdown-content pre {
  margin: 8px 0;
  border-radius: 6px;
  overflow-x: auto;
}

.streaming-content .markdown-content code {
  font-family: 'Consolas', 'Monaco', monospace;
  font-size: 0.9em;
}

.streaming-content .markdown-content :not(pre) > code {
  background: #f3f4f6;
  padding: 2px 6px;
  border-radius: 4px;
  color: #e11d48;
}

.streaming-content .markdown-content blockquote {
  border-left: 3px solid #e5e7eb;
  padding-left: 12px;
  margin: 8px 0;
  color: #6b7280;
}

.streaming-content .markdown-content table {
  border-collapse: collapse;
  width: 100%;
  margin: 8px 0;
}

.streaming-content .markdown-content th,
.streaming-content .markdown-content td {
  border: 1px solid #e5e7eb;
  padding: 6px 12px;
  text-align: left;
}

.streaming-content .markdown-content th {
  background: #f9fafb;
  font-weight: 600;
}

/* 引用标记样式 */
.streaming-content .citation-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 4px;
  font-size: 11px;
  font-weight: 600;
  color: #2080f0;
  background: #eff6ff;
  border-radius: 3px;
  cursor: pointer;
  vertical-align: super;
  line-height: 1;
  transition: background 0.15s;
}

.streaming-content .citation-mark:hover {
  background: #dbeafe;
  color: #1d4ed8;
}
.streaming-content .citation-mark + .citation-mark {
  margin-left: 2px;
}

/* 思考块样式 */
.streaming-content .reasoning-block {
  margin: 12px 0;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  overflow: hidden;
  background: #fefce8;
}

.streaming-content .reasoning-block.streaming {
  border-color: #fbbf24;
}

.streaming-content .reasoning-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  cursor: pointer;
  user-select: none;
  font-size: 13px;
  color: #92400e;
  background: #fef9c3;
}

.streaming-content .reasoning-header:hover {
  background: #fef08a;
}

.streaming-content .reasoning-icon {
  font-size: 14px;
}

.streaming-content .reasoning-title {
  flex: 1;
  font-weight: 500;
}

.streaming-content .reasoning-toggle {
  font-size: 12px;
  color: #a16207;
}

.streaming-content .toggle-arrow {
  display: inline-block;
  transition: transform 0.2s;
  font-size: 10px;
}

.streaming-content .toggle-arrow.rotated {
  transform: rotate(90deg);
}

.streaming-content .reasoning-body {
  padding: 8px 12px;
  border-top: 1px solid #fde68a;
}

.streaming-content .reasoning-text {
  font-size: 12px;
  color: #78350f;
  line-height: 1.6;
  word-break: break-word;
  max-height: 300px;
  overflow-y: auto;
}

.streaming-content .reasoning-text p {
  margin: 0.3em 0;
}

.streaming-content .reasoning-text ul,
.streaming-content .reasoning-text ol {
  margin: 0.3em 0;
  padding-left: 1.5em;
}

.streaming-content .reasoning-text li {
  margin: 0.1em 0;
}

.streaming-content .reasoning-text code {
  background: rgba(120, 53, 15, 0.1);
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 11px;
}

.streaming-content .reasoning-text pre {
  background: rgba(120, 53, 15, 0.08);
  padding: 6px 8px;
  border-radius: 4px;
  overflow-x: auto;
  margin: 0.3em 0;
}

.streaming-content .reasoning-text strong {
  color: #92400e;
}

/* Mermaid 图表样式 */
.streaming-content .mermaid-chart-wrapper {
  position: relative;
  margin: 1.5em 0;
  padding: 16px;
  background: #fafafa;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  overflow-x: auto;
}

.streaming-content .mermaid-chart-wrapper .mermaid-container {
  display: flex;
  justify-content: center;
}

.streaming-content .mermaid-chart-wrapper svg {
  max-width: 100%;
  height: auto;
}

.streaming-content .mermaid-placeholder {
  background: #f8fafc;
  border: 1px dashed #cbd5e1;
  border-radius: 6px;
}

.streaming-content .mermaid-placeholder code {
  color: #64748b;
}

.streaming-content .mermaid-error {
  background: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: 6px;
  padding: 12px;
  margin: 8px 0;
}

.streaming-content .mermaid-error code {
  color: #991b1b;
  font-size: 12px;
  white-space: pre-wrap;
}

/* ECharts 图表样式 */
.streaming-content .echarts-chart-wrapper {
  position: relative;
  width: 100%;
  margin: 1.5em 0;
  background: #fafafa;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  overflow: hidden;
}

.streaming-content .echarts-placeholder {
  background: #f8fafc;
  border: 1px dashed #cbd5e1;
  border-radius: 6px;
}

.streaming-content .echarts-placeholder code {
  color: #64748b;
}

.streaming-content .echarts-error {
  background: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: 6px;
  padding: 12px;
  margin: 8px 0;
}

.streaming-content .echarts-error pre {
  margin: 0 0 8px;
  background: #fff5f5;
  padding: 8px;
  border-radius: 4px;
  overflow-x: auto;
}

.streaming-content .echarts-error code {
  color: #991b1b;
  font-size: 12px;
  white-space: pre-wrap;
}

.streaming-content .echarts-error-msg {
  color: #dc2626;
  font-size: 12px;
  margin-top: 8px;
}

/* 图表工具栏（移植自 md-viewer） */
.streaming-content .chart-toggle-bar {
  position: absolute;
  top: 8px;
  right: 8px;
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 4px;
  background: rgba(255, 255, 255, 0.88);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  z-index: 10;
  overflow: visible;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.2s ease;
}

.streaming-content .mermaid-chart-wrapper:hover .chart-toggle-bar,
.streaming-content .echarts-chart-wrapper:hover .chart-toggle-bar {
  opacity: 1;
  pointer-events: auto;
}

.streaming-content .chart-action-btn {
  position: relative;
  padding: 4px 8px;
  font-size: 13px;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: #6b7280;
  cursor: pointer;
  transition: all 0.15s;
  line-height: 1;
}

.streaming-content .chart-action-btn:hover {
  background: #f3f4f6;
  color: #1f2937;
}

.streaming-content .chart-action-btn:active {
  background: #6366f1;
  color: white;
}

/* 通用 tooltip（图表工具栏 + 代码视图按钮） */
.streaming-content [data-tooltip] {
  position: relative;
}

.streaming-content [data-tooltip]::after {
  content: attr(data-tooltip);
  position: absolute;
  top: calc(100% + 6px);
  left: 50%;
  transform: translateX(-50%);
  padding: 4px 8px;
  font-size: 12px;
  line-height: 1.3;
  color: #fff;
  background: #1f2937;
  border-radius: 4px;
  white-space: nowrap;
  pointer-events: none;
  opacity: 0;
  transition: opacity 0.15s;
  z-index: 20;
}

.streaming-content [data-tooltip]:hover::after {
  opacity: 1;
}

/* 代码视图 */
.streaming-content .chart-code-view {
  position: relative;
  width: 100%;
  max-height: 600px;
  overflow: auto;
  background: #1e293b;
  border-radius: 8px;
}

.streaming-content .chart-code-view pre {
  margin: 0;
  padding: 36px 16px 16px;
  background: transparent;
  overflow-x: auto;
}

.streaming-content .chart-code-view code {
  color: #e2e8f0 !important;
  background: transparent !important;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
}

/* 代码视图顶部按钮栏 */
.streaming-content .chart-code-toolbar {
  position: absolute;
  top: 8px;
  right: 8px;
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 4px;
  background: rgba(255, 255, 255, 0.12);
  border: 1px solid rgba(255, 255, 255, 0.15);
  border-radius: 6px;
  z-index: 10;
}

.streaming-content .chart-code-btn {
  padding: 4px 8px;
  font-size: 13px;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: #e2e8f0;
  cursor: pointer;
  transition: all 0.15s;
  line-height: 1;
}

.streaming-content .chart-code-btn:hover {
  background: rgba(255, 255, 255, 0.2);
}
</style>
