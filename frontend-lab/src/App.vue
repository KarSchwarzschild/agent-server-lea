<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js/lib/core'
import javascript from 'highlight.js/lib/languages/javascript'
import typescript from 'highlight.js/lib/languages/typescript'
import python from 'highlight.js/lib/languages/python'
import json from 'highlight.js/lib/languages/json'
import xml from 'highlight.js/lib/languages/xml'
import css from 'highlight.js/lib/languages/css'
import bash from 'highlight.js/lib/languages/bash'
import 'highlight.js/styles/github.css'
import {
  createSession,
  createAgent,
  deleteAgent,
  checkAgentShare,
  listChatModels,
  listRetrievalModels,
  listPublishRequests,
  reviewPublishRequest,
  shareAgent,
  unshareAgent,
  createKnowledgeBase,
  createMemory,
  createMcpServer,
  createTool,
  createTeam,
  updateTeam,
  deleteTeam,
  addTeamMember,
  deleteKnowledgeDocument,
  deleteMemory,
  deleteUserFile,
  downloadAttachment,
  downloadWorkspaceFile,
  getMessages,
  getUserStorage,
  importFileToSession,
  listUserFiles,
  listAgents,
  listAgentBindings,
  listMcpServers,
  listProjects,
  listSessionAttachments,
  listTeamMembers,
  listKnowledgeBases,
  listKnowledgeDocuments,
  listMemories,
  listSessions,
  listSkills,
  listTools,
  listTenants,
  listTeams,
  listWorkspaceFiles,
  publishWorkspaceFile,
  sendMessageStream,
  resumeMessageStream,
  stopGeneration,
  getTurnStatus,
  deleteSession,
  STREAM_CLOSED,
  setSessionActiveAgent,
  uploadAttachment,
  uploadKnowledgeDocument,
  toggleMcpServer,
  syncMcpServer,
  removeTeamMember,
  detachSessionResource,
  updateAgent,
  uploadSkillBundle,
  createProject,
  updateProject,
  deleteProject,
  archiveProject,
  updateSkill,
  deleteSkill,
  publishSkill,
  updateMcpServer,
  deleteMcpServer,
  updateTool,
  deleteTool,
  updateKnowledgeBase,
  deleteKnowledgeBase,
  updateMemory,
  clearMemories,
  searchKnowledgeBase,
  bindAgentKnowledgeBase,
  bindAgentSkill,
  bindAgentTool,
  bindAgentMcp,
  unbindAgentKnowledgeBase,
  unbindAgentSkill,
  unbindAgentTool,
  unbindAgentMcp,
  ApiError,
  type ChatEvent,
  type StorageUsage,
  type UserFile,
  type McpMarketItem,
  type SkillMarketItem,
  type ToolMarketItem, type ProjectItem,
  type ShareCheck, type ShareBlocker, type ChatModel, type RetrievalModel, type PublishRequest, type MemoryItem, type MemoryCategory, type MemoryScope
  , type AgentInfo, type AgentBindings, type KnowledgeBaseItem, type KnowledgeDocument, type KnowledgeSearchHit, type SessionAttachment, type TeamInfo, type TeamMember, type TenantItem
} from './api'
import {
  Add,
  AlbumsOutline,
  AppsOutline,
  AttachOutline,
  BookOutline,
  BulbOutline,
  ChatbubblesOutline,
  ChevronBackOutline,
  ChevronForwardOutline,
  CodeSlashOutline,
  CompassOutline,
  DocumentTextOutline,
  FolderOpenOutline,
  FolderOutline,
  LibraryOutline,
  PeopleOutline,
  RefreshOutline,
  SearchOutline,
  SettingsOutline,
  TerminalOutline
} from '@vicons/ionicons5'
import {
  NButton,
  NConfigProvider,
  NIcon,
  NInput,
  NInputNumber,
  NModal,
  NSelect,
  NSwitch,
  NTag,
  NUpload,
  lightTheme,
  type UploadFileInfo
} from 'naive-ui'

type NavKey = 'chat' | 'projects' | 'experts' | 'market' | 'knowledge' | 'memories' | 'tasks' | 'settings'
// library = 用户的长期文件库（跨会话）。跟 files 那页是两回事：files 是**当前会话**
// 工作区里的东西，30 天就随会话回收；library 是长期留下来的。
type InspectorTab = 'files' | 'library' | 'artifacts' | 'citations' | 'debug'
type MessageRole = 'user' | 'assistant' | 'system'
type ChatMode = 'default' | 'plan' | 'expert' | 'team'

type Message = {
  id: number
  role: MessageRole
  author: string
  text: string
  status?: 'running' | 'done' | 'failed'
  activities?: ActivityItem[]
  references?: ReferenceItem[]
  planApproval?: {
    question: string
    options: string[]
    resolved?: 'approved' | 'rejected'
  }
  // 只有历史重放（chat_messages.extra.usage）才有——直播的 SSE 事件表里
  // 没有专门的 usage 事件（见 docs/FRONTEND_API.md 的七种事件类型），token
  // 数只在这一轮落库之后、刷新/重新打开会话时才能看到，不是漏加了字段，
  // 是这一步本来就拿不到直播数据。
  usage?: { inputTokens: number; outputTokens: number }
}

type ReferenceItem = {
  id: string
  title: string
  path?: string
  url?: string
  source?: string
  authors?: string[]
  year?: number
  abstract?: string
}

// SandboxArtifact 是沙箱执行产出的文件。后端两种约定都可能出现（纯字符串路径，
// 或 {name,path,size}），见 collector.go 的 normalizeArtifacts —— 前端统一成一种形状。
type SandboxArtifact = {
  path: string
  name: string
  size?: number
}

type ActivityItem = {
  id: string
  kind: 'thinking' | 'tool' | 'sandbox' | 'chain'
  title: string
  detail?: string
  status?: string
  // ── 沙箱专用的结构化字段 ──────────────────────────────────
  //
  // 之前沙箱活动只有一个 detail，值是 `pretty(整个对象)` —— 界面上就是一坨 JSON。
  // 而后端（session/collector.go 的 onSandboxLocked）是**特意**把代码、stdout、
  // stderr、产物、耗时拆成独立字段落库的，全被压回一个字符串等于把那份工作扔了。
  // 更实际的问题是 stderr：代码跑挂的时候 traceback 就在里面，可它被埋在 JSON 中间，
  // 跟 codePreview 一个颜色，用户得先读懂这坨 JSON 才能找到报错。
  sandbox?: {
    language?: string
    code?: string
    stdout?: string
    stderr?: string
    message?: string
    durationMs?: number
    truncated?: boolean
    artifacts: SandboxArtifact[]
  }
  // ── AskUser 专用的结构化字段 ──────────────────────────────────
  //
  // AskUser 是引擎里的一个普通工具，走的是跟 Skill/Read 一样的 tool_event 通道——
  // 不是像计划批准那样的专门 SSE 事件类型。之前没特殊处理，界面上就是把工具的
  // 原始 stdout 行数组（[{stream,text}, ...]）整个 pretty 打印出来，问题文本和
  // 候选项都在一大段 JSON 里，候选项也不能点。
  //
  // answered 记"这条卡片已经点过一个候选"，纯本地 UI 状态，不落库——只是让
  // 按钮点过之后变灰，避免同一屏里对着一张已经回复过的卡片再点一次候选。
  askUser?: {
    question: string
    candidates: string[]
    answered?: boolean
  }
}

type AssetFile = {
  path: string
  // dir 是 path 去掉文件名之后的那段，'' 表示工作区根目录。
  //
  // 单独留一份而不是每次现算，是因为界面按目录分组渲染。以前这里只存 basename
  // （`path.split('/').pop()`），目录结构在进入界面的那一刻就丢了 —— 后端明明返的是
  // 带完整相对路径的文件树，前端把它拍平成一串文件名，于是 output/report.md 和
  // data/report.md 在列表里长得一模一样，点哪个是哪个全靠猜。
  dir: string
  name: string
  type: string
  size: string
  mtime: number
  status: 'ready' | 'indexing' | 'draft'
}

type LogItem = {
  method: string
  path: string
  status: number | string
  time: number
  body: unknown
}

const navItems = [
  { key: 'chat', label: '对话', icon: ChatbubblesOutline },
  { key: 'experts', label: '专家', icon: PeopleOutline },
  { key: 'market', label: '市场', icon: CompassOutline },
  { key: 'knowledge', label: '知识库', icon: LibraryOutline },
  { key: 'memories', label: '记忆', icon: BulbOutline },
  { key: 'settings', label: '设置', icon: SettingsOutline }
] as const

