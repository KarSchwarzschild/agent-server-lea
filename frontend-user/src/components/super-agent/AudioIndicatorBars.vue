<template>
  <span class="audio-bars" :style="{ '--bar-color': color }">
    <span class="bar" v-for="i in 3" :key="i" :style="{ animationDelay: `${(i - 1) * 0.15}s` }" />
  </span>
</template>

<script setup lang="ts">
/**
 * AudioIndicatorBars — 三条竖线呼吸动画
 * 对齐 OpenMAIC audio-indicator.tsx
 */
defineProps<{
  color?: string
}>()
</script>

<style scoped>
.audio-bars {
  display: inline-flex;
  align-items: flex-end;
  gap: 2px;
  height: 14px;
}

.bar {
  width: 2px;
  border-radius: 1px;
  background: var(--bar-color, #a855f7);
  animation: breathe 0.5s ease-in-out infinite alternate;
}

@keyframes breathe {
  from { height: 4px; }
  to { height: 12px; }
}
</style>
