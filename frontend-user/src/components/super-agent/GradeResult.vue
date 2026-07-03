<script setup lang="ts">
import { ref, computed } from 'vue'

interface QuestionResult {
  index: number
  question: string
  type: 'choice' | 'fill' | 'judge' | 'short_answer'
  userAnswer: string
  correctAnswer: string
  score: number
  maxScore: number
  isCorrect: boolean
  isPartial: boolean
  feedback: string
  explanation: string
  tags: string[]
}

interface GradeData {
  totalScore: number
  maxScore: number
  percentage: number
  questions: QuestionResult[]
  weakPoints: string[]
  summary: string
}

const props = defineProps<{
  data: GradeData
}>()

const emit = defineEmits<{
  (e: 'review-topic', topic: string): void
  (e: 'retry-weak'): void
  (e: 'back'): void
}>()

// ── 展开/折叠 ──
const expandedQuestions = ref<Set<number>>(new Set())
const showAllQuestions = ref(false)

function toggleQuestion(idx: number) {
  if (expandedQuestions.value.has(idx)) {
    expandedQuestions.value.delete(idx)
  } else {
    expandedQuestions.value.add(idx)
  }
}

// ── 分数等级 ──
const scoreLevel = computed(() => {
  const p = props.data.percentage
  if (p >= 80) return { class: 'high', label: '表现出色！', color: '#18a058' }
  if (p >= 60) return { class: 'mid', label: '有进步空间，建议重点关注以下知识点', color: '#2080f0' }
  return { class: 'low', label: '需要加强学习，建议重新复习相关内容', color: '#f0a020' }
})

// ── 统计 ──
const correctCount = computed(() => props.data.questions.filter(q => q.isCorrect).length)
const wrongCount = computed(() => props.data.questions.filter(q => !q.isCorrect && !q.isPartial).length)
const partialCount = computed(() => props.data.questions.filter(q => q.isPartial).length)

// ── 需要关注的题目（错误 + 部分正确）──
const attentionQuestions = computed(() =>
  props.data.questions.filter(q => !q.isCorrect || q.isPartial)
)
</script>

<template>
  <div class="grade-result">
    <div class="grade-header">
      <span class="header-bar" />
      <span class="header-title">测验完成</span>
    </div>

    <!-- 总分区域 -->
    <div class="score-section" :class="scoreLevel.class">
      <div class="score-main">
        <span class="score-number" :style="{ color: scoreLevel.color }"
              :aria-label="`得分 ${data.totalScore} 分，满分 ${data.maxScore} 分`">
          {{ data.totalScore }}
        </span>
        <span class="score-unit">分</span>
      </div>
      <div class="score-bar-wrap">
        <div class="score-bar" :style="{ width: data.percentage + '%', background: scoreLevel.color }" />
      </div>
      <div class="score-hint" :style="{ color: scoreLevel.color }">{{ scoreLevel.label }}</div>
    </div>

    <!-- 统计行 -->
    <div class="stats-row">
      <span class="stat correct">正确 {{ correctCount }} 题</span>
      <span v-if="wrongCount" class="stat wrong">错误 {{ wrongCount }} 题</span>
      <span v-if="partialCount" class="stat partial">部分 {{ partialCount }} 题</span>
    </div>

    <!-- 需要关注 -->
    <div v-if="attentionQuestions.length" class="attention-section">
      <div class="section-label">需要关注：</div>
      <div
        v-for="q in attentionQuestions"
        :key="q.index"
        class="attention-item"
        @click="toggleQuestion(q.index)"
      >
        <div class="attention-header">
          <span class="attention-icon">{{ q.isPartial ? '&#9888;' : '&#10007;' }}</span>
          <span class="attention-title">
            Q{{ q.index + 1 }} &middot; {{ q.tags[0] || q.question.slice(0, 20) }}
          </span>
          <span v-if="q.type === 'choice'" class="attention-answer">
            <s>你选: {{ q.userAnswer }}</s> &rarr; 正确: {{ q.correctAnswer }}
          </span>
          <span v-else-if="q.type === 'short_answer'" class="attention-score">
            {{ q.score }}/{{ q.maxScore }} 分 &middot; "{{ q.feedback.slice(0, 30) }}"
          </span>
        </div>
        <div v-if="expandedQuestions.has(q.index)" class="attention-detail">
          <div class="detail-question">{{ q.question }}</div>
          <div v-if="q.explanation" class="detail-explanation">{{ q.explanation }}</div>
          <div v-if="q.feedback && q.type === 'short_answer'" class="detail-feedback">
            <strong>评语：</strong>{{ q.feedback }}
          </div>
        </div>
        <div v-else class="expand-hint">展开解析 &#9660;</div>
      </div>

      <button v-if="!showAllQuestions" class="btn-show-all" @click="showAllQuestions = true">
        查看全部题目
      </button>
    </div>

    <!-- 全部题目（可选展开） -->
    <div v-if="showAllQuestions" class="all-questions">
      <div class="section-label">全部题目：</div>
      <div
        v-for="q in data.questions"
        :key="q.index"
        class="question-row"
        :class="{ correct: q.isCorrect, wrong: !q.isCorrect && !q.isPartial, partial: q.isPartial }"
      >
        <span class="q-icon">{{ q.isCorrect ? '&#10003;' : q.isPartial ? '&#9888;' : '&#10007;' }}</span>
        <span class="q-label">Q{{ q.index + 1 }}. {{ q.question.slice(0, 60) }}</span>
        <span class="q-score">{{ q.score }}/{{ q.maxScore }}</span>
      </div>
    </div>

    <!-- 建议复习 -->
    <div v-if="data.weakPoints.length" class="review-section">
      <div class="section-label">建议复习：</div>
      <div class="weak-tags">
        <button
          v-for="topic in data.weakPoints"
          :key="topic"
          class="weak-tag"
          @click="emit('review-topic', topic)"
        >
          {{ topic }} &rarr; 让 AI 讲解
        </button>
      </div>
    </div>

    <!-- 底部操作 -->
    <div class="grade-actions">
      <button class="btn-back" @click="emit('back')">返回对话</button>
      <button v-if="data.weakPoints.length" class="btn-retry" @click="emit('retry-weak')">
        针对薄弱点重新测验
      </button>
    </div>
  </div>