const conversations = ref<{ id: string; title: string; meta: string; project_id?: number | null; agent_id?: number | null; model?: string | null }[]>([])
const projects = ref<ProjectItem[]>([])
const expertItems = ref<AgentInfo[]>([])
const teamItems = ref<TeamInfo[]>([])
const knowledgeBases = ref<KnowledgeBaseItem[]>([])
const selectedKnowledgeBase = ref<KnowledgeBaseItem | null>(null)
const knowledgeDocuments = ref<KnowledgeDocument[]>([])
const expertFormOpen = ref(false)
const editingExpertId = ref<number | null>(null)
const knowledgeFormOpen = ref(false)
const editingKnowledgeBaseId = ref<number | null>(null)
const knowledgeSearchQuery = ref('')
const knowledgeSearchResults = ref<KnowledgeSearchHit[]>([])
const knowledgeSearchLoading = ref(false)
const memories = ref<MemoryItem[]>([])
const memoriesLoading = ref(false)
const memoriesLoaded = ref(false)
const memoryError = ref('')
const memorySearch = ref('')
const memoryCategory = ref<'all' | MemoryCategory>('all')
const memoryScope = ref<'all' | MemoryScope>('all')
const selectedMemoryId = ref<number | null>(null)
const memoryEditorMode = ref<'create' | 'edit' | null>(null)
const memoryForm = ref<{ content: string; category: MemoryCategory; importance: number; scope: MemoryScope }>({ content: '', category: 'fact', importance: 5, scope: 'global' })
const expertForm = ref({ name: '', description: '', category: 'general', soul_prompt: '', model: 'qwen-plus', skill_ids: [] as number[], mcp_ids: [] as number[], tool_ids: [] as number[], kb_ids: [] as number[] })
const teamFormOpen = ref(false)
const editingTeamId = ref<number | null>(null)
const teamForm = ref({ agent_id: 0, strategy: 'star', decomposer_prompt: '', synthesizer_prompt: '', max_subtasks: 5, member_ids: [] as number[] })
// 注意：selectedTeamId 只表示"当前对话用的是哪个专家团"。成员面板开在哪个团由
// editingTeamId 决定 —— 以前两件事挤在 selectedTeamId 一个 ref 里，于是在聊天里选中
// 一个团（previewConfiguration/startWithConfiguration 都会写它）就顺带把成员面板弹出来，
// 而那条路径不会去拉成员，面板只能显示"还没有成员。"。
const selectedTeamId = ref<number | null>(null)
const configurationPanelOpen = ref(false)
const configurationSelection = ref('default')
const chatMode = ref<ChatMode>('default')
const teamMembers = ref<TeamMember[]>([])
const expertBindings = ref<Record<number, AgentBindings>>({})
const teamMemberForm = ref({ member_agent_id: 0, role: 'worker', name: '' })
const knowledgeForm = ref({
  name: '', description: '', visibility: 'private',
  embedding_model: '', reranker_model: '', reranker_enabled: true,
  vlm_model: '', vlm_enabled: true, multimodal: false
})
const attachmentFiles = ref<{ id: number; name: string; size: string; downloadable?: boolean }[]>([])
// 从文件库选中但还没真正导入的文件——点选中只是本地暂存，真正调导入接口
// 推迟到发消息那一刻（见 flushPendingLibraryImports）。跟 skill/tool 那次
// 是同一个原则,但没法完全照抄那次的"直接把 id 塞进发消息请求"做法：
// attachment_id 是导入这个动作本身的产物，选中的时候根本还不存在,没有
// id 可以先塞进去,所以这里必须先调一次导入接口拿到 id,再继续发消息——
// 区别只是"第一次碰后端"从"点选中"挪到了"点发送",不是完全没有网络请求。
const pendingLibraryImports = ref<UserFile[]>([])
// 工具卡片默认收起的原始输出（Read/Skill/Bash 那种大段 dump）,点标题展开——
// 故意跟 activity 数据本身分开放,不掺进 ActivityItem：那份数据每条 SSE 事件
// 都会重新构造、用 Object.assign 合并进已有对象（见 addActivity），如果把
// "展开没展开"也塞进 activity 上,下一条事件一来就会被覆盖回默认值,用户刚
// 点开的卡片会莫名其妙又收起——AskUser 那个合并覆盖的坑就是这个模式踩过的,
// 这次直接用一个独立的集合，只由用户点击改，SSE 事件完全碰不到它。
const expandedActivities = ref<Set<string>>(new Set())
function toggleActivity(id: string) {
  const next = new Set(expandedActivities.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  expandedActivities.value = next
}

const marketItems = ref<{ skills: SkillMarketItem[]; mcp: McpMarketItem[]; tools: ToolMarketItem[] }>({ skills: [], mcp: [], tools: [] })
const marketLoading = ref(false)
const marketError = ref('')
const skillVersion = ref('1.0.0')
const skillCategory = ref('general')
const skillChangeLog = ref('')
const skillUploadOpen = ref(false)
const skillUploadFile = ref<File | null>(null)
const skillUploadLoading = ref(false)
const toolFormOpen = ref(false)
const editingMarketKind = ref<'skill' | 'mcp' | 'tool' | null>(null)
const editingMarketId = ref<number | null>(null)
const marketEditOpen = ref(false)
const marketEditForm = ref({ name: '', display_name: '', description: '', category: 'general', system_prompt: '', endpoint: '', bearerToken: '', toolFilter: '', schema_json: '{\n  "type": "object",\n  "properties": {}\n}' })
const publishSkillId = ref<number | null>(null)
const publishTarget = ref<'tenant' | 'global'>('tenant')
const publishNote = ref('')
const toolForm = ref({ name: '', display_name: '', description: '', category: 'general', endpoint: '', schema_json: '{\n  "type": "object",\n  "properties": {}\n}' })
const mcpFormOpen = ref(false)
const mcpForm = ref({ name: '', description: '', category: 'general', endpoint: '', bearerToken: '', toolFilter: '' })
const sessionAttachments = ref<SessionAttachment[]>([])
// 选了 skill/mcp/tool 但还没点发送——只是本地暂存,不调"挂载"接口。真正挂上去
// 是发送那一刻跟消息一起完成的（见 sendChatMessage），不需要用户先手动点一次
// "挂载到当前会话"再回来发消息——那是两步做一件事,还容易让人以为选完就已经
// 生效了(其实挂载要等下一条消息重建引擎才生效,见 session_attachments.go 顶部
// 的说明,选跟挂根本不需要拆成两次用户操作)。
const pendingAttachments = ref<{ kind: SessionAttachment['kind']; ref_id: number; name: string }[]>([])
const mountPanelOpen = ref(false)
const mountKind = ref<SessionAttachment['kind']>('skill')
const mountSelection = ref(0)
const modelPanelOpen = ref(false)
const modelDraft = ref('qwen-plus')
// 可选聊天模型。取不到就是空数组 —— 选择器开着 tag，照样能手输，不影响用。
const chatModels = ref<ChatModel[]>([])
const modelOptions = computed(() => chatModels.value.map(model => ({
  // 把 provider 标在标签上：同名模型可能来自不同 provider，而"我选的这个走的是
  // 哪条线"是排查"答得不对劲"时第一个要问的问题。
  label: model.is_default ? `${model.name}（${model.provider} · 默认）` : `${model.name}（${model.provider}）`,
  value: model.name
})))
const defaultChatModel = computed(() =>
  chatModels.value.find(model => model.is_default)?.name || chatModels.value[0]?.name || 'qwen-plus'
)

function selectCurrentModel(value: unknown) {
  const model = typeof value === 'string' ? value.trim() : ''
  if (!model) return
  modelDraft.value = model
  const conversation = conversations.value.find(item => item.id === currentSessionId.value)
  if (conversation) conversation.model = model
  modelPanelOpen.value = false
}

async function loadChatModels() {
  try {
    const result = await listChatModels()
    chatModels.value = result.items || []
  } catch {
    // 静默：模型列表读不到只是退回手输，不该在界面上顶一条红色报错。
    chatModels.value = []
  }
}

// 建库/改库表单的 embedding/reranker/vlm 下拉供数，跟 chatModels 是同一个模式：
// 读不到就退回空数组，表单退化成手输，不在界面上顶红色报错。
const retrievalModels = ref<{ embedding: RetrievalModel[]; reranker: RetrievalModel[]; vlm: RetrievalModel[] }>({ embedding: [], reranker: [], vlm: [] })

async function loadRetrievalModels() {
  try {
    const [embedding, reranker, vlm] = await Promise.all([
      listRetrievalModels('embedding'),
      listRetrievalModels('reranker'),
      listRetrievalModels('vlm')
    ])
    retrievalModels.value = {
      embedding: embedding.items || [],
      reranker: reranker.items || [],
      vlm: vlm.items || []
    }
  } catch {
    retrievalModels.value = { embedding: [], reranker: [], vlm: [] }
  }
}

// 正在编辑的知识库已经处理过多少文档——embedding_model/embedding_dim 只在这个数
// 为 0 时才可能改成功（后端按同一条件门，见 server_user_kb.py），编辑一个已有
// 文档的库时把输入框禁掉，别让用户点了保存才在错误提示里第一次知道改不了。
const editingKnowledgeBaseDocCount = computed(() => {
  if (!editingKnowledgeBaseId.value) return 0
  return knowledgeBases.value.find(kb => kb.id === editingKnowledgeBaseId.value)?.document_count || 0
})

// 建库/改库表单的三个模型下拉——跟聊天模型选择器（modelOptions）同一个套路：
// 真实列表渲染选项，同时 filterable+tag 允许手输，列表读不到也不影响填表。
const embeddingModelOptions = computed(() => retrievalModels.value.embedding.map(model => ({
  label: model.is_default ? `${model.name}（默认）` : model.name, value: model.name
})))
const rerankerModelOptions = computed(() => retrievalModels.value.reranker.map(model => ({
  label: model.is_default ? `${model.name}（默认）` : model.name, value: model.name
})))
const vlmModelOptions = computed(() => retrievalModels.value.vlm.map(model => ({
  label: model.is_default ? `${model.name}（默认）` : model.name, value: model.name
})))

const files = ref<AssetFile[]>([])
const messages = ref<Message[]>([])

// 用户文件库
const userFiles = ref<UserFile[]>([])
const userStorage = ref<StorageUsage | null>(null)
const libraryKind = ref<'' | 'upload' | 'artifact'>('')
const libraryLoading = ref(false)
const libraryError = ref('')
// 按行记 busy：导入/删除是逐个文件的操作，一个全局 loading 会让整张列表都不能点，
// 而用户完全可以连着删好几个。
const libraryBusyKey = ref('')

const activeNav = ref<NavKey>('chat')
const expertTab = ref<'experts' | 'teams'>('experts')
const marketTab = ref<'skills' | 'mcp' | 'tools'>('skills')
const marketCategory = ref('all')
const inspectorOpen = ref(false)
const inspectorTab = ref<InspectorTab>('files')
const selectedConversation = ref('')
const selectedProjectId = ref<number | null>(null)
const projectFormOpen = ref(false)
const editingProjectId = ref<number | null>(null)
const projectForm = ref({ name: '', description: '' })
const currentSessionId = ref('')
const sessionLoading = ref(false)
// 每次离开会话或开始一轮发送都使旧世代失效。历史加载、SSE 和重连共用它，
// 避免旧请求迟到的 session/done 事件覆盖新对话，或把新消息发回旧 session。
// 仅比较 session ID 不够：新草稿尚无 ID，A→B→A 也可能收到第一次 A 的响应。
let openSessionGeneration = 0
const agentId = ref(0)
const prompt = ref('')
const token = ref(localStorage.getItem('lea_workspace_token') || '')
const connectionError = ref('')
const tenants = ref<TenantItem[]>([])
const selectedTenantId = ref(localStorage.getItem('lea_workspace_tenant_id') || '')
const streamRunning = ref(false)
const selectedFile = ref<AssetFile | undefined>()
const workspacePublishLoading = ref(false)
const requestLogs = ref<LogItem[]>([])
const attachmentIds = ref<number[]>([])
const filePreviewUrl = ref('')
const filePreviewKind = ref<'text' | 'image' | 'pdf' | 'html' | 'unsupported'>('unsupported')
const filePreviewText = ref('')
const filePreviewLoading = ref(false)
const workspaceLoading = ref(false)
const workspaceError = ref('')
// 后端 /api/files/list 除了 files 还返这两个，以前都被丢掉了 —— 见 api.ts 里
// listWorkspaceFiles 的注释。
const workspaceTruncated = ref(false)
const workspaceStore = ref('')
const showHiddenFiles = ref(false)
let stopCurrentStream: (() => void) | null = null

// 续传游标：最后一个收到的 SSE id。断线后用它告诉服务端"给我这之后的"。
// 空串 = 从头补（新一轮开始时重置）。
let lastEventId = ''
// 一轮之内最多自动重连几次。够不着就停手交给用户 —— 无限重连在服务端真的挂了
// 的时候只会让人看着转圈，而手动按钮至少说明"我们知道断了"。
const MAX_RESUME_ATTEMPTS = 3
let resumeAttempts = 0
// 断了且自动重连用完之后置位，模板据此显示"重新连接"按钮。
const resumeAvailable = ref(false)

const apiForm = ref({
  method: 'GET',
  path: '/health',
  body: ''
})

const activeTitle = computed(() => navItems.find(item => item.key === activeNav.value)?.label || '对话')
const memoryCategoryOptions: { label: string; value: MemoryCategory }[] = [
  { label: '事实', value: 'fact' },
  { label: '偏好', value: 'preference' },
  { label: '身份', value: 'identity' },
  { label: '进展', value: 'progress' }
]
const memoryScopeOptions = computed<{ label: string; value: MemoryScope }[]>(() => [
  { label: '平台级 · 跨 Agent', value: 'global' },
  ...expertItems.value.map(expert => ({ label: `Agent · ${expert.name}`, value: `agent:${expert.id}` as MemoryScope }))
])
const selectedMemory = computed(() => memories.value.find(item => item.id === selectedMemoryId.value) || null)
const filteredMemories = computed(() => {
  const query = memorySearch.value.trim().toLowerCase()
  return memories.value.filter(item => {
    if (memoryCategory.value !== 'all' && item.category !== memoryCategory.value) return false
    if (memoryScope.value !== 'all' && item.scope !== memoryScope.value) return false
    return !query || item.content.toLowerCase().includes(query)
  })
})

function memoryCategoryLabel(category: string) {
  return memoryCategoryOptions.find(item => item.value === category)?.label || category
}

function memoryScopeLabel(scope: string) {
  if (scope === 'global') return '平台级 · 跨 Agent'
  const agentId = Number(scope.replace(/^agent:/, ''))
  const expert = expertItems.value.find(item => item.id === agentId)
  return expert ? `Agent · ${expert.name}` : (Number.isFinite(agentId) ? `Agent #${agentId}` : '未知范围')
}

function memoryScopeType(scope: string) {
  return scope === 'global' ? 'success' : 'info'
}

function formatMemoryDate(value?: string | null) {
  if (!value) return '时间未知'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function openNavigation(key: NavKey) {
  activeNav.value = key
  if (key === 'memories') void loadMemories()
}

async function loadMemories(force = false) {
  if (memoriesLoading.value || (memoriesLoaded.value && !force)) return
  memoriesLoading.value = true
  memoryError.value = ''
  try {
    memories.value = await listMemories()
    memoriesLoaded.value = true
    if (selectedMemoryId.value && !memories.value.some(item => item.id === selectedMemoryId.value)) {
      selectedMemoryId.value = null
      memoryEditorMode.value = null
    }
  } catch (error) {
    memoryError.value = error instanceof Error ? error.message : String(error)
    requestLogs.value.unshift({ method: 'GET', path: '/api/user/memories', status: 'ERR', time: 0, body: memoryError.value })
  } finally {
    memoriesLoading.value = false
  }
}

function startCreateMemory() {
  selectedMemoryId.value = null
  memoryForm.value = { content: '', category: 'fact', importance: 5, scope: 'global' }
  memoryEditorMode.value = 'create'
}

function selectMemory(item: MemoryItem) {
  selectedMemoryId.value = item.id
  memoryEditorMode.value = null
}

function startEditMemory(item: MemoryItem) {
  selectedMemoryId.value = item.id
  memoryForm.value = { content: item.content, category: item.category, importance: item.importance || 5, scope: item.scope || 'global' }
  memoryEditorMode.value = 'edit'
}

function cancelMemoryEdit() {
  memoryEditorMode.value = null
  if (!selectedMemoryId.value) selectedMemoryId.value = memories.value[0]?.id || null
}

async function saveMemory() {
  const content = memoryForm.value.content.trim()
  if (!content || submitBusyKey.value) return
  submitBusyKey.value = 'memory-save'
  memoryError.value = ''
  const editingId = memoryEditorMode.value === 'edit' ? selectedMemoryId.value : null
  try {
    if (editingId) {
      await updateMemory(editingId, { content, category: memoryForm.value.category, importance: memoryForm.value.importance, scope: memoryForm.value.scope })
    } else {
      const created = await createMemory({ content, category: memoryForm.value.category, importance: memoryForm.value.importance, scope: memoryForm.value.scope })
      selectedMemoryId.value = created.id
    }
    memoryEditorMode.value = null
    memoriesLoaded.value = false
    await loadMemories(true)
  } catch (error) {
    memoryError.value = error instanceof Error ? error.message : String(error)
    requestLogs.value.unshift({ method: editingId ? 'PUT' : 'POST', path: editingId ? `/api/user/memories/${editingId}` : '/api/user/memories', status: 'ERR', time: 0, body: memoryError.value })
  } finally {
    submitBusyKey.value = ''
  }
}

async function removeMemory(item: MemoryItem) {
  if (!window.confirm('删除这条记忆？后续对话将不再使用它。') || submitBusyKey.value) return
  submitBusyKey.value = `memory-delete-${item.id}`
  memoryError.value = ''
  try {
    await deleteMemory(item.id)
    memories.value = memories.value.filter(memory => memory.id !== item.id)
    if (selectedMemoryId.value === item.id) {
      selectedMemoryId.value = memories.value[0]?.id || null
      memoryEditorMode.value = null
    }
  } catch (error) {
    memoryError.value = error instanceof Error ? error.message : String(error)
    requestLogs.value.unshift({ method: 'DELETE', path: `/api/user/memories/${item.id}`, status: 'ERR', time: 0, body: memoryError.value })
  } finally {
    submitBusyKey.value = ''
  }
}

async function removeAllMemories() {
  if (!memories.value.length || !window.confirm('清空全部长期记忆？此操作无法撤销。') || submitBusyKey.value) return
  submitBusyKey.value = 'memory-clear'
  memoryError.value = ''
  try {
    await clearMemories()
    memories.value = []
    selectedMemoryId.value = null
    memoryEditorMode.value = null
  } catch (error) {
    memoryError.value = error instanceof Error ? error.message : String(error)
    requestLogs.value.unshift({ method: 'DELETE', path: '/api/user/memories', status: 'ERR', time: 0, body: memoryError.value })
  } finally {
    submitBusyKey.value = ''
  }
}

function marketCategoryKey(category?: string) {
  return category || 'uncategorized'
}

function marketCategoryLabel(category: string) {
  const labels: Record<string, string> = {
    all: '全部',
    uncategorized: '未分类',
    general: '通用',
    writing: '写作',
    data: '数据',
    file: '文件',
    research: '研究',
    development: '开发',
    search: '搜索',
    productivity: '效率',
    automation: '自动化'
  }
  return labels[category] || category
}

const visibleMarketItems = computed(() => {
  const items = marketItems.value[marketTab.value]
  if (marketCategory.value === 'all') return items
  return items.filter(item => marketCategoryKey(item.category) === marketCategory.value)
})
const marketCategories = computed(() => {
  const values = new Set(marketItems.value[marketTab.value].map(item => marketCategoryKey(item.category)))
  return ['all', ...Array.from(values).sort()]
})
function marketCategoryCount(category: string) {
  if (category === 'all') return marketItems.value[marketTab.value].length
  return marketItems.value[marketTab.value].filter(item => marketCategoryKey(item.category) === category).length
}
const activeExpert = computed(() => expertItems.value.find(item => item.id === agentId.value))
const activeTeam = computed(() => teamItems.value.find(item => item.agent_id === agentId.value))
const activeConfigurationName = computed(() => activeTeam.value ? `专家团 · ${activeTeam.value.name || `#${activeTeam.value.id}`}` : activeExpert.value?.name || '默认 Super Agent')
const handoffTeamActive = computed(() => Boolean(activeTeam.value && /handoff|dynamic/i.test(activeTeam.value.strategy || '')))
const modeConfigurations = computed(() => chatMode.value === 'expert'
  ? expertItems.value.map(expert => ({ value: `agent:${expert.id}`, label: expert.name }))
  : teamItems.value.map(team => ({ value: `team:${team.id}`, label: team.name || `专家团 #${team.id}` })))

function sessionRow(session: { id: string; title: string; message_count: number; last_active_at: string; project_id?: number | null; agent_id?: number | null; model?: string | null }) {
  return {
    id: session.id,
    title: session.title || '未命名会话',
    meta: `${session.message_count} 条消息 · ${new Date(session.last_active_at).toLocaleString()}`,
    project_id: session.project_id,
    agent_id: session.agent_id,
    model: session.model
  }
}

async function refreshSessionList() {
  const result = await listSessions()
  conversations.value = (result.sessions || []).map(sessionRow)
}

function upsertConversation(sessionId: string, projectId = selectedProjectId.value || null) {
  const existing = conversations.value.find(item => item.id === sessionId)
  if (existing) return existing
  const item = { id: sessionId, title: '新对话', meta: '正在同步…', project_id: projectId, agent_id: agentId.value || null, model: modelDraft.value }
  conversations.value.unshift(item)
  return item
}

async function loadWorkspace() {
  const [sessionResult, agentResult, teamResult, knowledgeResult, projectResult] = await Promise.allSettled([listSessions(), listAgents(), listTeams(), listKnowledgeBases(), listProjects()])
  try {
    if (agentResult.status === 'fulfilled') {
      const agents = Array.isArray(agentResult.value) ? agentResult.value : agentResult.value.items || []
      expertItems.value = agents
      if (agents[0]) {
        modelDraft.value = agents[0].model || defaultChatModel.value
        teamForm.value.agent_id = agents[0].id
      }
    }
    if (teamResult.status === 'fulfilled') teamItems.value = teamResult.value.items || []
    if (knowledgeResult.status === 'fulfilled') knowledgeBases.value = knowledgeResult.value.knowledge_bases || []
    if (projectResult.status === 'fulfilled') projects.value = projectResult.value.projects || []
    if (sessionResult.status === 'fulfilled') {
      const sessions = sessionResult.value.sessions || []
      if (sessions.length) {
        const firstSession = sessions[0]!
        conversations.value = sessions.map(sessionRow)
        selectedConversation.value = firstSession.id
        await openSession(firstSession.id)
      }
    }
    if (currentSessionId.value) {
      const current = conversations.value.find(item => item.id === currentSessionId.value)
      agentId.value = current?.agent_id || 0
      configurationSelection.value = current?.agent_id ? `agent:${current.agent_id}` : 'default'
    }
    await loadMarkets()
  } catch (error) {
    requestLogs.value.unshift({ method: 'GET', path: '/api/sessions', status: 'ERR', time: 0, body: error instanceof Error ? error.message : String(error) })
  }
}

// 创建/编辑共用一个弹窗，所以"打开新建"必须显式清掉编辑态 —— 否则上次编辑完
// 留下的 editingExpertId 会让"创建"变成又改一遍那个专家。跟知识库那边同一个套路。
function openCreateExpertForm() {
  editingExpertId.value = null
  expertForm.value = { name: '', description: '', category: 'general', soul_prompt: '', model: defaultChatModel.value, skill_ids: [], mcp_ids: [], tool_ids: [], kb_ids: [] }
  expertFormOpen.value = true
}

function closeExpertForm() {
  expertFormOpen.value = false
  editingExpertId.value = null
  expertForm.value = { name: '', description: '', category: 'general', soul_prompt: '', model: defaultChatModel.value, skill_ids: [], mcp_ids: [], tool_ids: [], kb_ids: [] }
}

async function createMarketExpert() {
  if (!expertForm.value.name.trim() || submitBusyKey.value) return
  submitBusyKey.value = 'expert-save'
  try {
    const { skill_ids, mcp_ids, tool_ids, kb_ids, ...agentPayload } = expertForm.value
    const editingExpert = editingExpertId.value
      ? expertItems.value.find(e => e.id === editingExpertId.value)
      : null
    const item = editingExpertId.value
      ? await updateAgent(editingExpertId.value, { ...agentPayload, name: expertForm.value.name.trim(), expected_updated_at: editingExpert?.updated_at })
      : await createAgent({ ...agentPayload, name: expertForm.value.name.trim() })
    const old = expertBindings.value[item.id] || { skills: [], mcp_servers: [], tools: [], knowledge_bases: [] }
    const wanted = {
      skills: new Set(skill_ids),
      mcp_servers: new Set(mcp_ids),
      tools: new Set(tool_ids),
      knowledge_bases: new Set(kb_ids)
    }
    await Promise.all([
      ...skill_ids.filter(id => !old.skills.some(binding => binding.skill_id === id)).map(id => bindAgentSkill(item.id, id)),
      ...mcp_ids.filter(id => !old.mcp_servers.some(binding => binding.mcp_server_id === id)).map(id => bindAgentMcp(item.id, id)),
      ...tool_ids.filter(id => !old.tools.some(binding => binding.tool_id === id)).map(id => bindAgentTool(item.id, id)),
      ...kb_ids.filter(id => !old.knowledge_bases.some(binding => binding.kb_id === id)).map(id => bindAgentKnowledgeBase(item.id, id)),
      ...old.skills.filter(binding => !wanted.skills.has(binding.skill_id)).map(binding => unbindAgentSkill(item.id, binding.skill_id)),
      ...old.mcp_servers.filter(binding => !wanted.mcp_servers.has(binding.mcp_server_id)).map(binding => unbindAgentMcp(item.id, binding.mcp_server_id)),
      ...old.tools.filter(binding => !wanted.tools.has(binding.tool_id)).map(binding => unbindAgentTool(item.id, binding.tool_id)),
      ...old.knowledge_bases.filter(binding => !wanted.knowledge_bases.has(binding.kb_id)).map(binding => unbindAgentKnowledgeBase(item.id, binding.kb_id))
    ])
    const index = expertItems.value.findIndex(expert => expert.id === item.id)
    if (index >= 0) expertItems.value[index] = item
    else expertItems.value.unshift(item)
    closeExpertForm()
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) {
      connectionError.value = error.message || '该专家已被修改，请刷新后重试'
    } else {
      requestLogs.value.unshift({ method: 'POST', path: '/api/user/agents', status: 'ERR', time: 0, body: error instanceof Error ? error.message : String(error) })
    }
  } finally {
    submitBusyKey.value = ''
  }
}

// 先开窗、再拉绑定。以前是拉成功才开窗，绑定接口一挂就整个点击没反应——用户看到的
// 是"编辑按钮点了没用"，而错误只进了右侧请求日志。
async function editExpert(expert: AgentInfo) {
  editingExpertId.value = expert.id
  expertForm.value = {
    name: expert.name,
    description: expert.description || '',
    category: expert.category || 'general',
    soul_prompt: expert.soul_prompt || '',
    model: expert.model || defaultChatModel.value,
    skill_ids: [], mcp_ids: [], tool_ids: [], kb_ids: []
  }
  expertFormOpen.value = true
  try {
    const bindings = await listAgentBindings(expert.id)
    // 陈旧响应丢弃：这个请求发出去之后，用户可能已经点开了另一个专家的编辑
    // 面板——两个 listAgentBindings 请求同时在途，网络顺序不保证跟点击顺序
    // 一致。如果不判断，后到达的响应会不看是谁发的、直接覆盖 expertForm，
    // 而 editingExpertId 早已经指向另一个专家：用户看着"专家 B"的名字，
    // 保存时却把"专家 A"的技能/工具/知识库绑定提交上去——不是显示错乱，
    // 是会把 A 的绑定写到 B 身上、冲掉 B 原有的绑定，一次真实的数据损坏。
    if (editingExpertId.value !== expert.id) return
    expertBindings.value[expert.id] = bindings
    expertForm.value.skill_ids = bindings.skills.map(item => item.skill_id)
    expertForm.value.mcp_ids = bindings.mcp_servers.map(item => item.mcp_server_id)
    expertForm.value.tool_ids = bindings.tools.map(item => item.tool_id)
    expertForm.value.kb_ids = bindings.knowledge_bases.map(item => item.kb_id)
  } catch (error) {
    requestLogs.value.unshift({ method: 'GET', path: `/api/user/agents/${expert.id}/bindings`, status: 'ERR', time: 0, body: error instanceof Error ? error.message : String(error) })
  }
}

async function removeExpert(expert: AgentInfo) {
  if (!window.confirm(`删除专家「${expert.name}」？已绑定的资源不会被删除。`)) return
  if (submitBusyKey.value) return
  submitBusyKey.value = 'expert-delete'
  try {
    await deleteAgent(expert.id)
    expertItems.value = expertItems.value.filter(item => item.id !== expert.id)
    if (agentId.value === expert.id) await selectChatMode('default')
  } catch (error) {
    requestLogs.value.unshift({ method: 'DELETE', path: `/api/user/agents/${expert.id}`, status: 'ERR', time: 0, body: error instanceof Error ? error.message : String(error) })
  } finally {
    submitBusyKey.value = ''
  }
}

// ── 专家共享 ──────────────────────────────────────────────
//
// 共享是把自己的专家升到"全租户可用"。它带一层真实的**泄露风险**：专家身上绑的
// 私有工具/技能/MCP server 会跟着一起下发给所有使用者，所以后端有一张硬拦清单。
//
// 界面做成"先预检、再动手"而不是"点了再说"：后端专门给了只读的 share-check 就是
// 为这个。直接 POST 的话用户拿到的是一句"有 3 项没共享"，而他真正需要知道的是
// 哪 3 项 —— 那才是他能拿来行动的东西。

// ── 提交守卫：防止慢接口期间重复点击建出多份数据 ──────────────────
//
// 实测踩过：建知识库的接口要 5~6 秒，用户以为没反应连点了几下，就真的建出了
// 几个同名知识库——POST 本身不幂等，前端不挡就没人挡。
//
// 用一个"当前正在提交哪个动作"的键，而不是给每个函数各加一个 ref：这类提交
// 函数有二十多个（建/改/删 专家、专家团、知识库、MCP、工具、项目…），各写一个
// 标志位必然漏几个，而漏掉的那个要等用户点出脏数据才会被发现。
//
// 每个提交函数三步：
//
//	if (submitBusyKey.value) return        ← 已在提交中，丢弃这次点击
//	submitBusyKey.value = '<动作名>'        ← 占位，顺便给按钮做 :loading
//	... finally { submitBusyKey.value = '' } ← 必定清空，失败也不卡死按钮
//
// 丢弃而不是排队：用户连点的第二下想要的不是"再建一个"，是"你倒是有点反应"。
const submitBusyKey = ref('')

const sharePanelExpert = ref<AgentInfo | null>(null)
const shareCheck = ref<ShareCheck | null>(null)
const shareBusy = ref(false)
const shareNotice = ref('')
// 共享成功后的软提示（私有知识库那类）。跟 shareCheck 分开存，因为面板这时候
// 已经翻篇了，而这批提示仍然要留在屏幕上。
const shareWarnings = ref<ShareBlocker[]>([])

// 共享/撤回之后重拉一次专家列表：归属档变了，is_mine 和可见范围都跟着变，
// 不重拉的话卡片上的按钮还是旧状态。
// listAgents 有两种返回形状（裸数组 / {items}），跟 loadWorkspace 里的处理保持一致。
async function refreshExperts() {
  const result = await listAgents()
  expertItems.value = Array.isArray(result) ? result : result.items || []
}

async function openSharePanel(expert: AgentInfo) {
  sharePanelExpert.value = expert
  shareCheck.value = null
  shareNotice.value = ''
  shareWarnings.value = []
  shareBusy.value = true
  try {
    const result = await checkAgentShare(expert.id)
    // 陈旧响应丢弃：同一个原因，见 editExpert 的说明——这里严重度低一些，
    // 因为真正共享时后端会重新校验 blockers，不会因为前端预检显示错了就
    // 放过一次不该共享的 agent；但用户看着"专家 B 的面板"却读到"专家 A 的
    // 阻塞项清单"，仍然是个会误导判断的真实 bug。
    if (sharePanelExpert.value !== expert) return
    shareCheck.value = result
  } catch (error) {
    shareNotice.value = error instanceof Error ? error.message : String(error)
  } finally {
    shareBusy.value = false
  }
}

async function confirmShare() {
  const expert = sharePanelExpert.value
  if (!expert) return
  shareBusy.value = true
  shareNotice.value = ''
  try {
    const result = await shareAgent(expert.id)
    // warnings 成功时也返回，得显示出来 —— 只在失败路径上显示它，等于它永远
    // 不会被看到（失败的时候用户满脑子是 blockers）。
    shareWarnings.value = result.warnings || []
    shareNotice.value = `「${expert.name}」已共享到租户。`
    sharePanelExpert.value = null
    await refreshExperts()
  } catch (error) {
    // 409 的 blockers 清单在 detail 里。预检之后还撞上，说明预检和提交之间
    // 资源状态变了（别人把某个工具改回私有），所以照样要把清单摊开。
    if (error instanceof ApiError && error.status === 409) {
      const detail = error.detail as { blockers?: ShareBlocker[]; warnings?: ShareBlocker[] } | null
      if (detail?.blockers && shareCheck.value) {
        shareCheck.value = { ...shareCheck.value, can_share: false, blockers: detail.blockers, warnings: detail.warnings || [] }
      }
    }
    shareNotice.value = error instanceof Error ? error.message : String(error)
  } finally {
    shareBusy.value = false
  }
}

async function confirmUnshare() {
  const expert = sharePanelExpert.value
  if (!expert) return
  if (!window.confirm(`撤回「${expert.name}」的共享？租户里其他人将不能再用它。`)) return
  shareBusy.value = true
  shareNotice.value = ''
  try {
    await unshareAgent(expert.id)
    shareNotice.value = `「${expert.name}」已撤回共享。`
    sharePanelExpert.value = null
    await refreshExperts()
  } catch (error) {
    shareNotice.value = error instanceof Error ? error.message : String(error)
  } finally {
    shareBusy.value = false
  }
}

// 成员增删都作用在"弹窗里正在编辑的这个团"，也就是 editingTeamId，而不是聊天选中的
// selectedTeamId。两者同名很久，导致成员加到了另一个团上这种错位。
async function saveTeamMember() {
  if (!editingTeamId.value || !teamMemberForm.value.member_agent_id || submitBusyKey.value) return
  submitBusyKey.value = 'team-member-add'
  try {
    const item = await addTeamMember(editingTeamId.value, teamMemberForm.value)
    teamMembers.value.push(item)
    // 表单里那份 member_ids 也要跟上：它是保存整个团时的期望成员集，落后的话
    // 紧接着点"保存"会把刚加的人当成"多出来的"再删掉。
    if (!teamForm.value.member_ids.includes(item.member_agent_id)) teamForm.value.member_ids.push(item.member_agent_id)
    teamMemberForm.value = { member_agent_id: 0, role: 'worker', name: '' }
  } catch (error) {
    requestLogs.value.unshift({ method: 'POST', path: `/api/user/teams/${editingTeamId.value}/members`, status: 'ERR', time: 0, body: error instanceof Error ? error.message : String(error) })
  } finally {
    submitBusyKey.value = ''
  }
}

async function deleteTeamMember(member: TeamMember) {
  if (!editingTeamId.value || submitBusyKey.value) return
  submitBusyKey.value = 'team-member-delete'
  try {
    await removeTeamMember(editingTeamId.value, member.id)
    teamMembers.value = teamMembers.value.filter(item => item.id !== member.id)
    teamForm.value.member_ids = teamForm.value.member_ids.filter(id => id !== member.member_agent_id)
  } catch (error) {
    requestLogs.value.unshift({ method: 'DELETE', path: `/api/user/teams/${editingTeamId.value}/members/${member.id}`, status: 'ERR', time: 0, body: error instanceof Error ? error.message : String(error) })
  } finally {
    submitBusyKey.value = ''
  }
}

function useExpert(expert: AgentInfo) {
  void startWithConfiguration(expert)
}

function useTeam(team: TeamInfo) {
  void startWithConfiguration(team)
}

// startWithConfiguration 只是选定"下一条消息用哪个 agent/团队"，同样不碰
// 后端——原因跟 newConversation 一样：POST /api/sessions 建一个新会话要串行
// 打两次 lea-config 的网络请求，不是"内存操作所以很便宜"。真正建会话推迟到
// 发第一条消息（session_id 传空，后端顺手建）。
function startWithConfiguration(configuration: AgentInfo | TeamInfo) {
  detachCurrentStream()
  const nextAgentId = 'agent_id' in configuration ? configuration.agent_id : configuration.id
  agentId.value = nextAgentId
  selectedTeamId.value = 'agent_id' in configuration ? configuration.id : null
  modelDraft.value = expertItems.value.find(expert => expert.id === nextAgentId)?.model || defaultChatModel.value
  configurationSelection.value = 'agent_id' in configuration ? `team:${configuration.id}` : `agent:${configuration.id}`
  currentSessionId.value = ''
  selectedConversation.value = ''
  messages.value = []
  sessionAttachments.value = []
  pendingAttachments.value = []
  attachmentIds.value = []
  attachmentFiles.value = []
  pendingLibraryImports.value = []
  files.value = []
  selectedFile.value = undefined
  clearFilePreview()
  activeNav.value = 'chat'
}

// 选择专家时只更新本地预选。真正写 active_agent_id 放到发送前：一是符合客户端
// "选配置 -> 发送后生效" 的心智，二是刚建、尚未发送过消息的 session 还没有持久化
// 行，提前 PUT /active-agent 必然会收到 session not found。
function previewConfiguration(agent: AgentInfo | TeamInfo | null) {
  const nextAgentId = agent && 'agent_id' in agent ? agent.agent_id : agent?.id || 0
  agentId.value = nextAgentId
  modelDraft.value = expertItems.value.find(expert => expert.id === nextAgentId)?.model || defaultChatModel.value
  selectedTeamId.value = agent && 'agent_id' in agent ? agent.id : null
  chatMode.value = agent && 'agent_id' in agent ? 'team' : agent ? 'expert' : 'default'
}

async function selectChatMode(mode: ChatMode) {
  if (mode === 'plan' && handoffTeamActive.value) return
  chatMode.value = mode
  if (mode === 'expert' || mode === 'team') {
    if (mode === 'expert') {
      const result = await listAgents()
      expertItems.value = Array.isArray(result) ? result : result.items || []
    }
    if (mode === 'team') teamItems.value = (await listTeams()).items || []
    const prefix = mode === 'expert' ? 'agent:' : 'team:'
    if (!configurationSelection.value.startsWith(prefix)) configurationSelection.value = modeConfigurations.value[0]?.value || ''
    if (!configurationSelection.value) {
      connectionError.value = mode === 'expert' ? '当前租户还没有可用专家' : '当前租户还没有可用专家团'
      return
    }
    applySelectedConfiguration()
    return
  }
  agentId.value = 0
  selectedTeamId.value = null
  configurationSelection.value = 'default'
}

function applySelectedConfiguration() {
  const [kind, rawId] = configurationSelection.value.split(':')
  const id = Number(rawId)
  const configuration = kind === 'team'
    ? teamItems.value.find(team => team.id === id)
    : expertItems.value.find(expert => expert.id === id)
  if (configuration) previewConfiguration(configuration)
}

function chatModeLabel(mode: ChatMode) {
  return ({ default: '默认', plan: 'Plan', expert: '专家', team: '专家团' })[mode]
}

async function createMarketTeam() {
  if (!teamForm.value.agent_id || submitBusyKey.value) return
  submitBusyKey.value = 'team-save'
  try {
    const { member_ids, ...teamPayload } = teamForm.value
    const editingTeam = editingTeamId.value
      ? teamItems.value.find(t => t.id === editingTeamId.value)
      : null
    const item = editingTeamId.value
      ? await updateTeam(editingTeamId.value, { ...teamPayload, expected_updated_at: editingTeam?.updated_at })
      : await createTeam(teamPayload)
    if (editingTeamId.value) {
      const currentMembers = (await listTeamMembers(item.id)).items || []
      await Promise.all(currentMembers.filter(member => !member_ids.includes(member.member_agent_id)).map(member => removeTeamMember(item.id, member.id)))
      await Promise.all(member_ids.filter(id => !currentMembers.some(member => member.member_agent_id === id)).map(id => addTeamMember(item.id, { member_agent_id: id, role: 'worker', name: '' })))
      const index = teamItems.value.findIndex(team => team.id === item.id)
      if (index >= 0) teamItems.value[index] = { ...teamItems.value[index], ...item }
    } else {
      await Promise.all(member_ids.map(memberAgentId => addTeamMember(item.id, { member_agent_id: memberAgentId, role: 'worker', name: '' })))
      teamItems.value.unshift(item)
    }
    closeTeamForm()
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) {
      connectionError.value = error.message || '该专家团已被修改，请刷新后重试'
    } else {
      requestLogs.value.unshift({ method: 'POST', path: '/api/user/teams', status: 'ERR', time: 0, body: error instanceof Error ? error.message : String(error) })
    }
  } finally {
    submitBusyKey.value = ''
  }
}

function openCreateTeamForm() {
  editingTeamId.value = null
  teamMembers.value = []
  teamMemberForm.value = { member_agent_id: 0, role: 'worker', name: '' }
  teamForm.value = { agent_id: agentId.value || expertItems.value[0]?.id || 0, strategy: 'star', decomposer_prompt: '', synthesizer_prompt: '', max_subtasks: 5, member_ids: [] }
  teamFormOpen.value = true
}

function closeTeamForm() {
  teamFormOpen.value = false
  editingTeamId.value = null
  teamMembers.value = []
  teamMemberForm.value = { member_agent_id: 0, role: 'worker', name: '' }
  teamForm.value = { agent_id: agentId.value || expertItems.value[0]?.id || 0, strategy: 'star', decomposer_prompt: '', synthesizer_prompt: '', max_subtasks: 5, member_ids: [] }
}

// 点专家团卡片 = 进编辑态：团的参数和成员名单在同一个弹窗里，不再是"编辑"和
// "成员配置"两个各自弹出来的面板。成员拉失败要留痕，但不能挡住弹窗打开 ——
// 否则又回到"点进去什么都没有"那个体验，只是这次连参数都看不到。
async function openTeamForm(team: TeamInfo) {
  editingTeamId.value = team.id
  teamMemberForm.value = { member_agent_id: 0, role: 'worker', name: '' }
  teamMembers.value = []
  teamForm.value = {
    agent_id: team.agent_id,
    strategy: team.strategy || 'star',
    decomposer_prompt: team.decomposer_prompt || '',
    synthesizer_prompt: team.synthesizer_prompt || '',
    max_subtasks: team.max_subtasks || 5,
    member_ids: []
  }
  teamFormOpen.value = true
  try {
    const members = (await listTeamMembers(team.id)).items || []
    // 陈旧响应丢弃：同一个原因，见 editExpert 里的说明——这里更要紧，因为
    // saveTeamMember/createMarketTeam 会拿 member_ids 跟旧绑定做 diff 去调
    // addTeamMember/removeTeamMember，陈旧数据一旦被当成"这个团现在该有的
    // 成员名单"，保存时会把别的团的成员错误地加/删到这个团上。
    if (editingTeamId.value !== team.id) return
    teamMembers.value = members
    teamForm.value.member_ids = members.map(member => member.member_agent_id)
  } catch (error) {
    requestLogs.value.unshift({ method: 'GET', path: `/api/user/teams/${team.id}/members`, status: 'ERR', time: 0, body: error instanceof Error ? error.message : String(error) })
  }
}

async function removeExpertTeam(team: TeamInfo) {
  if (!window.confirm(`删除专家团「${team.name || `#${team.id}`}」？成员专家不会被删除。`)) return
  if (submitBusyKey.value) return
  submitBusyKey.value = 'team-delete'
  try {
    await deleteTeam(team.id)
    teamItems.value = teamItems.value.filter(item => item.id !== team.id)
    if (selectedTeamId.value === team.id) selectedTeamId.value = null
    if (editingTeamId.value === team.id) closeTeamForm()
  } catch (error) {
    requestLogs.value.unshift({ method: 'DELETE', path: `/api/user/teams/${team.id}`, status: 'ERR', time: 0, body: error instanceof Error ? error.message : String(error) })
  } finally {
    submitBusyKey.value = ''
  }
}

// 点知识库卡片——查看/编辑/传文档合并成一个弹窗，不再单独放一个"编辑"按钮：
// 卡片本身就是入口，弹窗里同时能改参数、看文档、传新文档、测检索。
async function openKnowledgeBase(item: KnowledgeBaseItem) {
  selectedKnowledgeBase.value = item
  editingKnowledgeBaseId.value = item.id
  knowledgeForm.value = {
    name: item.name,
    description: item.description || '',
    visibility: item.visibility || 'private',
    embedding_model: item.embedding_model || '',
    reranker_model: item.reranker_model || '',
    reranker_enabled: item.reranker_enabled ?? true,
    vlm_model: item.vlm_model || '',
    vlm_enabled: item.vlm_enabled ?? true,
    multimodal: item.multimodal ?? false
  }
  knowledgeSearchQuery.value = ''
  knowledgeSearchResults.value = []
  knowledgeFormOpen.value = true
  try {
    const result = await listKnowledgeDocuments(item.id)
    knowledgeDocuments.value = result.documents || []
  } catch (error) {
    requestLogs.value.unshift({ method: 'GET', path: `/api/user/knowledge-bases/${item.id}/documents`, status: 'ERR', time: 0, body: error instanceof Error ? error.message : String(error) })
  }
}

// 工具栏"创建知识库"按钮的入口——跟点卡片走同一个弹窗，只是表单清空、没有
// document_count 门槛，也没有文档区（还没有 kb_id，传不了文件）。
function openCreateKnowledgeBaseForm() {
  editingKnowledgeBaseId.value = null
  selectedKnowledgeBase.value = null
  knowledgeDocuments.value = []
  knowledgeSearchQuery.value = ''
  knowledgeSearchResults.value = []
  knowledgeForm.value = {
    name: '', description: '', visibility: 'private',
    embedding_model: '', reranker_model: '', reranker_enabled: true,
    vlm_model: '', vlm_enabled: true, multimodal: false
  }
  knowledgeFormOpen.value = true
}

// 关弹窗（保存成功、点取消、点遮罩/Esc 都走这里）——统一清掉编辑态，免得下次
// 打开时残留上一个知识库的表单值或文档列表。
function closeKnowledgeBaseForm() {
  knowledgeFormOpen.value = false
  editingKnowledgeBaseId.value = null
  selectedKnowledgeBase.value = null
  knowledgeDocuments.value = []
  knowledgeSearchResults.value = []
}

async function createMarketKnowledgeBase() {
  if (!knowledgeForm.value.name.trim() || submitBusyKey.value) return
  submitBusyKey.value = 'kb-save'
  try {
    const { embedding_model, ...rest } = knowledgeForm.value
    const payload: Record<string, unknown> = { ...rest, name: knowledgeForm.value.name.trim() }
    // embedding_model 只在新建、或编辑一个还没有文档的库时带上——已有文档的库带
    // 上这个字段会被后端显式 400（见 server_user_kb.py），不是"传了也没用"会被
    // 静默忽略，所以这里干脆不传，别让用户点了保存才看见一条陌生的报错。
    if (!editingKnowledgeBaseId.value || editingKnowledgeBaseDocCount.value === 0) {
      payload.embedding_model = embedding_model
    }
    if (editingKnowledgeBaseId.value) {
      const item = await updateKnowledgeBase(editingKnowledgeBaseId.value, { ...payload, expected_updated_at: selectedKnowledgeBase.value?.updated_at })
      const index = knowledgeBases.value.findIndex(current => current.id === item.id)
      if (index >= 0) knowledgeBases.value[index] = { ...knowledgeBases.value[index], ...item }
    } else {
      const item = await createKnowledgeBase(payload as Parameters<typeof createKnowledgeBase>[0])
      knowledgeBases.value.unshift(item)
    }
    closeKnowledgeBaseForm()
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) {
      connectionError.value = error.message || '该知识库已被修改，请刷新后重试'
    } else {
      requestLogs.value.unshift({ method: editingKnowledgeBaseId.value ? 'PUT' : 'POST', path: '/api/user/knowledge-bases', status: 'ERR', time: 0, body: error instanceof Error ? error.message : String(error) })
    }
  } finally {
    submitBusyKey.value = ''
  }
}

async function removeKnowledgeBase(item: KnowledgeBaseItem) {
  if (!window.confirm(`删除知识库「${item.name}」？其中的文档也会被移除。`)) return
  if (submitBusyKey.value) return
  submitBusyKey.value = 'kb-delete'
  try {
    await deleteKnowledgeBase(item.id)
    knowledgeBases.value = knowledgeBases.value.filter(current => current.id !== item.id)
    if (selectedKnowledgeBase.value?.id === item.id) closeKnowledgeBaseForm()
  } catch (error) {
    requestLogs.value.unshift({ method: 'DELETE', path: `/api/user/knowledge-bases/${item.id}`, status: 'ERR', time: 0, body: error instanceof Error ? error.message : String(error) })
  } finally {
    submitBusyKey.value = ''
  }
}

async function searchSelectedKnowledgeBase() {
  if (!selectedKnowledgeBase.value || !knowledgeSearchQuery.value.trim()) return
  knowledgeSearchLoading.value = true
  try {
    const result = await searchKnowledgeBase(selectedKnowledgeBase.value.id, knowledgeSearchQuery.value.trim())
    knowledgeSearchResults.value = result.results || []
  } catch (error) {
    requestLogs.value.unshift({ method: 'POST', path: `/api/user/knowledge-bases/${selectedKnowledgeBase.value.id}/search`, status: 'ERR', time: 0, body: error instanceof Error ? error.message : String(error) })
  } finally {
    knowledgeSearchLoading.value = false
  }
}

async function uploadKnowledgeFile(options: { file: UploadFileInfo }) {
  if (!options.file.file || !selectedKnowledgeBase.value) return
  try {
    const item = await uploadKnowledgeDocument(selectedKnowledgeBase.value.id, options.file.file)
    knowledgeDocuments.value.unshift(item)
  } catch (error) {
    requestLogs.value.unshift({ method: 'POST', path: `/api/user/knowledge-bases/${selectedKnowledgeBase.value.id}/documents`, status: 'ERR', time: 0, body: error instanceof Error ? error.message : String(error) })
  }
}

async function removeKnowledgeDocument(document: KnowledgeDocument) {
  if (!selectedKnowledgeBase.value || submitBusyKey.value) return
  submitBusyKey.value = 'kb-document-delete'
  try {
    await deleteKnowledgeDocument(selectedKnowledgeBase.value.id, document.id)
    knowledgeDocuments.value = knowledgeDocuments.value.filter(item => item.id !== document.id)
  } catch (error) {
    requestLogs.value.unshift({ method: 'DELETE', path: `/api/user/knowledge-bases/${selectedKnowledgeBase.value.id}/documents/${document.id}`, status: 'ERR', time: 0, body: error instanceof Error ? error.message : String(error) })
  } finally {
    submitBusyKey.value = ''
  }
}

async function loadMarkets() {
  marketLoading.value = true
  marketError.value = ''
  const [skills, mcp, tools] = await Promise.allSettled([listSkills(), listMcpServers(), listTools()])
  if (skills.status === 'fulfilled') marketItems.value.skills = skills.value.skills || []
  if (mcp.status === 'fulfilled') marketItems.value.mcp = mcp.value.items || []
  if (tools.status === 'fulfilled') marketItems.value.tools = tools.value.items || []
  const failures = [skills, mcp, tools].filter(result => result.status === 'rejected')
  if (failures.length) marketError.value = '部分市场接口不可用，请检查登录状态或服务配置。'
  marketLoading.value = false
}

function marketItemTitle(item: SkillMarketItem | McpMarketItem | ToolMarketItem) {
  return ('display_name' in item && item.display_name) || item.name
}

function marketItemDescription(item: SkillMarketItem | McpMarketItem | ToolMarketItem) {
  return item.description || '暂无描述'
}

function clearFilePreview() {
  if (filePreviewUrl.value) URL.revokeObjectURL(filePreviewUrl.value)
  filePreviewUrl.value = ''
  filePreviewText.value = ''
  filePreviewKind.value = 'unsupported'
}

async function selectWorkspaceFile(file: AssetFile) {
  selectedFile.value = file
  clearFilePreview()
  if (!currentSessionId.value || !file.path) return
  filePreviewLoading.value = true
  try {
    const { blob, contentType } = await downloadWorkspaceFile(currentSessionId.value, file.path)
    // 陈旧响应丢弃：用户点了文件 A 又很快点了文件 B，两次下载同时在途，
    // 网络顺序不保证跟点击顺序一致。不判断的话，后到达的响应会不管是谁的
    // 直接覆盖 filePreviewUrl/Text/Kind——用户看着"当前选中 = B"（文件名/
    // 路径都显示 B），预览区却是 A 的内容，会被当成"这就是 B 的内容"。
    // 这个判断顺带堵住一处内存泄漏：原来的代码会用陈旧响应的 URL 覆盖掉
    // 正确响应刚创建的 URL，被覆盖掉的那个引用就此丢失、永远没人 revoke——
    // 现在陈旧响应在这里直接返回，根本碰不到 filePreviewUrl，不会覆盖。
    if (selectedFile.value !== file) return
    if (contentType.startsWith('image/')) {
      filePreviewUrl.value = URL.createObjectURL(blob)
      filePreviewKind.value = 'image'
    } else if (contentType === 'application/pdf' || /\.pdf$/i.test(file.name)) {
      // PDF 甩给浏览器自带的查看器，不用装 pdf.js——Chrome/Edge/Firefox 都内置
      // 了 PDF 渲染能力，iframe 指向一个 blob URL 就够，跟装专门的渲染库比
      // 零依赖、零维护成本。
      filePreviewUrl.value = URL.createObjectURL(blob)
      filePreviewKind.value = 'pdf'
    } else if (contentType === 'text/html' || /\.html?$/i.test(file.name)) {
      // 同样甩给浏览器原生能力，但 HTML 不能像 PDF 那样直接扔 iframe src——
      // sandbox 属性只在**同源 srcdoc 内容**上才生效为"完全隔离"（跨源资源
      // iframe 即使不加 sandbox 也已经有一层同源隔离，但那不是我们能控制的
      // 安全边界）。走 srcdoc + sandbox="allow-scripts" 而不给 allow-same-origin：
      // 这样里面的脚本能跑（模型生成的 HTML demo 经常带交互脚本），但拿不到
      // 这个页面的 cookie/localStorage/token，看到的是一个空的匿名源，
      // 不会变成一个能读用户会话状态的通道。
      filePreviewText.value = await blob.text()
      filePreviewKind.value = 'html'
    } else if (contentType.startsWith('text/') || /\.(md|txt|json|csv|log|py|js|ts|vue)$/i.test(file.name)) {
      filePreviewText.value = await blob.text()
      filePreviewKind.value = 'text'
    }
  } catch (error) {
    requestLogs.value.unshift({ method: 'GET', path: `/api/files/download?path=${file.path}`, status: 'ERR', time: 0, body: error instanceof Error ? error.message : String(error) })
  } finally {
    filePreviewLoading.value = false
  }
}

async function downloadSelectedFile() {
  if (!currentSessionId.value || !selectedFile.value?.path) return
  try {
    const { blob } = await downloadWorkspaceFile(currentSessionId.value, selectedFile.value.path)
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = selectedFile.value.name
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    requestLogs.value.unshift({ method: 'GET', path: `/api/files/download?path=${selectedFile.value.path}`, status: 'ERR', time: 0, body: error instanceof Error ? error.message : String(error) })
  }
}

async function saveSelectedWorkspaceFile() {
  if (!currentSessionId.value || !selectedFile.value || workspacePublishLoading.value) return
  workspacePublishLoading.value = true
  workspaceError.value = ''
  try {
    await publishWorkspaceFile(currentSessionId.value, selectedFile.value.path, selectedFile.value.name)
    inspectorTab.value = 'library'
    await loadUserLibrary()
  } catch (error) {
    workspaceError.value = error instanceof Error ? error.message : String(error)
  } finally {
    workspacePublishLoading.value = false
  }
}

async function toggleMarketMcp(item: McpMarketItem) {
  try {
    const updated = await toggleMcpServer(item.id, !item.enabled)
    const index = marketItems.value.mcp.findIndex(server => server.id === item.id)
    if (index >= 0) marketItems.value.mcp[index] = updated
  } catch (error) {
    requestLogs.value.unshift({ method: 'PATCH', path: `/api/user/mcp-servers/${item.id}/enable`, status: 'ERR', time: 0, body: error instanceof Error ? error.message : String(error) })
  }
}

async function syncMarketMcp(item: McpMarketItem) {
  try {
    await syncMcpServer(item.id)
    await loadMarkets()
  } catch (error) {
    requestLogs.value.unshift({ method: 'POST', path: `/api/user/mcp-servers/${item.id}/sync`, status: 'ERR', time: 0, body: error instanceof Error ? error.message : String(error) })
  }
}

async function createMarketMcp() {
  if (!mcpForm.value.name.trim() || !mcpForm.value.endpoint.trim() || submitBusyKey.value) return
  submitBusyKey.value = 'mcp-save'
  try {
    await createMcpServer({
      name: mcpForm.value.name.trim(),
      description: mcpForm.value.description.trim(),
      endpoint: mcpForm.value.endpoint.trim(),
      category: mcpForm.value.category,
      transport: 'streamable_http',
      auth_config: mcpForm.value.bearerToken.trim() ? { type: 'bearer', token: mcpForm.value.bearerToken.trim() } : undefined,
      tool_filter: mcpForm.value.toolFilter.split(',').map(item => item.trim()).filter(Boolean)
    })
    mcpFormOpen.value = false
    mcpForm.value = { name: '', description: '', category: 'general', endpoint: '', bearerToken: '', toolFilter: '' }
    await loadMarkets()
  } catch (error) {
    requestLogs.value.unshift({ method: 'POST', path: '/api/user/mcp-servers/mine', status: 'ERR', time: 0, body: error instanceof Error ? error.message : String(error) })
    inspectorOpen.value = true
    inspectorTab.value = 'debug'
  } finally {
    submitBusyKey.value = ''
  }
}

function selectSkillUploadFile(options: { file: UploadFileInfo }) {
  skillUploadFile.value = options.file.file || null
}

async function submitSkillUpload() {
  if (!skillUploadFile.value || !skillVersion.value.trim() || !skillCategory.value) return
  skillUploadLoading.value = true
  try {
    await uploadSkillBundle(skillUploadFile.value, skillVersion.value.trim(), skillCategory.value, skillChangeLog.value.trim())
    skillUploadOpen.value = false
    skillUploadFile.value = null
    skillChangeLog.value = ''
    await loadMarkets()
  } catch (error) {
    requestLogs.value.unshift({ method: 'POST', path: '/api/user/skills/upload', status: 'ERR', time: 0, body: error instanceof Error ? error.message : String(error) })
  } finally {
    skillUploadLoading.value = false
  }
}

async function createMarketTool() {
  if (!toolForm.value.name.trim() || submitBusyKey.value) return
  submitBusyKey.value = 'tool-save'
  try {
    await createTool({
      name: toolForm.value.name.trim(),
      display_name: toolForm.value.display_name.trim(),
      description: toolForm.value.description.trim(),
      category: toolForm.value.category,
      execution_type: 'api',
      endpoint: toolForm.value.endpoint.trim() || undefined,
      schema_json: JSON.parse(toolForm.value.schema_json)
    })
    toolFormOpen.value = false
    toolForm.value = { name: '', display_name: '', description: '', category: 'general', endpoint: '', schema_json: '{\n  "type": "object",\n  "properties": {}\n}' }
    await loadMarkets()
  } catch (error) {
    requestLogs.value.unshift({ method: 'POST', path: '/api/user/tools', status: 'ERR', time: 0, body: error instanceof Error ? error.message : String(error) })
    inspectorOpen.value = true
    inspectorTab.value = 'debug'
  } finally {
    submitBusyKey.value = ''
  }
}

function marketCanEdit(item: SkillMarketItem | McpMarketItem | ToolMarketItem) {
  if ('can_edit' in item) return Boolean(item.can_edit)
  return 'owner_scope' in item && item.owner_scope === 'user'
}

function editMarketItem(item: SkillMarketItem | McpMarketItem | ToolMarketItem) {
  if (marketTab.value === 'skills') {
    const skill = item as SkillMarketItem
    editingMarketKind.value = 'skill'
    marketEditForm.value = {
      name: skill.name,
      display_name: skill.display_name || skill.name,
      description: skill.description || '',
      category: skill.category || 'general',
      system_prompt: '',
      endpoint: '',
      bearerToken: '',
      toolFilter: '',
      schema_json: marketEditForm.value.schema_json
    }
  } else if (marketTab.value === 'mcp') {
    const mcp = item as McpMarketItem
    editingMarketKind.value = 'mcp'
    marketEditForm.value = {
      name: mcp.name,
      display_name: '',
      description: mcp.description || '',
      category: mcp.category || 'general',
      system_prompt: '',
      endpoint: mcp.endpoint || '',
      bearerToken: '',
      toolFilter: (mcp.tool_filter || []).join(', '),
      schema_json: marketEditForm.value.schema_json
    }
  } else {
    const tool = item as ToolMarketItem
    editingMarketKind.value = 'tool'
    marketEditForm.value = {
      name: tool.name,
      display_name: tool.display_name || tool.name,
      description: tool.description || '',
      category: tool.category || 'general',
      system_prompt: '',
      endpoint: '',
      bearerToken: '',
      toolFilter: '',
      schema_json: '{\n  "type": "object",\n  "properties": {}\n}'
    }
  }
  editingMarketId.value = item.id
  marketEditOpen.value = true
}

async function saveMarketEdit() {
  if (!editingMarketKind.value || !editingMarketId.value || submitBusyKey.value) return
  submitBusyKey.value = 'market-item-save'
  try {
    if (editingMarketKind.value === 'skill') {
      await updateSkill(editingMarketId.value, {
        display_name: marketEditForm.value.display_name.trim(),
        description: marketEditForm.value.description.trim(),
        category: marketEditForm.value.category,
        system_prompt: marketEditForm.value.system_prompt
      })
    } else if (editingMarketKind.value === 'mcp') {
      await updateMcpServer(editingMarketId.value, {
        name: marketEditForm.value.name.trim(),
        description: marketEditForm.value.description.trim(),
        category: marketEditForm.value.category,
        endpoint: marketEditForm.value.endpoint.trim(),
        tool_filter: marketEditForm.value.toolFilter.split(',').map(item => item.trim()).filter(Boolean),
        ...(marketEditForm.value.bearerToken.trim() ? { auth_config: { type: 'bearer', token: marketEditForm.value.bearerToken.trim() } } : {})
      })
    } else {
      await updateTool(editingMarketId.value, {
        display_name: marketEditForm.value.display_name.trim(),
        description: marketEditForm.value.description.trim(),
        category: marketEditForm.value.category,
        endpoint: marketEditForm.value.endpoint.trim() || undefined,
        schema_json: JSON.parse(marketEditForm.value.schema_json)
      })
    }
    marketEditOpen.value = false
    await loadMarkets()
  } catch (error) {
    requestLogs.value.unshift({ method: 'PUT', path: `/api/user/${editingMarketKind.value}`, status: 'ERR', time: 0, body: error instanceof Error ? error.message : String(error) })
  } finally {
    submitBusyKey.value = ''
  }
}

async function removeMarketItem(item: SkillMarketItem | McpMarketItem | ToolMarketItem) {
  const kind = marketTab.value === 'skills' ? 'Skill' : marketTab.value === 'mcp' ? 'MCP 服务' : 'Tool'
  if (!window.confirm(`删除${kind}「${marketItemTitle(item)}」？`)) return
  if (submitBusyKey.value) return
  submitBusyKey.value = 'market-item-delete'
  try {
    if (marketTab.value === 'skills') await deleteSkill(item.id)
    else if (marketTab.value === 'mcp') await deleteMcpServer(item.id)
    else await deleteTool(item.id)
    if (marketTab.value === 'skills') marketItems.value.skills = marketItems.value.skills.filter(current => current.id !== item.id)
    else if (marketTab.value === 'mcp') marketItems.value.mcp = marketItems.value.mcp.filter(current => current.id !== item.id)
    else marketItems.value.tools = marketItems.value.tools.filter(current => current.id !== item.id)
  } catch (error) {
    requestLogs.value.unshift({ method: 'DELETE', path: `/api/user/${marketTab.value}/${item.id}`, status: 'ERR', time: 0, body: error instanceof Error ? error.message : String(error) })
  } finally {
    submitBusyKey.value = ''
  }
}

function openSkillPublish(skill: SkillMarketItem) {
  publishSkillId.value = skill.id
  publishTarget.value = 'tenant'
  publishNote.value = ''
}

async function submitSkillPublish() {
  if (!publishSkillId.value) return
  try {
    await publishSkill(publishSkillId.value, { target_scope: publishTarget.value, note: publishNote.value.trim() })
    publishSkillId.value = null
    await loadMarkets()
  } catch (error) {
    requestLogs.value.unshift({ method: 'POST', path: `/api/user/skills/${publishSkillId.value}/publish`, status: 'ERR', time: 0, body: error instanceof Error ? error.message : String(error) })
  }
}

// ── 上架申请 ──────────────────────────────────────────────
//
// 在此之前 publishSkill 是个黑洞：提交完就没有任何反馈，用户既看不到"在审"
// 也看不到"被驳回了、理由是什么"，只能去问管理员。而审核方更彻底 —— 界面上
// 压根没有待审队列，申请提了没人知道。
//
// 后端是**一条 URL 按角色分流**的（普通用户看自己提的，租户管理员看本租户待审的
// tenant 档，平台管理员看 global 档），所以这里不做任何角色判断 —— 判断放在前端
// 就会跟后端的判据分叉。
const publishRequestsOpen = ref(false)
const publishRequests = ref<PublishRequest[]>([])
const publishRequestsStatus = ref<'pending' | 'approved' | 'rejected'>('pending')
const publishRequestsError = ref('')
const publishReviewNote = ref<Record<number, string>>({})

async function loadPublishRequests() {
  publishRequestsError.value = ''
  try {
    const result = await listPublishRequests(publishRequestsStatus.value)
    publishRequests.value = result.requests || []
  } catch (error) {
    publishRequests.value = []
    publishRequestsError.value = error instanceof Error ? error.message : String(error)
  }
}

function togglePublishRequests() {
  publishRequestsOpen.value = !publishRequestsOpen.value
  if (publishRequestsOpen.value) void loadPublishRequests()
}

async function reviewRequest(request: PublishRequest, approve: boolean) {
  if (!approve && !window.confirm(`驳回「${request.skill_name}」的上架申请？`)) return
  publishRequestsError.value = ''
  try {
    await reviewPublishRequest(request.id, { approve, note: publishReviewNote.value[request.id]?.trim() || undefined })
    delete publishReviewNote.value[request.id]
    // 审完这条就不在 pending 里了，重拉一次；顺带刷市场 —— 通过会改 skill 的
    // 归属档，列表里的可见性/可编辑标记跟着变。
    await loadPublishRequests()
    await loadMarkets()
  } catch (error) {
    publishRequestsError.value = error instanceof Error ? error.message : String(error)
  }
}

function recordValue(value: unknown): Record<string, any> {
  if (value && typeof value === 'object') return value as Record<string, any>
  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value)
      if (parsed && typeof parsed === 'object') return parsed as Record<string, any>
    } catch { /* keep plain text */ }
  }
  return {}
}

