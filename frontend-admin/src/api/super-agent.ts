/**
 * 超级智能体管理 API
 *
 * 包含：
 * - Skills 管理
 * - 执行日志查询
 * - 辩论配置
 */

import request from '../utils/request'

// ==================== 类型定义 ====================

// Resolver 来源标签(Stage 8: v2 *_override 字段下线后)
//   capability = required/optional_capabilities 声明触发
//   tier       = tool_tier 触发
//   manual     = allowed_tools 触发
//   default_pool = 啥都没声明,fallback 到默认全工具池
export type SkillResolvedSource =
  | 'capability'
  | 'tier'
  | 'manual'
  | 'default_pool'

// Skill 相关
export interface Skill {
  id: number
  name: string
  display_name: string  // 后端返回的是 display_name
  title: string         // 前端使用的别名 = display_name
  description: string
  category: string
  version: string
  context: string            // fork/shared
  agent_type: string | null
  enabled: boolean
  allowed_tools: string[]
  trigger_keywords: string[] | null
  model_tier: string | null
  execution_mode: string     // normal/plan
  sub_skills: string[] | null
  sub_agent_config: {
    max_depth?: number
    timeout?: number
    max_concurrent?: number
    allow_background?: boolean
  } | null
  is_system: boolean
  sort_order: number
  tool_tier: string | null       // common/standard/extended
  extra_tools: string[] | null   // 额外工具
  denied_tools: string[] | null  // 排除工具
  resolved_tools: string[]       // 最终解析后的工具列表（只读）
  // v11.0: Capability 抽象层（跨平台 Skill 契约,来自 skill.md）
  required_capabilities?: string[] | null
  optional_capabilities?: string[] | null
  preferred_sub_skills?: string[] | null
  // 平台未注册或被 platform_gate 关闭的 capability（前端可标红）
  unavailable_capabilities?: string[] | null
  // resolved_tools 解析路径标签
  resolved_source?: SkillResolvedSource | null
  intent_priority: number | null  // 意图识别优先级(0-100)
  regex_patterns: string[] | null // 正则匹配模式列表
  exclusion_rules: Record<string, any> | null // 排他规则配置
  system_prompt?: string     // 详情接口返回
  user_id?: string | null
  expert_count?: number
  // P9: 沙箱隔离配置
  sandbox_enabled?: boolean
  sandbox_timeout?: number
  sandbox_memory_mb?: number
  // v9.0: 上下文与状态管理
  high_context_threshold?: boolean
  state_tracking?: boolean
  plan_stages?: PlanStageConfig[] | null
  prompt_sections_meta?: PromptSection[] | null
  output_format?: string | null  // null=普通输出, 'artifact'=Artifact面板, 'html-*'=富媒体
  created_at?: string
  updated_at?: string
}

export interface SkillStats {
  total: number
  enabled: number
  disabled: number
  by_category: Record<string, number>
}

// 后端返回的原始格式
interface SkillStatsResponse {
  total_skills: number
  enabled_skills: number
  disabled_skills: number
  categories: Record<string, number>
  recent_executions: number
}

export interface PlanStageConfig {
  name: string
  display_name: string
  system_prompt_suffix: string
  allowed_tools?: string[] | null  // null=继承Skill全局, []=禁用所有, [...]= 指定子集
  max_iterations: number           // 1-50, 默认 5
  output_key: string
  critical: boolean
  max_tokens: number               // 512-32768, 默认 4096
  require_attachments: boolean
  timeout_weight: number           // 0.0-1.0
  visible_to_user: boolean         // true=内容直接流式展示, false=折叠在阶段进度中
  prompt_section_ids?: string[] | null  // null=完整继承, []=不使用, [ids]=指定段落
  output_format?: string | null    // null=普通输出(content事件), 'artifact'=输出到Artifact编辑面板
}

export interface PromptSection {
  id: string
  title: string
  order: number
  chars: number
  preview: string
}

export interface SkillUpdateRequest {
  enabled?: boolean
  display_name?: string
  description?: string
  model_tier?: string
  system_prompt?: string
  allowed_tools?: string[]
  trigger_keywords?: string[]
  execution_mode?: string
  sub_skills?: string[]
  sub_agent_config?: { max_depth?: number; timeout?: number; max_concurrent?: number; allow_background?: boolean }
  sort_order?: number
  category?: string
  tool_tier?: string | null
  extra_tools?: string[] | null
  denied_tools?: string[] | null
  // v11.0: Capability 抽象层
  required_capabilities?: string[] | null
  optional_capabilities?: string[] | null
  preferred_sub_skills?: string[] | null
  intent_priority?: number | null
  regex_patterns?: string[] | null
  exclusion_rules?: Record<string, any> | null
  verification_config?: {
    strategy?: string
    dimensions?: Array<{ name: string; description: string; weight: number }>
    min_score?: number
  } | null
  // P9: 沙箱隔离配置
  sandbox_enabled?: boolean
  sandbox_timeout?: number
  sandbox_memory_mb?: number
  // v9.0: 上下文与状态管理
  high_context_threshold?: boolean
  state_tracking?: boolean
  plan_stages?: PlanStageConfig[] | null
}

