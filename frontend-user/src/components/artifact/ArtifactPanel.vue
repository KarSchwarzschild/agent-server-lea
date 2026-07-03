<template>
  <!-- 折叠态：面板关闭但有 artifact 时，显示浮动卡片 -->
  <div
    v-if="!artifactStore.panelVisible && artifactStore.artifactCount > 0"
    class="artifact-inline-card"
    @click="reopenPanel"
  >
    <div class="inline-card-icon">📄</div>
    <div class="inline-card-info">
      <div class="inline-card-title">{{ latestArtifactTitle }}</div>
      <div class="inline-card-meta">{{ latestArtifactMeta }}</div>
    </div>
    <div class="inline-card-action">打开 →</div>
  </div>

  <div
    v-if="artifactStore.panelVisible"
    class="artifact-panel"
    :class="{
      'is-streaming': artifactStore.isStreaming,
      'is-fullscreen': artifactStore.isFullscreen,
    }"
  >
    <!-- 骨架屏 -->
    <div v-if="artifactStore.panelMode === 'skeleton'" class="artifact-skeleton">
      <div class="skeleton-header">
        <div class="skeleton-line w60 pulse" />
        <div class="skeleton-actions">
          <div class="skeleton-btn pulse" />
          <div class="skeleton-btn pulse" />
        </div>
      </div>
      <div class="skeleton-body">
        <div class="skeleton-line w80 pulse" />
        <div class="skeleton-line w70 pulse" />
        <div class="skeleton-line w90 pulse" />
        <div class="skeleton-line w50 pulse" />
        <div class="skeleton-line w75 pulse" />
      </div>
      <div class="skeleton-hint">正在准备文档...</div>
    </div>

    <!-- 正常内容 -->
    <template v-else>
      <ArtifactHeader
        :is-fullscreen="artifactStore.isFullscreen"
        @close="artifactStore.closePanel()"
        @toggle-fullscreen="artifactStore.toggleFullscreen()"
        @export="handleExport"
        @save-version="handleSaveVersion"
      />

      <div class="artifact-body" ref="bodyRef" @mousemove="handleCitationMousemove" @mouseleave="handleTooltipLeave">
        <!-- AI 处理中遮罩层 -->
        <div v-if="isAIProcessing" class="ai-processing-overlay">
          <div class="ai-processing-spinner" />
          <span>AI 正在修改文档...</span>
        </div>

        <!-- Diff 视图（替代正常内容） -->
        <div v-if="artifactStore.diffData" class="diff-view">
          <div class="diff-stats-bar">
            <span class="diff-stat-insert">+{{ artifactStore.diffData.stats.inserts }} 行</span>
            <span class="diff-stat-delete">-{{ artifactStore.diffData.stats.deletes }} 行</span>
          </div>
          <div class="diff-lines">
            <div
              v-for="(op, i) in artifactStore.diffData.ops"
              :key="i"
              class="diff-line"
              :class="'diff-' + op.type"
            >
              <span class="diff-marker">{{ op.type === 'insert' ? '+' : op.type === 'delete' ? '-' : ' ' }}</span>
              <span class="diff-text">{{ op.content }}</span>
            </div>
          </div>
        </div>

        <!-- 正常文档内容 -->
        <ArtifactEditor
          v-else
          ref="editorRef"
          :editable="artifactStore.panelMode === 'editing' && !isAIProcessing"
          @content-change="handleContentChange"
          @selection-change="handleSelectionChange"
        />
      </div>

      <!-- 高亮工具栏（仅编辑模式下、选中文字时弹出） -->
      <HighlightToolbar
        :visible="artifactStore.panelMode === 'editing' && !!selectionHighlight && !isAIProcessing && !isAIPreview"
        :position="selectionPosition"
        @rewrite="handleHighlightRewrite"
      />

      <!-- 编辑模式底部区域（三态互斥） -->
      <template v-if="artifactStore.panelMode === 'editing'">
        <!-- AI 处理中提示（优先级最高） -->
        <div v-if="isAIProcessing" class="artifact-ai-processing-bar">
          <span class="ai-processing-dot" />
          <span>AI 正在处理中...</span>
        </div>

        <!-- AI 修改预览栏（采纳 = 接受修改继续编辑，撤销 = 恢复原文） -->
        <div v-else-if="isAIPreview" class="artifact-ai-preview-section">
          <div class="artifact-ai-preview-bar">
            <div class="ai-preview-info">
              <span class="ai-preview-badge">AI 修改</span>
              <span class="ai-preview-summary">{{ aiChangeSummary || '已修改，未保存' }}</span>
            </div>
            <div class="ai-preview-actions">
              <n-button size="small" quaternary @click="showAIDiff = !showAIDiff">
                {{ showAIDiff ? '▲ 收起修改' : '▼ 查看修改' }}
              </n-button>
              <n-button size="small" @click="handleDiscardAIEdit">撤销</n-button>
              <n-button size="small" type="primary" @click="handleAcceptAIEdit">采纳</n-button>
            </div>
          </div>
          <!-- 展开的词级 diff 区域 -->
          <div v-if="showAIDiff && aiDiffSpans.length" class="ai-diff-panel">
            <div class="ai-diff-header">
              <span>词级对比</span>
              <span class="ai-diff-stats">
                <span class="diff-stat-delete">-{{ aiDiffStats.deletes }}</span>
                <span class="diff-stat-insert">+{{ aiDiffStats.inserts }}</span>
              </span>
            </div>
            <div class="ai-diff-inline">
              <span
                v-for="(span, i) in aiDiffSpans"
                :key="i"
                :class="{ 'diff-ins': span.type === 'insert', 'diff-del': span.type === 'delete' }"
              >{{ span.text }}</span>
            </div>
          </div>
        </div>

        <!-- AI 指令框 -->
        <div v-else class="artifact-ai-input">
          <input
            v-model="aiInstruction"
            placeholder="AI 指令（如：润色第三段、补充数据...）"
            @keydown.enter="handleAIInstruction"
            class="ai-input-field"
          />
          <button class="ai-input-send" :disabled="!aiInstruction.trim()" @click="handleAIInstruction">
            发送
          </button>
        </div>
      </template>

      <div class="artifact-footer">
        <span class="word-count" v-if="wordCount > 0">{{ wordCount }} 字</span>
        <div class="footer-actions">
          <n-button size="small" quaternary @click="handleExport('docx:standard')" :disabled="exporting">
            <template #icon><n-icon size="14"><DocumentText /></n-icon></template>
            Word
          </n-button>
          <n-button size="small" quaternary @click="handleExport('pdf')" :disabled="exporting">
            <template #icon><n-icon size="14"><Document /></n-icon></template>
            PDF
          </n-button>
          <n-button size="small" quaternary @click="handleCopy">
            <template #icon><n-icon size="14"><CopyOutline /></n-icon></template>
            复制
          </n-button>
        </div>
      </div>
    </template>
  </div>

  <!-- 引用悬浮 Tooltip -->
  <Teleport to="body">
    <div v-if="tooltipVisible" class="citation-tooltip"
         :style="{ left: tooltipPosition.x + 'px', top: tooltipPosition.y + 'px' }"
         @mouseenter="handleTooltipEnter"
         @mouseleave="handleTooltipLeave">
      <div class="citation-tooltip-title">{{ tooltipContent.title }}</div>
      <div v-if="tooltipContent.authors" class="citation-tooltip-meta">{{ tooltipContent.authors }}</div>
      <div v-if="tooltipContent.year || tooltipContent.source" class="citation-tooltip-meta">
        {{ [tooltipContent.year, tooltipContent.source].filter(Boolean).join(' · ') }}
      </div>
      <div v-if="tooltipContent.abstract" class="citation-tooltip-abstract">{{ tooltipContent.abstract }}</div>
      <a v-if="tooltipContent.url" class="citation-tooltip-link" :href="tooltipContent.url" target="_blank" rel="noopener noreferrer">查看原文 →</a>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, ref, watch, nextTick } from 'vue'