function normalizeReferences(value: unknown): ReferenceItem[] {
  if (!Array.isArray(value)) return []
  return value.map((item, index) => {
    const source = recordValue(item)
    const path = String(source.path || '')
    const url = String(source.url || '')
    const title = String(source.title || source.name || path || url || `引用 ${index + 1}`)
    return {
      id: String(source.id || path || url || `reference-${index}`),
      title,
      path: path || undefined,
      url: url || undefined,
      source: String(source.source || source.kind || ''),
      authors: Array.isArray(source.authors) ? source.authors.map(item => String(item)) : undefined,
      year: source.year ? Number(source.year) : undefined,
      abstract: source.abstract ? String(source.abstract) : undefined
    }
  })
}

function referencesFromExtra(extra: unknown) {
  const source = recordValue(extra)
  return normalizeReferences(source.references || source.citations)
}

// usageFromExtra 只覆盖历史重放——直播那条走的是 'usage' SSE 事件（applyChatEvent
// 里单独处理），两条路径最终应该落到同一个 message.usage 字段上，来源不同但
// 展示逻辑不用分叉。
function usageFromExtra(extra: unknown): Message['usage'] {
  const main = recordValue(recordValue(extra).usage).main
  const record = recordValue(main)
  const inputTokens = typeof record.inputTokens === 'number' ? record.inputTokens : 0
  const outputTokens = typeof record.outputTokens === 'number' ? record.outputTokens : 0
  if (!inputTokens && !outputTokens) return undefined
  return { inputTokens, outputTokens }
}

