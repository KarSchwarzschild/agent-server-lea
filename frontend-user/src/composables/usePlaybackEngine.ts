/**
 * usePlaybackEngine — 课堂播放引擎 v2
 *
 * 4 态状态机（对齐 OpenMAIC lib/playback/engine.ts）：
 *   idle → playing ↔ paused
 *   playing → live (via confirmDiscussion / handleUserInterrupt)
 *   live ↔ paused
 *   live → idle (via handleEndDiscussion)
 *
 * Action 类型：
 *   spotlight  → 非阻塞（设置高亮 ID）
 *   speech     → 阻塞（音频/阅读时间）
 *   discussion → 3s 延迟触发 ProactiveCard，引擎暂停等待
 *   highlight  → 非阻塞（多元素高亮，P8 预留）
 *   laser      → 非阻塞（激光笔，P8 预留）
 *   zoom       → 非阻塞（缩放聚焦，P8 预留）
 */
import { ref, computed, onUnmounted, type Ref } from 'vue'

// ─── 类型定义 ───

export type EngineMode = 'idle' | 'playing' | 'paused' | 'live'

export interface PlaybackAction {
  type: 'spotlight' | 'speech' | 'discussion' | 'highlight' | 'laser' | 'zoom'
  // spotlight
  elementId?: string
  dimness?: number
  // speech
  text?: string
  audioFileId?: string
  audioDurationMs?: number
  // discussion
  id?: string
  topic?: string
  prompt?: string
  agentId?: string
  // highlight (P8)
  elementIds?: string[]
  color?: string
  // zoom (P8)
  scale?: number
  // fallback metadata
  fallbackReason?: string
}

export interface SpotlightOptions {
  dimness: number
}

export interface TriggerEvent {
  id: string
  question: string
  prompt?: string
  agentId?: string
}

export interface UsePlaybackEngineReturn {
  // 状态
  engineMode: Ref<EngineMode>
  isPlaying: Ref<boolean>
  isPaused: Ref<boolean>
  isLive: Ref<boolean>
  spotlightElementId: Ref<string | null>
  spotlightOptions: Ref<SpotlightOptions | null>
  zoomTarget: Ref<{ elementId?: string; x?: number; y?: number } | null>
  zoomScale: Ref<number>
  currentSpeechText: Ref<string>
  currentActionIndex: Ref<number>
  totalActions: Ref<number>
  playbackSpeed: Ref<number>
  needsInteraction: Ref<boolean>
  volume: Ref<number>
  isMuted: Ref<boolean>
  onEnded: Ref<(() => void) | null>

  // 回调注册
  onProactiveShow: Ref<((trigger: TriggerEvent) => void) | null>
  onProactiveHide: Ref<(() => void) | null>
  onDiscussionConfirmed: Ref<((topic: string, prompt?: string, agentId?: string) => void) | null>
  onDiscussionEnd: Ref<(() => void) | null>
  onModeChange: Ref<((mode: EngineMode) => void) | null>

  // 方法
  loadScene: (actions: PlaybackAction[]) => void
  play: () => void
  pause: () => void
  resume: () => void
  stop: () => void
  setSpeed: (speed: number) => void
  setVolume: (v: number) => void
  toggleMute: () => void
  confirmDiscussion: () => void
  skipDiscussion: () => void
  handleUserInterrupt: (text: string) => void
  handleEndDiscussion: () => void
  setMode: (mode: EngineMode) => void
}

