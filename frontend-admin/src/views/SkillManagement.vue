<script setup lang="ts">
/**
 * Skills 管理页面 (v2 - 完整 CRUD)
 *
 * 功能：
 * - 卡片列表 + 分类筛选 + 搜索
 * - 启用/禁用 Skill
 * - 编辑抽屉 (3 Tabs: 基本信息 / 系统提示词 / 工具与子代理)
 * - 创建 Skill
 * - 删除非系统 Skill
 * - 从文件种子同步（开发者通过 API 操作）
 */

import { ref, reactive, computed, onMounted, nextTick, h } from 'vue'
import {
  NCard, NButton, NSpace, NGrid, NGridItem, NStatistic,
  NTag, NSwitch, NSelect, NInput, NIcon, NEmpty, NSpin,
  NTooltip, NDrawer, NDrawerContent, NTabs, NTabPane,
  NForm, NFormItem, NInputNumber, NTransfer, NDynamicTags,
  NPopconfirm, NBadge, NCollapse, NCollapseItem, NCheckboxGroup, NCheckbox,
  NTimeline, NTimelineItem,
  NModal, NAlert,
  useMessage
} from 'naive-ui'
import type { TransferOption } from 'naive-ui'
import {
  SearchOutline, SettingsOutline,
  CheckmarkCircleOutline, CloseCircleOutline,
  CodeSlashOutline, DocumentTextOutline, GlobeOutline,
  SchoolOutline, AnalyticsOutline, AppsOutline,
  AddOutline, TrashOutline, CreateOutline, EyeOutline,
  SparklesOutline
} from '@vicons/ionicons5'
import {
  getSkills, getSkillStats, getSkillCategories, getSkill,
  updateSkill,
  createSkill, deleteSkill, getBuiltinSkills, getSkillAuditLogs, rollbackSkill,
  MODEL_TIER_MAP, getCategoryColor, getCategoryName, buildModelTierMap,
  RESOLVED_SOURCE_LABEL, RESOLVED_SOURCE_TYPE,
  type Skill, type SkillStats, type SkillUpdateRequest, type SkillCreateRequest, type BuiltinSkill, type SkillAuditLog,
  type PlanStageConfig,
  getModelConfig, type ModelTierConfig,
  getSkillResources, getSkillResourceContent,
  type SkillResourceItem, type SkillResourceContent
} from '../api/super-agent'
import SkillAiCreatorPanel from '../components/SkillAiCreatorPanel.vue'
import PlanStagesEditor from '../components/skill/plan-stages/PlanStagesEditor.vue'
import { getTools, type ToolInfo } from '../api/tools'
import { getKnowledgeBases, type KnowledgeBase } from '../api/knowledge-bases'
import { getDocuments, type Document as DocItem } from '../api/documents'
import { diffLines } from 'diff'

const message = useMessage()

// ==================== 响应式数据 ====================

const planStagesEditorRef = ref()
const loading = ref(false)
const skills = ref<Skill[]>([])
const stats = ref<SkillStats | null>(null)
const categories = ref<string[]>([])

// 筛选
const filters = reactive({
  category: '' as string,
  search: '',
  executionMode: '' as string,
  modelTier: '' as string,
  enabled: '' as string,  // '' | 'true' | 'false'
  outputFormat: '' as string,  // '' | 'artifact' | 'html-animation' | ...
})

// 所有可用工具（编辑抽屉用）
const allTools = ref<ToolInfo[]>([])
const disabledTools = ref<string[]>([])

// 编辑抽屉
const drawerVisible = ref(false)
const drawerMode = ref<'edit' | 'create'>('edit')
const drawerLoading = ref(false)
const saving = ref(false)
const activeTab = ref('basic')

// AI 辅助创建
const aiDrawerVisible = ref(false)
const aiCreatorRef = ref<InstanceType<typeof SkillAiCreatorPanel> | null>(null)
const createSource = ref<'admin' | 'ai-generated'>('admin')  // 当前创建来源
const showAiHint = ref(false)  // 是否显示 AI 填充提示

// 待审核筛选
const filterPendingReview = ref(false)

// 审计日志
const auditLogs = ref<SkillAuditLog[]>([])
const actionLabel = (action: string) => {
  const map: Record<string, string> = { update: '修改了配置', create: '创建了 Skill', delete: '删除了 Skill', reload: '重新加载了 Skill', 'reload-all': '批量重新加载了配置', rollback: '回滚了配置' }
  return map[action] || action
}

// M4 / Stage 9: 包内资源(只读)
const skillResources = ref<SkillResourceItem[]>([])
const resourcesLoading = ref(false)
const resourceModalVisible = ref(false)
const resourceContent = ref<SkillResourceContent | null>(null)
const resourceContentLoading = ref(false)

const groupedResources = computed(() => {
  const out: Record<string, SkillResourceItem[]> = {}
  for (const r of skillResources.value) {
    const dir = r.path.split('/')[0] || 'other'
    ;(out[dir] ||= []).push(r)
  }
  return out
})

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

async function loadSkillResources(name: string) {
  resourcesLoading.value = true
  try {
    const res = await getSkillResources(name)
    skillResources.value = res.resources || []
  } catch (e: any) {
    skillResources.value = []
    // 静默：资源面板不应阻塞抽屉
    console.warn('[skill-resources] 加载失败', e?.message || e)
  } finally {
    resourcesLoading.value = false
  }
}

async function viewResource(item: SkillResourceItem) {
  const name = originalSkill.value?.name
  if (!name) return
  resourceModalVisible.value = true
  resourceContentLoading.value = true
  resourceContent.value = null
  try {
    resourceContent.value = await getSkillResourceContent(name, item.path)
  } catch (e: any) {
    message.error(e.message || '读取资源失败')
    resourceModalVisible.value = false
  } finally {
    resourceContentLoading.value = false
  }
}

// diff 查看 & 回滚
const diffModalVisible = ref(false)
const diffLog = ref<SkillAuditLog | null>(null)
const diffItems = computed(() => {
  const log = diffLog.value
  if (!log || !log.changed_fields) return []
  return log.changed_fields.map(field => {
    const oldVal = log.old_values?.[field]
    const newVal = log.new_values?.[field]
    const fmt = (v: any) => v == null ? '' : typeof v === 'object' ? JSON.stringify(v, null, 2) : String(v)
    const oldStr = fmt(oldVal)
    const newStr = fmt(newVal)
    const parts = diffLines(oldStr, newStr)
    return { field, oldStr, newStr, parts }
  })
})

function showDiff(log: SkillAuditLog) {
  diffLog.value = log
  diffModalVisible.value = true
}

const rollingBack = ref(false)
async function handleRollback(log: SkillAuditLog) {
  const name = originalSkill.value?.name
  if (!name || !log.old_values) return
  rollingBack.value = true
  try {
    const res = await rollbackSkill(name, log.id)
    message.success('回滚成功')
    if (res.warnings?.length) {
      res.warnings.forEach(w => message.warning(w, { duration: 8000 }))
    }
    diffModalVisible.value = false
    await loadAuditLogs()
    await loadData()
  } catch (e: any) {
    message.error(e.message || '回滚失败')
  } finally {
    rollingBack.value = false
  }
}

// 编辑表单
const editForm = reactive({
  name: '',
  display_name: '',
  description: '',
  category: '',
  execution_mode: 'normal',
  model_tier: 'mid',
  sort_order: 0,
  system_prompt: '',
  allowed_tools: [] as string[],
  trigger_keywords: [] as string[],
  sub_skills: [] as string[],
  sub_agent_config: {
    max_depth: 2,
    timeout: 300,
    max_concurrent: 3,
    allow_background: false
  },
  tool_tier: null as string | null,
  extra_tools: [] as string[],
  denied_tools: [] as string[],
  // v11.0: Capability 抽象层（跨平台 Skill 契约，skill.md 默认契约）
  required_capabilities: [] as string[],
  optional_capabilities: [] as string[],
  preferred_sub_skills: [] as string[],
  // v12.0(Stage 7h): 平台 Override（admin 在 DB 设置,覆盖 skill.md 默认）
  intent_priority: null as number | null,
  regex_patterns: [] as string[],
  exclude_keywords: [] as string[],
  exclude_pairs: [] as [string, string][],
  // Phase 2: 输出验证配置
  verification_config: null as {
    strategy: string
    dimensions: Array<{ name: string; description: string; weight: number }>
    min_score: number
  } | null,
  // P9: 沙箱隔离配置
  sandbox_enabled: false,
  sandbox_timeout: 600,
  sandbox_memory_mb: 512,
  // Aggregation@N 聚合增强
  aggregation_n: 1,
  // v9.0: 上下文与状态管理
  high_context_threshold: false,
  state_tracking: false,
  // Phase 1.4: 上下文压缩策略（DB 驱动）
  compression_policy: null as {
    compress_threshold: number
    preserve_tool_results: boolean
    preserve_categories: string[]
  } | null,
  compression_preset: 'default' as 'research' | 'generate' | 'default' | 'custom',
  // v10.0: 知识库绑定 + 模型选择
  kb_bindings: [] as Array<{ kb_id: string; scope: 'all' | 'categories' | 'doc_ids'; categories: string[]; doc_ids: string[] }>,
  model_id: '' as string,
  model_select_mode: 'tier' as 'tier' | 'specific',  // 按等级 vs 指定模型
  include_personal_kb: true,  // 同时检索用户个人知识库
  plan_stages: [] as PlanStageConfig[],
  // Artifact 系统：Skill 级输出格式
  output_format: null as string | null,  // null=普通输出, 'artifact'=输出到Artifact面板
})

// 压缩策略预设模板
const COMPRESSION_PRESETS: Record<string, { compress_threshold: number; preserve_tool_results: boolean; preserve_categories: string[] }> = {
  research: { compress_threshold: 45, preserve_tool_results: true, preserve_categories: ['reference', 'citation'] },
  generate: { compress_threshold: 30, preserve_tool_results: false, preserve_categories: ['outline', 'structure'] },
  default: { compress_threshold: 25, preserve_tool_results: false, preserve_categories: [] },
}

function applyCompressionPreset(preset: string) {
  if (preset !== 'custom' && COMPRESSION_PRESETS[preset]) {
    editForm.compression_policy = { ...COMPRESSION_PRESETS[preset] }
  }
  // custom 模式保持当前值不变
}

// 知识库 & 模型数据
const knowledgeBases = ref<KnowledgeBase[]>([])
const availableModels = ref<Array<{ id: string; name: string; provider: string }>>([])

async function loadKbAndModels() {
  try {
    const [kbRes, modelRes] = await Promise.all([
      getKnowledgeBases(),
      getModelConfig(),
    ])
    knowledgeBases.value = kbRes.items || []
    availableModels.value = modelRes.available_models || []
  } catch (e) {
    console.warn('加载知识库/模型列表失败:', e)
  }
}

const kbOptions = computed(() =>
  knowledgeBases.value.map(kb => ({
    label: kb.name,
    value: kb.kb_id,
    kbId: kb.kb_id,
    docCount: (kb as any).document_count || 0,
  }))
)

// 每个 KB 的文档列表和分类缓存
const kbDocsCache = ref<Record<string, DocItem[]>>({})

async function loadKbDocs(kbId: string) {
  if (kbDocsCache.value[kbId]) return
  try {
    const res = await getDocuments({ kb_id: kbId, page_size: 200, status: 'active' })
    kbDocsCache.value[kbId] = res.documents || []
  } catch {
    kbDocsCache.value[kbId] = []
  }
}

function getKbCategories(kbId: string): string[] {
  const docs = kbDocsCache.value[kbId] || []
  const cats = new Set(docs.map(d => d.category).filter(Boolean) as string[])
  return [...cats].sort()
}

function getKbDocOptions(kbId: string) {
  const docs = kbDocsCache.value[kbId] || []
  return docs.filter(d => d.datacollection_id).map(d => ({
    label: d.title || d.filename || d.doc_id,
    value: d.datacollection_id!
  }))
}

const modelOptions = computed(() => [
  { label: '(不指定，使用模型等级)', value: '' },
  ...availableModels.value.map(m => ({
    label: `${m.name} (${m.provider})`,
    value: m.id
  }))
])

// ==================== 测试沙箱 ====================
const regexTestInput = ref('')
const regexTestResults = computed(() => {
  const input = regexTestInput.value.trim()
  if (!input) return []
  return editForm.regex_patterns.map((pattern, idx) => {
    if (!pattern) return { idx, pattern, match: false, error: '' }
    try {
      const re = new RegExp(pattern)
      const m = re.exec(input)
      return { idx, pattern, match: !!m, error: '', matched: m ? m[0] : '' }
    } catch (e: any) {
      return { idx, pattern, match: false, error: e.message }
    }
  })
})
const exclusionTestInput = ref('')
const exclusionTestResult = computed(() => {
  const input = exclusionTestInput.value.trim()
  if (!input) return { keyword: null as string | null, pair: null as [string, string] | null }
  const kw = editForm.exclude_keywords.find(k => input.includes(k)) || null
  const pair = editForm.exclude_pairs.find(p => p[0].trim() && p[1].trim() && input.includes(p[0].trim()) && input.includes(p[1].trim())) || null
  return { keyword: kw, pair }
})

// Phase 2: 验证配置权重计算
const verificationWeightTotal = computed(() => {
  if (!editForm.verification_config?.dimensions) return 0
  return editForm.verification_config.dimensions.reduce((sum, d) => sum + (d.weight || 0), 0)
})
const verificationWeightStatus = computed(() => {
  const total = verificationWeightTotal.value
  if (Math.abs(total - 1.0) <= 0.01) return 'success'
  if (total < 1.0) return 'warning'
  return 'error'
})

// 当前编辑的 Skill 原始数据（用于对比变更）
const originalSkill = ref<Skill | null>(null)
const builtinSkills = ref<BuiltinSkill[]>([])
// Phase 2: 全局 LLM Judge 开关状态（简单标记，无需实时查询系统设置）
const globalJudgeEnabled = ref(false)

// 加载全局 LLM Judge 状态
async function loadGlobalJudgeState() {
  try {
    const { getFeatureToggles } = await import('../api/settings')
    const toggles = await getFeatureToggles()
    if (toggles.llm_judge) {
      globalJudgeEnabled.value = toggles.llm_judge.value !== 'off'
    }
  } catch (e) {
    // 查询失败不影响页面，保持默认 false
  }
}
loadGlobalJudgeState()
// ==================== 计算属性 ====================

