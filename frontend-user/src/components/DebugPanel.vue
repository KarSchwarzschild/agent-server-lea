<script setup lang="ts">
/**
 * Debug 面板组件 - 实时 SSE 事件流查看器
 *
 * 功能：
 * - 事件统计栏：总事件数 | 总耗时 | 总字节数 | model | tokens
 * - 实时事件列表：每行 [+0.3s] event_type { ... }
 * - 事件过滤器：按 event type 多选过滤
 * - 单条事件展开：点击展开完整 JSON
 * - 导出按钮：导出完整事件日志
 */
import { ref, computed } from 'vue'
import {
  NIcon,
  NButton,
  NTag,
  NCheckbox,
  NCheckboxGroup,
} from 'naive-ui'
import {
  ChevronDownOutline,
  DownloadOutline,
} from '@vicons/ionicons5'
import type { SSEDebugEvent, SSEDebugStats } from '../composables/useSSEDebug'

const props = defineProps<{
  events: SSEDebugEvent[]
  stats: SSEDebugStats
  onExport: () => void
}>()

// 面板折叠状态
const collapsed = ref(true)

// 事件过滤
const allEventTypes = computed(() => {
  const types = new Set<string>()
  for (const evt of props.events) {
    types.add(evt.event)
  }
  return Array.from(types)
})

const selectedTypes = ref<string[]>([])

// 选中为空 = 显示全部
const filteredEvents = computed(() => {
  if (selectedTypes.value.length === 0) return props.events
  return props.events.filter(e => selectedTypes.value.includes(e.event))
})

// 单条事件展开
const expandedIds = ref<Set<number>>(new Set())

function toggleEvent(id: number) {
  if (expandedIds.value.has(id)) {
    expandedIds.value.delete(id)
  } else {
    expandedIds.value.add(id)
  }
  // 触发响应式
  expandedIds.value = new Set(expandedIds.value)
}

