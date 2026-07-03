<template>
  <Teleport to="body">
    <Transition name="mention-dropdown">
      <div
        v-if="show && items.length > 0"
        ref="dropdownRef"
        class="skill-mention-dropdown"
        :style="positionStyle"
      >
        <div class="mention-dropdown-header">选择技能</div>
        <div class="mention-dropdown-list" ref="listRef">
          <div
            v-for="(item, index) in items"
            :key="item.name"
            class="mention-dropdown-item"
            :class="{ active: index === activeIndex }"
            @mouseenter="$emit('hover', index)"
            @click="$emit('select', item)"
          >
            <span class="item-icon">{{ item.isSpecial ? '🎭' : getSkillIcon(item.name) }}</span>
            <div class="item-content">
              <span class="item-name">{{ item.display_name }}</span>
              <span class="item-desc">{{ item.description }}</span>
            </div>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, watch, nextTick, computed } from 'vue'
import type { MentionSkill } from '../composables/useSkillMention'

const props = defineProps<{
  show: boolean
  items: MentionSkill[]
  activeIndex: number
  anchorRect?: { bottom: number; left: number; width: number } | null
}>()

defineEmits<{
  select: [skill: MentionSkill]
  hover: [index: number]
}>()

const dropdownRef = ref<HTMLElement>()
const listRef = ref<HTMLElement>()

// 技能图标映射
const skillIconMap: Record<string, string> = {
  'academic-search': '📚',
  'deep-research': '🔍',
  'web-search': '🌐',
  'doc-generator': '📝',
  'quiz-generator': '📋',
  'course-outline-generator': '📐',
  'citation-helper': '📎',
  'note-organizer': '🗂️',
  'auto-grader': '✅',
  'policy-analyzer': '📜',
  'official-doc': '📄',
  'learning-planner': '🗺️',
  'translation': '🌍',
}

function getSkillIcon(name: string): string {
  return skillIconMap[name] || '⚡'
}

// 定位：显示在输入框上方
const positionStyle = computed(() => {
  if (!props.anchorRect) {
    return { bottom: '80px', left: '16px' }
  }
  const { bottom, left, width } = props.anchorRect
  // 显示在锚点上方
  const viewportHeight = window.innerHeight
  return {
    bottom: `${viewportHeight - bottom + 8}px`,
    left: `${left}px`,
    maxWidth: `${Math.min(width, 360)}px`,
  }
})

// 自动滚动到 active 项
watch(() => props.activeIndex, async (idx) => {
  await nextTick()
  const list = listRef.value
  if (!list) return
  const activeEl = list.children[idx] as HTMLElement
  if (activeEl) {
    activeEl.scrollIntoView({ block: 'nearest' })
  }
})
</script>

<style scoped>
.skill-mention-dropdown {
  position: fixed;
  z-index: 1000;
  width: 320px;
  max-height: 280px;
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 4px 24px rgba(0, 0, 0, 0.12), 0 0 0 1px rgba(0, 0, 0, 0.05);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

@media (max-width: 640px) {
  .skill-mention-dropdown {
    width: calc(100vw - 24px);
    left: 12px !important;
    max-width: none !important;
  }
}

.mention-dropdown-header {
  padding: 8px 14px 4px;
  font-size: 12px;
  color: #999;
  font-weight: 500;
  flex-shrink: 0;
}

.mention-dropdown-list {
  overflow-y: auto;
  max-height: 240px;
  padding: 4px 6px 6px;
}

.mention-dropdown-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s;
}

.mention-dropdown-item:hover,
.mention-dropdown-item.active {
  background: #f0f5ff;
}

.item-icon {
  font-size: 18px;
  flex-shrink: 0;
  width: 28px;
  text-align: center;
}

.item-content {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.item-name {
  font-size: 14px;
  font-weight: 500;
  color: #333;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.item-desc {
  font-size: 12px;
  color: #999;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-top: 1px;
}

/* 过渡动画 */
.mention-dropdown-enter-active {
  transition: all 0.15s ease-out;
}
.mention-dropdown-leave-active {
  transition: all 0.1s ease-in;
}
.mention-dropdown-enter-from {
  opacity: 0;
  transform: translateY(8px);
}
.mention-dropdown-leave-to {
  opacity: 0;
  transform: translateY(8px);
}
</style>
