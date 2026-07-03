<script setup lang="ts">
/**
 * 综述生成页面 - 响应式版本
 * v2.1: 移动端适配
 */
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import {
  NCard,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NSelect,
  NButton,
  NSpace,
  NProgress,
  NAlert,
  NTabs,
  NTabPane,
  NSpin,
  NEmpty,
  NTag,
  NModal,
  NScrollbar,
  NIcon,
  useMessage,
} from 'naive-ui'
import { DownloadOutline, RefreshOutline } from '@vicons/ionicons5'
import { marked } from 'marked'
import {
  createReview,
  getReviewStatus,
  getReviewHistory,
  deleteReview,
  type ReviewTask,
  type ReviewCreateParams,
} from '../api/review'
import { getCategories } from '../api/search'
import { useResponsive } from '../composables/useResponsive'

const route = useRoute()
const message = useMessage()
const { isMobile } = useResponsive()

// ==================== 常量配置 ====================
const STORAGE_KEY = 'review_task_state'
const SSE_MAX_RETRIES = 3
const POLLING_INTERVAL = 2000
const RENDER_THROTTLE_MS = 300

// ==================== 表单数据 ====================
const formData = ref<ReviewCreateParams>({
  topic: '',
  category: undefined,
  sub_category: undefined,
  methodology: undefined,
  year_from: undefined,
  year_to: undefined,
  word_count: 3000,
  requirements: '',
})

// 分类选项
const categoryOptions = ref<Array<{ label: string; value: string }>>([])
const subCategoryOptions = ref<Array<{ label: string; value: string }>>([])
const methodologyOptions = ref<Array<{ label: string; value: string }>>([])

// 字数选项
const wordCountOptions = [
  { label: '2000字（简要）', value: 2000 },
  { label: '3000字（标准）', value: 3000 },
  { label: '5000字（详细）', value: 5000 },
]

// ==================== 状态管理 ====================
const loading = ref(false)
const currentTask = ref<ReviewTask | null>(null)
const historyList = ref<ReviewTask[]>([])
const historyLoading = ref(false)

// SSE相关
const sseRetryCount = ref(0)
const sseConnected = ref(false)
const usePolling = ref(false)  // 是否降级到轮询
let eventSource: EventSource | null = null
let pollingTimer: number | null = null

// 节流渲染相关
let renderBuffer: Partial<ReviewTask> = {}
let renderTimer: number | null = null

// 结果展示
const showResultModal = ref(false)
const selectedResult = ref<ReviewTask | null>(null)

// 流式预览滚动容器 ref
const streamScrollRef = ref<any>(null)

// ==================== 计算属性 ====================
const progressPercent = computed(() => {
  if (currentTask.value?.progress_percent !== undefined) {
    return currentTask.value.progress_percent
  }
  return currentTask.value?.progress?.progress || 0
})

const progressStep = computed(() => {
  return currentTask.value?.progress_step || currentTask.value?.progress?.step || 'pending'
})

const progressMessage = computed(() => {
  // 完成状态显示"生成完成"，而不是默认的"准备中..."
  if (currentTask.value?.status === 'completed') {
    return currentTask.value?.progress_message || '生成完成'
  }
  return currentTask.value?.progress_message || currentTask.value?.progress?.message || '准备中...'
})

const progressStatus = computed(() => {
  const step = progressStep.value
  if (step === 'error') return 'error'
  if (step === 'complete') return 'success'
  return 'default'
})

const connectionStatus = computed(() => {
  if (usePolling.value) return '轮询模式'
  if (sseConnected.value) return 'SSE连接中'
  return '连接中...'
})

// ==================== localStorage 持久化 ====================
function saveTaskState() {
  if (currentTask.value && ['pending', 'processing'].includes(currentTask.value.status)) {
    const state = {
      taskId: currentTask.value.task_id,
      topic: currentTask.value.topic,
      status: currentTask.value.status,
      timestamp: Date.now()
    }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state))
  }
}

