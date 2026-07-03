<script setup lang="ts">
/**
 * 工具管理页面
 *
 * Tab 1: 工具列表（含详情 Drawer、参数标签、来源标签）
 * Tab 2: 工具源管理（CRUD、同步、创建 Drawer）
 */
import { ref, computed, onMounted, h } from 'vue'
import {
  NCard,
  NButton,
  NInput,
  NSelect,
  NTag,
  NSwitch,
  NSpin,
  NEmpty,
  NGrid,
  NGridItem,
  NStatistic,
  NTooltip,
  NTabs,
  NTabPane,
  NDrawer,
  NDrawerContent,
  NDescriptions,
  NDescriptionsItem,
  NDataTable,
  NRadioGroup,
  NRadio,
  NForm,
  NFormItem,
  NInputNumber,
  NDynamicInput,
  NPopconfirm,
  NSpace,
  NModal,
  NPagination,
  NCollapse,
  NCollapseItem,
  NAlert,
  NSkeleton,
  useMessage,
  useDialog,
} from 'naive-ui'
import {
  getTools,
  getToolDetail,
  disableTool,
  enableTool,
  deleteTool,
  getToolSources,
  createToolSource,
  deleteToolSource,
  syncToolSource,
  toggleToolSource,
  createTool,
  updateToolDisplayName,
} from '../api/tools'
import type { ToolInfo, ToolDetail, ToolSource, ToolSourceConfig } from '../api/tools'
import {
  getPluginMarket,
  installFromIndex,
  installPlugin,
  previewManifest,
  getPluginSettings,
  updatePluginSettings,
  resyncPlugin,
} from '../api/plugins'
import type { MarketPlugin, MarketTag, PluginManifest, PluginSettings } from '../api/plugins'

const message = useMessage()
const dialog = useDialog()
const loading = ref(false)
const tools = ref<ToolInfo[]>([])
const disabledSet = ref<Set<string>>(new Set())

// ==================== Tab 1: 工具列表 ====================

const searchText = ref('')
const filterCategory = ref<string | null>(null)
const filterStatus = ref<string | null>(null)
const filterSource = ref<string | null>(null)

// 分类颜色
const CATEGORY_COLORS: Record<string, { type: 'default' | 'success' | 'info' | 'warning' | 'error'; label: string }> = {
  network: { type: 'info', label: '网络' },
  knowledge: { type: 'success', label: '知识库' },
  file: { type: 'warning', label: '文件' },
  research: { type: 'default', label: '学术' },
  document: { type: 'default', label: '文档' },
  education: { type: 'default', label: '教育' },
  openapi: { type: 'info', label: 'OpenAPI' },
  mcp: { type: 'warning', label: 'MCP' },
  custom: { type: 'default', label: '自定义' },
}

const SOURCE_TYPE_MAP: Record<string, { type: 'default' | 'success' | 'info' | 'warning' | 'error'; label: string; color?: string }> = {
  builtin: { type: 'success', label: '内置' },
  openapi: { type: 'info', label: 'OpenAPI' },
  mcp: { type: 'warning', label: 'MCP' },
  client: { type: 'default', label: 'Client' },
  plugin: { type: 'default' as const, label: '插件', color: '#8b5cf6' },
}

function getCategoryTag(category: string) {
  return CATEGORY_COLORS[category] || { type: 'default' as const, label: category }
}

function getSourceTag(sourceType: string, pluginTitle?: string) {
  const map = SOURCE_TYPE_MAP[sourceType]
  if (!map) return { type: 'default' as const, label: sourceType }
  if (sourceType === 'plugin' && pluginTitle) {
    return { ...map, label: `插件: ${pluginTitle}` }
  }
  return map
}

function getPluginAvatar(tool: ToolInfo): string | undefined {
  if (tool.source_type !== 'plugin' || !tool.source_id) return undefined
  const source = toolSources.value.find(s => s.id === tool.source_id)
  return (source?.config as any)?.meta?.avatar
}

function getPluginTitle(tool: ToolInfo): string {
  if (tool.source_type !== 'plugin') return ''
  if (tool.source_id) {
    const source = toolSources.value.find(s => s.id === tool.source_id)
    const title = (source?.config as any)?.meta?.title
    if (title) return title
  }
  return tool.name.split('__')[0]
}

const categoryOptions = computed(() => {
  const cats = new Set(tools.value.map(t => t.category))
  return [
    { label: '全部分类', value: '' },
    ...Array.from(cats).map(c => ({
      label: getCategoryTag(c).label,
      value: c,
    }))
  ]
})

const sourceOptions = computed(() => {
  const types = new Set(tools.value.map(t => t.source_type))
  return [
    { label: '全部来源', value: '' },
    ...Array.from(types).map(t => ({
      label: getSourceTag(t).label,
      value: t,
    }))
  ]
})

const statusOptions = [
  { label: '全部状态', value: '' },
  { label: '已启用', value: 'enabled' },
  { label: '已禁用', value: 'disabled' },
]

const filteredTools = computed(() => {
  return tools.value.filter(tool => {
    if (searchText.value) {
      const q = searchText.value.toLowerCase()
      if (!tool.name.toLowerCase().includes(q) && !tool.description.toLowerCase().includes(q)) {
        return false
      }
    }
    if (filterCategory.value && tool.category !== filterCategory.value) return false
    if (filterSource.value && tool.source_type !== filterSource.value) return false
    if (filterStatus.value === 'enabled' && !tool.enabled) return false
    if (filterStatus.value === 'disabled' && tool.enabled) return false
    return true
  })
})

const stats = computed(() => {
  const total = tools.value.length
  const enabled = tools.value.filter(t => t.enabled).length
  const disabled = total - enabled
  const categories = new Set(tools.value.map(t => t.category)).size
  const sourceCount = toolSources.value.length
  return { total, enabled, disabled, categories, sourceCount }
})

async function loadTools() {
  loading.value = true
  try {
    const res = await getTools()
    tools.value = res.tools
    disabledSet.value = new Set(res.disabled)
  } catch (e: any) {
    message.error(e?.message || '加载工具列表失败')
  } finally {
    loading.value = false
  }
}

const togglingTools = ref<Set<string>>(new Set())

async function handleToggle(tool: ToolInfo) {
  togglingTools.value.add(tool.name)
  try {
    if (tool.enabled) {
      const res = await disableTool(tool.name)
      if (res.success) {
        message.success(res.message)
        tool.enabled = false
        disabledSet.value.add(tool.name)
      } else {
        message.warning(res.message)
      }
    } else {
      const res = await enableTool(tool.name)
      if (res.success) {
        message.success(res.message)
        tool.enabled = true
        disabledSet.value.delete(tool.name)
      } else {
        message.warning(res.message)
      }
    }
  } catch (e: any) {
    message.error(e?.message || '操作失败')
  } finally {
    togglingTools.value.delete(tool.name)
  }
}

async function handleDeleteTool(toolName: string) {
  try {
    const res = await deleteTool(toolName)
    if (res.success) {
      message.success(res.message)
      loadTools()
      loadSources()
    } else {
      message.warning(res.message)
    }
  } catch (e: any) {
    message.error(e?.message || '删除失败')
  }
}

// ==================== 工具详情 Drawer ====================

const detailDrawerVisible = ref(false)
const detailLoading = ref(false)
const toolDetail = ref<ToolDetail | null>(null)
const editDisplayName = ref('')
const savingDisplayName = ref(false)

async function showDetail(toolName: string) {
  detailDrawerVisible.value = true
  detailLoading.value = true
  try {
    toolDetail.value = await getToolDetail(toolName)
    editDisplayName.value = (toolDetail.value as any).display_name || ''
  } catch (e: any) {
    message.error(e?.message || '加载详情失败')
  } finally {
    detailLoading.value = false
  }
}

async function handleSaveDisplayName() {
  if (!toolDetail.value) return
  savingDisplayName.value = true
  try {
    await updateToolDisplayName(toolDetail.value.name, editDisplayName.value)
    message.success(editDisplayName.value ? `显示名已更新为「${editDisplayName.value}」` : '显示名已清除')
    // 刷新工具列表
    await loadTools()
  } catch (e: any) {
    message.error(e?.message || '保存失败')
  } finally {
    savingDisplayName.value = false
  }
}

