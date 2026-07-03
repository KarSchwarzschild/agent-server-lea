<script setup lang="ts">
/**
 * 创建/编辑订阅对话框
 * 创建模式: Step 1 自然语言输入 + AI 解析 → Step 2 确认配置
 * 编辑模式: 直接进入 Step 2（配置表单），提交调用 updateSubscription
 */
import { ref, computed, watch, onMounted } from 'vue'
import {
  NModal,
  NCard,
  NInput,
  NButton,
  NSpace,
  NTag,
  NSpin,
  NIcon,
  NSelect,
  NCheckbox,
  NCheckboxGroup,
  NCollapse,
  NCollapseItem,
  NInputNumber,
  NAlert,
  NSlider,
  NRadioGroup,
  NRadio,
  NSteps,
  NStep,
  NDrawer,
  NDrawerContent,
  useMessage,
  useDialog,
} from 'naive-ui'
import {
  SparklesOutline,
  ArrowForwardOutline,
  ArrowBackOutline,
  CheckmarkOutline,
} from '@vicons/ionicons5'
import {
  parseSubscription,
  createSubscriptionFromConfig,
  updateSubscription,
  triggerSubscription,
  resolveBuiltinSources,
  getBuiltinSources,
  type ParseResult,
  type Subscription,
  type SourceSelection,
  type BuiltinSource,
} from '../../api/subscription'
import {
  getTemplates,
  installTemplate,
  type SubscriptionTemplate,
} from '../../api/subscriptionTemplate'
import { getWebhooks, type Webhook } from '../../api/webhook'
import { useResponsive } from '../../composables/useResponsive'
import SourceSelector from './SourceSelector.vue'

const props = defineProps<{
  show: boolean
  editSubscription?: Subscription | null
}>()

const emit = defineEmits<{
  (e: 'update:show', value: boolean): void
  (e: 'success'): void
}>()

const message = useMessage()
const dialog = useDialog()
const { isMobile } = useResponsive()

// 编辑模式
const isEditMode = computed(() => !!props.editSubscription)

// 步骤控制
const currentStep = ref(1)
const parsing = ref(false)
const creating = ref(false)

// Step 1: 自然语言输入
const description = ref('')

// Step 2: 解析结果
const parseResult = ref<ParseResult | null>(null)
const editName = ref('')
const editCategory = ref('')
const editKeywords = ref<string[]>([])
const editExecMode = ref('quick')
const editSchedule = ref('daily')
const editScheduleTime = ref('08:00')
const editNotificationChannels = ref<string[]>(['web'])
const editMinRelevanceScore = ref(0.5)
const editRelevanceMode = ref('keyword')

// 用户 Webhook 群机器人
const userWebhooks = ref<Webhook[]>([])
const activeWebhooks = computed(() => userWebhooks.value.filter(w => w.is_active))

// 模板相关
const LAST_TAB_KEY = 'subscription_create_last_tab'
const savedTab = localStorage.getItem(LAST_TAB_KEY) as 'template' | 'custom' | 'source' | null
const createMode = ref<'template' | 'custom' | 'source'>(savedTab || 'template')
const templates = ref<SubscriptionTemplate[]>([])
const loadingTemplates = ref(false)
const installingSlug = ref<string | null>(null)
const templateCategory = ref<string>('all')

// 内置数据源相关
const sourceSelections = ref<SourceSelection[]>([])
const resolvingSources = ref(false)
const builtinSourcesMap = ref<Record<string, BuiltinSource>>({})
// 右侧抽屉（Step 2 追加内置源）
const showSourceDrawer = ref(false)
const drawerSelections = ref<SourceSelection[]>([])
const resolvingDrawer = ref(false)

// 模板安装确认
const selectedTemplate = ref<SubscriptionTemplate | null>(null)
const tmplExecMode = ref('quick')
const tmplSchedule = ref('daily')
const tmplScheduleTime = ref('09:00')
const tmplNotificationChannels = ref<string[]>(['web'])

// 分类选项
const categoryOptions = [
  { label: '学术追踪', value: 'academic' },
  { label: '领域动态', value: 'tech' },
  { label: '热点雷达', value: 'trending' },
  { label: '自定义', value: 'custom' },
]

// 执行模式选项
const execModeOptions = [
  { label: '每日简报 - 快速扫描，生成关键发现摘要', value: 'quick' },
  { label: '每周深度报告 - 深度分析，生成完整研究报告', value: 'deep' },
  { label: '简报 + 周报 - 每日简报 + 每周深度报告', value: 'hybrid' },
]

// 推送频率选项
const scheduleOptions = [
  { label: '每天', value: 'daily' },
  { label: '工作日', value: 'weekday' },
  { label: '每周一', value: 'weekly' },
]

// 时间选项
const timeOptions = [
  { label: '06:00', value: '06:00' },
  { label: '08:00', value: '08:00' },
  { label: '10:00', value: '10:00' },
  { label: '12:00', value: '12:00' },
  { label: '18:00', value: '18:00' },
  { label: '20:00', value: '20:00' },
]

// cron 表达式转换
const scheduleToCron = (schedule: string, time: string): string => {
  const [hour, minute] = time.split(':')
  const map: Record<string, string> = {
    daily: `${minute} ${hour} * * *`,
    weekday: `${minute} ${hour} * * 1-5`,
    weekly: `${minute} ${hour} * * 1`,
  }
  return map[schedule] || map.daily
}

