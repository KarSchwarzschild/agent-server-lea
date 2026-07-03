/**
 * 超级智能体前端类型定义
 * 从 SuperAgent.vue 提取，供所有子组件和 composable 共享
 */

// 视图模式
export type ViewMode = 'simple' | 'professional' | 'debug'

// 聊天消息
export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  isStreaming?: boolean
  /** 消息创建时间戳（ISO format，用于课堂关联匹配等） */
  timestamp?: string
  /** v5.0: 消息节点 ID（节点池中的 key） */
  messageId?: string
  /** v5.0: 同父节点下的兄弟 ID 列表（按时间排序） */
  siblings?: string[]
  /** v5.0: 当前消息在 siblings 中的索引 */
  siblingIndex?: number
  intent?: string
  skillName?: string
  skillDisplayName?: string
  /** Artifact 关联 ID（用于内联卡片渲染） */
  artifactId?: string
  thinkingSteps?: ThinkingStep[]
  toolCalls?: ToolCall[]
  debateSessionId?: string
  experts?: import('../api/superagent').Expert[]
  /** 辩论话题预研背景资料 */
  preResearchContent?: string
  references?: Reference[]
  reasoningBlocks?: ReasoningBlock[]
  subAgents?: SubAgentState[]
  /** NEW-1: 子代理批量执行状态 */
  subAgentBatch?: SubAgentBatchState
  pendingClientTool?: ClientToolRequest
  pendingPlanQuestion?: PlanQuestionRequest
  executionPlan?: ExecutionPlanState
  /** Token 用量统计（llm_usage 事件累积） */
  tokenUsage?: { inputTokens: number; outputTokens: number }
  followupType?: 'refine' | 'continuation' | 'acknowledge'
  attachments?: AttachmentInfo[]
  /** 结构化流水线各阶段的输出摘要（非 write/revise 阶段） */
  stageOutputs?: Array<{
    stage: string
    title: string
    content: string
  }>
  /** Aggregation@N 聚合进度 */
  aggregationProgress?: AggregationProgress
  /** 聚合合成流式内容 */
  synthesisContent?: string
  /** 结构化 analyze 计划（JSON 解析后） */
  analyzePlan?: {
    topicCategory: string
    researchTopic: string
    keyQuestions: string[]
    searchSteps: Array<{
      stepId: number
      toolDisplay: string
      keywords: string
      language: string
      purpose: string
    }>
  }
  /** 低置信度意图确认（P1b） */
  pendingIntentConfirm?: {
    skillName: string
    skillDisplayName: string
    confidence: number
    originalMessage: string
    clarifyHint?: string
    alternatives?: Array<{
      skillName: string
      skillDisplayName: string
      confidence: number
    }>
  }
  /** 模式选择（辩论） */
  pendingModeSelection?: {
    topic: string
    recommendedMode: string
    modes: Array<{ value: string; label: string; icon: string; desc: string }>
    timeoutSeconds: number
  }
  /** 大纲预览（outline_preview 事件，聊天侧暂停交互） */
  pendingOutline?: {
    outline: any  // OutlineIR
    planId: string
    sessionId: string
    timeout: number
  }
  /** Skill 推荐链（skill_done 后推荐后续 Skill） */
  nextSkillSuggestions?: Array<{
    skill: string
    prompt: string
  }>
  /** 并行编排子任务列表 */
  parallelSubtasks?: Array<{
    id: string
    title: string
    scope: string
    dependsOn?: string[]
    status?: 'pending' | 'running' | 'done' | 'failed' | 'cancelled'
    stageName?: string
    startTime?: number
    error?: string
    content?: string
    toolName?: string
    expanded?: boolean
    references?: Reference[]
    retryReason?: string
    isRetrying?: boolean
    contentVersion?: number
    finalContent?: string
    researchContent?: string
    researchExpanded?: boolean
  }>
  /** 合成阶段标记 */
  isSynthesizing?: boolean
  /** 诊断中状态（独立于 isSynthesizing，用于质量诊断阶段） */
  isDiagnosing?: boolean
  /** 诊断阶段描述 */
  diagnosticPhase?: string
  /** 诊断报告数据 */
  diagnosticReport?: DiagnosticReportData
  /** 同行评审结果 */
  peerReview?: PeerReviewData
  /** 并行编排进度 */
  parallelProgress?: {
    completed: Array<{ sub_task_id: string; status: string }>
    failed: Array<{ sub_task_id: string; error: string }>
    total: number
  }
  /** 输出质量验证结果 */
  verification?: {
    passed: boolean
    score: number
    issues: Array<{ type: string; severity: string; message: string }>
    dimensions?: Array<{ name: string; score: number; reasoning: string; weight: number; description?: string }>
    llm_score?: number
    judge_model?: string
    judge_mode?: string
    duration_ms?: number
  }
  /** 输出格式（前端渲染路由，由 skill_start SSE 事件传递） */
  outputFormat?: string
  /** 统一错误信息（Skill 异常/超时/质量失败等） */
  errorInfo?: {
    code: string
    message: string
    retryable: boolean
    originalQuestion?: string
    skill?: string
  }
  /** Phase 2.1a: 结构化 ContentBlock 数组（v2 协议，与 normalizeMessage 字段名对齐） */
  blocks?: import('./content-block').ContentBlock[]
  /** 搜索深度元数据（Perplexica 深度分化） */
  metadata?: {
    depthRequested?: string
    depthApplied?: string
    depthOverridden?: boolean
    depthGate?: {
      rejected: boolean
      actual: number
      required: number
    }
  }
  /** 课堂教学数据 */
  classroomData?: {
    sessionId: string
    status: string
    totalScenes?: number
    firstReadyScene?: number
    firstUsableScene?: number
    canEnter?: boolean
    events: Array<{ type: string; data: any; ts: number }>
  }
}

