<script setup lang="ts">
/**
 * UserMessage — 用户消息渲染组件
 *
 * 从 MessageItem.vue 提取（Phase 2.1b 拆分）。
 * 职责：用户消息文本 + 附件展示 + 编辑模式 + 版本导航
 */
import { ref } from 'vue'
import type { ChatMessage, AttachmentInfo } from '../../types/super-agent'
import InlineEditor from './InlineEditor.vue'
import VersionNavigator from './VersionNavigator.vue'

const props = defineProps<{
  msg: ChatMessage
  isMobile?: boolean
  isGlobalStreaming?: boolean
  messagesAfterCount?: number
}>()

const emit = defineEmits<{
  (e: 'edit-message', messageId: string, newContent: string): void
  (e: 'switch-version', targetMessageId: string): void
}>()

const isEditing = ref(false)

function startEditing() {
  isEditing.value = true
}

function cancelEditing() {
  isEditing.value = false
}

function submitEdit(newContent: string) {
  isEditing.value = false
  emit('edit-message', props.msg.id, newContent)
}

function handleSwitchVersion(targetMessageId: string) {
  emit('switch-version', targetMessageId)
}
</script>

<template>
  <div class="message user-message" :class="{ 'mobile-message': isMobile }">
    <div class="message-content user-content" :class="{ 'user-content--editing': isEditing }">
      <template v-if="isEditing">
        <InlineEditor
          :content="msg.content"
          :truncate-count="messagesAfterCount"
          @submit="submitEdit"
          @cancel="cancelEditing"
        />
      </template>
      <template v-else>
        {{ msg.content }}
        <div v-if="msg.attachments && msg.attachments.length > 0" class="user-attachments">
          <div
            v-for="att in msg.attachments"
            :key="att.id"
            class="user-att-item"
          >
            <img
              v-if="att.fileCategory === 'image' && att.thumbnailUrl"
              :src="att.thumbnailUrl"
              :alt="att.filename"
              class="user-att-thumb"
            />
            <span v-else class="user-att-icon">{{ att.fileExt?.toUpperCase() }}</span>
            <span class="user-att-name">{{ att.filename }}</span>
          </div>
        </div>
      </template>
    </div>
    <div v-if="!isEditing && !isGlobalStreaming" class="user-message-actions">
      <button class="msg-action-btn" @click="startEditing" title="编辑">
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.2">
          <path d="M10.5 1.5l2 2-8 8H2.5v-2z" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </button>
      <VersionNavigator
        v-if="msg.siblings && msg.siblings.length > 1"
        :siblings="msg.siblings"
        :current-index="msg.siblingIndex ?? 0"
        :disabled="isGlobalStreaming"
        @switch="handleSwitchVersion"
      />
    </div>
  </div>
</template>


<style>
.user-message {
  justify-content: flex-end;
}

.user-content {
  background: #2080f0;
  color: #fff;
  border-bottom-right-radius: 4px;
  white-space: pre-wrap;
}
.user-content--editing {
  background: transparent;
  color: inherit;
  padding: 0;
  border-radius: 0;
}

/* 用户消息附件 */
.user-attachments {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid rgba(255, 255, 255, 0.2);
}

.user-att-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px;
  background: rgba(255, 255, 255, 0.15);
  border-radius: 6px;
  font-size: 12px;
}

.user-att-thumb {
  width: 28px;
  height: 28px;
  border-radius: 4px;
  object-fit: cover;
}

.user-att-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.2);
  font-size: 9px;
  font-weight: 600;
}

.user-att-name {
  max-width: 120px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.user-message-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 4px;
  opacity: 0;
  transition: opacity 0.2s;
  justify-content: flex-end;
}
.user-message:hover .user-message-actions {
  opacity: 1;
}

/* AI 消息操作栏 */
</style>