const paramColumns = [
  { title: '名称', key: 'name', width: 120 },
  { title: '类型', key: 'type', width: 80 },
  { title: '描述', key: 'description' },
  {
    title: '必填',
    key: 'required',
    width: 60,
    render: (row: any) => row.required ? h(NTag, { type: 'error', size: 'small', bordered: false }, () => '*') : '-',
  },
  {
    title: '默认值',
    key: 'default',
    width: 80,
    render: (row: any) => row.default != null ? String(row.default) : '-',
  },
]

// ==================== Tab 2: 工具源管理 ====================

const sourcesLoading = ref(false)
const toolSources = ref<ToolSource[]>([])

async function loadSources() {
  sourcesLoading.value = true
  try {
    const res = await getToolSources()
    toolSources.value = res.sources
  } catch (e: any) {
    message.error(e?.message || '加载工具源失败')
  } finally {
    sourcesLoading.value = false
  }
}

const syncingIds = ref<Set<string>>(new Set())
const togglingIds = ref<Set<string>>(new Set())

async function handleToggleSource(row: ToolSource) {
  togglingIds.value.add(row.id)
  try {
    const res = await toggleToolSource(row.id)
    if (res.success) {
      // 乐观更新：立即修改本地数据，避免 NSwitch 闪烁
      const target = toolSources.value.find(s => s.id === row.id)
      if (target) target.is_enabled = res.is_enabled
      message.success(res.message)
      loadTools()
    }
  } catch (e: any) {
    message.error(e?.message || '操作失败')
  } finally {
    togglingIds.value.delete(row.id)
  }
}

async function handleSync(sourceId: string) {
  syncingIds.value.add(sourceId)
  try {
    const res = await syncToolSource(sourceId)
    if (res.success) {
      message.success(res.message)
      loadSources()
      loadTools()
    } else {
      message.warning(res.message)
    }
  } catch (e: any) {
    message.error(e?.message || '同步失败')
  } finally {
    syncingIds.value.delete(sourceId)
  }
}

async function handleDeleteSource(sourceId: string) {
  try {
    const res = await deleteToolSource(sourceId)
    if (res.success) {
      message.success(res.message)
      loadSources()
      loadTools()
    } else {
      message.warning(res.message)
    }
  } catch (e: any) {
    message.error(e?.message || '删除失败')
  }
}

// ==================== 创建工具源 Drawer ====================

const createDrawerVisible = ref(false)
const createLoading = ref(false)
const createForm = ref({
  name: '',
  type: 'openapi' as 'openapi' | 'mcp' | 'client',
  // OpenAPI
  specSource: 'url' as 'url' | 'content',
  specUrl: '',
  specContent: '',
  baseUrl: '',
  authType: '' as '' | 'bearer' | 'api_key' | 'basic',
  authToken: '',
  authKey: '',
  authHeader: 'X-API-Key',
  authUsername: '',
  authPassword: '',
  // MCP
  transport: 'stdio' as 'stdio' | 'http',
  command: '',
  args: [] as string[],
  mcpUrl: '',
})

function resetCreateForm() {
  createForm.value = {
    name: '',
    type: 'openapi',
    specSource: 'url',
    specUrl: '',
    specContent: '',
    baseUrl: '',
    authType: '',
    authToken: '',
    authKey: '',
    authHeader: 'X-API-Key',
    authUsername: '',
    authPassword: '',
    transport: 'stdio',
    command: '',
    args: [],
    mcpUrl: '',
  }
}

function openCreateDrawer() {
  resetCreateForm()
  createDrawerVisible.value = true
}

async function handleCreate() {
  const f = createForm.value
  if (!f.name.trim()) {
    message.warning('请输入名称')
    return
  }

  // 构建 config
  const config: ToolSourceConfig = {}

  if (f.type === 'openapi') {
    if (f.specSource === 'url') {
      if (!f.specUrl.trim()) { message.warning('请输入 Spec URL'); return }
      config.spec_url = f.specUrl.trim()
    } else {
      if (!f.specContent.trim()) { message.warning('请粘贴 Spec 内容'); return }
      config.spec_content = f.specContent.trim()
    }
    if (f.baseUrl.trim()) config.base_url = f.baseUrl.trim()
    if (f.authType) {
      const auth: any = { type: f.authType }
      if (f.authType === 'bearer') auth.token = f.authToken
      else if (f.authType === 'api_key') { auth.key = f.authKey; auth.header = f.authHeader }
      else if (f.authType === 'basic') { auth.username = f.authUsername; auth.password = f.authPassword }
      config.auth = auth
    }
  } else if (f.type === 'mcp') {
    config.transport = f.transport
    if (f.transport === 'stdio') {
      if (!f.command.trim()) { message.warning('请输入命令'); return }
      config.command = f.command.trim()
      config.args = f.args.filter(a => a.trim())
    } else {
      if (!f.mcpUrl.trim()) { message.warning('请输入 MCP URL'); return }
      config.url = f.mcpUrl.trim()
    }
  }

  createLoading.value = true
  try {
    const res = await createToolSource({
      name: f.name.trim(),
      type: f.type,
      config,
    })
    if (res.success) {
      message.success(res.message)
      createDrawerVisible.value = false
      loadSources()
      loadTools()
    }
  } catch (e: any) {
    message.error(e?.message || '创建失败')
  } finally {
    createLoading.value = false
  }
}

// ==================== 创建 Client 工具 Modal ====================

const clientToolDrawerVisible = ref(false)
const clientToolForm = ref({ name: '', description: '', schemaJson: '{}', sourceId: '' })

function openClientToolDrawer(sourceId: string) {
  clientToolForm.value = { name: '', description: '', schemaJson: '{\n  "type": "object",\n  "properties": {},\n  "required": []\n}', sourceId }
  clientToolDrawerVisible.value = true
}

async function handleCreateClientTool() {
  const f = clientToolForm.value
  if (!f.name.trim()) { message.warning('请输入工具名称'); return }

  let inputSchema
  try {
    inputSchema = JSON.parse(f.schemaJson)
  } catch {
    message.error('参数 Schema 格式错误，请输入有效的 JSON')
    return
  }

  try {
    const res = await createTool({
      name: f.name.trim(),
      description: f.description,
      input_schema: inputSchema,
      source_id: f.sourceId,
    })
    if (res.success) {
      message.success(res.message)
      clientToolDrawerVisible.value = false
      loadSources()
      loadTools()
    }
  } catch (e: any) {
    message.error(e?.message || '创建工具失败')
  }
}

// ==================== 工具源表格列 ====================

