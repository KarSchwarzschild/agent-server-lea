<script setup lang="ts">
/**
 * 聊天输入区域组件（v5.0 ChatGPT 风格紧凑单框）
 *
 * 重构：工具栏移到输入框内部底部，发送/停止按钮在右下角
 * 快捷回复已迁移到 MessageItem.vue 的 AI 消息底部
 */
import { ref } from 'vue'
import { NInput, NIcon, NTag, NTooltip } from 'naive-ui'
import { SendOutline, MapOutline, AttachOutline, ChatbubblesOutline, LibraryOutline, StopCircleOutline } from '@vicons/ionicons5'
import SkillMentionDropdown from '../SkillMentionDropdown.vue'
import AttachmentPreview from './AttachmentPreview.vue'
import KBDocPicker from './KBDocPicker.vue'
import type { MentionSkill } from '../../composables/useSkillMention'
import type { AttachmentInfo } from '../../types/super-agent'

const props = defineProps<{
  inputText: string
  loading: boolean
  disabled?: boolean
  disabledPlaceholder?: string
  selectedSkill: MentionSkill | null
  showMentionDropdown: boolean
  mentionFilteredList: MentionSkill[]
  mentionDropdownIndex: number
  mentionAnchorRect: { bottom: number; left: number; width: number } | null
  chatMode?: 'chat' | 'plan'
  chatModeDisabled?: boolean
  attachments?: AttachmentInfo[]
  isDragging?: boolean
  hasUploading?: boolean
  /** 当前关联的 artifact（画布模式下显示文档标签） */
  activeArtifact?: { id: string; title: string; versionIndex: number } | null
  /** @deprecated v5.0: 快捷回复已迁移到 MessageItem，保留 prop 兼容 */
  quickReplies?: string[]
}>()

const emit = defineEmits<{
  (e: 'update:inputText', value: string): void
  (e: 'send'): void
  (e: 'stop'): void
  (e: 'clear-skill'): void
  (e: 'clear-artifact'): void
  (e: 'mention-input', text: string, cursorPos: number): void
  (e: 'mention-keydown', event: KeyboardEvent): boolean | void
  (e: 'mention-select', skill: MentionSkill): void
  (e: 'mention-hover', index: number): void
  (e: 'update:chatMode', value: 'chat' | 'plan'): void
  (e: 'add-files', files: FileList): void
  (e: 'import-kb-docs', docIds: string[]): void
  (e: 'remove-attachment', id: string): void
  (e: 'paste', event: ClipboardEvent): void
  (e: 'drag-enter', event: DragEvent): void
  (e: 'drag-leave', event: DragEvent): void
  (e: 'drag-over', event: DragEvent): void
  (e: 'drop', event: DragEvent): void
  (e: 'quick-reply', text: string): void
}>()

const pcInputWrapperRef = ref<HTMLElement>()
const fileInputRef = ref<HTMLInputElement>()
const showKBPicker = ref(false)
const inputFocused = ref(false)

function onInput() {
  const textarea = pcInputWrapperRef.value?.querySelector('textarea')
  const cursorPos = textarea?.selectionStart ?? 0
  emit('mention-input', (textarea?.value || ''), cursorPos)
}

function onKeydown(e: KeyboardEvent) {
  emit('mention-keydown', e)
}

function onMentionSelect(skill: MentionSkill) {
  emit('mention-select', skill)
}

function triggerFileInput() {
  fileInputRef.value?.click()
}

function onFileSelected(e: Event) {
  const input = e.target as HTMLInputElement
  if (input.files && input.files.length > 0) {
    emit('add-files', input.files)
    input.value = ''
  }
}

function onPaste(e: ClipboardEvent) {
  emit('paste', e)
}

function onKBDocsConfirm(docIds: string[]) {
  emit('import-kb-docs', docIds)
}
</script>