// 聊天附件信息
export interface AttachmentInfo {
  id: string
  filename: string
  fileCategory: 'document' | 'image'
  fileExt: string
  size: number
  thumbnailUrl?: string        // 图片本地预览 URL (URL.createObjectURL)
  uploadProgress?: number
  status: 'uploading' | 'ready' | 'error'
  errorMessage?: string
  indexStatus?: 'building' | 'ready' | 'not_supported' | 'failed'  // PageIndex 索引状态
  sourceType?: 'upload' | 'kb_doc'  // 来源类型
}

// 思考块（DeepSeek-R1/QwQ 等模型）
export interface ReasoningBlock {
  expertId: string
  expertName: string
  reasoning: string
  isCollapsed: boolean
  phase?: string
  round?: number
}

// 思考步骤
export interface ThinkingStep {
  text: string
  step: number
  toolCallId?: string
  toolName?: string
  toolDisplayName?: string
  status?: 'pending' | 'running' | 'success' | 'error' | 'empty' | 'receiving'
  summary?: string
  durationMs?: number
  isToolCall?: boolean
  startTime?: number
  totalDurationMs?: number
  phaseDurations?: {
    thinking?: number
    tool_exec?: number
  }
  input?: Record<string, unknown>
  inputRaw?: string
  isStreaming?: boolean
  /** 聚合模式：所属路径索引（0-based） */
  pathIndex?: number
  /** 聚合模式：所属路径标签 */
  pathLabel?: string
}

// 工具调用
export interface ToolCall {
  tool: string
  toolDisplayName?: string
  toolCallId?: string
  stageName?: string  // 所属 plan stage
  input: Record<string, unknown>
  result?: string
  status?: string
  summary?: string
  durationMs?: number
  isStandalone?: boolean
  standalonePlugin?: {
    pluginIdentifier: string
    originalIdentifier?: string  // manifest 原始标识符（如 moonlit-mind-map），postMessage 需要
    originalApiName?: string     // manifest 原始 API 名（如 mindMap），postMessage 需要
    iframeUrl: string
    iframeHeight: number
    iframeWidth: number
    // 不含 pluginSettings — API Key 不到达浏览器
  }
  pendingToolUseId?: string  // client_tool 的 tool_use_id，用于 submitToolResult 回传
}

// 子代理工具调用
export interface SubAgentToolCall {
  tool: string
  displayName: string
  toolCallId?: string
  status: 'receiving' | 'running' | 'success' | 'empty' | 'error'
  input?: Record<string, unknown>
  inputRaw?: string
  summary?: string
  durationMs?: number
}

// 子代理状态
export interface SubAgentState {
  id: string
  subSkill: string
  displayName: string
  task: string
  status: 'running' | 'completed' | 'failed' | 'timeout'
  toolCalls: SubAgentToolCall[]
  startTime: number
  duration?: number
  error?: string
  // P0-B 新增字段
  output?: string
  thinking?: string
  expanded?: boolean
  userToggled?: boolean
  stageName?: string   // 所属 plan stage（用于完成后清理）
  isHidden?: boolean   // 标记隐藏（invisible stage 完成后）
}

// NEW-1: 子代理批量执行状态
export interface SubAgentBatchState {
  batchId: string
  count: number
  subSkills: string[]
  status: 'running' | 'completed'
  startTime: number
  succeeded?: number
  failed?: number
  durationMs?: number
}

// 客户端工具请求（Phase 7）
export interface ClientToolRequest {
  toolUseId: string
  toolName: string
  toolLabel: string
  toolInput: Record<string, unknown>
}

// Standalone 插件 iframe 执行请求（已弃用，改为通过 ToolCall.standalonePlugin 持久渲染）
// StandalonePluginRequest 已移除

