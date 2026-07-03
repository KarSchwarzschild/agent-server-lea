<script setup lang="ts">
/**
 * Execution Timeline 组件 - 阶段时间线可视化
 *
 * 基于 SSE 事件时间戳，渲染水平条形图：
 * [意图识别 0.3s] [工具执行 2.1s] [LLM思考 1.5s] [答案生成 3.2s]
 */
import { computed, ref } from 'vue'
import type { SSEDebugEvent } from '../composables/useSSEDebug'

interface TimelinePhase {
  name: string
  startMs: number
  endMs: number
  durationMs: number
  color: string
}

const props = defineProps<{
  events: SSEDebugEvent[]
}>()

const expanded = ref(false)

const phases = computed<TimelinePhase[]>(() => {
  if (props.events.length === 0) return []

  const result: TimelinePhase[] = []

  // 按相对时间分析事件阶段
  let intentStart = -1
  let intentEnd = -1
  let toolStart = -1
  let toolEnd = -1
  let answerStart = -1
  let answerEnd = -1
  let thinkingMs = 0
  let lastThinkingStart = -1

  for (const evt of props.events) {
    const t = evt.relativeTime

    switch (evt.event) {
      case 'thinking':
      case 'intent':
        if (intentStart < 0) intentStart = t
        intentEnd = t
        break

      case 'skill_start':
        if (intentStart >= 0 && intentEnd >= 0) {
          intentEnd = t
        }
        break

      case 'iteration_start':
        lastThinkingStart = t
        break

      case 'action':
      case 'tool_call_start':
        if (lastThinkingStart >= 0) {
          thinkingMs += t - lastThinkingStart
          lastThinkingStart = -1
        }
        if (toolStart < 0) toolStart = t
        break

      case 'observation':
        toolEnd = t
        break

      case 'answering':
        answerStart = t
        if (lastThinkingStart >= 0) {
          thinkingMs += t - lastThinkingStart
          lastThinkingStart = -1
        }
        break

      case 'content':
        if (answerStart < 0) answerStart = t
        answerEnd = t
        break

      case 'done':
      case 'skill_done':
        if (answerStart >= 0) answerEnd = t
        break
    }
  }

  // 构建阶段列表
  if (intentStart >= 0 && intentEnd > intentStart) {
    result.push({
      name: '意图识别',
      startMs: intentStart,
      endMs: intentEnd,
      durationMs: intentEnd - intentStart,
      color: '#3b82f6',
    })
  }

  if (thinkingMs > 0) {
    // LLM 思考时间是分散在不同阶段的总和
    result.push({
      name: 'LLM 思考',
      startMs: 0, // 虚拟位置
      endMs: 0,
      durationMs: thinkingMs,
      color: '#8b5cf6',
    })
  }

  if (toolStart >= 0 && toolEnd > toolStart) {
    result.push({
      name: '工具执行',
      startMs: toolStart,
      endMs: toolEnd,
      durationMs: toolEnd - toolStart,
      color: '#f59e0b',
    })
  }

  if (answerStart >= 0 && answerEnd > answerStart) {
    result.push({
      name: '答案生成',
      startMs: answerStart,
      endMs: answerEnd,
      durationMs: answerEnd - answerStart,
      color: '#10b981',
    })
  }

  return result
})

const totalDurationMs = computed(() => {
  if (props.events.length === 0) return 0
  const last = props.events[props.events.length - 1]
  return last.relativeTime
})

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function getWidthPercent(durationMs: number): number {
  if (totalDurationMs.value <= 0) return 0
  // 按每个阶段占总时间的比例计算
  const sum = phases.value.reduce((acc, p) => acc + p.durationMs, 0)
  if (sum <= 0) return 0
  return Math.max(5, (durationMs / sum) * 100)
}
</script>

<template>
  <div v-if="phases.length > 0" class="execution-timeline">
    <div class="timeline-header" @click="expanded = !expanded">
      <span class="timeline-title">
        <span class="timeline-toggle" :class="{ expanded }">▶</span>
        Execution Timeline
      </span>
      <span class="timeline-total">{{ phases.length }} phases · {{ formatDuration(totalDurationMs) }}</span>
    </div>
    <template v-if="expanded">
      <div class="timeline-bar-container">
        <div
          v-for="(phase, idx) in phases"
          :key="idx"
          class="timeline-segment"
          :style="{
            width: getWidthPercent(phase.durationMs) + '%',
            background: phase.color,
          }"
          :title="`${phase.name}: ${formatDuration(phase.durationMs)}`"
        >
          <span class="segment-label" v-if="getWidthPercent(phase.durationMs) > 12">
            {{ phase.name }}
          </span>
        </div>
      </div>
      <div class="timeline-legend">
        <div
          v-for="(phase, idx) in phases"
          :key="idx"
          class="legend-item"
        >
          <span class="legend-dot" :style="{ background: phase.color }"></span>
          <span class="legend-name">{{ phase.name }}</span>
          <span class="legend-duration">{{ formatDuration(phase.durationMs) }}</span>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.execution-timeline {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 10px 12px;
  background: #fafbfc;
  margin-top: 8px;
}

.timeline-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  cursor: pointer;
  user-select: none;
}

.timeline-header:hover {
  opacity: 0.8;
}

/* 展开时有下边距，收起时无 */
.execution-timeline:has(.timeline-bar-container) .timeline-header {
  margin-bottom: 8px;
}

.timeline-toggle {
  display: inline-block;
  font-size: 9px;
  margin-right: 4px;
  transition: transform 0.2s;
}

.timeline-toggle.expanded {
  transform: rotate(90deg);
}

.timeline-title {
  font-size: 11px;
  font-weight: 600;
  color: #374151;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.timeline-total {
  font-size: 12px;
  font-weight: 500;
  color: #6b7280;
  font-variant-numeric: tabular-nums;
}

.timeline-bar-container {
  display: flex;
  height: 20px;
  border-radius: 4px;
  overflow: hidden;
  gap: 1px;
}

.timeline-segment {
  display: flex;
  align-items: center;
  justify-content: center;
  min-width: 4px;
  transition: width 0.3s ease;
}

.segment-label {
  font-size: 10px;
  color: #fff;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  padding: 0 4px;
}

.timeline-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 6px;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
}

.legend-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.legend-name {
  color: #6b7280;
}

.legend-duration {
  color: #374151;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}

@media (max-width: 480px) {
  .execution-timeline {
    padding: 8px;
  }

  .timeline-legend {
    gap: 8px;
  }

  .legend-item {
    font-size: 10px;
  }
}
</style>