// 工具解析预览（dry-run）
export interface PreviewToolsRequest {
  required_capabilities?: string[] | null
  optional_capabilities?: string[] | null
  preferred_sub_skills?: string[] | null
  tool_tier?: string | null
  extra_tools?: string[] | null
  denied_tools?: string[] | null
  allowed_tools?: string[] | null
  sandbox_enabled?: boolean
}

export interface PreviewToolsResponse {
  resolved_tools: string[]
  unavailable_capabilities?: string[] | null
  source_path: SkillResolvedSource
}

// SkillCreateRequest — 全 5 板块字段
export interface SkillCreateRequest {
  name: string
  display_name: string
  description?: string
  category?: string
  context?: string
  agent_type?: string
  execution_mode?: string
  model_tier?: string
  sort_order?: number
  max_iterations?: number
  system_prompt?: string
  allowed_tools?: string[]
  tool_tier?: string | null
  extra_tools?: string[] | null
  denied_tools?: string[] | null
  // v11.0: Capability 抽象层
  required_capabilities?: string[] | null
  optional_capabilities?: string[] | null
  preferred_sub_skills?: string[] | null
  sub_skills?: string[] | null
  sub_agent_config?: { max_depth?: number; timeout?: number; max_concurrent?: number; allow_background?: boolean } | null
  verification_config?: {
    strategy?: string
    dimensions?: Array<{ name: string; description: string; weight: number }>
    min_score?: number
  } | null
  trigger_keywords?: string[] | null
  intent_priority?: number | null
  regex_patterns?: string[] | null
  exclusion_rules?: Record<string, any> | null
  // v9.0: 上下文与状态管理
  high_context_threshold?: boolean
  state_tracking?: boolean
  plan_stages?: PlanStageConfig[] | null
  source?: 'admin' | 'ai-generated'
  is_enabled?: boolean
}

// AI 生成 Skill 配置
export interface SkillGenerateRequest {
  messages: Array<{ role: 'user' | 'assistant'; content: string }>
}

export interface SkillGenerateResponse {
  reply: string
  skill_config?: Record<string, any> | null
  validation?: { passed: boolean; warnings: string[]; errors: string[] } | null
  is_final: boolean
  conversation_round: number
}

// 内置技能
export interface BuiltinSkill {
  name: string
  description: string
  allowed_tools: string[]
  read_only: boolean
  timeout: number
}

// 执行日志相关
export interface SkillLog {
  id: number
  session_id: string
  skill_name: string
  user_id: string
  username?: string
  input_text: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  output_summary?: string
  error_message?: string
  tool_calls_count: number
  tool_calls_success: number  // v2.0 新增
  tool_calls_failed: number   // v2.0 新增
  tokens_used: number
  duration_ms: number
  created_at: string
  completed_at?: string
}

// 工具调用相关 (v2.0 新增)
export interface ToolCallSummary {
  id: number
  call_index: number
  tool_name: string
  status: 'pending' | 'running' | 'success' | 'failed'
  duration_ms: number
  tokens_used: number
  input_preview: string
  output_preview: string
  has_full_output: boolean
  error_message?: string
  created_at: string
}

export interface ToolCallSummaryResponse {
  calls: ToolCallSummary[]
  summary: {
    total: number
    success: number
    failed: number
    running: number
    total_duration_ms: number
  }
}

export interface ToolCallDetail {
  id: number
  execution_id: number
  call_index: number
  tool_name: string
  tool_input: any
  tool_output: any
  output_truncated: boolean
  status: string
  error_message?: string
  duration_ms: number
  tokens_used: number
  created_at: string
}

export interface WaterfallItem {
  tool_name: string
  start_offset_ms: number
  duration_ms: number
  status: 'success' | 'failed' | 'thinking' | string
  item_type: 'tool_call' | 'llm_thinking'
}

export interface WaterfallResponse {
  calls: WaterfallItem[]
  total_duration_ms: number
  tool_execution_ms: number    // v2.0 新增
  llm_thinking_ms: number      // v2.0 新增
}

