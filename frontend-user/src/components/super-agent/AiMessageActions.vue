<script setup lang="ts">
/**
 * AiMessageActions - AI 消息操作栏 + 快捷回复
 *
 * 从 MessageItem.vue 提取（Phase 2.1b 拆分）。
 * 职责：复制/重新生成/版本导航/快捷回复
 */
import type { ChatMessage } from '../../types/super-agent'
import RegenerateMenu from './RegenerateMenu.vue'
import type { SkillOption } from './RegenerateMenu.vue'
import VersionNavigator from './VersionNavigator.vue'

const props = defineProps<{
  msg: ChatMessage
  availableSkills?: SkillOption[]
  isGlobalStreaming?: boolean
  quickReplies?: string[]
}>()

const emit = defineEmits<{
  (e: 'regenerate', messageId: string, forceSkill?: string): void
  (e: 'stop-session'): void
  (e: 'switch-version', targetMessageId: string): void
  (e: 'quick-reply', text: string): void
}>()

function handleCopy() {
  navigator.clipboard.writeText(props.msg.content).catch(() => {})
}

function handleRegenerate(forceSkill?: string) {
  if (props.msg.messageId) {
    emit('regenerate', props.msg.messageId, forceSkill)
  }
}

function handleSwitchVersion(targetMessageId: string) {
  emit('switch-version', targetMessageId)
}
</script>

<template>
  <!-- 完成状态操作栏 -->
  <div v-if="!msg.isStreaming" class="ai-message-actions">
    <div class="ai-actions-left">
      <button class="msg-action-btn" @click="handleCopy" title="复制">
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.2">
          <rect x="4.5" y="4.5" width="7" height="7" rx="1.5"/>
          <path d="M9.5 4.5V3A1.5 1.5 0 008 1.5H3A1.5 1.5 0 001.5 3v5A1.5 1.5 0 003 9.5h1.5"/>
        </svg>
      </button>
      <RegenerateMenu
        :skills="availableSkills ?? []"
        :disabled="isGlobalStreaming"
        :is-streaming="msg.isStreaming"
        @regenerate="handleRegenerate"
        @stop="emit('stop-session')"
      />
    </div>
    <VersionNavigator
      v-if="msg.siblings && msg.siblings.length > 1"
      :siblings="msg.siblings"
      :current-index="msg.siblingIndex ?? 0"
      :disabled="isGlobalStreaming"
      @switch="handleSwitchVersion"
    />
  </div>
  <!-- 流式生成中的操作栏 -->
  <div v-else class="ai-message-actions ai-message-actions--streaming">
    <div class="ai-actions-left">
      <RegenerateMenu
        :skills="[]"
        :is-streaming="true"
        @stop="emit('stop-session')"
      />
    </div>
  </div>

  <!-- 快捷回复 -->
  <div v-if="quickReplies?.length && !msg.isStreaming" class="ai-quick-replies">
    <button
      v-for="(reply, idx) in quickReplies"
      :key="idx"
      class="ai-quick-reply-chip"
      @click="emit('quick-reply', reply)"
    >
      {{ reply }}
    </button>
  </div>
</template>