// 格式化
function formatRelativeTime(ms: number): string {
  return `+${(ms / 1000).toFixed(2)}s`
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`
  return `${(bytes / 1024).toFixed(1)}KB`
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function formatJson(data: Record<string, unknown>): string {
  return JSON.stringify(data, null, 2)
}

function truncateJson(data: Record<string, unknown>): string {
  const str = JSON.stringify(data)
  if (str.length <= 80) return str
  return str.substring(0, 77) + '...'
}

// 事件类型颜色
function getEventColor(event: string): string {
  const colorMap: Record<string, string> = {
    'thinking': '#8b5cf6',
    'intent': '#3b82f6',
    'skill_start': '#0ea5e9',
    'action': '#f59e0b',
    'observation': '#10b981',
    'answering': '#6366f1',
    'content': '#22c55e',
    'done': '#16a34a',
    'error': '#ef4444',
    'references': '#8b5cf6',
    'iteration_start': '#94a3b8',
    'llm_usage': '#06b6d4',
    'tool_call_start': '#eab308',
    'tool_call_delta': '#d97706',
    'skill_done': '#059669',
  }
  return colorMap[event] || '#6b7280'
}
</script>

<template>
  <div class="debug-panel" :class="{ collapsed }">
    <!-- 统计栏 (始终可见) -->
    <div class="debug-stats-bar" @click="collapsed = !collapsed">
      <div class="stats-left">
        <span class="stats-label">Debug</span>
        <span class="stat-item">
          <span class="stat-value">{{ stats.totalEvents }}</span>
          <span class="stat-unit">事件</span>
        </span>
        <span class="stat-divider">|</span>
        <span class="stat-item">
          <span class="stat-value">{{ formatDuration(stats.elapsedMs) }}</span>
        </span>
        <span class="stat-divider">|</span>
        <span class="stat-item">
          <span class="stat-value">{{ formatBytes(stats.totalBytes) }}</span>
        </span>
        <template v-if="stats.model">
          <span class="stat-divider">|</span>
          <span class="stat-item stat-model">{{ stats.model }}</span>
        </template>
        <template v-if="stats.totalTokens > 0">
          <span class="stat-divider">|</span>
          <span class="stat-item">
            <span class="stat-value">{{ stats.totalTokens }}</span>
            <span class="stat-unit">tokens</span>
          </span>
        </template>
      </div>
      <div class="stats-actions">
        <n-button
          size="tiny"
          quaternary
          @click.stop="onExport"
          title="导出 JSON"
        >
          <template #icon>
            <n-icon :component="DownloadOutline" :size="14" />
          </template>
        </n-button>
        <n-icon
          :component="ChevronDownOutline"
          :size="16"
          class="collapse-icon"
          :class="{ expanded: !collapsed }"
        />
      </div>
    </div>

    <!-- 展开内容 -->
    <template v-if="!collapsed">
      <!-- 过滤器 -->
      <div v-if="allEventTypes.length > 0" class="debug-filter">
        <n-checkbox-group v-model:value="selectedTypes" size="small">
          <n-checkbox
            v-for="t in allEventTypes"
            :key="t"
            :value="t"
            :label="t"
            size="small"
          />
        </n-checkbox-group>
        <span v-if="selectedTypes.length > 0" class="filter-hint">
          显示 {{ filteredEvents.length }}/{{ events.length }}
        </span>
      </div>

      <!-- 事件列表 -->
      <div class="debug-event-list">
        <div
          v-for="evt in filteredEvents"
          :key="evt.id"
          class="debug-event-item"
          @click="toggleEvent(evt.id)"
        >
          <div class="event-row">
            <span class="event-time">{{ formatRelativeTime(evt.relativeTime) }}</span>
            <n-tag
              :bordered="false"
              size="small"
              :style="{ '--n-color': getEventColor(evt.event) + '18', '--n-text-color': getEventColor(evt.event) }"
              class="event-type-tag"
            >
              {{ evt.event }}
            </n-tag>
            <span class="event-preview">{{ truncateJson(evt.data) }}</span>
          </div>
          <!-- 展开态：完整 JSON -->
          <pre
            v-if="expandedIds.has(evt.id)"
            class="event-detail"
          >{{ formatJson(evt.data) }}</pre>
        </div>
        <div v-if="filteredEvents.length === 0" class="debug-empty">
          暂无事件
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.debug-panel {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fafbfc;
  margin-top: 12px;
  overflow: hidden;
  font-size: 12px;
}

.debug-panel.collapsed {
  border-radius: 8px;
}

.debug-stats-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 12px;
  background: #f1f3f5;
  cursor: pointer;
  user-select: none;
  gap: 8px;
}

.debug-stats-bar:hover {
  background: #e9ecef;
}

.stats-left {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  min-width: 0;
}

.stats-label {
  font-weight: 600;
  color: #374151;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.stat-item {
  display: flex;
  align-items: center;
  gap: 2px;
  color: #4b5563;
}

.stat-value {
  font-weight: 500;
  color: #1f2937;
  font-variant-numeric: tabular-nums;
}

.stat-unit {
  color: #9ca3af;
}

.stat-model {
  color: #6366f1;
  font-weight: 500;
}

.stat-divider {
  color: #d1d5db;
}

.stats-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.collapse-icon {
  transition: transform 0.2s ease;
  color: #6b7280;
}

.collapse-icon.expanded {
  transform: rotate(180deg);
}

.debug-filter {
  padding: 6px 12px;
  border-bottom: 1px solid #e5e7eb;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.filter-hint {
  color: #9ca3af;
  font-size: 11px;
  margin-left: auto;
}

.debug-event-list {
  max-height: 300px;
  overflow-y: auto;
  padding: 4px 0;
}

.debug-event-item {
  padding: 3px 12px;
  cursor: pointer;
  transition: background 0.1s;
}

.debug-event-item:hover {
  background: #f0f1f3;
}

.event-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.event-time {
  font-family: 'SF Mono', 'Fira Code', monospace;
  color: #9ca3af;
  font-size: 11px;
  flex-shrink: 0;
  width: 60px;
  text-align: right;
}

.event-type-tag {
  flex-shrink: 0;
  font-size: 11px;
  height: 20px;
  padding: 0 6px;
}

.event-preview {
  font-family: 'SF Mono', 'Fira Code', monospace;
  color: #6b7280;
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.event-detail {
  margin: 4px 0 4px 68px;
  padding: 8px;
  background: #f8f9fa;
  border: 1px solid #e5e7eb;
  border-radius: 4px;
  font-family: 'SF Mono', 'Fira Code', monospace;
  font-size: 11px;
  color: #374151;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 200px;
  overflow-y: auto;
}

.debug-empty {
  padding: 16px;
  text-align: center;
  color: #9ca3af;
}

/* 移动端适配 */
@media (max-width: 480px) {
  .debug-panel {
    font-size: 11px;
  }

  .debug-stats-bar {
    padding: 4px 8px;
  }

  .stats-left {
    gap: 4px;
  }

  .debug-event-list {
    max-height: 200px;
  }

  .event-time {
    width: 50px;
    font-size: 10px;
  }

  .event-detail {
    margin-left: 58px;
  }
}
</style>