// 计划交互式提问（MIG-2）
export interface PlanQuestionRequest {
  toolUseId: string
  question: string
  options?: string[]
  context?: string
  stepTitle?: string
}

// 计划步骤（Phase 8）
export interface PlanStepState {
  id: string
  title: string
  description: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped'
  output?: string
  dependsOn?: string[]
  skipReason?: string
  retryCount?: number
  maxRetries?: number
}

// 执行计划（Phase 8）
export interface ExecutionPlanState {
  planId: string
  title: string
  summary: string
  steps: PlanStepState[]
  status: 'created' | 'submitted' | 'approved' | 'rejected' | 'executing' | 'completed'
  /** 自定义阶段模式：所有阶段内容进入主内容区（非 deep-research 默认阶段） */
  customStages?: boolean
  visibleStages?: Record<string, boolean>  // stage_name → visible_to_user
}

// Aggregation@N 聚合进度
export interface AggregationPathInfo {
  index: number
  label: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'timeout'
  refs_count: number
  tool_calls: number
  duration_s: number
}

export interface AggregationProgress {
  done: number
  total: number
  phase: 'research' | 'synthesis' | 'complete'
  paths: AggregationPathInfo[]
}

// 学术引用
export interface Reference {
  id: string
  title: string
  authors: string[]
  year?: number
  source?: string
  url?: string
  doi?: string
  abstract?: string
  data_source?: string
  relevance_label?: string
}

// 工具名称到显示名称映射
export const TOOL_DISPLAY_NAMES: Record<string, string> = {
  'academic_search': '学术数据库',
  'wanfang_search': '万方数据库',
  'wanfang': '万方数据库',
  'semantic_scholar_search': 'Semantic Scholar',
  'semantic_scholar': 'Semantic Scholar',
  'arxiv_search': 'arXiv',
  'arxiv': 'arXiv',
  'kb_query': '本地知识库',
  'web_fetch': '互联网',
  'web_search': '网络搜索',
}

// 工具图标映射 (P2-A)
export const TOOL_ICONS: Record<string, string> = {
  'academic_search': '📚',
  'wanfang_search': '📚',
  'wanfang': '📚',
  'semantic_scholar_search': '📚',
  'semantic_scholar': '📚',
  'arxiv_search': '📚',
  'arxiv': '📚',
  'kb_query': '📖',
  'knowledge_query': '📖',
  'web_fetch': '🌐',
  'web_search': '🌐',
  'write_file': '💾',
  'file_write': '💾',
  'read_file': '📄',
  'delegate_task': '🤖',
  'generate_document': '📝',
  'generate_outline': '📝',
  'create_summary': '📝',
  'update_kb': '📖',
  'wikipedia_search': '🔍',
  'github_search_repos': '🔍',
  'github_get_repo': '🔀',
}

// 获取工具图标
export function getToolIcon(toolName: string): string {
  return TOOL_ICONS[toolName] || '🔧'
}

// 生成类工具列表
export const GENERATIVE_TOOLS = ['write_file', 'update_kb', 'create_summary', 'generate_outline']

// 判断是否为生成类工具
export function isGenerativeTool(toolName: string | undefined): boolean {
  if (!toolName) return false
  return GENERATIVE_TOOLS.includes(toolName)
}

// 生成文件信息
export interface GeneratedFileInfo {
  filename: string
  downloadUrl: string
  size: string
}

/** 文件生成相关的工具名称 */
export const FILE_GENERATION_TOOLS = ['write_file', 'file_write', 'sandbox_write_file', 'sandbox_bash', 'export_to_docx'] as const
export type FileGenerationTool = typeof FILE_GENERATION_TOOLS[number]

// 格式化耗时
export function formatStepDuration(step: ThinkingStep): string {
  if (isGenerativeTool(step.toolName) && step.totalDurationMs) {
    return `${(step.totalDurationMs / 1000).toFixed(1)}s`
  }
  if (step.durationMs) {
    return `${(step.durationMs / 1000).toFixed(1)}s`
  }
  return ''
}

// 用户可见的工具参数白名单
export const USER_VISIBLE_PARAMS: Record<string, string[]> = {
  'academic_search': ['query', 'year_from', 'year_to'],
  'wanfang_search': ['query', 'year_from', 'year_to'],
  'wanfang': ['query', 'year_from', 'year_to'],
  'semantic_scholar_search': ['query', 'year_from', 'year_to'],
  'semantic_scholar': ['query', 'year_from', 'year_to'],
  'arxiv_search': ['query'],
  'arxiv': ['query'],
  'kb_query': ['query'],
  'knowledge_query': ['query'],
  'web_fetch': ['url'],
  'web_search': ['query'],
  'write_file': ['filename'],
  'file_write': ['filename'],
}

