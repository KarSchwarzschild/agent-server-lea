/**
 * Guide 学习指导状态 Store
 *
 * 管理交互式学习指导的进度、快捷回复等状态。
 * 生命周期跟随会话，不做 localStorage 持久化。
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export interface GuideState {
  phase: string          // 'locating' | 'learning' | 'summary'
  current_index: number  // 当前知识点序号（从 1 开始）
  total: number          // 知识点总数
  current_topic: string  // 当前知识点名称
  mastery: Record<string, string>  // 各知识点掌握情况
}

export const useSuperAgentGuideStore = defineStore('superAgentGuide', () => {
  // 学习进度
  const guideState = ref<GuideState | null>(null)

  // 快捷回复建议
  const suggestedReplies = ref<string[]>([])

  // 计算属性
  const progress = computed(() => {
    if (!guideState.value || guideState.value.total <= 0) return 0
    return Math.round((guideState.value.current_index / guideState.value.total) * 100)
  })

  const progressText = computed(() => {
    if (!guideState.value) return ''
    return `${guideState.value.current_index}/${guideState.value.total}`
  })

  const isGuideActive = computed(() => guideState.value !== null)

  const currentTopic = computed(() => guideState.value?.current_topic || '')

  /** 从 SSE skill_state 事件更新状态 */
  function updateFromSSE(data: { guide_state?: GuideState; state?: any; suggested_replies?: string[] }) {
    const stateData = data.guide_state ?? data.state  // 优先旧字段，兼容新字段
    if (stateData) {
      guideState.value = stateData
    }
    if (data.suggested_replies) {
      suggestedReplies.value = data.suggested_replies
    }
  }

  /** 重置所有状态 */
  function reset() {
    guideState.value = null
    suggestedReplies.value = []
  }

  /** 只清除 guide 进度，保留 suggestedReplies（供 conversational skill 使用） */
  function clearGuideState() {
    guideState.value = null
  }

  return {
    guideState,
    suggestedReplies,
    progress,
    progressText,
    isGuideActive,
    currentTopic,
    updateFromSSE,
    reset,
    clearGuideState,
  }
})
