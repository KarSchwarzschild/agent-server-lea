<script setup lang="ts">
/**
 * ClassroomPlayer — 课堂模式播放器 (v2 OpenMAIC UI)
 *
 * 整页接管模式：点击「进入课堂」后 slide-up 过渡进入全屏。
 * 布局：Header + Sidebar + Canvas(SlideRenderer + HighlightOverlay) + Toolbar + SpeechBubble
 *
 * 参考：OpenMAIC components/stage.tsx + canvas/canvas-area.tsx
 */
import { ref, computed, watch, provide, onMounted, onBeforeUnmount, nextTick } from 'vue'
import katex from 'katex'
import 'katex/dist/katex.min.css'
import SlideRenderer from './SlideRenderer.vue'
import ClassroomSidebar from './ClassroomSidebar.vue'
import ClassroomToolbar from './ClassroomToolbar.vue'
import ClassroomSpeechBubble from './ClassroomSpeechBubble.vue'
import SlideHighlightOverlay from './SlideHighlightOverlay.vue'
import ClassroomQuizView from './ClassroomQuizView.vue'
import ClassroomDiscussion from './ClassroomDiscussion.vue'
import ClassroomInteractiveView from './ClassroomInteractiveView.vue'
import ClassroomPblView from './ClassroomPblView.vue'
import ClassroomWhiteboardView from './ClassroomWhiteboardView.vue'
import type { DiscussionMessage } from './ClassroomDiscussion.vue'
import ProactiveCard from './ProactiveCard.vue'
import { usePlaybackEngine } from '../../composables/usePlaybackEngine'
import type { PlaybackAction, TriggerEvent } from '../../composables/usePlaybackEngine'
import type { SlideData } from './SlideRenderer.vue'
import { getThemeById } from './pptist-elements/themes'
import { resolveScenePlaybackActions } from './classroomPlaybackActions'

// ─── Types ───

export interface ClassroomScene {
  id: string
  title: string
  scene_type: 'slide' | 'quiz' | 'discussion' | 'interactive' | 'pbl' | 'whiteboard'
  content: SlideData | Record<string, any>
  speaker_notes: string
  audio_file_id: string
  audio_duration_ms: number
  status: string
  scene_index: number
  actions?: PlaybackAction[]
}

export interface ClassroomData {
  session_id: string
  topic: string
  status: string
  first_usable_scene?: number
  can_enter?: boolean
  current_scene_index: number
  total_scenes: number
  scenes: ClassroomScene[]
  config: Record<string, any>
}

// ─── Props & Emits ───

const props = defineProps<{
  data: ClassroomData
  /** 来自 SSE 实时更新的场景数组 */
  liveScenes?: Array<Record<string, any>>
  /** 来自 SSE 的讨论消息（classroomData.discussionMessages） */
  liveDiscussionMessages?: Array<Record<string, any>>
  /** 讨论是否正在流式输出 */
  isDiscussionStreaming?: boolean
}>()

const emit = defineEmits<{
  (e: 'exit'): void
  (e: 'control', action: string, params?: Record<string, any>): void
}>()

// ─── Playback Engine ───

const playback = usePlaybackEngine()
provide('playback', playback)

// ─── State ───

const currentIndex = ref(props.data.current_scene_index || 0)
const autoAdvance = ref(false)
const sidebarVisible = ref(true)
const sidebarRef = ref<InstanceType<typeof ClassroomSidebar> | null>(null)
const slideContainerRef = ref<HTMLElement | null>(null)
const slideDirection = ref<'next' | 'prev'>('next')
const canvasAreaRef = ref<HTMLElement | null>(null)

// ─── 暗色模式（检测系统偏好 + localStorage）───
const isDarkMode = ref((() => {
  try {
    const saved = localStorage.getItem('classroom_dark_mode')
    if (saved !== null) return saved === 'true'
  } catch { /* ignore */ }
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false
})())
// 从 session config 中读取主题
const slideTheme = computed(() => getThemeById(props.data.config?.theme_id))

function toggleDarkMode() {
  isDarkMode.value = !isDarkMode.value
  try { localStorage.setItem('classroom_dark_mode', String(isDarkMode.value)) } catch { /* ignore */ }
}

// ─── Discussion / ProactiveCard 状态 ───
const discussionTrigger = ref<TriggerEvent | null>(null)
const discussionMessages = ref<DiscussionMessage[]>([])
const isDiscussionActive = computed(() => playback.engineMode.value === 'live')