export interface LogListParams {
  page?: number
  page_size?: number
  skill_name?: string
  status?: string
  user_id?: string
  date_from?: string
  date_to?: string
}

export interface LogStats {
  total_executions: number
  success_count: number
  failure_count: number
  avg_duration_ms: number
  total_tokens: number
  by_skill: Record<string, number>
  by_status: Record<string, number>
}

export interface DailyStats {
  date: string
  executions: number
  success: number
  failure: number
  tokens: number
}

// 辩论相关
export interface Expert {
  id: string
  name: string
  perspective: string
  description: string
  model_tier: string
  enabled: boolean
}

export interface DebateConfig {
  default_rounds: number
  max_rounds: number
  expert_count: number
  model_distribution: {
    high: number
    mid: number
    low: number
  }
  timeout_seconds: number
}

// ==================== 常量映射 ====================

export const SKILL_CATEGORY_MAP: Record<string, string> = {
  'research': '科研工具',
  'document': '文档生成',
  'search': '信息检索',
  'education': '教育辅助',
  'analysis': '分析工具',
  'general': '通用工具'
}

/**
 * 工具解析路径的显示文案 + 徽章颜色(Stage 8 简化版)
 */
export const RESOLVED_SOURCE_LABEL: Record<SkillResolvedSource, string> = {
  capability:   '能力声明',
  tier:         '工具层级',
  manual:       '手动白名单',
  default_pool: '默认工具池',
}

export const RESOLVED_SOURCE_TYPE: Record<SkillResolvedSource, 'success' | 'info' | 'warning' | 'default'> = {
  capability:   'info',
  tier:         'info',
  manual:       'info',
  default_pool: 'success',
}

export const SKILL_CATEGORY_COLOR: Record<string, string> = {
  'research': '#18a058',
  'document': '#2080f0',
  'search': '#f0a020',
  'education': '#d03050',
  'analysis': '#8a2be2',
  'general': '#909399'
}

export const LOG_STATUS_MAP: Record<string, string> = {
  'pending': '等待中',
  'running': '执行中',
  'completed': '已完成',
  'failed': '失败'
}

export const LOG_STATUS_TYPE: Record<string, 'default' | 'info' | 'success' | 'warning' | 'error'> = {
  'pending': 'default',
  'running': 'info',
  'completed': 'success',
  'failed': 'error'
}

// 工具调用状态映射 (v2.0 新增)
export const TOOL_CALL_STATUS_MAP: Record<string, string> = {
  'pending': '等待中',
  'running': '执行中',
  'success': '成功',
  'failed': '失败'
}

export const TOOL_CALL_STATUS_TYPE: Record<string, 'default' | 'info' | 'success' | 'warning' | 'error'> = {
  'pending': 'default',
  'running': 'info',
  'success': 'success',
  'failed': 'error'
}

// 工具名称映射
export const TOOL_NAME_MAP: Record<string, string> = {
  'wanfang_search': '万方检索',
  'semantic_scholar': 'Semantic Scholar',
  'knowledge_base': '知识库检索',
  'web_search': '网页搜索',
  'synthesize': '综合分析',
  'generate_outline': '生成大纲',
  'write_section': '撰写章节',
  'review_content': '内容审核'
}

export const MODEL_TIER_MAP: Record<string, string> = {
  'high': '高性能 (qwen-max)',
  'mid': '标准 (glm-4-plus)',
  'low': '轻量 (glm-4-plus)'
}

/**
 * 根据模型配置生成显示映射
 */
export function buildModelTierMap(config: ModelConfigResponse): Record<string, string> {
  const map: Record<string, string> = {}
  for (const [tier, info] of Object.entries(config.tiers)) {
    map[tier] = `${info.display_name} (${info.model})`
  }
  return map
}

// ==================== Skills API ====================

/**
 * 映射后端返回的 skill 数据，添加 title 字段
 */
function mapSkillResponse(skill: any): Skill {
  return {
    ...skill,
    title: skill.display_name || skill.name  // 将 display_name 映射到 title
  }
}

/**
 * 获取 Skills 列表
 */
export const getSkills = async (params?: { category?: string; enabled?: boolean }): Promise<{
  skills: Skill[]
  total: number
}> => {
  const response: any = await request.get('/api/admin/super-skills', { params })
  return {
    skills: (response.skills || []).map(mapSkillResponse),
    total: response.total || 0
  }
}

/**
 * 获取 Skills 统计
 */
