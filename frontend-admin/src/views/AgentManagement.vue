<script setup lang="ts">
/**
 * 智能体管理页面
 *
 * 功能：
 * 1. 统计卡片（按分类显示智能体数量）
 * 2. 筛选搜索（分类、状态、关键词）
 * 3. CRUD操作（新增、编辑、删除、启用/禁用）
 * 4. 基座同步（手动同步、状态显示）
 * 5. 动态颜色映射（6色颜色池）
 */
import { ref, reactive, onMounted, computed, h } from 'vue'
import {
  NCard,
  NButton,
  NSpace,
  NTag,
  NModal,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NSelect,
  NPopconfirm,
  NGrid,
  NGi,
  NSpin,
  NPagination,
  NEmpty,
  NTooltip,
  NStatistic,
  NText,
  NDescriptions,
  NDescriptionsItem,
  useMessage,
  type FormInst,
  type FormRules,
  type SelectOption,
} from 'naive-ui'
import {
  getAgents,
  getAgentCategories,
  createAgent,
  updateAgent,
  deleteAgent,
  syncAgent,
  batchSyncAgents,
  AGENT_STATUS_MAP,
  SYNC_STATUS_MAP,
  SYNC_STATUS_ICON,
  JIZUO_APP_STATUS_ICON,
  type Agent,
  type AgentListParams,
} from '../api/agents'
import { getKnowledgeBases, type KnowledgeBase } from '../api/knowledge-bases'

const message = useMessage()
const formRef = ref<FormInst | null>(null)

// ========== 颜色池（6色循环映射） ==========
const CATEGORY_COLOR_POOL = [
  { primary: '#1890ff', light: '#e6f7ff', dark: '#096dd9' },  // 蓝色 - 索引0
  { primary: '#fa8c16', light: '#fff7e6', dark: '#d46b08' },  // 橙色 - 索引1
  { primary: '#52c41a', light: '#f6ffed', dark: '#389e0d' },  // 绿色 - 索引2
  { primary: '#13c2c2', light: '#e6fffb', dark: '#08979c' },  // 青色 - 索引3
  { primary: '#eb2f96', light: '#fff0f6', dark: '#c41d7f' },  // 粉色 - 索引4
  { primary: '#722ed1', light: '#f9f0ff', dark: '#531dab' },  // 紫色 - 索引5
]

// 默认灰色（分类不存在时）
const DEFAULT_COLORS = { primary: '#8c8c8c', light: '#f5f5f5', dark: '#595959' }

// 动态获取分类颜色
function getCategoryColors(category: string) {
  const index = categories.value.indexOf(category)
  if (index === -1) {
    return DEFAULT_COLORS
  }
  return CATEGORY_COLOR_POOL[index % CATEGORY_COLOR_POOL.length] || DEFAULT_COLORS
}

// 获取分类颜色（按索引）
function getCategoryColorByIndex(index: number) {
  return CATEGORY_COLOR_POOL[index % CATEGORY_COLOR_POOL.length] || DEFAULT_COLORS
}

// ========== 表格数据 ==========
const tableData = ref<Agent[]>([])
const loading = ref(false)
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(20)

// ========== 分类列表 ==========
const categories = ref<string[]>([])

// ========== 知识库列表 ==========
const knowledgeBases = ref<KnowledgeBase[]>([])
const loadingKbs = ref(false)

// 知识库选项（用于NSelect）
const knowledgeBaseOptions = computed(() => {
  return knowledgeBases.value.map(kb => ({
    label: kb.name,
    value: kb.kb_id,
    description: kb.description || ''
  }))
})

// ========== 统计数据 ==========
const categoryStats = computed(() => {
  const stats: Record<string, { total: number; active: number }> = {}
  // 初始化所有分类
  categories.value.forEach(cat => {
    stats[cat] = { total: 0, active: 0 }
  })
  // 统计
  tableData.value.forEach(agent => {
    const catStats = stats[agent.category]
    if (catStats) {
      catStats.total++
      if (agent.status === 'active') {
        catStats.active++
      }
    }
  })
  return stats
})

// ========== 筛选条件 ==========
const filters = reactive({
  search: '',
  category: '' as string,
  status: '' as string
})

// 筛选选项
const categoryOptions = computed<SelectOption[]>(() => [
  { label: '全部分类', value: '' },
  ...categories.value.map(c => ({ label: c, value: c }))
])