// 连接 PlaybackEngine 讨论回调
playback.onProactiveShow.value = (trigger: TriggerEvent) => {
  discussionTrigger.value = trigger
}
playback.onProactiveHide.value = () => {
  discussionTrigger.value = null
}
playback.onDiscussionConfirmed.value = async (topic: string, prompt?: string) => {
  discussionMessages.value = []
  // SSE 流式获取 AI 教师开场白
  await readDiscussionSSE(
    `/api/user/classroom/${props.data.session_id}/discussion/start`,
    { topic, prompt },
  )
}
playback.onDiscussionEnd.value = () => {
  discussionMessages.value = []
  nextTick(() => playback.play())
}

function handleJoinDiscussion() {
  const trigger = discussionTrigger.value
  if (trigger?.id?.startsWith('scene_disc_')) {
    // Scene-level trigger: 直接进入讨论（不走引擎的 confirmDiscussion）
    playback.setMode('live')
    discussionMessages.value = []
    readDiscussionSSE(
      `/api/user/classroom/${props.data.session_id}/discussion/start`,
      { topic: trigger.question, prompt: trigger.prompt },
    )
    discussionTrigger.value = null
  } else {
    playback.confirmDiscussion()
  }
}

function handleSkipDiscussion() {
  const trigger = discussionTrigger.value
  if (trigger?.id?.startsWith('scene_disc_')) {
    // Scene-level trigger: skip 后跳到下一场景
    discussionTrigger.value = null
    if (canNext.value) next()
  } else {
    playback.skipDiscussion()
    discussionTrigger.value = null
  }
}

async function handleEndDiscussion() {
  // 调后端 API 结束讨论并获取总结
  try {
    const token = localStorage.getItem('token') || ''
    await fetch(`/api/user/classroom/${props.data.session_id}/discussion/end`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    })
  } catch (e) {
    console.warn('[Classroom] 讨论结束请求失败:', e)
  }
  playback.handleEndDiscussion()
  discussionTrigger.value = null
}

async function handleDiscussionSend(text: string) {
  // 添加用户消息（本地立即显示）
  discussionMessages.value.push({
    id: `user_${Date.now()}`,
    role: 'user',
    name: '你',
    content: text,
  })

  // SSE 流式调后端 API，逐 chunk 渲染 AI 回应
  await readDiscussionSSE(
    `/api/user/classroom/${props.data.session_id}/discussion/message`,
    {
      content: text,
      topic: currentScene.value?.title || '',
      history: discussionMessages.value.map(m => ({ role: m.role, content: m.content })),
    },
  )
}

/**
 * 通用 SSE 流式读取：POST 请求 → 逐行解析 SSE → 实时追加 AI 消息
 */
async function readDiscussionSSE(url: string, body: Record<string, any>) {
  const token = localStorage.getItem('token') || ''
  let currentMsg: DiscussionMessage | null = null

  try {
    const resp = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(body),
    })

    if (!resp.ok || !resp.body) return

    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || '' // 保留未完成的行

      // SSE 格式: "event: xxx\ndata: {...}\n\n"
      // 按双换行分割为独立事件块，每块内解析 event + data
      const rawEvents = (lines.join('\n') + '\n').split('\n\n')
      for (const block of rawEvents) {
        if (!block.trim()) continue
        let evtType = ''
        let evtData = ''
        for (const bline of block.split('\n')) {
          if (bline.startsWith('event: ')) evtType = bline.slice(7).trim()
          else if (bline.startsWith('data: ')) evtData = bline.slice(6)
        }
        if (!evtData || evtType === 'done') continue

        try {
          const data = JSON.parse(evtData)

          if (evtType.includes('discussion_message') && data.content) {
            if (data.is_delta && currentMsg) {
              currentMsg.content += data.content
              currentMsg.isDelta = true
            } else if (data.is_delta && !currentMsg) {
              currentMsg = {
                id: `ai_${Date.now()}`,
                role: data.role || 'teacher',
                name: data.name || 'AI 教师',
                content: data.content,
                isDelta: true,
              }
              discussionMessages.value.push(currentMsg)
            } else {
              currentMsg = null
              discussionMessages.value.push({
                id: `ai_${Date.now()}_${Math.random()}`,
                role: data.role || 'teacher',
                name: data.name || 'AI 教师',
                content: data.content,
              })
            }
          }
        } catch { /* 忽略非 JSON */ }
      }
    }

    // 流结束，标记最后一条消息不再是 delta
    if (currentMsg) currentMsg.isDelta = false

  } catch (e) {
    console.warn('[Classroom] SSE 讨论流读取失败:', e)
  }
}