export const getSkillStats = async (): Promise<SkillStats> => {
  const response: SkillStatsResponse = await request.get('/api/admin/super-skills/stats')
  return {
    total: response.total_skills,
    enabled: response.enabled_skills,
    disabled: response.disabled_skills,
    by_category: response.categories
  }
}

/**
 * 获取分类列表
 */
export const getSkillCategories = (): Promise<string[]> => {
  return request.get('/api/admin/super-skills/categories')
}

/**
 * 获取 Skill 详情
 */
export const getSkill = async (name: string): Promise<Skill> => {
  const response = await request.get(`/api/admin/super-skills/${name}`)
  return mapSkillResponse(response)
}

/**
 * 更新 Skill 配置
 */
export const updateSkill = (name: string, data: SkillUpdateRequest): Promise<{ message: string; skill: Skill; warnings?: string[] }> => {
  return request.patch(`/api/admin/super-skills/${name}`, data)
}

/**
 * 重新加载单个 Skill
 */
export const reloadSkill = (name: string): Promise<{ success: boolean; message: string }> => {
  return request.post(`/api/admin/super-skills/${name}/reload`)
}

/**
 * Dry-run 工具解析预览 — 支持 capability/tier/manual 三种路径，不写库
 */
export const previewSkillTools = (
  name: string,
  data: PreviewToolsRequest = {},
): Promise<PreviewToolsResponse> => {
  return request.post(`/api/admin/super-skills/${name}/preview-tools`, data)
}

/**
 * 重新加载所有 Skills
 */
export const reloadAllSkills = (): Promise<{ success: boolean; loaded: number; message: string }> => {
  return request.post('/api/admin/super-skills/reload-all')
}

/**
 * 创建 Skill
 */
/** AI 生成执行阶段 */
export const generateStages = (data: {
  description: string
  system_prompt: string
  available_tools: string[]
  skill_name: string
}): Promise<{ success: boolean; stages: PlanStageConfig[]; error?: string }> => {
  return request.post('/api/admin/super-skills/generate-stages', data)
}

export const createSkill = (data: SkillCreateRequest): Promise<Skill> => {
  return request.post('/api/admin/super-skills', data)
}

/**
 * AI 辅助生成 Skill 配置
 */
export const generateSkill = (data: SkillGenerateRequest): Promise<SkillGenerateResponse> => {
  return request.post('/api/admin/super-skills/generate', data)
}

/**
 * 删除 Skill（仅非系统 Skill）
 */
export const deleteSkill = (identifier: string): Promise<{ message: string }> => {
  return request.delete(`/api/admin/super-skills/${identifier}`)
}

// ==================== M4 / Stage 9: Skill 包内资源 (只读) ====================

export interface SkillResourceItem {
  path: string
  resource_type: 'reference' | 'script' | 'asset'
  size: number
  updated_at: string | null
}

export interface SkillResourceContent {
  path: string
  resource_type: 'reference' | 'script' | 'asset'
  size: number
  content: string
  truncated: boolean
  is_binary: boolean
}

/**
 * 列出指定 Skill 的包内资源 (references / scripts)
 */
export const getSkillResources = (
  identifier: string,
): Promise<{ skill_name: string; total: number; resources: SkillResourceItem[] }> => {
  return request.get(`/api/admin/super-skills/${identifier}/resources`)
}

/**
 * 读取单个资源原文 (> 100KB 会截断)
 */
export const getSkillResourceContent = (
  identifier: string,
  path: string,
): Promise<SkillResourceContent> => {
  return request.get(`/api/admin/super-skills/${identifier}/resources/content`, {
    params: { path },
  })
}

// ==================== Phase 3: Skill Creator 增强 API ====================

/**
 * 描述唯一性评估
 */
export interface DescriptionEvalResult {
  score: number  // 0-100, 越高越独特
  conflicts: Array<{ name: string; display_name: string; similarity: number }>
  suggestion: string | null
}

export const evaluateDescription = (data: {
  description: string
  exclude_name?: string
}): Promise<DescriptionEvalResult> => {
  return request.post('/api/admin/super-skills/evaluate-description', data)
}

/**
 * 意图匹配测试
 */
export interface IntentTestResult {
  input: string
  result: {
    type: string
    skill_name: string | null
    confidence: number
    match_source: string | null
  }
  alternatives: Array<{ skill_name: string; confidence: number }>
}

export const testIntent = (data: {
  prompt: string
  agent_id?: number
}): Promise<IntentTestResult> => {
  return request.post('/api/admin/super-skills/test-intent', data)
}

// ==================== 审计日志 ====================

