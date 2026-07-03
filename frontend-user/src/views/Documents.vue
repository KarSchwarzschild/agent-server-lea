<template>
  <div class="documents-page">
    <!-- 顶部栏 -->
    <div class="documents-header">
      <h2 class="page-title">📄 文档中心</h2>
      <div class="header-actions">
        <n-input
          v-model:value="searchQuery"
          placeholder="搜索文档..."
          clearable
          size="small"
          style="width: 200px"
        >
          <template #prefix><n-icon size="16"><SearchOutline /></n-icon></template>
        </n-input>
      </div>
    </div>

    <!-- 分类 Tab -->
    <n-tabs v-model:value="selectedCategory" type="segment" size="small" class="category-tabs">
      <n-tab-pane name="" tab="📋 全部" />
      <n-tab-pane name="official" tab="🏛️ 公文" />
      <n-tab-pane name="academic" tab="📚 学术" />
      <n-tab-pane name="teaching" tab="📖 教学" />
      <n-tab-pane name="report" tab="📊 报告" />
    </n-tabs>

    <!-- 文档列表 -->
    <div class="documents-list">
      <div v-if="loading" class="loading-state">加载中...</div>
      <div v-else-if="documents.length === 0" class="empty-state">
        <p>暂无文档</p>
        <p class="empty-hint">在对话中使用"智能文档生成"技能创建文档，即可在此管理</p>
      </div>
      <div
        v-else
        v-for="doc in documents"
        :key="doc.id"
        class="document-item"
        @click="handleOpen(doc)"
      >
        <div class="doc-main">
          <div class="doc-title">{{ doc.title }}</div>
          <div class="doc-meta">
            <span class="doc-category">{{ getCategoryLabel(doc.docCategory) }}</span>
            <span class="doc-sep">|</span>
            <span>v{{ doc.currentIndex }}</span>
            <span class="doc-sep">|</span>
            <span>{{ formatDate(doc.updatedAt) }}</span>
          </div>
        </div>
        <div class="doc-actions" @click.stop>
          <n-button size="tiny" quaternary @click="handleExport(doc, 'docx')">导出</n-button>
        </div>
      </div>
    </div>

    <!-- 分页 -->
    <div v-if="total > pageSize" class="pagination">
      <n-pagination
        v-model:page="currentPage"
        :page-count="Math.ceil(total / pageSize)"
        :page-size="pageSize"
        @update:page="fetchDocuments"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { NInput, NTabs, NTabPane, NButton, NPagination, NIcon, useMessage } from 'naive-ui'
import { SearchOutline } from '@vicons/ionicons5'
import type { Artifact, DocCategory } from '@/types/artifact'
import { DOC_CATEGORY_META } from '@/types/artifact'
import { listDocuments, exportArtifact } from '@/api/artifact'

const router = useRouter()
const message = useMessage()

const documents = ref<Artifact[]>([])
const total = ref(0)
const loading = ref(false)
const searchQuery = ref('')
const selectedCategory = ref('')
const currentPage = ref(1)
const pageSize = 20

async function fetchDocuments() {
  loading.value = true
  try {
    const result = await listDocuments({
      category: selectedCategory.value || undefined,
      limit: pageSize,
      offset: (currentPage.value - 1) * pageSize,
    })
    documents.value = result.items
    total.value = result.total
  } catch (e) {
    console.error('Failed to load documents:', e)
  } finally {
    loading.value = false
  }
}

function getCategoryLabel(category: string): string {
  return DOC_CATEGORY_META[category as DocCategory]?.label ?? category
}

function formatDate(dateStr: string): string {
  if (!dateStr) return ''
  return new Date(dateStr).toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}

function handleOpen(doc: Artifact) {
  // 跳转到对话页面并打开 artifact 面板
  router.push({ name: 'super-agent', query: { artifact: doc.id, session: doc.sessionId } })
}

async function handleExport(doc: Artifact, format: 'docx' | 'pdf') {
  try {
    const result = await exportArtifact(doc.id, format)
    // 触发下载
    window.open(`/api/user/super-agent/files/${result.file_id}/download`, '_blank')
    message.success(`${doc.title} 导出成功`)
  } catch {
    message.error('导出失败')
  }
}

watch(selectedCategory, () => {
  currentPage.value = 1
  fetchDocuments()
})

onMounted(fetchDocuments)
</script>

<style scoped>
.documents-page {
  max-width: 900px;
  margin: 0 auto;
  padding: 24px;
}

.documents-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.page-title {
  font-size: 20px;
  font-weight: 600;
  margin: 0;
}

.category-tabs {
  margin-bottom: 16px;
}

.documents-list {
  display: flex;
  flex-direction: column;
  gap: 1px;
  background: #e8e8e8;
  border-radius: 8px;
  overflow: hidden;
}

.document-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px;
  background: #fff;
  cursor: pointer;
  transition: background 0.15s;
}
.document-item:hover {
  background: #f8f9fb;
}
.document-item:hover .doc-actions {
  opacity: 1;
}

.doc-main {
  flex: 1;
  min-width: 0;
}
.doc-title {
  font-size: 15px;
  font-weight: 500;
  margin-bottom: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.doc-meta {
  font-size: 12px;
  color: #999;
  display: flex;
  gap: 4px;
}
.doc-sep { color: #ddd; }
.doc-category {
  padding: 1px 6px;
  background: #f0f4ff;
  border-radius: 3px;
  color: #2080f0;
  font-size: 11px;
}

.doc-actions {
  opacity: 0;
  transition: opacity 0.15s;
}

.loading-state, .empty-state {
  text-align: center;
  padding: 40px 20px;
  background: #fff;
  border-radius: 8px;
  color: #999;
}
.empty-hint {
  font-size: 13px;
  margin-top: 8px;
}

.pagination {
  display: flex;
  justify-content: center;
  margin-top: 16px;
}
</style>