// 同步 SSE 讨论消息到内部状态
watch(
  () => props.liveDiscussionMessages,
  (sseMessages) => {
    if (!sseMessages || sseMessages.length === 0) return
    // SSE 消息中包含 AI 角色的消息，合并到 discussionMessages（去重）
    for (const sseMsg of sseMessages) {
      if (sseMsg.role === 'user') continue // 用户消息已本地添加
      const exists = discussionMessages.value.find(m => m.id === sseMsg.id)
      if (exists) {
        // 增量更新（流式追加）
        exists.content = sseMsg.content as string
        exists.isDelta = !!sseMsg.isDelta
      } else {
        discussionMessages.value.push({
          id: (sseMsg.id as string) || `sse_${Date.now()}`,
          role: (sseMsg.role as 'teacher' | 'agent' | 'user') || 'teacher',
          name: (sseMsg.name as string) || '',
          content: (sseMsg.content as string) || '',
          isDelta: !!sseMsg.isDelta,
        })
      }
    }
  },
  { deep: true },
)
const wrapperStyle = ref<Record<string, string>>({})

function updateWrapperSize() {
  const el = canvasAreaRef.value
  if (!el) return
  const aw = el.clientWidth - 16   // padding 8px × 2
  const ah = el.clientHeight - 16
  if (aw <= 0 || ah <= 0) return
  const R = 16 / 9
  let w: number, h: number
  if (aw / ah > R) {
    h = ah; w = Math.round(h * R)
  } else {
    w = aw; h = Math.round(w / R)
  }
  wrapperStyle.value = { width: w + 'px', height: h + 'px' }
}

// ─── Computed: 合并静态数据 + 实时 SSE 数据 ───

const mergedScenes = computed<ClassroomScene[]>(() => {
  const base = [...props.data.scenes]
  const live = props.liveScenes || []

  // 用 liveScenes 更新 base 中已有的场景
  for (const ls of live) {
    if (!ls) continue
    const idx = ls.scene_index as number
    if (idx >= 0 && idx < base.length) {
      // 更新状态和内容
      if (ls.status) base[idx].status = ls.status as string
      if (ls.content) base[idx].content = ls.content as any
      if (ls.speaker_notes) base[idx].speaker_notes = ls.speaker_notes as string
      if (ls.audio_file_id) base[idx].audio_file_id = ls.audio_file_id as string
      if (ls.actions) base[idx].actions = ls.actions as PlaybackAction[]
    } else if (idx >= base.length) {
      // 新增场景
      base.push({
        id: `scene_${idx}`,
        title: (ls.title as string) || '',
        scene_type: (ls.scene_type as ClassroomScene['scene_type']) || 'slide',
        content: (ls.content as any) || {},
        speaker_notes: (ls.speaker_notes as string) || '',
        audio_file_id: (ls.audio_file_id as string) || '',
        audio_duration_ms: 0,
        status: (ls.status as string) || 'generating',
        scene_index: idx,
        actions: (ls.actions as PlaybackAction[]) || [],
      })
    }
  }
  return base
})

const totalScenes = computed(() => Math.max(props.data.total_scenes, mergedScenes.value.length))

const currentScene = computed<ClassroomScene | null>(() => {
  if (currentIndex.value >= mergedScenes.value.length) return null
  return mergedScenes.value[currentIndex.value]
})

const isSlide = computed(() => currentScene.value?.scene_type === 'slide')
const sceneStatus = computed(() => currentScene.value?.status || 'pending')
const isSceneReady = computed(() => ['content_ready', 'audio_ready', 'ready'].includes(sceneStatus.value))
const isSceneFailed = computed(() => sceneStatus.value === 'failed')
const isSceneLoading = computed(() => ['pending', 'outline_ready', 'generating'].includes(sceneStatus.value))

// ─── Discussion 场景自动触发 ProactiveCard ───
const triggeredDiscussionScenes = new Set<string>()
watch([currentScene, isSceneReady], ([scene, ready]) => {
  if (scene?.scene_type === 'discussion' && ready && !isDiscussionActive.value) {
    const sceneKey = `scene_disc_${scene.scene_index ?? currentIndex.value}`
    if (triggeredDiscussionScenes.has(sceneKey)) return
    triggeredDiscussionScenes.add(sceneKey)
    discussionTrigger.value = {
      id: sceneKey,
      question: (scene.content as any)?.topic || scene.title,
      prompt: (scene.content as any)?.prompt,
    }
  }
}, { immediate: true })

