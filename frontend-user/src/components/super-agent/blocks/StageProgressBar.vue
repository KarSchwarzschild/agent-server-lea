<script setup lang="ts">
/**
 * StageProgressBar — 执行阶段进度条
 */
defineProps<{
  stageName: string
  index: number
  total: number
  title: string
}>()
</script>

<template>
  <div class="stage-progress">
    <div class="stage-progress__steps">
      <span
        v-for="i in total"
        :key="i"
        class="stage-progress__step"
        :class="{ 'stage-progress__step--done': i <= index, 'stage-progress__step--current': i === index }"
      >
        {{ i <= index ? '✅' : '○' }} {{ i }}/{{ total }}
      </span>
    </div>
    <div class="stage-progress__bar">
      <div class="stage-progress__fill" :style="{ width: `${(index / total) * 100}%` }" />
    </div>
  </div>
</template>

<style scoped>
.stage-progress {
  padding: 8px 0;
}
.stage-progress__steps {
  display: flex;
  gap: 16px;
  font-size: 13px;
  color: #666;
  margin-bottom: 4px;
}
.stage-progress__step--current {
  font-weight: 600;
  color: #1890ff;
}
.stage-progress__bar {
  height: 4px;
  background: #f0f0f0;
  border-radius: 2px;
  overflow: hidden;
}
.stage-progress__fill {
  height: 100%;
  background: #1890ff;
  border-radius: 2px;
  transition: width 0.3s ease;
}
</style>
