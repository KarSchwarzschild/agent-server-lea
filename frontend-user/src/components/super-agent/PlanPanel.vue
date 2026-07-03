<script setup lang="ts">
/**
 * 计划审批面板（三态模式 + 步骤可编辑）
 *
 * 三种展示形态：
 * - expanded（submitted）：完整展示，标题+摘要+可编辑步骤+审批按钮+蓝色脉冲边框
 * - compact（approved/executing）：紧凑态，一行标题+进度+步骤图标
 * - collapsed（completed/rejected）：折叠态，单行可展开
 */
import { computed, ref, watch, onUnmounted } from 'vue'
import { NButton } from 'naive-ui'
import MarkdownIt from 'markdown-it'
import type { ExecutionPlanState, PlanStepState } from '../../types/super-agent'
import { sanitizeLightHtml } from '../../utils/sanitize'

// 轻量 Markdown 渲染（仅用于 plan summary）
const md = new MarkdownIt({ html: false, linkify: true, breaks: true })
function renderMd(text: string): string {
  if (!text) return ''
  return sanitizeLightHtml(md.render(text))
}

interface EditableStep {
  title: string
  description: string
  dependsOn: string[]
}

const props = defineProps<{
  plan: ExecutionPlanState
}>()

const emit = defineEmits<{
  approve: [planId: string, steps?: Array<{ title: string; description: string }>]
  reject: [planId: string]
}>()

const displayMode = computed(() => {
  if (props.plan.status === 'submitted') return 'expanded'
  if (['approved', 'executing'].includes(props.plan.status)) return 'compact'
  return 'collapsed' // completed / rejected
})

// 紧凑态/折叠态展开查看详情
const compactExpanded = ref(false)
const collapsedExpanded = ref(false)

// ==================== 可编辑步骤状态 ====================
const editableSteps = ref<EditableStep[]>([])
const isEdited = ref(false)
const expandedStepIndex = ref<number | null>(null)
const isApproving = ref(false)

// 初始化 editableSteps
function initEditableSteps() {
  editableSteps.value = props.plan.steps.map(s => ({
    title: s.title,
    description: s.description,
    dependsOn: s.dependsOn || [],
  }))
  isEdited.value = false
  expandedStepIndex.value = null
  isApproving.value = false
}

// watch plan.status → submitted 时初始化
watch(() => props.plan.status, (newVal) => {
  if (newVal === 'submitted') {
    initEditableSteps()
  }
}, { immediate: true })

// 也 watch steps 变化（首次 plan_created 时 steps 可能还没到）
watch(() => props.plan.steps, () => {
  if (props.plan.status === 'submitted' && !isEdited.value) {
    initEditableSteps()
  }
}, { deep: true })

// 操作函数
function updateStepTitle(index: number, title: string) {
  editableSteps.value[index].title = title
  isEdited.value = true
}

function updateStepDescription(index: number, description: string) {
  editableSteps.value[index].description = description
  isEdited.value = true
}

function toggleStepDescription(index: number) {
  expandedStepIndex.value = expandedStepIndex.value === index ? null : index
}

function addStep() {
  editableSteps.value.push({ title: '', description: '', dependsOn: [] })
  isEdited.value = true
  // 自动展开新步骤的描述
  expandedStepIndex.value = editableSteps.value.length - 1
}

function removeStep(index: number) {
  if (editableSteps.value.length <= 1) return
  editableSteps.value.splice(index, 1)
  isEdited.value = true
  if (expandedStepIndex.value === index) {
    expandedStepIndex.value = null
  } else if (expandedStepIndex.value !== null && expandedStepIndex.value > index) {
    expandedStepIndex.value--
  }
}

function moveStepUp(index: number) {
  if (index <= 0) return
  const steps = editableSteps.value
  ;[steps[index - 1], steps[index]] = [steps[index], steps[index - 1]]
  isEdited.value = true
  if (expandedStepIndex.value === index) expandedStepIndex.value = index - 1
  else if (expandedStepIndex.value === index - 1) expandedStepIndex.value = index
}

