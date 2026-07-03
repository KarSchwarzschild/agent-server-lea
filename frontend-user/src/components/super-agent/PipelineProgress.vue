<script setup lang="ts">
/**
 * PipelineProgress - 流式执行中的 Pipeline 实时进度视图
 *
 * 从 MessageItem.vue 提取（Phase 2.5）。
 * 仅在 msg.isStreaming && msg.executionPlan && !customStages 时显示。
 * 显示分析卡片/检索时间线/补充搜索/大纲预览/等待提示。
 *
 * 与 ResearchPipeline.vue 共享数据源但用于不同状态：
 * - PipelineProgress: 流式中的实时视图（展开态、有进度动画）
 * - ResearchPipeline: 完成后的折叠摘要（收起态、有概览 chips）
 */
import { ref, computed, watch } from 'vue'
import type { ChatMessage, ThinkingStep } from '../../types/super-agent'
import { renderCitationMarkdown, renderOutlineMarkdown } from '../../composables/useCitationRenderer'
import { useCitationTooltip } from '../../composables/useCitationTooltip'

const props = defineProps<{
  msg: ChatMessage
}>()

// ── 内部状态 ──
const expandedCards = ref<Record<string, boolean>>({})
const { handleCitationMousemove } = useCitationTooltip()

// ── 派生数据 ──

const analyzeOutput = computed(() =>
  props.msg.stageOutputs?.find(s => s.stage === 'analyze')?.content
)

const outlineOutput = computed(() =>
  props.msg.stageOutputs?.find(s => s.stage === 'outline')?.content
)

const outlineHtml = computed(() =>
  outlineOutput.value ? renderOutlineMarkdown(outlineOutput.value, props.msg.references || []) : ''
)

const gapDetectionOutput = computed(() =>
  props.msg.stageOutputs?.find(s => s.stage === 'gap_detection')?.content
)

const targetedResearchContent = computed(() =>
  props.msg.stageOutputs?.find(s => s.stage === 'targeted_research')?.content
)

const targetedResearchContentHtml = computed(() =>
  targetedResearchContent.value
    ? renderCitationMarkdown(targetedResearchContent.value, props.msg.references || [], 'TARGETED')
    : ''
)

const researchContentHtml = computed(() => {
  const c = props.msg.stageOutputs?.find(s => s.stage === 'research')?.content
  return c ? renderCitationMarkdown(c, props.msg.references || [], 'RESEARCH') : ''
})

const isResearchOrLater = computed(() => {
  const plan = props.msg.executionPlan
  if (!plan?.steps) return false
  const researchStep = plan.steps.find((s: any) => s.title === 'research' || s.title?.includes('信息收集') || s.title?.includes('资料收集'))
  return researchStep && (researchStep.status === 'running' || researchStep.status === 'completed')
})

const researchToolSteps = computed(() => {
  if (!props.msg.thinkingSteps) return []
  return props.msg.thinkingSteps.filter((s: ThinkingStep) =>
    s.isToolCall && (s.stage === 'research' || (!s.stage && !props.msg.executionPlan?.customStages))
  )
})

const targetedResearchToolSteps = computed(() => {
  if (!props.msg.thinkingSteps) return []
  return props.msg.thinkingSteps.filter((s: ThinkingStep) =>
    s.isToolCall && s.stage === 'targeted_research'
  )
})

const isAggregationMode = computed(() => {
  if (props.msg.aggregationProgress && props.msg.aggregationProgress.total > 1) return true
  if (props.msg.thinkingSteps?.some((s: ThinkingStep) => s.pathIndex !== undefined)) return true
  return false
})

