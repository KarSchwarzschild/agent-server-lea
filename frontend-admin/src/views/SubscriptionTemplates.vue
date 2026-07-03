<script setup lang="ts">
/**
 * 订阅模板管理页面 — 管理后台
 */
import { ref, computed, onMounted, h } from 'vue'
import {
  NCard,
  NDataTable,
  NTag,
  NSpace,
  NButton,
  NInput,
  NSelect,
  NModal,
  NForm,
  NFormItem,
  NSwitch,
  NPagination,
  NPopconfirm,
  NInputNumber,
  NDynamicTags,
  NIcon,
  NSpin,
  NAlert,
  NAutoComplete,
  NTooltip,
  useMessage,
  type DataTableColumns,
} from 'naive-ui'
import { AddOutline, RefreshOutline, SearchOutline } from '@vicons/ionicons5'
import {
  getTemplates,
  createTemplate,
  updateTemplate,
  deleteTemplate,
  getAdminBuiltinSources,
  previewAdminBuiltinSource,
  testAdminBuiltinSource,
  type SubscriptionTemplate,
  type TemplateCreateRequest,
  type AdminBuiltinSource,
  type TestSourceResponse,
  type PreviewSourceResponse,
} from '../api/subscription-templates'

const message = useMessage()

// ==================== 状态 ====================