function moveStepDown(index: number) {
  if (index >= editableSteps.value.length - 1) return
  const steps = editableSteps.value
  ;[steps[index], steps[index + 1]] = [steps[index + 1], steps[index]]
  isEdited.value = true
  if (expandedStepIndex.value === index) expandedStepIndex.value = index + 1
  else if (expandedStepIndex.value === index + 1) expandedStepIndex.value = index
}

function resetEdits() {
  initEditableSteps()
}

// 验证：所有步骤标题非空
const isValid = computed(() =>
  editableSteps.value.length > 0 &&
  editableSteps.value.every(s => s.title.trim().length > 0)
)

// 批准
function handleApprove() {
  if (isApproving.value || !isValid.value) return
  isApproving.value = true
  emit(
    'approve',
    props.plan.planId,
    isEdited.value ? editableSteps.value.map(s => ({ title: s.title.trim(), description: s.description.trim(), depends_on: s.dependsOn })) : undefined
  )
  // 3秒后自动恢复（防止网络异常卡死）
  setTimeout(() => { isApproving.value = false }, 3000)
}

// ==================== 紧凑态/折叠态逻辑 ====================

// 批准后延迟折叠动画
const delayedCompact = ref(false)
watch(() => props.plan.status, (newVal, oldVal) => {
  if (oldVal === 'submitted' && newVal === 'approved') {
    delayedCompact.value = false
    setTimeout(() => { delayedCompact.value = true }, 1500)
  } else if (['approved', 'executing'].includes(newVal)) {
    delayedCompact.value = true
  }
}, { immediate: true })

const showCompact = computed(() =>
  displayMode.value === 'compact' && delayedCompact.value
)

const totalSteps = computed(() => props.plan.steps.length)

// 当前执行到第几步（running 的那一步，或者最后一个 completed + 1）
const currentStepNum = computed(() => {
  const runningIdx = props.plan.steps.findIndex(s => s.status === 'running')
  if (runningIdx >= 0) return runningIdx + 1
  const completedCount = props.plan.steps.filter(s => s.status === 'completed').length
  return Math.min(completedCount + 1, totalSteps.value)
})

function stepIcon(step: PlanStepState): string {
  if (step.status === 'running' && step.retryCount) return '⟳'
  switch (step.status) {
    case 'completed': return '✅'
    case 'running': return '●'
    case 'failed': return '❌'
    case 'skipped': return '⏭'
    default: return '○'
  }
}

// ==================== 步骤运行计时 + 延时提示 ====================
const stepRunningSeconds = ref(0)
let stepTimer: ReturnType<typeof setInterval> | null = null

const currentRunningStep = computed(() =>
  props.plan.steps.find(s => s.status === 'running')
)

watch(currentRunningStep, (newStep, oldStep) => {
  if (newStep && newStep !== oldStep) {
    stepRunningSeconds.value = 0
    if (stepTimer) clearInterval(stepTimer)
    stepTimer = setInterval(() => stepRunningSeconds.value++, 1000)
  } else if (!newStep) {
    if (stepTimer) clearInterval(stepTimer)
    stepTimer = null
  }
})

onUnmounted(() => { if (stepTimer) clearInterval(stepTimer) })

const stepHint = computed(() => {
  const step = currentRunningStep.value
  if (!step || step.status !== 'running') return ''
  if (step.retryCount && step.retryCount >= 1) return '深度分析中...'
  if (stepRunningSeconds.value > 15) return '处理时间较长，请耐心等待'
  return ''
})
</script>

