<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useMessage, useDialog } from 'naive-ui'
import { getDeepReviewHistory, deleteDeepReview, type DeepReviewListItem } from '../../api/deep-review'

const router = useRouter()
const message = useMessage()
const dialog = useDialog()

const loading = ref(true)
const items = ref<DeepReviewListItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const searchQuery = ref('')

onMounted(() => loadHistory())

async function loadHistory() {
  loading.value = true
  try {
    const res = await getDeepReviewHistory(page.value, pageSize.value)
    items.value = res.items
    total.value = res.total
  } catch {
    message.error('加载历史失败')
  } finally {
    loading.value = false
  }
}

function getStatusType(status: string) {
  const map: Record<string, 'success' | 'info' | 'error' | 'warning' | 'default'> = {
    completed: 'success',
    running: 'info',
    pending: 'info',
    failed: 'error',
    cancelled: 'warning',
  }
  return map[status] || 'default'
}

function getStatusLabel(status: string) {
  const map: Record<string, string> = {
    completed: '已完成',
    running: '生成中',
    pending: '排队中',
    failed: '失败',
    cancelled: '已取消',
  }
  return map[status] || status
}

function getReportTypeLabel(type: string) {
  const map: Record<string, string> = {
    literature_review: '文献综述',
    policy_analysis: '政策分析',
    research_report: '研究报告',
  }
  return map[type] || type
}

function viewReport(item: DeepReviewListItem) {
  if (item.status === 'completed') {
    router.push(`/review/report/${item.task_id}`)
  } else {
    router.push(`/review/progress/${item.task_id}`)
  }
}

function handleDelete(item: DeepReviewListItem) {
  dialog.warning({
    title: '确认删除',
    content: `确定删除「${item.topic}」？此操作不可恢复。`,
    positiveText: '删除',
    negativeText: '取消',
    onPositive: async () => {
      try {
        await deleteDeepReview(item.task_id)
        message.success('已删除')
        loadHistory()
      } catch {
        message.error('删除失败')
      }
    },
  })
}

function handlePageChange(p: number) {
  page.value = p
  loadHistory()
}

function goCreate() {
  router.push('/review/new')
}

const filteredItems = computed(() => {
  if (!searchQuery.value.trim()) return items.value
  const q = searchQuery.value.trim().toLowerCase()
  return items.value.filter(i => i.topic.toLowerCase().includes(q))
})
</script>

<template>
  <div class="review-history">
    <div class="history-header">
      <div class="header-left">
        <h2 class="page-title">综述历史</h2>
        <n-tag size="small" round>{{ total }} 篇</n-tag>
      </div>
      <n-button type="primary" @click="goCreate">新建综述</n-button>
    </div>

    <div class="search-bar">
      <n-input
        v-model:value="searchQuery"
        placeholder="搜索主题..."
        clearable
        size="small"
        style="max-width: 320px;"
      />
    </div>

    <n-spin :show="loading">
      <n-empty v-if="!loading && items.length === 0" description="还没有综述记录" style="padding: 80px 0;">
        <template #extra>
          <n-button type="primary" @click="goCreate">创建第一篇综述</n-button>
        </template>
      </n-empty>

      <div v-else class="history-list">
        <div
          v-for="item in filteredItems"
          :key="item.task_id"
          class="history-item"
          @click="viewReport(item)"
        >
          <div class="item-main">
            <div class="item-title">{{ item.topic }}</div>
            <div class="item-meta">
              <n-tag :type="getStatusType(item.status)" size="small" round>
                {{ getStatusLabel(item.status) }}
              </n-tag>
              <span class="meta-text">{{ getReportTypeLabel(item.report_type) }}</span>
              <span v-if="item.word_count" class="meta-text">{{ item.word_count.toLocaleString() }} 字</span>
              <span v-if="item.chapters_count" class="meta-text">{{ item.chapters_count }} 章</span>
              <span class="meta-text">{{ new Date(item.created_at).toLocaleDateString() }}</span>
            </div>
            <n-progress
              v-if="item.status === 'running'"
              type="line"
              :percentage="item.progress_percent"
              :height="4"
              style="margin-top: 8px; max-width: 300px;"
            />
          </div>
          <div class="item-actions" @click.stop>
            <n-button v-if="item.status === 'completed'" text size="small" @click="viewReport(item)">查看</n-button>
            <n-button v-else-if="item.status === 'running' || item.status === 'pending'" text size="small" @click="viewReport(item)">查看进度</n-button>
            <n-button v-else-if="item.status === 'failed'" text size="small" type="warning" @click="viewReport(item)">查看详情</n-button>
            <n-button text size="small" type="error" @click="handleDelete(item)">删除</n-button>
          </div>
        </div>
      </div>

      <div v-if="total > pageSize" class="pagination">
        <n-pagination
          :page="page"
          :page-size="pageSize"
          :item-count="total"
          @update:page="handlePageChange"
        />
      </div>
    </n-spin>
  </div>
</template>

<style scoped>
.review-history {
  max-width: 800px;
  margin: 24px auto;
  padding: 0 20px;
}
.history-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}
.header-left {
  display: flex;
  align-items: center;
  gap: 10px;
}
.page-title {
  font-size: 20px;
  font-weight: 700;
  color: #1a1a1a;
  margin: 0;
}
.search-bar {
  margin-bottom: 16px;
}
.history-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.history-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  border: 1px solid #f0f0f0;
  border-radius: 12px;
  cursor: pointer;
  transition: all 0.2s;
  background: #fff;
}
.history-item:hover {
  border-color: #d9d9d9;
  box-shadow: 0 2px 8px rgba(0,0,0,0.04);
}
.item-title {
  font-size: 15px;
  font-weight: 600;
  color: #1a1a1a;
  margin-bottom: 6px;
}
.item-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.meta-text {
  font-size: 12px;
  color: #999;
}
.item-actions {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
}
.pagination {
  display: flex;
  justify-content: center;
  margin-top: 24px;
}
</style>