const pathGroupedToolSteps = computed(() => {
  if (!isAggregationMode.value || !props.msg.thinkingSteps) return []

  const agg = props.msg.aggregationProgress
  if (agg && agg.paths?.length > 0) {
    const groups: Array<{
      index: number; label: string; status: string;
      refsCount: number; durationS: number; steps: ThinkingStep[]
    }> = []

    for (const p of agg.paths) {
      const pathSteps = props.msg.thinkingSteps.filter(
        (s: ThinkingStep) => s.isToolCall && s.pathIndex === p.index
      )
      let liveStatus = p.status
      if (p.status === 'pending' && pathSteps.length > 0) {
        liveStatus = 'running'
      }
      groups.push({
        index: p.index,
        label: p.label,
        status: liveStatus,
        refsCount: p.refs_count || 0,
        durationS: p.duration_s || 0,
        steps: pathSteps,
      })
    }
    return groups
  }

  const pathMap = new Map<number, { label: string, steps: ThinkingStep[] }>()
  for (const s of props.msg.thinkingSteps) {
    if (!s.isToolCall || s.pathIndex === undefined) continue
    if (!pathMap.has(s.pathIndex)) {
      pathMap.set(s.pathIndex, { label: s.pathLabel || `路${s.pathIndex + 1}`, steps: [] })
    }
    pathMap.get(s.pathIndex)!.steps.push(s)
  }
  return Array.from(pathMap.entries()).map(([idx, { label, steps }]) => ({
    index: idx,
    label,
    status: steps.some(s => s.status === 'running' || s.status === 'receiving') ? 'running' : 'completed',
    refsCount: 0,
    durationS: 0,
    steps,
  }))
})

const nonPathToolSteps = computed(() => {
  if (!isAggregationMode.value) return researchToolSteps.value
  return (props.msg.thinkingSteps || []).filter((s: ThinkingStep) =>
    s.isToolCall && s.stage === 'research' && (s.pathIndex === undefined || s.pathIndex === null)
  )
})

const totalAggRefs = computed(() => {
  if (!props.msg.aggregationProgress?.paths) return 0
  return props.msg.aggregationProgress.paths.reduce((sum: number, p: any) => sum + (p.refs_count || 0), 0)
})

const doneToolCount = computed(() =>
  researchToolSteps.value.filter(s => s.status === 'success' || s.status === 'error' || s.status === 'empty').length
)

const researchStepTitle = computed(() => {
  const plan = props.msg.executionPlan
  if (!plan) return '信息收集'
  const step = plan.steps?.find((s: any) => s.title?.includes('信息收集') || s.title?.includes('资料收集'))
    || plan.steps?.[1]
  return step?.title || '信息收集'
})

const STAGE_HINTS: Record<string, string> = {
  analyze: '正在分析...', research: '正在检索...', outline: '正在拟定大纲...', write: '正在撰写...',
}

const pipelineWaitingText = computed(() => {
  const plan = props.msg.executionPlan
  if (!plan?.steps) return '正在处理...'
  const running = plan.steps.find((s: any) => s.status === 'running')
  if (running) return STAGE_HINTS[running.title] || `${running.displayName || running.title}...`
  return '正在处理...'
})

const showPipelineHint = computed(() => {
  return props.msg.isStreaming && props.msg.executionPlan && !props.msg.content?.trim()
    && !analyzeOutput.value && !isResearchOrLater.value
})

/** 合成阶段开始时间（用于实时计时） */
const synthesisStartTime = ref<number | null>(null)
watch(() => props.msg.aggregationProgress?.phase, (phase) => {
  if (phase === 'synthesis' && !synthesisStartTime.value) {
    synthesisStartTime.value = Date.now()
  } else if (phase === 'complete') {
    synthesisStartTime.value = null
  }
})

function isStageActive(stageName: string): boolean {
  return props.msg.executionPlan?.steps?.some((s: any) => s.title === stageName && s.status === 'running') || false
}

function getStepKeywords(step: ThinkingStep): string {
  if (!step.input) return ''
  try {
    const args = typeof step.input === 'string' ? JSON.parse(step.input) : step.input
    return args.query || args.url || ''
  } catch { return '' }
}

/** 折叠态：获取路径当前最新工具调用信息 */
function getPathCurrentInfo(steps: ThinkingStep[]): string {
  const running = steps.filter(s => s.status === 'running')
  if (running.length) {
    return '⟳ ' + running.map(s => {
      const name = s.toolDisplayName || s.toolName || ''
      const kw = getStepKeywords(s)
      return kw ? `${name} ${kw}` : name
    }).join(', ')
  }
  const done = steps.filter(s => s.status === 'success' || s.status === 'error' || s.status === 'empty')
  if (done.length) {
    const last = done[done.length - 1]
    const name = last.toolDisplayName || last.toolName || ''
    const kw = getStepKeywords(last)
    const summary = last.summary ? ` — ${last.summary}` : ''
    const icon = last.status === 'success' ? '✓' : last.status === 'error' ? '✗' : '○'
    return `${icon} ${name}${kw ? ' ' + kw : ''}${summary}`
  }
  return '⟳ 准备中...'
}