export function usePlaybackEngine(): UsePlaybackEngineReturn {
  // ─── 响应式状态 ───
  const engineMode = ref<EngineMode>('idle')
  const isPlaying = computed(() => engineMode.value === 'playing')
  const isPaused = computed(() => engineMode.value === 'paused')
  const isLive = computed(() => engineMode.value === 'live')

  const spotlightElementId = ref<string | null>(null)
  const spotlightOptions = ref<SpotlightOptions | null>(null)
  const zoomTarget = ref<{ elementId?: string; x?: number; y?: number } | null>(null)
  const zoomScale = ref(1)
  const currentSpeechText = ref('')
  const currentActionIndex = ref(0)
  const totalActions = ref(0)
  // 从 localStorage 恢复设置
  const _saved = (() => {
    try {
      const s = localStorage.getItem('classroom_settings')
      return s ? JSON.parse(s) : {}
    } catch { return {} }
  })()
  const playbackSpeed = ref(_saved.speed ?? 1)
  const needsInteraction = ref(false)
  const volume = ref(_saved.volume ?? 1)
  const isMuted = ref(_saved.muted ?? false)
  const onEnded = ref<(() => void) | null>(null)

  // 回调
  const onProactiveShow = ref<((trigger: TriggerEvent) => void) | null>(null)
  const onProactiveHide = ref<(() => void) | null>(null)
  const onDiscussionConfirmed = ref<((topic: string, prompt?: string, agentId?: string) => void) | null>(null)
  const onDiscussionEnd = ref<(() => void) | null>(null)
  const onModeChange = ref<((mode: EngineMode) => void) | null>(null)

  // ─── 内部状态 ───
  const actions = ref<PlaybackAction[]>([])
  const audioEl = new Audio()
  let readingTimerHandle: ReturnType<typeof setTimeout> | null = null
  let readingTimerStartedAt = 0
  let readingTimerRemainingMs = 0
  let readingTimerReject: (() => void) | null = null

  // 讨论状态
  const consumedDiscussions = new Set<string>()
  let savedSceneIndex: number | null = null
  let savedActionIndex: number | null = null
  let currentTrigger: TriggerEvent | null = null
  let triggerDelayTimer: ReturnType<typeof setTimeout> | null = null
  let pendingDiscussionResolve: (() => void) | null = null
  let previousModeBeforePause: EngineMode | null = null

  function setMode(mode: EngineMode) {
    engineMode.value = mode
    onModeChange.value?.(mode)
  }

  // ─── 核心播放循环 ───

  async function runPlaybackLoop() {
    try {
      while (currentActionIndex.value < actions.value.length) {
        if (engineMode.value !== 'playing') return

        const action = actions.value[currentActionIndex.value]
        currentActionIndex.value++

        // Spotlight — 非阻塞
        if (action.type === 'spotlight') {
          spotlightElementId.value = action.elementId || null
          spotlightOptions.value = action.elementId
            ? { dimness: action.dimness ?? 0.7 }
            : null
          continue
        }

        // Speech — 阻塞
        if (action.type === 'speech') {
          currentSpeechText.value = action.text || ''
          if (action.audioFileId) {
            try {
              await playAudio(action.audioFileId)
            } catch {
              await waitReading(action.text || '', action.audioDurationMs)
            }
          } else {
            await waitReading(action.text || '', action.audioDurationMs)
          }
          if (engineMode.value !== 'playing') return
          continue
        }

        // Discussion — 3s 延迟触发 ProactiveCard
        if (action.type === 'discussion') {
          const discId = action.id || `disc_${currentActionIndex.value}`
          if (consumedDiscussions.has(discId)) continue

          // 3s 延迟后触发 ProactiveCard，引擎等待 confirm/skip resolve
          await new Promise<void>((resolve) => {
            triggerDelayTimer = setTimeout(() => {
              triggerDelayTimer = null
              if (engineMode.value !== 'playing') { resolve(); return }

              currentTrigger = {
                id: discId,
                question: action.topic || action.text || '',
                prompt: action.prompt,
                agentId: action.agentId,
              }
              // 存储 resolve，等 confirmDiscussion/skipDiscussion 调用
              pendingDiscussionResolve = resolve
              onProactiveShow.value?.(currentTrigger)
            }, 3000)
          })

          // confirm/skip resolve 后到这里
          if (engineMode.value !== 'playing') return
          continue
        }

        // Highlight/Laser — 非阻塞 (P8 预留)
        if (action.type === 'highlight' || action.type === 'laser') {
          continue
        }

        // Zoom — CSS transform 缩放效果
        if (action.type === 'zoom') {
          zoomTarget.value = {
            elementId: action.elementId,
            x: action.x ?? 50,
            y: action.y ?? 50,
          }
          zoomScale.value = action.scale || 2
          await new Promise<void>((resolve) => {
            setTimeout(resolve, action.duration_ms || 3000)
          })
          zoomTarget.value = null
          zoomScale.value = 1
          if (engineMode.value !== 'playing') return
          continue
        }

        // 未知 action type → skip + 日志（数据库兼容性）
        console.warn(`[PlaybackEngine] Unknown action type: ${action.type}, skipping`)
      }

      // 所有 action 完成
      setMode('idle')
      currentSpeechText.value = ''
      onEnded.value?.()
    } catch {
      return
    }
  }

  async function playAudio(fileId: string): Promise<void> {
    const token = localStorage.getItem('token') || ''
    const resp = await fetch(`/api/user/super-agent/files/${fileId}/download`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    if (!resp.ok) throw new Error(`Audio download failed: ${resp.status}`)

    const blob = await resp.blob()
    const blobUrl = URL.createObjectURL(blob)

    return new Promise((resolve, reject) => {
      audioEl.src = blobUrl
      audioEl.playbackRate = playbackSpeed.value
      audioEl.volume = isMuted.value ? 0 : volume.value

      audioEl.onended = () => { URL.revokeObjectURL(blobUrl); resolve() }
      audioEl.onerror = () => { URL.revokeObjectURL(blobUrl); reject(new Error('Audio play failed')) }

      audioEl.play().catch((err: Error) => {
        URL.revokeObjectURL(blobUrl)
        if (err.name === 'NotAllowedError') {
          needsInteraction.value = true
          resolve()
          return
        }
        reject(err)
      })
    })
  }

  function waitReading(text: string, durationMs?: number): Promise<void> {
    return new Promise((resolve, reject) => {
      // CJK 检测（参考 OpenMAIC: cjkCount/length > 0.3 → 150ms/字符, 否则 240ms/词）
      const cjkCount = (text.match(/[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]/g) || []).length
      const isCJK = cjkCount > text.length * 0.3
      const rawMs = durationMs || (isCJK
        ? Math.max(2000, text.length * 150)
        : Math.max(2000, text.split(/\s+/).filter(Boolean).length * 240))
      const adjustedMs = rawMs / playbackSpeed.value

      readingTimerRemainingMs = adjustedMs
      readingTimerStartedAt = Date.now()
      readingTimerReject = reject

      readingTimerHandle = setTimeout(() => {
        readingTimerHandle = null
        readingTimerRemainingMs = 0
        readingTimerReject = null
        resolve()
      }, adjustedMs)
    })
  }

  // ─── 公开方法 ───

  function loadScene(newActions: PlaybackAction[]) {
    stop()
    actions.value = newActions
    totalActions.value = newActions.length
    currentActionIndex.value = 0
    spotlightElementId.value = null
    spotlightOptions.value = null
    currentSpeechText.value = ''
  }

  function play() {
    if (engineMode.value === 'paused') {
      resume()
      return
    }
    needsInteraction.value = false
    currentActionIndex.value = 0
    spotlightElementId.value = null
    spotlightOptions.value = null
    currentSpeechText.value = ''
    setMode('playing')
    runPlaybackLoop()
  }

  function pause() {
    previousModeBeforePause = engineMode.value
    setMode('paused')
    audioEl.pause()

    if (readingTimerHandle) {
      clearTimeout(readingTimerHandle)
      readingTimerHandle = null
      const elapsed = Date.now() - readingTimerStartedAt
      readingTimerRemainingMs = Math.max(readingTimerRemainingMs - elapsed, 0)
    }
  }

  function resume() {
    if (engineMode.value !== 'paused') return
    needsInteraction.value = false

    // 恢复到暂停前的模式
    const restoreTo = previousModeBeforePause === 'live' ? 'live' : 'playing'
    previousModeBeforePause = null
    setMode(restoreTo)

    if (restoreTo === 'live') return // live 模式由讨论系统管理

    // 恢复音频播放
    if (audioEl.src && audioEl.paused && !audioEl.ended) {
      audioEl.play().catch(() => { needsInteraction.value = true })
      return
    }

    // 恢复阅读定时器
    if (readingTimerRemainingMs > 0) {
      readingTimerStartedAt = Date.now()
      readingTimerHandle = setTimeout(() => {
        readingTimerHandle = null
        readingTimerRemainingMs = 0
        runPlaybackLoop()
      }, readingTimerRemainingMs)
      return
    }

    runPlaybackLoop()
  }

  function stop() {
    setMode('idle')
    audioEl.pause()
    audioEl.src = ''
    if (readingTimerHandle) { clearTimeout(readingTimerHandle); readingTimerHandle = null }
    if (readingTimerReject) { readingTimerReject(); readingTimerReject = null }
    if (triggerDelayTimer) { clearTimeout(triggerDelayTimer); triggerDelayTimer = null }
    if (pendingDiscussionResolve) { pendingDiscussionResolve(); pendingDiscussionResolve = null }
    readingTimerRemainingMs = 0
    spotlightElementId.value = null
    spotlightOptions.value = null
    currentSpeechText.value = ''
    currentActionIndex.value = 0
    currentTrigger = null
    previousModeBeforePause = null
  }

  function _saveSettings() {
    try {
      localStorage.setItem('classroom_settings', JSON.stringify({
        speed: playbackSpeed.value,
        volume: volume.value,
        muted: isMuted.value,
      }))
    } catch { /* ignore */ }
  }

  function setSpeed(speed: number) {
    playbackSpeed.value = speed
    audioEl.playbackRate = speed
    _saveSettings()
  }

  function setVolume(v: number) {
    volume.value = Math.max(0, Math.min(1, v))
    audioEl.volume = isMuted.value ? 0 : volume.value
    _saveSettings()
  }

  function toggleMute() {
    isMuted.value = !isMuted.value
    audioEl.volume = isMuted.value ? 0 : volume.value
    _saveSettings()
  }

  // ─── 讨论控制（P5 核心）───

  function confirmDiscussion() {
    if (!currentTrigger) return

    consumedDiscussions.add(currentTrigger.id)

    // 保存讲座位置（actionIndex 已指向 discussion 之后）
    savedSceneIndex = 0
    savedActionIndex = currentActionIndex.value

    const trigger = currentTrigger
    currentTrigger = null

    // 先设 mode 再停 audio（防止竞态：onended 触发 processNext）
    setMode('live')
    audioEl.pause()

    // resolve 挂起的 discussion Promise（loop 不再继续，因为 mode 已是 live）
    if (pendingDiscussionResolve) { pendingDiscussionResolve(); pendingDiscussionResolve = null }

    onProactiveHide.value?.()
    onDiscussionConfirmed.value?.(trigger.question, trigger.prompt, trigger.agentId)
  }

  function skipDiscussion() {
    if (currentTrigger) {
      consumedDiscussions.add(currentTrigger.id)
      currentTrigger = null
    }
    onProactiveHide.value?.()

    // resolve 挂起的 discussion Promise → loop 自然继续到下一个 action
    if (pendingDiscussionResolve) { pendingDiscussionResolve(); pendingDiscussionResolve = null }
  }

  function handleUserInterrupt(text: string) {
    if (engineMode.value === 'playing' || engineMode.value === 'paused') {
      // 保存位置（actionIndex 已递增，减 1 重新播放被中断的 action）
      if (savedActionIndex === null) {
        savedSceneIndex = 0
        savedActionIndex = Math.max(0, currentActionIndex.value - 1)
      }

      // 清除讨论延迟
      if (triggerDelayTimer) {
        clearTimeout(triggerDelayTimer)
        triggerDelayTimer = null
      }
    }

    // 先设 mode 再停 audio
    setMode('live')
    audioEl.pause()
    currentSpeechText.value = ''
  }

  function handleEndDiscussion() {
    spotlightElementId.value = null
    spotlightOptions.value = null
    currentSpeechText.value = ''

    onDiscussionEnd.value?.()

    // 恢复讲座位置
    if (savedActionIndex !== null) {
      currentActionIndex.value = savedActionIndex
      savedActionIndex = null
      savedSceneIndex = null
    }

    setMode('idle')
  }

  // ─── 清理 ───
  onUnmounted(() => { stop() })

  return {
    engineMode,
    isPlaying: isPlaying as unknown as Ref<boolean>,
    isPaused: isPaused as unknown as Ref<boolean>,
    isLive: isLive as unknown as Ref<boolean>,
    spotlightElementId,
    spotlightOptions,
    zoomTarget,
    zoomScale,
    currentSpeechText,
    currentActionIndex,
    totalActions,
    playbackSpeed,
    needsInteraction,
    volume,
    isMuted,
    onEnded,
    onProactiveShow,
    onProactiveHide,
    onDiscussionConfirmed,
    onDiscussionEnd,
    onModeChange,
    loadScene,
    play,
    pause,
    resume,
    stop,
    setSpeed,
    setVolume,
    toggleMute,
    confirmDiscussion,
    skipDiscussion,
    handleUserInterrupt,
    handleEndDiscussion,
    setMode,
  }
}
