<script setup lang="ts">
/**
 * ResearchPipeline - 研究流水线摘要面板
 *
 * 从 MessageItem.vue 提取（Phase 2.3）。
 * 职责：折叠摘要 chips + 展开详情（分析计划/检索结果/大纲/自定义阶段）
 *       + 并行子任务面板 + 聚合合成结果
 *
 * expandedCards 状态完全在此组件内部管理（不跨组件共享）。
 */
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import MarkdownIt from 'markdown-it'
import type { ChatMessage, ThinkingStep, Reference } from '../../types/super-agent'
import { renderCitationMarkdown, renderOutlineMarkdown, renderSubtaskMarkdown } from '../../composables/useCitationRenderer'
import { useCitationTooltip } from '../../composables/useCitationTooltip'

const props = defineProps<{
  msg: ChatMessage
  viewMode?: string
}>()

const emit = defineEmits<{
  (e: 'citation-click', event: MouseEvent, references: Reference[]): void
}>()

// ── 展开状态（全部内部管理，不暴露）──
const expandedCards = ref<Record<string, boolean>>({})

const SUMMARY_CHIP_KEYS = ['s_analyze', 's_research', 's_targeted', 's_outline']

function toggleSummaryChip(key: string) {
  const willOpen = !expandedCards.value[key]
  // 互斥：关闭所有
  for (const k of [...SUMMARY_CHIP_KEYS, ...customStageOutputs.value.map(s => `s_custom_${s.stage}`)]) {
    expandedCards.value[k] = false
  }
  if (willOpen) expandedCards.value[key] = true
}

// ── 派生数据（从 msg 计算）──

const customStageOutputs = computed(() =>
  (props.msg.stageOutputs || []).filter(s =>
    !['analyze', 'research', 'targeted_research', 'outline', 'write', 'revise', 'gap_detection'].includes(s.stage)
  )
)

const analyzeOutput = computed(() =>
  props.msg.stageOutputs?.find(s => s.stage === 'analyze')?.content
)