export interface SkillAuditLog {
  id: number
  skill_name: string
  admin_user_id: string
  admin_username: string | null
  action: string
  changed_fields: string[] | null
  old_values: Record<string, any> | null
  new_values: Record<string, any> | null
  created_at: string | null
}

/**
 * 获取 Skill 审计日志
 */
export const getSkillAuditLogs = (name: string, limit = 50, offset = 0): Promise<{ logs: SkillAuditLog[]; total: number }> => {
  return request.get(`/api/admin/super-skills/${name}/audit-logs`, { params: { limit, offset } })
}

/**
 * 回滚 Skill 到某条审计日志的修改前状态
 */
export const rollbackSkill = (name: string, logId: number): Promise<{ message: string; skill: Skill; warnings?: string[] }> => {
  return request.post(`/api/admin/super-skills/${name}/rollback/${logId}`)
}

/**
 * 获取内置技能列表
 */
export const getBuiltinSkills = (): Promise<{ builtin_skills: BuiltinSkill[] }> => {
  return request.get('/api/admin/super-skills/builtin-skills')
}

// ==================== 执行日志 API ====================

/**
 * 获取执行日志列表
 */
export const getSkillLogs = (params?: LogListParams): Promise<{
  logs: SkillLog[]
  total: number
  page: number
  page_size: number
}> => {
  return request.get('/api/admin/skill-logs', { params })
}

/**
 * 获取日志统计
 */
export const getLogStats = (params?: { days?: number }): Promise<LogStats> => {
  return request.get('/api/admin/skill-logs/stats', { params })
}

/**
 * 获取每日统计
 */
export const getDailyStats = (params?: { days?: number }): Promise<DailyStats[]> => {
  return request.get('/api/admin/skill-logs/daily-stats', { params })
}

/**
 * 获取日志详情
 */
export const getLogDetail = (id: number): Promise<SkillLog> => {
  return request.get(`/api/admin/skill-logs/${id}`)
}

/**
 * 删除日志
 */
export const deleteLog = (id: number): Promise<{ success: boolean }> => {
  return request.delete(`/api/admin/skill-logs/${id}`)
}

/**
 * 批量删除日志
 */
export const batchDeleteLogs = (params: {
  before_date?: string
  skill_name?: string
}): Promise<{
  success: boolean
  deleted_count: number
}> => {
  return request.delete('/api/admin/skill-logs', { params })
}

// ==================== 工具调用 API (v2.0 新增) ====================

/**
 * 获取工具调用摘要列表
 */
export const getToolCallsSummary = (executionId: number): Promise<ToolCallSummaryResponse> => {
  return request.get(`/api/admin/skill-logs/${executionId}/tool-calls/summary`)
}

/**
 * 获取工具调用详情
 */
export const getToolCallDetail = (callId: number): Promise<ToolCallDetail> => {
  return request.get(`/api/admin/skill-logs/tool-calls/${callId}/detail`)
}

/**
 * 获取瀑布图数据
 */
export const getWaterfallData = (executionId: number): Promise<WaterfallResponse> => {
  return request.get(`/api/admin/skill-logs/${executionId}/waterfall`)
}

/**
 * 清理旧工具调用记录
 */
export const cleanupToolCalls = (days: number): Promise<{
  success: boolean
  deleted_count: number
  message: string
}> => {
  return request.delete('/api/admin/skill-logs/tool-calls/cleanup', { params: { days } })
}

/**
 * 获取工具名称显示文本
 */
export function getToolDisplayName(toolName: string): string {
  return TOOL_NAME_MAP[toolName] || toolName
}

// ==================== 辩论配置 API ====================

/**
 * 获取专家列表
 */
export const getExperts = (): Promise<Expert[]> => {
  return request.get('/api/user/debate/experts/list')
}

/**
 * 获取辩论配置（管理后台）
 */
export const getDebateConfig = (): Promise<DebateConfig & { version?: number; updated_at?: string }> => {
  return request.get('/api/admin/config/debate')
}

/**
 * 更新辩论配置
 */
export const updateDebateConfig = (config: Partial<DebateConfig>): Promise<DebateConfig & { version?: number; updated_at?: string }> => {
  return request.put('/api/admin/config/debate', config)
}

// ==================== 模型配置 API ====================

/**
 * 模型等级配置
 */
export interface ModelTierConfig {
  model: string
  display_name: string
  description?: string
}

/**
 * 可用模型
 */
export interface AvailableModel {
  id: string
  name: string
  provider: string
}

/**
 * 模型配置响应
 */
export interface ModelConfigResponse {
  tiers: Record<string, ModelTierConfig>
  available_models: AvailableModel[]
  version?: number
  updated_at?: string
}

