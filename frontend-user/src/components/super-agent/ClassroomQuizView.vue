<script setup lang="ts">
/**
 * ClassroomQuizView — 课堂 Quiz 4态组件
 * 对齐 OpenMAIC quiz-view.tsx：
 *   not_started → answering → grading → reviewing
 *   SVG 圆环成绩动画
 */
import { computed } from 'vue'
import katex from 'katex'
import 'katex/dist/katex.min.css'
import { useQuizState } from '../../composables/useQuizState'

const props = defineProps<{
  scene: any
}>()

const sceneId = computed(() => props.scene?.id || props.scene?.scene_index || 'unknown')
const content = computed(() => props.scene?.content || {})
const questions = computed(() => {
  // 支持单题和多题
  const c = content.value
  if (Array.isArray(c.questions)) return c.questions
  if (c.question) return [{ ...c, id: 'q_0' }]
  return []
})

const { phase, answers, gradeResult, startQuiz, setAnswer, submitAnswers, reset } = useQuizState(sceneId.value)

// 渲染 LaTeX 公式：$...$ 和 $$...$$
function renderMath(text: string): string {
  if (!text) return ''
  let result = text.replace(/\$\$([\s\S]+?)\$\$/g, (_m, f) => {
    try { return katex.renderToString(f.trim(), { displayMode: true, throwOnError: false }) }
    catch { return f }
  })
  result = result.replace(/\$([^\$]+?)\$/g, (_m, f) => {
    try { return katex.renderToString(f.trim(), { displayMode: false, throwOnError: false }) }
    catch { return f }
  })
  return result
}

// 去除选项中已有的字母前缀（如 "A. xxx" → "xxx"）
function stripOptionPrefix(opt: string): string {
  // 支持 A. A、A) (A) a. 1. 等前缀格式
  return opt.replace(/^\(?[A-Za-z0-9][.、)]\s*/, '').replace(/^\([A-Za-z0-9]\)\s*/, '')
}

// 答题进度
const answeredCount = computed(() => {
  return questions.value.filter((_: any, i: number) => {
    const id = _.id || `q_${i}`
    const a = answers.value[id]
    return a !== undefined && a !== '' && (!Array.isArray(a) || a.length > 0)
  }).length
})

// 简答字数
function charCount(qId: string): number {
  const a = answers.value[qId]
  return typeof a === 'string' ? a.length : 0
}

// 正确/错误计数
const correctCount = computed(() => gradeResult.value?.details.filter(d => d.isCorrect).length || 0)
const incorrectCount = computed(() => gradeResult.value?.details.filter(d => !d.isCorrect).length || 0)

const allAnswered = computed(() => {
  return questions.value.every((q: any, i: number) => {
    const id = q.id || `q_${i}`
    const a = answers.value[id]
    return a !== undefined && a !== '' && (!Array.isArray(a) || a.length > 0)
  })
})

// SVG 圆环
const scorePercent = computed(() => {
  if (!gradeResult.value) return 0
  return Math.round((gradeResult.value.score / gradeResult.value.maxScore) * 100)
})

const scoreColor = computed(() => {
  const p = scorePercent.value
  if (p >= 80) return '#16a34a'
  if (p >= 60) return '#ea580c'
  return '#dc2626'
})

const scoreLabel = computed(() => {
  const p = scorePercent.value
  if (p >= 80) return '优秀'
  if (p >= 60) return '继续加油'
  return '需要复习'
})

// 圆环动画参数
const circumference = 2 * Math.PI * 42
const dashOffset = computed(() => circumference * (1 - scorePercent.value / 100))

function handleSelect(qId: string, value: string, multi: boolean) {
  if (multi) {
    const current = (answers.value[qId] as string[]) || []
    const idx = current.indexOf(value)
    if (idx >= 0) {
      setAnswer(qId, current.filter((_, i) => i !== idx))
    } else {
      setAnswer(qId, [...current, value])
    }
  } else {
    setAnswer(qId, value)
  }
}
</script>