const statusOptions: SelectOption[] = [
  { label: '全部状态', value: '' },
  { label: '已启用', value: 'active' },
  { label: '已禁用', value: 'disabled' },
]

// ========== 弹窗状态 ==========
const showModal = ref(false)
const modalTitle = ref('新增智能体')
const editingAgent = ref<Agent | null>(null)
const submitLoading = ref(false)

// ========== 表单数据 ==========
const formData = reactive({
  agent_id: '',
  name: '',
  icon: '🤖',
  category: '',
  description: '',
  system_prompt: '',
  kb_ids: [] as string[],
  sort_order: 0,
})

// 表单验证规则（放宽agent_id格式）
const rules: FormRules = {
  agent_id: [
    { required: true, message: '请输入智能体ID', trigger: 'blur' },
    { pattern: /^[a-zA-Z][a-zA-Z0-9_-]{1,48}$/, message: '格式：字母开头，2-50字符（字母数字下划线短横线）', trigger: 'blur' },
  ],
  name: [
    { required: true, message: '请输入智能体名称', trigger: 'blur' },
    { min: 2, max: 100, message: '名称2-100个字符', trigger: 'blur' },
  ],
  category: [
    { required: true, message: '请选择分类', trigger: 'change' },
  ],
  system_prompt: [
    { required: true, message: '请输入系统提示词', trigger: 'blur' },
    { min: 10, message: '提示词至少10个字符', trigger: 'blur' },
  ],
}

// ========== 数据加载 ==========

// 加载分类列表
async function loadCategories() {
  try {
    categories.value = await getAgentCategories()
  } catch (error) {
    console.error('Failed to load categories:', error)
    // 降级到默认值
    categories.value = ['通用问答', '研究辅助', '写作支持', '分析评估']
  }
}

// 加载知识库列表
async function loadKnowledgeBases() {
  loadingKbs.value = true
  try {
    const response = await getKnowledgeBases()
    knowledgeBases.value = response.items
  } catch (error) {
    console.error('Failed to load knowledge bases:', error)
    message.error('加载知识库列表失败')
  } finally {
    loadingKbs.value = false
  }
}

// 自定义渲染：下拉选项显示 "名称 + ID后缀"
function renderKbLabel(option: SelectOption) {
  return h(NSpace, { vertical: true, size: 'small' }, {
    default: () => [
      h(NText, { strong: true }, { default: () => option.label }),
      h(NText, { depth: 3, style: { fontSize: '12px' } }, {
        default: () => `ID: ${option.value}`
      })
    ]
  })
}

// 自定义标签渲染：已选择的标签显示名称（不显示ID）
function renderKbTag({ option, handleClose }: { option: SelectOption; handleClose: () => void }) {
  return h(
    NTag,
    {
      type: 'info',
      closable: true,
      onClose: (e: MouseEvent) => {
        e.stopPropagation()
        handleClose()
      }
    },
    { default: () => option.label }
  )
}

// 加载智能体列表
async function loadData() {
  loading.value = true
  try {
    const params: AgentListParams = {
      page: currentPage.value,
      page_size: pageSize.value,
      search: filters.search || undefined,
      category: filters.category || undefined,
      status: filters.status || undefined,
    }
    const response = await getAgents(params)
    tableData.value = response.items
    total.value = response.total
  } catch (error) {
    console.error('Failed to load agents:', error)
    message.error('加载智能体列表失败')
  } finally {
    loading.value = false
  }
}

// ========== 筛选操作 ==========

function handleSearch() {
  currentPage.value = 1
  loadData()
}

function handleReset() {
  filters.search = ''
  filters.category = ''
  filters.status = ''
  currentPage.value = 1
  loadData()
}

function handlePageChange(page: number) {
  currentPage.value = page
  loadData()
}

// ========== 弹窗操作 ==========

// 打开新增弹窗
function handleAdd() {
  modalTitle.value = '新增智能体'
  editingAgent.value = null
  Object.assign(formData, {
    agent_id: '',
    name: '',
    icon: '🤖',
    category: '',
    description: '',
    system_prompt: '',
    kb_ids: [],
    sort_order: 0,
  })
  showModal.value = true
}