// 参数显示名称映射
export const PARAM_DISPLAY_NAMES: Record<string, string> = {
  'query': '搜索',
  'url': '网址',
  'filename': '文件名',
  'year_from': '起始年份',
  'year_to': '截止年份',
}

// 过滤工具输入参数
export function filterToolInput(toolName: string, input: Record<string, unknown>): Record<string, string> {
  const allowedKeys = USER_VISIBLE_PARAMS[toolName] || ['query', 'url']
  const filtered: Record<string, string> = {}
  for (const [key, value] of Object.entries(input)) {
    if (allowedKeys.includes(key) && value !== undefined && value !== null && value !== '') {
      filtered[PARAM_DISPLAY_NAMES[key] || key] = String(value)
    }
  }
  // 白名单过滤后为空时，fallback 显示所有非空参数
  if (Object.keys(filtered).length === 0) {
    for (const [key, value] of Object.entries(input)) {
      if (value !== undefined && value !== null && value !== '') {
        const strVal = typeof value === 'object' ? JSON.stringify(value) : String(value)
        // 截断过长的值
        filtered[key] = strVal.length > 100 ? strVal.substring(0, 100) + '...' : strVal
      }
    }
  }
  return filtered
}

// 获取用户可见的工具调用步骤（排除 delegate_task + 已完成的 invisible 阶段）
export function getVisibleToolSteps(steps?: ThinkingStep[], plan?: ExecutionPlanState | null): ThinkingStep[] {
  if (!steps) return []
  // 收集已完成且 visible_to_user=false 的阶段名
  const hiddenStages = new Set<string>()
  if (plan?.visibleStages && plan.steps) {
    for (const step of plan.steps) {
      if (step.status === 'completed') {
        // 从 step.id 提取 stage name（格式 "stage_xxx" → "xxx"）
        const stageName = step.id.startsWith('stage_') ? step.id.slice(6) : step.id
        if (plan.visibleStages[stageName] === false) {
          hiddenStages.add(stageName)
        }
      }
    }
  }
  return steps.filter(s => {
    if (!s.isToolCall || s.toolName === 'delegate_task' || s.toolName === 'done') return false
    if ((s as any).isHidden) return false
    // 隐藏已完成的 invisible 阶段的工具调用
    if (hiddenStages.size > 0 && (s as any).stageName && hiddenStages.has((s as any).stageName)) return false
    return true
  })
}

// 格式化过滤后的参数
export function formatFilteredInput(toolName: string, input: Record<string, unknown>): string {
  const filtered = filterToolInput(toolName, input)
  return Object.entries(filtered)
    .map(([key, value]) => `${key}: "${value}"`)
    .join('，')
}

// 诊断报告数据
export interface DiagnosticReportData {
  rounds: number
  fixed: boolean
  should_escalate: boolean
  score_history: number[]
  token_consumed: number
  evidence_log: Array<{
    round: number
    category: string
    issue_count: number
    severity: 'error' | 'warning'
    over_modified?: boolean
  }>
}

// 同行评审条目
export interface PeerReviewItem {
  dimension: 'argument' | 'logic' | 'citation' | 'balance' | 'originality'
  score: number
  severity: 'critical' | 'important' | 'minor'
  location: string
  comment: string
  suggestion: string
}

// 同行评审数据
export interface PeerReviewData {
  overall_score: number
  items: PeerReviewItem[]
  has_critical: boolean
  parse_failed: boolean
  token_consumed: number
}

// 记忆分类
export type MemoryCategory = 'profile' | 'preferences' | 'entities' | 'events' | 'cases' | 'patterns'

// 用户长期记忆（Viking Memory 三级结构）
export interface MemoryItem {
  id: string
  memory: string              // 兼容字段 = overview
  category?: MemoryCategory   // 6 分类
  abstract?: string           // L0: 标签/索引
  overview?: string           // L1: 结构化摘要
  content?: string            // L2: 完整详情
  uri?: string                // memory://{user_id}/{category}/{uuid}
  created_at?: string
  updated_at?: string
  schema_version?: number
  metadata?: {
    merge_count?: number
    session_id?: string
    [key: string]: unknown
  }
}

// 分类配置（显示名 + 颜色）
export const MEMORY_CATEGORY_MAP: Record<MemoryCategory | 'all', { label: string; color: string }> = {
  all: { label: '全部', color: '#909399' },
  profile: { label: '身份', color: '#409EFF' },
  preferences: { label: '偏好', color: '#67C23A' },
  entities: { label: '实体', color: '#E6A23C' },
  events: { label: '事件', color: '#F56C6C' },
  cases: { label: '案例', color: '#9B59B6' },
  patterns: { label: '模式', color: '#00BCD4' },
}
