const apiBase = ''

function authHeaders(extra: Record<string, string> = {}) {
  const storedToken = localStorage.getItem('lea_workspace_token') || localStorage.getItem('token') || ''
  const token = storedToken.trim().replace(/^Bearer\s+/i, '')
  const tenantId = localStorage.getItem('lea_workspace_tenant_id') || ''
  return { ...extra, ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(tenantId ? { 'X-Selected-Tenant-ID': tenantId } : {}) }
}

// ApiError 比裸 Error 多带 status 和 code。加这两个字段是因为有些失败**不是**"重试一下"
// 能解决的：507 存储超限重试一万次都是 507，正确的反应是让用户去删文件。光靠 message
// 里的中文去 indexOf 匹配是脆的（文案一改就失效），所以后端给了 code，这里带上来。
//
// detail 放**结构化**的错误体。有些失败光有一句话不够用：共享 agent 被拒时后端返的是
// 一张"卡在哪几项资源上"的清单，把它压成字符串，用户就只能看到"有 3 项没共享"而不知道
// 是哪 3 项 —— 那条信息正是他唯一能拿来行动的东西。
export class ApiError extends Error {
  status: number
  code: string
  detail: unknown
  constructor(message: string, status: number, code = '', detail: unknown = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.detail = detail
  }
}

