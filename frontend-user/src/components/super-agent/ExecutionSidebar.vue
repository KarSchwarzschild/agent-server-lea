<script setup lang="ts">
/**
 * 执行过程侧栏组件
 *
 * 从 MessageItem.vue 提取的执行过程渲染逻辑。
 * PC 端作为侧栏（300px），移动端内联渲染。
 * 包含：思考摘要面板、SubAgentGroup、ToolCallCard 列表
 */
import { computed } from 'vue'
import { NTag, NIcon, NButton } from 'naive-ui'
import {
  BulbOutline,
  ChevronDownOutline,
  DownloadOutline,
  EyeOutline,
  DocumentOutline,
} from '@vicons/ionicons5'
import type {
  ThinkingStep,
  ToolCall,
  SubAgentState,
  ViewMode,
  AggregationProgress,
  ExecutionPlanState,
} from '../../types/super-agent'
import {
  formatStepDuration,
  getVisibleToolSteps,
} from '../../types/super-agent'
import { extractFileIdFromUrl } from '../../api/superagent'
import { parseAllFileResults, isFileGenerationTool } from '../../composables/useFileParser'
import SubAgentGroup from './SubAgentGroup.vue'
import ToolCallCard from './ToolCallCard.vue'

const props = defineProps<{
  msgId: string
  thinkingSteps?: ThinkingStep[]
  toolCalls?: ToolCall[]
  subAgents?: SubAgentState[]
  isStreaming?: boolean
  viewMode: ViewMode
  expandedThinking: Set<string>
  isMobile?: boolean
  tokenUsage?: { inputTokens: number; outputTokens: number }
  aggregationProgress?: AggregationProgress
  executionPlan?: ExecutionPlanState | null
}>()

const emit = defineEmits<{
  (e: 'toggle-thinking', msgId: string): void
  (e: 'stop-session'): void
  (e: 'preview-file', fileInfo: { filename: string; downloadUrl: string; size: string }): void
  (e: 'download-file', downloadUrl: string): void
  (e: 'convert-to-pdf', fileInfo: { filename: string; downloadUrl: string; size: string }): void
}>()

// ==================== 计算属性 ====================

const showThinkingSummary = computed(() => props.viewMode === 'professional' || props.viewMode === 'debug')
const showToolCallSummary = computed(() => props.viewMode === 'professional' || props.viewMode === 'debug')
const showFullThinking = computed(() => props.viewMode === 'debug')
const showJsonLog = computed(() => props.viewMode === 'debug')

const visibleToolSteps = computed(() => getVisibleToolSteps(props.thinkingSteps, props.executionPlan))

// 过滤已隐藏的 SubAgent（invisible stage 完成后标记 isHidden）
const visibleSubAgents = computed(() => {
  if (!props.subAgents?.length) return []
  return props.subAgents.filter(sa => !sa.isHidden)
})

// 聚合模式：按路径分组的工具调用
const toolStepsByPath = computed(() => {
  const map = new Map<number, ThinkingStep[]>()
  for (const step of visibleToolSteps.value) {
    if (step.pathIndex !== undefined) {
      if (!map.has(step.pathIndex)) map.set(step.pathIndex, [])
      map.get(step.pathIndex)!.push(step)
    }
  }
  return map
})

// 聚合模式：是否有路径信息
const isAggregationMode = computed(() =>
  props.aggregationProgress && props.aggregationProgress.total > 1
)

// 获取某路最新正在执行的工具名
function getPathCurrentWork(pathIndex: number): string | null {
  const steps = toolStepsByPath.value.get(pathIndex)
  if (!steps || steps.length === 0) return null
  // 找最后一个 running/receiving 的
  for (let i = steps.length - 1; i >= 0; i--) {
    if (steps[i].status === 'running' || steps[i].status === 'receiving') {
      return steps[i].toolDisplayName || steps[i].toolName || null
    }
  }
  // 没有 running 的，返回最后一个已完成的工具
  const last = steps[steps.length - 1]
  return last.toolDisplayName || last.toolName || null
}

// 获取某路完成/总数
function getPathProgress(pathIndex: number): string {
  const steps = toolStepsByPath.value.get(pathIndex)
  if (!steps || steps.length === 0) return ''
  const done = steps.filter(s => s.status === 'success').length
  return `${done}/${steps.length}`
}

function formatPathTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return m > 0 ? `${m}:${String(s).padStart(2, '0')}` : `${s}s`
}

