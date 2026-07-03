<script setup lang="ts">
import { ref, onMounted } from 'vue'
import {
  NSpin, NSpace, NAlert, NButton, NTable, NPopconfirm,
  NModal, NForm, NFormItem, NInput,
  useMessage,
} from 'naive-ui'
import {
  getKnowledgeBases,
  createKnowledgeBase,
  deleteKnowledgeBase,
  type KnowledgeBaseInfo,
} from '../../api/settings'

const message = useMessage()

const kbLoading = ref(false)
const knowledgeBases = ref<KnowledgeBaseInfo[]>([])
const showKbModal = ref(false)
const newKb = ref({ kb_id: '', name: '', description: '' })
const kbCreating = ref(false)

async function loadKnowledgeBases() {
  kbLoading.value = true
  try {
    knowledgeBases.value = await getKnowledgeBases()
  } catch (e: any) {
    message.error(e.message || '加载知识库列表失败')
  } finally {
    kbLoading.value = false
  }
}

async function handleCreateKb() {
  if (!newKb.value.kb_id || !newKb.value.name) {
    message.warning('请填写知识库ID和名称')
    return
  }
  kbCreating.value = true
  try {
    await createKnowledgeBase(newKb.value.kb_id, newKb.value.name, newKb.value.description)
    message.success('知识库添加成功')
    showKbModal.value = false
    newKb.value = { kb_id: '', name: '', description: '' }
    loadKnowledgeBases()
  } catch (e: any) {
    message.error(e.message || '添加知识库失败')
  } finally {
    kbCreating.value = false
  }
}

async function handleDeleteKb(kbId: string) {
  try {
    await deleteKnowledgeBase(kbId)
    message.success('知识库已归档')
    loadKnowledgeBases()
  } catch (e: any) {
    message.error(e.message || '删除知识库失败')
  }
}

onMounted(() => { loadKnowledgeBases() })
</script>

<template>
  <n-spin :show="kbLoading">
    <div class="tab-content">
      <n-space justify="space-between" style="margin-bottom: 16px">
        <n-alert type="info" title="说明" :bordered="false" style="flex: 1">
          管理系统中注册的知识库。知识库需要先在智能基座中创建，然后在此处添加记录。
        </n-alert>
        <n-button type="primary" @click="showKbModal = true">
          添加知识库
        </n-button>
      </n-space>

      <n-table :data="knowledgeBases" :bordered="false" :single-line="false">
        <thead>
          <tr>
            <th>知识库ID</th>
            <th>名称</th>
            <th>描述</th>
            <th>状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="kb in knowledgeBases" :key="kb.kb_id">
            <td>{{ kb.kb_id }}</td>
            <td>{{ kb.name }}</td>
            <td>{{ kb.description || '-' }}</td>
            <td>{{ kb.status === 'active' ? '正常' : '已归档' }}</td>
            <td>
              <n-popconfirm @positive-click="handleDeleteKb(kb.kb_id)">
                <template #trigger>
                  <n-button size="small" type="error">归档</n-button>
                </template>
                确定要归档这个知识库吗？
              </n-popconfirm>
            </td>
          </tr>
          <tr v-if="knowledgeBases.length === 0">
            <td colspan="5" style="text-align: center; color: #999;">
              暂无知识库，点击"添加知识库"按钮添加
            </td>
          </tr>
        </tbody>
      </n-table>
    </div>
  </n-spin>

  <!-- 添加知识库弹窗 -->
  <n-modal
    v-model:show="showKbModal"
    preset="card"
    title="添加知识库"
    style="width: 500px"
  >
    <n-form label-placement="top">
      <n-form-item label="知识库ID" required>
        <n-input
          v-model:value="newKb.kb_id"
          placeholder="从智能基座获取的知识库ID"
        />
      </n-form-item>
      <n-form-item label="知识库名称" required>
        <n-input
          v-model:value="newKb.name"
          placeholder="知识库显示名称"
        />
      </n-form-item>
      <n-form-item label="描述">
        <n-input
          v-model:value="newKb.description"
          type="textarea"
          :rows="3"
          placeholder="知识库描述（可选）"
        />
      </n-form-item>
    </n-form>

    <template #footer>
      <n-space justify="end">
        <n-button @click="showKbModal = false">取消</n-button>
        <n-button type="primary" :loading="kbCreating" @click="handleCreateKb">
          添加
        </n-button>
      </n-space>
    </template>
  </n-modal>
</template>
