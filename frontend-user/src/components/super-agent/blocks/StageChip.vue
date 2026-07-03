<script setup lang="ts">
/**
 * StageChip — invisible 阶段折叠芯片
 * 点击 inline 展开/折叠（手风琴模式由父组件控制）
 */
defineProps<{
  stageName: string
  visible?: boolean
  content: string
  title?: string
}>()

const expanded = defineModel<boolean>('expanded', { default: false })
</script>

<template>
  <div class="stage-chip" :class="{ 'stage-chip--expanded': expanded }">
    <button
      class="stage-chip__trigger"
      @click="expanded = !expanded"
    >
      <span class="stage-chip__icon">📋</span>
      <span class="stage-chip__label">{{ title || stageName }}</span>
      <span class="stage-chip__chevron" :class="{ 'rotate-180': expanded }">▾</span>
    </button>
    <Transition name="collapse">
      <div v-if="expanded" class="stage-chip__content">
        <div class="prose prose-sm max-w-none" v-html="content" />
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.stage-chip {
  display: inline-flex;
  flex-direction: column;
  margin: 2px 4px;
}
.stage-chip__trigger {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 12px;
  border-radius: 16px;
  background: #f0f0f0;
  border: 1px solid #e0e0e0;
  cursor: pointer;
  font-size: 13px;
  transition: background 0.2s;
  min-height: 32px;
}
.stage-chip__trigger:hover {
  background: #e8e8e8;
}
.stage-chip__chevron {
  transition: transform 0.2s;
  font-size: 12px;
}
.rotate-180 {
  transform: rotate(180deg);
}
.stage-chip__content {
  margin-top: 8px;
  padding: 12px;
  border-radius: 8px;
  background: #fafafa;
  border: 1px solid #eee;
  max-height: 60vh;
  overflow-y: auto;
}
@media (max-width: 768px) {
  .stage-chip__trigger {
    min-height: 44px;
    padding: 8px 16px;
  }
  .stage-chip__content {
    max-height: 40vh;
  }
}
.collapse-enter-active, .collapse-leave-active {
  transition: all 0.2s ease;
  overflow: hidden;
}
.collapse-enter-from, .collapse-leave-to {
  max-height: 0;
  opacity: 0;
  margin-top: 0;
  padding: 0 12px;
}
</style>