const filteredSkills = computed(() => {
  let result = skills.value
  if (filters.category) {
    result = result.filter(s => s.category === filters.category)
  }
  if (filters.search) {
    const keyword = filters.search.toLowerCase()
    result = result.filter(s =>
      s.name.toLowerCase().includes(keyword) ||
      s.title.toLowerCase().includes(keyword) ||
      s.description.toLowerCase().includes(keyword)
    )
  }
  if (filters.executionMode) {
    result = result.filter(s => (s.execution_mode || 'normal') === filters.executionMode)
  }
  if (filters.modelTier) {
    result = result.filter(s => (s.model_tier || 'mid') === filters.modelTier)
  }
  if (filters.enabled) {
    const isEnabled = filters.enabled === 'true'
    result = result.filter(s => s.enabled === isEnabled)
  }
  if (filters.outputFormat) {
    if (filters.outputFormat === '_null') {
      result = result.filter(s => !s.output_format)
    } else {
      result = result.filter(s => s.output_format === filters.outputFormat)
    }
  }
  if (filterPendingReview.value) {
    result = result.filter(s => !s.enabled && !s.is_system)
  }
  return result
})

// 待审核数量
const pendingReviewCount = computed(() =>
  skills.value.filter(s => !s.enabled && !s.is_system).length
)

const categoryOptions = computed(() => [
  { label: '全部分类', value: '' },
  ...categories.value.map(c => ({ label: getCategoryName(c), value: c }))
])

const drawerTitle = computed(() => {
  if (drawerMode.value === 'create') {
    return createSource.value === 'ai-generated' ? '创建 Skill（AI 已填充）' : '创建 Skill'
  }
  return `编辑 Skill: ${originalSkill.value?.title || ''}`
})

// 工具 Transfer 数据源
const toolTransferOptions = computed<TransferOption[]>(() =>
  allTools.value
    .filter(t => !disabledTools.value.includes(t.name))
    .map(t => ({ value: t.name, label: getToolLabel(t.name), description: t.description }))
)

// 子 Skill 选项（排除自身）
const subSkillOptions = computed(() =>
  skills.value
    .filter(s => s.name !== editForm.name)
    .map(s => ({ label: s.title || s.name, value: s.name, description: s.description || '', category: s.category || '' }))
)

// 追加到系统提示词的内容预览
const previewTools = computed(() => {
  let tools: string[]
  if (editForm.tool_tier) {
    const included = resolveTierTools(editForm.tool_tier)
    const denied = new Set(editForm.denied_tools)
    const base = included.filter(t => !denied.has(t))
    const extra = editForm.extra_tools.filter(t => !denied.has(t))
    tools = [...base, ...extra]
  } else {
    tools = editForm.allowed_tools
  }

  // sandbox_enabled 时自动追加沙箱工具
  if (editForm.sandbox_enabled) {
    tools = [...tools, ...SANDBOX_TOOLS.filter(t => !tools.includes(t))]
  }

  return tools
})

const promptPreview = computed(() => {
  const tools = previewTools.value
  if (tools.length === 0 && editForm.sub_skills.length === 0) return ''

  let preview = ''

  if (tools.length > 0) {
    preview += '## 可用工具\n\n'
    tools.forEach(name => {
      const tool = allTools.value.find(t => t.name === name)
      const desc = tool?.description || name
      const label = TOOL_DISPLAY_NAMES.value[name] || name
      preview += `- **${label}** (${name}): ${desc}\n`
    })
  }

  if (editForm.sub_skills.length > 0) {
    preview += '\n## 可委派的子任务\n\n'
    editForm.sub_skills.forEach(name => {
      const skill = skills.value.find(s => s.name === name)
      preview += `- ${name}: ${skill?.title || name} — ${skill?.description || ''}\n`
    })
  }

  return preview
})

// 模型等级配置（从 Settings 动态加载）
const modelTierConfigs = ref<Record<string, ModelTierConfig>>({})

// 动态模型等级显示映射（加载成功用动态值，否则 fallback 到静态 MODEL_TIER_MAP）
const dynamicModelTierMap = computed(() => {
  const tiers = modelTierConfigs.value
  if (Object.keys(tiers).length === 0) return MODEL_TIER_MAP
  const map: Record<string, string> = {}
  for (const [tier, info] of Object.entries(tiers)) {
    map[tier] = `${info.display_name} (${info.model})`
  }
  return map
})

const modelTierOptions = computed(() => {
  const tiers = modelTierConfigs.value
  const modelName = (tier: string) => tiers[tier]?.model ? ` → ${tiers[tier].model}` : ''
  return [
    { label: `高性能${modelName('high')}`, value: 'high' },
    { label: `标准${modelName('mid')}`, value: 'mid' },
    { label: `轻量${modelName('low')}`, value: 'low' },
  ]
})

// 执行模式选项
const executionModeOptions = [
  { label: '普通模式', value: 'normal', desc: '单轮调用，直接返回结果' },
  { label: '计划模式', value: 'plan', desc: '自动拆分为多个阶段依次执行，适合深度调研等复杂任务' },
  { label: '对话模式', value: 'conversational', desc: '与用户多轮交互，逐步引导完成任务（如头脑风暴）' },
  { label: '课堂模式', value: 'classroom', desc: '独立编排引擎，生成场景序列+幻灯片+TTS，前端用 ClassroomPlayer 播放' },
]

// 输出格式选项（Artifact 系统）
const outputFormatOptions = [
  { label: '💬 普通输出（默认）', value: '_default', description: '内容在对话气泡中流式显示，适合问答、搜索等短输出场景' },
  { type: 'group', label: '文档编辑', key: 'g-doc', children: [
    { label: '📄 Artifact 面板', value: 'artifact', description: '内容输出到右侧文档编辑面板，支持版本管理、高亮修改和 Word/PDF 导出。适合报告、公文、教案等需要多轮润色的长文档' },
  ]},
  { type: 'group', label: '富媒体渲染', key: 'g-html', children: [
    { label: '🎬 HTML 动画', value: 'html-animation', description: '输出为可播放的 HTML 动画，适合知识点讲解、流程演示' },
    { label: '📊 HTML 演示', value: 'html-slides', description: '输出为 HTML 幻灯片，适合 PPT 风格的分页展示' },
    { label: '🎮 HTML 交互', value: 'html-interactive', description: '输出为可交互的 HTML 页面，适合数据可视化、互动教学' },
  ]},
  { type: 'group', label: '课堂教学', key: 'g-classroom', children: [
    { label: '🏫 课堂模式', value: 'classroom', description: '生成结构化课堂场景（幻灯片+语音+检测题+讨论），前端用 ClassroomPlayer 播放' },
  ]},
]

// 输出格式：下拉框选项渲染（仅在下拉列表中显示描述，选中态只显示标签）
function renderOutputFormatLabel(option: any, selected: boolean) {
  if (option.type === 'group') return option.label
  if (selected) return option.label
  return h('div', { style: 'display:flex;flex-direction:column;padding:4px 0' }, [
    h('span', {}, option.label),
    option.description
      ? h('span', { style: 'color:#999;font-size:11px;line-height:1.3;margin-top:2px;white-space:normal' }, option.description)
      : null,
  ])
}

// 输出格式：底部说明文字（根据选中值动态变化）
const OUTPUT_FORMAT_HINTS: Record<string, string> = {
  '': '内容在对话气泡中流式显示，适合问答、搜索等短输出场景',
  'artifact': '输出到右侧 Artifact 编辑面板，用户可编辑、版本回溯、导出 Word/PDF。适合报告、公文、教案等需要多轮润色的长文档',
  'html-animation': '输出为可播放的 HTML 动画，前端用 AnimationPlayer 渲染',
  'html-slides': '输出为 HTML 幻灯片，前端用 InteractivePlayer 分页展示',
  'html-interactive': '输出为可交互的 HTML 页面，前端用 InteractivePlayer 渲染',
  'classroom': '生成结构化课堂场景（幻灯片+语音+检测题+讨论），前端用 ClassroomPlayer 整页播放',
}
const outputFormatHint = computed(() => {
  const _of = editForm.output_format === '_default' ? '' : (editForm.output_format || '')
  return OUTPUT_FORMAT_HINTS[_of] || OUTPUT_FORMAT_HINTS['']
})

// 内置技能中文名和说明
const BUILTIN_SKILL_DISPLAY: Record<string, { label: string; desc: string }> = {
  plan: {
    label: '规划分析',
    desc: '启用后，Skill 可调用内置的规划分析能力来分解复杂需求、制定执行计划',
  },
}

// 执行模式中文映射（用于列表卡片标签）
const EXECUTION_MODE_MAP: Record<string, string> = {
  plan: '计划模式',
  conversational: '对话模式',
  classroom: '课堂模式',
}

// 输出格式标签映射（用于列表卡片标签）
const OUTPUT_FORMAT_TAG: Record<string, string> = {
  artifact: '📄 Artifact',
  'html-animation': '🎬 动画',
  'html-slides': '📊 演示',
  'html-interactive': '🎮 交互',
}

// 会话隔离模式中文映射
const CONTEXT_MODE_MAP: Record<string, string> = {
  fork: '独立会话（每次新建）',
  maintain: '保持会话（跨轮复用）',
  shared: '共享主会话',
}

// 工具中文名映射
// 工具显示名（从 API display_name 动态构建，allTools 加载后自动更新）
const TOOL_DISPLAY_NAMES = computed<Record<string, string>>(() => {
  const map: Record<string, string> = {}
  for (const t of allTools.value) {
    if ((t as any).display_name) {
      map[t.name] = (t as any).display_name
    }
  }
  return map
})

// 工具层级定义（与后端 TOOL_TIERS + TIER_HIERARCHY 保持一致）
const TOOL_TIERS: Record<string, string[]> = {
  common: ['kb_query', 'write_file'],
  standard: ['web_search', 'web_fetch', 'academic_search'],
  extended: ['wikipedia_search', 'wikipedia_get_article'],
}
const TIER_HIERARCHY = ['common', 'standard', 'extended']

// 沙箱工具列表（通过 sandbox_enabled 开关控制，不参与 tier/extra/denied）
const SANDBOX_TOOLS = ['sandbox_bash', 'sandbox_ls', 'sandbox_read_file',
                       'sandbox_write_file', 'sandbox_str_replace']

/** 解析某层级包含的所有工具（含继承） */
function resolveTierTools(tier: string): string[] {
  const idx = TIER_HIERARCHY.indexOf(tier)
  if (idx < 0) return []
  const result: string[] = []
  for (let i = 0; i <= idx; i++) {
    result.push(...(TOOL_TIERS[TIER_HIERARCHY[i]] || []))
  }
  return result
}

/** 当前层级已包含的工具 */
const tierIncludedTools = computed(() =>
  editForm.tool_tier ? resolveTierTools(editForm.tool_tier) : []
)

/** "额外添加"选项：全部工具 - 当前层级已包含的 */
const extraToolOptions = computed(() => {
  const included = new Set(tierIncludedTools.value)
  return toolTransferOptions.value.filter(o =>
    !included.has(o.value as string) &&
    !SANDBOX_TOOLS.includes(o.value as string)
  )
})

/** "排除"选项：当前层级已包含的工具 */
const deniedToolOptions = computed(() => {
  const included = new Set(tierIncludedTools.value)
  return toolTransferOptions.value.filter(o =>
    included.has(o.value as string) &&
    !SANDBOX_TOOLS.includes(o.value as string)
  )
})

function getToolLabel(name: string): string {
  const displayName = TOOL_DISPLAY_NAMES.value[name]
  return displayName ? `${displayName} (${name})` : name
}

// Select2 风格工具选项渲染
function renderToolOption(option: any) {
  const name = option.value as string
  const dn = TOOL_DISPLAY_NAMES.value[name]
  const tool = allTools.value.find(t => t.name === name)
  const desc = tool?.description || ''
  return h('div', { style: 'padding: 4px 0' }, [
    h('div', { style: 'font-size: 13px; font-weight: 500' }, dn || name),
    dn ? h('code', { style: 'font-size: 11px; color: #999;' }, name) : null,
    desc ? h('div', { style: 'font-size: 11px; color: #999; margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 360px' }, desc) : null,
  ])
}

function renderToolTag({ option, handleClose }: { option: any; handleClose: () => void }) {
  const name = option.value as string
  const dn = TOOL_DISPLAY_NAMES.value[name] || name
  return h('div', {
    style: 'display: inline-flex; align-items: center; gap: 4px; padding: 0 6px; margin: 2px; border-radius: 3px; background: #f0f9ff; border: 1px solid #91caff; font-size: 12px; line-height: 22px',
  }, [
    h('span', {}, dn),
    h('span', { style: 'cursor: pointer; margin-left: 2px; color: #999; font-size: 14px', onClick: (e: Event) => { e.stopPropagation(); handleClose() } }, '×'),
  ])
}

function filterToolOption(pattern: string, option: any): boolean {
  const p = pattern.toLowerCase()
  const name = ((option.value as string) || '').toLowerCase()
  const dn = (TOOL_DISPLAY_NAMES.value[option.value as string] || '').toLowerCase()
  const tool = allTools.value.find(t => t.name === option.value)
  const desc = (tool?.description || '').toLowerCase()
  return name.includes(p) || dn.includes(p) || desc.includes(p)
}

// Select2 风格 Skill 选项渲染
function renderSkillOption(option: any) {
  const name = option.value as string
  const desc = (option.description || '').slice(0, 60)
  const cat = option.category ? getCategoryName(option.category) : ''
  return h('div', { style: 'padding: 4px 0' }, [
    h('div', { style: 'display: flex; align-items: center; gap: 6px' }, [
      h('span', { style: 'font-size: 13px; font-weight: 500' }, option.label as string),
      cat ? h('span', { style: 'font-size: 10px; color: #999; background: #f5f5f5; padding: 1px 5px; border-radius: 3px' }, cat) : null,
    ]),
    h('code', { style: 'font-size: 11px; color: #aaa;' }, name),
    desc ? h('div', { style: 'font-size: 11px; color: #999; margin-top: 1px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 360px' }, desc) : null,
  ])
}

function renderSkillTag({ option, handleClose }: { option: any; handleClose: () => void }) {
  return h('div', {
    style: 'display: inline-flex; align-items: center; gap: 4px; padding: 0 6px; margin: 2px; border-radius: 3px; background: #f6ffed; border: 1px solid #b7eb8f; font-size: 12px; line-height: 22px',
  }, [
    h('span', {}, option.label as string),
    h('span', { style: 'cursor: pointer; margin-left: 2px; color: #999; font-size: 14px', onClick: (e: Event) => { e.stopPropagation(); handleClose() } }, '×'),
  ])
}

function filterSkillOption(pattern: string, option: any): boolean {
  const p = pattern.toLowerCase()
  return ((option.value as string) || '').toLowerCase().includes(p) ||
    ((option.label as string) || '').toLowerCase().includes(p) ||
    ((option.description as string) || '').toLowerCase().includes(p)
}

