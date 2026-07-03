<script setup lang="ts">
/**
 * 消息渲染组件 (v5 Sidebar Layout)
 *
 * 从 SuperAgent.vue 提取的单条消息渲染逻辑。
 * v5: 执行过程信息移至右侧侧栏，主内容区全高可见。
 *
 * 布局：
 * - 普通对话：单列 70% 宽度（无侧栏）
 * - 有执行过程（PC）：flex row = content(flex:1) + sidebar(300px)
 * - 移动端：ExecutionSidebar 内联在主内容区
 */
import { computed, ref, watch } from 'vue'
import { NTag, NIcon, NButton } from 'naive-ui'
import { BuildOutline, PeopleOutline, ChatbubbleOutline } from '@vicons/ionicons5'
import type {
  ChatMessage, ThinkingStep, ToolCall, Reference, ViewMode, GeneratedFileInfo, AttachmentInfo,
} from '../../types/super-agent'
import { getVisibleToolSteps } from '../../types/super-agent'
import type { Expert } from '../../api/superagent'
import type { SkillOption } from './RegenerateMenu.vue'
import { sanitizeCitationHtml } from '../../utils/sanitize'
import { useContentParser } from '../../composables/useContentParser'
// 子组件
import ExecutionProgress from '../ExecutionProgress.vue'
import StatusText from '../StatusText.vue'
import ExecutionSidebar from './ExecutionSidebar.vue'
import StreamingContent from './StreamingContent.vue'
import ContentRenderer from './ContentRenderer.vue'
import AnimationPlayer from './AnimationPlayer.vue'
import InteractivePlayer from './InteractivePlayer.vue'
import QuizBlock from './QuizBlock.vue'
import SlidesPresenter from './SlidesPresenter.vue'
import ModeSelectionCard from './ModeSelectionCard.vue'
import StandalonePluginIframe from './StandalonePluginIframe.vue'
import ToolCallCard from './ToolCallCard.vue'
import UserMessage from './UserMessage.vue'
import AiMessageActions from './AiMessageActions.vue'
import AiMessagePostContent from './AiMessagePostContent.vue'
import ResearchPipeline from './ResearchPipeline.vue'
import PipelineProgress from './PipelineProgress.vue'
import ClassroomPlayer from './ClassroomPlayer.vue'
import type { ClassroomData } from './ClassroomPlayer.vue'
import SlideRenderer from './SlideRenderer.vue'
import type { SlideData } from './SlideRenderer.vue'
// extractHtmlBlock, sanitizeLightHtml 已移至 useContentParser
import { useCitationTooltip } from '../../composables/useCitationTooltip'

const props = defineProps<{
  msg: ChatMessage
  viewMode: ViewMode
  currentSkill: string
  currentPhase: string
  isSkillExecuting: boolean
  skillExecutionTime: number
  latestEventType: string
  latestEventData: Record<string, unknown>
  connectionHealth: string
  expandedThinking: Set<string>
  isMobile?: boolean
  streamingExpertId?: string
  /** v5.0: 可用 Skill 列表（用于重新生成下拉） */
  availableSkills?: SkillOption[]
  /** v5.0: 是否有活跃的流式任务（全局级） */
  isGlobalStreaming?: boolean
  /** v5.0: 该消息之后还有多少条消息（编辑截断确认用） */
  messagesAfterCount?: number
  /** v5.0: 快捷回复建议（仅最后一条 assistant 消息显示） */
  quickReplies?: string[]
}>()

const emit = defineEmits<{
  (e: 'toggle-thinking', msgId: string): void
  (e: 'open-ask-expert', expert: Expert, debateSessionId: string): void
  (e: 'citation-click', event: MouseEvent, references: Reference[]): void
  (e: 'open-reference-drawer', references: Reference[]): void
  (e: 'open-reference-modal', references: Reference[]): void
  (e: 'mode-confirm', mode: string): void
  (e: 'open-reference-link', url: string): void
  (e: 'preview-file', fileInfo: { filename: string; downloadUrl: string; size: string }): void
  (e: 'download-file', downloadUrl: string): void
  (e: 'convert-to-pdf', fileInfo: { filename: string; downloadUrl: string; size: string }): void
  (e: 'stop-session'): void
  (e: 'retry-question'): void
  (e: 'quick-reply', text: string): void
  (e: 'regenerate', messageId: string, forceSkill?: string): void
  (e: 'edit-message', messageId: string, newContent: string): void
  (e: 'switch-version', targetMessageId: string): void
}>()

// ==================== v5.0: 编辑 & 版本管理 ====================
// isEditing/startEditing/cancelEditing/submitEdit 已移至 UserMessage.vue

/** 是否显示版本导航（siblings > 1） */
// ==================== 侧栏逻辑 ====================

const sidebarRef = ref<HTMLElement>()

// ==================== 工具函数 ====================

