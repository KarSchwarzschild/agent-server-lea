<script setup lang="ts">
/**
 * 错误状态组件 - P0优先级
 * 处理各种错误场景：网络超时、数据源失败、空结果等
 *
 * 特性：
 * - 支持自动降级提示
 * - 显示已尝试的数据源
 * - 提供重试和备选操作
 */
import { computed } from 'vue'
import { NButton, NIcon, NAlert, NSpace } from 'naive-ui'
import {
  RefreshOutline,
  CloseCircleOutline,
  WarningOutline,
  SearchOutline,
  ArrowForwardOutline,
} from '@vicons/ionicons5'

// ==================== 类型定义 ====================

export type ErrorType =
  | 'network_timeout'      // 网络超时
  | 'source_failed'        // 单个数据源失败
  | 'all_sources_failed'   // 所有数据源失败
  | 'empty_result'         // 空结果
  | 'server_error'         // 服务器错误
  | 'unknown'              // 未知错误

export interface DataSourceStatus {
  name: string
  displayName: string
  status: 'success' | 'failed' | 'trying' | 'pending'
  error?: string
}

// ==================== Props ====================

const props = withDefaults(defineProps<{
  /** 错误类型 */
  errorType: ErrorType
  /** 错误消息 */
  message?: string
  /** 数据源状态（用于降级场景） */
  dataSources?: DataSourceStatus[]
  /** 搜索关键词（用于空结果场景） */
  searchQuery?: string
  /** HTTP 状态码 */
  statusCode?: number
  /** 是否正在重试 */
  retrying?: boolean
}>(), {
  message: '',
  dataSources: () => [],
  searchQuery: '',
  statusCode: 0,
  retrying: false,
})

// ==================== Emits ====================

const emit = defineEmits<{
  /** 重试操作 */
  (e: 'retry'): void
  /** 跳过当前数据源 */
  (e: 'skip'): void
  /** 仅查询本地知识库 */
  (e: 'use-local-only'): void
  /** 修改搜索词 */
  (e: 'modify-query'): void
  /** 关闭/取消 */
  (e: 'close'): void
}>()

// ==================== 计算属性 ====================

/** 错误配置 */
const errorConfig = computed(() => {
  const configs: Record<ErrorType, {
    icon: typeof WarningOutline
    title: string
    alertType: 'warning' | 'error' | 'info'
    showDataSources: boolean
  }> = {
    'network_timeout': {
      icon: WarningOutline,
      title: '请求超时',
      alertType: 'warning',
      showDataSources: false,
    },
    'source_failed': {
      icon: WarningOutline,
      title: '数据源连接失败',
      alertType: 'warning',
      showDataSources: true,
    },
    'all_sources_failed': {
      icon: CloseCircleOutline,
      title: '学术数据源暂时不可用',
      alertType: 'error',
      showDataSources: true,
    },
    'empty_result': {
      icon: SearchOutline,
      title: '未找到相关文献',
      alertType: 'info',
      showDataSources: true,
    },
    'server_error': {
      icon: CloseCircleOutline,
      title: `服务暂时不可用${props.statusCode ? ` (${props.statusCode})` : ''}`,
      alertType: 'error',
      showDataSources: false,
    },
    'unknown': {
      icon: WarningOutline,
      title: '发生错误',
      alertType: 'warning',
      showDataSources: false,
    },
  }
  return configs[props.errorType] || configs['unknown']
})

/** 默认错误消息 */
const defaultMessage = computed(() => {
  const messages: Record<ErrorType, string> = {
    'network_timeout': '请检查网络连接后重试',
    'source_failed': '系统正在自动尝试其他学术数据源...',
    'all_sources_failed': '可能原因：网络问题或服务器维护',
    'empty_result': `未找到「${props.searchQuery}」相关文献`,
    'server_error': '请稍后重试或联系管理员',
    'unknown': '请稍后重试',
  }
  return props.message || messages[props.errorType] || '发生未知错误'
})

/** 空结果建议 */
const emptyResultSuggestions = [
  '尝试更宽泛的关键词',
  '检查拼写是否正确',
  '尝试用英文关键词搜索国际文献',
]

/** 是否显示数据源状态 */
const showDataSources = computed(() => {
  return errorConfig.value.showDataSources && props.dataSources.length > 0
})

/** 是否有正在尝试的数据源 */
const hasTryingSource = computed(() => {
  return props.dataSources.some(s => s.status === 'trying')
})

/** 数据源状态图标 */
function getSourceStatusIcon(status: DataSourceStatus['status']) {
  switch (status) {
    case 'success': return '✓'
    case 'failed': return '✗'
    case 'trying': return '⏳'
    default: return '○'
  }
}

/** 数据源状态样式类 */
function getSourceStatusClass(status: DataSourceStatus['status']) {
  return `source-${status}`
}

// ==================== 方法 ====================

function handleRetry() {
  emit('retry')
}

function handleSkip() {
  emit('skip')
}

function handleUseLocalOnly() {
  emit('use-local-only')
}

function handleModifyQuery() {
  emit('modify-query')
}

function handleClose() {
  emit('close')
}
</script>