// ─── Zoom 视觉效果 ───
const slideZoomStyle = computed(() => {
  if (!playback.zoomTarget.value) return {}
  const { x = 50, y = 50 } = playback.zoomTarget.value
  return {
    transform: `scale(${playback.zoomScale.value})`,
    transformOrigin: `${x}% ${y}%`,
    transition: 'transform 0.6s cubic-bezier(0.16, 1, 0.3, 1)',
  }
})

const hasCurrentActions = computed(() => {
  return resolveScenePlaybackActions(currentScene.value).length > 0
})

const canPrev = computed(() => currentIndex.value > 0)
const canNext = computed(() => currentIndex.value < totalScenes.value - 1)

// ─── Sidebar scenes format ───

const sidebarScenes = computed(() =>
  mergedScenes.value.map((s, i) => ({
    index: i,
    title: s.title,
    scene_type: s.scene_type,
    status: s.status,
    content: hasSlideContent(s) ? s.content : null,
  }))
)

// ─── Helpers ───

/** 渲染文本中的数学公式：$...$（inline）和 $$...$$（display）— 保留给需要的地方 */
function renderMath(text: string): string {
  if (!text) return ''
  let result = text.replace(/\$\$([\s\S]+?)\$\$/g, (_m, formula) => {
    try { return katex.renderToString(formula.trim(), { displayMode: true, throwOnError: false }) }
    catch { return formula }
  })
  result = result.replace(/\$([^\$]+?)\$/g, (_m, formula) => {
    try { return katex.renderToString(formula.trim(), { displayMode: false, throwOnError: false }) }
    catch { return formula }
  })
  return result
}

function hasSlideContent(scene: ClassroomScene | null): boolean {
  if (!scene?.content) return false
  const c = scene.content as any
  if (c.layout) return true
  if (c.background && c.elements) return true
  if (c.elements?.length > 0 && c.elements[0].left !== undefined) return true
  return false
}

// ─── Navigation ───

function goTo(index: number) {
  if (index < 0 || index >= totalScenes.value) return
  // 场景切换门禁：live 模式下禁止切换（讨论进行中）
  if (playback.engineMode.value === 'live') return
  slideDirection.value = index > currentIndex.value ? 'next' : 'prev'
  currentIndex.value = index
  playback.stop()
  emit('control', 'goto_scene', { scene_index: index })
}

function prev() { if (canPrev.value) goTo(currentIndex.value - 1) }
function next() { if (canNext.value) goTo(currentIndex.value + 1) }

// ─── 场景重新生成 ───

async function handleRegenerateScene(sceneIndex: number) {
  const token = localStorage.getItem('token') || ''
  const sessionId = props.data.session_id
  try {
    const resp = await fetch(`/api/user/classroom/${sessionId}/regenerate-scene/${sceneIndex}`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
    })
    if (!resp.ok) {
      console.error('[Classroom] 重新生成失败:', resp.status)
      sidebarRef.value?.clearRegenerating()
      return
    }
    // 读取 SSE 流，等待完成
    const reader = resp.body?.getReader()
    const decoder = new TextDecoder()
    if (reader) {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        const text = decoder.decode(value, { stream: true })
        // 解析 SSE 事件，更新场景数据
        for (const line of text.split('\n')) {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6))
              // 场景内容生成完成 → 更新本地场景数据
              if (data.scene_index === sceneIndex && data.content) {
                const scene = props.data.scenes?.[sceneIndex]
                if (scene) {
                  scene.content = data.content
                  scene.speaker_notes = data.speaker_notes || ''
                  scene.status = 'content_ready'
                }
              }
              // 音频就绪
              if (data.scene_index === sceneIndex && data.audio_file_id) {
                const scene = props.data.scenes?.[sceneIndex]
                if (scene) {
                  scene.audio_file_id = data.audio_file_id
                  scene.status = 'audio_ready'
                }
              }
            } catch { /* ignore parse errors */ }
          }
        }
      }
    }
    // 重新生成完成 → 刷新课堂状态
    const stateResp = await fetch(`/api/user/classroom/${sessionId}/state`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    if (stateResp.ok) {
      const newState = await stateResp.json()
      // 更新 props.data 中的场景（通过直接赋值，Vue 响应式会自动更新）
      if (newState.scenes) {
        for (let i = 0; i < newState.scenes.length; i++) {
          if (props.data.scenes[i]) {
            Object.assign(props.data.scenes[i], newState.scenes[i])
          }
        }
      }
    }
  } catch (e) {
    console.error('[Classroom] 重新生成异常:', e)
  } finally {
    sidebarRef.value?.clearRegenerating()
  }
}