// cron 表达式反向解析为 schedule + time
const cronToSchedule = (cron: string): { schedule: string; time: string } => {
  const parts = cron.trim().split(/\s+/)
  // 格式: minute hour * * dayOfWeek
  if (parts.length < 5) return { schedule: 'daily', time: '08:00' }
  const minute = parts[0].padStart(2, '0')
  const hour = parts[1].padStart(2, '0')
  const dow = parts[4]
  let schedule = 'daily'
  if (dow === '1-5') schedule = 'weekday'
  else if (dow === '1') schedule = 'weekly'
  return { schedule, time: `${hour}:${minute}` }
}

// 工具名规范化（与后端 executor.py TOOL_ALIASES 保持同步）
const TOOL_NAME_ALIASES: Record<string, string> = {
  arxiv_search: 'academic_search',
  arxiv: 'academic_search',
  scholar_search: 'academic_search',
  google_scholar: 'academic_search',
  github_search: 'github_search_repos',
  rss: 'rss_fetch',
}

function normalizeToolName(name: string): string {
  return TOOL_NAME_ALIASES[name] ?? name
}

// 用户端数据源类型（场景化命名）
const userToolTypeOptions = [
  { label: '📰 订阅期刊/网站更新', value: 'rss_fetch' },
  { label: '🎓 学术文献检索', value: 'academic_search' },
  { label: '🌐 全网关键词搜索', value: 'web_search' },
  { label: '💻 GitHub 项目追踪', value: 'github_search_repos' },
  { label: '📄 网页内容抓取', value: 'web_fetch' },
]

// 学术数据源
const userSourceOptions = [
  { label: '自动匹配 (推荐)', value: 'auto' },
  { label: 'arXiv (理工科预印本)', value: 'arxiv' },
  { label: 'Semantic Scholar (英文综合)', value: 'semantic_scholar' },
  { label: '万方数据 (中文期刊/学位论文)', value: 'wanfang' },
  { label: 'OpenAlex (多学科开放数据)', value: 'openalex' },
  { label: 'CrossRef (DOI引用追踪)', value: 'crossref' },
]

function isKnownToolType(name: string): boolean {
  return userToolTypeOptions.some(opt => opt.value === name)
}

// 数据源编辑状态（_source_id 存在时为内置源，只读显示）
const editTools = ref<Array<{ name: string; params: Record<string, any>; _source_id?: string; _source_params?: Record<string, string> }>>([])
const showToolsEditor = ref(false)

const addEditTool = () => {
  editTools.value.push({ name: 'web_search', params: { query: '' } })
}

const removeEditTool = (idx: number) => {
  editTools.value.splice(idx, 1)
}

const onEditToolTypeChange = (idx: number, newType: string) => {
  const defaults: Record<string, Record<string, any>> = {
    rss_fetch: { url: '', max_items: 20 },
    academic_search: { query: '', source: 'auto' },
    web_search: { query: '' },
    github_search_repos: { query: '' },
    web_fetch: { url: '' },
  }
  editTools.value[idx].params = { ...(defaults[newType] || {}) }
}

// 编辑模式：弹窗打开时自动填充数据
watch(() => props.show, (show) => {
  if (show && isEditMode.value && props.editSubscription) {
    const sub = props.editSubscription
    // 直接跳到 Step 2
    currentStep.value = 2
    editName.value = sub.name
    description.value = sub.description || ''
    editCategory.value = sub.category || 'custom'
    editKeywords.value = sub.config?.keywords ? [...sub.config.keywords] : []
    editExecMode.value = sub.exec_mode || 'quick'
    editNotificationChannels.value = sub.notification_channels
      ? [...sub.notification_channels]
      : ['web']
    editMinRelevanceScore.value = sub.config?.min_relevance_score ?? 0.5
    editRelevanceMode.value = sub.config?.relevance_mode || 'keyword'

    // 初始化数据源编辑（含别名规范化）
    editTools.value = (sub.config?.tools || []).map((t: any) => ({
      name: normalizeToolName(t.name),
      params: { ...t.params },
      _source_id: t._source_id,
      _source_params: t._source_params,
    }))

    // 反向解析 cron → schedule + time
    if (sub.cron_job_id) {
      // cron_job_id 可能不含 cron 表达式，尝试从 schedule_time 恢复
    }
    // 尝试从后端返回的 schedule 字段恢复（如果有的话）
    // 否则保持默认值
    // 注：后端 subscription 没有单独的 schedule/schedule_time 字段
    // 这里从 subscription 的元数据中尝试恢复
    const subAny = sub as any
    if (subAny.schedule) {
      const parsed = cronToSchedule(subAny.schedule)
      editSchedule.value = parsed.schedule
      // 只用已有的时间选项
      const matchTime = timeOptions.find(t => t.value === parsed.time)
      editScheduleTime.value = matchTime ? parsed.time : '08:00'
    } else if (subAny.schedule_time) {
      editScheduleTime.value = subAny.schedule_time
    }
  }
})

const modalStyle = computed(() => ({
  width: isMobile.value ? '95vw' : '640px',
  maxWidth: '95vw',
}))

// 加载用户 Webhook 群机器人
const loadWebhooks = async () => {
  try {
    const res = await getWebhooks()
    userWebhooks.value = res.items
  } catch {
    // 静默失败，不影响主流程
  }
}

// 加载模板列表
const loadTemplates = async () => {
  loadingTemplates.value = true
  try {
    const res = await getTemplates()
    templates.value = res.items
  } catch {
    // 静默失败
  } finally {
    loadingTemplates.value = false
  }
}

// 筛选后的模板
const filteredTemplates = computed(() => {
  if (templateCategory.value === 'all') return templates.value
  return templates.value.filter(t => t.category === templateCategory.value)
})

