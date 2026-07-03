<script setup lang="ts">
/**
 * 版本导航组件 ← 1/2 →
 *
 * 在消息有多个兄弟版本时显示导航按钮。
 * 始终占位（单版本时 1/1 箭头禁用），方便用户发现功能。
 */
defineProps<{
  siblings: string[]
  currentIndex: number
  disabled?: boolean
}>()

const emit = defineEmits<{
  (e: 'switch', targetMessageId: string): void
}>()

function handlePrev(siblings: string[], currentIndex: number) {
  if (currentIndex > 0) {
    emit('switch', siblings[currentIndex - 1])
  }
}

function handleNext(siblings: string[], currentIndex: number) {
  if (currentIndex < siblings.length - 1) {
    emit('switch', siblings[currentIndex + 1])
  }
}
</script>

<template>
  <div v-if="siblings && siblings.length > 0" class="version-nav" :class="{ disabled }">
    <button
      class="version-nav-btn"
      :disabled="disabled || currentIndex <= 0"
      :class="{ 'btn-disabled': disabled || currentIndex <= 0 }"
      @click="handlePrev(siblings, currentIndex)"
      title="上一个版本"
    >
      <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor">
        <path d="M8 1L3 6l5 5" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
    </button>
    <span class="version-nav-label">{{ currentIndex + 1 }}/{{ siblings.length }}</span>
    <button
      class="version-nav-btn"
      :disabled="disabled || currentIndex >= siblings.length - 1"
      :class="{ 'btn-disabled': disabled || currentIndex >= siblings.length - 1 }"
      @click="handleNext(siblings, currentIndex)"
      title="下一个版本"
    >
      <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor">
        <path d="M4 1l5 5-5 5" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
    </button>
  </div>
</template>

<style scoped>
.version-nav {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  user-select: none;
}
.version-nav.disabled {
  opacity: 0.5;
  pointer-events: none;
}
.version-nav-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border: none;
  background: transparent;
  border-radius: 4px;
  cursor: pointer;
  color: #666;
  padding: 0;
  transition: all 0.15s;
}
.version-nav-btn:hover:not(:disabled) {
  background: rgba(0, 0, 0, 0.06);
  color: #333;
}
.version-nav-btn.btn-disabled {
  color: #ccc;
  cursor: default;
}
.version-nav-label {
  font-size: 12px;
  color: #888;
  min-width: 28px;
  text-align: center;
  font-variant-numeric: tabular-nums;
}
</style>
