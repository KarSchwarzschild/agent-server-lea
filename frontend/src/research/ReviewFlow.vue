<template>
  <div class="review-flow">
    <div class="header">
      <n-button text @click="router.push('/research')">
        <template #icon><n-icon :component="ArrowBack" /></template>
        返回
      </n-button>
      <span class="title">综述任务: {{ review?.title || '加载中...' }}</span>
      <n-tag :type="statusType">{{ statusText }}</n-tag>
    </div>

    <div class="body">
      <!-- Left: Flow Graph -->
      <div class="graph-panel">
        <FlowGraph :statuses="sse.state.nodeStatuses" />

        <n-card size="small" title="执行日志" class="log-card">
          <div class="log-list">
            <div v-for="log in sse.state.logs.slice(-8)" :key="log.time" class="log-item">
              <n-tag size="tiny" :bordered="false">{{ log.node }}</n-tag>
              <span class="log-time">{{ formatTime(log.time) }}</span>
            </div>
            <div v-if="sse.state.logs.length === 0" class="log-empty">暂无日志</div>
          </div>
        </n-card>
      </div>

      <!-- Right: Content + HITL -->
      <div class="content-panel">
        <n-alert v-if="sse.state.error" type="error" :title="sse.state.error" class="error-alert" />

        <!-- Stage content (during execution) -->
        <n-card v-if="latestContent && review?.status !== 'completed'" size="small" :title="latestContent.title">
          <div class="stage-content" v-html="latestContent.html"></div>
        </n-card>

        <!-- HITL Panel -->
        <HitlPanel
          v-if="sse.state.interruptPayload"
          :payload="sse.state.interruptPayload"
          :loading="sse.state.running"
          @approve="handleApprove"
          @reject="handleReject"
        />

        <!-- Start button -->
        <div v-if="canStart" class="start-area">
          <n-button type="primary" size="large" @click="handleStart" :loading="sse.state.running">
            {{ review?.status === 'running' ? '重新启动' : '启动综述生成' }}
          </n-button>
        </div>

        <!-- Completed: show full review + download -->
        <template v-if="review?.status === 'completed' && review.review_content">
          <n-card size="small" title="综述内容" class="review-card">
            <template #header-extra>
              <n-dropdown :options="exportOptions" @select="handleExport">
                <n-button size="small" type="primary">
                  <template #icon><n-icon :component="DownloadOutline" /></template>
                  导出
                </n-button>
              </n-dropdown>
            </template>
            <div class="review-content" v-html="renderedReview"></div>
          </n-card>
        </template>

        <template v-else-if="review?.status === 'completed' && !review.review_content">
          <n-result status="success" title="综述生成完成" description="内容已保存">
            <template #footer>
              <n-button @click="router.push('/research')">返回列表</n-button>
            </template>
          </n-result>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NButton, NTag, NCard, NIcon, NAlert, NResult, NDropdown,
} from 'naive-ui'
import type { DropdownOption } from 'naive-ui'
import { ArrowBack, DownloadOutline } from '@vicons/ionicons5'
import request from '../utils/request'
import FlowGraph from './components/FlowGraph.vue'
import HitlPanel from './components/HitlPanel.vue'
import { useReviewSSE } from './composables/useReviewSSE'

const route = useRoute()
const router = useRouter()
const sse = useReviewSSE()

const reviewId = computed(() => Number(route.params.id))
const review = ref<any>(null)

const canStart = computed(() =>
  review.value && ['pending', 'error', 'running'].includes(review.value.status) && !sse.state.running
)

const statusType = computed(() => {
  const m: Record<string, 'default' | 'info' | 'success' | 'warning' | 'error'> = {
    pending: 'default', running: 'info', completed: 'success', error: 'error',
  }
  return m[review.value?.status] || 'default'
})

const statusText = computed(() => {
  if (sse.state.running) return '执行中...'
  if (sse.state.interruptPayload) return '等待审批'
  const m: Record<string, string> = {
    pending: '待启动', running: '执行中', completed: '已完成', error: '出错',
  }
  return m[review.value?.status] || review.value?.status || ''
})

