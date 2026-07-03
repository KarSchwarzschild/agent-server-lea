<script setup lang="ts">
/**
 * 阶段进度组件 - P0优先级
 * 显示 Skill 执行的阶段进度：意图识别 → 工具执行 → 生成答案
 *
 * 设计原则：
 * - 使用「阶段进度」而非「步骤进度」，避免精确百分比
 * - 纯前端推断当前阶段，不依赖后端返回 UI 信息
 */
import { computed } from 'vue'
import { NIcon } from 'naive-ui'
import {
  CheckmarkCircleOutline,
  EllipseOutline,
  TimeOutline,
} from '@vicons/ionicons5'
import type { AggregationProgress } from '../types/super-agent'

// ==================== 类型定义 ====================

interface Phase {
  key: string
  label: string
  icon: string
}

type PhaseStatus = 'pending' | 'active' | 'done'

// ==================== Props ====================

const props = withDefaults(defineProps<{
  /** Skill 名称，用于获取对应的阶段配置 */
  skillName: string
  /** 当前阶段 key */
  currentPhase: string
  /** 是否已完成 */
  isCompleted?: boolean
  /** 执行耗时（毫秒） */
  executionTime?: number
  /** Aggregation@N 聚合进度 */
  aggregationProgress?: AggregationProgress
}>(), {
  isCompleted: false,
  executionTime: 0,
})

// ==================== 阶段配置 ====================

const SKILL_PHASES: Record<string, Phase[]> = {
  // 学术文献检索
  'academic-search': [
    { key: 'intent', label: '意图识别', icon: '🎯' },
    { key: 'search', label: '文献检索', icon: '🔍' },
    { key: 'answer', label: '生成答案', icon: '✍️' }
  ],

  // 联网搜索
  'web-search': [
    { key: 'intent', label: '意图识别', icon: '🎯' },
    { key: 'fetch', label: '网络检索', icon: '🌐' },
    { key: 'answer', label: '生成答案', icon: '✍️' }
  ],

  // 深度调研
  'deep-research': [
    { key: 'intent', label: '意图识别', icon: '🎯' },
    { key: 'analyze', label: '需求分析', icon: '📋' },
    { key: 'research', label: '资料收集', icon: '📚' },
    { key: 'outline', label: '大纲生成', icon: '🏗️' },
    { key: 'write', label: '报告撰写', icon: '📝' }
  ],

  // 智能文档生成
  'doc-generator': [
    { key: 'intent', label: '意图识别', icon: '🎯' },
    { key: 'outline', label: '框架生成', icon: '🏗️' },
    { key: 'content', label: '内容生成', icon: '✍️' }
  ],

  // 课程大纲生成
  'course-outline-generator': [
    { key: 'intent', label: '意图识别', icon: '🎯' },
    { key: 'structure', label: '结构设计', icon: '📋' },
    { key: 'output', label: '输出大纲', icon: '📄' }
  ],

  // 学习路径规划
  'learning-path-planner': [
    { key: 'intent', label: '意图识别', icon: '🎯' },
    { key: 'analyze', label: '需求分析', icon: '🧭' },
    { key: 'plan', label: '路径规划', icon: '🗺️' }
  ],

  // 政策分析
  'policy-analyzer': [
    { key: 'intent', label: '意图识别', icon: '🎯' },
    { key: 'collect', label: '资料收集', icon: '📰' },
    { key: 'analyze', label: '政策分析', icon: '📊' }
  ],

  // 多专家辩论
  'multi-expert-debate': [
    { key: 'intent', label: '意图识别', icon: '🎯' },
    { key: 'expert', label: '专家分析', icon: '👥' },
    { key: 'debate', label: '观点辩论', icon: '💬' },
    { key: 'summary', label: '生成总结', icon: '📝' }
  ],

  // 动画讲解生成
  'animation-generator': [
    { key: 'intent', label: '意图识别', icon: '🎯' },
    { key: 'knowledge', label: '知识获取', icon: '📚' },
    { key: 'create', label: '动画创作', icon: '🎬' }
  ],

  // 默认（兜底）
  'default': [
    { key: 'intent', label: '理解问题', icon: '🎯' },
    { key: 'process', label: '处理中', icon: '⚙️' },
    { key: 'answer', label: '生成答案', icon: '✍️' }
  ]
}

// ==================== 计算属性 ====================

/** 获取当前 Skill 的阶段列表 */
const phases = computed<Phase[]>(() => {
  return SKILL_PHASES[props.skillName] || SKILL_PHASES['default']
})

/** 当前阶段索引 */
const currentPhaseIndex = computed(() => {
  if (props.isCompleted) {
    return phases.value.length // 所有阶段完成
  }
  const index = phases.value.findIndex(p => p.key === props.currentPhase)
  return index >= 0 ? index : 0
})

