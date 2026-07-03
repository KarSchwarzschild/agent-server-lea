<script setup lang="ts">
/**
 * 公共知识库管理页面 - Naive UI + vxe-table 版本
 * 支持批量选择和批量删除
 */
import { ref, reactive, onMounted, onUnmounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import {
  NCard,
  NButton,
  NSpace,
  NTag,
  NSelect,
  NPopconfirm,
  NPagination,
  NSpin,
  NModal,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NCheckbox,
  NAlert,
  useMessage,
} from 'naive-ui'
import { getDocuments, deleteDocument, batchDeleteDocuments, updateClassification, buildPageIndex, type Document, type ClassificationUpdate } from '../api/documents'

const router = useRouter()
const message = useMessage()

// 表格数据
const tableData = ref<Document[]>([])
const loading = ref(false)
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(20)

// 批量选择
const selectedDocIds = ref<string[]>([])
const isAllSelected = computed(() => {
  return (tableData.value?.length ?? 0) > 0 && selectedDocIds.value.length === tableData.value.length
})
const hasSelection = computed(() => selectedDocIds.value.length > 0)

// 批量删除弹窗
const showBatchDeleteModal = ref(false)
const batchDeleteLoading = ref(false)
const batchHardDelete = ref(false)

// 动态计算表格高度
const tableHeight = ref(400)

function calcTableHeight() {
  // 视口高度 - 顶部header(64) - 内容区padding(48) - 卡片header(约60) - 筛选栏(约50) - 批量操作栏(约50) - 分页(约50) - 安全边距(20)
  const extraHeight = hasSelection.value ? 50 : 0
  const height = window.innerHeight - 64 - 48 - 60 - 50 - extraHeight - 50 - 20
  tableHeight.value = Math.max(height, 300) // 最小300px
}

onMounted(() => {
  calcTableHeight()
  window.addEventListener('resize', calcTableHeight)
  loadData()
})

onUnmounted(() => {
  window.removeEventListener('resize', calcTableHeight)
})

// 编辑弹窗
const showEditModal = ref(false)
const editLoading = ref(false)
const editingDoc = ref<Document | null>(null)
const editForm = reactive<ClassificationUpdate & { title?: string; authors?: string; year?: number }>({
  category: '',
  sub_category: '',
  methodology: '',
  keywords: '',
  title: '',
  authors: '',
  year: undefined,
})

// 筛选条件
const filters = reactive({
  category: null as string | null,
  status: 'active',
  year_from: null as number | null,
  year_to: null as number | null
})

// 分类选项
const categoryOptions = [
  { label: '全部分类', value: null },
  { label: '理论研究', value: '理论研究' },
  { label: '政策研究', value: '政策研究' },
  { label: '实践研究', value: '实践研究' },
  { label: '方法研究', value: '方法研究' },
] as const

// 状态选项
const statusOptions = [
  { label: '正常', value: 'active' },
  { label: '已删除', value: 'deleted' },
]

// 编辑表单分类选项
const editCategoryOptions = [
  { label: '理论研究', value: '理论研究' },
  { label: '政策研究', value: '政策研究' },
  { label: '实践研究', value: '实践研究' },
  { label: '方法研究', value: '方法研究' },
]

const subCategoryOptions = [
  { label: '老年教育', value: '老年教育' },
  { label: '社区教育', value: '社区教育' },
  { label: '职业继续教育', value: '职业继续教育' },
  { label: '开放远程教育', value: '开放远程教育' },
  { label: '农村成人教育', value: '农村成人教育' },
  { label: '终身学习体系', value: '终身学习体系' },
]

const methodologyOptions = [
  { label: '实证研究', value: '实证研究' },
  { label: '理论研究', value: '理论研究' },
  { label: '案例研究', value: '案例研究' },
  { label: '综述研究', value: '综述研究' },
  { label: '政策分析', value: '政策分析' },
  { label: '比较研究', value: '比较研究' },
  { label: '混合方法', value: '混合方法' },
]

// 年份选项（动态生成）
const currentYear = new Date().getFullYear()
const yearOptions = computed(() => {
  const options: Array<{ label: string; value: number | null }> = [{ label: '不限', value: null }]
  for (let y = currentYear; y >= 1990; y--) {
    options.push({ label: String(y), value: y })
  }
  return options
})

// 加载数据
async function loadData() {
  loading.value = true
  try {
    const response = await getDocuments({
      page: currentPage.value,
      page_size: pageSize.value,
      category: filters.category || undefined,
      status: filters.status,
      year_from: filters.year_from || undefined,
      year_to: filters.year_to || undefined,
    })
    tableData.value = response.documents
    total.value = response.total
    // 清空选择
    selectedDocIds.value = []
  } catch (error) {
    console.error('Failed to load documents:', error)
    message.error('加载文献列表失败')
  } finally {
    loading.value = false
  }
}

// 搜索
function handleSearch() {
  currentPage.value = 1
  loadData()
}

// 重置
function handleReset() {
  filters.category = null
  filters.status = 'active'
  filters.year_from = null
  filters.year_to = null
  currentPage.value = 1
  loadData()
}

// 分页
function handlePageChange(page: number) {
  currentPage.value = page
  loadData()
}

// 单个选择
function toggleSelect(docId: string) {
  const index = selectedDocIds.value.indexOf(docId)
  if (index === -1) {
    selectedDocIds.value.push(docId)
  } else {
    selectedDocIds.value.splice(index, 1)
  }
  calcTableHeight()
}

// 全选/取消全选
function toggleSelectAll() {
  if (isAllSelected.value) {
    selectedDocIds.value = []
  } else {
    selectedDocIds.value = tableData.value.map(d => d.doc_id)
  }
  calcTableHeight()
}

// 取消选择
function clearSelection() {
  selectedDocIds.value = []
  calcTableHeight()
}

// 单个删除
async function handleDelete(row: Document) {
  try {
    const response = await deleteDocument(row.doc_id)
    if (response.warnings && response.warnings.length > 0) {
      message.warning(`删除成功，但有警告：${response.warnings.join('; ')}`)
    } else {
      message.success('删除成功')
    }
    loadData()
  } catch (error: unknown) {
    const err = error as { response?: { data?: { detail?: string } } }
    message.error(err?.response?.data?.detail || '删除失败')
  }
}

// 单个文档索引
const indexingDocId = ref('')
async function handleBuildIndex(docId: string) {
  indexingDocId.value = docId
  try {
    const res = await buildPageIndex([docId])
    message.success(res.message)
    loadData()
  } catch (error: any) {
    message.error(error?.response?.data?.detail || '索引请求失败')
  } finally {
    indexingDocId.value = ''
  }
}

// 批量索引
const batchIndexing = ref(false)
async function handleBatchBuildIndex() {
  if (selectedDocIds.value.length === 0) {
    message.warning('请先选择要索引的文件')
    return
  }
  batchIndexing.value = true
  try {
    const res = await buildPageIndex(selectedDocIds.value)
    message.success(res.message)
    loadData()
  } catch (error: any) {
    message.error(error?.response?.data?.detail || '批量索引失败')
  } finally {
    batchIndexing.value = false
  }
}

// 打开批量删除弹窗
function openBatchDeleteModal() {
  if (selectedDocIds.value.length === 0) {
    message.warning('请先选择要删除的文献')
    return
  }
  batchHardDelete.value = false
  showBatchDeleteModal.value = true
}

// 执行批量删除
async function handleBatchDelete() {
  if (selectedDocIds.value.length === 0) return

  batchDeleteLoading.value = true
  try {
    const response = await batchDeleteDocuments({
      doc_ids: selectedDocIds.value,
      hard_delete: batchHardDelete.value
    })

    // 显示结果
    if (response.failed > 0) {
      message.warning(response.message)
    } else {
      message.success(response.message)
    }

    showBatchDeleteModal.value = false
    loadData()
  } catch (error: unknown) {
    const err = error as { response?: { data?: { detail?: string } } }
    message.error(err?.response?.data?.detail || '批量删除失败')
  } finally {
    batchDeleteLoading.value = false
  }
}

// 打开编辑弹窗
function handleEdit(row: Document) {
  editingDoc.value = row
  editForm.category = row.category || ''
  editForm.sub_category = row.sub_category || ''
  editForm.methodology = row.methodology || ''
  editForm.keywords = row.keywords || ''
  editForm.title = row.title || ''
  editForm.authors = row.authors || ''
  editForm.year = row.year ?? undefined
  showEditModal.value = true
}

// 保存编辑
async function handleSaveEdit() {
  if (!editingDoc.value) return

  editLoading.value = true
  try {
    await updateClassification(editingDoc.value.doc_id, {
      title: editForm.title || undefined,
      authors: editForm.authors || undefined,
      year: editForm.year || undefined,
      category: editForm.category || undefined,
      sub_category: editForm.sub_category || undefined,
      methodology: editForm.methodology || undefined,
      keywords: editForm.keywords || undefined,
    })
    message.success('保存成功')
    showEditModal.value = false
    loadData()
  } catch (error: unknown) {
    const err = error as { response?: { data?: { detail?: string } } }
    message.error(err?.response?.data?.detail || '保存失败')
  } finally {
    editLoading.value = false
  }
}

// 跳转上传
function goUpload() {
  router.push('/documents/upload')
}

// 格式化时间
function formatTime(time: string | null): string {
  if (!time) return '-'
  return new Date(time).toLocaleDateString()
}

// 判断是否选中
function isSelected(docId: string): boolean {
  return selectedDocIds.value.includes(docId)
}
</script>

<template>
  <div class="documents-page">
    <!-- 主卡片 -->
    <n-card title="公共知识库管理">
      <!-- 筛选栏 -->
      <template #header-extra>
        <n-space>
          <n-select
            v-model:value="filters.category"
            :options="(categoryOptions as any)"
            placeholder="分类"
            clearable
            style="width: 140px"
            @update:value="handleSearch"
          />
          <n-select
            v-model:value="filters.status"
            :options="statusOptions"
            placeholder="状态"
            style="width: 100px"
            @update:value="handleSearch"
          />
          <n-select
            v-model:value="filters.year_from"
            :options="(yearOptions as any)"
            placeholder="起始年份"
            clearable
            style="width: 110px"
            @update:value="handleSearch"
          />
          <span style="line-height: 34px">至</span>
          <n-select
            v-model:value="filters.year_to"
            :options="(yearOptions as any)"
            placeholder="结束年份"
            clearable
            style="width: 110px"
            @update:value="handleSearch"
          />
          <n-button @click="handleReset">重置</n-button>
          <n-button type="primary" @click="goUpload">上传文件</n-button>
        </n-space>
      </template>

      <!-- 批量操作栏 -->
      <div v-if="hasSelection" class="batch-action-bar">
        <n-space align="center">
          <n-checkbox :checked="isAllSelected" @update:checked="toggleSelectAll">
            已选择 {{ selectedDocIds.length }} 项
          </n-checkbox>
          <n-button type="primary" size="small" :loading="batchIndexing" @click="handleBatchBuildIndex">批量索引</n-button>
          <n-button type="error" size="small" @click="openBatchDeleteModal">批量删除</n-button>
          <n-button size="small" @click="clearSelection">取消选择</n-button>
        </n-space>
      </div>

      <!-- 表格 -->
      <n-spin :show="loading">
        <vxe-table
          :data="tableData"
          stripe
          border
          round
          auto-resize
          :height="tableHeight"
        >
        <!-- 勾选列 -->
        <vxe-column title="" width="50" align="center">
          <template #header>
            <n-checkbox :checked="isAllSelected" @update:checked="toggleSelectAll" />
          </template>
          <template #default="{ row }">
            <n-checkbox :checked="isSelected(row.doc_id)" @update:checked="toggleSelect(row.doc_id)" />
          </template>
        </vxe-column>
        <vxe-column title="标题" min-width="200">
          <template #default="{ row }">
            {{ row.title || row.filename || '-' }}
          </template>
        </vxe-column>
        <vxe-column field="authors" title="作者" width="120" />
        <vxe-column field="year" title="年份" width="80" align="center" />
        <vxe-column title="分类" width="100">
          <template #default="{ row }">
            <n-tag v-if="row.category" size="small">{{ row.category }}</n-tag>
            <span v-else class="text-gray">未分类</span>
          </template>
        </vxe-column>
        <vxe-column title="子领域" width="100">
          <template #default="{ row }">
            <n-tag v-if="row.sub_category" size="small" :bordered="false">{{ row.sub_category }}</n-tag>
            <span v-else class="text-gray">-</span>
          </template>
        </vxe-column>
        <vxe-column title="方法" width="100">
          <template #default="{ row }">
            <n-tag v-if="row.methodology" size="small" type="info" :bordered="false">{{ row.methodology }}</n-tag>
            <span v-else class="text-gray">-</span>
          </template>
        </vxe-column>
        <vxe-column title="状态" width="80" align="center">
          <template #default="{ row }">
            <n-tag :type="row.status === 'active' ? 'success' : 'default'" size="small">
              {{ row.status === 'active' ? '正常' : '已删除' }}
            </n-tag>
          </template>
        </vxe-column>
        <vxe-column title="分类确认" width="90" align="center">
          <template #default="{ row }">
            <n-tag :type="row.classification_confirmed ? 'success' : 'warning'" size="small">
              {{ row.classification_confirmed ? '已确认' : '待确认' }}
            </n-tag>
          </template>
        </vxe-column>
        <vxe-column title="精读索引" width="90" align="center">
          <template #default="{ row }">
            <n-tag
              :type="row.pageindex_status === 'completed' ? 'success' : row.pageindex_status === 'failed' ? 'error' : row.pageindex_status === 'processing' ? 'warning' : row.pageindex_status === 'no_file' ? 'default' : 'default'"
              size="small"
            >
              {{ row.pageindex_status === 'completed' ? '可精读' : row.pageindex_status === 'failed' ? '索引失败' : row.pageindex_status === 'processing' ? '索引中...' : row.pageindex_status === 'no_file' ? '无文件' : '待索引' }}
            </n-tag>
          </template>
        </vxe-column>
        <vxe-column title="创建时间" width="110">
          <template #default="{ row }">
            {{ formatTime(row.created_at) }}
          </template>
        </vxe-column>
        <vxe-column title="操作" width="200" fixed="right">
          <template #default="{ row }">
            <n-space>
              <n-button size="small" type="primary" @click="handleEdit(row)">编辑</n-button>
              <n-button
                v-if="row.pageindex_status !== 'completed' && row.pageindex_status !== 'processing' && row.pageindex_status !== 'no_file'"
                size="small"
                :loading="indexingDocId === row.doc_id"
                @click="handleBuildIndex(row.doc_id)"
              >{{ row.pageindex_status === 'failed' ? '重试' : '索引' }}</n-button>
              <n-popconfirm @positive-click="handleDelete(row)">
                <template #trigger>
                  <n-button size="small" type="error">删除</n-button>
                </template>
                确定要删除这篇文献吗？删除后将同步移除知识库中的数据。
              </n-popconfirm>
            </n-space>
          </template>
        </vxe-column>
      </vxe-table>
      </n-spin>

      <!-- 分页 -->
      <div style="display: flex; justify-content: flex-end; margin-top: 16px">
        <n-pagination
          v-model:page="currentPage"
          :page-size="pageSize"
          :item-count="total"
          @update:page="handlePageChange"
        />
      </div>
    </n-card>

    <!-- 编辑弹窗 -->
    <n-modal
      v-model:show="showEditModal"
      preset="dialog"
      title="编辑文献分类"
      :style="{ width: '600px' }"
    >
      <n-form label-placement="left" label-width="80px" :model="editForm">
        <n-form-item label="标题">
          <n-input v-model:value="editForm.title" placeholder="文献标题" />
        </n-form-item>
        <n-form-item label="作者">
          <n-input v-model:value="editForm.authors" placeholder="作者" />
        </n-form-item>
        <n-form-item label="年份">
          <n-input-number v-model:value="editForm.year" placeholder="年份" :min="1900" :max="2100" style="width: 100%" />
        </n-form-item>
        <n-form-item label="研究领域">
          <n-select
            v-model:value="editForm.category"
            :options="editCategoryOptions"
            placeholder="请选择研究领域"
            clearable
          />
        </n-form-item>
        <n-form-item label="子领域">
          <n-select
            v-model:value="editForm.sub_category"
            :options="subCategoryOptions"
            placeholder="请选择子领域"
            clearable
          />
        </n-form-item>
        <n-form-item label="研究方法">
          <n-select
            v-model:value="editForm.methodology"
            :options="methodologyOptions"
            placeholder="请选择研究方法"
            clearable
          />
        </n-form-item>
        <n-form-item label="关键词">
          <n-input
            v-model:value="editForm.keywords"
            type="textarea"
            placeholder="多个关键词用逗号分隔"
            :rows="2"
          />
        </n-form-item>
      </n-form>
      <template #action>
        <n-space>
          <n-button @click="showEditModal = false">取消</n-button>
          <n-button type="primary" :loading="editLoading" @click="handleSaveEdit">保存</n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 批量删除确认弹窗 -->
    <n-modal
      v-model:show="showBatchDeleteModal"
      preset="dialog"
      title="确认批量删除"
      :style="{ width: '450px' }"
    >
      <n-alert type="warning" :show-icon="true" style="margin-bottom: 16px">
        确定要删除选中的 <strong>{{ selectedDocIds.length }}</strong> 篇文献吗？
      </n-alert>
      <p style="margin-bottom: 12px; color: #666;">删除后将同时移除：</p>
      <ul style="margin: 0 0 16px 20px; color: #666; line-height: 1.8;">
        <li>本地数据库记录</li>
        <li>智能基座知识库中的数据集及其切片</li>
      </ul>
      <n-checkbox v-model:checked="batchHardDelete">
        彻底删除（不可恢复，同时删除本地文件）
      </n-checkbox>
      <template #action>
        <n-space>
          <n-button @click="showBatchDeleteModal = false">取消</n-button>
          <n-button type="error" :loading="batchDeleteLoading" @click="handleBatchDelete">
            确认删除
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.documents-page {
  min-height: 100%;
}

.text-gray {
  color: #999;
  font-size: 12px;
}

.batch-action-bar {
  background: #f5f7fa;
  padding: 10px 16px;
  margin-bottom: 12px;
  border-radius: 4px;
  border: 1px solid #e4e7ed;
}
</style>