// 计时器
const timerTick = ref(0)
let timerInterval: ReturnType<typeof setInterval> | null = null

function formatElapsed(startTime: number): string {
  void timerTick.value
  const sec = Math.floor((Date.now() - startTime) / 1000)
  const m = Math.floor(sec / 60)
  const s = sec % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

import { onMounted, onUnmounted } from 'vue'
onMounted(() => {
  timerInterval = setInterval(() => { timerTick.value++ }, 1000)
})
onUnmounted(() => {
  if (timerInterval) clearInterval(timerInterval)
})

function renderBrief(content: string): string {
  return renderCitationMarkdown(content, props.msg.references || [], 'PIPE')
}
</script>

<template>
  <div class="pipeline-progress">
    <!-- analyze 阶段：调研计划卡片 -->
    <div v-if="analyzeOutput || msg.analyzePlan" class="progress-card">
      <div class="progress-card-header">调研计划</div>
      <div class="progress-card-body" :class="{ expanded: expandedCards['analyze'] }">
        <!-- 流式文本部分（概念校准、范围等） -->
        <div v-if="analyzeOutput" v-html="renderBrief(analyzeOutput)" />
        <!-- 结构化搜索计划（JSON 解析后渲染） -->
        <div v-if="msg.analyzePlan" class="analyze-plan-structured">
          <div v-if="msg.analyzePlan.keyQuestions.length" class="plan-questions">
            <div class="plan-label">核心问题</div>
            <div v-for="(q, i) in msg.analyzePlan.keyQuestions" :key="i" class="plan-question-item">{{ q }}</div>
          </div>
          <table v-if="msg.analyzePlan.searchSteps.length" class="plan-search-table">
            <thead><tr><th>步骤</th><th>搜索方式</th><th>关键词</th><th>语言</th><th>目的</th></tr></thead>
            <tbody>
              <tr v-for="s in msg.analyzePlan.searchSteps" :key="s.stepId">
                <td>{{ s.stepId }}</td>
                <td>{{ s.toolDisplay }}</td>
                <td>{{ s.keywords }}</td>
                <td>{{ s.language }}</td>
                <td>{{ s.purpose }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      <button class="card-expand-btn" @click="expandedCards['analyze'] = !expandedCards['analyze']">
        {{ expandedCards['analyze'] ? '收起' : '展开' }}
      </button>
    </div>

    <!-- research 阶段：搜索活动时间线 -->
    <div v-if="isResearchOrLater" class="progress-card">
      <div class="progress-card-header">
        {{ researchStepTitle }}
        <span v-if="isStageActive('research') && !isAggregationMode" class="badge-running">
          {{ doneToolCount }}/{{ researchToolSteps.length }}
        </span>
        <span v-else-if="!isStageActive('research')" class="badge-done">已完成</span>
      </div>

      <!-- 聚合模式：概念校准工具调用（无 pathIndex 的） -->
      <div v-if="isAggregationMode && nonPathToolSteps.length" class="search-timeline">
        <div v-for="step in nonPathToolSteps" :key="step.toolCallId || step.step" class="timeline-item" :class="{ running: step.status === 'running' }">
          <span class="timeline-status">{{ step.status === 'success' ? '✓' : step.status === 'error' ? '✗' : step.status === 'running' ? '⟳' : '○' }}</span>
          <span class="timeline-tool">{{ step.toolDisplayName || step.toolName }}</span>
          <span v-if="getStepKeywords(step)" class="timeline-keywords">{{ getStepKeywords(step) }}</span>
          <span v-if="step.summary" class="timeline-summary">— {{ step.summary }}</span>
        </div>
      </div>

      <!-- 聚合模式：路径分组卡片 -->
      <div v-if="isAggregationMode" class="agg-path-cards">
        <div
          v-for="group in pathGroupedToolSteps"
          :key="group.index"
          class="agg-path-card"
          :class="group.status"
        >
          <div class="agg-path-card-header" @click="expandedCards[`path_${group.index}`] = !expandedCards[`path_${group.index}`]" style="cursor: pointer;">
            <span class="agg-path-card-icon">
              <template v-if="group.status === 'completed'">✅</template>
              <template v-else-if="group.status === 'running'">⏳</template>
              <template v-else-if="group.status === 'failed' || group.status === 'timeout'">❌</template>
              <template v-else>○</template>
            </span>
            <span class="agg-path-card-label">路{{ group.index + 1 }}: {{ group.label }}</span>
            <span class="agg-path-card-meta">
              <template v-if="group.status === 'completed' && group.refsCount">{{ group.refsCount }}篇引用</template>
              <template v-else-if="group.status === 'failed'">失败</template>
              <template v-else-if="group.status === 'timeout'">超时</template>
              <template v-else-if="group.steps.length">{{ group.steps.filter(s => s.status === 'success' || s.status === 'error' || s.status === 'empty').length }}/{{ group.steps.length }} 调用</template>
              <template v-else-if="group.status === 'running'">搜索中</template>
            </span>
            <!-- 实时耗时：执行中显示动态计时，完成后显示最终耗时 -->
            <span v-if="group.durationS" class="agg-path-card-time">{{ group.durationS.toFixed(0) }}s</span>
            <span v-else-if="group.steps.length && group.steps[0].startTime && (group.status === 'running' || group.status === 'pending')" class="agg-path-card-time running-timer">{{ formatElapsed(group.steps[0].startTime) }}</span>
            <i class="chip-arrow agg-path-arrow" :class="{ open: expandedCards[`path_${group.index}`] }">›</i>
          </div>
          <!-- 折叠态：显示最新工具调用信息 -->
          <div v-if="!expandedCards[`path_${group.index}`] && group.status === 'running' && group.steps.length" class="agg-path-current">
            {{ getPathCurrentInfo(group.steps) }}
          </div>
          <!-- 展开态：路内工具调用时间线 -->
          <div v-if="expandedCards[`path_${group.index}`] && group.steps.length" class="search-timeline agg-path-timeline">
            <div v-for="step in group.steps" :key="step.toolCallId || step.step" class="timeline-item" :class="{ running: step.status === 'running' }">
              <span class="timeline-status">{{ step.status === 'success' ? '✓' : step.status === 'error' ? '✗' : step.status === 'running' ? '⟳' : '○' }}</span>
              <span class="timeline-tool">{{ step.toolDisplayName || step.toolName }}</span>
              <span v-if="getStepKeywords(step)" class="timeline-keywords">{{ getStepKeywords(step) }}</span>
              <span v-if="step.summary" class="timeline-summary">— {{ step.summary }}</span>
            </div>
          </div>
          <div v-else-if="!expandedCards[`path_${group.index}`] && group.status === 'running' && !group.steps.length" class="agg-path-loading">搜索中...</div>
          <!-- 完成摘要（折叠态也显示） -->
          <div v-if="group.status === 'completed' && group.refsCount && !expandedCards[`path_${group.index}`]" class="agg-path-card-summary">
            {{ group.refsCount }} 篇引用 · {{ group.steps.length }} 次工具调用
          </div>
        </div>

        <!-- 聚合合成进度指示器 -->
        <div v-if="msg.aggregationProgress?.phase === 'synthesis'" class="agg-synthesis-indicator">
          <div class="agg-synthesis-header">
            <span class="agg-synthesis-icon">🧠</span>
            <span class="agg-synthesis-title">正在合成 {{ msg.aggregationProgress.done }} 路搜索结果...</span>
            <span v-if="synthesisStartTime" class="agg-synthesis-timer">{{ formatElapsed(synthesisStartTime) }}</span>
          </div>
          <div class="agg-synthesis-stats">
            <span v-for="p in msg.aggregationProgress.paths" :key="p.index" class="agg-synthesis-path-chip" :class="p.status">
              路{{ p.index + 1 }}: {{ p.label }}
              <template v-if="p.refs_count"> · {{ p.refs_count }}篇</template>
            </span>
          </div>
          <div v-if="msg.synthesisContent" class="agg-synthesis-content" v-html="renderBrief(msg.synthesisContent)" />
          <div v-else class="agg-synthesis-waiting">
            <span class="synthesis-dots">合成模型处理中</span>
          </div>
        </div>

        <!-- 聚合完成摘要 + 可展开合成数据 -->
        <div v-else-if="msg.aggregationProgress?.phase === 'complete'" class="agg-synthesis-done">
          <div class="agg-synthesis-done-header" @click="expandedCards['agg_synthesis'] = !expandedCards['agg_synthesis']">
            <span class="agg-synthesis-done-icon">✅</span>
            <span class="agg-synthesis-done-text">
              {{ msg.aggregationProgress.done }} 路搜索结果合成完成
              <template v-if="msg.synthesisContent"> · {{ msg.synthesisContent.length }}字</template>
              <template v-if="totalAggRefs"> · {{ totalAggRefs }}篇引用</template>
            </span>
            <i v-if="msg.synthesisContent" class="chip-arrow" :class="{ open: expandedCards['agg_synthesis'] }">›</i>
          </div>
          <div v-if="expandedCards['agg_synthesis'] && msg.synthesisContent" class="agg-synthesis-detail" @mousemove="handleCitationMousemove" v-html="renderBrief(msg.synthesisContent)" />
        </div>
      </div>

      <!-- 非聚合模式：原有扁平时间线 -->
      <div v-if="!isAggregationMode" class="search-timeline">
        <div v-for="step in researchToolSteps" :key="step.toolCallId || step.step" class="timeline-item" :class="{ running: step.status === 'running' }">
          <span class="timeline-status">{{ step.status === 'success' ? '✓' : step.status === 'error' ? '✗' : step.status === 'running' ? '⟳' : '○' }}</span>
          <span class="timeline-tool">{{ step.toolDisplayName || step.toolName }}</span>
          <span v-if="getStepKeywords(step)" class="timeline-keywords">{{ getStepKeywords(step) }}</span>
          <span v-if="step.summary" class="timeline-summary">— {{ step.summary }}</span>
        </div>
      </div>

      <div v-if="researchContentHtml" class="research-thinking" v-html="researchContentHtml" />
    </div>

    <!-- gap_detection + targeted_research 阶段：补充搜索 -->
    <div v-if="gapDetectionOutput || targetedResearchContentHtml || isStageActive('gap_detection') || isStageActive('targeted_research')" class="progress-card">
      <div class="progress-card-header">
        补充搜索
        <span v-if="isStageActive('gap_detection') || isStageActive('targeted_research')" class="badge-running">进行中</span>
        <span v-else class="badge-done">已完成</span>
      </div>
      <div v-if="gapDetectionOutput" class="research-thinking" @mousemove="handleCitationMousemove" v-html="renderBrief(gapDetectionOutput)" />
      <!-- 补充搜索工具调用时间线 -->
      <div v-if="targetedResearchToolSteps.length" class="search-timeline" style="margin: 8px 0;">
        <div v-for="step in targetedResearchToolSteps" :key="step.toolCallId || step.step" class="timeline-item" :class="{ running: step.status === 'running' }">
          <span class="timeline-status">{{ step.status === 'success' ? '✓' : step.status === 'error' ? '✗' : step.status === 'running' ? '⟳' : '○' }}</span>
          <span class="timeline-tool">{{ step.toolDisplayName || step.toolName }}</span>
          <span v-if="getStepKeywords(step)" class="timeline-keywords">{{ getStepKeywords(step) }}</span>
          <span v-if="step.summary" class="timeline-summary">— {{ step.summary }}</span>
        </div>
      </div>
      <div v-if="targetedResearchContentHtml" class="research-thinking" @mousemove="handleCitationMousemove" v-html="targetedResearchContentHtml" />
    </div>

    <!-- outline 阶段：大纲预览 -->
    <div v-if="outlineOutput" class="progress-card">
      <div class="progress-card-header">报告大纲</div>
      <div class="progress-card-body outline-preview" :class="{ expanded: expandedCards['outline'] }" @mousemove="handleCitationMousemove" v-html="outlineHtml" />
      <button class="card-expand-btn" @click="expandedCards['outline'] = !expandedCards['outline']">
        {{ expandedCards['outline'] ? '收起' : '展开' }}
      </button>
    </div>

    <!-- 等待提示（当前有 running 阶段且主区域无内容时） -->
    <div v-if="showPipelineHint" class="pipeline-waiting-hint">
      <span class="pipeline-waiting-icon">
        <svg class="spin-icon" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/>
        </svg>
      </span>
      <span class="pipeline-waiting-text">{{ pipelineWaitingText }}</span>
    </div>
  </div>
</template>


<style>
.pipeline-progress {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.progress-card {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 12px 16px;
  border-left: 3px solid #3b82f6;
}

.progress-card-header {
  font-size: 13px;
  font-weight: 600;
  color: #374151;
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.progress-card-body {
  font-size: 13px;
  color: #6b7280;
  line-height: 1.6;
  max-height: 120px;
  overflow: hidden;
  mask-image: linear-gradient(to bottom, black 80%, transparent 100%);
  -webkit-mask-image: linear-gradient(to bottom, black 80%, transparent 100%);
  transition: max-height 0.3s ease;
}

.progress-card-body.expanded {
  max-height: none;
  mask-image: none;
  -webkit-mask-image: none;
}

.card-expand-btn {
  background: none;
  border: none;
  color: #3b82f6;
  font-size: 12px;
  cursor: pointer;
  padding: 4px 0 0;
  display: block;
}

.card-expand-btn:hover {
  color: #2563eb;
  text-decoration: underline;
}

.search-timeline {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.timeline-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #6b7280;
  padding: 2px 0;
}

.timeline-status { width: 14px; text-align: center; flex-shrink: 0; }
.timeline-tool { font-weight: 500; color: #374151; flex-shrink: 0; }
.timeline-keywords {
  color: #6b7280;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}
.timeline-summary { color: #9ca3af; font-size: 12px; flex-shrink: 0; }
.timeline-item.running .timeline-status {
  display: inline-block;
  animation: spin 1.5s linear infinite;
}
@media (prefers-reduced-motion: reduce) {
  .timeline-item.running .timeline-status { animation: none; }
}
/* === 聚合路径卡片 === */
.agg-path-cards {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 8px;
}
.agg-path-card {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 10px 12px;
  background: #fff;
  transition: border-color 0.3s;
}
.agg-path-card.completed {
  border-color: #86efac;
  background: #f0fdf4;
}
.agg-path-card.running {
  border-color: #93c5fd;
  background: #eff6ff;
  animation: pathCardPulse 2s ease-in-out infinite;
}
.agg-path-card.failed,
.agg-path-card.timeout {
  border-color: #fca5a5;
  background: #fef2f2;
}
.agg-path-card-header {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  user-select: none;
}
.agg-path-arrow {
  font-size: 14px;
  color: #9ca3af;
  margin-left: 2px;
  flex-shrink: 0;
}
.agg-path-current {
  font-size: 11px;
  color: #6b7280;
  padding-left: 22px;
  margin-top: 3px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.agg-path-card-icon {
  font-size: 14px;
  flex-shrink: 0;
}
.agg-path-card-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.agg-path-card-time {
  font-size: 12px;
  color: #1a56db;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}
.agg-path-card-time.running-timer {
  color: #3b82f6;
  font-variant-numeric: tabular-nums;
}
.agg-path-card-meta {
  font-size: 11px;
  color: #6b7280;
  flex-shrink: 0;
  margin-left: auto;
  margin-right: 4px;
}
.agg-path-timeline {
  margin-top: 6px;
  padding-left: 4px;
  border-left: 2px solid #e5e7eb;
}
.agg-path-card.completed .agg-path-timeline {
  border-left-color: #86efac;
}
.agg-path-card.running .agg-path-timeline {
  border-left-color: #93c5fd;
}
.agg-path-loading {
  font-size: 12px;
  color: #6b7280;
  margin-top: 4px;
  padding-left: 8px;
}
.agg-path-card-summary {
  font-size: 11px;
  color: #6b7280;
  margin-top: 6px;
  padding-top: 4px;
  border-top: 1px solid #f3f4f6;
}
/* 聚合合成进度指示器 */
.agg-synthesis-indicator {
  margin-top: 10px;
  padding: 10px 12px;
  background: linear-gradient(135deg, #eff6ff 0%, #f0fdf4 100%);
  border: 1px solid #bfdbfe;
  border-radius: 8px;
  animation: synthesisPulse 2s ease-in-out infinite;
}
.agg-synthesis-header {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 500;
  color: #1e40af;
}
.agg-synthesis-icon {
  font-size: 16px;
}
.agg-synthesis-timer {
  margin-left: auto;
  font-size: 12px;
  color: #3b82f6;
  font-variant-numeric: tabular-nums;
}
.agg-synthesis-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}
.agg-synthesis-path-chip {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 10px;
  background: #dbeafe;
  color: #1e40af;
}
.agg-synthesis-path-chip.completed {
  background: #dcfce7;
  color: #166534;
}
.agg-synthesis-path-chip.failed,
.agg-synthesis-path-chip.timeout {
  background: #fef2f2;
  color: #991b1b;
}
.agg-synthesis-content {
  margin-top: 8px;
  font-size: 12px;
  color: #374151;
  line-height: 1.6;
  max-height: 200px;
  overflow-y: auto;
  padding: 6px 8px;
  background: rgba(255,255,255,0.6);
  border-radius: 4px;
}
.agg-synthesis-content h1,
.agg-synthesis-content h2,
.agg-synthesis-content h3,
.agg-synthesis-content h4 {
  font-size: 13px;
  font-weight: 600;
  margin: 8px 0 4px;
  color: #1f2937;
}
.agg-synthesis-content h3 { font-size: 12.5px; }
.agg-synthesis-content p { margin: 4px 0; }
.agg-synthesis-content ul, .agg-synthesis-content ol {
  padding-left: 18px;
  margin: 4px 0;
}
.agg-synthesis-content li { margin: 2px 0; }
.agg-synthesis-content strong { font-weight: 600; }
.agg-synthesis-waiting {
  margin-top: 8px;
  font-size: 12px;
  color: #6b7280;
}
.synthesis-dots::after {
  content: '';
  animation: synthesisDots 1.5s steps(3, end) infinite;
}
@keyframes synthesisDots {
  0% { content: '.'; }
  33% { content: '..'; }
  66% { content: '...'; }
}
@keyframes synthesisPulse {
  0%, 100% { border-color: #bfdbfe; }
  50% { border-color: #93c5fd; }
}

/* 聚合完成摘要 */
.agg-synthesis-done {
  margin-top: 10px;
  padding: 8px 12px;
  background: #f0fdf4;
  border: 1px solid #86efac;
  border-radius: 8px;
  font-size: 13px;
  color: #166534;
}
.agg-synthesis-done-header {
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
}
.agg-synthesis-done-header:hover {
  opacity: 0.8;
}
.agg-synthesis-done-header .chip-arrow {
  margin-left: auto;
  font-style: normal;
  font-size: 14px;
  transition: transform 0.2s;
  color: #6b7280;
}
.agg-synthesis-done-header .chip-arrow.open {
  transform: rotate(90deg);
}
.agg-synthesis-done-icon {
  font-size: 14px;
}
.agg-synthesis-detail {
  margin-top: 8px;
  padding: 10px 12px;
  background: rgba(255,255,255,0.7);
  border-radius: 6px;
  font-size: 12.5px;
  color: #374151;
  line-height: 1.7;
  max-height: 400px;
  overflow-y: auto;
  border: 1px solid #e5e7eb;
}
.agg-synthesis-detail h1,
.agg-synthesis-detail h2,
.agg-synthesis-detail h3,
.agg-synthesis-detail h4 {
  font-size: 14px;
  font-weight: 600;
  margin: 10px 0 6px;
  color: #1f2937;
}
.agg-synthesis-detail h3 { font-size: 13.5px; }
.agg-synthesis-detail p { margin: 6px 0; }
.agg-synthesis-detail ul, .agg-synthesis-detail ol {
  padding-left: 20px;
  margin: 6px 0;
}
.agg-synthesis-detail li { margin: 3px 0; }
.agg-synthesis-detail strong { font-weight: 600; }

@keyframes pathCardPulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.85; }
}
@media (prefers-reduced-motion: reduce) {
  .agg-path-card.running { animation: none; }
}

.badge-running {
  font-size: 12px;
  color: #3b82f6;
  background: #eff6ff;
  padding: 1px 6px;
  border-radius: 4px;
  animation: pulse-badge 2s ease-in-out infinite;
}

.badge-done {
  font-size: 11px;
  color: #10b981;
  background: #ecfdf5;
  padding: 1px 6px;
  border-radius: 4px;
}

@keyframes pulse-badge {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

.pipeline-collapsed-summary {
  display: flex;
  gap: 8px;
  padding: 6px 12px;
  background: #f0f9ff;
  border-radius: 6px;
  font-size: 12px;
  color: #64748b;
  margin-bottom: 8px;
  flex-wrap: wrap;
}

.pipeline-waiting-hint {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 16px;
  color: #4b5563;
  font-size: 14px;
  background: #f0f7ff;
  border-radius: 8px;
  margin-top: 8px;
}

.pipeline-waiting-icon {
  display: flex;
  align-items: center;
  color: #3b82f6;
}

.pipeline-waiting-icon .spin-icon {
  width: 18px;
  height: 18px;
}

.pipeline-waiting-text {
  color: #4b5563;
}
</style>
