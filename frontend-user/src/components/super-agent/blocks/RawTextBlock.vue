<script setup lang="ts">
/**
 * RawTextBlock — 兜底组件，未知 block type 渲染为 pre
 */
defineProps<{
  type: string
  [key: string]: unknown
}>()
</script>

<template>
  <pre class="raw-text-block">{{ JSON.stringify($props, null, 2) }}</pre>
</template>

<style scoped>
.raw-text-block {
  font-size: 12px;
  color: #999;
  background: #f5f5f5;
  padding: 8px;
  border-radius: 4px;
  white-space: pre-wrap;
  overflow-x: auto;
}
</style>
