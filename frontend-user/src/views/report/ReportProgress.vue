<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import {
  createDeepReviewStream,
  getDeepReviewStatus,
  cancelDeepReview,
  confirmOutline,
  type DeepReviewTask,
} from '../../api/deep-review'
import OutlineEditor from '../../components/report/OutlineEditor.vue'

const route = useRoute()
const router = useRouter()
const message = useMessage()

const taskId = route.params.taskId as string
const task = ref<DeepReviewTask | null>(null)
const progress = ref(0)
const currentStage = ref('pending')
const progressMessage = ref('准备中...')
const error = ref('')
const isComplete = ref(false)
const isCancelled = ref(false)
const isWaiting = ref(false)

// ── 大纲预览 ──────────────────────────────────────────
const showOutlineEditor = ref(false)
const outlineData = ref<any>(null)
const outlineTimeout = ref(180)

// ── 章节追踪 ──────────────────────────────────────────
interface ChapterItem {
  id: string
  title: string
  status: 'pending' | 'writing' | 'done'
  content: string
  wordCount: number
}
const allChapters = ref<ChapterItem[]>([])
const currentChapterIndex = ref(-1)
const totalChapters = ref(0)
const completedChapters = computed(() => allChapters.value.filter(c => c.status === 'done'))

// ── 活动日志 ──────────────────────────────────────────
interface LogEntry {
  time: string
  text: string
  type: 'info' | 'success' | 'pending' | 'error'
}
const logs = ref<LogEntry[]>([])
const startTime = Date.now()

