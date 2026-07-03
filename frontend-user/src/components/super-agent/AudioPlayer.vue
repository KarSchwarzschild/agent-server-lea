<script setup lang="ts">
/**
 * AudioPlayer — TTS 音频播放组件
 *
 * 三种状态：
 * 1. loading  — TTS 生成中（shimmer 骨架屏）
 * 2. ready    — 就绪/播放中/暂停
 * 3. error    — 加载失败（重试按钮）
 *
 * 使用：
 *   <AudioPlayer file-id="tts_xxx" />
 *   <AudioPlayer :src="blobUrl" />
 *   <AudioPlayer file-id="tts_xxx" loading />
 */
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'

const props = defineProps<{
  fileId?: string
  src?: string
  loading?: boolean
}>()

const emit = defineEmits<{
  (e: 'play'): void
  (e: 'pause'): void
  (e: 'ended'): void
  (e: 'error', err: string): void
}>()

type PlayerState = 'loading' | 'ready' | 'playing' | 'paused' | 'error'

const state = ref<PlayerState>('loading')
const errorMsg = ref('')
const currentTime = ref(0)
const duration = ref(0)
const speedIndex = ref(0)
const speeds = [1.0, 1.25, 1.5, 2.0]

const audioEl = ref<HTMLAudioElement | null>(null)
const progressBar = ref<HTMLDivElement | null>(null)

const audioSrc = computed(() => {
  if (props.src) return props.src
  if (props.fileId) return `/api/user/files/${props.fileId}/download`
  return ''
})

const progress = computed(() => {
  if (duration.value <= 0) return 0
  return (currentTime.value / duration.value) * 100
})

const currentSpeed = computed(() => speeds[speedIndex.value])

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

// ==================== 控制 ====================

function togglePlay() {
  if (!audioEl.value) return
  if (state.value === 'playing') {
    audioEl.value.pause()
    state.value = 'paused'
    emit('pause')
  } else {
    audioEl.value.play().catch(err => {
      state.value = 'error'
      errorMsg.value = '播放失败'
      emit('error', err.message)
    })
    state.value = 'playing'
    emit('play')
  }
}

function cycleSpeed() {
  speedIndex.value = (speedIndex.value + 1) % speeds.length
  if (audioEl.value) {
    audioEl.value.playbackRate = currentSpeed.value
  }
}

function seekTo(e: MouseEvent) {
  if (!progressBar.value || !audioEl.value || duration.value <= 0) return
  const rect = progressBar.value.getBoundingClientRect()
  const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width))
  audioEl.value.currentTime = ratio * duration.value
}

function retry() {
  state.value = 'loading'
  errorMsg.value = ''
  if (audioEl.value) {
    audioEl.value.load()
  }
}

// ==================== 事件 ====================

function onLoadedMetadata() {
  if (audioEl.value) {
    duration.value = audioEl.value.duration
    state.value = 'ready'
  }
}

function onTimeUpdate() {
  if (audioEl.value) {
    currentTime.value = audioEl.value.currentTime
  }
}

function onEnded() {
  state.value = 'ready'
  currentTime.value = 0
  emit('ended')
}

function onAudioError() {
  state.value = 'error'
  errorMsg.value = '音频加载失败'
  emit('error', errorMsg.value)
}

// ==================== 生命周期 ====================

watch(() => props.loading, (v) => {
  if (v) state.value = 'loading'
})

watch(() => props.fileId, () => {
  if (props.fileId && state.value === 'loading' && !props.loading) {
    // fileId 更新，触发加载
    state.value = 'loading'
  }
})

onMounted(() => {
  if (audioEl.value && audioSrc.value) {
    audioEl.value.playbackRate = currentSpeed.value
  }
})

onBeforeUnmount(() => {
  if (audioEl.value) {
    audioEl.value.pause()
    audioEl.value.src = ''
  }
})
</script>

