import axios from 'axios'

export class ApiError extends Error {
  constructor(public status: number, message: string, public detail?: unknown) {
    super(message)
  }
}

export const authStore = {
  get token() { return localStorage.getItem('lea_console_token') || '' },
  get tenantId() { return localStorage.getItem('lea_console_tenant_id') || '' },
  save(token: string, tenantId: string) {
    localStorage.setItem('lea_console_token', token.trim().replace(/^Bearer\s+/i, ''))
    localStorage.setItem('lea_console_tenant_id', tenantId.trim())
  },
  clear() {
    localStorage.removeItem('lea_console_token')
    localStorage.removeItem('lea_console_tenant_id')
  },
}

const client = axios.create({ baseURL: import.meta.env.VITE_API_BASE_URL || '', timeout: 60000 })
client.interceptors.request.use((config) => {
  if (authStore.token) config.headers.Authorization = `Bearer ${authStore.token}`
  if (authStore.tenantId) config.headers['X-Selected-Tenant-ID'] = authStore.tenantId
  return config
})
client.interceptors.response.use(
  (response) => response.data,
  (error) => {
    const status = error.response?.status || 0
    const body = error.response?.data
    const detail = body?.detail
    const message = body?.error || (typeof detail === 'string' ? detail : detail?.message) || error.message || '请求失败'
    return Promise.reject(new ApiError(status, message, detail))
  },
)

export type ConsoleContext = { user_id: string; tenant_id: string; role: 'admin' | 'platform_admin'; capabilities: string[] }
export type PageResult<T> = { items?: T[]; requests?: T[]; total: number; page: number; size: number }
export type PublicationRequest = Record<string, any> & { id: number; resource_type: string; resource_id: string; resource_version: string; tenant_id: string; target_scope: string; status: string; requested_by: string; request_note: string; review_note: string; risk_snapshot: Record<string, unknown>; resource_snapshot: Record<string, unknown>; created_at: string }
export type Publication = { id: number; resource_type: string; resource_id: string; tenant_id: string; scope: string; status: string; published_by: string; updated_by: string; updated_at: string }
export type Provider = { id: number; tenant_id: string; name: string; base_url: string; has_api_key: boolean; models: string[]; model_limits: Record<string, number>; is_default: boolean; enabled: boolean; created_at: string }
export type Run = Record<string, any>
export type DocumentJob = { id: number; kb_id: number; tenant_id: string; filename: string; status: string; error_message: string; celery_task_id: string; file_size: number; chunk_count: number; created_at: string; updated_at: string }
export type AuditEvent = { event_id: string; tenant_id: string; actor_user_id: string; actor_role: string; action: string; resource_type: string; resource_id: string; result: string; reason: string; created_at: string; before_summary: object; after_summary: object }
export type Agent = {
  id: number
  name: string
  description: string
  avatar: string
  welcome_message: string
  soul_prompt: string
  category: string
  is_system: boolean
  is_mine: boolean
  is_enabled: boolean
  sort_order: number
  model: string
  temperature: number
  max_turns: number
  max_tokens: number
  kb_mode: string
  memory_mode: string
  timeout_seconds: number
  example_questions: string[]
  updated_at: string | null
  publication_status: string
  allowed_actions: string[]
  tenant_id?: string
  owner_scope?: string
  owner_user_id?: string | null
  created_by?: string | null
  visibility?: 'private' | 'tenant' | 'global'
  can_edit?: boolean
}
export type AgentBindings = {
  skills: Array<{ skill_id: number; priority: number; enabled: boolean }>
  knowledge_bases: Array<{ kb_id: number; strategy: string; top_k: number; enabled: boolean }>
  tools: Array<{ tool_id: number; enabled: boolean }>
  mcp_servers: Array<{ mcp_server_id: number; enabled: boolean }>
}
export type ManagedResourceBase = {
  id: number
  name: string
  description: string
  enabled: boolean
  publication_status: string
  allowed_actions: string[]
}
export type SkillResource = ManagedResourceBase & {
  display_name: string
  category: string
  version: string
  model_tier: string
  allowed_tools: string[]
  visibility: 'private' | 'tenant' | 'global'
  is_builtin: boolean
  has_bundle: boolean
  can_edit: boolean
  pending_publish: string | null
  system_prompt: string
}
export type ToolResource = ManagedResourceBase & {
  display_name: string
  category: string
  execution_type: 'api' | 'mcp'
  schema_json: Record<string, unknown>
  endpoint: string | null
  http_adapter_config: Record<string, unknown> | null
  mcp_server_id: number | null
  is_builtin: boolean
  owner_scope: string
  owner_user_id: string | null
  tenant_id: string
  updated_at: string | null
}
export type McpResource = ManagedResourceBase & {
  category: string
  tenant_id: string
  owner_scope: string
  owner_user_id: string | null
  transport: string
  endpoint: string
  has_auth: boolean
  tool_filter: string[]
  connection_status: string
  last_connected_at: string | null
  last_sync_at: string | null
  last_error: string
  created_at: string | null
}
export type KnowledgeBaseResource = ManagedResourceBase & {
  embedding_model: string
  embedding_dim: number
  retrieval_mode: string
  retrieval_top_k: number
  score_threshold: number
  reranker_model: string
  reranker_enabled: boolean
  vlm_model: string
  vlm_enabled: boolean
  multimodal: boolean
  owner: string
  visibility: string
  document_count: number
  word_count: number
  created_at: string | null
  updated_at: string | null
}