// 后端有**两种**错误体，因为是两种框架：
//
//   lea-agent（Go/gin）   → { "error": "…", "code": "…" }
//   lea-config / lea-rag（FastAPI） → { "detail": "…" } 或 { "detail": { message, … } }
//
// 老实现只认第一种，于是整个配置面（agent / skill / team / tool / mcp）的报错文案
// 全被吃掉，界面上一律显示 "409 Conflict" 这种只有状态码的空话 —— 后端明明把原因
// 写得很清楚。两种都认，顺带把结构化 detail 原样带出去。
function parseError(body: any): { message: string; code: string; detail: unknown } {
  if (!body || typeof body !== 'object') return { message: '', code: '', detail: null }
  const code = typeof body.code === 'string' ? body.code : ''
  if (typeof body.error === 'string' && body.error) return { message: body.error, code, detail: null }
  const d = body.detail
  if (typeof d === 'string') return { message: d, code, detail: null }
  if (d && typeof d === 'object') {
    // detail 是对象时，message 字段是给人看的那句；没有就退回 JSON，
    // 总比显示 "[object Object]" 强。
    const message = typeof d.message === 'string' ? d.message : JSON.stringify(d)
    return { message, code: code || (typeof d.code === 'string' ? d.code : ''), detail: d }
  }
  return { message: '', code, detail: null }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, {
    ...init,
    headers: authHeaders({ ...(init.headers as Record<string, string> | undefined) })
  })
  if (!response.ok) {
    let parsed = { message: '', code: '', detail: null as unknown }
    try {
      parsed = parseError(await response.json())
    } catch { /* plain text response */ }
    throw new ApiError(
      parsed.message || `${response.status} ${response.statusText}`,
      response.status, parsed.code, parsed.detail
    )
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export type SessionRow = {
  id: string
  title: string
  message_count: number
  last_active_at: string
  created_at: string
  is_archived: boolean
  project_id?: number | null
  agent_id?: number | null
}

export type ProjectItem = { id: number; name: string; description?: string; session_count?: number; is_archived?: boolean }

export type TenantItem = { id: string; type?: string; role?: string }

export type MessageRow = {
  id: number
  role: 'user' | 'assistant'
  content: string
  created_at: string
  extra?: Record<string, unknown>
}

export type SessionAttachment = {
  kind: 'skill' | 'mcp_server' | 'tool' | 'kb'
  ref_id: number
  name?: string
  display_name?: string
  description?: string
  category?: string
  available?: boolean
}

export type WorkspaceFile = {
  path: string
  size: number
  mtime: number
  in_output: boolean
}

export type AgentInfo = {
  id: number
  name: string
  description?: string
  soul_prompt?: string
  category?: string
  avatar?: string
  model?: string
  is_mine?: boolean
  // 乐观并发控制用——保存时原样带回去当 expected_updated_at，见
  // updateAgent 的说明。没有这个字段（老缓存数据）就不校验。
  updated_at?: string
}

export type TeamInfo = {
  id: number
  agent_id: number
  name?: string
  description?: string
  strategy?: string
  decomposer_prompt?: string
  synthesizer_prompt?: string
  max_subtasks?: number
  updated_at?: string
}

export type TeamMember = {
  id: number
  team_id: number
  member_agent_id: number
  role?: string
  name?: string
}

export type SkillMarketItem = {
  id: number
  name: string
  display_name?: string
  description?: string
  category?: string
  version?: string
  visibility?: string
  enabled?: boolean
  is_own?: boolean
  can_edit?: boolean
  is_builtin?: boolean
  pending_publish?: string | null
}

export type McpMarketItem = {
  id: number
  name: string
  description?: string
  category?: string
  transport?: string
  enabled?: boolean
  connection_status?: string
  owner_scope?: string
  endpoint?: string
  has_auth?: boolean
  tool_filter?: string[]
  last_error?: string
  owner_user_id?: string | null
}

export type ToolMarketItem = {
  id: number
  name: string
  display_name?: string
  description?: string
  category?: string
  execution_type?: string
  enabled?: boolean
  owner_scope?: string
  owner_user_id?: string | null
  is_builtin?: boolean
}

export type KnowledgeBaseItem = {
  id: number
  name: string
  description?: string
  visibility?: string
  document_count?: number
  word_count?: number
  enabled?: boolean
  retrieval_mode?: string
  retrieval_top_k?: number
  score_threshold?: number
  embedding_model?: string
  embedding_dim?: number
  reranker_model?: string
  reranker_enabled?: boolean
  vlm_model?: string
  vlm_enabled?: boolean
  multimodal?: boolean
  updated_at?: string
}

export type KnowledgeDocument = {
  id: number
  kb_id: number
  filename: string
  file_type?: string
  file_size?: number
  status?: string
  chunk_count?: number
  error_message?: string
}

export type KnowledgeSearchHit = {
  content: string
  score: number
  kb_id: number
  document_id?: number
  source_filename?: string
}

// UserFile 是"我的文件"列表里的一行。上传件和模型交付的成品合并在一个列表里 ——
// 用户不关心这个区别，他要的是"我的文件"。kind 保留是因为删除/导入要知道去哪张表。
export type UserFile = {
  kind: 'upload' | 'artifact'
  id: number
  filename: string
  size: number
  media_type?: string
  file_type?: string
  session_id?: string
  created_at?: string
  // 成品才有：模型交付时写的标题和说明，用户几周后靠这两句找回它
  title?: string
  description?: string
  // stored=false 意味着原件没保住（OBS 故障，或交付时配额已满的软放行）。
  // 这种行不能下载、也不能导入，按钮要置灰 —— 让用户点了才拿到 410 是最差的做法。
  stored?: boolean
  can_import?: boolean
}

export type StorageUsage = {
  upload_bytes: number
  artifact_bytes: number
  total_bytes: number
  // quota_bytes=0 表示不限，这时 percent 恒为 0，不要画进度条
  quota_bytes: number
  upload_count: number
  artifact_count: number
  percent: number
}

export type ChatEvent = { type: string; data: unknown; id?: string }

export const listSessions = () => request<{ sessions: SessionRow[] }>('/api/sessions')
export const listProjects = () => request<{ projects: ProjectItem[] }>('/api/projects')
export const createProject = (payload: { name: string; description?: string }) => request<ProjectItem>('/api/projects', {
  method: 'POST', body: JSON.stringify(payload), headers: { 'Content-Type': 'application/json' }
})
export const updateProject = (projectId: number, payload: { name?: string; description?: string }) => request<ProjectItem>(`/api/projects/${projectId}`, {
  method: 'PUT', body: JSON.stringify(payload), headers: { 'Content-Type': 'application/json' }
})
export const deleteProject = (projectId: number) => request<void>(`/api/projects/${projectId}`, { method: 'DELETE' })
export const archiveProject = (projectId: number) => request<ProjectItem>(`/api/projects/${projectId}/archive`, { method: 'POST' })
export const listTenants = () => request<{ items: TenantItem[] }>('/api/tenants')
export const getMessages = (id: string) => request<{ messages: MessageRow[] }>(`/api/sessions/${id}/messages`)
export const setSessionActiveAgent = (sessionId: string, agentId: number) => request<{ session_id: string; agent_id: number; active: boolean }>(`/api/sessions/${sessionId}/active-agent`, {
  method: 'PUT', body: JSON.stringify({ agent_id: agentId }), headers: { 'Content-Type': 'application/json' }
})
export const createSession = (agentId = 0, projectId?: number) => request<{ session_id: string }>('/api/sessions', {
  method: 'POST', body: JSON.stringify({ agent_id: agentId, ...(projectId ? { project_id: projectId } : {}) }), headers: { 'Content-Type': 'application/json' }
})
export const deleteSession = (id: string) => request<{ ok: boolean }>(`/api/sessions/${id}`, { method: 'DELETE' })

// stopGeneration 是**真的**让服务端停下来的那一条，跟关掉浏览器这头的连接不是一回事。
//
// AbortController 只断本地的 fetch：服务端那一轮照跑、照烧 token、照写库，用户下次
// 打开会话还会看到那条它以为自己取消掉的回复。所以"停止"必须两件事都做 ——
// 先 POST 告诉服务端，再 abort 本地连接。
//
// forwarded=true 表示这个请求落到的副本不持有该会话，已经经控制面转给持有者了
// （多副本下是常态，不是异常）。
export const stopGeneration = (sessionId: string) =>
  request<{ session_id: string; stopped: boolean; forwarded?: boolean }>(
    `/api/chat/stop/${sessionId}`, { method: 'POST' }
  )

// getTurnStatus 回答"断流之后那一轮还在跑吗"。跨副本有效（查的是控制面，不是本地内存）。
export const getTurnStatus = (sessionId: string) =>
  request<{ session_id: string; running: boolean }>(`/api/chat/status/${sessionId}`)
export const listAgents = () => request<{ items?: AgentInfo[] } | AgentInfo[]>('/api/user/agents')
export const createAgent = (payload: { name: string; description: string; category: string; soul_prompt: string; model?: string }) => request<AgentInfo>('/api/user/agents', {
  method: 'POST', body: JSON.stringify(payload), headers: { 'Content-Type': 'application/json' }
})
// expected_updated_at：乐观并发控制，传上一次读到的 updated_at 原样带回去。
// 省略=不检查（旧调用点兼容）。带了但跟数据库当前值不一致时后端返 409——
// 后端只用了 HTTPException(409, "纯字符串")，没有专门的 code，调用方按
// error.status === 409 分支，不要指望 error.code。
export const updateAgent = (agentId: number, payload: { name?: string; description?: string; category?: string; soul_prompt?: string; model?: string; expected_updated_at?: string }) => request<AgentInfo>(`/api/user/agents/${agentId}`, {
  method: 'PUT', body: JSON.stringify(payload), headers: { 'Content-Type': 'application/json' }
})
export const deleteAgent = (agentId: number) => request<{ message: string }>(`/api/user/agents/${agentId}`, { method: 'DELETE' })

// ShareBlocker：挡住共享的那一项资源，或一条软提示。两者同形，区别只在后端把它放进
// blockers 还是 warnings —— blockers 硬拦（私有工具/技能/MCP server/私有团成员，
// 共享出去等于把私有东西泄给全租户），warnings 只提示（私有知识库：别人用这个 agent
// 时检索不到，答案会跟作者自己用的时候不一样，但不构成泄露）。
export type ShareBlocker = { kind: string; id: number; name: string; reason: string }

export type ShareCheck = {
  agent_id: number
  already_shared: boolean
  can_share: boolean
  blockers: ShareBlocker[]
  warnings: ShareBlocker[]
}

// checkAgentShare 是**点之前**先问一次，不是点之后才知道。
//
// 后端专门给了这个只读端点就是为这个（见 check_share 的 docstring）：共享是个
// 对别人可见的动作，让用户点一下再吃 409 很糟；把清单先摊开，他才知道要先去
// 共享哪几个工具。
export const checkAgentShare = (agentId: number) =>
  request<ShareCheck>(`/api/user/agents/${agentId}/share-check`)

// shareAgent 把私有 agent 升到租户共享档。幂等（已共享再调不报错）。
//
// ⚠️ warnings **成功时也会返回**，别丢：共享成功了，但"里面挂着一个私有知识库"
// 这件事用户仍然该知道。只在失败路径上显示 warnings 等于永远没人看见它。
// 被拦时是 409，清单在 ApiError.detail.blockers 里（见 parseError）。
export const shareAgent = (agentId: number) =>
  request<{ agent: AgentInfo; warnings: ShareBlocker[] }>(`/api/user/agents/${agentId}/share`, { method: 'POST' })

// unshareAgent 撤回共享，还给原作者。有它共享才不是单向门。
// created_by 为空的行（平台种子数据）会被 400 拒绝 —— 撤回它等于把预置 agent
// 塞进当前用户名下。
export const unshareAgent = (agentId: number) =>
  request<{ agent: AgentInfo }>(`/api/user/agents/${agentId}/unshare`, { method: 'POST' })
export type AgentBindings = {
  skills: { skill_id: number; enabled?: boolean }[]
  knowledge_bases: { kb_id: number; enabled?: boolean }[]
  tools: { tool_id: number; enabled?: boolean }[]
  mcp_servers: { mcp_server_id: number; enabled?: boolean }[]
}
export const listAgentBindings = (agentId: number) => request<AgentBindings>(`/api/user/agents/${agentId}/bindings`)
export const bindAgentSkill = (agentId: number, skillId: number) => request<unknown>(`/api/user/agents/${agentId}/skills`, {
  method: 'POST', body: JSON.stringify({ skill_id: skillId }), headers: { 'Content-Type': 'application/json' }
})
export const bindAgentKnowledgeBase = (agentId: number, kbId: number) => request<unknown>(`/api/user/agents/${agentId}/kbs`, {
  method: 'POST', body: JSON.stringify({ kb_id: kbId }), headers: { 'Content-Type': 'application/json' }
})
export const bindAgentTool = (agentId: number, toolId: number) => request<unknown>(`/api/user/agents/${agentId}/tools`, {
  method: 'POST', body: JSON.stringify({ tool_id: toolId }), headers: { 'Content-Type': 'application/json' }
})
export const bindAgentMcp = (agentId: number, mcpServerId: number) => request<unknown>(`/api/user/agents/${agentId}/mcp`, {
  method: 'POST', body: JSON.stringify({ mcp_server_id: mcpServerId }), headers: { 'Content-Type': 'application/json' }
})
export const unbindAgentSkill = (agentId: number, skillId: number) => request<void>(`/api/user/agents/${agentId}/skills/${skillId}`, { method: 'DELETE' })
export const unbindAgentKnowledgeBase = (agentId: number, kbId: number) => request<void>(`/api/user/agents/${agentId}/kbs/${kbId}`, { method: 'DELETE' })
export const unbindAgentTool = (agentId: number, toolId: number) => request<void>(`/api/user/agents/${agentId}/tools/${toolId}`, { method: 'DELETE' })
export const unbindAgentMcp = (agentId: number, mcpServerId: number) => request<void>(`/api/user/agents/${agentId}/mcp/${mcpServerId}`, { method: 'DELETE' })
export const listTeams = () => request<{ items: TeamInfo[] }>('/api/user/teams?page=1&page_size=50')
export const createTeam = (payload: { agent_id: number; strategy: string; decomposer_prompt: string; synthesizer_prompt: string; max_subtasks: number }) => request<TeamInfo>('/api/user/teams', {
  method: 'POST', body: JSON.stringify(payload), headers: { 'Content-Type': 'application/json' }
})
export const updateTeam = (teamId: number, payload: { strategy?: string; decomposer_prompt?: string; synthesizer_prompt?: string; max_subtasks?: number; expected_updated_at?: string }) => request<TeamInfo>(`/api/user/teams/${teamId}`, {
  method: 'PUT', body: JSON.stringify(payload), headers: { 'Content-Type': 'application/json' }
})
export const deleteTeam = (teamId: number) => request<void>(`/api/user/teams/${teamId}`, { method: 'DELETE' })
export const listTeamMembers = (teamId: number) => request<{ items: TeamMember[] }>(`/api/user/teams/${teamId}/members`)
export const addTeamMember = (teamId: number, payload: { member_agent_id: number; role: string; name: string }) => request<TeamMember>(`/api/user/teams/${teamId}/members`, {
  method: 'POST', body: JSON.stringify(payload), headers: { 'Content-Type': 'application/json' }
})
export const removeTeamMember = (teamId: number, memberId: number) => request<void>(`/api/user/teams/${teamId}/members/${memberId}`, { method: 'DELETE' })
export const listKnowledgeBases = () => request<{ knowledge_bases: KnowledgeBaseItem[] }>('/api/user/knowledge-bases?page=1&size=50')
export const createKnowledgeBase = (payload: {
  name: string
  description: string
  visibility: string
  embedding_model?: string
  reranker_model?: string
  reranker_enabled?: boolean
  vlm_model?: string
  vlm_enabled?: boolean
  multimodal?: boolean
}) => request<KnowledgeBaseItem>('/api/user/knowledge-bases', {
  method: 'POST', body: JSON.stringify(payload), headers: { 'Content-Type': 'application/json' }
})
// embedding_model/embedding_dim 类型层面允许传，但后端只在 document_count===0
// 时真的接受，否则 400——前端按 document_count 禁用输入框，不在类型层面禁止。
export const updateKnowledgeBase = (kbId: number, payload: {
  name?: string
  description?: string
  visibility?: string
  retrieval_mode?: string
  retrieval_top_k?: number
  score_threshold?: number
  enabled?: boolean
  embedding_model?: string
  embedding_dim?: number
  reranker_model?: string
  reranker_enabled?: boolean
  vlm_model?: string
  vlm_enabled?: boolean
  multimodal?: boolean
  expected_updated_at?: string
}) => request<KnowledgeBaseItem>(`/api/user/knowledge-bases/${kbId}`, {
  method: 'PUT', body: JSON.stringify(payload), headers: { 'Content-Type': 'application/json' }
})
export const deleteKnowledgeBase = (kbId: number) => request<{ message: string }>(`/api/user/knowledge-bases/${kbId}`, { method: 'DELETE' })
export const listKnowledgeDocuments = (kbId: number) => request<{ documents: KnowledgeDocument[] }>(`/api/user/knowledge-bases/${kbId}/documents?page=1&size=50`)
export const uploadKnowledgeDocument = (kbId: number, file: File) => {
  const body = new FormData()
  body.append('file', file)
  return request<KnowledgeDocument>(`/api/user/knowledge-bases/${kbId}/documents`, { method: 'POST', body })
}
export const deleteKnowledgeDocument = (kbId: number, documentId: number) => request<{ message: string }>(`/api/user/knowledge-bases/${kbId}/documents/${documentId}`, { method: 'DELETE' })
export const searchKnowledgeBase = (kbId: number, query: string, limit = 5) => request<{ results: KnowledgeSearchHit[]; total: number }>(`/api/user/knowledge-bases/${kbId}/search`, {
  method: 'POST', body: JSON.stringify({ query, limit }), headers: { 'Content-Type': 'application/json' }
})
export const listSkills = (visibility?: 'private' | 'tenant' | 'global') => request<{ skills: SkillMarketItem[] }>(`/api/user/skills?page=1&size=50${visibility ? `&visibility=${visibility}` : ''}`)
export const updateSkill = (skillId: number, payload: { display_name?: string; description?: string; category?: string; system_prompt?: string; model_tier?: string; allowed_tools?: string[]; enabled?: boolean }) => request<SkillMarketItem>(`/api/user/skills/${skillId}`, {
  method: 'PUT', body: JSON.stringify(payload), headers: { 'Content-Type': 'application/json' }
})
export const deleteSkill = (skillId: number) => request<{ message: string }>(`/api/user/skills/${skillId}`, { method: 'DELETE' })
export const publishSkill = (skillId: number, payload: { target_scope: 'tenant' | 'global'; note?: string }) => request<unknown>(`/api/user/skills/${skillId}/publish`, {
  method: 'POST', body: JSON.stringify(payload), headers: { 'Content-Type': 'application/json' }
})
export type ChatModel = { name: string; provider: string; is_default: boolean; context_window: number }

// listChatModels 给模型选择器供数。
//
// 在此之前 agents.model 只能手打，而打错**不报错** —— 后端匹配不到 provider 就
// 静默回落到默认那个（lea-config internal.py），用户看到的是"这专家答得不对劲"，
// 没人会往拼写上想。
//
// 列表可能是空的（租户没配 llm_providers）。那时候选择器必须仍然允许手输，
// 否则连模型都设不了，比没有列表更糟。
export const listChatModels = () => request<{ items: ChatModel[] }>('/api/user/llm-models')

export type RetrievalModel = { name: string; model_type: string; model_name: string; dimension?: number; is_default: boolean }

// listRetrievalModels 给知识库建库/改库表单的 embedding/reranker/vlm 下拉供数，
// 跟 listChatModels 是同一个问题的同一个修法：自由文本打错字会静默落到默认模型。
export const listRetrievalModels = (modelType?: 'embedding' | 'reranker' | 'vlm') =>
  request<{ items: RetrievalModel[] }>(`/api/user/retrieval-models${modelType ? `?model_type=${modelType}` : ''}`)

export type PublishRequest = {
  id: number
  skill_id: number
  skill_name: string
  tenant_id: string
  requested_by: string
  target_scope: 'tenant' | 'global'
  status: 'pending' | 'approved' | 'rejected'
  note?: string | null
  review_note?: string | null
  reviewed_by?: string | null
  reviewed_at?: string | null
  created_at?: string
}

// listPublishRequests 一条 URL 两种语义，后端按调用者角色自己分流：
//
//   普通用户   → 我自己提的申请（"批了没、驳回理由是什么"）
//   租户管理员 → 本租户待审的 tenant 档
//   平台管理员 → 全平台待审的 global 档
//
// 所以前端不需要（也不该）自己判断角色去拼不同的查询 —— 那只会跟后端的判据分叉。
// 界面上换个标题就行。
//
// 没有这条的时候 publishSkill 是个**黑洞**：用户提交完就再也看不到任何反馈，
// 连"被驳回了"都不知道。
export const listPublishRequests = (status: 'pending' | 'approved' | 'rejected' = 'pending') =>
  request<{ requests: PublishRequest[] }>(`/api/user/skills/publish-requests?status=${status}`)

// reviewPublishRequest 通过/驳回。审核权按**目标层**分，不看谁提的：
// global 档要平台管理员，tenant 档租户管理员即可。越权返 403，重复处理返 409。
export const reviewPublishRequest = (requestId: number, payload: { approve: boolean; note?: string }) =>
  request<{ status: string; skill_id: number }>(`/api/user/skills/publish-requests/${requestId}/review`, {
    method: 'POST', body: JSON.stringify(payload), headers: { 'Content-Type': 'application/json' }
  })

export const listMcpServers = () => request<{ items: McpMarketItem[] }>('/api/user/mcp-servers?page=1&size=50')
export const createMcpServer = (payload: { name: string; description: string; category: string; endpoint: string; transport: string; auth_config?: Record<string, string>; tool_filter?: string[] }) => request<McpMarketItem>('/api/user/mcp-servers/mine', {
  method: 'POST', body: JSON.stringify(payload), headers: { 'Content-Type': 'application/json' }
})
export const listTools = () => request<{ items: ToolMarketItem[] }>('/api/user/tools?page=1&page_size=50')
export const toggleMcpServer = (id: number, enabled: boolean) => request<McpMarketItem>(`/api/user/mcp-servers/${id}/enable`, {
  method: 'PATCH', body: JSON.stringify({ enabled }), headers: { 'Content-Type': 'application/json' }
})
export const syncMcpServer = (id: number) => request<{ synced: number; tools: string[] }>(`/api/user/mcp-servers/${id}/sync`, { method: 'POST' })
export const updateMcpServer = (id: number, payload: { name?: string; description?: string; category?: string; endpoint?: string; transport?: string; auth_config?: Record<string, string>; tool_filter?: string[]; enabled?: boolean }) => request<McpMarketItem>(`/api/user/mcp-servers/${id}`, {
  method: 'PUT', body: JSON.stringify(payload), headers: { 'Content-Type': 'application/json' }
})
export const deleteMcpServer = (id: number) => request<void>(`/api/user/mcp-servers/${id}`, { method: 'DELETE' })
export const uploadSkillBundle = async (file: File, version: string, category = 'general', changeLog = '') => {
  const body = new FormData()
  body.append('file', file)
  body.append('version', version)
  body.append('category', category)
  body.append('change_log', changeLog)
  return request<SkillMarketItem>('/api/user/skills/upload', { method: 'POST', body })
}
export const createTool = (payload: {
  name: string
  display_name: string
  description: string
  category: string
  execution_type: 'api'
  schema_json: Record<string, unknown>
  endpoint?: string
}) => request<ToolMarketItem>('/api/user/tools', {
  method: 'POST', body: JSON.stringify(payload), headers: { 'Content-Type': 'application/json' }
})
export const updateTool = (id: number, payload: { display_name?: string; description?: string; category?: string; execution_type?: 'api' | 'mcp'; schema_json?: Record<string, unknown>; endpoint?: string; enabled?: boolean }) => request<ToolMarketItem>(`/api/user/tools/${id}`, {
  method: 'PUT', body: JSON.stringify(payload), headers: { 'Content-Type': 'application/json' }
})
export const deleteTool = (id: number) => request<void>(`/api/user/tools/${id}`, { method: 'DELETE' })
// listWorkspaceFiles 列当前会话沙箱工作区的**文件树**（后端返的是带完整相对路径的
// 扁平列表，目录结构在 path 里）。
//
// showHidden 对应后端的 ?show_hidden=true。lea-tools 默认会剪掉 dist/build/.git
// 这类目录 —— 对"看看模型写了什么"是对的，但模型真去跑了一次前端构建时，产物全在
// dist 下，不给开关的话用户会看到一个空工作区，并且没有任何线索说明东西被藏了。
//
// store 和 truncated 都要用：
//   store === 'pending' —— 会话还没落库（POST /sessions 只建内存 id，首条消息才写库），
//     跟"沙箱是空的"是两回事，文案不能一样。
//   truncated —— 列表被截断了。不显示的话用户看到的是一棵**看起来完整的残树**，
//     而"我的文件哪去了"这种问题最难查。
export const listWorkspaceFiles = (sessionId: string, showHidden = false) =>
  request<{ files: WorkspaceFile[]; store?: string; truncated?: boolean }>(
    `/api/files/list?session_id=${encodeURIComponent(sessionId)}${showHidden ? '&show_hidden=true' : ''}`
  )
export const listSessionAttachments = (sessionId: string) => request<{ attachments: SessionAttachment[] }>(`/api/sessions/${sessionId}/attachments?resolve=true`)
export const attachSessionResource = (sessionId: string, kind: SessionAttachment['kind'], refId: number) => request<{ attached: boolean }>(`/api/sessions/${sessionId}/attachments`, {
  method: 'POST', body: JSON.stringify({ kind, ref_id: refId }), headers: { 'Content-Type': 'application/json' }
})
export const detachSessionResource = (sessionId: string, resource: SessionAttachment) => request<{ attached: boolean }>(`/api/sessions/${sessionId}/attachments/${resource.kind}/${resource.ref_id}`, { method: 'DELETE' })

// ── 用户文件库：跨会话的那一层 ────────────────────────────────
// 这几条挂在 /api/user 下而不是 /api/sessions/:id 下，因为这个库的意义就是跨会话。
export const getUserStorage = () => request<StorageUsage>('/api/user/storage')
export const listUserFiles = (kind?: 'upload' | 'artifact', limit = 50, offset = 0) =>
  request<{ files: UserFile[]; limit: number; offset: number }>(
    `/api/user/files?limit=${limit}&offset=${offset}${kind ? `&kind=${kind}` : ''}`
  )
export const deleteUserFile = (kind: 'upload' | 'artifact', id: number) =>
  request<{ status: string; deleted: boolean; filename: string; object_deleted: boolean }>(
    `/api/user/files/${kind}/${id}`, { method: 'DELETE' }
  )
// 把文件库里的一个文件物化进某个会话。返回的 attachment_id 是**唯一必须用的字段**：
// 下一条消息的 attachment_ids 带上它，模型才知道这个文件存在（跟上传后那条路完全一样）。
export const importFileToSession = (sessionId: string, kind: 'upload' | 'artifact', id: number) =>
  request<{
    status: string
    attachment_id: number
    filename: string
    file_type: string
    media_type: string
    file_size: number
    staged_name: string
    sandbox_path: string
    has_text: boolean
  }>(`/api/sessions/${sessionId}/files/import`, {
    method: 'POST', body: JSON.stringify({ kind, id }), headers: { 'Content-Type': 'application/json' }
  })

export const publishWorkspaceFile = (sessionId: string, path: string, title?: string) =>
  request<{ status: string; id: number; filename: string; title: string; size: number }>(
    `/api/sessions/${sessionId}/files/publish`, {
      method: 'POST',
      body: JSON.stringify({ path, ...(title ? { title } : {}) }),
      headers: { 'Content-Type': 'application/json' }
    }
  )

export async function downloadWorkspaceFile(sessionId: string, path: string) {
  const response = await fetch(`/api/files/download?session_id=${encodeURIComponent(sessionId)}&path=${encodeURIComponent(path)}`, {
    headers: authHeaders()
  })
  if (!response.ok) {
    let detail = ''
    try { detail = (await response.json()).error || '' } catch { /* plain text response */ }
    throw new Error(detail || `下载失败 (${response.status})`)
  }
  const blob = await response.blob()
  const contentType = response.headers.get('Content-Type') || blob.type || 'application/octet-stream'
  return { blob, contentType }
}

export function uploadAttachment(file: File, sessionId?: string) {
  const body = new FormData()
  body.append('file', file)
  if (sessionId) body.append('session_id', sessionId)
  return request<{ id: number; filename: string; file_type: string; file_size: number; downloadable?: boolean }>('/api/chat/attachments/upload', { method: 'POST', body })
}

export async function downloadAttachment(id: number) {
  const response = await fetch(`/api/chat/attachments/${id}/download`, { headers: authHeaders() })
  if (!response.ok) throw new Error(`附件下载失败 (${response.status})`)
  return { blob: await response.blob(), contentType: response.headers.get('Content-Type') || 'application/octet-stream' }
}

export async function sendMessageStream(
  payload: {
    session_id?: string; agent_id: number; project_id?: number; message: string
    attachment_ids?: number[]; plan_mode?: boolean; plan_action?: string
    // 完整替换语义——每次都发这条会话现在该有的完整清单（已挂载 + 这一轮新选的），
    // 不是"这次要新增的增量"。省略这个字段=不动现有挂载（后端按 nil 处理，
    // 兼容不知道这个字段的旧客户端），带了就整体替换，见 lea-agent 里
    // SendRequest.Attachments 的说明。
    attachments?: { kind: SessionAttachment['kind']; ref_id: number }[]
  },
  onEvent: (event: ChatEvent) => void
) {
  const controller = new AbortController()
  const response = await fetch(`${apiBase}/api/chat/stream`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json', Accept: 'text/event-stream' }),
    body: JSON.stringify(payload),
    signal: controller.signal
  })
  if (!response.ok || !response.body) {
    let detail = ''
    let code = ''
    try {
      const body = await response.json()
      detail = body.error || ''
      code = body.code || ''
    } catch { /* plain text response */ }
    // 用 ApiError（带 code）而不是裸 Error——跟 resumeMessageStream 保持一致，
    // 否则 session_archived（会话已被删除）这类需要专门分支处理的错误，
    // 调用方只能拿字符串去匹配中文文案，文案一改就失效。
    throw new ApiError(detail || `stream failed: ${response.status}`, response.status, code)
  }

  void consumeSse(response.body, onEvent, controller.signal)
  return () => controller.abort()
}