const sourceColumns = computed(() => [
  {
    title: '名称',
    key: 'name',
    width: 200,
  },
  {
    title: '类型',
    key: 'type',
    width: 100,
    render: (row: ToolSource) => {
      const tag = getSourceTag(row.type)
      if (tag.color) {
        return h(NTag, { size: 'small', color: { textColor: tag.color, borderColor: tag.color } }, () => tag.label)
      }
      return h(NTag, { type: tag.type, size: 'small' }, () => tag.label)
    },
  },
  {
    title: '工具数',
    key: 'tool_count',
    width: 80,
  },
  {
    title: '状态',
    key: 'status',
    width: 90,
    render: (row: any) => {
      const switchNode = h(NSwitch, {
        value: row.is_enabled,
        loading: togglingIds.value.has(row.id),
        onUpdateValue: () => handleToggleSource(row),
      })
      // plugin 需设置时显示带 tooltip 的 switch
      if (row.type === 'plugin' && row.needs_settings && row.is_enabled) {
        return h(NTooltip, {}, {
          trigger: () => switchNode,
          default: () => '需要配置插件设置',
        })
      }
      return switchNode
    },
  },
  {
    title: '创建者',
    key: 'created_by',
    width: 100,
  },
  {
    title: '创建时间',
    key: 'created_at',
    width: 170,
    render: (row: ToolSource) => row.created_at ? new Date(row.created_at).toLocaleString() : '-',
  },
  {
    title: '操作',
    key: 'actions',
    width: 200,
    render: (row: ToolSource) => {
      const buttons: any[] = []
      if (row.type === 'plugin') {
        buttons.push(
          h(NButton, {
            text: true,
            size: 'small',
            type: 'primary',
            onClick: () => openSettingsDrawer(row.id, row.name),
          }, () => '设置')
        )
        buttons.push(
          h(NButton, {
            text: true,
            size: 'small',
            type: 'error',
            onClick: () => openUninstallModal(row.id, row.name, row.tool_count),
          }, () => '卸载')
        )
      } else if (row.type !== 'client') {
        buttons.push(
          h(NButton, {
            text: true,
            size: 'small',
            loading: syncingIds.value.has(row.id),
            onClick: () => handleSync(row.id),
          }, () => '同步')
        )
      } else {
        buttons.push(
          h(NButton, {
            text: true,
            size: 'small',
            type: 'primary',
            onClick: () => openClientToolDrawer(row.id),
          }, () => '添加工具')
        )
      }

      if (row.type !== 'plugin') {
        buttons.push(
          h(NPopconfirm, {
            onPositiveClick: () => handleDeleteSource(row.id),
          }, {
            trigger: () => h(NButton, { text: true, size: 'small', type: 'error' }, () => '删除'),
            default: () => `确定要删除工具源 ${row.name}？关联的工具也将被删除。`,
          })
        )
      }

      return h(NSpace, { size: 'small' }, () => buttons)
    },
  },
])

// ==================== Tab 3: 插件市场 ====================

const marketLoading = ref(false)
const marketPlugins = ref<MarketPlugin[]>([])
const marketTags = ref<MarketTag[]>([])
const marketTotal = ref(0)
const marketPage = ref(1)
const marketPageSize = ref(24)
const marketSearch = ref('')
const marketTagFilter = ref<string | null>(null)
const marketStatusFilter = ref<string | null>(null)
const installingIds = ref<Set<string>>(new Set())

// 安装确认 Drawer
const installDrawerVisible = ref(false)
const installPreviewLoading = ref(false)
const installPreview = ref<PluginManifest | null>(null)
const installTarget = ref<MarketPlugin | null>(null)
const installResultVisible = ref(false)
const installResult = ref<{ success: boolean; tools_count?: number; tool_names?: string[]; message?: string; error?: string } | null>(null)

// 自定义 URL 导入
const customManifestUrl = ref('')
const customPreviewLoading = ref(false)

// 卸载确认
const uninstallModalVisible = ref(false)
const uninstallTarget = ref<{ sourceId: string; name: string; toolCount: number } | null>(null)

async function loadMarket() {
  marketLoading.value = true
  try {
    const params: any = {
      page: marketPage.value,
      page_size: marketPageSize.value,
    }
    if (marketSearch.value) params.search = marketSearch.value
    if (marketTagFilter.value) params.tag = marketTagFilter.value
    if (marketStatusFilter.value) params.status = marketStatusFilter.value

    const res = await getPluginMarket(params)
    marketPlugins.value = res.items
    marketTags.value = res.tags
    marketTotal.value = res.total
  } catch (e: any) {
    message.error(e?.message || '加载插件市场失败')
  } finally {
    marketLoading.value = false
  }
}

function handleMarketSearch() {
  marketPage.value = 1
  loadMarket()
}

function handleMarketTagFilter(tag: string | null) {
  marketTagFilter.value = tag
  marketPage.value = 1
  loadMarket()
}

function handleMarketStatusFilter(status: string | null) {
  marketStatusFilter.value = status
  marketPage.value = 1
  loadMarket()
}

function handleMarketPageChange(page: number) {
  marketPage.value = page
  loadMarket()
}

async function handleInstallFromIndex(plugin: MarketPlugin) {
  installTarget.value = plugin
  installPreview.value = null
  installResultVisible.value = false
  installResult.value = null
  installDrawerVisible.value = true
  installPreviewLoading.value = true

  try {
    const manifest = await previewManifest(plugin.manifest_url)
    installPreview.value = manifest
  } catch (e: any) {
    message.error(e?.message || '预览 manifest 失败')
    installDrawerVisible.value = false
  } finally {
    installPreviewLoading.value = false
  }
}

async function confirmInstall() {
  if (!installTarget.value) return

  installingIds.value.add(installTarget.value.identifier)
  try {
    const res = await installFromIndex(installTarget.value.identifier)
    installResult.value = { success: true, ...res }
    installResultVisible.value = true
    loadMarket()
    loadTools()
    loadSources()
  } catch (e: any) {
    installResult.value = { success: false, error: e?.message || '安装失败' }
    installResultVisible.value = true
  } finally {
    if (installTarget.value) {
      installingIds.value.delete(installTarget.value.identifier)
    }
  }
}

async function handleCustomPreview() {
  if (!customManifestUrl.value.trim()) {
    message.warning('请输入 Manifest URL')
    return
  }
  installTarget.value = null
  installPreview.value = null
  installResultVisible.value = false
  installResult.value = null
  installDrawerVisible.value = true
  installPreviewLoading.value = true

  try {
    const manifest = await previewManifest(customManifestUrl.value.trim())
    installPreview.value = manifest
    installTarget.value = {
      identifier: manifest.identifier,
      meta: manifest.meta,
      manifest_url: manifest.manifest_url,
      installed: false,
    }
  } catch (e: any) {
    message.error(e?.message || '预览失败')
    installDrawerVisible.value = false
  } finally {
    installPreviewLoading.value = false
  }
}

async function confirmCustomInstall() {
  if (!customManifestUrl.value.trim()) return

  customPreviewLoading.value = true
  try {
    const res = await installPlugin(customManifestUrl.value.trim())
    installResult.value = { success: true, ...res }
    installResultVisible.value = true
    customManifestUrl.value = ''
    loadMarket()
    loadTools()
    loadSources()
  } catch (e: any) {
    installResult.value = { success: false, error: e?.message || '安装失败' }
    installResultVisible.value = true
  } finally {
    customPreviewLoading.value = false
  }
}

function openUninstallModal(sourceId: string, name: string, toolCount: number) {
  uninstallTarget.value = { sourceId, name, toolCount }
  uninstallModalVisible.value = true
}

async function confirmUninstall() {
  if (!uninstallTarget.value) return
  try {
    const res = await deleteToolSource(uninstallTarget.value.sourceId)
    if (res.success) {
      message.success(res.message)
      uninstallModalVisible.value = false
      loadMarket()
      loadTools()
      loadSources()
    }
  } catch (e: any) {
    message.error(e?.message || '卸载失败')
  }
}

// ==================== 插件设置 Drawer ====================

const settingsDrawerVisible = ref(false)
const settingsLoading = ref(false)
const settingsSaving = ref(false)
const settingsResyncLoading = ref(false)
const settingsSourceId = ref('')
const settingsSourceName = ref('')
const settingsSchema = ref<Record<string, any> | null>(null)
const settingsValues = ref<Record<string, any>>({})
const settingsMeta = ref<Record<string, any>>({})
const settingsIdentifier = ref('')
const settingsManifestUrl = ref('')
const settingsManifestFormat = ref('')
const settingsOrigin = ref('')
const settingsVersion = ref('')
const settingsCreatedAt = ref('')
const settingsTools = ref<Array<{ name: string; description: string; http_method?: string; http_path?: string; is_enabled: boolean }>>([])
const settingsTogglingTools = ref<Set<string>>(new Set())

/** 从 JSON Schema 生成表单字段列表 */
function getSettingsFields(schema: Record<string, any> | null) {
  if (!schema || !schema.properties) return []
  const properties = schema.properties as Record<string, any>
  const required = new Set<string>(schema.required || [])
  return Object.entries(properties).map(([key, prop]: [string, any]) => ({
    key,
    label: prop.title || key,
    type: prop.type || 'string',
    description: prop.description || '',
    required: required.has(key),
    enum: prop.enum || null,
    default: prop.default,
    format: prop.format || null,
  }))
}

