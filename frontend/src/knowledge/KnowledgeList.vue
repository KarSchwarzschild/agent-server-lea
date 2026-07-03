<template>
  <div class="kb-page">
    <div class="header">
      <n-h2 style="margin: 0">知识库</n-h2>
      <n-button type="primary" @click="openCreate">创建知识库</n-button>
    </div>

    <n-alert v-if="error" type="error" style="margin-bottom: 16px" closable @close="error = ''">
      {{ error }}
    </n-alert>

    <n-spin :show="loading">
      <n-grid v-if="kbs.length" :cols="3" :x-gap="12" :y-gap="12">
        <n-gi v-for="kb in kbs" :key="kb.id">
          <n-card size="small" hoverable @click="$router.push(`/knowledge/${kb.id}`)">
            <template #header>
              <div class="card-header">
                <span class="kb-name">{{ kb.name }}</span>
                <n-tag :type="visColor(kb.visibility)" size="small">{{ visLabel(kb.visibility) }}</n-tag>
              </div>
            </template>
            <p class="desc">{{ kb.description || '暂无描述' }}</p>
            <n-descriptions :column="2" size="small">
              <n-descriptions-item label="文档数">{{ kb.document_count }}</n-descriptions-item>
              <n-descriptions-item label="检索模式">{{ kb.retrieval_mode }}</n-descriptions-item>
            </n-descriptions>
            <template #action>
              <n-space justify="end">
                <n-popconfirm @positive-click="handleDelete(kb)">
                  <template #trigger>
                    <n-button size="small" type="error" secondary @click.stop>删除</n-button>
                  </template>
                  确定删除「{{ kb.name }}」及所有文档？
                </n-popconfirm>
              </n-space>
            </template>
          </n-card>
        </n-gi>
      </n-grid>
      <n-empty v-else-if="!loading" description="暂无知识库">
        <template #extra>
          <n-button size="small" type="primary" @click="openCreate">创建第一个知识库</n-button>
        </template>
      </n-empty>
    </n-spin>

    <!-- 创建弹窗 -->
    <n-modal v-model:show="showCreate" preset="card" title="创建知识库" style="width: 480px">
      <n-form :model="form" label-placement="left" label-width="80">
        <n-form-item label="名称">
          <n-input v-model:value="form.name" placeholder="如：终身教育文献库" />
        </n-form-item>
        <n-form-item label="描述">
          <n-input v-model:value="form.description" type="textarea" :rows="2" />
        </n-form-item>
        <n-form-item label="可见性">
          <n-select v-model:value="form.visibility" :options="visOptions" />
        </n-form-item>
      </n-form>
      <template #action>
        <n-space justify="end">
          <n-button @click="showCreate = false">取消</n-button>
          <n-button type="primary" @click="handleCreate" :loading="creating">创建</n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import {
  NH2, NButton, NGrid, NGi, NCard, NTag, NDescriptions, NDescriptionsItem,
  NSpace, NSpin, NEmpty, NAlert, NPopconfirm, NModal, NForm, NFormItem,
  NInput, NSelect, useMessage,
} from 'naive-ui'
import request from '../utils/request'

interface KB {
  id: number
  name: string
  description: string
  visibility: string
  document_count: number
  retrieval_mode: string
}

const message = useMessage()
const kbs = ref<KB[]>([])
const loading = ref(false)
const error = ref('')
const showCreate = ref(false)
const creating = ref(false)

const form = reactive({ name: '', description: '', visibility: 'private' })

const visOptions = [
  { label: '私有（仅自己）', value: 'private' },
  { label: '共享（租户内）', value: 'shared' },
  { label: '公开（全平台）', value: 'public' },
]

function visColor(v: string) {
  if (v === 'public') return 'success'
  if (v === 'shared') return 'info'
  return 'default'
}

function visLabel(v: string) {
  if (v === 'public') return '公开'
  if (v === 'shared') return '共享'
  return '私有'
}

async function fetchKBs() {
  loading.value = true
  error.value = ''
  try {
    const { data } = await request.get('/api/knowledge-bases')
    kbs.value = data.knowledge_bases || []
  } catch (e: any) {
    error.value = e.response?.data?.detail || '获取知识库列表失败'
  }
  loading.value = false
}

function openCreate() {
  form.name = ''
  form.description = ''
  form.visibility = 'private'
  showCreate.value = true
}

async function handleCreate() {
  if (!form.name.trim()) {
    message.warning('名称不能为空')
    return
  }
  creating.value = true
  try {
    await request.post('/api/knowledge-bases', form)
    message.success('创建成功')
    showCreate.value = false
    fetchKBs()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '创建失败')
  }
  creating.value = false
}

async function handleDelete(kb: KB) {
  try {
    await request.delete(`/api/knowledge-bases/${kb.id}`)
    message.success('已删除')
    fetchKBs()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '删除失败')
  }
}

onMounted(fetchKBs)
</script>

<style scoped>
.kb-page { padding: 8px; }
.header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.card-header { display: flex; justify-content: space-between; align-items: center; }
.kb-name { font-weight: 600; }
.desc { color: #666; font-size: 13px; margin: 0 0 8px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
</style>
