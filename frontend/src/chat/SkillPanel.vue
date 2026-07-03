<template>
  <n-popover trigger="click" placement="top-start" :show="visible" @update:show="visible = $event" style="padding: 0">
    <template #trigger>
      <n-button circle secondary size="small" class="plus-btn" :disabled="disabled">
        <template #icon><n-icon :component="AddCircleOutline" /></template>
      </n-button>
    </template>
    <div class="panel">
      <div class="panel-header">
        <n-text strong>选择技能</n-text>
      </div>
      <div class="skill-grid" v-if="skills.length">
        <div
          v-for="skill in skills"
          :key="skill.id"
          class="skill-card"
          @click="handleSelect(skill)"
        >
          <span class="skill-icon">{{ getCategoryIcon(skill.category) }}</span>
          <div class="skill-info">
            <div class="skill-name">{{ skill.display_name }}</div>
            <div class="skill-desc">{{ skill.description || '暂无描述' }}</div>
          </div>
        </div>
      </div>
      <n-empty v-else size="small" description="暂无可用技能" />
    </div>
  </n-popover>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { NPopover, NButton, NIcon, NText, NEmpty } from 'naive-ui'
import { AddCircleOutline } from '@vicons/ionicons5'

export interface SkillItem {
  id: number
  name: string
  display_name: string
  description: string
  category: string
  execution_mode: string
  model_tier: string
}

defineProps<{
  skills: SkillItem[]
  disabled?: boolean
}>()

const emit = defineEmits<{
  select: [skill: SkillItem]
}>()

const visible = ref(false)

const categoryIcons: Record<string, string> = {
  research: '🔍',
  education: '📚',
  writing: '✍️',
  analysis: '📊',
  general: '🤖',
}

function getCategoryIcon(category: string) {
  return categoryIcons[category] || '⚡'
}

function handleSelect(skill: SkillItem) {
  emit('select', skill)
  visible.value = false
}
</script>

<style scoped>
.panel {
  width: 320px;
  max-height: 360px;
  overflow-y: auto;
}

.panel-header {
  padding: 10px 14px 6px;
}

.skill-grid {
  display: flex;
  flex-direction: column;
  padding: 0 6px 6px;
}

.skill-card {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s;
}

.skill-card:hover {
  background: #f3f3f5;
}

.skill-icon {
  font-size: 22px;
  flex-shrink: 0;
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f0f0f0;
  border-radius: 8px;
}

.skill-info {
  min-width: 0;
}

.skill-name {
  font-size: 13px;
  font-weight: 600;
  line-height: 1.4;
}

.skill-desc {
  font-size: 12px;
  color: #888;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