<template>
  <!-- 状态 1：加载中 (shimmer) -->
  <div
    v-if="state === 'loading'"
    class="audio-player audio-player--loading"
    aria-label="语音生成中"
  >
    <div class="audio-player__icon">◌</div>
    <div class="audio-player__shimmer" />
    <div class="audio-player__hint">语音生成中...</div>
    <div class="audio-player__time">--:--</div>
  </div>

  <!-- 状态 3：错误 -->
  <div
    v-else-if="state === 'error'"
    class="audio-player audio-player--error"
    aria-label="语音加载失败"
  >
    <div class="audio-player__icon">⚠</div>
    <div class="audio-player__hint">{{ errorMsg || '语音生成失败' }}</div>
    <button class="audio-player__retry" @click="retry">重试</button>
  </div>

  <!-- 状态 2：就绪/播放 -->
  <div v-else class="audio-player audio-player--ready" aria-label="音频播放器">
    <audio
      ref="audioEl"
      :src="audioSrc"
      preload="metadata"
      @loadedmetadata="onLoadedMetadata"
      @timeupdate="onTimeUpdate"
      @ended="onEnded"
      @error="onAudioError"
    />

    <button
      class="audio-player__play-btn"
      :aria-label="state === 'playing' ? '暂停' : '播放'"
      @click="togglePlay"
    >
      {{ state === 'playing' ? '⏸' : '▶' }}
    </button>

    <span class="audio-player__current">{{ formatTime(currentTime) }}</span>

    <div
      ref="progressBar"
      class="audio-player__progress"
      role="slider"
      :aria-valuenow="Math.round(progress)"
      aria-valuemin="0"
      aria-valuemax="100"
      aria-label="播放进度"
      @click="seekTo"
    >
      <div class="audio-player__progress-fill" :style="{ width: progress + '%' }" />
      <div class="audio-player__progress-thumb" :style="{ left: progress + '%' }" />
    </div>

    <span class="audio-player__duration">{{ formatTime(duration) }}</span>

    <button
      class="audio-player__speed"
      aria-label="切换播放速度"
      @click="cycleSpeed"
    >
      {{ currentSpeed }}x
    </button>
  </div>
</template>

<style scoped>
.audio-player {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 8px;
  background: var(--audio-bg, #f5f5f5);
  font-size: 13px;
  min-height: 40px;
  user-select: none;
}

/* Loading shimmer */
.audio-player--loading {
  color: #999;
}
.audio-player__shimmer {
  flex: 1;
  height: 6px;
  border-radius: 3px;
  background: linear-gradient(90deg, #e0e0e0 25%, #f0f0f0 50%, #e0e0e0 75%);
  background-size: 200% 100%;
  animation: shimmer 1.5s infinite;
}
@keyframes shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}

/* Error */
.audio-player--error {
  color: #d32f2f;
}
.audio-player__retry {
  padding: 2px 10px;
  border: 1px solid #d32f2f;
  border-radius: 4px;
  background: transparent;
  color: #d32f2f;
  cursor: pointer;
  font-size: 12px;
}
.audio-player__retry:hover {
  background: #fbe9e7;
}

/* Ready/Playing */
.audio-player--ready {
  color: #333;
}
.audio-player__play-btn {
  width: 28px;
  height: 28px;
  border: none;
  border-radius: 50%;
  background: var(--audio-accent, #1a73e8);
  color: #fff;
  font-size: 12px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.audio-player__play-btn:hover {
  opacity: 0.85;
}

.audio-player__current,
.audio-player__duration {
  font-size: 11px;
  color: #666;
  min-width: 30px;
  text-align: center;
  flex-shrink: 0;
}

.audio-player__progress {
  flex: 1;
  height: 6px;
  background: #ddd;
  border-radius: 3px;
  cursor: pointer;
  position: relative;
}
.audio-player__progress-fill {
  height: 100%;
  background: var(--audio-accent, #1a73e8);
  border-radius: 3px;
  transition: width 0.1s linear;
}
.audio-player__progress-thumb {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: var(--audio-accent, #1a73e8);
  border: 2px solid #fff;
  box-shadow: 0 1px 3px rgba(0,0,0,0.2);
  position: absolute;
  top: 50%;
  transform: translate(-50%, -50%);
  transition: left 0.1s linear;
}

.audio-player__speed {
  padding: 2px 6px;
  border: 1px solid #ccc;
  border-radius: 4px;
  background: transparent;
  color: #666;
  font-size: 11px;
  cursor: pointer;
  flex-shrink: 0;
}
.audio-player__speed:hover {
  background: #eee;
}

.audio-player__icon {
  flex-shrink: 0;
}
.audio-player__hint {
  flex: 1;
  font-size: 12px;
}
.audio-player__time {
  font-size: 11px;
  color: #999;
  flex-shrink: 0;
}
</style>
