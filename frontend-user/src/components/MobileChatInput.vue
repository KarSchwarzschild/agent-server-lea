<template>
  <div class="mobile-chat-input" :class="{ 'keyboard-visible': isKeyboardVisible }">
    <!-- 已选 skill tag -->
    <div v-if="selectedSkill" class="mobile-skill-tag">
      <span class="skill-tag-label">{{ selectedSkill.display_name }}</span>
      <button class="skill-tag-close" @click="$emit('clear-skill')">×</button>
    </div>

    <!-- 附件预览区 -->
    <AttachmentPreview
      v-if="attachments && attachments.length > 0"
      :attachments="attachments"
      @remove="$emit('remove-attachment', $event)"
    />

    <!-- 隐藏的文件选择器 -->
    <input
      ref="fileInputRef"
      type="file"
      multiple
      accept=".pdf,.docx,.xlsx,.pptx,.txt,.md,.jpg,.jpeg,.png,.gif,.webp"
      style="display: none"
      @change="onFileSelected"
    />

    <div class="input-container">
      <!-- 技能选择按钮 -->
      <button
        class="skill-btn"
        :class="{ active: showSkillSheet }"
        @click="showSkillSheet = true"
        title="选择技能"
      >
        <n-icon :component="AppsOutline" :size="20" />
      </button>
      <!-- 附件按钮 -->
      <button
        class="attach-btn"
        @click="triggerFileInput"
        title="添加附件"
      >
        <n-icon :component="AttachOutline" :size="20" />
      </button>
      <!-- 模式切换按钮组 -->
      <div class="mobile-mode-switch">
        <button
          class="mode-btn"
          :class="{ active: chatMode === 'chat' || !chatMode }"
          :disabled="chatModeDisabled"
          @click="chatMode !== 'chat' && emit('update:chatMode', 'chat')"
        >
          <n-icon :component="ChatbubblesOutline" :size="14" />
        </button>
        <button
          class="mode-btn"
          :class="{ active: chatMode === 'plan' }"
          :disabled="chatModeDisabled"
          @click="chatMode !== 'plan' && emit('update:chatMode', 'plan')"
        >
          <n-icon :component="MapOutline" :size="14" />
        </button>
      </div>
      <textarea
        ref="textareaRef"
        v-model="inputValue"
        :placeholder="computedPlaceholder"
        :rows="1"
        :disabled="disabled"
        @focus="handleFocus"
        @blur="handleBlur"
        @input="autoResize"
        @keydown.enter.exact.prevent="handleSend"
      />
      <button
        class="send-btn"
        :class="{ active: canSend, loading: loading }"
        :disabled="!canSend || disabled || loading || hasUploading"
        @click="handleSend"
      >
        <n-spin v-if="loading" :size="18" />
        <n-icon v-else :component="SendOutline" :size="20" />
      </button>
    </div>
    <!-- iOS安全区域（键盘未弹出时显示） -->
    <div v-if="!isKeyboardVisible" class="safe-area-bottom" />

    <!-- 技能选择底部抽屉 -->
    <n-drawer
      v-model:show="showSkillSheet"
      placement="bottom"
      :height="'auto'"
      :max-height="400"
      :trap-focus="false"
    >
      <n-drawer-content title="选择技能" :native-scrollbar="false">
        <div class="skill-sheet-list">
          <div
            v-for="skill in skills"
            :key="skill.name"
            class="skill-sheet-item"
            @click="handleSelectSkill(skill)"
          >
            <span class="skill-sheet-icon">{{ skill.isSpecial ? '🎭' : '⚡' }}</span>
            <div class="skill-sheet-info">
              <div class="skill-sheet-name">{{ skill.display_name }}</div>
              <div class="skill-sheet-desc">{{ skill.description }}</div>
            </div>
          </div>
        </div>
      </n-drawer-content>
    </n-drawer>
  </div>
</template>

<script setup lang="ts">
/**
 * MobileChatInput - 移动端一体式聊天输入框
 * 特点：
 * - 输入框和发送按钮一体化设计
 * - 自动调整高度
 * - iOS虚拟键盘适配
 * - 技能选择按钮 + 底部 ActionSheet
 */
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { NIcon, NSpin, NDrawer, NDrawerContent } from 'naive-ui'
import { SendOutline, AppsOutline, MapOutline, AttachOutline, ChatbubblesOutline } from '@vicons/ionicons5'
import type { MentionSkill } from '../composables/useSkillMention'
import type { AttachmentInfo } from '../types/super-agent'
import AttachmentPreview from './super-agent/AttachmentPreview.vue'

const props = withDefaults(defineProps<{
  modelValue: string
  placeholder?: string
  disabled?: boolean
  loading?: boolean
  selectedSkill?: MentionSkill | null
  skills?: MentionSkill[]
  chatMode?: 'chat' | 'plan'
  chatModeDisabled?: boolean
  attachments?: AttachmentInfo[]
  hasUploading?: boolean
}>(), {
  placeholder: '输入您的问题...',
  disabled: false,
  loading: false,
  selectedSkill: null,
  skills: () => [],
  chatMode: 'chat',
  chatModeDisabled: false,
  attachments: () => [],
  hasUploading: false,
})

const emit = defineEmits<{
  'update:modelValue': [value: string]
  'send': []
  'select-skill': [skill: MentionSkill]
  'clear-skill': []
  'update:chatMode': [value: 'chat' | 'plan']
  'add-files': [files: FileList]
  'remove-attachment': [id: string]
}>()

const textareaRef = ref<HTMLTextAreaElement>()
const fileInputRef = ref<HTMLInputElement>()
const isKeyboardVisible = ref(false)
const showSkillSheet = ref(false)