<template>
  <div class="quiz-view">
    <!-- NOT_STARTED -->
    <div v-if="phase === 'not_started'" class="quiz-cover">
      <div class="cover-icon">📝</div>
      <h3 class="cover-title">测验环节</h3>
      <p class="cover-desc">共 {{ questions.length }} 道题</p>
      <button class="cover-btn" @click="startQuiz">开始答题</button>
    </div>

    <!-- ANSWERING -->
    <div v-else-if="phase === 'answering'" class="quiz-answering">
      <!-- 答题进度 -->
      <div class="answering-header">
        <span class="answering-progress">{{ answeredCount }} / {{ questions.length }}</span>
      </div>

      <div
        v-for="(q, qi) in questions"
        :key="q.id || qi"
        class="question-card"
      >
        <!-- 左侧竖条 -->
        <div class="q-accent-bar" />
        <div class="q-number">{{ qi + 1 }}</div>
        <p class="q-text" v-html="renderMath(q.question)" />

        <!-- 单选/判断 -->
        <div v-if="(q.type === 'choice' || q.type === 'judge') && !q.multiple" class="q-options">
          <label
            v-for="(opt, oi) in q.options"
            :key="oi"
            class="q-option"
            :class="{ 'is-selected': answers[q.id || `q_${qi}`] === String.fromCharCode(65 + oi) }"
            @click="handleSelect(q.id || `q_${qi}`, String.fromCharCode(65 + oi), false)"
          >
            <span class="option-radio" />
            <span class="option-text" v-html="String.fromCharCode(65 + oi) + '. ' + renderMath(stripOptionPrefix(String(opt)))" />
          </label>
        </div>

        <!-- 多选 -->
        <div v-else-if="q.type === 'multiple' || q.multiple" class="q-options">
          <div class="q-multi-hint">可选多个</div>
          <label
            v-for="(opt, oi) in q.options"
            :key="oi"
            class="q-option"
            :class="{ 'is-selected': ((answers[q.id || `q_${qi}`] as string[]) || []).includes(String.fromCharCode(65 + oi)) }"
            @click="handleSelect(q.id || `q_${qi}`, String.fromCharCode(65 + oi), true)"
          >
            <span class="option-checkbox" />
            <span class="option-text" v-html="String.fromCharCode(65 + oi) + '. ' + renderMath(stripOptionPrefix(String(opt)))" />
          </label>
        </div>

        <!-- 简答 -->
        <div v-else-if="q.type === 'fill' || q.type === 'short_answer'" class="q-textarea-wrap">
          <textarea
            class="q-textarea"
            :value="(answers[q.id || `q_${qi}`] as string) || ''"
            @input="setAnswer(q.id || `q_${qi}`, ($event.target as HTMLTextAreaElement).value)"
            placeholder="请输入你的答案..."
            rows="3"
          />
          <div class="q-char-count">{{ charCount(q.id || `q_${qi}`) }} 字</div>
        </div>
      </div>

      <button class="submit-btn" :disabled="!allAnswered" @click="submitAnswers(questions)">
        提交答案
      </button>
    </div>

    <!-- GRADING -->
    <div v-else-if="phase === 'grading'" class="quiz-grading">
      <div class="grading-spinner" />
      <p class="grading-text">AI 评分中</p>
      <div class="grading-dots">
        <span class="dot" /><span class="dot" /><span class="dot" />
      </div>
      <p class="grading-hint">请稍候，正在分析你的答案...</p>
    </div>

    <!-- REVIEWING -->
    <div v-else-if="phase === 'reviewing' && gradeResult" class="quiz-reviewing">
      <!-- 成绩头部（双列：左侧信息 + 右侧圆环）-->
      <div class="score-header">
        <div class="score-info">
          <div class="score-label-big" :style="{ color: scoreColor }">{{ scoreLabel }}</div>
          <div class="score-detail">
            {{ gradeResult.score }} / {{ gradeResult.maxScore }} 分
          </div>
          <div class="score-counts">
            <span class="count-correct">✓ {{ correctCount }} 正确</span>
            <span class="count-wrong">✗ {{ incorrectCount }} 错误</span>
          </div>
          <button class="retry-btn" @click="reset">↻ 重新答题</button>
        </div>
        <div class="score-ring-wrap">
          <svg class="score-ring" viewBox="0 0 100 100">
            <circle cx="50" cy="50" r="42" fill="none" stroke="#e5e7eb" stroke-width="6" />
            <circle
              cx="50" cy="50" r="42"
              fill="none"
              :stroke="scoreColor"
              stroke-width="6"
              stroke-linecap="round"
              :stroke-dasharray="circumference"
              :stroke-dashoffset="dashOffset"
              transform="rotate(-90 50 50)"
              class="score-arc"
            />
          </svg>
          <div class="score-text">
            <span class="score-number">{{ scorePercent }}</span>
            <span class="score-unit">%</span>
          </div>
        </div>
      </div>

      <!-- 逐题回顾 -->
      <div
        v-for="(detail, di) in gradeResult.details"
        :key="di"
        class="review-item"
        :class="{ 'review-correct': detail.isCorrect, 'review-wrong': !detail.isCorrect }"
      >
        <!-- 左侧竖条 -->
        <div class="review-accent" :style="{ background: detail.isCorrect ? '#16a34a' : '#dc2626' }" />
        <div class="review-body">
          <div class="review-head">
            <span class="review-icon">{{ detail.isCorrect ? '✓' : '✗' }}</span>
            <span class="review-q">第 {{ di + 1 }} 题</span>
            <span class="review-score-tag">{{ detail.earnedScore }}/{{ detail.maxScore }}</span>
          </div>
          <!-- AI 讲解 -->
          <div v-if="detail.feedback" class="review-ai-comment">
            <span class="ai-comment-icon">✦</span>
            <span class="ai-comment-label">AI 讲解</span>
            <p class="ai-comment-text">{{ detail.feedback }}</p>
          </div>
          <!-- 题目分析 -->
          <div v-if="questions[di]?.analysis" class="review-analysis">
            <span class="analysis-label">分析</span>
            <p class="analysis-text">{{ questions[di].analysis }}</p>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.quiz-view {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  overflow-y: auto;
}

