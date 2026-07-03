<script setup lang="ts">
/**
 * AiMessagePostContent - AI 消息后置内容区
 *
 * 从 MessageItem.vue 提取（Phase 2.2 拆分）。
 * 职责：生成文件卡片 / Quiz 检测题 / 参考文献按钮 / 质量验证面板 / 诊断报告 / 错误横幅
 */
import { ref, computed } from 'vue'
import { NButton } from 'naive-ui'
import type { ChatMessage, GeneratedFileInfo, Reference } from '../../types/super-agent'
import QuizBlock from './QuizBlock.vue'
import type { QuizData } from './QuizBlock.vue'
import DiagnosticPanel from './DiagnosticPanel.vue'
import PeerReviewPanel from './PeerReviewPanel.vue'
import { parseAllFileResults, isFileGenerationTool } from '../../composables/useFileParser'

const props = defineProps<{
  msg: ChatMessage
  isMobile?: boolean
  extractedQuizzes?: QuizData[]
  isHtmlInteractive?: boolean
}>()

const emit = defineEmits<{
  (e: 'preview-file', file: GeneratedFileInfo): void
  (e: 'download-file', url: string): void
  (e: 'convert-to-pdf', file: GeneratedFileInfo): void
  (e: 'open-reference-drawer', refs: Reference[]): void
  (e: 'open-reference-modal', refs: Reference[]): void
  (e: 'retry-question'): void
}>()

const verificationExpanded = ref(false)

const generatedFiles = computed<GeneratedFileInfo[]>(() => {
  if (!props.msg.toolCalls || props.msg.isStreaming) return []
  const filesMap = new Map<string, GeneratedFileInfo>()
  for (const tc of props.msg.toolCalls) {
    if (isFileGenerationTool(tc.tool) && tc.result) {
      const parsed = parseAllFileResults(tc.result)
      for (const info of parsed) {
        filesMap.set(info.downloadUrl, info)
      }
    }
  }
  return Array.from(filesMap.values())
})

function handleFeedback() {
  // placeholder
}
</script>