const sessionReferences = computed(() => {
  const merged = new Map<string, ReferenceItem>()
  for (const message of messages.value) {
    for (const reference of message.references || []) merged.set(reference.id, reference)
  }
  return Array.from(merged.values())
})

// normalizeArtifacts 把沙箱产物统一成 SandboxArtifact[]。
// 两种来源形状都要认，见 SandboxArtifact 的注释。
function normalizeArtifacts(raw: unknown): SandboxArtifact[] {
  if (!Array.isArray(raw)) return []
  const out: SandboxArtifact[] = []
  for (const item of raw) {
    if (typeof item === 'string') {
      if (item) out.push({ path: item, name: item.split('/').pop() || item })
      continue
    }
    const record = recordValue(item)
    const path = String(record.path || record.name || '')
    if (!path) continue
    out.push({
      path,
      name: String(record.name || path.split('/').pop() || path),
      size: typeof record.size === 'number' ? record.size : undefined
    })
  }
  return out
}

// sandboxFromCall 读**落库形状**（extra.sandbox_calls[i]，由 Go 的 messageCollector 写）。
function sandboxFromCall(call: Record<string, unknown>): ActivityItem['sandbox'] {
  return {
    language: call.language ? String(call.language) : undefined,
    code: call.codePreview ? String(call.codePreview) : undefined,
    stdout: call.stdoutPreview ? String(call.stdoutPreview) : undefined,
    stderr: call.stderrPreview ? String(call.stderrPreview) : undefined,
    message: call.message ? String(call.message) : undefined,
    durationMs: typeof call.durationMs === 'number' ? call.durationMs : undefined,
    truncated: call.outputTruncated === true,
    artifacts: normalizeArtifacts(call.artifacts)
  }
}

// sandboxFromEvent 读**直播形状**（SSE tool_event 的 data，就是 agentloop 的 tool.Event）。
//
// 跟 sandboxFromCall 拆成两个函数、而不是让上层各解析各的，是因为两条路必须收敛到
// 同一个渲染结构：直播看到的卡片和刷新页面后从库里读出来的卡片，只要长得不一样，
// 用户就会以为刷新把东西弄丢了。落库那份是 Go 侧从**同一批事件**归纳出来的
// （collector.go 的 onSandboxLocked），所以这里的字段映射是照着它抄的。
function sandboxFromEvent(data: Record<string, unknown>, previous?: ActivityItem['sandbox']): ActivityItem['sandbox'] {
  const input = recordValue(data.input)
  const payload = recordValue(data.data)
  // Started 带 input（代码在里面），Progress 带 data（产物/输出），Completed/Failed
  // 带 output（按 stream 分好的行）。三种事件共用一个 toolUseID，所以要**并进**上一次
  // 的结果而不是覆盖 —— 否则 Progress 一到，Started 记下的代码就被清空了。
  const stdoutLines: string[] = []
  const stderrLines: string[] = []
  for (const line of Array.isArray(data.output) ? data.output : []) {
    const record = recordValue(line)
    const text = String(record.text || '')
    if (record.stream === 'stderr') stderrLines.push(text)
    else stdoutLines.push(text)
  }
  const code = input.code || input.command
  const durationMs = typeof payload.duration_ms === 'number'
    ? payload.duration_ms
    : typeof data.durationMs === 'number' ? data.durationMs : undefined
  return {
    language: String(input.language || (input.command ? 'bash' : '')) || previous?.language,
    code: code ? String(code) : previous?.code,
    stdout: String(payload.stdout || stdoutLines.join('\n')) || previous?.stdout,
    stderr: String(payload.stderr || stderrLines.join('\n')) || previous?.stderr,
    // message 只在既没 stdout 也没 stderr 时才有意义 —— 它是引擎的状态描述
    // （"completed with error"），不是沙箱输出，摆在输出位置上会误导。
    message: data.message && !stdoutLines.length && !stderrLines.length ? String(data.message) : previous?.message,
    durationMs: durationMs ?? previous?.durationMs,
    truncated: data.outputTruncated === true || previous?.truncated === true,
    artifacts: normalizeArtifacts(payload.artifacts).length
      ? normalizeArtifacts(payload.artifacts)
      : previous?.artifacts || []
  }
}

// parseAskUserPayload 从工具调用的原始数据里把"问题文本"和"候选项"拆出来。
//
// 直接读 input.question / input.options——这是 AskUser 工具**自己的调用参数**，
// 结构化的，Go 那边工具定义就是这么声明的（agentloop/tools/askuser），不是
// 猜出来的格式。之前那版靠拿 result 字段里"已向用户提出问题,等待回复:...
// (候选:A / B / C)"这句人类可读摘要去正则抠 —— 这句摘要只在**直播**的
// tool_event.data.output 里长这个 {stream,text}[] 的形状，落库之后
// （chat_messages.extra.tool_calls[].result）就是一整段**字符串**，同一个
// 正则找不到"候选"就整卡退化成原始 JSON，表现正是"直播时有选项、刷新页面
// 之后就没了"——查了库里存的真实结构才发现 input 字段从头到尾都是干净的
// {question, options}，直播和历史重放两条路径的 tool_event.data.input /
// tool_calls[].input 是同一份东西，没有这个"格式看直播还是重放而变"的问题。
//
// 返回 null 表示这不是 AskUser 的调用（没有 question 字段）——调用方据此
// 决定要不要退回原来的"整段 JSON 原样打印"。
function parseAskUserPayload(raw: unknown): { question: string; candidates: string[] } | null {
  const record = recordValue(raw)
  const input = recordValue(record.input)
  const question = typeof input.question === 'string' ? input.question.trim() : ''
  if (!question) return null
  const candidates = Array.isArray(input.options) ? input.options.map(item => String(item)) : []
  return { question, candidates }
}

function activitiesFromExtra(extra: unknown): ActivityItem[] {
  const source = recordValue(extra)
  const activities: ActivityItem[] = []
  const tools = Array.isArray(source.tool_calls) ? source.tool_calls : []
  const sandboxes = Array.isArray(source.sandbox_calls) ? source.sandbox_calls : []
  for (const segment of Array.isArray(source.segments) ? source.segments : []) {
    const item = recordValue(segment)
    if (item.kind === 'thinking') {
      activities.push({ id: `thinking-${activities.length}`, kind: 'thinking', title: '思考过程', detail: String(item.content || '') })
    } else if (item.kind === 'tool') {
      const tool = recordValue(tools[Number(item.index)])
      const askUser = parseAskUserPayload(tool)
      activities.push({
        id: String(tool.toolCallId || `tool-${activities.length}`),
        kind: 'tool',
        title: `工具 · ${tool.tool || '未知工具'}`,
        detail: askUser ? undefined : pretty(tool),
        status: String(tool.status || 'completed'),
        askUser: askUser || undefined
      })
    } else if (item.kind === 'sandbox') {
      const sandbox = recordValue(sandboxes[Number(item.index)])
      const detail = sandboxFromCall(sandbox)
      activities.push({
        id: String(sandbox.callId || `sandbox-${activities.length}`),
        kind: 'sandbox',
        // 标题带上语言，跟直播那条对齐。以前历史里固定写死 "Session 沙箱"，
        // 于是同一次执行在直播时叫 "Session 沙箱 · Bash"、刷新后叫 "Session 沙箱"。
        title: detail?.language ? `Session 沙箱 · ${detail.language}` : 'Session 沙箱',
        sandbox: detail,
        status: String(sandbox.status || 'completed')
      })
    }
  }
  return activities
}

function addActivity(message: Message, activity: ActivityItem) {
  if (!message.activities) message.activities = []
  const existing = message.activities.find(item => item.id === activity.id)
  if (existing) {
    Object.assign(existing, activity)
    return
  }
  const last = message.activities[message.activities.length - 1]
  if (activity.kind === 'thinking' && last?.kind === 'thinking') {
    last.detail = `${last.detail || ''}${activity.detail || ''}`
    return
  }
  message.activities.push(activity)
}

async function loadSessionWorkspace(sessionId: string) {
  workspaceLoading.value = true
  workspaceError.value = ''
  try {
    const workspace = await listWorkspaceFiles(sessionId, showHiddenFiles.value)
    // 陈旧响应丢弃：sessionId 是调用时闭包捕获的，这次请求在途时用户完全
    // 可能已经切到了别的会话——文件工作区面板展示的语义就是"当前打开的
    // 这个会话"，不是"发起这次请求时是哪个会话"，用 currentSessionId 校验
    // 足够，不需要像 openSession 那样另开一个世代计数器。
    if (currentSessionId.value !== sessionId) return
    workspaceTruncated.value = workspace.truncated === true
    workspaceStore.value = workspace.store || ''
    files.value = (workspace.files || []).map(file => {
      const slash = file.path.lastIndexOf('/')
      const name = slash >= 0 ? file.path.slice(slash + 1) : file.path
      // 扩展名从**文件名**里取，不是从整条路径里取：路径中间的目录名带点时
      // （`v1.2/report`）在整条路径上取最后一段会拿到 "2/REPORT"。
      const dot = name.lastIndexOf('.')
      return {
        path: file.path,
        dir: slash >= 0 ? file.path.slice(0, slash) : '',
        name,
        type: dot > 0 ? name.slice(dot + 1).toUpperCase() : 'FILE',
        size: formatBytes(file.size),
        mtime: file.mtime || 0,
        status: (file.in_output ? 'ready' : 'draft') as AssetFile['status']
      }
    })
    if (selectedFile.value && files.value.some(file => file.path === selectedFile.value?.path)) {
      await selectWorkspaceFile(selectedFile.value)
    } else if (files.value[0]) {
      await selectWorkspaceFile(files.value[0])
    } else {
      selectedFile.value = undefined
      clearFilePreview()
    }
  } catch (error) {
    workspaceError.value = error instanceof Error ? error.message : String(error)
  } finally {
    workspaceLoading.value = false
  }
}

// workspaceTree 把后端返的扁平路径列表还原成**真的目录树**，根节点是 workspace。
//
// 以前这里只按 dir 字符串分一层组。那样 output/ 和 output/figures/ 是两个平级的组，
// 看不出后者在前者里面，深一点的结构（data/raw/、data/clean/）更是彻底摊平 ——
// 而用户想看的恰恰是"模型在工作区里搭了个什么结构"。
//
// 排序：目录在前、文件在后；目录按名字，文件按 mtime 倒序。倒序是因为来这个面板
// 九成是为了看"模型刚写了什么"，按字母序会把刚生成的文件埋在中间。
type WorkspaceNode = {
  // 目录用相对路径当 key（根是 ''），文件用完整 path。两者不会撞，因为目录 key
  // 永远不带扩展名那一段的完整文件路径。
  key: string
  name: string
  depth: number
  kind: 'dir' | 'file'
  file?: AssetFile
  children: WorkspaceNode[]
  // 整棵子树里的文件数，不只是直接子节点 —— 折叠起来的时候这个数字才有意义。
  fileCount: number
}

const workspaceTree = computed<WorkspaceNode>(() => {
  const root: WorkspaceNode = { key: '', name: 'workspace', depth: 0, kind: 'dir', children: [], fileCount: 0 }
  const dirs = new Map<string, WorkspaceNode>([['', root]])

  // 按需建目录节点，顺带把缺失的父目录一路补齐 —— 后端只返文件，中间目录
  // （output/figures 里只有文件时的 figures）不会单独出现在列表里。
  const ensureDir = (dir: string): WorkspaceNode => {
    const hit = dirs.get(dir)
    if (hit) return hit
    const slash = dir.lastIndexOf('/')
    const parent = ensureDir(slash >= 0 ? dir.slice(0, slash) : '')
    const node: WorkspaceNode = {
      key: dir,
      name: slash >= 0 ? dir.slice(slash + 1) : dir,
      depth: parent.depth + 1,
      kind: 'dir',
      children: [],
      fileCount: 0
    }
    parent.children.push(node)
    dirs.set(dir, node)
    return node
  }

  for (const file of files.value) {
    const parent = ensureDir(file.dir)
    parent.children.push({
      key: file.path, name: file.name, depth: parent.depth + 1,
      kind: 'file', file, children: [], fileCount: 0
    })
  }

  const finish = (node: WorkspaceNode): number => {
    node.children.sort((a, b) => {
      if (a.kind !== b.kind) return a.kind === 'dir' ? -1 : 1
      if (a.kind === 'dir') return a.name.localeCompare(b.name)
      return (b.file!.mtime - a.file!.mtime) || a.name.localeCompare(b.name)
    })
    node.fileCount = node.children.reduce(
      (sum, child) => sum + (child.kind === 'file' ? 1 : finish(child)), 0
    )
    return node.fileCount
  }
  finish(root)
  return root
})

// 不含根节点 workspace —— 用户数的是"模型建了几个目录"，把根算进去会永远多 1。
const workspaceDirCount = computed(() => {
  const count = (node: WorkspaceNode): number =>
    node.children.reduce((sum, child) => sum + (child.kind === 'dir' ? 1 + count(child) : 0), 0)
  return count(workspaceTree.value)
})

// 折叠态存"收起来的目录"而不是"展开的目录"：默认全展开，新生成的目录不需要
// 用户再点一次才看得见。模型刚写完文件就自动刷新，默认收起会让人以为没生成。
const collapsedDirs = ref(new Set<string>())

function toggleDir(key: string) {
  if (collapsedDirs.value.has(key)) collapsedDirs.value.delete(key)
  else collapsedDirs.value.add(key)
}

// 摊平成一维行列表再渲染，避免为了一棵树引入递归组件。被折叠目录的子树整段跳过。
const visibleWorkspaceRows = computed(() => {
  const rows: WorkspaceNode[] = []
  const walk = (node: WorkspaceNode) => {
    rows.push(node)
    if (node.kind === 'dir' && !collapsedDirs.value.has(node.key)) node.children.forEach(walk)
  }
  walk(workspaceTree.value)
  return rows
})

// 产物 = 写进 output/ 的文件（后端 file_store.py 的判据就是 path.startswith("output/")，
// 前端在 loadSessionWorkspace 里映射成 status==='ready'）。跟工具事件里那个
// artifacts 字段无关 —— 那个目前恒为空，没有消费方。
const readyFiles = computed(() =>
  files.value.filter(file => file.status === 'ready')
    .slice().sort((a, b) => (b.mtime - a.mtime) || a.path.localeCompare(b.path))
)

async function toggleHiddenFiles() {
  showHiddenFiles.value = !showHiddenFiles.value
  if (currentSessionId.value) await loadSessionWorkspace(currentSessionId.value)
}

// openSandboxArtifact 让沙箱卡片里列出的产物可以直接点开。
//
// 产物路径就是会话工作区里的相对路径，所以复用文件面板那条链：选中它 → 预览/下载。
// 不直接下载是因为大多数产物是图或文本，预览一眼就够了，下载反而多一步。
// 找不到对应文件时（产物被后续步骤删了、或者列表被截断了）只报一句，不静默无反应。
async function openSandboxArtifact(artifact: SandboxArtifact) {
  inspectorOpen.value = true
  inspectorTab.value = 'files'
  const match = files.value.find(file => file.path === artifact.path)
  if (match) {
    await selectWorkspaceFile(match)
    return
  }
  workspaceError.value = `产物 ${artifact.path} 不在当前工作区文件列表里，可能已被覆盖或列表被截断。`
}

async function openSession(sessionId: string, forceReload = false) {
  // 重复点正在生成的会话不应重载历史、再订阅一遍同一轮。
  if (!forceReload && currentSessionId.value === sessionId && streamRunning.value) return
  detachCurrentStream()
  if (currentSessionId.value !== sessionId) messages.value = []
  currentSessionId.value = sessionId
  const generation = ++openSessionGeneration
  sessionLoading.value = true
  try {
    const conversation = conversations.value.find(item => item.id === sessionId)
    agentId.value = conversation?.agent_id || 0
    selectedTeamId.value = teamItems.value.find(team => team.agent_id === agentId.value)?.id || null
    chatMode.value = selectedTeamId.value ? 'team' : agentId.value ? 'expert' : 'default'
    configurationSelection.value = selectedTeamId.value
      ? `team:${selectedTeamId.value}`
      : agentId.value ? `agent:${agentId.value}` : 'default'
    modelDraft.value = conversation?.model || expertItems.value.find(item => item.id === agentId.value)?.model || defaultChatModel.value
    const result = await getMessages(sessionId)
    // 陈旧响应丢弃：这一步之后还有好几步要写共享状态（消息列表、附件、
    // 工作区文件，最后甚至会去接一条正在直播的 SSE 流），任何一步都可能
    // 因为用户这期间又点了别的会话而变成"写错对象"。不是显示错乱那么
    // 简单——resumeActiveRunIfAny 真的会把另一个会话正在直播的内容注入
    // 到当前看着的这个会话里。这里检查一次就够，不合格直接放弃剩余全部
    // 步骤，不留半程状态。
    if (generation !== openSessionGeneration) return
    messages.value = result.messages.map(message => ({
      id: message.id,
      role: message.role,
      author: message.role === 'user' ? '你' : 'LEA',
      text: message.content,
      status: 'done',
      activities: activitiesFromExtra(message.extra),
      references: referencesFromExtra(message.extra),
      usage: usageFromExtra(message.extra)
    }))
    const attachments = await listSessionAttachments(sessionId)
    if (generation !== openSessionGeneration) return
    sessionAttachments.value = attachments.attachments || []
    await loadSessionWorkspace(sessionId)
    if (generation !== openSessionGeneration) return
    void scrollToBottom(true)
    await resumeActiveRunIfAny(sessionId, generation)
  } catch (error) {
    requestLogs.value.unshift({ method: 'GET', path: `/api/sessions/${sessionId}/messages`, status: 'ERR', time: 0, body: error instanceof Error ? error.message : String(error) })
  } finally {
    if (generation === openSessionGeneration) sessionLoading.value = false
  }
}

// resumeActiveRunIfAny 补的是"刷新页面/重新打开会话时，后端其实还有一轮没跑完"
// 这个洞。openSession 拉到的历史只有**已落库**的消息——正在跑的那一轮还没
// 提交，压根不在这份历史里,界面上看起来就是"任务没了"。而后端并不知道前端
// 刚刚经历了一次刷新,它那边的并发保护(session.ErrBusy/persistence.ErrActiveRun)
// 仍然认为这个 session 有一轮在跑——用户这时候发新消息,只会撞上一句莫名其妙
// 的"a turn is already running for this session"，看起来像是卡死了,其实
// 是后端做了正确的事,前端没有把这件事reflect出来。
//
// `from` 传空串 = 从头补（resumeMessageStream 的既有约定，见它自己的注释）——
// 刷新之后 lastEventId 这种内存态游标本来就丢了，没有更精确的续传点可用，
// 只能整轮从头重放。
async function resumeActiveRunIfAny(sessionId: string, generation: number) {
  let running = false
  try {
    running = (await getTurnStatus(sessionId)).running
  } catch {
    // 查不到就当没有在跑——这只是个"锦上添花"的检测，查询本身失败不该
    // 阻塞正常打开会话，顶多是这次刷新没接上，用户手动重发一次也能恢复。
    return
  }
  if (generation !== openSessionGeneration || !running) return

  messages.value.push({ id: Date.now(), role: 'assistant', author: 'LEA', text: '', status: 'running', activities: [] })
  const assistant = messages.value[messages.value.length - 1]!
  streamRunning.value = true
  resumeAvailable.value = false
  lastEventId = ''
  resumeAttempts = 0
  try {
    await attachChatStream(generation,
      resumeMessageStream(sessionId, '', event => applyChatEvent(assistant, event, generation)))
  } catch (error) {
    finishBrokenTurn(assistant, error instanceof Error ? error.message : String(error), generation)
  }
}

// removeConversation 删一个会话。后端那条 DELETE 一直都在，只是前端没接 ——
// 于是"最近对话"是个只进不出的列表，攒到几十条之后侧栏就没法用了。
//
// 删的如果是**当前打开的那个**，必须把聊天区一起清空：留着不动的话界面显示的是一个
// 已经不存在的会话，下一条消息会带着死 session_id 发出去，报错在后端（404），
// 而用户看到的是"发消息失败"，完全对不上他刚才做的事。
async function removeConversation(sessionId: string) {
  if (!window.confirm('删除这个会话？对话记录会一并删除，不能恢复。')) return
  if (submitBusyKey.value) return
  submitBusyKey.value = 'conversation-delete'
  try {
    await deleteSession(sessionId)
  } catch (error) {
    connectionError.value = error instanceof Error ? error.message : String(error)
    return
  } finally {
    submitBusyKey.value = ''
  }
  conversations.value = conversations.value.filter(item => item.id !== sessionId)
  if (currentSessionId.value === sessionId) {
    detachCurrentStream()
    currentSessionId.value = ''
    selectedConversation.value = ''
    messages.value = []
    sessionAttachments.value = []
    attachmentIds.value = []
  }
}

async function ensureCurrentSession(generation = openSessionGeneration) {
  if (currentSessionId.value) return currentSessionId.value
  const result = await createSession(agentId.value, selectedProjectId.value || undefined, modelDraft.value.trim() || undefined)
  if (generation !== openSessionGeneration) return result.session_id
  currentSessionId.value = result.session_id
  selectedConversation.value = result.session_id
  upsertConversation(result.session_id)
  return result.session_id
}

const mountOptions = computed(() => {
  if (mountKind.value === 'skill') return marketItems.value.skills.map(item => ({ id: item.id, name: marketItemTitle(item), description: marketItemDescription(item) }))
  if (mountKind.value === 'mcp_server') return marketItems.value.mcp.map(item => ({ id: item.id, name: marketItemTitle(item), description: marketItemDescription(item) }))
  return marketItems.value.tools.map(item => ({ id: item.id, name: marketItemTitle(item), description: marketItemDescription(item) }))
})

// addPendingAttachment 只是本地暂存,不打任何接口。挂到资源下拉的 @change 上——
// 选中即算数,不需要额外一个"添加"按钮:下拉本身选中就是一次明确操作,不存在
// "划过去看一眼"的悬停触发。选完之后重置下拉，方便连续选下一个。真正的挂载
// 请求延到 sendChatMessage 里,跟这条消息一起提交,不需要先跑一次"挂载"再跑
// 一次"发送"。
function addPendingAttachment() {
  if (!mountSelection.value) return
  const option = mountOptions.value.find(item => item.id === mountSelection.value)
  if (!option) return
  const already = pendingAttachments.value.some(item => item.kind === mountKind.value && item.ref_id === mountSelection.value)
  if (!already) pendingAttachments.value.push({ kind: mountKind.value, ref_id: mountSelection.value, name: option.name })
  mountSelection.value = 0
}

function removePendingAttachment(index: number) {
  pendingAttachments.value.splice(index, 1)
}

// buildAttachmentsPayload 拼出发消息时要带的完整清单——已挂载的 + 这一轮刚选的。
// 不调任何接口，纯本地拼装；真正的整体替换由后端在处理这条消息时原子完成
// （lea-agent 的 applyRequestAttachments），不需要"先挂载再发送"两次请求。
//
// 每条消息都带上完整清单，不只在"这一轮有新增"时才带——判断"有没有真的变化"
// 交给后端去跟当前状态比对（指纹一样直接跳过，不会拖慢日常消息），前端不用
// 猜"这次要不要带这个字段"，永远带最新的完整状态就行，更不容易因为忘记合并
// 已挂载项而把它们误当成"要清空"。
function buildAttachmentsPayload(): { kind: SessionAttachment['kind']; ref_id: number }[] {
  const merged = [
    ...sessionAttachments.value.map(item => ({ kind: item.kind, ref_id: item.ref_id })),
    ...pendingAttachments.value.map(item => ({ kind: item.kind, ref_id: item.ref_id }))
  ]
  const seen = new Set<string>()
  const unique: { kind: SessionAttachment['kind']; ref_id: number }[] = []
  for (const item of merged) {
    const key = `${item.kind}:${item.ref_id}`
    if (seen.has(key)) continue
    seen.add(key)
    unique.push(item)
  }
  return unique
}

// commitPendingAttachments 在消息已经发出去之后，把本地暂存的选择转成"已确认"，
// 并跟服务端再对一次账——此时后端的整体替换已经落库（跟消息同一个请求原子完成），
// 这里只是让界面上的 chip 从"待发送时挂载"变成"已挂载"的普通样式，失败了也
// 不影响消息本身（消息已经发出去了），只是下次列表刷新前 chip 状态可能滞后一下。
async function commitPendingAttachments() {
  if (!pendingAttachments.value.length) return
  pendingAttachments.value = []
  if (!currentSessionId.value) return
  try {
    await loadSessionAttachments(currentSessionId.value)
  } catch {
    // 静默失败——见上面的说明，不影响已经发出去的消息。
  }
}

async function loadSessionAttachments(sessionId: string) {
  const generation = openSessionGeneration
  const result = await listSessionAttachments(sessionId)
  if (generation !== openSessionGeneration || currentSessionId.value !== sessionId) return
  sessionAttachments.value = result.attachments || []
}