const inputValue = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val)
})

const canSend = computed(() =>
  inputValue.value.trim().length > 0 ||
  (props.attachments && props.attachments.some(a => a.status === 'ready'))
)

const computedPlaceholder = computed(() => {
  if (props.selectedSkill) {
    return `使用「${props.selectedSkill.display_name}」处理...`
  }
  return props.placeholder
})

function handleSelectSkill(skill: MentionSkill) {
  emit('select-skill', skill)
  showSkillSheet.value = false
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

// 自动调整高度
const autoResize = () => {
  const textarea = textareaRef.value
  if (textarea) {
    textarea.style.height = 'auto'
    textarea.style.height = Math.min(textarea.scrollHeight, 100) + 'px'
  }
}

// 虚拟键盘处理
const handleFocus = () => {
  const isTouchDevice = 'ontouchstart' in window || navigator.maxTouchPoints > 0
  if (isTouchDevice) {
    isKeyboardVisible.value = true
    if (/iPhone|iPad/i.test(navigator.userAgent)) {
      setTimeout(() => {
        textareaRef.value?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
      }, 300)
    }
  }
}

const handleBlur = () => {
  setTimeout(() => { isKeyboardVisible.value = false }, 100)
}

const handleSend = () => {
  if (canSend.value && !props.disabled && !props.loading && !props.hasUploading) {
    emit('send')
    nextTick(() => {
      if (textareaRef.value) {
        textareaRef.value.style.height = 'auto'
      }
    })
  }
}

let viewportHandler: (() => void) | null = null

onMounted(() => {
  if (window.visualViewport) {
    viewportHandler = () => {
      const vh = window.visualViewport!.height
      document.documentElement.style.setProperty('--le-vh', `${vh * 0.01}px`)
    }
    window.visualViewport.addEventListener('resize', viewportHandler)
    viewportHandler()
  }
})

onUnmounted(() => {
  if (viewportHandler && window.visualViewport) {
    window.visualViewport.removeEventListener('resize', viewportHandler)
  }
})
</script>

<style scoped>
.mobile-chat-input {
  position: fixed;
  bottom: calc(56px + env(safe-area-inset-bottom, 0px));
  left: 0;
  right: 0;
  background: #fff;
  border-top: 1px solid #eee;
  padding: 8px 12px;
  z-index: 101;
  transition: bottom 0.3s ease;
}

.mobile-chat-input.keyboard-visible {
  bottom: 0;
}

/* 已选 skill tag */
.mobile-skill-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: #e8f4fd;
  color: #2080f0;
  border-radius: 12px;
  padding: 2px 8px 2px 10px;
  font-size: 12px;
  margin-bottom: 6px;
}

.skill-tag-label {
  white-space: nowrap;
}

.skill-tag-close {
  border: none;
  background: none;
  color: #2080f0;
  font-size: 16px;
  cursor: pointer;
  padding: 0 2px;
  line-height: 1;
}

.input-container {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  background: #f5f5f5;
  border-radius: 20px;
  padding: 8px 12px;
}

/* 技能选择按钮 */
.skill-btn {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  border: none;
  background: transparent;
  color: #666;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  flex-shrink: 0;
  -webkit-tap-highlight-color: transparent;
  transition: color 0.2s;
}

.skill-btn.active,
.skill-btn:active {
  color: #2080f0;
}

/* 附件按钮 */
.attach-btn {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  border: none;
  background: transparent;
  color: #666;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  flex-shrink: 0;
  -webkit-tap-highlight-color: transparent;
  transition: color 0.2s;
}

.attach-btn:active {
  color: #2080f0;
}

/* 模式切换按钮组 */
.mobile-mode-switch {
  display: inline-flex;
  border: 1px solid #e5e5ea;
  border-radius: 8px;
  overflow: hidden;
  background: #eee;
  flex-shrink: 0;
}

.mode-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  background: transparent;
  color: #aaa;
  cursor: pointer;
  transition: all 0.2s;
  -webkit-tap-highlight-color: transparent;
}

.mode-btn.active {
  color: #18a058;
  background: #fff;
  box-shadow: 0 1px 3px rgba(0,0,0,0.06);
}

.mode-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

textarea {
  flex: 1;
  border: none;
  background: transparent;
  font-size: 16px;
  line-height: 1.4;
  resize: none;
  max-height: 100px;
  outline: none;
  font-family: inherit;
  color: #333;
}

textarea::placeholder {
  color: #999;
}

textarea:disabled {
  opacity: 0.6;
}

.send-btn {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  border: none;
  background: #e0e0e0;
  color: #999;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
  flex-shrink: 0;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}

.send-btn.active {
  background: var(--le-color-primary, #2080f0);
  color: #fff;
}

.send-btn.loading {
  background: var(--le-color-primary, #2080f0);
  opacity: 0.7;
}

.send-btn:disabled {
  cursor: not-allowed;
}

.send-btn:active:not(:disabled) {
  transform: scale(0.95);
}

.safe-area-bottom {
  height: 0;
}

/* 技能选择 ActionSheet */
.skill-sheet-list {
  padding: 4px 0;
}

.skill-sheet-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  cursor: pointer;
  transition: background 0.15s;
  border-radius: 8px;
}

.skill-sheet-item:active {
  background: #f5f5f5;
}

.skill-sheet-icon {
  font-size: 20px;
  flex-shrink: 0;
}

.skill-sheet-info {
  flex: 1;
  min-width: 0;
}

.skill-sheet-name {
  font-size: 15px;
  font-weight: 500;
  color: #333;
}

.skill-sheet-desc {
  font-size: 12px;
  color: #999;
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>