<template>
  <!-- 生成文件卡片 -->
  <div v-if="generatedFiles.length > 0" class="generated-files">
    <div
      v-for="file in generatedFiles"
      :key="file.downloadUrl"
      class="inline-file-card"
    >
      <div class="inline-file-info">
        <span class="inline-file-icon">📄</span>
        <span class="inline-file-name">{{ file.filename }}</span>
        <span class="inline-file-size">{{ file.size }}</span>
      </div>
      <div class="inline-file-actions">
        <n-button size="small" @click="emit('preview-file', file)">预览</n-button>
        <n-button type="primary" size="small" @click="emit('download-file', file.downloadUrl)">下载</n-button>
      </div>
    </div>
  </div>

  <!-- Quiz 检测题 -->
  <QuizBlock
    v-for="(quiz, idx) in (extractedQuizzes || [])"
    :key="`quiz-${msg.id}-${idx}`"
    :quiz="quiz"
    v-if="!isHtmlInteractive && extractedQuizzes && extractedQuizzes.length > 0"
  />

  <!-- Artifact 预览卡片插槽 -->
  <slot name="artifact-card" />

  <!-- 参考文献按钮 -->
  <div v-if="msg.references && msg.references.length > 0" class="references-section">
    <n-button
      text
      type="primary"
      size="small"
      @click="isMobile ? emit('open-reference-drawer', msg.references) : emit('open-reference-modal', msg.references)"
    >
      📋 查看 {{ msg.references.length }} 篇参考资料
    </n-button>
  </div>

  <!-- 质量验证面板 -->
  <div
    v-if="msg.verification && !msg.isStreaming && msg.verification.judge_mode !== 'shadow'"
    class="verification-panel"
    :class="msg.verification.passed ? 'passed' : 'warning'"
  >
    <div class="verification-header" style="cursor: pointer" @click="verificationExpanded = !verificationExpanded">
      <span>{{ msg.verification.passed ? '✅ 质量验证通过' : '⚠️ 质量验证发现问题' }}</span>
      <span v-if="msg.verification.llm_score != null" class="verification-llm-score" :title="'AI 多维评分（加权平均），点击展开查看各维度详情'">AI评分 {{ (msg.verification.llm_score * 100).toFixed(0) }}分</span>
      <span v-else class="verification-score" :title="'规则检查通过率：格式、长度、引用等基础规则'">规则 {{ (msg.verification.score * 100).toFixed(0) }}%</span>
      <i class="chip-arrow" :class="{ open: verificationExpanded }">›</i>
    </div>
    <div v-if="verificationExpanded" class="verification-details">
      <div v-if="msg.verification.dimensions?.length" class="verification-dimensions">
        <div v-for="dim in msg.verification.dimensions" :key="dim.name" class="dimension-row">
          <div class="dimension-top">
            <span class="dimension-label">{{ (dim.description || dim.name).split('：')[0].split(':')[0] }}</span>
            <div class="dimension-bar-wrap">
              <div
                class="dimension-bar"
                :style="{
                  width: (dim.score * 100) + '%',
                  background: dim.score >= 0.7 ? '#18a058' : dim.score >= 0.5 ? '#f0a020' : '#d03050'
                }"
              />
            </div>
            <span class="dimension-score">{{ (dim.score * 100).toFixed(0) }}%</span>
          </div>
          <div class="dimension-reasoning">{{ dim.reasoning }}</div>
        </div>
        <div class="dimension-meta">
          <span v-if="msg.verification.judge_model">{{ msg.verification.judge_model }}</span>
          <span v-if="msg.verification.duration_ms">{{ (msg.verification.duration_ms / 1000).toFixed(1) }}s</span>
        </div>
      </div>
      <div v-if="msg.verification.issues?.length" class="verification-issues">
        <div v-for="(issue, i) in msg.verification.issues" :key="i" class="verification-issue" :class="issue.severity">
          <span class="issue-severity">{{ issue.severity === 'error' ? '❌' : '⚠️' }}</span>
          {{ issue.message }}
        </div>
      </div>
    </div>
  </div>

  <!-- 诊断报告 -->
  <DiagnosticPanel
    v-if="msg.diagnosticReport && !msg.isStreaming"
    :report="msg.diagnosticReport"
    @resubmit="emit('retry-question')"
  />

  <!-- 同行评审 -->
  <PeerReviewPanel
    v-if="msg.peerReview && !msg.isStreaming"
    :review="msg.peerReview"
  />

  <!-- 错误横幅 -->
  <div v-if="msg.errorInfo" class="error-banner">
    <div class="error-banner-icon">&#9888;&#65039;</div>
    <div class="error-banner-body">
      <div class="error-banner-title">{{ msg.errorInfo.message }}</div>
      <div class="error-banner-desc">
        {{ msg.errorInfo.code === 'SKILL_TIMEOUT'
          ? '处理时间过长已自动中断，您可以重试或换个方式描述。'
          : '建议您稍后重新提问，或换一种方式描述您的需求。' }}
      </div>
      <div class="error-banner-actions">
        <button v-if="msg.errorInfo.retryable" class="error-banner-btn error-banner-btn--primary" @click="emit('retry-question')">重试</button>
        <button class="error-banner-btn error-banner-btn--text" @click="handleFeedback">反馈问题</button>
      </div>
    </div>
  </div>
</template>


