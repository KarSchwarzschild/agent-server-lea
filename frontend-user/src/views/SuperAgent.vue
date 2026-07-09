<script setup lang="ts">
/**
 * 超级智能体页面 - 响应式版本
 * PC端：左侧会话列表 + 右侧聊天区 + Skill状态展示
 * 移动端：底部抽屉会话列表 + 全屏聊天 + 一体式输入
 *
 * 特性：
 * - 支持Skill执行可视化（思考过程、工具调用）
 * - 支持多模型辩论模式
 * - SSE流式输出
 */
import { ref, nextTick, onMounted, onUnmounted, computed, watch, provide, type Ref } from 'vue'
import {
  NLayout,
  NLayoutSider,
  NLayoutContent,
  NButton,
  NTag,
  NTabs,
  NTabPane,
  NIcon,
  NModal,
  NButtonGroup,
  NDropdown,
  NTooltip,
  NDrawer,
  NDrawerContent,
  NDivider,
  useMessage,
} from 'naive-ui'
import {
  FlashOutline,
  DownloadOutline,
  ListOutline,
  EllipsisVertical,
  BuildOutline,
  PeopleOutline,
  BulbOutline,
  ConstructOutline,
  EyeOutline,
  TimeOutline,
  CheckmarkCircleOutline,
  SearchOutline,
  GlobeOutline,
  DocumentTextOutline,
  ColorWandOutline,
  GitBranchOutline,
  AnalyticsOutline,
  SchoolOutline,
} from '@vicons/ionicons5'
import {
  getSuperAgentStreamUrl,
  getSkills,
  getSessions,
  getSessionDetail,
  deleteSession,
  createStreamRequest,
  getAskExpertStreamUrl,
  extractFileIdFromUrl,
  convertToPdf,
  exportSuperAgentSession,
  stopSession,
  submitToolResult,
  approvePlan,
  rejectPlan,
  type SuperAgentSession,
  type Skill,
  type SSEEventData,
  type Expert,
  type GeneratedFileV2,
  getUserMemories,
  deleteMemory as apiDeleteMemory,
  clearAllMemories,
  getDefaultAgent,
  setDefaultAgent,
  clearDefaultAgent,
  getAgent,
  regenerateMessage,
  editMessage,
  switchVersion,
  type AgentInfo,
} from '../api/superagent'
import { marked } from 'marked'
import { sanitizeHtml } from '../utils/sanitize'
import { ModeRecommender } from '../utils/modeRecommender'
import { useUserStore } from '../stores/user'
import { useArtifactStore } from '../stores/artifact'
import { protectCitationMarks, restoreCitationMarks } from '../utils/citationRenderer'
import { useResponsive } from '../composables/useResponsive'
import { useSSEHandler } from '../composables/useSSEHandler'
import { useSkillMention } from '../composables/useSkillMention'
import { useAttachments } from '../composables/useAttachments'
import { useSuperAgentGuideStore } from '../stores/superAgentGuide'
import { useSearchSuggestionsStore } from '../stores/searchSuggestions'
import type { MentionSkill } from '../composables/useSkillMention'
import type {
  ChatMessage,
  Reference,
  ViewMode,
  ClientToolRequest,
  MemoryItem,
} from '../types/super-agent'
import MobileChatInput from '../components/MobileChatInput.vue'
import MobileSessionDrawer from '../components/MobileSessionDrawer.vue'
import ConversationList from '../components/super-agent/ConversationList.vue'
import MessageItem from '../components/super-agent/MessageItem.vue'
import ChatInputArea from '../components/super-agent/ChatInputArea.vue'
import DebateUI from '../components/super-agent/DebateUI.vue'
import ArtifactPanel from '../components/artifact/ArtifactPanel.vue'
import MobileArtifactDrawer from '../components/artifact/MobileArtifactDrawer.vue'
import DebugPanel from '../components/DebugPanel.vue'
import ExecutionTimeline from '../components/ExecutionTimeline.vue'
import ErrorState, { type ErrorType, type DataSourceStatus } from '../components/ErrorState.vue'
import FilePreviewModal from '../components/FilePreviewModal.vue'
import ClientToolInline from '../components/super-agent/ClientToolInline.vue'
import PlanPanel from '../components/super-agent/PlanPanel.vue'
import PlanQuestion from '../components/super-agent/PlanQuestion.vue'
import AgentSelector from '../components/super-agent/AgentSelector.vue'
import AgentCreatorDrawer from '../components/super-agent/AgentCreatorDrawer.vue'
import { useRouter, useRoute } from 'vue-router'
import { parseSSEStream } from '../composables/useStreamParser'
import { useInteractivePlayerRegistry, INTERACTIVE_PLAYER_REGISTRY_KEY } from '../composables/useInteractivePlayerRegistry'
import MarkdownIt from 'markdown-it'

// 记忆面板 Markdown 渲染（轻量版，不需要代码高亮和公式）
const memoryMd = new MarkdownIt({ html: false, linkify: true, typographer: true })
function renderMemoryMd(text: string): string {
  if (!text) return ''
  return memoryMd.render(text)
}

const router = useRouter()
const route = useRoute()
const message = useMessage()
const userStore = useUserStore()
const artifactStore = useArtifactStore()
const guideStore = useSuperAgentGuideStore()
const searchSuggestionsStore = useSearchSuggestionsStore()
provide(INTERACTIVE_PLAYER_REGISTRY_KEY, useInteractivePlayerRegistry())
const { isMobile } = useResponsive()

// 强制技能常量
const FORCE_SKILL_QA = '__qa__'

// ==================== 类型定义（从 types/super-agent.ts 导入） ====================
// ViewMode, ChatMessage, ThinkingStep, ToolCall, SubAgentState, SubAgentToolCall, Reference, ReasoningBlock
// 均已从 '../types/super-agent' 导入

// ==================== 状态 ====================

const messagesContainer = ref<HTMLElement>()
const streamingPreviewRef = ref<HTMLElement | null>(null)
const messages = ref<ChatMessage[]>([])
const inputText = ref('')
const loading = ref(false)
const currentSessionId = ref<string>('')
// v5.0: 乐观锁版本号
const sessionRevision = ref(0)

// 历史会话
const sessions = ref<SuperAgentSession[]>([])
const sessionsLoading = ref(false)
const siderCollapsed = ref(false)

// 可用Skills
const skills = ref<Skill[]>([])

// AI 助手选择
const showAgentSelector = ref(false)
const showAgentCreator = ref(false)
const currentAgent = ref<AgentInfo | null>(null)
const defaultAgentId = ref<number | null>(null)

// 当前助手的展示信息（null = 默认通用助手，保持原始体验）
const agentDisplay = computed(() => {
  const a = currentAgent.value
  if (!a) return null
  return {
    avatar: a.avatar || '🤖',
    name: a.name,
    description: a.description || a.welcome_message || '',
    welcomeMessage: a.welcome_message || `你好！我是${a.name}，有什么可以帮你的？`,
  }
})

// 欢迎页是否显示 description：welcomeMessage 存在且 >= 100 字符时隐藏
const shouldShowDescription = computed(() => {
  const wm = agentDisplay.value?.welcomeMessage
  return !wm || wm.length < 100
})

// 折叠 header：有用户发送的消息时折叠
const isHeaderCollapsed = ref(false)
const hasUserMessages = computed(() =>
  messages.value.some(m => m.role === 'user')
)
watch(hasUserMessages, (val) => {
  if (val) isHeaderCollapsed.value = true
  else isHeaderCollapsed.value = false
})

// 渲染欢迎消息中的 Markdown（加粗、列表、换行等）
function renderWelcomeMessage(text: string): string {
  if (!text) return ''
  return sanitizeHtml(marked.parse(text, { async: false, breaks: true }) as string)
}

// 动态技能过滤：当前助手限定技能时，只展示允许的技能
const displaySkills = computed(() => {
  const allowed = currentAgent.value?.allowed_skills
  if (!allowed || allowed.length === 0) return skills.value
  return skills.value.filter(s => allowed.includes(s.name))
})

// @mention 技能选择
const {
  showDropdown: showMentionDropdown,
  filteredList: mentionFilteredList,
  mentionList,
  selectedSkill,
  dropdownIndex: mentionDropdownIndex,
  handleInput: handleMentionInput,
  selectSkill,
  clearSkill,
  handleKeydown: handleMentionKeydown,
} = useSkillMention(displaySkills)

// 切换 Agent 时：清除不兼容 Skill + 重新加载该助手的历史会话
watch(currentAgent, () => {
  if (selectedSkill.value) {
    const allowed = currentAgent.value?.allowed_skills
    if (allowed && allowed.length > 0 && !allowed.includes(selectedSkill.value.name)) {
      clearSkill()
    }
  }
  // 重新加载（按新助手过滤）
  loadSessions()
})

// 输入框锚点位置（用于定位下拉菜单）
const mentionAnchorRect = ref<{ bottom: number; left: number; width: number } | null>(null)

// 聊天模式：chat / plan / artifact
const chatMode = ref<'chat' | 'plan'>('chat')

// 当前会话关联的画布信息（输入框上方文档标签）
// Phase 3：画布从模式解耦为上下文能力 —— 只要会话挂着 artifact 就显示，不再依赖 chatMode。
const activeArtifactInfo = computed(() => {
  const art = artifactStore.activeArtifact
  if (!art) return null
  return { id: art.id, title: art.title, versionIndex: art.currentIndex }
})

// 快捷回复/搜索建议合并计算属性
// 优先级：searchSuggestions > guideStore.suggestedReplies
// suggestedReplies 不要求 isGuideActive，因为 conversational skill 的快捷回复在 skill_done 后仍需展示
const displaySuggestions = computed<string[]>(() => {
  const fromSearch = searchSuggestionsStore.suggestions
  const fromGuide = guideStore.suggestedReplies
  const result = fromSearch.length > 0 ? fromSearch.map(s => s.text) : fromGuide
  return result
})

// 查询改写提示条状态
const queryRewriteBarExpanded = ref(false)

// 附件管理
const {
  attachments,
  readyAttachmentIds,
  hasUploading: attachmentUploading,
  hasAttachments,
  isDragging: attachmentDragging,
  addFiles: attachmentAddFiles,
  importFromKB: attachmentImportFromKB,
  removeAttachment,
  clearAll: clearAttachments,
  handlePaste: handleAttachmentPaste,
  handleDragEnter: attachmentDragEnter,
  handleDragLeave: attachmentDragLeave,
  handleDragOver: attachmentDragOver,
  handleDrop: attachmentDrop,
} = useAttachments(() => userStore.token)

// 移动端状态
const showSessionDrawer = ref(false)
const showMoreActions = ref(false)

// 🆕 视图模式状态（方案C - 渐进式信息披露）
const VIEW_MODE_KEY = 'super-agent-view-mode'
const viewMode = ref<ViewMode>(
  (localStorage.getItem(VIEW_MODE_KEY) as ViewMode) || 'simple'
)

// 视图模式切换
function setViewMode(mode: ViewMode) {
  viewMode.value = mode
  localStorage.setItem(VIEW_MODE_KEY, mode)
}

// 移动端视图模式下拉菜单选项
const viewModeOptions = [
  { label: '简洁模式', key: 'simple', description: '仅显示进度和结果' },
  { label: '专业模式', key: 'professional', description: '显示思考摘要' },
  { label: '调试模式', key: 'debug', description: '显示完整技术日志' },
]

// 当前执行状态
const currentIntent = ref<string>('')
const currentSkill = ref<string>('')
const isSkillExecuting = ref(false)
const isGeneratingAnswer = ref(false)
const skillExecutionTime = ref(0)

// 活跃的 EventSource 连接（用于中断流式输出）
let activeEventSource: EventSource | null = null

// 错误状态
const showErrorState = ref(false)
const errorType = ref<ErrorType>('unknown')
const errorMessage = ref('')
const dataSources = ref<DataSourceStatus[]>([])
const isRetrying = ref(false)

// 学术引用状态
const currentReferences = ref<Reference[]>([])
const showReferenceDrawer = ref(false)
const showReferenceModal = ref(false)
const refFilterTab = ref('all')
const selectedReference = ref<Reference | null>(null)
const expandedRefs = ref<Set<string>>(new Set())

// 文档处理中提示
const processingDocNames = ref<string[]>([])
let processingDocTimer: ReturnType<typeof setInterval> | null = null

async function checkProcessingDocs() {
  try {
    const { getDocuments } = await import('../api/user-kb')
    const res = await getDocuments({ page: 1, page_size: 5 })
    const processing = (res?.items || []).filter(
      (d: any) => d.upload_status === 'completed' && d.chunk_status && !['completed', 'failed'].includes(d.chunk_status)
    )
    processingDocNames.value = processing.map((d: any) => d.filename || d.title || '文档')
    // 如果没有 processing 的了，停止轮询
    if (processing.length === 0 && processingDocTimer) {
      clearInterval(processingDocTimer)
      processingDocTimer = null
    }
  } catch (err: any) {
    // 知识库不存在（404）或其他错误：停止轮询，无需反复重试
    if (processingDocTimer) {
      clearInterval(processingDocTimer)
      processingDocTimer = null
    }
  }
}

// 长期记忆管理
const showMemoryModal = ref(false)
function openMemoryModal() {
  // 先释放焦点，避免 n-modal 设置 aria-hidden 时底层元素仍持有焦点
  ;(document.activeElement as HTMLElement)?.blur?.()
  showMemoryModal.value = true
}
const memoryList = ref<MemoryItem[]>([])
const memoryLoading = ref(false)
const memoryFilterCategory = ref<string>('all')
const memorySearchKeyword = ref('')
const selectedMemory = ref<MemoryItem | null>(null)

// 分类配置（直接定义，避免 import const 在模板中的 reactive 问题）
const categoryMap: Record<string, { label: string; color: string }> = {
  all: { label: '全部', color: '#909399' },
  profile: { label: '身份', color: '#409EFF' },
  preferences: { label: '偏好', color: '#67C23A' },
  entities: { label: '实体', color: '#E6A23C' },
  events: { label: '事件', color: '#F56C6C' },
  cases: { label: '案例', color: '#9B59B6' },
  patterns: { label: '模式', color: '#00BCD4' },
}

function getCategoryConfig(cat?: string) {
  return categoryMap[cat || ''] || categoryMap.all
}

// 思考过程展开状态
const expandedThinking = ref<Set<string>>(new Set())

// SSE Handler composable — 初始化在 scroll 函数定义之后（见下方 "SSE Handler 初始化"）

// 追问专家状态
const showAskExpertModal = ref(false)
const askExpertQuestion = ref('')
const askExpertLoading = ref(false)
const selectedExpert = ref<Expert | null>(null)
const currentDebateSessionId = ref<string>('')

// 辩论轮次控制状态
const debateStatus = ref<'idle' | 'running' | 'paused' | 'ended' | 'pre_researching'>('idle')
const currentDebateRound = ref(0)
const maxDebateRounds = ref(3)
const canContinueDebate = ref(false)
const roundHint = ref<{ message: string; options: Array<{ action: string; label: string; enabled: boolean }>; experts?: string[] } | null>(null)
const isDebateProcessing = ref(false)  // 防止重复点击
const discussionMode = ref<string>('debate')  // 讨论模式: debate / seminar / socratic

// 辩论音频片段（从最后一条 assistant 消息的 subAgents 提取）
const debateAudioSegments = computed(() => {
  if (debateStatus.value === 'idle') return []
  const lastMsg = messages.value.filter(m => m.role === 'assistant').pop()
  if (!lastMsg?.subAgents) return []
  const segments: any[] = []
  for (const agent of lastMsg.subAgents) {
    const segs = (agent as any).audioSegments
    if (segs && Array.isArray(segs)) {
      for (const s of segs) {
        segments.push({ ...s, expertId: agent.agentId, expertName: agent.name || agent.agentId })
      }
    }
  }
  return segments
})

// 讨论模式选项
const discussionModes = [
  { value: 'debate', label: '辩论', icon: '⚔️' },
  { value: 'seminar', label: '研讨', icon: '🎓' },
  { value: 'socratic', label: '苏格拉底', icon: '💡' }
]

// 模式选择状态
const pendingModeSelection = ref<{
  topic: string
  recommendedMode: string
  modes: Array<{ value: string; label: string; icon: string; desc: string }>
  timeoutSeconds: number
} | null>(null)

// 🆕 专家思考过程状态（DeepSeek-R1/QwQ等思考模型）
const currentExpertReasoning = ref<{
  expertId: string
  expertName: string
  reasoning: string
} | null>(null)
const expertReasonings = ref<Map<string, string>>(new Map())  // 存储每个专家的完整思考过程

// 文件预览状态
const showFilePreviewModal = ref(false)
const previewFile = ref<GeneratedFileV2 | null>(null)
const convertingFileId = ref<string | null>(null)  // 正在转换的文件ID

const exampleQuestions = [
  // 简单问答（零工具调用）
  '什么是终身教育？',
  // 联网搜索（web_search）
  '搜索一下2026年成人高考的最新政策变化',
  // 学术搜索（academic_search）
  '帮我查几篇关于在线教育学习效果的研究论文',
  // 知识库查询（kb_query）
  '我们知识库里有没有关于学分银行的资料？',
  // 多工具组合（search + write）
  '搜索2026年职业教育改革的最新动态，帮我整理成 markdown 文件',
  // 知识库+网络对比
  '查一下知识库里关于学分银行的政策，再搜索最新国家标准，帮我对比差异',
  // 多专家辩论
  '多角度辩论：高校应该优先发展线上教学还是线下教学？',
  // 公文写作
  '写一份关于召开教学研讨会的通知',
  // 政策分析（policy-analyzer）
  '分析一下《职业教育法》修订后对终身教育体系的影响',
  // 深度调研
  '深度调研一下人工智能技术在K12基础教育中的落地案例与效果评估',
  // 研究选题助手（brainstorm）
  '我想研究在线教育相关的课题，但不确定具体方向，帮我选个题',
  // 并行综述（parallel orchestrator）
  '写一篇关于AI在医疗诊断中应用的全面综述，涵盖影像识别、辅助决策和药物研发',
  // 学习规划（learning-planner）
  '我是一名在职教师，想系统学习教育技术，帮我制定一个3个月的学习计划',
  // 选题 + 综述组合
  '帮我从终身教育的角度选一个研究方向，确定后直接生成文献综述',
  // 计划模式 + DAG 依赖调度（触发 plan 审批 → 步骤间有依赖 → 可能触发交互式提问）
  '帮我深度调研"双一流建设对地方高校的影响"，需要先收集政策文件，再检索学术文献，然后综合分析并撰写报告',
  // 并行编排 + 子代理委派（触发 parallel orchestrator → 多子代理并发）
  '系统对比分析中美欧三地的终身教育体系，从政策法规、实施模式、技术应用等多个方面全面综述',
  // 长期记忆体验（Viking Memory，适合老用户）
  '结合我之前的对话记录，我是什么身份？偏好是什么？关注哪些内容？帮我总结一下我最近关注的研究主题和进展',
  '帮助分别深度调研一下chatgpt、秘塔在深度研究（Deep Research）的技术原理'

]

// 动态示例问题：优先展示当前助手的自定义示例，回退到系统默认
const displayExampleQuestions = computed(() => {
  const agentQuestions = currentAgent.value?.example_questions
  if (agentQuestions && agentQuestions.length > 0) {
    return agentQuestions
  }
  return exampleQuestions
})

// ==================== 计算属性 ====================

const currentSessionTitle = computed(() => {
  if (!currentSessionId.value) return '新对话'
  const session = sessions.value.find(s => s.session_id === currentSessionId.value)
  return session?.title || '对话'
})

// 视图模式计算属性（供模板和子组件使用）
const showJsonLog = computed(() => viewMode.value === 'debug')

// ==================== 学术引用功能（已移至 StreamingContent.vue） ====================

/** 从消息列表的 toolCalls 中查找文件名对应的下载 URL */
function findFileDownloadUrl(fileName: string): string | null {
  // 从最新消息往前找
  for (let i = messages.value.length - 1; i >= 0; i--) {
    const msg = messages.value[i]
    if (!msg.toolCalls) continue
    for (const tc of msg.toolCalls) {
      if ((tc.tool === 'write_file' || tc.tool === 'file_write') && tc.result) {
        // 检查结果中是否包含该文件名
        if (tc.result.includes(fileName)) {
          const urlMatch = tc.result.match(/\[点击下载\]\(([^)]+)\)/) || tc.result.match(/下载链接:\s*(\S+)/)
          if (urlMatch) return urlMatch[1].trim()
        }
      }
    }
  }
  return null
}

/** 处理子组件传来的引用点击事件（文件下载链接） */
function handleCitationClickFromChild(event: MouseEvent, _references: Reference[]) {
  const target = event.target as HTMLElement
  // 处理文件下载链接点击（v2.0）
  if (target.tagName === 'A') {
    const href = target.getAttribute('href') || ''
    const fileId = extractFileIdFromUrl(href)
    if (fileId) {
      event.preventDefault()
      handleFileDownload(fileId, href)
      return
    }

    // v4.0: 如果链接不是标准下载 URL，尝试从 toolCalls 中查找匹配的文件
    // LLM 可能在回答中生成了文件名链接（如 [xxx.md](xxx.md)），需要映射到正确的下载 URL
    const linkText = target.textContent || ''
    const FILE_EXTENSIONS = ['.md', '.txt', '.json', '.csv', '.pdf', '.docx', '.doc']
    const looksLikeFile = FILE_EXTENSIONS.some(ext => href.endsWith(ext) || linkText.endsWith(ext))
    if (looksLikeFile) {
      // 在当前消息列表中查找 write_file 工具调用的结果
      const fileName = linkText || href.split('/').pop() || ''
      const downloadUrl = findFileDownloadUrl(fileName)
      if (downloadUrl) {
        event.preventDefault()
        const foundFileId = extractFileIdFromUrl(downloadUrl)
        if (foundFileId) {
          handleFileDownload(foundFileId, downloadUrl)
        }
        return
      }
      // 找不到匹配的文件，阻止无效跳转
      event.preventDefault()
      message.warning('文件链接无效，请使用右侧面板的下载按钮')
    }
  }
}

