<script setup lang="ts">
/**
 * 同行评审面板
 *
 * PeerReviewer 的 5 维度审查结果，按 severity 分组折叠展示。
 * 独立组件，接收 PeerReviewData prop。
 */
import { ref, computed } from 'vue'
import type { PeerReviewData, PeerReviewItem } from '../../types/super-agent'

const props = defineProps<{
  review: PeerReviewData
}>()

const expanded = ref(false)

const totalItems = computed(() => props.review.items.length)

const groupedItems = computed(() => {
  const groups: Record<'critical' | 'important' | 'minor', PeerReviewItem[]> = {
    critical: [],
    important: [],
    minor: [],
  }
  for (const item of props.review.items) {
    const severity = item.severity || 'minor'
    if (groups[severity]) {
      groups[severity].push(item)
    } else {
      groups.minor.push(item)
    }
  }
  return groups
})

const dimensionLabels: Record<string, string> = {
  argument: '论点支撑',
  logic: '逻辑推理',
  citation: '引用规范',
  balance: '观点平衡',
  originality: '原创分析',
}

const severityConfig = {
  critical: { icon: '\uD83D\uDD34', label: '需要关注', color: '#dc2626' },
  important: { icon: '\uD83D\uDFE1', label: '建议改进', color: '#d97706' },
  minor: { icon: '\u26AA', label: '轻微建议', color: '#6b7280' },
} as const

function getDimensionLabel(dimension: string): string {
  return dimensionLabels[dimension] || dimension
}
</script>

<template>
  <div class="peer-review-panel">
    <button
      class="review-header"
      :aria-expanded="expanded"
      aria-controls="review-body"
      @click="expanded = !expanded"
    >
      <span class="review-title">
        <span class="review-icon">\uD83D\uDCCB</span>
        同行评审意见
      </span>
      <span class="review-count">{{ totalItems }}条建议</span>
      <i class="chip-arrow" :class="{ open: expanded }">&#x203A;</i>
    </button>

    <div
      v-if="expanded"
      id="review-body"
      role="region"
      class="review-body"
    >
      <div v-if="review.parse_failed" class="review-parse-warning">
        \u26A0\uFE0F 评审结果解析异常，以下为原始反馈
      </div>

      <template v-for="severity in (['critical', 'important', 'minor'] as const)" :key="severity">
        <div v-if="groupedItems[severity].length > 0" class="review-group">
          <div class="group-header" :style="{ color: severityConfig[severity].color }">
            {{ severityConfig[severity].icon }} {{ severityConfig[severity].label }} ({{ groupedItems[severity].length }})
          </div>
          <div class="group-items">
            <div
              v-for="(item, idx) in groupedItems[severity]"
              :key="idx"
              class="review-item"
            >
              <div class="item-header">
                <span class="item-dimension">{{ getDimensionLabel(item.dimension) }}</span>
                <span v-if="item.location" class="item-location">{{ item.location }}</span>
              </div>
              <div class="item-comment">{{ item.comment }}</div>
              <div v-if="item.suggestion" class="item-suggestion">
                建议: {{ item.suggestion }}
              </div>
            </div>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.peer-review-panel {
  margin-top: 12px;
  border-radius: 8px;
  font-size: 13px;
  background: #f1f5f9;
  border: 1px solid #cbd5e1;
  overflow: hidden;
}

.review-header {
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
.review-header:focus-visible {
  outline: 2px solid #3b82f6;
  outline-offset: -2px;
}

.review-title {
  display: flex;
  align-items: center;
  gap: 6px;
}
.review-icon {
  font-size: 14px;
}
.review-count {
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

.review-body {
  padding: 0 14px 12px;
  border-top: 1px solid rgba(0,0,0,0.06);
}

.review-parse-warning {
  margin: 8px 0;
  padding: 6px 10px;
  background: #fef3c7;
  border-radius: 4px;
  font-size: 12px;
  color: #92400e;
}

.review-group {
  margin-top: 10px;
}
.group-header {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 6px;
}
.group-items {
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  overflow: hidden;
  background: #fff;
}

.review-item {
  padding: 8px 12px;
}
.review-item + .review-item {
  border-top: 1px solid #e2e8f0;
}

.item-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}
.item-dimension {
  font-weight: 600;
  color: #1f2937;
  font-size: 13px;
}
.item-location {
  font-size: 11px;
  color: #9ca3af;
}
.item-comment {
  color: #374151;
  font-size: 13px;
  line-height: 1.5;
}
.item-suggestion {
  margin-top: 4px;
  font-size: 12px;
  color: #6b7280;
  font-style: italic;
}
</style>