// 打开编辑弹窗
function handleEdit(row: Agent) {
  modalTitle.value = '编辑智能体'
  editingAgent.value = row
  Object.assign(formData, {
    agent_id: row.agent_id,
    name: row.name,
    icon: row.icon || '🤖',
    category: row.category,
    description: row.description || '',
    system_prompt: row.system_prompt,
    kb_ids: row.kb_ids || [],
    sort_order: row.sort_order,
  })
  showModal.value = true
}

// 提交表单
async function handleSubmit() {
  try {
    await formRef.value?.validate()
    submitLoading.value = true

    if (editingAgent.value) {
      // 更新
      await updateAgent(editingAgent.value.agent_id, {
        name: formData.name,
        icon: formData.icon,
        category: formData.category,
        description: formData.description,
        system_prompt: formData.system_prompt,
        kb_ids: formData.kb_ids,
        sort_order: formData.sort_order,
      })
      message.success('更新成功')
    } else {
      // 创建
      await createAgent({
        agent_id: formData.agent_id,
        name: formData.name,
        icon: formData.icon,
        category: formData.category,
        description: formData.description,
        system_prompt: formData.system_prompt,
        kb_ids: formData.kb_ids,
        sort_order: formData.sort_order,
      })
      message.success('创建成功，基座应用已自动创建')
    }

    showModal.value = false
    loadData()
  } catch (error: any) {
    if (error?.errors) {
      // 验证错误
      return
    }
    console.error('Failed to submit:', error)
    message.error(error?.message || '操作失败')
  } finally {
    submitLoading.value = false
  }
}

// ========== 表格操作 ==========

// 删除智能体
async function handleDelete(row: Agent) {
  try {
    await deleteAgent(row.agent_id)
    message.success('删除成功')
    loadData()
  } catch (error: any) {
    console.error('Failed to delete:', error)
    message.error(error?.message || '删除失败')
  }
}

// 启用/禁用智能体
async function handleToggleStatus(row: Agent) {
  const newStatus = row.status === 'active' ? 'disabled' : 'active'
  try {
    await updateAgent(row.agent_id, { status: newStatus })
    message.success(newStatus === 'active' ? '已启用' : '已禁用')
    loadData()
  } catch (error: any) {
    console.error('Failed to toggle status:', error)
    message.error(error?.message || '操作失败')
  }
}

// 手动同步
async function handleSync(row: Agent) {
  try {
    await syncAgent(row.agent_id)
    message.success('同步成功')
    loadData()
  } catch (error: any) {
    console.error('Failed to sync:', error)
    message.error(error?.message || '同步失败')
  }
}

// 批量同步状态
const batchSyncing = ref(false)

// 批量同步
async function handleBatchSync() {
  batchSyncing.value = true
  try {
    const response = await batchSyncAgents()
    const { results } = response

    // 显示同步结果
    if (results.failed.length === 0 && results.skipped.length === 0) {
      message.success(`批量同步完成: 全部 ${results.success.length} 个智能体同步成功`)
    } else {
      const successCount = results.success.length
      const failedCount = results.failed.length
      const skippedCount = results.skipped.length

      let msg = `批量同步完成: 成功 ${successCount}`
      if (failedCount > 0) {
        msg += `, 失败 ${failedCount}`
      }
      if (skippedCount > 0) {
        msg += `, 跳过 ${skippedCount}`
      }

      if (failedCount > 0) {
        message.warning(msg)
        console.error('Failed agents:', results.failed)
      } else {
        message.success(msg)
      }
    }

    // 刷新列表
    await loadData()
  } catch (error: any) {
    console.error('Batch sync failed:', error)
    message.error(error?.message || '批量同步失败')
  } finally {
    batchSyncing.value = false
  }
}

// ========== 工具函数 ==========

