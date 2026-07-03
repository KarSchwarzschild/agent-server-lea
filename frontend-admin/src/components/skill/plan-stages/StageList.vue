<template>
  <div class="stage-list">
    <div v-if="stages.length === 0" class="stage-empty">
      <NEmpty description="暂无阶段" size="small" />
    </div>

    <div
      v-for="(stage, index) in stages"
      :key="stage._uid"
      class="stage-item"
      :class="{ 'stage-item--selected': stage._uid === selectedUid }"
      @click="$emit('update:selectedUid', stage._uid)"
    >
      <div class="stage-row-top">
        <span class="stage-index">{{ circledNumber(index + 1) }}</span>
        <span class="stage-name">{{ stage.display_name || stage.name }}</span>
        <span v-if="stage.critical" class="stage-critical-dot" title="关键阶段" />
      </div>
      <div class="stage-row-bottom">
        <NButton quaternary size="tiny" class="action-btn" :disabled="index === 0" title="上移" @click.stop="$emit('move-up', stage._uid)">
          <template #icon><NIcon size="12"><ChevronUpOutline /></NIcon></template>
        </NButton>
        <NButton quaternary size="tiny" class="action-btn" :disabled="index === stages.length - 1" title="下移" @click.stop="$emit('move-down', stage._uid)">
          <template #icon><NIcon size="12"><ChevronDownOutline /></NIcon></template>
        </NButton>
        <NPopconfirm placement="right" @positive-click="$emit('remove', stage._uid)">
          <template #trigger>
            <NButton quaternary size="tiny" class="action-btn delete-btn" title="删除" @click.stop>
              <template #icon><NIcon size="12"><TrashOutline /></NIcon></template>
            </NButton>
          </template>
          确定删除「{{ stage.display_name || stage.name }}」？
        </NPopconfirm>
      </div>
    </div>

    <div class="stage-add">
      <NButton
        dashed
        size="small"
        block
        @click="$emit('add')"
      >
        <template #icon>
          <NIcon><AddOutline /></NIcon>
        </template>
        添加阶段
      </NButton>
    </div>
  </div>
</template>

<script setup lang="ts">
import { NButton, NIcon, NEmpty, NPopconfirm } from 'naive-ui'
import { AddOutline, TrashOutline, ChevronUpOutline, ChevronDownOutline } from '@vicons/ionicons5'

interface StageItem {
  _uid: string
  name: string
  display_name: string
  critical: boolean
  visible_to_user: boolean
}

const props = defineProps<{
  stages: StageItem[]
  selectedUid: string
}>()

defineEmits<{
  'update:selectedUid': [uid: string]
  'add': []
  'remove': [uid: string]
  'move-up': [uid: string]
  'move-down': [uid: string]
}>()

const CIRCLED_NUMBERS = ['①', '②', '③', '④', '⑤', '⑥', '⑦', '⑧', '⑨', '⑩',
  '⑪', '⑫', '⑬', '⑭', '⑮', '⑯', '⑰', '⑱', '⑲', '⑳']

function circledNumber(n: number): string {
  return CIRCLED_NUMBERS[n - 1] ?? `${n}.`
}
</script>

<style scoped>
.stage-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px;
}

.stage-empty {
  padding: 16px 0;
}

.stage-item {
  padding: 8px 8px 4px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.15s;
  border: 1px solid transparent;
}

.stage-item:hover {
  background: #f5f5f5;
}

.stage-item--selected {
  background: #e8f4fd;
  border-color: #91caff;
}

.stage-item--selected:hover {
  background: #d6ebfa;
}

.stage-row-top {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 2px;
}

.stage-index {
  font-size: 14px;
  flex-shrink: 0;
  color: #999;
}

.stage-name {
  flex: 1;
  font-size: 13px;
  font-weight: 500;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  color: #333;
}

.stage-critical-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #ef4444;
  flex-shrink: 0;
}

.stage-row-bottom {
  display: flex;
  align-items: center;
  gap: 2px;
  padding-left: 18px;
}

.action-btn {
  padding: 0 !important;
  width: 20px !important;
  height: 20px !important;
  opacity: 0.5;
}

.stage-item:hover .action-btn {
  opacity: 0.8;
}

.delete-btn {
  opacity: 0 !important;
  color: #e03131 !important;
  margin-left: auto;
}

.stage-item:hover .delete-btn {
  opacity: 0.7 !important;
}

.stage-add {
  margin-top: 6px;
  padding: 0 2px;
}
</style>