/** 处理文件下载（v2.0 - 带认证） */
async function handleFileDownload(fileId: string, downloadUrl: string) {
  const token = userStore.token
  if (!token) {
    message.error('请先登录')
    return
  }

  try {
    // 由于我们没有完整的文件信息，先使用简单的 Blob 下载方式
    // 对于大文件，由于接口需要认证，直接跳转不可行，统一使用 Blob
    const response = await fetch(downloadUrl, {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${token}`
      }
    })

    if (!response.ok) {
      throw new Error(`下载失败: ${response.status}`)
    }

    // 从 Content-Disposition 获取文件名，或从 URL 提取
    const contentDisposition = response.headers.get('Content-Disposition')
    let filename = `file_${fileId}`
    if (contentDisposition) {
      // 尝试解析 RFC 5987 格式的文件名
      const utf8Match = contentDisposition.match(/filename\*=UTF-8''(.+?)(?:;|$)/)
      if (utf8Match) {
        filename = decodeURIComponent(utf8Match[1])
      } else {
        const asciiMatch = contentDisposition.match(/filename="?(.+?)"?(?:;|$)/)
        if (asciiMatch) {
          filename = asciiMatch[1]
        }
      }
    } else {
      // 沙箱文件 URL 中直接包含文件名
      const urlFilename = downloadUrl.split('/').pop()
      if (urlFilename && urlFilename.includes('.')) {
        filename = urlFilename
      }
    }

    const blob = await response.blob()
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    window.URL.revokeObjectURL(url)
    document.body.removeChild(a)

    message.success(`已下载: ${filename}`)
  } catch (error) {
    console.error('文件下载失败:', error)
    message.error('文件下载失败，请重试')
  }
}

/** 判断是否为沙箱文件 URL */
function isSandboxFileUrl(url: string): boolean {
  return /\/sandbox\/files\/[a-f0-9]+\//.test(url)
}

/** 处理工具结果中的文件下载 */
async function handleToolFileDownload(downloadUrl: string | undefined) {
  if (!downloadUrl) {
    message.error('下载链接无效')
    return
  }

  // 沙箱文件或已有 fileId 的文件统一走带认证的下载
  const fileId = extractFileIdFromUrl(downloadUrl)
  if (fileId) {
    await handleFileDownload(fileId, downloadUrl)
  } else if (isSandboxFileUrl(downloadUrl)) {
    // 沙箱文件：用文件名做 ID
    const filename = downloadUrl.split('/').pop() || 'file'
    await handleFileDownload(filename, downloadUrl)
  } else {
    window.open(downloadUrl, '_blank')
  }
}

/** 打开文件预览模态框 */
function handlePreviewFile(fileResult: { filename: string; downloadUrl: string; size: string } | null) {
  if (!fileResult) {
    message.error('无法获取文件信息')
    return
  }

  const fileId = extractFileIdFromUrl(fileResult.downloadUrl)

  if (fileId) {
    // 老版 FileService 文件 — 走 FilePreviewModal
    const fileType = fileResult.filename.split('.').pop()?.toLowerCase() || 'md'
    previewFile.value = {
      id: fileId,
      filename: fileResult.filename,
      size: parseFileSizeToBytes(fileResult.size),
      file_type: fileType,
      created_at: new Date().toISOString(),
      download_count: 0
    }
    showFilePreviewModal.value = true
  } else if (isSandboxFileUrl(fileResult.downloadUrl)) {
    // 沙箱文件 — 直接用 Blob URL 预览
    previewSandboxFile(fileResult)
  } else {
    message.error('无法识别文件ID')
  }
}

/** 预览沙箱文件（图片直接显示，文本显示代码，其他下载） */
async function previewSandboxFile(fileResult: { filename: string; downloadUrl: string; size: string }) {
  const token = userStore.token
  if (!token) {
    message.error('请先登录')
    return
  }

  const ext = fileResult.filename.split('.').pop()?.toLowerCase() || ''
  const imageExts = ['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'bmp']
  const textExts = ['py', 'txt', 'md', 'json', 'csv', 'html', 'xml', 'js', 'css', 'yaml', 'yml', 'sql', 'sh', 'log', 'ini', 'toml', 'java', 'go', 'rs', 'ts', 'jsx', 'tsx', 'vue']
  const officeExts = ['docx', 'doc', 'xlsx', 'xls', 'pptx', 'ppt']

  try {
    // Office 文件：交给 FilePreviewModal（走 kkFileView 或 fallback）
    if (officeExts.includes(ext)) {
      previewFile.value = {
        id: `sandbox_${Date.now()}`,
        filename: fileResult.filename,
        size: parseFileSizeToBytes(fileResult.size),
        file_type: ext,
        created_at: new Date().toISOString(),
        download_count: 0,
        _downloadUrl: fileResult.downloadUrl,
      }
      showFilePreviewModal.value = true
      return
    }

    const response = await fetch(fileResult.downloadUrl, {
      method: 'GET',
      headers: { 'Authorization': `Bearer ${token}` }
    })
    if (!response.ok) throw new Error(`加载失败: ${response.status}`)

    if (imageExts.includes(ext)) {
      // 图片：Blob URL 预览
      const blob = await response.blob()
      const blobUrl = URL.createObjectURL(blob)
      previewFile.value = {
        id: `sandbox_${Date.now()}`,
        filename: fileResult.filename,
        size: parseFileSizeToBytes(fileResult.size),
        file_type: ext,
        created_at: new Date().toISOString(),
        download_count: 0,
        _blobUrl: blobUrl,
      }
      showFilePreviewModal.value = true
    } else if (textExts.includes(ext)) {
      // 文本/代码文件：读取内容，交给 FilePreviewModal 渲染
      const text = await response.text()
      previewFile.value = {
        id: `sandbox_${Date.now()}`,
        filename: fileResult.filename,
        size: parseFileSizeToBytes(fileResult.size),
        file_type: ext,
        created_at: new Date().toISOString(),
        download_count: 0,
        _textContent: text,
        _downloadUrl: fileResult.downloadUrl,
      }
      showFilePreviewModal.value = true
    } else {
      // 其他类型：直接下载
      const blob = await response.blob()
      const blobUrl = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = blobUrl
      a.download = fileResult.filename
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(blobUrl)
    }
  } catch (e: unknown) {
    const err = e as Error
    message.error(`预览失败: ${err.message}`)
  }
}

/** 将文件大小（字符串或数字）转换为字节数 */
function parseFileSizeToBytes(size: string | number | undefined): number {
  if (size == null) return 0
  // 如果已经是数字，直接返回
  if (typeof size === 'number') return Math.round(size)
  if (!size || size === '未知') return 0
  const match = size.match(/([\d.]+)\s*(B|KB|MB|GB|字节)?/i)
  if (!match) return 0
  const value = parseFloat(match[1])
  const unit = (match[2] || 'B').toUpperCase()
  if (unit === '字节') return Math.round(value)
  const multipliers: Record<string, number> = { 'B': 1, 'KB': 1024, 'MB': 1024 * 1024, 'GB': 1024 * 1024 * 1024 }
  return Math.round(value * (multipliers[unit] || 1))
}

/** 直接转换文件为PDF（从下载卡片触发） */
async function handleConvertToPdf(fileResult: { filename: string; downloadUrl: string; size: string } | null) {
  if (!fileResult) {
    message.error('无法获取文件信息')
    return
  }

  const fileId = extractFileIdFromUrl(fileResult.downloadUrl)
  if (!fileId) {
    message.error('无法识别文件ID')
    return
  }

  // 检查文件类型是否支持转换
  const fileType = fileResult.filename.split('.').pop()?.toLowerCase() || ''
  if (!['md', 'txt', 'json'].includes(fileType)) {
    message.warning('仅支持 Markdown、TXT、JSON 文件转换为 PDF')
    return
  }

  convertingFileId.value = fileId

  try {
    const result = await convertToPdf(fileId)
    message.success(`转换成功，耗时 ${result.conversion_time_ms}ms`)
    // 自动打开预览新生成的PDF
    previewFile.value = result.pdf_file
    showFilePreviewModal.value = true
  } catch (e: unknown) {
    const err = e as Error
    message.error(`转换失败: ${err.message}`)
  } finally {
    convertingFileId.value = null
  }
}

/** 处理预览模态框中创建的PDF文件 */
function handlePdfCreated(pdfFile: GeneratedFileV2) {
  message.success(`已生成 PDF: ${pdfFile.filename}`)
  // 可选：自动切换到预览新PDF
  previewFile.value = pdfFile
}

/** 打开原文链接 */
function openReferenceLink(url: string | undefined) {
  if (url) {
    window.open(url, '_blank', 'noopener,noreferrer')
  }
}

/** 引用分类分组 */
const refGroups = computed(() => {
  const groups: Record<string, { icon: string; label: string; count: number }> = {
    web_search: { icon: '🌐', label: '网页', count: 0 },
    web_fetch: { icon: '🔗', label: '网页抓取', count: 0 },
    kb: { icon: '📚', label: '公共知识库', count: 0 },
    kb_personal: { icon: '📁', label: '个人知识库', count: 0 },
    wikipedia: { icon: '📖', label: '维基百科', count: 0 },
    github: { icon: '💻', label: 'GitHub', count: 0 },
    scholar: { icon: '📄', label: '学术文献', count: 0 },
  }
  const scholarSources = new Set(['semantic_scholar', 'arxiv', 'wanfang', 'openalex', 'crossref'])
  for (const ref of currentReferences.value) {
    const src = ref.data_source || 'scholar'
    if (scholarSources.has(src)) {
      groups.scholar.count++
    } else if (groups[src]) {
      groups[src].count++
    }
  }
  return Object.entries(groups)
    .filter(([, g]) => g.count > 0)
    .map(([key, g]) => ({ key, ...g }))
})

/** 按分类过滤引用 */
const filteredReferences = computed(() => {
  if (refFilterTab.value === 'all') return currentReferences.value
  const scholarSources = new Set(['semantic_scholar', 'arxiv', 'wanfang', 'openalex', 'crossref'])
  return currentReferences.value.filter((ref) => {
    const src = ref.data_source || 'scholar'
    if (refFilterTab.value === 'scholar') return scholarSources.has(src)
    return src === refFilterTab.value
  })
})

/** data_source 对应的标签类型 */
function getSourceTagType(dataSource?: string): 'info' | 'success' | 'warning' | 'default' | 'primary' {
  const scholarSources = new Set(['semantic_scholar', 'arxiv', 'wanfang', 'openalex', 'crossref'])
  if (!dataSource || scholarSources.has(dataSource)) return 'primary'
  const map: Record<string, 'info' | 'success' | 'warning' | 'default'> = {
    web_search: 'info', web_fetch: 'info', kb: 'success', kb_personal: 'info', wikipedia: 'warning', github: 'default',
  }
  return map[dataSource] || 'default'
}

/** data_source 对应的图标 */
function getSourceIcon(dataSource?: string): string {
  const map: Record<string, string> = {
    web_search: '🌐', web_fetch: '🔗', kb: '📚', kb_personal: '📁', wikipedia: '📖', github: '💻',
    semantic_scholar: '📄', arxiv: '📄', wanfang: '📄', openalex: '📄', crossref: '📄',
  }
  return map[dataSource || ''] || '📄'
}

/** data_source 对应的用户友好标签名 */
function getSourceLabel(dataSource?: string): string {
  const map: Record<string, string> = {
    web_search: '网页搜索', web_fetch: '网页抓取', kb: '公共知识库', kb_personal: '个人知识库',
    wikipedia: '维基百科', github: 'GitHub',
    semantic_scholar: '学术文献', arxiv: '学术文献', wanfang: '万方', openalex: '学术文献', crossref: '学术文献',
  }
  return map[dataSource || ''] || dataSource || 'scholar'
}

// ==================== 历史会话管理 ====================

async function loadSessions() {
  sessionsLoading.value = true
  try {
    const agentId = currentAgent.value?.id
    const params: { limit?: number; offset?: number; agent_id?: number } = { limit: 50, offset: 0 }
    if (agentId != null) params.agent_id = agentId
    const res = await getSessions(params)
    // API直接返回数组
    sessions.value = res || []
  } catch (e: unknown) {
    console.error('加载会话列表失败:', e)
    sessions.value = []
  } finally {
    sessionsLoading.value = false
  }
}

/** 消息后处理：修复工具调用状态、重建 thinkingSteps、引用继承 */
function postProcessMessages() {
  for (const msg of messages.value) {
    if (msg.toolCalls) {
      // 修复中断的工具调用状态
      for (const tc of msg.toolCalls) {
        if (tc.status === 'receiving' || tc.status === 'running' || (!tc.status && !tc.result)) {
          if (tc.tool === 'done') {
            // done 工具未完成说明被拒绝，不是错误
            tc.status = 'success'
            tc.result = tc.result || '继续搜索中'
          } else if (!tc.result) {
            tc.status = 'error'
            tc.result = '执行中断（连接断开）'
          } else {
            tc.status = 'success'
          }
        }
      }
      // 从持久化的 toolCalls 重建 thinkingSteps（用于 MessageItem 渲染执行侧栏）
      if (!msg.thinkingSteps && msg.toolCalls.length > 0) {
        msg.thinkingSteps = msg.toolCalls.map((tc: Record<string, unknown>, i: number) => ({
          step: i + 1,
          text: '',
          isToolCall: true,
          toolCallId: tc.toolCallId as string,
          toolName: tc.tool as string,
          toolDisplayName: tc.toolDisplayName as string || tc.tool as string,
          input: tc.input as Record<string, unknown>,
          result: tc.result as string,
          summary: tc.summary as string,
          status: tc.status as string || 'success',
          durationMs: tc.durationMs as number,
          pathIndex: tc.pathIndex as number | undefined,
          pathLabel: tc.pathLabel as string | undefined,
        }))
      }
    }
  }
  // 多轮对话引用继承
  let accumulatedRefs = new Map<string, Reference>()
  for (const msg of messages.value) {
    if (msg.role === 'assistant') {
      if (msg.references && msg.references.length > 0) {
        for (const ref of msg.references) {
          accumulatedRefs.set(ref.id, ref)
        }
      }
      if ((!msg.references || msg.references.length === 0) && accumulatedRefs.size > 0) {
        const hasRefPattern = /\[(?:ref:)?ref_\d+\]/.test(msg.content)
        if (hasRefPattern) {
          msg.references = Array.from(accumulatedRefs.values())
        }
      }
    }
  }
}

async function switchSession(sessionId: string) {
  if (sessionId === currentSessionId.value) return

  // 中断正在进行的流式输出
  if (activeEventSource) {
    activeEventSource.close()
    activeEventSource = null
  }
  if (currentSessionId.value && loading.value) {
    stopSession(currentSessionId.value).catch(() => {})
  }
  closeHeartbeat()

  currentSessionId.value = sessionId
  messages.value = []
  loading.value = true
  chatMode.value = 'chat'
  guideStore.reset()
  searchSuggestionsStore.restoreForSession(sessionId)
  // 同步 URL
  if (route.params.sessionId !== sessionId) {
    router.replace({ name: 'SuperAgent', params: { sessionId } })
  }

  try {
    const res = await getSessionDetail(sessionId)
    // 后端返回的messages是JSON字段，格式为 {role, content, timestamp, intent?, skill?, tool_calls?, references?}
    const rawMessages = res.messages || []
    // v5.0: 提取 revision
    sessionRevision.value = res.context?.revision ?? 0
    messages.value = rawMessages.map((m: Record<string, unknown>, index: number) => ({
      id: (m.message_id as string) || `msg_${index}_${Date.now()}`,
      role: m.role as 'user' | 'assistant',
      content: m.content as string || '',
      intent: m.intent as string | undefined,
      skillName: m.skill as string | undefined,
      outputFormat: m.output_format as string | undefined,
      artifactId: m.artifact_id as string | undefined,
      // 恢复持久化的工具调用记录（用于文件卡片、执行过程等）
      toolCalls: m.tool_calls as ChatMessage['toolCalls'] | undefined,
      references: m.references as ChatMessage['references'] | undefined,
      // 恢复并行编排过程数据（用于子任务面板展示）
      parallelSubtasks: m.parallel_subtasks as ChatMessage['parallelSubtasks'] | undefined,
      parallelProgress: m.parallel_progress as ChatMessage['parallelProgress'] | undefined,
      // 恢复附件信息（后端已从 attachment_ids 展开为完整对象）
      attachments: m.attachments as ChatMessage['attachments'] | undefined,
      // 恢复 Aggregation@N 聚合数据
      aggregationProgress: m.aggregation_progress as ChatMessage['aggregationProgress'] | undefined,
      synthesisContent: m.synthesis_content as string | undefined,
      // 恢复执行计划和阶段输出（深度研究折叠摘要用）
      executionPlan: m.execution_plan as ChatMessage['executionPlan'] | undefined,
      stageOutputs: m.stage_outputs as ChatMessage['stageOutputs'] | undefined,
      // 恢复思考过程（reasoning），历史记录默认折叠
      reasoningBlocks: (m.reasoning_blocks as ChatMessage['reasoningBlocks'] | undefined)?.map(b => ({ ...b, isCollapsed: true })),
      nextSkillSuggestions: m.next_skill_suggestions as ChatMessage['nextSkillSuggestions'] | undefined,
      // Classroom 关联：从持久化 extra 恢复 classroomData.sessionId
      ...(m.classroom_session_id ? { classroomData: { sessionId: m.classroom_session_id as string, status: 'ready', events: [], scenes: [] } } : {}),
      // 消息时间戳（用于课堂关联匹配等）
      timestamp: m.timestamp as string | undefined,
      // v5.0: 版本管理字段
      messageId: m.message_id as string | undefined,
      siblings: m.siblings as string[] | undefined,
      siblingIndex: m.siblingIndex as number | undefined,
    }))

    postProcessMessages()
    scrollToBottom()

    // 加载会话关联的 Artifact
    // 先无条件 reset —— 换会话必须清掉旧 session 的 artifact，
    // 否则 getSessionArtifacts 若 404/失败会残留跨会话状态
    artifactStore.resetForSession()
    try {
      const { getSessionArtifacts } = await import('@/api/artifact')
      const artifacts = await getSessionArtifacts(sessionId, true)
      if (artifacts.length > 0) {
        artifactStore.loadArtifacts(artifacts)
        // 不自动打开面板（ChatGPT Canvas 风格：对话中显示卡片，用户点击才展开）
      }
    } catch (e) {
      console.warn('[Artifact] 加载失败:', e)
    }
  } catch {
    message.error('加载对话历史失败')
  } finally {
    loading.value = false
  }
}

function startNewChat() {
  // 中断正在进行的流式输出
  if (activeEventSource) {
    activeEventSource.close()
    activeEventSource = null
  }
  if (currentSessionId.value && loading.value) {
    // 通知后端停止生成（fire-and-forget）
    stopSession(currentSessionId.value).catch(() => {})
  }
  loading.value = false
  isSkillExecuting.value = false
  closeHeartbeat()

  currentSessionId.value = ''
  messages.value = []
  inputText.value = ''
  currentIntent.value = ''
  currentSkill.value = ''
  chatMode.value = 'chat'
  clearAttachments()
  // 重置辩论状态
  debateStatus.value = 'idle'
  currentDebateRound.value = 0
  canContinueDebate.value = false
  roundHint.value = null
  currentDebateSessionId.value = ''
  discussionMode.value = 'debate'
  // 重置思考过程状态
  currentExpertReasoning.value = null
  expertReasonings.value.clear()
  // 重置学习指导状态（清除 suggestedReplies 等）
  guideStore.reset()
  // 重置搜索建议
  searchSuggestionsStore.clear()
  // 重置错误状态
  showErrorState.value = false
  errorMessage.value = ''
  // URL 回到干净的 /qa/super-agent
  if (route.params.sessionId) {
    router.replace({ name: 'SuperAgent', params: {} })
  }
}

function handleNewChat() {
  // 已有 Agent → 直接用当前 Agent 新建对话
  if (currentAgent.value) {
    startNewChat()
    return
  }
  // 无 Agent 但有默认助手 → 加载默认助手后新建
  if (defaultAgentId.value) {
    loadDefaultAgentAndStart()
    return
  }
  // 都没有 → 弹出选择弹窗
  showAgentSelector.value = true
}

async function loadDefaultAgentAndStart() {
  try {
    const res = await getAgent(defaultAgentId.value!) as any
    currentAgent.value = res
    startNewChat()
  } catch {
    // 默认助手加载失败，降级为弹窗选择
    showAgentSelector.value = true
  }
}

function handleAgentSelect(agent: AgentInfo | null) {
  currentAgent.value = agent
  startNewChat()
}

function handleAgentSaved(agent: AgentInfo) {
  currentAgent.value = agent
  startNewChat()
}

async function handleSetDefault(agentId: number) {
  try {
    await setDefaultAgent(agentId)
    defaultAgentId.value = agentId
    message.success('已设为默认助手')
  } catch {
    message.error('设置默认助手失败')
  }
}

async function handleClearDefault() {
  try {
    await clearDefaultAgent()
    defaultAgentId.value = null
    message.success('已取消默认助手')
  } catch {
    message.error('取消默认助手失败')
  }
}

async function handleDeleteSession(sessionId: string) {
  try {
    await deleteSession(sessionId)
    sessions.value = sessions.value.filter(s => s.session_id !== sessionId)
    if (currentSessionId.value === sessionId) {
      startNewChat()
    }
    message.success('已删除')
  } catch {
    message.error('删除失败')
  }
}

// ==================== Skills管理 ====================

async function loadSkills() {
  try {
    const res = await getSkills()
    // API直接返回数组
    skills.value = res || []
  } catch (e) {
    console.error('加载Skills失败:', e)
    skills.value = []
  }
}

// ==================== 阶段进度辅助函数（已移至 useSSEHandler / types/super-agent） ====================

/** 处理错误状态（供外部调用，如网络请求 catch 块）
 * @internal 暂未使用，保留用于未来集成
 */
function handleErrorStateInternal(errorMsg: string, type?: ErrorType) {
  errorMessage.value = errorMsg
  errorType.value = type || 'unknown'
  showErrorState.value = true
}
void handleErrorStateInternal // suppress unused warning

/** 重试操作 */
function handleRetry() {
  showErrorState.value = false
  isRetrying.value = true
  // 重新发送最后一条用户消息
  const lastUserMsg = [...messages.value].reverse().find(m => m.role === 'user')
  if (lastUserMsg) {
    inputText.value = lastUserMsg.content
    // 删除最后的用户消息和对应的助手消息
    const userMsgIndex = messages.value.indexOf(lastUserMsg)
    if (userMsgIndex >= 0) {
      messages.value = messages.value.slice(0, userMsgIndex)
    }
    nextTick(() => {
      sendMessage()
      isRetrying.value = false
    })
  } else {
    isRetrying.value = false
  }
}

/** 关闭错误状态 */
function handleErrorClose() {
  showErrorState.value = false
}

// ==================== 消息处理 ====================

// 🆕 用户滚动状态跟踪
const userHasScrolledUp = ref(false)
const scrollThreshold = 100 // 距离底部多少像素内认为是"在底部"

// 检测是否在底部附近
const isNearBottom = () => {
  if (!messagesContainer.value) return true
  const { scrollTop, scrollHeight, clientHeight } = messagesContainer.value
  return scrollHeight - scrollTop - clientHeight < scrollThreshold
}

// 处理用户滚动事件
const handleScroll = () => {
  if (!messagesContainer.value) return
  // 如果用户滚动到底部附近，恢复自动滚动
  if (isNearBottom()) {
    userHasScrolledUp.value = false
  } else {
    // 用户向上滚动了
    userHasScrolledUp.value = true
  }
}

const scrollToBottom = (force = false) => {
  // 如果用户主动向上滚动查看内容，不自动滚动（除非强制）
  if (userHasScrolledUp.value && !force) {
    return
  }
  nextTick(() => {
    if (messagesContainer.value) {
      messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
    }
  })
}

// 强制滚动到底部（用于用户发送消息等场景）
const forceScrollToBottom = () => {
  userHasScrolledUp.value = false
  scrollToBottom(true)
}

// 🆕 RAF 节流滚动（每帧最多滚动一次，消除 setTimeout 延迟导致的抖动）
let scrollRafId: number | null = null
const throttledScrollToBottom = () => {
  if (scrollRafId) return
  scrollRafId = requestAnimationFrame(() => {
    scrollToBottom()
    scrollRafId = null
  })
}

// ==================== SSE Handler 初始化 ====================
const sseHandler = useSSEHandler({
  messages,
  viewMode,
  currentSkill,
  currentIntent,
  isSkillExecuting,
  isGeneratingAnswer,
  skillExecutionTime,
  currentSessionId,
  currentReferences,
  dataSources,
  debateStatus,
  currentDebateRound,
  maxDebateRounds,
  canContinueDebate,
  roundHint: roundHint as Ref<unknown>,
  currentDebateSessionId,
  discussionMode,
  pendingModeSelection,
  currentExpertReasoning,
  expertReasonings,
  scrollToBottom,
  throttledScrollToBottom,
  messageApi: message,
})

const {
  currentPhase,
  latestEventType,
  latestEventData,
  connectionHealth,
  debugCapture,
  handleSSEEvent,
  resetSSEState,
  resetHeartbeat,
  closeHeartbeat,
  queryRewriteInfo,
} = sseHandler

const sendExample = (text: string) => {
  inputText.value = text
  sendMessage()
}

// ==================== @mention 技能选择辅助函数 ====================

/** PC 端 ChatInputArea 的 mention-input 事件处理 */
function onPcMentionInput(text: string, cursorPos: number) {
  handleMentionInput(text, cursorPos)
}

/** PC 端输入框 @keydown 事件处理 */
function onPcInputKeydown(e: KeyboardEvent) {
  // 先让 mention 处理，如果消费了就不走 Enter 发送
  if (handleMentionKeydown(e)) return
  // 原有 Enter 发送逻辑
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    sendMessage()
  }
}

/** 选择 mention skill 后清除输入框中的 @xxx 文本 */
function onMentionSelect(skill: MentionSkill) {
  const range = selectSkill(skill)
  if (range) {
    const text = inputText.value
    inputText.value = text.slice(0, range.start) + text.slice(range.end)
  }
}

/** [BUG-2] 共享 SSE 事件类型常量 — 主连接和重连共用，避免白名单不同步 */
/**
 * SSE 连接基地址：开发环境直连后端（绕过 Vite http-proxy，避免并发 SSE 流混淆），
 * 生产环境走 nginx 代理（相对路径）。
 */
const SSE_BASE_URL = import.meta.env.DEV
  ? (import.meta.env.VITE_SSE_BASE_URL || 'http://127.0.0.1:8100')
  : ''

const SSE_EVENT_TYPES = [
  'thinking', 'intent', 'skill_start', 'content', 'skill_done',
  'action', 'observation', 'tool_call', 'tool_result', 'tool_call_start',
  'tool_call_delta', 'references', 'answering', 'cancelled',
  'sub_agent_start', 'sub_agent_action', 'sub_agent_observation',
  'sub_agent_complete', 'sub_agent_error', 'sub_agent_thinking', 'sub_agent_content',
  'sub_agent_tool_call_start', 'sub_agent_tool_call_delta', 'sub_agent_answering',
  'subagent_batch_start', 'subagent_batch_done',
  'debate_created', 'round_start', 'round_end', 'final_summary', 'debate_done',
  'reasoning_chunk', 'reasoning_done',
  'session_created', 'heartbeat', 'client_tool',
  'plan_created', 'plan_submitted', 'plan_approved', 'plan_rejected', 'plan_step', 'plan_completed',
  'intent_confirm', 'analyze_plan',
  'parallel_decompose', 'parallel_progress',
  'parallel_subtask_start', 'parallel_subtask_done',
  'parallel_subtask_progress', 'parallel_subtask_content',
  'parallel_subtask_tool', 'parallel_subtask_content_reset',
  'parallel_subtask_scope_retry', 'parallel_synthesizing',
  'parallel_diagnosing', 'diagnostic_escalation', 'peer_review',
  'memory_context', 'verification', 'data_source_progress',
  'status', 'speech', 'round_summary', 'iteration_start',
  // 事件补全
  'llm_usage', 'expert_intro', 'speech_chunk',
  // Guide 学习状态事件
  'skill_state',
  // Perplexica 迁移新增事件
  'suggestions', 'query_rewrite', 'phase',
  // Pipeline Skill 阶段推进（slide-generator 等）
  'progress',
  // Aggregation@N 聚合增强事件
  'aggregation_progress', 'synthesis_content',
  // plan_step 内容重置
  'plan_step_content_reset',
  'routing',
  // 模式选择事件
  'mode_selection_required',
  // Skill 推荐链
  'next_skill_suggestions',
  // Artifact 系统事件
  'artifact_create', 'artifact_delta', 'artifact_done',
  // Classroom 课堂模式事件
  'classroom_outline_ready', 'classroom_scene_generated',
  'classroom_scene_audio_ready', 'classroom_scene_failed',
  'classroom_ready', 'classroom_scene_change',
  'classroom_action_start', 'classroom_action_done',
  'classroom_status_change',
  'classroom_discussion_start', 'classroom_discussion_end',
] as const

/** 断连重连：使用 EventSource（原生自动重连） */
async function reconnectWithBackoff(
  sessionId: string,
  lastEventId: string,
  assistantMsgId: string,
  maxRetries = 5
): Promise<boolean> {
  // 构建 EventSource URL（token 通过 query param 传递，因为 EventSource 无法设置 Header）
  // 开发环境直连后端，避免 Vite http-proxy 并发 SSE 流混淆
  const params = new URLSearchParams()
  if (lastEventId) params.set('last_event_id', lastEventId)
  params.set('token', userStore.token)
  const url = `${SSE_BASE_URL}/api/user/super-agent/chat/events/${sessionId}?${params.toString()}`

  return new Promise<boolean>((resolve) => {
    let retryCount = 0
    let resolved = false

    const es = new EventSource(url)
    activeEventSource = es
    let currentEventId = lastEventId

    // [BUG-2] 使用共享常量，主连接和重连事件列表一致
    function handleEvent(e: MessageEvent) {
      try {
        const data = JSON.parse(e.data)
        // 防串流校验：如果事件携带 _sid 且与当前 session 不匹配，丢弃
        if (data._sid && data._sid !== sessionId) {
          console.warn(`[SSE reconnect] 事件串流！期望 session=${sessionId}，收到 session=${data._sid}，事件已丢弃`)
          return
        }
        // 追踪 event id
        if (e.lastEventId) {
          currentEventId = e.lastEventId
          sessionStorage.setItem(`sse_last_event_id_${sessionId}`, currentEventId)
        }
        handleSSEEvent(e.type, data, assistantMsgId)
      } catch {
        // JSON 解析错误，忽略
      }
    }

    for (const type of SSE_EVENT_TYPES) {
      es.addEventListener(type, handleEvent)
    }

    function cleanup() {
      if (activeEventSource === es) activeEventSource = null
    }

    // done 事件：流结束
    es.addEventListener('done', (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data)
        // 防串流校验
        if (data._sid && data._sid !== sessionId) {
          console.warn(`[SSE reconnect] done 事件串流！期望 session=${sessionId}，收到 session=${data._sid}`)
          return
        }
        handleSSEEvent('done', data, assistantMsgId)
      } catch { /* ignore */ }
      es.close()
      cleanup()
      if (!resolved) {
        resolved = true
        resolve(true)
      }
    })

    // error 事件（SSE 业务错误）
    es.addEventListener('error', (e: Event) => {
      // 区分 SSE 业务 error 事件和 EventSource 连接错误
      const me = e as MessageEvent
      if (me.data) {
        try {
          const data = JSON.parse(me.data)
          handleSSEEvent('error', data, assistantMsgId)
        } catch { /* ignore */ }
        es.close()
        cleanup()
        if (!resolved) {
          resolved = true
          resolve(true) // 业务错误也算重连成功（事件已送达）
        }
        return
      }

      // EventSource 连接错误 → 自动重连（浏览器内置）
      retryCount++
      console.warn(`[SSE EventSource] 连接错误，重试 ${retryCount}/${maxRetries}`)
      if (retryCount >= maxRetries) {
        es.close()
        cleanup()
        if (!resolved) {
          resolved = true
          resolve(false)
        }
      }
    })

    es.onopen = () => {
      console.log('[SSE EventSource] 重连成功')
      retryCount = 0 // 连接成功，重置重试计数
      resetHeartbeat()
    }
  })
}

/** 复制 artifact 内容 */
async function handleArtifactCopy(artifactId: string) {
  const art = artifactStore.artifacts.get(artifactId)
  if (!art) return
  const content = art.contents?.[art.contents.length - 1]?.content || ''
  await navigator.clipboard.writeText(content)
  message.success('已复制到剪贴板')
}

/** 导出 artifact */
async function handleArtifactExport(artifactId: string, format: string) {
  try {
    const { exportArtifact } = await import('@/api/artifact')
    const result = await exportArtifact(artifactId, format as 'docx' | 'pdf')
    // 带鉴权下载（window.open 不携带 Authorization header）
    const token = localStorage.getItem('token') || ''
    const baseURL = import.meta.env.VITE_API_BASE_URL || ''
    const resp = await fetch(`${baseURL}/api/user/super-agent/files/${result.file_id}/download`, {
      headers: { 'Authorization': `Bearer ${token}` },
    })
    if (!resp.ok) throw new Error(`下载失败: HTTP ${resp.status}`)
    const blob = await resp.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = result.filename
    document.body.appendChild(a)
    a.click()
    URL.revokeObjectURL(url)
    document.body.removeChild(a)
    message.success('导出成功')
  } catch {
    message.info('导出功能开发中')
  }
}

/** 获取 artifact 前几行内容的 HTML 预览 */
function getArtifactPreviewHtml(artifactId: string, references?: Reference[]): string {
  // 触碰 artifactCount 确保 Map.set() 后 Vue 能追踪到变化并重新渲染
  void artifactStore.artifactCount
  const art = artifactStore.artifacts.get(artifactId)
  if (!art) return ''
  let content = art.contents?.[art.contents.length - 1]?.content || ''
  // 清理开头的对话性文字
  const titleIdx = content.search(/^#{1,4}\s+\S/m)
  if (titleIdx > 0) content = content.slice(titleIdx)
  content = content.replace(/^[\s#]*---[\s]*\n*/m, '')
  // 截取前 500 字符（更多预览内容）
  const preview = content.slice(0, 500)
  // 引用占位符保护
  const { content: protectedContent, placeholders } = protectCitationMarks(preview)
  // 简单 Markdown → HTML（标题、粗体、列表）
  let html = protectedContent
    .replace(/^### (.+)/gm, '<h4>$1</h4>')
    .replace(/^## (.+)/gm, '<h3>$1</h3>')
    .replace(/^# (.+)/gm, '<h2>$1</h2>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\n\n/g, '<br>')
    .replace(/\n/g, '<br>')
  // 还原引用角标
  html = restoreCitationMarks(html, placeholders, references || [])
  return html + (content.length > 500 ? '<span style="color:#9ca3af">...</span>' : '')
}

/** 获取 artifact 预览 HTML（支持流式生成中实时预览） */
function getArtifactStreamingPreviewHtml(artifactId: string, references?: Reference[]): string {
  // 如果正在流式生成此 artifact，使用 streamingContent
  const isStreaming = artifactStore._isBackgroundStreaming && artifactStore.activeArtifactId === artifactId
  if (isStreaming && artifactStore.streamingContent) {
    let content = artifactStore.streamingContent
    // 清理开头的对话性文字
    const titleIdx = content.search(/^#{1,4}\s+\S/m)
    if (titleIdx > 0) content = content.slice(titleIdx)
    // 流式预览展示更多内容以支持自动滚动
    const preview = content.slice(0, 2000)
    let html = preview
      .replace(/^### (.+)/gm, '<h4>$1</h4>')
      .replace(/^## (.+)/gm, '<h3>$1</h3>')
      .replace(/^# (.+)/gm, '<h2>$1</h2>')
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/\n\n/g, '<br>')
      .replace(/\n/g, '<br>')
    return html + '<span style="color:#9ca3af">...</span>'
  }
  // 已完成：走原逻辑
  return getArtifactPreviewHtml(artifactId, references)
}

/** 判断是否 slides artifact（按 id 前缀，不依赖 store 缓存） */
function isSlideArtifact(artifactId: string): boolean {
  return !!artifactId && artifactId.startsWith('slides_')
}

/** slides artifact 的页数（气泡卡片副标题用；store 无缓存时返回 0） */
function getSlideCount(artifactId: string): number {
  void artifactStore.artifactCount
  const art = artifactStore.artifacts.get(artifactId)
  if (!art) return 0
  const content = art.contents?.[art.contents.length - 1]?.content || ''
  if (!content) return 0
  try {
    const obj = JSON.parse(content)
    return Array.isArray(obj?.slides) ? obj.slides.length : 0
  } catch {
    return 0
  }
}

/** 获取 artifact 标题（用于对话中的内联卡片） */
function getArtifactTitle(artifactId: string): string {
  void artifactStore.artifactCount
  const art = artifactStore.artifacts.get(artifactId)
  // slides：优先从 JSON 里读 metadata.title
  if (isSlideArtifact(artifactId)) {
    if (art) {
      const content = art.contents?.[art.contents.length - 1]?.content || ''
      try {
        const obj = JSON.parse(content)
        if (obj?.metadata?.title) return String(obj.metadata.title).slice(0, 40)
      } catch { /* ignore */ }
      return art.title?.slice(0, 40) || '演示文稿'
    }
    return '演示文稿'
  }
  if (!art) return '查看文档'
  // markdown / code：从内容提取标题
  const content = art.contents?.[art.contents.length - 1]?.content || ''
  const match = content.match(/^#{1,3}\s+(.+)/m)
  if (match) return match[1].trim().slice(0, 40)
  return art.title?.slice(0, 40) || '查看文档'
}

/** 快捷回复：仅填入输入框，不自动发送（用户可编辑后手动发送） */
function handleQuickReply(text: string) {
  inputText.value = text
  nextTick(() => sendMessage())
}

// ==================== v5.0: 多轮对话版本管理 ====================

/** 可用 Skill 列表（转换为 RegenerateMenu 需要的格式） */
const availableSkillOptions = computed(() => {
  return skills.value.map(s => ({
    name: s.name,
    displayName: s.display_name,
    description: s.description,
  }))
})

/** 重新生成 assistant 消息 */
async function handleRegenerate(messageId: string, forceSkill?: string) {
  if (!currentSessionId.value || loading.value) return

  try {
    loading.value = true

    // 确保有最新的 messageId 和 revision（首轮对话后本地消息可能没有 messageId）
    if (!messageId) {
      await reloadCurrentSession()
      const lastAssistant = [...messages.value].reverse().find(m => m.role === 'assistant')
      messageId = lastAssistant?.messageId || ''
      if (!messageId) {
        window.$message?.error('无法确定消息 ID，请刷新页面重试')
        return
      }
    }

    const res = await regenerateMessage({
      session_id: currentSessionId.value,
      message_id: messageId,
      force_skill: forceSkill,
      agent_id: currentAgent.value?.id,
      revision: sessionRevision.value,
      ...(chatMode.value !== 'chat' ? { mode: chatMode.value } : {}),
    })
    sessionRevision.value = res.revision

    // 添加流式占位消息
    const streamingMsg: ChatMessage = {
      id: res.new_message_id,
      role: 'assistant',
      content: '',
      isStreaming: true,
      messageId: res.new_message_id,
    }

    // 找到被重新生成的 assistant 消息的位置，替换为新的流式消息
    const msgIndex = messages.value.findIndex(m => m.messageId === messageId)
    if (msgIndex !== -1) {
      // 截断该消息及之后的所有消息
      messages.value = messages.value.slice(0, msgIndex)
    }
    messages.value.push(streamingMsg)

    // 连接 SSE
    await connectEventSource(currentSessionId.value, res.new_message_id)
    // SSE 完成后重新加载（跳过有待确认意图的情况）
    if (!messages.value.some(m => m.pendingIntentConfirm || m.pendingModeSelection)) {
      try { await reloadCurrentSession() } catch (err) { console.error('[regenerate] reloadCurrentSession 失败:', err) }
    }
  } catch (e: any) {
    console.error('[regenerate] 异常:', e)
    if (e?.response?.status === 409) {
      // 版本冲突：自动 reload 获取最新 revision 后重试
      await reloadCurrentSession()
      loading.value = false
      // 用最新的 messageId 重试一次
      const lastAssistant = [...messages.value].reverse().find(m => m.role === 'assistant')
      if (lastAssistant?.messageId) {
        return handleRegenerate(lastAssistant.messageId, forceSkill)
      }
    } else {
      window.$message?.error(e?.message || '重新生成失败')
    }
  } finally {
    loading.value = false
    closeHeartbeat()
    scrollToBottom()
  }
}

/** 编辑用户消息并重新生成 */
async function handleEditMessage(messageId: string, newContent: string) {
  if (!currentSessionId.value || loading.value) return

  try {
    loading.value = true

    // 确保有最新的 messageId（首轮对话后本地消息可能没有 messageId）
    if (!messageId) {
      await reloadCurrentSession()
      const lastUser = [...messages.value].reverse().find(m => m.role === 'user')
      messageId = lastUser?.messageId || ''
      if (!messageId) {
        window.$message?.error('无法确定消息 ID，请刷新页面重试')
        return
      }
    }

    const res = await editMessage({
      session_id: currentSessionId.value,
      message_id: messageId,
      new_content: newContent,
      agent_id: currentAgent.value?.id,
      revision: sessionRevision.value,
      ...(chatMode.value !== 'chat' ? { mode: chatMode.value } : {}),
    })
    sessionRevision.value = res.revision

    // 找到被编辑消息的位置
    const msgIndex = messages.value.findIndex(m => m.messageId === messageId)
    if (msgIndex !== -1) {
      // 截断该消息及之后的所有消息，替换为编辑后的用户消息 + 流式占位
      messages.value = messages.value.slice(0, msgIndex)
    }

    // 添加编辑后的用户消息
    messages.value.push({
      id: `edited_${Date.now()}`,
      role: 'user',
      content: newContent,
    })

    // 添加流式占位
    messages.value.push({
      id: res.new_message_id,
      role: 'assistant',
      content: '',
      isStreaming: true,
      messageId: res.new_message_id,
    })

    await connectEventSource(currentSessionId.value, res.new_message_id)
    // SSE 完成后重新加载（跳过有待确认意图/模式选择的情况）
    if (!messages.value.some(m => m.pendingIntentConfirm || m.pendingModeSelection)) {
      await reloadCurrentSession()
    }
  } catch (e: any) {
    if (e?.response?.status === 409) {
      // 版本冲突：自动 reload（编辑操作不自动重试，因为内容可能变了）
      await reloadCurrentSession()
      window.$message?.info('会话已更新，请重新编辑')
    } else {
      window.$message?.error(e?.message || '编辑失败')
    }
  } finally {
    loading.value = false
    closeHeartbeat()
    scrollToBottom()
  }
}

/** 切换消息版本 */
async function handleSwitchVersion(targetMessageId: string) {
  if (!currentSessionId.value) return
  if (loading.value) {
    // 生成中：自动停止后切换
    await stopGeneration()
    // 等待 loading 状态清除
    await new Promise(resolve => setTimeout(resolve, 300))
  }

  try {
    const res = await switchVersion({
      session_id: currentSessionId.value,
      target_message_id: targetMessageId,
      revision: sessionRevision.value,
    })
    sessionRevision.value = res.revision

    // 用返回的消息列表替换当前显示
    await reloadCurrentSession()
  } catch (e: any) {
    if (e?.response?.status === 409) {
      // 版本冲突：自动 reload 后重试
      await reloadCurrentSession()
      return handleSwitchVersion(targetMessageId)
    } else {
      window.$message?.error(e?.message || '切换版本失败')
    }
  }
}

/** 重新加载当前会话 */
async function reloadCurrentSession() {
  if (currentSessionId.value) {
    // 保留内存中的 thinkingSteps（DB 不持久化过程 step，reload 会清空导致过程折叠区消失）
    // 匹配策略：messageId 优先、index 兜底（首轮消息还没拿到 messageId 前的情况）
    const stepsByMessageId = new Map<string, ChatMessage['thinkingSteps']>()
    const stepsByIndex: (ChatMessage['thinkingSteps'] | undefined)[] = []
    messages.value.forEach((prev, i) => {
      if (prev.thinkingSteps?.length) {
        if (prev.messageId) stepsByMessageId.set(prev.messageId, prev.thinkingSteps)
        stepsByIndex[i] = prev.thinkingSteps
      }
    })

    const res = await getSessionDetail(currentSessionId.value)
    sessionRevision.value = res.context?.revision ?? 0
    const rawMessages = res.messages || []
    messages.value = rawMessages.map((m: Record<string, unknown>, index: number) => ({
      id: (m.message_id as string) || `msg_${index}_${Date.now()}`,
      role: m.role as 'user' | 'assistant',
      content: m.content as string || '',
      intent: m.intent as string | undefined,
      skillName: m.skill as string | undefined,
      outputFormat: m.output_format as string | undefined,
      artifactId: m.artifact_id as string | undefined,
      toolCalls: m.tool_calls as ChatMessage['toolCalls'] | undefined,
      references: m.references as ChatMessage['references'] | undefined,
      parallelSubtasks: m.parallel_subtasks as ChatMessage['parallelSubtasks'] | undefined,
      parallelProgress: m.parallel_progress as ChatMessage['parallelProgress'] | undefined,
      attachments: m.attachments as ChatMessage['attachments'] | undefined,
      aggregationProgress: m.aggregation_progress as ChatMessage['aggregationProgress'] | undefined,
      synthesisContent: m.synthesis_content as string | undefined,
      executionPlan: m.execution_plan as ChatMessage['executionPlan'] | undefined,
      stageOutputs: m.stage_outputs as ChatMessage['stageOutputs'] | undefined,
      reasoningBlocks: (m.reasoning_blocks as ChatMessage['reasoningBlocks'] | undefined)?.map(b => ({ ...b, isCollapsed: true })),
      nextSkillSuggestions: m.next_skill_suggestions as ChatMessage['nextSkillSuggestions'] | undefined,
      thinkingSteps: (m.message_id && stepsByMessageId.get(m.message_id as string)) || stepsByIndex[index],
      // Classroom 关联：从持久化 extra 恢复 classroomData.sessionId
      ...(m.classroom_session_id ? { classroomData: { sessionId: m.classroom_session_id as string, status: 'ready', events: [], scenes: [] } } : {}),
      timestamp: m.timestamp as string | undefined,
      messageId: m.message_id as string | undefined,
      siblings: m.siblings as string[] | undefined,
      siblingIndex: m.siblingIndex as number | undefined,
    }))
    postProcessMessages()
    loading.value = false

    // Phase 4 修复：reloadCurrentSession 后同步加载 artifacts，避免 store 数据
    // 与 messages 不一致导致预览卡片空白（switchSession 已有此逻辑，这里补齐）
    try {
      const { getSessionArtifacts } = await import('@/api/artifact')
      const artifacts = await getSessionArtifacts(currentSessionId.value, true)
      if (artifacts.length > 0) {
        artifactStore.loadArtifacts(artifacts)
      }
    } catch (e) {
      console.warn('[Artifact] reload 加载失败:', e)
    }
  }
}

/** 从知识库导入文档作为附件 */
async function handleImportKBDocs(docIds: string[]) {
  try {
    await attachmentImportFromKB(docIds, currentSessionId.value || undefined)
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : '导入失败'
    window.$message?.error(msg)
  }
}

async function sendMessage() {
  const query = inputText.value.trim()
  const currentAttachmentIds = readyAttachmentIds.value
  const currentAttachments = attachments.value.filter(a => a.status === 'ready')

  // 需要有文本或附件才能发送
  if ((!query && currentAttachmentIds.length === 0) || loading.value) return
  // 有附件正在上传时不允许发送
  if (attachmentUploading.value) return

  // 画布模式：发送前自动保存编辑中的文档
  if (artifactStore.panelMode === 'editing') {
    await artifactStore.saveIfDirty()
  }

  // 清除可能存在的意图确认卡片（防竞态）
  const pendingMsg = pendingIntentConfirm.value
  if (pendingMsg) {
    pendingMsg.pendingIntentConfirm = undefined
  }

  // 添加用户消息（含附件信息）
  const userMsgId = `user_${Date.now()}`
  messages.value.push({
    id: userMsgId,
    role: 'user',
    content: query || '(附件)',
    attachments: currentAttachments.length > 0 ? [...currentAttachments] : undefined,
  })
  inputText.value = ''
  clearAttachments()
  loading.value = true
  forceScrollToBottom()  // 用户发送消息时强制滚动到底部

  // 创建流式响应占位
  const assistantMsgId = `assistant_${Date.now()}`
  const assistantMsg: ChatMessage = {
    id: assistantMsgId,
    role: 'assistant',
    content: '',
    isStreaming: true,
    thinkingSteps: [],
    toolCalls: [],
  }
  messages.value.push(assistantMsg)
  forceScrollToBottom()  // 用户发送消息时强制滚动到底部

  // 重置执行状态
  currentIntent.value = ''
  currentSkill.value = ''
  isSkillExecuting.value = false
  skillExecutionTime.value = 0

  // 重置阶段进度、SSE 调试和连接健康状态
  showErrorState.value = false
  errorMessage.value = ''
  dataSources.value = []
  // 清除上一轮的搜索建议、快捷回复和查询改写提示
  searchSuggestionsStore.clear()
  guideStore.reset()
  queryRewriteBarExpanded.value = false
  resetSSEState()

  try {
    // Phase 6: POST→202 异步模式
    const baseURL = import.meta.env.VITE_API_BASE_URL || ''
    const postUrl = `${baseURL}/api/user/super-agent/chat/stream?async=true`
    const postRes = await fetch(postUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${userStore.token}`,
      },
      body: JSON.stringify({
        message: query,
        session_id: currentSessionId.value || undefined,
        protocol_version: 2,
        ...(currentAgent.value ? { agent_id: currentAgent.value.id } : {}),
        ...(selectedSkill.value ? { force_skill: selectedSkill.value.name } : {}),
        ...(chatMode.value !== 'chat' && selectedSkill.value?.name !== '__debate__'
          ? { mode: chatMode.value } : {}),
        ...(discussionMode.value && selectedSkill.value?.name === '__debate__'
          ? { discussion_mode: discussionMode.value } : {}),
        ...(currentAttachmentIds.length > 0 ? { attachment_ids: currentAttachmentIds } : {}),
      }),
    })

    if (!postRes.ok) {
      const errorText = await postRes.text()
      throw new Error(errorText || `请求失败: ${postRes.status}`)
    }

    const { session_id } = await postRes.json()
    currentSessionId.value = session_id
    // 同步 URL（新对话首次获得 session_id）
    if (route.params.sessionId !== session_id) {
      router.replace({ name: 'SuperAgent', params: { sessionId: session_id } })
    }

    // 发送后清除选中的 skill
    clearSkill()

    // 连接 EventSource 接收事件
    await connectEventSource(session_id, assistantMsgId)
  } catch (error: unknown) {
    console.error('请求失败:', error)

    const msgIndex = messages.value.findIndex(m => m.id === assistantMsgId)
    if (msgIndex !== -1 && messages.value[msgIndex]) {
      messages.value[msgIndex]!.content = '抱歉，发生了错误，请稍后重试。'
      messages.value[msgIndex]!.isStreaming = false
    }
    const errorMsg = error instanceof Error ? error.message : '请求失败'
    message.error(errorMsg)
  } finally {
    loading.value = false
    isSkillExecuting.value = false
    closeHeartbeat()
    scrollToBottom()
    // 刷新会话列表（新会话创建或标题更新）
    loadSessions()
    // v5.0: SSE 完成后静默 reload，获取 messageId/siblings 以支持版本导航
    // 但如果有待确认的意图（pendingIntentConfirm），跳过 reload 避免覆盖确认 UI
    const hasPendingConfirm = messages.value.some(m => m.pendingIntentConfirm || m.pendingModeSelection)
    if (currentSessionId.value && !hasPendingConfirm) {
      reloadCurrentSession().catch((err) => {
        console.warn('[SuperAgent] reload 失败，保留内存数据:', err)
      })
    }
  }
}

