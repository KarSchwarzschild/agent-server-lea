<script setup lang="ts">
/**
 * 子代理任务组组件 (P0-B 增强版)
 *
 * 特性：
 * - 可展开/收起（userToggled 模式：执行中自动展开，完成后自动收起）
 * - 实时计时器（running 状态每秒更新）
 * - 嵌套工具调用（复用 ToolCallCard 组件）
 * - 子代理输出流（Markdown 渲染）
 * - 左侧彩色竖线（按状态上色）
 * - thinking 渲染、复制输出、停止按钮（禁用态）
 */
import { computed, ref, watch, onUnmounted } from 'vue'
import { NBadge } from 'naive-ui'
import MarkdownIt from 'markdown-it'
import type { SubAgentState, SubAgentToolCall, ThinkingStep, ToolCall } from '../../types/super-agent'
import ToolCallCard from './ToolCallCard.vue'
import { sanitizeLightHtml } from '../../utils/sanitize'

// 轻量 Markdown 实例（子代理输出用，不需要 KaTeX）
const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

function renderMarkdown(text: string): string {
  if (!text) return ''
  const html = md.render(text)
  return sanitizeLightHtml(html)
}

const props = defineProps<{
  subAgents: SubAgentState[]
  /** 简洁模式：只显示名称+状态，不展开 */
  compact?: boolean
}>()

const emit = defineEmits<{
  (e: 'toggle-agent', agentId: string): void
  (e: 'stop-session'): void
}>()

// 实时计时器
const now = ref(Date.now())
let timerInterval: ReturnType<typeof setInterval> | null = null

const hasRunning = computed(() => props.subAgents.some(a => a.status === 'running'))

// 启动/停止计时器
function startTimer() {
  if (timerInterval) return
  timerInterval = setInterval(() => {
    now.value = Date.now()
  }, 1000)
}

function stopTimer() {
  if (timerInterval) {
    clearInterval(timerInterval)
    timerInterval = null
  }
}

// 监听 running 状态
watch(hasRunning, (running) => {
  if (running) startTimer()
  else stopTimer()
}, { immediate: true })

onUnmounted(() => stopTimer())

// 计算子代理耗时
function getAgentDuration(agent: SubAgentState): string {
  if (agent.duration) {
    return `${(agent.duration / 1000).toFixed(1)}s`
  }
  if (agent.status === 'running') {
    const elapsed = now.value - agent.startTime
    return `${(elapsed / 1000).toFixed(0)}s`
  }
  return ''
}

// 状态图标
function getStatusIcon(status: SubAgentState['status']): string {
  switch (status) {
    case 'running': return '⏳'
    case 'completed': return '✅'
    case 'failed': return '❌'
    case 'timeout': return '⏰'
    default: return '⏳'
  }
}

// 完成/总数统计
const completedCount = computed(() =>
  props.subAgents.filter(a => a.status === 'completed').length
)
const allCompleted = computed(() =>
  props.subAgents.every(a => a.status === 'completed')
)

// 切换展开/收起
function toggleAgent(agent: SubAgentState) {
  agent.userToggled = true
  agent.expanded = !agent.expanded
  emit('toggle-agent', agent.id)
}

/**
 * 将 SubAgentToolCall 转换为 ToolCallCard 所需的 ThinkingStep + ToolCall 格式
 * 这是实现组件复用的关键适配层
 */
function toThinkingStep(tc: SubAgentToolCall, index: number): ThinkingStep {
  const statusMap: Record<string, ThinkingStep['status']> = {
    running: 'running',
    success: 'success',
    empty: 'empty',
    error: 'error',
  }
  return {
    text: tc.displayName,
    step: index,
    toolName: tc.tool,
    toolDisplayName: tc.displayName,
    status: statusMap[tc.status] || 'running',
    summary: tc.summary,
    durationMs: tc.durationMs,
    isToolCall: true,
  }
}

function toToolCall(tc: SubAgentToolCall): ToolCall {
  return {
    tool: tc.tool,
    toolDisplayName: tc.displayName,
    input: tc.input || {},
    status: tc.status,
    summary: tc.summary,
    durationMs: tc.durationMs,
  }
}

// 复制输出到剪贴板
const copiedAgentId = ref<string | null>(null)

async function copyOutput(agent: SubAgentState) {
  if (!agent.output) return
  try {
    await navigator.clipboard.writeText(agent.output)
    copiedAgentId.value = agent.id
    setTimeout(() => { copiedAgentId.value = null }, 2000)
  } catch {
    // 静默失败
  }
}
</script>