// 选择模板 → 进入确认页
const handleSelectTemplate = (tmpl: SubscriptionTemplate) => {
  selectedTemplate.value = tmpl
  tmplExecMode.value = tmpl.suggested_exec_mode || 'quick'
  if (tmpl.suggested_schedule) {
    const parsed = cronToSchedule(tmpl.suggested_schedule)
    tmplSchedule.value = parsed.schedule
    const matchTime = timeOptions.find(t => t.value === parsed.time)
    tmplScheduleTime.value = matchTime ? parsed.time : '09:00'
  } else {
    tmplSchedule.value = 'daily'
    tmplScheduleTime.value = '09:00'
  }
  tmplNotificationChannels.value = ['web', ...activeWebhooks.value.map(w => String(w.id))]
}

// 确认安装模板
const handleConfirmInstall = async () => {
  if (!selectedTemplate.value) return
  const tmpl = selectedTemplate.value
  installingSlug.value = tmpl.slug
  try {
    const res = await installTemplate(tmpl.slug, {
      exec_mode: tmplExecMode.value,
      schedule: scheduleToCron(tmplSchedule.value, tmplScheduleTime.value),
      notification_channels: tmplNotificationChannels.value,
    })
    message.success(`已创建订阅「${tmpl.name}」`)
    emit('success')
    handleClose()
    if (res.subscription_id) {
      let triggered = false
      dialog.info({
        title: '订阅已创建',
        content: `「${tmpl.name}」将按计划自动执行。是否立即执行一次？`,
        positiveText: '立即执行',
        negativeText: '稍后再说',
        onPositiveClick: async () => {
          if (triggered) return
          triggered = true
          try {
            await triggerSubscription(res.subscription_id)
            message.success('已触发执行')
          } catch {
            triggered = false
            message.warning('触发失败，请稍后在订阅详情中手动触发')
          }
        },
      })
    }
  } catch {
    message.error('安装失败，请重试')
  } finally {
    installingSlug.value = null
  }
}

// 工具 → 数据源标签（含具体来源）
const toolSourceLabel = (tool: { name: string; params?: Record<string, any>; _source_id?: string; _source_params?: Record<string, string> }) => {
  // 优先通过 _source_id 查注册表（内置源）
  if (tool._source_id && builtinSourcesMap.value[tool._source_id]) {
    const source = builtinSourcesMap.value[tool._source_id]
    if (tool._source_params) {
      const paramVal = Object.values(tool._source_params)[0]
      if (paramVal) return `${source.name}:${paramVal}`
    }
    return source.name
  }
  // fallback 到 URL 域名识别逻辑（手动源 + 旧订阅）
  if (tool.name === 'rss_fetch' && tool.params?.url) {
    try {
      const host = new URL(tool.params.url).hostname.replace(/^www\./, '')
      const friendlyNames: Record<string, string> = {
        'rss.arxiv.org': 'arXiv',
        'rsshub.rssforever.com': 'RSSHub',
        'hnrss.org': 'Hacker News',
        'frontendfoc.us': 'Frontend Focus',
        'javascriptweekly.com': 'JS Weekly',
        'ruanyifeng.com': '阮一峰博客',
      }
      return friendlyNames[host] || host
    } catch { return 'RSS' }
  }
  const map: Record<string, string> = {
    academic_search: '学术搜索',
    web_search: '网页搜索',
    github_search_repos: 'GitHub',
    web_fetch: '网页抓取',
  }
  return map[tool.name] || tool.name
}

// 加载内置源 map（用于 Step 2 友好名称展示）
const loadBuiltinSourcesMap = async () => {
  try {
    const res = await getBuiltinSources()
    const map: Record<string, BuiltinSource> = {}
    res.sources.forEach(s => { map[s.id] = s })
    builtinSourcesMap.value = map
  } catch {
    // 静默失败
  }
}

onMounted(() => {
  loadWebhooks()
  loadTemplates()
  loadBuiltinSourcesMap()
})

// 切换 Tab 时记忆到 localStorage
watch(createMode, (val) => {
  localStorage.setItem(LAST_TAB_KEY, val)
})

// Step 1 -> Step 2: 内置数据源模式 resolve
const handleResolveSources = async () => {
  if (sourceSelections.value.length === 0) {
    message.warning('请至少选择一个数据源')
    return
  }
  resolvingSources.value = true
  try {
    const result = await resolveBuiltinSources(sourceSelections.value)
    // 注入 tools（保留 _source_id 等元信息）
    editTools.value = result.tools.map((t: any) => ({
      name: t.name,
      params: { ...t.params },
      _source_id: t._source_id,
      _source_params: t._source_params,
    }))
    editName.value = result.suggested_name || ''
    editCategory.value = result.suggested_category || 'custom'
    currentStep.value = 2
  } catch {
    message.error('解析数据源失败，请重试')
  } finally {
    resolvingSources.value = false
  }
}

// Step 2 抽屉：确认追加内置源
const handleDrawerConfirm = async () => {
  if (drawerSelections.value.length === 0) {
    showSourceDrawer.value = false
    return
  }
  resolvingDrawer.value = true
  try {
    const result = await resolveBuiltinSources(drawerSelections.value)
    // 追加到 editTools（不覆盖已有）
    const newTools = result.tools.map((t: any) => ({
      name: t.name,
      params: { ...t.params },
      _source_id: t._source_id,
      _source_params: t._source_params,
    }))
    editTools.value = [...editTools.value, ...newTools]
    drawerSelections.value = []
    showSourceDrawer.value = false
    message.success(`已追加 ${newTools.length} 个数据源`)
  } catch {
    message.error('解析数据源失败，请重试')
  } finally {
    resolvingDrawer.value = false
  }
}