// ─── Playback ───

function loadCurrentSceneActions() {
  playback.loadScene(resolveScenePlaybackActions(currentScene.value))
}

function togglePlay() {
  if (playback.needsInteraction.value) {
    playback.resume()
    return
  }
  if (playback.isPaused.value) {
    playback.resume()
  } else if (playback.isPlaying.value) {
    playback.pause()
  } else {
    loadCurrentSceneActions()
    playback.play()
  }
}

// Auto-advance when scene finishes
playback.onEnded.value = () => {
  if (autoAdvance.value && canNext.value) {
    setTimeout(() => {
      next()
      // next() 会触发 watch(currentIndex) → loadCurrentSceneActions()
      // 等 DOM 更新后自动播放新场景
      nextTick(() => playback.play())
    }, 1500)
  }
}

// Load actions when scene changes
watch(currentIndex, () => {
  loadCurrentSceneActions()
})

// ─── Keyboard ───

function onKeyDown(e: KeyboardEvent) {
  // 输入框聚焦时不触发
  if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
  // 修饰键按下时不触发（避免和系统快捷键冲突）
  if (e.ctrlKey || e.metaKey || e.altKey) return

  switch (e.key) {
    case 'ArrowLeft':
      if (playback.engineMode.value === 'live') break // 讨论中禁止翻页
      e.preventDefault()
      prev()
      break
    case 'ArrowRight':
      if (playback.engineMode.value === 'live') break
      e.preventDefault()
      next()
      break
    case ' ':
    case 'Spacebar':
      // live 模式下不触发（由讨论系统管理）
      if (playback.isLive.value) break
      e.preventDefault()
      togglePlay()
      break
    case 'Escape':
      if (document.fullscreenElement) {
        document.exitFullscreen()
      } else {
        emit('exit')
      }
      break
    case 'f':
    case 'F':
      toggleFullscreen()
      break
    case 'ArrowUp':
      e.preventDefault()
      playback.setVolume(Math.min(1, playback.volume.value + 0.1))
      break
    case 'ArrowDown':
      e.preventDefault()
      playback.setVolume(Math.max(0, playback.volume.value - 0.1))
      break
    case 'm':
    case 'M':
      e.preventDefault()
      playback.toggleMute()
      break
    case 's':
    case 'S':
      e.preventDefault()
      sidebarVisible.value = !sidebarVisible.value
      break
  }
}

// ─── Fullscreen ───

const playerRef = ref<HTMLElement | null>(null)

function toggleFullscreen() {
  if (document.fullscreenElement) {
    document.exitFullscreen()
  } else {
    playerRef.value?.requestFullscreen()
  }
}

// ─── Lifecycle ───

let _ro: ResizeObserver | null = null

onMounted(async () => {
  window.addEventListener('keydown', onKeyDown)
  loadCurrentSceneActions()
  if (canvasAreaRef.value) {
    _ro = new ResizeObserver(updateWrapperSize)
    _ro.observe(canvasAreaRef.value)
    updateWrapperSize()
  }

  // 恢复讨论历史（刷新后）
  try {
    const token = localStorage.getItem('token') || ''
    const resp = await fetch(`/api/user/classroom/${props.data.session_id}/discussion/history`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    if (resp.ok) {
      const data = await resp.json()
      if (data.messages?.length > 0) {
        discussionMessages.value = data.messages
      }
    }
  } catch { /* 静默失败 */ }
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeyDown)
  _ro?.disconnect()
  playback.stop()
})

watch(() => props.data.current_scene_index, (v) => {
  if (v !== undefined) currentIndex.value = v
})
</script>

