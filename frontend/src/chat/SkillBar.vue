<template>
  <div class="skill-bar" v-if="skills.length">
    <n-tag
      v-for="skill in displayedSkills"
      :key="skill.id"
      :type="selectedId === skill.id ? 'primary' : 'default'"
      :bordered="selectedId !== skill.id"
      round
      size="small"
      class="skill-tag"
      @click="toggle(skill)"
    >
      {{ getCategoryIcon(skill.category) }} {{ skill.display_name }}
    </n-tag>
    <n-tag
      v-if="skills.length > maxVisible"
      round
      size="small"
      class="skill-tag more-tag"
      @click="$emit('showAll')"
    >
      更多 {{ skills.length - maxVisible }}+
    </n-tag>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { NTag } from 'naive-ui'
import type { SkillItem } from './SkillPanel.vue'

const props = defineProps<{
  skills: SkillItem[]
  selectedId: number | null
  maxVisible?: number
}>()

const emit = defineEmits<{
  select: [skill: SkillItem | null]
  showAll: []
}>()

const maxCount = computed(() => props.maxVisible ?? 6)

const displayedSkills = computed(() => props.skills.slice(0, maxCount.value))

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

function toggle(skill: SkillItem) {
  if (props.selectedId === skill.id) {
    emit('select', null)
  } else {
    emit('select', skill)
  }
}
</script>

<style scoped>
.skill-bar {
  display: flex;
  gap: 6px;
  padding: 6px 16px;
  overflow-x: auto;
  scrollbar-width: none;
}

.skill-bar::-webkit-scrollbar {
  display: none;
}

.skill-tag {
  cursor: pointer;
  flex-shrink: 0;
  transition: all 0.15s;
}

.skill-tag:hover {
  opacity: 0.85;
}

.more-tag {
  color: #666;
}
</style>