// resumeMessageStream 是**唯一的重连入口**（见 router/resume.go 顶部）。
//
// 主链路 POST /chat/stream 把"发消息"和"收流"压在一条连接里，所以断了不能重发 ——
// 重发一个 POST 会真的再问模型一遍。要接回去只能走这条 GET，把最后收到的 SSE id
// 用 ?from= 报回去，服务端补齐之后的事件再接上直播。
//
// `from` 传空串 = 从头补。传 id 的语义是**开区间**（"我已经有 id 了，给我之后的"）。
//
// 410 + code=log_expired 是一个**要分开处理**的结果，不是普通错误：它表示事件日志
// 过期/没写过，流补不回来了，但**对话内容本身在库里**。正确反应是回落到
// getMessages() 拉一次历史，而不是给用户报错。所以这里原样抛 ApiError（带 code），
// 让调用方按 code 分支，别去 indexOf 中文文案。
export async function resumeMessageStream(
  sessionId: string,
  from: string,
  onEvent: (event: ChatEvent) => void
) {
  const controller = new AbortController()
  const query = from ? `?from=${encodeURIComponent(from)}` : ''
  const response = await fetch(`${apiBase}/api/chat/stream/${sessionId}${query}`, {
    headers: authHeaders({ Accept: 'text/event-stream' }),
    signal: controller.signal
  })
  if (!response.ok || !response.body) {
    let detail = ''
    let code = ''
    try {
      const body = await response.json()
      detail = body.error || ''
      code = body.code || ''
    } catch { /* plain text response */ }
    throw new ApiError(detail || `resume failed: ${response.status}`, response.status, code)
  }

  void consumeSse(response.body, onEvent, controller.signal)
  return () => controller.abort()
}

