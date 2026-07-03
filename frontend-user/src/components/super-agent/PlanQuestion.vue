<script setup lang="ts">
/**
 * PlanQuestion - 计划执行期间的交互式提问组件（MIG-2）
 *
 * 内联渲染（非模态），支持：
 * - 选项按钮（有 options 时）
 * - 自定义文本输入
 * - 300s 倒计时
 * - 跳过按钮
 */
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { submitToolResult } from '../../api/superagent'

const props = defineProps<{
  toolUseId: string
  question: string
  options?: string[]
  context?: string
  stepTitle?: string
}>()

const emit = defineEmits<{
  (e: 'answered'): void
}>()

const customInput = ref('')
const isSubmitting = ref(false)
const isAnswered = ref(false)
const countdown = ref(300) // 5 分钟
let timer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  timer = setInterval(() => {
    countdown.value--
    if (countdown.value <= 0) {
      handleSkip()
    }
  }, 1000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})

const formattedTime = computed(() => {
  const m = Math.floor(countdown.value / 60)
  const s = countdown.value % 60
  return `${m}:${s.toString().padStart(2, '0')}`
})

async function submitAnswer(content: string) {
  if (isSubmitting.value || isAnswered.value) return
  isSubmitting.value = true
  try {
    await submitToolResult({ tool_use_id: props.toolUseId, content, is_error: false })
    isAnswered.value = true
    if (timer) clearInterval(timer)
    emit('answered')
  } catch (e) {
    console.error('[PlanQuestion] 提交失败:', e)
  } finally {
    isSubmitting.value = false
  }
}

function handleOptionClick(option: string) {
  submitAnswer(option)
}

function handleCustomSubmit() {
  if (!customInput.value.trim()) return
  submitAnswer(customInput.value.trim())
}

function handleSkip() {
  submitAnswer('用户跳过了此问题，请自行决定')
}
</script>

<template>
  <div class="plan-question" :class="{ 'plan-question--answered': isAnswered }">
    <div class="pq-header">
      <span class="pq-icon">🤔</span>
      <span v-if="stepTitle" class="pq-context">Agent 在执行「{{ stepTitle }}」时需要确认</span>
      <span v-else class="pq-context">Agent 需要您的确认</span>
    </div>

    <div class="pq-question">{{ question }}</div>

    <div v-if="context" class="pq-extra-context">{{ context }}</div>

    <template v-if="!isAnswered">
      <div v-if="options && options.length" class="pq-options">
        <button
          v-for="opt in options"
          :key="opt"
          class="pq-option-btn"
          :disabled="isSubmitting"
          @click="handleOptionClick(opt)"
        >{{ opt }}</button>
      </div>

      <div class="pq-custom">
        <input
          v-model="customInput"
          class="pq-input"
          :placeholder="options?.length ? '或输入自定义回答...' : '请输入您的回答...'"
          :disabled="isSubmitting"
          @keyup.enter="handleCustomSubmit"
        />
        <button
          class="pq-submit-btn"
          :disabled="isSubmitting || !customInput.trim()"
          @click="handleCustomSubmit"
        >提交</button>
      </div>

      <div class="pq-footer">
        <span class="pq-countdown">⏱ 剩余 {{ formattedTime }}</span>
        <button class="pq-skip-btn" :disabled="isSubmitting" @click="handleSkip">跳过此问题</button>
      </div>
    </template>

    <div v-else class="pq-answered">
      ✅ 已回答
    </div>
  </div>
</template>

<style scoped>
.plan-question {
  border: 2px solid #667eea;
  border-radius: 12px;
  padding: 16px;
  margin: 12px 0;
  background: linear-gradient(135deg, #f0f5ff 0%, #f5f0ff 100%);
  animation: pq-highlight 2s ease-in-out;
}

.plan-question--answered {
  border-color: #52c41a;
  opacity: 0.7;
}

@keyframes pq-highlight {
  0%, 100% { box-shadow: 0 0 0 0 rgba(102, 126, 234, 0); }
  50% { box-shadow: 0 0 0 4px rgba(102, 126, 234, 0.3); }
}

.pq-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  font-size: 13px;
  color: #666;
}

.pq-icon { font-size: 18px; }

.pq-question {
  font-size: 15px;
  font-weight: 500;
  color: #1a1a1a;
  margin-bottom: 12px;
  line-height: 1.5;
}

.pq-extra-context {
  font-size: 13px;
  color: #888;
  margin-bottom: 12px;
  padding: 8px;
  background: rgba(255,255,255,0.6);
  border-radius: 6px;
}

.pq-options {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}

.pq-option-btn {
  padding: 8px 20px;
  border: 1px solid #667eea;
  border-radius: 20px;
  background: white;
  color: #667eea;
  cursor: pointer;
  font-size: 14px;
  transition: all 0.2s;
}

.pq-option-btn:hover:not(:disabled) {
  background: #667eea;
  color: white;
}

.pq-option-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.pq-custom {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}

.pq-input {
  flex: 1;
  padding: 8px 12px;
  border: 1px solid #d9d9d9;
  border-radius: 8px;
  font-size: 14px;
  outline: none;
}

.pq-input:focus {
  border-color: #667eea;
}

.pq-submit-btn {
  padding: 8px 16px;
  background: #667eea;
  color: white;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  font-size: 14px;
}

.pq-submit-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.pq-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.pq-countdown {
  font-size: 13px;
  color: #888;
}

.pq-skip-btn {
  padding: 4px 12px;
  background: none;
  border: 1px solid #d9d9d9;
  border-radius: 6px;
  color: #888;
  cursor: pointer;
  font-size: 13px;
}

.pq-skip-btn:hover:not(:disabled) {
  border-color: #999;
  color: #666;
}

.pq-answered {
  text-align: center;
  color: #52c41a;
  font-size: 14px;
  padding: 8px 0;
}
</style>
