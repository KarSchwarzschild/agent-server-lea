<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'

interface Mode {
  value: string
  label: string
  icon: string
  desc: string
}

const props = defineProps<{
  recommendedMode: string
  modes: Mode[]
  timeoutSeconds: number
}>()

const emit = defineEmits<{
  (e: 'confirm', mode: string): void
}>()

const selectedMode = ref(props.recommendedMode)
const countdown = ref(props.timeoutSeconds)
let timer: number | null = null

const selectMode = (mode: string) => {
  selectedMode.value = mode
}

const confirmMode = () => {
  if (timer) clearInterval(timer)
  emit('confirm', selectedMode.value)
}

onMounted(() => {
  timer = window.setInterval(() => {
    countdown.value--
    if (countdown.value === 0) {
      confirmMode()
    }
  }, 1000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<template>
  <div class="mode-selection-card">
    <div class="header">
      <span class="icon">🎯</span>
      <span class="title">选择讨论模式</span>
      <span class="countdown">{{ countdown }}s</span>
    </div>

    <div class="modes">
      <div
        v-for="mode in modes"
        :key="mode.value"
        :class="['mode-item', {
          recommended: mode.value === recommendedMode,
          selected: mode.value === selectedMode
        }]"
        @click="selectMode(mode.value)"
      >
        <div class="mode-icon">{{ mode.icon }}</div>
        <div class="mode-label">{{ mode.label }}</div>
        <div class="mode-desc">{{ mode.desc }}</div>
        <span v-if="mode.value === recommendedMode" class="badge">推荐</span>
      </div>
    </div>

    <div class="footer">
      <div class="tip">{{ countdown }}秒后自动使用推荐模式</div>
      <button class="confirm-btn" @click="confirmMode">确认开始</button>
    </div>
  </div>
</template>

<style scoped>
.mode-selection-card {
  background: white;
  border-radius: 12px;
  padding: 20px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.1);
  margin: 16px 0;
}

.header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
}

.header .icon {
  font-size: 20px;
}

.header .title {
  flex: 1;
  font-size: 16px;
  font-weight: 600;
  color: #1f2937;
}

.header .countdown {
  font-size: 14px;
  color: #6b7280;
  background: #f3f4f6;
  padding: 4px 12px;
  border-radius: 12px;
}

.modes {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  margin-bottom: 16px;
}

.mode-item {
  position: relative;
  padding: 16px;
  border: 2px solid #e5e7eb;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s;
  text-align: center;
}

.mode-item:hover {
  border-color: #6366f1;
  background: #f5f3ff;
}

.mode-item.selected {
  border-color: #6366f1;
  background: #eef2ff;
}

.mode-item.recommended {
  border-color: #10b981;
}

.mode-icon {
  font-size: 32px;
  margin-bottom: 8px;
}

.mode-label {
  font-size: 14px;
  font-weight: 600;
  color: #1f2937;
  margin-bottom: 4px;
}

.mode-desc {
  font-size: 12px;
  color: #6b7280;
}

.badge {
  position: absolute;
  top: 8px;
  right: 8px;
  font-size: 10px;
  padding: 2px 6px;
  background: #10b981;
  color: white;
  border-radius: 4px;
}

.footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 12px;
  border-top: 1px solid #e5e7eb;
}

.tip {
  font-size: 12px;
  color: #6b7280;
}

.confirm-btn {
  padding: 8px 20px;
  background: #6366f1;
  color: white;
  border: none;
  border-radius: 6px;
  font-size: 14px;
  cursor: pointer;
  transition: background 0.2s;
}

.confirm-btn:hover {
  background: #4f46e5;
}

@media (max-width: 768px) {
  .modes {
    grid-template-columns: 1fr;
  }
}
</style>