const outlineOutput = computed(() =>
  props.msg.stageOutputs?.find(s => s.stage === 'outline')?.content
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

const outlineHtml = computed(() =>
  outlineOutput.value
    ? renderOutlineMarkdown(outlineOutput.value, props.msg.references || [])
    : ''
)

const researchToolSteps = computed(() => {
  if (!props.msg.thinkingSteps) return []
  const plan = props.msg.executionPlan
  const researchStages = plan?.steps
    ?.filter((s: any) => s.title === 'research' || s.title?.includes('research'))
    .map((s: any) => s.title) || ['research']
  return props.msg.thinkingSteps.filter((s: ThinkingStep) =>
    s.isToolCall && (researchStages.includes(s.stage) || (!s.stage && !plan?.customStages))
  )
})

const isAggregationMode = computed(() => {
  return !!(props.msg.aggregationProgress && props.msg.aggregationProgress.paths?.length > 1)
    || !!(props.msg.thinkingSteps?.some((s: ThinkingStep) => s.pathIndex !== undefined && s.pathIndex !== null))
})

const pathGroupedToolSteps = computed(() => {
  if (!isAggregationMode.value || !props.msg.thinkingSteps) return []
  const groups = new Map<number, any>()
  const agg = props.msg.aggregationProgress
  // 初始化路径组
  if (agg?.paths) {
    for (const p of agg.paths) {
      groups.set(p.index, {
        index: p.index,
        label: p.label || `路径 ${p.index + 1}`,
        status: p.status || 'running',
        refsCount: p.refs_count || 0,
        durationS: p.duration_s || 0,
        steps: [],
      })
    }
  }
  // 填充工具步骤
  for (const step of props.msg.thinkingSteps) {
    if (step.isToolCall && step.pathIndex !== undefined && step.pathIndex !== null) {
      let group = groups.get(step.pathIndex)
      if (!group) {
        group = { index: step.pathIndex, label: step.pathLabel || `路径 ${step.pathIndex + 1}`, status: 'running', refsCount: 0, durationS: 0, steps: [] }
        groups.set(step.pathIndex, group)
      }
      group.steps.push(step)
    }
  }
  return Array.from(groups.values()).sort((a, b) => a.index - b.index)
})

const totalAggRefs = computed(() => {
  if (!props.msg.aggregationProgress?.paths) return 0
  return props.msg.aggregationProgress.paths.reduce((sum: number, p: any) => sum + (p.refs_count || 0), 0)
})

const parallelPanelCollapsed = ref(false)

// ── 并行面板逻辑 ──

function taskStatusIcon(t: { status?: string }): string {
  switch (t.status) {
    case 'done': return '✓'
    case 'failed': return '⚠'
    case 'cancelled': return '⊘'
    case 'running': return '◉'
    default: return '⟳'
  }
}

// 计时器：每秒触发响应式更新
const timerTick = ref(0)
let timerInterval: ReturnType<typeof setInterval> | null = null

function formatElapsed(startTime: number): string {
  void timerTick.value
  const sec = Math.floor((Date.now() - startTime) / 1000)
  const m = Math.floor(sec / 60)
  const s = sec % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

onMounted(() => {
  timerInterval = setInterval(() => { timerTick.value++ }, 1000)
})
onUnmounted(() => {
  if (timerInterval) clearInterval(timerInterval)
  if (synthInterval) clearInterval(synthInterval)
})

// 合成阶段动态文案轮播
const synthesizingMessages = [
  '正在合成研究结果...',
  '正在汇总各子任务数据...',
  '正在生成综述大纲...',
  '正在进行格式排版...',
]
const synthesizingIndex = ref(0)
const synthesizingText = computed(() => synthesizingMessages[synthesizingIndex.value % synthesizingMessages.length])
let synthInterval: ReturnType<typeof setInterval> | null = null

watch(() => props.msg.isSynthesizing, (val) => {
  if (val) {
    synthesizingIndex.value = 0
    synthInterval = setInterval(() => { synthesizingIndex.value++ }, 12000)
  } else {
    if (synthInterval) { clearInterval(synthInterval); synthInterval = null }
  }
})

// ── 并行面板分组（依赖可视化） ──

const independentTasks = computed(() =>
  (props.msg.parallelSubtasks || []).filter(t => !t.dependsOn?.length)
)
const dependentTasks = computed(() =>
  (props.msg.parallelSubtasks || []).filter(t => t.dependsOn?.length)
)
const hasDependentTasks = computed(() => dependentTasks.value.length > 0)

const allTasksDone = computed(() => {
  const subs = props.msg.parallelSubtasks
  if (!subs?.length) return false
  return subs.every(t => t.status === 'done' || t.status === 'failed' || t.status === 'cancelled')
})

const synthesisStatus = computed<'pending' | 'running' | 'done'>(() => {
  if (props.msg.content?.trim()) return 'done'
  if (props.msg.isSynthesizing || props.msg.isDiagnosing) return 'running'
  return 'pending'
})

const overallProgress = computed(() => {
  const subs = props.msg.parallelSubtasks
  if (!subs?.length) return 0
  const done = subs.filter(t => t.status === 'done' || t.status === 'failed' || t.status === 'cancelled').length
  // 子任务占 70%，合成占 30%
  const taskProgress = (done / subs.length) * 70
  const synthProgress = synthesisStatus.value === 'done' ? 30 : synthesisStatus.value === 'running' ? 15 : 0
  return Math.round(taskProgress + synthProgress)
})

const isResearchComplete = computed(() =>
  allTasksDone.value && synthesisStatus.value === 'done'
)

// 质量验证区
const qualityExpanded = ref(false)

const hasQualityData = computed(() =>
  !!props.msg.diagnosticReport || !!props.msg.peerReview
)

const qualitySummaryText = computed(() => {
  if (!hasQualityData.value) return ''
  const parts: string[] = []
  if (props.msg.diagnosticReport) {
    const dr = props.msg.diagnosticReport
    parts.push(dr.fixed ? '已验证结论一致性' : '存在待确认问题')
  }
  if (props.msg.peerReview) {
    parts.push(`评审 ${props.msg.peerReview.overall_score}/100`)
  }
  return parts.join(' · ')
})

const reviewDimensionLabels: Record<string, string> = {
  argument: '论证', logic: '逻辑', citation: '引用',
  balance: '平衡', originality: '原创',
}

function severityClass(severity: string): string {
  if (severity === 'critical' || severity === 'error') return 'severity-critical'
  if (severity === 'important' || severity === 'warning') return 'severity-important'
  return 'severity-minor'
}

function formatScoreHistory(scores: number[]): string {
  return scores.map(s => Math.round(s)).join('→')
}

// ── 工具函数 ──

function getStepKeywords(step: ThinkingStep): string {
  if (!step.input) return ''
  try {
    const args = typeof step.input === 'string' ? JSON.parse(step.input) : step.input
    return args.query || args.url || ''
  } catch { return '' }
}

function formatPathDuration(seconds: number): string {
  if (seconds >= 60) return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`
  return `${seconds.toFixed(0)}s`
}

function renderBrief(content: string): string {
  return renderCitationMarkdown(content, props.msg.references || [], 'BRIEF')
}

function escapeAttr(val: any): string {
  if (!val) return ''
  return String(val).replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

// 子任务内容渲染
const subtaskMd = new MarkdownIt({ html: true, linkify: true, typographer: true, breaks: true })
const _subtaskDefaultLinkOpen = subtaskMd.renderer.rules.link_open || function(tokens: any, idx: any, options: any, _env: any, self: any) {
  return self.renderToken(tokens, idx, options)
}
subtaskMd.renderer.rules.link_open = function(tokens: any, idx: any, options: any, env: any, self: any) {
  const token = tokens[idx]
  const ti = token.attrIndex('target')
  const ri = token.attrIndex('rel')
  if (ti < 0) token.attrPush(['target', '_blank']); else token.attrs![ti][1] = '_blank'
  if (ri < 0) token.attrPush(['rel', 'noopener noreferrer']); else token.attrs![ri][1] = 'noopener noreferrer'
  return _subtaskDefaultLinkOpen(tokens, idx, options, env, self)
}

function hasSubtaskContent(t: any): boolean {
  return !!(t.finalContent || t.content || t.researchContent)
}

function getDisplayContent(t: any): string {
  return t.finalContent || t.content || ''
}

function renderSubtaskContent(content: string, subtaskRefs?: any[]): string {
  let cleaned = content
    .replace(/<tool_call>[\s\S]*?<\/tool_call>/g, '')
    .replace(/<tool_call>\s*\{[\s\S]*?\}\s*/g, '')
    .replace(/<\/?tool_call>/g, '')
    .replace(/```(?:json|xml)?\s*\{\s*"(?:name|tool)"[\s\S]*?\}\s*```/g, '')
    .trim()
  if (!cleaned) return ''

  cleaned = cleaned.replace(/([^\n])(#{1,6}\s)/g, '$1\n\n$2')
  cleaned = cleaned.replace(/([^\n])\n(#{1,6}\s)/g, '$1\n\n$2')

  const refs = subtaskRefs || props.msg.references || []
  const citations: Map<string, string> = new Map()
  let subtaskCiteIdx = 0
  const safe = cleaned.replace(/\[(?:ref:)?(ref_([\w]+))\]/g, (_, refId: string, num: string) => {
    subtaskCiteIdx++
    const displayNum = /^\d+$/.test(num) ? num : subtaskCiteIdx
    const ph = `SUBTASKCITE${subtaskCiteIdx}END`
    const ref = refs.find((r: any) => r.id === refId)
    if (ref) {
      citations.set(ph, `<span class="citation-mark" data-ref-id="${refId}" data-ref-title="${escapeAttr(ref.title)}" data-ref-authors="${escapeAttr(ref.authors?.slice(0, 3).join(', '))}" data-ref-year="${escapeAttr(ref.year)}" data-ref-source="${escapeAttr(ref.source || ref.data_source)}" data-ref-url="${escapeAttr(ref.url)}" data-ref-abstract="${escapeAttr(ref.abstract)}">[${displayNum}]</span>`)
    } else {
      citations.set(ph, `<span class="citation-mark" data-ref-id="${refId}">[${displayNum}]</span>`)
    }
    return ph
  })
  let html = subtaskMd.render(safe)
  citations.forEach((markup, ph) => {
    html = html.split(ph).join(markup)
  })
  return html
}

// 引用 tooltip
const { handleCitationMousemove } = useCitationTooltip()
</script>

<template>
  <!-- 折叠摘要行：write 开始后显示在报告上方 -->
  <div v-if="msg.content?.trim() && msg.stageOutputs?.length" class="pipeline-collapsed-summary">
    <span v-if="analyzeOutput" class="summary-chip" @click="toggleSummaryChip('s_analyze')">📋 调研计划 <i class="chip-arrow" :class="{ open: expandedCards['s_analyze'] }">›</i></span>
    <span v-if="researchToolSteps.length" class="summary-chip" @click="toggleSummaryChip('s_research')">🔍 {{ researchToolSteps.length }}条资料{{ isAggregationMode ? ` · ${pathGroupedToolSteps.length}路` : '' }} <i class="chip-arrow" :class="{ open: expandedCards['s_research'] }">›</i></span>
    <span v-if="gapDetectionOutput || targetedResearchContentHtml" class="summary-chip" @click="toggleSummaryChip('s_targeted')">🔄 补充搜索 <i class="chip-arrow" :class="{ open: expandedCards['s_targeted'] }">›</i></span>
    <span v-if="outlineOutput" class="summary-chip" @click="toggleSummaryChip('s_outline')">📝 大纲就绪 <i class="chip-arrow" :class="{ open: expandedCards['s_outline'] }">›</i></span>
    <!-- 自定义阶段 chip（非默认 deep-research 阶段） -->
    <span
      v-for="cs in customStageOutputs"
      :key="cs.stage"
      class="summary-chip"
      @click="toggleSummaryChip(`s_custom_${cs.stage}`)"
    >
      📄 {{ cs.title }} <i class="chip-arrow" :class="{ open: expandedCards[`s_custom_${cs.stage}`] }">›</i>
    </span>
  </div>
  <!-- 自定义阶段展开详情（紧跟 chips 行，不被正文挤走） -->
  <div
    v-for="cs in customStageOutputs"
    :key="`detail_${cs.stage}`"
    v-show="expandedCards[`s_custom_${cs.stage}`]"
    class="summary-detail"
    @mousemove="handleCitationMousemove"
  >
    <div v-html="renderBrief(cs.content)" />
  </div>
  <!-- 摘要展开详情 -->
  <div v-if="expandedCards['s_analyze'] && analyzeOutput" class="summary-detail" @mousemove="handleCitationMousemove">
    <div v-html="renderBrief(analyzeOutput)" />
    <div v-if="msg.analyzePlan" class="analyze-plan-structured">
      <div v-if="msg.analyzePlan.keyQuestions.length" class="plan-questions">
        <div class="plan-label">核心问题</div>
        <div v-for="(q, i) in msg.analyzePlan.keyQuestions" :key="i" class="plan-question-item">{{ q }}</div>
      </div>
      <table v-if="msg.analyzePlan.searchSteps.length" class="plan-search-table">
        <thead><tr><th>步骤</th><th>搜索方式</th><th>关键词</th><th>语言</th><th>目的</th></tr></thead>
        <tbody>
          <tr v-for="s in msg.analyzePlan.searchSteps" :key="s.stepId">
            <td>{{ s.stepId }}</td><td>{{ s.toolDisplay }}</td><td>{{ s.keywords }}</td><td>{{ s.language }}</td><td>{{ s.purpose }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
  <div v-if="expandedCards['s_research'] && researchToolSteps.length" class="summary-detail">
    <!-- 聚合模式：按路径分组展示（与实时执行视图一致的 timeline 格式） -->
    <template v-if="isAggregationMode && pathGroupedToolSteps.length">
      <div v-for="group in pathGroupedToolSteps" :key="group.index" class="agg-path-card summary-agg-path-card" :class="group.status">
        <div class="agg-path-card-header" @click="expandedCards[`s_path_${group.index}`] = !expandedCards[`s_path_${group.index}`]">
          <span class="agg-path-card-icon">
            <template v-if="group.status === 'completed'">✅</template>
            <template v-else-if="group.status === 'failed' || group.status === 'timeout'">❌</template>
            <template v-else>⏳</template>
          </span>
          <span class="agg-path-card-label">{{ group.label }}</span>
          <span class="agg-path-card-meta">
            <template v-if="group.refsCount">{{ group.refsCount }}篇引用</template>
            {{ group.steps.length }}次调用
          </span>
          <span v-if="group.durationS" class="agg-path-card-time">{{ formatPathDuration(group.durationS) }}</span>
          <i class="chip-arrow agg-path-arrow" :class="{ open: expandedCards[`s_path_${group.index}`] }">›</i>
        </div>
        <!-- 折叠时：running 路显示当前工具 -->
        <div v-if="!expandedCards[`s_path_${group.index}`] && group.status === 'running' && group.steps.length" class="agg-path-current">
          🔧 {{ group.steps[group.steps.length - 1].toolDisplayName || group.steps[group.steps.length - 1].toolName }}
        </div>
        <!-- 展开时：工具调用 timeline -->
        <div v-if="expandedCards[`s_path_${group.index}`] && group.steps.length" class="search-timeline agg-path-timeline">
          <div v-for="step in group.steps" :key="step.toolCallId" class="timeline-item">
            <span class="timeline-status">{{ step.status === 'success' ? '✓' : step.status === 'error' ? '✗' : '○' }}</span>
            <span class="timeline-tool">{{ step.toolDisplayName || step.toolName }}</span>
            <span v-if="getStepKeywords(step)" class="timeline-keywords">{{ getStepKeywords(step) }}</span>
            <span v-if="step.summary" class="timeline-summary">— {{ step.summary }}</span>
          </div>
        </div>
      </div>
    </template>
    <!-- 非聚合模式：timeline 格式（与实时执行视图一致） -->
    <template v-else>
      <div class="search-timeline">
        <div v-for="step in researchToolSteps" :key="step.toolCallId" class="timeline-item">
          <span class="timeline-status">{{ step.status === 'success' ? '✓' : step.status === 'error' ? '✗' : '○' }}</span>
          <span class="timeline-tool">{{ step.toolDisplayName || step.toolName }}</span>
          <span v-if="getStepKeywords(step)" class="timeline-keywords">{{ getStepKeywords(step) }}</span>
          <span v-if="step.summary" class="timeline-summary">— {{ step.summary }}</span>
        </div>
      </div>
    </template>
    <!-- 聚合合成结果（折叠摘要中也展示） -->
    <div v-if="isAggregationMode && msg.aggregationProgress?.phase === 'complete' && msg.synthesisContent" class="agg-synthesis-done summary-synthesis-done">
      <div class="agg-synthesis-done-header" @click="expandedCards['s_agg_synthesis'] = !expandedCards['s_agg_synthesis']">
        <span class="agg-synthesis-done-icon">🧠</span>
        <span class="agg-synthesis-done-text">
          合成结果 · {{ msg.synthesisContent.length }}字
          <template v-if="totalAggRefs"> · {{ totalAggRefs }}篇引用</template>
        </span>
        <i class="chip-arrow" :class="{ open: expandedCards['s_agg_synthesis'] }">›</i>
      </div>
      <div v-if="expandedCards['s_agg_synthesis']" class="agg-synthesis-detail" @mousemove="handleCitationMousemove" v-html="renderBrief(msg.synthesisContent)" />
    </div>
  </div>
  <div v-if="expandedCards['s_targeted'] && (gapDetectionOutput || targetedResearchContentHtml)" class="summary-detail">
    <div v-if="gapDetectionOutput" class="research-analysis-content" @mousemove="handleCitationMousemove" v-html="renderBrief(gapDetectionOutput)" />
    <div v-if="targetedResearchContentHtml" class="research-analysis-content" @mousemove="handleCitationMousemove" v-html="targetedResearchContentHtml" />
  </div>
  <div v-if="expandedCards['s_outline'] && outlineOutput" class="summary-detail" @mousemove="handleCitationMousemove">
    <div v-html="outlineHtml" />
  </div>
  <!-- 并行编排：子任务实时状态 + 依赖可视化 + 合成 + 质量 -->
  <div v-if="msg.parallelSubtasks?.length" class="parallel-panel">
    <!-- 面板 header -->
    <div class="parallel-header" @click="parallelPanelCollapsed = !parallelPanelCollapsed">
      <span class="parallel-header-icon">{{ isResearchComplete ? '✅' : '🔬' }}</span>
      <span class="parallel-header-title">{{ isResearchComplete ? '研究完成' : '深度研究进展' }}</span>
      <span class="parallel-header-count">
        <template v-if="parallelPanelCollapsed && msg.parallelProgress">
          · {{ msg.parallelProgress.completed.length + msg.parallelProgress.failed.length }}/{{ msg.parallelProgress.total }} 完成
          <template v-if="synthesisStatus === 'running'"> · 整合中...</template>
        </template>
        <template v-else>
          {{ (msg.parallelSubtasks || []).filter(t => t.status === 'done' || t.status === 'failed' || t.status === 'cancelled').length }}/{{ (msg.parallelSubtasks || []).length }}
        </template>
      </span>
      <i class="chip-arrow parallel-collapse-arrow" :class="{ open: !parallelPanelCollapsed }">›</i>
    </div>
    <!-- 微型进度条 -->
    <div v-if="!isResearchComplete" class="progress-bar-mini">
      <div class="progress-bar-fill" :style="{ width: overallProgress + '%' }"></div>
    </div>

    <template v-if="!parallelPanelCollapsed">
      <!-- 独立子任务列表 -->
      <div class="parallel-tasks">
        <div v-for="t in independentTasks" :key="t.id" class="parallel-task-wrapper">
          <div class="parallel-task-item"
               :class="t.status || 'pending'"
               :title="t.error || ''"
               @click="hasSubtaskContent(t) && (t.expanded = !t.expanded)">
            <span class="parallel-task-status" :class="{ spinning: t.status === 'running' }">
              {{ taskStatusIcon(t) }}
            </span>
            <span class="parallel-task-title">
              {{ t.title }}
              <span v-if="t.status === 'running' && t.toolName" class="parallel-tool-hint">· {{ t.toolName }}</span>
              <span v-else-if="t.status === 'running' && t.stageName" class="parallel-stage-hint">· {{ t.stageName }}</span>
              <span v-if="t.retryReason" class="parallel-retry-badge" title="内容未聚焦，已重新生成">⟳ 已重试</span>
            </span>
            <span v-if="t.status === 'running' && t.startTime" class="parallel-task-timer">{{ formatElapsed(t.startTime) }}</span>
            <span v-if="hasSubtaskContent(t)" class="parallel-expand-arrow" :class="{ open: t.expanded }">›</span>
          </div>
          <div v-if="t.expanded && hasSubtaskContent(t)" class="parallel-subtask-content" :class="{ 'is-retrying': t.isRetrying }" @mousemove="handleCitationMousemove">
            <!-- 最终报告 -->
            <div v-if="getDisplayContent(t)" class="subtask-content-inner" v-html="renderSubtaskContent(getDisplayContent(t), t.references)"></div>
            <!-- 研究过程（done 后默认折叠） -->
            <template v-if="t.researchContent">
              <div v-if="t.status === 'done' && getDisplayContent(t)" class="subtask-research-toggle" @click.stop="t.researchExpanded = !t.researchExpanded">
                {{ t.researchExpanded ? '▾' : '▸' }} 研究过程
              </div>
              <div v-if="(t.status === 'done' && t.researchExpanded) || (t.status === 'running' && !getDisplayContent(t))"
                   class="subtask-content-inner research-content" v-html="renderSubtaskContent(t.researchContent, t.references)"></div>
            </template>
          </div>
          <div v-else-if="t.expanded && t.isRetrying && !hasSubtaskContent(t)" class="parallel-subtask-retrying-placeholder">
            <span class="retrying-spinner">&#10227;</span> 重新生成中，请稍候...
          </div>
        </div>
      </div>

      <!-- 依赖分隔线 -->
      <div v-if="hasDependentTasks" class="dep-separator">
        <span class="dep-separator-text">等待以上完成后开始</span>
      </div>

      <!-- 依赖子任务列表 -->
      <div v-if="hasDependentTasks" class="parallel-tasks">
        <div v-for="t in dependentTasks" :key="t.id" class="parallel-task-wrapper">
          <div class="parallel-task-item"
               :class="t.status || 'pending'"
               :title="t.error || ''"
               @click="hasSubtaskContent(t) && (t.expanded = !t.expanded)">
            <span class="parallel-task-status" :class="{ spinning: t.status === 'running' }">
              {{ taskStatusIcon(t) }}
            </span>
            <span class="parallel-task-title">
              {{ t.title }}
              <span v-if="t.status === 'running' && t.toolName" class="parallel-tool-hint">· {{ t.toolName }}</span>
              <span v-else-if="t.status === 'running' && t.stageName" class="parallel-stage-hint">· {{ t.stageName }}</span>
              <span v-if="t.retryReason" class="parallel-retry-badge">⟳ 已重试</span>
            </span>
            <span v-if="t.status === 'running' && t.startTime" class="parallel-task-timer">{{ formatElapsed(t.startTime) }}</span>
            <span v-if="hasSubtaskContent(t)" class="parallel-expand-arrow" :class="{ open: t.expanded }">›</span>
          </div>
          <div v-if="t.expanded && hasSubtaskContent(t)" class="parallel-subtask-content" :class="{ 'is-retrying': t.isRetrying }" @mousemove="handleCitationMousemove">
            <div v-if="getDisplayContent(t)" class="subtask-content-inner" v-html="renderSubtaskContent(getDisplayContent(t), t.references)"></div>
            <template v-if="t.researchContent">
              <div v-if="t.status === 'done' && getDisplayContent(t)" class="subtask-research-toggle" @click.stop="t.researchExpanded = !t.researchExpanded">
                {{ t.researchExpanded ? '▾' : '▸' }} 研究过程
              </div>
              <div v-if="(t.status === 'done' && t.researchExpanded) || (t.status === 'running' && !getDisplayContent(t))"
                   class="subtask-content-inner research-content" v-html="renderSubtaskContent(t.researchContent, t.references)"></div>
            </template>
          </div>
          <div v-else-if="t.expanded && t.isRetrying && !hasSubtaskContent(t)" class="parallel-subtask-retrying-placeholder">
            <span class="retrying-spinner">&#10227;</span> 重新生成中，请稍候...
          </div>
        </div>
      </div>

      <!-- 合成分隔线 + 合成状态 -->
      <div class="synth-separator"></div>
      <div class="synth-status" :class="synthesisStatus">
        <span v-if="synthesisStatus === 'done'" class="synth-icon done">✓</span>
        <span v-else-if="synthesisStatus === 'running'" class="synth-icon running">⟳</span>
        <span v-else class="synth-icon pending">○</span>
        <span class="synth-label">
          <template v-if="synthesisStatus === 'done'">
            整合完成
            <template v-if="hasQualityData"> · {{ qualitySummaryText }}</template>
          </template>
          <template v-else-if="msg.isDiagnosing">{{ msg.diagnosticPhase || '正在优化报告质量...' }}</template>
          <template v-else-if="synthesisStatus === 'running'">{{ synthesizingText }}</template>
          <template v-else>整合研究结论<span class="synth-waiting">等待中</span></template>
        </span>
      </div>

      <!-- 质量验证详情（Accordion） -->
      <div v-if="hasQualityData && synthesisStatus === 'done'" class="quality-accordion" @click="qualityExpanded = !qualityExpanded">
        <div class="quality-accordion-header">
          <i class="chip-arrow" :class="{ open: qualityExpanded }">›</i>
          <template v-if="msg.diagnosticReport">
            🔍 诊断 {{ msg.diagnosticReport.rounds }}轮
            <template v-if="msg.diagnosticReport.score_history?.length"> · {{ formatScoreHistory(msg.diagnosticReport.score_history) }}</template>
          </template>
          <template v-if="msg.peerReview">
            <template v-if="msg.diagnosticReport"> · </template>
            👥 评审 {{ msg.peerReview.overall_score }}分
          </template>
        </div>
      </div>
      <div v-if="qualityExpanded && hasQualityData" class="quality-detail">
        <!-- 诊断报告 -->
        <div v-if="msg.diagnosticReport" class="quality-section">
          <div class="quality-section-title">
            🔍 一致性诊断
            <span :class="msg.diagnosticReport.fixed ? 'quality-badge-pass' : 'quality-badge-fail'">
              {{ msg.diagnosticReport.fixed ? '已修复' : '需关注' }}
            </span>
          </div>
          <table v-if="msg.diagnosticReport.evidence_log?.length" class="quality-table">
            <thead><tr><th>轮次</th><th>类别</th><th>问题数</th><th>严重度</th></tr></thead>
            <tbody>
              <tr v-for="ev in msg.diagnosticReport.evidence_log" :key="ev.round">
                <td>{{ ev.round }}</td>
                <td>{{ ev.category }}</td>
                <td>{{ ev.issue_count }}</td>
                <td><span :class="severityClass(ev.severity)">{{ ev.severity }}</span></td>
              </tr>
            </tbody>
          </table>
        </div>
        <!-- 同行评审 -->
        <div v-if="msg.peerReview" class="quality-section">
          <div class="quality-section-title">
            👥 同行评审 · 总分 {{ msg.peerReview.overall_score }}/100
            <span :class="msg.peerReview.has_critical ? 'quality-badge-fail' : 'quality-badge-pass'">
              {{ msg.peerReview.has_critical ? '有关键问题' : '通过' }}
            </span>
          </div>
          <table v-if="msg.peerReview.items?.length" class="quality-table">
            <thead><tr><th>维度</th><th>评分</th><th>严重度</th><th>意见</th></tr></thead>
            <tbody>
              <tr v-for="(item, idx) in msg.peerReview.items" :key="idx">
                <td>{{ reviewDimensionLabels[item.dimension] || item.dimension }}</td>
                <td>{{ item.score }}</td>
                <td><span :class="severityClass(item.severity)">{{ item.severity }}</span></td>
                <td class="quality-comment-cell">{{ item.comment }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- 底部进度摘要 -->
      <div v-if="msg.parallelProgress && !isResearchComplete" class="parallel-summary">
        {{ msg.parallelProgress.completed.length + msg.parallelProgress.failed.length }}/{{ msg.parallelProgress.total }} 完成
        <span v-if="msg.parallelProgress.failed.length" class="parallel-partial-hint">
          （{{ msg.parallelProgress.failed.length }} 项数据不全）
        </span>
      </div>
    </template>
  </div>
</template>


<style>
.summary-chip {
  cursor: pointer;
  padding: 2px 8px;
  border-radius: 4px;
  transition: background 0.15s;
  user-select: none;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.summary-chip:hover {
  background: #dbeafe;
  color: #2563eb;
}

.summary-detail {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 12px 14px;
  margin-bottom: 8px;
  font-size: 13px;
  max-height: 400px;
  overflow-y: auto;
}
.summary-detail :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 8px 0;
}
.summary-detail :deep(th),
.summary-detail :deep(td) {
  border: 1px solid #d1d5db;
  padding: 6px 10px;
  text-align: left;
  font-size: 12px;
}
.summary-detail :deep(th) {
  background: #f3f4f6;
  font-weight: 600;
}
.summary-detail :deep(tr:nth-child(even)) {
  background: #f9fafb;
}

/* 折叠摘要中的路径卡片紧凑样式 */
.summary-detail .summary-agg-path-card {
  margin-bottom: 8px;
  padding: 8px 10px;
}
.summary-detail .summary-agg-path-card:last-child {
  margin-bottom: 0;
}
.summary-detail .agg-path-timeline {
  padding-left: 4px;
}
.summary-detail .summary-synthesis-done {
  margin-top: 8px;
}

.summary-agg-path {
  margin-bottom: 10px;
  padding: 8px;
  background: #f9fafb;
  border-radius: 6px;
  border: 1px solid #e5e7eb;
}
.summary-agg-path-header {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 500;
  margin-bottom: 6px;
  padding-bottom: 4px;
  border-bottom: 1px solid #f3f4f6;
}
.summary-agg-path-icon { font-size: 13px; flex-shrink: 0; }
.summary-agg-path-label { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.summary-agg-path-refs { font-size: 11px; color: #6b7280; flex-shrink: 0; }
.summary-agg-path-time { font-size: 11px; color: #9ca3af; flex-shrink: 0; }

/* 结构化流水线等待提示 */
.parallel-panel {
  margin-top: 12px;
  padding: 10px 14px;
  background: #f0f7ff;
  border-radius: 8px;
  border: 1px solid #d0e3ff;
  font-size: 13px;
}
.parallel-header {
  font-weight: 600;
  margin-bottom: 8px;
  color: #1a56db;
  cursor: pointer;
  display: flex;
  align-items: center;
  user-select: none;
}
.parallel-header-summary {
  font-weight: 400;
  font-size: 12px;
  color: #6b7280;
  margin-left: 2px;
}
.parallel-collapse-arrow {
  margin-left: auto;
  font-size: 14px;
}
.parallel-task-wrapper {
  margin-bottom: 2px;
}
.parallel-task-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px;
  color: #6b7280;
  border-radius: 4px;
  transition: background 0.15s;
}
.parallel-task-item:hover {
  background: rgba(0,0,0,0.04);
}
.parallel-task-item.running { color: #1890ff; }
.parallel-task-item.done { color: #059669; }
.parallel-task-item.failed { color: #dc2626; cursor: help; }
.parallel-task-item.cancelled { color: #9ca3af; }
.parallel-task-status { width: 16px; text-align: center; flex-shrink: 0; }
.parallel-task-item.running .parallel-task-status {
  display: inline-block;
  animation: parallel-spin 1.2s linear infinite;
}
@keyframes parallel-spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
@media (prefers-reduced-motion: reduce) {
  .parallel-task-item.running .parallel-task-status {
    animation: none;
    opacity: 0.7;
  }
  .synthesizing-spinner { animation: none; }
}
.parallel-task-title { flex: 1; min-width: 0; }
.parallel-stage-hint, .parallel-tool-hint {
  color: #8c8c8c;
  font-size: 12px;
}
.parallel-tool-hint { color: #722ed1; }
.parallel-retry-badge {
  font-size: 11px;
  color: #d48806;
  background: #fffbe6;
  border: 1px solid #ffe58f;
  border-radius: 3px;
  padding: 0 4px;
  margin-left: 6px;
}
.parallel-task-timer {
  margin-left: auto;
  color: #8c8c8c;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}
.parallel-expand-arrow {
  margin-left: 4px;
  transition: transform 0.2s;
  color: #8c8c8c;
  font-size: 14px;
  flex-shrink: 0;
}
.parallel-expand-arrow.open { transform: rotate(90deg); }
.parallel-subtask-content.is-retrying {
  opacity: 0.35;
  pointer-events: none;
  position: relative;
}
.parallel-subtask-content.is-retrying::after {
  content: '⟳ 内容聚焦优化中...';
  position: absolute;
  top: 8px;
  left: 50%;
  transform: translateX(-50%);
  background: rgba(59, 130, 246, 0.9);
  color: #fff;
  padding: 4px 12px;
  border-radius: 4px;
  font-size: 12px;
  z-index: 2;
}
.subtask-research-toggle {
  margin-top: 8px;
  padding: 6px 12px;
  font-size: 12px;
  color: #666;
  cursor: pointer;
  border-top: 1px solid #eee;
  user-select: none;
}
.subtask-research-toggle:hover {
  color: #333;
  background: #f8f9fa;
}
.parallel-subtask-content .research-content {
  background: #f8f9fa;
  border-radius: 6px;
  padding: 12px;
  margin-top: 4px;
  font-size: 0.9em;
  color: #555;
  border-left: 3px solid #e0e0e0;
}
.parallel-subtask-retrying-placeholder {
  margin-left: 24px;
  padding: 16px;
  text-align: center;
  color: #999;
  font-size: 13px;
}
.parallel-partial-hint {
  color: #e6a23c;
  font-size: 12px;
}
.parallel-subtask-content {
  margin-left: 24px;
  padding: 8px 12px;
  border-left: 2px solid #d0e3ff;
  overflow-y: auto;
  overflow-x: hidden;
  font-size: 13px;
  color: #595959;
  background: #fafafa;
  border-radius: 0 4px 4px 0;
  min-width: 0;
}
/* done 后限高，流式过程中不限制 */
.parallel-task-item.done + .parallel-subtask-content {
  max-height: 400px;
}
.parallel-subtask-content .subtask-content-inner {
  word-break: break-word;
  overflow-wrap: break-word;
  line-height: 1.6;
  min-width: 0;
}
.parallel-subtask-content .subtask-content-inner h1,
.parallel-subtask-content .subtask-content-inner h2,
.parallel-subtask-content .subtask-content-inner h3,
.parallel-subtask-content .subtask-content-inner h4 {
  margin: 8px 0 4px;
  font-weight: 600;
  color: #262626;
}
.parallel-subtask-content .subtask-content-inner h1 { font-size: 16px; }
.parallel-subtask-content .subtask-content-inner h2 { font-size: 15px; }
.parallel-subtask-content .subtask-content-inner h3 { font-size: 14px; }
.parallel-subtask-content .subtask-content-inner p {
  margin: 4px 0;
}
.parallel-subtask-content .subtask-content-inner ul,
.parallel-subtask-content .subtask-content-inner ol {
  margin: 4px 0;
  padding-left: 20px;
}
.parallel-subtask-content .subtask-content-inner li {
  margin: 2px 0;
}
.parallel-subtask-content .subtask-content-inner pre {
  background: #f0f0f0;
  border-radius: 4px;
  padding: 8px;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  margin: 4px 0;
}
.parallel-subtask-content .subtask-content-inner code {
  background: #f0f0f0;
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 12px;
}
.parallel-subtask-content .subtask-content-inner pre code {
  background: none;
  padding: 0;
}
.parallel-subtask-content .subtask-content-inner blockquote {
  border-left: 3px solid #d9d9d9;
  margin: 4px 0;
  padding: 2px 8px;
  color: #8c8c8c;
}
.parallel-subtask-content .subtask-content-inner table {
  border-collapse: collapse;
  margin: 4px 0;
  font-size: 12px;
  width: 100%;
}
.parallel-subtask-content .subtask-content-inner th,
.parallel-subtask-content .subtask-content-inner td {
  border: 1px solid #e8e8e8;
  padding: 4px 8px;
}
.parallel-subtask-content .subtask-content-inner th {
  background: #f5f5f5;
  font-weight: 600;
}
.parallel-subtask-content .subtask-content-inner a {
  color: #1890ff;
  text-decoration: none;
}
.parallel-subtask-content .subtask-content-inner a:hover {
  text-decoration: underline;
}
.parallel-synthesizing {
  padding: 8px 0 4px;
  color: #1890ff;
  font-size: 13px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.parallel-synthesizing.diagnosing {
  color: #d97706;
}
.synthesizing-spinner {
  display: inline-block;
  animation: parallel-spin 1.2s linear infinite;
}
.parallel-summary {
  margin-top: 6px;
  font-size: 12px;
  color: #6b7280;
}
.parallel-failed { color: #dc2626; }

/* 质量验证面板 */
/* ── 微型进度条 ── */
.progress-bar-mini {
  height: 3px;
  background: #e5e7eb;
  border-radius: 2px;
  margin: 4px 0 8px;
  overflow: hidden;
}
.progress-bar-fill {
  height: 100%;
  background: linear-gradient(90deg, #3b82f6, #10b981);
  border-radius: 2px;
  transition: width 0.5s ease;
}

/* ── 面板 header 改进 ── */
.parallel-header-icon { margin-right: 4px; }
.parallel-header-title { font-weight: 600; }
.parallel-header-count { color: #6b7280; font-size: 12px; margin-left: 4px; }

/* ── 状态图标动效 ── */
.parallel-task-status.spinning {
  display: inline-block;
  animation: parallel-spin 1.2s linear infinite;
}
.parallel-task-item.done .parallel-task-status { color: #10b981; }
.parallel-task-item.running .parallel-task-status { color: #3b82f6; }
.parallel-task-item.pending .parallel-task-status { color: #d1d5db; }
.parallel-task-item.failed .parallel-task-status { color: #f59e0b; }

/* ── 依赖分隔线 ── */
.dep-separator {
  display: flex;
  align-items: center;
  margin: 8px 0;
  gap: 8px;
}
.dep-separator::before,
.dep-separator::after {
  content: '';
  flex: 1;
  border-top: 2px dashed #d1d5db;
}
.dep-separator-text {
  font-size: 11px;
  color: #9ca3af;
  white-space: nowrap;
}

/* ── 合成分隔线 + 状态 ── */
.synth-separator {
  border-top: 1px solid #e5e7eb;
  margin: 10px 0 6px;
}
.synth-status {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: #6b7280;
  padding: 4px 0;
}
.synth-icon { font-size: 14px; flex-shrink: 0; }
.synth-icon.done { color: #10b981; }
.synth-icon.running { color: #3b82f6; animation: parallel-spin 1.2s linear infinite; display: inline-block; }
.synth-icon.pending { color: #d1d5db; }
.synth-waiting { color: #9ca3af; font-size: 12px; margin-left: 6px; }
.synth-status.done .synth-label { color: #374151; }

/* ── 质量验证 Accordion ── */
.quality-accordion {
  cursor: pointer;
  padding: 6px 8px;
  margin: 4px 0;
  border-radius: 6px;
  font-size: 12px;
  color: #6b7280;
  transition: background 0.15s;
}
.quality-accordion:hover { background: #f3f4f6; }
.quality-accordion-header {
  display: flex;
  align-items: center;
  gap: 4px;
}
.quality-detail {
  padding: 8px;
  background: #f9fafb;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  margin: 4px 0;
}
.quality-section { margin-bottom: 10px; }
.quality-section:last-child { margin-bottom: 0; }
.quality-section-title {
  font-size: 13px;
  font-weight: 500;
  color: #374151;
  margin-bottom: 6px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.quality-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.quality-table th,
.quality-table td {
  border: 1px solid #e5e7eb;
  padding: 4px 8px;
  text-align: left;
}
.quality-table th {
  background: #f3f4f6;
  font-weight: 600;
  font-size: 11px;
  color: #6b7280;
}
.quality-comment-cell {
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.quality-badge-pass {
  font-size: 11px;
  color: #059669;
  background: #ecfdf5;
  border: 1px solid #a7f3d0;
  border-radius: 3px;
  padding: 0 5px;
}
.quality-badge-fail {
  font-size: 11px;
  color: #dc2626;
  background: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: 3px;
  padding: 0 5px;
}
.severity-critical { color: #dc2626; font-weight: 500; }
.severity-important { color: #d97706; }
.severity-minor { color: #6b7280; }

/* ── 移动端适配 ── */
@media (max-width: 640px) {
  .dep-separator-text { font-size: 10px; }
  .quality-table { font-size: 11px; }
  .quality-table th, .quality-table td { padding: 3px 5px; }
  .quality-comment-cell { max-width: 100px; }
  .quality-detail { overflow-x: auto; }
}
</style>