<template>
  <div class="error-state">
    <n-alert :type="errorConfig.alertType" :bordered="false">
      <template #icon>
        <n-icon :component="errorConfig.icon" :size="20" />
      </template>

      <template #header>
        {{ errorConfig.title }}
      </template>

      <div class="error-content">
        <!-- 错误描述 -->
        <p class="error-message">{{ defaultMessage }}</p>

        <!-- 数据源状态列表 -->
        <div v-if="showDataSources" class="data-sources">
          <div class="sources-title">已尝试数据源：</div>
          <div class="sources-list">
            <div
              v-for="source in dataSources"
              :key="source.name"
              class="source-item"
              :class="getSourceStatusClass(source.status)"
            >
              <span class="source-status">{{ getSourceStatusIcon(source.status) }}</span>
              <span class="source-name">{{ source.displayName }}</span>
              <span v-if="source.status === 'trying'" class="source-hint">尝试中...</span>
              <span v-else-if="source.status === 'failed'" class="source-hint">{{ source.error || '失败' }}</span>
            </div>
          </div>
        </div>

        <!-- 空结果建议 -->
        <div v-if="errorType === 'empty_result'" class="suggestions">
          <div class="suggestions-title">💡 建议：</div>
          <ul class="suggestions-list">
            <li v-for="(suggestion, index) in emptyResultSuggestions" :key="index">
              {{ suggestion }}
            </li>
          </ul>
        </div>
      </div>
    </n-alert>

    <!-- 操作按钮 -->
    <n-space class="error-actions" justify="center" :wrap="true">
      <!-- 网络超时：重试 + 取消 -->
      <template v-if="errorType === 'network_timeout'">
        <n-button
          type="primary"
          :loading="retrying"
          @click="handleRetry"
        >
          <template #icon>
            <n-icon :component="RefreshOutline" />
          </template>
          重试
        </n-button>
        <n-button @click="handleClose">取消</n-button>
      </template>

      <!-- 单数据源失败：跳过 + 重试 -->
      <template v-else-if="errorType === 'source_failed'">
        <n-button @click="handleSkip" :disabled="hasTryingSource">
          跳过
        </n-button>
        <n-button
          type="primary"
          :loading="retrying"
          @click="handleRetry"
        >
          手动重试
        </n-button>
      </template>

      <!-- 所有数据源失败：全部重试 + 仅查询本地 -->
      <template v-else-if="errorType === 'all_sources_failed'">
        <n-button
          type="primary"
          :loading="retrying"
          @click="handleRetry"
        >
          <template #icon>
            <n-icon :component="RefreshOutline" />
          </template>
          全部重试
        </n-button>
        <n-button @click="handleUseLocalOnly">
          仅查询本地知识库
        </n-button>
      </template>

      <!-- 空结果：重新搜索 + 修改问题 -->
      <template v-else-if="errorType === 'empty_result'">
        <n-button type="primary" @click="handleModifyQuery">
          <template #icon>
            <n-icon :component="ArrowForwardOutline" />
          </template>
          修改搜索词
        </n-button>
        <n-button @click="handleRetry" :loading="retrying">
          重新搜索
        </n-button>
      </template>

      <!-- 服务器错误：重试 + 返回 -->
      <template v-else-if="errorType === 'server_error'">
        <n-button
          type="primary"
          :loading="retrying"
          @click="handleRetry"
        >
          <template #icon>
            <n-icon :component="RefreshOutline" />
          </template>
          重试
        </n-button>
        <n-button @click="handleClose">返回</n-button>
      </template>

      <!-- 未知错误：重试 -->
      <template v-else>
        <n-button
          type="primary"
          :loading="retrying"
          @click="handleRetry"
        >
          重试
        </n-button>
      </template>
    </n-space>
  </div>
</template>

<style scoped>
.error-state {
  padding: 16px;
  background: #fff;
  border-radius: var(--le-radius-lg, 16px);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.08);
}

.error-content {
  margin-top: 8px;
}

.error-message {
  margin: 0 0 12px;
  font-size: 14px;
  color: #4b5563;
  line-height: 1.5;
}

/* 数据源列表 */
.data-sources {
  margin-bottom: 12px;
}

.sources-title {
  font-size: 13px;
  color: #6b7280;
  margin-bottom: 8px;
}

.sources-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px;
  background: #f9fafb;
  border-radius: var(--le-radius-md, 8px);
}

.source-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}

.source-status {
  width: 18px;
  text-align: center;
  flex-shrink: 0;
}

.source-name {
  flex: 1;
  color: #374151;
}

.source-hint {
  font-size: 12px;
  color: #9ca3af;
}

/* 状态样式 */
.source-success .source-status {
  color: #10b981;
}

.source-failed .source-status {
  color: #ef4444;
}

.source-trying .source-status {
  color: #f59e0b;
  animation: pulse 1s infinite;
}

.source-pending .source-status {
  color: #9ca3af;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

/* 建议列表 */
.suggestions {
  padding: 12px;
  background: #fffbeb;
  border-radius: var(--le-radius-md, 8px);
  border-left: 3px solid #f59e0b;
}

.suggestions-title {
  font-size: 13px;
  font-weight: 500;
  color: #92400e;
  margin-bottom: 8px;
}

.suggestions-list {
  margin: 0;
  padding-left: 20px;
  font-size: 13px;
  color: #78350f;
  line-height: 1.6;
}

.suggestions-list li {
  margin-bottom: 4px;
}

.suggestions-list li:last-child {
  margin-bottom: 0;
}

/* 操作按钮 */
.error-actions {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid #e5e7eb;
}

/* 移动端适配 */
@media (max-width: 480px) {
  .error-state {
    padding: 12px;
  }

  .error-message {
    font-size: 13px;
  }

  .sources-list {
    padding: 8px;
  }

  .source-item {
    font-size: 12px;
  }

  .suggestions {
    padding: 10px;
  }

  .suggestions-list {
    font-size: 12px;
  }

  .error-actions {
    margin-top: 12px;
    padding-top: 12px;
  }

  .error-actions :deep(.n-button) {
    font-size: 13px;
    padding: 0 12px;
  }
}
</style>