<template>
  <div
    class="plan-panel"
    :class="{
      'plan-panel--expanded': displayMode === 'expanded',
      'plan-panel--compact': showCompact,
      'plan-panel--collapsed': displayMode === 'collapsed',
      'plan-panel--transitioning': displayMode === 'compact' && !delayedCompact,
    }"
  >
    <!-- 展开态：submitted，可编辑步骤 -->
    <template v-if="displayMode === 'expanded'">
      <div class="plan-header">
        <span class="plan-icon">📋</span>
        <span class="plan-title">{{ plan.title }}</span>
        <span class="plan-badge plan-badge--waiting">等待审批</span>
        <span v-if="isEdited" class="plan-badge plan-badge--edited">已修改</span>
      </div>

      <div v-if="plan.summary" class="plan-summary" v-html="renderMd(plan.summary)" />

      <div class="plan-steps-editable">
        <div
          v-for="(step, index) in editableSteps"
          :key="index"
          class="plan-step--editable"
        >
          <div class="step-row">
            <span class="step-number">{{ index + 1 }}</span>
            <input
              class="step-title-input"
              :value="step.title"
              placeholder="步骤标题"
              @input="updateStepTitle(index, ($event.target as HTMLInputElement).value)"
            />
            <div class="step-action-group">
              <button
                class="step-action-btn"
                :title="expandedStepIndex === index ? '收起描述' : '展开描述'"
                @click="toggleStepDescription(index)"
              >{{ expandedStepIndex === index ? '▾' : '▸' }}</button>
              <button
                class="step-action-btn"
                title="上移"
                :disabled="index === 0"
                @click="moveStepUp(index)"
              >↑</button>
              <button
                class="step-action-btn"
                title="下移"
                :disabled="index === editableSteps.length - 1"
                @click="moveStepDown(index)"
              >↓</button>
              <button
                class="step-action-btn step-action-btn--danger"
                title="删除"
                :disabled="editableSteps.length <= 1"
                @click="removeStep(index)"
              >×</button>
            </div>
          </div>
          <div v-if="expandedStepIndex === index" class="step-desc-wrap">
            <textarea
              class="step-desc-input"
              :value="step.description"
              placeholder="步骤描述（可选）"
              rows="2"
              @input="updateStepDescription(index, ($event.target as HTMLTextAreaElement).value)"
            />
          </div>
        </div>

        <button class="add-step-btn" @click="addStep">+ 添加步骤</button>
      </div>

      <div class="plan-actions">
        <NButton
          type="primary"
          size="medium"
          :disabled="!isValid || isApproving"
          :loading="isApproving"
          @click="handleApprove"
        >
          🚀 {{ isEdited ? '批准修改后的计划' : '批准执行' }}
        </NButton>
        <NButton
          v-if="isEdited"
          text
          type="default"
          size="small"
          class="reset-btn"
          @click="resetEdits"
        >
          重置修改
        </NButton>
        <NButton text type="default" size="small" class="reject-btn" @click="emit('reject', plan.planId)">
          拒绝
        </NButton>
      </div>
    </template>

    <!-- 过渡态：刚批准，保持展开 1.5s -->
    <template v-else-if="displayMode === 'compact' && !delayedCompact">
      <div class="plan-header">
        <span class="plan-icon">📋</span>
        <span class="plan-title">{{ plan.title }}</span>
        <span class="plan-badge plan-badge--executing">已批准</span>
      </div>
      <div v-if="plan.summary" class="plan-summary" v-html="renderMd(plan.summary)" />
      <div class="plan-steps">
        <div v-for="step in plan.steps" :key="step.id" class="plan-step">
          <span class="step-icon">{{ stepIcon(step) }}</span>
          <span class="step-title">{{ step.title }}</span>
        </div>
      </div>
    </template>

    <!-- 紧凑态：executing，点击可展开步骤列表 -->
    <template v-else-if="showCompact">
      <div class="compact-row" @click="compactExpanded = !compactExpanded">
        <span class="compact-toggle">{{ compactExpanded ? '▾' : '▸' }}</span>
        <span class="plan-icon">📋</span>
        <span class="compact-title">{{ plan.title }}（{{ totalSteps }} 阶段）</span>
        <span class="plan-badge plan-badge--executing">
          步骤 {{ currentStepNum }}/{{ totalSteps }}
        </span>
      </div>
      <div v-if="!compactExpanded" class="compact-steps">
        <span
          v-for="step in plan.steps" :key="step.id"
          class="compact-step-icon"
          :class="{ 'compact-step-icon--retrying': step.status === 'running' && step.retryCount }"
        >
          {{ stepIcon(step) }}
        </span>
      </div>
      <div v-if="!compactExpanded && stepHint" class="step-hint"
           :class="{ 'step-hint--retrying': currentRunningStep?.retryCount }">
        {{ stepHint }}
      </div>
      <div v-if="compactExpanded" class="compact-detail">
        <div class="plan-steps">
          <div v-for="step in plan.steps" :key="step.id" class="plan-step" :class="`plan-step--${step.status}`">
            <span class="step-icon">{{ stepIcon(step) }}</span>
            <div class="step-content">
              <span class="step-title">{{ step.title }}</span>
              <div v-if="step.description" class="step-desc-rendered" v-html="renderMd(step.description)" />
              <div v-if="step.dependsOn && step.dependsOn.length" class="step-deps">
                🔗 依赖: {{ step.dependsOn.map(d => {
                  const dep = plan.steps.find(s => s.id === d)
                  return dep ? dep.title : d
                }).join(', ') }}
              </div>
              <div v-if="step.status === 'skipped' && step.skipReason" class="step-skip-reason">
                跳过: {{ step.skipReason === 'dependency_failed' ? '依赖步骤失败' : step.skipReason }}
              </div>
            </div>
          </div>
        </div>
      </div>
    </template>

    <!-- 折叠态：completed / rejected -->
    <template v-else>
      <div
        class="collapsed-row"
        @click="collapsedExpanded = !collapsedExpanded"
      >
        <span class="collapsed-toggle">{{ collapsedExpanded ? '▾' : '▸' }}</span>
        <span class="plan-icon">📋</span>
        <span class="collapsed-title">{{ plan.title }}（{{ totalSteps }} 阶段）</span>
        <template v-if="plan.status === 'completed'">
          <span class="collapsed-sep">|</span>
          <span class="collapsed-info">{{ totalSteps }}步完成</span>
        </template>
        <span
          class="plan-badge"
          :class="plan.status === 'completed' ? 'plan-badge--done' : 'plan-badge--rejected'"
        >
          {{ plan.status === 'completed' ? '✅ 已完成' : '❌ 已拒绝' }}
        </span>
        <span class="collapsed-expand-hint">{{ collapsedExpanded ? '' : '[展开]' }}</span>
      </div>

      <!-- 折叠态展开详情 -->
      <div v-if="collapsedExpanded" class="collapsed-detail">
        <div v-if="plan.summary" class="plan-summary" v-html="renderMd(plan.summary)" />
        <div class="plan-steps">
          <div v-for="step in plan.steps" :key="step.id" class="plan-step">
            <span class="step-icon">{{ stepIcon(step) }}</span>
            <div class="step-content">
              <span class="step-title">{{ step.title }}</span>
              <div v-if="step.description" class="step-desc-rendered" v-html="renderMd(step.description)" />
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.plan-panel {
  margin: 8px 0;
  padding: 12px 16px;
  border: 1px solid #e8e8e8;
  border-radius: 8px;
  background: #fafafa;
  transition: min-height 0.3s ease, padding 0.3s ease, border-color 0.3s ease;
  overflow: hidden;
}