/** 连接 EventSource 接收 SSE 事件（Phase 6 核心） */
function connectEventSource(sessionId: string, assistantMsgId: string): Promise<void> {
  // 开发环境直连后端，避免 Vite http-proxy 并发 SSE 流混淆
  const params = new URLSearchParams()
  params.set('token', userStore.token)
  const url = `${SSE_BASE_URL}/api/user/super-agent/chat/events/${sessionId}?${params.toString()}`

  return new Promise<void>((resolve, reject) => {
    const es = new EventSource(url)
    activeEventSource = es
    let lastReceivedEventId = ''

    function cleanup() {
      if (activeEventSource === es) activeEventSource = null
    }

    function handleEvent(e: MessageEvent) {
      try {
        const data = JSON.parse(e.data)
        // 防串流校验：如果事件携带 _sid 且与当前 session 不匹配，丢弃
        if (data._sid && data._sid !== sessionId) {
          console.warn(`[SSE] 事件串流！期望 session=${sessionId}，收到 session=${data._sid}，事件已丢弃`)
          return
        }
        if (e.lastEventId) {
          lastReceivedEventId = e.lastEventId
          sessionStorage.setItem(`sse_last_event_id_${sessionId}`, e.lastEventId)
        }
        handleSSEEvent(e.type, data, assistantMsgId)
      } catch { /* ignore */ }
    }

    for (const type of SSE_EVENT_TYPES) {
      es.addEventListener(type, handleEvent)
    }

    es.addEventListener('done', (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data)
        // 防串流校验
        if (data._sid && data._sid !== sessionId) {
          console.warn(`[SSE] done 事件串流！期望 session=${sessionId}，收到 session=${data._sid}`)
          return  // 不关闭连接，等待自己的 done
        }
        handleSSEEvent('done', data, assistantMsgId)
      } catch { /* ignore */ }
      es.close()
      cleanup()
      resolve()
    })

    es.addEventListener('error', async (e: Event) => {
      const me = e as MessageEvent
      if (me.data) {
        try {
          const data = JSON.parse(me.data)
          handleSSEEvent('error', data, assistantMsgId)
        } catch { /* ignore */ }
        es.close()
        cleanup()
        resolve()
        return
      }
      // [BUG-2 + B1] 连接错误 → 关闭浏览器自动重连，启用自定义退避重连
      es.close()
      cleanup()
      const lastId = lastReceivedEventId || sessionStorage.getItem(`sse_last_event_id_${sessionId}`) || ''
      try {
        const success = await reconnectWithBackoff(sessionId, lastId, assistantMsgId)
        if (success) {
          resolve()
        } else {
          reject(new Error('SSE 重连失败'))
        }
      } catch (err) {
        reject(err)
      }
    })

    es.onopen = () => {
      resetHeartbeat()
    }
  })
}