/**
 * 获取模型配置
 */
export const getModelConfig = (): Promise<ModelConfigResponse> => {
  return request.get('/api/admin/config/models')
}

/**
 * 更新模型配置
 */
export const updateModelConfig = (config: {
  high?: string
  mid?: string
  low?: string
}): Promise<{
  message: string
  updated_tiers: string[]
  version: number
  updated_at: string
}> => {
  return request.put('/api/admin/config/models', config)
}

// ==================== 角色模型配置 API (v1.2 新增) ====================

/**
 * 角色模型配置
 */
export interface RoleModelConfig {
  model: string
  display_name: string
  description: string
  fallback_tier: string
}

/**
 * 角色模型配置响应
 */
export interface RoleModelsResponse {
  roles: Record<string, RoleModelConfig>
  available_models: AvailableModel[]
  version?: number
  updated_at?: string
}

/**
 * 角色模型更新请求
 * v1.3: 辩论专家拆分为 high/mid/low 三个角色
 * v1.4: 新增系统辅助角色（vision/memory/query_rewrite/judge/title_generation/compression）
 */
export interface RoleModelUpdateRequest {
  intent_recognition?: string
  qa_chat?: string
  skill_execution?: string
  debate_expert_high?: string
  debate_expert_mid?: string
  debate_expert_low?: string
  debate_coordinator?: string
  review_generation?: string
  vision?: string
  memory?: string
  query_rewrite?: string
  judge?: string
  title_generation?: string
  compression?: string
  budget?: string
  kb_text_model?: string
}

/**
 * 角色显示名称映射
 * v1.3: 辩论专家拆分为 high/mid/low 三个角色
 * v1.4: 新增系统辅助角色
 */
export const ROLE_DISPLAY_MAP: Record<string, string> = {
  'intent_recognition': '意图识别',
  'qa_chat': 'QA对话',
  'skill_execution': 'Skill执行',
  'debate_expert_high': '辩论专家（高性能）',
  'debate_expert_mid': '辩论专家（标准）',
  'debate_expert_low': '辩论专家（轻量）',
  'debate_coordinator': '辩论主持人',
  'review_generation': '综述生成',
  'vision': '图像理解',
  'memory': '记忆提取',
  'query_rewrite': '查询改写',
  'judge': '输出评判',
  'title_generation': '标题生成',
  'compression': '上下文压缩',
  'budget': '预算模型',
  'kb_text_model': '知识库文本模型',
  'artifact_edit': '文档AI编辑'
}

/**
 * 角色图标颜色
 */
export const ROLE_COLOR_MAP: Record<string, string> = {
  'intent_recognition': '#909399',  // 灰色 - 轻量任务
  'qa_chat': '#2080f0',             // 蓝色 - 对话
  'skill_execution': '#18a058',     // 绿色 - 执行
  'debate_expert_high': '#d03050',  // 红色 - 高性能专家
  'debate_expert_mid': '#f0a020',   // 橙色 - 标准专家
  'debate_expert_low': '#909399',   // 灰色 - 轻量专家
  'debate_coordinator': '#8a2be2',  // 紫色 - 主持人
  'review_generation': '#18a058',   // 绿色 - 生成
  'vision': '#36ad6a',              // 青绿 - 图像理解
  'memory': '#b88230',              // 棕金 - 记忆提取
  'query_rewrite': '#63a8c9',       // 浅蓝 - 查询改写
  'judge': '#d96a3a',               // 橙红 - 输出评判
  'title_generation': '#8b8b8b',    // 中灰 - 标题生成
  'compression': '#6b7fd7',         // 蓝紫 - 上下文压缩
  'budget': '#52c41a',              // 亮绿 - 预算模型
  'kb_text_model': '#1890ff',       // 天蓝 - 知识库文本模型
  'artifact_edit': '#e67e22'        // 橙色 - 文档AI编辑
}

/**
 * 获取角色模型配置
 */
export const getRoleModels = (): Promise<RoleModelsResponse> => {
  return request.get('/api/admin/config/roles')
}

/**
 * 更新角色模型配置
 */
export const updateRoleModels = (config: RoleModelUpdateRequest): Promise<{
  message: string
  updated_roles: string[]
  version: number
  updated_at: string
}> => {
  return request.put('/api/admin/config/roles', config)
}

// ==================== 专家管理 API (v2.0 DB CRUD) ====================

/**
 * 专家定义（数据库驱动）
 */