// Step 1 -> Step 2: AI 解析
const handleParse = async () => {
  if (!description.value.trim()) {
    message.warning('请输入订阅描述')
    return
  }

  parsing.value = true
  try {
    const result = await parseSubscription(description.value)
    parseResult.value = result

    // 填充编辑字段
    editName.value = result.name
    editCategory.value = result.category || 'custom'
    editKeywords.value = result.keywords || []
    editExecMode.value = result.suggested_exec_mode || 'quick'
    if (result.suggested_time) {
      editScheduleTime.value = result.suggested_time
    }
    if (result.suggested_schedule) {
      // 尝试从 cron 表达式推断 schedule type
      if (result.suggested_schedule.includes('1-5')) {
        editSchedule.value = 'weekday'
      } else if (result.suggested_schedule.includes('* 1') || result.suggested_schedule.endsWith(' 1')) {
        editSchedule.value = 'weekly'
      } else {
        editSchedule.value = 'daily'
      }
    }

    // 解析结果初始化到 editTools
    if (result.tools) {
      editTools.value = result.tools.map((t: any) => ({
        name: normalizeToolName(t.name),
        params: { ...t.params },
      }))
    }
    showToolsEditor.value = false

    currentStep.value = 2
  } catch {
    message.error('AI 解析失败，请重试')
  } finally {
    parsing.value = false
  }
}

// Step 2 -> 创建 / 更新
const handleSubmit = async () => {
  if (!editName.value.trim()) {
    message.warning('请填写订阅名称')
    return
  }

  creating.value = true
  try {
    // 空值校验
    if (editTools.value.filter(t => t.name).length === 0) {
      message.warning('请至少配置一个数据源')
      return
    }

    const config: Record<string, any> = {
      keywords: editKeywords.value,
      tools: editTools.value.filter(t => t.name),
      min_relevance_score: editMinRelevanceScore.value,
      relevance_mode: editRelevanceMode.value,
    }

    if (isEditMode.value && props.editSubscription) {
      // 编辑模式：调用 updateSubscription
      await updateSubscription(props.editSubscription.id, {
        name: editName.value,
        description: description.value || undefined,
        category: editCategory.value,
        config,
        exec_mode: editExecMode.value,
        schedule: scheduleToCron(editSchedule.value, editScheduleTime.value),
        schedule_time: editScheduleTime.value,
        notification_channels: editNotificationChannels.value,
      })
      message.success('订阅已更新')
    } else {
      // 创建模式：调用 createSubscriptionFromConfig
      await createSubscriptionFromConfig({
        name: editName.value,
        description: description.value,
        category: editCategory.value,
        config,
        exec_mode: editExecMode.value,
        schedule: scheduleToCron(editSchedule.value, editScheduleTime.value),
        schedule_time: editScheduleTime.value,
        notification_channels: editNotificationChannels.value,
      })
      message.success('订阅创建成功')
    }

    emit('success')
    handleClose()
  } catch {
    message.error(isEditMode.value ? '更新失败，请重试' : '创建失败，请重试')
  } finally {
    creating.value = false
  }
}

// 关闭并重置
const handleClose = () => {
  emit('update:show', false)
  // 延迟重置避免闪烁
  setTimeout(() => {
    currentStep.value = 1
    const savedMode = localStorage.getItem(LAST_TAB_KEY) as 'template' | 'custom' | 'source' | null
    createMode.value = savedMode || 'template'
    selectedTemplate.value = null
    description.value = ''
    parseResult.value = null
    editName.value = ''
    editCategory.value = ''
    editKeywords.value = []
    editExecMode.value = 'quick'
    editSchedule.value = 'daily'
    editScheduleTime.value = '08:00'
    editNotificationChannels.value = ['web', ...activeWebhooks.value.map(w => String(w.id))]
    editMinRelevanceScore.value = 0.5
    editRelevanceMode.value = 'keyword'
    editTools.value = []
    showToolsEditor.value = false
    // 内置源状态重置（builtinSourcesMap 不清除，可复用）
    sourceSelections.value = []
    drawerSelections.value = []
    showSourceDrawer.value = false
  }, 300)
}

// 添加关键词
const newKeyword = ref('')
const handleAddKeyword = () => {
  const kw = newKeyword.value.trim()
  if (kw && !editKeywords.value.includes(kw)) {
    editKeywords.value.push(kw)
  }
  newKeyword.value = ''
}

const handleRemoveKeyword = (index: number) => {
  editKeywords.value.splice(index, 1)
}
</script>

