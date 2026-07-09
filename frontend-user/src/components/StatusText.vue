<script setup lang="ts">
/**
 * 动态状态文字组件 - P0优先级
 * 基于 SSE 事件显示友好的状态提示文字
 *
 * 示例：
 * - 「正在分析您的问题...」
 * - 「正在从万方数据库检索文献...」
 * - 「已找到 15 篇相关文献」
 * - 「正在生成回答...」
 */
import { computed } from 'vue'
import { NSpin } from 'naive-ui'

// ==================== 类型定义 ====================

export interface StatusEvent {
  type: string
  data?: Record<string, unknown>
}

// ==================== Props ====================

const props = withDefaults(defineProps<{
  /** 当前事件类型 */
  eventType: string
  /** 事件数据 */
  eventData?: Record<string, unknown>
  /** Skill 名称 */
  skillName?: string
  /** 是否显示加载动画 */
  showSpinner?: boolean
}>(), {
  eventData: () => ({}),
  skillName: '',
  showSpinner: true,
})

// ==================== 工具名称映射 ====================

const TOOL_DISPLAY_NAMES: Record<string, string> = {
  // 学术搜索相关
  'academic_search': '学术数据库',
  'wanfang_search': '万方数据库',
  'semantic_scholar_search': 'Semantic Scholar',
  'arxiv_search': 'arXiv',
  'wanfang': '万方数据库',
  'semantic_scholar': 'Semantic Scholar',
  'arxiv': 'arXiv',

  // 知识库
  'kb_query': '本地知识库',
  'knowledge_query': '知识库',
  'query_kb': '知识库',

  // 网络
  'web_fetch': '互联网',
  'web_search': '网络搜索',
  'fetch_url': '网页内容',

  // 文件操作
  'write_file': '文件生成',
  'file_write': '文件写入',
  'read_file': '文件读取',

  // 大纲生成
  'generate_outline': '大纲生成',

  // 引用处理
  'cite_extract': '引用提取',
  'cite_normalize': '引用规范化',
  'cite_check': '引用检查',
  'cite_reformat': '引用重编号',
  'word_count': '字数统计',

  // 教育数据
  'edu_data_search': '教育数据检索',
}

// ==================== 状态文字映射 ====================

const STATUS_TEXT_MAP: Record<string, (data: Record<string, unknown>) => string> = {
  // 思考/意图识别
  'thinking': (data) => (data.text as string) || (data.content as string) || (data.thought as string) || '正在分析您的问题...',
  'intent': (data) => {
    const skillName = data.skill_display_name || data.skill || data.skill_name || '智能问答'
    return `已识别为「${skillName}」`
  },

  // Pipeline 阶段推进（透传后端文案）
  'progress': (data) => (data.content as string) || (data.message as string) || '处理中...',

  // 技能执行
  'skill_start': (data) => `开始执行「${data.display_name || data.skill || '技能'}」`,

  // 工具调用（新版 action 事件）
  'action': (data) => {
    const toolName = TOOL_DISPLAY_NAMES[data.tool as string] || data.tool_display_name as string || data.tool as string || '工具'
    return `正在从${toolName}检索...`
  },

  // 工具调用（兼容旧版）
  'tool_call': (data) => {
    const toolName = TOOL_DISPLAY_NAMES[data.tool as string] || data.tool as string || '工具'
    return `正在从${toolName}检索...`
  },

  // 工具结果（新版 observation 事件）
  'observation': (data) => {
    if (data.status === 'error') {
      const toolName = TOOL_DISPLAY_NAMES[data.tool_name as string] || data.tool_display_name as string || '工具'
      return `${toolName}执行失败`
    }
    if (data.summary) {
      return data.summary as string
    }
    return '检索完成'
  },

  // 工具结果（兼容旧版）
  'tool_result': (data) => {
    // 尝试从结果中提取文献数量
    if (data.doc_count) {
      return `已找到 ${data.doc_count} 篇相关文献`
    }
    const result = data.result as string
    if (result && typeof result === 'string') {
      // 尝试从文本中提取数量
      const match = result.match(/共(\d+)条|找到(\d+)篇|(\d+)\s*(条|篇|个)/)
      if (match) {
        const count = match[1] || match[2] || match[3]
        return `已找到 ${count} 篇相关文献`
      }
    }
    return '检索完成'
  },

  // 答案生成（优先使用后端推送的 message）
  'answering': (data) => (data.message as string) || '正在生成回答...',
  'content': () => '生成中...',

  // 完成
  'skill_done': (data) => {
    const time = data.execution_time_ms as number
    if (time) {
      return `完成 (${(time / 1000).toFixed(1)}s)`
    }
    return '完成'
  },
  'done': () => '完成',

  // 辩论相关
  'debate_created': () => '辩论会话已创建',
  'round_start': (data) => `第 ${data.round} 轮辩论开始`,
  'round_end': (data) => `第 ${data.round} 轮辩论结束`,
  'expert_speaking': (data) => `${data.expert_name || '专家'}正在发言...`,
  'final_summary': () => '正在生成最终总结...',

  // 错误
  'error': (data) => data.message as string || '发生错误',
}

