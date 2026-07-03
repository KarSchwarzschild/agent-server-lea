<script setup lang="ts">
/**
 * 工具调用卡片组件 (P2-A 增强版)
 *
 * 三阶段视觉：
 * - receiving: 蓝色脉冲边框 + 流式 JSON 语法高亮 + 闪烁光标
 * - running: 黄色边框 + 旋转图标 + 完整参数
 * - success/error/empty: 绿色/红色/灰色 + 结果 Markdown 渲染
 *
 * 增强功能：
 * - Input/Output 分区布局
 * - JSON 语法高亮（key/string/number/boolean 着色）
 * - 结果 Markdown 渲染（轻量 MarkdownIt）
 * - 复制结果按钮
 * - 执行耗时显示
 */
import { ref, watch, computed } from 'vue'
import MarkdownIt from 'markdown-it'
import type { ThinkingStep, ToolCall } from '../../types/super-agent'
import { getToolIcon, formatStepDuration, formatFilteredInput } from '../../types/super-agent'
import { sanitizeLightHtml } from '../../utils/sanitize'

const props = defineProps<{
  step: ThinkingStep
  /** 对应的 ToolCall 数据（含 input/result） */
  toolCall?: ToolCall
  /** 是否显示完整 JSON（调试模式） */
  showJson?: boolean
  /** 流式原始参数（receiving 阶段） */
  streamingInputRaw?: string | null
}>()

const emit = defineEmits<{
  (e: 'toggle'): void
}>()

// 轻量 Markdown 渲染器（工具结果专用，不需要 KaTeX/引用）
const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
})

function renderResultMarkdown(text: string): string {
  if (!text) return ''
  // 截断过长结果（8000 字符，UI 通过 max-height + 滚动控制可视区域）
  const truncated = text.length > 8000 ? text.substring(0, 8000) + '\n\n...(结果已截断)' : text
  return sanitizeLightHtml(md.render(truncated))
}

// JSON 语法高亮
function highlightJson(raw: string): string {
  if (!raw) return ''
  return raw
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"([^"\\]*(\\.[^"\\]*)*)"\s*:/g, '<span class="json-key">"$1"</span>:')
    .replace(/:\s*"([^"\\]*(\\.[^"\\]*)*)"/g, ': <span class="json-string">"$1"</span>')
    .replace(/:\s*(\d+\.?\d*)/g, ': <span class="json-number">$1</span>')
    .replace(/:\s*(true|false|null)/g, ': <span class="json-bool">$1</span>')
}

// 展开/收起状态：默认收起，仅在 running/error 时自动展开
const expanded = ref(props.step.status === 'receiving' || props.step.status === 'running')
const userToggled = ref(false)
const copySuccess = ref(false)

// 自动展开/收起
watch(() => props.step.status, (status) => {
  if (userToggled.value) return
  if (status === 'receiving' || status === 'running') {
    expanded.value = true
  } else {
    expanded.value = false
  }
})

function toggle() {
  userToggled.value = true
  expanded.value = !expanded.value
  emit('toggle')
}

// 复制结果
async function copyResult() {
  const text = props.toolCall?.result
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    copySuccess.value = true
    setTimeout(() => { copySuccess.value = false }, 1500)
  } catch {
    // fallback
  }
}

// 格式化 JSON 字符串（用于 Input 区域）
const formattedInput = computed(() => {
  if (!props.toolCall?.input) return ''
  try {
    return JSON.stringify(props.toolCall.input, null, 2)
  } catch {
    return ''
  }
})

// 状态标签
function getStatusLabel(status?: string): string {
  switch (status) {
    case 'receiving': return '接收中'
    case 'running': return '执行中'
    case 'success': return '完成'
    case 'error': return '失败'
    case 'empty': return '无结果'
    default: return '等待'
  }
}
</script>