<template>
  <div
    class="input-section"
    @dragenter="emit('drag-enter', $event)"
    @dragleave="emit('drag-leave', $event)"
    @dragover="emit('drag-over', $event)"
    @drop="emit('drop', $event)"
  >
    <!-- 拖拽高亮遮罩 -->
    <div v-if="isDragging" class="drag-overlay">
      <span>释放文件以添加附件</span>
    </div>

    <!-- 隐藏的文件选择器 -->
    <input
      ref="fileInputRef"
      type="file"
      multiple
      accept=".pdf,.docx,.xlsx,.pptx,.txt,.md,.jpg,.jpeg,.png,.gif,.webp"
      style="display: none"
      @change="onFileSelected"
    />

    <!-- v5.0: ChatGPT 风格紧凑输入框 -->
    <div class="compact-input-box" :class="{ expanded: inputFocused || inputText.length > 0 || (attachments && attachments.length > 0) }" ref="pcInputWrapperRef"
         @focusin="inputFocused = true" @focusout="inputFocused = false">
      <!-- 附件预览（输入框内顶部） -->
      <AttachmentPreview
        v-if="attachments && attachments.length > 0"
        :attachments="attachments"
        class="inner-attachments"
        @remove="emit('remove-attachment', $event)"
      />

      <!-- 画布模式关联文档标签 -->
      <div v-if="activeArtifact" class="artifact-context-tag">
        <span class="act-icon">📄</span>
        <span class="act-title">{{ activeArtifact.title }}</span>
        <span class="act-version">(v{{ activeArtifact.versionIndex }})</span>
        <button class="act-close" @click="emit('clear-artifact')">×</button>
      </div>

      <!-- 文本输入区（全宽，在上） -->
      <n-input
        :value="inputText"
        @update:value="emit('update:inputText', $event)"
        type="textarea"
        :placeholder="disabled ? (disabledPlaceholder || '请先审批调研计划后再继续对话') : selectedSkill ? `使用「${selectedSkill.display_name}」处理...` : '输入您的问题...（输入 @ 选择技能）'"
        :autosize="{ minRows: 1, maxRows: 6 }"
        :disabled="loading || disabled"
        :bordered="false"
        class="inner-textarea"
        @keydown="onKeydown"
        @input="onInput"
        @paste="onPaste"
      />

      <!-- 工具栏（在下）— mousedown.prevent 阻止按钮点击触发 focusin 导致布局突变 -->
      <div class="inner-toolbar" @mousedown.prevent>
        <div class="toolbar-left">
          <n-tooltip trigger="hover">
            <template #trigger>
              <button class="toolbar-icon-btn" @click="triggerFileInput">
                <n-icon :component="AttachOutline" :size="16" />
              </button>
            </template>
            添加附件（支持 PDF、Word、Excel 等）
          </n-tooltip>
          <n-tooltip trigger="hover">
            <template #trigger>
              <button class="toolbar-icon-btn kb-btn" @click="showKBPicker = true">
                <n-icon :component="LibraryOutline" :size="16" />
              </button>
            </template>
            从知识库引用文档
          </n-tooltip>

          <!-- 已选 Skill 标签 -->
          <n-tag v-if="selectedSkill" closable size="small" type="info" @close="emit('clear-skill')">
            {{ selectedSkill.display_name }}
          </n-tag>

          <!-- 模式切换 -->
          <div class="mode-switch">
            <n-tooltip trigger="hover" :style="{ maxWidth: '260px' }">
              <template #trigger>
                <button
                  class="mode-btn"
                  :class="{ active: chatMode === 'chat' || !chatMode }"
                  :disabled="chatModeDisabled"
                  @click="chatMode !== 'chat' && emit('update:chatMode', 'chat')"
                >
                  <n-icon :component="ChatbubblesOutline" :size="13" />
                  对话
                </button>
              </template>
              <div>
                <div style="font-weight: 600; margin-bottom: 4px;">对话模式</div>
                <div style="font-size: 12px; opacity: 0.85;">直接执行任务，AI 回复以普通消息呈现</div>
              </div>
            </n-tooltip>
            <n-tooltip trigger="hover" :style="{ maxWidth: '260px' }">
              <template #trigger>
                <button
                  class="mode-btn"
                  :class="{ active: chatMode === 'plan' }"
                  :disabled="chatModeDisabled"
                  @click="chatMode !== 'plan' && emit('update:chatMode', 'plan')"
                >
                  <n-icon :component="MapOutline" :size="13" />
                  计划
                </button>
              </template>
              <div>
                <div style="font-weight: 600; margin-bottom: 4px;">计划模式</div>
                <div style="font-size: 12px; opacity: 0.85;">AI 会先生成执行计划供您审批，确认后再执行，适合复杂任务</div>
              </div>
            </n-tooltip>
          </div>
        </div>

        <!-- 发送/停止按钮 -->
        <button
          v-if="loading"
          class="send-btn send-btn--stop"
          @click="emit('stop')"
          title="停止生成"
        >
          <span class="stop-icon"></span>
        </button>
        <button
          v-else
          class="send-btn"
          :disabled="disabled || hasUploading || (!inputText.trim() && !(attachments && attachments.length > 0))"
          @click="emit('send')"
          title="发送"
        >
          <n-icon :component="SendOutline" :size="18" />
        </button>
      </div>
    </div>

    <!-- @mention 下拉菜单 -->
    <SkillMentionDropdown
      :show="showMentionDropdown"
      :items="mentionFilteredList"
      :active-index="mentionDropdownIndex"
      :anchor-rect="mentionAnchorRect"
      @select="onMentionSelect"
      @hover="emit('mention-hover', $event)"
    />

    <!-- 知识库文档选择器 -->
    <KBDocPicker
      v-model:show="showKBPicker"
      @confirm="onKBDocsConfirm"
    />
  </div>