/* 展开态：蓝色脉冲边框 */
.plan-panel--expanded {
  border-color: #3b82f6;
  animation: pulse-border 2s ease-in-out infinite;
}

@keyframes pulse-border {
  0%, 100% { border-color: #3b82f6; box-shadow: 0 0 0 0 rgba(59, 130, 246, 0.15); }
  50% { border-color: #60a5fa; box-shadow: 0 0 0 4px rgba(59, 130, 246, 0.08); }
}

/* 紧凑态：sticky 浮动，streaming 时始终可见 */
.plan-panel--compact {
  padding: 8px 16px;
  position: sticky;
  top: 0;
  z-index: 5;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
}

/* 折叠态 */
.plan-panel--collapsed {
  padding: 8px 16px;
  cursor: default;
}

/* 过渡态（刚批准，保持展开） */
.plan-panel--transitioning {
  border-color: #10b981;
}

.plan-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.plan-icon { font-size: 16px; }
.plan-title {
  font-weight: 600;
  font-size: 14px;
  color: #333;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.plan-badge {
  font-size: 11px;
  padding: 1px 8px;
  border-radius: 10px;
  white-space: nowrap;
  flex-shrink: 0;
}
.plan-badge--waiting { background: #fff7e6; color: #d48806; }
.plan-badge--executing { background: #e6f7ff; color: #1890ff; }
.plan-badge--done { background: #f6ffed; color: #52c41a; }
.plan-badge--rejected { background: #fff1f0; color: #f5222d; }
.plan-badge--edited { background: #e6f7ff; color: #1890ff; }

.plan-summary {
  font-size: 13px;
  color: #666;
  margin-bottom: 10px;
  line-height: 1.6;
}
.plan-summary :deep(p) {
  margin: 0 0 6px;
}
.plan-summary :deep(p:last-child) {
  margin-bottom: 0;
}
.plan-summary :deep(ul),
.plan-summary :deep(ol) {
  margin: 4px 0;
  padding-left: 20px;
}
.plan-summary :deep(li) {
  margin: 2px 0;
}
.plan-summary :deep(strong) {
  color: #333;
  font-weight: 600;
}
.plan-summary :deep(code) {
  background: #f0f0f0;
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 12px;
}
.plan-summary :deep(a) {
  color: #1890ff;
  text-decoration: none;
}
.plan-summary :deep(a:hover) {
  text-decoration: underline;
}

.plan-steps {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.plan-step {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 4px 0;
  font-size: 13px;
}
.step-icon {
  width: 18px;
  text-align: center;
  flex-shrink: 0;
  line-height: 20px;
}
.step-title { color: #555; white-space: normal; word-break: break-word; }
.step-content {
  flex: 1;
  min-width: 0;
}
.step-desc-rendered {
  margin-top: 4px;
  font-size: 12px;
  color: #888;
  line-height: 1.5;
}
.step-desc-rendered :deep(p) {
  margin: 0 0 4px;
}
.step-desc-rendered :deep(p:last-child) {
  margin-bottom: 0;
}
.step-desc-rendered :deep(code) {
  background: #f0f0f0;
  padding: 1px 3px;
  border-radius: 2px;
  font-size: 11px;
}
.step-desc-rendered :deep(strong) {
  color: #666;
}

/* 审批按钮区域 */
.plan-actions {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid #e8e8e8;
  display: flex;
  align-items: center;
  gap: 16px;
}
.reject-btn {
  color: #999;
  font-size: 13px;
}
.reject-btn:hover {
  color: #f5222d;
}

/* 紧凑态 */
.compact-row {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  border-radius: 4px;
  transition: background 0.15s;
}
.compact-row:hover {
  background: #f0f0f0;
}
.compact-toggle {
  width: 16px;
  text-align: center;
  color: #999;
  font-size: 12px;
  flex-shrink: 0;
}
.compact-title {
  font-weight: 600;
  font-size: 13px;
  color: #333;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.compact-steps {
  display: flex;
  gap: 4px;
  margin-top: 4px;
  padding-left: 24px;
  font-size: 14px;
}
.compact-step-icon {
  width: 20px;
  text-align: center;
}
.compact-step-icon--retrying {
  animation: spin 1.5s linear infinite;
}
@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
@media (prefers-reduced-motion: reduce) {
  .compact-step-icon--retrying { animation: none; }
}
.step-hint {
  font-size: 12px;
  color: #9ca3af;
  margin-top: 4px;
  text-align: center;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
  padding-left: 24px;
}
.step-hint--retrying {
  color: #e6a23c;
}

.compact-detail {
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px dashed #e8e8e8;
}
.compact-detail .plan-step--running .step-title {
  color: #1890ff;
  font-weight: 500;
}
.compact-detail .plan-step--completed .step-title {
  color: #52c41a;
}
.compact-detail .plan-step--failed .step-title {
  color: #f5222d;
}
.compact-detail .plan-step--skipped .step-title {
  color: #999;
  font-style: italic;
  text-decoration: line-through;
}
.compact-detail .plan-step--pending .step-title {
  color: #999;
}

.step-deps {
  font-size: 12px;
  color: #8c8c8c;
  margin-top: 2px;
}

.step-skip-reason {
  font-size: 12px;
  color: #f5222d;
  margin-top: 2px;
}

/* 折叠态 */
.collapsed-row {
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  padding: 2px 0;
  border-radius: 4px;
  transition: background 0.15s;
}
.collapsed-row:hover {
  background: #f0f0f0;
}
.collapsed-toggle {
  width: 16px;
  text-align: center;
  color: #999;
  font-size: 12px;
  flex-shrink: 0;
}
.collapsed-title {
  font-weight: 500;
  font-size: 13px;
  color: #555;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.collapsed-sep {
  color: #d9d9d9;
  font-size: 12px;
}
.collapsed-info {
  font-size: 12px;
  color: #999;
  white-space: nowrap;
}
.collapsed-expand-hint {
  font-size: 11px;
  color: #bbb;
  margin-left: auto;
  flex-shrink: 0;
}

.collapsed-detail {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed #e8e8e8;
}

/* 可编辑步骤 */
.plan-steps-editable {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.plan-step--editable {
  background: #fff;
  border: 1px solid #e8e8e8;
  border-radius: 6px;
  padding: 6px 10px;
  transition: border-color 0.15s;
  overflow: hidden;
}
.plan-step--editable:hover {
  border-color: #d0d0d0;
}
.step-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.step-number {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: #f0f0f0;
  color: #666;
  font-size: 12px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.step-title-input {
  flex: 1;
  border: none;
  outline: none;
  font-size: 13px;
  color: #333;
  padding: 4px 0;
  background: transparent;
  border-bottom: 1px solid transparent;
  transition: border-color 0.15s;
}
.step-title-input:focus {
  border-bottom-color: #3b82f6;
}
.step-title-input::placeholder {
  color: #ccc;
}
.step-action-group {
  display: flex;
  gap: 2px;
  flex-shrink: 0;
}
.step-action-btn {
  width: 24px;
  height: 24px;
  border: none;
  background: transparent;
  border-radius: 4px;
  cursor: pointer;
  font-size: 13px;
  color: #999;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.15s, color 0.15s;
}
.step-action-btn:hover:not(:disabled) {
  background: #f0f0f0;
  color: #555;
}
.step-action-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}
.step-action-btn--danger:hover:not(:disabled) {
  background: #fff1f0;
  color: #f5222d;
}
.step-desc-wrap {
  margin-top: 6px;
  padding-left: 30px;
}
.step-desc-input {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid #e8e8e8;
  border-radius: 4px;
  padding: 6px 8px;
  font-size: 12px;
  color: #555;
  resize: vertical;
  outline: none;
  transition: border-color 0.15s;
  font-family: inherit;
}
.step-desc-input:focus {
  border-color: #3b82f6;
}
.step-desc-input::placeholder {
  color: #ccc;
}
.add-step-btn {
  margin-top: 4px;
  padding: 8px;
  border: 1px dashed #d9d9d9;
  border-radius: 6px;
  background: transparent;
  color: #999;
  font-size: 13px;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
  text-align: center;
}
.add-step-btn:hover {
  border-color: #3b82f6;
  color: #3b82f6;
}
.reset-btn {
  color: #999;
  font-size: 13px;
}
.reset-btn:hover {
  color: #1890ff;
}
</style>