async function openSettingsDrawer(sourceId: string, sourceName: string) {
  settingsSourceId.value = sourceId
  settingsSourceName.value = sourceName
  settingsSchema.value = null
  settingsValues.value = {}
  settingsMeta.value = {}
  settingsTools.value = []
  settingsIdentifier.value = ''
  settingsManifestUrl.value = ''
  settingsManifestFormat.value = ''
  settingsOrigin.value = ''
  settingsVersion.value = ''
  settingsCreatedAt.value = ''
  settingsDrawerVisible.value = true
  settingsLoading.value = true

  try {
    const res = await getPluginSettings(sourceId)
    settingsSchema.value = res.schema
    settingsValues.value = { ...(res.values || {}) }
    settingsMeta.value = res.meta || {}
    settingsIdentifier.value = res.identifier || ''
    settingsManifestUrl.value = res.manifest_url || ''
    settingsManifestFormat.value = res.manifest_format || ''
    settingsOrigin.value = res.origin || ''
    settingsVersion.value = res.version || ''
    settingsCreatedAt.value = res.created_at || ''
    settingsTools.value = res.tools || []
  } catch (e: any) {
    message.error(e?.message || '加载设置失败')
  } finally {
    settingsLoading.value = false
  }
}

async function handleTogglePluginTool(toolName: string, enabled: boolean) {
  settingsTogglingTools.value.add(toolName)
  try {
    const res = enabled ? await enableTool(toolName) : await disableTool(toolName)
    if (res.success) {
      const t = settingsTools.value.find(t => t.name === toolName)
      if (t) t.is_enabled = enabled
      loadTools()
    }
  } catch (e: any) {
    message.error(e?.message || '操作失败')
  } finally {
    settingsTogglingTools.value.delete(toolName)
  }
}

async function saveSettings() {
  settingsSaving.value = true
  try {
    const res = await updatePluginSettings(settingsSourceId.value, settingsValues.value)
    if (res.success) {
      message.success(res.message || '设置已保存')
    }
  } catch (e: any) {
    message.error(e?.message || '保存设置失败')
  } finally {
    settingsSaving.value = false
  }
}

async function handleResync() {
  settingsResyncLoading.value = true
  try {
    const res = await resyncPlugin(settingsSourceId.value)
    if (res.success) {
      message.success(res.message)
      loadTools()
      loadSources()
      loadMarket()
    }
  } catch (e: any) {
    message.error(e?.message || '重新同步失败')
  } finally {
    settingsResyncLoading.value = false
  }
}

function handleUninstallFromSettings() {
  settingsDrawerVisible.value = false
  const source = toolSources.value.find(s => s.id === settingsSourceId.value)
  openUninstallModal(
    settingsSourceId.value,
    settingsSourceName.value,
    source?.tool_count || 0,
  )
}

// ==================== 生命周期 ====================

onMounted(() => {
  loadTools()
  loadSources()
  loadMarket()
})
</script>

