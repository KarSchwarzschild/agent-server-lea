<script setup lang="ts">
/**
 * 个人知识库管理页面
 * - 未创建：显示引导页
 * - 已创建：显示知识库信息 + 文档列表
 */
import { ref, h, onMounted, computed } from 'vue'
import {
  NCard,
  NButton,
  NSpace,
  NIcon,
  NEmpty,
  NProgress,
  NDataTable,
  NPagination,
  NTag,
  NModal,
  NForm,
  NFormItem,
  NInput,
  NSpin,
  NPopconfirm,
  NText,
  NTooltip,
  useMessage,
  useDialog,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import {
  AddOutline,
  TrashOutline,
  CreateOutline,
  CloudUploadOutline,
  RefreshOutline,
  SearchOutline,
} from '@vicons/ionicons5'
import { useResponsive } from '../composables/useResponsive'
import { useUserKB } from '../composables/useUserKB'
import { useRouter } from 'vue-router'
import {
  createKnowledgeBase,
  getKnowledgeBase,
  updateKnowledgeBase,
  deleteKnowledgeBase,
  getDocuments,
  deleteDocument,
  retryChunk,
} from '../api/user-kb'
import type { UserKnowledgeBase, UserDocument } from '../api/user-kb'
import DocumentUploader from '../components/DocumentUploader.vue'

const message = useMessage()
const dialog = useDialog()
const router = useRouter()
const { isMobile } = useResponsive()
const { invalidateCache } = useUserKB()

// ========== 状态 ==========
const loading = ref(true)
const kbInfo = ref<UserKnowledgeBase | null>(null)
const hasKB = ref(false)

// 文档列表
const documents = ref<UserDocument[]>([])
const docTotal = ref(0)
const docPage = ref(1)
const docPageSize = ref(20)
const docLoading = ref(false)

// 弹窗
const showCreateModal = ref(false)
const showEditModal = ref(false)
const showUploader = ref(false)
const createForm = ref({ name: '我的知识库', description: '' })
const editForm = ref({ name: '', description: '' })
const formLoading = ref(false)

// 文件名搜索过滤
const searchKeyword = ref('')
const filteredDocuments = computed(() => {
  const kw = searchKeyword.value.trim().toLowerCase()
  if (!kw) return documents.value
  return documents.value.filter(d =>
    d.filename.toLowerCase().includes(kw) ||
    d.file_type.toLowerCase().includes(kw)
  )
})

function goSearchContent() {
  router.push({ path: '/search', query: { scope: 'personal' } })
}

// ========== 数据加载 ==========
async function loadKB() {
  loading.value = true
  try {
    const data = await getKnowledgeBase()
    kbInfo.value = data
    hasKB.value = true
    await loadDocuments()
  } catch (error: any) {
    if (error?.response?.status === 404) {
      hasKB.value = false
      kbInfo.value = null
    }
  } finally {
    loading.value = false
  }
}

async function loadDocuments() {
  docLoading.value = true
  try {
    const data = await getDocuments({
      page: docPage.value,
      page_size: docPageSize.value,
    })
    documents.value = data.items
    docTotal.value = data.total
  } catch {
    message.error('加载文档列表失败')
  } finally {
    docLoading.value = false
  }
}

// ========== 知识库操作 ==========
async function handleCreate() {
  formLoading.value = true
  try {
    const data = await createKnowledgeBase(createForm.value)
    kbInfo.value = data
    hasKB.value = true
    invalidateCache()
    showCreateModal.value = false
    message.success('知识库创建成功')
    await loadDocuments()
  } catch (error: any) {
    const detail = error?.response?.data?.detail
    message.error(detail || '创建失败')
  } finally {
    formLoading.value = false
  }
}

async function handleUpdate() {
  formLoading.value = true
  try {
    const data = await updateKnowledgeBase(editForm.value)
    kbInfo.value = data
    showEditModal.value = false
    message.success('更新成功')
  } catch {
    message.error('更新失败')
  } finally {
    formLoading.value = false
  }
}

function openEditModal() {
  editForm.value = {
    name: kbInfo.value?.name || '',
    description: kbInfo.value?.description || '',
  }
  showEditModal.value = true
}

function confirmDelete() {
  dialog.warning({
    title: '删除知识库',
    content: '将删除所有文档，此操作不可恢复。确定要删除吗？',
    positiveText: '确定删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await deleteKnowledgeBase()
        kbInfo.value = null
        hasKB.value = false
        documents.value = []
        invalidateCache()
        message.success('知识库已删除')
      } catch {
        message.error('删除失败')
      }
    },
  })
}