function effectiveTools(skill: any): string[] {
  return skill.resolved_tools?.length > 0
    ? skill.resolved_tools
    : skill.allowed_tools || []
}

// 分类图标映射
const categoryIcons: Record<string, any> = {
  'research': CodeSlashOutline,
  'document': DocumentTextOutline,
  'search': GlobeOutline,
  'education': SchoolOutline,
  'analysis': AnalyticsOutline,
  'general': AppsOutline
}

// ==================== 方法 ====================

async function loadData() {
  loading.value = true
  try {
    const [skillsRes, statsRes, categoriesRes] = await Promise.all([
      getSkills(), getSkillStats(), getSkillCategories()
    ])
    skills.value = skillsRes.skills || []
    stats.value = statsRes
    categories.value = categoriesRes || []
    // 加载模型等级配置（用于显示实际模型名）
    try {
      const modelRes = await getModelConfig()
      modelTierConfigs.value = modelRes.tiers || {}
    } catch { /* 非关键，静默失败 */ }
  } catch (error: any) {
    message.error(error.message || '加载数据失败')
  } finally {
    loading.value = false
  }
}

async function loadTools() {
  try {
    const res = await getTools()
    allTools.value = res.tools || []
    disabledTools.value = res.disabled || []
  } catch (error: any) {
    console.error('加载工具列表失败:', error)
  }
}

function handleToggleEnabled(skill: Skill) {
  updateSkill(skill.name, { enabled: !skill.enabled })
    .then(() => {
      skill.enabled = !skill.enabled
      message.success(`${skill.title} 已${skill.enabled ? '启用' : '禁用'}`)
    })
    .catch((error: any) => message.error(error.message || '操作失败'))
}

// 打开编辑抽屉
async function handleEdit(skill: Skill) {
  drawerMode.value = 'edit'
  drawerVisible.value = true
  drawerLoading.value = true
  activeTab.value = 'basic'

  // 并行加载详情和工具列表
  try {
    const [detail] = await Promise.all([
      getSkill(skill.name),
      allTools.value.length === 0 ? loadTools() : Promise.resolve(),
      knowledgeBases.value.length === 0 ? loadKbAndModels() : Promise.resolve()
    ])
    originalSkill.value = detail
    fillForm(detail)
  } catch (error: any) {
    message.error(error.message || '获取详情失败')
    originalSkill.value = skill
    fillForm(skill)
  } finally {
    drawerLoading.value = false
  }
  // 异步加载审计日志和包内资源（不阻塞抽屉打开）
  loadAuditLogs()
  loadSkillResources(skill.name)
}

// 打开手动创建抽屉
async function handleCreate() {
  createSource.value = 'admin'
  showAiHint.value = false
  drawerMode.value = 'create'
  drawerVisible.value = true
  drawerLoading.value = true
  activeTab.value = 'basic'
  originalSkill.value = null
  auditLogs.value = []
  skillResources.value = []

  resetForm()

  if (allTools.value.length === 0) {
    await loadTools()
  }
  drawerLoading.value = false
}

// 打开 AI 辅助创建
function handleAiCreate() {
  aiDrawerVisible.value = true
}

// AI 生成完毕，切换到表单审核
async function handleAiConfigGenerated(config: Record<string, any>) {
  aiDrawerVisible.value = false
  createSource.value = 'ai-generated'
  showAiHint.value = true
  drawerMode.value = 'create'
  drawerVisible.value = true
  drawerLoading.value = true
  activeTab.value = 'basic'
  originalSkill.value = null
  auditLogs.value = []
  skillResources.value = []

  resetForm()

  // 从 AI 配置填充表单
  if (config.name) editForm.name = config.name
  if (config.display_name) editForm.display_name = config.display_name
  if (config.description) editForm.description = config.description
  if (config.category) editForm.category = config.category
  if (config.execution_mode) editForm.execution_mode = config.execution_mode
  if (config.model_tier) editForm.model_tier = config.model_tier
  if (config.output_format) editForm.output_format = config.output_format
  else editForm.output_format = '_default'
  if (config.sort_order != null) editForm.sort_order = config.sort_order
  if (config.system_prompt) editForm.system_prompt = config.system_prompt
  if (config.tool_tier) editForm.tool_tier = config.tool_tier
  if (config.extra_tools) editForm.extra_tools = [...config.extra_tools]
  if (config.denied_tools) editForm.denied_tools = [...config.denied_tools]
  if (config.trigger_keywords) editForm.trigger_keywords = [...config.trigger_keywords]
  if (config.intent_priority != null) editForm.intent_priority = config.intent_priority
  if (config.regex_patterns) editForm.regex_patterns = [...config.regex_patterns]
  if (config.verification_config) {
    editForm.verification_config = {
      strategy: config.verification_config.strategy || 'rules_only',
      dimensions: (config.verification_config.dimensions || []).map((d: any) => ({
        name: d.name || '',
        description: d.description || '',
        weight: d.weight ?? 0,
      })),
      min_score: config.verification_config.min_score ?? 0.6,
    }
  }
  if (config.exclusion_rules) {
    const excRules = config.exclusion_rules
    editForm.exclude_keywords = [...(excRules.exclude_if_contains || [])]
    editForm.exclude_pairs = (excRules.exclude_if_both || []).map(
      (p: any) => [p[0] || '', p[1] || ''] as [string, string]
    )
  }
  if (config.max_iterations != null) {
    // max_iterations 目前表单中没有独立字段，但会在保存时使用
  }

  if (allTools.value.length === 0) {
    await loadTools()
  }
  drawerLoading.value = false
}

// 返回 AI 对话
function handleBackToAi() {
  drawerVisible.value = false
  aiDrawerVisible.value = true
}

// 加载审计日志
async function loadAuditLogs() {
  const name = editForm.name
  if (!name) { auditLogs.value = []; return }
  try {
    const res = await getSkillAuditLogs(name, 20)
    auditLogs.value = res.logs || []
  } catch {
    auditLogs.value = []
  }
}

function handleToolModeSwitch(mode: string) {
  if (mode === 'manual') {
    // 分层 → 手动：将当前生效工具自动填入 allowed_tools
    const resolved = originalSkill.value?.resolved_tools || []
    const validValues = new Set(toolTransferOptions.value.map(o => o.value))
    editForm.allowed_tools = resolved.filter(t => validValues.has(t))
    editForm.tool_tier = null
    editForm.extra_tools = []
    editForm.denied_tools = []
  } else {
    // 手动 → 分层：清空手动选择，默认 standard 层
    editForm.tool_tier = 'standard'
    editForm.allowed_tools = []
  }
}

function handleTierChange(tier: string) {
  const oldTier = editForm.tool_tier
  editForm.tool_tier = tier

  // 层级变更后，清理已失效的 extra/denied 选项
  const newIncluded = new Set(resolveTierTools(tier))

  // extra_tools 中，如果某工具已被新层级包含，则移除（不再需要"额外添加"了）
  editForm.extra_tools = editForm.extra_tools.filter(t => !newIncluded.has(t))

  // denied_tools 中，如果某工具不在新层级中，则移除（排除一个不存在的工具没意义）
  editForm.denied_tools = editForm.denied_tools.filter(t => newIncluded.has(t))
}

function fillForm(skill: Skill) {
  editForm.name = skill.name
  editForm.display_name = skill.display_name || skill.title || ''
  editForm.description = skill.description || ''
  editForm.category = skill.category || ''
  editForm.execution_mode = skill.execution_mode || 'normal'
  editForm.model_tier = skill.model_tier || 'mid'
  editForm.sort_order = skill.sort_order || 0
  editForm.system_prompt = skill.system_prompt || ''
  editForm.allowed_tools = [...(skill.allowed_tools || [])]
  editForm.trigger_keywords = [...(skill.trigger_keywords || [])]
  editForm.sub_skills = [...(skill.sub_skills || [])]
  const sac = skill.sub_agent_config || {}
  editForm.sub_agent_config = {
    max_depth: sac.max_depth ?? 2,
    timeout: sac.timeout ?? 300,
    max_concurrent: sac.max_concurrent ?? 3,
    allow_background: sac.allow_background ?? false
  }
  editForm.tool_tier = skill.tool_tier || null
  editForm.extra_tools = [...(skill.extra_tools || [])]
  editForm.denied_tools = [...(skill.denied_tools || [])]
  // v11.0: Capability 抽象层
  editForm.required_capabilities = [...(skill.required_capabilities || [])]
  editForm.optional_capabilities = [...(skill.optional_capabilities || [])]
  editForm.preferred_sub_skills = [...(skill.preferred_sub_skills || [])]
  // v12.0(Stage 7h): 平台 Override
  editForm.intent_priority = skill.intent_priority ?? null
  editForm.regex_patterns = [...(skill.regex_patterns || [])]
  const excRules = skill.exclusion_rules || {}
  editForm.exclude_keywords = [...(excRules.exclude_if_contains || [])]
  editForm.exclude_pairs = (excRules.exclude_if_both || []).map((p: any) => [p[0] || '', p[1] || ''] as [string, string])
  // Phase 2: 验证配置
  const vc = skill.verification_config
  if (vc && vc.strategy) {
    editForm.verification_config = {
      strategy: vc.strategy || 'rules_only',
      dimensions: (vc.dimensions || []).map((d: any) => ({ name: d.name || '', description: d.description || '', weight: d.weight ?? 0 })),
      min_score: vc.min_score ?? 0.6,
    }
  } else {
    editForm.verification_config = null
  }
  // P9: 沙箱配置
  editForm.sandbox_enabled = skill.sandbox_enabled || false
  editForm.sandbox_timeout = skill.sandbox_timeout || 600
  editForm.sandbox_memory_mb = skill.sandbox_memory_mb || 512
  // Aggregation@N
  editForm.aggregation_n = skill.aggregation_n || 1
  // v9.0: 上下文与状态管理
  editForm.high_context_threshold = skill.high_context_threshold || false
  editForm.state_tracking = skill.state_tracking || false
  // Phase 1.4: 压缩策略
  editForm.compression_policy = (skill as any).compression_policy || { compress_threshold: 25, preserve_tool_results: false, preserve_categories: [] }
  // 推断 preset
  if ((skill as any).compression_policy) {
    const cp = (skill as any).compression_policy
    if (cp.compress_threshold >= 40 && cp.preserve_tool_results) editForm.compression_preset = 'research'
    else if (cp.compress_threshold >= 28 && !cp.preserve_tool_results) editForm.compression_preset = 'generate'
    else editForm.compression_preset = 'custom'
  } else if (skill.high_context_threshold) {
    editForm.compression_preset = 'research'
    editForm.compression_policy = { compress_threshold: 45, preserve_tool_results: true, preserve_categories: [] }
  } else {
    editForm.compression_preset = 'default'
  }
  // v10.0: 知识库绑定 + 模型选择
  editForm.kb_bindings = ((skill as any).kb_bindings || []).map((b: any) => ({
    kb_id: b.kb_id,
    scope: b.categories?.length ? 'categories' : b.doc_ids?.length ? 'doc_ids' : 'all',
    categories: b.categories || [],
    doc_ids: b.doc_ids || [],
  }))
  editForm.model_id = (skill as any).model_id || ''
  editForm.model_select_mode = editForm.model_id ? 'specific' : 'tier'
  editForm.include_personal_kb = true
  editForm.plan_stages = JSON.parse(JSON.stringify((skill as any).plan_stages || []))
  editForm.output_format = (skill as any).output_format || '_default'

  // 预加载已绑定KB的文档列表（分类筛选和指定文件模式需要）
  for (const b of editForm.kb_bindings) {
    if (b.scope === 'categories' || b.scope === 'doc_ids') {
      loadKbDocs(b.kb_id)
    }
  }
}

function resetForm() {
  editForm.name = ''
  editForm.display_name = ''
  editForm.description = ''
  editForm.category = ''
  editForm.execution_mode = 'normal'
  editForm.model_tier = 'mid'
  editForm.sort_order = 0
  editForm.system_prompt = ''
  editForm.allowed_tools = []
  editForm.trigger_keywords = []
  editForm.sub_skills = []
  editForm.sub_agent_config = { max_depth: 2, timeout: 300, max_concurrent: 3, allow_background: false }
  editForm.tool_tier = null
  editForm.extra_tools = []
  editForm.denied_tools = []
  // v11.0: Capability
  editForm.required_capabilities = []
  editForm.optional_capabilities = []
  editForm.preferred_sub_skills = []
  // v12.0(Stage 7h): 平台 Override
  editForm.intent_priority = null
  editForm.regex_patterns = []
  editForm.exclude_keywords = []
  editForm.exclude_pairs = []
  editForm.verification_config = null
  editForm.sandbox_enabled = false
  editForm.sandbox_timeout = 600
  editForm.sandbox_memory_mb = 512
  editForm.aggregation_n = 1
  editForm.high_context_threshold = false
  editForm.state_tracking = false
  editForm.compression_policy = { compress_threshold: 25, preserve_tool_results: false, preserve_categories: [] }
  editForm.compression_preset = 'default'
  editForm.kb_bindings = []
  editForm.model_id = ''
  editForm.model_select_mode = 'tier'
  editForm.include_personal_kb = true
  editForm.plan_stages = []
}