// handleSSEEvent 已移至 useSSEHandler composable

// ==================== 停止生成 ====================

async function stopGeneration() {
  if (!currentSessionId.value) return
  try {
    await stopSession(currentSessionId.value)
  } catch (e) {
    console.warn('停止请求失败:', e)
  }
}

// ==================== Intent Confirm (P1b) ====================

const pendingIntentConfirm = computed(() => {
  return messages.value.findLast(m => m.role === 'assistant' && m.pendingIntentConfirm) || null
})

async function handleIntentConfirm(skillName: string) {
  const msg = pendingIntentConfirm.value
  if (!msg?.pendingIntentConfirm) return

  const originalMessage = msg.pendingIntentConfirm.originalMessage
  // 清除确认状态
  msg.pendingIntentConfirm = undefined
  msg.content = ''

  // 重新发送，带 force_skill
  loading.value = true
  msg.isStreaming = true

  // 重置执行状态
  currentIntent.value = ''
  currentSkill.value = ''
  isSkillExecuting.value = false
  skillExecutionTime.value = 0
  showErrorState.value = false
  errorMessage.value = ''
  dataSources.value = []
  resetSSEState()

  try {
    const baseURL = import.meta.env.VITE_API_BASE_URL || ''
    const postUrl = `${baseURL}/api/user/super-agent/chat/stream?async=true`
    const postRes = await fetch(postUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${userStore.token}`,
      },
      body: JSON.stringify({
        message: originalMessage,
        session_id: currentSessionId.value || undefined,
        force_skill: skillName,
        ...(chatMode.value !== 'chat' && !skillName.startsWith('__') ? { mode: chatMode.value } : {}),
      }),
    })

    if (!postRes.ok) {
      const errorText = await postRes.text()
      throw new Error(errorText || `请求失败: ${postRes.status}`)
    }

    const { session_id } = await postRes.json()
    currentSessionId.value = session_id
    if (route.params.sessionId !== session_id) {
      router.replace({ name: 'SuperAgent', params: { sessionId: session_id } })
    }
    await connectEventSource(session_id, msg.id)
  } catch (error: unknown) {
    console.error('意图确认请求失败:', error)
    msg.content = '抱歉，发生了错误，请稍后重试。'
    msg.isStreaming = false
    const errorMsg = error instanceof Error ? error.message : '请求失败'
    message.error(errorMsg)
  } finally {
    loading.value = false
  }
}

function handleIntentCancel() {
  const msg = pendingIntentConfirm.value
  if (!msg) return
  msg.pendingIntentConfirm = undefined
  msg.content = '已取消，你可以重新描述需求。'
}

/** Skill 推荐链：用户点击推荐按钮，以推荐 prompt 作为新消息发送，并强制指定 Skill */
function handleNextSkillSuggestion(skillName: string, prompt: string) {
  inputText.value = prompt
  selectedSkill.value = { name: skillName, displayName: skillName }
  nextTick(() => sendMessage())
}

// ==================== Plan Mode ====================

/** 是否有待审批的计划（阻断式审批：禁用输入框） */
const hasPendingPlan = computed(() =>
  messages.value.some(m => m.executionPlan?.status === 'submitted')
)

async function handlePlanApprove(planId: string, steps?: Array<{ title: string; description: string }>) {
  try {
    await approvePlan(planId, steps)
  } catch (e: any) {
    const msg = e?.response?.status === 404 ? '计划已过期，请重新发起' : '批准失败，请重试'
    message.error(msg)
  }
}

async function handlePlanReject(planId: string) {
  try {
    await rejectPlan(planId)
  } catch (e: any) {
    const msg = e?.response?.status === 404 ? '计划已过期' : '拒绝失败，请重试'
    message.error(msg)
  }
}

// ==================== 思考过程展开/收起 ====================

function toggleThinking(msgId: string) {
  if (expandedThinking.value.has(msgId)) {
    expandedThinking.value.delete(msgId)
  } else {
    expandedThinking.value.add(msgId)
  }
}

// ==================== 追问专家功能 ====================

function openAskExpertModal(expert: Expert, debateSessionId: string) {
  selectedExpert.value = expert
  currentDebateSessionId.value = debateSessionId
  askExpertQuestion.value = ''
  showAskExpertModal.value = true
}

function closeAskExpertModal() {
  showAskExpertModal.value = false
  selectedExpert.value = null
  askExpertQuestion.value = ''
}

function showAskExpertFromPanel() {
  // 从操作面板打开追问弹窗，需要先选择专家
  // 查找最近消息中的专家列表
  const lastMsgWithExperts = [...messages.value].reverse().find(m => m.experts && m.experts.length > 0)
  if (lastMsgWithExperts?.experts && lastMsgWithExperts.experts.length > 0) {
    // 默认选择第一个专家
    selectedExpert.value = lastMsgWithExperts.experts[0]
    askExpertQuestion.value = ''
    showAskExpertModal.value = true
  } else {
    message.warning('暂无可追问的专家')
  }
}

async function sendAskExpert() {
  if (!selectedExpert.value || !askExpertQuestion.value.trim() || !currentDebateSessionId.value) {
    return
  }

  const question = askExpertQuestion.value.trim()
  const expert = selectedExpert.value
  const debateSessionId = currentDebateSessionId.value

  // 关闭弹窗
  showAskExpertModal.value = false
  askExpertLoading.value = true

  // 添加用户追问消息
  const userMsgId = `user_ask_${Date.now()}`
  messages.value.push({
    id: userMsgId,
    role: 'user',
    content: `追问 ${expert.name}：${question}`
  })
  forceScrollToBottom()  // 用户操作，强制滚动

  // 创建助手回复占位
  const assistantMsgId = `assistant_ask_${Date.now()}`
  const assistantMsg: ChatMessage = {
    id: assistantMsgId,
    role: 'assistant',
    content: '',
    isStreaming: true,
    skillDisplayName: `${expert.name} 回答`,
  }
  messages.value.push(assistantMsg)
  forceScrollToBottom()  // 用户操作，强制滚动

  try {
    const reader = await createStreamRequest(
      getAskExpertStreamUrl(debateSessionId),
      {
        expert_id: expert.id,
        question: question
      },
      userStore.token
    )

    await parseSSEStream(reader, {
      onEvent: (event, data) => {
        handleAskExpertEvent(event, data, assistantMsgId)
      }
    })
  } catch (error: unknown) {
    console.error('追问专家失败:', error)
    const msgIndex = messages.value.findIndex(m => m.id === assistantMsgId)
    if (msgIndex !== -1 && messages.value[msgIndex]) {
      messages.value[msgIndex]!.content = '追问失败，请稍后重试。'
      messages.value[msgIndex]!.isStreaming = false
    }
    const errorMsg = error instanceof Error ? error.message : '请求失败'
    message.error(errorMsg)
  } finally {
    askExpertLoading.value = false
    askExpertQuestion.value = ''
    selectedExpert.value = null
    scrollToBottom()
  }
}

function handleAskExpertEvent(event: string, data: Record<string, unknown>, msgId: string) {
  const msgIndex = messages.value.findIndex(m => m.id === msgId)
  if (msgIndex === -1) return
  const msg = messages.value[msgIndex]!

  switch (event) {
    case 'status':
      // 状态更新，可选处理
      break

    case 'speech_chunk':
      // 流式内容片段
      if (data.chunk) {
        msg.content += data.chunk as string
        scrollToBottom()
      }
      break

    case 'speech':
      // 完整发言（备用）
      if (data.content && !msg.content) {
        msg.content = data.content as string
      }
      msg.isStreaming = false
      break

    case 'error':
      msg.content = (data.message as string) || '发生错误'
      msg.isStreaming = false
      message.error((data.message as string) || '追问失败')
      break

    default:
      // 检查是否完成
      if (!event && msg.isStreaming) {
        msg.isStreaming = false
      }
  }

  // 强制触发响应式更新
  messages.value = [...messages.value]
}

// ==================== 模式选择处理 ====================

async function handleModeConfirm(mode: string) {
  console.log('[ModeConfirm] 触发', { mode, hasPending: !!pendingModeSelection.value })

  if (!pendingModeSelection.value) {
    console.warn('[ModeConfirm] pendingModeSelection 为空，退出')
    return
  }

  const topic = pendingModeSelection.value.topic
  pendingModeSelection.value = null

  // 移除模式选择卡片消息（保留原始用户消息）
  messages.value = messages.value.filter(m => !m.pendingModeSelection)

  // 直接发起 API 请求（不再通过 sendMessage，避免重复 push 用户消息）
  discussionMode.value = mode
  loading.value = true

  const assistantMsgId = `assistant_${Date.now()}`
  messages.value.push({
    id: assistantMsgId,
    role: 'assistant',
    content: '',
    isStreaming: true,
    thinkingSteps: [],
    toolCalls: [],
  })
  forceScrollToBottom()

  try {
    const baseURL = import.meta.env.VITE_API_BASE_URL || ''
    const postUrl = `${baseURL}/api/user/super-agent/chat/stream?async=true`
    const postRes = await fetch(postUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${userStore.token}`,
      },
      body: JSON.stringify({
        message: topic,
        session_id: currentSessionId.value || undefined,
        force_skill: '__debate__',
        discussion_mode: mode,
      }),
    })

    if (!postRes.ok) {
      throw new Error(await postRes.text() || `请求失败: ${postRes.status}`)
    }

    const { session_id } = await postRes.json()
    currentSessionId.value = session_id
    if (route.params.sessionId !== session_id) {
      router.replace({ name: 'SuperAgent', params: { sessionId: session_id } })
    }

    clearSkill()
    await connectEventSource(session_id, assistantMsgId)
  } catch (error: unknown) {
    console.error('[ModeConfirm] 请求失败:', error)
    const msg = messages.value.find(m => m.id === assistantMsgId)
    if (msg) {
      msg.isStreaming = false
      msg.content = `请求失败: ${error instanceof Error ? error.message : '未知错误'}`
    }
  } finally {
    loading.value = false
    discussionMode.value = 'debate'
  }
}