</template>

<style scoped>
.input-section {
  position: relative;
  padding: 12px 20px;
  background: transparent;
  z-index: 1;
}

/* 拖拽高亮遮罩 */
.drag-overlay {
  position: absolute;
  inset: 0;
  background: rgba(32, 128, 240, 0.08);
  border: 2px dashed #2080f0;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 10;
  font-size: 14px;
  color: #2080f0;
  font-weight: 500;
  pointer-events: none;
}

/* 快捷回复 Chip */
.quick-replies {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
  overflow-x: auto;
  scrollbar-width: none;
}
.quick-replies::-webkit-scrollbar {
  display: none;
}
.quick-reply-chip {
  flex-shrink: 0;
  padding: 5px 12px;
  border: 1px solid #e0e0e6;
  border-radius: 16px;
  background: #f8f9fa;
  color: #374151;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s;
  white-space: nowrap;
}
.quick-reply-chip:hover {
  background: #e8f0fe;
  border-color: #3b82f6;
  color: #1d4ed8;
}

/* v5.0: 紧凑输入框（ChatGPT 风格）
   默认态：工具栏和输入框同一行（单行紧凑）
   聚焦态/有内容：切换为上下两层（textarea 在上，工具栏在下） */
.compact-input-box {
  display: flex;
  flex-direction: row;
  align-items: center;
  border: 1px solid #c8c8d0;
  border-radius: 24px;
  background: #f5f7f9;
  padding: 0 8px 0 4px;
  transition: border-color 0.2s, background 0.2s, border-radius 0.2s, min-height 0.2s ease, padding 0.2s;
}
/* 聚焦/有内容：切换为上下布局 */
.compact-input-box.expanded {
  flex-direction: column;
  align-items: stretch;
  background: #fff;
  border-color: #a0a0ae;
  border-radius: 18px;
  min-height: 120px;
  padding: 0;
}

.inner-attachments {
  padding: 10px 14px 0;
}

/* 画布模式关联文档标签 */
.artifact-context-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin: 8px 14px 0;
  padding: 4px 8px 4px 6px;
  background: #f0f7ff;
  border: 1px solid #d0e3f7;
  border-radius: 6px;
  font-size: 12px;
  color: #1a73e8;
  max-width: 280px;
}
.act-icon { flex-shrink: 0; }
.act-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.act-version {
  flex-shrink: 0;
  color: #888;
  font-size: 11px;
}
.act-close {
  flex-shrink: 0;
  border: none;
  background: none;
  color: #1a73e8;
  font-size: 14px;
  cursor: pointer;
  padding: 0 2px;
  line-height: 1;
  opacity: 0.6;
}
.act-close:hover { opacity: 1; }