// ==================== 计算属性 ====================

/** 获取状态图标 */
const statusIcon = computed(() => {
  const iconMap: Record<string, string> = {
    'thinking': '🎯',
    'intent': '🎯',
    'progress': '⚙️',
    'skill_start': '⚡',
    'action': '📡',
    'tool_call': '📡',
    'observation': '📚',
    'tool_result': '📚',
    'answering': '✍️',
    'content': '✍️',
    'skill_done': '✓',
    'done': '✓',
    'debate_created': '👥',
    'round_start': '💬',
    'round_end': '⏸️',
    'expert_speaking': '🎤',
    'final_summary': '📝',
    'error': '❌',
  }
  return iconMap[props.eventType] || '⏳'
})

/** 获取状态文字 */
const statusText = computed(() => {
  const handler = STATUS_TEXT_MAP[props.eventType]
  if (handler) {
    return handler(props.eventData)
  }
  return '处理中...'
})

/** 是否显示旋转加载 */
const showLoading = computed(() => {
  if (!props.showSpinner) return false
  const loadingTypes = ['thinking', 'action', 'tool_call', 'answering', 'content', 'skill_start', 'progress', 'expert_speaking', 'final_summary']
  return loadingTypes.includes(props.eventType)
})

/** 状态类型（用于样式） */
const statusType = computed(() => {
  if (['skill_done', 'done', 'tool_result'].includes(props.eventType)) {
    return 'success'
  }
  if (props.eventType === 'observation') {
    if (props.eventData?.status === 'error') return 'warning'
    return 'success'
  }
  if (props.eventType === 'error') {
    return 'error'
  }
  return 'processing'
})
</script>

<template>
  <div class="status-text" :class="statusType">
    <!-- 状态图标 -->
    <span class="status-icon">{{ statusIcon }}</span>

    <!-- 状态文字 -->
    <span class="status-message">{{ statusText }}</span>

    <!-- 加载动画 -->
    <n-spin v-if="showLoading" :size="14" class="status-spinner" />
  </div>
</template>

<style scoped>
.status-text {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: #f0f5ff;
  border-radius: var(--le-radius-md, 8px);
  font-size: 13px;
  color: #3b82f6;
  transition: all 0.3s ease;
}

.status-text.success {
  background: #f0fdf4;
  color: #16a34a;
}

.status-text.error {
  background: #fef2f2;
  color: #dc2626;
}

.status-text.warning {
  background: #fffbeb;
  color: #d97706;
}

.status-icon {
  font-size: 16px;
  line-height: 1;
  flex-shrink: 0;
}

.status-message {
  flex: 1;
  line-height: 1.4;
}

.status-spinner {
  flex-shrink: 0;
}

/* 移动端适配 */
@media (max-width: 480px) {
  .status-text {
    padding: 6px 10px;
    font-size: 12px;
  }

  .status-icon {
    font-size: 14px;
  }
}
</style>