// ==================== 辩论轮次控制 ====================

async function continueDebate() {
  if (!currentDebateSessionId.value || isDebateProcessing.value) {
    return
  }

  isDebateProcessing.value = true
  roundHint.value = null

  // 添加一条系统消息表示继续
  const systemMsgId = `system_continue_${Date.now()}`
  messages.value.push({
    id: systemMsgId,
    role: 'assistant',
    content: '',
    isStreaming: true
  })
  forceScrollToBottom()  // 用户点击继续，强制滚动

  try {
    const baseURL = import.meta.env.VITE_API_BASE_URL || ''
    const response = await fetch(`${baseURL}/api/user/debate/${currentDebateSessionId.value}/continue`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${userStore.token}`
      }
    })

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }

    const reader = response.body?.getReader()
    if (!reader) throw new Error('无法获取响应流')

    await parseSSEStream(reader, {
      onEvent: (event, data) => {
        handleDebateControlEvent(event, data, systemMsgId)
      }
    })
  } catch (error: unknown) {
    console.error('继续辩论失败:', error)
    const msgIndex = messages.value.findIndex(m => m.id === systemMsgId)
    if (msgIndex !== -1 && messages.value[msgIndex]) {
      messages.value[msgIndex]!.content = '继续辩论失败，请稍后重试。'
      messages.value[msgIndex]!.isStreaming = false
    }
    const errorMsg = error instanceof Error ? error.message : '请求失败'
    message.error(errorMsg)
  } finally {
    isDebateProcessing.value = false
    scrollToBottom()
  }
}

async function endDebate() {
  if (!currentDebateSessionId.value || isDebateProcessing.value) {
    return
  }

  isDebateProcessing.value = true
  roundHint.value = null

  // 添加一条系统消息
  const systemMsgId = `system_end_${Date.now()}`
  messages.value.push({
    id: systemMsgId,
    role: 'assistant',
    content: '',
    isStreaming: true
  })
  forceScrollToBottom()  // 用户点击结束，强制滚动

  try {
    const baseURL = import.meta.env.VITE_API_BASE_URL || ''
    const response = await fetch(`${baseURL}/api/user/debate/${currentDebateSessionId.value}/end`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${userStore.token}`
      }
    })

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }

    const reader = response.body?.getReader()
    if (!reader) throw new Error('无法获取响应流')

    await parseSSEStream(reader, {
      onEvent: (event, data) => {
        handleDebateControlEvent(event, data, systemMsgId)
      }
    })
  } catch (error: unknown) {
    console.error('结束辩论失败:', error)
    const msgIndex = messages.value.findIndex(m => m.id === systemMsgId)
    if (msgIndex !== -1 && messages.value[msgIndex]) {
      messages.value[msgIndex]!.content = '结束辩论失败，请稍后重试。'
      messages.value[msgIndex]!.isStreaming = false
    }
    const errorMsg = error instanceof Error ? error.message : '请求失败'
    message.error(errorMsg)
  } finally {
    isDebateProcessing.value = false
    scrollToBottom()
  }
}

function handleDebateControlEvent(event: string, data: Record<string, unknown>, msgId: string) {
  const msgIndex = messages.value.findIndex(m => m.id === msgId)
  if (msgIndex === -1) return
  const msg = messages.value[msgIndex]!

  switch (event) {
    case 'round_start':
      currentDebateRound.value = data.round as number
      debateStatus.value = 'running'
      break

    case 'content':
      // 统一的内容事件（包含格式化的专家名称、轮次标题等）
      if (data.text) {
        msg.content += data.text as string
        scrollToBottom()
      }
      break

    case 'speech_chunk':
      // 兼容旧格式
      if (data.chunk) {
        msg.content += data.chunk as string
        scrollToBottom()
      }
      break

    // 🆕 思考模型支持：reasoning_chunk 和 reasoning_done 事件
    case 'reasoning_chunk': {
      // 兼容辩论模式（有 expert_id）和普通对话模式（无 expert_id，使用 __model__）
      const rcExpertId = (data.expert_id as string) || '__model__'
      const rcExpertName = (data.expert_name as string) || '思考过程'
      const rcChunk = data.chunk as string
      const rcPhase = data.phase as string | undefined
      const rcRound = data.round as number | undefined

      if (rcChunk) {
        if (!msg.reasoningBlocks) {
          msg.reasoningBlocks = []
        }

        let block = msg.reasoningBlocks.find(b => b.expertId === rcExpertId)
        if (!block) {
          block = {
            expertId: rcExpertId,
            expertName: rcExpertName,
            reasoning: '',
            isCollapsed: false,
            phase: rcPhase,
            round: rcRound,
          }
          msg.reasoningBlocks.push(block)
        }

        block.reasoning += rcChunk
        currentExpertReasoning.value = { expertId: rcExpertId, expertName: rcExpertName, reasoning: block.reasoning }
        scrollToBottom()
      }
      break
    }

    case 'reasoning_done': {
      const rdExpertId = (data.expert_id as string) || '__model__'
      const rdReasoning = data.reasoning as string
      expertReasonings.value.set(rdExpertId, rdReasoning)

      if (msg.reasoningBlocks) {
        const block = msg.reasoningBlocks.find(b => b.expertId === rdExpertId)
        if (block) {
          block.isCollapsed = true
        }
      }

      currentExpertReasoning.value = null
      scrollToBottom()
      break
    }

    case 'round_end':
      currentDebateRound.value = data.round as number
      debateStatus.value = 'paused'
      canContinueDebate.value = data.can_continue as boolean
      roundHint.value = data.hint as typeof roundHint.value
      msg.isStreaming = false
      // 轮次结束时隐藏思考面板
      scrollToBottom()
      break

    case 'final_summary':
      debateStatus.value = 'ended'
      roundHint.value = null
      msg.isStreaming = false
      break

    case 'status':
      if (data.status === 'completed') {
        debateStatus.value = 'ended'
        roundHint.value = null
        msg.isStreaming = false
      }
      break

    case 'error':
      msg.content = (data.message as string) || '发生错误'
      msg.isStreaming = false
      message.error((data.message as string) || '操作失败')
      break
  }

  messages.value = [...messages.value]
}

// ==================== 长期记忆管理 ====================

// 过滤后的记忆列表
const filteredMemoryList = computed(() => {
  let list = memoryList.value
  // 分类过滤
  if (memoryFilterCategory.value !== 'all') {
    list = list.filter(m => m.category === memoryFilterCategory.value)
  }
  // 搜索过滤
  const kw = memorySearchKeyword.value.trim().toLowerCase()
  if (kw) {
    list = list.filter(m =>
      (m.memory || '').toLowerCase().includes(kw) ||
      (m.abstract || '').toLowerCase().includes(kw) ||
      (m.content || '').toLowerCase().includes(kw)
    )
  }
  return list
})

// 各分类计数
const memoryCategoryCounts = computed(() => {
  const counts: Record<string, number> = { all: memoryList.value.length }
  for (const item of memoryList.value) {
    const cat = item.category || 'unknown'
    counts[cat] = (counts[cat] || 0) + 1
  }
  return counts
})

async function loadMemories() {
  memoryLoading.value = true
  try {
    const res = await getUserMemories()
    memoryList.value = (res as any)?.memories || []
  } catch (e) {
    console.error('加载记忆失败:', e)
    memoryList.value = []
  } finally {
    memoryLoading.value = false
  }
}

async function handleDeleteMemory(memoryId: string) {
  try {
    await apiDeleteMemory(memoryId)
    memoryList.value = memoryList.value.filter(m => m.id !== memoryId)
    if (selectedMemory.value?.id === memoryId) selectedMemory.value = null
    message.success('已删除')
  } catch (e) {
    message.error('删除失败')
  }
}

async function handleClearMemories() {
  try {
    await clearAllMemories()
    memoryList.value = []
    selectedMemory.value = null
    message.success('已清空所有记忆')
  } catch (e) {
    message.error('清空失败')
  }
}

// 打开弹窗时自动加载 + 重置状态
watch(showMemoryModal, (val) => {
  if (val) {
    memoryFilterCategory.value = 'all'
    memorySearchKeyword.value = ''
    selectedMemory.value = null
    loadMemories()
  }
})

// ==================== 导出功能 ====================

async function exportChat(format: 'markdown' | 'txt' = 'markdown') {
  if (!currentSessionId.value) {
    message.warning('请先选择一个对话')
    return
  }

  try {
    const blob = await exportSuperAgentSession(currentSessionId.value, format)
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `超级智能体对话_${currentSessionId.value}.${format === 'markdown' ? 'md' : 'txt'}`
    document.body.appendChild(a)
    a.click()
    window.URL.revokeObjectURL(url)
    document.body.removeChild(a)

    message.success('导出成功')
  } catch (error: unknown) {
    console.error('导出失败:', error)
    const errorMsg = error instanceof Error ? error.message : '导出失败'
    message.error(errorMsg)
  }
}

async function exportPdf() {
  if (!currentSessionId.value) {
    message.warning('请先选择一个对话')
    return
  }

  const loadingMsg = message.loading('正在生成 PDF，请稍候...', { duration: 0 })
  try {
    const blob = await exportSuperAgentSession(currentSessionId.value, 'pdf')
    // 触发浏览器下载
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `超级智能体对话_${currentSessionId.value}.pdf`
    document.body.appendChild(a)
    a.click()
    window.URL.revokeObjectURL(url)
    document.body.removeChild(a)
    message.success('PDF 导出成功')
  } catch (error: unknown) {
    console.error('导出PDF失败:', error)
    const errorMsg = error instanceof Error ? error.message : '导出PDF失败'
    message.error(errorMsg)
  } finally {
    loadingMsg.destroy()
  }
}

async function exportDocx(style: 'standard' | 'official' | 'internal' | 'report' = 'standard') {
  if (!currentSessionId.value) {
    message.warning('请先选择一个对话')
    return
  }

  const styleNames: Record<string, string> = {
    standard: '通用格式', official: '行政公文', internal: '内部文件', report: '研究报告',
  }
  const styleName = styleNames[style] || '通用格式'
  const loadingMsg = message.loading(`正在生成 Word 文档（${styleName}），请稍候...`, { duration: 0 })
  try {
    const blob = await exportSuperAgentSession(currentSessionId.value, 'docx', style)
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `超级智能体对话_${currentSessionId.value}.docx`
    document.body.appendChild(a)
    a.click()
    window.URL.revokeObjectURL(url)
    document.body.removeChild(a)
    message.success('Word 导出成功')
  } catch (error: unknown) {
    console.error('导出Word失败:', error)
    const errorMsg = error instanceof Error ? error.message : '导出Word失败'
    message.error(errorMsg)
  } finally {
    loadingMsg.destroy()
  }
}

// ==================== 移动端会话选择 ====================

function handleMobileSessionSelect(session: { session_id: string }) {
  switchSession(session.session_id)
}

// ==================== 生命周期 ====================

onMounted(async () => {
  // 并行加载会话列表、技能列表、默认助手
  const loadDefault = async () => {
    try {
      const res = await getDefaultAgent() as any
      defaultAgentId.value = res?.agent_id ?? null
    } catch {
      // 静默失败
    }
  }
  await Promise.all([loadSessions(), loadSkills(), loadDefault()])

  const queryText = route.query.q as string | undefined
  if (queryText) {
    inputText.value = queryText
    router.replace({ path: route.path, query: {} })
  }

  // 从 Agent 广场跳转：agent_id + new_session=true
  const queryAgentId = route.query.agent_id as string | undefined
  const queryNewSession = route.query.new_session === 'true'
  if (queryAgentId && queryNewSession) {
    router.replace({ path: route.path, query: {} })
    try {
      const agent = await getAgent(Number(queryAgentId))
      currentAgent.value = agent as any
      handleNewChat()
    } catch (e) {
      console.error('加载助手失败', e)
    }
    // 检测是否有文档在处理中
    checkProcessingDocs()
    processingDocTimer = setInterval(checkProcessingDocs, 15000)
    return
  }

  // 优先级：URL Session > Default Agent > Generic
  const urlSessionId = route.params.sessionId as string | undefined
  if (urlSessionId) {
    // URL 携带 sessionId 时自动加载对应会话
    switchSession(urlSessionId)
  } else if (defaultAgentId.value && !currentAgent.value) {
    // 无 URL session 时，自动恢复默认助手
    try {
      const agent = await getAgent(defaultAgentId.value) as any
      currentAgent.value = agent
    } catch {
      // Agent 可能已被删除/禁用，静默忽略并清除无效的 defaultAgentId
      defaultAgentId.value = null
    }
  }
  // 检测是否有文档在处理中
  checkProcessingDocs()
  processingDocTimer = setInterval(checkProcessingDocs, 15000) // 每15秒检查
})

onUnmounted(() => {
  // 清理滚动 RAF
  if (scrollRafId) {
    cancelAnimationFrame(scrollRafId)
    scrollRafId = null
  }
  // 清理文档处理检测定时器
  if (processingDocTimer) {
    clearInterval(processingDocTimer)
    processingDocTimer = null
  }
})

// 🆕 移除了 watch(messages) 的自动滚动，改为在流式事件中智能控制
// 只有用户没有主动向上滚动时才自动滚动

// Artifact 预览卡片流式自动滚动
watch(
  () => artifactStore.streamingContent,
  () => {
    if (!artifactStore._isBackgroundStreaming || !streamingPreviewRef.value) return
    nextTick(() => {
      const el = streamingPreviewRef.value
      if (el) el.scrollTop = el.scrollHeight
    })
  },
)
// 流式结束后回到顶部
watch(
  () => artifactStore._isBackgroundStreaming,
  (val, oldVal) => {
    if (oldVal && !val && streamingPreviewRef.value) {
      nextTick(() => {
        if (streamingPreviewRef.value) {
          streamingPreviewRef.value.scrollTop = 0
        }
        streamingPreviewRef.value = null
      })
    }
  },
)
</script>