import { NButton, NIcon, useMessage } from 'naive-ui'
import { DocumentText, Document, CopyOutline } from '@vicons/ionicons5'
import { useArtifactStore } from '@/stores/artifact'
import { getContentWordCount } from '@/types/artifact'
import type { TextHighlight } from '@/types/artifact'
import { extractAnchor } from '@/composables/useAnchorLocator'
import { useCitationTooltip } from '@/composables/useCitationTooltip'

import { highlightRewrite, aiEdit } from '@/api/artifact'
import ArtifactHeader from './ArtifactHeader.vue'
import ArtifactEditor from './ArtifactEditor.vue'
import HighlightToolbar from './HighlightToolbar.vue'

const artifactStore = useArtifactStore()
const message = useMessage()
const bodyRef = ref<HTMLElement>()
const editorRef = ref<InstanceType<typeof ArtifactEditor>>()
const isAIProcessing = ref(false)

// ==================== 引用 Tooltip ====================
const {
  tooltipVisible,
  tooltipContent,
  tooltipPosition,
  handleCitationMousemove,
  handleTooltipEnter,
  handleTooltipLeave,
} = useCitationTooltip()

// Markdown 渲染已移至 ArtifactEditor 组件内部

// ==================== 字数统计 ====================

const wordCount = computed(() => {
  const content = artifactStore.displayContent
  if (!content) return 0
  return getContentWordCount(content)
})