<template>
  <n-modal
    :show="props.show"
    @update:show="handleClose"
    :mask-closable="true"
    transform-origin="center"
  >
    <n-card
      :title="isEditMode ? '编辑订阅' : '创建智能订阅'"
      :bordered="false"
      :style="modalStyle"
      closable
      @close="handleClose"
    >
      <!-- 步骤指示器（仅创建模式） -->
      <n-steps v-if="!isEditMode" :current="currentStep" size="small" class="create-steps">
        <n-step title="选择方式" />
        <n-step title="确认配置" />
      </n-steps>

      <!-- Step 1: 模板选择 / 自然语言输入（仅创建模式） -->
      <div v-if="!isEditMode && currentStep === 1" class="step-content">
        <!-- 模式切换 -->
        <div class="mode-tabs">
          <span
            :class="['mode-tab', { active: createMode === 'source' }]"
            @click="createMode = 'source'"
          >
            选择数据源
          </span>
          <span
            :class="['mode-tab', { active: createMode === 'template' }]"
            @click="createMode = 'template'"
          >
            从模板创建
          </span>
          <span
            :class="['mode-tab', { active: createMode === 'custom' }]"
            @click="createMode = 'custom'"
          >
            自定义创建
          </span>
        </div>

        <!-- 内置数据源模式 -->
        <template v-if="createMode === 'source'">
          <p class="step-hint">从内置数据源中选择，系统将自动生成订阅配置。</p>
          <source-selector
            v-model="sourceSelections"
          />
          <div class="step-actions">
            <n-button @click="handleClose">取消</n-button>
            <n-button
              type="primary"
              :loading="resolvingSources"
              :disabled="sourceSelections.length === 0"
              @click="handleResolveSources"
            >
              <template #icon>
                <n-icon :component="ArrowForwardOutline" />
              </template>
              下一步
            </n-button>
          </div>
        </template>

        <!-- 模板模式 -->
        <template v-if="createMode === 'template'">
          <!-- 模板列表（未选择模板时） -->
          <template v-if="!selectedTemplate">
            <div class="template-category-bar">
              <span
                v-for="cat in [{ key: 'all', label: '全部' }, { key: 'academic', label: '学术' }, { key: 'tech', label: '技术' }, { key: 'trending', label: '热门' }]"
                :key="cat.key"
                :class="['tmpl-cat-tag', { active: templateCategory === cat.key }]"
                @click="templateCategory = cat.key"
              >
                {{ cat.label }}
              </span>
            </div>

            <div v-if="loadingTemplates" class="template-loading">
              <n-spin size="small" />
            </div>
            <div v-else class="template-grid">
              <div
                v-for="tmpl in filteredTemplates"
                :key="tmpl.slug"
                class="template-card"
                style="cursor: pointer"
                @click="handleSelectTemplate(tmpl)"
              >
                <div class="template-card-header">
                  <span class="template-icon">{{ tmpl.icon || '📋' }}</span>
                  <span class="template-name">{{ tmpl.name }}</span>
                </div>
                <p class="template-desc">{{ tmpl.description }}</p>
                <div class="template-card-footer">
                  <span class="template-installs">{{ tmpl.install_count || 0 }} 人使用</span>
                  <n-button
                    size="small"
                    type="primary"
                    @click.stop="handleSelectTemplate(tmpl)"
                  >
                    安装
                  </n-button>
                </div>
              </div>
            </div>
            <div class="step-actions">
              <n-button @click="handleClose">取消</n-button>
            </div>
          </template>

          <!-- 模板安装确认页（选择模板后） -->
          <template v-else>
            <div class="tmpl-confirm-header">
              <span class="tmpl-confirm-icon">{{ selectedTemplate.icon || '📋' }}</span>
              <div class="tmpl-confirm-info">
                <h3>{{ selectedTemplate.name }}</h3>
                <p>{{ selectedTemplate.description }}</p>
              </div>
            </div>

            <!-- 配置摘要 -->
            <div class="tmpl-config-summary">
              <div class="config-summary-row">
                <span class="config-summary-label">数据源</span>
                <span class="config-summary-value">
                  <n-tag
                    v-for="(tool, idx) in (selectedTemplate.config_template?.tools || [])"
                    :key="idx"
                    size="small"
                    :bordered="false"
                  >
                    {{ toolSourceLabel(tool) }}
                  </n-tag>
                </span>
              </div>
            </div>

            <!-- 可调整配置 -->
            <div class="config-field">
              <label class="field-label">执行模式</label>
              <n-select
                v-model:value="tmplExecMode"
                :options="execModeOptions"
              />
            </div>

            <div class="config-field" style="display: flex; gap: 12px;">
              <div style="flex: 1;">
                <label class="field-label">推送频率</label>
                <n-select
                  v-model:value="tmplSchedule"
                  :options="scheduleOptions"
                />
              </div>
              <div style="flex: 1;">
                <label class="field-label">推送时间</label>
                <n-select
                  v-model:value="tmplScheduleTime"
                  :options="timeOptions"
                />
              </div>
            </div>

            <div class="config-field">
              <label class="field-label">通知渠道</label>
              <div class="channel-checkboxes">
                <n-checkbox :checked="true" :disabled="true">
                  Web 站内通知（默认）
                </n-checkbox>
                <template v-if="activeWebhooks.length > 0">
                  <n-checkbox
                    v-for="wh in activeWebhooks"
                    :key="wh.id"
                    :checked="tmplNotificationChannels.includes(String(wh.id))"
                    @update:checked="(val: boolean) => {
                      const idStr = String(wh.id)
                      if (val) tmplNotificationChannels.push(idStr)
                      else tmplNotificationChannels = tmplNotificationChannels.filter(c => c !== idStr)
                    }"
                  >
                    {{ { feishu_webhook: '飞书', dingtalk_webhook: '钉钉', wework_webhook: '企微', telegram_bot: 'TG', email: 'Email' }[wh.channel_type] || wh.channel_type }} · {{ wh.name }}
                  </n-checkbox>
                </template>
              </div>
            </div>

            <div class="step-actions">
              <n-button @click="selectedTemplate = null">
                <template #icon>
                  <n-icon :component="ArrowBackOutline" />
                </template>
                返回
              </n-button>
              <n-button
                type="primary"
                :loading="installingSlug === selectedTemplate.slug"
                @click="handleConfirmInstall"
              >
                <template #icon>
                  <n-icon :component="CheckmarkOutline" />
                </template>
                确认安装并开始追踪
              </n-button>
            </div>
          </template>
        </template>

        <!-- 自定义模式（原自然语言输入） -->
        <template v-if="createMode === 'custom'">
          <p class="step-hint">
            用自然语言描述你想追踪的内容，AI 会自动解析并生成订阅配置。
          </p>
        <n-input
          v-model:value="description"
          type="textarea"
          placeholder="例如：帮我追踪人工智能在教育领域的最新研究，特别是大语言模型辅助教学方面的论文"
          :rows="4"
          :maxlength="2000"
          show-count
        />
        <div class="step-examples">
          <span class="examples-label">示例：</span>
          <n-tag
            size="small"
            :bordered="false"
            class="example-tag"
            @click="description = '追踪深度学习在自然语言处理领域的最新论文和技术突破'"
          >
            NLP 最新论文
          </n-tag>
          <n-tag
            size="small"
            :bordered="false"
            class="example-tag"
            @click="description = '关注 AI Agent 和多智能体系统的前沿研究动态'"
          >
            AI Agent 动态
          </n-tag>
          <n-tag
            size="small"
            :bordered="false"
            class="example-tag"
            @click="description = '追踪终身教育和在线学习平台的政策变化和行业趋势'"
          >
            终身教育趋势
          </n-tag>
        </div>
        <div class="step-actions">
          <n-button @click="handleClose">取消</n-button>
          <n-button
            type="primary"
            @click="handleParse"
            :loading="parsing"
            :disabled="!description.trim()"
          >
            <template #icon>
              <n-icon :component="SparklesOutline" />
            </template>
            AI 智能解析
          </n-button>
        </div>
        </template>
      </div>

      <!-- Step 2: 解析结果预览 + 配置 -->
      <div v-if="currentStep === 2" class="step-content">
        <n-alert v-if="!isEditMode" type="success" :bordered="false" class="parse-alert">
          AI 已解析你的需求，请确认或调整以下配置
        </n-alert>

        <!-- 订阅名称 -->
        <div class="config-field">
          <label class="field-label">订阅名称</label>
          <n-input v-model:value="editName" placeholder="订阅名称" />
        </div>

        <!-- 调研需求（编辑模式显示，创建模式 Step 1 已输入） -->
        <div v-if="isEditMode" class="config-field">
          <label class="field-label">调研需求</label>
          <n-input
            v-model:value="description"
            type="textarea"
            placeholder="用自然语言描述你想追踪的内容"
            :rows="3"
            :maxlength="2000"
            show-count
          />
        </div>

        <!-- 分类 -->
        <div class="config-field">
          <label class="field-label">分类</label>
          <n-select
            v-model:value="editCategory"
            :options="categoryOptions"
          />
        </div>

        <!-- 关键词 -->
        <div class="config-field">
          <label class="field-label">追踪关键词</label>
          <div class="keywords-editor">
            <div class="keywords-list">
              <n-tag
                v-for="(kw, idx) in editKeywords"
                :key="idx"
                closable
                size="small"
                type="info"
                @close="handleRemoveKeyword(idx)"
              >
                {{ kw }}
              </n-tag>
            </div>
            <div class="keyword-input-row">
              <n-input
                v-model:value="newKeyword"
                placeholder="输入关键词后回车"
                size="small"
                @keyup.enter="handleAddKeyword"
              />
              <n-button size="small" @click="handleAddKeyword" :disabled="!newKeyword.trim()">
                添加
              </n-button>
            </div>
          </div>
        </div>

        <!-- 数据源（编辑模式可编辑 / 创建模式折叠可展开） -->
        <div v-if="editTools.length > 0" class="config-field">
          <label class="field-label">数据源</label>

          <!-- 创建模式：折叠预览 + 展开按钮 -->
          <div v-if="!isEditMode && !showToolsEditor" class="tools-preview">
            <div class="tools-preview-tags">
              <n-tag v-for="(tool, idx) in editTools" :key="idx"
                size="small" :bordered="false" type="info">
                {{ toolSourceLabel(tool) }}
              </n-tag>
            </div>
            <n-button text type="primary" size="small" @click="showToolsEditor = true">
              调整数据源
            </n-button>
          </div>

          <!-- 编辑模式 或 创建模式展开后 -->
          <div v-if="isEditMode || showToolsEditor" class="tools-editor">
            <div v-for="(tool, idx) in editTools" :key="idx" class="source-item">
              <!-- 内置源（有 _source_id）：只读友好名称 -->
              <template v-if="tool._source_id">
                <div class="source-item-header">
                  <n-tag size="small" type="success" :bordered="false">
                    {{ { academic: '🎓', tech: '💻', trending: '🔥', industry: '📰' }[builtinSourcesMap[tool._source_id]?.icon] || '📋' }}
                    {{ builtinSourcesMap[tool._source_id]?.name || tool._source_id }}
                  </n-tag>
                  <n-button text type="error" size="small" @click="removeEditTool(idx)">
                    删除
                  </n-button>
                </div>
                <div class="source-item-params">
                  <span class="builtin-source-hint">内置数据源（自动配置）</span>
                </div>
              </template>

              <!-- 已知工具类型：结构化编辑 -->
              <template v-else-if="isKnownToolType(tool.name)">
                <div class="source-item-header">
                  <n-select v-model:value="tool.name"
                    :options="userToolTypeOptions" size="small" style="width: 180px"
                    @update:value="(v: string) => onEditToolTypeChange(idx, v)" />
                  <n-button text type="error" size="small"
                    @click="removeEditTool(idx)" :disabled="editTools.length <= 1">
                    删除
                  </n-button>
                </div>
                <div class="source-item-params">
                  <template v-if="tool.name === 'rss_fetch' || tool.name === 'web_fetch'">
                    <n-input v-model:value="tool.params.url"
                      :placeholder="tool.name === 'rss_fetch' ? '期刊/网站的 RSS 订阅地址' : '目标网页 URL'"
                      size="small" />
                    <div v-if="tool.name === 'rss_fetch'" class="max-items-field">
                      <span class="max-items-label">最多</span>
                      <n-input-number v-model:value="tool.params.max_items"
                        :min="1" :max="50" size="small" style="width: 70px" />
                      <span class="max-items-label">条</span>
                    </div>
                  </template>
                  <template v-else>
                    <n-input v-model:value="tool.params.query"
                      placeholder="搜索关键词" size="small" />
                    <n-select v-if="tool.name === 'academic_search'"
                      v-model:value="tool.params.source"
                      :options="userSourceOptions" size="small" style="width: 180px" />
                  </template>
                </div>
              </template>

              <!-- 未知工具类型：只读 fallback -->
              <template v-else>
                <div class="source-item-header">
                  <n-tag size="small" type="warning">{{ tool.name }}</n-tag>
                  <n-button text type="error" size="small" @click="removeEditTool(idx)">
                    删除
                  </n-button>
                </div>
                <div class="source-item-params">
                  <span class="unknown-tool-hint">
                    高级数据源，参数：{{ JSON.stringify(tool.params) }}
                  </span>
                </div>
              </template>
            </div>

            <div class="tools-add-row">
              <n-button dashed size="small" @click="addEditTool">
                + 添加数据源
              </n-button>
              <n-button dashed size="small" type="primary" @click="showSourceDrawer = true">
                从内置数据源选择
              </n-button>
            </div>
          </div>
        </div>

        <!-- 高级设置（折叠） -->
        <n-collapse class="advanced-settings">
          <n-collapse-item title="推送设置" name="push">
            <div class="config-field">
              <label class="field-label">更新方式</label>
              <n-select
                v-model:value="editExecMode"
                :options="execModeOptions"
              />
            </div>
            <div class="config-field">
              <label class="field-label">推送频率</label>
              <n-select
                v-model:value="editSchedule"
                :options="scheduleOptions"
              />
            </div>
            <div class="config-field">
              <label class="field-label">推送时间</label>
              <n-select
                v-model:value="editScheduleTime"
                :options="timeOptions"
              />
            </div>
            <div class="config-field">
              <label class="field-label">通知渠道</label>
              <div class="channel-checkboxes">
                <n-checkbox :checked="true" :disabled="true">
                  Web 站内通知（默认）
                </n-checkbox>
                <template v-if="activeWebhooks.length > 0">
                  <n-checkbox
                    v-for="wh in activeWebhooks"
                    :key="wh.id"
                    :checked="editNotificationChannels.includes(String(wh.id))"
                    @update:checked="(val: boolean) => {
                      const idStr = String(wh.id)
                      if (val) editNotificationChannels.push(idStr)
                      else editNotificationChannels = editNotificationChannels.filter(c => c !== idStr)
                    }"
                  >
                    {{ { feishu_webhook: '飞书', dingtalk_webhook: '钉钉', wework_webhook: '企微', telegram_bot: 'TG', email: 'Email' }[wh.channel_type] || wh.channel_type }} · {{ wh.name }}
                  </n-checkbox>
                </template>
                <div v-else class="channel-empty-hint">
                  尚未添加？去「<router-link to="/settings/channels">通知渠道管理</router-link>」添加
                </div>
              </div>
            </div>
          <!-- 内容筛选精度 -->
            <div class="config-field" v-if="editKeywords.length > 0">
              <label class="field-label">内容筛选精度</label>
              <div class="relevance-slider">
                <span class="slider-label-left">宁多勿少</span>
                <n-slider
                  v-model:value="editMinRelevanceScore"
                  :min="0.1"
                  :max="0.9"
                  :step="0.1"
                  :format-tooltip="(v: number) => v.toFixed(1)"
                  style="flex: 1"
                />
                <span class="slider-label-right">少而精</span>
              </div>
              <p class="field-tip">阈值越高推送越精准但可能遗漏部分相关内容</p>
            </div>

            <!-- 筛选模式 -->
            <div class="config-field" v-if="editKeywords.length > 0">
              <label class="field-label">筛选模式</label>
              <n-radio-group v-model:value="editRelevanceMode">
                <n-space>
                  <n-radio value="keyword">关键词匹配（免费）</n-radio>
                  <n-radio value="ai">AI语义评分（消耗Token）</n-radio>
                </n-space>
              </n-radio-group>
            </div>
          </n-collapse-item>
        </n-collapse>

        <div class="step-actions">
          <n-button v-if="isEditMode" @click="handleClose">
            取消
          </n-button>
          <n-button v-else @click="currentStep = 1">
            <template #icon>
              <n-icon :component="ArrowBackOutline" />
            </template>
            返回
          </n-button>
          <n-button
            type="primary"
            @click="handleSubmit"
            :loading="creating"
          >
            <template #icon>
              <n-icon :component="CheckmarkOutline" />
            </template>
            {{ isEditMode ? '保存修改' : '确认创建' }}
          </n-button>
        </div>
      </div>
    </n-card>
  </n-modal>

  <!-- 右侧抽屉：追加内置数据源（Step 2） -->
  <n-drawer
    v-model:show="showSourceDrawer"
    :width="isMobile ? '100vw' : 420"
    placement="right"
    :trap-focus="false"
  >
    <n-drawer-content title="从内置数据源选择" closable>
      <source-selector
        v-model="drawerSelections"
        mode="drawer"
        :exclude-source-ids="editTools.filter(t => !!t._source_id).map(t => t._source_id!)"
        @confirm="handleDrawerConfirm"
      />
      <template #footer>
        <div style="display: flex; justify-content: flex-end; gap: 12px;">
          <n-button @click="showSourceDrawer = false">取消</n-button>
          <n-button
            type="primary"
            :loading="resolvingDrawer"
            :disabled="drawerSelections.length === 0"
            @click="handleDrawerConfirm"
          >
            确认追加 ({{ drawerSelections.length }})
          </n-button>
        </div>
      </template>
    </n-drawer-content>
  </n-drawer>