const loading = ref(false)
const templates = ref<SubscriptionTemplate[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const filterCategory = ref<string | null>(null)
const searchText = ref('')

// 编辑弹窗
const showModal = ref(false)
const modalLoading = ref(false)
const isEdit = ref(false)
const editingId = ref<string | null>(null)
const form = ref<TemplateCreateRequest>({
  name: '',
  slug: '',
  category: 'custom',
  description: '',
  config_template: {},
  suggested_exec_mode: 'quick',
  suggested_schedule: '0 9 * * *',
  icon: '',
  is_public: true,
})
const configJson = ref('{}')

// ==================== 调度 & 工具编辑器 ====================

const scheduleType = ref('daily')
const scheduleTime = ref('09:00')

const scheduleOptions = [
  { label: '每天', value: 'daily' },
  { label: '工作日', value: 'weekday' },
  { label: '每周一', value: 'weekly' },
]

const timeOptions = [
  '06:00', '08:00', '09:00', '10:00', '11:00', '12:00', '14:00', '18:00', '20:00',
].map(t => ({ label: t, value: t }))

const scheduleToCron = (type: string, time: string) => {
  const [h, m] = time.split(':')
  const map: Record<string, string> = {
    daily: `${m} ${h} * * *`,
    weekday: `${m} ${h} * * 1-5`,
    weekly: `${m} ${h} * * 1`,
  }
  return map[type] || map.daily
}

const cronToSchedule = (cron: string) => {
  const parts = cron.trim().split(/\s+/)
  if (parts.length < 5) return { type: 'daily', time: '09:00' }
  const time = `${parts[1].padStart(2, '0')}:${parts[0].padStart(2, '0')}`
  let type = 'daily'
  if (parts[4] === '1-5') type = 'weekday'
  else if (parts[4] === '1') type = 'weekly'
  return { type, time }
}

// 工具编辑
const formTools = ref<Array<{ name: string; params: Record<string, any> }>>([])
const formKeywords = ref<string[]>([])

const toolTypeOptions = [
  { label: 'RSS Feed', value: 'rss_fetch' },
  { label: '学术搜索', value: 'academic_search' },
  { label: '网页搜索', value: 'web_search' },
  { label: 'GitHub 搜索', value: 'github_search_repos' },
  { label: '网页抓取', value: 'web_fetch' },
]

const sourceOptions = [
  { label: '智能多源 (自动)', value: 'auto' },
  { label: 'arXiv (理工科预印本)', value: 'arxiv' },
  { label: 'Semantic Scholar (英文综合)', value: 'semantic_scholar' },
  { label: '万方数据 (中文)', value: 'wanfang' },
  { label: 'OpenAlex (多学科开放)', value: 'openalex' },
  { label: 'CrossRef (DOI引用)', value: 'crossref' },
]

const addTool = () => {
  formTools.value.push({ name: 'web_search', params: { query: '' } })
}

const removeTool = (index: number) => {
  formTools.value.splice(index, 1)
}

const onToolTypeChange = (index: number, newType: string) => {
  const defaults: Record<string, Record<string, any>> = {
    rss_fetch: { url: '', max_items: 20 },
    academic_search: { query: '', source: 'auto' },
    web_search: { query: '' },
    github_search_repos: { query: '' },
    web_fetch: { url: '' },
  }
  formTools.value[index].params = { ...(defaults[newType] || {}) }
}

// ==================== 内置数据源选择器 ====================

const showBuiltinPicker = ref(false)
const builtinLoading = ref(false)
const builtinSources = ref<AdminBuiltinSource[]>([])
const builtinCategories = ref<{ value: string; label: string }[]>([])
const builtinActiveCategory = ref('all')
const builtinSelected = ref<Set<string>>(new Set())

// parameterized 源输入状态
const builtinParamInputs = ref<Record<string, {
  value: string
  testing: boolean
  result: TestSourceResponse | null
  error: string | null
}>>({})

// fixed 源预览状态
const previewStates = ref<Record<string, {
  loading: boolean
  data: PreviewSourceResponse | null
  expanded: boolean
}>>({})

const iconMap: Record<string, string> = {
  academic: '🎓', tech: '💻', trending: '🔥', industry: '📰', news: '📰', default: '📋',
}
const getIcon = (icon?: string) => iconMap[icon || 'default'] || iconMap.default

const loadBuiltinSources = async () => {
  builtinLoading.value = true
  try {
    const res = await getAdminBuiltinSources()
    builtinSources.value = res.sources
    builtinCategories.value = res.categories
  } catch {
    message.error('加载内置数据源失败')
  } finally {
    builtinLoading.value = false
  }
}

const filteredBuiltinSources = computed(() => {
  if (builtinActiveCategory.value === 'all') return builtinSources.value
  return builtinSources.value.filter(s => s.category === builtinActiveCategory.value)
})

const builtinFixedSources = computed(() => filteredBuiltinSources.value.filter(s => s.type === 'fixed'))
const builtinParamSources = computed(() => filteredBuiltinSources.value.filter(s => s.type === 'parameterized'))

// 检查 fixed 源是否已在 formTools 中
const isSourceAlreadyAdded = (source: AdminBuiltinSource) => {
  return formTools.value.some(t => t.params?.url === source.preview_url)
}

const toggleBuiltinFixed = (source: AdminBuiltinSource) => {
  if (isSourceAlreadyAdded(source)) return
  const set = new Set(builtinSelected.value)
  if (set.has(source.id)) {
    set.delete(source.id)
  } else {
    set.add(source.id)
  }
  builtinSelected.value = set
}

const getBuiltinParamInput = (sourceId: string) => {
  if (!builtinParamInputs.value[sourceId]) {
    builtinParamInputs.value[sourceId] = { value: '', testing: false, result: null, error: null }
  }
  return builtinParamInputs.value[sourceId]
}

// autocomplete 选中后提取纯代码值
const extractParamValue = (source: AdminBuiltinSource, raw: string): string => {
  const trimmed = raw.trim()
  if (source.input_type === 'autocomplete' && source.options?.length) {
    const match = trimmed.match(/[（(]([A-Z0-9]{2,10})[）)]/)
    if (match) return match[1]
    const opt = source.options.find(o => o.value === trimmed || o.label === trimmed)
    if (opt) return opt.value
  }
  return trimmed
}

// autocomplete 下拉选项
const getAutoCompleteOptions = (source: AdminBuiltinSource) => {
  const input = getBuiltinParamInput(source.id)
  const keyword = (input.value || '').trim().toUpperCase()
  if (!source.options) return []
  return source.options
    .filter(opt => {
      if (!keyword) return true
      return opt.value.includes(keyword) || opt.label.includes(input.value.trim())
    })
    .map(opt => ({
      label: `${opt.label}（${opt.value}）`,
      value: opt.value,
    }))
}

// parameterized 源：探测 + 添加
const handleBuiltinParamAdd = async (source: AdminBuiltinSource & { route_template?: string }) => {
  const input = getBuiltinParamInput(source.id)
  const actualValue = extractParamValue(source, input.value)
  if (!actualValue) {
    input.error = '请输入内容'
    return
  }
  if (source.validation_regex) {
    const regex = new RegExp(source.validation_regex)
    if (!regex.test(actualValue)) {
      input.error = source.validation_error || '格式不正确'
      return
    }
  }
  input.error = null
  input.testing = true
  input.result = null

  const paramKey = source.param_key || source.params?.[0]?.name || ''
  const params = paramKey ? { [paramKey]: actualValue } : {}

  try {
    const result = await testAdminBuiltinSource(source.id, params)
    input.result = result
    if (result.ok) {
      doAddParam(source, paramKey, actualValue)
      input.value = ''
      input.result = null
    }
  } catch {
    input.result = { ok: false, message: '服务暂不可用，请稍后重试' }
  } finally {
    input.testing = false
  }
}

// 降级添加（探测失败时仍然添加）
const handleForceAdd = (source: AdminBuiltinSource & { route_template?: string }) => {
  const input = getBuiltinParamInput(source.id)
  const actualValue = extractParamValue(source, input.value)
  const paramKey = source.param_key || source.params?.[0]?.name || ''
  doAddParam(source, paramKey, actualValue)
  input.value = ''
  input.result = null
  input.error = null
}

// 实际执行添加 parameterized 源到 formTools
const doAddParam = (source: AdminBuiltinSource & { route_template?: string }, paramKey: string, paramVal: string) => {
  const routeTemplate = (source as any).route_template || ''
  const route = routeTemplate ? routeTemplate.replace(`{${paramKey}}`, paramVal) : `/${source.id}/${paramVal}`
  formTools.value.push({
    name: 'rss_fetch',
    params: {
      url: `{RSSHUB_BASE_URL}${route}`,
      max_items: 15,
    },
  })
  message.success(`已添加: ${source.name} · ${paramVal}`)
}

// fixed 源预览
const getPreviewState = (sourceId: string) => {
  if (!previewStates.value[sourceId]) {
    previewStates.value[sourceId] = { loading: false, data: null, expanded: false }
  }
  return previewStates.value[sourceId]
}

const handlePreview = async (source: AdminBuiltinSource, event: MouseEvent) => {
  event.stopPropagation()
  const state = getPreviewState(source.id)
  if (state.data) {
    state.expanded = !state.expanded
    return
  }
  state.loading = true
  state.expanded = true
  try {
    state.data = await previewAdminBuiltinSource(source.id)
  } catch {
    state.data = { ok: false, message: '预览请求失败' }
  } finally {
    state.loading = false
  }
}

const openBuiltinPicker = async () => {
  builtinSelected.value = new Set()
  builtinActiveCategory.value = 'all'
  builtinParamInputs.value = {}
  previewStates.value = {}
  showBuiltinPicker.value = true
  if (builtinSources.value.length === 0) {
    await loadBuiltinSources()
  }
}

const confirmBuiltinSelection = () => {
  let added = 0
  for (const sourceId of builtinSelected.value) {
    const source = builtinSources.value.find(s => s.id === sourceId)
    if (!source || source.type !== 'fixed' || !source.preview_url) continue
    // 检查重复
    if (formTools.value.some(t => t.params?.url === source.preview_url)) continue
    formTools.value.push({
      name: 'rss_fetch',
      params: { url: source.preview_url, max_items: 15 },
    })
    added++
  }
  if (added > 0) {
    message.success(`已添加 ${added} 个数据源`)
  }
  showBuiltinPicker.value = false
}

// ==================== 分类选项 ====================

const categoryOptions = [
  { label: '全部', value: null },
  { label: '学术', value: 'academic' },
  { label: '技术', value: 'tech' },
  { label: '新闻/财经', value: 'news' },
  { label: '社区/热点', value: 'trending' },
  { label: '行业/产品', value: 'industry' },
  { label: '自定义', value: 'custom' },
]

const categoryTagMap: Record<string, { type: 'info' | 'success' | 'warning' | 'default'; label: string }> = {
  academic: { type: 'info', label: '学术' },
  tech: { type: 'success', label: '技术' },
  news: { type: 'warning', label: '新闻/财经' },
  trending: { type: 'warning', label: '社区/热点' },
  industry: { type: 'info', label: '行业/产品' },
  custom: { type: 'default', label: '自定义' },
}

// ==================== 表格列 ====================

const columns = computed<DataTableColumns<SubscriptionTemplate>>(() => [
  {
    title: '图标',
    key: 'icon',
    width: 60,
    render: (row) => h('span', { style: 'font-size: 20px' }, row.icon || '📋'),
  },
  {
    title: '名称',
    key: 'name',
    width: 180,
    ellipsis: { tooltip: true },
  },
  {
    title: 'Slug',
    key: 'slug',
    width: 180,
    ellipsis: { tooltip: true },
    render: (row) => h('code', { style: 'font-size: 12px; color: #666' }, row.slug),
  },
  {
    title: '分类',
    key: 'category',
    width: 80,
    render: (row) => {
      const tag = categoryTagMap[row.category] || categoryTagMap.custom
      return h(NTag, { size: 'small', type: tag.type, bordered: false }, { default: () => tag.label })
    },
  },
  {
    title: '安装数',
    key: 'install_count',
    width: 80,
    render: (row) => h('span', null, String(row.install_count || 0)),
  },
  {
    title: '状态',
    key: 'is_public',
    width: 80,
    render: (row) => h(NTag, {
      size: 'small',
      type: row.is_public ? 'success' : 'default',
      bordered: false,
    }, { default: () => row.is_public ? '公开' : '隐藏' }),
  },
  {
    title: '操作',
    key: 'actions',
    width: 150,
    render: (row) => h(NSpace, { size: 'small' }, {
      default: () => [
        h(NButton, {
          size: 'small',
          onClick: () => handleEdit(row),
        }, { default: () => '编辑' }),
        h(NPopconfirm, {
          onPositiveClick: () => handleDelete(row.id),
        }, {
          trigger: () => h(NButton, { size: 'small', type: 'error' }, { default: () => '删除' }),
          default: () => `确定删除模板「${row.name}」？`,
        }),
      ],
    }),
  },
])

// ==================== 数据加载 ====================

const loadData = async () => {
  loading.value = true
  try {
    const params: Record<string, any> = {
      page: page.value,
      page_size: pageSize.value,
    }
    if (filterCategory.value) params.category = filterCategory.value
    if (searchText.value) params.search = searchText.value
    const res = await getTemplates(params)
    templates.value = res.items
    total.value = res.total
  } catch {
    message.error('加载模板列表失败')
  } finally {
    loading.value = false
  }
}

// ==================== 操作 ====================

const handleCreate = () => {
  isEdit.value = false
  editingId.value = null
  form.value = {
    name: '',
    slug: '',
    category: 'custom',
    description: '',
    config_template: {},
    suggested_exec_mode: 'quick',
    suggested_schedule: '0 9 * * *',
    icon: '',
    is_public: true,
  }
  configJson.value = '{}'
  formTools.value = []
  formKeywords.value = []
  scheduleType.value = 'daily'
  scheduleTime.value = '09:00'
  showModal.value = true
}

const handleEdit = (row: SubscriptionTemplate) => {
  isEdit.value = true
  editingId.value = row.id
  form.value = {
    name: row.name,
    slug: row.slug,
    category: row.category || 'custom',
    description: row.description || '',
    config_template: row.config_template || {},
    suggested_exec_mode: row.suggested_exec_mode || 'quick',
    suggested_schedule: row.suggested_schedule || '0 9 * * *',
    icon: row.icon || '',
    is_public: row.is_public !== false,
  }
  configJson.value = JSON.stringify(row.config_template || {}, null, 2)
  // 结构化解析
  const config = row.config_template || {}
  formTools.value = (config.tools || []).map((t: any) => ({ name: t.name, params: { ...t.params } }))
  formKeywords.value = [...(config.keywords || [])]
  const sched = cronToSchedule(row.suggested_schedule || '0 9 * * *')
  scheduleType.value = sched.type
  scheduleTime.value = sched.time
  showModal.value = true
}

const handleDelete = async (id: string) => {
  try {
    await deleteTemplate(id)
    message.success('删除成功')
    loadData()
  } catch {
    message.error('删除失败')
  }
}

const handleSubmit = async () => {
  if (!form.value.name.trim()) {
    message.warning('请填写模板名称')
    return
  }
  if (!form.value.slug.trim()) {
    message.warning('请填写 Slug')
    return
  }

  // 从结构化状态构建 config 和 schedule
  form.value.config_template = {
    tools: formTools.value.filter(t => t.name),
    keywords: formKeywords.value,
  }
  form.value.suggested_schedule = scheduleToCron(scheduleType.value, scheduleTime.value)

  modalLoading.value = true
  try {
    if (isEdit.value && editingId.value) {
      await updateTemplate(editingId.value, form.value)
      message.success('更新成功')
    } else {
      await createTemplate(form.value)
      message.success('创建成功')
    }
    showModal.value = false
    loadData()
  } catch {
    message.error(isEdit.value ? '更新失败' : '创建失败')
  } finally {
    modalLoading.value = false
  }
}

const handlePageChange = (p: number) => {
  page.value = p
  loadData()
}

onMounted(loadData)
</script>

<template>
  <div>
    <n-card title="订阅模板管理">
      <!-- 工具栏 -->
      <template #header-extra>
        <n-space>
          <n-button type="primary" @click="handleCreate">
            <template #icon><n-icon :component="AddOutline" /></template>
            新建模板
          </n-button>
          <n-button @click="loadData">
            <template #icon><n-icon :component="RefreshOutline" /></template>
          </n-button>
        </n-space>
      </template>

      <!-- 筛选栏 -->
      <n-space class="filter-bar" align="center">
        <n-select
          v-model:value="filterCategory"
          :options="categoryOptions"
          placeholder="分类筛选"
          clearable
          style="width: 120px"
          @update:value="loadData"
        />
        <n-input
          v-model:value="searchText"
          placeholder="搜索模板名称..."
          clearable
          style="width: 200px"
          @clear="loadData"
          @keyup.enter="loadData"
        />
      </n-space>

      <!-- 表格 -->
      <n-data-table
        :columns="columns"
        :data="templates"
        :loading="loading"
        :bordered="false"
        style="margin-top: 16px"
      />

      <!-- 分页 -->
      <div class="pagination-wrap" v-if="total > pageSize">
        <n-pagination
          v-model:page="page"
          :page-count="Math.ceil(total / pageSize)"
          @update:page="handlePageChange"
        />
      </div>
    </n-card>

    <!-- 编辑弹窗 -->
    <n-modal
      v-model:show="showModal"
      :title="isEdit ? '编辑模板' : '新建模板'"
      preset="card"
      style="width: 600px; max-width: 95vw"
      :mask-closable="false"
    >
      <n-form label-placement="left" label-width="100">
        <n-form-item label="模板名称">
          <n-input v-model:value="form.name" placeholder="如: arXiv AI论文日报" />
        </n-form-item>
        <n-form-item label="Slug">
          <n-input v-model:value="form.slug" placeholder="如: arxiv-cs-ai-daily" :disabled="isEdit" />
        </n-form-item>
        <n-form-item label="分类">
          <n-select
            v-model:value="form.category"
            :options="[
              { label: '学术', value: 'academic' },
              { label: '技术', value: 'tech' },
              { label: '新闻/财经', value: 'news' },
              { label: '社区/热点', value: 'trending' },
              { label: '行业/产品', value: 'industry' },
              { label: '自定义', value: 'custom' },
            ]"
          />
        </n-form-item>
        <n-form-item label="图标">
          <n-input v-model:value="form.icon" placeholder="emoji, 如 📄" style="width: 100px" />
        </n-form-item>
        <n-form-item label="描述">
          <n-input v-model:value="form.description" type="textarea" :rows="2" />
        </n-form-item>
        <n-form-item label="执行模式">
          <n-select
            v-model:value="form.suggested_exec_mode"
            :options="[
              { label: '快速检查', value: 'quick' },
              { label: '深度研究', value: 'deep' },
              { label: '混合模式', value: 'hybrid' },
            ]"
          />
        </n-form-item>
        <n-form-item label="调度频率">
          <n-space>
            <n-select v-model:value="scheduleType" :options="scheduleOptions" style="width: 120px" />
            <n-select v-model:value="scheduleTime" :options="timeOptions" style="width: 100px" />
          </n-space>
        </n-form-item>
        <n-form-item label="数据源">
          <div style="width: 100%">
            <div v-for="(tool, idx) in formTools" :key="idx" class="tool-item">
              <div class="tool-item-header">
                <n-select
                  v-model:value="tool.name"
                  :options="toolTypeOptions"
                  size="small"
                  style="width: 140px"
                  @update:value="(v: string) => onToolTypeChange(idx, v)"
                />
                <n-button text type="error" size="small" @click="removeTool(idx)">删除</n-button>
              </div>
              <div class="tool-item-params">
                <template v-if="tool.name === 'rss_fetch' || tool.name === 'web_fetch'">
                  <n-input v-model:value="tool.params.url" placeholder="URL 地址" size="small" />
                  <div v-if="tool.name === 'rss_fetch'" class="max-items-field">
                    <span class="max-items-label">最多</span>
                    <n-input-number
                      v-model:value="tool.params.max_items"
                      :min="1" :max="50"
                      size="small"
                      style="width: 80px"
                    />
                    <span class="max-items-label">条</span>
                  </div>
                </template>
                <template v-else>
                  <n-input v-model:value="tool.params.query" placeholder="搜索关键词" size="small" />
                  <n-select
                    v-if="tool.name === 'academic_search'"
                    v-model:value="tool.params.source"
                    :options="sourceOptions"
                    size="small"
                    style="width: 170px"
                  />
                </template>
              </div>
            </div>
            <n-space style="margin-top: 8px">
              <n-button dashed size="small" @click="addTool">
                <template #icon><n-icon :component="AddOutline" /></template>
                手动添加
              </n-button>
              <n-button size="small" type="primary" ghost @click="openBuiltinPicker">
                <template #icon><n-icon :component="SearchOutline" /></template>
                从内置数据源选择
              </n-button>
            </n-space>
          </div>
        </n-form-item>
        <n-form-item label="关键词">
          <n-dynamic-tags v-model:value="formKeywords" />
        </n-form-item>
        <n-form-item label="公开">
          <n-switch v-model:value="form.is_public" />
        </n-form-item>
      </n-form>

      <template #footer>
        <n-space justify="end">
          <n-button @click="showModal = false">取消</n-button>
          <n-button type="primary" :loading="modalLoading" @click="handleSubmit">
            {{ isEdit ? '保存' : '创建' }}
          </n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 内置数据源选择弹窗 -->
    <n-modal
      v-model:show="showBuiltinPicker"
      title="选择内置数据源"
      preset="card"
      style="width: 680px; max-width: 95vw"
      :mask-closable="false"
    >
      <!-- 分类筛选 -->
      <div class="builtin-category-bar">
        <span
          :class="['builtin-cat-tag', { active: builtinActiveCategory === 'all' }]"
          @click="builtinActiveCategory = 'all'"
        >全部</span>
        <span
          v-for="cat in builtinCategories"
          :key="cat.value"
          :class="['builtin-cat-tag', { active: builtinActiveCategory === cat.value }]"
          @click="builtinActiveCategory = cat.value"
        >{{ cat.label }}</span>
      </div>

      <!-- 加载中 -->
      <div v-if="builtinLoading" style="display: flex; justify-content: center; padding: 32px 0;">
        <n-spin size="small" />
      </div>

      <template v-else>
        <div class="builtin-scroll-area">
          <!-- Fixed 源卡片 -->
          <div v-if="builtinFixedSources.length > 0" class="builtin-grid">
            <template v-for="source in builtinFixedSources" :key="source.id">
              <div
                :class="[
                  'builtin-card',
                  {
                    selected: builtinSelected.has(source.id),
                    disabled: isSourceAlreadyAdded(source),
                  },
                ]"
                @click="toggleBuiltinFixed(source)"
              >
                <div class="builtin-card-header">
                  <span class="builtin-icon">{{ getIcon(source.icon) }}</span>
                  <span class="builtin-name">{{ source.name }}</span>
                  <span v-if="isSourceAlreadyAdded(source)" class="builtin-badge added">已添加</span>
                  <span v-else-if="builtinSelected.has(source.id)" class="builtin-badge selected">已选</span>
                </div>
                <p class="builtin-desc">{{ source.description }}</p>
                <!-- 预览按钮 -->
                <div class="builtin-card-footer">
                  <span
                    class="preview-link"
                    :class="{ active: getPreviewState(source.id).expanded }"
                    @click="handlePreview(source, $event)"
                  >
                    <n-spin v-if="getPreviewState(source.id).loading" :size="12" style="margin-right: 4px" />
                    {{ getPreviewState(source.id).expanded ? '收起预览' : '预览内容' }}
                  </span>
                </div>
              </div>
              <!-- 预览内容（独立 grid item，跨两列） -->
              <div
                v-if="getPreviewState(source.id).expanded && getPreviewState(source.id).data"
                class="preview-row"
              >
                <template v-if="getPreviewState(source.id).data!.ok">
                  <span class="preview-row-label">{{ source.name }} · 最新内容：</span>
                  <ul class="preview-list">
                    <li v-for="(item, idx) in getPreviewState(source.id).data!.sample_items" :key="idx">
                      <n-tooltip :disabled="!item.summary" placement="top" :style="{ maxWidth: '360px' }">
                        <template #trigger>
                          <a
                            v-if="item.link"
                            :href="item.link"
                            target="_blank"
                            rel="noopener noreferrer"
                            class="preview-item-link"
                            @click.stop
                          >{{ item.title }}</a>
                          <span v-else class="preview-item-text">{{ item.title }}</span>
                        </template>
                        <div class="preview-tooltip-content">
                          <p v-if="item.summary">{{ item.summary }}</p>
                          <span v-if="item.published" class="preview-tooltip-time">{{ item.published }}</span>
                        </div>
                      </n-tooltip>
                    </li>
                  </ul>
                </template>
                <p v-else class="preview-error">{{ getPreviewState(source.id).data!.message }}</p>
              </div>
            </template>
          </div>

          <!-- Parameterized 源 -->
          <div v-if="builtinParamSources.length > 0" class="builtin-param-sources">
            <div v-for="source in builtinParamSources" :key="source.id" class="builtin-param-item">
              <div class="builtin-card-header">
                <span class="builtin-icon">{{ getIcon(source.icon) }}</span>
                <span class="builtin-name">{{ source.name }}</span>
              </div>
              <p class="builtin-desc">{{ source.description }}</p>
              <div class="builtin-param-input-row">
                <!-- autocomplete 模式 -->
                <n-auto-complete
                  v-if="source.input_type === 'autocomplete'"
                  v-model:value="getBuiltinParamInput(source.id).value"
                  :options="getAutoCompleteOptions(source)"
                  :placeholder="source.param_placeholder || '选择或输入'"
                  size="small"
                  style="flex: 1"
                  :status="getBuiltinParamInput(source.id).error ? 'error' : undefined"
                  clearable
                  :get-show="() => true"
                />
                <!-- 普通文本输入 -->
                <n-input
                  v-else
                  v-model:value="getBuiltinParamInput(source.id).value"
                  :placeholder="source.param_placeholder || '请输入'"
                  size="small"
                  style="flex: 1"
                  :status="getBuiltinParamInput(source.id).error ? 'error' : undefined"
                  @keyup.enter="handleBuiltinParamAdd(source)"
                />
                <n-button
                  size="small"
                  type="primary"
                  :loading="getBuiltinParamInput(source.id).testing"
                  :disabled="!getBuiltinParamInput(source.id).value.trim()"
                  @click="handleBuiltinParamAdd(source)"
                >确认添加</n-button>
              </div>
              <!-- 校验错误 -->
              <p v-if="getBuiltinParamInput(source.id).error" class="builtin-param-error">
                {{ getBuiltinParamInput(source.id).error }}
              </p>
              <!-- 探测结果 -->
              <div v-if="getBuiltinParamInput(source.id).result" class="builtin-param-result">
                <n-alert
                  v-if="getBuiltinParamInput(source.id).result!.ok"
                  type="success"
                  :bordered="false"
                  style="padding: 6px 10px;"
                >
                  {{ getBuiltinParamInput(source.id).result!.message }}
                  <span v-if="getBuiltinParamInput(source.id).result!.feed_title">
                    · {{ getBuiltinParamInput(source.id).result!.feed_title }}
                  </span>
                  <ul v-if="getBuiltinParamInput(source.id).result!.sample_items?.length" class="preview-list" style="margin-top: 6px;">
                    <li v-for="(item, idx) in getBuiltinParamInput(source.id).result!.sample_items" :key="idx">
                      <a v-if="item.link" :href="item.link" target="_blank" class="preview-item-link" @click.stop>{{ item.title }}</a>
                      <span v-else class="preview-item-text">{{ item.title }}</span>
                    </li>
                  </ul>
                </n-alert>
                <div v-else class="builtin-param-unavailable">
                  <span class="builtin-param-unavailable-msg">{{ getBuiltinParamInput(source.id).result!.message }}</span>
                  <n-button text type="primary" size="small" @click="handleForceAdd(source)">
                    仍然添加
                  </n-button>
                </div>
              </div>
              <!-- 帮助链接 / 帮助文字 -->
              <div v-if="source.help_url || source.help_text" class="builtin-param-help">
                <a v-if="source.help_url" :href="source.help_url" target="_blank" class="builtin-help-link">
                  {{ source.help_url_label || '查看更多' }}
                </a>
                <p v-if="source.help_text" class="builtin-help-text">{{ source.help_text }}</p>
              </div>
            </div>
          </div>
        </div>
      </template>

      <template #footer>
        <n-space justify="space-between" align="center">
          <span v-if="builtinSelected.size > 0" style="font-size: 13px; color: #666;">
            已选 {{ builtinSelected.size }} 个数据源
          </span>
          <span v-else style="font-size: 13px; color: #999;">点击卡片选择数据源</span>
          <n-space>
            <n-button @click="showBuiltinPicker = false">取消</n-button>
            <n-button
              type="primary"
              :disabled="builtinSelected.size === 0"
              @click="confirmBuiltinSelection"
            >确认添加 ({{ builtinSelected.size }})</n-button>
          </n-space>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.filter-bar {
  margin-bottom: 0;
}