// 保存
async function handleSave() {
  saving.value = true
  try {
    if (drawerMode.value === 'create') {
      // 创建模式
      if (!editForm.name) {
        message.warning('请输入 Skill 名称')
        saving.value = false
        return
      }
      if (!/^[a-z0-9-]+$/.test(editForm.name)) {
        message.warning('名称只能包含小写字母、数字和连字符')
        saving.value = false
        return
      }
      // 构建排他规则
      const excRules: Record<string, any> = {}
      if (editForm.exclude_keywords.length > 0) {
        excRules.exclude_if_contains = editForm.exclude_keywords
      }
      const validPairsCreate = editForm.exclude_pairs.filter(p => p[0].trim() && p[1].trim())
      if (validPairsCreate.length > 0) {
        excRules.exclude_if_both = validPairsCreate.map(p => [p[0].trim(), p[1].trim()])
      }

      // 前端校验：如果启用了验证配置，权重必须合计为 1
      if (editForm.verification_config?.dimensions?.length) {
        const totalWeight = editForm.verification_config.dimensions.reduce((s, d) => s + (d.weight || 0), 0)
        if (Math.abs(totalWeight - 1.0) > 0.01) {
          message.error(`维度权重之和必须为 1.0，当前为 ${totalWeight.toFixed(2)}`)
          saving.value = false
          return
        }
      }

      const isAiSource = createSource.value === 'ai-generated'
      const payload: SkillCreateRequest = {
        name: editForm.name,
        display_name: editForm.display_name,
        description: editForm.description || undefined,
        category: editForm.category || undefined,
        allowed_tools: editForm.allowed_tools,
        system_prompt: editForm.system_prompt || undefined,
        trigger_keywords: editForm.trigger_keywords.length ? editForm.trigger_keywords : undefined,
        execution_mode: editForm.execution_mode,
        model_tier: editForm.model_tier,
        sort_order: editForm.sort_order,
        sub_skills: editForm.sub_skills.length ? editForm.sub_skills : undefined,
        sub_agent_config: editForm.sub_skills.length ? editForm.sub_agent_config : undefined,
        tool_tier: editForm.tool_tier,
        extra_tools: editForm.extra_tools.length ? editForm.extra_tools : undefined,
        denied_tools: editForm.denied_tools.length ? editForm.denied_tools : undefined,
        required_capabilities: editForm.required_capabilities.length ? editForm.required_capabilities : undefined,
        optional_capabilities: editForm.optional_capabilities.length ? editForm.optional_capabilities : undefined,
        preferred_sub_skills: editForm.preferred_sub_skills.length ? editForm.preferred_sub_skills : undefined,
        // v12.0(Stage 7h): 平台 Override
        verification_config: editForm.verification_config || undefined,
        intent_priority: editForm.intent_priority,
        regex_patterns: editForm.regex_patterns.length ? editForm.regex_patterns : undefined,
        exclusion_rules: Object.keys(excRules).length > 0 ? excRules : undefined,
        high_context_threshold: editForm.high_context_threshold,
        state_tracking: editForm.state_tracking,
        compression_policy: editForm.compression_preset !== 'default' ? editForm.compression_policy : undefined,
        plan_stages: editForm.plan_stages.length ? editForm.plan_stages : undefined,
        output_format: editForm.output_format === '_default' ? undefined : (editForm.output_format || undefined),
        source: createSource.value,
        is_enabled: isAiSource ? false : true,  // AI 创建默认禁用
      }
      await createSkill(payload)
      message.success(isAiSource ? 'Skill 创建成功（待审核，已默认禁用）' : 'Skill 创建成功')
    } else {
      // 编辑模式：只发送修改过的字段
      const changes: SkillUpdateRequest = {}
      const orig = originalSkill.value
      if (!orig) return

      if (editForm.display_name !== (orig.display_name || orig.title)) changes.display_name = editForm.display_name
      if (editForm.description !== (orig.description || '')) changes.description = editForm.description
      if (editForm.category !== (orig.category || '')) changes.category = editForm.category
      if (editForm.execution_mode !== (orig.execution_mode || 'normal')) changes.execution_mode = editForm.execution_mode
      if (editForm.model_tier !== (orig.model_tier || 'mid')) changes.model_tier = editForm.model_tier
      if (editForm.sort_order !== (orig.sort_order || 0)) changes.sort_order = editForm.sort_order
      if (editForm.system_prompt !== (orig.system_prompt || '')) changes.system_prompt = editForm.system_prompt

      const origTools = (orig.allowed_tools || []).sort().join(',')
      const newTools = [...editForm.allowed_tools].sort().join(',')
      if (origTools !== newTools) changes.allowed_tools = editForm.allowed_tools

      const origKw = (orig.trigger_keywords || []).sort().join(',')
      const newKw = [...editForm.trigger_keywords].sort().join(',')
      if (origKw !== newKw) changes.trigger_keywords = editForm.trigger_keywords

      const origSub = (orig.sub_skills || []).sort().join(',')
      const newSub = [...editForm.sub_skills].sort().join(',')
      if (origSub !== newSub) changes.sub_skills = editForm.sub_skills

      const origSac = orig.sub_agent_config || {}
      if (
        editForm.sub_agent_config.max_depth !== (origSac.max_depth ?? 2) ||
        editForm.sub_agent_config.timeout !== (origSac.timeout ?? 300) ||
        editForm.sub_agent_config.max_concurrent !== (origSac.max_concurrent ?? 3) ||
        editForm.sub_agent_config.allow_background !== (origSac.allow_background ?? false)
      ) {
        changes.sub_agent_config = { ...editForm.sub_agent_config }
      }

      // tool_tier / extra_tools / denied_tools
      if ((editForm.tool_tier || null) !== (orig.tool_tier || null)) changes.tool_tier = editForm.tool_tier
      const origExtra = (orig.extra_tools || []).sort().join(',')
      const newExtra = [...editForm.extra_tools].sort().join(',')
      if (origExtra !== newExtra) changes.extra_tools = editForm.extra_tools
      const origDenied = (orig.denied_tools || []).sort().join(',')
      const newDenied = [...editForm.denied_tools].sort().join(',')
      if (origDenied !== newDenied) changes.denied_tools = editForm.denied_tools

      // v11.0: Capability 抽象层 diff
      const origReqCap = (orig.required_capabilities || []).sort().join(',')
      const newReqCap = [...editForm.required_capabilities].sort().join(',')
      if (origReqCap !== newReqCap) {
        changes.required_capabilities = editForm.required_capabilities.length
          ? editForm.required_capabilities : null
      }
      const origOptCap = (orig.optional_capabilities || []).sort().join(',')
      const newOptCap = [...editForm.optional_capabilities].sort().join(',')
      if (origOptCap !== newOptCap) {
        changes.optional_capabilities = editForm.optional_capabilities.length
          ? editForm.optional_capabilities : null
      }
      const origPref = (orig.preferred_sub_skills || []).sort().join(',')
      const newPref = [...editForm.preferred_sub_skills].sort().join(',')
      if (origPref !== newPref) {
        changes.preferred_sub_skills = editForm.preferred_sub_skills.length
          ? editForm.preferred_sub_skills : null
      }

      // intent_priority / regex_patterns / exclusion_rules
      if ((editForm.intent_priority ?? null) !== (orig.intent_priority ?? null)) {
        changes.intent_priority = editForm.intent_priority
      }
      const origRegex = (orig.regex_patterns || []).sort().join(',')
      const newRegex = [...editForm.regex_patterns].sort().join(',')
      if (origRegex !== newRegex) {
        changes.regex_patterns = editForm.regex_patterns.length ? editForm.regex_patterns : null
      }
      // 从 exclude_keywords + exclude_pairs 重建 exclusion_rules 对象
      const newExcRules: Record<string, any> = {}
      if (editForm.exclude_keywords.length > 0) {
        newExcRules.exclude_if_contains = editForm.exclude_keywords
      }
      const validPairs = editForm.exclude_pairs.filter(p => p[0].trim() && p[1].trim())
      if (validPairs.length > 0) {
        newExcRules.exclude_if_both = validPairs.map(p => [p[0].trim(), p[1].trim()])
      }
      const origExcJson = orig.exclusion_rules ? JSON.stringify(orig.exclusion_rules, Object.keys(orig.exclusion_rules).sort()) : ''
      const newExcJson = Object.keys(newExcRules).length > 0 ? JSON.stringify(newExcRules, Object.keys(newExcRules).sort()) : ''
      if (origExcJson !== newExcJson) {
        changes.exclusion_rules = Object.keys(newExcRules).length > 0 ? newExcRules : null
      }

      // P9: sandbox 字段 diff
      if (editForm.sandbox_enabled !== (orig.sandbox_enabled || false)) {
        changes.sandbox_enabled = editForm.sandbox_enabled
      }
      if (editForm.sandbox_timeout !== (orig.sandbox_timeout || 600)) {
        changes.sandbox_timeout = editForm.sandbox_timeout
      }
      if (editForm.sandbox_memory_mb !== (orig.sandbox_memory_mb || 512)) {
        changes.sandbox_memory_mb = editForm.sandbox_memory_mb
      }

      // Aggregation@N diff
      if (editForm.aggregation_n !== (orig.aggregation_n || 1)) {
        changes.aggregation_n = editForm.aggregation_n
      }

      // v9.0: 上下文与状态管理字段 diff
      if (editForm.high_context_threshold !== (orig.high_context_threshold || false)) {
        changes.high_context_threshold = editForm.high_context_threshold
      }
      if (editForm.state_tracking !== (orig.state_tracking || false)) {
        changes.state_tracking = editForm.state_tracking
      }

      // plan_stages diff
      const origPsJson = JSON.stringify((orig as any).plan_stages || [])
      const newPsJson = JSON.stringify(editForm.plan_stages)
      if (origPsJson !== newPsJson) {
        (changes as any).plan_stages = editForm.plan_stages.length ? editForm.plan_stages : null
      }

      // output_format diff (Artifact 系统)
      const _newOf = editForm.output_format === '_default' ? null : (editForm.output_format || null)
      const _origOf = (orig as any).output_format || null
      if (_newOf !== _origOf) {
        (changes as any).output_format = _newOf
      }

      // Phase 2: verification_config diff
      const origVcJson = orig.verification_config ? JSON.stringify(orig.verification_config) : ''
      const newVcJson = editForm.verification_config ? JSON.stringify(editForm.verification_config) : ''
      if (origVcJson !== newVcJson) {
        // 前端校验：如果启用了验证配置，权重必须合计为 1
        if (editForm.verification_config?.dimensions?.length) {
          const totalWeight = editForm.verification_config.dimensions.reduce((s, d) => s + (d.weight || 0), 0)
          if (Math.abs(totalWeight - 1.0) > 0.01) {
            message.error(`维度权重之和必须为 1.0，当前为 ${totalWeight.toFixed(2)}`)
            saving.value = false
            return
          }
          const names = editForm.verification_config.dimensions.map(d => d.name)
          if (new Set(names).size !== names.length) {
            message.error('维度名称不能重复')
            saving.value = false
            return
          }
        }
        changes.verification_config = editForm.verification_config
      }

      // v10.0: model_id + kb_bindings diff
      const effectiveModelId = editForm.model_select_mode === 'specific' ? editForm.model_id : ''
      const origModelId = (orig as any).model_id || ''
      if (effectiveModelId !== origModelId) {
        (changes as any).model_id = effectiveModelId || null
      }
      // kb_bindings: 序列化 scope 到 categories/doc_ids
      const serializeKb = (bindings: typeof editForm.kb_bindings) =>
        bindings.map(b => ({
          kb_id: b.kb_id,
          ...(b.scope === 'categories' && b.categories.length ? { categories: b.categories } : {}),
          ...(b.scope === 'doc_ids' && b.doc_ids.length ? { doc_ids: b.doc_ids } : {}),
        }))
      const origKbJson = JSON.stringify((orig as any).kb_bindings || [])
      const newKbSerialized = serializeKb(editForm.kb_bindings)
      const newKbJson = JSON.stringify(newKbSerialized)
      if (origKbJson !== newKbJson) {
        (changes as any).kb_bindings = newKbSerialized.length > 0 ? newKbSerialized : null
      }

      if (Object.keys(changes).length === 0) {
        message.info('没有修改')
        saving.value = false
        return
      }

      const res = await updateSkill(orig.name, changes)
      message.success('保存成功')
      if (res.warnings && res.warnings.length > 0) {
        res.warnings.forEach(w => message.warning(w, { duration: 8000 }))
      }
    }

    drawerVisible.value = false
    showAiHint.value = false
    createSource.value = 'admin'
    // 重置 AI 对话面板状态
    aiCreatorRef.value?.resetState()
    await loadData()
  } catch (error: any) {
    message.error(error.message || '保存失败')
  } finally {
    saving.value = false
  }
}

// 删除
async function handleDelete(skill: Skill) {
  try {
    await deleteSkill(skill.name)
    message.success(`${skill.title} 已删除`)
    await loadData()
  } catch (error: any) {
    message.error(error.message || '删除失败')
  }
}

function getCategoryIcon(category: string) {
  return categoryIcons[category] || AppsOutline
}

// ==================== 生命周期 ====================

onMounted(() => {
  loadData()
  loadBuiltinSkillList()
})

async function loadBuiltinSkillList() {
  try {
    const res = await getBuiltinSkills()
    builtinSkills.value = res.builtin_skills || []
  } catch (e) {
    console.warn('加载内置技能列表失败', e)
  }
}
</script>