// ==================== 自动滚动到底部（流式阶段） ====================

watch(() => artifactStore.streamingContent, () => {
  if (artifactStore.isStreaming && bodyRef.value) {
    nextTick(() => {
      if (bodyRef.value) {
        bodyRef.value.scrollTop = bodyRef.value.scrollHeight
      }
    })
  }
})

// ==================== 导出 ====================

const exporting = ref(false)

/** 带鉴权的文件下载（fetch + Blob，避免 window.open 不带 token） */
async function downloadFileById(fileId: string, filename: string) {
  const token = localStorage.getItem('token') || ''
  const baseURL = import.meta.env.VITE_API_BASE_URL || ''
  const resp = await fetch(`${baseURL}/api/user/super-agent/files/${fileId}/download`, {
    headers: { 'Authorization': `Bearer ${token}` },
  })
  if (!resp.ok) throw new Error(`下载失败: HTTP ${resp.status}`)
  const blob = await resp.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  URL.revokeObjectURL(url)
  document.body.removeChild(a)
}

/** 解析复合导出 key: "docx:official" → { format: "docx", style: "official" } */
function parseExportKey(key: string): { format: string; style?: string } {
  if (key.startsWith('docx:')) {
    return { format: 'docx', style: key.split(':')[1] }
  }
  return { format: key }
}