<style>
.generated-files {
  margin-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.inline-file-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 14px;
  background: linear-gradient(135deg, #f0fdf4 0%, #ecfdf5 100%);
  border: 1px solid #bbf7d0;
  border-radius: 8px;
}

.inline-file-info {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.inline-file-icon {
  font-size: 20px;
  flex-shrink: 0;
}

.inline-file-name {
  font-weight: 600;
  color: #166534;
  font-size: 14px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.inline-file-size {
  font-size: 12px;
  color: #15803d;
  background: #dcfce7;
  padding: 2px 8px;
  border-radius: 4px;
  white-space: nowrap;
  flex-shrink: 0;
}

.inline-file-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

/* 移动端参考文献 */
.references-section {
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid #e5e7eb;
}

.references-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 600;
  color: #374151;
  margin-bottom: 12px;
}

.references-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.verification-panel {
  margin-top: 12px;
  padding: 8px 14px;
  border-radius: 8px;
  font-size: 13px;
}
.verification-panel.passed {
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
}
.verification-panel.warning {
  background: #fffbeb;
  border: 1px solid #fde68a;
}
.verification-header {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  user-select: none;
}
.verification-score {
  font-weight: 600;
  color: #6b7280;
}
.verification-llm-score {
  font-weight: 600;
  color: #2080f0;
  font-size: 12px;
}
.verification-mode {
  font-size: 11px;
  color: #999;
  background: #f0f0f0;
  padding: 1px 6px;
  border-radius: 3px;
}
.verification-header .chip-arrow {
  margin-left: auto;
}
.verification-details {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid rgba(0,0,0,0.06);
}
.verification-dimensions {
  margin-bottom: 6px;
}
.dimension-row {
  padding: 4px 0;
  font-size: 12px;
}
.dimension-row + .dimension-row {
  border-top: 1px solid rgba(0,0,0,0.04);
}
.dimension-top {
  display: flex;
  align-items: center;
  gap: 8px;
}
.dimension-label {
  width: 80px;
  flex-shrink: 0;
  color: #555;
  font-weight: 500;
}
.dimension-bar-wrap {
  flex: 1;
  height: 6px;
  background: #e8e8e8;
  border-radius: 3px;
  overflow: hidden;
}
.dimension-bar {
  height: 100%;
  border-radius: 3px;
  transition: width 0.3s;
}
.dimension-score {
  width: 36px;
  flex-shrink: 0;
  font-weight: 600;
  color: #555;
  text-align: right;
}
.dimension-reasoning {
  color: #999;
  margin-top: 2px;
  padding-left: 88px;
  line-height: 1.4;
}
.dimension-meta {
  display: flex;
  gap: 12px;
  margin-top: 4px;
  font-size: 11px;
  color: #bbb;
}
.verification-issues {
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px dashed rgba(0,0,0,0.06);
}
.verification-issue {
  padding: 3px 0;
  color: #6b7280;
}
.verification-issue.error { color: #dc2626; }
.verification-issue.warning { color: #d97706; }

/* 生成质量错误横幅 */
.error-banner {
  margin-top: 12px;
  padding: 12px 16px;
  background: #fdf6ec;
  border: 1px solid #e6a23c;
  border-radius: 8px;
  display: flex;
  gap: 10px;
  align-items: flex-start;
}
.error-banner-icon {
  font-size: 18px;
  flex-shrink: 0;
  line-height: 1.4;
}
.error-banner-body {
  flex: 1;
  min-width: 0;
}
.error-banner-title {
  font-size: 13px;
  font-weight: 500;
  color: #7c5c00;
  margin-bottom: 4px;
}
.error-banner-desc {
  font-size: 12px;
  color: #9a7b32;
  margin-bottom: 8px;
}
.error-banner-actions {
  display: flex;
  gap: 8px;
}
.error-banner-btn {
  padding: 4px 12px;
  border-radius: 4px;
  font-size: 12px;
  cursor: pointer;
  border: none;
  transition: background 0.15s;
}
.error-banner-btn--primary {
  background: #e6a23c;
  color: #fff;
}
.error-banner-btn--primary:hover {
  background: #d4940f;
}
.error-banner-btn--text {
  background: transparent;
  color: #9a7b32;
}
.error-banner-btn--text:hover {
  background: rgba(230, 162, 60, 0.1);
}

/* 流式光标（interactive 模式下在 InteractivePlayer 之后显示） */
</style>
