<script setup lang="ts">
/**
 * DebateAudioBar — 辩论单轨音频播放器
 *
 * 浮动在辩论容器底部，统一播放所有专家的语音片段。
 * 规划要求：不在每位专家下放独立 AudioPlayer，改为统一播放器 + 片段列表。
 */
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import { NButton, NSpace, NTag } from 'naive-ui'

export interface AudioSegment {
  round: number
  fileId: string
  voice: string
  durationMs: number
  expertId: string
  expertName: string
}

const props = defineProps<{
  segments: AudioSegment[]
}>()

const currentIdx = ref(-1)
const isPlaying = ref(false)
const audioEl = ref<HTMLAudioElement | null>(null)
const currentTime = ref(0)
const duration = ref(0)

const currentSegment = computed(() => {
  if (currentIdx.value < 0 || currentIdx.value >= props.segments.length) return null
  return props.segments[currentIdx.value]
})

const audioSrc = computed(() => {
  if (!currentSegment.value) return ''
  return `/api/user/files/${currentSegment.value.fileId}/download`
})

const progress = computed(() => {
  if (duration.value <= 0) return 0
  return (currentTime.value / duration.value) * 100
})

function formatTime(s: number): string {
  const m = Math.floor(s / 60)
  const sec = Math.floor(s % 60)
  return `${m}:${sec.toString().padStart(2, '0')}`
}

function playSegment(idx: number) {
  if (idx < 0 || idx >= props.segments.length) return
  currentIdx.value = idx
  isPlaying.value = true
  if (audioEl.value) {
    audioEl.value.load()
    audioEl.value.play().catch(() => { isPlaying.value = false })
  }
}

function togglePlay() {
  if (!audioEl.value) return
  if (isPlaying.value) {
    audioEl.value.pause()
    isPlaying.value = false
  } else {
    if (currentIdx.value < 0 && props.segments.length > 0) {
      playSegment(0)
    } else {
      audioEl.value.play().catch(() => {})
      isPlaying.value = true
    }
  }
}

function onEnded() {
  isPlaying.value = false
  // 自动播放下一个片段
  if (currentIdx.value < props.segments.length - 1) {
    playSegment(currentIdx.value + 1)
  }
}

function onTimeUpdate() {
  if (audioEl.value) currentTime.value = audioEl.value.currentTime
}

function onLoadedMetadata() {
  if (audioEl.value) duration.value = audioEl.value.duration
}

function segmentLabel(seg: AudioSegment): string {
  return `${seg.expertName}R${seg.round}`
}

onBeforeUnmount(() => {
  if (audioEl.value) {
    audioEl.value.pause()
    audioEl.value.src = ''
  }
})
</script>

<template>
  <div v-if="segments.length > 0" class="debate-audio-bar">
    <audio
      ref="audioEl"
      :src="audioSrc"
      preload="metadata"
      @timeupdate="onTimeUpdate"
      @loadedmetadata="onLoadedMetadata"
      @ended="onEnded"
    />

    <!-- 当前播放信息 -->
    <div class="dab-player">
      <span v-if="currentSegment" class="dab-speaker">{{ currentSegment.expertName }}</span>
      <button class="dab-play-btn" @click="togglePlay">
        {{ isPlaying ? '⏸' : '▶' }}
      </button>
      <span class="dab-time">{{ formatTime(currentTime) }}/{{ formatTime(duration) }}</span>
      <div class="dab-progress" @click.stop>
        <div class="dab-progress-fill" :style="{ width: progress + '%' }" />
      </div>
    </div>

    <!-- 片段列表 -->
    <div class="dab-segments">
      <span
        v-for="(seg, idx) in segments"
        :key="seg.fileId"
        class="dab-seg-chip"
        :class="{ 'dab-seg-chip--active': idx === currentIdx }"
        @click="playSegment(idx)"
      >
        {{ segmentLabel(seg) }}
      </span>
    </div>
  </div>
</template>

<style scoped>
.debate-audio-bar {
  position: sticky;
  bottom: 0;
  background: #fff;
  border-top: 1px solid #e5e7eb;
  padding: 8px 16px;
  z-index: 10;
}

.dab-player {
  display: flex;
  align-items: center;
  gap: 8px;
}

.dab-speaker {
  font-weight: 500;
  font-size: 13px;
  color: #374151;
  min-width: 60px;
}

.dab-play-btn {
  width: 28px;
  height: 28px;
  border: none;
  border-radius: 50%;
  background: #1a73e8;
  color: #fff;
  font-size: 12px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.dab-play-btn:hover { opacity: 0.85; }

.dab-time {
  font-size: 11px;
  color: #6b7280;
  min-width: 60px;
  flex-shrink: 0;
}

.dab-progress {
  flex: 1;
  height: 4px;
  background: #e5e7eb;
  border-radius: 2px;
  overflow: hidden;
}
.dab-progress-fill {
  height: 100%;
  background: #1a73e8;
  transition: width 0.1s linear;
}

.dab-segments {
  display: flex;
  gap: 4px;
  margin-top: 6px;
  flex-wrap: wrap;
}

.dab-seg-chip {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 10px;
  background: #f3f4f6;
  color: #6b7280;
  cursor: pointer;
  user-select: none;
  transition: all 0.15s;
}
.dab-seg-chip:hover {
  background: #e5e7eb;
}
.dab-seg-chip--active {
  background: #1a73e8;
  color: #fff;
}
</style>