<template>
  <div ref="playerRef" class="classroom-player" :class="{ dark: isDarkMode }">
    <!-- ═══ Header ═══ -->
    <header class="cp-header">
      <button class="cp-exit-btn" @click="emit('exit')">← 退出课堂</button>
      <span class="cp-topic">{{ data.topic }}</span>
      <span class="cp-page">{{ currentIndex + 1 }} / {{ totalScenes }}</span>
    </header>

    <div class="cp-body">
      <!-- ═══ Sidebar ═══ -->
      <ClassroomSidebar
        v-if="sidebarVisible"
        ref="sidebarRef"
        :scenes="sidebarScenes"
        :current-index="currentIndex"
        :theme="slideTheme"
        @select="goTo"
        @retry="(idx: number) => emit('control', 'retry_scene', { scene_index: idx })"
        @regenerate="handleRegenerateScene"
      />

      <!-- ═══ Main Content ═══ -->
      <div class="cp-main">
        <div ref="canvasAreaRef" class="cp-canvas-area">
          <!-- 场景编号水印（canvas-area 内、slide 外）-->
          <div class="cp-watermark" aria-hidden="true">
            {{ String(currentIndex + 1).padStart(2, '0') }}
          </div>

          <!-- Slide 渲染 -->
          <Transition :name="`slide-${slideDirection}`" mode="out-in">
            <div
              v-if="currentScene"
              :key="currentIndex"
              class="cp-slide-wrapper"
              :style="wrapperStyle"
            >
              <!-- 正常渲染 -->
              <div
                v-if="isSlide && isSceneReady && hasSlideContent(currentScene)"
                ref="slideContainerRef"
                class="cp-slide-card"
                :style="slideZoomStyle"
                @click="togglePlay"
              >
                <SlideRenderer
                  :slide="currentScene.content as any"
                  :theme="slideTheme"
                  mode="full"
                />
                <!-- Spotlight Overlay -->
                <SlideHighlightOverlay :container-ref="slideContainerRef" />
              </div>

              <!-- Quiz（ClassroomQuizView 4 态组件） -->
              <ClassroomQuizView
                v-else-if="currentScene.scene_type === 'quiz' && isSceneReady"
                :scene="currentScene"
              />

              <!-- Discussion（仅在 engine mode === 'live' 时显示） -->
              <ClassroomDiscussion
                v-else-if="isDiscussionActive"
                :topic="(currentScene.content as any)?.topic || currentScene.title"
                :prompt="(currentScene.content as any)?.prompt"
                :messages="discussionMessages"
                :isStreaming="isDiscussionStreaming"
                :sessionId="props.data.session_id"
                @send="handleDiscussionSend"
              />

              <!-- Discussion 等待确认（ProactiveCard 在上层覆盖） -->
              <div v-else-if="currentScene.scene_type === 'discussion' && isSceneReady" class="cp-state-card">
                <div style="font-size:48px; margin-bottom:12px">💬</div>
                <p>{{ (currentScene.content as any)?.topic || currentScene.title }}</p>
              </div>

              <!-- Interactive widgets -->
              <ClassroomInteractiveView
                v-else-if="currentScene.scene_type === 'interactive' && isSceneReady && (currentScene.content as any)?.kind === 'interactive_widget'"
                :scene="currentScene"
              />
              <div
                v-else-if="currentScene.scene_type === 'interactive' && isSceneReady"
                class="cp-state-card"
                role="status"
                aria-live="polite"
              >
                <h3>{{ currentScene.title }}</h3>
                <p>{{ (currentScene.content as any)?.display_message || '此互动内容暂以说明卡展示，当前组件类型尚未启用。' }}</p>
              </div>

              <!-- PBL project views -->
              <ClassroomPblView
                v-else-if="currentScene.scene_type === 'pbl' && isSceneReady && ['pbl_project_skeleton', 'pbl_workspace'].includes((currentScene.content as any)?.kind)"
                :scene="currentScene"
              />
              <div
                v-else-if="currentScene.scene_type === 'pbl' && isSceneReady"
                class="cp-state-card"
                role="status"
                aria-live="polite"
              >
                <h3>{{ currentScene.title }}</h3>
                <p>{{ (currentScene.content as any)?.display_message || '此项目式学习内容暂以项目说明展示，当前内容类型尚未启用。' }}</p>
              </div>

              <ClassroomWhiteboardView
                v-else-if="currentScene.scene_type === 'whiteboard' && isSceneReady && (currentScene.content as any)?.kind === 'whiteboard'"
                :scene="currentScene"
              />

              <!-- Loading -->
              <div v-else-if="isSceneLoading" class="cp-state-card">
                <div class="cp-spinner" />
                <p>场景生成中...</p>
              </div>

              <!-- Failed -->
              <div v-else-if="isSceneFailed" class="cp-state-card cp-state-failed">
                <div class="cp-fail-icon">⚠️</div>
                <p>生成失败</p>
                <button
                  class="cp-retry-btn"
                  @click="emit('control', 'retry_scene', { scene_index: currentIndex })"
                >
                  重试生成
                </button>
              </div>

              <!-- Fallback -->
              <div v-else class="cp-state-card">
                <p>{{ currentScene.title }}</p>
              </div>
            </div>
          </Transition>

          <!-- ProactiveCard 讨论触发卡片 -->
          <Transition name="fade">
            <div v-if="discussionTrigger" class="cp-proactive-wrapper">
              <ProactiveCard
                :trigger="discussionTrigger"
                @join="handleJoinDiscussion"
                @skip="handleSkipDiscussion"
              />
            </div>
          </Transition>

          <!-- Play button overlay: 仅 slide 且有 actions 时显示 -->
          <Transition name="fade">
            <div
              v-if="isSlide && hasCurrentActions && (!playback.isPlaying.value || playback.isPaused.value)"
              class="cp-play-overlay"
              @click="togglePlay"
            >
              <div class="cp-play-btn" :class="{ 'cp-play-btn--breathe': !playback.isPlaying.value }">
                ▶
              </div>
            </div>
          </Transition>

          <!-- Autoplay blocked hint (非阻塞小提示) -->
          <Transition name="fade">
            <div
              v-if="playback.needsInteraction.value"
              class="cp-autoplay-toast"
              @click="playback.needsInteraction.value = false"
            >
              🔇 浏览器已阻止自动播放音频，点击 slide 区域后可手动播放
            </div>
          </Transition>

        </div>

        <!-- ═══ Toolbar ═══ -->
        <ClassroomToolbar
          :current-index="currentIndex"
          :total-scenes="totalScenes"
          :auto-advance="autoAdvance"
          :sidebar-visible="sidebarVisible"
          :is-dark-mode="isDarkMode"
          @prev="prev"
          @next="next"
          @toggle-auto="autoAdvance = !autoAdvance"
          @toggle-sidebar="sidebarVisible = !sidebarVisible"
          @toggle-fullscreen="toggleFullscreen"
          @end-discussion="handleEndDiscussion"
          @toggle-dark="toggleDarkMode"
        />
        <!-- ═══ Speech Bubble（Toolbar 下方，flow 布局）═══ -->
        <ClassroomSpeechBubble />
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ═══ 暗色模式 CSS 变量 ═══ */
.classroom-player {
  --cp-bg: #f9fafb;
  --cp-surface: rgba(255, 255, 255, 0.85);
  --cp-text: #333;
  --cp-text-secondary: #666;
  --cp-text-muted: #999;
  --cp-border: rgba(0, 0, 0, 0.06);
  --cp-overlay: rgba(0, 0, 0, 0.5);
  --cp-card-bg: white;
  --cp-card-border: #e5e7eb;
}

