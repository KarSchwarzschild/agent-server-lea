<script setup lang="ts">
/**
 * 重新生成按钮 + Skill 选择下拉菜单
 *
 * 分离式双区域按钮：主按钮直接触发，箭头展开 Skill 菜单
 * 下拉样式复用 SkillMentionDropdown 的视觉风格
 */
import { ref, onMounted, onUnmounted } from 'vue'

export interface SkillOption {
  name: string
  displayName: string
  description: string
}

const props = defineProps<{
  skills: SkillOption[]
  disabled?: boolean
  isStreaming?: boolean
}>()

const emit = defineEmits<{
  (e: 'regenerate', forceSkill?: string): void
  (e: 'stop'): void
}>()

const showMenu = ref(false)
const menuRef = ref<HTMLElement>()
const triggerRef = ref<HTMLElement>()
const menuStyle = ref<Record<string, string>>({})

// 技能图标映射（同 SkillMentionDropdown）
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
  'kb-query': '📖',
  'literature-review': '📝',
  'quick-research': '⚡',
  'ppt-generator': '📊',
  'speech-writer': '🎤',
  'chart-generator': '📈',
  'work-report': '📋',
  'reference-formatter': '📎',
  'citation-auditor': '🔎',
  'edu-data-search': '📊',
  'ai-trace-remover': '✨',
  'web-info-collector': '🌐',
  'class-review-report': '📝',
  'regulation-reviewer': '📜',
  'math-solver': '🔢',
}

function getSkillIcon(name: string): string {
  return skillIconMap[name] || '⚡'
}

function handleMainClick() {
  if (props.isStreaming) {
    emit('stop')
  } else {
    emit('regenerate')
  }
}

function toggleMenu() {
  if (!showMenu.value && triggerRef.value) {
    const rect = triggerRef.value.getBoundingClientRect()
    const viewH = window.innerHeight
    menuStyle.value = {
      position: 'fixed',
      bottom: `${viewH - rect.top + 8}px`,
      right: `${window.innerWidth - rect.right}px`,
    }
  }
  showMenu.value = !showMenu.value
}

function handleSkillSelect(skillName?: string) {
  showMenu.value = false
  emit('regenerate', skillName)
}

// 点击外部关闭
function handleClickOutside(e: MouseEvent) {
  if (
    showMenu.value &&
    menuRef.value && !menuRef.value.contains(e.target as Node) &&
    triggerRef.value && !triggerRef.value.contains(e.target as Node)
  ) {
    showMenu.value = false
  }
}

onMounted(() => document.addEventListener('click', handleClickOutside))
onUnmounted(() => document.removeEventListener('click', handleClickOutside))
</script>