// 格式化日期
function formatDate(dateStr: string | null | undefined): string {
  if (!dateStr) return '-'
  const date = new Date(dateStr)
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

// 密钥脱敏显示
function maskSecret(secret: string | null | undefined): string {
  if (!secret) return '-'
  if (secret.length <= 8) return '****'
  return secret.slice(0, 4) + '****' + secret.slice(-4)
}

// ========== 生命周期 ==========
onMounted(() => {
  loadCategories()
  loadKnowledgeBases()  // ✅ 加载知识库列表
  loadData()
})
</script>

<template>
  <div class="agent-management">
    <!-- 顶部筛选栏 -->
    <NCard title="智能体管理" :bordered="false" class="mb-4">
      <template #header-extra>
        <NSpace>
          <NButton
            type="info"
            :loading="batchSyncing"
            @click="handleBatchSync"
          >
            🔄 批量同步
          </NButton>
          <NButton type="success" @click="handleAdd">
            + 新增智能体
          </NButton>
        </NSpace>
      </template>

      <NSpace vertical :size="16">
        <!-- 统计卡片 -->
        <NGrid :x-gap="16" :y-gap="16" :cols="categories.length || 4">
          <NGi v-for="(cat, index) in categories" :key="cat">
            <NCard
              size="small"
              :style="{
                borderLeft: `4px solid ${getCategoryColorByIndex(index).primary}`,
                backgroundColor: getCategoryColorByIndex(index).light,
              }"
            >
              <NStatistic :label="cat">
                <template #default>
                  <span :style="{ color: getCategoryColorByIndex(index).primary }">
                    {{ categoryStats[cat]?.total ?? 0 }}
                  </span>
                </template>
                <template #suffix>
                  <span style="font-size: 12px; color: #666; margin-left: 8px;">
                    启用 {{ categoryStats[cat]?.active ?? 0 }}
                  </span>
                </template>
              </NStatistic>
            </NCard>
          </NGi>
        </NGrid>

        <!-- 筛选器 -->
        <NGrid :x-gap="12" :y-gap="12" :cols="24">
          <NGi :span="6">
            <NInput
              v-model:value="filters.search"
              placeholder="搜索名称、描述、ID"
              clearable
              @keyup.enter="handleSearch"
            />
          </NGi>
          <NGi :span="4">
            <NSelect
              v-model:value="filters.category"
              :options="categoryOptions"
              placeholder="选择分类"
              clearable
            />
          </NGi>
          <NGi :span="4">
            <NSelect
              v-model:value="filters.status"
              :options="statusOptions"
              placeholder="选择状态"
              clearable
            />
          </NGi>
          <NGi :span="10">
            <NSpace>
              <NButton type="primary" @click="handleSearch">搜索</NButton>
              <NButton @click="handleReset">重置</NButton>
            </NSpace>
          </NGi>
        </NGrid>
      </NSpace>
    </NCard>

    <!-- 表格 -->
    <NCard :bordered="false">
      <NSpin :show="loading">
        <div v-if="tableData.length > 0">
          <div class="table-container">
            <table class="data-table">
              <thead>
                <tr>
                  <th width="60">图标</th>
                  <th width="150">名称</th>
                  <th width="100">分类</th>
                  <th>描述</th>
                  <th width="100">同步状态</th>
                  <th width="80">状态</th>
                  <th width="220">操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in tableData" :key="row.id">
                  <td class="text-center text-xl">{{ row.icon || '🤖' }}</td>
                  <td>
                    <div class="agent-name">{{ row.name }}</div>
                    <div class="agent-id">{{ row.agent_id }}</div>
                  </td>
                  <td>
                    <NTag
                      :color="{
                        color: getCategoryColors(row.category).light,
                        textColor: getCategoryColors(row.category).primary,
                        borderColor: 'transparent'
                      }"
                    >
                      {{ row.category }}
                    </NTag>
                  </td>
                  <td>
                    <div class="description-cell">{{ row.description || '-' }}</div>
                  </td>
                  <td class="text-center">
                    <NTooltip v-if="row.jizuo_sync_error" placement="top">
                      <template #trigger>
                        <span class="sync-status-error">
                          {{ SYNC_STATUS_ICON[row.jizuo_sync_status] }} {{ SYNC_STATUS_MAP[row.jizuo_sync_status] }}
                        </span>
                      </template>
                      {{ row.jizuo_sync_error }}
                    </NTooltip>
                    <span v-else>
                      {{ SYNC_STATUS_ICON[row.jizuo_sync_status] }} {{ SYNC_STATUS_MAP[row.jizuo_sync_status] }}
                    </span>
                  </td>
                  <td class="text-center">
                    <NTag :type="row.status === 'active' ? 'success' : 'default'" size="small">
                      {{ AGENT_STATUS_MAP[row.status] || row.status }}
                    </NTag>
                  </td>
                  <td>
                    <NSpace size="small">
                      <NButton size="small" @click="handleEdit(row)">编辑</NButton>
                      <NButton
                        size="small"
                        :type="row.status === 'active' ? 'warning' : 'success'"
                        @click="handleToggleStatus(row)"
                      >
                        {{ row.status === 'active' ? '禁用' : '启用' }}
                      </NButton>
                      <NButton
                        v-if="row.jizuo_app_id"
                        size="small"
                        type="info"
                        @click="handleSync(row)"
                      >
                        同步
                      </NButton>
                      <NPopconfirm
                        v-if="!row.is_system"
                        @positive-click="handleDelete(row)"
                      >
                        <template #trigger>
                          <NButton size="small" type="error">删除</NButton>
                        </template>
                        确认删除智能体"{{ row.name }}"？此操作将同时删除基座应用。
                      </NPopconfirm>
                    </NSpace>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <!-- 分页 -->
          <div class="pagination-container">
            <NPagination
              v-model:page="currentPage"
              :page-count="Math.ceil(total / pageSize)"
              :page-size="pageSize"
              show-size-picker
              :page-sizes="[10, 20, 50, 100]"
              @update:page="handlePageChange"
              @update:page-size="(val: number) => { pageSize = val; handlePageChange(1) }"
            >
              <template #prefix>
                共 {{ total }} 条
              </template>
            </NPagination>
          </div>
        </div>

        <NEmpty v-else description="暂无智能体数据" class="empty-state">
          <template #extra>
            <NButton type="primary" @click="handleAdd">创建第一个智能体</NButton>
          </template>
        </NEmpty>
      </NSpin>
    </NCard>

    <!-- 新增/编辑弹窗 -->
    <NModal
      v-model:show="showModal"
      :title="modalTitle"
      preset="dialog"
      :positive-text="editingAgent ? '更新' : '创建'"
      negative-text="取消"
      :loading="submitLoading"
      @positive-click="handleSubmit"
      style="width: 800px"
    >
      <NForm
        ref="formRef"
        :model="formData"
        :rules="rules"
        label-placement="left"
        label-width="120"
        require-mark-placement="left"
      >
        <!-- 基本信息 -->
        <div class="form-section">
          <div class="form-section-title">基本信息</div>

          <NFormItem label="智能体ID" path="agent_id">
            <NInput
              v-model:value="formData.agent_id"
              placeholder="如：general-qa、policy-analysis"
              :disabled="!!editingAgent"
            />
            <template #feedback>
              字母开头，支持字母、数字、下划线、短横线，2-50字符
            </template>
          </NFormItem>

          <NFormItem label="名称" path="name">
            <NInput v-model:value="formData.name" placeholder="终身教育智能问答助手" />
          </NFormItem>

          <NFormItem label="图标" path="icon">
            <NInput v-model:value="formData.icon" placeholder="🤖" maxlength="10" style="width: 100px" />
            <span style="margin-left: 12px; color: #666; font-size: 12px;">支持 Emoji 表情</span>
          </NFormItem>

          <NFormItem label="分类" path="category">
            <NSelect
              v-model:value="formData.category"
              :options="categories.map(c => ({ label: c, value: c }))"
              placeholder="选择分类"
            />
          </NFormItem>

          <NFormItem label="描述" path="description">
            <NInput
              v-model:value="formData.description"
              type="textarea"
              placeholder="智能体功能描述（显示在列表和选择器中）"
              :rows="2"
            />
          </NFormItem>
        </div>

        <!-- 系统提示词 -->
        <div class="form-section">
          <div class="form-section-title">系统提示词</div>

          <NFormItem label="提示词内容" path="system_prompt">
            <NInput
              v-model:value="formData.system_prompt"
              type="textarea"
              placeholder="你是一个终身教育研究助手，专注于...&#10;&#10;核心能力：&#10;1. ...&#10;2. ...&#10;&#10;行为准则：&#10;1. ..."
              :rows="10"
              :autosize="{ minRows: 8, maxRows: 20 }"
            />
          </NFormItem>
        </div>

        <!-- 知识库配置 -->
        <div class="form-section">
          <div class="form-section-title">知识库配置</div>

          <NFormItem label="关联知识库" path="kb_ids">
            <NSelect
              v-model:value="formData.kb_ids"
              multiple
              filterable
              :options="knowledgeBaseOptions"
              :loading="loadingKbs"
              placeholder="请选择知识库（可多选）"
              :render-label="renderKbLabel"
              :render-tag="renderKbTag"
            />
            <template #feedback>
              从下拉列表选择知识库，不可手动输入ID
            </template>
          </NFormItem>
        </div>

        <!-- 其他配置 -->
        <div class="form-section">
          <div class="form-section-title">其他配置</div>

          <NFormItem label="排序序号" path="sort_order">
            <NInputNumber
              v-model:value="formData.sort_order"
              :min="0"
              placeholder="0"
              style="width: 120px"
            />
            <span style="margin-left: 12px; color: #666; font-size: 12px;">数字越小排序越靠前</span>
          </NFormItem>
        </div>

        <!-- 只读字段（编辑模式显示） -->
        <template v-if="editingAgent">
          <div class="form-section">
            <div class="form-section-title">基座应用信息（只读）</div>

            <NDescriptions :column="2" label-placement="left" bordered size="small">
              <NDescriptionsItem label="应用ID">
                <code>{{ editingAgent.jizuo_app_id || '-' }}</code>
              </NDescriptionsItem>
              <NDescriptionsItem label="应用密钥">
                <NTooltip v-if="editingAgent.jizuo_app_key">
                  <template #trigger>
                    <code>{{ maskSecret(editingAgent.jizuo_app_key) }}</code>
                  </template>
                  {{ editingAgent.jizuo_app_key }}
                </NTooltip>
                <span v-else>-</span>
              </NDescriptionsItem>
              <NDescriptionsItem label="基座状态">
                <span v-if="editingAgent.jizuo_app_status">
                  {{ JIZUO_APP_STATUS_ICON[editingAgent.jizuo_app_status] }}
                  {{ editingAgent.jizuo_app_status }}
                </span>
                <span v-else>-</span>
              </NDescriptionsItem>
              <NDescriptionsItem label="同步状态">
                {{ SYNC_STATUS_ICON[editingAgent.jizuo_sync_status] }}
                {{ SYNC_STATUS_MAP[editingAgent.jizuo_sync_status] }}
              </NDescriptionsItem>
              <NDescriptionsItem label="基座创建时间">
                {{ formatDate(editingAgent.jizuo_created_at) }}
              </NDescriptionsItem>
              <NDescriptionsItem label="最后同步时间">
                {{ formatDate(editingAgent.jizuo_last_sync_at) }}
              </NDescriptionsItem>
              <NDescriptionsItem v-if="editingAgent.jizuo_sync_error" label="同步错误" :span="2">
                <span class="error-text">{{ editingAgent.jizuo_sync_error }}</span>
              </NDescriptionsItem>
            </NDescriptions>
          </div>
        </template>
      </NForm>
    </NModal>
  </div>