const renderedReview = computed(() => {
  const md = review.value?.review_content || ''
  return md
    .replace(/^### (.+)$/gm, '<h3>$1</h3>')
    .replace(/^## (.+)$/gm, '<h2>$1</h2>')
    .replace(/^# (.+)$/gm, '<h1>$1</h1>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/\n\n/g, '</p><p>')
    .replace(/\n/g, '<br/>')
    .replace(/^/, '<p>').replace(/$/, '</p>')
})

const exportOptions: DropdownOption[] = [
  { label: 'Word (.docx)', key: 'docx' },
  { label: 'PDF', key: 'pdf' },
  { label: 'LaTeX (.tex)', key: 'latex' },
  { label: 'Markdown (.md)', key: 'md' },
]

const latestContent = computed(() => {
  const logs = sse.state.logs
  if (logs.length === 0) return null

  for (let i = logs.length - 1; i >= 0; i--) {
    const { node, data } = logs[i]
    if (node === 'search' && data?.search_results) {
      return {
        title: `文献检索 (${data.search_results.length} 篇)`,
        html: data.search_results.slice(0, 5)
          .map((p: any) => `<b>${p.title || '无标题'}</b><br/><small>${(p.abstract || '').slice(0, 150)}...</small>`)
          .join('<hr style="margin:8px 0"/>'),
      }
    }
    if (node === 'analyze' && data?.analyses) {
      return {
        title: `文献分析 (${data.analyses.length} 篇)`,
        html: data.analyses.slice(0, 5)
          .map((a: any) => `<b>${a.paper_title || '无标题'}</b> — 相关度: ${a.relevance || 'N/A'}`)
          .join('<br/>'),
      }
    }
    if (node === 'outline' && data?.outline) {
      return { title: '大纲', html: `<pre style="white-space:pre-wrap">${data.outline}</pre>` }
    }
    if (node === 'generate' && data?.review_draft) {
      return { title: '综述草稿', html: `<pre style="white-space:pre-wrap">${data.review_draft.slice(0, 2000)}...</pre>` }
    }
  }

  const last = logs[logs.length - 1]
  return { title: `节点: ${last.node}`, html: `<pre>${JSON.stringify(last.data, null, 2).slice(0, 500)}</pre>` }
})

async function fetchReview() {
  try {
    const { data } = await request.get(`/api/apps/research/reviews/${reviewId.value}`)
    review.value = data
    if (data.status === 'running') {
      await sse.restoreState(reviewId.value)
    }
  } catch {
    router.push('/research')
  }
}

async function handleStart() {
  await sse.startReview(reviewId.value)
  await fetchReview()
}

async function handleApprove(feedback?: string) {
  await sse.resumeReview(reviewId.value, true, feedback)
  await fetchReview()
}

async function handleReject(feedback?: string) {
  await sse.resumeReview(reviewId.value, false, feedback)
}

async function handleExport(fmt: string) {
  const token = localStorage.getItem('lea_token')
  const url = `/api/apps/research/reviews/${reviewId.value}/export?fmt=${fmt}`
  const resp = await fetch(url, {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!resp.ok) return
  const blob = await resp.blob()
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  const extMap: Record<string, string> = { docx: '.docx', pdf: '.pdf', latex: '.tex', md: '.md' }
  a.download = `${review.value?.title || 'review'}${extMap[fmt] || ''}`
  a.click()
  URL.revokeObjectURL(a.href)
}

function formatTime(ts: number) {
  return new Date(ts).toLocaleTimeString()
}

watch(() => sse.state.running, (running) => {
  if (!running) fetchReview()
})

onMounted(fetchReview)
onUnmounted(() => sse.abort())
</script>

<style scoped>
.review-flow {
  height: calc(100vh - 82px);
  display: flex;
  flex-direction: column;
}

.header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  border-bottom: 1px solid #e8e8e8;
}

.title {
  font-weight: 500;
  flex: 1;
}

.body {
  display: flex;
  flex: 1;
  overflow: hidden;
}

.graph-panel {
  width: 380px;
  flex-shrink: 0;
  padding: 16px;
  border-right: 1px solid #e8e8e8;
  overflow-y: auto;
}

.content-panel {
  flex: 1;
  padding: 16px;
  overflow-y: auto;
}

.log-card {
  margin-top: 12px;
}

.log-list {
  max-height: 200px;
  overflow-y: auto;
}

.log-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 2px 0;
}

.log-time {
  font-size: 11px;
  color: #999;
}

.log-empty {
  color: #ccc;
  text-align: center;
  padding: 12px;
}

.stage-content {
  max-height: 400px;
  overflow-y: auto;
  line-height: 1.7;
}

.error-alert {
  margin-bottom: 12px;
}

.start-area {
  display: flex;
  justify-content: center;
  padding: 60px 0;
}

.review-card {
  margin-top: 12px;
}

.review-content {
  max-height: 600px;
  overflow-y: auto;
  line-height: 1.8;
  font-size: 14px;
}

.review-content h1, .review-content h2, .review-content h3 {
  margin: 16px 0 8px;
}

.review-content p {
  margin-bottom: 8px;
}
</style>