<template>
  <div
    class="tool-call-card"
    :class="[
      `tool-card-${step.status || 'pending'}`,
      { 'tool-card-expanded': expanded }
    ]"
  >
    <!-- 头部（始终显示） -->
    <div class="tool-card-header" @click="toggle">
      <!-- 第一行：toggle + icon + name + spacer + duration + status -->
      <div class="tool-card-row1">
        <span class="tool-card-toggle">{{ expanded ? '▾' : '▸' }}</span>
        <span class="tool-card-icon">{{ getToolIcon(step.toolName || '') }}</span>
        <span class="tool-card-name">{{ step.toolDisplayName || step.toolName || step.text }}</span>
        <span class="tool-card-spacer" />
        <span v-if="formatStepDuration(step)" class="tool-card-duration">
          {{ formatStepDuration(step) }}
        </span>
        <span class="tool-card-status" :class="`status-${step.status || 'pending'}`">
          <span v-if="step.status === 'running'" class="tool-card-spinner" />
          {{ getStatusLabel(step.status) }}
        </span>
      </div>
      <!-- 第二行：折叠态参数摘要 -->
      <div v-if="!expanded && toolCall" class="tool-card-row2">
        <span class="tool-card-param-summary">
          {{ formatFilteredInput(step.toolName || '', toolCall.input) }}
        </span>
      </div>
    </div>

    <!-- 展开内容 -->
    <template v-if="expanded">
      <!-- receiving 阶段：流式 JSON 语法高亮 + 闪烁光标 -->
      <div v-if="step.status === 'receiving' && streamingInputRaw" class="tool-card-streaming">
        <pre class="streaming-json" v-html="highlightJson(streamingInputRaw)"></pre>
        <span class="cursor-blink" />
      </div>

      <!-- running/completed 阶段：Input/Output 分区 -->
      <div v-else-if="toolCall" class="tool-card-body">
        <!-- Input 区域 -->
        <div class="tool-card-section">
          <div class="tool-card-section-header">
            <span class="tool-card-label">Input</span>
          </div>
          <div v-if="showJson" class="tool-card-input">
            <pre class="tool-card-json" v-html="highlightJson(formattedInput)"></pre>
          </div>
          <div v-else class="tool-card-params">
            {{ formatFilteredInput(step.toolName || '', toolCall.input) }}
          </div>
        </div>

        <!-- Output 区域（有结果时显示） -->
        <div v-if="toolCall.result || step.summary" class="tool-card-section tool-card-output-section">
          <div class="tool-card-section-header">
            <span class="tool-card-label">Output</span>
            <button
              v-if="toolCall.result"
              class="tool-card-copy-btn"
              :class="{ 'copy-success': copySuccess }"
              @click.stop="copyResult"
            >
              {{ copySuccess ? '✓' : '📋' }}
            </button>
          </div>

          <!-- Markdown 渲染结果（所有模式统一） -->
          <div
            v-if="toolCall.result"
            class="tool-card-result-md"
            v-html="renderResultMarkdown(toolCall.result)"
          />
        </div>

        <!-- 文件下载等自定义内容通过 slot 注入 -->
        <slot name="result" :tool-call="toolCall" />

        <!-- 调试模式：工具调用 ID -->
        <div v-if="showJson && step.toolCallId" class="tool-card-id">
          ID: {{ step.toolCallId }}
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.tool-call-card {
  border-radius: 6px;
  border: 1px solid #e2e8f0;
  border-left: 3px solid #e2e8f0;
  margin-bottom: 4px;
  transition: border-color 0.2s, box-shadow 0.2s;
  overflow: hidden;
}

/* 三阶段边框颜色 */
.tool-card-receiving {
  border-left-color: #3b82f6;
  border-color: #93c5fd;
  animation: pulse-border 2s ease-in-out infinite;
}

.tool-card-running {
  border-left-color: #f59e0b;
  border-color: #fcd34d;
}

.tool-card-success {
  border-left-color: #10b981;
}

.tool-card-error {
  border-left-color: #ef4444;
  border-color: #fca5a5;
}

.tool-card-empty {
  border-left-color: #9ca3af;
}

@keyframes pulse-border {
  0%, 100% { box-shadow: 0 0 0 0 rgba(59, 130, 246, 0); }
  50% { box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.15); }
}

/* 头部（两行布局） */
.tool-card-header {
  display: flex;
  flex-direction: column;
  padding: 8px 10px;
  cursor: pointer;
  user-select: none;
  font-size: 12px;
}

.tool-card-header:hover {
  background: rgba(0, 0, 0, 0.02);
}

/* 第一行：icon + name + duration + status */
.tool-card-row1 {
  display: flex;
  align-items: center;
  gap: 6px;
}

/* 第二行：折叠态参数摘要 */
.tool-card-row2 {
  padding-left: 30px; /* 对齐 toggle(10px) + icon(14px) + gap(6px) */
  margin-top: 2px;
}

.tool-card-toggle {
  font-size: 10px;
  color: #94a3b8;
  width: 10px;
  flex-shrink: 0;
}

.tool-card-icon {
  font-size: 14px;
  flex-shrink: 0;
}

