<script setup lang="ts">
/**
 * 诊断报告面板
 *
 * DiagnosticAgent 修复失败升级时展示优化记录。
 * 独立组件，接收 DiagnosticReportData prop。
 */
import { ref, computed } from 'vue'
import type { DiagnosticReportData } from '../../types/super-agent'

const props = defineProps<{
  report: DiagnosticReportData
}>()

const emit = defineEmits<{
  (e: 'resubmit'): void
}>()

const expanded = ref(false)

const panelClass = computed(() => props.report.fixed ? 'fixed' : 'unfixed')

const headerText = computed(() =>
  props.report.fixed
    ? '已自动优化完成'
    : '结果已优化，部分可进一步完善'
)

const headerIcon = computed(() => props.report.fixed ? '\u2705' : '\u26A0\uFE0F')

const roundsLabel = computed(() => `${props.report.rounds}次优化`)

const categoryLabels: Record<string, string> = {
  citation_issues: '引用不完整',
  structure_issues: '结构不完整',
  depth_issues: '深度不足',
  coherence_issues: '逻辑连贯性',
  completeness_issues: '内容完整性',
  balance_issues: '观点平衡性',
}

function getCategoryLabel(category: string): string {
  return categoryLabels[category] || category
}

function getSeverityLabel(severity: 'error' | 'warning'): string {
  return severity === 'error' ? '严重' : '警告'
}
</script>

<template>
  <div class="diagnostic-panel" :class="panelClass">
    <button
      class="diagnostic-header"
      :aria-expanded="expanded"
      aria-controls="diagnostic-body"
      @click="expanded = !expanded"
    >
      <span class="diagnostic-title">
        <span class="diagnostic-icon">{{ headerIcon }}</span>
        {{ headerText }}
      </span>
      <span class="diagnostic-rounds">{{ roundsLabel }}</span>
      <i class="chip-arrow" :class="{ open: expanded }">&#x203A;</i>
    </button>

    <div
      v-if="expanded"
      id="diagnostic-body"
      role="region"
      class="diagnostic-body"
    >
      <div class="diagnostic-subtitle">本次优化记录</div>

      <div
        v-for="log in report.evidence_log"
        :key="log.round"
        class="diagnostic-round"
      >
        <span class="round-badge">第 {{ log.round }} 次</span>
        <span class="round-category">{{ getCategoryLabel(log.category) }}</span>
        <span class="round-severity" :class="log.severity">
          {{ getSeverityLabel(log.severity) }}({{ log.issue_count }}处)
        </span>
        <span v-if="log.over_modified" class="round-over-modified">过度修改</span>
      </div>

      <div v-if="!report.fixed" class="diagnostic-hint">
        <span>已返回最佳结果。您可以手动补充完善，或：</span>
        <button class="resubmit-btn" @click="emit('resubmit')">
          重新提交更具体的问题
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.diagnostic-panel {
  margin-top: 12px;
  border-radius: 8px;
  font-size: 13px;
  overflow: hidden;
}
.diagnostic-panel.unfixed {
  background: #fffbeb;
  border: 1px solid #fde68a;
}
.diagnostic-panel.fixed {
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
}

.diagnostic-header {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 14px;
  border: none;
  background: transparent;
  cursor: pointer;
  font: inherit;
  text-align: left;
  user-select: none;
}
.diagnostic-header:focus-visible {
  outline: 2px solid #3b82f6;
  outline-offset: -2px;
}

.diagnostic-title {
  display: flex;
  align-items: center;
  gap: 6px;
}
.diagnostic-icon {
  font-size: 14px;
}
.diagnostic-rounds {
  margin-left: auto;
  font-size: 12px;
  color: #6b7280;
}
.chip-arrow {
  font-style: normal;
  font-size: 16px;
  color: #9ca3af;
  transition: transform 0.2s;
  display: inline-block;
}
.chip-arrow.open {
  transform: rotate(90deg);
}

.diagnostic-body {
  padding: 0 14px 12px;
  border-top: 1px solid rgba(0,0,0,0.06);
}

.diagnostic-subtitle {
  font-size: 12px;
  color: #6b7280;
  margin: 8px 0 6px;
}

.diagnostic-round {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
  flex-wrap: wrap;
}
.round-badge {
  background: #f3f4f6;
  padding: 1px 8px;
  border-radius: 4px;
  font-size: 12px;
  color: #374151;
}
.round-category {
  font-size: 13px;
  color: #1f2937;
}
.round-severity {
  font-size: 12px;
  padding: 1px 6px;
  border-radius: 3px;
}
.round-severity.error {
  background: #fee2e2;
  color: #dc2626;
}
.round-severity.warning {
  background: #fef3c7;
  color: #d97706;
}
.round-over-modified {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 3px;
  background: #fef3c7;
  color: #92400e;
}

.diagnostic-hint {
  margin-top: 10px;
  padding: 8px 12px;
  background: #f0f9ff;
  border-radius: 6px;
  font-size: 13px;
  color: #1e40af;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.resubmit-btn {
  background: #dbeafe;
  border: 1px solid #93c5fd;
  border-radius: 4px;
  padding: 3px 10px;
  font-size: 12px;
  color: #1d4ed8;
  cursor: pointer;
  transition: background 0.15s;
}
.resubmit-btn:hover {
  background: #bfdbfe;
}
.resubmit-btn:focus-visible {
  outline: 2px solid #3b82f6;
  outline-offset: 1px;
}
</style>