<template>
  <div v-if="subAgents.length > 0" class="sub-agent-group">
    <div class="sub-agent-group-header">
      <span class="sub-agent-group-icon">📋</span>
      <span>委派任务</span>
      <n-badge
        :value="`${completedCount}/${subAgents.length}`"
        :type="allCompleted ? 'success' : 'info'"
        size="small"
      />
    </div>

    <div
      v-for="agent in subAgents"
      :key="agent.id"
      class="sub-agent-card"
      :class="[
        `sub-agent-${agent.status}`,
        { 'sub-agent-active': agent.status === 'running' }
      ]"
    >
      <!-- 卡片头部（始终显示） -->
      <div class="sub-agent-card-header" @click="toggleAgent(agent)">
        <span class="sub-agent-toggle" v-if="!compact">
          {{ agent.expanded ? '▾' : '▸' }}
        </span>
        <span class="sub-agent-status-icon">
          {{ getStatusIcon(agent.status) }}
        </span>
        <span class="sub-agent-skill-name">{{ agent.displayName }}</span>
        <span class="sub-agent-duration" v-if="getAgentDuration(agent)">
          {{ getAgentDuration(agent) }}
        </span>
        <span class="sub-agent-status-tag" :class="`status-${agent.status}`">
          {{ agent.status === 'running' ? 'Running' :
             agent.status === 'completed' ? 'Completed' :
             agent.status === 'failed' ? 'Failed' : 'Timeout' }}
        </span>
      </div>

      <!-- 展开内容 -->
      <template v-if="agent.expanded && !compact">
        <!-- 任务描述 -->
        <div class="sub-agent-task">{{ agent.task }}</div>

        <!-- P0-B #4: 子代理思考过程（Markdown 渲染） -->
        <div v-if="agent.thinking" class="sub-agent-thinking">
          <div class="sub-agent-section-label">THINKING</div>
          <div class="sub-agent-thinking-content" v-html="renderMarkdown(agent.thinking)" />
          <span v-if="agent.status === 'running'" class="cursor-blink" />
        </div>

        <!-- 嵌套工具调用（复用 ToolCallCard 组件） -->
        <div v-if="agent.toolCalls.length > 0" class="sub-agent-tools">
          <div class="sub-agent-section-label">TOOL CALLS</div>
          <ToolCallCard
            v-for="(tc, tcIdx) in agent.toolCalls"
            :key="tcIdx"
            :step="toThinkingStep(tc, tcIdx)"
            :tool-call="toToolCall(tc)"
            :show-json="false"
          />
        </div>

        <!-- P0-B: 子代理输出 -->
        <div v-if="agent.output" class="sub-agent-output">
          <div class="sub-agent-output-header">
            <span class="sub-agent-section-label">OUTPUT</span>
            <!-- P0-B #12: 复制输出按钮 -->
            <button
              class="sub-agent-copy-btn"
              @click.stop="copyOutput(agent)"
              :title="copiedAgentId === agent.id ? '已复制' : '复制输出'"
            >
              {{ copiedAgentId === agent.id ? '✓ 已复制' : '📋 复制' }}
            </button>
          </div>
          <div class="sub-agent-output-content" v-html="renderMarkdown(agent.output || '')" />
          <span v-if="agent.status === 'running'" class="cursor-blink" />
        </div>

        <!-- 错误信息 -->
        <div v-if="agent.error" class="sub-agent-error">{{ agent.error }}</div>

        <!-- 底部统计信息 -->
        <div v-if="agent.toolCalls.length > 0" class="sub-agent-stats">
          <span>工具调用: {{ agent.toolCalls.length }}</span>
          <span>成功: {{ agent.toolCalls.filter(t => t.status === 'success').length }}</span>
          <span v-if="agent.toolCalls.some(t => t.status === 'error')">
            失败: {{ agent.toolCalls.filter(t => t.status === 'error').length }}
          </span>
          <span v-if="getAgentDuration(agent)">耗时: {{ getAgentDuration(agent) }}</span>
        </div>

        <!-- 停止按钮 -->
        <div v-if="agent.status === 'running'" class="sub-agent-actions">
          <button class="sub-agent-stop-btn" @click="emit('stop-session')">
            ⏹ 停止
          </button>
        </div>
      </template>

      <!-- 简洁模式：只显示工具数量 -->
      <template v-if="compact && agent.toolCalls.length > 0">
        <div class="sub-agent-compact-tools">
          {{ agent.toolCalls.filter(t => t.status === 'success').length }}/{{ agent.toolCalls.length }} 工具完成
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.sub-agent-group {
  margin: 8px 0;
  padding: 10px;
  background: #f8fafc;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
}

.sub-agent-group-header {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 500;
  color: #475569;
  margin-bottom: 8px;
}

.sub-agent-group-icon {
  font-size: 14px;
}

.sub-agent-card {
  background: white;
  border-radius: 6px;
  border: 1px solid #e2e8f0;
  border-left: 3px solid #e2e8f0;
  padding: 10px 12px;
  margin-bottom: 6px;
  transition: border-color 0.2s, box-shadow 0.2s;
}

.sub-agent-card:last-child {
  margin-bottom: 0;
}

.sub-agent-running {
  border-left-color: #3b82f6;
  background: #f0f7ff;
}

.sub-agent-active {
  border-color: #93c5fd;
  box-shadow: 0 0 0 1px rgba(59, 130, 246, 0.2);
}

.sub-agent-completed {
  border-left-color: #22c55e;
  background: #f0fdf4;
}