.tool-card-name {
  font-weight: 500;
  color: #1f2937;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}

.tool-card-param-summary {
  font-family: 'Consolas', 'Monaco', monospace;
  color: #94a3b8;
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  display: block;
}

.tool-card-spacer {
  flex: 1;
}

.tool-card-duration {
  font-size: 11px;
  color: #94a3b8;
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
  white-space: nowrap;
}

.tool-card-status {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 4px;
  font-weight: 500;
  flex-shrink: 0;
  white-space: nowrap;
}

.status-receiving {
  background: #dbeafe;
  color: #2563eb;
}

.status-running {
  background: #fef3c7;
  color: #d97706;
}

.tool-card-spinner {
  display: inline-block;
  width: 10px;
  height: 10px;
  border: 1.5px solid #fcd34d;
  border-top-color: #d97706;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  margin-right: 3px;
  vertical-align: middle;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.status-success {
  background: #dcfce7;
  color: #16a34a;
}

.status-error {
  background: #fee2e2;
  color: #dc2626;
}

.status-empty {
  background: #f3f4f6;
  color: #6b7280;
}

.status-pending {
  background: #f3f4f6;
  color: #9ca3af;
}

/* 流式参数 */
.tool-card-streaming {
  padding: 6px 10px 10px 28px;
  position: relative;
}

.streaming-json {
  font-family: 'Consolas', 'Monaco', monospace;
  font-size: 11px;
  color: #374151;
  background: #f0f7ff;
  padding: 6px 8px;
  border-radius: 4px;
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0;
}

.cursor-blink {
  display: inline-block;
  width: 2px;
  height: 12px;
  background: #3b82f6;
  margin-left: 1px;
  vertical-align: text-bottom;
  animation: blink 1s step-end infinite;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}

/* 展开内容 */
.tool-card-body {
  padding: 4px 10px 10px 28px;
}

/* Input/Output 分区 */
.tool-card-section {
  margin-bottom: 6px;
}

.tool-card-section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 2px;
}

.tool-card-output-section {
  border-top: 1px solid #f1f5f9;
  padding-top: 6px;
}

.tool-card-params {
  font-size: 12px;
  color: #6b7280;
}

.tool-card-label {
  font-size: 10px;
  font-weight: 600;
  color: #94a3b8;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.tool-card-json {
  font-family: 'Consolas', 'Monaco', monospace;
  font-size: 11px;
  color: #374151;
  background: #f8fafc;
  padding: 6px 8px;
  border-radius: 4px;
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0;
  max-height: 150px;
  overflow-y: auto;
}

/* JSON 语法高亮 */
.tool-card-json :deep(.json-key) {
  color: #7c3aed;
}

.tool-card-json :deep(.json-string) {
  color: #059669;
}

.tool-card-json :deep(.json-number) {
  color: #d97706;
}

.tool-card-json :deep(.json-bool) {
  color: #dc2626;
}

.streaming-json :deep(.json-key) {
  color: #7c3aed;
}

.streaming-json :deep(.json-string) {
  color: #059669;
}

.streaming-json :deep(.json-number) {
  color: #d97706;
}

.streaming-json :deep(.json-bool) {
  color: #dc2626;
}

.tool-card-summary {
  font-size: 12px;
  color: #374151;
}

/* 结果 Markdown 渲染 */
.tool-card-result-md {
  font-size: 12px;
  color: #374151;
  background: #f8fafc;
  padding: 8px;
  border-radius: 4px;
  max-height: 300px;
  overflow-y: auto;
  line-height: 1.5;
}

.tool-card-result-md :deep(p) {
  margin: 0 0 6px;
}

.tool-card-result-md :deep(p:last-child) {
  margin-bottom: 0;
}

.tool-card-result-md :deep(ul),
.tool-card-result-md :deep(ol) {
  margin: 4px 0;
  padding-left: 18px;
}

.tool-card-result-md :deep(code) {
  background: #e2e8f0;
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 11px;
}

.tool-card-result-md :deep(pre) {
  background: #1e293b;
  color: #e2e8f0;
  padding: 8px;
  border-radius: 4px;
  overflow-x: auto;
  font-size: 11px;
}

.tool-card-result-md :deep(a) {
  color: #3b82f6;
  text-decoration: none;
}

.tool-card-result-md :deep(a:hover) {
  text-decoration: underline;
}

/* 复制按钮 */
.tool-card-copy-btn {
  background: none;
  border: 1px solid #e2e8f0;
  border-radius: 4px;
  padding: 1px 6px;
  cursor: pointer;
  font-size: 11px;
  color: #94a3b8;
  transition: all 0.15s;
}

.tool-card-copy-btn:hover {
  background: #f1f5f9;
  color: #64748b;
}

.tool-card-copy-btn.copy-success {
  color: #16a34a;
  border-color: #86efac;
  background: #f0fdf4;
}

.tool-card-id {
  font-size: 10px;
  color: #94a3b8;
  font-family: 'Consolas', 'Monaco', monospace;
  margin-top: 4px;
}

.tool-card-input {
  margin-bottom: 6px;
}
</style>