function getThinkingSummary(steps: ThinkingStep[] | undefined): ThinkingStep[] {
  if (!steps || steps.length === 0) return []
  if (props.viewMode === 'debug') return steps
  return steps.slice(-2)
}

function getStreamingInputRaw(toolCallId?: string): string | null {
  if (!toolCallId || !props.thinkingSteps) return null
  const step = props.thinkingSteps.find(
    (s: ThinkingStep) => s.toolCallId === toolCallId && s.inputRaw && (s.status === 'running' || s.status === 'receiving')
  )
  if (step?.inputRaw && !step.input) return step.inputRaw
  return null
}

// ==================== Token 可视化 ====================
const CONTEXT_WINDOW = 128000

function formatTokens(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K`
  return String(n)
}

const tokenTotal = computed(() => {
  if (!props.tokenUsage) return 0
  return props.tokenUsage.inputTokens + props.tokenUsage.outputTokens
})

const tokenBarColor = computed(() => {
  const ratio = tokenTotal.value / CONTEXT_WINDOW
  if (ratio > 0.75) return '#d03050'
  if (ratio > 0.5) return '#e6a23c'
  return '#18a058'
})

function tokenBarWidth(tokens: number): string {
  const pct = Math.min((tokens / CONTEXT_WINDOW) * 100, 100)
  return `${pct}%`
}

</script>

<template>
  <div class="execution-sidebar-inner">
    <!-- 思考摘要面板 -->
    <div
      v-if="showThinkingSummary && thinkingSteps && thinkingSteps.length > 0"
      class="thinking-summary-section"
      :class="{ 'mobile-thinking-section': isMobile }"
    >
      <div
        class="summary-header"
        :class="{ 'thinking-header': isMobile }"
        @click="isMobile ? emit('toggle-thinking', msgId) : undefined"
      >
        <n-icon v-if="!isMobile" :component="BulbOutline" class="summary-icon" />
        <span v-if="isMobile">💭 思考{{ showFullThinking ? '' : '摘要' }} ({{ getThinkingSummary(thinkingSteps).length }})</span>
        <template v-else>
          <span>思考{{ showFullThinking ? '过程' : '摘要' }}</span>
          <span v-if="!showFullThinking" class="summary-badge">最新{{ getThinkingSummary(thinkingSteps).length }}条</span>
        </template>
        <n-icon
          v-if="!showFullThinking && !isMobile"
          :component="ChevronDownOutline"
          class="expand-icon"
          :class="{ expanded: expandedThinking.has(msgId) }"
          @click.stop="emit('toggle-thinking', msgId)"
          style="cursor: pointer; margin-left: auto;"
        />
        <span v-if="isMobile">{{ expandedThinking.has(msgId) ? '▲' : '▼' }}</span>
      </div>
      <div
        v-if="!isMobile || expandedThinking.has(msgId) || showFullThinking"
        class="summary-content"
        :class="{ 'thinking-content': isMobile }"
      >
        <div
          v-for="step in (showFullThinking ? thinkingSteps : getThinkingSummary(thinkingSteps))"
          :key="step.step"
          class="summary-step"
          :class="{
            'tool-step': step.isToolCall,
            'success': step.status === 'success',
            'error': step.status === 'error',
            'running': step.status === 'running',
            'empty': step.status === 'empty',
            'thinking-step': isMobile,
          }"
        >
          <div class="step-row1">
            <span class="step-icon" :class="step.status">
              <template v-if="step.isToolCall">
                <span v-if="step.status === 'running'" class="icon-spinning">🔄</span>
                <span v-else-if="step.status === 'success'">✓</span>
                <span v-else-if="step.status === 'error'">⚠️</span>
                <span v-else-if="step.status === 'empty'">○</span>
                <span v-else>🔄</span>
              </template>
              <template v-else>
                <span class="bullet">•</span>
              </template>
            </span>
            <span class="step-text">
              <template v-if="isMobile">{{ step.step }}. {{ step.text }}</template>
              <template v-else>{{ step.toolDisplayName || step.text }}</template>
            </span>
            <span v-if="formatStepDuration(step) && step.status !== 'running'" class="step-duration">
              {{ formatStepDuration(step) }}
            </span>
          </div>
          <div v-if="step.summary && step.status !== 'running'" class="step-row2">
            <span class="step-summary">{{ step.summary }}</span>
          </div>
        </div>
        <!-- 专业模式下可展开查看全部 -->
        <div
          v-if="!showFullThinking && !isMobile && thinkingSteps.length > 2 && expandedThinking.has(msgId)"
          class="expanded-steps"
        >
          <div
            v-for="step in thinkingSteps.slice(0, -2)"
            :key="`full_${step.step}`"
            class="summary-step faded"
            :class="{
              'tool-step': step.isToolCall,
              'success': step.status === 'success',
              'error': step.status === 'error',
            }"
          >
            <div class="step-row1">
              <span class="step-icon" :class="step.status">
                <template v-if="step.isToolCall">
                  <span v-if="step.status === 'success'">✓</span>
                  <span v-else-if="step.status === 'error'">⚠️</span>
                  <span v-else>•</span>
                </template>
                <template v-else>
                  <span class="bullet">•</span>
                </template>
              </span>
              <span class="step-text">{{ step.toolDisplayName || step.text }}</span>
              <span v-if="formatStepDuration(step)" class="step-duration">{{ formatStepDuration(step) }}</span>
            </div>
            <div v-if="step.summary" class="step-row2">
              <span class="step-summary">{{ step.summary }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 子代理任务卡片组 -->
    <SubAgentGroup
      v-if="visibleSubAgents.length > 0"
      :sub-agents="visibleSubAgents"
      :compact="viewMode === 'simple'"
      @stop-session="emit('stop-session')"
    />

    <!-- Aggregation@N 聚合路径卡片（简洁摘要） -->
    <div v-if="isAggregationMode && aggregationProgress" class="aggregation-section">
      <div class="aggregation-header">
        <span class="aggregation-badge">{{ aggregationProgress.total }}路增强</span>
        <span v-if="aggregationProgress.phase === 'synthesis'" class="aggregation-phase">合成中...</span>
        <span v-else-if="aggregationProgress.phase === 'complete'" class="aggregation-phase done">聚合完成</span>
        <span v-else class="aggregation-phase">{{ aggregationProgress.done }}/{{ aggregationProgress.total }} 路完成</span>
      </div>
      <div class="aggregation-paths">
        <div
          v-for="path in aggregationProgress.paths"
          :key="`agg_path_${path.index}`"
          class="aggregation-path-card"
          :class="path.status"
        >
          <div class="path-header">
            <span class="path-status-icon">
              <template v-if="path.status === 'completed'">✅</template>
              <template v-else-if="path.status === 'running'">⏳</template>
              <template v-else-if="path.status === 'failed'">❌</template>
              <template v-else-if="path.status === 'timeout'">⚠️</template>
              <template v-else>○</template>
            </span>
            <span class="path-label">{{ path.label }}</span>
            <span class="path-meta">
              <template v-if="path.refs_count > 0">{{ path.refs_count }} 篇引用</template>
              <template v-if="path.tool_calls > 0"> {{ path.tool_calls }} 次调用</template>
            </span>
            <span v-if="path.duration_s > 0" class="path-duration">{{ formatPathTime(path.duration_s) }}</span>
          </div>
          <!-- running 时显示当前工作 -->
          <div v-if="path.status === 'running'" class="path-current-work">
            <span class="path-current-icon">🔧</span>
            <span class="path-current-text">{{ getPathCurrentWork(path.index) || '准备中...' }}</span>
            <span v-if="getPathProgress(path.index)" class="path-current-progress">{{ getPathProgress(path.index) }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 工具调用卡片（所有模式都平铺在路径卡片下方） -->
    <div
      v-if="showToolCallSummary && visibleToolSteps.length > 0"
      class="tool-cards-section"
      :class="{ 'mobile-tool-cards': isMobile }"
    >
      <ToolCallCard
        v-for="step in visibleToolSteps"
        :key="step.toolCallId || step.step"
        :step="step"
        :tool-call="toolCalls?.find((t: ToolCall) => t.toolCallId === step.toolCallId)"
        :show-json="showJsonLog"
        :streaming-input-raw="getStreamingInputRaw(step.toolCallId)"
      >
        <template #result="{ toolCall: tc }">
          <template v-for="fileInfo in (isFileGenerationTool(tc.tool) && tc.result ? parseAllFileResults(tc.result) : [])" :key="fileInfo.downloadUrl">
            <template v-if="fileInfo">
              <div class="file-download-card">
                <div class="file-card-header">
                  <span class="file-icon">📄</span>
                  <span class="file-name">{{ fileInfo.filename }}</span>
                </div>
                <div v-if="!isMobile" class="file-card-meta">
                  <span class="file-size">{{ fileInfo.size }}</span>
                </div>
                <div class="file-card-actions">
                  <n-button size="small" @click="emit('preview-file', fileInfo)">
                    <template v-if="!isMobile" #icon><n-icon><EyeOutline /></n-icon></template>
                    预览
                  </n-button>
                  <n-button
                    v-if="!isMobile && ['md', 'txt', 'json'].includes(fileInfo.filename?.split('.')?.pop()?.toLowerCase() || '')"
                    size="small"
                    @click="emit('convert-to-pdf', fileInfo)"
                  >
                    <template #icon><n-icon><DocumentOutline /></n-icon></template>
                    转PDF
                  </n-button>
                  <n-button type="primary" size="small" @click="emit('download-file', fileInfo.downloadUrl)">
                    <template v-if="!isMobile" #icon><n-icon><DownloadOutline /></n-icon></template>
                    下载
                  </n-button>
                </div>
              </div>
            </template>
          </template>
        </template>
      </ToolCallCard>
    </div>

    <!-- Token 消耗面板（仅 debug 模式） -->
    <div v-if="showJsonLog && tokenUsage && tokenTotal > 0" class="token-panel">
      <div class="token-title">Token 消耗</div>
      <div class="token-row">
        <span class="token-label">输入</span>
        <span class="token-value">{{ formatTokens(tokenUsage.inputTokens) }}</span>
        <div class="token-bar-bg">
          <div class="token-bar" :style="{ width: tokenBarWidth(tokenUsage.inputTokens), background: tokenBarColor }"></div>
        </div>
      </div>
      <div class="token-row">
        <span class="token-label">输出</span>
        <span class="token-value">{{ formatTokens(tokenUsage.outputTokens) }}</span>
        <div class="token-bar-bg">
          <div class="token-bar" :style="{ width: tokenBarWidth(tokenUsage.outputTokens), background: tokenBarColor }"></div>
        </div>
      </div>
      <div class="token-total">
        合计 {{ formatTokens(tokenTotal) }}
      </div>
    </div>
  </div>
</template>

<style scoped>
.execution-sidebar-inner {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* 思考摘要面板 */
.thinking-summary-section {
  padding: 10px 12px;
  background: linear-gradient(135deg, #F0F5FF 0%, #F5F0FF 100%);
  border-radius: 8px;
  border-left: 3px solid #667eea;
}

.summary-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 500;
  color: #4338ca;
  margin-bottom: 8px;
}

.summary-icon {
  color: #667eea;
}

.summary-badge {
  font-size: 11px;
  font-weight: normal;
  color: #9ca3af;
  background: #e5e7eb;
  padding: 2px 6px;
  border-radius: 10px;
}

.summary-content {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.summary-step {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 12px;
  color: #4b5563;
  line-height: 1.5;
  padding: 4px 8px;
  border-radius: 4px;
  transition: all 0.3s ease;
}

.step-row1 {
  display: flex;
  align-items: center;
  gap: 6px;
}

.step-row2 {
  padding-left: 22px; /* 对齐 icon(16px) + gap(6px) */
}

.summary-step.tool-step.success {
  background: linear-gradient(90deg, rgba(16, 185, 129, 0.1) 0%, transparent 100%);
}

.summary-step.tool-step.error {
  background: rgba(239, 68, 68, 0.1);
}

.summary-step.tool-step.running {
  background: rgba(59, 130, 246, 0.05);
}

.summary-step.tool-step.empty {
  background: rgba(156, 163, 175, 0.1);
}

.step-icon {
  flex-shrink: 0;
  width: 16px;
  height: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
}

.step-icon .bullet { color: #667eea; }
.step-icon.success { color: #10b981; font-weight: bold; }
.step-icon.error { color: #ef4444; }
.step-icon.running { color: #3b82f6; }
.step-icon.empty { color: #9ca3af; }

.step-icon .icon-spinning {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

@media (prefers-reduced-motion: reduce) {
  .step-icon .icon-spinning { animation: none; }
}

.step-text {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}

.step-summary {
  color: #6b7280;
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  white-space: normal;
  word-break: break-all;
  min-width: 0;
}

.summary-step.error .step-summary {
  color: #ef4444;
}

.step-duration {
  color: #9ca3af;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  margin-left: auto;
  flex-shrink: 0;
}

.summary-step.faded {
  color: #9ca3af;
}

.expanded-steps {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed #c7d2fe;
}

.expand-icon {
  transition: transform 0.2s;
}

.expand-icon.expanded {
  transform: rotate(180deg);
}

/* 工具调用卡片区域 */
.tool-cards-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.mobile-tool-cards {
  gap: 6px;
}

/* 文件下载卡片 */
.file-download-card {
  background: linear-gradient(135deg, #f0fdf4 0%, #ecfdf5 100%);
  border: 1px solid #bbf7d0;
  border-radius: 8px;
  padding: 12px 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.file-card-header {
  display: flex;
  align-items: center;
  gap: 8px;
}

.file-icon { font-size: 24px; }

.file-name {
  font-weight: 600;
  color: #166534;
  font-size: 14px;
  word-break: break-all;
}

.file-card-meta {
  display: flex;
  align-items: center;
  gap: 12px;
}

.file-size {
  font-size: 12px;
  color: #15803d;
  background: #dcfce7;
  padding: 2px 8px;
  border-radius: 4px;
}

.file-card-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 4px;
}

/* 移动端思考区域 */
.mobile-thinking-section {
  padding: 6px 10px;
  background: #fffbeb;
  border-radius: 6px;
  font-size: 12px;
}

.mobile-thinking-section .thinking-header {
  display: flex;
  justify-content: space-between;
  cursor: pointer;
  color: #92400e;
}

.mobile-thinking-section .thinking-content {
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px dashed #fcd34d;
}

.mobile-thinking-section .thinking-step {
  font-size: 11px;
  color: #78350f;
  margin-bottom: 2px;
}

/* Token 消耗面板 */
.token-panel {
  padding: 10px 12px;
  background: #fafafa;
  border-radius: 8px;
  border: 1px solid #e8e8e8;
}
.token-title {
  font-size: 12px;
  font-weight: 500;
  color: #666;
  margin-bottom: 8px;
}
.token-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}
.token-label {
  font-size: 11px;
  color: #999;
  width: 28px;
  flex-shrink: 0;
}
.token-value {
  font-size: 11px;
  color: #333;
  font-variant-numeric: tabular-nums;
  width: 42px;
  text-align: right;
  flex-shrink: 0;
}
.token-bar-bg {
  flex: 1;
  height: 6px;
  background: #e8e8e8;
  border-radius: 3px;
  overflow: hidden;
}
.token-bar {
  height: 100%;
  border-radius: 3px;
  transition: width 0.3s ease;
}
.token-total {
  font-size: 11px;
  color: #666;
  text-align: right;
  margin-top: 4px;
}

/* Aggregation@N 聚合路径卡片 */
.aggregation-section {
  margin-bottom: 12px;
}
.aggregation-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  font-size: 13px;
}
.aggregation-badge {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 600;
}
.aggregation-phase {
  color: #6b7280;
  font-size: 12px;
}
.aggregation-phase.done {
  color: #059669;
}
.aggregation-paths {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.aggregation-path-card {
  padding: 8px 10px;
  border-radius: 6px;
  background: #f9fafb;
  border: 1px solid #e5e7eb;
  transition: border-color 0.2s;
}
.aggregation-path-card.completed {
  border-color: #86efac;
  background: #f0fdf4;
}
.aggregation-path-card.running {
  border-color: #93c5fd;
  background: #eff6ff;
  animation: agg-pulse 2s ease-in-out infinite;
}
.aggregation-path-card.failed,
.aggregation-path-card.timeout {
  border-color: #fca5a5;
  background: #fef2f2;
}
@keyframes agg-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.7; }
}
.path-header {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
}
.path-status-icon {
  font-size: 13px;
  flex-shrink: 0;
}
.path-label {
  font-weight: 500;
  color: #374151;
  flex-shrink: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.path-meta {
  flex-shrink: 0;
  font-size: 11px;
  color: #9ca3af;
  white-space: nowrap;
  margin-left: auto;
}
.path-duration {
  color: #1a56db;
  font-size: 11px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  margin-left: 4px;
  flex-shrink: 0;
}
/* 当前工作摘要 */
.path-current-work {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 4px;
  padding-left: 20px;
  font-size: 11px;
  color: #6b7280;
  animation: fade-in 0.2s;
}
.path-current-icon {
  font-size: 11px;
}
.path-current-text {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.path-current-progress {
  color: #9ca3af;
  font-variant-numeric: tabular-nums;
}
@keyframes fade-in {
  from { opacity: 0; }
  to { opacity: 1; }
}
</style>