.classroom-player.dark {
  --cp-bg: #111827;
  --cp-surface: rgba(17, 24, 39, 0.9);
  --cp-text: #e5e7eb;
  --cp-text-secondary: #9ca3af;
  --cp-text-muted: #6b7280;
  --cp-border: rgba(255, 255, 255, 0.08);
  --cp-overlay: rgba(0, 0, 0, 0.7);
  --cp-card-bg: #1f2937;
  --cp-card-border: #374151;
}

/* ═══ Root ═══ */
.classroom-player {
  position: fixed;
  inset: 0;
  z-index: 1000;
  background: var(--cp-bg);
  display: flex;
  flex-direction: column;
  font-family: system-ui, -apple-system, 'Segoe UI', sans-serif;
  color: var(--cp-text);
}

/* ═══ Header ═══ */
.cp-header {
  height: 56px;
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 0 20px;
  background: var(--cp-surface);
  -webkit-backdrop-filter: blur(24px);
  backdrop-filter: blur(24px);
  border-bottom: 1px solid var(--cp-border);
  flex-shrink: 0;
  z-index: 10;
}

@supports not (backdrop-filter: blur(1px)) {
  .cp-header { background: rgba(255, 255, 255, 0.95); }
}

.cp-exit-btn {
  border: none;
  background: transparent;
  font-size: 14px;
  color: #555;
  cursor: pointer;
  padding: 6px 12px;
  border-radius: 6px;
  transition: background 0.15s;
}

.cp-exit-btn:hover {
  background: rgba(0, 0, 0, 0.05);
}

.cp-topic {
  font-weight: 600;
  font-size: 15px;
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: #222;
}

.cp-page {
  color: #888;
  font-size: 13px;
  flex-shrink: 0;
}

/* ═══ Body ═══ */
.cp-body {
  flex: 1;
  display: flex;
  overflow: hidden;
}