<template>
  <div class="super-agent-page" :class="{ 'super-agent-page-mobile': isMobile }">
    <!-- ===== PC端布局 ===== -->
    <n-layout v-if="!isMobile" has-sider style="height: 100%">
      <!-- 左侧会话列表 -->
      <n-layout-sider
        bordered
        :collapsed="siderCollapsed"
        collapse-mode="width"
        :collapsed-width="0"
        :width="260"
        show-trigger="bar"
        @collapse="siderCollapsed = true"
        @expand="siderCollapsed = false"
        style="background: #fff"
      >
        <ConversationList
          :sessions="sessions"
          :sessions-loading="sessionsLoading"
          :current-session-id="currentSessionId"
          :skills="displaySkills"
          :current-skill="currentSkill"
          :current-agent="currentAgent"
          :default-agent-id="defaultAgentId"
          @new-chat="handleNewChat"
          @switch-session="switchSession"
          @delete-session="handleDeleteSession"
          @manage-agents="showAgentSelector = true"
          @set-default="handleSetDefault"
          @clear-default="handleClearDefault"
        />
      </n-layout-sider>

      <!-- 右侧聊天区域 + Artifact 面板 -->
      <n-layout-content style="background: #f5f7f9">
        <div class="content-split">
        <div class="chat-main">
          <!-- 顶部状态栏（参考图2设计） -->
          <div class="status-header" :class="{ 'collapsed': isHeaderCollapsed }">
            <div class="status-left">
              <div class="agent-avatar" :class="{ 'agent-avatar-emoji': agentDisplay }">
                <template v-if="agentDisplay">{{ agentDisplay.avatar }}</template>
                <n-icon v-else :component="FlashOutline" :size="24" />
              </div>
              <div class="agent-info">
                <div class="agent-title">
                  <span class="status-title">{{ agentDisplay?.name || '超级智能体' }}</span>
                  <n-tag v-if="currentIntent" size="small" type="info" style="margin-left: 8px;">
                    {{ currentIntent === 'skill' ? `技能: ${currentSkill}` :
                       currentIntent === 'debate' ? '多专家辩论' : '问答' }}
                  </n-tag>
                </div>
                <div class="agent-description header-expandable">{{ agentDisplay?.description || '综合科研能力，支持文献检索、多专家辩论、智能写作' }}</div>
              </div>
            </div>
            <div class="status-right">
              <!-- 🆕 视图模式切换按钮组（方案C） -->
              <n-button-group size="small" class="view-mode-group">
                <n-tooltip trigger="hover" placement="bottom">
                  <template #trigger>
                    <n-button
                      :type="viewMode === 'simple' ? 'primary' : 'default'"
                      @click="setViewMode('simple')"
                      :ghost="viewMode !== 'simple'"
                    >
                      简洁
                    </n-button>
                  </template>
                  仅显示阶段进度和状态文字
                </n-tooltip>
                <n-tooltip trigger="hover" placement="bottom">
                  <template #trigger>
                    <n-button
                      :type="viewMode === 'professional' ? 'primary' : 'default'"
                      @click="setViewMode('professional')"
                      :ghost="viewMode !== 'professional'"
                    >
                      专业
                    </n-button>
                  </template>
                  显示思考摘要和工具调用
                </n-tooltip>
                <n-tooltip trigger="hover" placement="bottom">
                  <template #trigger>
                    <n-button
                      :type="viewMode === 'debug' ? 'primary' : 'default'"
                      @click="setViewMode('debug')"
                      :ghost="viewMode !== 'debug'"
                    >
                      调试
                    </n-button>
                  </template>
                  显示完整思考和JSON日志
                </n-tooltip>
              </n-button-group>

              <n-button size="small" quaternary @click="openMemoryModal()">
                <template #icon>
                  <n-icon :component="BulbOutline" />
                </template>
                记忆管理
              </n-button>

              <n-tag v-if="isSkillExecuting" type="warning" size="small">
                <template #icon>
                  <n-icon :component="TimeOutline" />
                </template>
                执行中...
              </n-tag>
              <n-tag v-else-if="isGeneratingAnswer" type="info" size="small">
                <template #icon>
                  <n-icon :component="BulbOutline" />
                </template>
                生成答案中...
              </n-tag>
              <n-tag v-else-if="skillExecutionTime > 0" type="success" size="small">
                <template #icon>
                  <n-icon :component="CheckmarkCircleOutline" />
                </template>
                {{ (skillExecutionTime / 1000).toFixed(1) }}s
              </n-tag>
              <!-- 折叠/展开按钮：有消息时显示 -->
              <n-button
                v-if="hasUserMessages"
                size="small"
                quaternary
                class="header-toggle-btn"
                @click="isHeaderCollapsed = !isHeaderCollapsed"
              >
                {{ isHeaderCollapsed ? '▸ 查看简介' : '▴ 收起' }}
              </n-button>
            </div>
          </div>

          <!-- 顶部工具栏 -->
          <div v-if="currentSessionId && messages.length > 0" class="chat-toolbar">
            <span class="toolbar-title">{{ currentSessionTitle }}</span>
            <div class="toolbar-actions">
              <n-button size="small" quaternary @click="exportChat('markdown')">
                <template #icon>
                  <n-icon :component="DownloadOutline" />
                </template>
                导出 MD
              </n-button>
              <n-button size="small" quaternary @click="exportChat('txt')">
                <template #icon>
                  <n-icon :component="DownloadOutline" />
                </template>
                导出 TXT
              </n-button>
              <n-button size="small" quaternary @click="exportPdf()">
                <template #icon>
                  <n-icon :component="DownloadOutline" />
                </template>
                导出 PDF
              </n-button>
              <n-dropdown
                :options="[
                  { label: '通用格式', key: 'standard' },
                  { label: '行政公文（GB/T 9704）', key: 'official' },
                  { label: '内部文件', key: 'internal' },
                  { label: '研究报告', key: 'report' },
                ]"
                @select="(key: string) => exportDocx(key as 'standard' | 'official' | 'internal' | 'report')"
              >
                <n-button size="small" quaternary>
                  <template #icon>
                    <n-icon :component="DownloadOutline" />
                  </template>
                  导出 Word
                </n-button>
              </n-dropdown>
            </div>
          </div>

          <!-- 消息区域 -->
          <div
            ref="messagesContainer"
            class="messages-container"
            :style="{ paddingBottom: debateStatus === 'paused' && roundHint ? '180px' : '24px' }"
            @scroll="handleScroll"
          >
            <!-- 文档处理中提示（非侵入式） -->
            <div v-if="processingDocNames.length > 0" class="processing-doc-banner">
              <span style="margin-right: 6px">&#x2139;&#xFE0F;</span>
              正在为您准备文档「{{ processingDocNames.join('、') }}」，完成后可立即提问
            </div>
            <!-- 欢迎信息 -->
            <div v-if="messages.length === 0" class="welcome-section">
              <!-- 选中了特定助手 → 展示助手信息 -->
              <template v-if="agentDisplay">
                <div class="welcome-icon agent-welcome-avatar">{{ agentDisplay.avatar }}</div>
                <h2>{{ agentDisplay.name }}</h2>
                <p v-if="shouldShowDescription">{{ agentDisplay.description }}</p>
                <div v-if="agentDisplay.welcomeMessage && agentDisplay.welcomeMessage !== agentDisplay.description"
                     class="agent-welcome-message"
                     v-html="renderWelcomeMessage(agentDisplay.welcomeMessage)">
                </div>
              </template>
              <!-- 默认通用助手 → 保持现有设计不变 -->
              <template v-else>
                <div class="welcome-icon">
                  <n-icon :component="FlashOutline" size="64" color="#2080f0" />
                </div>
                <h2>超级智能体</h2>
                <p>我可以执行多种技能来帮助您完成科研任务</p>
              </template>
              <div v-if="!agentDisplay" class="feature-tags">
                <n-tag type="warning" round>
                  <template #icon>
                    <n-icon :component="BulbOutline" />
                  </template>
                  智能问答
                </n-tag>
                <n-tag type="info" round>
                  <template #icon>
                    <n-icon :component="SearchOutline" />
                  </template>
                  联网搜索
                </n-tag>
                <n-tag type="success" round>
                  <template #icon>
                    <n-icon :component="GlobeOutline" />
                  </template>
                  学术搜索
                </n-tag>
                <n-tag type="error" round>
                  <template #icon>
                    <n-icon :component="DocumentTextOutline" />
                  </template>
                  文件生成
                </n-tag>
                <n-tag round style="background: #f0e6ff; color: #7c3aed;">
                  <template #icon>
                    <n-icon :component="ConstructOutline" />
                  </template>
                  计划模式
                </n-tag>
                <n-tag round style="background: #fef3c7; color: #d97706;">
                  <template #icon>
                    <n-icon :component="PeopleOutline" />
                  </template>
                  多专家辩论
                </n-tag>
                <n-tag round style="background: #d1fae5; color: #059669;">
                  <template #icon>
                    <n-icon :component="BuildOutline" />
                  </template>
                  公文写作
                </n-tag>
                <n-tag round style="background: #ede9fe; color: #6d28d9;">
                  <template #icon>
                    <n-icon :component="ColorWandOutline" />
                  </template>
                  研究选题
                </n-tag>
                <n-tag round style="background: #e0f2fe; color: #0369a1;">
                  <template #icon>
                    <n-icon :component="GitBranchOutline" />
                  </template>
                  并行调研
                </n-tag>
                <n-tag round style="background: #fee2e2; color: #b91c1c;">
                  <template #icon>
                    <n-icon :component="AnalyticsOutline" />
                  </template>
                  政策分析
                </n-tag>
                <n-tag round style="background: #ecfdf5; color: #047857;">
                  <template #icon>
                    <n-icon :component="SchoolOutline" />
                  </template>
                  学习规划
                </n-tag>
              </div>
              <div class="example-questions">
                <n-tag
                  v-for="example in displayExampleQuestions"
                  :key="example"
                  round
                  :bordered="false"
                  type="default"
                  class="example-tag"
                  @click="sendExample(example)"
                >
                  {{ example }}
                </n-tag>
              </div>
            </div>

            <!-- 消息列表 -->
            <template v-for="msg in messages" :key="msg.id">
              <MessageItem
                v-if="!(msg.pendingIntentConfirm && !msg.content.trim() && !msg.isStreaming)"
                :msg="msg"
                :view-mode="viewMode"
                :current-skill="currentSkill"
                :is-skill-executing="isSkillExecuting"
                :latest-event-type="latestEventType"
                :latest-event-data="latestEventData"
                :connection-health="connectionHealth"
                :expanded-thinking="expandedThinking"
                :streaming-expert-id="currentExpertReasoning?.expertId"
                :available-skills="availableSkillOptions"
                :is-global-streaming="loading"
                :messages-after-count="messages.length - messages.indexOf(msg) - 1"
                :quick-replies="msg === messages[messages.length - 1] && msg.role === 'assistant' ? displaySuggestions : undefined"
                @toggle-thinking="toggleThinking"
                @open-ask-expert="openAskExpertModal"
                @mode-confirm="handleModeConfirm"
                @citation-click="handleCitationClickFromChild"
                @open-reference-drawer="(refs) => { currentReferences = refs; showReferenceDrawer = true }"
                @open-reference-modal="(refs) => { currentReferences = refs; showReferenceModal = true }"
                @open-reference-link="openReferenceLink"
                @preview-file="handlePreviewFile"
                @download-file="handleToolFileDownload"
                @convert-to-pdf="handleConvertToPdf"
                @stop-session="stopGeneration"
                @quick-reply="handleQuickReply"
                @retry-question="handleRegenerate(msg.messageId || '')"
                @regenerate="handleRegenerate"
                @edit-message="handleEditMessage"
                @switch-version="handleSwitchVersion"
              >
                <template #plan-panel v-if="msg.executionPlan">
                  <PlanPanel
                    :plan="msg.executionPlan"
                    @approve="handlePlanApprove"
                    @reject="handlePlanReject"
                  />
                  <PlanQuestion
                    v-if="msg.pendingPlanQuestion"
                    :tool-use-id="msg.pendingPlanQuestion.toolUseId"
                    :question="msg.pendingPlanQuestion.question"
                    :options="msg.pendingPlanQuestion.options"
                    :context="msg.pendingPlanQuestion.context"
                    :step-title="msg.pendingPlanQuestion.stepTitle"
                    @answered="msg.pendingPlanQuestion = undefined"
                  />
                </template>
                <template #client-tool v-if="msg.pendingClientTool">
                  <ClientToolInline
                    :request="msg.pendingClientTool"
                    @answered="msg.pendingClientTool = undefined"
                  />
                </template>
                <template #artifact-card v-if="msg.artifactId && msg.role === 'assistant'">
                  <div
                    class="artifact-preview-card"
                    :class="{ 'is-streaming': artifactStore._isBackgroundStreaming && artifactStore.activeArtifactId === msg.artifactId }"
                    @click="!artifactStore._isBackgroundStreaming && artifactStore.openPanel(msg.artifactId, msg.references)"
                  >
                    <div class="preview-card-header">
                      <div class="preview-card-title-row">
                        <span class="preview-card-icon">{{ isSlideArtifact(msg.artifactId) ? '📊' : '📄' }}</span>
                        <span class="preview-card-title">{{ getArtifactTitle(msg.artifactId) }}</span>
                        <span
                          v-if="isSlideArtifact(msg.artifactId) && getSlideCount(msg.artifactId) > 0"
                          class="preview-card-meta"
                        >· {{ getSlideCount(msg.artifactId) }} 页</span>
                        <span v-if="artifactStore._isBackgroundStreaming && artifactStore.activeArtifactId === msg.artifactId" class="preview-card-streaming-badge">生成中...</span>
                      </div>
                      <!-- 非 slides 才显示这些按钮（Word/复制 markdown 对 PPT 无意义，Phase C 换成 PPTX 导出） -->
                      <div v-if="!isSlideArtifact(msg.artifactId) && (!artifactStore._isBackgroundStreaming || artifactStore.activeArtifactId !== msg.artifactId)" class="preview-card-actions" @click.stop>
                        <button class="pca-btn" @click="handleArtifactCopy(msg.artifactId)" title="复制">复制</button>
                        <button class="pca-btn" @click="artifactStore.openPanel(msg.artifactId, msg.references)" title="编辑">编辑</button>
                        <button class="pca-btn" @click="handleArtifactExport(msg.artifactId, 'docx')" title="下载">下载</button>
                      </div>
                    </div>
                    <!-- slides 不渲染 preview body（JSON 内容当 markdown 展示会露出源码） -->
                    <div
                      v-if="!isSlideArtifact(msg.artifactId)"
                      class="preview-card-body"
                      :class="{ 'is-streaming': artifactStore._isBackgroundStreaming && artifactStore.activeArtifactId === msg.artifactId }"
                      :ref="(el: any) => { if (el && artifactStore._isBackgroundStreaming && artifactStore.activeArtifactId === msg.artifactId) streamingPreviewRef = el as HTMLElement }"
                      v-html="getArtifactStreamingPreviewHtml(msg.artifactId, msg.references)"
                    ></div>
                  </div>
                </template>
              </MessageItem>
              <!-- P1b: 低置信度意图确认卡片 -->
              <div v-if="msg.pendingIntentConfirm" class="intent-confirm-card">
                <div class="intent-confirm-icon">🤔</div>
                <div class="intent-confirm-body">
                  <div v-if="msg.pendingIntentConfirm.clarifyHint" class="intent-confirm-text" v-html="msg.pendingIntentConfirm.clarifyHint"></div>
                  <div v-else class="intent-confirm-text">
                    我理解你想使用「{{ msg.pendingIntentConfirm.skillDisplayName }}」
                    <span class="intent-confirm-confidence">({{ Math.round(msg.pendingIntentConfirm.confidence * 100) }}%)</span>
                    ，对吗？
                  </div>
                  <div v-if="msg.pendingIntentConfirm.alternatives?.length" class="intent-confirm-alternatives">
                    <span v-if="!msg.pendingIntentConfirm.clarifyHint" class="intent-confirm-alt-label">或者：</span>
                    <n-button
                      v-for="alt in msg.pendingIntentConfirm.alternatives"
                      :key="alt.skillName"
                      size="tiny"
                      quaternary
                      @click="handleIntentConfirm(alt.skillName)"
                    >
                      {{ alt.skillDisplayName }}
                      <span class="intent-confirm-confidence">({{ Math.round(alt.confidence * 100) }}%)</span>
                    </n-button>
                  </div>
                  <div class="intent-confirm-actions">
                    <n-button size="small" type="primary" @click="handleIntentConfirm(msg.pendingIntentConfirm!.skillName)">
                      确认使用「{{ msg.pendingIntentConfirm.skillDisplayName }}」
                    </n-button>
                    <n-button size="small" secondary @click="handleIntentConfirm(FORCE_SKILL_QA)">
                      直接问答
                    </n-button>
                    <n-button size="small" quaternary @click="handleIntentCancel">
                      取消
                    </n-button>
                  </div>
                </div>
              </div>
              <!-- Skill 推荐链卡片 -->
              <div v-if="msg.nextSkillSuggestions?.length && !msg.isStreaming" class="next-skill-suggestions">
                <div class="next-skill-suggestions-label">推荐后续操作：</div>
                <div class="next-skill-suggestions-list">
                  <n-button
                    v-for="suggestion in msg.nextSkillSuggestions"
                    :key="suggestion.skill"
                    size="small"
                    secondary
                    @click="handleNextSkillSuggestion(suggestion.skill, suggestion.prompt)"
                  >
                    {{ suggestion.display || suggestion.prompt }}
                  </n-button>
                </div>
              </div>
            </template>
            <ErrorState
              v-if="showErrorState"
              :error-type="errorType"
              :message="errorMessage"
              :data-sources="dataSources"
              :search-query="inputText"
              :retrying="isRetrying"
              @retry="handleRetry"
              @close="handleErrorClose"
              class="error-state-wrapper"
            />
            <!-- 滚动锚点：浏览器原生 overflow-anchor 自动保持滚动位置 -->
            <div class="scroll-anchor" />
          </div>

          <!-- 🆕 Debug 模式：ExecutionTimeline + DebugPanel -->
          <template v-if="viewMode === 'debug' && debugCapture.events.value.length > 0">
            <ExecutionTimeline
              :events="debugCapture.events.value"
              class="debug-timeline-wrapper"
            />
            <DebugPanel
              :events="debugCapture.events.value"
              :stats="debugCapture.stats.value"
              :on-export="debugCapture.exportLog"
              class="debug-panel-wrapper"
            />
          </template>

          <!-- 底部输入区（停止按钮 + 输入框，停止按钮绝对定位不占文档流） -->
          <div class="input-area-wrapper">
            <!-- 查询改写提示条（Perplexica 迁移） -->
            <div v-if="queryRewriteInfo" class="query-rewrite-bar">
              <div class="query-rewrite-summary" @click="queryRewriteBarExpanded = !queryRewriteBarExpanded">
                <span class="rewrite-icon">↺</span>
                <span class="rewrite-label">查询已优化</span>
                <span class="rewrite-toggle">{{ queryRewriteBarExpanded ? '▾' : '▸' }}</span>
                <button
                  class="rewrite-retry-btn"
                  @click.stop="inputText = queryRewriteInfo!.original; sendMessage()"
                >使用原始查询重试</button>
              </div>
              <div v-if="queryRewriteBarExpanded" class="query-rewrite-detail">
                <div class="rewrite-row">
                  <span class="rewrite-row-label">原始：</span>
                  <span class="rewrite-row-value">{{ queryRewriteInfo.original }}</span>
                </div>
                <div class="rewrite-row">
                  <span class="rewrite-row-label">改写：</span>
                  <span class="rewrite-row-value">{{ queryRewriteInfo.rewritten }}</span>
                </div>
              </div>
            </div>

            <!-- 输入区域（v5.0: 停止按钮已内嵌到输入框） -->
            <ChatInputArea
            :input-text="inputText"
            :loading="loading"
            :disabled="hasPendingPlan"
            :disabled-placeholder="hasPendingPlan ? '请先审批调研计划后再继续对话' : undefined"
            :selected-skill="selectedSkill"
            :show-mention-dropdown="showMentionDropdown"
            :mention-filtered-list="mentionFilteredList"
            :mention-dropdown-index="mentionDropdownIndex"
            :mention-anchor-rect="mentionAnchorRect"
            :chat-mode="chatMode"
            :chat-mode-disabled="hasPendingPlan || loading"
            :active-artifact="activeArtifactInfo"
            :attachments="attachments"
            :is-dragging="attachmentDragging"
            :has-uploading="attachmentUploading"
            :quick-replies="displaySuggestions"
            @update:input-text="inputText = $event"
            @send="sendMessage"
            @stop="stopGeneration"
            @clear-skill="clearSkill"
            @clear-artifact="artifactStore.closePanel()"
            @mention-input="onPcMentionInput"
            @mention-keydown="onPcInputKeydown"
            @mention-select="onMentionSelect"
            @mention-hover="mentionDropdownIndex = $event"
            @update:chat-mode="chatMode = $event"
            @add-files="attachmentAddFiles"
            @import-kb-docs="handleImportKBDocs"
            @remove-attachment="removeAttachment"
            @paste="handleAttachmentPaste($event)"
            @drag-enter="attachmentDragEnter"
            @drag-leave="attachmentDragLeave"
            @drag-over="attachmentDragOver"
            @drop="attachmentDrop"
            @quick-reply="handleQuickReply"
          />
          </div>
        </div>
        </div>
        <!-- Artifact 面板（overlay 模式）：只在展开态挂载。
             收起态入口在气泡内联卡片（每条 assistant 消息一张） -->
        <ArtifactPanel v-if="artifactStore.panelVisible" />
      </n-layout-content>
    </n-layout>

    <!-- ===== 移动端布局 ===== -->
    <template v-else>
      <div class="mobile-chat-container">
        <!-- 移动端顶部栏 -->
        <div class="mobile-header">
          <div class="mobile-header-content">
            <div class="mobile-agent-info">
              <template v-if="agentDisplay">
                <span class="mobile-agent-emoji">{{ agentDisplay.avatar }}</span>
              </template>
              <n-icon v-else :component="FlashOutline" :size="20" color="#2080f0" />
              <span class="mobile-agent-name">{{ agentDisplay?.name || '超级智能体' }}</span>
              <n-tag v-if="currentIntent" size="tiny" type="info">
                {{ currentIntent === 'skill' ? currentSkill :
                   currentIntent === 'debate' ? '辩论' : 'QA' }}
              </n-tag>
            </div>
            <div class="mobile-header-actions">
              <!-- 🆕 移动端视图模式下拉菜单 -->
              <n-dropdown
                trigger="click"
                :options="viewModeOptions"
                @select="(key: string) => setViewMode(key as ViewMode)"
              >
                <n-button quaternary circle class="view-mode-btn-mobile">
                  <template #icon>
                    <n-icon :component="EyeOutline" :size="18" />
                  </template>
                </n-button>
              </n-dropdown>

              <n-button
                v-if="currentSessionId && messages.length > 0"
                quaternary
                circle
                @click="showMoreActions = true"
              >
                <template #icon>
                  <n-icon :component="EllipsisVertical" :size="20" />
                </template>
              </n-button>
              <n-button quaternary circle @click="showSessionDrawer = true">
                <template #icon>
                  <n-icon :component="ListOutline" :size="22" />
                </template>
              </n-button>
            </div>
          </div>
        </div>

        <!-- 消息区域 -->
        <div
          ref="messagesContainer"
          class="mobile-messages-container"
          :style="{ paddingBottom: debateStatus === 'paused' && roundHint ? 'calc(180px + 56px + env(safe-area-inset-bottom, 0px))' : 'calc(60px + 56px + env(safe-area-inset-bottom, 0px))' }"
          @scroll="handleScroll"
        >
          <!-- 文档处理中提示（移动端） -->
          <div v-if="processingDocNames.length > 0" class="processing-doc-banner">
            <span style="margin-right: 6px">&#x2139;&#xFE0F;</span>
            正在为您准备文档「{{ processingDocNames.join('、') }}」，完成后可立即提问
          </div>
          <!-- 欢迎信息 -->
          <div v-if="messages.length === 0" class="mobile-welcome-section">
            <!-- 选中了特定助手 → 展示助手信息 -->
            <template v-if="agentDisplay">
              <div class="welcome-icon agent-welcome-avatar" style="font-size: 48px;">{{ agentDisplay.avatar }}</div>
              <h3>{{ agentDisplay.name }}</h3>
              <p v-if="shouldShowDescription">{{ agentDisplay.description }}</p>
              <div v-if="agentDisplay.welcomeMessage && agentDisplay.welcomeMessage !== agentDisplay.description"
                   class="agent-welcome-message"
                   v-html="renderWelcomeMessage(agentDisplay.welcomeMessage)">
              </div>
            </template>
            <!-- 默认通用助手 → 保持现有设计不变 -->
            <template v-else>
              <div class="welcome-icon">
                <n-icon :component="FlashOutline" size="48" color="#2080f0" />
              </div>
              <h3>超级智能体</h3>
              <p>执行多种技能完成科研任务</p>
              <div class="mobile-feature-tags">
                <n-tag size="small" type="warning">智能问答</n-tag>
                <n-tag size="small" type="info">联网搜索</n-tag>
                <n-tag size="small" type="success">学术搜索</n-tag>
                <n-tag size="small" type="error">文件生成</n-tag>
                <n-tag size="small" style="background: #f0e6ff; color: #7c3aed;">计划模式</n-tag>
                <n-tag size="small" style="background: #fef3c7; color: #d97706;">多专家辩论</n-tag>
                <n-tag size="small" style="background: #d1fae5; color: #059669;">公文写作</n-tag>
                <n-tag size="small" style="background: #ede9fe; color: #6d28d9;">研究选题</n-tag>
                <n-tag size="small" style="background: #e0f2fe; color: #0369a1;">并行调研</n-tag>
                <n-tag size="small" style="background: #fee2e2; color: #b91c1c;">政策分析</n-tag>
                <n-tag size="small" style="background: #ecfdf5; color: #047857;">学习规划</n-tag>
              </div>
            </template>
            <div class="mobile-example-questions">
              <div
                v-for="example in displayExampleQuestions"
                :key="example"
                class="mobile-example-item"
                @click="sendExample(example)"
              >
                {{ example }}
              </div>
            </div>
          </div>

          <!-- 消息列表 -->
          <template v-for="msg in messages" :key="msg.id">
            <MessageItem
              v-if="!(msg.pendingIntentConfirm && !msg.content.trim() && !msg.isStreaming)"
              :msg="msg"
              :view-mode="viewMode"
              :current-skill="currentSkill"
              :is-skill-executing="isSkillExecuting"
              :latest-event-type="latestEventType"
              :latest-event-data="latestEventData"
              :connection-health="connectionHealth"
              :expanded-thinking="expandedThinking"
              :is-mobile="true"
              :streaming-expert-id="currentExpertReasoning?.expertId"
              :available-skills="availableSkillOptions"
              :is-global-streaming="loading"
              :messages-after-count="messages.length - messages.indexOf(msg) - 1"
              :quick-replies="msg === messages[messages.length - 1] && msg.role === 'assistant' ? displaySuggestions : undefined"
              @toggle-thinking="toggleThinking"
              @open-ask-expert="openAskExpertModal"
              @mode-confirm="handleModeConfirm"
              @citation-click="handleCitationClickFromChild"
              @open-reference-drawer="(refs) => { currentReferences = refs; showReferenceDrawer = true }"
              @open-reference-modal="(refs) => { currentReferences = refs; showReferenceModal = true }"
              @open-reference-link="openReferenceLink"
              @preview-file="handlePreviewFile"
              @download-file="handleToolFileDownload"
              @convert-to-pdf="handleConvertToPdf"
              @stop-session="stopGeneration"
              @quick-reply="handleQuickReply"
              @regenerate="handleRegenerate"
              @edit-message="handleEditMessage"
              @switch-version="handleSwitchVersion"
            >
              <template #plan-panel v-if="msg.executionPlan">
                <PlanPanel
                  :plan="msg.executionPlan"
                  @approve="handlePlanApprove"
                  @reject="handlePlanReject"
                />
                <PlanQuestion
                  v-if="msg.pendingPlanQuestion"
                  :tool-use-id="msg.pendingPlanQuestion.toolUseId"
                  :question="msg.pendingPlanQuestion.question"
                  :options="msg.pendingPlanQuestion.options"
                  :context="msg.pendingPlanQuestion.context"
                  :step-title="msg.pendingPlanQuestion.stepTitle"
                  @answered="msg.pendingPlanQuestion = undefined"
                />
              </template>
              <template #client-tool v-if="msg.pendingClientTool">
                <ClientToolInline
                  :request="msg.pendingClientTool"
                  @answered="msg.pendingClientTool = undefined"
                />
              </template>
              <template #artifact-card v-if="msg.artifactId && msg.role === 'assistant'">
                <div
                  class="artifact-preview-card"
                  :class="{ 'is-streaming': artifactStore._isBackgroundStreaming && artifactStore.activeArtifactId === msg.artifactId }"
                  @click="!artifactStore._isBackgroundStreaming && artifactStore.openPanel(msg.artifactId)"
                >
                  <div class="preview-card-header">
                    <div class="preview-card-title-row">
                      <span class="preview-card-icon">{{ isSlideArtifact(msg.artifactId) ? '📊' : '📄' }}</span>
                      <span class="preview-card-title">{{ getArtifactTitle(msg.artifactId) }}</span>
                      <span
                        v-if="isSlideArtifact(msg.artifactId) && getSlideCount(msg.artifactId) > 0"
                        class="preview-card-meta"
                      >· {{ getSlideCount(msg.artifactId) }} 页</span>
                      <span v-if="artifactStore._isBackgroundStreaming && artifactStore.activeArtifactId === msg.artifactId" class="preview-card-streaming-badge">生成中...</span>
                    </div>
                  </div>
                  <!-- slides 不渲染 preview body -->
                  <div
                    v-if="!isSlideArtifact(msg.artifactId)"
                    class="preview-card-body"
                    :class="{ 'is-streaming': artifactStore._isBackgroundStreaming && artifactStore.activeArtifactId === msg.artifactId }"
                    :ref="(el: any) => { if (el && artifactStore._isBackgroundStreaming && artifactStore.activeArtifactId === msg.artifactId) streamingPreviewRef = el as HTMLElement }"
                    v-html="getArtifactStreamingPreviewHtml(msg.artifactId)"
                  ></div>
                </div>
              </template>
            </MessageItem>
            <!-- P1b: 低置信度意图确认卡片（移动端） -->
            <div v-if="msg.pendingIntentConfirm" class="intent-confirm-card">
              <div class="intent-confirm-icon">🤔</div>
              <div class="intent-confirm-body">
                <div v-if="msg.pendingIntentConfirm.clarifyHint" class="intent-confirm-text" v-html="msg.pendingIntentConfirm.clarifyHint"></div>
                <div v-else class="intent-confirm-text">
                  我理解你想使用「{{ msg.pendingIntentConfirm.skillDisplayName }}」
                  <span class="intent-confirm-confidence">({{ Math.round(msg.pendingIntentConfirm.confidence * 100) }}%)</span>
                  ，对吗？
                </div>
                <div v-if="msg.pendingIntentConfirm.alternatives?.length" class="intent-confirm-alternatives">
                  <span v-if="!msg.pendingIntentConfirm.clarifyHint" class="intent-confirm-alt-label">或者：</span>
                  <n-button
                    v-for="alt in msg.pendingIntentConfirm.alternatives"
                    :key="alt.skillName"
                    size="tiny"
                    quaternary
                    @click="handleIntentConfirm(alt.skillName)"
                  >
                    {{ alt.skillDisplayName }}
                    <span class="intent-confirm-confidence">({{ Math.round(alt.confidence * 100) }}%)</span>
                  </n-button>
                </div>
                <div class="intent-confirm-actions">
                  <n-button size="small" type="primary" @click="handleIntentConfirm(msg.pendingIntentConfirm!.skillName)">
                    确认使用「{{ msg.pendingIntentConfirm.skillDisplayName }}」
                  </n-button>
                  <n-button size="small" secondary @click="handleIntentConfirm(FORCE_SKILL_QA)">
                    直接问答
                  </n-button>
                  <n-button size="small" quaternary @click="handleIntentCancel">
                    取消
                  </n-button>
                </div>
              </div>
            </div>
            <!-- Skill 推荐链卡片（移动端） -->
            <div v-if="msg.nextSkillSuggestions?.length && !msg.isStreaming" class="next-skill-suggestions">
              <div class="next-skill-suggestions-label">推荐后续操作：</div>
              <div class="next-skill-suggestions-list">
                <n-button
                  v-for="suggestion in msg.nextSkillSuggestions"
                  :key="suggestion.skill"
                  size="small"
                  secondary
                  @click="handleNextSkillSuggestion(suggestion.skill, suggestion.prompt)"
                >
                  {{ suggestion.prompt }}
                </n-button>
              </div>
            </div>
          </template>

          <!-- 🆕 错误状态组件（移动端） -->
          <ErrorState
            v-if="showErrorState"
            :error-type="errorType"
            :message="errorMessage"
            :data-sources="dataSources"
            :search-query="inputText"
            :retrying="isRetrying"
            @retry="handleRetry"
            @close="handleErrorClose"
            class="error-state-wrapper mobile-error"
          />
        </div>

        <!-- 移动端底部输入区 -->
        <div class="input-area-wrapper">
          <div v-if="loading && currentSessionId" class="stop-generation-bar">
            <button class="stop-generation-btn" @click="stopGeneration">
              <span class="stop-icon">■</span>
              停止生成
            </button>
          </div>

          <!-- 移动端 Artifact 抽屉：只在展开态挂载 -->
          <MobileArtifactDrawer v-if="artifactStore.panelVisible" />

          <!-- 移动端输入区 -->
          <MobileChatInput
          v-model="inputText"
          :loading="loading"
          :disabled="loading || hasPendingPlan"
          :placeholder="hasPendingPlan ? '请先审批调研计划后再继续对话' : undefined"
          :selected-skill="selectedSkill"
          :skills="mentionList"
          :chat-mode="chatMode"
          :chat-mode-disabled="hasPendingPlan || loading"
          :attachments="attachments"
          :has-uploading="attachmentUploading"
          @send="sendMessage"
          @select-skill="selectSkill"
          @clear-skill="clearSkill"
          @update:chat-mode="chatMode = $event"
          @add-files="attachmentAddFiles"
          @remove-attachment="removeAttachment"
        />
        </div>
      </div>

      <!-- 移动端会话抽屉 -->
      <MobileSessionDrawer
        v-model:show="showSessionDrawer"
        :sessions="sessions as any"
        :current-session-id="currentSessionId"
        :loading="sessionsLoading"
        @select="handleMobileSessionSelect"
        @delete="handleDeleteSession"
        @new-chat="handleNewChat"
      />

      <!-- 移动端更多操作ActionSheet -->
      <n-modal
        v-model:show="showMoreActions"
        :mask-closable="true"
        transform-origin="center"
      >
        <div class="mobile-action-sheet">
          <div class="action-sheet-item" @click="exportChat('markdown'); showMoreActions = false">
            <n-icon :size="20" style="margin-right: 12px;">
              <DownloadOutline />
            </n-icon>
            导出为 Markdown
          </div>
          <div class="action-sheet-item" @click="exportChat('txt'); showMoreActions = false">
            <n-icon :size="20" style="margin-right: 12px;">
              <DownloadOutline />
            </n-icon>
            导出为 TXT
          </div>
          <div class="action-sheet-item" @click="exportPdf(); showMoreActions = false">
            <n-icon :size="20" style="margin-right: 12px;">
              <DownloadOutline />
            </n-icon>
            导出为 PDF
          </div>
          <div class="action-sheet-item" @click="exportDocx('standard'); showMoreActions = false">
            <n-icon :size="20" style="margin-right: 12px;">
              <DownloadOutline />
            </n-icon>
            导出 Word（通用格式）
          </div>
          <div class="action-sheet-item" @click="exportDocx('official'); showMoreActions = false">
            <n-icon :size="20" style="margin-right: 12px;">
              <DownloadOutline />
            </n-icon>
            导出 Word（行政公文）
          </div>
          <div class="action-sheet-item" @click="exportDocx('internal'); showMoreActions = false">
            <n-icon :size="20" style="margin-right: 12px;">
              <DownloadOutline />
            </n-icon>
            导出 Word（内部文件）
          </div>
          <div class="action-sheet-item" @click="exportDocx('report'); showMoreActions = false">
            <n-icon :size="20" style="margin-right: 12px;">
              <DownloadOutline />
            </n-icon>
            导出 Word（研究报告）
          </div>
          <div class="action-sheet-item" @click="openMemoryModal(); showMoreActions = false">
            <n-icon :size="20" style="margin-right: 12px;">
              <BulbOutline />
            </n-icon>
            记忆管理
          </div>
          <div class="action-sheet-cancel" @click="showMoreActions = false">
            取消
          </div>
        </div>
      </n-modal>

      <!-- PC端引用弹窗 Modal -->
      <!-- 注意：已移至根模板层级，见 </template> 后 -->

      <!-- 🆕 移动端引用详情抽屉（方案B） -->
      <n-drawer
        v-model:show="showReferenceDrawer"
        placement="bottom"
        :height="500"
        :close-on-esc="true"
      >
        <n-drawer-content title="参考文献">
          <div class="reference-drawer-content">
            <div
              v-for="ref in currentReferences"
              :key="ref.id"
              class="reference-drawer-item"
            >
              <div class="reference-drawer-header">
                <span class="reference-number">[{{ parseInt(ref.id.replace('ref_', ''), 10) }}]</span>
                <span class="reference-title-text">{{ ref.title }}</span>
              </div>
              <div class="reference-meta">
                <n-tag v-if="ref.data_source" size="tiny" :type="getSourceTagType(ref.data_source)" style="margin-right: 4px">
                  {{ getSourceIcon(ref.data_source) }} {{ getSourceLabel(ref.data_source) }}
                </n-tag>
                <n-tag v-if="ref.relevance_label" size="tiny" :bordered="false">{{ ref.relevance_label }}</n-tag>
                <n-tag v-if="ref.year" size="small" :bordered="false">{{ ref.year }}</n-tag>
                <span v-if="ref.authors && ref.authors.length > 0" class="reference-authors">
                  {{ ref.authors.join(' · ') }}
                </span>
              </div>
              <div v-if="ref.source" class="reference-source-text">
                来源: {{ ref.source }}
              </div>
              <div v-if="ref.abstract" class="reference-abstract">
                <template v-if="ref.abstract.length <= 200">{{ ref.abstract }}</template>
                <template v-else>
                  <span v-if="!expandedRefs.has(ref.id)">
                    {{ ref.abstract.slice(0, 200) }}...
                    <n-button text type="primary" size="tiny" @click="expandedRefs.add(ref.id)">展开全文</n-button>
                  </span>
                  <span v-else>
                    {{ ref.abstract }}
                    <n-button text type="primary" size="tiny" @click="expandedRefs.delete(ref.id)">收起</n-button>
                  </span>
                </template>
              </div>
              <n-button
                v-if="ref.url"
                type="primary"
                block
                @click="openReferenceLink(ref.url)"
              >
                查看原文
              </n-button>
              <n-divider />
            </div>
          </div>
        </n-drawer-content>
      </n-drawer>
    </template>

    <!-- PC端引用弹窗 Modal（放在根模板层级，PC/移动端均可触发） -->
    <n-modal
      v-model:show="showReferenceModal"
      preset="card"
      title="参考资料"
      :style="{ width: '700px', maxHeight: '80vh' }"
      :segmented="{ content: true }"
      @after-leave="refFilterTab = 'all'"
    >
      <n-tabs v-model:value="refFilterTab" type="segment" size="small" style="margin-bottom: 12px">
        <n-tab-pane name="all" :tab="`全部(${currentReferences.length})`" />
        <n-tab-pane
          v-for="group in refGroups"
          :key="group.key"
          :name="group.key"
          :tab="`${group.icon}${group.label}(${group.count})`"
        />
      </n-tabs>
      <div style="max-height: 60vh; overflow-y: auto">
        <div
          v-for="ref in filteredReferences"
          :key="ref.id"
          style="padding: 10px 0; border-bottom: 1px solid var(--n-border-color, #eee)"
        >
          <div style="display: flex; align-items: baseline; gap: 6px; margin-bottom: 4px">
            <span style="color: var(--n-text-color-3, #999); font-size: 12px">[{{ parseInt(ref.id.replace('ref_', ''), 10) }}]</span>
            <span style="font-weight: 500">{{ ref.title }}</span>
          </div>
          <div style="display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--n-text-color-3, #999); margin-bottom: 4px">
            <n-tag size="tiny" :type="getSourceTagType(ref.data_source)">
              {{ getSourceIcon(ref.data_source) }} {{ getSourceLabel(ref.data_source) }}
            </n-tag>
            <n-tag v-if="ref.relevance_label" size="tiny" :bordered="false">{{ ref.relevance_label }}</n-tag>
            <span v-if="ref.authors && ref.authors.length > 0">{{ ref.authors.join(', ') }}</span>
            <span v-if="ref.year">· {{ ref.year }}</span>
          </div>
          <div v-if="ref.abstract" style="font-size: 13px; color: var(--n-text-color-2, #666); margin-bottom: 4px; line-height: 1.5">
            <template v-if="ref.abstract.length <= 200">{{ ref.abstract }}</template>
            <template v-else>
              <span v-if="!expandedRefs.has(ref.id)">
                {{ ref.abstract.slice(0, 200) }}...
                <n-button text type="primary" size="tiny" @click="expandedRefs.add(ref.id)">展开全文</n-button>
              </span>
              <span v-else>
                {{ ref.abstract }}
                <n-button text type="primary" size="tiny" @click="expandedRefs.delete(ref.id)">收起</n-button>
              </span>
            </template>
          </div>
          <n-button v-if="ref.url" text type="primary" size="small" @click="openReferenceLink(ref.url)">
            查看原文 →
          </n-button>
        </div>
        <div v-if="filteredReferences.length === 0" style="text-align: center; padding: 20px; color: var(--n-text-color-3, #999)">
          暂无此类引用
        </div>
      </div>
    </n-modal>

    <!-- 辩论 UI（操作面板 + 追问弹窗） -->
    <DebateUI
      :debate-status="debateStatus"
      :current-debate-round="currentDebateRound"
      :max-debate-rounds="maxDebateRounds"
      :can-continue-debate="canContinueDebate"
      :round-hint="roundHint"
      :is-debate-processing="isDebateProcessing"
      :show-ask-expert-modal="showAskExpertModal"
      :selected-expert="selectedExpert"
      :ask-expert-question="askExpertQuestion"
      :ask-expert-loading="askExpertLoading"
      :discussion-mode="discussionMode"
      @continue-debate="continueDebate"
      @end-debate="endDebate"
      @show-ask-expert="showAskExpertFromPanel"
      @send-ask-expert="sendAskExpert"
      @close-ask-expert="closeAskExpertModal"
      @update:show-ask-expert-modal="showAskExpertModal = $event"
      @update:ask-expert-question="askExpertQuestion = $event"
      :audio-segments="debateAudioSegments"
    />

    <!-- 文件预览模态框 -->
    <FilePreviewModal
      v-model:show="showFilePreviewModal"
      :file="previewFile"
      @pdf-created="handlePdfCreated"
    />

    <!-- 记忆管理弹窗（Viking Memory 升级版） -->
    <n-modal
      v-model:show="showMemoryModal"
      preset="card"
      title="长期记忆管理"
      :style="{ width: isMobile ? '95vw' : '900px', maxHeight: '85vh' }"
      :mask-closable="true"
    >
      <div v-if="memoryLoading" style="text-align: center; padding: 24px;">
        加载中...
      </div>
      <div v-else>
        <!-- 搜索框 -->
        <n-input
          v-model:value="memorySearchKeyword"
          placeholder="搜索记忆..."
          clearable
          size="small"
          style="margin-bottom: 12px;"
        />

        <!-- Filter Chips -->
        <div class="memory-filter-chips">
          <span
            v-for="(cfg, key) in categoryMap"
            :key="key"
            class="memory-chip"
            :class="{ active: memoryFilterCategory === key }"
            :style="memoryFilterCategory === key ? { background: cfg.color, color: '#fff', borderColor: cfg.color } : { borderColor: cfg.color, color: cfg.color }"
            @click="memoryFilterCategory = key as string"
          >
            {{ cfg.label }}
            <span v-if="memoryCategoryCounts[key]" class="chip-count">{{ memoryCategoryCounts[key] }}</span>
          </span>
        </div>

        <!-- 空状态 -->
        <div v-if="filteredMemoryList.length === 0" style="text-align: center; padding: 32px; color: #999;">
          {{ memoryList.length === 0 ? '暂无记忆数据' : '无匹配结果' }}
        </div>

        <!-- 分栏布局（桌面）/ 列表（移动） -->
        <div v-else class="memory-layout" :class="{ 'memory-layout--mobile': isMobile }">
          <!-- 左侧列表 -->
          <div class="memory-list-panel">
            <div
              v-for="item in filteredMemoryList"
              :key="item.id"
              class="memory-card"
              :class="{ 'memory-card--selected': selectedMemory?.id === item.id }"
              @click="selectedMemory = item"
            >
              <div class="memory-card-header">
                <span
                  v-if="item.category"
                  class="memory-category-tag"
                  :style="{ background: getCategoryConfig(item.category).color + '18', color: getCategoryConfig(item.category).color }"
                >
                  {{ getCategoryConfig(item.category).label }}
                </span>
                <span class="memory-card-abstract">{{ item.abstract || '' }}</span>
              </div>
              <div class="memory-card-overview">{{ item.memory || item.overview || '' }}</div>
              <div class="memory-card-meta">
                <span v-if="item.created_at">{{ item.created_at?.slice(0, 10) }}</span>
                <span v-if="item.metadata?.merge_count" style="margin-left: 8px;">合并×{{ item.metadata.merge_count }}</span>
              </div>
            </div>
          </div>

          <!-- 右侧详情（桌面端） -->
          <div v-if="!isMobile && selectedMemory" class="memory-detail-panel">
            <div class="memory-detail-title">{{ selectedMemory.abstract || '记忆详情' }}</div>
            <div class="memory-detail-category">
              分类:
              <span
                v-if="selectedMemory.category"
                :style="{ color: getCategoryConfig(selectedMemory.category).color }"
              >
                {{ getCategoryConfig(selectedMemory.category).label }}
              </span>
            </div>
            <div class="memory-detail-section">
              <div class="memory-detail-label">摘要</div>
              <div class="memory-detail-content markdown-content" v-html="renderMemoryMd(selectedMemory.overview || selectedMemory.memory)"></div>
            </div>
            <div v-if="selectedMemory.content" class="memory-detail-section">
              <div class="memory-detail-label">详细内容</div>
              <div class="memory-detail-content markdown-content" v-html="renderMemoryMd(selectedMemory.content)"></div>
            </div>
            <div class="memory-detail-meta">
              <div v-if="selectedMemory.created_at">创建: {{ selectedMemory.created_at }}</div>
              <div v-if="selectedMemory.updated_at">更新: {{ selectedMemory.updated_at }}</div>
              <div v-if="selectedMemory.metadata?.merge_count">合并次数: {{ selectedMemory.metadata.merge_count }}</div>
            </div>
            <div style="margin-top: 16px;">
              <n-button size="small" type="error" @click="handleDeleteMemory(selectedMemory.id)">删除此记忆</n-button>
            </div>
          </div>
          <div v-else-if="!isMobile" class="memory-detail-panel memory-detail-empty">
            点击左侧卡片查看详情
          </div>
        </div>

        <!-- 移动端 Bottom Sheet 详情 -->
        <n-drawer
          v-if="isMobile"
          :show="!!selectedMemory"
          placement="bottom"
          :height="'70vh'"
          :mask-closable="true"
          @update:show="(val: boolean) => { if (!val) selectedMemory = null }"
        >
          <n-drawer-content v-if="selectedMemory" :title="selectedMemory.abstract || '记忆详情'" closable>
            <div class="memory-detail-category">
              分类:
              <span
                v-if="selectedMemory.category"
                :style="{ color: getCategoryConfig(selectedMemory.category).color }"
              >
                {{ getCategoryConfig(selectedMemory.category).label }}
              </span>
            </div>
            <div class="memory-detail-section">
              <div class="memory-detail-label">摘要</div>
              <div class="markdown-content" v-html="renderMemoryMd(selectedMemory.overview || selectedMemory.memory)"></div>
            </div>
            <div v-if="selectedMemory.content" class="memory-detail-section">
              <div class="memory-detail-label">详细内容</div>
              <div class="markdown-content" v-html="renderMemoryMd(selectedMemory.content)"></div>
            </div>
            <div class="memory-detail-meta">
              <div v-if="selectedMemory.created_at">创建: {{ selectedMemory.created_at }}</div>
              <div v-if="selectedMemory.updated_at">更新: {{ selectedMemory.updated_at }}</div>
            </div>
            <template #footer>
              <n-button size="small" type="error" @click="handleDeleteMemory(selectedMemory!.id)">删除此记忆</n-button>
            </template>
          </n-drawer-content>
        </n-drawer>
      </div>

      <template #footer>
        <div style="display: flex; justify-content: space-between;">
          <n-button
            type="error"
            size="small"
            :disabled="memoryList.length === 0"
            @click="handleClearMemories"
          >
            清空全部
          </n-button>
          <span style="color: #999; font-size: 13px; line-height: 34px;">
            共 {{ memoryList.length }} 条记忆
            <template v-if="filteredMemoryList.length !== memoryList.length">
              (筛选 {{ filteredMemoryList.length }})
            </template>
          </span>
        </div>
      </template>
    </n-modal>

    <!-- AI 助手选择面板 -->
    <AgentSelector
      v-model:show="showAgentSelector"
      :current-agent-id="currentAgent?.id ?? null"
      :default-agent-id="defaultAgentId"
      @select="handleAgentSelect"
      @create="showAgentCreator = true; showAgentSelector = false"
      @set-default="handleSetDefault"
      @clear-default="handleClearDefault"
    />

    <!-- AI 助手创建/编辑抽屉 -->
    <AgentCreatorDrawer
      v-model:show="showAgentCreator"
      @saved="handleAgentSaved"
    />
  </div>
