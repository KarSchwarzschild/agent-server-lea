<script setup lang="ts">
/**
 * Cursor 风格 slash 命令面板 — 输入框内敲 "/" 唤起，展示 activeAgentSkills。
 *
 * 选中一项后，父组件负责把 textarea 值替换为 `请使用「XX」技能：`。
 * 面板本身只负责：过滤、键盘导航、发选中事件。
 */
import { computed, ref, watch } from 'vue'
import type { SkillInfo } from '../../api/agent-chat'

const props = defineProps<{
  visible: boolean
  keyword: string
  skills: SkillInfo[]
}>()

const emit = defineEmits<{
  (e: 'pick', skill: SkillInfo): void
  (e: 'close'): void
}>()

const filtered = computed(() => {
  const kw = props.keyword.trim().toLowerCase()
  if (!kw) return props.skills.slice(0, 8)
  return props.skills.filter(s => {
    const name = (s.display_name || s.name || '').toLowerCase()
    const desc = (s.description || '').toLowerCase()
    return name.includes(kw) || desc.includes(kw)
  }).slice(0, 8)
})

const activeIdx = ref(0)

watch(() => props.keyword, () => { activeIdx.value = 0 })
watch(() => props.visible, v => { if (v) activeIdx.value = 0 })

const onKey = (e: KeyboardEvent) => {
  if (!props.visible || filtered.value.length === 0) return
  switch (e.key) {
    case 'ArrowDown':
      e.preventDefault()
      activeIdx.value = (activeIdx.value + 1) % filtered.value.length
      break
    case 'ArrowUp':
      e.preventDefault()
      activeIdx.value = (activeIdx.value - 1 + filtered.value.length) % filtered.value.length
      break
    case 'Enter':
      e.preventDefault()
      emit('pick', filtered.value[activeIdx.value])
      break
    case 'Escape':
      e.preventDefault()
      emit('close')
      break
  }
}

defineExpose({ onKey, hasItems: () => filtered.value.length > 0 })
</script>

<template>
  <div v-if="visible && filtered.length" class="skill-picker">
    <div class="skill-picker-header">技能 (↑↓ 选择, Enter 确认, Esc 关闭)</div>
    <div
      v-for="(s, idx) in filtered"
      :key="s.id"
      class="skill-picker-item"
      :class="{ active: idx === activeIdx }"
      @mouseenter="activeIdx = idx"
      @mousedown.prevent="emit('pick', s)"
    >
      <div class="skill-name">{{ s.display_name || s.name }}</div>
      <div v-if="s.description" class="skill-desc">{{ s.description }}</div>
    </div>
  </div>
</template>

<style scoped>
.skill-picker {
  position: absolute;
  bottom: 100%;
  left: 0;
  right: 0;
  margin-bottom: 8px;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.08);
  max-height: 320px;
  overflow-y: auto;
  z-index: 20;
}

.skill-picker-header {
  padding: 8px 12px;
  font-size: 12px;
  color: #6b7280;
  border-bottom: 1px solid #f3f4f6;
  background: #fafafa;
}

.skill-picker-item {
  padding: 10px 12px;
  cursor: pointer;
  border-bottom: 1px solid #f9fafb;
  transition: background 0.1s;
}

.skill-picker-item:last-child {
  border-bottom: none;
}

.skill-picker-item.active {
  background: #eff6ff;
}

.skill-name {
  font-size: 14px;
  color: #111827;
  font-weight: 500;
}

.skill-desc {
  font-size: 12px;
  color: #6b7280;
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