function addLog(text: string, type: LogEntry['type'] = 'info') {
  const elapsed = Math.round((Date.now() - startTime) / 1000)
  const min = Math.floor(elapsed / 60)
  const sec = elapsed % 60
  const time = `${String(min).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
  if (logs.value.length > 0 && logs.value[logs.value.length - 1].type === 'pending') {
    logs.value[logs.value.length - 1].type = 'success'
  }
  logs.value.push({ time, text, type })
  nextTick(() => {
    const el = document.querySelector('.log-list')
    if (el) el.scrollTop = el.scrollHeight
  })
}

let streamHandle: { abort: () => void } | null = null

// ── 阶段定义 ──────────────────────────────────────────
const stages = [
  { key: 'outline', label: '构建提纲' },
  { key: 'layout', label: '文献分析' },
  { key: 'budget', label: '篇幅规划' },
  { key: 'chapter', label: '章节撰写' },
  { key: 'stitching', label: '引用校验' },
  { key: 'complete', label: '排版导出' },
]

function stageStatus(stageKey: string): 'done' | 'current' | 'wait' {
  const cur = currentStage.value
  // pending/running 但还没到任何阶段 → 第一个阶段闪烁
  if (cur === 'pending' || cur === 'running') {
    return stageKey === 'outline' ? 'current' : 'wait'
  }
  const currentIdx = stages.findIndex(s => s.key === cur)
  const idx = stages.findIndex(s => s.key === stageKey)
  if (currentIdx < 0) return 'wait' // 未知阶段
  if (idx < currentIdx) return 'done'
  if (idx === currentIdx) return 'current'
  return 'wait'
}

// ── 预计剩余 ──────────────────────────────────────────
const estimatedRemaining = computed(() => {
  if (progress.value >= 100) return '即将完成'
  if (progress.value <= 5) return '计算中...'
  const elapsed = (Date.now() - startTime) / 1000
  const totalEstimate = elapsed / (progress.value / 100)
  const remaining = Math.ceil((totalEstimate - elapsed) / 60)
  if (remaining <= 1) return '不到 1 分钟'
  return `约 ${remaining} 分钟`
})

// ── 当前进度描述 ──────────────────────────────────────
const currentProgressDesc = computed(() => {
  if (currentStage.value === 'chapter' && allChapters.value.length > 0) {
    const writing = allChapters.value.find(c => c.status === 'writing')
    if (writing) {
      const idx = allChapters.value.indexOf(writing) + 1
      return `正在撰写第 ${idx}/${totalChapters.value} 章：${writing.title}`
    }
  }
  return progressMessage.value
})

const chapterStatsLine = computed(() => {
  if (totalChapters.value === 0) return ''
  const done = completedChapters.value.length
  const totalWords = completedChapters.value.reduce((s, c) => s + c.wordCount, 0)
  const parts = [`已生成 ${done}/${totalChapters.value} 章节`]
  if (totalWords > 0) parts.push(`${totalWords.toLocaleString()} 字`)
  return parts.join(' · ')
})

// ── 等待心跳（超过 5 秒没有新事件则显示"处理中"）──
let lastEventTime = Date.now()
let heartbeatTimer: ReturnType<typeof setInterval> | null = null

function startHeartbeat() {
  heartbeatTimer = setInterval(() => {
    isWaiting.value = Date.now() - lastEventTime > 5000
  }, 1000)
}
function stopHeartbeat() {
  if (heartbeatTimer) clearInterval(heartbeatTimer)
}
function touchEvent() {
  lastEventTime = Date.now()
  isWaiting.value = false
}

// ── SSE 流 ────────────────────────────────────────────
let lastStage = ''

onMounted(async () => {
  addLog('连接服务器...', 'pending')
  try {
    task.value = await getDeepReviewStatus(taskId)
    if (task.value.status === 'completed') {
      router.replace(`/review/report/${taskId}`)
      return
    }
    if (task.value.status === 'failed') {
      error.value = task.value.error_message || '生成失败'
      addLog(`生成失败：${error.value}`, 'error')
      return
    }
    // 从初始状态恢复进度（刷新页面后重连场景）
    if (task.value.current_stage) {
      currentStage.value = task.value.current_stage
      lastStage = task.value.current_stage
    }
    if (task.value.progress_percent > 0) {
      progress.value = task.value.progress_percent
    }
    if (task.value.progress_message) {
      progressMessage.value = task.value.progress_message
    }
    addLog(`已连接，当前进度 ${progress.value}%`, 'info')
  } catch {
    error.value = '获取任务状态失败'
    addLog('连接失败', 'error')
    return
  }
  startStream()
})

onUnmounted(() => {
  streamHandle?.abort()
  stopHeartbeat()
})

function startStream() {
  const stream = createDeepReviewStream(taskId)
  streamHandle = stream
  startHeartbeat()

  stream.start({
    onProgress(data) {
      touchEvent()
      progress.value = data.progress || 0
      const stage = data.stage || currentStage.value
      const msg = data.message || ''

      if (stage !== lastStage) {
        lastStage = stage
        currentStage.value = stage
      }

      if (msg && msg !== progressMessage.value) {
        progressMessage.value = msg
        const isFinish = msg.includes('完成')
        addLog(msg, isFinish ? 'success' : 'pending')
      }

      // 从大纲事件初始化章节列表
      if (data.data?.chapters && allChapters.value.length === 0) {
        const titles = data.data.chapters as string[]
        totalChapters.value = titles.length
        allChapters.value = titles.map((t, i) => ({
          id: `ch${i + 1}`,
          title: t,
          status: 'pending',
          content: '',
          wordCount: 0,
        }))
        addLog(`大纲章节：${titles.join(' / ')}`, 'info')
      }

      // 章节撰写时标记当前章节
      if (stage === 'chapter' && data.data?.chapter_index !== undefined) {
        const idx = data.data.chapter_index as number
        currentChapterIndex.value = idx
        if (allChapters.value[idx]) {
          allChapters.value[idx].status = 'writing'
        }
      }
    },
    onChapterDone(data) {
      touchEvent()
      // 找到对应章节并更新
      const idx = allChapters.value.findIndex(c => c.status === 'writing')
      if (idx >= 0) {
        allChapters.value[idx].status = 'done'
        allChapters.value[idx].content = data.content || ''
        allChapters.value[idx].wordCount = data.word_count || 0
        if (data.chapter_title) allChapters.value[idx].title = data.chapter_title
      } else {
        // fallback: 追加
        allChapters.value.push({
          id: data.chapter_id,
          title: data.chapter_title,
          status: 'done',
          content: data.content || '',
          wordCount: data.word_count || 0,
        })
        totalChapters.value = Math.max(totalChapters.value, allChapters.value.length)
      }
      const doneCount = allChapters.value.filter(c => c.status === 'done').length
      addLog(`第${doneCount}章「${data.chapter_title}」完成（${(data.word_count || 0).toLocaleString()} 字）`, 'success')
    },
    onOutlinePreview(data) {
      touchEvent()
      outlineData.value = data
      outlineTimeout.value = data.timeout || 180
      showOutlineEditor.value = true
      addLog('大纲已生成，等待确认...', 'pending')
    },
    onComplete(data) {
      showOutlineEditor.value = false
      isComplete.value = true
      progress.value = 100
      currentStage.value = 'complete'
      addLog('综述生成完成！', 'success')
      message.success('综述生成完成')
      setTimeout(() => {
        router.push(`/review/report/${taskId}`)
      }, 2500)
    },
    onError(data) {
      error.value = data.message || '生成失败'
      addLog(`错误：${data.message || '未知错误'}`, 'error')
    },
  })
}

async function handleCancel() {
  try {
    await cancelDeepReview(taskId)
    isCancelled.value = true
    streamHandle?.abort()
    addLog('任务已取消', 'error')
    message.info('任务已取消')
  } catch {
    message.error('取消失败')
  }
}

async function handleOutlineConfirm(outline: any) {
  try {
    await confirmOutline(taskId, outline)
    showOutlineEditor.value = false
    addLog('大纲已确认，继续生成', 'success')
  } catch {
    message.error('确认大纲失败')
  }
}

async function handleOutlineSkip() {
  try {
    await confirmOutline(taskId)
    showOutlineEditor.value = false
    addLog('跳过大纲编辑，使用原始大纲继续', 'info')
  } catch {
    message.error('操作失败')
  }
}

function goBack() {
  router.push('/review/history')
}

function goToReport() {
  router.push(`/review/report/${taskId}`)
}
</script>

<template>
  <div class="report-progress">
    <n-card :bordered="false">
      <template #header>
        <div class="header-row">
          <span>深度综述生成中</span>
          <n-text depth="3">预计剩余：{{ estimatedRemaining }}</n-text>
        </div>
      </template>

      <!-- 错误状态 -->
      <n-result v-if="error" status="error" :title="error" description="生成过程中遇到问题">
        <template #footer>
          <n-space>
            <n-button @click="goBack">返回</n-button>
            <n-button type="primary" @click="startStream">重试</n-button>
          </n-space>
        </template>
      </n-result>

      <!-- 取消状态 -->
      <n-result v-else-if="isCancelled" status="warning" title="任务已取消">
        <template #footer>
          <n-button type="primary" @click="goBack">创建新综述</n-button>
        </template>
      </n-result>

      <!-- 进度主体 -->
      <template v-else>
        <!-- 阶段指示器（行内图标） -->
        <div class="stage-row">
          <div
            v-for="stage in stages"
            :key="stage.key"
            class="stage-item"
            :class="stageStatus(stage.key)"
          >
            <span class="stage-icon">
              <template v-if="stageStatus(stage.key) === 'done'">&#x2705;</template>
              <template v-else-if="stageStatus(stage.key) === 'current'">&#x25B6;</template>
              <template v-else>&#x25CB;</template>
            </span>
            <span class="stage-label">{{ stage.label }}</span>
          </div>
        </div>

        <!-- 大纲编辑覆盖层 -->
        <OutlineEditor
          v-if="showOutlineEditor && outlineData"
          :chapters="outlineData.outline?.chapters || outlineData.chapters || []"
          :total-chapters="outlineData.outline?.total_chapters || outlineData.total_chapters || 0"
          :timeout="outlineTimeout"
          :task-id="taskId"
          @confirm="handleOutlineConfirm"
          @skip="handleOutlineSkip"
        />

        <!-- 进度条 -->
        <n-progress
          type="line"
          :percentage="progress"
          :indicator-placement="'inside'"
          :processing="!isComplete"
          style="margin-bottom: 20px;"
        />

        <!-- 当前进度卡片 -->
        <div class="current-progress-card">
          <div class="progress-title">当前进度</div>
          <div class="progress-detail">
            {{ currentProgressDesc }}
            <span v-if="isWaiting" class="waiting-dot">（处理中）</span>
          </div>
          <div v-if="chapterStatsLine" class="progress-stats">{{ chapterStatsLine }}</div>
        </div>

        <!-- 全部章节列表 -->
        <div v-if="allChapters.length > 0" class="chapters-section">
          <div class="section-title">已完成章节预览</div>
          <div class="chapter-list">
            <div
              v-for="(ch, idx) in allChapters"
              :key="ch.id"
              class="chapter-row"
              :class="ch.status"
            >
              <div class="chapter-header">
                <span class="ch-icon">
                  <template v-if="ch.status === 'done'">&#x25BC;</template>
                  <template v-else>&#x25A1;</template>
                </span>
                <span class="ch-label">
                  第{{ idx + 1 }}章  {{ ch.title }}
                </span>
                <span v-if="ch.status === 'writing'" class="ch-badge writing">生成中...</span>
                <span v-else-if="ch.status === 'pending'" class="ch-badge pending">等待中</span>
                <span v-else-if="ch.wordCount" class="ch-badge done">{{ ch.wordCount.toLocaleString() }}字</span>
              </div>
              <!-- 已完成章节内容预览 -->
              <div v-if="ch.status === 'done' && ch.content" class="ch-preview">
                {{ ch.content.slice(0, 200) }}{{ ch.content.length > 200 ? ' ...' : '' }}
              </div>
            </div>
          </div>
        </div>

        <!-- 实时日志（折叠） -->
        <details class="log-section" open>
          <summary class="log-header">
            <span class="log-title">实时日志</span>
            <span v-if="!isComplete && !error" class="log-pulse" />
          </summary>
          <div class="log-list">
            <div
              v-for="(log, i) in logs"
              :key="i"
              class="log-entry"
              :class="log.type"
            >
              <span class="log-time">{{ log.time }}</span>
              <span class="log-icon">
                <template v-if="log.type === 'success'">&#10003;</template>
                <template v-else-if="log.type === 'pending'">&#9679;</template>
                <template v-else-if="log.type === 'error'">&#10007;</template>
                <template v-else>&#8226;</template>
              </span>
              <span class="log-text">{{ log.text }}</span>
            </div>
          </div>
        </details>

        <!-- 底部操作 -->
        <div class="footer-row">
          <n-space>
            <n-button v-if="!isComplete" @click="handleCancel" :disabled="isCancelled">
              取消
            </n-button>
            <n-button v-if="isComplete" type="primary" @click="goToReport">查看报告</n-button>
          </n-space>
          <n-text depth="3" style="font-size: 13px;">
            可离开页面，后台继续生成
          </n-text>
        </div>
      </template>
    </n-card>
  </div>
</template>

<style scoped>
.report-progress {
  max-width: 800px;
  margin: 0 auto;
  padding: 24px;
}
.header-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

/* ── 阶段指示器 ── */
.stage-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}
.stage-item {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 13.5px;
  padding: 2px 0;
}
.stage-item::after {
  content: '';
  display: inline-block;
  width: 20px;
  height: 1px;
  background: #ddd;
  margin-left: 6px;
}
.stage-item:last-child::after { display: none; }
.stage-icon { font-size: 14px; }
.stage-item.done .stage-label { color: #18a058; font-weight: 500; }
.stage-item.current .stage-label { color: #2080f0; font-weight: 600; }
.stage-item.current .stage-icon { color: #2080f0; }
.stage-item.wait .stage-label { color: #bbb; }
.stage-item.wait .stage-icon { color: #ccc; }

/* ── 当前进度卡片 ── */
.current-progress-card {
  border: 1px solid #e8e8e8;
  border-radius: 10px;
  padding: 16px 20px;
  margin-bottom: 20px;
  background: #fafbfc;
}
.progress-title {
  font-size: 12px;
  color: #999;
  margin-bottom: 6px;
  font-weight: 500;
}
.progress-detail {
  font-size: 15px;
  color: #333;
  font-weight: 500;
  margin-bottom: 4px;
}
.progress-stats {
  font-size: 13px;
  color: #18a058;
}
.waiting-dot {
  color: #2080f0;
  font-size: 13px;
  animation: pulse 1.5s ease-in-out infinite;
}

/* ── 章节列表 ── */
.chapters-section {
  margin-bottom: 20px;
}
.section-title {
  font-size: 13px;
  color: #888;
  font-weight: 500;
  margin-bottom: 10px;
  padding-left: 2px;
}
.chapter-list {
  border: 1px solid #f0f0f0;
  border-radius: 10px;
  overflow: hidden;
}
.chapter-row {
  padding: 12px 16px;
  border-bottom: 1px solid #f5f5f5;
}
.chapter-row:last-child { border-bottom: none; }
.chapter-header {
  display: flex;
  align-items: center;
  gap: 8px;
}
.ch-icon {
  font-size: 12px;
  color: #999;
  flex-shrink: 0;
}
.chapter-row.done .ch-icon { color: #18a058; }
.chapter-row.writing .ch-icon { color: #2080f0; }
.ch-label {
  font-size: 14px;
  color: #333;
  font-weight: 500;
  flex: 1;
}
.chapter-row.pending .ch-label { color: #bbb; }
.chapter-row.writing .ch-label { color: #2080f0; }
.ch-badge {
  font-size: 11px;
  padding: 1px 8px;
  border-radius: 10px;
  flex-shrink: 0;
}
.ch-badge.done { background: #f0faf4; color: #18a058; }
.ch-badge.writing {
  background: #eef4ff; color: #2080f0;
  animation: pulse 1.5s ease-in-out infinite;
}
.ch-badge.pending { background: #f5f5f5; color: #bbb; }
.ch-preview {
  margin-top: 8px;
  padding-left: 24px;
  font-size: 13px;
  line-height: 1.7;
  color: #666;
}

/* ── 日志区域 ── */
.log-section {
  margin-bottom: 20px;
  border: 1px solid #f0f0f0;
  border-radius: 10px;
  overflow: hidden;
  background: #fafbfc;
}
.log-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  cursor: pointer;
  user-select: none;
  background: #f5f6f8;
}
.log-title {
  font-size: 13px;
  font-weight: 600;
  color: #555;
}
.log-pulse {
  width: 8px; height: 8px;
  border-radius: 50%;
  background: #18a058;
  animation: pulse 1.5s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}
.log-list {
  max-height: 180px;
  overflow-y: auto;
  padding: 8px 0;
  font-family: 'SF Mono', 'Menlo', 'Consolas', monospace;
  font-size: 12px;
}
.log-entry {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 2px 16px;
  line-height: 1.6;
}
.log-time { color: #999; flex-shrink: 0; min-width: 42px; text-align: right; white-space: nowrap; }
.log-icon { flex-shrink: 0; width: 14px; text-align: center; font-size: 11px; }
.log-text { color: #444; word-break: break-all; }
.log-entry.success .log-icon { color: #18a058; }
.log-entry.pending .log-icon { color: #2080f0; animation: pulse 1s infinite; }
.log-entry.pending .log-text { color: #2080f0; }
.log-entry.error .log-icon { color: #d03050; }
.log-entry.error .log-text { color: #d03050; }
.log-entry.info .log-icon { color: #999; }
.log-entry.info .log-text { color: #888; }

/* ── 底部 ── */
.footer-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid #f0f0f0;
}
</style>