</template>

<style scoped>
.super-agent-page {
  height: 100%;
}

/* P1b: 意图确认卡片 */
.intent-confirm-card {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin: 8px 0 12px 48px;
  padding: 12px 16px;
  background: #fffbe6;
  border: 1px solid #ffe58f;
  border-radius: 10px;
  max-width: 480px;
}

.intent-confirm-icon {
  font-size: 20px;
  flex-shrink: 0;
  line-height: 1.4;
}

.intent-confirm-body {
  flex: 1;
  min-width: 0;
}

.intent-confirm-text {
  font-size: 14px;
  color: #333;
  line-height: 1.5;
  margin-bottom: 10px;
}

.intent-confirm-confidence {
  color: #999;
  font-size: 12px;
}

.intent-confirm-actions {
  display: flex;
  gap: 8px;
}

.intent-confirm-alternatives {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}

.intent-confirm-alt-label {
  font-size: 12px;
  color: #999;
  flex-shrink: 0;
}

/* Skill 推荐链卡片 */
.next-skill-suggestions {
  margin: 8px 0 12px 48px;
  padding: 10px 14px;
  background: #f0f9ff;
  border: 1px solid #bae0ff;
  border-radius: 10px;
  max-width: 480px;
}

.next-skill-suggestions-label {
  font-size: 13px;
  color: #666;
  margin-bottom: 8px;
}

