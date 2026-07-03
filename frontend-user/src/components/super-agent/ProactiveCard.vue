<script setup lang="ts">
/**
 * ProactiveCard — 讨论触发卡片
 * 对齐 OpenMAIC chat/proactive-card.tsx：
 *   - 5s 倒计时进度条（playback 模式自动跳过）
 *   - "加入讨论" / "跳过" 按钮
 *   - 入场动画
 */
import { ref, onMounted, onUnmounted, watch, useTemplateRef } from 'vue'
import type { TriggerEvent } from '../../composables/usePlaybackEngine'

const props = defineProps<{
  trigger: TriggerEvent
  /** 锚点元素，卡片定位在其上方 */
  anchorEl?: HTMLElement | null
  /** Portal 目标容器（默认当前位置） */
  teleportTo?: string | HTMLElement
}>()

const emit = defineEmits<{
  (e: 'join'): void
  (e: 'skip'): void
}>()

const progress = ref(100)
const DURATION = 5000
const INTERVAL = 50
const step = (INTERVAL / DURATION) * 100

let timer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  timer = setInterval(() => {
    progress.value -= step
    if (progress.value <= 0) {
      progress.value = 0
      if (timer) { clearInterval(timer); timer = null }
      emit('skip')
    }
  }, INTERVAL)
})

onUnmounted(() => {
  if (timer) { clearInterval(timer); timer = null }
})

const countdown = ref('5s')
let countdownTimer: ReturnType<typeof setInterval> | null = null
onMounted(() => {
  countdownTimer = setInterval(() => {
    countdown.value = `${Math.max(0, Math.ceil((progress.value / 100) * 5))}s`
  }, 200)
})
onUnmounted(() => { if (countdownTimer) clearInterval(countdownTimer) })

// 动态定位（锚定到 anchorEl 上方）
const cardStyle = ref<Record<string, string>>({})

function updatePosition() {
  if (!props.anchorEl) return
  const rect = props.anchorEl.getBoundingClientRect()
  const parent = props.anchorEl.closest('.classroom-player') || document.body
  const parentRect = parent.getBoundingClientRect()

  const centerX = rect.left + rect.width / 2 - parentRect.left
  const cardWidth = 400
  let left = centerX - cardWidth / 2
  left = Math.max(16, Math.min(parentRect.width - cardWidth - 16, left))

  cardStyle.value = {
    position: 'absolute',
    left: `${left}px`,
    bottom: `${parentRect.height - (rect.top - parentRect.top) + 12}px`,
    zIndex: '200',
  }
}

watch(() => props.anchorEl, updatePosition, { immediate: true })
onMounted(updatePosition)
</script>

<template>
  <Teleport :to="teleportTo || 'body'" :disabled="!teleportTo">
  <Transition name="proactive-slide">
    <div class="proactive-card" :style="anchorEl ? cardStyle : {}">
      <!-- 进度条 -->
      <div class="progress-bar">
        <div class="progress-fill" :style="{ width: progress + '%' }" />
      </div>

      <div class="card-body">
        <div class="card-icon">🎓</div>
        <div class="card-content">
          <div class="card-title">讨论环节</div>
          <p class="card-question">{{ trigger.question }}</p>
        </div>
        <div class="card-countdown">{{ countdown }}</div>
      </div>

      <div class="card-actions">
        <button class="btn-join" @click="emit('join')">加入讨论</button>
        <button class="btn-skip" @click="emit('skip')">跳过</button>
      </div>
    </div>
  </Transition>
  </Teleport>
</template>

<style scoped>
.proactive-card {
  background: var(--cp-card-bg, white);
  border: 1px solid rgba(0,0,0,0.08);
  border-radius: 16px;
  box-shadow: 0 8px 32px rgba(0,0,0,0.12);
  overflow: hidden;
  max-width: 400px;
  width: 100%;
}

.progress-bar {
  height: 3px;
  background: #e5e7eb;
}

.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #f59e0b, #f97316);
  transition: width 50ms linear;
  border-radius: 0 2px 2px 0;
}

.card-body {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 16px 16px 12px;
}

.card-icon {
  font-size: 24px;
  flex-shrink: 0;
  margin-top: 2px;
}

.card-content {
  flex: 1;
  min-width: 0;
}

.card-title {
  font-size: 12px;
  font-weight: 600;
  color: #f59e0b;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: 4px;
}

.card-question {
  font-size: 14px;
  line-height: 1.5;
  color: var(--cp-text, #333);
  margin: 0;
  word-break: break-word;
}

.card-countdown {
  font-size: 12px;
  font-weight: 600;
  color: #9ca3af;
  flex-shrink: 0;
  min-width: 20px;
  text-align: right;
}

.card-actions {
  display: flex;
  gap: 8px;
  padding: 0 16px 16px;
}

.btn-join {
  flex: 1;
  height: 32px;
  border: none;
  border-radius: 8px;
  background: linear-gradient(135deg, #f59e0b, #f97316);
  color: white;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s;
}
.btn-join:hover { transform: scale(1.02); box-shadow: 0 2px 8px rgba(245,158,11,0.3); }
.btn-join:active { transform: scale(0.98); }

.btn-skip {
  height: 32px;
  padding: 0 16px;
  border: 1px solid var(--cp-card-border, #e5e7eb);
  border-radius: 8px;
  background: var(--cp-card-bg, white);
  color: var(--cp-text-secondary, #666);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s;
}
.btn-skip:hover { background: var(--cp-bg, #f9fafb); border-color: #d1d5db; }

/* 入场动画 */
.proactive-slide-enter-active { transition: all 0.3s cubic-bezier(0.21, 1, 0.36, 1); }
.proactive-slide-leave-active { transition: all 0.2s ease; }
.proactive-slide-enter-from { opacity: 0; transform: translateY(12px) scale(0.95); }
.proactive-slide-leave-to { opacity: 0; transform: scale(0.9); }
</style>