.pagination-wrap {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

.tool-item {
  border: 1px solid #e8e8e8;
  border-radius: 6px;
  padding: 8px 10px;
  margin-bottom: 8px;
}

.tool-item-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}

.tool-item-params {
  display: flex;
  gap: 8px;
  align-items: center;
}

.max-items-field {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.max-items-label {
  font-size: 12px;
  color: #666;
  white-space: nowrap;
}

/* 内置数据源选择器 */
.builtin-category-bar {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.builtin-cat-tag {
  padding: 4px 12px;
  border-radius: 16px;
  font-size: 13px;
  color: #666;
  background: #f5f5f5;
  cursor: pointer;
  transition: all 0.2s;
  user-select: none;
}

.builtin-cat-tag.active {
  color: #fff;
  background: #18a058;
}

.builtin-scroll-area {
  max-height: 420px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.builtin-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.builtin-card {
  border: 1px solid #e8e8e8;
  border-radius: 8px;
  padding: 10px 12px;
  cursor: pointer;
  transition: border-color 0.2s, background 0.2s, box-shadow 0.2s;
  background: #fff;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.builtin-card:hover:not(.disabled) {
  border-color: #18a058;
  box-shadow: 0 2px 8px rgba(24, 160, 88, 0.1);
}

.builtin-card.selected {
  border: 2px solid #18a058;
  background: #f0faf4;
}

.builtin-card.disabled {
  border: 1px dashed #d0d0d0;
  background: #fafafa;
  cursor: not-allowed;
  opacity: 0.7;
}

.builtin-card-header {
  display: flex;
  align-items: center;
  gap: 6px;
}

.builtin-icon {
  font-size: 18px;
  flex-shrink: 0;
}

.builtin-name {
  font-size: 13px;
  font-weight: 500;
  color: #333;
  flex: 1;
}

.builtin-badge {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 10px;
  flex-shrink: 0;
}

.builtin-badge.added {
  background: #e0e0e0;
  color: #999;
}

.builtin-badge.selected {
  background: #d1f2e0;
  color: #18a058;
}

.builtin-desc {
  margin: 0;
  font-size: 12px;
  color: #999;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* 预览功能 */
.builtin-card-footer {
  display: flex;
  justify-content: flex-end;
  margin-top: 2px;
}

.preview-link {
  font-size: 12px;
  color: #999;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  user-select: none;
  transition: color 0.2s;
}

.preview-link:hover,
.preview-link.active {
  color: #18a058;
}

.preview-row {
  grid-column: 1 / -1;
  background: #f9fafb;
  border: 1px solid #e8e8e8;
  border-radius: 6px;
  padding: 8px 12px;
}

.preview-row-label {
  font-size: 12px;
  color: #666;
  font-weight: 500;
}

.preview-list {
  margin: 4px 0 0;
  padding: 0 0 0 16px;
  list-style: disc;
}

.preview-list li {
  font-size: 12px;
  color: #555;
  line-height: 1.8;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.preview-item-link {
  color: #18a058;
  text-decoration: none;
  cursor: pointer;
}

.preview-item-link:hover {
  text-decoration: underline;
  color: #0e7a3e;
}

.preview-item-text {
  color: #555;
}

.preview-tooltip-content {
  font-size: 12px;
  line-height: 1.5;
}

.preview-tooltip-content p {
  margin: 0 0 4px;
  color: #e8e8e8;
}

.preview-tooltip-time {
  color: #aaa;
  font-size: 11px;
}

.preview-error {
  margin: 0;
  font-size: 12px;
  color: #e03030;
}

/* Parameterized 源 */
.builtin-param-sources {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.builtin-param-item {
  border: 1px solid #e8e8e8;
  border-radius: 8px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.builtin-param-input-row {
  display: flex;
  gap: 8px;
  align-items: center;
}

.builtin-param-error {
  margin: 0;
  font-size: 12px;
  color: #e03030;
}

.builtin-param-result {
  margin-top: 2px;
}

.builtin-param-unavailable {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  background: #fff8e6;
  border-radius: 4px;
  border: 1px solid #ffcb3d;
}

.builtin-param-unavailable-msg {
  font-size: 12px;
  color: #b76a00;
  flex: 1;
}

.builtin-param-help {
  margin-top: 4px;
}

.builtin-help-link {
  font-size: 12px;
  color: #18a058;
  text-decoration: none;
}

.builtin-help-link:hover {
  text-decoration: underline;
}

.builtin-help-text {
  margin: 4px 0 0;
  font-size: 12px;
  color: #999;
  line-height: 1.4;
}
</style>