function loadTaskState(): { taskId: string; topic: string } | null {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (!saved) return null

    const state = JSON.parse(saved)
    // 检查是否过期（超过1小时认为过期）
    if (Date.now() - state.timestamp > 3600000) {
      localStorage.removeItem(STORAGE_KEY)
      return null
    }
    return state
  } catch {
    localStorage.removeItem(STORAGE_KEY)
    return null
  }
}

function clearTaskState() {
  localStorage.removeItem(STORAGE_KEY)
}

// ==================== 节流渲染 ====================
function scheduleRender(updates: Partial<ReviewTask>) {
  // 合并更新到缓冲区
  renderBuffer = { ...renderBuffer, ...updates }

  // 如果没有定时器，启动一个
  if (!renderTimer) {
    renderTimer = window.setTimeout(() => {
      applyRender()
    }, RENDER_THROTTLE_MS)
  }
}

function applyRender() {
  if (currentTask.value && Object.keys(renderBuffer).length > 0) {
    // 应用缓冲区的更新
    currentTask.value = { ...currentTask.value, ...renderBuffer }
  }
  renderBuffer = {}
  renderTimer = null
}

function flushRender() {
  if (renderTimer) {
    clearTimeout(renderTimer)
    renderTimer = null
  }
  applyRender()
}

// 滚动到流式预览底部
function scrollToBottom() {
  nextTick(() => {
    if (streamScrollRef.value) {
      const scrollContainer = streamScrollRef.value.$el?.querySelector('.n-scrollbar-container')
      if (scrollContainer) {
        scrollContainer.scrollTop = scrollContainer.scrollHeight
      }
    }
  })
}

// ==================== SSE 连接管理 ====================
function startSSE(taskId: string) {
  // 清理旧连接
  stopSSE()

  const baseURL = import.meta.env.VITE_API_BASE_URL || ''
  const token = localStorage.getItem('token')

  // EventSource 不支持自定义 Header，使用 query 参数传递 token
  // 后端需要支持从 query 参数读取 token（当前已支持）
  const url = `${baseURL}/api/user/review/${taskId}/stream`

  // 使用 fetch 模拟 SSE（因为需要 Authorization header）
  startFetchSSE(url, token || '')
}