/* 输入框 */
.inner-textarea {
  padding: 0;
  flex: 1;
  min-width: 0;
}
/* 聚焦态：textarea 纵向拉伸占满 */
.compact-input-box.expanded .inner-textarea {
  display: flex;
  flex-direction: column;
}
.compact-input-box.expanded .inner-textarea :deep(.n-input) {
  flex: 1;
  display: flex;
  flex-direction: column;
}
.compact-input-box.expanded .inner-textarea :deep(.n-input-wrapper) {
  flex: 1;
}
.compact-input-box.expanded .inner-textarea :deep(.n-input__textarea-el) {
  flex: 1;
}
.inner-textarea :deep(.n-input) {
  --n-border: none !important;
  --n-border-hover: none !important;
  --n-border-focus: none !important;
  --n-box-shadow-focus: none !important;
  --n-color: transparent !important;
  --n-color-focus: transparent !important;
}
.inner-textarea :deep(.n-input-wrapper) {
  padding: 0 !important;
}
.inner-textarea :deep(.n-input__textarea-el) {
  padding: 8px 12px 4px !important;
  font-size: 15px;
  line-height: 1.5;
}
/* 展开态 textarea 内边距（左侧配合 border-radius 18px） */
.compact-input-box.expanded .inner-textarea :deep(.n-input__textarea-el) {
  padding: 14px 20px 4px 18px !important;
}
.inner-textarea :deep(.n-input__border),
.inner-textarea :deep(.n-input__state-border) {
  display: none !important;
}

/* 工具栏 */
.inner-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 4px 0 0;
  flex-shrink: 0;
}
/* 聚焦态工具栏加底部 padding */
.compact-input-box.expanded .inner-toolbar {
  padding: 0 8px 6px 10px;
}
.toolbar-left {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
}
.toolbar-icon-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: none;
  background: transparent;
  border-radius: 8px;
  color: #b4b4b4;
  cursor: pointer;
  transition: all 0.15s;
}
.toolbar-icon-btn:hover {
  background: #f0f0f0;
  color: #666;
}
.kb-btn {
  color: #a78bfa;
}
.kb-btn:hover {
  color: #7c3aed;
  background: #f3f0ff;
}

/* 模式切换 */
.mode-switch {
  display: inline-flex;
  border: 1px solid #e5e5ea;
  border-radius: 10px;
  overflow: hidden;
  background: #f5f5f7;
  margin-left: 4px;
}
.mode-btn {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 3px 10px;
  border: none;
  background: transparent;
  color: #aaa;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
}
.mode-btn:hover:not(:disabled):not(.active) {
  color: #666;
  background: #ebebf0;
}
.mode-btn.active {
  color: #18a058;
  background: #fff;
  font-weight: 500;
  box-shadow: 0 1px 3px rgba(0,0,0,0.06);
}
.mode-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

/* 发送/停止按钮 */
.send-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  border-radius: 50%;
  background: #000;
  color: #fff;
  cursor: pointer;
  transition: all 0.15s;
  flex-shrink: 0;
  margin-left: 8px;
}
.send-btn :deep(svg) {
  width: 14px;
  height: 14px;
}
.send-btn:hover:not(:disabled) {
  background: #333;
}
.send-btn:disabled {
  background: #d4d4d8;
  cursor: default;
}
.send-btn--stop {
  background: #fff;
  border: 1.5px solid #888;
  color: #666;
  border-radius: 50%;
  width: 26px;
  height: 26px;
  margin-left: 8px;
}
.send-btn--stop:hover {
  background: #f5f5f5;
  border-color: #333;
}
.stop-icon {
  display: block;
  width: 8px;
  height: 8px;
  background: #666;
  border-radius: 1.5px;
}
.send-btn--stop:hover .stop-icon {
  background: #333;
}
</style>

<!-- 非 scoped：强制覆盖 Naive UI n-input 样式 -->
<style>
.compact-input-box .n-input {
  background: transparent !important;
  border-radius: 0 !important;
}
.compact-input-box .n-input__border,
.compact-input-box .n-input__state-border {
  display: none !important;
}
/* 展开态 textarea + placeholder 对齐（Naive UI placeholder 是独立元素） */
.compact-input-box.expanded .n-input__textarea-el,
.compact-input-box.expanded .n-input__placeholder {
  padding-left: 18px !important;
  padding-top: 14px !important;
}
</style>