<template>
  <div class="skill-management">
    <!-- 统计卡片 -->
    <n-grid :cols="4" :x-gap="16" :y-gap="16" class="stats-grid">
      <n-grid-item>
        <n-card size="small">
          <n-statistic label="总数" :value="stats?.total || 0" />
        </n-card>
      </n-grid-item>
      <n-grid-item>
        <n-card size="small">
          <n-statistic label="启用" :value="stats?.enabled || 0">
            <template #prefix>
              <n-icon :component="CheckmarkCircleOutline" color="#18a058" />
            </template>
          </n-statistic>
        </n-card>
      </n-grid-item>
      <n-grid-item>
        <n-card size="small">
          <n-statistic label="禁用" :value="stats?.disabled || 0">
            <template #prefix>
              <n-icon :component="CloseCircleOutline" color="#d03050" />
            </template>
          </n-statistic>
        </n-card>
      </n-grid-item>
      <n-grid-item>
        <n-card size="small">
          <n-statistic label="分类" :value="stats?.categories || 0" />
        </n-card>
      </n-grid-item>
    </n-grid>

    <!-- 分类标签 -->
    <div class="category-tags">
      <n-tag
        v-for="opt in categoryOptions"
        :key="opt.value"
        :type="filters.category === opt.value ? 'primary' : 'default'"
        :bordered="filters.category !== opt.value"
        checkable
        :checked="filters.category === opt.value"
        @update:checked="filters.category = opt.value"
        style="cursor: pointer"
      >
        {{ opt.label }}
        <template v-if="opt.value" #avatar>
          <n-icon :component="getCategoryIcon(opt.value)" size="14" />
        </template>
      </n-tag>
    </div>

    <!-- 操作栏 -->
    <div class="action-bar">
      <n-space>
        <n-input
          v-model:value="filters.search"
          placeholder="搜索 Skill..."
          clearable
          style="width: 200px"
        >
          <template #prefix>
            <n-icon :component="SearchOutline" />
          </template>
        </n-input>
        <n-select
          v-model:value="filters.executionMode"
          :options="[
            { label: '全部模式', value: '' },
            { label: '普通模式', value: 'normal' },
            { label: '计划模式', value: 'plan' },
            { label: '对话模式', value: 'conversational' },
            { label: '课堂模式', value: 'classroom' },
          ]"
          style="width: 130px"
          size="medium"
        />
        <n-select
          v-model:value="filters.modelTier"
          :options="[
            { label: '全部等级', value: '' },
            ...modelTierOptions.map(o => ({ label: o.label, value: o.value }))
          ]"
          style="width: 150px"
          size="medium"
        />
        <n-select
          v-model:value="filters.enabled"
          :options="[
            { label: '全部状态', value: '' },
            { label: '已启用', value: 'true' },
            { label: '已禁用', value: 'false' },
          ]"
          style="width: 120px"
          size="medium"
        />
        <n-select
          v-model:value="filters.outputFormat"
          :options="[
            { label: '全部格式', value: '' },
            { label: '💬 普通输出', value: '_null' },
            { label: '📄 Artifact', value: 'artifact' },
            { label: '🎬 HTML动画', value: 'html-animation' },
            { label: '📊 HTML演示', value: 'html-slides' },
            { label: '🎮 HTML交互', value: 'html-interactive' },
          ]"
          style="width: 140px"
          size="medium"
        />
        <n-button
          :type="filterPendingReview ? 'warning' : 'default'"
          secondary
          @click="filterPendingReview = !filterPendingReview"
        >
          待审核
          <template v-if="pendingReviewCount > 0" #icon>
            <n-badge :value="pendingReviewCount" :max="99" />
          </template>
        </n-button>
      </n-space>
      <n-space>
        <n-button @click="handleCreate">
          <template #icon><n-icon :component="AddOutline" /></template>
          手动创建
        </n-button>
        <n-button type="primary" @click="handleAiCreate">
          <template #icon><n-icon :component="SparklesOutline" /></template>
          AI 辅助创建
        </n-button>
      </n-space>
    </div>

    <!-- Skill 卡片列表 -->
    <n-spin :show="loading">
      <n-grid v-if="filteredSkills.length > 0" :cols="3" :x-gap="16" :y-gap="16" responsive="screen" :item-responsive="true">
        <n-grid-item v-for="skill in filteredSkills" :key="skill.name" span="3 m:1 l:1">
          <n-card size="small" hoverable class="skill-card">
            <!-- 卡片头部 -->
            <template #header>
              <div class="card-header">
                <div class="card-title">
                  <n-icon :component="getCategoryIcon(skill.category)" size="18" />
                  <span>{{ skill.title }}</span>
                  <n-tag v-if="skill.is_system" size="tiny" type="info" :bordered="false">系统</n-tag>
                  <n-tag v-if="!skill.enabled && !skill.is_system" size="tiny" type="warning" :bordered="false">待审核</n-tag>
                </div>
                <n-switch
                  :value="skill.enabled"
                  @update:value="() => handleToggleEnabled(skill)"
                  size="small"
                />
              </div>
            </template>

            <!-- 卡片内容 -->
            <div class="card-body">
              <p class="skill-desc">{{ skill.description || '暂无描述' }}</p>

              <div class="skill-tags">
                <n-tag size="tiny" :color="{ color: getCategoryColor(skill.category) + '20', textColor: getCategoryColor(skill.category) }">
                  {{ getCategoryName(skill.category) }}
                </n-tag>
                <n-tag size="tiny" type="default">v{{ skill.version }}</n-tag>
                <n-tag size="tiny" :type="skill.model_tier === 'high' ? 'warning' : skill.model_tier === 'low' ? 'default' : 'info'">
                  {{ dynamicModelTierMap[skill.model_tier || 'mid'] || skill.model_tier }}
                </n-tag>
                <n-tag v-if="skill.execution_mode && skill.execution_mode !== 'normal'" size="tiny" type="success">
                  {{ EXECUTION_MODE_MAP[skill.execution_mode] || skill.execution_mode }}
                </n-tag>
                <n-tag v-if="skill.output_format" size="tiny" :type="skill.output_format === 'artifact' ? 'warning' : 'default'">
                  {{ OUTPUT_FORMAT_TAG[skill.output_format] || skill.output_format }}
                </n-tag>
                <n-tooltip v-if="skill.unavailable_capabilities && skill.unavailable_capabilities.length > 0" trigger="hover">
                  <template #trigger>
                    <n-tag size="tiny" type="error" :bordered="false">
                      ⚠ 降级 ({{ skill.unavailable_capabilities.length }})
                    </n-tag>
                  </template>
                  缺失能力：{{ skill.unavailable_capabilities.join('、') }}
                </n-tooltip>
                <n-tooltip v-if="skill.resolved_source" trigger="hover">
                  <template #trigger>
                    <n-tag size="tiny" :type="RESOLVED_SOURCE_TYPE[skill.resolved_source] || 'default'" :bordered="false">
                      {{ RESOLVED_SOURCE_LABEL[skill.resolved_source] || skill.resolved_source }}
                    </n-tag>
                  </template>
                  当前生效的工具解析路径
                </n-tooltip>
              </div>

              <div class="skill-meta">
                <span v-if="effectiveTools(skill).length > 0" class="meta-item">
                  工具: {{ effectiveTools(skill).slice(0, 2).map(t => TOOL_DISPLAY_NAMES[t] || t).join(', ') }}
                  <template v-if="effectiveTools(skill).length > 2"> +{{ effectiveTools(skill).length - 2 }}</template>
                  <n-tag v-if="skill.tool_tier" size="tiny" :bordered="false" type="info">
                    {{ skill.tool_tier }}层
                  </n-tag>
                </span>
                <span v-if="skill.sub_skills && skill.sub_skills.length > 0" class="meta-item">
                  子Skill: {{ skill.sub_skills.slice(0, 2).join(', ') }}
                  <template v-if="skill.sub_skills.length > 2"> +{{ skill.sub_skills.length - 2 }}</template>
                </span>
                <span v-if="skill.trigger_keywords && skill.trigger_keywords.length > 0" class="meta-item">
                  触发词: {{ skill.trigger_keywords.slice(0, 2).join(', ') }}
                  <template v-if="skill.trigger_keywords.length > 2"> +{{ skill.trigger_keywords.length - 2 }}</template>
                </span>
              </div>
            </div>

            <!-- 卡片操作 -->
            <template #action>
              <n-space size="small">
                <n-button size="tiny" quaternary type="primary" @click="handleEdit(skill)">
                  <template #icon><n-icon :component="CreateOutline" /></template>
                  编辑
                </n-button>
                <n-popconfirm
                  v-if="!skill.is_system"
                  @positive-click="handleDelete(skill)"
                >
                  <template #trigger>
                    <n-button size="tiny" quaternary type="error">
                      <template #icon><n-icon :component="TrashOutline" /></template>
                      删除
                    </n-button>
                  </template>
                  确定删除 {{ skill.title }}？此操作不可恢复。
                </n-popconfirm>
              </n-space>
            </template>
          </n-card>
        </n-grid-item>
      </n-grid>
      <n-empty v-else description="没有找到匹配的 Skill" style="margin-top: 60px" />
    </n-spin>

    <!-- AI 辅助创建抽屉 -->
    <n-drawer v-model:show="aiDrawerVisible" :width="900" placement="right">
      <n-drawer-content title="AI 辅助创建 Skill" closable @close="aiCreatorRef?.handleClose()">
        <SkillAiCreatorPanel
          ref="aiCreatorRef"
          :visible="aiDrawerVisible"
          @config-generated="handleAiConfigGenerated"
          @close="aiDrawerVisible = false"
        />
      </n-drawer-content>
    </n-drawer>

    <!-- 编辑/创建抽屉 -->
    <n-drawer v-model:show="drawerVisible" :width="860" placement="right">
      <n-drawer-content :title="drawerTitle" closable>
        <n-spin :show="drawerLoading">
          <!-- AI 填充提示条 -->
          <n-alert
            v-if="showAiHint && drawerMode === 'create'"
            type="info"
            :bordered="false"
            style="margin-bottom: 16px"
            closable
            @close="showAiHint = false"
          >
            以下字段已由 AI 自动填充，请逐 Tab 审核后保存。创建后默认禁用，需手动启用。
            <n-button text type="primary" size="small" @click="handleBackToAi" style="margin-left: 8px">
              返回 AI 对话
            </n-button>
          </n-alert>

          <n-tabs v-model:value="activeTab" type="line" animated>
            <!-- Tab 1: 基本信息 -->
            <n-tab-pane name="basic" tab="基本信息">
              <n-form label-placement="left" label-width="100" :style="{ maxWidth: '560px' }">
                <n-form-item label="名称 (name)">
                  <n-input
                    v-model:value="editForm.name"
                    :disabled="drawerMode === 'edit'"
                    placeholder="小写字母、数字、连字符"
                  />
                </n-form-item>
                <n-form-item label="显示名称">
                  <n-input v-model:value="editForm.display_name" placeholder="中文显示名称" />
                </n-form-item>
                <n-form-item label="描述">
                  <n-input
                    v-model:value="editForm.description"
                    type="textarea"
                    :rows="3"
                    placeholder="Skill 功能描述"
                  />
                </n-form-item>
                <n-form-item label="分类">
                  <n-select
                    v-model:value="editForm.category"
                    :options="categories.map(c => ({ label: getCategoryName(c), value: c }))"
                    placeholder="选择分类"
                    clearable
                    tag
                  />
                </n-form-item>
                <n-form-item label="执行模式">
                  <div>
                    <n-radio-group v-model:value="editForm.execution_mode">
                      <n-space vertical :size="8">
                        <n-radio
                          v-for="opt in executionModeOptions"
                          :key="opt.value"
                          :value="opt.value"
                          style="display: flex; align-items: flex-start"
                        >
                          <span>{{ opt.label }}</span>
                          <span style="color: #999; font-size: 12px; margin-left: 6px">{{ opt.desc }}</span>
                        </n-radio>
                      </n-space>
                    </n-radio-group>
                  </div>
                </n-form-item>
                <n-form-item label="输出格式">
                  <div style="width: 100%">
                    <n-select
                      v-model:value="editForm.output_format"
                      :options="outputFormatOptions"
                      placeholder="选择输出格式"
                      style="width: 360px"
                      :render-label="renderOutputFormatLabel"
                    />
                    <div style="margin-top: 6px; color: #999; font-size: 12px; line-height: 1.5">
                      {{ outputFormatHint }}
                    </div>
                  </div>
                </n-form-item>
                <n-form-item label="模型选择">
                  <div style="width: 100%">
                    <n-space vertical :size="12">
                      <!-- Radio 切换 -->
                      <n-space>
                        <n-button
                          :type="editForm.model_select_mode === 'tier' ? 'primary' : 'default'"
                          size="small"
                          @click="editForm.model_select_mode = 'tier'; editForm.model_id = ''"
                        >
                          按等级（推荐）
                        </n-button>
                        <n-button
                          :type="editForm.model_select_mode === 'specific' ? 'primary' : 'default'"
                          size="small"
                          @click="editForm.model_select_mode = 'specific'"
                        >
                          指定模型
                        </n-button>
                      </n-space>

                      <!-- 按等级模式 -->
                      <div v-if="editForm.model_select_mode === 'tier'" style="padding: 12px; background: #f8f8fa; border-radius: 6px;">
                        <n-select
                          v-model:value="editForm.model_tier"
                          :options="modelTierOptions"
                          style="margin-bottom: 8px"
                        />
                        <div style="font-size: 12px; color: #666;">
                          <template v-if="modelTierConfigs[editForm.model_tier]">
                            当前映射：<strong>{{ modelTierConfigs[editForm.model_tier].model }}</strong>
                          </template>
                          <template v-else>
                            各等级对应的具体模型可在「系统设置 → AI模型」中配置
                          </template>
                        </div>
                      </div>

                      <!-- 指定模型模式 -->
                      <div v-else style="padding: 12px; background: #f8f8fa; border-radius: 6px;">
                        <n-select
                          v-model:value="editForm.model_id"
                          :options="modelOptions.filter(o => o.value !== '')"
                          placeholder="选择模型"
                          filterable
                          style="margin-bottom: 8px"
                        />
                        <div style="font-size: 12px; color: #666; margin-top: 8px;">
                          <span style="margin-right: 8px;">降级等级：</span>
                          <n-select
                            v-model:value="editForm.model_tier"
                            :options="modelTierOptions"
                            size="small"
                            style="width: 100%; margin-top: 4px;"
                          />
                          <span style="color: #999; margin-top: 2px; display: block;">首选模型不可用时回退到此等级</span>
                        </div>
                      </div>
                    </n-space>
                  </div>
                </n-form-item>
                <n-form-item label="排序权重">
                  <n-input-number v-model:value="editForm.sort_order" :min="0" :max="999" />
                </n-form-item>

                <!-- 只读信息 -->
                <template v-if="drawerMode === 'edit' && originalSkill">
                  <div class="readonly-info">
                    <span>版本: {{ originalSkill.version }}</span>
                    <span>类型: {{ originalSkill.is_system ? '系统内置' : '用户创建' }}</span>
                    <span>会话隔离: {{ CONTEXT_MODE_MAP[originalSkill.context] || originalSkill.context }}
                      <span v-if="originalSkill.execution_mode === 'conversational'" style="color: #999; font-size: 11px">（对话模式自动设为保持会话）</span>
                    </span>
                  </div>
                </template>
              </n-form>
            </n-tab-pane>

            <!-- Tab 2: 系统提示词 -->
            <n-tab-pane name="prompt" tab="系统提示词">
              <n-alert v-if="editForm.execution_mode === 'plan'" type="info" style="margin-bottom: 12px" :bordered="false">
                当前为计划模式。请在此编写通用身份和领域知识（角色定义、领域规则）。
                建议用 <strong>## 二级标题</strong> 划分知识段落（如 <code>## 验证规则</code>、<code>## 输出格式</code>），
                在「执行阶段」Tab 中可按段落精选，减少不必要的 Token 消耗。
                <strong>不要在此编写执行步骤</strong>，每个阶段的具体任务请在「执行阶段」Tab 中配置。
              </n-alert>
              <n-alert
                v-if="editForm.execution_mode === 'plan' && editForm.system_prompt && !editForm.system_prompt.includes('\n## ')"
                type="warning"
                style="margin-bottom: 12px"
                :bordered="false"
              >
                当前提示词没有使用 <code>## 二级标题</code> 分段，无法在「执行阶段」中按段落精选内容。
                建议用 <code>## 标题</code> 将提示词分为多个知识模块。
              </n-alert>
              <n-input
                v-model:value="editForm.system_prompt"
                type="textarea"
                placeholder="输入系统提示词 (Markdown 格式)..."
                style="font-family: 'SF Mono', 'Fira Code', monospace; font-size: 13px"
                :class="editForm.execution_mode === 'plan' ? 'prompt-textarea-with-alert' : 'prompt-textarea'"
              />
            </n-tab-pane>

            <!-- Tab 3: 工具与子代理 -->
            <n-tab-pane name="tools" tab="工具与子代理">
              <!-- 工具配置模式 -->
              <div class="section-title">工具配置方式</div>
              <n-alert :bordered="false" type="info" style="margin-bottom: 12px; font-size: 13px">
                <template v-if="!editForm.tool_tier">
                  <b>手动模式</b>：从左侧列表逐个选择该 Skill 可以使用的工具。适合需要精确控制的场景。
                </template>
                <template v-else>
                  <b>分层模式</b>：选择一个预设工具包，系统自动分配该层级包含的所有工具。可额外添加或排除个别工具。
                </template>
              </n-alert>
              <n-radio-group
                :value="editForm.tool_tier ? 'tier' : 'manual'"
                @update:value="handleToolModeSwitch"
                style="margin-bottom: 16px"
              >
                <n-radio-button value="manual">手动选择</n-radio-button>
                <n-radio-button value="tier">分层预设</n-radio-button>
              </n-radio-group>

              <!-- 手动模式：穿梭框 -->
              <template v-if="!editForm.tool_tier">
                <n-transfer
                  v-model:value="editForm.allowed_tools"
                  :options="toolTransferOptions"
                  source-title="可选工具"
                  target-title="已选工具"
                  source-filterable
                  target-filterable
                  style="height: 280px"
                />
              </template>

              <!-- 分层模式 -->
              <template v-else>
                <div class="section-title">选择工具层级</div>
                <n-radio-group
                  :value="editForm.tool_tier"
                  @update:value="handleTierChange"
                  style="margin-bottom: 8px"
                >
                  <n-radio-button value="common">基础</n-radio-button>
                  <n-radio-button value="standard">标准</n-radio-button>
                  <n-radio-button value="extended">扩展</n-radio-button>
                </n-radio-group>

                <!-- 层级包含的工具预览 -->
                <div style="background: #f9f9f9; border-radius: 6px; padding: 10px 12px; margin-bottom: 16px">
                  <div style="font-size: 12px; color: #666; margin-bottom: 6px">该层级已包含的工具：</div>
                  <n-space :size="[4, 6]">
                    <n-tag v-for="t in tierIncludedTools" :key="t" size="small" :bordered="false" type="info">
                      {{ TOOL_DISPLAY_NAMES[t] || t }}
                    </n-tag>
                  </n-space>
                </div>

                <div class="section-title">额外添加 <span style="color: #999; font-weight: normal; font-size: 12px">（追加上面没有的工具）</span></div>
                <n-select
                  v-model:value="editForm.extra_tools"
                  :options="extraToolOptions"
                  multiple
                  filterable
                  clearable
                  placeholder="不需要可留空"
                  :render-label="renderToolOption"
                  :render-tag="renderToolTag"
                  :filter="filterToolOption"
                  max-tag-count="responsive"
                  style="margin-bottom: 16px"
                />

                <div class="section-title">排除 <span style="color: #999; font-weight: normal; font-size: 12px">（从层级中移除不需要的）</span></div>
                <n-select
                  v-model:value="editForm.denied_tools"
                  :options="deniedToolOptions"
                  multiple
                  filterable
                  clearable
                  placeholder="不需要可留空"
                  :render-label="renderToolOption"
                  :render-tag="renderToolTag"
                  :filter="filterToolOption"
                  max-tag-count="responsive"
                  style="margin-bottom: 16px"
                />
              </template>

              <!-- 平台 Override(在 DB 层覆盖 skill.md 默认行为) -->
              <!-- 平台未注册能力诊断:管理员可据此调整上方工具配置 -->
              <n-alert
                v-if="originalSkill && originalSkill.unavailable_capabilities && originalSkill.unavailable_capabilities.length > 0"
                :bordered="false"
                type="warning"
                style="margin-bottom: 12px; font-size: 13px"
              >
                <template #header>⚠️ 当前平台缺失能力</template>
                以下 capability 未在本平台注册（或被 platform_gate 关闭），Skill 将以**降级模式**运行：
                <n-space :size="4" style="margin-top: 6px">
                  <n-tag v-for="c in originalSkill.unavailable_capabilities" :key="c" size="small" type="error" :bordered="false">
                    {{ c }}
                  </n-tag>
                </n-space>
              </n-alert>

              <!-- 沙箱隔离配置 -->
              <n-form-item label="沙箱隔离" label-placement="left" style="margin-top: 24px">
                <n-space vertical>
                  <n-space align="center">
                    <n-switch v-model:value="editForm.sandbox_enabled" />
                    <span style="font-size: 13px; color: #666">启用后，该 Skill 将在隔离容器中执行代码</span>
                  </n-space>
                  <template v-if="editForm.sandbox_enabled">
                    <n-space vertical style="margin-top: 8px; padding-left: 12px; border-left: 2px solid #e8e8e8">
                      <n-form-item label="执行超时 (秒)" label-placement="left">
                        <n-input-number v-model:value="editForm.sandbox_timeout" :min="30" :max="600" :step="30" style="width: 150px" />
                      </n-form-item>
                      <n-form-item label="内存限制 (MB)" label-placement="left">
                        <n-input-number v-model:value="editForm.sandbox_memory_mb" :min="128" :max="2048" :step="128" style="width: 150px" />
                      </n-form-item>
                      <div style="font-size: 12px; color: #999">
                        沙箱工具将自动注入：
                        <n-space :size="4" style="margin-top: 4px">
                          <n-tag v-for="t in SANDBOX_TOOLS" :key="t" size="tiny" :bordered="false">{{ TOOL_DISPLAY_NAMES[t] || t }}</n-tag>
                        </n-space>
                      </div>
                    </n-space>
                  </template>
                </n-space>
              </n-form-item>

              <!-- 上下文压缩策略 -->
              <n-divider style="margin: 16px 0 8px">上下文压缩策略</n-divider>
              <n-form-item label="策略模板">
                <n-radio-group v-model:value="editForm.compression_preset" @update:value="applyCompressionPreset">
                  <n-space>
                    <n-radio value="research">研究类（高保留）</n-radio>
                    <n-radio value="generate">生成类（中保留）</n-radio>
                    <n-radio value="default">通用（标准）</n-radio>
                    <n-radio value="custom">自定义</n-radio>
                  </n-space>
                </n-radio-group>
              </n-form-item>
              <!-- 自定义模式展开 -->
              <template v-if="editForm.compression_preset === 'custom'">
                <n-form-item label="压缩触发阈值（消息条数）">
                  <n-space align="center">
                    <n-input-number
                      v-model:value="editForm.compression_policy.compress_threshold"
                      :min="10" :max="100" :step="5" style="width: 120px"
                    />
                    <n-tooltip trigger="hover">
                      <template #trigger><span style="cursor: help; color: #999">ⓘ</span></template>
                      对话消息超过此数量才触发压缩。研究类建议 40-50，通用建议 20-30
                    </n-tooltip>
                  </n-space>
                </n-form-item>
                <n-form-item label="保留工具结果" label-placement="left">
                  <n-space align="center">
                    <n-switch v-model:value="editForm.compression_policy.preserve_tool_results" />
                    <n-tooltip trigger="hover">
                      <template #trigger><span style="cursor: help; color: #999">ⓘ</span></template>
                      开启后压缩时保留所有工具调用结果，适用于需要完整检索历史的研究类 Skill
                    </n-tooltip>
                  </n-space>
                </n-form-item>
                <n-form-item label="保留内容类型">
                  <n-dynamic-tags v-model:value="editForm.compression_policy.preserve_categories" />
                </n-form-item>
              </template>
              <!-- 非自定义模式：只读预览 -->
              <div v-else style="padding: 4px 0 8px; color: #666; font-size: 13px">
                阈值 {{ editForm.compression_policy?.compress_threshold || 25 }} 条 ·
                {{ editForm.compression_policy?.preserve_tool_results ? '保留工具结果' : '不保留工具结果' }}
                <template v-if="editForm.compression_policy?.preserve_categories?.length">
                  · 保留: {{ editForm.compression_policy.preserve_categories.join(', ') }}
                </template>
              </div>
              <n-form-item label="结构化状态跟踪" label-placement="left">
                <n-space align="center">
                  <n-switch v-model:value="editForm.state_tracking" />
                  <n-tooltip trigger="hover">
                    <template #trigger><span style="cursor: help; color: #999">ⓘ</span></template>
                    开启后，对话界面可实时显示任务进度（如"当前阶段：大纲确认 3/8"），适用于有明确阶段性工作流的 Skill
                  </n-tooltip>
                </n-space>
              </n-form-item>

              <!-- 触发关键词 -->
              <div class="section-title" style="margin-top: 24px">触发关键词</div>
              <div class="section-desc">用户输入包含这些关键词时，意图识别会优先匹配到此 Skill</div>
              <n-dynamic-tags v-model:value="editForm.trigger_keywords" />

              <!-- 可委派 Skill -->
              <div class="section-title" style="margin-top: 24px">可委派 Skill</div>
              <div class="section-desc">此 Skill 执行中可自动委派的子 Skill（如 deep-research 委派 web-search 搜索子任务）</div>
              <n-select
                v-model:value="editForm.sub_skills"
                :options="subSkillOptions"
                multiple
                filterable
                placeholder="搜索 Skill 名称或描述..."
                clearable
                :render-label="renderSkillOption"
                :render-tag="renderSkillTag"
                :filter="filterSkillOption"
                max-tag-count="responsive"
              />

              <!-- 委派执行参数 -->
              <template v-if="editForm.sub_skills.length > 0">
                <div class="section-title" style="margin-top: 20px">委派执行参数</div>
                <div class="section-desc">控制子 Skill 委派执行的资源限制</div>
                <n-form label-placement="left" label-width="120" :style="{ maxWidth: '500px' }">
                  <n-form-item label="最大嵌套深度">
                    <n-input-number
                      v-model:value="editForm.sub_agent_config.max_depth"
                      :min="1" :max="3"
                    />
                    <span class="field-hint">Skill 可递归委派的层数，1=只能委派一层</span>
                  </n-form-item>
                  <n-form-item label="超时时间(秒)">
                    <n-input-number
                      v-model:value="editForm.sub_agent_config.timeout"
                      :min="60" :max="600" :step="30"
                    />
                    <span class="field-hint">单个子 Skill 执行的最长时间</span>
                  </n-form-item>
                  <n-form-item label="最大并发数">
                    <n-input-number
                      v-model:value="editForm.sub_agent_config.max_concurrent"
                      :min="1" :max="10"
                    />
                    <span class="field-hint">同时运行的子 Skill 数量上限</span>
                  </n-form-item>
                  <n-form-item label="允许后台运行">
                    <n-switch v-model:value="editForm.sub_agent_config.allow_background" />
                    <span class="field-hint">开启后子 Skill 可在后台异步执行，不阻塞主流程</span>
                  </n-form-item>
                </n-form>
              </template>

              <!-- Aggregation@N 聚合增强配置 -->
              <div class="section-title" style="margin-top: 20px">聚合增强（Aggregation@N）</div>
              <div class="section-desc">对 research 阶段并发执行 N 路独立搜索，再用 LLM 合成为更全面的数据。增加约 2x Token 消耗和 20-40s 延迟，但可提升信息覆盖度和来源多样性</div>
              <n-form label-placement="left" label-width="120" :style="{ maxWidth: '500px' }">
                <n-form-item label="聚合路数">
                  <n-input-number
                    v-model:value="editForm.aggregation_n"
                    :min="1" :max="5"
                  />
                  <span class="field-hint">1=关闭，2~5=启用 N 路并发搜索聚合</span>
                </n-form-item>
              </n-form>

              <!-- 追加到系统提示词的内容预览 -->
              <n-collapse
                v-if="previewTools.length > 0 || editForm.sub_skills.length > 0"
                style="margin-top: 24px"
              >
                <n-collapse-item name="preview">
                  <template #header>
                    <n-space align="center" :size="8">
                      <n-icon :component="EyeOutline" />
                      <span>追加到系统提示词的内容预览</span>
                      <n-tag size="tiny" v-if="previewTools.length > 0">
                        {{ previewTools.length }} 个工具
                      </n-tag>
                      <n-tag size="tiny" v-if="editForm.sub_skills.length > 0">
                        {{ editForm.sub_skills.length }} 个委派 Skill
                      </n-tag>
                    </n-space>
                  </template>
                  <n-input
                    :value="promptPreview"
                    type="textarea"
                    readonly
                    :autosize="{ minRows: 4, maxRows: 12 }"
                    style="font-family: monospace; font-size: 12px; background: #f5f5f5"
                  />
                  <div style="margin-top: 8px; color: #999; font-size: 12px">
                    上述内容将自动追加到 Skill 的系统提示词末尾
                  </div>
                </n-collapse-item>
              </n-collapse>
            </n-tab-pane>

            <!-- Tab: 执行阶段 -->
            <n-tab-pane name="plan-stages" :disabled="editForm.execution_mode !== 'plan'">
              <template #tab>
                <n-badge :value="editForm.plan_stages.length" :show="editForm.plan_stages.length > 0" :offset="[10, -3]">
                  执行阶段
                </n-badge>
              </template>
              <PlanStagesEditor
                v-model="editForm.plan_stages"
                :available-tools="allTools"
                :execution-mode="editForm.execution_mode"
                :system-prompt="editForm.system_prompt"
                :prompt-sections-meta="(originalSkill as any)?.prompt_sections_meta"
                ref="planStagesEditorRef"
              />
            </n-tab-pane>

            <!-- 质量验证 (Phase 2: LLM-as-Judge) -->
            <!-- Tab: 知识库绑定 -->
            <n-tab-pane name="kb-binding" tab="知识库绑定">
              <div :style="{ maxWidth: '600px' }">

                <n-alert type="info" style="margin-bottom: 16px" :bordered="false">
                  绑定公共知识库后，该 Skill 调用 kb_query 时公共库部分将只检索绑定范围内的内容（不影响个人知识库和聊天附件）。不绑定则使用系统默认公共知识库。
                </n-alert>

                <!-- 已绑定知识库列表 -->
                <div v-for="(binding, idx) in editForm.kb_bindings" :key="idx"
                  style="margin-bottom: 12px; padding: 12px 16px; border: 1px solid #e8e8e8; border-radius: 8px; background: #fafafa;"
                >
                  <div style="display: flex; align-items: center; margin-bottom: 8px;">
                    <n-tag type="info" size="small" style="margin-right: 8px;">📚</n-tag>
                    <span style="flex: 1; font-weight: 500;">
                      {{ knowledgeBases.find(kb => kb.kb_id === binding.kb_id)?.name || '未知知识库' }}
                    </span>
                    <span style="font-size: 11px; color: #999; margin-right: 8px;">{{ binding.kb_id.substring(0, 12) }}...</span>
                    <n-button size="tiny" quaternary type="error" @click="editForm.kb_bindings.splice(idx, 1)">
                      <template #icon><n-icon :component="TrashOutline" /></template>
                    </n-button>
                  </div>

                  <!-- 文件范围选择 -->
                  <div style="padding-left: 32px; font-size: 13px;">
                    <div style="font-size: 12px; color: #666; margin-bottom: 8px;">文件范围：</div>

                    <label style="display: flex; align-items: center; cursor: pointer; margin-bottom: 8px;">
                      <input type="radio" :name="'scope_' + idx" value="all" v-model="binding.scope"
                        @change="binding.categories = []; binding.doc_ids = []"
                        style="margin-right: 6px;" />
                      全部文件
                    </label>

                    <div style="margin-bottom: 8px;">
                      <label style="display: flex; align-items: center; cursor: pointer;">
                        <input type="radio" :name="'scope_' + idx" value="categories" v-model="binding.scope"
                          @change="loadKbDocs(binding.kb_id)"
                          style="margin-right: 6px;" />
                        按分类筛选：
                      </label>
                      <div v-if="binding.scope === 'categories'" style="margin: 6px 0 0 20px;">
                        <n-spin :show="!kbDocsCache[binding.kb_id]" size="small">
                          <n-checkbox-group v-model:value="binding.categories">
                            <n-space :size="[16, 4]">
                              <n-checkbox
                                v-for="cat in getKbCategories(binding.kb_id)"
                                :key="cat" :value="cat" :label="cat"
                              />
                            </n-space>
                          </n-checkbox-group>
                          <div v-if="getKbCategories(binding.kb_id).length === 0" style="color: #999; font-size: 12px;">
                            该知识库暂无分类数据
                          </div>
                        </n-spin>
                      </div>
                    </div>

                    <div>
                      <label style="display: flex; align-items: center; cursor: pointer;">
                        <input type="radio" :name="'scope_' + idx" value="doc_ids" v-model="binding.scope"
                          @change="loadKbDocs(binding.kb_id)"
                          style="margin-right: 6px;" />
                        指定文件：
                      </label>
                      <div v-if="binding.scope === 'doc_ids'" style="margin: 6px 0 0 20px;">
                        <n-spin :show="!kbDocsCache[binding.kb_id]" size="small">
                          <n-transfer
                            v-model:value="binding.doc_ids"
                            :options="getKbDocOptions(binding.kb_id)"
                            source-filterable
                            source-filter-placeholder="搜索文件..."
                            style="height: 260px;"
                          />
                          <div v-if="getKbDocOptions(binding.kb_id).length === 0" style="color: #999; font-size: 12px;">
                            该知识库暂无文件
                          </div>
                        </n-spin>
                      </div>
                    </div>
                  </div>
                </div>

                <!-- 添加知识库 -->
                <n-select
                  :value="null"
                  :options="kbOptions.filter(o => !editForm.kb_bindings.some(b => b.kb_id === o.value))"
                  placeholder="+ 添加知识库"
                  filterable
                  :render-label="(option: any) => h('div', { style: 'padding: 3px 0' }, [
                    h('div', { style: 'font-size: 13px; font-weight: 500' }, option.label as string),
                    h('code', { style: 'font-size: 11px; color: #aaa;' }, option.kbId?.substring(0, 12) + '...'),
                  ])"
                  @update:value="(v: string) => { editForm.kb_bindings.push({ kb_id: v, scope: 'all', categories: [], doc_ids: [] }) }"
                  style="margin-top: 8px"
                />

                <!-- 个人知识库开关 -->
                <div style="margin-top: 20px; padding: 12px 16px; border: 1px solid #e8e8e8; border-radius: 8px;">
                  <n-space align="center">
                    <n-switch v-model:value="editForm.include_personal_kb" />
                    <span>同时检索用户个人知识库（如果用户已创建）</span>
                  </n-space>
                </div>

              </div>
            </n-tab-pane>

            <n-tab-pane name="verification" tab="质量验证">
              <n-form label-placement="left" label-width="120">
                <!-- 功能说明 -->
                <n-alert type="info" style="margin-bottom: 16px">
                  <template #header>什么是 AI 质量验证？</template>
                  <p style="margin: 0 0 8px 0">
                    Skill 执行完成后，系统会自动对输出内容进行质量评估。评估分两部分：
                    <strong>规则检查</strong>（检查格式、长度等硬性指标，不通过则标记失败）和
                    <strong>AI 多维评分</strong>（由 LLM 对内容的多个维度打分，作为质量参考）。
                    评估结果会以卡片形式展示在用户对话界面中，不会阻断输出。
                  </p>
                  <p style="margin: 0 0 8px 0">
                    <strong>Token 成本：</strong>启用 AI 评分后，每次 Skill 执行完成会额外调用一次 LLM 进行打分（约消耗数百 token）。
                    建议仅对高价值 Skill（如深度研究、政策分析）启用。
                  </p>
                  <p style="margin: 0">
                    <strong>推荐上线路径：</strong>先在全局设置中将 LLM Judge 设为 SHADOW（影子模式，仅记录评分不展示告警），
                    观察 1~2 周评分稳定后再切换为 ENFORCE（执行模式，低分展示告警）。
                  </p>
                </n-alert>

                <!-- 全局开关未启用提示 -->
                <n-alert v-if="!globalJudgeEnabled" type="warning" style="margin-bottom: 16px">
                  全局质量验证功能未启用（系统设置 → LLM Judge）。当前配置将在全局启用后自动生效。
                </n-alert>

                <n-form-item label="启用 AI 质检">
                  <n-space vertical>
                    <n-switch
                      :value="editForm.verification_config !== null"
                      @update:value="(v: boolean) => {
                      if (v) {
                        editForm.verification_config = {
                          strategy: 'rules_and_llm',
                          dimensions: [],
                          min_score: 0.6,
                        }
                      } else {
                        editForm.verification_config = null
                      }
                    }"
                  />
                    <span style="font-size: 12px; color: #999">
                      此为本 Skill 的独立开关。需同时在「系统设置 → LLM Judge」中开启全局开关才会生效。
                    </span>
                  </n-space>
                </n-form-item>

                <div :style="{ opacity: editForm.verification_config ? 1 : 0.4, pointerEvents: editForm.verification_config ? 'auto' : 'none' }">
                  <n-form-item label="验证策略">
                    <span style="display: block; font-size: 12px; color: #999; margin-bottom: 6px">
                      「仅规则检查」只做格式/长度等硬性校验；「规则 + AI 评分」额外由 LLM 对内容多维度打分。
                    </span>
                    <n-select
                      :value="editForm.verification_config?.strategy || 'rules_only'"
                      :options="[
                        { label: '仅规则检查', value: 'rules_only' },
                        { label: '规则 + AI 评分', value: 'rules_and_llm' },
                      ]"
                      @update:value="(v: string) => { if (editForm.verification_config) editForm.verification_config.strategy = v }"
                      style="width: 200px"
                    />
                  </n-form-item>

                  <n-form-item label="最低评分阈值">
                    <n-space vertical style="width: 100%">
                      <span style="font-size: 12px; color: #999">
                        AI 评分低于此值时，在 ENFORCE 模式下会向用户展示质量告警。SHADOW 模式下仅记录不展示。
                      </span>
                      <n-slider
                        :value="editForm.verification_config?.min_score ?? 0.6"
                        @update:value="(v: number) => { if (editForm.verification_config) editForm.verification_config.min_score = v }"
                        :min="0" :max="1" :step="0.05"
                        :marks="{ 0: '0', 0.5: '0.5', 1: '1.0' }"
                        :format-tooltip="(v: number) => v.toFixed(2)"
                      />
                      <span style="font-size: 12px; color: #999">
                        当前值：{{ (editForm.verification_config?.min_score ?? 0.6).toFixed(2) }} — 低于此阈值触发质量告警
                      </span>
                    </n-space>
                  </n-form-item>

                  <n-form-item label="评分维度">
                    <n-space vertical style="width: 100%">
                      <span style="font-size: 12px; color: #999">
                        定义 AI 评分的维度。每个维度包含英文标识名、中文描述和权重（0~1）。所有维度的权重之和必须为 1.0，最多 8 个维度。
                        不知道怎么填？试试下方的预设模板，一键填入后可微调。
                      </span>

                      <!-- 预设模板 -->
                      <n-space v-if="!editForm.verification_config?.dimensions?.length" style="margin-bottom: 4px">
                        <span style="font-size: 12px; color: #666">预设模板：</span>
                        <n-button size="tiny" secondary type="primary" @click="() => {
                          if (editForm.verification_config) editForm.verification_config.dimensions = [
                            { name: 'accuracy', description: '事实准确性：关键数据和结论是否有来源支撑', weight: 0.3 },
                            { name: 'completeness', description: '内容完整性：是否覆盖用户要求的各个方面', weight: 0.3 },
                            { name: 'citation_diversity', description: '引用多样性：是否依赖多个来源而非单一来源', weight: 0.2 },
                            { name: 'depth', description: '论证深度：分析是否有洞察力而非流于表面', weight: 0.2 },
                          ]
                        }">研究类</n-button>
                        <n-button size="tiny" secondary type="primary" @click="() => {
                          if (editForm.verification_config) editForm.verification_config.dimensions = [
                            { name: 'accuracy', description: '政策信息准确性：名称、时间、内容是否正确', weight: 0.35 },
                            { name: 'completeness', description: '分析完整性：是否覆盖背景、内容、影响', weight: 0.35 },
                            { name: 'timeliness', description: '时效性：是否引用最新政策版本', weight: 0.3 },
                          ]
                        }">政策分析类</n-button>
                        <n-button size="tiny" secondary type="primary" @click="() => {
                          if (editForm.verification_config) editForm.verification_config.dimensions = [
                            { name: 'accuracy', description: '内容准确性：信息是否正确无误', weight: 0.35 },
                            { name: 'completeness', description: '回答完整性：是否充分回应了用户问题', weight: 0.35 },
                            { name: 'clarity', description: '表达清晰度：逻辑是否通顺、易于理解', weight: 0.3 },
                          ]
                        }">通用类</n-button>
                      </n-space>

                      <div
                        v-for="(dim, idx) in (editForm.verification_config?.dimensions || [])"
                        :key="idx"
                        style="display: flex; gap: 8px; align-items: center; width: 100%; margin-bottom: 8px"
                      >
                        <n-tooltip trigger="hover">
                          <template #trigger>
                            <n-input
                              v-model:value="dim.name"
                              placeholder="如 accuracy"
                              :status="dim.name && !/^[a-z][a-z0-9_]*$/.test(dim.name) ? 'error' : undefined"
                              :allow-input="(v: string) => /^[a-z0-9_]*$/.test(v)"
                              style="width: 120px; font-family: monospace; font-size: 12px"
                            />
                          </template>
                          标识名：用于结果展示和内部标记，仅限小写英文、数字和下划线（如 accuracy、cite_div）
                        </n-tooltip>
                        <n-input
                          v-model:value="dim.description"
                          placeholder="如：事实准确性，关键数据是否有来源支撑"
                          style="flex: 1; font-size: 12px"
                        >
                          <template #prefix>
                            <n-tooltip trigger="hover">
                              <template #trigger>
                                <span style="color: #999; cursor: help; font-size: 12px">?</span>
                              </template>
                              描述决定 AI 如何理解此维度并打分，请写清楚评判标准
                            </n-tooltip>
                          </template>
                        </n-input>
                        <n-input-number
                          v-model:value="dim.weight"
                          :min="0" :max="1" :step="0.05"
                          :show-button="false"
                          placeholder="权重"
                          style="width: 110px"
                        />
                        <n-button
                          size="small" quaternary type="error"
                          @click="editForm.verification_config?.dimensions.splice(idx, 1)"
                        >删除</n-button>
                      </div>

                      <n-space>
                        <n-button
                          size="small" dashed
                          @click="() => {
                            const dims = editForm.verification_config?.dimensions
                            if (dims) {
                              const idx = dims.length + 1
                              dims.push({ name: 'dim_' + idx, description: '', weight: 0 })
                            }
                          }"
                        >+ 添加维度</n-button>
                        <n-button
                          size="small" quaternary
                          @click="() => {
                            const dims = editForm.verification_config?.dimensions
                            if (dims && dims.length > 0) {
                              const avg = Math.round((1 / dims.length) * 100) / 100
                              dims.forEach((d, i) => { d.weight = i < dims.length - 1 ? avg : Math.round((1 - avg * (dims.length - 1)) * 100) / 100 })
                            }
                          }"
                        >自动均分</n-button>
                      </n-space>

                      <!-- 权重合计指示器 -->
                      <div v-if="editForm.verification_config?.dimensions?.length" style="margin-top: 8px">
                        <div style="display: flex; align-items: center; gap: 8px">
                          <span style="font-size: 12px; color: #666">权重合计：{{ verificationWeightTotal.toFixed(2) }}</span>
                          <n-progress
                            type="line"
                            :percentage="Math.min(verificationWeightTotal * 100, 120)"
                            :color="verificationWeightStatus === 'success' ? '#18a058' : verificationWeightStatus === 'warning' ? '#f0a020' : '#d03050'"
                            :show-indicator="false"
                            style="width: 120px"
                          />
                          <span :style="{ fontSize: '12px', color: verificationWeightStatus === 'success' ? '#18a058' : verificationWeightStatus === 'warning' ? '#f0a020' : '#d03050' }">
                            <template v-if="verificationWeightStatus === 'success'">&#10003; 权重正确</template>
                            <template v-else-if="verificationWeightStatus === 'warning'">不足 {{ (1 - verificationWeightTotal).toFixed(2) }}</template>
                            <template v-else>超出 {{ (verificationWeightTotal - 1).toFixed(2) }}</template>
                          </span>
                        </div>
                      </div>
                    </n-space>
                  </n-form-item>
                </div>
              </n-form>
            </n-tab-pane>

            <!-- 意图识别配置 -->
            <n-tab-pane name="intent" tab="意图识别配置">
              <n-form label-placement="left" label-width="120">
                <n-form-item label="优先级">
                  <n-space vertical style="width: 100%">
                    <n-slider
                      v-model:value="editForm.intent_priority"
                      :min="0" :max="100" :step="5"
                      :marks="{ 0: '0', 50: '50', 100: '100' }"
                      :disabled="editForm.intent_priority === null"
                    />
                    <n-space align="center">
                      <n-switch
                        :value="editForm.intent_priority !== null"
                        @update:value="(v: boolean) => editForm.intent_priority = v ? 50 : null"
                      />
                      <span style="font-size: 12px; color: #999">
                        {{ editForm.intent_priority === null ? '使用默认值（硬编码）' : `当前值: ${editForm.intent_priority}` }}
                      </span>
                    </n-space>
                  </n-space>
                </n-form-item>

                <n-form-item label="正则匹配模式">
                  <n-space vertical style="width: 100%">
                    <div
                      v-for="(_, idx) in editForm.regex_patterns"
                      :key="idx"
                      style="display: flex; gap: 8px; align-items: center; width: 100%"
                    >
                      <n-input
                        v-model:value="editForm.regex_patterns[idx]"
                        placeholder="正则表达式，如：(深度|深入).{0,5}(调研|研究)"
                        style="flex: 1; font-family: monospace; font-size: 12px"
                      />
                      <n-button
                        size="small" quaternary type="error"
                        @click="editForm.regex_patterns.splice(idx, 1)"
                      >删除</n-button>
                    </div>
                    <n-button
                      size="small" dashed
                      @click="editForm.regex_patterns.push('')"
                    >+ 添加正则</n-button>
                    <div style="font-size: 12px; color: #999">
                      每行一条正则表达式，匹配用户输入时触发此 Skill。全部删除则回退到硬编码默认值。
                    </div>
                    <!-- 正则测试沙箱 -->
                    <div v-if="editForm.regex_patterns.length > 0" style="margin-top: 8px; padding: 10px; background: #fafafa; border-radius: 6px; border: 1px solid #eee">
                      <div style="font-size: 12px; color: #666; margin-bottom: 6px">测试沙箱</div>
                      <n-input
                        v-model:value="regexTestInput"
                        placeholder="输入一句话测试正则匹配效果"
                        size="small"
                      />
                      <div v-if="regexTestInput.trim() && regexTestResults.length" style="margin-top: 6px">
                        <div
                          v-for="r in regexTestResults" :key="r.idx"
                          style="font-size: 12px; line-height: 1.8"
                        >
                          <span v-if="r.error" style="color: #d03050">正则 #{{ r.idx + 1 }} 语法错误: {{ r.error }}</span>
                          <span v-else-if="r.match" style="color: #18a058">正则 #{{ r.idx + 1 }} 命中「{{ r.matched }}」</span>
                          <span v-else style="color: #999">正则 #{{ r.idx + 1 }} 未命中</span>
                        </div>
                      </div>
                    </div>
                  </n-space>
                </n-form-item>

                <n-form-item label="排他规则 — 单词排除">
                  <n-space vertical style="width: 100%">
                    <n-dynamic-tags v-model:value="editForm.exclude_keywords" />
                    <div style="font-size: 12px; color: #999">
                      当用户输入包含以下任一关键词时，排除此 Skill。
                    </div>
                  </n-space>
                </n-form-item>

                <n-form-item label="排他规则 — 复合排除">
                  <n-space vertical style="width: 100%">
                    <div
                      v-for="(pair, idx) in editForm.exclude_pairs"
                      :key="idx"
                      style="display: flex; gap: 8px; align-items: center; width: 100%"
                    >
                      <n-input
                        v-model:value="editForm.exclude_pairs[idx][0]"
                        placeholder="关键词 A"
                        style="flex: 1"
                      />
                      <span style="color: #999; white-space: nowrap">且含</span>
                      <n-input
                        v-model:value="editForm.exclude_pairs[idx][1]"
                        placeholder="关键词 B"
                        style="flex: 1"
                      />
                      <n-button
                        size="small" quaternary type="error"
                        @click="editForm.exclude_pairs.splice(idx, 1)"
                      >删除</n-button>
                    </div>
                    <n-button
                      size="small" dashed
                      @click="editForm.exclude_pairs.push(['', ''])"
                    >+ 添加词对</n-button>
                    <div style="font-size: 12px; color: #999">
                      当用户输入同时包含词 A 和词 B 时，排除此 Skill。
                    </div>
                  </n-space>
                </n-form-item>

                <!-- 排他规则测试沙箱 -->
                <div v-if="editForm.exclude_keywords.length > 0 || editForm.exclude_pairs.length > 0" style="padding: 10px; background: #fafafa; border-radius: 6px; border: 1px solid #eee; margin-bottom: 16px">
                  <div style="font-size: 12px; color: #666; margin-bottom: 6px">排他规则测试</div>
                  <n-input
                    v-model:value="exclusionTestInput"
                    placeholder="输入一句话测试是否会被排除"
                    size="small"
                  />
                  <div v-if="exclusionTestInput.trim()" style="margin-top: 6px; font-size: 12px">
                    <span v-if="exclusionTestResult.keyword" style="color: #d03050">
                      会被排除 — 命中单词「{{ exclusionTestResult.keyword }}」
                    </span>
                    <span v-else-if="exclusionTestResult.pair" style="color: #d03050">
                      会被排除 — 同时包含「{{ exclusionTestResult.pair[0] }}」和「{{ exclusionTestResult.pair[1] }}」
                    </span>
                    <span v-else style="color: #18a058">不会被排除</span>
                  </div>
                </div>
              </n-form>
            </n-tab-pane>

            <n-tab-pane v-if="drawerMode === 'edit'" name="resources" tab="包内资源">
              <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px">
                <div>
                  <span style="font-weight: 500; font-size: 14px">Skill 包内资源（只读）</span>
                  <n-tooltip trigger="hover">
                    <template #trigger><span style="cursor: help; color: #999; margin-left: 6px">ⓘ</span></template>
                    references/ 和 scripts/ 子目录,启动时从 backend/skills/{name}/ 同步到 DB。
                    LLM 通过 read_skill_file / run_skill_script 访问。
                  </n-tooltip>
                </div>
                <n-button
                  v-if="originalSkill"
                  size="small" quaternary
                  :loading="resourcesLoading"
                  @click="loadSkillResources(originalSkill.name)"
                >刷新</n-button>
              </div>

              <n-spin :show="resourcesLoading">
                <div v-if="skillResources.length === 0 && !resourcesLoading" style="color: #999; font-size: 13px; padding: 24px 0; text-align: center">
                  该 Skill 没有包内资源。
                  <div style="margin-top: 6px; font-size: 12px">
                    如需添加,把文件放到 <code>backend/skills/{{ originalSkill?.name }}/references/</code>
                    或 <code>scripts/</code> 下,重启后端自动 seed。
                  </div>
                </div>
                <template v-else>
                  <div v-for="(items, dir) in groupedResources" :key="dir" style="margin-bottom: 16px">
                    <div style="font-size: 13px; font-weight: 500; color: #666; margin-bottom: 8px">
                      <n-tag :type="dir === 'scripts' ? 'warning' : 'info'" size="small" :bordered="false">
                        {{ dir }}/
                      </n-tag>
                      <span style="margin-left: 6px; color: #999; font-weight: normal">{{ items.length }} 个文件</span>
                    </div>
                    <table style="width: 100%; border-collapse: collapse; font-size: 13px">
                      <tbody>
                        <tr
                          v-for="item in items" :key="item.path"
                          style="border-bottom: 1px solid #f0f0f0"
                        >
                          <td style="padding: 8px 4px; font-family: monospace">{{ item.path }}</td>
                          <td style="padding: 8px 4px; color: #999; width: 80px; text-align: right">
                            {{ formatSize(item.size) }}
                          </td>
                          <td style="padding: 8px 4px; width: 80px; text-align: right">
                            <n-button size="tiny" quaternary type="info" @click="viewResource(item)">
                              <template #icon><n-icon :component="EyeOutline" /></template>
                              查看
                            </n-button>
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </template>
              </n-spin>
            </n-tab-pane>

            <n-tab-pane name="history" tab="变更历史">
              <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px">
                <span style="font-weight: 500; font-size: 14px">变更历史</span>
                <n-button size="small" quaternary @click="loadAuditLogs">刷新</n-button>
              </div>
              <div v-if="auditLogs.length === 0" style="color: #999; font-size: 13px">暂无修改记录</div>
              <n-timeline v-else>
                <n-timeline-item
                  v-for="log in auditLogs"
                  :key="log.id"
                  :type="log.action === 'delete' ? 'error' : log.action === 'create' ? 'success' : log.action === 'rollback' ? 'warning' : 'info'"
                  :title="`${log.admin_username || log.admin_user_id} ${actionLabel(log.action)}`"
                  :time="log.created_at ? new Date(log.created_at).toLocaleString() : ''"
                >
                  <div style="font-size: 12px; color: #666">
                    <span v-if="log.changed_fields">
                      字段: {{ (log.changed_fields || []).join(', ') }}
                    </span>
                    <n-space style="margin-top: 4px" :size="4">
                      <n-button
                        v-if="log.old_values && log.new_values"
                        size="tiny" quaternary type="info"
                        @click="showDiff(log)"
                      >查看变更</n-button>
                      <n-popconfirm
                        v-if="log.old_values && log.action !== 'create'"
                        @positive-click="handleRollback(log)"
                      >
                        <template #trigger>
                          <n-button size="tiny" quaternary type="warning">回滚到此版本</n-button>
                        </template>
                        确定要回滚到此次修改前的状态吗？回滚操作会产生一条新的变更记录。
                      </n-popconfirm>
                    </n-space>
                  </div>
                </n-timeline-item>
              </n-timeline>
            </n-tab-pane>
          </n-tabs>
        </n-spin>

        <template #footer>
          <n-space justify="end">
            <n-button @click="drawerVisible = false">取消</n-button>
            <n-button type="primary" :loading="saving" @click="handleSave">
              {{ drawerMode === 'create' ? '创建' : '保存' }}
            </n-button>
          </n-space>
        </template>
      </n-drawer-content>
    </n-drawer>

    <!-- Diff 查看弹窗 -->
    <n-modal
      v-model:show="diffModalVisible"
      preset="card"
      title="变更详情"
      style="width: 720px; max-width: 90vw"
      :segmented="{ content: true, footer: true }"
    >
      <div v-if="diffLog" style="font-size: 13px">
        <div style="color: #999; margin-bottom: 12px">
          {{ diffLog.admin_username || diffLog.admin_user_id }}
          {{ actionLabel(diffLog.action) }}
          · {{ diffLog.created_at ? new Date(diffLog.created_at).toLocaleString() : '' }}
        </div>
        <div v-for="item in diffItems" :key="item.field" style="margin-bottom: 16px">
          <div style="font-weight: 500; margin-bottom: 6px; color: #333">{{ item.field }}</div>
          <div class="diff-block">
            <template v-for="(part, pi) in item.parts" :key="pi">
              <div
                v-for="(line, li) in part.value.split('\n').filter((l: string, i: number, a: string[]) => i < a.length - 1 || l)"
                :key="`${pi}-${li}`"
                :class="{ 'diff-add': part.added, 'diff-remove': part.removed, 'diff-context': !part.added && !part.removed }"
              >{{ part.added ? '+ ' : part.removed ? '- ' : '  ' }}{{ line }}</div>
            </template>
          </div>
        </div>
      </div>
      <template #footer>
        <n-space justify="end">
          <n-button @click="diffModalVisible = false">关闭</n-button>
          <n-popconfirm
            v-if="diffLog?.old_values && diffLog?.action !== 'create'"
            @positive-click="handleRollback(diffLog!)"
          >
            <template #trigger>
              <n-button type="warning" :loading="rollingBack">回滚到此版本前</n-button>
            </template>
            确定要回滚到此次修改前的状态吗？
          </n-popconfirm>
        </n-space>
      </template>
    </n-modal>

    <!-- 资源内容预览弹窗 (M4) -->
    <n-modal
      v-model:show="resourceModalVisible"
      preset="card"
      :title="resourceContent?.path || '资源预览'"
      style="width: 880px; max-width: 92vw"
      :segmented="{ content: true, footer: true }"
    >
      <n-spin :show="resourceContentLoading">
        <div v-if="resourceContent" style="font-size: 13px">
          <n-space style="margin-bottom: 10px" :size="8">
            <n-tag
              :type="resourceContent.resource_type === 'script' ? 'warning' : 'info'"
              size="small" :bordered="false"
            >{{ resourceContent.resource_type }}</n-tag>
            <n-tag size="small" :bordered="false">{{ formatSize(resourceContent.size) }}</n-tag>
            <n-tag v-if="resourceContent.truncated" type="error" size="small" :bordered="false">
              已截断 (> 100KB)
            </n-tag>
            <n-tag v-if="resourceContent.is_binary" type="warning" size="small" :bordered="false">
              二进制(预览不支持)
            </n-tag>
          </n-space>
          <pre class="resource-preview">{{ resourceContent.content }}</pre>
        </div>
      </n-spin>
      <template #footer>
        <n-space justify="end">
          <n-button @click="resourceModalVisible = false">关闭</n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.skill-management {
  padding: 0;
}
/* 提示词 textarea 自动填满可用高度 */
.prompt-textarea :deep(textarea) {
  height: calc(100vh - 220px) !important;
  min-height: 200px;
}
/* plan 模式有 alert 提示框，需要减去更多高度 */
.prompt-textarea-with-alert :deep(textarea) {
  height: calc(100vh - 350px) !important;
  min-height: 200px;
}

