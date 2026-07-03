<template>
  <div class="kb-detail">
    <div class="header" v-if="kb">
      <div>
        <n-button quaternary size="small" @click="$router.push('/knowledge')">← 返回</n-button>
        <n-h2 style="margin: 8px 0 0">{{ kb.name }}</n-h2>
        <n-text depth="3">{{ kb.description || '暂无描述' }}</n-text>
      </div>
      <n-upload
        :custom-request="handleUpload"
        :show-file-list="false"
        accept=".pdf,.txt,.md,.docx"
      >
        <n-button type="primary">上传文档</n-button>
      </n-upload>
    </div>

    <n-alert v-if="error" type="error" style="margin: 16px 0" closable @close="error = ''">
      {{ error }}
    </n-alert>

    <!-- 文档列表 -->
    <n-spin :show="loading" style="margin-top: 16px">
      <n-data-table
        v-if="docs.length"
        :columns="columns"
        :data="docs"
        :bordered="false"
        size="small"
      />
      <n-empty v-else-if="!loading" description="暂无文档，上传 PDF/TXT/DOCX 开始构建知识库" />
    </n-spin>

    <!-- 检索测试 -->
    <div v-if="kb" style="margin-top: 24px">
      <n-h3>检索测试</n-h3>
      <n-input-group>
        <n-input v-model:value="searchQuery" placeholder="输入关键词测试检索..." @keyup.enter="handleSearch" />
        <n-button type="primary" @click="handleSearch" :loading="searching">检索</n-button>
      </n-input-group>
      <div v-if="searchResults.length" style="margin-top: 12px">
        <n-card v-for="(r, i) in searchResults" :key="i" size="small" style="margin-bottom: 8px">
          <template #header>
            <n-text strong>{{ r.title || '未知文档' }}</n-text>
            <n-tag size="tiny" style="margin-left: 8px">score: {{ (r.score || 0).toFixed(3) }}</n-tag>
          </template>
          <n-text>{{ r.content }}</n-text>
        </n-card>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, h, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import {
  NH2, NH3, NText, NButton, NUpload, NDataTable, NTag, NSpin, NEmpty,
  NAlert, NCard, NInput, NInputGroup, useMessage,
} from 'naive-ui'
import type { DataTableColumns, UploadCustomRequestOptions } from 'naive-ui'
import request from '../utils/request'

const route = useRoute()
const message = useMessage()
const kbId = Number(route.params.id)

interface DocItem {
  id: number
  filename: string
  file_type: string
  file_size: number
  status: string
  chunk_count: number
  created_at: string
}

const kb = ref<any>(null)
const docs = ref<DocItem[]>([])
const loading = ref(false)
const error = ref('')
const searchQuery = ref('')
const searching = ref(false)
const searchResults = ref<any[]>([])

const columns: DataTableColumns<DocItem> = [
  { title: '文件名', key: 'filename', ellipsis: { tooltip: true } },
  { title: '类型', key: 'file_type', width: 80 },
  {
    title: '大小',
    key: 'file_size',
    width: 100,
    render(row) { return (row.file_size / 1024).toFixed(1) + ' KB' },
  },
  {
    title: '状态',
    key: 'status',
    width: 100,
    render(row) {
      const type = row.status === 'ready' ? 'success' : row.status === 'error' ? 'error' : 'warning'
      const label = row.status === 'ready' ? '就绪' : row.status === 'error' ? '失败' : '处理中'
      return h(NTag, { type, size: 'small' }, () => label)
    },
  },
  { title: '分块数', key: 'chunk_count', width: 80 },
  {
    title: '操作',
    key: 'actions',
    width: 80,
    render(row) {
      return h(NButton, { size: 'small', type: 'error', secondary: true, onClick: () => handleDelete(row) }, () => '删除')
    },
  },
]

async function fetchKB() {
  try {
    const { data } = await request.get(`/api/knowledge-bases/${kbId}`)
    kb.value = data
  } catch (e: any) {
    error.value = '知识库不存在'
  }
}

async function fetchDocs() {
  loading.value = true
  try {
    const { data } = await request.get(`/api/knowledge-bases/${kbId}/documents`)
    docs.value = data.documents || []
  } catch { /* ignore */ }
  loading.value = false
}

async function handleUpload({ file }: UploadCustomRequestOptions) {
  const formData = new FormData()
  formData.append('file', file.file as File)
  try {
    await request.post(`/api/knowledge-bases/${kbId}/documents`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    message.success(`${file.name} 已上传，正在处理...`)
    fetchDocs()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '上传失败')
  }
}

async function handleDelete(doc: DocItem) {
  try {
    await request.delete(`/api/knowledge-bases/${kbId}/documents/${doc.id}`)
    message.success('已删除')
    fetchDocs()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '删除失败')
  }
}

async function handleSearch() {
  if (!searchQuery.value.trim()) return
  searching.value = true
  try {
    const { data } = await request.post(`/api/knowledge-bases/${kbId}/search`, {
      query: searchQuery.value,
      limit: 5,
    })
    searchResults.value = data.results || []
    if (!searchResults.value.length) message.info('未找到相关内容')
  } catch (e: any) {
    message.error(e.response?.data?.detail || '检索失败')
  }
  searching.value = false
}

onMounted(() => {
  fetchKB()
  fetchDocs()
})
</script>

<style scoped>
.kb-detail { padding: 8px; }
.header { display: flex; justify-content: space-between; align-items: flex-start; }
</style>