const get = <T>(url: string, params?: object) => client.get<any, T>(url, { params })
const post = <T>(url: string, data?: object) => client.post<any, T>(url, data)
const put = <T>(url: string, data?: object) => client.put<any, T>(url, data)
const del = <T>(url: string) => client.delete<any, T>(url)

export const api = {
  context: () => get<ConsoleContext>('/api/console-context'),
  requests: (params?: object) => get<PageResult<PublicationRequest>>('/api/publication-requests', params),
  review: (id: number, decision: string, note: string) => post<PublicationRequest>(`/api/publication-requests/${id}/review`, { decision, note }),
  publications: (params?: object) => get<PageResult<Publication>>('/api/publications', params),
  transition: (item: Publication, action: string, reason: string) => post<Publication>(`/api/publications/${item.resource_type}/${item.resource_id}/${action}`, { reason }),
  providers: () => get<{ items: Provider[] }>('/api/providers'),
  createProvider: (data: object) => post<Provider>('/api/providers', data),
  updateProvider: (id: number, data: object) => put<Provider>(`/api/providers/${id}`, data),
  testProvider: (id: number) => post<{ ok: boolean; status_code: number | null; message: string }>(`/api/providers/${id}/test`),
  deleteProvider: (id: number, reason: string) => client.delete(`/api/providers/${id}`, { params: { reason } }),
  runs: (params?: object) => get<PageResult<Run>>('/api/operations/runs', params),
  runAction: (id: string, action: string, reason: string) => post<Run>(`/api/operations/runs/${id}/${action}`, { reason }),
  documentJobs: (params?: object) => get<PageResult<DocumentJob>>('/api/operations/document-jobs', params),
  documentAction: (id: number, action: string, reason: string) => post<DocumentJob>(`/api/operations/document-jobs/${id}/${action}`, { reason }),
  audits: (params?: object) => get<PageResult<AuditEvent>>('/api/audit-events', params),
  quotas: (user_id?: string) => get<Record<string, any>>('/api/quotas', user_id ? { user_id } : undefined),
  updateTenantQuota: (data: object) => put('/api/quotas/tenant', data),
  updateUserQuota: (userId: string, data: object) => put(`/api/quotas/users/${encodeURIComponent(userId)}`, data),
  resources: (type: string) => get<any>(`/api/${type}`),
  agents: (params?: object) => get<{ items: Agent[]; total: number }>('/api/agents', params),
  agent: (id: number) => get<Agent>(`/api/agents/${id}`),
  createAgent: (data: object) => post<Agent>('/api/agents', data),
  updateAgent: (id: number, data: object) => put<Agent>(`/api/agents/${id}`, data),
  deleteAgent: (id: number) => client.delete(`/api/agents/${id}`),
  shareCheck: (id: number) => get<any>(`/api/agents/${id}/share-check`),
  shareAgent: (id: number) => post<any>(`/api/agents/${id}/share`),
  unshareAgent: (id: number) => post<any>(`/api/agents/${id}/unshare`),
  agentBindings: (id: number) => get<AgentBindings>(`/api/agents/${id}/bindings`),
  bindAgentSkill: (id: number, skill_id: number, priority = 100) => post(`/api/agents/${id}/skills`, { skill_id, priority }),
  unbindAgentSkill: (id: number, skillId: number) => client.delete(`/api/agents/${id}/skills/${skillId}`),
  bindAgentKb: (id: number, kb_id: number, strategy = 'hybrid', top_k = 5) => post(`/api/agents/${id}/kbs`, { kb_id, strategy, top_k }),
  unbindAgentKb: (id: number, kbId: number) => client.delete(`/api/agents/${id}/kbs/${kbId}`),
  bindAgentTool: (id: number, tool_id: number) => post(`/api/agents/${id}/tools`, { tool_id, enabled: true }),
  unbindAgentTool: (id: number, toolId: number) => client.delete(`/api/agents/${id}/tools/${toolId}`),
  bindAgentMcp: (id: number, mcp_server_id: number) => post(`/api/agents/${id}/mcp`, { mcp_server_id, enabled: true }),
  unbindAgentMcp: (id: number, mcpId: number) => client.delete(`/api/agents/${id}/mcp/${mcpId}`),
  agentOptions: async () => {
    const [skills, tools, mcp, kbs, models] = await Promise.all([
      get<any>('/api/skills', { size: 200 }), get<any>('/api/tools', { size: 200 }),
      get<any>('/api/mcp-servers', { size: 200 }), get<any>('/api/knowledge-bases', { size: 200 }),
      get<any>('/api/user/llm-models'),
    ])
    return {
      skills: skills.skills || skills.items || [], tools: tools.items || [],
      mcpServers: mcp.items || [], knowledgeBases: kbs.knowledge_bases || kbs.items || [],
      models: models.items || [],
    }
  },
  skills: (params?: object) => get<{ skills: SkillResource[]; total: number }>('/api/skills', params),
  skill: (id: number) => get<SkillResource>(`/api/skills/${id}`),
  createSkill: (data: object) => post<SkillResource>('/api/skills', data),
  updateSkill: (id: number, data: object) => put<SkillResource & { impact?: Record<string, unknown> }>(`/api/skills/${id}`, data),
  deleteSkill: (id: number) => del<{ message: string; impact?: Record<string, number> }>(`/api/skills/${id}`),
  tools: (params?: object) => get<{ items: ToolResource[]; total: number }>('/api/tools', params),
  createTool: (data: object) => post<ToolResource>('/api/tools', data),
  updateTool: (id: number, data: object) => put<ToolResource>(`/api/tools/${id}`, data),
  deleteTool: (id: number) => del<void>(`/api/tools/${id}`),
  mcpServers: (params?: object) => get<{ items: McpResource[]; total: number }>('/api/mcp-servers', params),
  mcpServer: (id: number) => get<McpResource>(`/api/mcp-servers/${id}`),
  createMcpServer: (data: object) => post<McpResource>('/api/mcp-servers', data),
  updateMcpServer: (id: number, data: object) => put<McpResource>(`/api/mcp-servers/${id}`, data),
  deleteMcpServer: (id: number) => del<void>(`/api/mcp-servers/${id}`),
  syncMcpServer: (id: number) => post<{ synced: number; tools: string[]; removed: string[] }>(`/api/mcp-servers/${id}/sync`),
  knowledgeBases: (params?: object) => get<{ knowledge_bases: KnowledgeBaseResource[]; total: number }>('/api/knowledge-bases', params),
  knowledgeBase: (id: number) => get<KnowledgeBaseResource>(`/api/knowledge-bases/${id}`),
  createKnowledgeBase: (data: object) => post<KnowledgeBaseResource>('/api/knowledge-bases', data),
  updateKnowledgeBase: (id: number, data: object) => put<KnowledgeBaseResource>(`/api/knowledge-bases/${id}`, data),
  deleteKnowledgeBase: (id: number) => del<{ message: string }>(`/api/knowledge-bases/${id}`),
  requestResourcePublication: (type: 'skills' | 'tools' | 'mcp-servers' | 'knowledge-bases', id: number, target_scope: 'tenant' | 'global', note: string) =>
    post<PublicationRequest>(`/api/${type}/${id}/publication-requests`, { target_scope, note }),
}