/* ─── Cover ─── */
.quiz-cover {
  text-align: center;
  padding: 40px;
}
.cover-icon { font-size: 48px; margin-bottom: 16px; }
.cover-title { font-size: 24px; font-weight: 600; color: var(--cp-text, #333); margin: 0 0 8px; }
.cover-desc { font-size: 14px; color: var(--cp-text-secondary, #666); margin: 0 0 24px; }
.cover-btn {
  padding: 10px 32px;
  border: none;
  border-radius: 8px;
  background: linear-gradient(135deg, #7c3aed, #6d28d9);
  color: white;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s;
}
.cover-btn:hover { transform: scale(1.02); box-shadow: 0 4px 12px rgba(124,58,237,0.3); }

/* ─── Answering ─── */
.answering-header {
  display: flex;
  justify-content: flex-end;
  margin-bottom: -8px;
}
.answering-progress {
  font-size: 13px;
  color: var(--cp-text-secondary, #666);
  font-weight: 500;
  background: rgba(124,58,237,0.06);
  padding: 4px 12px;
  border-radius: 12px;
}

.quiz-answering {
  width: 100%;
  max-width: 600px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.question-card {
  position: relative;
  background: var(--cp-card-bg, white);
  border: 1px solid var(--cp-card-border, #e5e7eb);
  border-radius: 12px;
  padding: 20px 20px 20px 24px;
  overflow: hidden;
}
.q-accent-bar {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 4px;
  background: #7c3aed;
  border-radius: 4px 0 0 4px;
}
.q-number {
  width: 24px; height: 24px;
  border-radius: 50%;
  background: #7c3aed;
  color: white;
  font-size: 12px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 8px;
}
.q-text { font-size: 15px; color: var(--cp-text, #333); margin: 0 0 12px; line-height: 1.6; }
.q-options { display: flex; flex-direction: column; gap: 8px; }
.q-option {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border: 1px solid var(--cp-card-border, #e5e7eb);
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.15s;
  font-size: 14px;
  color: var(--cp-text, #333);
}
.q-option:hover { border-color: #7c3aed; background: rgba(124,58,237,0.04); }
.q-option.is-selected { border-color: #7c3aed; background: rgba(124,58,237,0.08); }
.option-radio {
  width: 16px; height: 16px;
  border: 2px solid #d1d5db;
  border-radius: 50%;
  flex-shrink: 0;
  transition: all 0.15s;
}
.q-option.is-selected .option-radio {
  border-color: #7c3aed;
  background: #7c3aed;
  box-shadow: inset 0 0 0 3px white;
}
.option-text { flex: 1; }

.q-multi-hint { font-size: 12px; color: var(--cp-text-muted, #999); margin-bottom: 4px; }
.option-checkbox {
  width: 16px; height: 16px;
  border: 2px solid #d1d5db;
  border-radius: 3px;
  flex-shrink: 0;
  transition: all 0.15s;
}
.q-option.is-selected .option-checkbox {
  border-color: #7c3aed;
  background: #7c3aed;
  background-image: url("data:image/svg+xml,%3csvg viewBox='0 0 16 16' fill='white' xmlns='http://www.w3.org/2000/svg'%3e%3cpath d='M12.207 4.793a1 1 0 010 1.414l-5 5a1 1 0 01-1.414 0l-2-2a1 1 0 011.414-1.414L6.5 9.086l4.293-4.293a1 1 0 011.414 0z'/%3e%3c/svg%3e");
}

.q-textarea-wrap { margin-top: 4px; }
.q-textarea {
  width: 100%;
  border: 1px solid var(--cp-card-border, #e5e7eb);
  border-radius: 8px;
  padding: 10px 14px;
  font-size: 14px;
  line-height: 1.6;
  resize: vertical;
  outline: none;
  transition: border-color 0.2s;
}
.q-textarea:focus { border-color: #7c3aed; box-shadow: 0 0 0 2px rgba(124,58,237,0.1); }
.q-char-count { text-align: right; font-size: 12px; color: var(--cp-text-muted, #999); margin-top: 4px; }

.submit-btn {
  align-self: center;
  padding: 10px 40px;
  border: none;
  border-radius: 8px;
  background: linear-gradient(135deg, #7c3aed, #6d28d9);
  color: white;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s;
}
.submit-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.submit-btn:not(:disabled):hover { transform: scale(1.02); }

/* ─── Grading ─── */
.quiz-grading { text-align: center; }
.grading-spinner {
  width: 40px; height: 40px;
  border: 3px solid #e5e7eb;
  border-top-color: #7c3aed;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  margin: 0 auto 12px;
}
@keyframes spin { to { transform: rotate(360deg); } }
.grading-text { font-size: 14px; color: var(--cp-text-secondary, #666); margin-bottom: 4px; }
.grading-dots { display: flex; gap: 4px; }
.grading-dots .dot {
  width: 6px; height: 6px; border-radius: 50%; background: #7c3aed;
  animation: dot-pulse 1s ease-in-out infinite;
}
.grading-dots .dot:nth-child(2) { animation-delay: 0.2s; }
.grading-dots .dot:nth-child(3) { animation-delay: 0.4s; }
@keyframes dot-pulse { 0%, 100% { opacity: 0.3; } 50% { opacity: 1; } }
.grading-hint { font-size: 12px; color: var(--cp-text-muted, #999); margin-top: 8px; }

/* ─── Reviewing ─── */
.quiz-reviewing {
  width: 100%;
  max-width: 560px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* 成绩头部：双列布局 */
.score-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--cp-card-bg, white);
  border-radius: 16px;
  padding: 24px;
  box-shadow: 0 2px 12px rgba(0,0,0,0.06);
}
.score-info { display: flex; flex-direction: column; gap: 6px; }
.score-label-big { font-size: 20px; font-weight: 700; }
.score-detail { font-size: 14px; color: var(--cp-text-secondary, #666); }
.score-counts { display: flex; gap: 12px; font-size: 13px; }
.count-correct { color: #16a34a; }
.count-wrong { color: #dc2626; }

.retry-btn {
  margin-top: 8px;
  padding: 6px 16px;
  border: 1px solid var(--cp-card-border, #e5e7eb);
  border-radius: 8px;
  background: var(--cp-card-bg, white);
  color: var(--cp-text-secondary, #666);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s;
  width: fit-content;
}
.retry-btn:hover { background: var(--cp-bg, #f9fafb); border-color: #7c3aed; color: #7c3aed; }

.score-ring-wrap {
  position: relative;
  width: 100px; height: 100px;
  flex-shrink: 0;
}
.score-ring { width: 100%; height: 100%; }
.score-arc { transition: stroke-dashoffset 1s cubic-bezier(0.4, 0, 0.2, 1); }
.score-text {
  position: absolute; inset: 0;
  display: flex; align-items: center; justify-content: center; gap: 1px;
}
.score-number { font-size: 24px; font-weight: 700; color: var(--cp-text, #333); }
.score-unit { font-size: 11px; color: var(--cp-text-muted, #999); margin-top: 6px; }

/* 逐题回顾 */
.review-item {
  position: relative;
  background: var(--cp-card-bg, white);
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 1px 4px rgba(0,0,0,0.04);
}
.review-item.review-correct { border: 1px solid rgba(22,163,74,0.2); }
.review-item.review-wrong { border: 1px solid rgba(220,38,38,0.2); }

.review-accent {
  position: absolute; left: 0; top: 0; bottom: 0; width: 4px;
}

.review-body { padding: 14px 16px 14px 20px; width: 100%; }
.review-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.review-icon { font-size: 14px; font-weight: 700; }
.review-correct .review-icon { color: #16a34a; }
.review-wrong .review-icon { color: #dc2626; }
.review-q { color: var(--cp-text, #333); font-weight: 500; flex: 1; font-size: 14px; }
.review-score-tag {
  font-size: 12px; color: var(--cp-text-secondary, #666); background: #f3f4f6; padding: 2px 8px; border-radius: 10px;
}

/* AI 讲解卡片 */
.review-ai-comment {
  background: rgba(124,58,237,0.06);
  border-radius: 8px;
  padding: 10px 14px;
  margin-top: 8px;
}
.ai-comment-icon { color: #7c3aed; margin-right: 4px; }
.ai-comment-label { font-size: 11px; font-weight: 600; color: #7c3aed; text-transform: uppercase; letter-spacing: 0.06em; }
.ai-comment-text { font-size: 13px; color: var(--cp-text-secondary, #555); line-height: 1.6; margin: 4px 0 0; }

/* 题目分析 */
.review-analysis {
  background: rgba(59,130,246,0.06);
  border-radius: 8px;
  padding: 10px 14px;
  margin-top: 6px;
}
.analysis-label { font-size: 11px; font-weight: 600; color: #3b82f6; text-transform: uppercase; letter-spacing: 0.06em; }
.analysis-text { font-size: 13px; color: var(--cp-text-secondary, #555); line-height: 1.6; margin: 4px 0 0; }
</style>