.next-skill-suggestions-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

/* 底部输入区 wrapper */
.input-area-wrapper {
  position: relative;
  flex-shrink: 0;
  background: #fff;
  padding: 0;
  border-top: 1px solid #e8e8ec;
}

/* 查询改写提示条（Perplexica 迁移） */
.query-rewrite-bar {
  margin: 0 24px 4px;
  background: #f5f5f5;
  border-radius: 6px;
  font-size: 12px;
  color: #888;
  overflow: hidden;
}

.query-rewrite-summary {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  cursor: pointer;
  user-select: none;
}

.query-rewrite-summary:hover {
  background: #eeeeee;
}

.rewrite-icon {
  font-size: 13px;
  color: #999;
}

.rewrite-label {
  flex: 1;
  font-size: 12px;
}

.rewrite-toggle {
  font-size: 11px;
  color: #aaa;
}

.rewrite-retry-btn {
  margin-left: 8px;
  padding: 1px 8px;
  border: 1px solid #d0d0d0;
  border-radius: 10px;
  background: #fff;
  color: #666;
  font-size: 11px;
  cursor: pointer;
  transition: all 0.15s;
}

.rewrite-retry-btn:hover {
  border-color: #2080f0;
  color: #2080f0;
}

.query-rewrite-detail {
  padding: 4px 10px 8px;
  border-top: 1px solid #ebebeb;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.rewrite-row {
  display: flex;
  gap: 6px;
  font-size: 12px;
}

.rewrite-row-label {
  color: #aaa;
  white-space: nowrap;
}

.rewrite-row-value {
  color: #555;
  word-break: break-all;
}

/* 停止生成按钮（绝对定位，不占文档流，不影响 messages-container 高度） */
.stop-generation-bar {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 100%;
  display: flex;
  justify-content: center;
  padding: 8px 0;
  pointer-events: none;
}

.stop-generation-btn {
  pointer-events: auto;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 16px;
  border: 1px solid #d9d9d9;
  border-radius: 16px;
  background: #fff;
  color: #666;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s;
}

.stop-generation-btn:hover {
  border-color: #ff4d4f;
  color: #ff4d4f;
}

.discussion-mode-selector {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  background: #f5f5f5;
  border-radius: 8px;
  margin-bottom: 12px;
}

.mode-label {
  font-size: 13px;
  color: #666;
  font-weight: 500;
}

.mode-btn {
  padding: 6px 12px;
  border: 1px solid #d9d9d9;
  border-radius: 6px;
  background: #fff;
  color: #666;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s;
}

.mode-btn:hover {
  border-color: #1890ff;
  color: #1890ff;
}

.mode-btn.active {
  border-color: #1890ff;
  background: #1890ff;
  color: #fff;
}

.stop-icon {
  font-size: 10px;
}

/* ===== PC端样式 ===== */

/* 对话区域容器（不再使用 grid 分栏） */
.content-split {
  position: relative;
  height: 100%;
}

/* Artifact 浮动卡片（面板关闭时右下角显示） */
.artifact-float-card {
  position: fixed;
  bottom: 80px;
  right: 24px;
  z-index: 200;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 18px;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  cursor: pointer;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.1);
  transition: all 0.2s;
  font-size: 14px;
  font-weight: 500;
  color: #1f2937;
}
.artifact-float-card:hover {
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.15);
  transform: translateY(-2px);
  border-color: #2080f0;
  color: #2080f0;
}
.float-card-icon { font-size: 18px; }
.float-card-text { white-space: nowrap; }

/* Artifact 预览卡片（ChatGPT Canvas 风格，内嵌在消息气泡中） */
.artifact-preview-card {
  margin: 12px 0 8px;
  border: 1px solid #e0e0e0;
  border-radius: 16px;
  overflow: hidden;
  background: #fff;
  cursor: pointer;
  transition: box-shadow 0.2s, border-color 0.2s;
}
.artifact-preview-card:hover {
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.08);
  border-color: #d0d0d0;
}
/* 流式生成中的卡片样式 */
.artifact-preview-card.is-streaming {
  border-color: #93c5fd;
  cursor: default;
  animation: artifact-pulse 2s ease-in-out infinite;
}
.artifact-preview-card.is-streaming:hover {
  box-shadow: none;
  border-color: #93c5fd;
}
.preview-card-streaming-badge {
  font-size: 11px;
  color: #3b82f6;
  font-weight: 400;
  animation: artifact-pulse 2s ease-in-out infinite;
}
@keyframes artifact-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

/* 标题栏：左侧标题 + 右侧操作按钮 */
.preview-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  border-bottom: 1px solid #f3f4f6;
}
.preview-card-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex: 1;
}
.preview-card-icon { font-size: 16px; flex-shrink: 0; }
.preview-card-title {
  font-size: 14px;
  font-weight: 600;
  color: #111827;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.preview-card-actions {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
}
.pca-btn {
  padding: 3px 10px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: #6b7280;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s;
}
.pca-btn:hover {
  background: #f3f4f6;
  color: #111827;
}

/* 内容预览区 */
.preview-card-body {
  padding: 14px 16px;
  font-size: 14px;
  line-height: 1.7;
  color: #374151;
  max-height: 240px;
  overflow: hidden;
  mask-image: linear-gradient(to bottom, black 65%, transparent 100%);
  -webkit-mask-image: linear-gradient(to bottom, black 65%, transparent 100%);
  transition: mask-image 0.3s;
}
/* 流式生成中：可滚动、无遮罩 */
.preview-card-body.is-streaming {
  overflow-y: auto;
  mask-image: none;
  -webkit-mask-image: none;
  scroll-behavior: smooth;
}
.preview-card-body.is-streaming::-webkit-scrollbar {
  width: 4px;
}
.preview-card-body.is-streaming::-webkit-scrollbar-thumb {
  background: #d1d5db;
  border-radius: 2px;
}
.preview-card-body h2 {
  font-size: 16px;
  font-weight: 700;
  margin: 6px 0 4px;
  color: #111827;
}
.preview-card-body h3 {
  font-size: 15px;
  font-weight: 600;
  margin: 4px 0 2px;
  color: #1f2937;
}
.preview-card-body h4 {
  font-size: 14px;
  font-weight: 600;
  margin: 4px 0 2px;
  color: #374151;
}

/* 聊天主区域 */
.chat-main {
  height: 100%;
  display: flex;
  flex-direction: column;
  min-width: 0; /* 防止 grid 子项溢出 */
}

/* 状态栏（参考图2设计：白色背景 + 底部边框） */
.status-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 24px;
  background: #fff;
  border-bottom: 1px solid #e5e7eb;
  overflow: hidden;
  max-height: 100px;
  transition: max-height 250ms ease, padding 250ms ease;
}

.status-header.collapsed {
  max-height: 56px;
  padding: 8px 24px;
}

.status-header.collapsed .header-expandable {
  display: none;
}

.header-toggle-btn {
  font-size: 12px;
  color: #9ca3af;
  flex-shrink: 0;
}

.status-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

/* 智能体头像 */
.agent-avatar {
  width: 44px;
  height: 44px;
  border-radius: 8px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

/* 智能体信息 */
.agent-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.agent-title {
  display: flex;
  align-items: center;
}

.status-title {
  font-size: 16px;
  font-weight: 600;
  color: #1f2937;
}

.agent-description {
  font-size: 13px;
  color: #6b7280;
}

.status-right {
  display: flex;
  gap: 8px;
  align-items: center;
}

/* 🆕 视图模式切换按钮组（方案C - 适配白色背景） */
.view-mode-group {
  margin-right: 12px;
}

.view-mode-group :deep(.n-button) {
  font-size: 12px;
  padding: 0 10px;
}

.view-mode-group :deep(.n-button--default-type) {
  background: #f3f4f6;
  border-color: #e5e7eb;
  color: #6b7280;
}

.view-mode-group :deep(.n-button--default-type:hover) {
  background: #e5e7eb;
  color: #374151;
}

.view-mode-group :deep(.n-button--primary-type) {
  background: #2080f0;
  border-color: #2080f0;
  color: #fff;
}

/* 顶部工具栏 */
.chat-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 24px;
  background: #fff;
  border-bottom: 1px solid #eee;
}

.toolbar-title {
  font-size: 14px;
  font-weight: 500;
  color: #333;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 300px;
}

.toolbar-actions {
  display: flex;
  gap: 8px;
}

.messages-container {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
  display: flex;
  flex-direction: column;
  overflow-anchor: none; /* 禁用容器内所有子元素的默认锚定 */
}

/* 文档处理中提示横幅 */
.processing-doc-banner {
  display: flex;
  align-items: center;
  padding: 8px 16px;
  margin-bottom: 12px;
  background: var(--n-color-hover, #f0f9ff);
  border: 1px solid var(--n-border-color, #bae6fd);
  border-radius: 8px;
  font-size: 13px;
  color: var(--n-text-color-2, #0369a1);
  line-height: 1.5;
}

/* 底部锚点：浏览器原生滚动锚定，内容增长时自动保持滚动位置，零延迟 */
.scroll-anchor {
  overflow-anchor: auto;
  height: 1px;
  flex-shrink: 0;
}

.welcome-section {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  flex: 1;
  min-height: 300px;
  text-align: center;
}

.welcome-icon {
  margin-bottom: 16px;
}

.agent-welcome-avatar {
  font-size: 56px;
  line-height: 1;
}

.agent-welcome-message {
  margin: 12px auto;
  max-width: 500px;
  padding: 12px 16px;
  background: #f8fafc;
  border-radius: 12px;
  color: #555;
  font-size: 14px;
  line-height: 1.6;
  text-align: left;

  :deep(p) {
    margin: 4px 0;
  }
  :deep(ul), :deep(ol) {
    margin: 4px 0;
    padding-left: 20px;
  }
  :deep(li) {
    margin: 2px 0;
  }
  :deep(strong) {
    color: #333;
  }
}

.agent-avatar-emoji {
  font-size: 24px;
  background: none !important;
}

.mobile-agent-emoji {
  font-size: 20px;
}

.welcome-section h2 {
  margin: 0 0 8px;
  font-size: 24px;
  font-weight: 600;
  color: #333;
}

.welcome-section p {
  margin: 0 0 16px;
  color: #666;
  max-width: 400px;
}

.feature-tags {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 8px;
  margin-bottom: 24px;
}

.example-questions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 8px;
}

.example-tag {
  cursor: pointer;
  transition: transform 0.2s;
  background: #f5f7f9;
}

.example-tag:hover {
  transform: scale(1.05);
  background: #e8f4ff;
}

.error-state-wrapper {
  margin: 16px 0;
}

/* 🆕 方案C：视图模式切换过渡动画 */
.view-mode-fade-enter-active,
.view-mode-fade-leave-active {
  transition: all 0.25s ease;
}

.view-mode-fade-enter-from,
.view-mode-fade-leave-to {
  opacity: 0;
  transform: translateY(-8px);
}

/* 🆕 Debug 面板/时间线位置 */
.debug-timeline-wrapper {
  margin: 8px 16px;
}

.debug-panel-wrapper {
  margin: 0 16px 12px;
}

/* 移动端引用抽屉 */
.reference-drawer-content {
  padding: 0 4px;
}

.reference-drawer-item {
  margin-bottom: 8px;
}

.reference-drawer-header {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-bottom: 8px;
}

.reference-authors {
  color: #6b7280;
  font-size: 13px;
}

.reference-source-text {
  font-size: 12px;
  color: #9ca3af;
  margin-bottom: 8px;
}

.mobile-error {
  margin: 12px 0;
}

/* ===== 移动端样式 ===== */
.super-agent-page-mobile {
  height: 100%;
  overflow: hidden;
}

.mobile-chat-container {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #f5f7f9;
}

.mobile-header {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  flex-shrink: 0;
}

.mobile-header-content {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
}

.mobile-agent-info {
  display: flex;
  align-items: center;
  gap: 8px;
}

.mobile-agent-name {
  font-size: 16px;
  font-weight: 600;
  color: #fff;
}

.mobile-header-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.mobile-header-actions :deep(.n-button) {
  color: #fff;
}

.mobile-messages-container {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  padding-bottom: calc(60px + 56px + env(safe-area-inset-bottom, 0px));
}

.mobile-welcome-section {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 40px 20px;
  text-align: center;
}

.mobile-welcome-section h3 {
  margin: 12px 0 8px;
  font-size: 18px;
  color: #333;
}

.mobile-welcome-section p {
  margin: 0 0 16px;
  font-size: 14px;
  color: #666;
}

.mobile-feature-tags {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 8px;
  margin-bottom: 20px;
}

.mobile-example-questions {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}

.mobile-example-item {
  padding: 12px 16px;
  background: #fff;
  border-radius: 12px;
  font-size: 14px;
  color: #333;
  cursor: pointer;
  transition: background 0.2s;
  text-align: left;
}

.mobile-example-item:active {
  background: #e8f4ff;
}

/* 移动端视图模式按钮样式 */
.view-mode-btn-mobile {
  color: #fff;
}

.view-mode-btn-mobile:hover {
  background: rgba(255, 255, 255, 0.2);
}

/* 移动端ActionSheet */
.mobile-action-sheet {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  background: #fff;
  border-radius: 12px 12px 0 0;
  padding-bottom: env(safe-area-inset-bottom, 0px);
  animation: slideUp 0.25s ease-out;
}

@keyframes slideUp {
  from {
    transform: translateY(100%);
  }
  to {
    transform: translateY(0);
  }
}

.action-sheet-item {
  display: flex;
  align-items: center;
  padding: 16px 20px;
  font-size: 16px;
  color: #333;
  border-bottom: 1px solid #f0f0f0;
  cursor: pointer;
  transition: background 0.2s;
}

.action-sheet-item:active {
  background: #f5f5f5;
}

.action-sheet-cancel {
  padding: 16px 20px;
  font-size: 16px;
  color: #999;
  text-align: center;
  cursor: pointer;
  margin-top: 8px;
  background: #f5f5f5;
}

.action-sheet-cancel:active {
  background: #eee;
}

/* ==================== 记忆管理面板样式 ==================== */
.memory-filter-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 16px;
}

.memory-chip {
  display: inline-flex;
  align-items: center;
  padding: 4px 12px;
  border-radius: 16px;
  border: 1px solid;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s;
  user-select: none;
}

.memory-chip:hover {
  opacity: 0.8;
}

.memory-chip.active {
  font-weight: 500;
}

.chip-count {
  margin-left: 4px;
  font-size: 11px;
  opacity: 0.7;
}

.memory-layout {
  display: flex;
  gap: 16px;
  max-height: 50vh;
}

.memory-layout--mobile {
  flex-direction: column;
}

.memory-list-panel {
  flex: 1;
  overflow-y: auto;
  min-width: 0;
}

.memory-card {
  padding: 12px;
  border: 1px solid #f0f0f0;
  border-radius: 8px;
  margin-bottom: 8px;
  cursor: pointer;
  transition: all 0.15s;
}

.memory-card:hover {
  border-color: #d0d0d0;
  background: #fafafa;
}

.memory-card--selected {
  border-color: #409EFF;
  background: #f0f7ff;
}

.memory-card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.memory-category-tag {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 500;
  flex-shrink: 0;
}

.memory-card-abstract {
  font-size: 14px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.memory-card-overview {
  font-size: 13px;
  color: #666;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.memory-card-meta {
  font-size: 11px;
  color: #999;
  margin-top: 6px;
}

.memory-detail-panel {
  flex: 1;
  padding: 16px;
  border: 1px solid #f0f0f0;
  border-radius: 8px;
  overflow-y: auto;
  min-width: 280px;
}

.memory-detail-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  color: #ccc;
  font-size: 14px;
}

.memory-detail-title {
  font-size: 16px;
  font-weight: 600;
  margin-bottom: 8px;
}

.memory-detail-category {
  font-size: 13px;
  color: #666;
  margin-bottom: 16px;
}

.memory-detail-section {
  margin-bottom: 16px;
}

.memory-detail-label {
  font-size: 12px;
  color: #999;
  margin-bottom: 4px;
  font-weight: 500;
}

.memory-detail-content {
  font-size: 14px;
  line-height: 1.6;
  color: #333;
}

.memory-detail-meta {
  font-size: 12px;
  color: #999;
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid #f0f0f0;
}

.memory-detail-meta > div {
  margin-bottom: 4px;
}

/* 记忆面板 Markdown 渲染样式 */
.memory-detail-panel .markdown-content :deep(p) {
  margin: 4px 0;
  line-height: 1.6;
}
.memory-detail-panel .markdown-content :deep(p:last-child) {
  margin-bottom: 0;
}
.memory-detail-panel .markdown-content :deep(h1),
.memory-detail-panel .markdown-content :deep(h2),
.memory-detail-panel .markdown-content :deep(h3) {
  margin: 8px 0 4px;
  font-weight: 600;
}
.memory-detail-panel .markdown-content :deep(h2) { font-size: 1.1em; }
.memory-detail-panel .markdown-content :deep(h3) { font-size: 1.05em; }
.memory-detail-panel .markdown-content :deep(ul),
.memory-detail-panel .markdown-content :deep(ol) {
  padding-left: 20px;
  margin: 4px 0;
}
.memory-detail-panel .markdown-content :deep(li) {
  margin: 2px 0;
  line-height: 1.5;
}
.memory-detail-panel .markdown-content :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 8px 0;
  font-size: 0.9em;
}
.memory-detail-panel .markdown-content :deep(th),
.memory-detail-panel .markdown-content :deep(td) {
  border: 1px solid #e0e0e0;
  padding: 4px 8px;
  text-align: left;
}
.memory-detail-panel .markdown-content :deep(th) {
  background: #f5f5f5;
  font-weight: 600;
}
.memory-detail-panel .markdown-content :deep(strong) {
  font-weight: 600;
}

/* 移动端 drawer 内的 markdown 样式 */
.n-drawer .markdown-content :deep(p) { margin: 4px 0; line-height: 1.6; }
.n-drawer .markdown-content :deep(h2) { font-size: 1.1em; margin: 8px 0 4px; font-weight: 600; }
.n-drawer .markdown-content :deep(h3) { font-size: 1.05em; margin: 6px 0 4px; font-weight: 600; }
.n-drawer .markdown-content :deep(ul),
.n-drawer .markdown-content :deep(ol) { padding-left: 20px; margin: 4px 0; }
.n-drawer .markdown-content :deep(li) { margin: 2px 0; }
.n-drawer .markdown-content :deep(strong) { font-weight: 600; }
</style>