// STREAM_CLOSED 是一个**本地合成的**事件，服务端从不发它。
//
// 它补的是一个很难认的洞：连接可以在**没有 done 事件**的情况下结束（进程被换、
// 中间层超时、网络断），而这两种结束在这里长得一模一样 —— 老实现对"读到 EOF"
// 直接 break，什么都不发。于是调用方的 streamRunning 永远留在 true：界面一直显示
// "生成中"，输入框因为 `if (streamRunning) return` 永久禁用，只能刷新页面。
//
// 更要命的是**那一轮在服务端可能还在跑**（我们只是断了这头），所以正确反应不是
// 报错，而是去问一次 status 再决定续传还是拉历史。把"流结束了"这件事显式说出来，
// 调用方才有机会做这个判断。
//
// 主动 abort 不发它 —— 那是调用方自己要停的，它清楚发生了什么。
export const STREAM_CLOSED = 'stream_closed'

async function consumeSse(body: ReadableStream<Uint8Array>, onEvent: (event: ChatEvent) => void, signal: AbortSignal) {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    while (!signal.aborted) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let split = -1
      while ((split = buffer.indexOf('\n\n')) >= 0) {
        const chunk = buffer.slice(0, split)
        buffer = buffer.slice(split + 2)
        const event = parseSse(chunk)
        if (event) onEvent(event)
      }
    }
  } catch (error) {
    if ((error as Error).name === 'AbortError') return
    // 不再自己合成 done —— done 的含义是"这一轮真的结束了"，而这里只知道
    // "这条连接断了"。混同的后果是调用方把一次网络抖动当成模型答完了。
    onEvent({ type: STREAM_CLOSED, data: (error as Error).message || '连接中断' })
    return
  }
  if (!signal.aborted) onEvent({ type: STREAM_CLOSED, data: null })
}

function parseSse(chunk: string): ChatEvent | null {
  let type = ''
  let id = ''
  const lines: string[] = []
  for (const line of chunk.split('\n')) {
    if (line.startsWith('id:')) id = line.slice(3).trim()
    else if (line.startsWith('event:')) type = line.slice(6).trim()
    else if (line.startsWith('data:')) lines.push(line.slice(5).trim())
  }
  if (!type) return null
  const raw = lines.join('\n')
  let data: unknown = raw
  if (raw.startsWith('{') || raw.startsWith('[')) {
    try { data = JSON.parse(raw) } catch { /* keep text */ }
  }
  return { type, data, id }
}
