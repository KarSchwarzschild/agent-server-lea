<template>
  <div class="research-view">
    <div class="toolbar">
      <h2>学术综述任务</h2>
      <n-button type="primary" @click="showCreate = true">新建综述</n-button>
    </div>

    <n-data-table
      :columns="columns"
      :data="reviews"
      :loading="loading"
      :row-key="(r: any) => r.id"
      size="medium"
    />

    <!-- Create dialog -->
    <n-modal v-model:show="showCreate" preset="dialog" title="新建综述任务">
      <n-form :model="form" label-placement="top">
        <n-form-item label="标题">
          <n-input v-model:value="form.title" placeholder="综述标题" />
        </n-form-item>
        <n-form-item label="研究主题">
          <n-input v-model:value="form.topic" type="textarea"
                   :autosize="{ minRows: 2, maxRows: 4 }"
                   placeholder="例如：大语言模型在教育领域的应用" />
        </n-form-item>
        <n-form-item label="要求（可选）">
          <n-input v-model:value="form.requirements" type="textarea"
                   :autosize="{ minRows: 2, maxRows: 4 }"
                   placeholder="综述字数、格式要求、重点方向等" />
        </n-form-item>
      </n-form>
      <template #action>
        <n-button @click="showCreate = false">取消</n-button>
        <n-button type="primary" :loading="creating" @click="handleCreate">创建</n-button>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, h, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import {
  NDataTable, NButton, NModal, NForm, NFormItem, NInput, NTag, useMessage,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import request from '../utils/request'

const router = useRouter()
const message = useMessage()

const reviews = ref<any[]>([])
const loading = ref(false)
const showCreate = ref(false)
const creating = ref(false)

const form = ref({ title: '', topic: '', requirements: '' })

const statusMap: Record<string, { label: string; type: 'default' | 'info' | 'success' | 'error' }> = {
  pending:   { label: '待启动', type: 'default' },
  running:   { label: '执行中', type: 'info' },
  completed: { label: '已完成', type: 'success' },
  error:     { label: '出错',   type: 'error' },
}

const columns: DataTableColumns = [
  { title: 'ID', key: 'id', width: 60 },
  { title: '标题', key: 'title', ellipsis: { tooltip: true } },
  { title: '主题', key: 'topic', ellipsis: { tooltip: true }, width: 250 },
  {
    title: '状态', key: 'status', width: 100,
    render: (row: any) => {
      const s = statusMap[row.status] || { label: row.status, type: 'default' as const }
      return h(NTag, { type: s.type, size: 'small' }, { default: () => s.label })
    },
  },
  {
    title: '创建时间', key: 'created_at', width: 170,
    render: (row: any) => row.created_at ? new Date(row.created_at).toLocaleString() : '-',
  },
  {
    title: '操作', key: 'actions', width: 160,
    render: (row: any) => h('div', { style: 'display:flex;gap:8px' }, [
      h(NButton, {
        size: 'small', type: 'primary', text: true,
        onClick: () => router.push(`/research/${row.id}`),
      }, { default: () => '查看' }),
      h(NButton, {
        size: 'small', type: 'error', text: true,
        onClick: () => handleDelete(row.id),
      }, { default: () => '删除' }),
    ]),
  },
]

async function fetchReviews() {
  loading.value = true
  try {
    const { data } = await request.get('/api/apps/research/reviews')
    reviews.value = data
  } catch {
    message.error('获取任务列表失败')
  } finally {
    loading.value = false
  }
}

async function handleCreate() {
  if (!form.value.title || !form.value.topic) {
    message.warning('请填写标题和主题')
    return
  }
  creating.value = true
  try {
    await request.post('/api/apps/research/reviews', form.value)
    showCreate.value = false
    form.value = { title: '', topic: '', requirements: '' }
    message.success('创建成功')
    await fetchReviews()
  } catch {
    message.error('创建失败')
  } finally {
    creating.value = false
  }
}

async function handleDelete(id: number) {
  try {
    await request.delete(`/api/apps/research/reviews/${id}`)
    message.success('已删除')
    await fetchReviews()
  } catch {
    message.error('删除失败')
  }
}

onMounted(fetchReviews)
</script>

<style scoped>
.research-view {
  padding: 8px;
}

.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.toolbar h2 {
  margin: 0;
  font-size: 18px;
}
</style>