.stats-grid {
  margin-bottom: 20px;
}

.category-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 16px;
}

.action-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.skill-card {
  transition: box-shadow 0.2s;
}
.skill-card:hover {
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
}

.card-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  font-size: 15px;
  overflow: hidden;
}

.card-body {
  min-height: 100px;
}

.skill-desc {
  color: #666;
  font-size: 13px;
  margin: 0 0 10px 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.skill-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 8px;
}

.skill-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 12px;
  color: #999;
}

.meta-item {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.section-title {
  font-weight: 600;
  font-size: 14px;
  margin-bottom: 4px;
  color: #333;
}
.section-desc {
  font-size: 12px;
  color: #999;
  margin-bottom: 10px;
  line-height: 1.5;
}
.field-hint {
  margin-left: 8px;
  color: #999;
  font-size: 12px;
  white-space: nowrap;
}

.readonly-info {
  display: flex;
  gap: 16px;
  padding: 12px 0;
  font-size: 13px;
  color: #999;
  border-top: 1px solid #f0f0f0;
  margin-top: 8px;
}

.diff-block {
  font-family: 'SF Mono', 'Menlo', 'Monaco', 'Consolas', monospace;
  font-size: 12px;
  line-height: 1.6;
  background: #fafafa;
  border: 1px solid #e8e8e8;
  border-radius: 4px;
  padding: 8px 12px;
  overflow-x: auto;
  white-space: pre;
}
.diff-add {
  background: #e6ffec;
  color: #1a7f37;
}
.diff-remove {
  background: #ffebe9;
  color: #cf222e;
}
.diff-context {
  color: #666;
}

.resource-preview {
  font-family: 'SF Mono', 'Menlo', 'Monaco', 'Consolas', monospace;
  font-size: 12px;
  line-height: 1.55;
  background: #fafafa;
  border: 1px solid #e8e8e8;
  border-radius: 4px;
  padding: 12px 14px;
  margin: 0;
  max-height: 60vh;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