async function unmountResource(resource: SessionAttachment) {
  if (!currentSessionId.value) return
  try {
    await detachSessionResource(currentSessionId.value, resource)
    sessionAttachments.value = sessionAttachments.value.filter(item => !(item.kind === resource.kind && item.ref_id === resource.ref_id))
  } catch (error) {
    requestLogs.value.unshift({ method: 'DELETE', path: `/api/sessions/${currentSessionId.value}/attachments/${resource.kind}/${resource.ref_id}`, status: 'ERR', time: 0, body: error instanceof Error ? error.message : String(error) })
  }
}

onMounted(() => {
  void loadWorkspace()
  // 跟 loadWorkspace 并行、且不参与它的错误处理：模型列表读不到不该拖住
  // 整个工作区加载。
  void loadChatModels()
  void loadRetrievalModels()
})

async function saveToken() {
  token.value = token.value.trim().replace(/^Bearer\s+/i, '')
  localStorage.setItem('lea_workspace_token', token.value)
  connectionError.value = ''
  try {
    tenants.value = (await listTenants()).items || []
    if (selectedTenantId.value && !tenants.value.some(item => item.id === selectedTenantId.value)) selectedTenantId.value = ''
    if (!tenants.value.length) connectionError.value = '当前账号没有可用租户'
  } catch (error) {
    connectionError.value = error instanceof Error ? error.message : String(error)
  }
}

async function connectTenant() {
  if (!selectedTenantId.value.trim()) {
    connectionError.value = '请选择或填写租户 ID'
    return
  }
  localStorage.setItem('lea_workspace_tenant_id', selectedTenantId.value.trim())
  connectionError.value = ''
  try {
    await listSessions()
    inspectorOpen.value = false
    await loadWorkspace()
  } catch (error) {
    connectionError.value = error instanceof Error ? error.message : String(error)
  }
}

function selectedConfigurationName() {
  if (selectedTeamId.value) {
    return teamItems.value.find(item => item.id === selectedTeamId.value)?.name || '所选专家团'
  }
  return expertItems.value.find(item => item.id === agentId.value)?.name || '默认 Super Agent'
}

async function persistSelectedConfiguration() {
  if (!currentSessionId.value) return
  const conversation = conversations.value.find(item => item.id === currentSessionId.value)

  // POST /sessions 只在内存中预建 session，首条消息才会把 chat_sessions 行写入 MySQL。
  // 这种会话无需 PUT；下面那次 SSE 的 agent_id 会作为首次落库的 active_agent_id。
  if (!conversation || conversation.meta === '正在同步…') return
  if ((conversation.agent_id || 0) === agentId.value) return

  try {
    await setSessionActiveAgent(currentSessionId.value, agentId.value)
    conversation.agent_id = agentId.value || null
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error)
    const message = error instanceof ApiError && error.status === 404
      ? detail === 'agent not found'
        ? `无法启用「${selectedConfigurationName()}」：该专家已删除、停用或不属于当前租户。`
        : `无法启用「${selectedConfigurationName()}」：当前会话已不存在或无权访问，请刷新后重试。`
      : `无法启用「${selectedConfigurationName()}」：${detail}`
    requestLogs.value.unshift({ method: 'PUT', path: `/api/sessions/${currentSessionId.value}/active-agent`, status: 'ERR', time: 0, body: detail })
    throw new Error(message)
  }
}

// messageListEl 是消息列表那个自己滚动的容器（见 styles.css 里 .message-list
// 的说明：外层 .main-stage 不再整页滚动，改成这个容器内部滚动，输入框才能
// 稳定贴底）。改成内部滚动之后必须自己维护"贴底"——原生页面滚动至少还会
// 让新内容落在可见区域附近，容器滚动完全不会，新增内容会落在视口之外，
// 表现就是"看起来卡住了，直到这一轮结束才一次性冒出来"（其实流一直在收，
// 只是容器没跟着滚，见 issue 复盘）。
const messageListEl = ref<HTMLElement | null>(null)

// isNearBottom 判断要不要自动跟着滚——只在用户本来就在底部附近时才追着滚，
// 免得用户往上翻看历史消息时，新来的流式内容把视图硬拽回底部。
// 120px 是个宽松阈值，不是要求刚好贴底才追。
function isNearBottom(): boolean {
  const el = messageListEl.value
  if (!el) return true
  return el.scrollHeight - el.scrollTop - el.clientHeight < 120
}

async function scrollToBottom(force = false) {
  if (!force && !isNearBottom()) return
  await nextTick()
  const el = messageListEl.value
  if (el) el.scrollTop = el.scrollHeight
}

// answerAskUser 点一个候选项等于把它当成用户输入直接发出去——AskUser 没有
// 独立的"应答"接口，它只是个普通工具，工具结果之后引擎等的就是**下一条消息**，
// 跟用户自己在输入框打完这句话回车是同一条路径，走 sendChatMessage 而不是
// 另开一个专门端点。
//
// answered 标一下这张卡片，纯本地状态，防止同一屏里对着已经点过的卡片再点
// 一次候选发出重复消息。
function answerAskUser(activity: ActivityItem, candidate: string) {
  if (!activity.askUser || activity.askUser.answered || streamRunning.value) return
  activity.askUser.answered = true
  void sendChatMessage(candidate)
}

async function sendChatMessage(value: string, planAction = '') {
  if (!value.trim() || streamRunning.value || sessionLoading.value) return
  const generation = ++openSessionGeneration
  // 配置/附件的准备也属于本轮，不能在等待期间再次发送。
  streamRunning.value = true
  try {
    await flushPendingLibraryImports(generation)
    if (generation !== openSessionGeneration) return
    await persistSelectedConfiguration()
    if (generation !== openSessionGeneration) return
  } catch (error) {
    if (generation !== openSessionGeneration) return
    streamRunning.value = false
    messages.value.push({
      id: Date.now(),
      role: 'system',
      author: 'LEA',
      text: error instanceof Error ? error.message : String(error),
      status: 'failed'
    })
    return
  }
  const attachmentsPayload = buildAttachmentsPayload()
  messages.value.push({ id: Date.now(), role: 'user', author: '你', text: value })
  messages.value.push({ id: Date.now() + 1, role: 'assistant', author: 'LEA', text: '', status: 'running', activities: [] })
  // 必须用"通过数组下标读出来的这个引用"，不能留着上面字面量对象本身的引用去
  // 改它的属性——Vue 的响应式转换发生在**读**的那一刻（reactive() 包一层代理），
  // push 进去的原始对象不会自动变成响应式的。留着原始引用直接改 .text/.activities，
  // 写是写进去了，但完全绕开了代理的 set 陷阱，Vue 的依赖追踪压根不知道发生了
  // 写入——界面会一直卡在 push 那一刻的样子，直到某个**不相关**的响应式状态变化
  // （比如 done 事件里的 streamRunning.value = false）顺带触发一次重渲染，才会
  // 把这一轮攒下来的最终结果一次性显示出来。这正是"流一直在收、界面却卡在
  // running 直到整轮结束才一次性冒出来"的真正原因，不是网络也不是滚动的问题。
  const assistant = messages.value[messages.value.length - 1]!
  void scrollToBottom(true)
  streamRunning.value = true
  // 新一轮，游标和重连计数都归零 —— 上一轮的 id 对这一轮没有意义。
  lastEventId = ''
  resumeAttempts = 0
  resumeAvailable.value = false
  try {
    await attachChatStream(generation,
      sendMessageStream({ session_id: currentSessionId.value || undefined, agent_id: agentId.value, project_id: selectedProjectId.value || undefined, message: value, model: modelDraft.value.trim() || undefined, attachment_ids: attachmentIds.value, plan_mode: planAction ? false : chatMode.value === 'plan', plan_action: planAction, attachments: attachmentsPayload }, event => applyChatEvent(assistant, event, generation)))
    if (generation !== openSessionGeneration) return
    // 挂载的整体替换是后端在处理这条消息时原子完成的（跟这次 fetch 是同一个
    // 请求），走到这里说明已经落库——把暂存的转成已确认，跟服务端同步一次
    // 用来刷新 chip 显示，不影响已经在跑的这一轮。
    void commitPendingAttachments()
  } catch (error) {
    if (generation !== openSessionGeneration) return
    // 该对话已被另一个标签页删除——不是这一轮回答失败，是这条会话本身没了。
    // 引导回到列表/新对话，而不是让用户对着一句裸错误反复重试同一个已经
    // 不存在的 session_id。
    if (error instanceof ApiError && error.code === 'session_archived') {
      assistant.status = 'failed'
      assistant.text = '该对话已被删除，无法继续。请返回列表或新建对话。'
      streamRunning.value = false
      conversations.value = conversations.value.filter(item => item.id !== currentSessionId.value)
      return
    }
    assistant.status = 'failed'
    assistant.text = error instanceof Error ? error.message : String(error)
    streamRunning.value = false
  }
}

// 响应头可能在用户离开之后才回来；此时才拿到的 abort 也只属于旧连接。
// 不提前取消 POST，保证已经提交的旧任务能在服务端继续生成并保存历史。
async function attachChatStream(generation: number, stream: Promise<() => void>) {
  const stop = await stream
  if (generation !== openSessionGeneration || !streamRunning.value) {
    stop()
    return
  }
  stopCurrentStream = stop
}

// applyChatEvent 是 SSE 事件的唯一落点 —— 首次发送和断线续传共用它。
//
// 抽出来不是为了短，是因为**续传必须走完全相同的解释逻辑**：补发的事件跟直播的
// 事件是同一批（服务端只是从日志里重放），两处各写一遍的话，差异只会在断线之后
// 才显形，而那是最难复现的场景。
function applyChatEvent(assistant: Message, event: ChatEvent, generation: number) {
  if (generation !== openSessionGeneration) {
    // 旧任务仍可能刚创建会话；同步侧栏即可，不改变正在看的新对话。
    if (event.type === 'session' || event.type === 'done') void refreshSessionList().catch(() => {})
    return
  }
  // 游标每个事件都更新。续传靠它，所以放在所有分支之前 —— 放进某个分支里
  // 就会漏掉没被显式处理的事件类型，断点因此往回退，重连后收到重复内容。
  if (event.id) lastEventId = event.id
  if (event.type === 'session' && typeof event.data === 'object' && event.data && 'session_id' in event.data) {
        currentSessionId.value = String((event.data as { session_id: string }).session_id)
        selectedConversation.value = currentSessionId.value
        upsertConversation(currentSessionId.value)
      } else if (event.type === 'delta') {
        assistant.text += String(event.data || '')
      } else if (event.type === 'thinking') {
        addActivity(assistant, { id: `thinking-live-${assistant.activities?.length || 0}`, kind: 'thinking', title: '思考过程', detail: String(event.data || '') })
      } else if (event.type === 'tool_event') {
        const data = recordValue(event.data)
        const toolName = String(data.toolName || data.tool || data.name || '工具')
        const status = String(data.type || data.status || 'running').toLowerCase()
        // 沙箱的判据跟后端保持一致（collector.go 的 onSandboxLocked）：Bash 和
        // code_interpreter 走沙箱组，别的都是普通工具。两边判据分叉的话，同一次调用
        // 直播时是沙箱卡片、刷新后变成工具卡片。
        const kind = toolName.toLowerCase() === 'bash' || toolName.toLowerCase() === 'code_interpreter' ? 'sandbox' : 'tool'
        const activityId = String(data.toolUseID || data.toolUseId || data.toolCallId || `${kind}-${assistant.activities?.length || 0}`)
        if (kind === 'sandbox') {
          // 同一次执行会连来 started / progress / completed 三条事件，各带一部分信息。
          // 把上一次的结果传进去做并集 —— 见 sandboxFromEvent 里的说明。
          const previous = assistant.activities?.find(item => item.id === activityId)?.sandbox
          const detail = sandboxFromEvent(data, previous)
          addActivity(assistant, {
            id: activityId,
            kind,
            title: detail?.language ? `Session 沙箱 · ${detail.language}` : `Session 沙箱 · ${toolName}`,
            sandbox: detail,
            status
          })
        } else {
          // AskUser 的 started 事件带完整的 input.question/options，但后续
          // completed 事件只带 output，不会重发 input——parseAskUserPayload(data)
          // 对这次事件单独解析会解析不出来，如果不merge 上一次已经解析好的结果，
          // Object.assign（addActivity 里）会用这次的 undefined 把 started 时
          // 已经解析出来的 askUser 覆盖掉，表现是"直播时问题卡片一开始有候选
          // 按钮，completed 一到候选就消失、退化成原始 JSON"。跟沙箱卡片的
          // previous 合并是同一个模式（那边是 started 记代码、progress 才有
          // 输出，这边是 started 记 input、completed 才有 output，都是"同一个
          // toolUseID 分几次给不同字段，要并集不要覆盖"）。
          const previousAskUser = assistant.activities?.find(item => item.id === activityId)?.askUser
          const askUser = parseAskUserPayload(data) || previousAskUser
          addActivity(assistant, {
            id: activityId,
            kind,
            title: `工具 · ${toolName}`,
            detail: askUser ? undefined : pretty(data.input || data.output || data.summary || data),
            status,
            askUser
          })
        }
      } else if (event.type === 'chain_hop') {
        const data = recordValue(event.data)
        addActivity(assistant, {
          id: `chain-${data.index || assistant.activities?.length || 0}`,
          kind: 'chain',
          title: `接力专家 · ${String(data.expert || data.agent || '下一棒')}`,
          detail: [data.role, data.handedTo ? `交给 ${data.handedTo}` : ''].filter(Boolean).join(' · '),
          status: 'completed'
        })
      } else if (event.type === 'references') {
        const data = recordValue(event.data)
        const incoming = normalizeReferences(data.references)
        if (incoming.length) {
          const existing = new Map((assistant.references || []).map(reference => [reference.id, reference]))
          for (const reference of incoming) existing.set(reference.id, reference)
          assistant.references = Array.from(existing.values())
        }
      } else if (event.type === 'result' && typeof event.data === 'object' && event.data && 'text' in event.data) {
        const data = event.data as { text?: string; references?: unknown }
        assistant.text = String(data.text || assistant.text)
        const incoming = normalizeReferences(data.references)
        if (incoming.length) assistant.references = incoming
      } else if (event.type === 'ask_user' && typeof event.data === 'object' && event.data) {
        const data = event.data as { question?: string; options?: unknown }
        const options = Array.isArray(data.options) ? data.options.map(option => String(option)) : []
        assistant.planApproval = { question: String(data.question || '请确认是否继续执行当前计划'), options }
      } else if (event.type === 'error') {
        assistant.text = String(event.data || '对话失败')
        assistant.status = 'failed'
      } else if (event.type === 'usage' && typeof event.data === 'object' && event.data) {
        const data = event.data as { input_tokens?: number; output_tokens?: number }
        assistant.usage = { inputTokens: Number(data.input_tokens) || 0, outputTokens: Number(data.output_tokens) || 0 }
      } else if (event.type === 'done') {
        assistant.status = assistant.status === 'failed' ? 'failed' : 'done'
        stopCurrentStream = null
        streamRunning.value = false
        resumeAvailable.value = false
        attachmentIds.value = []
        if (currentSessionId.value) {
          void refreshSessionList()
          void loadSessionWorkspace(currentSessionId.value)
          // openSession 会用 getMessages 结果整体替换 messages.value。
          // done 时 persistTurn 可能还未提交，这时拉到的是空数组，会清掉
          // 流里已经渲染好的消息。流已经完整渲染，不必重新加载——只同步附件列表。
          void loadSessionAttachments(currentSessionId.value)
        }
      } else if (event.type === STREAM_CLOSED) {
        // done 之后服务端也会关连接，所以这个事件**正常结束时同样会到**。
        // 只有还在 running 才说明是真断了。
        if (assistant.status === 'running') void handleStreamClosed(assistant, generation)
      }
  // 每个事件都可能带来新内容（delta/工具卡片/AskUser 问题……），容器是自己
  // 滚动的（见 messageListEl 的说明），不追着滚新内容就会落在视口外——
  // 只在用户本来就在底部附近时才追，别把正在往上翻历史的人拽回去。
  void scrollToBottom()
}

// handleStreamClosed 决定断线之后该续传、该拉历史，还是该认输。
//
// 关键是**先问一次 status 再动手**，而不是盲目重连：那一轮可能早就在服务端跑完了
// （我们只是没收到最后几个事件），这时候续传流是白费的，直接拉历史就能拿到完整答案。
// status 查的是控制面，多副本下有效。
async function handleStreamClosed(assistant: Message, generation: number) {
  if (generation !== openSessionGeneration) return
  const sessionId = currentSessionId.value
  if (!sessionId) {
    finishBrokenTurn(assistant, '连接中断', generation)
    return
  }
  if (resumeAttempts >= MAX_RESUME_ATTEMPTS) {
    finishBrokenTurn(assistant, '连接中断，已停止自动重连', generation)
    return
  }
  resumeAttempts += 1
  try {
    const { running } = await getTurnStatus(sessionId)
    if (generation !== openSessionGeneration) return
    if (!running) {
      // 那一轮已经结束了，答案在库里。拉一次历史，比续传一条已经没有后续的流实在。
      await openSession(sessionId, true)
      return
    }
    await attachChatStream(generation,
      resumeMessageStream(sessionId, lastEventId, event => applyChatEvent(assistant, event, generation)))
  } catch (error) {
    if (generation !== openSessionGeneration) return
    // 410 log_expired：事件日志没了，流补不回来 —— 但对话内容在库里，这不是错误。
    // 按 code 分支，不去匹配中文文案（文案一改就失效，见 ApiError 的注释）。
    if (error instanceof ApiError && error.code === 'log_expired') {
      await openSession(sessionId, true)
      return
    }
    finishBrokenTurn(assistant, error instanceof Error ? error.message : String(error), generation)
  }
}

// finishBrokenTurn 把界面从"生成中"里解出来。
//
// 必须放掉 streamRunning：它同时是输入框的禁用条件（sendChatMessage 开头那句
// `if (streamRunning) return`），卡住不放的表现是整个对话框永久变灰、只能刷新页面。
function finishBrokenTurn(assistant: Message, reason: string, generation: number) {
  if (generation !== openSessionGeneration) return
  assistant.status = 'failed'
  if (!assistant.text) assistant.text = reason
  stopCurrentStream = null
  streamRunning.value = false
  // 留一个手动入口：自动重连用完了不代表服务端没救，用户按一下值得再试。
  resumeAvailable.value = Boolean(currentSessionId.value)
}

// retryResume 是"重新连接"按钮的处理器 —— 手动重试把自动计数清零。
async function retryResume() {
  const assistant = [...messages.value].reverse().find(item => item.role === 'assistant')
  if (!assistant) return
  const generation = ++openSessionGeneration
  resumeAttempts = 0
  assistant.status = 'running'
  streamRunning.value = true
  resumeAvailable.value = false
  await handleStreamClosed(assistant, generation)
}

async function addMessage() {
  if (streamRunning.value || sessionLoading.value) return
  const value = prompt.value.trim()
  if (!value) return
  prompt.value = ''
  await sendChatMessage(value)
}

async function resolvePlan(message: Message, action: 'approve' | 'reject') {
  if (!message.planApproval || message.planApproval.resolved || streamRunning.value) return
  message.planApproval.resolved = action === 'approve' ? 'approved' : 'rejected'
  await sendChatMessage(
    action === 'approve' ? '请按已批准的计划继续执行。' : '请停止并取消当前计划。',
    action
  )
}

// newConversation 只是切换到一个空白草稿状态,不碰后端。
//
// 以前这里会立刻调 POST /api/sessions——本意是"反正只在内存建会话,不落库,
// 很便宜",但没量过到底多便宜:GetOrCreate 建一个新会话要串行打两次 lea-config
// 的网络请求(LoadAgentConfig 拉底座配置 + mergeSessionAttachments 解析挂载),
// 用户点一次"+"就要付 3~4 秒,侧栏还会挂着一条"新对话 · 正在同步…"晾在那里,
// 直到发第一条消息才真正落库、状态才会更新——点个新建按钮却要等大半个
// LLM 请求的延迟,不合理。
//
// 真正建会话推迟到发第一条消息的时候:sendChatMessage 发的请求里
// session_id 传空,后端的 GetOrCreate 会顺手建一个,跟这里手动建是同一条
// 后端逻辑,只是把这次不可避免的网络延迟挪到"用户已经在等模型回复"这个
// 时间点上,而不是"用户只是想开始打字"这个时间点上。
function newConversation() {
  detachCurrentStream()
  currentSessionId.value = ''
  selectedConversation.value = ''
  activeNav.value = 'chat'
  messages.value = []
  sessionAttachments.value = []
  pendingAttachments.value = []
  attachmentIds.value = []
  attachmentFiles.value = []
  pendingLibraryImports.value = []
  files.value = []
  selectedFile.value = undefined
  clearFilePreview()
}

async function saveProject() {
  if (!projectForm.value.name.trim() || submitBusyKey.value) return
  submitBusyKey.value = 'project-save'
  try {
    const payload = { name: projectForm.value.name.trim(), description: projectForm.value.description.trim() }
    if (editingProjectId.value) {
      const project = await updateProject(editingProjectId.value, payload)
      const index = projects.value.findIndex(current => current.id === project.id)
      if (index >= 0) projects.value[index] = { ...projects.value[index], ...project }
    } else {
      const project = await createProject(payload)
      projects.value.unshift(project)
      selectedProjectId.value = project.id
    }
    editingProjectId.value = null
    projectForm.value = { name: '', description: '' }
    projectFormOpen.value = false
  } catch (error) {
    requestLogs.value.unshift({ method: editingProjectId.value ? 'PUT' : 'POST', path: '/api/projects', status: 'ERR', time: 0, body: error instanceof Error ? error.message : String(error) })
  } finally {
    submitBusyKey.value = ''
  }
}

function editProject(project: ProjectItem) {
  editingProjectId.value = project.id
  projectForm.value = { name: project.name, description: project.description || '' }
  projectFormOpen.value = true
  activeNav.value = 'projects'
}

function startCreateProject() {
  editingProjectId.value = null
  projectForm.value = { name: '', description: '' }
  projectFormOpen.value = true
}

async function removeProject(project: ProjectItem) {
  if (!window.confirm(`删除项目「${project.name}」？会话会保留，但会解除项目归属。`)) return
  if (submitBusyKey.value) return
  submitBusyKey.value = 'project-delete'
  try {
    await deleteProject(project.id)
    projects.value = projects.value.filter(current => current.id !== project.id)
    conversations.value = conversations.value.map(item => item.project_id === project.id ? { ...item, project_id: null } : item)
    if (selectedProjectId.value === project.id) selectedProjectId.value = null
  } catch (error) {
    requestLogs.value.unshift({ method: 'DELETE', path: `/api/projects/${project.id}`, status: 'ERR', time: 0, body: error instanceof Error ? error.message : String(error) })
  } finally {
    submitBusyKey.value = ''
  }
}

async function archiveProjectItem(project: ProjectItem) {
  try {
    const updated = await archiveProject(project.id)
    const index = projects.value.findIndex(current => current.id === project.id)
    if (index >= 0) projects.value[index] = { ...projects.value[index], ...updated, is_archived: true }
  } catch (error) {
    requestLogs.value.unshift({ method: 'POST', path: `/api/projects/${project.id}/archive`, status: 'ERR', time: 0, body: error instanceof Error ? error.message : String(error) })
  }
}

function openProject(project: ProjectItem) {
  selectedProjectId.value = project.id
  activeNav.value = 'projects'
}

// detachCurrentStream 在**离开**当前对话之前调用——切到另一个会话、新建对话、
// 换专家开聊，都是"人还在，只是不看这个对话了"，跟用户主动点"停止"是两种
//完全不同的意图，不能共用 stopStream：
//
//	stopStream        用户明确要求"别再生成了"——发 POST /chat/stop，
//	                  服务端那一轮真的会被取消。
//	detachCurrentStream 只是不再盯着这条本地连接看——服务端那一轮完全不受
//	                  影响，继续在后台跑完、正常落库，用户回到这个对话时
//	                  能看到完整结果（呼应 resumeActiveRunIfAny：刷新页面
//	                  之后还能接回去，是同一条设计原则）。
//
// 不调用这个函数的后果：streamRunning 是全局的一个标志位，不是"这个会话在
// 跑"而是"**某个**会话在跑"——切走之后旧会话如果还没跑完，这个标志位不会
// 复位，新对话的输入框会一直被"生成中"锁住，直到旧会话那一轮不知道什么
// 时候结束（可能是一个跑很久的任务）才解锁，期间用户会以为整个应用卡死了。
// 本地这条 fetch 的读取循环也会在背景里空转，直到服务端关闭连接才停。
function detachCurrentStream() {
  ++openSessionGeneration
  sessionLoading.value = false
  stopCurrentStream?.()
  stopCurrentStream = null
  streamRunning.value = false
  resumeAvailable.value = false
}

// stopStream 必须做**两件**事，顺序也不能反。
//
// 老实现只做了第二件（abort 本地 fetch），于是"停止"是个假动作：服务端那一轮继续
// 跑到底、继续烧 token、继续把结果写进库 —— 用户下次打开这个会话，会看到一条他以为
// 自己取消掉了的完整回复。
//
// 先 POST 再 abort：反过来的话连接已经断了，而 stop 是否送达就全看运气了。
// POST 失败也照样 abort —— 至少把界面还给用户，总比卡在"生成中"强。
async function stopStream() {
  // 先使准备中和已断开的回调失效，避免等停止请求返回时又提交本轮消息。
  const generation = ++openSessionGeneration
  sessionLoading.value = false
  const sessionId = currentSessionId.value
  const stop = stopCurrentStream
  if (sessionId) {
    try {
      await stopGeneration(sessionId)
    } catch (error) {
      if (generation !== openSessionGeneration) return
      connectionError.value = `停止请求没送达：${error instanceof Error ? error.message : String(error)}`
    }
  }
  stop?.()
  if (generation !== openSessionGeneration) return
  stopCurrentStream = null
  streamRunning.value = false
  resumeAvailable.value = false
  // 用户主动停的，不是断线 —— 别让重连逻辑把它当故障接回去。
  resumeAttempts = MAX_RESUME_ATTEMPTS
  const assistant = [...messages.value].reverse().find(item => item.role === 'assistant')
  if (assistant && assistant.status === 'running') assistant.status = 'done'
}