/** 获取阶段状态 */
function getPhaseStatus(phase: Phase, index: number): PhaseStatus {
  if (props.isCompleted) return 'done'
  if (index < currentPhaseIndex.value) return 'done'
  if (index === currentPhaseIndex.value) return 'active'
  return 'pending'
}

/** 格式化执行时间 */
const formattedTime = computed(() => {
  if (!props.executionTime) return ''
  return (props.executionTime / 1000).toFixed(1) + 's'
})
</script>

<template>
  <div class="execution-progress" :class="{ completed: isCompleted }">
    <!-- 阶段列表 -->
    <div class="phases-container">
      <div
        v-for="(phase, index) in phases"
        :key="phase.key"
        class="phase-item"
        :class="getPhaseStatus(phase, index)"
      >
        <!-- 阶段图标 -->
        <div class="phase-icon">
          <template v-if="getPhaseStatus(phase, index) === 'done'">
            <n-icon :component="CheckmarkCircleOutline" :size="18" />
          </template>
          <template v-else-if="getPhaseStatus(phase, index) === 'active'">
            <n-icon :component="TimeOutline" :size="18" class="pulse" />
          </template>
          <template v-else>
            <n-icon :component="EllipseOutline" :size="18" />
          </template>
        </div>

        <!-- 阶段标签 -->
        <div class="phase-label">
          <span class="phase-emoji">{{ phase.icon }}</span>
          <span class="phase-text">{{ phase.label }}</span>
        </div>

        <!-- 连接线 -->
        <div v-if="index < phases.length - 1" class="phase-connector">
          <div
            class="connector-line"
            :class="{ filled: getPhaseStatus(phase, index) === 'done' }"
          />
        </div>
      </div>
    </div>

    <!-- Aggregation@N 聚合进度 -->
    <div v-if="aggregationProgress && aggregationProgress.total > 1" class="aggregation-inline">
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

    <!-- 完成状态时显示耗时 -->
    <div v-if="isCompleted && executionTime" class="completion-badge">
      <n-icon :component="CheckmarkCircleOutline" :size="14" />
      <span>完成 ({{ formattedTime }})</span>
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

.execution-progress.completed {
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
}

.phases-container {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 4px;
}

.phase-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  position: relative;
  flex: 1;
  min-width: 0;
}

/* 阶段图标 */
.phase-icon {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #e5e7eb;
  color: #9ca3af;
  transition: all 0.3s ease;
  margin-bottom: 6px;
  position: relative;
  z-index: 1;
}

.phase-item.done .phase-icon {
  background: #10b981;
  color: #fff;
}

.phase-item.active .phase-icon {
  background: #3b82f6;
  color: #fff;
  box-shadow: 0 0 0 4px rgba(59, 130, 246, 0.2);
}

.phase-item.pending .phase-icon {
  background: #e5e7eb;
  color: #9ca3af;
}

/* 脉冲动画 */
.pulse {
  animation: pulse 1.5s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% {
    opacity: 1;
  }
  50% {
    opacity: 0.5;
  }
}

/* 阶段标签 */
.phase-label {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
}

.phase-emoji {
  font-size: 14px;
  line-height: 1;
}

.phase-text {
  font-size: 11px;
  color: #6b7280;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 70px;
  text-align: center;
}

.phase-item.active .phase-text {
  color: #3b82f6;
  font-weight: 500;
}

.phase-item.done .phase-text {
  color: #10b981;
}

/* 连接线 */
.phase-connector {
  position: absolute;
  top: 14px;
  left: 50%;
  width: calc(100% - 28px);
  height: 2px;
  transform: translateX(14px);
  z-index: 0;
}

.connector-line {
  width: 100%;
  height: 100%;
  background: #e5e7eb;
  transition: background 0.3s ease;
}

.connector-line.filled {
  background: #10b981;
}

/* 完成徽章 */
.completion-badge {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  margin-top: 10px;
  padding: 4px 10px;
  background: #dcfce7;
  color: #16a34a;
  border-radius: var(--le-radius-full, 9999px);
  font-size: 12px;
  font-weight: 500;
}

/* Aggregation@N 聚合进度 */
.aggregation-inline {
  margin-top: 10px;
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

  .phase-icon {
    width: 24px;
    height: 24px;
  }

  .phase-icon :deep(.n-icon) {
    font-size: 14px !important;
  }

  .phase-emoji {
    font-size: 12px;
  }

  .phase-text {
    font-size: 10px;
    max-width: 50px;
  }

  .phase-connector {
    top: 12px;
    width: calc(100% - 24px);
    transform: translateX(12px);
  }
}
</style>
