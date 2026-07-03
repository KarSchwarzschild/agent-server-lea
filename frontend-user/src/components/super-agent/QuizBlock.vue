<script setup lang="ts">
/**
 * QuizBlock — 内联检测题组件（学习指导 Guide 专用）
 *
 * 设计语言：
 *   - 白色背景 + 细灰色边框 + 蓝色左侧标识线
 *   - 扁平卡片选项（区别于 PlanQuestion 的胶囊按钮）
 *   - 无倒计时（学习场景非考试）
 *
 * 可访问性：
 *   - 语义化 radio 组 + role="radiogroup"
 *   - aria-checked + tabindex + 键盘导航
 */
import { ref, computed } from 'vue'

export interface QuizData {
  type: 'choice' | 'fill' | 'judge'
  question: string
  options?: string[]
  answer: number | string | boolean
  explanation: string
}

const props = defineProps<{
  quiz: QuizData
}>()

const selectedOption = ref<number | null>(null)
const fillAnswer = ref('')
const isAnswered = ref(false)
const isCorrect = ref(false)

const optionLabels = ['A', 'B', 'C', 'D', 'E', 'F']

function handleSelect(index: number) {
  if (isAnswered.value) return
  selectedOption.value = index
}

function handleKeydown(e: KeyboardEvent, index: number) {
  if (isAnswered.value) return
  if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault()
    selectedOption.value = index
  } else if (e.key === 'ArrowDown' || e.key === 'ArrowRight') {
    e.preventDefault()
    const next = (index + 1) % (props.quiz.options?.length || 1)
    const el = document.querySelector(`[data-quiz-option="${next}"]`) as HTMLElement
    el?.focus()
  } else if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') {
    e.preventDefault()
    const prev = (index - 1 + (props.quiz.options?.length || 1)) % (props.quiz.options?.length || 1)
    const el = document.querySelector(`[data-quiz-option="${prev}"]`) as HTMLElement
    el?.focus()
  }
}

const canSubmit = computed(() => {
  if (isAnswered.value) return false
  if (props.quiz.type === 'choice' || props.quiz.type === 'judge') {
    return selectedOption.value !== null
  }
  if (props.quiz.type === 'fill') {
    return fillAnswer.value.trim().length > 0
  }
  return false
})

function handleSubmit() {
  if (!canSubmit.value) return
  isAnswered.value = true

  if (props.quiz.type === 'choice') {
    isCorrect.value = selectedOption.value === props.quiz.answer
  } else if (props.quiz.type === 'judge') {
    const userAnswer = selectedOption.value === 0 // 0 = 正确, 1 = 错误
    isCorrect.value = userAnswer === props.quiz.answer
  } else if (props.quiz.type === 'fill') {
    isCorrect.value = fillAnswer.value.trim().toLowerCase() === String(props.quiz.answer).trim().toLowerCase()
  }
}

function getOptionClass(index: number) {
  if (!isAnswered.value) {
    return { selected: selectedOption.value === index }
  }
  const isCorrectOption = index === props.quiz.answer
  const isUserSelected = selectedOption.value === index
  return {
    correct: isCorrectOption,
    wrong: isUserSelected && !isCorrectOption,
    'not-selected': !isUserSelected && !isCorrectOption,
  }
}
</script>

<template>
  <div class="quiz-block">
    <!-- 标题 -->
    <div class="quiz-header">
      <span class="quiz-icon">&#128221;</span>
      <span class="quiz-title">检测题</span>
      <span v-if="isAnswered" class="quiz-result" :class="isCorrect ? 'correct' : 'wrong'">
        {{ isCorrect ? '&#10004; 回答正确！' : '&#10008; 回答有误' }}
      </span>
    </div>

    <!-- 题目 -->
    <div class="quiz-question">{{ quiz.question }}</div>

    <!-- 选择题选项 -->
    <div
      v-if="quiz.type === 'choice' && quiz.options"
      class="quiz-options"
      role="radiogroup"
      :aria-label="quiz.question"
    >
      <div
        v-for="(option, idx) in quiz.options"
        :key="idx"
        class="quiz-option"
        :class="getOptionClass(idx)"
        :data-quiz-option="idx"
        role="radio"
        :aria-checked="selectedOption === idx"
        :tabindex="idx === 0 || selectedOption === idx ? 0 : -1"
        @click="handleSelect(idx)"
        @keydown="handleKeydown($event, idx)"
      >
        <span class="option-label">{{ optionLabels[idx] }}</span>
        <span class="option-text">{{ option }}</span>
        <span v-if="isAnswered && idx === quiz.answer" class="option-mark correct-mark">&#10004;</span>
        <span v-if="isAnswered && selectedOption === idx && idx !== quiz.answer" class="option-mark wrong-mark">&#10008;</span>
      </div>
    </div>

    <!-- 判断题选项 -->
    <div
      v-else-if="quiz.type === 'judge'"
      class="quiz-options"
      role="radiogroup"
      :aria-label="quiz.question"
    >
      <div
        v-for="(label, idx) in ['正确', '错误']"
        :key="idx"
        class="quiz-option"
        :class="getOptionClass(idx)"
        :data-quiz-option="idx"
        role="radio"
        :aria-checked="selectedOption === idx"
        :tabindex="idx === 0 || selectedOption === idx ? 0 : -1"
        @click="handleSelect(idx)"
        @keydown="handleKeydown($event, idx)"
      >
        <span class="option-label">{{ idx === 0 ? '&#10004;' : '&#10008;' }}</span>
        <span class="option-text">{{ label }}</span>
      </div>
    </div>

    <!-- 填空题输入 -->
    <div v-else-if="quiz.type === 'fill'" class="quiz-fill">
      <input
        v-model="fillAnswer"
        type="text"
        class="fill-input"
        :disabled="isAnswered"
        placeholder="输入你的答案..."
        @keydown.enter="handleSubmit"
      />
    </div>

    <!-- 提交按钮 -->
    <div v-if="!isAnswered" class="quiz-actions">
      <button
        class="submit-btn"
        :disabled="!canSubmit"
        @click="handleSubmit"
      >
        提交答案
      </button>
    </div>

    <!-- 解析 -->
    <div v-if="isAnswered && quiz.explanation" class="quiz-explanation">
      <span class="explanation-icon">&#128161;</span>
      <span class="explanation-label">解析：</span>
      <span class="explanation-text">{{ quiz.explanation }}</span>
    </div>
  </div>