export interface ExpertConfig {
  id: number
  slug: string
  name: string
  title: string
  category: string
  perspective: string
  persona: string
  keywords: string[]
  model_tier: string
  priority: number
  is_active: boolean
  current_model?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export interface ExpertCreateRequest {
  slug: string
  name: string
  title: string
  category: string
  perspective: string
  persona: string
  keywords: string[]
  model_tier: string
  priority: number
}

export interface ExpertUpdateRequest {
  name?: string
  title?: string
  category?: string
  perspective?: string
  persona?: string
  keywords?: string[]
  model_tier?: string
  priority?: number
}

/**
 * 获取专家列表
 */
export const getExpertsConfig = (): Promise<{
  experts: ExpertConfig[]
  total: number
}> => {
  return request.get('/api/admin/experts')
}

/**
 * 新增专家
 */
export const createExpert = (data: ExpertCreateRequest): Promise<ExpertConfig> => {
  return request.post('/api/admin/experts', data)
}

/**
 * 更新专家
 */
export const updateExpert = (expertId: number, data: ExpertUpdateRequest): Promise<ExpertConfig> => {
  return request.put(`/api/admin/experts/${expertId}`, data)
}

/**
 * 删除专家
 */
export const deleteExpert = (expertId: number): Promise<{ success: boolean; message: string }> => {
  return request.delete(`/api/admin/experts/${expertId}`)
}

/**
 * 启用/禁用专家
 */
export const toggleExpert = (expertId: number): Promise<{
  success: boolean
  is_active: boolean
  message: string
}> => {
  return request.post(`/api/admin/experts/${expertId}/toggle`)
}

/**
 * 重置所有专家配置
 */
export const resetExperts = (): Promise<{
  success: boolean
  message: string
}> => {
  return request.post('/api/admin/experts/reset')
}

// ==================== 工具函数 ====================

/**
 * 格式化持续时间
 */
export function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${(ms / 60000).toFixed(1)}min`
}

/**
 * 格式化 Token 数量
 */
export function formatTokens(tokens: number): string {
  if (tokens < 1000) return `${tokens}`
  if (tokens < 1000000) return `${(tokens / 1000).toFixed(1)}K`
  return `${(tokens / 1000000).toFixed(2)}M`
}

/**
 * 获取分类颜色
 */
export function getCategoryColor(category: string): string {
  return SKILL_CATEGORY_COLOR[category] ?? SKILL_CATEGORY_COLOR['general'] ?? '#909399'
}

/**
 * 获取分类名称
 */
export function getCategoryName(category: string): string {
  return SKILL_CATEGORY_MAP[category] || category || 'general'
}

// ==================== 辩论风格配置 API (v2.0 新增) ====================

/**
 * 辩论参数
 */
export interface DebateParamsV2 {
  default_rounds: number
  expert_count: number
  timeout_seconds: number
  speech_min_length: number
  speech_max_length: number
}

/**
 * 主持人风格配置
 */
export interface CoordinatorStyleConfig {
  preset_id: 'standard' | 'concise' | 'academic'
}

/**
 * 辩论配置响应 (v2.0)
 */
export interface DebateConfigV2Response {
  params: DebateParamsV2
  coordinator_style: CoordinatorStyleConfig
  version: number
  updated_at?: string
  updated_by?: string
}

/**
 * 预设摘要
 */
export interface DebatePresetSummary {
  id: string
  name: string
  description?: string
  is_system: boolean
}

/**
 * 预设详情
 */
export interface DebatePreset {
  id: string
  name: string
  description?: string
  style_prompt: string
  is_system: boolean
  sort_order: number
}

/**
 * 配置预览
 */
export interface ConfigPreview {
  total_speeches: number
  estimated_duration_min: number
  estimated_duration_max: number
  estimated_words_min: number
  estimated_words_max: number
  flow_description: string
}

/**
 * 获取辩论配置 (v2.0)
 */
export const getDebateConfigV2 = (): Promise<DebateConfigV2Response> => {
  return request.get('/api/admin/debate/config')
}

/**
 * 更新辩论参数 (v2.0)
 */
export const updateDebateParamsV2 = (params: DebateParamsV2, version: number): Promise<DebateConfigV2Response> => {
  return request.put('/api/admin/debate/config/params', { params, version })
}

/**
 * 更新主持人风格 (v2.0)
 */
export const updateCoordinatorStyle = (preset_id: string, version: number): Promise<DebateConfigV2Response> => {
  return request.put('/api/admin/debate/config/style', { preset_id, version })
}

/**
 * 重置配置 (v2.0)
 */
export const resetDebateConfigV2 = (): Promise<DebateConfigV2Response> => {
  return request.post('/api/admin/debate/config/reset')
}

/**
 * 获取预设列表
 */
export const getDebatePresets = (): Promise<DebatePresetSummary[]> => {
  return request.get('/api/admin/debate/presets')
}

/**
 * 获取预设详情
 */
export const getDebatePreset = (presetId: string): Promise<DebatePreset> => {
  return request.get(`/api/admin/debate/presets/${presetId}`)
}

/**
 * 预览配置效果
 */
export const previewDebateConfig = (params: DebateParamsV2): Promise<ConfigPreview> => {
  return request.post('/api/admin/debate/config/preview', params)
}

/**
 * 预设显示信息
 */
export const PRESET_INFO: Record<string, { icon: string; color: string }> = {
  'standard': { icon: '📋', color: '#2080f0' },
  'concise': { icon: '📝', color: '#18a058' },
  'academic': { icon: '🎓', color: '#8a2be2' }
}

// ==================== 专家匹配预览 ====================

export interface PreviewExpertItem {
  id: string
  name: string
  title: string
  category: string
  model_tier: string
  keywords: string[]
  matched_keywords: string[]
  score: number
}

export interface PreviewExpertsResponse {
  experts: PreviewExpertItem[]
  tier_distribution: Record<string, number>
}

export const previewExperts = (topic: string, expert_count: number = 5): Promise<PreviewExpertsResponse> => {
  return request.post('/api/admin/debate/preview-experts', { topic, expert_count })
}

// ==================== v1.4: Fallback + Health API ====================

/**
 * 获取模型 Fallback 配置
 */
export const getModelFallback = (): Promise<Record<string, string[]>> => {
  return request.get('/api/admin/config/models/fallback')
}

/**
 * 更新模型 Fallback 配置
 */
export const updateModelFallback = (config: Record<string, string[]>): Promise<{ message: string }> => {
  return request.put('/api/admin/config/models/fallback', config)
}

/**
 * 获取 Provider 健康状态
 */
export const getProviderHealth = (): Promise<{
  endpoints: Array<{
    name: string
    url: string
    state: 'closed' | 'open' | 'half_open'
    success_rate: number
    models: string[]
    last_checked?: string
  }>
}> => {
  return request.get('/api/admin/config/provider-health')
}


// ==================== SuperAgent (AI 助手) 管理 ====================

export interface SuperAgentItem {
  id: number
  name: string
  description: string | null
  system_prompt: string | null
  model_tier: string
  capabilities: string[] | null
  soul_prompt: string | null
  owner_user_id: string | null
  visibility: 'global' | 'personal'
  avatar: string | null
  welcome_message: string | null
  sort_order: number
  is_enabled: boolean
  allowed_skills: string[] | null
  example_questions: string[] | null
}

export interface SuperAgentUpdateData {
  name?: string
  description?: string
  system_prompt?: string
  model_tier?: string
  soul_prompt?: string | null
  visibility?: 'global' | 'personal'
  avatar?: string | null
  welcome_message?: string | null
  sort_order?: number
  is_enabled?: boolean
  allowed_skills?: string[] | null
  example_questions?: string[] | null
}

/**
 * 获取所有 SuperAgent 列表
 */
export const getSuperAgents = () =>
  request.get<SuperAgentItem[]>('/api/admin/super-agents')

/**
 * 获取单个 SuperAgent 详情
 */
export const getSuperAgent = (id: number) =>
  request.get<SuperAgentItem>(`/api/admin/super-agents/${id}`)

/**
 * 更新 SuperAgent
 */
export const updateSuperAgent = (id: number, data: SuperAgentUpdateData) =>
  request.put<SuperAgentItem>(`/api/admin/super-agents/${id}`, data)

/**
 * 创建 SuperAgent（系统预设）
 */
export const createSuperAgent = (data: SuperAgentUpdateData) =>
  request.post<SuperAgentItem>('/api/admin/super-agents', data)

// AI 辅助生成 Agent 配置
export interface AgentGenerateRequest {
  messages: Array<{ role: 'user' | 'assistant'; content: string }>
}

export interface AgentGenerateResponse {
  reply: string
  agent_config?: Record<string, any> | null
  is_final: boolean
}

/**
 * AI 辅助生成 Agent 配置
 */
export const generateAgent = (data: AgentGenerateRequest): Promise<AgentGenerateResponse> => {
  return request.post('/api/admin/super-agents/generate', data)
}

/**
 * 启用/禁用 SuperAgent
 */
export const toggleSuperAgent = (id: number) =>
  request.patch<{ id: number; is_enabled: boolean }>(`/api/admin/super-agents/${id}/toggle`)