// ========== 文档操作 ==========
function getDocStatus(doc: UserDocument): { type: string; text: string; tooltip?: string } {
  if (doc.upload_status === 'failed') {
    return {
      type: 'error',
      text: '上传失败',
      tooltip: doc.error_message || '文件上传至云端失败，请稍后重试',
    }
  }
  if (doc.upload_status === 'processing') return { type: 'warning', text: '上传中' }
  // upload_status === 'completed'
  if (!doc.chunk_status || doc.chunk_status === 'completed') return { type: 'success', text: '可检索' }
  if (doc.chunk_status === 'processing') return { type: 'warning', text: '准备中' }
  if (doc.chunk_status === 'pending') return { type: 'default', text: '排队中' }
  if (doc.chunk_status === 'failed') {
    const msg = doc.error_message || '文档处理失败'
    let shortTip = '处理失败'
    if (msg.includes('Qdrant') || msg.includes('不可用')) shortTip = '服务暂不可用'
    else if (msg.includes('解析结果为空')) shortTip = '解析失败'
    else if (msg.includes('文件不存在')) shortTip = '文件丢失'
    else if (msg.includes('embed') || msg.includes('Embedding')) shortTip = '向量化失败'
    return { type: 'error', text: shortTip, tooltip: msg }
  }
  return { type: 'default', text: doc.upload_status }
}

async function handleRetryChunk(docId: string) {
  try {
    await retryChunk(docId)
    message.success('已重新提交处理')
    await loadDocuments()
  } catch (e: any) {
    const detail = e?.response?.data?.detail || e?.message || ''
    if (detail.includes('Qdrant') || detail.includes('不可用')) {
      message.error('向量数据库服务暂不可用，请稍后再试')
    } else {
      message.error(detail || '重试失败，请稍后再试')
    }
  }
}

let _refreshTimer: ReturnType<typeof setTimeout> | null = null
function handleUploaded() {
  // 防抖：多文件上传时避免频繁刷新
  if (_refreshTimer) clearTimeout(_refreshTimer)
  _refreshTimer = setTimeout(() => {
    loadKB()
    _refreshTimer = null
  }, 800)
}

async function handleDeleteDoc(docId: string) {
  try {
    await deleteDocument(docId)
    message.success('文档已删除')
    await loadKB()
  } catch {
    message.error('删除失败')
  }
}

function handlePageChange(page: number) {
  docPage.value = page
  loadDocuments()
}

// 使用统计
const storagePercent = computed(() => {
  if (!kbInfo.value) return 0
  return Math.round((kbInfo.value.storage_used_mb / kbInfo.value.quota_mb) * 100)
})

const docCountPercent = computed(() => {
  if (!kbInfo.value) return 0
  return Math.round((kbInfo.value.document_count / kbInfo.value.max_documents) * 100)
})

// 表格列定义
const columns: DataTableColumns<UserDocument> = [
  { title: '文件名', key: 'filename', ellipsis: { tooltip: true } },
  { title: '类型', key: 'file_type', width: 80 },
  {
    title: '大小',
    key: 'file_size_kb',
    width: 100,
    render: (row) => {
      const kb = row.file_size_kb || 0
      return kb >= 1024 ? `${(kb / 1024).toFixed(1)} MB` : `${kb} KB`
    },
  },
  {
    title: '状态',
    key: 'upload_status',
    width: 100,
    render: (row) => {
      const { type, text, tooltip } = getDocStatus(row)
      const tagNode = h(NTag, {
        size: 'small',
        type,
      }, { default: () => text })

      return tooltip
        ? h(NTooltip, null, {
            trigger: () => tagNode,
            default: () => tooltip,
          })
        : tagNode
    },
  },
  {
    title: '上传时间',
    key: 'created_at',
    width: 160,
    render: (row) => row.created_at ? new Date(row.created_at).toLocaleString('zh-CN') : '-',
  },
  {
    title: '操作',
    key: 'actions',
    width: 120,
    render: (row) => {
      const nodes: any[] = []
      // 处理失败时显示重试按钮
      if (row.upload_status === 'completed' && row.chunk_status === 'failed') {
        nodes.push(
          h(NButton, {
            size: 'small',
            quaternary: true,
            type: 'primary',
            onClick: () => handleRetryChunk(row.doc_id),
          }, { default: () => '重试' })
        )
      }
      nodes.push(
        h(NPopconfirm, { onPositiveClick: () => handleDeleteDoc(row.doc_id) }, {
          trigger: () =>
            h(NButton, { size: 'small', quaternary: true, type: 'error' }, { default: () => '删除' }),
          default: () => '确定删除此文档？',
        })
      )
      return h('div', { style: 'display: flex; align-items: center; gap: 2px' }, nodes)
    },
  },
]