function escapeAttr(str: unknown): string {
  const s = String(str ?? '')
  return s.replace(/"/g, '&quot;').replace(/'/g, '&#39;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

// ==================== 侧栏可见性 ====================

const hasProcessInfo = computed(() => {
  const msg = props.msg
  const hasToolCalls = getVisibleToolSteps(msg.thinkingSteps).length > 0
  return hasToolCalls || (msg.subAgents && msg.subAgents.length > 0)
})

const sidebarLocked = ref(false)

watch(hasProcessInfo, (val) => {
  if (val) sidebarLocked.value = true
})
watch(() => props.msg.isStreaming, (val) => {
  if (!val) {
    sidebarLocked.value = false
    sidebarCollapsed.value = true
  }
})

const showSidebar = computed(() =>
  (hasProcessInfo.value || (sidebarLocked.value && props.msg.isStreaming)) && props.viewMode !== 'simple'
)

// Simple 模式工具摘要行
// customStages 或有 stageOutputs 时隐藏——工具调用在阶段 chip 展开中可见

// 过滤后的工具摘要（排除 invisible 阶段的工具调用）

// 主内容区文件卡片（从 toolCalls 中提取生成的文件，按 URL 去重）
// generatedFiles 已移至 AiMessagePostContent.vue

// ==================== 内容解析（已提取为 useContentParser composable） ====================
const {
  isHtmlAnimation, extractedAnimationHtml, animationStreamingCode,
  isHtmlInteractive, extractedInteractiveHtml, interactiveStreamingCode,
  guideProgress, extractedQuizzes, extractedSlides,
  contentWithoutQuizAndState, normalizedContent,
  hasInlineMarkers, contentBlocks, firstTextBlockIndex,
} = useContentParser(() => props.msg)

// ==================== Classroom 课堂模式 ====================
const isClassroomMode = computed(() => props.msg.outputFormat === 'classroom')
const classroomStatus = computed(() => props.msg.classroomData?.status || classroomRestoredData.value?.status || 'generating')
const classroomSessionId = computed(() => props.msg.classroomData?.sessionId || classroomRestoredData.value?.session_id || '')
const classroomCanEnter = computed(() => {
  const liveData = props.msg.classroomData as any
  const restoredData = classroomRestoredData.value as any
  if (liveData?.can_enter === true || liveData?.canEnter === true) return true
  if (restoredData?.can_enter === true || restoredData?.canEnter === true) return true
  return classroomStatus.value === 'ready'
})
const classroomReady = computed(() => classroomCanEnter.value)

// 从 SSE events 中提取进度信息
/** 从实时 SSE 构建的 scenes 或 API 恢复数据中提取缩略图 */
const classroomLiveScenes = computed<Array<{ index: number; title: string; slide: any | null; status: string }>>(() => {
  // 优先用 SSE 实时数据，回退到 API 恢复数据
  const sseScenes = props.msg.classroomData?.scenes || []
  const restoredScenes = classroomRestoredData.value?.scenes || []
  const scenes = sseScenes.length > 0 ? sseScenes : restoredScenes

  return scenes.filter(Boolean).map((s: any) => {
    let slide: any = null
    const sceneType = s.scene_type || 'slide'
    const content = s.content
    if (content && sceneType === 'slide') {
      const c = content as any
      if (c.layout || c.background || c.elements?.[0]?.left !== undefined) {
        slide = c
      }
    }
    return { index: s.scene_index, title: s.title || '', slide, status: s.status || 'pending' }
  })
})

const classroomProgress = computed(() => {
  if (classroomRestoredData.value) {
    const d = classroomRestoredData.value
    const audios = d.scenes?.filter((s: any) => s.audio_file_id)?.length || 0
    return { outlines: d.total_scenes || 0, scenes: d.total_scenes || 0, audios, total: d.total_scenes || 0 }
  }
  const events = props.msg.classroomData?.events || []
  const outlines = events.filter(e => e.type === 'classroom_outline_ready').length
  const scenes = events.filter(e => e.type === 'classroom_scene_generated').length
  const audios = events.filter(e => e.type === 'classroom_scene_audio_ready').length
  const total = props.msg.classroomData?.totalScenes || outlines || 0
  return { outlines, scenes, audios, total }
})

// ClassroomPlayer 全屏 overlay
const showClassroomPlayer = ref(false)
const classroomFullData = ref<ClassroomData | null>(null)
const classroomLoading = ref(false)

// 页面加载恢复：从 API 拉取已完成的课堂数据
const classroomRestoredData = ref<ClassroomData | null>(null)
const classroomRestoreTriedMsgId = ref('')
const classroomRestoring = ref(false)

// 监听：outputFormat=classroom + 非流式 + 需要恢复 → 从 API 恢复
watch(
  () => ({
    isClassroom: isClassroomMode.value,
    streaming: props.msg.isStreaming,
    hasSessionId: !!props.msg.classroomData?.sessionId,
    hasFullData: !!(props.msg.classroomData?.scenes?.length),
    msgId: props.msg.id,
  }),
  async ({ isClassroom, streaming, hasSessionId, hasFullData, msgId }) => {
    // 有 sessionId 但无完整场景数据 → 需要恢复完整状态
    if (isClassroom && !streaming && hasSessionId && !hasFullData && msgId !== classroomRestoreTriedMsgId.value) {
      classroomRestoreTriedMsgId.value = msgId
      classroomRestoredData.value = null
      await restoreClassroomData()
    }
    // 无 sessionId → fallback 到 by-session（旧数据兼容）
    if (isClassroom && !streaming && !hasSessionId && !(props.msg.classroomData) && msgId !== classroomRestoreTriedMsgId.value) {
      classroomRestoreTriedMsgId.value = msgId
      classroomRestoredData.value = null
      await restoreClassroomData()
    }
  },
  { immediate: true },
)

async function restoreClassroomData() {
  classroomRestoring.value = true
  try {
    const token = localStorage.getItem('token') || ''
    // 优先：msg 自带 classroom_session_id（正向关联，精确查询）
    const clsSessionId = props.msg.classroomData?.sessionId
    if (clsSessionId) {
      const resp = await fetch(`/api/user/classroom/${clsSessionId}/state`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (resp.ok) {
        classroomRestoredData.value = await resp.json()
        return
      }
    }
    // fallback：by-session（旧数据兼容，返回课堂列表，按时间匹配）
    const saSessionId = window.location.pathname.match(/sa_[a-f0-9]+/)?.[0]
    if (!saSessionId) return
    const resp = await fetch(`/api/user/classroom/by-session/${saSessionId}`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    if (resp.ok) {
      const data = await resp.json()
      // API 返回课堂列表（按创建时间升序），按消息时间戳匹配最近的课堂
      if (Array.isArray(data) && data.length > 0) {
        const msgTime = props.msg.timestamp ? new Date(props.msg.timestamp).getTime() : 0
        if (msgTime && data.length > 1) {
          // 找创建时间 <= 消息时间戳 且最接近的课堂
          let best = data[data.length - 1] // 默认最新
          for (let i = data.length - 1; i >= 0; i--) {
            const clsTime = data[i].created_at ? new Date(data[i].created_at).getTime() : 0
            if (clsTime && clsTime <= msgTime + 60000) { // 容忍 1 分钟误差
              best = data[i]
              break
            }
          }
          classroomRestoredData.value = best
        } else {
          // 只有一个课堂或无时间戳，取最新
          classroomRestoredData.value = data[data.length - 1]
        }
      } else if (data && !Array.isArray(data)) {
        // 兼容旧格式（单个对象）
        classroomRestoredData.value = data
      }
    }
  } catch (e) {
    console.error('[Classroom] 恢复课堂数据失败', e)
  } finally {
    classroomRestoring.value = false
  }
}

async function openClassroom() {
  const sid = classroomSessionId.value
  if (!sid) return
  classroomLoading.value = true
  try {
    const token = localStorage.getItem('token') || ''
    const resp = await fetch(`/api/user/classroom/${sid}/state`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    if (resp.ok) {
      classroomFullData.value = await resp.json()
      showClassroomPlayer.value = true
    }
  } catch (e) {
    console.error('[Classroom] 加载课堂数据失败', e)
  } finally {
    classroomLoading.value = false
  }
}

async function handleClassroomControl(action: string, params?: Record<string, any>) {
  if (!classroomSessionId.value) return
  try {
    const token = localStorage.getItem('token') || ''
    await fetch(`/api/user/classroom/${classroomSessionId.value}/control`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, ...params }),
    })
  } catch (e) {
    console.error('[Classroom] 控制请求失败', e)
  }
}

/** 根据 toolCallId 找到对应的 ThinkingStep */
// ==================== 进度节点导航 ====================

// ==================== 简洁模式工具摘要 ====================

const showToolSummaryLine = computed(() =>
  props.viewMode === 'simple' &&
  !props.msg.executionPlan?.customStages &&
  !props.msg.stageOutputs?.length
)

const filteredToolSummarySteps = computed(() => {
  if (!props.msg.thinkingSteps) return []
  return props.msg.thinkingSteps.filter((s: ThinkingStep) =>
    s.isToolCall && (s.status === 'success' || s.status === 'error' || s.status === 'running')
  )
})

// ==================== Standalone 插件 ====================

const standaloneToolCalls = computed(() => {
  if (!props.msg.toolCalls) return []
  return props.msg.toolCalls.filter(tc => tc.standalonePlugin)
})

const expandedPlugins = ref<Set<string>>(new Set())

watch(standaloneToolCalls, (newVal, oldVal) => {
  const oldIds = new Set((oldVal || []).map(tc => tc.toolCallId))
  for (const tc of newVal) {
    if (tc.toolCallId && !oldIds.has(tc.toolCallId)) {
      expandedPlugins.value.add(tc.toolCallId)
    }
  }
}, { immediate: true })

function isPluginExpanded(toolCallId: string): boolean {
  return expandedPlugins.value.has(toolCallId)
}

function togglePluginExpand(toolCallId: string) {
  if (expandedPlugins.value.has(toolCallId)) {
    expandedPlugins.value.delete(toolCallId)
  } else {
    expandedPlugins.value.add(toolCallId)
  }
}

// ==================== 侧栏状态 ====================

const sidebarCollapsed = ref(!props.msg.isStreaming)

function handleSidebarScroll() {
  // 侧栏滚动检测（简化版）
}

// ==================== 内联工具调用 ====================

function findToolStep(toolCallId: string) {
  return props.msg.thinkingSteps?.find((s: ThinkingStep) => s.toolCallId === toolCallId && s.isToolCall)
}

function findToolCall(toolCallId: string) {
  return props.msg.toolCalls?.find((tc: ToolCall) => tc.toolCallId === toolCallId)
}

function handleNavigateTo(index: number) {
  emit('quick-reply', `回到知识点 ${index}`)
}

// ==================== 引用 Tooltip ====================
const {
  tooltipVisible,
  tooltipContent,
  tooltipPosition,
  handleCitationMousemove,
  handleTooltipEnter,
  handleTooltipLeave,
} = useCitationTooltip()
</script>

<template>
  <!-- 用户消息 -->
  <UserMessage
    v-if="msg.role === 'user'"
    :msg="msg"
    :is-mobile="isMobile"
    :is-global-streaming="isGlobalStreaming"
    :messages-after-count="messagesAfterCount"
    @edit-message="(msgId, content) => $emit('edit-message', msgId, content)"
    @switch-version="(targetId) => $emit('switch-version', targetId)"
  />

  <!-- AI 消息 -->
  <div v-else class="message ai-message"
       :class="{
         'mobile-message': isMobile,
         'ai-message-with-sidebar': showSidebar && !isMobile
       }">
    <div class="message-content ai-content">
      <!-- 模式选择卡片 -->
      <ModeSelectionCard
        v-if="msg.pendingModeSelection"
        :recommended-mode="msg.pendingModeSelection.recommendedMode"
        :modes="msg.pendingModeSelection.modes"
        :timeout-seconds="msg.pendingModeSelection.timeoutSeconds"
        @confirm="$emit('mode-confirm', $event)"
      />

      <!-- Skill 标签 -->
      <div v-if="msg.skillDisplayName" class="skill-badge">
        <n-tag type="info" :size="isMobile ? 'tiny' : 'small'">
          <template #icon>
            <n-icon :component="BuildOutline" />
          </template>
          {{ msg.skillDisplayName }}
        </n-tag>
      </div>

      <!-- 执行阶段进度（仅 simple 模式，professional/debug 有侧栏替代） -->
      <ExecutionProgress
        v-if="msg.isStreaming && currentSkill && viewMode === 'simple'"
        :skill-name="currentSkill"
        :current-phase="currentPhase"
        :is-completed="!msg.isStreaming && skillExecutionTime > 0"
        :execution-time="skillExecutionTime"
        :aggregation-progress="msg.aggregationProgress"
        class="execution-progress-wrapper"
        :class="{ 'mobile-progress': isMobile }"
      />

      <!-- 动态状态文字 -->
      <div v-if="msg.isStreaming && latestEventType" class="status-row">
        <span
          v-if="viewMode === 'debug'"
          class="connection-health-dot"
          :class="connectionHealth"
          :title="connectionHealth === 'connected' ? '连接正常' : connectionHealth === 'warning' ? '30s 无响应' : '连接已关闭'"
        />
        <StatusText
          :event-type="latestEventType"
          :event-data="latestEventData"
          :skill-name="currentSkill"
          class="status-text-wrapper"
        />
      </div>

      <!-- 辩论专家标签 -->
      <div v-if="msg.experts && msg.experts.length > 0" class="experts-section">
        <div class="experts-header">
          <n-tag type="success" size="small">
            <template #icon>
              <n-icon :component="PeopleOutline" />
            </template>
            多专家辩论 ({{ msg.experts.length }}位专家)
          </n-tag>
          <span class="experts-hint">点击专家可追问</span>
        </div>
        <div class="experts-list">
          <div
            v-for="expert in msg.experts"
            :key="expert.id"
            class="expert-card"
            @click="msg.debateSessionId && emit('open-ask-expert', expert, msg.debateSessionId)"
          >
            <div class="expert-avatar">{{ expert.name.charAt(0) }}</div>
            <div class="expert-info">
              <div class="expert-name">{{ expert.name }}</div>
              <div class="expert-title">{{ expert.title }}</div>
            </div>
            <n-icon :component="ChatbubbleOutline" class="ask-icon" />
          </div>
        </div>
      </div>

      <!-- 辩论话题预研背景 -->
      <div
        v-if="msg.isStreaming && msg.experts && msg.experts.length > 0 && !msg.content && !msg.preResearchContent"
        class="pre-research-loading"
      >
        <span class="pre-research-loading-dot"></span>
        正在搜索话题背景资料...
      </div>
      <div v-if="msg.preResearchContent" class="pre-research-section">
        <div class="pre-research-header" @click="preResearchExpanded = !preResearchExpanded">
          <span class="pre-research-icon">&#128218;</span>
          <span class="pre-research-title">话题背景资料</span>
          <span class="pre-research-toggle">{{ preResearchExpanded ? '收起' : '展开' }}</span>
        </div>
        <div v-if="preResearchExpanded" class="pre-research-body">
          <div class="pre-research-content">{{ msg.preResearchContent }}</div>
          <div class="pre-research-source">来源：网络搜索自动预研</div>
        </div>
      </div>

      <!-- 简洁模式：工具执行一行摘要（保留在主内容区，排除 invisible 阶段） -->
      <div
        v-if="showToolSummaryLine && filteredToolSummarySteps.length > 0"
        class="tool-summary-lines"
      >
        <div
          v-for="step in filteredToolSummarySteps"
          :key="step.toolCallId || step.step"
          class="tool-summary-line"
          :class="`tool-line-${step.status || 'pending'}`"
        >
          <span class="tool-line-status">{{ step.status === 'success' ? '✓' : step.status === 'error' ? '✗' : step.status === 'running' ? '⟳' : '·' }}</span>
          <span class="tool-line-name">{{ step.toolDisplayName || step.toolName }}</span>
          <span v-if="step.summary" class="tool-line-summary">→ {{ step.summary }}</span>
        </div>
      </div>

      <!-- 计划面板插槽（由 SuperAgent 通过 slot 注入） -->
      <slot name="plan-panel" />

      <!-- 移动端：执行过程内联渲染（非 Simple 模式） -->
      <ExecutionSidebar
        v-if="showSidebar && isMobile"
        :msg-id="msg.id"
        :thinking-steps="msg.thinkingSteps"
        :tool-calls="msg.toolCalls"
        :sub-agents="msg.subAgents"
        :is-streaming="msg.isStreaming"
        :view-mode="viewMode"
        :expanded-thinking="expandedThinking"
        :is-mobile="true"
        :token-usage="msg.tokenUsage"
        :aggregation-progress="msg.aggregationProgress"
        :execution-plan="msg.executionPlan"
        @toggle-thinking="(id) => emit('toggle-thinking', id)"
        @stop-session="emit('stop-session')"
        @preview-file="(f) => emit('preview-file', f)"
        @download-file="(u) => emit('download-file', u)"
        @convert-to-pdf="(f) => emit('convert-to-pdf', f)"
      />

      <!-- 研究流水线摘要面板（已提取为 ResearchPipeline） -->
      <ResearchPipeline :msg="msg" :view-mode="viewMode" />

      <!-- 课堂模式（classroom output-format 驱动渲染路由） -->
      <div v-if="isClassroomMode" class="classroom-entry">
        <!-- 进度卡片（始终显示生成进度） -->
        <div class="classroom-progress-card">
          <div class="classroom-progress-icon">🏫</div>
          <div class="classroom-progress-info">
            <div class="classroom-progress-title">
              {{ classroomReady && classroomStatus === 'ready' ? '课堂已就绪' : classroomReady ? '课堂准备中（可提前进入）' : '正在生成课堂' }}
            </div>
            <div class="classroom-progress-detail">
              <span v-if="classroomProgress.outlines > 0">{{ classroomProgress.total }} 个场景</span>
              <span v-if="classroomProgress.scenes > 0"> · {{ classroomProgress.scenes }} 个就绪</span>
              <span v-if="classroomProgress.audios > 0"> · {{ classroomProgress.audios }} 个带语音</span>
            </div>
            <div class="classroom-progress-bar">
              <div
                class="classroom-progress-fill"
                :style="{ width: classroomProgress.total ? `${Math.round((classroomProgress.scenes / classroomProgress.total) * 100)}%` : '0%' }"
              />
            </div>
          </div>
          <!-- 进入课堂按钮（partial_ready 或 ready 时出现） -->
          <NButton
            v-if="classroomReady"
            type="primary"
            :loading="classroomLoading"
            @click.stop="openClassroom"
          >
            进入课堂
          </NButton>
        </div>

        <!-- 实时场景缩略图（边生成边出现） -->
        <div v-if="classroomLiveScenes.length > 0" class="classroom-live-thumbnails">
          <div
            v-for="scene in classroomLiveScenes"
            :key="scene.index"
            class="classroom-thumb-item"
            :class="{ 'is-skeleton': !scene.slide, 'is-failed': scene.status === 'failed' }"
          >
            <SlideRenderer
              v-if="scene.slide"
              :slide="scene.slide"
              mode="thumbnail"
            />
            <div v-else-if="scene.status === 'failed'" class="classroom-thumb-failed">
              <span>⚠️</span>
            </div>
            <div v-else class="classroom-thumb-skeleton">
              <div class="skeleton-line w60" />
              <div class="skeleton-line w80" />
              <div class="skeleton-line w40" />
            </div>
            <div class="classroom-thumb-title">{{ scene.title }}</div>
          </div>
        </div>

        <!-- ClassroomPlayer 全屏 overlay -->
        <Teleport to="body">
          <Transition name="classroom-slide">
            <ClassroomPlayer
              v-if="showClassroomPlayer && classroomFullData"
              :data="classroomFullData"
              :live-scenes="msg.classroomData?.scenes || []"
              @exit="showClassroomPlayer = false"
              @control="handleClassroomControl"
            />
          </Transition>
        </Teleport>
      </div>

      <!-- 动画播放器（output-format 驱动渲染路由） -->
      <AnimationPlayer
        v-if="isHtmlAnimation && (extractedAnimationHtml || msg.isStreaming)"
        :html-content="extractedAnimationHtml || ''"
        :is-streaming="msg.isStreaming"
        :streaming-code="animationStreamingCode"
        @retry-question="emit('retry-question')"
      />

      <!-- 交互式学习播放器（html-interactive 路由） -->
      <template v-else-if="isHtmlInteractive">
        <!-- Markdown 内容先渲染（过滤掉 HTML 代码块 + Quiz 块 + GUIDE_STATE 防止双显） -->
        <StreamingContent
          v-if="(normalizedContent && normalizedContent.trim()) || (msg.reasoningBlocks && msg.reasoningBlocks.length > 0)"
          :content="normalizedContent"
          :is-streaming="msg.isStreaming"
          :hide-cursor="true"
          :references="msg.references"
          :reasoning-blocks="msg.reasoningBlocks"
          :msg-id="msg.id"
          :streaming-expert-id="streamingExpertId"
          @citation-click="(e, refs) => emit('citation-click', e, refs)"
          @citation-mousemove="handleCitationMousemove"
        />
        <!-- 交互式内容播放器（跟在 Markdown 文字之后） -->
        <InteractivePlayer
          v-if="extractedInteractiveHtml || (msg.isStreaming && interactiveStreamingCode)"
          :msg-id="msg.id"
          :html-content="extractedInteractiveHtml || ''"
          :is-streaming="msg.isStreaming"
          :streaming-code="interactiveStreamingCode"
          :progress="guideProgress?.progress"
          :progress-text="guideProgress?.progressText"
          :current-topic="guideProgress?.currentTopic"
          :current-index="guideProgress?.currentIndex"
          :total="guideProgress?.total"
          :mastery="guideProgress?.mastery"
          @retry-question="emit('retry-question')"
          @navigate-to="handleNavigateTo"
        />
        <!-- 流式光标：放在 InteractivePlayer 之后 -->
        <span v-if="msg.isStreaming && !msg.pendingClientTool" class="streaming-cursor" />
        <!-- Quiz 检测题块 -->
        <QuizBlock
          v-for="(quiz, idx) in extractedQuizzes"
          :key="`quiz-${msg.id}-${idx}`"
          :quiz="quiz"
        />
        <!-- Slides 在线演示 -->
        <SlidesPresenter
          v-if="extractedSlides"
          :slides-data="extractedSlides"
        />
      </template>

      <!-- Phase 2.1b: v2 ContentBlock 渲染路径（仅非 plan 模式，plan 模式仍走旧路径） -->
      <ContentRenderer
        v-else-if="msg.blocks && msg.blocks.length > 0 && !msg.executionPlan"
        :blocks="msg.blocks"
        :msg="msg"
      >
        <template #tool-call="{ toolCallId, thinkingStep, toolCall }">
          <div v-if="thinkingStep" class="inline-tool-call">
            <ToolCallCard
              :step="thinkingStep"
              :tool-call="toolCall"
            />
          </div>
        </template>
      </ContentRenderer>

      <!-- 主要内容（带内联工具调用的交错渲染模式 — 旧 v1 路径） -->
      <template v-else-if="contentBlocks && contentBlocks.length > 0">
        <template v-for="(block, bIdx) in contentBlocks" :key="`cb-${bIdx}`">
          <StreamingContent
            v-if="block.type === 'text'"
            :content="block.text"
            :is-streaming="msg.isStreaming && bIdx === contentBlocks.length - 1"
            :hide-cursor="!!msg.pendingClientTool"
            :references="msg.references"
            :reasoning-blocks="bIdx === firstTextBlockIndex ? msg.reasoningBlocks : undefined"
            :msg-id="msg.id"
            :streaming-expert-id="bIdx === firstTextBlockIndex ? streamingExpertId : undefined"
            @citation-click="(e, refs) => emit('citation-click', e, refs)"
            @citation-mousemove="handleCitationMousemove"
          />
          <div v-else-if="block.type === 'tool' && findToolStep(block.toolCallId)" class="inline-tool-call">
            <ToolCallCard
              :step="findToolStep(block.toolCallId)!"
              :tool-call="findToolCall(block.toolCallId)"
            />
          </div>
          <div v-else-if="block.type === 'progress'" class="stage-progress-line">
            <span class="stage-progress-spinner"></span>
            <span class="stage-progress-text">{{ block.title }}（{{ block.progress }}）...</span>
          </div>
        </template>
      </template>

      <!-- 主要内容（普通模式 StreamingContent 组件） -->
      <StreamingContent
        v-else-if="msg.content.trim() || (msg.reasoningBlocks && msg.reasoningBlocks.length > 0)"
        :content="normalizedContent"
        :is-streaming="msg.isStreaming"
        :hide-cursor="!!msg.pendingClientTool"
        :references="msg.references"
        :reasoning-blocks="msg.reasoningBlocks"
        :msg-id="msg.id"
        :streaming-expert-id="streamingExpertId"
        @citation-click="(e, refs) => emit('citation-click', e, refs)"
        @citation-mousemove="handleCitationMousemove"
      />

      <!-- Standalone 插件 iframe（跟在 AI 文字之后显示） -->
      <template v-if="standaloneToolCalls.length > 0">
        <div
          v-for="tc in standaloneToolCalls"
          :key="`spi-${tc.toolCallId}`"
          class="standalone-plugin-wrapper"
        >
          <template v-if="isPluginExpanded(tc.toolCallId || '')">
            <StandalonePluginIframe :tool-call="tc" />
          </template>
          <div
            v-else
            class="standalone-plugin-collapsed"
            @click="togglePluginExpand(tc.toolCallId || '')"
          >
            <span class="spi-collapsed-icon">🔌</span>
            <span class="spi-collapsed-title">{{ tc.toolDisplayName || tc.tool }}</span>
            <span class="spi-collapsed-hint">点击展开插件</span>
          </div>
        </div>
      </template>

      <!-- 结构化流水线：渐进式内容流（write 未开始时，仅默认 deep-research 流水线） -->
      <!-- Pipeline 流式进度视图（已提取为 PipelineProgress） -->
      <PipelineProgress
        v-else-if="msg.isStreaming && msg.executionPlan && !msg.executionPlan.customStages"
        :msg="msg"
      />
      <!-- 客户端工具内联交互插槽（融入正文流中） -->
      <slot name="client-tool" />

      <!-- 流式光标（在所有内联内容之后，非 Pipeline 流式时显示） -->
      <span v-if="msg.isStreaming && !(msg.executionPlan && !msg.executionPlan.customStages)" class="typing-cursor">&#9612;</span>

      <!-- 后置内容（生成文件/Quiz/引用/验证/诊断/错误，已提取为 AiMessagePostContent） -->
      <AiMessagePostContent
        :msg="msg"
        :is-mobile="isMobile"
        :extracted-quizzes="extractedQuizzes"
        :is-html-interactive="isHtmlInteractive"
        @preview-file="(f) => emit('preview-file', f)"
        @download-file="(url) => emit('download-file', url)"
        @convert-to-pdf="(f) => emit('convert-to-pdf', f)"
        @open-reference-drawer="(refs) => emit('open-reference-drawer', refs)"
        @open-reference-modal="(refs) => emit('open-reference-modal', refs)"
        @retry-question="emit('retry-question')"
      >
        <template #artifact-card>
          <slot name="artifact-card" />
        </template>
      </AiMessagePostContent>

      <!-- v5.0: AI 消息操作栏 + 快捷回复（已提取为 AiMessageActions） -->
      <AiMessageActions
        :msg="msg"
        :available-skills="availableSkills"
        :is-global-streaming="isGlobalStreaming"
        :quick-replies="quickReplies"
        @regenerate="(msgId, skill) => emit('regenerate', msgId, skill)"
        @stop-session="emit('stop-session')"
        @switch-version="(targetId) => emit('switch-version', targetId)"
        @quick-reply="(text) => emit('quick-reply', text)"
      />
    </div>

    <!-- PC端：执行过程侧栏 -->
    <div v-if="showSidebar && !isMobile"
         ref="sidebarRef"
         class="execution-sidebar"
         :class="{ collapsed: sidebarCollapsed }"
         role="complementary"
         aria-label="执行过程"
         @scroll="handleSidebarScroll">
      <!-- P1-7: 收起/展开按钮（完成后显示） -->
      <button v-if="!msg.isStreaming"
              class="sidebar-collapse-btn"
              @click="sidebarCollapsed = !sidebarCollapsed"
              :aria-label="sidebarCollapsed ? '展开执行过程' : '收起执行过程'">
        {{ sidebarCollapsed ? '»' : '«' }}
      </button>
      <ExecutionSidebar
        v-show="!sidebarCollapsed"
        :msg-id="msg.id"
        :thinking-steps="msg.thinkingSteps"
        :tool-calls="msg.toolCalls"
        :sub-agents="msg.subAgents"
        :is-streaming="msg.isStreaming"
        :view-mode="viewMode"
        :expanded-thinking="expandedThinking"
        :token-usage="msg.tokenUsage"
        :aggregation-progress="msg.aggregationProgress"
        :execution-plan="msg.executionPlan"
        @toggle-thinking="(id) => emit('toggle-thinking', id)"
        @stop-session="emit('stop-session')"
        @preview-file="(f) => emit('preview-file', f)"
        @download-file="(u) => emit('download-file', u)"
        @convert-to-pdf="(f) => emit('convert-to-pdf', f)"
      />
    </div>
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

<style>
/* 消息基础样式 */
.message {
  display: flex;
  margin-bottom: 16px;
}

.user-message {
  justify-content: flex-end;
}

.ai-message {
  justify-content: flex-start;
}

.message-content {
  max-width: 75%;
  padding: 12px 16px;
  border-radius: 16px;
}

.user-content {
  background: #2080f0;
  color: #fff;
  border-bottom-right-radius: 4px;
  white-space: pre-wrap;
}
.user-content--editing {
  background: transparent;
  color: inherit;
  padding: 0;
  border-radius: 0;
}

/* 用户消息附件 */
.user-attachments {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid rgba(255, 255, 255, 0.2);
}

.user-att-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px;
  background: rgba(255, 255, 255, 0.15);
  border-radius: 6px;
  font-size: 12px;
}