</template>

<style scoped>
.grade-result {
  border: 1px solid #e0e0e0;
  border-left: 3px solid #3b82f6;
  border-radius: 8px;
  background: #fff;
  margin: 16px 0;
  overflow: hidden;
}

.grade-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  background: #f8fafc;
  border-bottom: 1px solid #f0f0f0;
}

.header-bar {
  width: 3px;
  height: 16px;
  background: #3b82f6;
  border-radius: 2px;
}

.header-title {
  font-size: 14px;
  font-weight: 600;
  color: #333;
}

/* ── 总分 ── */
.score-section {
  padding: 20px 16px;
  text-align: center;
}

.score-main {
  display: flex;
  align-items: baseline;
  justify-content: center;
  gap: 4px;
  margin-bottom: 8px;
}

.score-number {
  font-size: 48px;
  font-weight: 700;
  line-height: 1;
}

.score-unit {
  font-size: 16px;
  color: #999;
}

.score-bar-wrap {
  height: 6px;
  background: #f0f0f0;
  border-radius: 3px;
  margin: 0 40px 8px;
}

.score-bar {
  height: 100%;
  border-radius: 3px;
  transition: width 0.6s ease;
}

.score-hint {
  font-size: 13px;
  font-weight: 500;
}

/* ── 统计行 ── */
.stats-row {
  display: flex;
  justify-content: center;
  gap: 16px;
  padding: 8px 16px 16px;
  border-bottom: 1px solid #f0f0f0;
}

.stat {
  font-size: 13px;
  font-weight: 500;
}

.stat.correct { color: #18a058; }
.stat.wrong { color: #d03050; }
.stat.partial { color: #f0a020; }

/* ── 需要关注 ── */
.attention-section {
  padding: 12px 16px;
}

.section-label {
  font-size: 12px;
  color: #888;
  font-weight: 600;
  margin-bottom: 8px;
}

.attention-item {
  padding: 8px 12px;
  margin-bottom: 4px;
  border-radius: 6px;
  background: #fafbfc;
  cursor: pointer;
}

.attention-item:hover {
  background: #f0f4ff;
}

.attention-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}

.attention-icon {
  font-size: 14px;
}

.attention-title {
  font-weight: 500;
  color: #333;
  flex: 1;
}

.attention-answer {
  font-size: 12px;
  color: #666;
}

.attention-answer s {
  color: #d03050;
}

.attention-score {
  font-size: 12px;
  color: #666;
}

.expand-hint {
  font-size: 11px;
  color: #3b82f6;
  margin-top: 4px;
  padding-left: 22px;
}

.attention-detail {
  margin-top: 8px;
  padding: 8px 12px 8px 22px;
  font-size: 13px;
  color: #555;
  line-height: 1.6;
  background: #f8f9fa;
  border-radius: 4px;
}

.detail-question {
  font-weight: 500;
  margin-bottom: 4px;
}

.detail-explanation {
  color: #666;
  margin-bottom: 4px;
}

.detail-feedback {
  color: #666;
}

.btn-show-all {
  display: block;
  margin: 8px auto 0;
  padding: 4px 12px;
  border: 1px solid #d0d0d0;
  border-radius: 4px;
  background: #fff;
  font-size: 12px;
  color: #666;
  cursor: pointer;
}

/* ── 全部题目 ── */
.all-questions {
  padding: 12px 16px;
  border-top: 1px solid #f0f0f0;
}

.question-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
  font-size: 13px;
}

.q-icon { width: 16px; text-align: center; }
.question-row.correct .q-icon { color: #18a058; }
.question-row.wrong .q-icon { color: #d03050; }
.question-row.partial .q-icon { color: #f0a020; }

.q-label { flex: 1; color: #333; }
.q-score { color: #888; font-size: 12px; }

/* ── 建议复习 ── */
.review-section {
  padding: 12px 16px;
  border-top: 1px solid #f0f0f0;
}

.weak-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.weak-tag {
  padding: 4px 12px;
  border: 1px solid #3b82f6;
  border-radius: 16px;
  background: #eff6ff;
  color: #3b82f6;
  font-size: 12px;
  cursor: pointer;
}

.weak-tag:hover {
  background: #dbeafe;
}

/* ── 底部操作 ── */
.grade-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 12px 16px;
  border-top: 1px solid #f0f0f0;
}

.btn-back {
  padding: 6px 16px;
  border: 1px solid #d0d0d0;
  border-radius: 6px;
  background: #fff;
  color: #666;
  font-size: 13px;
  cursor: pointer;
}

.btn-retry {
  padding: 6px 16px;
  border: none;
  border-radius: 6px;
  background: #3b82f6;
  color: #fff;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
}

.btn-retry:hover {
  background: #2563eb;
}
</style>
