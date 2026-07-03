<template>
  <div class="kb-admin">
    <n-h2>知识库管理</n-h2>

    <n-alert v-if="error" type="error" style="margin-bottom: 16px" closable @close="error = ''">
      {{ error }}
    </n-alert>

    <n-spin :show="loading">
      <n-data-table :columns="columns" :data="kbs" :bordered="false" size="small" />
      <n-empty v-if="!loading && !kbs.length" description="暂无知识库" />
    </n-spin>
  </div>
</template>

<script setup lang="ts">
import { ref, h, onMounted } from 'vue'
import { NH2, NDataTable, NTag, NSwitch, NSpin, NEmpty, NAlert, useMessage } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import request from '../utils/request'

interface KB {
  id: number
  name: string
  owner: string
  tenant_id: string
  visibility: string
  document_count: number
  enabled: boolean
  created_at: string
}

const message = useMessage()
const kbs = ref<KB[]>([])
const loading = ref(false)
const error = ref('')

const columns: DataTableColumns<KB> = [
  { title: 'ID', key: 'id', width: 60 },
  { title: '名称', key: 'name' },
  { title: '拥有者', key: 'owner', width: 140 },
  { title: '租户', key: 'tenant_id', width: 100 },
  {
    title: '可见性', key: 'visibility', width: 80,
    render(row) {
      const map: Record<string, string> = { private: '私有', shared: '共享', public: '公开' }
      const type = row.visibility === 'public' ? 'success' : row.visibility === 'shared' ? 'info' : 'default'
      return h(NTag, { type, size: 'small' }, () => map[row.visibility] || row.visibility)
    },
  },
  { title: '文档数', key: 'document_count', width: 80 },
  {
    title: '启用', key: 'enabled', width: 70,
    render(row) {
      return h(NSwitch, {
        value: row.enabled,
        size: 'small',
        onUpdateValue: (v: boolean) => toggleEnabled(row, v),
      })
    },
  },
]

async function fetchKBs() {
  loading.value = true
  error.value = ''
  try {
    const { data } = await request.get('/api/admin/knowledge-bases')
    kbs.value = data.knowledge_bases || []
  } catch (e: any) {
    error.value = e.response?.data?.detail || '获取失败'
  }
  loading.value = false
}

async function toggleEnabled(kb: KB, enabled: boolean) {
  try {
    await request.put(`/api/admin/knowledge-bases/${kb.id}`, { enabled })
    kb.enabled = enabled
  } catch (e: any) {
    message.error(e.response?.data?.detail || '操作失败')
  }
}

onMounted(fetchKBs)
</script>

<style scoped>
.kb-admin { padding: 8px; }
</style>