</template>

<style scoped>
.quiz-block {
  background: #fff;
  border: 1px solid #e5e7eb;
  border-left: 3px solid #3b82f6;
  border-radius: 8px;
  padding: 16px;
  margin: 12px 0;
}

.quiz-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 12px;
}

.quiz-icon {
  font-size: 16px;
}

.quiz-title {
  font-size: 14px;
  font-weight: 600;
  color: #374151;
}

.quiz-result {
  margin-left: auto;
  font-size: 13px;
  font-weight: 500;
}

.quiz-result.correct {
  color: #16a34a;
}

.quiz-result.wrong {
  color: #dc2626;
}

.quiz-question {
  font-size: 15px;
  color: #1f2937;
  line-height: 1.6;
  margin-bottom: 14px;
}

/* ==================== 选项 ==================== */
.quiz-options {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 14px;
}

.quiz-option {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.15s;
  outline: none;
}

.quiz-option:hover:not(.correct):not(.wrong):not(.not-selected) {
  background: #f8fafc;
  border-color: #d1d5db;
}

.quiz-option:focus-visible {
  box-shadow: 0 0 0 2px #3b82f6;
}

.quiz-option.selected {
  background: #eff6ff;
  border-color: #3b82f6;
}

.quiz-option.correct {
  background: #f0fdf4;
  border-color: #16a34a;
}

.quiz-option.wrong {
  background: #fef2f2;
  border-color: #dc2626;
}

.quiz-option.not-selected {
  opacity: 0.6;
  cursor: default;
}

.option-label {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: 50%;
  background: #f3f4f6;
  color: #4b5563;
  font-size: 13px;
  font-weight: 600;
  flex-shrink: 0;
}

.quiz-option.selected .option-label {
  background: #3b82f6;
  color: #fff;
}

.quiz-option.correct .option-label {
  background: #16a34a;
  color: #fff;
}

.quiz-option.wrong .option-label {
  background: #dc2626;
  color: #fff;
}

.option-text {
  font-size: 14px;
  color: #374151;
  flex: 1;
}

.option-mark {
  font-size: 16px;
  flex-shrink: 0;
}

.correct-mark {
  color: #16a34a;
}

.wrong-mark {
  color: #dc2626;
}

/* ==================== 填空题 ==================== */
.quiz-fill {
  margin-bottom: 14px;
}

.fill-input {
  width: 100%;
  padding: 10px 14px;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  font-size: 14px;
  outline: none;
  transition: border-color 0.15s;
  box-sizing: border-box;
}

.fill-input:focus {
  border-color: #3b82f6;
  box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.15);
}

.fill-input:disabled {
  background: #f9fafb;
  cursor: not-allowed;
}

/* ==================== 提交按钮 ==================== */
.quiz-actions {
  display: flex;
  justify-content: center;
  margin-bottom: 4px;
}

.submit-btn {
  padding: 8px 24px;
  background: #3b82f6;
  color: #fff;
  border: none;
  border-radius: 6px;
  font-size: 14px;
  cursor: pointer;
  transition: background 0.15s;
}

.submit-btn:hover:not(:disabled) {
  background: #2563eb;
}

.submit-btn:disabled {
  background: #9ca3af;
  cursor: not-allowed;
}

/* ==================== 解析 ==================== */
.quiz-explanation {
  margin-top: 12px;
  padding: 12px;
  background: #f8fafc;
  border-radius: 6px;
  font-size: 13px;
  line-height: 1.6;
  color: #4b5563;
}

.explanation-icon {
  font-size: 14px;
  margin-right: 2px;
}

.explanation-label {
  font-weight: 600;
  color: #374151;
}
</style>