.user-att-thumb {
  width: 28px;
  height: 28px;
  border-radius: 4px;
  object-fit: cover;
}

.user-att-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.2);
  font-size: 9px;
  font-weight: 600;
}

.user-att-name {
  max-width: 120px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.ai-content {
  background: #fff;
  color: #333;
  border-bottom-left-radius: 4px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
  width: 70%;
  min-width: 280px;
}

/* v5 侧栏布局 */
.ai-message-with-sidebar {
  max-width: 96%;
  gap: 16px;
  align-items: flex-start;
}

.ai-message-with-sidebar .ai-content {
  width: auto;
  max-width: none;
  flex: 1;
  min-width: 0;
}

.execution-sidebar {
  width: 300px;
  flex-shrink: 0;
  position: sticky;
  top: 0;
  align-self: flex-start;
  max-height: calc(100vh - 320px);
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
  padding: 0 0 0 16px;
  background: transparent;
  border: none;
  border-left: 1px solid #e5e7eb;
  border-radius: 0;
}

.execution-sidebar::-webkit-scrollbar {
  width: 4px;
}

.execution-sidebar::-webkit-scrollbar-thumb {
  background: #cbd5e1;
  border-radius: 2px;
}

/* P1-7: 收起侧栏按钮 */
.sidebar-collapse-btn {
  position: sticky;
  top: 0;
  z-index: 1;
  float: right;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  border: 1px solid #e5e7eb;
  background: #fff;
  cursor: pointer;
  font-size: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #94a3b8;
  margin-bottom: 8px;
}
.sidebar-collapse-btn:hover {
  color: #475569;
  border-color: #cbd5e1;
}

/* 侧栏收起状态 */
.execution-sidebar.collapsed {
  width: 24px;
  padding: 0;
  border: none;
  overflow: hidden;
}

/* 响应式 */
@media (max-width: 1200px) {
  .execution-sidebar { width: 260px; }
}

/* P1-4: 1024px 以下回退为堆叠布局 */
@media (max-width: 1024px) {
  .ai-message-with-sidebar {
    flex-direction: column;
    max-width: 80%;
  }
  .execution-sidebar {
    width: 100%;
    position: static;
    max-height: none;
    border-left: none;
    border-top: 1px solid #e5e7eb;
    padding: 12px 0 0 0;
  }
}

/* P2-1: 打印时隐藏侧栏 */
@media print {
  .execution-sidebar { display: none; }
  .ai-message-with-sidebar .ai-content { width: 100%; }
}

.mobile-message .message-content {
  max-width: 90%;
}

.mobile-message .ai-content {
  width: auto;
  min-width: unset;
  max-width: 90%;
}

.mobile-message .ai-content :deep(.markdown-content) {
  min-width: unset;
  overflow-wrap: break-word;
  word-break: break-word;
}

.mobile-message :deep(.reasoning-block) {
  margin: 8px 0;
}

.mobile-message :deep(.reasoning-block .reasoning-header) {
  padding: 8px 12px;
}

.mobile-message :deep(.reasoning-block .reasoning-body) {
  padding: 10px 12px;
}

.mobile-message :deep(.reasoning-block .reasoning-text) {
  font-size: 12px;
  line-height: 1.6;
}

/* 过渡动画 */
.view-mode-fade-enter-active,
.view-mode-fade-leave-active {
  transition: opacity 0.2s ease, transform 0.2s ease;
}

.view-mode-fade-enter-from,
.view-mode-fade-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

/* 渐进式内容流 */
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

.analyze-plan-structured {
  margin-top: 8px;
}
.plan-label {
  font-weight: 600;
  font-size: 12px;
  color: #374151;
  margin-bottom: 4px;
}
.plan-question-item {
  font-size: 12px;
  color: #4b5563;
  padding: 2px 0;
}
.plan-search-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  margin-top: 8px;
}
.plan-search-table th {
  background: #f3f4f6;
  padding: 4px 8px;
  text-align: left;
  font-weight: 600;
  color: #374151;
  border-bottom: 1px solid #e5e7eb;
}
.plan-search-table td {
  padding: 4px 8px;
  border-bottom: 1px solid #f3f4f6;
  color: #4b5563;
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

.research-thinking {
  margin-top: 8px;
  padding: 8px 10px;
  background: #f8fafc;
  border-radius: 6px;
  font-size: 13px;
  color: #475569;
  border-left: 2px solid #93c5fd;
  line-height: 1.6;
}
.research-thinking :deep(h1),
.research-thinking :deep(h2),
.research-thinking :deep(h3),
.research-thinking :deep(h4) {
  font-size: 13px;
  font-weight: 600;
  color: #334155;
  margin: 10px 0 4px;
  padding: 0;
  line-height: 1.4;
}
.research-thinking :deep(h1:first-child),
.research-thinking :deep(h2:first-child),
.research-thinking :deep(h3:first-child) {
  margin-top: 0;
}
.research-thinking :deep(p) {
  margin: 4px 0;
}
.research-thinking :deep(ul),
.research-thinking :deep(ol) {
  margin: 4px 0;
  padding-left: 18px;
}
.research-thinking :deep(li) {
  margin: 2px 0;
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

.outline-preview {
  max-height: 150px;
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

.inline-tool-call {
  margin: 12px 0;
  max-width: 500px;
}

.stage-progress-line {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  margin: 8px 0;
  background: #f0f9ff;
  border-radius: 6px;
  border-left: 3px solid #3b82f6;
  font-size: 13px;
  color: #1e40af;
}

.stage-progress-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid #93c5fd;
  border-top-color: #3b82f6;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.stage-progress-text {
  font-weight: 500;
}

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

.chip-arrow {
  font-style: normal;
  font-size: 11px;
  transition: transform 0.2s;
  display: inline-block;
}
.chip-arrow.open {
  transform: rotate(90deg);
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

.citation-brief {
  color: #3b82f6;
  font-size: 0.8em;
  cursor: default;
}

.research-tool-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
  border-bottom: 1px solid #f1f5f9;
}
.research-tool-item:last-child {
  border-bottom: none;
}
.research-tool-item .tool-icon {
  flex-shrink: 0;
}
.research-tool-item .tool-label {
  font-weight: 500;
  color: #334155;
  white-space: nowrap;
}
.research-tool-item .tool-summary {
  color: #64748b;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* research 阶段子问题分析内容 */
.research-analysis-content {
  font-size: 13px;
  color: #475569;
  line-height: 1.6;
  padding-bottom: 8px;
  margin-bottom: 8px;
  border-bottom: 1px dashed #e2e8f0;
}
.research-analysis-content :deep(p) { margin: 0 0 6px; }
.research-analysis-content :deep(p:last-child) { margin-bottom: 0; }
.research-analysis-content :deep(ul),
.research-analysis-content :deep(ol) { margin: 4px 0; padding-left: 18px; }
.research-analysis-content :deep(li) { margin: 2px 0; }
.research-analysis-content :deep(strong) { color: #334155; }
.research-analysis-content :deep(h1),
.research-analysis-content :deep(h2),
.research-analysis-content :deep(h3) { font-size: 13px; font-weight: 600; color: #334155; margin: 8px 0 4px; }

/* 摘要展开中的聚合路径分组 */
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

.spin-icon {
  animation: spin 1.5s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.pipeline-waiting-text {
  color: #4b5563;
}

.typing-cursor {
  display: inline-block;
  animation: blink 1s step-end infinite;
  color: #3b82f6;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}

/* Skill 标签 */
.skill-badge {
  margin-bottom: 8px;
}

/* 执行进度 */
.execution-progress-wrapper {
  margin-bottom: 12px;
}

/* 状态行 */
.status-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.status-text-wrapper {
  flex: 1;
}

/* 连接健康状态点（调试模式） */
.connection-health-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.connection-health-dot.connected {
  background: #10b981;
}

.connection-health-dot.warning {
  background: #f59e0b;
  animation: pulse-dot 1.5s ease-in-out infinite;
}

.connection-health-dot.closed {
  background: #ef4444;
}

@keyframes pulse-dot {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}

/* 专家辩论区域 */
.experts-section {
  margin-bottom: 12px;
  padding: 12px;
  background: #f0fdf4;
  border-radius: 10px;
  border: 1px solid #bbf7d0;
}

.experts-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.experts-hint {
  font-size: 12px;
  color: #16a34a;
}

.experts-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.expert-card {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  background: #fff;
  border-radius: 10px;
  cursor: pointer;
  transition: all 0.2s;
  border: 1px solid #e5e7eb;
  min-width: 160px;
}

.expert-card:hover {
  border-color: #16a34a;
  box-shadow: 0 2px 8px rgba(22, 163, 74, 0.15);
  transform: translateY(-1px);
}

.expert-avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: linear-gradient(135deg, #16a34a 0%, #22c55e 100%);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 600;
  font-size: 14px;
  flex-shrink: 0;
}

.expert-info {
  flex: 1;
  min-width: 0;
}

.expert-name {
  font-weight: 600;
  font-size: 13px;
  color: #1f2937;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.expert-title {
  font-size: 11px;
  color: #6b7280;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.ask-icon {
  color: #16a34a;
  opacity: 0;
  transition: opacity 0.2s;
  flex-shrink: 0;
}

.expert-card:hover .ask-icon {
  opacity: 1;
}

/* 话题预研背景 */
.pre-research-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  padding: 10px 14px;
  border-radius: 10px;
  background: #eff6ff;
  border: 1px solid #bfdbfe;
  color: #3b82f6;
  font-size: 13px;
  animation: pre-research-fade-in 0.3s ease;
}

.pre-research-loading-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #3b82f6;
  animation: pre-research-pulse 1.2s ease-in-out infinite;
}

@keyframes pre-research-pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.3; transform: scale(0.8); }
}

@keyframes pre-research-fade-in {
  from { opacity: 0; transform: translateY(-4px); }
  to { opacity: 1; transform: translateY(0); }
}

.pre-research-section {
  margin-bottom: 12px;
  border-radius: 10px;
  border: 1px solid #bfdbfe;
  background: #eff6ff;
  overflow: hidden;
}

.pre-research-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  cursor: pointer;
  user-select: none;
  transition: background 0.15s;
}

.pre-research-header:hover {
  background: #dbeafe;
}

.pre-research-icon {
  font-size: 14px;
}

.pre-research-title {
  font-size: 13px;
  font-weight: 600;
  color: #1e40af;
  flex: 1;
}

.pre-research-toggle {
  font-size: 12px;
  color: #3b82f6;
}

.pre-research-body {
  padding: 0 14px 12px;
}

.pre-research-content {
  font-size: 13px;
  line-height: 1.7;
  color: #1e3a5f;
  white-space: pre-wrap;
}

.pre-research-source {
  margin-top: 8px;
  font-size: 11px;
  color: #93c5fd;
  text-align: right;
}

/* 简洁模式：工具执行一行摘要 */
.tool-summary-lines {
  margin-bottom: 8px;
}

.tool-summary-line {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #6b7280;
  padding: 2px 0;
}

.tool-line-success .tool-line-status {
  color: #10b981;
}

.tool-line-error .tool-line-status {
  color: #ef4444;
}

.tool-line-running .tool-line-status {
  color: #3b82f6;
}

.tool-line-name {
  font-weight: 500;
  color: #374151;
}

.tool-line-summary {
  color: #9ca3af;
  font-size: 11px;
}

/* 移动端进度 */
.mobile-progress {
  /* 移动端阶段进度精简样式由组件内部响应式处理 */
}

/* 主内容区文件卡片 */
.generated-files {
  margin-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.inline-file-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 14px;
  background: linear-gradient(135deg, #f0fdf4 0%, #ecfdf5 100%);
  border: 1px solid #bbf7d0;
  border-radius: 8px;
}

.inline-file-info {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.inline-file-icon {
  font-size: 20px;
  flex-shrink: 0;
}

.inline-file-name {
  font-weight: 600;
  color: #166534;
  font-size: 14px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.inline-file-size {
  font-size: 12px;
  color: #15803d;
  background: #dcfce7;
  padding: 2px 8px;
  border-radius: 4px;
  white-space: nowrap;
  flex-shrink: 0;
}

.inline-file-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

/* 移动端参考文献 */
.mobile-references-hint {
  margin-top: 12px;
  padding-top: 8px;
  border-top: 1px dashed #e5e7eb;
}

/* 参考文献区域 */
.references-section {
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid #e5e7eb;
}

.references-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 600;
  color: #374151;
  margin-bottom: 12px;
}

.refs-toggle-arrow {
  margin-left: auto;
  font-size: 12px;
  color: #9ca3af;
  transition: transform 0.2s;
}

.refs-toggle-arrow.expanded {
  transform: rotate(90deg);
}

.references-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.reference-item {
  padding: 12px 16px;
  background: #f9fafb;
  border-radius: 8px;
  border-left: 3px solid var(--le-primary, #2080f0);
  transition: all 0.3s;
}

.reference-item:hover {
  background: #f0f5ff;
  box-shadow: 0 2px 8px rgba(0,0,0,0.08);
}

.reference-header {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-bottom: 8px;
}

.reference-number {
  flex-shrink: 0;
  color: var(--le-primary, #2080f0);
  font-weight: 600;
  font-size: 14px;
}

.reference-title-text {
  font-size: 14px;
  font-weight: 500;
  color: #1f2937;
  line-height: 1.5;
}

.reference-meta {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  color: #6b7280;
  margin-bottom: 8px;
  flex-wrap: wrap;
}

.reference-year {
  color: #9ca3af;
}

.reference-source {
  color: #9ca3af;
}

.reference-abstract {
  font-size: 13px;
  color: #6b7280;
  line-height: 1.6;
  margin-bottom: 8px;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
/* 并行编排面板 */
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
.parallel-subtask-retrying-placeholder {
  margin-left: 24px;
  padding: 16px;
  text-align: center;
  color: #999;
  font-size: 13px;
}
.retrying-spinner {
  display: inline-block;
  animation: spin 1s linear infinite;
  margin-right: 4px;
}
.parallel-partial-hint {
  color: #e6a23c;
  font-size: 12px;
}
.parallel-subtask-content {
  margin-left: 24px;
  padding: 8px 12px;
  border-left: 2px solid #d0e3ff;
  max-height: 200px;
  overflow-y: auto;
  overflow-x: hidden;
  font-size: 13px;
  color: #595959;
  background: #fafafa;
  border-radius: 0 4px 4px 0;
  min-width: 0;
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
.verification-panel {
  margin-top: 12px;
  padding: 8px 14px;
  border-radius: 8px;
  font-size: 13px;
}
.verification-panel.passed {
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
}
.verification-panel.warning {
  background: #fffbeb;
  border: 1px solid #fde68a;
}
.verification-header {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  user-select: none;
}
.verification-score {
  font-weight: 600;
  color: #6b7280;
}
.verification-llm-score {
  font-weight: 600;
  color: #2080f0;
  font-size: 12px;
}
.verification-mode {
  font-size: 11px;
  color: #999;
  background: #f0f0f0;
  padding: 1px 6px;
  border-radius: 3px;
}
.verification-header .chip-arrow {
  margin-left: auto;
}
.verification-details {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid rgba(0,0,0,0.06);
}
.verification-dimensions {
  margin-bottom: 6px;
}
.dimension-row {
  padding: 4px 0;
  font-size: 12px;
}
.dimension-row + .dimension-row {
  border-top: 1px solid rgba(0,0,0,0.04);
}
.dimension-top {
  display: flex;
  align-items: center;
  gap: 8px;
}
.dimension-label {
  width: 80px;
  flex-shrink: 0;
  color: #555;
  font-weight: 500;
}
.dimension-bar-wrap {
  flex: 1;
  height: 6px;
  background: #e8e8e8;
  border-radius: 3px;
  overflow: hidden;
}
.dimension-bar {
  height: 100%;
  border-radius: 3px;
  transition: width 0.3s;
}
.dimension-score {
  width: 36px;
  flex-shrink: 0;
  font-weight: 600;
  color: #555;
  text-align: right;
}
.dimension-reasoning {
  color: #999;
  margin-top: 2px;
  padding-left: 88px;
  line-height: 1.4;
}
.dimension-meta {
  display: flex;
  gap: 12px;
  margin-top: 4px;
  font-size: 11px;
  color: #bbb;
}
.verification-issues {
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px dashed rgba(0,0,0,0.06);
}
.verification-issue {
  padding: 3px 0;
  color: #6b7280;
}
.verification-issue.error { color: #dc2626; }
.verification-issue.warning { color: #d97706; }

/* 生成质量错误横幅 */
.error-banner {
  margin-top: 12px;
  padding: 12px 16px;
  background: #fdf6ec;
  border: 1px solid #e6a23c;
  border-radius: 8px;
  display: flex;
  gap: 10px;
  align-items: flex-start;
}
.error-banner-icon {
  font-size: 18px;
  flex-shrink: 0;
  line-height: 1.4;
}
.error-banner-body {
  flex: 1;
  min-width: 0;
}
.error-banner-title {
  font-size: 13px;
  font-weight: 500;
  color: #7c5c00;
  margin-bottom: 4px;
}
.error-banner-desc {
  font-size: 12px;
  color: #9a7b32;
  margin-bottom: 8px;
}
.error-banner-actions {
  display: flex;
  gap: 8px;
}
.error-banner-btn {
  padding: 4px 12px;
  border-radius: 4px;
  font-size: 12px;
  cursor: pointer;
  border: none;
  transition: background 0.15s;
}
.error-banner-btn--primary {
  background: #e6a23c;
  color: #fff;
}
.error-banner-btn--primary:hover {
  background: #d4940f;
}
.error-banner-btn--text {
  background: transparent;
  color: #9a7b32;
}
.error-banner-btn--text:hover {
  background: rgba(230, 162, 60, 0.1);
}

/* 流式光标（interactive 模式下在 InteractivePlayer 之后显示） */
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

/* Standalone 插件折叠卡片 */
.standalone-plugin-wrapper {
  margin: 8px 0;
}
.standalone-plugin-collapsed {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border: 1px solid #e8e8e8;
  border-radius: 8px;
  background: #fafafa;
  cursor: pointer;
  transition: background 0.2s;
}
.standalone-plugin-collapsed:hover {
  background: #f0f0f0;
}
.spi-collapsed-icon {
  font-size: 16px;
}
.spi-collapsed-title {
  font-weight: 500;
  font-size: 13px;
  color: #333;
}
.spi-collapsed-hint {
  font-size: 12px;
  color: #999;
  margin-left: auto;
}

/* ==================== v5.0: 消息操作栏 ==================== */

/* 通用操作按钮 */
.msg-action-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  background: transparent;
  border-radius: 6px;
  cursor: pointer;
  color: #999;
  padding: 0;
  transition: all 0.15s;
}
.msg-action-btn:hover {
  background: rgba(0, 0, 0, 0.06);
  color: #555;
}

/* 用户消息操作栏 */
.user-message-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 4px;
  opacity: 0;
  transition: opacity 0.2s;
  justify-content: flex-end;
}
.user-message:hover .user-message-actions {
  opacity: 1;
}

/* AI 消息操作栏 */
.ai-message-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 0 0 0;
  margin-top: 8px;
  border-top: 1px solid rgba(0, 0, 0, 0.04);
}
.ai-message-actions--streaming {
  border-top: none;
  margin-top: 4px;
}
.ai-actions-left {
  display: flex;
  align-items: center;
  gap: 2px;
}

/* 快捷回复 chips（AI 消息底部） */
.ai-quick-replies {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding-top: 10px;
}
.ai-quick-reply-chip {
  padding: 6px 14px;
  border: 1px solid #e0e0e6;
  border-radius: 16px;
  background: #f8f9fa;
  color: #374151;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
  white-space: nowrap;
}
.ai-quick-reply-chip:hover {
  background: #e8f0fe;
  border-color: #3b82f6;
  color: #1d4ed8;
}

</style>

<style>
/* 进度节点点击后的高亮闪烁（非 scoped，JS 动态添加 class） */
.message.ai-message.highlight-flash {
  animation: flash-highlight 1.5s ease-out;
}
@keyframes flash-highlight {
  0% { background-color: rgba(59, 130, 246, 0.15); }
  100% { background-color: transparent; }
}
/* Tooltip 样式（非 scoped，因为 Teleport 到 body） */
.citation-tooltip {
  position: fixed;
  transform: translate(-50%, -100%) translateY(-8px);
  max-width: 360px;
  padding: 10px 14px;
  background: #1f2937;
  color: #fff;
  border-radius: 8px;
  font-size: 13px;
  line-height: 1.5;
  box-shadow: 0 4px 12px rgba(0,0,0,0.15);
  z-index: 9999;
}
.citation-tooltip-title {
  font-weight: 600;
  margin-bottom: 4px;
}
.citation-tooltip-meta {
  font-size: 12px;
  color: #9ca3af;
}
.citation-tooltip-abstract {
  font-size: 12px;
  color: #d1d5db;
  margin-top: 4px;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.citation-tooltip-link {
  display: inline-block;
  margin-top: 6px;
  font-size: 12px;
  color: #60a5fa;
  text-decoration: none;
}
.citation-tooltip-link:hover {
  text-decoration: underline;
}
.citation-mark {
  display: inline;
  font-size: 0.75em;
  vertical-align: super;
  line-height: 1;
  color: #3b82f6;
  cursor: pointer;
  font-weight: 600;
}
.citation-mark:hover {
  color: #2563eb;
  text-decoration: underline;
}

/* ==================== Classroom 课堂模式 ==================== */
.classroom-entry {
  margin: 12px 0;
}

.classroom-progress-card,
.classroom-ready-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px 20px;
  border-radius: 12px;
  background: linear-gradient(135deg, #f0f7ff 0%, #e8f4f8 100%);
  border: 1px solid #d0e3f0;
}

.classroom-ready-card {
  cursor: pointer;
  transition: all 0.2s ease;
  background: linear-gradient(135deg, #e8f5e9 0%, #e3f2fd 100%);
  border-color: #b8d4e3;
}
.classroom-ready-card:hover {
  box-shadow: 0 2px 12px rgba(0, 120, 212, 0.15);
  border-color: #90c4e0;
}

.classroom-progress-icon,
.classroom-ready-icon {
  font-size: 32px;
  flex-shrink: 0;
}

.classroom-progress-info,
.classroom-ready-info {
  flex: 1;
  min-width: 0;
}

.classroom-progress-title,
.classroom-ready-title {
  font-size: 15px;
  font-weight: 600;
  color: #1a1a2e;
  margin-bottom: 4px;
}

.classroom-progress-detail,
.classroom-ready-meta {
  font-size: 13px;
  color: #666;
}

.classroom-progress-bar {
  margin-top: 8px;
  height: 4px;
  background: #d0e3f0;
  border-radius: 2px;
  overflow: hidden;
}
.classroom-progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #4a9eff, #36d1dc);
  border-radius: 2px;
  transition: width 0.5s ease;
}

/* 实时场景缩略图 */
.classroom-live-thumbnails {
  display: flex;
  gap: 8px;
  margin-top: 12px;
  overflow-x: auto;
  padding: 4px 0;
}
.classroom-thumb-item {
  flex: 0 0 120px;
  border-radius: 6px;
  overflow: hidden;
  border: 1px solid #e5e7eb;
  background: #fff;
  transition: transform 0.2s, box-shadow 0.2s;
}
.classroom-thumb-item:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
}
.classroom-thumb-item.is-skeleton {
  border-color: #f0f0f0;
}
.classroom-thumb-item.is-failed {
  border-color: #fecaca;
}
.classroom-thumb-failed {
  aspect-ratio: 16 / 9;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #fef2f2;
  font-size: 20px;
}
.classroom-thumb-skeleton {
  aspect-ratio: 16 / 9;
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  justify-content: center;
  background: #fafafa;
}
.skeleton-line {
  height: 3px;
  background: #e5e7eb;
  border-radius: 2px;
  animation: skeleton-pulse 1.5s infinite ease-in-out;
}
.skeleton-line.w60 { width: 60%; }
.skeleton-line.w80 { width: 80%; }
.skeleton-line.w40 { width: 40%; }
@keyframes skeleton-pulse {
  0%, 100% { opacity: 0.4; }
  50% { opacity: 1; }
}
.classroom-thumb-title {
  font-size: 10px;
  color: #666;
  padding: 3px 6px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  border-top: 1px solid #f0f0f0;
}

/* ClassroomPlayer slide-up transition */
.classroom-slide-enter-active,
.classroom-slide-leave-active {
  transition: transform 0.35s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.25s ease;
}
.classroom-slide-enter-from {
  transform: translateY(100%);
  opacity: 0;
}
.classroom-slide-leave-to {
  transform: translateY(100%);
  opacity: 0;
}
</style>