async function startFetchSSE(url: string, token: string) {
  try {
    const response = await fetch(url, {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${token}`,
        'Accept': 'text/event-stream',
      },
    })

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }

    if (!response.body) {
      throw new Error('Response body is null')
    }

    sseConnected.value = true
    // 只有在连接成功后才重置重试计数
    sseRetryCount.value = 0

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let receivedComplete = false  // 标记是否收到完成事件

    while (true) {
      const { done, value } = await reader.read()

      if (done) {
        // 如果已经收到完成事件，正常退出
        if (receivedComplete) {
          return  // 正常完成，不触发错误处理
        }
        break
      }

      buffer += decoder.decode(value, { stream: true })

      // 处理缓冲区中的事件
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''  // 保留最后一个不完整的行

      for (const line of lines) {
        const result = processSSELine(line)
        if (result === 'complete' || result === 'error') {
          receivedComplete = true
        }
      }
    }

    // 如果循环正常结束但没收到完成事件，可能是连接被关闭
    if (!receivedComplete) {
      throw new Error('Connection closed without complete event')
    }
  } catch (error) {
    console.error('SSE连接错误:', error)
    handleSSEError()
  }
}

function processSSELine(line: string): 'complete' | 'error' | undefined {
  // 忽略空行和心跳
  if (!line || line.startsWith(':')) {
    return undefined
  }

  // 解析事件类型
  if (line.startsWith('event:')) {
    // 暂存事件类型，等待 data 行
    return undefined
  }

  // 解析数据
  if (line.startsWith('data:')) {
    const dataStr = line.slice(5).trim()
    if (!dataStr) return undefined

    try {
      const data = JSON.parse(dataStr)
      return handleSSEData(data)
    } catch (e) {
      console.error('解析SSE数据失败:', e, line)
    }
  }
  return undefined
}

function handleSSEData(data: any): 'complete' | 'error' | undefined {
  // 根据数据类型处理

  // 流式内容更新（综述生成阶段的逐字输出）
  if (data.text !== undefined) {
    if (currentTask.value) {
      // 追加新内容到 partial_content
      const currentContent = currentTask.value.partial_content || ''
      scheduleRender({
        partial_content: currentContent + data.text
      })
      // 自动滚动到底部
      scrollToBottom()
    }
    return undefined
  }

  if (data.step !== undefined) {
    // 进度更新
    scheduleRender({
      progress_step: data.step,
      progress_percent: data.percent,
      progress_message: data.message,
      progress: {
        step: data.step,
        progress: data.percent,
        message: data.message,
      }
    })

    // 保存状态
    saveTaskState()
  }

  if (data.task_id !== undefined && data.word_count !== undefined) {
    // 完成事件
    flushRender()

    if (currentTask.value) {
      currentTask.value = {
        ...currentTask.value,
        status: 'completed',
        word_count: data.word_count,
        progress_step: 'complete',
        progress_percent: 100,
        progress_message: '生成完成',
      }
    }

    stopSSE()
    clearTaskState()
    loadHistory()
    message.success('综述生成完成！')

    // 自动刷新任务详情
    refreshCurrentTask()
    return 'complete'
  }

  if (data.message !== undefined && data.retryable !== undefined) {
    // 错误事件
    flushRender()

    if (currentTask.value) {
      currentTask.value = {
        ...currentTask.value,
        status: 'failed',
        progress_step: 'error',
        progress_percent: 0,
        progress_message: data.message,
        error_message: data.message,
      }
    }

    stopSSE()
    clearTaskState()
    loadHistory()

    if (data.retryable) {
      message.warning(`综述生成失败: ${data.message}，可以重试`)
    } else {
      message.error(`综述生成失败: ${data.message}`)
    }
    return 'error'
  }

  return undefined
}

function handleSSEError() {
  sseConnected.value = false
  sseRetryCount.value++

  if (sseRetryCount.value >= SSE_MAX_RETRIES) {
    // 达到重试上限，降级到轮询
    console.warn('SSE连接失败次数过多，降级到轮询模式')
    usePolling.value = true
    if (currentTask.value) {
      startPolling(currentTask.value.task_id)
    }
  } else {
    // 重试SSE连接
    console.log(`SSE连接重试 ${sseRetryCount.value}/${SSE_MAX_RETRIES}`)
    setTimeout(() => {
      if (currentTask.value && currentTask.value.status === 'processing') {
        startSSE(currentTask.value.task_id)
      }
    }, 2000)
  }
}

function stopSSE() {
  if (eventSource) {
    eventSource.close()
    eventSource = null
  }
  sseConnected.value = false
}

// ==================== 轮询降级 ====================
function startPolling(taskId: string) {
  stopPolling()

  pollingTimer = window.setInterval(async () => {
    try {
      const task = await getReviewStatus(taskId)

      // 使用节流渲染
      scheduleRender({
        status: task.status,
        progress_step: task.progress?.step,
        progress_percent: task.progress?.progress,
        progress_message: task.progress?.message,
        progress: task.progress,
        documents_used: task.documents_used,
        word_count: task.word_count,
      })

      // 如果完成或失败，停止轮询
      if (task.status === 'completed' || task.status === 'failed') {
        flushRender()
        currentTask.value = task
        stopPolling()
        clearTaskState()
        loadHistory()

        if (task.status === 'completed') {
          message.success('综述生成完成！')
        } else {
          message.error('综述生成失败')
        }
      }
    } catch (e) {
      console.error('轮询获取进度失败:', e)
    }
  }, POLLING_INTERVAL)
}

function stopPolling() {
  if (pollingTimer) {
    clearInterval(pollingTimer)
    pollingTimer = null
  }
}

// ==================== 刷新当前任务 ====================
async function refreshCurrentTask() {
  if (!currentTask.value) return

  try {
    const task = await getReviewStatus(currentTask.value.task_id)
    currentTask.value = task
  } catch (e) {
    console.error('刷新任务失败:', e)
  }
}

// ==================== 加载分类选项 ====================
async function loadCategories() {
  try {
    const res = await getCategories()
    categoryOptions.value = (res.categories || []).map((c: string) => ({ label: c, value: c }))
    subCategoryOptions.value = (res.sub_categories || []).map((c: string) => ({ label: c, value: c }))
    methodologyOptions.value = (res.methodologies || []).map((c: string) => ({ label: c, value: c }))
  } catch (e) {
    console.error('加载分类失败:', e)
  }
}

// ==================== 加载历史记录 ====================
async function loadHistory() {
  historyLoading.value = true
  try {
    const res = await getReviewHistory(1, 20)
    historyList.value = res.tasks || []
  } catch (e) {
    console.error('加载历史失败:', e)
  } finally {
    historyLoading.value = false
  }
}

// ==================== 提交生成任务 ====================
async function handleSubmit() {
  if (!formData.value.topic || formData.value.topic.length < 2) {
    message.warning('请输入研究主题（至少2个字符）')
    return
  }

  loading.value = true
  try {
    const res = await createReview(formData.value)
    message.success('任务已创建，正在生成中...')

    // 初始化当前任务
    currentTask.value = {
      task_id: res.task_id,
      topic: formData.value.topic,
      status: 'processing',
      documents_used: 0,
      word_count: 0,
      created_at: new Date().toISOString(),
      progress_step: 'pending',
      progress_percent: 0,
      progress_message: '准备开始...',
      progress: {
        step: 'pending',
        progress: 0,
        message: '准备开始...'
      }
    }

    // 保存状态到localStorage
    saveTaskState()

    // 重置SSE状态
    sseRetryCount.value = 0
    usePolling.value = false

    // 开始SSE连接
    startSSE(res.task_id)
  } catch (e: any) {
    if (e.response?.status === 429) {
      message.error(e.response.data?.detail || '您有太多任务正在处理中，请稍后再试')
    } else {
      message.error(e.message || '创建任务失败')
    }
  } finally {
    loading.value = false
  }
}

// ==================== 恢复进行中的任务 ====================
async function resumeTask() {
  const savedState = loadTaskState()
  if (!savedState) return

  try {
    const task = await getReviewStatus(savedState.taskId)

    // 如果任务仍在进行中，恢复监听
    if (task.status === 'pending' || task.status === 'processing') {
      currentTask.value = task
      message.info('已恢复进行中的任务')

      // 尝试SSE，失败后降级到轮询
      startSSE(task.task_id)
    } else {
      // 任务已完成或失败，清理状态
      clearTaskState()
      loadHistory()
    }
  } catch (e) {
    console.error('恢复任务失败:', e)
    clearTaskState()
  }
}

// ==================== 查看结果 ====================
async function viewResult(task: ReviewTask) {
  try {
    // 获取完整的综述内容（历史列表只返回500字摘要）
    const fullTask = await getReviewStatus(task.task_id)
    selectedResult.value = fullTask
    showResultModal.value = true
  } catch (error) {
    console.error('获取综述内容失败:', error)
    message.error('获取综述内容失败')
  }
}

// ==================== 渲染Markdown ====================
function renderMarkdown(content: string) {
  return marked(content || '')
}

// ==================== 删除历史 ====================
async function handleDelete(task: ReviewTask) {
  try {
    await deleteReview(task.task_id)
    message.success('删除成功')
    loadHistory()
  } catch (e: any) {
    message.error(e.message || '删除失败')
  }
}

// ==================== 重新开始 ====================
function resetForm() {
  currentTask.value = null
  stopSSE()
  stopPolling()
  flushRender()
  clearTaskState()
  sseRetryCount.value = 0
  usePolling.value = false
}

// ==================== 格式化时间 ====================
function formatTime(time: string) {
  if (!time) return '-'
  return new Date(time).toLocaleString('zh-CN')
}

// ==================== 状态颜色 ====================
function getStatusType(status: string): 'default' | 'success' | 'warning' | 'error' | 'info' {
  switch (status) {
    case 'completed': return 'success'
    case 'processing': return 'info'
    case 'pending': return 'warning'
    case 'failed': return 'error'
    default: return 'default'
  }
}

// ==================== 状态文字 ====================
function getStatusText(status: string) {
  switch (status) {
    case 'completed': return '已完成'
    case 'processing': return '生成中'
    case 'pending': return '等待中'
    case 'failed': return '失败'
    default: return status
  }
}

// ==================== 生命周期 ====================
onMounted(async () => {
  loadCategories()
  loadHistory()

  // 检查路由参数，如果有预设值则填充表单
  const { topic, category, methodology, year_from, year_to } = route.query
  if (topic) {
    formData.value.topic = topic as string
  }
  if (category) {
    formData.value.category = category as string
  }
  if (methodology) {
    formData.value.methodology = methodology as string
  }
  if (year_from) {
    formData.value.year_from = parseInt(year_from as string, 10) || undefined
  }
  if (year_to) {
    formData.value.year_to = parseInt(year_to as string, 10) || undefined
  }

  // 恢复进行中的任务
  await resumeTask()
})

onUnmounted(() => {
  stopSSE()
  stopPolling()
  flushRender()
})

// 监听任务状态变化，保存到localStorage
watch(currentTask, () => {
  saveTaskState()
}, { deep: true })

// ==================== 导出功能 ====================
async function exportReview(taskId: string, format: 'markdown' | 'txt' = 'markdown') {
  try {
    const baseURL = import.meta.env.VITE_API_BASE_URL || ''
    const token = localStorage.getItem('token')
    const response = await fetch(
      `${baseURL}/api/user/export/review/${taskId}?format=${format}`,
      {
        method: 'GET',
        headers: {
          'Authorization': `Bearer ${token}`
        }
      }
    )

    if (!response.ok) {
      throw new Error(`导出失败: HTTP ${response.status}`)
    }

    // 从响应头获取文件名
    const contentDisposition = response.headers.get('Content-Disposition')
    let filename = `综述_${taskId}.${format === 'markdown' ? 'md' : 'txt'}`
    if (contentDisposition) {
      const match = contentDisposition.match(/filename\*=UTF-8''(.+)/)
      if (match && match[1]) {
        filename = decodeURIComponent(match[1])
      }
    }

    // 下载文件
    const blob = await response.blob()
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    window.URL.revokeObjectURL(url)
    document.body.removeChild(a)

    message.success('导出成功')
  } catch (error: any) {
    console.error('导出失败:', error)
    message.error(error.message || '导出失败')
  }
}
</script>

<template>
  <div class="review-container" :class="{ 'review-container-mobile': isMobile }">
    <n-tabs type="line" animated>
      <!-- 生成综述 Tab -->
      <n-tab-pane name="generate" tab="生成综述">
        <div class="generate-section">
          <!-- 配置表单 -->
          <n-card v-if="!currentTask" title="综述配置" class="config-card">
            <n-form
              :model="formData"
              :label-placement="isMobile ? 'top' : 'left'"
              :label-width="isMobile ? undefined : 100"
            >
              <n-form-item label="研究主题" required>
                <n-input
                  v-model:value="formData.topic"
                  placeholder="请输入研究主题，如：老年教育数字化"
                  clearable
                />
              </n-form-item>

              <n-form-item label="研究领域">
                <n-select
                  v-model:value="formData.category"
                  :options="categoryOptions"
                  placeholder="可选：筛选特定领域"
                  clearable
                />
              </n-form-item>

              <n-form-item label="子领域">
                <n-select
                  v-model:value="formData.sub_category"
                  :options="subCategoryOptions"
                  placeholder="可选：筛选子领域"
                  clearable
                />
              </n-form-item>

              <n-form-item label="研究方法">
                <n-select
                  v-model:value="formData.methodology"
                  :options="methodologyOptions"
                  placeholder="可选：筛选研究方法"
                  clearable
                />
              </n-form-item>

              <n-form-item label="年份范围">
                <div :class="['year-range-row', { 'year-range-mobile': isMobile }]">
                  <n-input-number
                    v-model:value="formData.year_from"
                    placeholder="起始年份"
                    :min="1990"
                    :max="2030"
                    clearable
                  />
                  <span class="year-separator">至</span>
                  <n-input-number
                    v-model:value="formData.year_to"
                    placeholder="结束年份"
                    :min="1990"
                    :max="2030"
                    clearable
                  />
                </div>
              </n-form-item>

              <n-form-item label="字数要求">
                <n-select
                  v-model:value="formData.word_count"
                  :options="wordCountOptions"
                />
              </n-form-item>

              <n-form-item label="其他要求">
                <n-input
                  v-model:value="formData.requirements"
                  type="textarea"
                  placeholder="可选：其他特殊要求，如关注点、写作风格等"
                  :rows="3"
                />
              </n-form-item>

              <n-form-item>
                <n-button
                  type="primary"
                  :size="isMobile ? 'large' : 'large'"
                  :block="isMobile"
                  :loading="loading"
                  @click="handleSubmit"
                >
                  开始生成综述
                </n-button>
              </n-form-item>
            </n-form>
          </n-card>

          <!-- 生成进度 -->
          <n-card v-else title="生成进度" class="progress-card">
            <template #header-extra>
              <n-tag :type="usePolling ? 'warning' : 'success'" size="small">
                {{ connectionStatus }}
              </n-tag>
            </template>

            <div class="progress-content">
              <div class="topic-info">
                <strong>研究主题：</strong>{{ currentTask.topic }}
              </div>

              <n-progress
                type="line"
                :percentage="progressPercent"
                :status="progressStatus"
                :show-indicator="true"
                class="progress-bar"
              />

              <div class="progress-message">
                {{ progressMessage }}
              </div>

              <!-- 阶段指示器 -->
              <div :class="['steps-indicator', { 'steps-indicator-mobile': isMobile }]">
                <div
                  v-for="(step, idx) in ['search', 'analyze', 'extract', 'generate']"
                  :key="step"
                  :class="[
                    'step-item',
                    {
                      'active': progressStep === step,
                      'completed': currentTask.status === 'completed' || ['search', 'analyze', 'extract', 'generate'].indexOf(progressStep) > idx
                    }
                  ]"
                >
                  <div class="step-dot">{{ idx + 1 }}</div>
                  <div class="step-label">
                    {{ step === 'search' ? '检索' :
                       step === 'analyze' ? '分析' :
                       step === 'extract' ? '提炼' : '生成' }}
                  </div>
                </div>
              </div>

              <!-- 流式内容预览（生成综述阶段实时显示） -->
              <template v-if="progressStep === 'generate' && currentTask.partial_content && currentTask.status !== 'completed'">
                <n-card class="stream-preview" size="small">
                  <template #header>
                    <span style="font-size: 14px; color: #666;">实时预览 ({{ currentTask.partial_content.length }} 字)</span>
                  </template>
                  <n-scrollbar ref="streamScrollRef" :style="{ maxHeight: isMobile ? '200px' : '300px' }">
                    <div
                      class="stream-content markdown-body"
                      v-html="renderMarkdown(currentTask.partial_content)"
                    />
                  </n-scrollbar>
                </n-card>
              </template>

              <!-- 完成后显示结果 -->
              <template v-if="currentTask.status === 'completed'">
                <n-alert type="success" title="综述生成完成" class="result-alert">
                  共引用 {{ currentTask.documents_used }} 篇文献，
                  综述字数 {{ currentTask.word_count }} 字
                </n-alert>

                <n-space :vertical="isMobile" :size="isMobile ? 12 : 'medium'" class="action-buttons">
                  <n-button type="primary" :block="isMobile" @click="viewResult(currentTask)">
                    查看综述
                  </n-button>
                  <n-button :block="isMobile" @click="resetForm">
                    生成新的综述
                  </n-button>
                </n-space>
              </template>

              <!-- 失败后显示 -->
              <template v-if="currentTask.status === 'failed'">
                <n-alert type="error" title="生成失败" class="result-alert">
                  {{ currentTask.error_message || progressMessage || '生成过程中出现错误，请重试' }}
                </n-alert>

                <n-space :vertical="isMobile" :size="isMobile ? 12 : 'medium'">
                  <n-button type="primary" :block="isMobile" @click="resetForm">
                    重新开始
                  </n-button>
                  <n-button :block="isMobile" @click="refreshCurrentTask">
                    <template #icon>
                      <n-icon :component="RefreshOutline" />
                    </template>
                    刷新状态
                  </n-button>
                </n-space>
              </template>

              <!-- 进行中时显示取消按钮 -->
              <template v-if="currentTask.status === 'processing' || currentTask.status === 'pending'">
                <n-space :vertical="isMobile" :size="isMobile ? 12 : 'medium'" class="action-buttons" style="margin-top: 24px;">
                  <n-button :block="isMobile" @click="resetForm" :disabled="loading">
                    取消任务
                  </n-button>
                  <n-button :block="isMobile" @click="refreshCurrentTask" :disabled="loading">
                    <template #icon>
                      <n-icon :component="RefreshOutline" />
                    </template>
                    刷新状态
                  </n-button>
                </n-space>
              </template>
            </div>
          </n-card>
        </div>
      </n-tab-pane>

      <!-- 历史记录 Tab -->
      <n-tab-pane name="history" tab="生成历史">
        <div class="history-section">
          <n-spin :show="historyLoading">
            <template v-if="historyList.length > 0">
              <div class="history-list">
                <n-card
                  v-for="task in historyList"
                  :key="task.task_id"
                  class="history-item"
                  :class="{ 'history-item-mobile': isMobile }"
                  hoverable
                >
                  <div class="history-header">
                    <span class="history-topic">{{ task.topic }}</span>
                    <n-tag :type="getStatusType(task.status)" size="small">
                      {{ getStatusText(task.status) }}
                    </n-tag>
                  </div>

                  <div class="history-meta">
                    <span>{{ formatTime(task.created_at) }}</span>
                    <span v-if="task.documents_used">
                      · {{ task.documents_used }}篇
                    </span>
                    <span v-if="task.word_count">
                      · {{ task.word_count }}字
                    </span>
                  </div>

                  <div class="history-actions">
                    <n-button
                      v-if="task.status === 'completed'"
                      type="primary"
                      size="small"
                      @click="viewResult(task)"
                    >
                      查看
                    </n-button>
                    <n-button
                      v-if="task.status === 'completed'"
                      size="small"
                      @click.stop="exportReview(task.task_id, 'markdown')"
                    >
                      <template #icon>
                        <n-icon :component="DownloadOutline" />
                      </template>
                      导出
                    </n-button>
                    <n-button size="small" @click="handleDelete(task)">
                      删除
                    </n-button>
                  </div>
                </n-card>
              </div>
            </template>

            <n-empty v-else description="暂无生成历史" />
          </n-spin>
        </div>
      </n-tab-pane>
    </n-tabs>

    <!-- 结果详情弹窗（响应式） -->
    <n-modal
      v-model:show="showResultModal"
      preset="card"
      title="综述内容"
      :style="{
        width: isMobile ? '95vw' : '80%',
        maxWidth: isMobile ? '95vw' : '1000px',
        maxHeight: '90vh'
      }"
      :mask-closable="true"
    >
      <template v-if="selectedResult">
        <div class="result-header">
          <div class="result-header-left">
            <h3>{{ selectedResult.topic }}</h3>
            <div class="result-meta">
              <span>引用文献：{{ selectedResult.documents_used }}篇</span>
              <span>字数：{{ selectedResult.word_count }}</span>
              <span>生成时间：{{ formatTime(selectedResult.completed_at || selectedResult.created_at) }}</span>
            </div>
          </div>
          <div class="result-header-right">
            <n-space>
              <n-button size="small" @click="exportReview(selectedResult.task_id, 'markdown')">
                <template #icon>
                  <n-icon :component="DownloadOutline" />
                </template>
                导出 MD
              </n-button>
              <n-button size="small" @click="exportReview(selectedResult.task_id, 'txt')">
                <template #icon>
                  <n-icon :component="DownloadOutline" />
                </template>
                导出 TXT
              </n-button>
            </n-space>
          </div>
        </div>

        <n-scrollbar style="max-height: 60vh;">
          <div
            class="review-content markdown-body"
            v-html="renderMarkdown(selectedResult.review_content || '')"
          />
        </n-scrollbar>

        <!-- 注：引用文献已在综述正文（review_content）中由 LLM 生成，无需重复显示 -->
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.review-container {
  height: 100%;
  padding: 24px;
  overflow: auto;
}

.generate-section,
.history-section {
  max-width: 800px;
  margin: 0 auto;
}

.config-card,
.progress-card {
  margin-bottom: 24px;
}

.progress-content {
  text-align: center;
}

.topic-info {
  margin-bottom: 24px;
  font-size: 16px;
}

.progress-bar {
  margin-bottom: 16px;
}

.progress-message {
  margin-bottom: 24px;
  color: #666;
  min-height: 24px;
}

.steps-indicator {
  display: flex;
  justify-content: center;
  gap: 40px;
  margin: 32px 0;
}

.step-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  opacity: 0.5;
  transition: opacity 0.3s ease;
}

.step-item.active,
.step-item.completed {
  opacity: 1;
}

.step-dot {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: #e0e0e0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: bold;
  margin-bottom: 8px;
  transition: all 0.3s ease;
}

.step-item.active .step-dot {
  background: #2080f0;
  color: white;
  transform: scale(1.1);
}

.step-item.completed .step-dot {
  background: #18a058;
  color: white;
}

.step-label {
  font-size: 12px;
  color: #666;
}

.result-alert {
  margin: 24px 0;
}

.action-buttons {
  margin-top: 24px;
}

/* 流式内容预览样式 */
.stream-preview {
  margin: 24px 0;
  text-align: left;
}

.stream-content {
  line-height: 1.8;
  font-size: 14px;
  padding: 8px;
}

.stream-content :deep(h1),
.stream-content :deep(h2),
.stream-content :deep(h3) {
  margin: 12px 0 8px;
}

.stream-content :deep(p) {
  margin-bottom: 8px;
}

/* 历史记录样式 */
.history-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.history-item {
  cursor: pointer;
}

.history-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.history-topic {
  font-weight: 500;
  font-size: 16px;
}

.history-meta {
  font-size: 12px;
  color: #999;
  margin-bottom: 12px;
}

.history-meta span {
  margin-right: 8px;
}

.history-actions {
  display: flex;
  gap: 8px;
}

/* 结果详情样式 */
.result-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 24px;
  padding-bottom: 16px;
  border-bottom: 1px solid #eee;
}

.result-header-left h3 {
  margin: 0 0 8px 0;
}

.result-meta {
  font-size: 12px;
  color: #999;
}

.result-meta span {
  margin-right: 16px;
}

.review-content {
  line-height: 1.8;
  padding: 16px;
}

.review-content :deep(h1) {
  font-size: 24px;
  margin-bottom: 16px;
}

.review-content :deep(h2) {
  font-size: 20px;
  margin: 24px 0 16px;
  padding-bottom: 8px;
  border-bottom: 1px solid #eee;
}

.review-content :deep(h3) {
  font-size: 16px;
  margin: 16px 0 12px;
}

.review-content :deep(p) {
  margin-bottom: 12px;
}

/* ===== 移动端样式 ===== */
.review-container-mobile {
  padding: 12px;
  padding-bottom: calc(56px + env(safe-area-inset-bottom, 0px) + 12px);
}

.review-container-mobile .generate-section,
.review-container-mobile .history-section {
  max-width: 100%;
}

/* 年份范围行 */
.year-range-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.year-range-row :deep(.n-input-number) {
  flex: 1;
}

.year-separator {
  color: #666;
  flex-shrink: 0;
}

.year-range-mobile {
  gap: 8px;
}

/* 移动端步骤指示器 */
.steps-indicator-mobile {
  gap: 16px;
}

.steps-indicator-mobile .step-dot {
  width: 28px;
  height: 28px;
  font-size: 12px;
}

.steps-indicator-mobile .step-label {
  font-size: 11px;
}

/* 移动端历史项 */
.history-item-mobile .history-topic {
  font-size: 14px;
}

.history-item-mobile .history-actions {
  flex-wrap: wrap;
}
</style>
