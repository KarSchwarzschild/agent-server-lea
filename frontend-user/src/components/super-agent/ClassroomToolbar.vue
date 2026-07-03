<script setup lang="ts">
/**
 * ClassroomToolbar v2 — 课堂底部工具栏
 * 对齐 OpenMAIC canvas-toolbar.tsx：
 *   - 音量图标 + hover 垂直滑块
 *   - live 模式讨论控制（红点 + 结束讨论）
 *   - 白板按钮占位
 */
import { ref, inject, computed } from 'vue'
import type { UsePlaybackEngineReturn } from '../../composables/usePlaybackEngine'

const props = defineProps<{
  currentIndex: number
  totalScenes: number
  autoAdvance: boolean
  sidebarVisible: boolean
  isDarkMode?: boolean
}>()

const emit = defineEmits<{
  (e: 'prev'): void
  (e: 'next'): void
  (e: 'toggle-auto'): void
  (e: 'toggle-sidebar'): void
  (e: 'toggle-fullscreen'): void
  (e: 'end-discussion'): void
  (e: 'toggle-dark'): void
}>()

const playback = inject<UsePlaybackEngineReturn>('playback')!

const isLive = computed(() => playback.engineMode.value === 'live')
const volumeHover = ref(false)
let volumeLeaveTimer: ReturnType<typeof setTimeout> | null = null

function handleVolumeEnter() {
  if (volumeLeaveTimer) { clearTimeout(volumeLeaveTimer); volumeLeaveTimer = null }
  volumeHover.value = true
}

function handleVolumeLeave() {
  volumeLeaveTimer = setTimeout(() => { volumeHover.value = false }, 300)
}

const speedOptions = [1, 1.25, 1.5, 2]
function cycleSpeed() {
  const current = playback.playbackSpeed.value
  const idx = speedOptions.indexOf(current)
  playback.setSpeed(speedOptions[(idx + 1) % speedOptions.length])
}

function togglePlay() {
  if (playback.isPaused.value) playback.resume()
  else if (playback.isPlaying.value) playback.pause()
  else playback.play()
}

const playIcon = computed(() => {
  if (playback.isPaused.value) return '▶'
  if (playback.isPlaying.value) return '⏸'
  return '▶'
})

const volumeIcon = computed(() => {
  if (playback.isMuted.value || playback.volume.value === 0) return '🔇'
  if (playback.volume.value < 0.5) return '🔉'
  return '🔊'
})

const volumePercent = computed(() => Math.round((playback.isMuted.value ? 0 : playback.volume.value) * 100))
</script>

<template>
  <div class="classroom-toolbar">
    <!-- Left: sidebar toggle + page -->
    <div class="toolbar-left">
      <button
        class="toolbar-btn"
        :class="{ 'is-active': sidebarVisible }"
        title="切换侧栏 (S)"
        @click="emit('toggle-sidebar')"
      >📋</button>
      <span class="toolbar-page">{{ currentIndex + 1 }} / {{ totalScenes }}</span>
    </div>

    <!-- Center: controls -->
    <div class="toolbar-center">
      <!-- 音量 -->
      <div
        class="volume-container"
        @mouseenter="handleVolumeEnter"
        @mouseleave="handleVolumeLeave"
      >
        <button class="toolbar-btn" :title="`音量 ${volumePercent}% (M)`" @click="playback.toggleMute()">
          {{ volumeIcon }}
        </button>
        <!-- 垂直滑块 -->
        <Transition name="volume-pop">
          <div v-if="volumeHover" class="volume-popup">
            <div class="volume-popup-inner">
              <span class="volume-label">{{ volumePercent }}</span>
              <input
                type="range"
                class="volume-slider"
                min="0" max="1" step="0.05"
                :value="playback.isMuted.value ? 0 : playback.volume.value"
                @input="(e) => {
                  const v = parseFloat((e.target as HTMLInputElement).value)
                  playback.setVolume(v)
                  if (v > 0 && playback.isMuted.value) playback.toggleMute()
                }"
              />
            </div>
            <div class="volume-arrow" />
          </div>
        </Transition>
      </div>

      <!-- Live 模式：讨论控制 -->
      <template v-if="isLive">
        <button class="toolbar-btn discussion-btn" @click="emit('end-discussion')">
          <span class="discussion-dot" />
          <span class="discussion-label">结束讨论</span>
        </button>
      </template>

      <!-- 播放模式：标准控制 -->
      <template v-else>
        <button class="toolbar-btn toolbar-speed" title="播放速度" @click="cycleSpeed">
          {{ playback.playbackSpeed.value }}x
        </button>
        <button class="toolbar-btn" title="上一场景 (←)" :disabled="currentIndex <= 0" @click="emit('prev')">◁</button>
        <button class="toolbar-btn toolbar-play" :title="playIcon === '⏸' ? '暂停 (Space)' : '播放 (Space)'" @click="togglePlay">
          {{ playIcon }}
        </button>
        <button class="toolbar-btn" title="下一场景 (→)" :disabled="currentIndex >= totalScenes - 1" @click="emit('next')">▷</button>
        <button class="toolbar-btn" :class="{ 'is-active': autoAdvance }" title="自动播放" @click="emit('toggle-auto')">🔁</button>
      </template>
    </div>

    <!-- Right: dark-mode + whiteboard + fullscreen -->
    <div class="toolbar-right">
      <button class="toolbar-btn" :title="props.isDarkMode ? '浅色模式' : '暗色模式'" @click="emit('toggle-dark')">
        {{ props.isDarkMode ? '☀' : '🌙' }}
      </button>
      <button class="toolbar-btn" title="白板 (即将推出)" disabled style="opacity:0.3">✏️</button>
      <button class="toolbar-btn" title="全屏 (F)" @click="emit('toggle-fullscreen')">⛶</button>
    </div>
  </div>