.sub-agent-failed,
.sub-agent-timeout {
  border-left-color: #ef4444;
  background: #fef2f2;
}

.sub-agent-card-header {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  user-select: none;
}

.sub-agent-toggle {
  font-size: 11px;
  color: #94a3b8;
  width: 12px;
  flex-shrink: 0;
}

.sub-agent-status-icon {
  font-size: 14px;
  flex-shrink: 0;
}

.sub-agent-skill-name {
  color: #1e293b;
}

.sub-agent-duration {
  margin-left: auto;
  font-size: 11px;
  color: #94a3b8;
  font-weight: 400;
  font-variant-numeric: tabular-nums;
}

.sub-agent-status-tag {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 4px;
  font-weight: 500;
  flex-shrink: 0;
}

.status-running {
  background: #dbeafe;
  color: #2563eb;
}

.status-completed {
  background: #dcfce7;
  color: #16a34a;
}

.status-failed,
.status-timeout {
  background: #fee2e2;
  color: #dc2626;
}

.sub-agent-task {
  font-size: 12px;
  color: #64748b;
  margin: 6px 0 8px 30px;
  line-height: 1.4;
}

.sub-agent-tools {
  margin-left: 30px;
  margin-bottom: 6px;
}

.sub-agent-section-label {
  font-size: 10px;
  font-weight: 600;
  color: #94a3b8;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 4px;
}

/* P0-B: 子代理思考过程 */
.sub-agent-thinking {
  margin: 6px 0 8px 30px;
}

.sub-agent-thinking-content {
  font-size: 12px;
  color: #64748b;
  line-height: 1.5;
  background: #fefce8;
  border-radius: 4px;
  padding: 6px 8px;
  max-height: 120px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-word;
  border-left: 2px solid #fbbf24;
}

/* P0-B: 子代理输出 */
.sub-agent-output {
  margin: 6px 0 0 30px;
}

.sub-agent-output-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}

.sub-agent-copy-btn {
  font-size: 11px;
  color: #64748b;
  background: none;
  border: 1px solid #e2e8f0;
  border-radius: 4px;
  padding: 1px 6px;
  cursor: pointer;
  transition: all 0.15s;
}

.sub-agent-copy-btn:hover {
  background: #f1f5f9;
  border-color: #cbd5e1;
}

.sub-agent-output-content {
  font-size: 12px;
  color: #334155;
  line-height: 1.5;
  background: #f8fafc;
  border-radius: 4px;
  padding: 8px;
  max-height: 200px;
  overflow-y: auto;
  word-break: break-word;
}

/* Markdown 渲染内容样式 */
.sub-agent-output-content :deep(p) {
  margin: 0 0 4px;
}

.sub-agent-output-content :deep(p:last-child) {
  margin-bottom: 0;
}

.sub-agent-output-content :deep(ul),
.sub-agent-output-content :deep(ol) {
  margin: 4px 0;
  padding-left: 20px;
}

.sub-agent-output-content :deep(code) {
  font-size: 0.9em;
  background: #e2e8f0;
  padding: 1px 4px;
  border-radius: 3px;
}

.sub-agent-output-content :deep(pre) {
  margin: 4px 0;
  padding: 6px 8px;
  background: #1e293b;
  color: #e2e8f0;
  border-radius: 4px;
  overflow-x: auto;
}

.sub-agent-output-content :deep(pre code) {
  background: none;
  padding: 0;
}

.sub-agent-output-content :deep(a) {
  color: #2563eb;
  text-decoration: none;
}

.sub-agent-output-content :deep(a:hover) {
  text-decoration: underline;
}

.sub-agent-thinking-content :deep(p) {
  margin: 0 0 4px;
}

.sub-agent-thinking-content :deep(p:last-child) {
  margin-bottom: 0;
}

.cursor-blink {
  display: inline-block;
  width: 2px;
  height: 14px;
  background: #3b82f6;
  margin-left: 1px;
  vertical-align: text-bottom;
  animation: blink 1s step-end infinite;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}

.sub-agent-error {
  margin: 4px 0 0 30px;
  font-size: 12px;
  color: #ef4444;
}

.sub-agent-compact-tools {
  font-size: 11px;
  color: #94a3b8;
  margin-left: 30px;
  margin-top: 2px;
}

/* P0-B: 操作按钮区 */
.sub-agent-actions {
  margin: 6px 0 0 30px;
  display: flex;
  gap: 6px;
}

.sub-agent-stop-btn {
  font-size: 11px;
  color: #94a3b8;
  background: none;
  border: 1px solid #e2e8f0;
  border-radius: 4px;
  padding: 2px 8px;
  cursor: pointer;
  transition: all 0.2s;
}

.sub-agent-stop-btn:hover {
  color: #ef4444;
  border-color: #ef4444;
}

/* 底部统计信息 */
.sub-agent-stats {
  margin: 8px 0 0 30px;
  padding-top: 6px;
  border-top: 1px solid #e2e8f0;
  display: flex;
  gap: 12px;
  font-size: 11px;
  color: #94a3b8;
  font-variant-numeric: tabular-nums;
}
</style>