<template>
  <div class="regenerate-menu" :class="{ disabled }">
    <!-- 流式生成中：停止按钮 -->
    <template v-if="isStreaming">
      <button class="regen-btn regen-btn--stop" @click="handleMainClick" title="停止生成">
        <svg width="14" height="14" viewBox="0 0 14 14" fill="currentColor">
          <rect x="3" y="3" width="8" height="8" rx="1"/>
        </svg>
        <span class="regen-label">停止</span>
      </button>
    </template>

    <!-- 正常状态：重新生成 + 下拉 -->
    <template v-else>
      <button
        class="regen-btn regen-btn--main"
        :disabled="disabled"
        @click="handleMainClick"
        title="重新生成"
      >
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.3">
          <path d="M1.5 7a5.5 5.5 0 0 1 9.27-4M12.5 7a5.5 5.5 0 0 1-9.27 4" stroke-linecap="round"/>
          <path d="M10.77 1v2h-2M3.23 13v-2h2" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        <span class="regen-label">重新生成</span>
      </button>

      <div v-if="skills.length > 0" class="dropdown-anchor">
        <button
          ref="triggerRef"
          class="regen-btn regen-btn--dropdown"
          :disabled="disabled"
          title="选择不同方式重新生成"
          @click.stop="toggleMenu"
        >
          <svg width="10" height="10" viewBox="0 0 10 10" fill="currentColor">
            <path d="M2 3.5l3 3 3-3"/>
          </svg>
        </button>

      </div>
      <!-- 下拉面板 Teleport 到 body，避免 overflow:hidden 裁剪 -->
      <Teleport to="body">
        <Transition name="regen-dropdown">
          <div v-if="showMenu" ref="menuRef" class="regen-skill-dropdown" :style="menuStyle">
            <div class="regen-dropdown-header">切换不同方式重新生成</div>
            <div class="regen-dropdown-list">
              <!-- 自动选择 -->
              <div class="regen-dropdown-item" @click="handleSkillSelect(undefined)">
                <span class="regen-item-icon">✨</span>
                <div class="regen-item-content">
                  <span class="regen-item-name">自动选择最合适的方式</span>
                  <span class="regen-item-desc">推荐</span>
                </div>
              </div>
              <!-- Skill 列表 -->
              <div
                v-for="skill in skills"
                :key="skill.name"
                class="regen-dropdown-item"
                @click="handleSkillSelect(skill.name)"
              >
                <span class="regen-item-icon">{{ getSkillIcon(skill.name) }}</span>
                <div class="regen-item-content">
                  <span class="regen-item-name">{{ skill.displayName }}</span>
                  <span class="regen-item-desc">{{ skill.description }}</span>
                </div>
              </div>
            </div>
          </div>
        </Transition>
      </Teleport>
    </template>
  </div>
</template>

<style>
.regenerate-menu {
  display: inline-flex;
  align-items: center;
  gap: 0;
}
.regenerate-menu.disabled {
  opacity: 0.5;
  pointer-events: none;
}
.regen-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  border: none;
  background: transparent;
  cursor: pointer;
  font-size: 12px;
  color: #666;
  padding: 4px 8px;
  border-radius: 4px;
  transition: all 0.15s;
  white-space: nowrap;
}
.regen-btn:hover:not(:disabled) {
  background: rgba(0, 0, 0, 0.06);
  color: #333;
}
.regen-btn:disabled {
  color: #ccc;
  cursor: default;
}
.regen-btn--stop {
  color: #e74c3c;
}
.regen-btn--stop:hover {
  background: rgba(231, 76, 60, 0.08);
  color: #c0392b;
}
.regen-btn--dropdown {
  padding: 4px 4px;
  margin-left: -2px;
  border-radius: 0 4px 4px 0;
}
.regen-btn--main {
  border-radius: 4px 0 0 4px;
}
.regen-label {
  font-size: 12px;
}

/* 下拉面板（复用 SkillMentionDropdown 风格，Teleport 到 body） */
.regen-skill-dropdown {
  position: fixed;
  width: 320px;
  max-height: 340px;
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 4px 24px rgba(0, 0, 0, 0.12), 0 0 0 1px rgba(0, 0, 0, 0.05);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  z-index: 1000;
}

.regen-dropdown-header {
  padding: 8px 14px 4px;
  font-size: 12px;
  color: #999;
  font-weight: 500;
  flex-shrink: 0;
}

.regen-dropdown-list {
  overflow-y: auto;
  max-height: 290px;
  padding: 4px 6px 6px;
}

.regen-dropdown-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s;
}
.regen-dropdown-item:hover {
  background: #f0f5ff;
}

.regen-item-icon {
  font-size: 18px;
  flex-shrink: 0;
  width: 28px;
  text-align: center;
}

.regen-item-content {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.regen-item-name {
  font-size: 14px;
  font-weight: 500;
  color: #333;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.regen-item-desc {
  font-size: 12px;
  color: #999;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-top: 1px;
}

/* 过渡动画 */
.regen-dropdown-enter-active {
  transition: all 0.15s ease-out;
}
.regen-dropdown-leave-active {
  transition: all 0.1s ease-in;
}
.regen-dropdown-enter-from {
  opacity: 0;
  transform: translateY(8px);
}
.regen-dropdown-leave-to {
  opacity: 0;
  transform: translateY(8px);
}
</style>