async function runApiProbe() {
  const started = performance.now()
  try {
    const headers: Record<string, string> = {}
    if (token.value.trim()) headers.Authorization = `Bearer ${token.value.trim()}`
    const response = await fetch(apiForm.value.path, {
      method: apiForm.value.method,
      headers,
      body: ['GET', 'DELETE'].includes(apiForm.value.method) || !apiForm.value.body.trim()
        ? undefined
        : JSON.stringify(JSON.parse(apiForm.value.body))
    })
    const text = await response.text()
    requestLogs.value.unshift({
      method: apiForm.value.method,
      path: apiForm.value.path,
      status: response.status,
      time: Math.round(performance.now() - started),
      body: text
    })
  } catch (error) {
    requestLogs.value.unshift({
      method: apiForm.value.method,
      path: apiForm.value.path,
      status: 'ERR',
      time: Math.round(performance.now() - started),
      body: error instanceof Error ? error.message : String(error)
    })
  }
  inspectorOpen.value = true
  inspectorTab.value = 'debug'
}

async function uploadFile(options: { file: UploadFileInfo }) {
  if (!options.file.file) return
  try {
    const uploaded = await uploadAttachment(options.file.file, currentSessionId.value || undefined)
    attachmentIds.value.push(uploaded.id)
    attachmentFiles.value.unshift({ id: uploaded.id, name: uploaded.filename, size: `${Math.max(1, Math.round(uploaded.file_size / 1024))} KB`, downloadable: uploaded.downloadable })
    inspectorOpen.value = true
    inspectorTab.value = 'files'
  } catch (error) {
    requestLogs.value.unshift({ method: 'POST', path: '/api/chat/attachments/upload', status: 'ERR', time: 0, body: error instanceof Error ? error.message : String(error) })
    // 存储超限不是"再试一次"能解决的 —— 重试一万遍都是 507。直接把用户带到文件库
    // 并把原话摆出来（后端那句已经写清了已用多少、该怎么办），他在那儿就能删。
    if (error instanceof ApiError && error.code === 'storage_quota_exceeded') {
      openLibrary()
      libraryError.value = error.message
      return
    }
    inspectorOpen.value = true
    inspectorTab.value = 'files'
  }
}

async function downloadUploadedAttachment(file: { id: number; name: string }) {
  try {
    const result = await downloadAttachment(file.id)
    const url = URL.createObjectURL(result.blob)
    const link = document.createElement('a')
    link.href = url
    link.download = file.name
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    requestLogs.value.unshift({ method: 'GET', path: `/api/chat/attachments/${file.id}/download`, status: 'ERR', time: 0, body: error instanceof Error ? error.message : String(error) })
  }
}