</template>

<style scoped>
.create-steps {
  margin-bottom: 24px;
}

.step-content {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.step-hint {
  margin: 0;
  font-size: 14px;
  color: #666;
  line-height: 1.5;
}

.step-examples {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.examples-label {
  font-size: 13px;
  color: #999;
}

.example-tag {
  cursor: pointer;
  transition: opacity 0.2s;
}

.example-tag:hover {
  opacity: 0.7;
}

.step-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  padding-top: 8px;
}

.parse-alert {
  margin-bottom: 8px;
}

.config-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field-label {
  font-size: 13px;
  font-weight: 500;
  color: #333;
}

.keywords-editor {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.keywords-list {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  min-height: 28px;
}

.keyword-input-row {
  display: flex;
  gap: 8px;
}

/* 数据源折叠预览 */
.tools-preview {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.tools-preview-tags {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

/* 数据源编辑器 */
.tools-editor {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}

.source-item {
  border: 1px solid #e8e8e8;
  border-radius: 6px;
  padding: 8px 10px;
}

.source-item-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}

.source-item-params {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
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

.unknown-tool-hint {
  font-size: 12px;
  color: #999;
  word-break: break-all;
}

.advanced-settings {
  margin-top: 4px;
}

.channel-checkboxes {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.channel-empty-hint {
  font-size: 12px;
  color: #999;
  margin-top: 4px;
}

.channel-empty-hint a {
  color: #2080f0;
  text-decoration: none;
}

.channel-empty-hint a:hover {
  text-decoration: underline;
}

/* 相关度滑块 */
.relevance-slider {
  display: flex;
  align-items: center;
  gap: 10px;
}

.slider-label-left,
.slider-label-right {
  font-size: 12px;
  color: #999;
  white-space: nowrap;
}

.field-tip {
  margin: 0;
  font-size: 12px;
  color: #999;
}

/* ===== 模板选择 ===== */
.mode-tabs {
  display: flex;
  gap: 0;
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  overflow: hidden;
}

.mode-tab {
  flex: 1;
  text-align: center;
  padding: 8px 0;
  font-size: 14px;
  cursor: pointer;
  transition: all 0.2s;
  color: #666;
  background: #fafafa;
}

.mode-tab.active {
  color: #fff;
  background: #2080f0;
  font-weight: 500;
}

.template-category-bar {
  display: flex;
  gap: 8px;
}

.tmpl-cat-tag {
  padding: 4px 12px;
  border-radius: 16px;
  font-size: 13px;
  color: #666;
  background: #f5f5f5;
  cursor: pointer;
  transition: all 0.2s;
}

.tmpl-cat-tag.active {
  color: #fff;
  background: #2080f0;
}

.template-loading {
  display: flex;
  justify-content: center;
  padding: 32px 0;
}

.template-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  max-height: 400px;
  overflow-y: auto;
}

.template-card {
  border: 1px solid #e8e8e8;
  border-radius: 8px;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  transition: border-color 0.2s, box-shadow 0.2s;
}

.template-card:hover {
  border-color: #2080f0;
  box-shadow: 0 2px 8px rgba(32, 128, 240, 0.1);
}

.template-card-header {
  display: flex;
  align-items: center;
  gap: 8px;
}

.template-icon {
  font-size: 20px;
}

.template-name {
  font-size: 14px;
  font-weight: 500;
  color: #333;
}

.template-desc {
  margin: 0;
  font-size: 12px;
  color: #999;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  flex: 1;
}

.template-card-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.template-installs {
  font-size: 12px;
  color: #bbb;
}

/* ===== 模板安装确认页 ===== */
.tmpl-confirm-header {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  padding: 12px;
  background: #f8f9fa;
  border-radius: 8px;
}

.tmpl-confirm-icon {
  font-size: 32px;
  flex-shrink: 0;
}

.tmpl-confirm-info h3 {
  margin: 0 0 4px;
  font-size: 16px;
  font-weight: 600;
  color: #333;
}

.tmpl-confirm-info p {
  margin: 0;
  font-size: 13px;
  color: #666;
  line-height: 1.5;
}

.tmpl-config-summary {
  background: #fafafa;
  border-radius: 6px;
  padding: 10px 12px;
}

.config-summary-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.config-summary-label {
  font-size: 13px;
  color: #999;
  flex-shrink: 0;
}

.config-summary-value {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}

/* 内置数据源工具（只读） */
.builtin-source-hint {
  font-size: 12px;
  color: #18a058;
}

/* 数据源添加按钮行 */
.tools-add-row {
  display: flex;
  gap: 8px;
  margin-top: 6px;
  flex-wrap: wrap;
}
</style>