</template>

<style scoped>
.agent-management {
  padding: 20px;
}

.mb-4 {
  margin-bottom: 16px;
}

/* 表单分组样式 */
.form-section {
  margin-bottom: 24px;
}

.form-section-title {
  font-size: 14px;
  font-weight: 600;
  color: #333;
  margin-bottom: 16px;
  padding-bottom: 8px;
  border-bottom: 1px solid #f0f0f0;
}

/* 表格样式 */
.table-container {
  overflow-x: auto;
}

.data-table {
  width: 100%;
  border-collapse: collapse;
}

.data-table th,
.data-table td {
  padding: 12px;
  text-align: left;
  border-bottom: 1px solid #f0f0f0;
}

.data-table th {
  background-color: #fafafa;
  font-weight: 600;
  font-size: 13px;
}

.data-table tbody tr:hover {
  background-color: #fafafa;
}

.text-center {
  text-align: center !important;
}

.text-xl {
  font-size: 20px;
}

/* 智能体名称样式 */
.agent-name {
  font-weight: 500;
  color: #333;
}

.agent-id {
  font-size: 12px;
  color: #999;
  margin-top: 2px;
}

/* 描述单元格 */
.description-cell {
  max-width: 250px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #666;
}

/* 同步状态错误样式 */
.sync-status-error {
  color: #ff4d4f;
  cursor: help;
}

/* 分页容器 */
.pagination-container {
  margin-top: 16px;
  display: flex;
  justify-content: flex-end;
}

/* 空状态 */
.empty-state {
  padding: 60px 0;
}

/* 错误文本 */
.error-text {
  color: #ff4d4f;
  font-size: 12px;
  word-break: break-all;
}

/* 代码样式 */
code {
  background-color: #f5f5f5;
  padding: 2px 6px;
  border-radius: 3px;
  font-family: monospace;
  font-size: 12px;
}
</style>