</template>

<style scoped>
.classroom-toolbar {
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 12px;
  background: var(--cp-surface, rgba(255, 255, 255, 0.8));
  -webkit-backdrop-filter: blur(24px);
  backdrop-filter: blur(24px);
  border-top: 1px solid var(--cp-border, rgba(0, 0, 0, 0.06));
  flex-shrink: 0;
  gap: 8px;
}

.toolbar-left, .toolbar-center, .toolbar-right {
  display: flex;
  align-items: center;
  gap: 4px;
}
.toolbar-center { gap: 2px; }

.toolbar-page { font-size: 12px; color: var(--cp-text-secondary, #666); margin-left: 4px; min-width: 40px; }

.toolbar-btn {
  width: 28px; height: 28px;
  display: flex; align-items: center; justify-content: center;
  border: none; background: transparent; border-radius: 6px;
  cursor: pointer; font-size: 14px; color: var(--cp-text-secondary, #555);
  transition: all 0.15s; flex-shrink: 0;
}
.toolbar-btn:hover:not(:disabled) { background: rgba(0,0,0,0.06); }
.toolbar-btn:active:not(:disabled) { transform: scale(0.9); }
.toolbar-btn:disabled { opacity: 0.3; cursor: not-allowed; }
.toolbar-btn.is-active { color: #7c3aed; background: rgba(124,58,237,0.08); }
.toolbar-speed { font-size: 11px; font-weight: 600; width: auto; padding: 0 6px; min-width: 36px; }
.toolbar-play { width: 32px; height: 32px; font-size: 16px; }

/* ─── Volume popup ─── */
.volume-container { position: relative; display: flex; align-items: center; }

.volume-popup {
  position: absolute;
  bottom: 100%;
  left: 50%;
  transform: translateX(-50%);
  margin-bottom: 8px;
  z-index: 10;
}

.volume-popup-inner {
  background: var(--cp-card-bg, white);
  border: 1px solid var(--cp-card-border, rgba(0,0,0,0.1));
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0,0,0,0.12);
  padding: 8px 6px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
}

.volume-label { font-size: 10px; color: var(--cp-text-secondary, #666); font-weight: 600; }

.volume-slider {
  height: 64px;
  width: 4px;
  -webkit-appearance: none;
  appearance: none;
  writing-mode: vertical-lr;
  direction: rtl;
  background: #e5e7eb;
  border-radius: 2px;
  outline: none;
}

.volume-slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 12px; height: 12px;
  border-radius: 50%;
  background: #7c3aed;
  cursor: pointer;
}

.volume-arrow {
  width: 8px; height: 8px;
  background: white;
  border-bottom: 1px solid rgba(0,0,0,0.1);
  border-right: 1px solid rgba(0,0,0,0.1);
  transform: rotate(45deg);
  margin: -5px auto 0;
}

.volume-pop-enter-active, .volume-pop-leave-active { transition: all 0.2s ease; }
.volume-pop-enter-from, .volume-pop-leave-to { opacity: 0; transform: translateX(-50%) translateY(4px); }

/* ─── Discussion button ─── */
.discussion-btn {
  width: auto !important;
  padding: 0 10px;
  gap: 6px;
  height: 24px;
  background: rgba(239,68,68,0.1);
  color: #dc2626;
  font-size: 11px;
  font-weight: 600;
  border-radius: 6px;
  white-space: nowrap;
}
.discussion-btn:hover { background: rgba(239,68,68,0.2); }

.discussion-dot {
  position: relative;
  width: 6px; height: 6px;
  flex-shrink: 0;
}
.discussion-dot::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: 50%;
  background: #dc2626;
}
.discussion-dot::after {
  content: '';
  position: absolute;
  inset: -2px;
  border-radius: 50%;
  background: #dc2626;
  animation: ping 1.5s cubic-bezier(0,0,0.2,1) infinite;
  opacity: 0.75;
}

@keyframes ping {
  75%, 100% { transform: scale(2); opacity: 0; }
}
</style>
