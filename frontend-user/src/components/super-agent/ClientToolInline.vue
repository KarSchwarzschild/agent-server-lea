<script setup lang="ts">
/**
 * ClientToolInline - 客户端工具内联交互组件
 *
 * 内联渲染（非模态），支持：
 * - confirm: 确认/取消
 * - select: 多选选项（checkbox 风格）
 * - input: 文本输入
 * - 60s 倒计时自动取消
 */
import { ref, computed, onMounted, onUnmounted } from 'vue'
import MarkdownIt from 'markdown-it'
import { submitToolResult } from '../../api/superagent'
import type { ClientToolRequest } from '../../types/super-agent'

const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

const props = defineProps<{
  request: ClientToolRequest
}>()

const emit = defineEmits<{
  (e: 'answered'): void
}>()

const selectedOptions = ref<Set<string>>(new Set())
const textInput = ref('')
const isSubmitting = ref(false)
const isAnswered = ref(false)
const answeredText = ref('')
const countdown = ref(60)
let timer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  timer = setInterval(() => {
    countdown.value--
    if (countdown.value <= 0) handleCancel()
  }, 1000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})

const toolType = computed(() => {
  const name = props.request.toolName
  if (name === 'confirm' || name === 'client_confirm') return 'confirm'
  if (name === 'user_select' || name === 'client_select') return 'select'
  if (name === 'text_input' || name === 'client_input') return 'input'
  return 'confirm'
})

const title = computed(() => {
  return (props.request.toolInput?.title as string)
    || props.request.toolLabel
    || props.request.toolName
})

const message = computed(() => {
  return (props.request.toolInput?.message as string)
    || (props.request.toolInput?.prompt as string)
    || (props.request.toolInput?.description as string)
    || ''
})

const messageHtml = computed(() => {
  if (!message.value) return ''
  return md.render(message.value)
})

const options = computed(() => {
  const opts = props.request.toolInput?.options
  if (Array.isArray(opts)) return opts as string[]
  return []
})

const maxSelect = computed(() => {
  const v = props.request.toolInput?.max_select
  return typeof v === 'number' && v > 0 ? v : 0  // 0 = 不限制（多选）
})

const isSingleSelect = computed(() => maxSelect.value === 1)

async function doSubmit(content: string, isError: boolean) {
  if (isSubmitting.value || isAnswered.value) return
  isSubmitting.value = true
  try {
    await submitToolResult({ tool_use_id: props.request.toolUseId, content, is_error: isError })
    isAnswered.value = true
    answeredText.value = isError ? '已取消' : (content === 'confirmed' ? '已确认' : content)
    if (timer) clearInterval(timer)
    emit('answered')
  } catch (e) {
    console.error('[ClientToolInline] 提交失败:', e)
  } finally {
    isSubmitting.value = false
  }
}

function handleConfirm() {
  if (toolType.value === 'select') {
    const selected = Array.from(selectedOptions.value)
    if (!selected.length) return
    doSubmit(selected.join(', '), false)
  } else if (toolType.value === 'input') {
    doSubmit(textInput.value || '(空)', false)
  } else {
    doSubmit('confirmed', false)
  }
}

function handleCancel() {
  doSubmit('用户取消了操作', true)
}

function toggleOption(opt: string) {
  if (isSingleSelect.value) {
    // 单选模式：互斥
    selectedOptions.value = new Set([opt])
    return
  }
  const s = new Set(selectedOptions.value)
  if (s.has(opt)) {
    s.delete(opt)
  } else {
    if (maxSelect.value > 0 && s.size >= maxSelect.value) return  // 达到上限
    s.add(opt)
  }
  selectedOptions.value = s
}

function selectAll() {
  selectedOptions.value = new Set(options.value)
}
</script>