<template>
  <div class="tool-management">
    <!-- 页面标题 -->
    <div class="page-header">
      <div>
        <h2 class="page-title">工具管理</h2>
        <p class="page-desc">管理超级智能体可用的工具和工具源</p>
      </div>
      <NSpace>
        <NButton type="primary" @click="openCreateDrawer">+ 添加工具源</NButton>
        <NButton @click="loadTools(); loadSources()" :loading="loading">刷新</NButton>
      </NSpace>
    </div>

    <NTabs type="line" animated>
      <!-- ==================== Tab 1: 工具列表 ==================== -->
      <NTabPane name="tools" tab="工具列表">
        <!-- 统计卡片 -->
        <NGrid :cols="5" :x-gap="12" :y-gap="12" style="margin-bottom: 16px">
          <NGridItem>
            <NCard size="small">
              <NStatistic label="工具总数" :value="stats.total" />
            </NCard>
          </NGridItem>
          <NGridItem>
            <NCard size="small">
              <NStatistic label="已启用" :value="stats.enabled">
                <template #suffix>
                  <span style="color: #18a058; font-size: 14px">/ {{ stats.total }}</span>
                </template>
              </NStatistic>
            </NCard>
          </NGridItem>
          <NGridItem>
            <NCard size="small">
              <NStatistic label="已禁用" :value="stats.disabled" />
            </NCard>
          </NGridItem>
          <NGridItem>
            <NCard size="small">
              <NStatistic label="分类数" :value="stats.categories" />
            </NCard>
          </NGridItem>
          <NGridItem>
            <NCard size="small">
              <NStatistic label="工具源数" :value="stats.sourceCount" />
            </NCard>
          </NGridItem>
        </NGrid>

        <!-- 筛选栏 -->
        <div class="filter-bar" style="margin-bottom: 12px">
          <NInput
            v-model:value="searchText"
            placeholder="搜索工具名称或描述..."
            clearable
            size="small"
            style="width: 220px"
          />
          <NSelect
            v-model:value="filterCategory"
            :options="categoryOptions"
            placeholder="分类"
            clearable
            size="small"
            style="width: 110px"
          />
          <NSelect
            v-model:value="filterSource"
            :options="sourceOptions"
            placeholder="来源"
            clearable
            size="small"
            style="width: 110px"
          />
          <NSelect
            v-model:value="filterStatus"
            :options="statusOptions"
            placeholder="状态"
            clearable
            size="small"
            style="width: 100px"
          />
        </div>

        <!-- 工具列表 -->
        <NSpin :show="loading">
          <NEmpty v-if="!loading && filteredTools.length === 0" description="没有找到匹配的工具" />

          <NGrid :cols="3" :x-gap="12" :y-gap="12" v-else>
            <NGridItem v-for="tool in filteredTools" :key="tool.name">
              <NCard hoverable size="small" class="tool-card">
                <template #header>
                  <div class="tool-header">
                    <img
                      v-if="getPluginAvatar(tool)"
                      :src="getPluginAvatar(tool)"
                      class="tool-plugin-avatar"
                    />
                    <span class="tool-name">{{ (tool as any).display_name || tool.name }}</span>
                    <NTag :type="getCategoryTag(tool.category).type" size="small">
                      {{ getCategoryTag(tool.category).label }}
                    </NTag>
                    <NTag
                      v-if="tool.source_type === 'plugin'"
                      size="small"
                      :bordered="false"
                      :color="{ textColor: '#8b5cf6' }"
                    >
                      插件: {{ getPluginTitle(tool) }}
                    </NTag>
                    <NTag v-else :type="getSourceTag(tool.source_type).type" size="small" :bordered="false">
                      {{ getSourceTag(tool.source_type).label }}
                    </NTag>
                  </div>
                </template>
                <template #header-extra>
                  <NTooltip>
                    <template #trigger>
                      <NSwitch
                        :value="tool.enabled"
                        :loading="togglingTools.has(tool.name)"
                        @update:value="handleToggle(tool)"
                        :rail-style="({ checked }: { checked: boolean }) => checked ? { background: '#18a058' } : { background: '#d03050' }"
                      />
                    </template>
                    {{ tool.enabled ? '点击禁用' : '点击启用' }}
                  </NTooltip>
                </template>

                <code v-if="(tool as any).display_name" class="tool-name-code">{{ tool.name }}</code>
                <p class="tool-desc">{{ tool.description }}</p>

                <div class="tool-params" v-if="tool.parameters && tool.parameters.length > 0">
                  <div class="params-label">参数：</div>
                  <div class="params-list">
                    <NTooltip v-for="param in tool.parameters" :key="param.name">
                      <template #trigger>
                        <NTag
                          size="small"
                          :bordered="false"
                          :type="param.required ? 'primary' : 'default'"
                          class="param-tag"
                        >
                          {{ param.name }}
                          <span v-if="param.required" class="required-mark">*</span>
                        </NTag>
                      </template>
                      <div>
                        <div><strong>{{ param.name }}</strong> ({{ param.type }})</div>
                        <div>{{ param.description }}</div>
                        <div v-if="param.required" style="color: #f0a020">必填</div>
                        <div v-if="param.default != null">默认值: {{ param.default }}</div>
                      </div>
                    </NTooltip>
                  </div>
                </div>

                <template #footer>
                  <div class="tool-footer">
                    <NTag :type="tool.enabled ? 'success' : 'error'" size="small">
                      {{ tool.enabled ? '已启用' : '已禁用' }}
                    </NTag>
                    <NSpace size="small">
                      <NButton text size="small" @click="showDetail(tool.name)">详情</NButton>
                      <NPopconfirm
                        v-if="tool.source_type !== 'builtin'"
                        @positive-click="handleDeleteTool(tool.name)"
                      >
                        <template #trigger>
                          <NButton text size="small" type="error">删除</NButton>
                        </template>
                        确定要删除工具 {{ tool.name }}？
                      </NPopconfirm>
                    </NSpace>
                  </div>
                </template>
              </NCard>
            </NGridItem>
          </NGrid>
        </NSpin>
      </NTabPane>

      <!-- ==================== Tab 2: 工具源管理 ==================== -->
      <NTabPane name="sources" tab="工具源管理">
        <NSpin :show="sourcesLoading">
          <NEmpty v-if="!sourcesLoading && toolSources.length === 0" description="暂无工具源" />

          <NDataTable
            v-else
            :columns="sourceColumns"
            :data="toolSources"
            :bordered="false"
            striped
          />
        </NSpin>
      </NTabPane>

      <!-- ==================== Tab 3: 插件市场 ==================== -->
      <NTabPane name="market" tab="插件市场">
        <!-- 搜索和过滤 -->
        <div class="filter-bar" style="margin-bottom: 12px">
          <NInput
            v-model:value="marketSearch"
            placeholder="搜索插件名称或描述..."
            clearable
            size="small"
            style="width: 250px"
            @keyup.enter="handleMarketSearch"
            @clear="handleMarketSearch"
          />
          <NRadioGroup
            :value="marketStatusFilter || 'all'"
            size="small"
            @update:value="(v: string) => handleMarketStatusFilter(v === 'all' ? null : v)"
          >
            <NRadio value="all">全部</NRadio>
            <NRadio value="installed">已安装</NRadio>
            <NRadio value="not_installed">未安装</NRadio>
          </NRadioGroup>
          <NButton size="small" @click="handleMarketSearch">搜索</NButton>
        </div>

        <!-- 标签导航 -->
        <div class="tag-nav-scroll">
          <NTag
            :type="!marketTagFilter ? 'primary' : 'default'"
            size="small"
            :bordered="!!marketTagFilter"
            style="cursor: pointer; flex-shrink: 0"
            @click="handleMarketTagFilter(null)"
          >
            全部:{{ marketTotal }}
          </NTag>
          <NTag
            v-for="tag in marketTags.slice(0, 20)"
            :key="tag.name"
            :type="marketTagFilter === tag.name ? 'primary' : 'default'"
            size="small"
            :bordered="marketTagFilter !== tag.name"
            style="cursor: pointer; flex-shrink: 0"
            @click="handleMarketTagFilter(marketTagFilter === tag.name ? null : tag.name)"
          >
            {{ tag.name }}:{{ tag.count }}
          </NTag>
        </div>

        <!-- 骨架屏加载状态 -->
        <template v-if="marketLoading">
          <NGrid :cols="3" :x-gap="12" :y-gap="12">
            <NGridItem v-for="i in 6" :key="i">
              <NCard size="small">
                <NSkeleton text style="width: 60%" />
                <NSkeleton text :repeat="2" />
                <NSkeleton text style="width: 40%" />
              </NCard>
            </NGridItem>
          </NGrid>
        </template>

        <!-- 空状态 -->
        <NEmpty v-else-if="marketPlugins.length === 0" description="未找到匹配的插件" />

        <!-- 插件卡片网格 -->
        <template v-else>
          <NGrid :cols="3" :x-gap="12" :y-gap="12">
            <NGridItem v-for="plugin in marketPlugins" :key="plugin.identifier">
              <NCard hoverable size="small" class="plugin-card">
                <template #header>
                  <div class="plugin-header">
                    <img
                      v-if="plugin.meta.avatar"
                      :src="plugin.meta.avatar"
                      class="plugin-avatar"
                      @error="($event.target as HTMLImageElement).style.display='none'"
                    />
                    <span class="plugin-title">{{ plugin.meta.title || plugin.identifier }}</span>
                  </div>
                </template>

                <div class="plugin-card-body">
                  <p class="plugin-desc">{{ plugin.meta.description }}</p>

                  <div class="plugin-card-bottom">
                    <div class="plugin-tags" v-if="plugin.meta.tags && plugin.meta.tags.length">
                      <NTag v-for="tag in plugin.meta.tags.slice(0, 3)" :key="tag" size="tiny" :bordered="false">
                        {{ tag }}
                      </NTag>
                    </div>

                    <div class="plugin-meta-row">
                      <span v-if="plugin.meta.author" class="plugin-author">
                        <span style="color: #bbb">by</span> {{ plugin.meta.author }}
                      </span>
                    </div>

                    <div class="plugin-actions">
                      <template v-if="plugin.installed">
                        <NTag type="success" size="small" :bordered="false">已安装</NTag>
                        <NButton text size="small" type="primary" @click="openSettingsDrawer(plugin.source_id!, plugin.meta.title)">
                          设置
                        </NButton>
                        <NButton text size="small" type="error" @click="openUninstallModal(plugin.source_id!, plugin.meta.title, 0)">
                          卸载
                        </NButton>
                      </template>
                      <template v-else>
                        <NButton
                          type="primary"
                          size="small"
                          :loading="installingIds.has(plugin.identifier)"
                          @click="handleInstallFromIndex(plugin)"
                        >
                          安装
                        </NButton>
                      </template>
                    </div>
                  </div>
                </div>
              </NCard>
            </NGridItem>
          </NGrid>

          <!-- 分页 -->
          <div style="margin-top: 16px; display: flex; justify-content: center;" v-if="marketTotal > marketPageSize">
            <NPagination
              :page="marketPage"
              :page-size="marketPageSize"
              :item-count="marketTotal"
              @update:page="handleMarketPageChange"
            />
          </div>
        </template>

        <!-- 自定义 URL 导入 -->
        <NCard size="small" style="margin-top: 20px">
          <template #header>
            <span style="font-size: 13px; font-weight: 500">从 Manifest URL 导入插件</span>
          </template>
          <div style="display: flex; gap: 8px; align-items: center;">
            <NInput
              v-model:value="customManifestUrl"
              placeholder="https://example.com/manifest.json"
              style="flex: 1"
              size="small"
              @keydown.enter="handleCustomPreview"
            />
            <NButton size="small" @click="handleCustomPreview" :loading="customPreviewLoading" :disabled="!customManifestUrl.trim()">
              预览
            </NButton>
            <NButton size="small" type="primary" @click="handleCustomPreview" :loading="customPreviewLoading" :disabled="!customManifestUrl.trim()">
              安装
            </NButton>
          </div>
          <p style="margin: 6px 0 0; font-size: 12px; color: #aaa">
            支持 LobeChat Manifest / OpenAI Plugin Manifest 格式
          </p>
        </NCard>
      </NTabPane>
    </NTabs>

    <!-- ==================== 工具详情 Drawer ==================== -->
    <NDrawer v-model:show="detailDrawerVisible" :width="520" placement="right">
      <NDrawerContent title="工具详情" closable>
        <NSpin :show="detailLoading">
          <template v-if="toolDetail">
            <NDescriptions label-placement="left" :column="1" bordered size="small" :label-style="{ width: '80px', whiteSpace: 'nowrap' }">
              <NDescriptionsItem label="名称">
                <code>{{ toolDetail.name }}</code>
              </NDescriptionsItem>
              <NDescriptionsItem label="描述">{{ toolDetail.description }}</NDescriptionsItem>
              <NDescriptionsItem label="显示名">
                <div style="display: flex; align-items: center; gap: 8px">
                  <NInput
                    v-model:value="editDisplayName"
                    placeholder="输入中文显示名..."
                    size="small"
                    style="flex: 1"
                    clearable
                  />
                  <NButton
                    size="small"
                    type="primary"
                    :loading="savingDisplayName"
                    @click="handleSaveDisplayName"
                  >
                    保存
                  </NButton>
                </div>
              </NDescriptionsItem>
              <NDescriptionsItem label="分类">
                <NTag :type="getCategoryTag(toolDetail.category).type" size="small">
                  {{ getCategoryTag(toolDetail.category).label }}
                </NTag>
              </NDescriptionsItem>
              <NDescriptionsItem label="来源">
                <NTag
                  v-if="toolDetail.source_type === 'plugin'"
                  size="small"
                  :color="{ textColor: '#8b5cf6', borderColor: '#8b5cf6' }"
                  style="cursor: pointer"
                  @click="() => { detailDrawerVisible = false; openSettingsDrawer(toolDetail!.source_id || '', toolDetail!.source_name || toolDetail!.name.split('__')[0]) }"
                >
                  插件: {{ toolDetail.source_name || toolDetail.name.split('__')[0] }}
                </NTag>
                <NTag v-else :type="getSourceTag(toolDetail.source_type).type" size="small">
                  {{ getSourceTag(toolDetail.source_type).label }}
                </NTag>
              </NDescriptionsItem>
              <NDescriptionsItem label="状态">
                <NTag :type="toolDetail.enabled ? 'success' : 'error'" size="small">
                  {{ toolDetail.enabled ? '已启用' : '已禁用' }}
                </NTag>
              </NDescriptionsItem>
              <NDescriptionsItem label="返回类型">{{ toolDetail.returns }}</NDescriptionsItem>
              <NDescriptionsItem v-if="toolDetail.http_method" label="HTTP 方法">
                {{ toolDetail.http_method }}
              </NDescriptionsItem>
              <NDescriptionsItem v-if="toolDetail.http_path" label="HTTP 路径">
                <code>{{ toolDetail.http_path }}</code>
              </NDescriptionsItem>
            </NDescriptions>

            <h4 style="margin: 16px 0 8px">参数定义</h4>
            <NDataTable
              v-if="toolDetail.parameters && toolDetail.parameters.length"
              :columns="paramColumns"
              :data="toolDetail.parameters"
              :bordered="false"
              size="small"
              :pagination="false"
            />
            <NEmpty v-else description="无参数" size="small" />

            <h4 style="margin: 16px 0 8px">关联 Skills</h4>
            <div v-if="toolDetail.used_by_skills && toolDetail.used_by_skills.length" class="skill-tags">
              <NTag
                v-for="skill in toolDetail.used_by_skills"
                :key="skill.name"
                size="small"
                type="primary"
                :bordered="false"
              >
                {{ skill.display_name || skill.name }}
              </NTag>
            </div>
            <NEmpty v-else description="暂无关联 Skill" size="small" />
          </template>
        </NSpin>
      </NDrawerContent>
    </NDrawer>

    <!-- ==================== 创建工具源 Drawer ==================== -->
    <NDrawer v-model:show="createDrawerVisible" :width="520" placement="right">
      <NDrawerContent title="添加工具源" closable>
        <NForm label-placement="top">
          <NFormItem label="源类型">
            <NRadioGroup v-model:value="createForm.type">
              <NRadio value="openapi">OpenAPI</NRadio>
              <NRadio value="mcp">MCP</NRadio>
              <NRadio value="client">Client</NRadio>
            </NRadioGroup>
          </NFormItem>

          <NFormItem label="名称">
            <NInput v-model:value="createForm.name" placeholder="如: Petstore API" />
          </NFormItem>

          <!-- OpenAPI 配置 -->
          <template v-if="createForm.type === 'openapi'">
            <NFormItem label="Spec 来源">
              <NRadioGroup v-model:value="createForm.specSource">
                <NRadio value="url">URL</NRadio>
                <NRadio value="content">粘贴内容</NRadio>
              </NRadioGroup>
            </NFormItem>

            <NFormItem v-if="createForm.specSource === 'url'" label="Spec URL">
              <NInput v-model:value="createForm.specUrl" placeholder="https://petstore3.swagger.io/api/v3/openapi.json" />
            </NFormItem>

            <NFormItem v-else label="Spec 内容 (JSON/YAML)">
              <NInput
                v-model:value="createForm.specContent"
                type="textarea"
                :autosize="{ minRows: 6, maxRows: 12 }"
                placeholder='粘贴 OpenAPI JSON 或 YAML 内容...'
              />
            </NFormItem>

            <NFormItem label="Base URL (可选)">
              <NInput v-model:value="createForm.baseUrl" placeholder="留空自动从 spec 提取" />
            </NFormItem>

            <NFormItem label="认证类型 (可选)">
              <NSelect
                v-model:value="createForm.authType"
                :options="[
                  { label: '无认证', value: '' },
                  { label: 'Bearer Token', value: 'bearer' },
                  { label: 'API Key', value: 'api_key' },
                  { label: 'Basic Auth', value: 'basic' },
                ]"
                clearable
              />
            </NFormItem>

            <NFormItem v-if="createForm.authType === 'bearer'" label="Token">
              <NInput v-model:value="createForm.authToken" type="password" show-password-on="click" placeholder="Bearer Token" />
            </NFormItem>
            <template v-if="createForm.authType === 'api_key'">
              <NFormItem label="API Key">
                <NInput v-model:value="createForm.authKey" type="password" show-password-on="click" placeholder="API Key" />
              </NFormItem>
              <NFormItem label="Header 名称">
                <NInput v-model:value="createForm.authHeader" placeholder="X-API-Key" />
              </NFormItem>
            </template>
            <template v-if="createForm.authType === 'basic'">
              <NFormItem label="用户名">
                <NInput v-model:value="createForm.authUsername" placeholder="用户名" />
              </NFormItem>
              <NFormItem label="密码">
                <NInput v-model:value="createForm.authPassword" type="password" show-password-on="click" placeholder="密码" />
              </NFormItem>
            </template>
          </template>

          <!-- MCP 配置 -->
          <template v-if="createForm.type === 'mcp'">
            <NFormItem label="Transport 类型">
              <NRadioGroup v-model:value="createForm.transport">
                <NRadio value="stdio">Stdio (本地进程)</NRadio>
                <NRadio value="http">HTTP (远程服务)</NRadio>
              </NRadioGroup>
            </NFormItem>

            <template v-if="createForm.transport === 'stdio'">
              <NFormItem label="命令">
                <NInput v-model:value="createForm.command" placeholder="npx" />
              </NFormItem>
              <NFormItem label="参数">
                <NDynamicInput v-model:value="createForm.args" placeholder="输入参数" />
              </NFormItem>
            </template>

            <NFormItem v-if="createForm.transport === 'http'" label="MCP Server URL">
              <NInput v-model:value="createForm.mcpUrl" placeholder="http://localhost:3000" />
            </NFormItem>
          </template>

          <!-- Client 无额外配置 -->
          <template v-if="createForm.type === 'client'">
            <NCard size="small" style="background: #f8f8f8">
              <p style="margin: 0; color: #666; font-size: 13px">
                Client 类型工具源创建后，需要手动添加工具定义。工具的实际执行逻辑由前端/调用方实现。
              </p>
            </NCard>
          </template>
        </NForm>

        <template #footer>
          <NSpace>
            <NButton @click="createDrawerVisible = false">取消</NButton>
            <NButton type="primary" :loading="createLoading" @click="handleCreate">
              {{ createForm.type === 'mcp' ? '创建并连接' : '创建并解析' }}
            </NButton>
          </NSpace>
        </template>
      </NDrawerContent>
    </NDrawer>

    <!-- ==================== 创建 Client 工具 Drawer ==================== -->
    <NDrawer v-model:show="clientToolDrawerVisible" :width="480" placement="right">
      <NDrawerContent title="添加工具 (Client)" closable>
        <NForm label-placement="top">
          <NFormItem label="工具名称">
            <NInput v-model:value="clientToolForm.name" placeholder="如: my_custom_tool" />
          </NFormItem>
          <NFormItem label="描述">
            <NInput
              v-model:value="clientToolForm.description"
              type="textarea"
              :autosize="{ minRows: 2, maxRows: 4 }"
              placeholder="工具功能描述"
            />
          </NFormItem>
          <NFormItem label="参数 Schema (JSON)">
            <NInput
              v-model:value="clientToolForm.schemaJson"
              type="textarea"
              :autosize="{ minRows: 6, maxRows: 12 }"
              placeholder='{"type": "object", "properties": {...}, "required": [...]}'
              style="font-family: monospace"
            />
          </NFormItem>
        </NForm>

        <template #footer>
          <NSpace>
            <NButton @click="clientToolDrawerVisible = false">取消</NButton>
            <NButton type="primary" @click="handleCreateClientTool">创建工具</NButton>
          </NSpace>
        </template>
      </NDrawerContent>
    </NDrawer>

    <!-- ==================== 安装插件 Drawer ==================== -->
    <NDrawer v-model:show="installDrawerVisible" :width="520" placement="right">
      <NDrawerContent closable>
        <template #header>
          {{ installResultVisible ? (installResult?.success ? '安装成功' : '安装失败') : '安装插件' }}
        </template>

        <!-- 安装结果 -->
        <template v-if="installResultVisible && installResult">
          <div v-if="installResult.success" style="text-align: center; padding: 20px 0;">
            <div style="font-size: 48px; margin-bottom: 12px;">&#10003;</div>
            <h3>插件安装成功</h3>
            <p>已注册 {{ installResult.tools_count }} 个工具</p>

            <div v-if="installResult.tool_names && installResult.tool_names.length" style="text-align: left; margin-top: 16px;">
              <h4 style="margin-bottom: 8px">已注册工具：</h4>
              <div v-for="name in installResult.tool_names" :key="name" style="padding: 4px 0;">
                <code>{{ name }}</code>
              </div>
            </div>

            <NAlert type="success" style="margin-top: 16px; text-align: left;">
              这些工具已在用户对话中自动可用。例如用户可以直接向智能助手提问来触发相关工具。如需仅在特定 Skill 中使用，可在「Skill 管理」中配置。
            </NAlert>
          </div>

          <div v-else style="text-align: center; padding: 20px 0;">
            <div style="font-size: 48px; margin-bottom: 12px; color: #d03050;">&#10007;</div>
            <h3>安装失败</h3>
            <NAlert type="error" style="margin-top: 12px; text-align: left;">
              {{ installResult.error }}
            </NAlert>
          </div>
        </template>

        <!-- 预览内容 -->
        <template v-else>
          <NSpin :show="installPreviewLoading">
            <template v-if="installPreview">
              <!-- 基本信息 -->
              <div class="plugin-header" style="margin-bottom: 16px;">
                <img
                  v-if="installPreview.meta.avatar"
                  :src="installPreview.meta.avatar"
                  class="plugin-avatar"
                  style="width: 40px; height: 40px;"
                />
                <div>
                  <h3 style="margin: 0;">{{ installPreview.meta.title }}</h3>
                  <p style="margin: 4px 0 0; color: #999; font-size: 13px;">
                    {{ installPreview.meta.description }}
                  </p>
                  <span style="font-size: 12px; color: #bbb;">
                    {{ installPreview.meta.author }} | 格式: {{ installPreview.manifest_format }}
                  </span>
                  <span
                    v-if="installPreview.plugin_type === 'standalone'"
                    style="display: inline-block; font-size: 11px; padding: 1px 6px; border-radius: 4px; background: #fa8c16; color: #fff; margin-left: 6px;"
                  >standalone</span>
                </div>
              </div>

              <!-- 安全信息 -->
              <NAlert type="warning" style="margin-bottom: 16px;">
                <template #header>该插件将请求以下外部域名</template>
                <div v-for="domain in installPreview.external_domains" :key="domain">
                  {{ domain }}
                </div>
              </NAlert>

              <!-- 工具列表 -->
              <h4 style="margin: 0 0 8px;">将注册 {{ installPreview.tools.length }} 个工具</h4>
              <div v-for="tool in installPreview.tools" :key="tool.name" style="padding: 6px 0; border-bottom: 1px solid #f0f0f0;">
                <div>
                  <code>{{ tool.name }}</code>
                  <span
                    v-if="tool.exec_type === 'client'"
                    style="font-size: 10px; padding: 1px 4px; border-radius: 3px; background: #fa8c16; color: #fff; margin-left: 4px;"
                  >iframe</span>
                </div>
                <div style="font-size: 12px; color: #666;">{{ tool.description }}</div>
                <div style="font-size: 11px; color: #bbb;">
                  <template v-if="tool.exec_type === 'client'">
                    客户端 iframe 执行
                  </template>
                  <template v-else>
                    {{ tool.endpoint.method }} {{ tool.endpoint.url }}
                  </template>
                </div>
              </div>

              <!-- 设置提示 -->
              <div v-if="installPreview.settings_schema" style="margin-top: 12px;">
                <NAlert type="info">
                  该插件有可选设置项，安装后可在工具源管理中配置。
                </NAlert>
              </div>

              <!-- standalone 插件注意事项 -->
              <div v-if="installPreview.plugin_type === 'standalone' && installPreview.ui_url" style="margin-top: 12px;">
                <NAlert type="warning">
                  <template #header>Standalone 客户端插件</template>
                  此插件在用户浏览器中通过 iframe 执行，需要用户网络可达插件服务器（{{ installPreview.ui_url }}）。国内网络可能无法访问部分海外插件。
                </NAlert>
              </div>
            </template>
          </NSpin>
        </template>

        <template #footer>
          <NSpace>
            <NButton @click="installDrawerVisible = false">
              {{ installResultVisible ? '关闭' : '取消' }}
            </NButton>
            <NButton
              v-if="!installResultVisible && installPreview"
              type="primary"
              :loading="installTarget ? installingIds.has(installTarget.identifier) : customPreviewLoading"
              @click="installTarget && installTarget.manifest_url !== customManifestUrl ? confirmInstall() : confirmCustomInstall()"
            >
              确认安装
            </NButton>
          </NSpace>
        </template>
      </NDrawerContent>
    </NDrawer>

    <!-- ==================== 卸载确认 Modal ==================== -->
    <NModal v-model:show="uninstallModalVisible" preset="dialog" title="卸载插件" type="warning">
      <template v-if="uninstallTarget">
        <p>确定要卸载插件「{{ uninstallTarget.name }}」吗？</p>
        <p style="color: #999; font-size: 13px;">
          这将删除该插件的所有工具，已配置此工具的 Skill 将无法继续使用。
        </p>
      </template>
      <template #action>
        <NSpace>
          <NButton @click="uninstallModalVisible = false">取消</NButton>
          <NButton type="error" @click="confirmUninstall">确认卸载</NButton>
        </NSpace>
      </template>
    </NModal>

    <!-- ==================== 插件设置 Drawer ==================== -->
    <NDrawer v-model:show="settingsDrawerVisible" :width="520" placement="right">
      <NDrawerContent closable>
        <template #header>插件设置: {{ settingsSourceName }}</template>

        <NSpin :show="settingsLoading">
          <!-- 插件信息 -->
          <div v-if="settingsMeta.title" class="plugin-header" style="margin-bottom: 16px;">
            <img
              v-if="settingsMeta.avatar"
              :src="settingsMeta.avatar"
              class="plugin-avatar"
              style="width: 36px; height: 36px;"
              @error="($event.target as HTMLImageElement).style.display='none'"
            />
            <div>
              <h4 style="margin: 0;">{{ settingsMeta.title }}</h4>
              <p style="margin: 2px 0 0; color: #999; font-size: 12px;">{{ settingsMeta.description }}</p>
            </div>
          </div>

          <!-- 基本信息 -->
          <NDescriptions label-placement="left" :column="1" bordered size="small" style="margin-bottom: 16px;">
            <NDescriptionsItem label="标识符">
              <code>{{ settingsIdentifier }}</code>
            </NDescriptionsItem>
            <NDescriptionsItem label="来源">
              {{ settingsOrigin === 'lobechat' ? 'LobeChat 插件市场' : settingsOrigin || '-' }}
            </NDescriptionsItem>
            <NDescriptionsItem label="Manifest">
              <span style="font-size: 11px; word-break: break-all;">{{ settingsManifestUrl || '-' }}</span>
            </NDescriptionsItem>
            <NDescriptionsItem label="格式">
              <NTag size="small" :bordered="false">{{ settingsManifestFormat || '-' }}</NTag>
            </NDescriptionsItem>
            <NDescriptionsItem v-if="settingsCreatedAt" label="安装时间">
              {{ new Date(settingsCreatedAt).toLocaleString() }}
            </NDescriptionsItem>
          </NDescriptions>

          <!-- 设置表单 -->
          <template v-if="settingsSchema && settingsSchema.properties">
            <h4 style="margin: 0 0 12px;">配置项</h4>
            <NForm label-placement="top" size="small">
              <NFormItem
                v-for="field in getSettingsFields(settingsSchema)"
                :key="field.key"
                :label="field.label + (field.required ? ' *' : '')"
              >
                <!-- string + enum → NSelect -->
                <NSelect
                  v-if="field.type === 'string' && field.enum"
                  :value="settingsValues[field.key]"
                  @update:value="(v: string) => settingsValues[field.key] = v"
                  :options="field.enum.map((e: string) => ({ label: e, value: e }))"
                  clearable
                  :placeholder="field.description || `选择 ${field.label}`"
                />
                <!-- string + password format → NInput type=password -->
                <NInput
                  v-else-if="field.type === 'string' && field.format === 'password'"
                  :value="settingsValues[field.key] || ''"
                  @update:value="(v: string) => settingsValues[field.key] = v"
                  type="password"
                  show-password-on="click"
                  :placeholder="field.description || `输入 ${field.label}`"
                />
                <!-- string → NInput -->
                <NInput
                  v-else-if="field.type === 'string'"
                  :value="settingsValues[field.key] || ''"
                  @update:value="(v: string) => settingsValues[field.key] = v"
                  :placeholder="field.description || `输入 ${field.label}`"
                />
                <!-- number / integer → NInputNumber -->
                <NInputNumber
                  v-else-if="field.type === 'number' || field.type === 'integer'"
                  :value="settingsValues[field.key]"
                  @update:value="(v: number | null) => settingsValues[field.key] = v"
                  :placeholder="field.description || `输入 ${field.label}`"
                  style="width: 100%"
                />
                <!-- boolean → NSwitch -->
                <NSwitch
                  v-else-if="field.type === 'boolean'"
                  :value="!!settingsValues[field.key]"
                  @update:value="(v: boolean) => settingsValues[field.key] = v"
                />
                <!-- fallback → NInput -->
                <NInput
                  v-else
                  :value="settingsValues[field.key] || ''"
                  @update:value="(v: string) => settingsValues[field.key] = v"
                  :placeholder="field.description || `输入 ${field.label}`"
                />
                <template #feedback v-if="field.description && field.type !== 'boolean'">
                  <span style="font-size: 11px; color: #999;">{{ field.description }}</span>
                </template>
              </NFormItem>
            </NForm>
          </template>

          <!-- 工具列表 -->
          <div style="margin-top: 16px;" v-if="settingsTools.length > 0">
            <h4 style="margin: 0 0 12px;">工具列表 ({{ settingsTools.length }})</h4>
            <div
              v-for="t in settingsTools"
              :key="t.name"
              style="display: flex; justify-content: space-between; align-items: center; padding: 8px 0; border-bottom: 1px solid #f5f5f5;"
            >
              <div style="flex: 1; min-width: 0;">
                <div><code style="font-size: 12px;">{{ t.name }}</code></div>
                <div style="font-size: 11px; color: #999; margin-top: 2px;">{{ t.description }}</div>
                <div v-if="t.http_method && t.http_path" style="font-size: 10px; color: #ccc; margin-top: 1px;">
                  {{ t.http_method }} {{ t.http_path }}
                </div>
              </div>
              <NSwitch
                :value="t.is_enabled"
                :loading="settingsTogglingTools.has(t.name)"
                @update:value="(v: boolean) => handleTogglePluginTool(t.name, v)"
                size="small"
              />
            </div>
          </div>

          <!-- 操作区域 -->
          <div style="margin-top: 24px; padding-top: 16px; border-top: 1px solid #f0f0f0;">
            <h4 style="margin: 0 0 12px;">操作</h4>
            <NSpace>
              <NButton
                size="small"
                :loading="settingsResyncLoading"
                @click="handleResync"
              >
                重新同步 Manifest
              </NButton>
              <NButton
                size="small"
                type="error"
                @click="handleUninstallFromSettings"
              >
                卸载插件
              </NButton>
            </NSpace>
          </div>
        </NSpin>

        <template #footer>
          <NSpace>
            <NButton @click="settingsDrawerVisible = false">
              {{ settingsSchema && settingsSchema.properties ? '取消' : '关闭' }}
            </NButton>
            <NButton
              v-if="settingsSchema && settingsSchema.properties"
              type="primary"
              :loading="settingsSaving"
              @click="saveSettings"
            >
              保存设置
            </NButton>
          </NSpace>
        </template>
      </NDrawerContent>
    </NDrawer>
  </div>