async function handleExport(key: string) {
  if (key === 'copy-md') {
    await navigator.clipboard.writeText(artifactStore.displayContent)
    message.success('Markdown 已复制到剪贴板')
    return
  }
  if (key === 'copy-text') {
    const text = artifactStore.displayContent
      .replace(/#{1,6}\s/g, '')
      .replace(/\*\*([^*]+)\*\*/g, '$1')
      .replace(/\*([^*]+)\*/g, '$1')
      .replace(/`([^`]+)`/g, '$1')
    await navigator.clipboard.writeText(text)
    message.success('纯文本已复制到剪贴板')
    return
  }

  const { format, style } = parseExportKey(key)
  if (format === 'docx' || format === 'pdf') {
    if (!artifactStore.activeArtifactId || exporting.value) return
    exporting.value = true
    try {
      const { exportArtifact } = await import('@/api/artifact')
      const result = await exportArtifact(
        artifactStore.activeArtifactId,
        format as 'docx' | 'pdf',
        style,
      )
      await downloadFileById(result.file_id, result.filename)
      message.success(`${format.toUpperCase()} 导出成功`)
    } catch (e) {
      message.error(`导出失败: ${e}`)
    } finally {
      exporting.value = false
    }
  }
}

const latestArtifactTitle = computed(() => {
  const last = [...artifactStore.artifacts.values()].pop()
  if (!last) return '文档'
  // 从内容提取标题
  const content = last.contents?.[last.contents.length - 1]?.content || ''
  const match = content.match(/^#{1,3}\s+(.+)/m)
  if (match) return match[1].trim().slice(0, 30)
  return last.title?.slice(0, 30) || '文档'
})

const latestArtifactMeta = computed(() => {
  const last = [...artifactStore.artifacts.values()].pop()
  if (!last) return ''
  const version = last.contents?.[last.contents.length - 1]
  const wordCount = version?.content?.length || 0
  return `v${last.currentIndex} · ${wordCount > 0 ? Math.round(wordCount * 0.6) + '字' : ''}`
})

async function handleSaveVersion() {
  if (!pendingContent && artifactStore.displayContent) {
    pendingContent = artifactStore.displayContent
  }
  if (!pendingContent) {
    message.warning('没有可保存的内容')
    return
  }
  const result = await artifactStore.saveUserVersion(pendingContent, '手动保存')
  if (result) {
    message.success(`已保存为 v${result.versionIndex}`)
    pendingContent = ''
  } else {
    message.error('保存失败')
  }
}

function reopenPanel() {
  // 打开最近的 artifact
  const lastId = artifactStore.activeArtifactId || [...artifactStore.artifacts.keys()].pop()
  if (lastId) {
    artifactStore.openPanel(lastId)
  }
}

async function handleCopy() {
  await navigator.clipboard.writeText(artifactStore.displayContent)
  message.success('已复制到剪贴板')
}

// 编辑器内容变更（用于 Phase 3 手动保存 + 发送前自动保存）
let pendingContent = ''
function handleContentChange(markdown: string) {
  pendingContent = markdown
  artifactStore.setPendingEdit(markdown)
}

const selectionHighlight = ref<TextHighlight | null>(null)
const selectionPosition = ref<{ x: number; y: number } | null>(null)
const aiInstruction = ref('')

// AI 修改预览状态：记住修改前的原内容，用于"撤销修改"
const preEditContent = ref('')
const aiChangeSummary = ref('')

/** AI 修改预览态：有 preEditContent 说明 AI 已修改但用户未确认 */
const isAIPreview = computed(() => !!preEditContent.value)

// ==================== AI 修改 Diff（词级 inline） ====================

const showAIDiff = ref(false)

/** 记录 AI 修改后的内容（用于 diff 对比） */
const aiEditedContent = ref('')
/** 记录 diff 对比的旧文本（选区原文或全文） */
const aiDiffOldText = ref('')

interface InlineSpan { type: 'equal' | 'insert' | 'delete'; text: string }

/** 词级 diff：对比选区修改前后内容，返回 inline spans */
const aiDiffSpans = computed<InlineSpan[]>(() => {
  // 优先用选区旧文本（高亮重写场景），否则降级到全文（AI 指令场景）
  const oldText = aiDiffOldText.value || preEditContent.value
  if (!oldText || !aiEditedContent.value) return []
  return computeWordDiff(oldText, aiEditedContent.value)
})

const aiDiffStats = computed(() => {
  const spans = aiDiffSpans.value
  const del = spans.filter(s => s.type === 'delete').reduce((n, s) => n + s.text.length, 0)
  const ins = spans.filter(s => s.type === 'insert').reduce((n, s) => n + s.text.length, 0)
  return { deletes: del, inserts: ins }
})

/** 按中文字/英文词分词 */
function tokenize(text: string): string[] {
  const tokens: string[] = []
  // 匹配：中文单字 | 英文单词 | 数字 | 标点符号 | 空白
  const re = /[\u4e00-\u9fff]|[a-zA-Z]+|[0-9]+|[^\u4e00-\u9fffa-zA-Z0-9\s]|\s+/g
  let m: RegExpExecArray | null
  while ((m = re.exec(text)) !== null) tokens.push(m[0])
  return tokens
}

/** 词级 LCS diff */
function computeWordDiff(oldText: string, newText: string): InlineSpan[] {
  const oldTokens = tokenize(oldText)
  const newTokens = tokenize(newText)
  const m = oldTokens.length, n = newTokens.length

  // 优化：限制 token 数量，超大文档降级为整段 diff
  if (m * n > 500000) {
    return [
      { type: 'delete', text: oldText },
      { type: 'insert', text: newText },
    ]
  }

  // DP 求 LCS
  const dp: number[][] = Array.from({ length: m + 1 }, () => Array(n + 1).fill(0))
  for (let i = 1; i <= m; i++)
    for (let j = 1; j <= n; j++)
      dp[i][j] = oldTokens[i - 1] === newTokens[j - 1]
        ? dp[i - 1][j - 1] + 1
        : Math.max(dp[i - 1][j], dp[i][j - 1])

  // 回溯
  const raw: InlineSpan[] = []
  let i = m, j = n
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && oldTokens[i - 1] === newTokens[j - 1]) {
      raw.push({ type: 'equal', text: oldTokens[i - 1] })
      i--; j--
    } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
      raw.push({ type: 'insert', text: newTokens[j - 1] })
      j--
    } else {
      raw.push({ type: 'delete', text: oldTokens[i - 1] })
      i--
    }
  }
  raw.reverse()

  // 合并相邻同类型 span
  const merged: InlineSpan[] = []
  for (const span of raw) {
    const last = merged[merged.length - 1]
    if (last && last.type === span.type) {
      last.text += span.text
    } else {
      merged.push({ ...span })
    }
  }
  return merged
}

/** 撤销 AI 修改（恢复到修改前的内容） */
function handleDiscardAIEdit() {
  if (!preEditContent.value) return
  if (editorRef.value?.getEditor?.()) {
    const ed = editorRef.value.getEditor()
    ed?.commands?.setContent?.(preEditContent.value)
  }
  preEditContent.value = ''
  aiChangeSummary.value = ''
  aiEditedContent.value = ''
  aiDiffOldText.value = ''
  showAIDiff.value = false
  message.info('已撤销修改')
}

/** 采纳 AI 修改（不入库，只清除预览状态，继续编辑） */
function handleAcceptAIEdit() {
  preEditContent.value = ''
  aiChangeSummary.value = ''
  aiEditedContent.value = ''
  aiDiffOldText.value = ''
  showAIDiff.value = false
  message.success('已采纳修改，可继续编辑后保存')
}

function handleSelectionChange(highlight: TextHighlight | null) {
  selectionHighlight.value = highlight
  if (highlight) {
    // 获取选区位置（简化：使用 window.getSelection 的范围）
    const sel = window.getSelection()
    if (sel && sel.rangeCount > 0) {
      const rect = sel.getRangeAt(0).getBoundingClientRect()
      selectionPosition.value = { x: rect.left + rect.width / 2, y: rect.top }
    }
  } else {
    selectionPosition.value = null
  }
}

// ==================== 构建锚点三元组（公共逻辑） ====================

function buildAnchorFromSelection(): ReturnType<typeof extractAnchor> {
  const editor = editorRef.value?.getEditor?.()
  let anchor = editor ? extractAnchor(editor) : null
  if (!anchor && selectionHighlight.value) {
    const { fullMarkdown, selectedText } = selectionHighlight.value
    const idx = fullMarkdown.indexOf(selectedText)
    const prefix = idx > 0 ? fullMarkdown.slice(Math.max(0, idx - 50), idx) : ''
    const suffix = fullMarkdown.slice(idx + selectedText.length, idx + selectedText.length + 50)
    anchor = {
      node_path: '',
      text_quote: { prefix, exact: selectedText, suffix },
      offset: 0,
    }
  }
  return anchor
}

// ==================== AI 修改 → 编辑模式预览 → 用户确认保存/撤销 ====================
//
// 核心原则：AI 修改不入库。
// 1. AI 返回新内容 → 更新编辑器 → 进入编辑模式（未保存）
// 2. 用户点"保存" → createVersion 入库
// 3. 用户点"撤销" → 恢复原内容 → 回到只读模式
//

async function handleHighlightRewrite(instruction: string) {
  if (!selectionHighlight.value || !artifactStore.activeArtifactId) return

  const artifactId = artifactStore.activeArtifactId
  const anchor = buildAnchorFromSelection()
  if (!anchor) {
    message.error('无法定位选区')
    return
  }

  // 记住修改前的全文（用于撤销）
  preEditContent.value = artifactStore.displayContent
  isAIProcessing.value = true
  selectionHighlight.value = null

  try {
    await highlightRewrite(artifactId, { anchor, instruction }, {
      onStart: () => {},
      onDelta: () => {},
      onDone: (data) => {
        const finalContent = data.content
        aiChangeSummary.value = data.change_summary || `AI${instruction}`

        // 后端直接返回选区的原文和重写结果，diff 只对比选区
        aiDiffOldText.value = data.original_selection || ''
        aiEditedContent.value = data.rewritten_selection || finalContent

        if (editorRef.value?.getEditor?.()) {
          const ed = editorRef.value.getEditor()
          ed?.commands?.setContent?.(finalContent)
        }
        artifactStore.enterEditMode()
        message.info('AI 已修改，请检查后保存或撤销')
      },
      onError: (err) => {
        message.error(`重写失败: ${err}`)
        preEditContent.value = ''
        aiDiffOldText.value = ''
      },
    })
  } catch (e: any) {
    message.error(`重写失败: ${e.message || e}`)
    preEditContent.value = ''
    aiDiffOldText.value = ''
  } finally {
    isAIProcessing.value = false
  }
}

async function handleAIInstruction() {
  const text = aiInstruction.value.trim()
  if (!text || !artifactStore.activeArtifactId) return

  const artifactId = artifactStore.activeArtifactId
  aiInstruction.value = ''

  // ai-edit 是全文重写，忽略选区
  selectionHighlight.value = null

  // 记住修改前的全文（用于撤销 + 全文 diff）
  preEditContent.value = artifactStore.displayContent
  aiDiffOldText.value = ''  // 全文 diff
  isAIProcessing.value = true

  try {
    await aiEdit(artifactId, { instruction: text }, {
      onStart: () => {},
      onDelta: () => {},
      onDone: (data) => {
        const finalContent = data.content
        aiChangeSummary.value = data.change_summary || `AI编辑: ${text}`
        aiEditedContent.value = finalContent

        if (editorRef.value?.getEditor?.()) {
          const ed = editorRef.value.getEditor()
          ed?.commands?.setContent?.(finalContent)
        }
        artifactStore.enterEditMode()
        message.info('AI 已修改，请检查后保存或撤销')
      },
      onError: (err) => {
        message.error(`AI 编辑失败: ${err}`)
        preEditContent.value = ''
        aiDiffOldText.value = ''
      },
    })
  } catch (e: any) {
    message.error(`AI 编辑失败: ${e.message || e}`)
    preEditContent.value = ''
    aiDiffOldText.value = ''
  } finally {
    isAIProcessing.value = false
  }
}
</script>

<style scoped>
/* ================================================================
   Artifact Panel — 文档编辑面板
   设计参考：Notion / Google Docs / Open Canvas
   ================================================================ */

/* ── 折叠态内联卡片（ChatGPT Canvas 风格） ── */
.artifact-inline-card {
  position: fixed;
  bottom: 80px;
  right: 24px;
  z-index: 100;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  cursor: pointer;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.1), 0 0 1px rgba(0, 0, 0, 0.1);
  transition: all 0.2s;
  max-width: 360px;
}
.artifact-inline-card:hover {
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.14);
  transform: translateY(-1px);
}
.inline-card-icon {
  font-size: 24px;
  flex-shrink: 0;
}
.inline-card-info {
  flex: 1;
  min-width: 0;
}
.inline-card-title {
  font-size: 14px;
  font-weight: 600;
  color: #111827;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.inline-card-meta {
  font-size: 12px;
  color: #9ca3af;
  margin-top: 2px;
}
.inline-card-action {
  font-size: 13px;
  color: #2080f0;
  font-weight: 500;
  flex-shrink: 0;
  padding: 4px 12px;
  border-radius: 6px;
  transition: background 0.15s;
}
.inline-card-action:hover {
  background: #eff6ff;
}

/* A+B 模式：overlay 面板，覆盖在对话区右侧，不挤压布局 */
.artifact-panel {
  position: absolute;
  top: 8px;
  right: 8px;
  bottom: 8px;
  width: min(60%, 800px);
  display: flex;
  flex-direction: column;
  border-radius: 16px;
  border: 1px solid #e0e0e0;
  background: #ffffff;
  overflow: hidden;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.12), 0 0 1px rgba(0, 0, 0, 0.1);
  z-index: 50;
  animation: panel-slide-in 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}
@keyframes panel-slide-in {
  from { transform: translateX(30px); opacity: 0; }
  to { transform: translateX(0); opacity: 1; }
}
@media (max-width: 1200px) {
  .artifact-panel {
    width: min(75%, 700px);
  }
}
@media (min-width: 1600px) {
  .artifact-panel {
    width: min(55%, 900px);
  }
}

.artifact-panel.is-fullscreen {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  width: 100% !important;
  z-index: 1000;
  border-radius: 0;
  border: none;
  box-shadow: none;
  margin: 0;
  animation: none;
}

/* ── 骨架屏 ── */
.artifact-skeleton {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 32px 40px;
}
.skeleton-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 32px;
  padding-bottom: 16px;
  border-bottom: 1px solid #f3f4f6;
}
.skeleton-actions {
  display: flex;
  gap: 10px;
}
.skeleton-btn {
  width: 36px;
  height: 28px;
  border-radius: 6px;
  background: #f3f4f6;
}
.skeleton-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-width: 680px;
}
.skeleton-line {
  height: 14px;
  border-radius: 6px;
  background: linear-gradient(90deg, #f3f4f6 25%, #e5e7eb 50%, #f3f4f6 75%);
  background-size: 200% 100%;
}
.skeleton-line.w50 { width: 50%; }
.skeleton-line.w60 { width: 60%; }
.skeleton-line.w70 { width: 70%; }
.skeleton-line.w75 { width: 75%; }
.skeleton-line.w80 { width: 80%; }
.skeleton-line.w90 { width: 90%; }

.pulse {
  animation: skeleton-shimmer 1.8s ease-in-out infinite;
}
@keyframes skeleton-shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}

.skeleton-hint {
  text-align: center;
  color: #9ca3af;
  font-size: 13px;
  margin-top: 24px;
  letter-spacing: 0.5px;
}

/* ── 正文区域 ── */
.artifact-body {
  flex: 1;
  overflow-y: auto;
  padding: 16px 20px;
  scroll-behavior: smooth;
  background: #fff;
  position: relative;
}

/* ── AI 处理中遮罩 ── */
.ai-processing-overlay {
  position: absolute;
  inset: 0;
  z-index: 10;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  background: rgba(255, 255, 255, 0.75);
  backdrop-filter: blur(2px);
  font-size: 14px;
  color: #2563eb;
  font-weight: 500;
  pointer-events: all;
}
.ai-processing-spinner {
  width: 32px;
  height: 32px;
  border: 3px solid #dbeafe;
  border-top-color: #2563eb;
  border-radius: 50%;
  animation: ai-spin 0.8s linear infinite;
}
@keyframes ai-spin {
  to { transform: rotate(360deg); }
}

/* ── Diff 视图 ── */
.diff-view {
  font-family: 'Consolas', 'Monaco', 'Menlo', monospace;
  font-size: 13px;
  line-height: 1.6;
}
.diff-stats-bar {
  padding: 8px 16px;
  background: #f9fafb;
  border-bottom: 1px solid #e5e7eb;
  display: flex;
  gap: 12px;
  font-size: 13px;
  font-weight: 500;
  position: sticky;
  top: 0;
  z-index: 1;
}
.diff-stat-insert { color: #16a34a; }
.diff-stat-delete { color: #dc2626; }
.diff-lines { padding: 4px 0; }
.diff-line {
  display: flex;
  padding: 1px 16px;
  min-height: 22px;
}
.diff-insert {
  background: #dcfce7;
}
.diff-delete {
  background: #fee2e2;
  text-decoration: line-through;
  opacity: 0.7;
}
.diff-marker {
  width: 20px;
  flex-shrink: 0;
  color: #9ca3af;
  user-select: none;
  text-align: center;
}
.diff-insert .diff-marker { color: #16a34a; }
.diff-delete .diff-marker { color: #dc2626; }
.diff-text {
  white-space: pre-wrap;
  word-break: break-word;
}

/* ── AI 预览区域（栏 + diff） ── */
.artifact-ai-preview-section {
  border-top: 1px solid #fde68a;
  background: #fffbeb;
}
.artifact-ai-preview-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 16px;
}
.ai-preview-info {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.ai-preview-badge {
  flex-shrink: 0;
  padding: 2px 8px;
  border-radius: 4px;
  background: #f59e0b;
  color: #fff;
  font-size: 12px;
  font-weight: 500;
}
.ai-preview-summary {
  font-size: 13px;
  color: #92400e;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-preview-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

/* ── AI Diff 面板 ── */
.ai-diff-panel {
  border-top: 1px dashed #fde68a;
  max-height: 160px;
  overflow-y: auto;
  font-family: 'Consolas', 'Monaco', 'Menlo', monospace;
  font-size: 12px;
  line-height: 1.5;
  background: #fefce8;
}
.ai-diff-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 16px;
  font-size: 11px;
  color: #92400e;
  font-weight: 500;
  position: sticky;
  top: 0;
  background: #fef9c3;
  z-index: 1;
}
.ai-diff-stats {
  display: flex;
  gap: 8px;
}
.ai-diff-inline {
  padding: 8px 16px;
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.8;
}
.ai-diff-inline .diff-ins {
  background: rgba(22, 163, 74, 0.18);
  color: #15803d;
  border-radius: 2px;
  padding: 0 1px;
}
.ai-diff-inline .diff-del {
  background: rgba(220, 38, 38, 0.12);
  color: #b91c1c;
  text-decoration: line-through;
  border-radius: 2px;
  padding: 0 1px;
}

/* ── AI 处理中提示 ── */
.artifact-ai-processing-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  border-top: 1px solid #dbeafe;
  background: #eff6ff;
  font-size: 13px;
  color: #2563eb;
}
.ai-processing-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #2563eb;
  animation: ai-dot-pulse 1.2s ease-in-out infinite;
}
@keyframes ai-dot-pulse {
  0%, 100% { opacity: 0.3; transform: scale(0.8); }
  50% { opacity: 1; transform: scale(1.2); }
}

/* ── AI 指令框 ── */
.artifact-ai-input {
  display: flex;
  gap: 8px;
  padding: 8px 16px;
  border-top: 1px solid #f3f4f6;
  background: #fafbfc;
}
.ai-input-field {
  flex: 1;
  padding: 7px 12px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  font-size: 13px;
  outline: none;
  transition: border-color 0.15s;
}
.ai-input-field:focus {
  border-color: #2080f0;
}
.ai-input-send {
  padding: 6px 14px;
  border: none;
  border-radius: 8px;
  background: #2080f0;
  color: #fff;
  font-size: 13px;
  cursor: pointer;
  transition: background 0.15s;
}
.ai-input-send:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.ai-input-send:hover:not(:disabled) {
  background: #1a6fd4;
}

/* ── 底部栏 ── */
.artifact-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px;
  border-top: 1px solid #f3f4f6;
  background: #fafbfc;
  min-height: 40px;
}

.word-count {
  font-size: 12px;
  color: #9ca3af;
  font-variant-numeric: tabular-nums;
}

.footer-actions {
  display: flex;
  gap: 4px;
}

/* ── 流式状态 ── */
.artifact-panel.is-streaming .artifact-footer {
  background: #eff6ff;
  border-top-color: #dbeafe;
}
.artifact-panel.is-streaming .artifact-body::after {
  content: '';
  display: block;
  height: 40px;
  background: linear-gradient(to bottom, rgba(255,255,255,0), rgba(255,255,255,1));
  position: sticky;
  bottom: 0;
  pointer-events: none;
}
</style>

<!-- 非 scoped：Markdown 内容渲染样式（穿透 v-html / Tiptap） -->
<style>
/* 内容区域充满面板宽度 */

/* Markdown 排版（对齐项目 StreamingContent 风格） */
.artifact-body h1 {
  font-size: 1.75em;
  font-weight: 700;
  margin: 1.2em 0 0.6em;
  padding-bottom: 0.3em;
  border-bottom: 1px solid #f3f4f6;
  color: #111827;
  line-height: 1.3;
}
.artifact-body h2 {
  font-size: 1.4em;
  font-weight: 600;
  margin: 1em 0 0.5em;
  color: #1f2937;
  line-height: 1.35;
}
.artifact-body h3 {
  font-size: 1.15em;
  font-weight: 600;
  margin: 0.8em 0 0.4em;
  color: #374151;
}
.artifact-body h4 {
  font-size: 1em;
  font-weight: 600;
  margin: 0.6em 0 0.3em;
  color: #4b5563;
}
.artifact-body p {
  margin: 0 0 0.8em;
  line-height: 1.8;
  color: #374151;
}
.artifact-body p:last-child {
  margin-bottom: 0;
}

/* 列表 */
.artifact-body ul, .artifact-body ol {
  margin: 0.5em 0 0.8em;
  padding-left: 1.5em;
}
.artifact-body li {
  margin-bottom: 0.3em;
  line-height: 1.7;
  color: #374151;
}
.artifact-body li > p {
  margin-bottom: 0.2em;
}

/* 引用 */
.artifact-body blockquote {
  border-left: 3px solid #2080f0;
  padding: 8px 16px;
  margin: 0.8em 0;
  background: #f0f7ff;
  border-radius: 0 6px 6px 0;
  color: #4b5563;
}
.artifact-body blockquote p {
  margin-bottom: 0.3em;
}

/* 表格 */
.artifact-body table {
  border-collapse: collapse;
  width: 100%;
  margin: 0.8em 0;
  font-size: 0.95em;
}
.artifact-body th,
.artifact-body td {
  border: 1px solid #e5e7eb;
  padding: 8px 12px;
  text-align: left;
  line-height: 1.5;
}
.artifact-body th {
  background: #f9fafb;
  font-weight: 600;
  color: #1f2937;
}
.artifact-body tr:hover td {
  background: #fafbfc;
}

/* 代码 */
.artifact-body pre {
  margin: 0.8em 0;
  padding: 14px 16px;
  background: #1e293b;
  border-radius: 8px;
  overflow-x: auto;
  color: #e2e8f0;
  font-size: 0.9em;
  line-height: 1.6;
}
.artifact-body :not(pre) > code {
  background: #f3f4f6;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 0.9em;
  color: #e11d48;
  font-family: 'Consolas', 'Monaco', 'Menlo', monospace;
}

/* 分隔线 */
.artifact-body hr {
  border: none;
  border-top: 1px solid #e5e7eb;
  margin: 1.5em 0;
}

/* 强调 */
.artifact-body strong {
  font-weight: 600;
  color: #111827;
}

/* 链接 */
.artifact-body a {
  color: #2080f0;
  text-decoration: none;
}
.artifact-body a:hover {
  text-decoration: underline;
}

/* 图片 */
.artifact-body img {
  max-width: 100%;
  border-radius: 6px;
  margin: 0.5em 0;
}
</style>