/* ═══ Main ═══ */
.cp-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* ═══ Canvas Area ═══ */
.cp-canvas-area {
  flex: 1;
  min-height: 0;
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 8px;
  overflow: hidden;
  background: rgba(249, 250, 251, 0.3);
}

/* ─── Scene watermark（canvas-area 右上角，slide 外面）─── */
.cp-watermark {
  position: absolute;
  right: 16px;
  top: 8px;
  font-weight: 900;
  font-size: 48px;
  color: rgba(0, 0, 0, 0.06);
  pointer-events: none;
  z-index: 1;
  user-select: none;
  line-height: 1;
}

/* ─── Slide wrapper (JS contain 定位容器，视觉样式由内部 viewport 承担) ─── */
.cp-slide-wrapper {
  position: relative;
  overflow: visible;
  flex-shrink: 0;
}

.cp-slide-card {
  position: absolute;
  inset: 0;
  cursor: pointer;
  overflow: hidden;
  transition: transform 0.6s cubic-bezier(0.16, 1, 0.3, 1);
}

/* SlideRenderer 在 ClassroomPlayer 内：铺满 wrapper */
.cp-slide-card :deep(.slide-canvas) {
  aspect-ratio: unset;
  width: 100%;
  height: 100%;
}

/* ─── Play button overlay ─── */
.cp-play-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 30;
  pointer-events: none;
}

.cp-play-btn {
  width: 80px;
  height: 80px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.95);
  box-shadow: 0 4px 30px rgba(147, 51, 234, 0.15);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 28px;
  color: #7c3aed;
  pointer-events: auto;
  cursor: pointer;
  transition: transform 0.2s, box-shadow 0.2s;
}

.cp-play-btn:hover {
  transform: scale(1.08);
  box-shadow: 0 8px 40px rgba(147, 51, 234, 0.25);
}

.cp-play-btn--breathe {
  animation: breathe 2.5s ease-in-out infinite;
}

@keyframes breathe {
  0%, 100% { transform: scale(1); }
  50% { transform: scale(1.06); }
}

/* ─── Autoplay toast ─── */
.cp-autoplay-toast {
  position: absolute;
  top: 16px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 60;
  background: rgba(0, 0, 0, 0.7);
  color: white;
  padding: 8px 20px;
  border-radius: 8px;
  font-size: 13px;
  cursor: pointer;
  white-space: nowrap;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
}

.cp-interaction-card span {
  font-size: 36px;
}

.cp-interaction-card p {
  margin: 8px 0 0;
  font-size: 16px;
  color: #333;
}

/* ─── State cards (loading/failed) ─── */
.cp-state-card {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  padding: 40px;
  background: white;
}

.cp-state-card p {
  color: #666;
  font-size: 15px;
  margin: 0;
}

.cp-state-failed p {
  color: #dc2626;
}

.cp-fail-icon {
  font-size: 48px;
}

.cp-retry-btn {
  border: 2px solid #dc2626;
  color: #dc2626;
  background: transparent;
  padding: 8px 20px;
  border-radius: 8px;
  font-size: 14px;
  cursor: pointer;
  transition: all 0.2s;
}

.cp-retry-btn:hover {
  background: #dc2626;
  color: white;
}

/* ─── Spinner ─── */
.cp-spinner {
  width: 32px;
  height: 32px;
  border: 3px solid rgba(124, 58, 237, 0.2);
  border-top-color: #7c3aed;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* ─── ProactiveCard wrapper ─── */
.cp-proactive-wrapper {
  position: absolute;
  bottom: 16px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 60;
  max-width: 420px;
  width: calc(100% - 32px);
}

/* ═══ Transitions ═══ */
.slide-next-enter-active,
.slide-next-leave-active,
.slide-prev-enter-active,
.slide-prev-leave-active {
  transition: all 0.35s cubic-bezier(0.21, 1, 0.36, 1);
}

.slide-next-enter-from { opacity: 0; transform: translateX(40px); }
.slide-next-leave-to { opacity: 0; transform: translateX(-40px); }
.slide-prev-enter-from { opacity: 0; transform: translateX(-40px); }
.slide-prev-leave-to { opacity: 0; transform: translateX(40px); }

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.3s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

/* ═══ Mobile ═══ */
@media (max-width: 768px) {
  .cp-canvas-area {
    padding: 4px;
  }
  .cp-header {
    height: 48px;
    padding: 0 12px;
  }
  .cp-watermark {
    font-size: 32px;
    right: 8px;
    top: 8px;
  }
  .cp-play-btn {
    width: 60px;
    height: 60px;
    font-size: 22px;
  }
}
</style>
