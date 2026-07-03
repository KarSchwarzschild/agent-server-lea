<template>
  <div
    class="agent-card"
    :class="{ selected }"
    :style="cardStyle"
    @click="handleClick"
  >
    <!-- 选中标记 -->
    <div v-if="selected" class="selected-mark">
      <n-icon :size="16">
        <CheckCircle />
      </n-icon>
    </div>

    <!-- 图标 -->
    <div class="agent-icon">
      {{ agent.icon || '🤖' }}
    </div>

    <!-- 名称 -->
    <div class="agent-name">
      {{ agent.name }}
    </div>

    <!-- 描述 -->
    <div class="agent-description" v-if="agent.description">
      {{ agent.description }}
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { NIcon } from 'naive-ui'
import { CheckmarkCircle as CheckCircle } from '@vicons/ionicons5'
import type { AgentPublic } from '../api/agents'

interface Props {
  agent: AgentPublic
  selected?: boolean
  categoryColor?: string
}

const props = withDefaults(defineProps<Props>(), {
  selected: false,
  categoryColor: '#3B82F6'
})

const emit = defineEmits<{
  (e: 'select', agent: AgentPublic): void
}>()

const cardStyle = computed(() => {
  if (props.selected) {
    return {
      borderColor: props.categoryColor,
      backgroundColor: `${props.categoryColor}08`,
    }
  }
  return {}
})

const handleClick = () => {
  emit('select', props.agent)
}
</script>

<style scoped>
.agent-card {
  position: relative;
  padding: 16px;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  background: #fff;
  cursor: pointer;
  transition: all 0.2s ease;
  text-align: center;
  min-height: 120px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
}

.agent-card:hover {
  border-color: #d1d5db;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
  transform: translateY(-2px);
}

.agent-card.selected {
  border-width: 2px;
}

.selected-mark {
  position: absolute;
  top: 8px;
  right: 8px;
  color: v-bind(categoryColor);
}

.agent-icon {
  font-size: 32px;
  line-height: 1;
}

.agent-name {
  font-size: 14px;
  font-weight: 600;
  color: #1f2937;
  line-height: 1.4;
}

.agent-description {
  font-size: 12px;
  color: #6b7280;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 移动端适配 */
@media (max-width: 640px) {
  .agent-card {
    padding: 12px;
    min-height: 100px;
  }

  .agent-icon {
    font-size: 28px;
  }

  .agent-name {
    font-size: 13px;
  }

  .agent-description {
    font-size: 11px;
  }
}
</style>