onMounted(loadKB)
</script>

<template>
  <div class="kb-container" :class="{ 'kb-mobile': isMobile }">
    <n-spin :show="loading">
      <!-- 未创建知识库：引导页 -->
      <template v-if="!loading && !hasKB">
        <n-card :bordered="false">
          <n-empty description="您还没有创建个人知识库" size="huge">
            <template #extra>
              <n-button type="primary" @click="showCreateModal = true">
                <template #icon><n-icon :component="AddOutline" /></template>
                创建知识库
              </n-button>
            </template>
          </n-empty>
        </n-card>
      </template>

      <!-- 已创建知识库：管理页面 -->
      <template v-if="!loading && hasKB && kbInfo">
        <!-- 知识库信息卡片 -->
        <n-card :bordered="false" style="margin-bottom: 16px">
          <div style="display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 12px">
            <div>
              <h3 style="margin: 0 0 4px 0">{{ kbInfo.name }}</h3>
              <n-text depth="3" v-if="kbInfo.description">{{ kbInfo.description }}</n-text>
            </div>
            <n-space>
              <n-button size="small" @click="openEditModal">
                <template #icon><n-icon :component="CreateOutline" /></template>
                编辑
              </n-button>
              <n-button size="small" type="error" @click="confirmDelete">
                <template #icon><n-icon :component="TrashOutline" /></template>
                删除
              </n-button>
            </n-space>
          </div>

          <div style="display: flex; gap: 32px; margin-top: 16px; flex-wrap: wrap">
            <div style="flex: 1; min-width: 200px">
              <n-text depth="3">存储空间</n-text>
              <n-progress
                type="line"
                :percentage="storagePercent"
                :indicator-placement="'inside'"
                style="margin-top: 4px"
              />
              <n-text depth="3" style="font-size: 12px">
                {{ kbInfo.storage_used_mb.toFixed(1) }} / {{ kbInfo.quota_mb }} MB
              </n-text>
            </div>
            <div style="flex: 1; min-width: 200px">
              <n-text depth="3">文档数量</n-text>
              <n-progress
                type="line"
                :percentage="docCountPercent"
                :indicator-placement="'inside'"
                style="margin-top: 4px"
              />
              <n-text depth="3" style="font-size: 12px">
                {{ kbInfo.document_count }} / {{ kbInfo.max_documents }}
              </n-text>
            </div>
          </div>
        </n-card>

        <!-- 文档管理区域 -->
        <n-card :bordered="false" title="文档管理">
          <template #header-extra>
            <n-space>
              <n-button size="small" @click="loadDocuments">
                <template #icon><n-icon :component="RefreshOutline" /></template>
                刷新
              </n-button>
              <n-button size="small" type="primary" @click="showUploader = true">
                <template #icon><n-icon :component="CloudUploadOutline" /></template>
                上传文档
              </n-button>
            </n-space>
          </template>

          <!-- 搜索栏 -->
          <div style="display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap">
            <n-input
              v-model:value="searchKeyword"
              placeholder="搜索文件名..."
              clearable
              size="small"
              style="flex: 1; min-width: 160px; max-width: 300px"
            >
              <template #prefix>
                <n-icon :component="SearchOutline" />
              </template>
            </n-input>
            <n-button size="small" quaternary type="primary" @click="goSearchContent">
              <template #icon><n-icon :component="SearchOutline" /></template>
              搜索文档内容
            </n-button>
          </div>

          <n-data-table
            v-if="!isMobile"
            :columns="columns"
            :data="filteredDocuments"
            :loading="docLoading"
            :bordered="false"
            size="small"
          />

          <!-- 移动端：卡片列表 -->
          <div v-else>
            <div v-if="filteredDocuments.length === 0 && !docLoading">
              <n-empty :description="searchKeyword ? '未找到匹配的文档' : '暂无文档'" />
            </div>
            <div
              v-for="doc in filteredDocuments"
              :key="doc.doc_id"
              style="padding: 12px 0; border-bottom: 1px solid #f0f0f0"
            >
              <div style="display: flex; justify-content: space-between; align-items: center">
                <div style="flex: 1; min-width: 0">
                  <div style="font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">
                    {{ doc.filename }}
                  </div>
                  <n-space size="small" style="margin-top: 4px">
                    <n-tag size="tiny">{{ doc.file_type }}</n-tag>
                    <n-tooltip v-if="getDocStatus(doc).tooltip">
                      <template #trigger>
                        <n-tag
                          size="tiny"
                          :type="getDocStatus(doc).type"
                          :aria-label="`文档状态：${getDocStatus(doc).text}`"
                        >
                          {{ getDocStatus(doc).text }}
                        </n-tag>
                      </template>
                      {{ getDocStatus(doc).tooltip }}
                    </n-tooltip>
                    <n-tag
                      v-else
                      size="tiny"
                      :type="getDocStatus(doc).type"
                      :aria-label="`文档状态：${getDocStatus(doc).text}`"
                    >
                      {{ getDocStatus(doc).text }}
                    </n-tag>
                    <n-button
                      v-if="doc.upload_status === 'completed' && doc.chunk_status === 'failed'"
                      size="tiny"
                      type="primary"
                      aria-label="重试文档处理"
                      @click="handleRetryChunk(doc.doc_id)"
                    >
                      重试
                    </n-button>
                  </n-space>
                </div>
                <n-popconfirm @positive-click="handleDeleteDoc(doc.doc_id)">
                  <template #trigger>
                    <n-button size="tiny" quaternary type="error">删除</n-button>
                  </template>
                  确定删除？
                </n-popconfirm>
              </div>
            </div>
          </div>

          <div v-if="docTotal > docPageSize" style="margin-top: 16px; display: flex; justify-content: flex-end">
            <n-pagination
              :page="docPage"
              :page-size="docPageSize"
              :item-count="docTotal"
              @update:page="handlePageChange"
            />
          </div>
        </n-card>
      </template>
    </n-spin>

    <!-- 创建知识库弹窗 -->
    <n-modal v-model:show="showCreateModal">
      <n-card title="创建个人知识库" :bordered="false" style="width: 450px; max-width: 95vw" closable @close="showCreateModal = false">
        <n-form>
          <n-form-item label="名称" required>
            <n-input v-model:value="createForm.name" placeholder="我的知识库" maxlength="200" />
          </n-form-item>
          <n-form-item label="描述">
            <n-input v-model:value="createForm.description" type="textarea" placeholder="可选" maxlength="1000" :rows="3" />
          </n-form-item>
        </n-form>
        <template #footer>
          <n-space justify="end">
            <n-button @click="showCreateModal = false">取消</n-button>
            <n-button type="primary" :loading="formLoading" @click="handleCreate">创建</n-button>
          </n-space>
        </template>
      </n-card>
    </n-modal>

    <!-- 编辑知识库弹窗 -->
    <n-modal v-model:show="showEditModal">
      <n-card title="编辑知识库" :bordered="false" style="width: 450px; max-width: 95vw" closable @close="showEditModal = false">
        <n-form>
          <n-form-item label="名称" required>
            <n-input v-model:value="editForm.name" maxlength="200" />
          </n-form-item>
          <n-form-item label="描述">
            <n-input v-model:value="editForm.description" type="textarea" maxlength="1000" :rows="3" />
          </n-form-item>
        </n-form>
        <template #footer>
          <n-space justify="end">
            <n-button @click="showEditModal = false">取消</n-button>
            <n-button type="primary" :loading="formLoading" @click="handleUpdate">保存</n-button>
          </n-space>
        </template>
      </n-card>
    </n-modal>

    <!-- 上传组件 -->
    <DocumentUploader
      :visible="showUploader"
      @update:visible="showUploader = $event"
      @uploaded="handleUploaded"
    />
  </div>
</template>

<style scoped>
.kb-container {
  max-width: 1000px;
  margin: 0 auto;
  padding: 16px;
}
.kb-mobile {
  padding: 8px;
}

</style>

<!-- 全局样式：DataTable render 函数内联动画需要全局 keyframes -->
<style>
@keyframes pulse-sq {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}
</style>