</template>

<style scoped>
.tool-management {
  width: 100%;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 20px;
}

.page-title {
  margin: 0 0 4px 0;
  font-size: 22px;
  font-weight: 600;
  color: #1a1a1a;
}

.page-desc {
  margin: 0;
  font-size: 14px;
  color: #999;
}

.filter-bar {
  display: flex;
  gap: 12px;
  align-items: center;
}

.tool-card {
  transition: box-shadow 0.2s;
}

.tool-card:hover {
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
}

.tool-header {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.tool-name {
  font-weight: 600;
  font-size: 13px;
}
.tool-name-code {
  display: block;
  font-size: 11px;
  color: #aaa;
  margin-bottom: 2px;
  font-family: 'SF Mono', 'Monaco', 'Consolas', monospace;
}

.tool-desc {
  color: #666;
  font-size: 12px;
  margin: 0 0 8px 0;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.tool-params {
  display: flex;
  gap: 8px;
  align-items: flex-start;
}

.params-label {
  font-size: 12px;
  color: #999;
  flex-shrink: 0;
  line-height: 24px;
}

.params-list {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.param-tag {
  cursor: help;
}

.required-mark {
  color: #d03050;
  margin-left: 2px;
}

.tool-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.skill-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.tag-nav-scroll {
  display: flex;
  gap: 6px;
  margin-bottom: 16px;
  overflow-x: auto;
  padding-bottom: 4px;
  scrollbar-width: thin;
}

.tag-nav-scroll::-webkit-scrollbar {
  height: 4px;
}

.tag-nav-scroll::-webkit-scrollbar-thumb {
  background: #ddd;
  border-radius: 2px;
}

.plugin-card {
  transition: box-shadow 0.2s;
  height: 100%;
}

.plugin-card :deep(.n-card__content) {
  flex: 1;
  display: flex;
  flex-direction: column;
  padding-bottom: 12px !important;
}

.plugin-card :deep(.n-card__footer) {
  display: none;
}

.plugin-card:hover {
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
}

.plugin-header {
  display: flex;
  align-items: center;
  gap: 8px;
}

.plugin-avatar {
  width: 28px;
  height: 28px;
  border-radius: 6px;
  object-fit: cover;
}

.tool-plugin-avatar {
  width: 20px;
  height: 20px;
  border-radius: 4px;
  object-fit: cover;
  flex-shrink: 0;
}

.plugin-title {
  font-weight: 600;
  font-size: 14px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.plugin-card-body {
  display: flex;
  flex-direction: column;
  flex: 1;
}

.plugin-desc {
  color: #666;
  font-size: 12px;
  margin: 0 0 auto 0;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  min-height: 36px;
}

.plugin-card-bottom {
  margin-top: 10px;
}

.plugin-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 6px;
}

.plugin-meta-row {
  font-size: 12px;
  color: #999;
  margin-bottom: 10px;
}

.plugin-author {
  color: #888;
}

.plugin-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.tag-nav {
  scrollbar-width: thin;
}

.tag-nav::-webkit-scrollbar {
  height: 4px;
}

.tag-nav::-webkit-scrollbar-thumb {
  background: #ddd;
  border-radius: 2px;
}
</style>
