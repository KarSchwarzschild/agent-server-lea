<script setup lang="ts">
/**
 * Aggregation@N 多路增强进度组件
 *
 * 说明：原本还渲染「意图识别 → 处理中 → 生成答案」三阶段条，但对通用 chat
 * 是低信息占位符（StatusText 已经给出实时状态，MessageItem 折叠区已存全过程）。
 * 现在只保留多路增强分路进度 —— 那是真正有价值的动态信息。
 */
import type { AggregationProgress } from '../types/super-agent'

// ==================== Props ====================

defineProps<{
  /** Aggregation@N 聚合进度 */
  aggregationProgress?: AggregationProgress
}>()
</script>

<template>
  <div
    v-if="aggregationProgress && aggregationProgress.total > 1"
    class="execution-progress"
  >
    <div class="aggregation-inline">
      <div class="agg-header">
        <span class="agg-badge">⚡ {{ aggregationProgress.total }}路增强</span>
        <span v-if="aggregationProgress.phase === 'synthesis'" class="agg-phase synthesis">合成中...</span>
        <span v-else-if="aggregationProgress.phase === 'complete'" class="agg-phase complete">聚合完成</span>
        <span v-else class="agg-phase">{{ aggregationProgress.done }}/{{ aggregationProgress.total }} 路完成</span>
      </div>
      <div class="agg-paths">
        <div
          v-for="path in aggregationProgress.paths"
          :key="path.index"
          class="agg-path"
          :class="path.status"
        >
          <span class="agg-path-icon">
            <template v-if="path.status === 'completed'">✅</template>
            <template v-else-if="path.status === 'running'">⏳</template>
            <template v-else-if="path.status === 'failed' || path.status === 'timeout'">❌</template>
            <template v-else>○</template>
          </span>
          <span class="agg-path-label">{{ path.label }}</span>
          <span v-if="path.refs_count" class="agg-path-refs">{{ path.refs_count }}篇</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.execution-progress {
  padding: 12px 16px;
  background: var(--le-color-background, #f5f7f9);
  border-radius: var(--le-radius-md, 8px);
  margin-bottom: 12px;
}

/* Aggregation@N 聚合进度 */
.aggregation-inline {
  padding: 8px 12px;
  background: linear-gradient(135deg, #eff6ff 0%, #f0fdf4 100%);
  border: 1px solid #bfdbfe;
  border-radius: 8px;
}

.agg-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.agg-badge {
  font-size: 12px;
  font-weight: 600;
  color: #2563eb;
  background: #dbeafe;
  padding: 2px 8px;
  border-radius: 10px;
}

.agg-phase {
  font-size: 12px;
  color: #6b7280;
}

.agg-phase.synthesis {
  color: #f59e0b;
  font-weight: 500;
}

.agg-phase.complete {
  color: #10b981;
  font-weight: 500;
}

.agg-paths {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.agg-path {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: #374151;
  padding: 3px 8px;
  background: #fff;
  border-radius: 6px;
  border: 1px solid #e5e7eb;
}

.agg-path.completed {
  border-color: #86efac;
  background: #f0fdf4;
}

.agg-path.running {
  border-color: #93c5fd;
  background: #eff6ff;
  animation: pathPulse 2s ease-in-out infinite;
}

.agg-path.failed,
.agg-path.timeout {
  border-color: #fca5a5;
  background: #fef2f2;
  color: #991b1b;
}

.agg-path-icon {
  font-size: 11px;
}

.agg-path-label {
  max-width: 100px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agg-path-refs {
  font-size: 10px;
  color: #9ca3af;
}

@keyframes pathPulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.7; }
}

/* 移动端适配 */
@media (max-width: 480px) {
  .execution-progress {
    padding: 10px 12px;
  }
}
</style>
