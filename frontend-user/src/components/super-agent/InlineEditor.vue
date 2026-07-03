<script setup lang="ts">
/**
 * 用户消息内联编辑组件
 *
 * 点击编辑按钮后替换消息内容为可编辑 textarea。
 * 提交前显示截断确认（如果编辑的消息后面还有后续对话）。
 */
import { ref, nextTick, onMounted } from 'vue'

const props = defineProps<{
  content: string
  /** 编辑后将被替换的后续消息数量（用于截断确认） */
  truncateCount?: number
}>()

const emit = defineEmits<{
  (e: 'submit', newContent: string): void
  (e: 'cancel'): void
}>()

const editContent = ref(props.content)
const textareaRef = ref<HTMLTextAreaElement>()
const isSubmitting = ref(false)

onMounted(() => {
  nextTick(() => {
    if (textareaRef.value) {
      textareaRef.value.focus()
      // 自动调整高度
      adjustHeight()
      // 光标移到末尾
      const len = editContent.value.length
      textareaRef.value.setSelectionRange(len, len)
    }
  })
})

function adjustHeight() {
  if (textareaRef.value) {
    textareaRef.value.style.height = 'auto'
    textareaRef.value.style.height = textareaRef.value.scrollHeight + 'px'
  }
}

function handleInput() {
  adjustHeight()
}

function handleSubmit() {
  const trimmed = editContent.value.trim()
  if (!trimmed || trimmed === props.content.trim()) {
    emit('cancel')
    return
  }
  isSubmitting.value = true
  emit('submit', trimmed)
}

function handleKeydown(e: KeyboardEvent) {
  // Ctrl/Cmd + Enter 提交
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
    e.preventDefault()
    handleSubmit()
  }
  // Esc 取消
  if (e.key === 'Escape') {
    emit('cancel')
  }
}
</script>

<template>
  <div class="inline-editor">
    <textarea
      ref="textareaRef"
      v-model="editContent"
      class="inline-editor-textarea"
      :disabled="isSubmitting"
      @input="handleInput"
      @keydown="handleKeydown"
      rows="1"
    />

    <!-- 截断确认提示 -->
    <div v-if="truncateCount && truncateCount > 0" class="truncate-warning">
      修改此问题后，之后的 {{ truncateCount }} 条消息将被替换重新生成
    </div>

    <div class="inline-editor-actions">
      <span class="inline-editor-hint">Ctrl+Enter 提交，Esc 取消</span>
      <div class="inline-editor-buttons">
        <button class="editor-btn editor-btn--cancel" @click="emit('cancel')" :disabled="isSubmitting">
          取消
        </button>
        <button
          class="editor-btn editor-btn--submit"
          @click="handleSubmit"
          :disabled="isSubmitting || !editContent.trim()"
        >
          {{ isSubmitting ? '提交中...' : '提交修改' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.inline-editor {
  width: 100%;
}
.inline-editor-textarea {
  width: 100%;
  min-height: 60px;
  max-height: 300px;
  padding: 10px 12px;
  border: 1.5px solid #4f8ef7;
  border-radius: 8px;
  font-size: 14px;
  line-height: 1.6;
  color: #333;
  background: #fff;
  resize: none;
  overflow-y: auto;
  outline: none;
  font-family: inherit;
  box-sizing: border-box;
}
.inline-editor-textarea:focus {
  border-color: #357ae8;
  box-shadow: 0 0 0 2px rgba(79, 142, 247, 0.15);
}
.inline-editor-textarea:disabled {
  opacity: 0.6;
}
.truncate-warning {
  margin-top: 6px;
  padding: 4px 8px;
  font-size: 11px;
  color: #b8860b;
  background: #fffbe6;
  border-radius: 4px;
  border: 1px solid #ffe58f;
  line-height: 1.4;
}
.inline-editor-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 8px;
  gap: 8px;
}
.inline-editor-hint {
  font-size: 11px;
  color: #bbb;
}
.inline-editor-buttons {
  display: flex;
  gap: 6px;
}
.editor-btn {
  padding: 5px 14px;
  border: none;
  border-radius: 6px;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
}
.editor-btn:disabled {
  opacity: 0.5;
  cursor: default;
}
.editor-btn--cancel {
  background: #f5f5f5;
  color: #666;
}
.editor-btn--cancel:hover:not(:disabled) {
  background: #e8e8e8;
}
.editor-btn--submit {
  background: #4f8ef7;
  color: #fff;
}
.editor-btn--submit:hover:not(:disabled) {
  background: #357ae8;
}
</style>