function formatBytes(bytes: number) {
  if (!bytes) return '0 B'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

// libraryKey 给每行一个稳定标识。id 在两张表里各自自增，会撞 —— upload#7 和
// artifact#7 是两个不同的文件，只用 id 做 key 会让 Vue 复用错行。
function libraryKey(file: UserFile) {
  return `${file.kind}:${file.id}`
}

async function loadUserLibrary() {
  libraryLoading.value = true
  libraryError.value = ''
  try {
    const [list, storage] = await Promise.all([
      listUserFiles(libraryKind.value || undefined, 100),
      getUserStorage()
    ])
    userFiles.value = list.files || []
    userStorage.value = storage
  } catch (error) {
    libraryError.value = error instanceof Error ? error.message : String(error)
  } finally {
    libraryLoading.value = false
  }
}

function openLibrary() {
  inspectorOpen.value = true
  inspectorTab.value = 'library'
  void loadUserLibrary()
}

async function switchLibraryKind(kind: '' | 'upload' | 'artifact') {
  libraryKind.value = kind
  await loadUserLibrary()
}

// importLibraryFile 只是本地暂存，不打任何接口——选中这一步不需要碰后端，
// 见 pendingLibraryImports 的说明。真正的导入延到 flushPendingLibraryImports，
// 跟着下一次发送一起做。
function importLibraryFile(file: UserFile) {
  if (!file.can_import) return
  const already = pendingLibraryImports.value.some(item => libraryKey(item) === libraryKey(file))
  if (!already) pendingLibraryImports.value.push(file)
  // 切回"文件"页让用户看见它已经排进本轮附件了 —— 否则选中之后界面毫无变化，
  // 分不清是选上了还是没反应。
  inspectorTab.value = 'files'
}

function removePendingLibraryImport(file: UserFile) {
  pendingLibraryImports.value = pendingLibraryImports.value.filter(item => libraryKey(item) !== libraryKey(file))
}

// flushPendingLibraryImports 在发送消息之前把暂存的库文件真正导入进会话。
// ensureCurrentSession 保证这时候一定有 session_id 可用——这一步不能再往后
// 推了：attachment_id 是导入这个动作的产物，发消息请求本身要带着它，没法
// 像 skill/tool 那样让后端在处理消息的同一个请求里顺带做掉。
async function flushPendingLibraryImports(generation = openSessionGeneration): Promise<void> {
  if (!pendingLibraryImports.value.length) return
  const imports = [...pendingLibraryImports.value]
  const sessionId = await ensureCurrentSession(generation)
  if (generation !== openSessionGeneration) return
  for (const file of imports) {
    const imported = await importFileToSession(sessionId, file.kind, file.id)
    if (generation !== openSessionGeneration) return
    attachmentIds.value.push(imported.attachment_id)
    attachmentFiles.value.unshift({
      id: imported.attachment_id,
      name: imported.filename,
      size: formatBytes(imported.file_size),
      downloadable: true
    })
  }
  pendingLibraryImports.value = []
}

async function removeLibraryFile(file: UserFile) {
  if (libraryBusyKey.value) return
  if (!window.confirm(`删除「${file.filename}」？原件会从文件库里移除，已经导入到会话里的副本不受影响。`)) return
  libraryBusyKey.value = libraryKey(file)
  try {
    await deleteUserFile(file.kind, file.id)
    userFiles.value = userFiles.value.filter(item => libraryKey(item) !== libraryKey(file))
    // 重新拉一次用量：删掉的文件可能跟别的行共享同一个对象（内容寻址），
    // 这时占用并不会降。前端自己减是猜的，问后端才是准的。
    userStorage.value = await getUserStorage()
  } catch (error) {
    libraryError.value = error instanceof Error ? error.message : String(error)
  } finally {
    libraryBusyKey.value = ''
  }
}

function pretty(value: unknown) {
  return typeof value === 'string' ? value : JSON.stringify(value || {}, null, 2)
}

// renderMarkdown 把消息正文过一遍 markdown-it 再交给 v-html。
//
// html: false（默认值，这里显式写出来不是多余——防止以后有人"顺手"加个
// html: true 就把 XSS 打开了）：模型生成的文本里可能包含看起来像标签的
// 字符串（用户复制粘贴、或者模型在解释代码时写了 <script> 这种词），
// 关掉之后这些原样转义成文本显示，不会被当成真 HTML 解析执行。
// linkify + breaks 是聊天场景该有的默认行为：裸链接自动可点，单个换行
// 当真换行——纯文本消息不会因为没有markdown空行就挤成一段。
const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

function renderMarkdown(text: string): string {
  return md.render(text || '')
}

// 用 highlight.js 的 core 版本手动注册几种语言，而不是引入全量包——
// 工作区文件预览常见的类型就这几种，没必要为了"以后可能用得上"的语言
// 把首屏 JS 体积再堆上去（这个应用已经因为 naive-ui 报过一次 500KB 警告）。
hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('typescript', typescript)
hljs.registerLanguage('python', python)
hljs.registerLanguage('json', json)
hljs.registerLanguage('xml', xml)
hljs.registerLanguage('css', css)
hljs.registerLanguage('bash', bash)

const EXT_TO_LANG: Record<string, string> = {
  js: 'javascript', mjs: 'javascript', jsx: 'javascript', vue: 'xml',
  ts: 'typescript', tsx: 'typescript',
  py: 'python', json: 'json', css: 'css', sh: 'bash', bash: 'bash'
}

// highlightPreviewText 给文本预览做语法高亮，猜不出语言（比如 .md/.txt/.log/.csv）
// 就原样转义显示——hljs 认不出的语言硬塞给它会直接抛错，不是退化成无高亮，
// 所以必须先自己判断一遍，不能无差别调用 highlightAuto（那个开销也更大，
// 对预览这种一次性的小文本没必要）。
function highlightPreviewText(text: string, filename: string): string {
  const ext = filename.split('.').pop()?.toLowerCase() || ''
  const lang = EXT_TO_LANG[ext]
  if (!lang) return escapeHtml(text)
  try {
    return hljs.highlight(text, { language: lang }).value
  } catch {
    return escapeHtml(text)
  }
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}
</script>

<template>
  <n-config-provider :theme="lightTheme">
    <main class="app-shell" :class="{ 'inspector-visible': inspectorOpen }">
      <aside class="sidebar">
        <div class="workspace-switcher">
          <div class="logo">L</div>
          <div>
            <strong>LEA Workspace</strong>
            <small>Personal AI Client</small>
          </div>
        </div>

        <div class="quick-actions">
          <n-button quaternary circle>
            <template #icon><n-icon :component="SearchOutline" /></template>
          </n-button>
          <n-button type="primary" circle @click="newConversation">
            <template #icon><n-icon :component="Add" /></template>
          </n-button>
        </div>

        <nav class="primary-nav">
          <button
            v-for="item in navItems"
            :key="item.key"
            :class="{ active: activeNav === item.key }"
            @click="openNavigation(item.key)"
          >
            <n-icon :component="item.icon" />
            <span>{{ item.label }}</span>
          </button>
        </nav>

        <section class="sidebar-section">
          <header>
            <span>项目</span>
            <button class="section-add" title="创建项目" @click="projectFormOpen = true"><n-icon :component="Add" /></button>
          </header>
          <button v-for="project in projects" :key="project.id" class="project-row" :class="{ active: selectedProjectId === project.id }" @click="openProject(project)">
            <span>{{ project.name }}</span>
            <small>{{ project.session_count || 0 }}</small>
          </button>
          <small v-if="!projects.length" class="sidebar-empty">暂无项目</small>
        </section>

        <section class="sidebar-section grow">
          <header>
            <span>最近对话</span>
            <n-icon :component="ChatbubblesOutline" />
          </header>
          <!-- 外层是 div 而不是 button：删除键要嵌在里面，而 button 套 button 是非法
               HTML，浏览器会把内层拆出去，点击区域随之错位。 -->
          <div
            v-for="conversation in conversations"
            :key="conversation.id"
            class="conversation-row"
            :class="{ active: selectedConversation === conversation.id }"
            role="button"
            tabindex="0"
            @click="selectedConversation = conversation.id; activeNav = 'chat'; openSession(conversation.id)"
            @keydown.enter="selectedConversation = conversation.id; activeNav = 'chat'; openSession(conversation.id)"
          >
            <span>{{ conversation.title }}</span>
            <small>{{ conversation.meta }}</small>
            <button class="conversation-delete" title="删除会话" :disabled="submitBusyKey === 'conversation-delete'" @click.stop="removeConversation(conversation.id)">×</button>
          </div>
          <small v-if="!conversations.length" class="sidebar-empty">暂无会话，点击上方加号开始</small>
        </section>
      </aside>

      <section class="main-stage">
        <header class="topbar">
          <div>
            <p class="eyebrow">Current Space</p>
            <h1>{{ activeTitle }}</h1>
          </div>
          <div class="topbar-actions">
            <n-tag v-if="token" :bordered="false" type="success">已连接</n-tag>
            <n-button v-else secondary @click="inspectorOpen = true; inspectorTab = 'debug'">连接账号</n-button>
            <n-button secondary @click="inspectorOpen = !inspectorOpen">
              <template #icon>
                <n-icon :component="inspectorOpen ? ChevronForwardOutline : ChevronBackOutline" />
              </template>
              文件区
            </n-button>
          </div>
        </header>

        <section v-if="activeNav === 'chat'" class="conversation-stage">
          <div class="session-header">
            <div>
            <h2>{{ conversations.find(item => item.id === selectedConversation)?.title || '新对话' }}</h2>
              <p>{{ activeConfigurationName }} · {{ currentSessionId ? '会话已连接 · POST SSE' : '发送第一条消息后创建会话' }}</p>
            </div>
            <n-tag :bordered="false" :type="streamRunning ? 'warning' : 'success'">{{ streamRunning ? '生成中' : 'SSE 就绪' }}
            </n-tag>
          </div>

          <div class="message-list" ref="messageListEl">
            <article v-for="message in messages" :key="message.id" class="message" :class="message.role">
              <div class="avatar">{{ message.author.slice(0, 1) }}</div>
              <div class="bubble">
                <header>
                  <strong>{{ message.author }}</strong>
                  <div class="bubble-header-right">
                    <n-tag
                      v-if="message.status"
                      size="small"
                      :type="message.status === 'failed' ? 'error' : message.status === 'running' ? 'warning' : 'success'"
                    >
                      {{ message.status }}
                    </n-tag>
                    <!-- 跑完立刻显示（usage SSE 事件），不是逐轮实时更新——引擎没有
                         "一轮完成"的回调，做不到那个程度，见后端 usageSnapshot 的说明。 -->
                    <small v-if="message.usage" class="token-usage">输入 {{ message.usage.inputTokens }} · 输出 {{ message.usage.outputTokens }} tokens</small>
                  </div>
                </header>
                <!-- markdown-it 渲染，见 renderMarkdown 的说明——html 关着，
                     模型/用户输入里的尖括号原样转义显示，不会被当成真标签解析。 -->
                <div class="markdown-body" v-html="renderMarkdown(message.text)" />
                <div v-if="message.activities?.length" class="activity-timeline">
                  <div v-for="activity in message.activities" :key="activity.id" class="activity-item" :class="`activity-${activity.kind}`">
                    <!-- 只有"通用原始输出"这一种值得收起——沙箱的代码/输出、AskUser
                         的问题都是用户马上要看/要回答的内容，收起反而添乱，见下面
                         各分支自己决定要不要认 expandedActivities。点击标题切换，
                         光标只在真的有内容可展开时才显示"能点"。 -->
                    <div
                      class="activity-heading"
                      :class="{ clickable: !!activity.detail && !activity.sandbox && !activity.askUser }"
                      @click="activity.detail && !activity.sandbox && !activity.askUser && toggleActivity(activity.id)"
                    >
                      <span class="activity-dot" />
                      <strong>{{ activity.title }}</strong>
                      <n-tag size="tiny" :type="activity.status === 'failed' || activity.status === 'error' ? 'error' : activity.status === 'running' || activity.status === 'started' ? 'warning' : 'success'">{{ activity.status || '已完成' }}</n-tag>
                      <small v-if="activity.sandbox?.durationMs">{{ activity.sandbox.durationMs }} ms</small>
                      <small v-if="activity.detail && !activity.sandbox && !activity.askUser" class="activity-toggle-hint">{{ expandedActivities.has(activity.id) ? '收起 ▲' : '展开 ▼' }}</small>
                    </div>
                    <!-- 沙箱走结构化渲染：代码 / stdout / stderr / 产物各自成块。
                         stderr 单独标红是最要紧的一处 —— 代码跑挂时 traceback 就在
                         那里，以前它和代码一样是灰底 JSON 的一部分。 -->
                    <template v-if="activity.sandbox">
                      <pre v-if="activity.sandbox.code" class="activity-detail sandbox-code">{{ activity.sandbox.code }}</pre>
                      <pre v-if="activity.sandbox.stdout" class="activity-detail">{{ activity.sandbox.stdout }}</pre>
                      <pre v-if="activity.sandbox.stderr" class="activity-detail sandbox-stderr">{{ activity.sandbox.stderr }}</pre>
                      <p v-if="activity.sandbox.message && !activity.sandbox.stdout && !activity.sandbox.stderr" class="sandbox-message">{{ activity.sandbox.message }}</p>
                      <p v-if="activity.sandbox.truncated" class="sandbox-message">输出过长已被截断，完整内容见工作区文件。</p>
                      <div v-if="activity.sandbox.artifacts.length" class="sandbox-artifacts">
                        <small>产物</small>
                        <button v-for="artifact in activity.sandbox.artifacts" :key="artifact.path" class="sandbox-artifact" :title="artifact.path" @click="openSandboxArtifact(artifact)">
                          {{ artifact.name }}<span v-if="artifact.size"> · {{ formatBytes(artifact.size) }}</span>
                        </button>
                      </div>
                    </template>
                    <template v-else-if="activity.askUser">
                      <div class="activity-detail ask-user-question markdown-body" v-html="renderMarkdown(activity.askUser.question)" />
                      <div v-if="activity.askUser.candidates.length" class="ask-user-candidates">
                        <n-button
                          v-for="candidate in activity.askUser.candidates" :key="candidate"
                          size="small" secondary :disabled="activity.askUser.answered || streamRunning"
                          @click="answerAskUser(activity, candidate)"
                        >{{ candidate }}</n-button>
                      </div>
                    </template>
                    <pre v-else-if="activity.detail && expandedActivities.has(activity.id)" class="activity-detail">{{ activity.detail }}</pre>
                  </div>
                </div>
                <div v-if="message.planApproval" class="plan-approval-card">
                  <div class="plan-approval-heading">
                    <span class="plan-approval-mark">PLAN</span>
                    <strong>需要确认执行计划</strong>
                  </div>
                  <p>{{ message.planApproval.question }}</p>
                  <div v-if="message.planApproval.options.length" class="plan-options">
                    <span v-for="option in message.planApproval.options" :key="option">{{ option }}</span>
                  </div>
                  <div class="plan-approval-actions" v-if="!message.planApproval.resolved">
                    <n-button size="small" type="primary" :disabled="streamRunning" @click="resolvePlan(message, 'approve')">批准计划</n-button>
                    <n-button size="small" secondary :disabled="streamRunning" @click="resolvePlan(message, 'reject')">拒绝</n-button>
                  </div>
                  <n-tag v-else size="small" :type="message.planApproval.resolved === 'approved' ? 'success' : 'warning'">
                    {{ message.planApproval.resolved === 'approved' ? '已批准，正在执行' : '已拒绝' }}
                  </n-tag>
                </div>
              </div>
            </article>
          </div>

          <div class="composer">
            <div v-if="sessionAttachments.length || pendingAttachments.length" class="attachment-chips composer-chips">
              <n-tag v-for="resource in sessionAttachments" :key="`${resource.kind}-${resource.ref_id}`" size="small" closable @close="unmountResource(resource)">
                {{ resource.display_name || resource.name || `${resource.kind} #${resource.ref_id}` }}
              </n-tag>
              <!-- 待挂载：只是本地暂存，还没打过任何接口，点发送才会真正生效。
                   type="warning" 跟上面已挂载的区分开，免得用户以为选完就已经生效了。 -->
              <n-tag
                v-for="(item, index) in pendingAttachments" :key="`pending-${item.kind}-${item.ref_id}`"
                size="small" type="warning" closable @close="removePendingAttachment(index)"
              >
                {{ item.name }}（待发送时挂载）
              </n-tag>
            </div>
            <div v-if="configurationPanelOpen" class="session-config-panel composer-panel">
              <select :value="chatMode" aria-label="聊天模式" @change="selectChatMode(($event.target as HTMLSelectElement).value as ChatMode)">
                <option value="default">默认 Super Agent</option>
                <option value="plan" :disabled="handoffTeamActive">Plan{{ handoffTeamActive ? '（接力型不可用）' : '' }}</option>
                <option value="expert">专家</option>
                <option value="team">专家团</option>
              </select>
              <small v-if="chatMode === 'plan'" class="sidebar-empty">Plan 会让当前对话先产出计划；批准后再进入执行阶段。</small>
              <template v-if="chatMode === 'expert' || chatMode === 'team'">
              <select v-model="configurationSelection" aria-label="选择专家配置" @change="applySelectedConfiguration">
                <option v-for="configuration in modeConfigurations" :key="configuration.value" :value="configuration.value">{{ configuration.label }}</option>
              </select>
              </template>
            </div>
            <div v-if="modelPanelOpen" class="session-config-panel composer-panel">
              <!-- tag 打开 = 列表里没有的名字仍然能手输。租户没配 llm_providers 时
                   列表是空的，纯下拉会让"选择模型"这件事彻底做不了。 -->
              <n-select
                :value="modelDraft" :options="modelOptions" filterable tag
                placeholder="选择或输入模型名称" style="min-width: 240px"
                @update:value="selectCurrentModel"
              />
              <small v-if="!chatModels.length" class="toolbar-label">没读到可选模型列表，可直接输入名称。</small>
            </div>
            <div v-if="mountPanelOpen" class="session-config-panel mount-panel composer-panel">
              <select v-model="mountKind" @change="mountSelection = 0"><option value="skill">Skill</option><option value="mcp_server">MCP</option><option value="tool">Tool</option></select>
              <select v-model.number="mountSelection" @change="addPendingAttachment"><option :value="0">选择要挂载的资源</option><option v-for="option in mountOptions" :key="option.id" :value="option.id">{{ option.name }}</option></select>
              <small v-if="!mountOptions.length" class="sidebar-empty">当前类型暂无可用资源，请先在市场配置。</small>
              <small class="sidebar-empty">选中即加入下方列表，不会立刻生效，跟随下一次发送一起挂载。</small>
            </div>
            <n-input
              v-model:value="prompt"
              type="textarea"
              placeholder="向当前专家、专家团或项目提问...（Enter 发送，Shift+Enter 换行）"
              :autosize="{ minRows: 2, maxRows: 6 }"
              @keydown.enter.exact.prevent="addMessage"
            />
            <div class="composer-actions">
              <n-button quaternary circle title="临时挂载 Skill、MCP 或 Tool" @click="mountPanelOpen = !mountPanelOpen; modelPanelOpen = false">
                <template #icon><n-icon :component="Add" /></template>
              </n-button>
              <n-button quaternary title="选择聊天模式、专家或专家团" @click="configurationPanelOpen = !configurationPanelOpen; mountPanelOpen = false; modelPanelOpen = false">
                模式 · {{ chatModeLabel(chatMode) }}
              </n-button>
              <n-button quaternary @click="modelPanelOpen = !modelPanelOpen; mountPanelOpen = false">模型 · {{ modelDraft }}
              </n-button>
              <n-upload :custom-request="uploadFile" :show-file-list="false">
                <n-button secondary>
                  <template #icon><n-icon :component="AttachOutline" /></template>
                  附件
                </n-button>
              </n-upload>
              <!-- 生成中把"发送"换成"停止" —— 两个按钮并排的话，生成中那个发送键
                   是禁用的死键（sendChatMessage 开头就 return），占着位置还误导人。 -->
              <n-button v-if="streamRunning" type="error" secondary @click="stopStream">停止生成</n-button>
              <n-button v-else-if="resumeAvailable" type="warning" secondary @click="retryResume">重新连接</n-button>
              <n-button v-else type="primary" :disabled="sessionLoading" @click="addMessage">发送</n-button>
            </div>
          </div>
        </section>

        <section v-else-if="activeNav === 'experts'" class="content-board">
          <div class="segmented">
            <button :class="{ active: expertTab === 'experts' }" @click="expertTab = 'experts'">专家</button>
            <button :class="{ active: expertTab === 'teams' }" @click="expertTab = 'teams'">专家团</button>
            <n-button type="primary" @click="expertTab === 'teams' ? openCreateTeamForm() : openCreateExpertForm()">
              <template #icon><n-icon :component="Add" /></template>
              创建
            </n-button>
          </div>
          <div class="asset-grid">
            <article v-if="expertTab === 'teams' && !teamItems.length" class="asset-card empty-card">
              <n-icon :component="PeopleOutline" />
              <h3>暂无专家团</h3>
              <p>主专家负责主持和调度；成员专家参与分析与执行。</p>
            </article>
            <article v-for="team in (expertTab === 'teams' ? teamItems : [])" :key="team.id" class="asset-card clickable-card" @click="openTeamForm(team)">
              <n-icon :component="PeopleOutline" />
              <h3>专家团 · {{ team.name || `#${team.id}` }}</h3>
              <p>主专家：{{ expertItems.find(expert => expert.id === team.agent_id)?.name || `#${team.agent_id}` }} · {{ team.strategy === 'handoff' ? '接力协作' : '星型协作' }}</p>
              <div class="tag-row"><n-tag size="small">最多 {{ team.max_subtasks || 5 }} 个子任务
              </n-tag></div>
              <!-- 卡片本体就是"配置"入口（点开弹窗改参数 + 管成员），所以这里不再
                   单独放"成员配置"和"编辑"两个按钮，跟知识库卡片保持一致。 -->
              <div class="card-actions" @click.stop>
                <n-button size="small" type="primary" @click="useTeam(team)">用于新对话</n-button>
                <n-button size="small" secondary @click="openTeamForm(team)">配置</n-button>
                <n-button v-if="expertItems.find(expert => expert.id === team.agent_id)?.is_mine" size="small" quaternary :loading="submitBusyKey === 'team-delete'" :disabled="!!submitBusyKey" @click="removeExpertTeam(team)">删除</n-button>
              </div>
            </article>
            <!-- 只有自己建的专家点卡片才进编辑弹窗。别人的/平台预置的（is_mine=false，
                 种子数据 created_by 为空）后端 _require_mine 一律 403，让它能点开一个
                 改不了的表单只会让人以为"改了没保存上"。 -->
            <article
              v-for="expert in (expertTab === 'experts' ? expertItems : [])" :key="expert.id" class="asset-card"
              :class="{ 'clickable-card': expert.is_mine }"
              @click="expert.is_mine && editExpert(expert)"
            >
              <n-icon :component="AlbumsOutline" />
              <h3>{{ expert.name }}</h3>
              <p>{{ expert.description || '暂无描述' }}</p>
              <div class="tag-row"><n-tag size="small">{{ expert.category || '通用' }}</n-tag><n-tag v-if="expert.model" size="small">{{ expert.model }}</n-tag><n-tag v-if="!expert.is_mine" size="small" type="warning">只读</n-tag></div>
              <div class="card-actions" @click.stop><n-button size="small" type="primary" @click="useExpert(expert)">用于新对话</n-button><n-button v-if="expert.is_mine" size="small" secondary @click="editExpert(expert)">配置</n-button><n-button v-if="expert.is_mine" size="small" quaternary @click="openSharePanel(expert)">共享</n-button><n-button v-if="expert.is_mine" size="small" quaternary :loading="submitBusyKey === 'expert-delete'" :disabled="!!submitBusyKey" @click="removeExpert(expert)">删除</n-button></div>
            </article>
          </div>

          <!-- 共享面板。先把预检结果摊开，再让用户决定 —— 直接给个"共享"按钮的话，
               被拦的用户只会看到一句"有 N 项没共享"，而不知道是哪几项。 -->
          <div v-if="sharePanelExpert" class="tool-create-form">
            <div class="preview-header">
              <strong>共享「{{ sharePanelExpert.name }}」</strong>
              <n-button size="small" quaternary @click="sharePanelExpert = null">关闭</n-button>
            </div>
            <p v-if="shareBusy && !shareCheck" class="market-status">正在检查…</p>
            <template v-else-if="shareCheck">
              <p v-if="shareCheck.already_shared" class="market-status">这个专家已经共享到租户，租户里的人都能用。</p>
              <p v-else-if="shareCheck.can_share" class="market-status">可以共享。共享后租户内所有人都能使用它。</p>
              <p v-else class="market-status error">还不能共享，先处理下面 {{ shareCheck.blockers.length }} 项。</p>

              <!-- blockers 硬拦：这些资源是私有的，跟着专家共享出去等于泄露。 -->
              <div v-for="blocker in shareCheck.blockers" :key="`b-${blocker.kind}-${blocker.id}`" class="library-error">
                <strong>{{ blocker.name }}</strong>（{{ blocker.kind }}）— {{ blocker.reason }}
              </div>
              <!-- warnings 只提示：不构成泄露，但别人用起来的效果会跟作者不一样。 -->
              <div v-for="warning in shareCheck.warnings" :key="`w-${warning.kind}-${warning.id}`" class="share-warning">
                <strong>{{ warning.name }}</strong>（{{ warning.kind }}）— {{ warning.reason }}
              </div>

              <div class="plan-approval-actions">
                <n-button v-if="shareCheck.already_shared" :loading="shareBusy" secondary @click="confirmUnshare">撤回共享</n-button>
                <n-button v-else type="primary" :disabled="!shareCheck.can_share" :loading="shareBusy" @click="confirmShare">确认共享</n-button>
                <n-button quaternary :loading="shareBusy" @click="openSharePanel(sharePanelExpert)">重新检查</n-button>
              </div>
            </template>
          </div>
          <p v-if="shareNotice" class="market-status">{{ shareNotice }}</p>
          <!-- 成功之后仍然要显示的软提示 —— 面板这时已经关了。 -->
          <div v-for="warning in shareWarnings" :key="`sw-${warning.kind}-${warning.id}`" class="share-warning">
            <strong>{{ warning.name }}</strong> — {{ warning.reason }}
          </div>
          <!-- 专家：创建和编辑一个弹窗。关闭统一走 closeExpertForm()（保存成功/取消/
               遮罩/Esc），跟知识库弹窗同一个套路。 -->
          <n-modal
            :show="expertFormOpen"
            preset="card"
            :title="editingExpertId ? '配置专家' : '创建专家'"
            style="width: min(720px, calc(100vw - 32px)); max-height: 85vh; overflow-y: auto;"
            @update:show="value => { if (!value) closeExpertForm() }"
          >
            <div class="tool-create-form">
              <div class="form-grid">
                <n-input v-model:value="expertForm.name" placeholder="专家名称" />
                <select v-model="expertForm.category" aria-label="专家分类">
                  <option value="">选择分类</option><option value="general">通用</option><option value="writing">写作</option><option value="data">数据</option><option value="research">研究</option><option value="development">开发</option>
                </select>
                <n-select v-model:value="expertForm.model" :options="modelOptions" filterable tag placeholder="选择或输入模型" />
              </div>
              <n-input v-model:value="expertForm.description" placeholder="专家描述" />
              <n-input v-model:value="expertForm.soul_prompt" type="textarea" placeholder="系统提示词" :autosize="{ minRows: 4, maxRows: 8 }" />
              <label class="config-field"><span>绑定 Skill</span><select v-model="expertForm.skill_ids" multiple><option v-for="skill in marketItems.skills" :key="skill.id" :value="skill.id">{{ skill.display_name || skill.name }}</option></select></label>
              <label class="config-field"><span>绑定 MCP 服务</span><select v-model="expertForm.mcp_ids" multiple><option v-for="mcp in marketItems.mcp" :key="mcp.id" :value="mcp.id">{{ mcp.name }}</option></select></label>
              <label class="config-field"><span>绑定工具</span><select v-model="expertForm.tool_ids" multiple><option v-for="tool in marketItems.tools" :key="tool.id" :value="tool.id">{{ tool.display_name || tool.name }}</option></select></label>
              <label class="config-field"><span>绑定知识库</span><select v-model="expertForm.kb_ids" multiple><option v-for="kb in knowledgeBases" :key="kb.id" :value="kb.id">{{ kb.name }}</option></select></label>
              <div class="card-actions">
                <n-button type="primary" :loading="submitBusyKey === 'expert-save'" :disabled="!!submitBusyKey" @click="createMarketExpert">保存</n-button>
                <n-button quaternary @click="closeExpertForm">取消</n-button>
              </div>
            </div>
          </n-modal>

          <!-- 专家团：团参数 + 成员名单合到一个弹窗。以前"编辑"和"成员配置"是两个
               各自弹出的面板，成员那块还跟聊天选中的团共用一个 selectedTeamId。 -->
          <n-modal
            :show="teamFormOpen"
            preset="card"
            :title="editingTeamId ? '配置专家团' : '创建专家团'"
            style="width: min(720px, calc(100vw - 32px)); max-height: 85vh; overflow-y: auto;"
            @update:show="value => { if (!value) closeTeamForm() }"
          >
            <div class="tool-create-form">
              <div class="form-grid">
                <select v-model.number="teamForm.agent_id">
                  <option :value="0">选择主专家</option>
                  <option v-for="expert in expertItems" :key="expert.id" :value="expert.id">{{ expert.name }}</option>
                </select>
                <select v-model="teamForm.strategy" aria-label="专家团协作模式">
                  <option value="star">星型协作</option>
                  <option value="handoff">接力协作</option>
                </select>
              </div>
              <n-input v-model:value="teamForm.decomposer_prompt" placeholder="拆解提示词" />
              <n-input v-model:value="teamForm.synthesizer_prompt" placeholder="汇总提示词" />
              <n-input-number v-model:value="teamForm.max_subtasks" :min="1" :max="20" />
              <div class="card-actions">
                <n-button type="primary" :loading="submitBusyKey === 'team-save'" :disabled="!!submitBusyKey" @click="createMarketTeam">保存</n-button>
                <n-button quaternary @click="closeTeamForm">取消</n-button>
              </div>

              <n-divider />
              <!-- 新建时还没有 team_id，加不了成员（addTeamMember 要真 id），所以这里
                   退化成一个多选，等团建出来再一次性挂上去；编辑态才给逐条增删 + 角色。 -->
              <template v-if="editingTeamId">
                <div class="preview-header"><strong>成员</strong></div>
                <div v-if="teamMembers.length" class="member-list">
                  <div v-for="member in teamMembers" :key="member.id" class="document-row">
                    <span>{{ member.name || expertItems.find(expert => expert.id === member.member_agent_id)?.name || `专家 #${member.member_agent_id}` }}</span>
                    <n-tag size="small">{{ member.role || 'worker' }}</n-tag>
                    <n-button size="small" quaternary :loading="submitBusyKey === 'team-member-delete'" :disabled="!!submitBusyKey" @click="deleteTeamMember(member)">移除</n-button>
                  </div>
                </div>
                <p v-else>还没有成员。</p>
                <div class="form-grid">
                  <select v-model.number="teamMemberForm.member_agent_id"><option :value="0">选择成员专家</option><option v-for="expert in expertItems.filter(expert => expert.id !== teamForm.agent_id)" :key="expert.id" :value="expert.id">{{ expert.name }}</option></select>
                  <select v-model="teamMemberForm.role"><option value="entry">入口</option><option value="worker">执行</option><option value="exit">出口</option></select>
                  <n-input v-model:value="teamMemberForm.name" placeholder="成员显示名（可选）" />
                </div>
                <n-button secondary :disabled="!teamMemberForm.member_agent_id || !!submitBusyKey" :loading="submitBusyKey === 'team-member-add'" @click="saveTeamMember">添加成员</n-button>
              </template>
              <label v-else class="config-field">
                <span>团队成员专家</span>
                <select v-model="teamForm.member_ids" multiple><option v-for="expert in expertItems.filter(expert => expert.id !== teamForm.agent_id)" :key="expert.id" :value="expert.id">{{ expert.name }}</option></select>
              </label>
            </div>
          </n-modal>
        </section>

        <section v-else-if="activeNav === 'market'" class="content-board">
          <div class="segmented">
            <button :class="{ active: marketTab === 'skills' }" @click="marketTab = 'skills'; marketCategory = 'all'">Skills</button>
            <button :class="{ active: marketTab === 'mcp' }" @click="marketTab = 'mcp'; marketCategory = 'all'">MCP</button>
            <button :class="{ active: marketTab === 'tools' }" @click="marketTab = 'tools'; marketCategory = 'all'">Tools</button>
            <n-button v-if="marketTab === 'skills'" secondary @click="skillUploadOpen = true">上传 Skill</n-button>
            <n-button v-if="marketTab === 'skills'" quaternary @click="togglePublishRequests">{{ publishRequestsOpen ? '收起申请' : '上架申请' }}</n-button>
            <n-button v-if="marketTab === 'mcp'" type="primary" @click="mcpFormOpen = !mcpFormOpen">{{ mcpFormOpen ? '收起创建' : '创建我的 MCP' }}</n-button>
            <n-button v-if="marketTab === 'tools'" type="primary" @click="toolFormOpen = !toolFormOpen">{{ toolFormOpen ? '收起创建' : '创建 Tool' }}</n-button>
            <n-button quaternary @click="loadMarkets">刷新市场</n-button>
          </div>
          <div class="market-categories">
            <button v-for="category in marketCategories" :key="category" :class="{ active: marketCategory === category }" @click="marketCategory = category">
              {{ marketCategoryLabel(category) }}
              <span class="market-category-count">{{ marketCategoryCount(category) }}</span>
            </button>
          </div>
          <p v-if="marketLoading" class="market-status">正在加载真实市场数据...</p>
          <p v-else-if="marketError" class="market-status error">{{ marketError }}</p>
          <p v-else-if="!visibleMarketItems.length" class="market-status">当前市场暂无可见资源。</p>
          <!-- 上架申请。同一块界面对两类人有两种含义（自己的申请 / 待审队列），
               由后端按角色决定返回哪一批，前端不判断。 -->
          <div v-if="marketTab === 'skills' && publishRequestsOpen" class="tool-create-form">
            <div class="preview-header">
              <strong>上架申请</strong>
              <div class="library-filters">
                <button v-for="status in (['pending', 'approved', 'rejected'] as const)" :key="status"
                  :class="{ active: publishRequestsStatus === status }"
                  @click="publishRequestsStatus = status; loadPublishRequests()">
                  {{ status === 'pending' ? '在审' : status === 'approved' ? '已通过' : '已驳回' }}
                </button>
              </div>
            </div>
            <p v-if="publishRequestsError" class="library-error">{{ publishRequestsError }}</p>
            <p v-else-if="!publishRequests.length" class="market-status">这一档暂无申请。</p>
            <div v-for="request in publishRequests" :key="request.id" class="library-row">
              <div class="library-main">
                <span class="library-name">{{ request.skill_name }}</span>
                <n-tag size="small">{{ request.target_scope === 'global' ? '上架到全平台' : '共享到租户' }}</n-tag>
              </div>
              <div class="library-meta">
                <span>{{ request.requested_by }} 提交{{ request.created_at ? ` · ${request.created_at}` : '' }}</span>
                <span v-if="request.review_note">审核意见：{{ request.review_note }}</span>
              </div>
              <!-- 审核按钮对没有权限的人也会显示，点了返 403。
                   不在前端按角色藏起来是有意的：藏与不藏的判据一旦和后端分叉，
                   结果是"按钮在但点不动"或"有权限却看不到按钮"，两种都更难查。 -->
              <div v-if="request.status === 'pending'" class="library-actions">
                <n-input v-model:value="publishReviewNote[request.id]" size="small" placeholder="审核意见（可选）" />
                <n-button size="small" type="primary" @click="reviewRequest(request, true)">通过</n-button>
                <n-button size="small" quaternary @click="reviewRequest(request, false)">驳回</n-button>
              </div>
            </div>
          </div>
          <div v-if="marketTab === 'tools' && toolFormOpen" class="tool-create-form">
            <div class="form-grid">
              <n-input v-model:value="toolForm.name" placeholder="工具名称，例如 weather_lookup" />
              <n-input v-model:value="toolForm.display_name" placeholder="显示名称" />
              <select v-model="toolForm.category"><option value="general">通用</option><option value="search">搜索</option><option value="data">数据</option><option value="file">文件</option><option value="automation">自动化</option></select>
            </div>
            <n-input v-model:value="toolForm.endpoint" placeholder="Endpoint（API 类型填写）" />
            <n-input v-model:value="toolForm.description" placeholder="工具描述" />
            <n-input v-model:value="toolForm.schema_json" type="textarea" placeholder="JSON Schema" :autosize="{ minRows: 5, maxRows: 10 }" />
            <n-button type="primary" :loading="submitBusyKey === 'tool-save'" :disabled="!!submitBusyKey" @click="createMarketTool">保存 Tool</n-button>
          </div>
          <div v-if="marketTab === 'mcp' && mcpFormOpen" class="tool-create-form">
            <div class="form-grid">
              <n-input v-model:value="mcpForm.name" placeholder="MCP 名称，例如 notion" />
              <n-input v-model:value="mcpForm.endpoint" placeholder="https://example.com/mcp" />
              <select v-model="mcpForm.category"><option value="general">通用</option><option value="search">搜索</option><option value="data">数据</option><option value="productivity">效率</option><option value="development">开发</option></select>
            </div>
            <n-input v-model:value="mcpForm.description" placeholder="用途说明" />
            <n-input v-model:value="mcpForm.bearerToken" type="password" show-password-on="click" placeholder="Bearer Token（可选，仅保存到私有 MCP）" />
            <n-input v-model:value="mcpForm.toolFilter" placeholder="允许的工具，逗号分隔（留空表示全部）" />
            <n-button type="primary" :loading="submitBusyKey === 'mcp-save'" :disabled="!!submitBusyKey" @click="createMarketMcp">保存并添加</n-button>
          </div>
          <div class="market-list">
            <article v-for="item in visibleMarketItems" :key="item.id">
              <n-icon :component="marketTab === 'skills' ? BookOutline : marketTab === 'mcp' ? AppsOutline : TerminalOutline" />
              <div>
                <h3>{{ marketItemTitle(item) }}</h3>
                <p>{{ marketItemDescription(item) }}</p>
                <small class="market-category-value">{{ marketCategoryLabel(marketCategoryKey(item.category)) }}</small>
                <small v-if="marketTab === 'mcp' && (item as McpMarketItem).endpoint" class="endpoint-value">{{ (item as McpMarketItem).endpoint }}</small>
                <small v-if="marketTab === 'mcp' && (item as McpMarketItem).last_error" class="connection-error">{{ (item as McpMarketItem).last_error }}</small>
              </div>
              <n-button v-if="marketTab === 'mcp'" secondary @click="toggleMarketMcp(item as McpMarketItem)">{{ (item as McpMarketItem).enabled ? '停用' : '启用' }}
              </n-button>
              <n-button v-if="marketTab === 'mcp'" quaternary @click="syncMarketMcp(item as McpMarketItem)">同步
              </n-button>
              <n-button v-if="marketCanEdit(item)" size="small" secondary @click="editMarketItem(item)">编辑</n-button>
              <n-button v-if="marketCanEdit(item)" size="small" quaternary :loading="submitBusyKey === 'market-item-delete'" :disabled="!!submitBusyKey" @click="removeMarketItem(item)">删除</n-button>
              <n-button v-if="marketTab === 'skills' && (item as SkillMarketItem).can_edit && !(item as SkillMarketItem).pending_publish" size="small" quaternary @click="openSkillPublish(item as SkillMarketItem)">发布</n-button>
            </article>
          </div>
        </section>

        <section v-else-if="activeNav === 'knowledge'" class="content-board">
          <div class="segmented">
            <h2>知识库</h2>
            <n-button type="primary" @click="openCreateKnowledgeBaseForm">创建知识库</n-button>
          </div>
          <div class="asset-grid">
            <article v-for="knowledgeBase in knowledgeBases" :key="knowledgeBase.id" class="asset-card clickable-card" @click="openKnowledgeBase(knowledgeBase)">
              <n-icon :component="LibraryOutline" />
              <h3>{{ knowledgeBase.name }}</h3>
              <p>{{ knowledgeBase.description || '暂无描述' }}</p>
              <div class="tag-row"><n-tag size="small">{{ knowledgeBase.document_count || 0 }} 个文档</n-tag><n-tag size="small">{{ knowledgeBase.visibility || 'private' }}</n-tag></div>
              <div class="card-actions" @click.stop>
                <n-button size="small" quaternary :loading="submitBusyKey === 'kb-delete'" :disabled="!!submitBusyKey" @click="removeKnowledgeBase(knowledgeBase)">删除</n-button>
              </div>
            </article>
          </div>

          <!-- 创建和编辑合成一个弹窗：点卡片直接进编辑态、同时能看/传文档，不再单独
               放一个"编辑"按钮。关闭方式（保存成功/点取消/点遮罩/Esc）统一走
               closeKnowledgeBaseForm()，跟下面"发布 Skill"弹窗一个套路。 -->
          <n-modal
            :show="knowledgeFormOpen"
            preset="card"
            :title="editingKnowledgeBaseId ? '编辑知识库' : '创建知识库'"
            style="width: min(720px, calc(100vw - 32px)); max-height: 85vh; overflow-y: auto;"
            @update:show="value => { if (!value) closeKnowledgeBaseForm() }"
          >
            <div class="tool-create-form">
              <n-input v-model:value="knowledgeForm.name" placeholder="知识库名称" />
              <n-input v-model:value="knowledgeForm.description" placeholder="知识库描述" />
              <select v-model="knowledgeForm.visibility"><option value="private">私有</option><option value="shared">同租户共享</option><option value="public">公开</option></select>
              <!-- embedding_model 只在新建、或编辑一个还没有文档的库时可改——换模型
                   意味着老向量全部失效，见 server_user_kb.py 里对应的显式 400。 -->
              <n-select
                v-model:value="knowledgeForm.embedding_model" :options="embeddingModelOptions" filterable tag
                :disabled="!!editingKnowledgeBaseId && editingKnowledgeBaseDocCount > 0"
                placeholder="选择或输入 embedding 模型（默认 bge-m3）" style="min-width: 220px"
              />
              <small v-if="!!editingKnowledgeBaseId && editingKnowledgeBaseDocCount > 0" class="toolbar-label">该知识库已有文档，embedding 模型不可更改，需新建知识库重新导入。</small>
              <small v-else-if="!embeddingModelOptions.length" class="toolbar-label">没读到可选 embedding 模型列表，可直接输入名称。</small>
              <n-select
                v-model:value="knowledgeForm.reranker_model" :options="rerankerModelOptions" filterable tag clearable
                placeholder="选择或输入 rerank 模型（可留空）" style="min-width: 220px"
              />
              <n-select
                v-model:value="knowledgeForm.vlm_model" :options="vlmModelOptions" filterable tag clearable
                placeholder="选择或输入 VLM 模型（可留空，用于图片/多模态解析）" style="min-width: 220px"
              />
              <div class="tag-row">
                <span class="toolbar-label">启用 rerank</span><n-switch v-model:value="knowledgeForm.reranker_enabled" size="small" />
                <span class="toolbar-label">启用 VLM 解析</span><n-switch v-model:value="knowledgeForm.vlm_enabled" size="small" />
                <span class="toolbar-label">多模态检索</span><n-switch v-model:value="knowledgeForm.multimodal" size="small" />
              </div>
              <div class="card-actions">
                <n-button type="primary" :loading="submitBusyKey === 'kb-save'" :disabled="!!submitBusyKey" @click="createMarketKnowledgeBase">保存</n-button>
                <n-button quaternary @click="closeKnowledgeBaseForm">取消</n-button>
              </div>

              <!-- 文档管理只在编辑态显示——新建时还没有 kb_id，传不了文件。 -->
              <template v-if="editingKnowledgeBaseId">
                <n-divider />
                <div class="preview-header"><strong>文档与检索</strong><n-upload :custom-request="uploadKnowledgeFile" :show-file-list="false"><n-button secondary>上传文档</n-button></n-upload></div>
                <div class="knowledge-search-row">
                  <n-input v-model:value="knowledgeSearchQuery" placeholder="输入问题测试检索" @keydown.enter="searchSelectedKnowledgeBase" />
                  <n-button type="primary" :loading="knowledgeSearchLoading" :disabled="!knowledgeSearchQuery.trim()" @click="searchSelectedKnowledgeBase">检索</n-button>
                </div>
                <div v-if="knowledgeSearchResults.length" class="knowledge-search-results">
                  <div v-for="(hit, index) in knowledgeSearchResults" :key="`${hit.document_id || 'hit'}-${index}`" class="knowledge-hit">
                    <div><n-tag size="tiny">{{ hit.source_filename || '文档片段' }}</n-tag><small>相似度 {{ Number(hit.score || 0).toFixed(3) }}</small></div>
                    <p>{{ hit.content }}</p>
                  </div>
                </div>
                <p v-if="!knowledgeDocuments.length">还没有文档。</p>
                <div v-for="document in knowledgeDocuments" :key="document.id" class="document-row"><span>{{ document.filename }}</span><n-tag size="small">{{ document.status || 'processing' }}</n-tag><n-button size="small" quaternary :loading="submitBusyKey === 'kb-document-delete'" :disabled="!!submitBusyKey" @click="removeKnowledgeDocument(document)">删除</n-button></div>
              </template>
            </div>
          </n-modal>
        </section>

        <section v-else-if="activeNav === 'memories'" class="content-board memory-board">
          <div class="memory-toolbar">
            <div>
              <h2>我的长期记忆</h2>
              <p>{{ memories.length }} 条内容会在相关对话中用于个性化回答</p>
            </div>
            <div class="memory-toolbar-actions">
              <n-button quaternary circle title="刷新记忆" :loading="memoriesLoading" @click="loadMemories(true)">
                <template #icon><n-icon :component="RefreshOutline" /></template>
              </n-button>
              <n-button type="primary" @click="startCreateMemory">
                <template #icon><n-icon :component="Add" /></template>
                新增记忆
              </n-button>
            </div>
          </div>

          <div class="memory-filters">
            <n-input v-model:value="memorySearch" clearable placeholder="搜索记忆内容" />
            <n-select
              v-model:value="memoryCategory"
              :options="[{ label: '全部分类', value: 'all' }, ...memoryCategoryOptions]"
              aria-label="按记忆分类筛选"
            />
            <n-select
              v-model:value="memoryScope"
              :options="[{ label: '全部范围', value: 'all' }, ...memoryScopeOptions]"
              aria-label="按记忆范围筛选"
            />
          </div>

          <p v-if="memoryError" class="memory-error">{{ memoryError }}</p>

          <div class="memory-workbench">
            <div class="memory-list" aria-label="长期记忆列表">
              <div v-if="memoriesLoading && !memoriesLoaded" class="memory-empty">正在读取记忆...</div>
              <div v-else-if="!filteredMemories.length" class="memory-empty">
                <n-icon :component="BulbOutline" />
                <strong>{{ memories.length ? '没有匹配的记忆' : '还没有长期记忆' }}</strong>
                <span>{{ memories.length ? '调整搜索词或分类筛选。' : '对话会自动提取，你也可以手动添加。' }}</span>
              </div>
              <template v-else>
                <button
                  v-for="item in filteredMemories"
                  :key="item.id"
                  class="memory-row"
                  :class="{ active: selectedMemoryId === item.id }"
                  @click="selectMemory(item)"
                >
                  <span class="memory-row-top">
                    <span class="memory-row-tags">
                      <n-tag size="tiny" :bordered="false">{{ memoryCategoryLabel(item.category) }}</n-tag>
                      <n-tag size="tiny" :bordered="false" :type="memoryScopeType(item.scope)">{{ memoryScopeLabel(item.scope) }}</n-tag>
                    </span>
                    <small>重要度 {{ item.importance }}/10</small>
                  </span>
                  <strong>{{ item.content }}</strong>
                  <span class="memory-row-meta">{{ formatMemoryDate(item.created_at) }} · 使用 {{ item.access_count || 0 }} 次</span>
                </button>
              </template>
            </div>

            <div class="memory-detail">
              <template v-if="memoryEditorMode">
                <div class="memory-detail-header">
                  <div><small>{{ memoryEditorMode === 'create' ? 'NEW MEMORY' : 'EDIT MEMORY' }}</small><h3>{{ memoryEditorMode === 'create' ? '新增记忆' : '编辑记忆' }}</h3></div>
                </div>
                <div class="memory-form">
                  <label><span>记忆内容</span><n-input v-model:value="memoryForm.content" type="textarea" placeholder="例如：我偏好先看结论，再看详细推导。" :autosize="{ minRows: 6, maxRows: 14 }" maxlength="4000" show-count /></label>
                  <div class="memory-form-grid">
                    <label><span>分类</span><n-select v-model:value="memoryForm.category" :options="memoryCategoryOptions" /></label>
                    <label><span>重要度</span><n-input-number v-model:value="memoryForm.importance" :min="1" :max="10" /></label>
                  </div>
                  <label><span>记忆范围</span><n-select v-model:value="memoryForm.scope" :options="memoryScopeOptions" /></label>
                  <div class="card-actions">
                    <n-button type="primary" :loading="submitBusyKey === 'memory-save'" :disabled="!memoryForm.content.trim() || !!submitBusyKey" @click="saveMemory">{{ memoryEditorMode === 'create' ? '添加记忆' : '保存修改' }}</n-button>
                    <n-button quaternary :disabled="submitBusyKey === 'memory-save'" @click="cancelMemoryEdit">取消</n-button>
                  </div>
                </div>
              </template>

              <template v-else-if="selectedMemory">
                <div class="memory-detail-header">
                  <div><small>MEMORY #{{ selectedMemory.id }}</small><h3>{{ memoryCategoryLabel(selectedMemory.category) }}</h3></div>
                  <n-tag :bordered="false">重要度 {{ selectedMemory.importance }}/10</n-tag>
                </div>
                <p class="memory-content">{{ selectedMemory.content }}</p>
                <dl class="memory-metadata">
                  <div><dt>记忆范围</dt><dd>{{ memoryScopeLabel(selectedMemory.scope) }}</dd></div>
                  <div><dt>创建时间</dt><dd>{{ formatMemoryDate(selectedMemory.created_at) }}</dd></div>
                  <div><dt>被使用</dt><dd>{{ selectedMemory.access_count || 0 }} 次</dd></div>
                </dl>
                <div class="card-actions memory-detail-actions">
                  <n-button secondary @click="startEditMemory(selectedMemory)">编辑</n-button>
                  <n-button type="error" quaternary :loading="submitBusyKey === `memory-delete-${selectedMemory.id}`" :disabled="!!submitBusyKey" @click="removeMemory(selectedMemory)">删除</n-button>
                </div>
              </template>

              <div v-else class="memory-detail-empty">
                <n-icon :component="BulbOutline" />
                <h3>选择一条记忆</h3>
                <p>你可以检查 AI 记住了什么，并随时修正或删除。</p>
              </div>
            </div>
          </div>

          <div class="memory-footer">
            <span>只显示当前账号在当前租户下的记忆。</span>
            <n-button type="error" quaternary size="small" :loading="submitBusyKey === 'memory-clear'" :disabled="!memories.length || !!submitBusyKey" @click="removeAllMemories">清空全部记忆</n-button>
          </div>
        </section>

        <section v-else-if="activeNav === 'projects'" class="content-board">
          <div class="segmented"><h2>项目</h2><n-button type="primary" @click="startCreateProject">创建项目</n-button></div>
          <div v-if="projectFormOpen" class="tool-create-form">
            <n-input v-model:value="projectForm.name" placeholder="项目名称" />
            <n-input v-model:value="projectForm.description" placeholder="项目描述（可选）" />
            <div class="card-actions"><n-button type="primary" :loading="submitBusyKey === 'project-save'" :disabled="!!submitBusyKey" @click="saveProject">{{ editingProjectId ? '保存修改' : '保存项目' }}</n-button><n-button quaternary @click="projectFormOpen = false; editingProjectId = null">取消</n-button></div>
          </div>
          <div class="asset-grid">
            <article v-for="project in projects" :key="project.id" class="asset-card" :class="{ 'muted-card': project.is_archived }" @click="openProject(project)">
              <h3>{{ project.name }}</h3>
              <p>{{ project.description || '暂无描述' }}</p>
              <div class="tag-row"><n-tag size="small">{{ project.session_count || 0 }} 个会话</n-tag><n-tag v-if="project.is_archived" size="small" type="warning">已归档</n-tag></div>
              <div class="card-actions" @click.stop><n-button size="small" secondary @click="editProject(project)">编辑</n-button><n-button size="small" quaternary :disabled="project.is_archived" @click="archiveProjectItem(project)">归档</n-button><n-button size="small" quaternary :loading="submitBusyKey === 'project-delete'" :disabled="!!submitBusyKey" @click="removeProject(project)">删除</n-button></div>
            </article>
          </div>
          <div v-if="selectedProjectId" class="project-session-list">
            <h3>{{ projects.find(project => project.id === selectedProjectId)?.name }}</h3>
            <button v-for="conversation in conversations.filter(item => item.project_id === selectedProjectId)" :key="conversation.id" class="conversation-row" @click="selectedConversation = conversation.id; activeNav = 'chat'; openSession(conversation.id)">
              <span>{{ conversation.title }}</span><small>{{ conversation.meta }}</small>
            </button>
            <p v-if="!conversations.some(item => item.project_id === selectedProjectId)" class="empty-state">这个项目还没有会话。</p>
          </div>
          <p v-else class="empty-state">选择一个项目查看会话，或创建一个新项目。</p>
        </section>
        <section v-else class="content-board placeholder-board">
          <n-icon :component="SettingsOutline" />
          <h2>设置</h2>
          <p>账号、租户和模型连接设置在右侧“文件区”中管理。</p>
        </section>
      </section>

      <aside class="inspector" :class="{ open: inspectorOpen }">
        <button class="inspector-toggle" @click="inspectorOpen = !inspectorOpen">
          <n-icon :component="inspectorOpen ? ChevronForwardOutline : ChevronBackOutline" />
        </button>
        <template v-if="inspectorOpen">
          <header class="inspector-header">
            <div>
              <strong>Session 沙箱工作区</strong>
              <small>{{ currentSessionId ? `会话 ${currentSessionId}` : '发送消息后创建会话' }}</small>
            </div>
            <n-button v-if="inspectorTab === 'files' && currentSessionId" size="small" quaternary :loading="workspaceLoading" title="刷新当前 Session 沙箱文件" @click="loadSessionWorkspace(currentSessionId)">刷新</n-button>
          </header>
          <div class="inspector-tabs">
            <button :class="{ active: inspectorTab === 'files' }" @click="inspectorTab = 'files'">文件</button>
            <button :class="{ active: inspectorTab === 'library' }" @click="openLibrary">我的文件</button>
            <button :class="{ active: inspectorTab === 'artifacts' }" @click="inspectorTab = 'artifacts'">产物</button>
            <button :class="{ active: inspectorTab === 'citations' }" @click="inspectorTab = 'citations'">引用</button>
            <button :class="{ active: inspectorTab === 'debug' }" @click="inspectorTab = 'debug'">调试</button>
          </div>

          <section v-if="inspectorTab === 'files'" class="inspector-body">
            <div class="workspace-summary">
              <span><strong>{{ files.length }}</strong> 个沙箱文件 · {{ workspaceDirCount }} 个目录</span>
              <small>只有模型实际写入 Session 工作区的文件会出现在这里</small>
              <!-- 隐藏目录默认剪掉（lea-tools 侧的 _HIDDEN_DIRS：dist/build/.git 之类）。
                   开关放在这里而不是藏进设置，是因为"模型跑了个构建，产物在 dist 里"
                   是很常见的一类任务，而默认视图下那种会话的工作区看起来是空的。 -->
              <n-button size="tiny" quaternary :disabled="!currentSessionId" @click="toggleHiddenFiles">
                {{ showHiddenFiles ? '隐藏 dist/.git 等目录' : '显示 dist/.git 等目录' }}
              </n-button>
            </div>
            <p v-if="workspaceTruncated" class="share-warning">
              文件列表已被截断，下面显示的不是全部。缩小范围或直接用路径下载单个文件。
            </p>
            <div v-if="attachmentFiles.length" class="attachment-list">
              <strong>本轮附件（原件）</strong>
              <div v-for="attachment in attachmentFiles" :key="attachment.id" class="document-row">
                <span>{{ attachment.name }} · {{ attachment.size }}</span>
                <n-button v-if="attachment.downloadable" size="small" quaternary @click="downloadUploadedAttachment(attachment)">下载</n-button>
              </div>
            </div>
            <!-- 待导入：点了"导入"但还没发消息,本地暂存,还没真正调导入接口，
                 见 pendingLibraryImports 的说明。 -->
            <div v-if="pendingLibraryImports.length" class="attachment-list">
              <strong>待发送时导入</strong>
              <div v-for="file in pendingLibraryImports" :key="libraryKey(file)" class="document-row">
                <span>{{ file.filename }} · {{ formatBytes(file.size) }}</span>
                <n-button size="small" quaternary @click="removePendingLibraryImport(file)">取消</n-button>
              </div>
            </div>
            <p v-if="workspaceError" class="library-error">读取 Session 沙箱失败：{{ workspaceError }}</p>
            <p v-else-if="workspaceLoading && !files.length" class="empty-state">正在读取 Session 沙箱...</p>
            <p v-else-if="!currentSessionId" class="empty-state">当前还没有 Session。发送第一条消息后，这里会显示沙箱工作区。</p>
            <!-- store === 'pending' 是"会话还没落库"，不是"沙箱是空的"。两种情况用户
                 要做的事完全不同（前者等一下，后者去让模型干活），文案必须分开。 -->
            <p v-else-if="workspaceStore === 'pending'" class="empty-state">会话还没落库，工作区要等第一条消息发出去之后才存在。</p>
            <p v-else-if="!files.length" class="empty-state">当前 Session 沙箱还是空的。模型调用 Bash 并产生文件后，文件会出现在这里。</p>
            <!-- 一维行列表 + 缩进模拟树。根节点 workspace 也在里面，折叠它等于收起整个
                 工作区，所以不给它特殊待遇。 -->
            <div v-if="files.length" class="workspace-tree">
              <template v-for="node in visibleWorkspaceRows" :key="node.kind === 'dir' ? `d:${node.key}` : `f:${node.key}`">
                <button
                  v-if="node.kind === 'dir'"
                  class="dir-row"
                  :style="{ paddingLeft: `${node.depth * 16 + 12}px` }"
                  :title="node.key || 'workspace'"
                  @click="toggleDir(node.key)"
                >
                  <span class="tree-caret">{{ collapsedDirs.has(node.key) ? '▸' : '▾' }}</span>
                  <n-icon :component="collapsedDirs.has(node.key) ? FolderOutline : FolderOpenOutline" />
                  <span>{{ node.name }}</span>
                  <small>{{ node.fileCount }}</small>
                </button>
                <button
                  v-else
                  class="file-row"
                  :class="{ active: selectedFile?.path === node.key }"
                  :style="{ paddingLeft: `${node.depth * 16 + 12}px` }"
                  :title="node.key"
                  @click="selectWorkspaceFile(node.file!)"
                >
                  <n-icon :component="DocumentTextOutline" />
                  <span>{{ node.name }}</span>
                  <small>{{ node.file!.type }} · {{ node.file!.size }}</small>
                  <n-tag size="small" :type="node.file!.status === 'ready' ? 'success' : node.file!.status === 'indexing' ? 'warning' : 'info'">{{ node.file!.status }}</n-tag>
                </button>
              </template>
            </div>
            <div class="preview">
              <div class="preview-header">
                <!-- 显示完整路径而不是文件名：分组之后同名文件仍然可能同时出现在
                     列表里，预览区得说清楚现在看的是哪一个。 -->
                <strong>{{ selectedFile?.path || '选择文件' }}</strong>
                <n-button size="small" secondary :loading="workspacePublishLoading" :disabled="!selectedFile?.path" @click="saveSelectedWorkspaceFile">保存到我的文件</n-button>
                <n-button size="small" secondary :disabled="!selectedFile?.path" @click="downloadSelectedFile">下载</n-button>
              </div>
              <p v-if="filePreviewLoading">正在读取工作区文件...</p>
              <img v-else-if="filePreviewKind === 'image'" :src="filePreviewUrl" :alt="selectedFile?.name" class="image-preview" />
              <!-- PDF/HTML 都甩给浏览器原生能力（PDF viewer / iframe），不装专门的
                   渲染库——见 selectWorkspaceFile 的说明。HTML 用 sandbox 隔离，
                   allow-scripts 不带 allow-same-origin，跑得了脚本拿不到本页的
                   会话状态。 -->
              <iframe v-else-if="filePreviewKind === 'pdf'" :src="filePreviewUrl" class="file-preview-frame" title="PDF 预览" />
              <iframe v-else-if="filePreviewKind === 'html'" :srcdoc="filePreviewText" sandbox="allow-scripts" class="file-preview-frame" title="HTML 预览" />
              <pre v-else-if="filePreviewKind === 'text'" class="text-preview"><code v-html="highlightPreviewText(filePreviewText, selectedFile?.name || '')" /></pre>
              <p v-else>该文件可下载；PDF/HTML/图片/文本文件可直接在此预览，Office 文档暂不支持预览。</p>
            </div>
          </section>

          <section v-else-if="inspectorTab === 'library'" class="inspector-body">
            <!-- 用量：quota_bytes=0 是"不限"，这时画进度条没有意义（永远 0%），只报已用。 -->
            <div v-if="userStorage" class="storage-meter">
              <div class="storage-line">
                <strong>{{ formatBytes(userStorage.total_bytes) }}</strong>
                <small v-if="userStorage.quota_bytes"> / {{ formatBytes(userStorage.quota_bytes) }}</small>
                <small v-else> 已用（不限额）</small>
              </div>
              <div v-if="userStorage.quota_bytes" class="storage-bar">
                <span :style="{ width: `${Math.min(100, userStorage.percent)}%` }" :class="{ danger: userStorage.percent >= 90 }" />
              </div>
              <small class="storage-detail">
                上传 {{ userStorage.upload_count }} 个 · {{ formatBytes(userStorage.upload_bytes) }}
                ｜成品 {{ userStorage.artifact_count }} 个 · {{ formatBytes(userStorage.artifact_bytes) }}
              </small>
            </div>

            <div class="library-filters">
              <button :class="{ active: libraryKind === '' }" @click="switchLibraryKind('')">全部</button>
              <button :class="{ active: libraryKind === 'upload' }" @click="switchLibraryKind('upload')">我上传的</button>
              <button :class="{ active: libraryKind === 'artifact' }" @click="switchLibraryKind('artifact')">生成的成品</button>
              <n-button size="tiny" quaternary :loading="libraryLoading" @click="loadUserLibrary">刷新</n-button>
            </div>

            <p v-if="libraryError" class="library-error">{{ libraryError }}</p>
            <p v-else-if="libraryLoading" class="empty-state">正在读取文件库...</p>
            <p v-else-if="!userFiles.length" class="empty-state">文件库还是空的。上传的附件和模型交付的成品会自动出现在这里。</p>

            <div v-for="file in userFiles" :key="libraryKey(file)" class="library-row">
              <div class="library-main">
                <span class="library-name">{{ file.filename }}</span>
                <n-tag size="tiny" :type="file.kind === 'upload' ? 'info' : 'success'">
                  {{ file.kind === 'upload' ? '上传' : '成品' }}
                </n-tag>
                <!-- 原件没保住的行：说清楚，而不是让用户点了导入才拿到 410 -->
                <n-tag v-if="!file.stored" size="tiny" type="warning">无原件</n-tag>
              </div>
              <!-- 成品的 title/description 是模型交付时写的，用户几周后靠这两句认出它 -->
              <small v-if="file.title" class="library-title">{{ file.title }}</small>
              <small v-if="file.description" class="library-desc">{{ file.description }}</small>
              <div class="library-meta">
                <small>{{ formatBytes(file.size) }}<template v-if="file.created_at"> · {{ file.created_at }}</template></small>
                <div class="library-actions">
                  <n-button
                    size="tiny"
                    secondary
                    :disabled="!file.can_import || pendingLibraryImports.some(item => libraryKey(item) === libraryKey(file))"
                    @click="importLibraryFile(file)"
                  >{{ pendingLibraryImports.some(item => libraryKey(item) === libraryKey(file)) ? '已选中·待发送' : '导入会话' }}</n-button>
                  <n-button size="tiny" quaternary :disabled="!!libraryBusyKey" @click="removeLibraryFile(file)">删除</n-button>
                </div>
              </div>
            </div>
          </section>

          <section v-else-if="inspectorTab === 'artifacts'" class="inspector-body">
            <!-- 以前这里只报一个数字（"N 个已生成文件，可在文件页预览或下载"），等于
                 把用户支到别的 tab 再自己找一遍。产物就那几个，直接列出来并且能点开。 -->
            <p v-if="!currentSessionId" class="empty-state">当前还没有 Session。</p>
            <p v-else-if="!readyFiles.length" class="empty-state">
              当前会话还没有产物。写进 <code>output/</code> 的文件才算产物；
              工作区根目录下的中间文件在“文件”页看。
            </p>
            <template v-else>
              <div class="workspace-summary">
                <span><strong>{{ readyFiles.length }}</strong> 个产物</span>
                <small>output/ 下的文件</small>
              </div>
              <button
                v-for="file in readyFiles"
                :key="file.path"
                class="file-row"
                :class="{ active: selectedFile?.path === file.path }"
                :title="file.path"
                @click="selectWorkspaceFile(file)"
              >
                <n-icon :component="DocumentTextOutline" />
                <!-- 这里显示完整路径而不是文件名：产物列表是拍平的，output/a/report.md
                     和 output/b/report.md 只看文件名分不出来。 -->
                <span>{{ file.path }}</span>
                <small>{{ file.type }} · {{ file.size }}</small>
              </button>
              <div class="preview">
                <div class="preview-header">
                  <strong>{{ selectedFile?.path || '选择产物' }}</strong>
                  <n-button size="small" secondary :loading="workspacePublishLoading" :disabled="!selectedFile?.path" @click="saveSelectedWorkspaceFile">保存到我的文件</n-button>
                  <n-button size="small" secondary :disabled="!selectedFile?.path" @click="downloadSelectedFile">下载</n-button>
                </div>
                <p v-if="filePreviewLoading">正在读取工作区文件...</p>
                <img v-else-if="filePreviewKind === 'image'" :src="filePreviewUrl" :alt="selectedFile?.name" class="image-preview" />
                <iframe v-else-if="filePreviewKind === 'pdf'" :src="filePreviewUrl" class="file-preview-frame" title="PDF 预览" />
                <iframe v-else-if="filePreviewKind === 'html'" :srcdoc="filePreviewText" sandbox="allow-scripts" class="file-preview-frame" title="HTML 预览" />
                <pre v-else-if="filePreviewKind === 'text'" class="text-preview"><code v-html="highlightPreviewText(filePreviewText, selectedFile?.name || '')" /></pre>
                <p v-else>该文件可下载；PDF/HTML/图片/文本文件可直接在此预览，Office 文档暂不支持预览。</p>
              </div>
            </template>
          </section>

          <section v-else-if="inspectorTab === 'citations'" class="inspector-body">
            <p v-if="!sessionReferences.length" class="empty-state">当前会话还没有可展示的引用。</p>
            <article v-for="reference in sessionReferences" :key="reference.id" class="citation-card">
              <a v-if="reference.url" :href="reference.url" target="_blank" rel="noreferrer">{{ reference.title }}</a>
              <strong v-else>{{ reference.title }}</strong>
              <small v-if="reference.source || reference.path">{{ reference.source }}<template v-if="reference.source && reference.path"> · </template>{{ reference.path }}</small>
              <small v-if="reference.authors?.length || reference.year">{{ reference.authors?.join('、') }}<template v-if="reference.authors?.length && reference.year"> · </template>{{ reference.year }}</small>
              <p v-if="reference.abstract">{{ reference.abstract }}</p>
            </article>
          </section>

          <section v-else class="inspector-body debug-panel">
            <strong>连接账号</strong>
            <p class="inspector-hint">粘贴登录接口返回的 Access Token，不需要填写 “Bearer”。</p>
            <n-input v-model:value="token" type="textarea" placeholder="Access Token" :autosize="{ minRows: 2, maxRows: 4 }" />
            <n-button block type="primary" @click="saveToken">获取租户</n-button>
            <template v-if="tenants.length">
              <select v-model="selectedTenantId"><option value="">选择租户</option><option v-for="tenant in tenants" :key="tenant.id" :value="tenant.id">{{ tenant.id }} · {{ tenant.type || '租户' }} · {{ tenant.role || 'user' }}</option></select>
              <n-button block type="primary" @click="connectTenant">使用所选租户连接</n-button>
            </template>
            <template v-else-if="token">
              <n-input v-model:value="selectedTenantId" placeholder="手动输入租户 ID" />
              <n-button block secondary @click="connectTenant">使用此租户连接</n-button>
            </template>
            <p v-if="connectionError" class="connection-error">连接失败：{{ connectionError }}</p>
            <div class="probe-row">
              <n-input v-model:value="apiForm.method" />
              <n-input v-model:value="apiForm.path" />
            </div>
            <n-input v-model:value="apiForm.body" type="textarea" placeholder="JSON Body" :autosize="{ minRows: 4, maxRows: 8 }" />
            <n-button block type="primary" @click="runApiProbe">发送接口探测</n-button>
            <pre v-for="log in requestLogs" :key="`${log.path}-${log.time}`">{{ log.method }} {{ log.path }} · {{ log.status }} · {{ log.time }}ms
{{ pretty(log.body) }}</pre>
          </section>
        </template>
      </aside>
    </main>
    <n-modal v-model:show="skillUploadOpen" preset="card" title="上传 Skill" style="width: min(560px, calc(100vw - 32px))">
      <div class="skill-upload-form">
        <n-upload :default-upload="false" :max="1" accept=".zip,.skill" @change="selectSkillUploadFile">
          <n-button secondary>选择 Skill 文件</n-button>
        </n-upload>
        <span class="upload-file-name">{{ skillUploadFile?.name || '尚未选择文件' }}</span>
        <n-input v-model:value="skillVersion" placeholder="版本，例如 1.0.0" />
        <select v-model="skillCategory" aria-label="Skill 分类">
          <option value="">选择分类</option>
          <option value="general">通用</option>
          <option value="writing">写作</option>
          <option value="data">数据</option>
          <option value="file">文件</option>
          <option value="research">研究</option>
          <option value="development">开发</option>
        </select>
        <n-input v-model:value="skillChangeLog" type="textarea" placeholder="更新说明（可选）" :autosize="{ minRows: 3, maxRows: 6 }" />
        <n-button type="primary" :loading="skillUploadLoading" :disabled="!skillUploadFile || !skillVersion.trim() || !skillCategory" @click="submitSkillUpload">开始上传</n-button>
      </div>
    </n-modal>
    <n-modal v-model:show="marketEditOpen" preset="card" title="编辑市场资源" style="width: min(620px, calc(100vw - 32px))">
      <div class="tool-create-form modal-form">
        <div class="form-grid">
          <n-input v-model:value="marketEditForm.name" placeholder="名称" :disabled="editingMarketKind === 'tool' || editingMarketKind === 'skill'" />
          <n-input v-model:value="marketEditForm.display_name" placeholder="显示名称" v-if="editingMarketKind !== 'mcp'" />
          <select v-model="marketEditForm.category"><option value="">选择分类</option><option value="general">通用</option><option value="writing">写作</option><option value="data">数据</option><option value="file">文件</option><option value="research">研究</option><option value="development">开发</option><option value="search">搜索</option><option value="productivity">效率</option><option value="automation">自动化</option></select>
        </div>
        <n-input v-model:value="marketEditForm.description" placeholder="描述" />
        <n-input v-if="editingMarketKind === 'mcp'" v-model:value="marketEditForm.endpoint" placeholder="MCP Endpoint" />
        <n-input v-if="editingMarketKind === 'mcp'" v-model:value="marketEditForm.bearerToken" type="password" show-password-on="click" placeholder="Bearer Token（留空保持原值）" />
        <n-input v-if="editingMarketKind === 'mcp'" v-model:value="marketEditForm.toolFilter" placeholder="允许的工具，逗号分隔" />
        <n-input v-if="editingMarketKind === 'tool'" v-model:value="marketEditForm.endpoint" placeholder="Endpoint" />
        <n-input v-if="editingMarketKind === 'tool'" v-model:value="marketEditForm.schema_json" type="textarea" :autosize="{ minRows: 5, maxRows: 10 }" placeholder="JSON Schema" />
        <n-input v-if="editingMarketKind === 'skill'" v-model:value="marketEditForm.system_prompt" type="textarea" :autosize="{ minRows: 4, maxRows: 8 }" placeholder="核心提示词（如果后端允许修改）" />
        <div class="card-actions"><n-button type="primary" :loading="submitBusyKey === 'market-item-save'" :disabled="!!submitBusyKey" @click="saveMarketEdit">保存修改</n-button><n-button quaternary @click="marketEditOpen = false">取消</n-button></div>
      </div>
    </n-modal>
    <n-modal :show="publishSkillId !== null" preset="card" title="发布 Skill" style="width: min(480px, calc(100vw - 32px))" @update:show="value => { if (!value) publishSkillId = null }">
      <div class="tool-create-form modal-form">
        <select v-model="publishTarget"><option value="tenant">发布到当前租户</option><option value="global">申请全局发布</option></select>
        <n-input v-model:value="publishNote" type="textarea" placeholder="发布说明（可选）" :autosize="{ minRows: 3, maxRows: 6 }" />
        <n-button type="primary" @click="submitSkillPublish">提交发布</n-button>
      </div>
    </n-modal>
  </n-config-provider>
</template>