<template>
  <div class="cti" :class="{ 'cti--done': isAnswered }">
    <div class="cti-bar">
      <span class="cti-title">{{ title }}</span>
      <span v-if="!isAnswered" class="cti-timer">{{ countdown }}s</span>
    </div>

    <div v-if="message" class="cti-msg" v-html="messageHtml" />

    <template v-if="!isAnswered">
      <!-- 选项列表（单选 radio / 多选 checkbox） -->
      <div v-if="toolType === 'select' && options.length" class="cti-opts">
        <label
          v-for="opt in options"
          :key="opt"
          class="cti-opt"
          :class="{ 'cti-opt--on': selectedOptions.has(opt) }"
        >
          <span class="cti-check">
            <!-- 单选 radio 样式 -->
            <template v-if="isSingleSelect">
              <svg v-if="selectedOptions.has(opt)" viewBox="0 0 16 16" width="14" height="14">
                <circle cx="8" cy="8" r="7" fill="none" stroke="currentColor" stroke-width="1.5"/>
                <circle cx="8" cy="8" r="4" fill="currentColor"/>
              </svg>
              <svg v-else viewBox="0 0 16 16" width="14" height="14">
                <circle cx="8" cy="8" r="7" fill="none" stroke="currentColor" stroke-width="1.5"/>
              </svg>
            </template>
            <!-- 多选 checkbox 样式 -->
            <template v-else>
              <svg v-if="selectedOptions.has(opt)" viewBox="0 0 16 16" width="14" height="14">
                <rect width="16" height="16" rx="3" fill="currentColor"/>
                <path d="M4.5 8.2L7 10.5 11.5 5.5" stroke="#fff" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
              </svg>
              <svg v-else viewBox="0 0 16 16" width="14" height="14">
                <rect x="0.5" y="0.5" width="15" height="15" rx="2.5" fill="none" stroke="currentColor"/>
              </svg>
            </template>
          </span>
          <span class="cti-opt-text" @click.prevent="toggleOption(opt)">{{ opt }}</span>
        </label>
        <button v-if="!isSingleSelect && options.length > 2" class="cti-all-btn" @click="selectAll">全选</button>
      </div>

      <!-- 文本输入 -->
      <div v-if="toolType === 'input'" class="cti-input-wrap">
        <input
          v-model="textInput"
          class="cti-input"
          placeholder="请输入..."
          :disabled="isSubmitting"
          @keyup.enter="handleConfirm"
        />
      </div>

      <div class="cti-actions">
        <button class="cti-btn cti-btn--ghost" :disabled="isSubmitting" @click="handleCancel">取消</button>
        <button
          class="cti-btn cti-btn--primary"
          :disabled="isSubmitting || (toolType === 'select' && !selectedOptions.size)"
          @click="handleConfirm"
        >确认</button>
      </div>
    </template>

    <div v-else class="cti-done-text">{{ answeredText }}</div>
  </div>
</template>

<style scoped>
.cti {
  border-left: 3px solid #36ad6a;
  border-radius: 8px;
  padding: 12px 14px;
  margin: 10px 0;
  background: #f8fdf9;
}

.cti--done {
  border-left-color: #c0c0c0;
  background: #fafafa;
}

.cti-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}

.cti-title {
  font-size: 14px;
  font-weight: 600;
  color: #1a1a1a;
}

.cti-timer {
  font-size: 12px;
  color: #999;
  font-variant-numeric: tabular-nums;
}

.cti-msg {
  font-size: 13px;
  color: #555;
  margin-bottom: 10px;
  line-height: 1.5;
}

.cti-msg :deep(p) {
  margin: 0 0 4px;
}

.cti-msg :deep(p:last-child) {
  margin-bottom: 0;
}

/* 多选选项 */
.cti-opts {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 10px;
}

.cti-opt {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border: 1px solid #e8e8e8;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s;
  background: #fff;
  user-select: none;
}

.cti-opt:hover {
  border-color: #36ad6a;
  background: #f0faf4;
}

.cti-opt--on {
  border-color: #36ad6a;
  background: #f0faf4;
}

.cti-check {
  flex-shrink: 0;
  color: #36ad6a;
  display: flex;
  align-items: center;
}

.cti-opt:not(.cti-opt--on) .cti-check {
  color: #ccc;
}

.cti-opt-text {
  font-size: 14px;
  color: #333;
  flex: 1;
}

.cti-all-btn {
  align-self: flex-start;
  padding: 2px 10px;
  font-size: 12px;
  color: #36ad6a;
  background: none;
  border: none;
  cursor: pointer;
}

.cti-all-btn:hover {
  text-decoration: underline;
}

/* 输入框 */
.cti-input-wrap {
  margin-bottom: 10px;
}

.cti-input {
  width: 100%;
  padding: 7px 10px;
  border: 1px solid #d9d9d9;
  border-radius: 6px;
  font-size: 13px;
  outline: none;
  box-sizing: border-box;
}

.cti-input:focus {
  border-color: #36ad6a;
}

/* 操作按钮 */
.cti-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.cti-btn {
  padding: 5px 16px;
  border-radius: 5px;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
}

.cti-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.cti-btn--ghost {
  background: none;
  border: 1px solid #d9d9d9;
  color: #666;
}

.cti-btn--ghost:hover:not(:disabled) {
  border-color: #999;
  color: #333;
}

.cti-btn--primary {
  background: #36ad6a;
  border: none;
  color: #fff;
}

.cti-btn--primary:hover:not(:disabled) {
  background: #2d9a5e;
}

/* 完成状态 */
.cti-done-text {
  font-size: 13px;
  color: #999;
}
</style>
