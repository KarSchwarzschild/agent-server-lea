/**
 * 系统设置API
 */
import request from '../utils/request'

// ========== 类型定义 ==========

export interface CategoryConfig {
  categories: string[]
  sub_categories: string[]
  methodologies: string[]
  agent_categories: string[]
}

export interface PromptConfig {
  classification_prompt: string
  review_prompt: string
  chat_system_prompt: string
}

export interface ConnectionConfig {
  tenant_api_base: string
  app_api_base: string
  default_kb_id: string
}

export interface KnowledgeBaseInfo {
  kb_id: string
  name: string
  description: string | null
  status: string
}

export interface ConfigItem {
  value: any
  description: string | null
  updated_at: string | null
}

// ========== 分类体系配置 ==========

/**
 * 获取分类体系配置
 */
export function getCategoryConfig(): Promise<CategoryConfig> {
  return request.get('/api/admin/settings/categories')
}

/**
 * 更新分类体系配置
 */
export function updateCategoryConfig(config: CategoryConfig): Promise<{ message: string }> {
  return request.put('/api/admin/settings/categories', config)
}

// ========== 提示词配置 ==========

/**
 * 获取提示词配置
 */
export function getPromptConfig(): Promise<PromptConfig> {
  return request.get('/api/admin/settings/prompts')
}

/**
 * 更新提示词配置
 */
export function updatePromptConfig(config: PromptConfig): Promise<{ message: string }> {
  return request.put('/api/admin/settings/prompts', config)
}

// ========== 连接配置 ==========

/**
 * 获取基座连接配置
 */
export function getConnectionConfig(): Promise<ConnectionConfig> {
  return request.get('/api/admin/settings/connection')
}

/**
 * 更新基座连接配置
 */
export function updateConnectionConfig(config: ConnectionConfig): Promise<{ message: string }> {
  return request.put('/api/admin/settings/connection', config)
}

// ========== 知识库管理 ==========

/**
 * 获取知识库列表
 */
export function getKnowledgeBases(): Promise<KnowledgeBaseInfo[]> {
  return request.get('/api/admin/settings/knowledge-bases')
}

/**
 * 添加知识库
 */
export function createKnowledgeBase(kbId: string, name: string, description?: string): Promise<{ message: string; kb_id: string }> {
  return request.post('/api/admin/settings/knowledge-bases', null, {
    params: { kb_id: kbId, name, description }
  })
}

/**
 * 删除知识库
 */
export function deleteKnowledgeBase(kbId: string): Promise<{ message: string }> {
  return request.delete(`/api/admin/settings/knowledge-bases/${kbId}`)
}

// ========== 通用配置 ==========

/**
 * 获取所有配置项
 */
export function getAllConfigs(): Promise<Record<string, ConfigItem>> {
  return request.get('/api/admin/settings/configs')
}

/**
 * 获取单个配置项
 */
export function getConfigByKey(key: string): Promise<ConfigItem & { key: string }> {
  return request.get(`/api/admin/settings/configs/${key}`)
}

/**
 * 更新单个配置项
 */
export function updateConfigByKey(key: string, value: string): Promise<{ message: string }> {
  return request.put(`/api/admin/settings/configs/${key}`, { value })
}

// ========== 功能开关 ==========

export interface FeatureToggleItem {
  value: string
  label: string
  description: string
  options?: string[]
}

export function getFeatureToggles(): Promise<Record<string, FeatureToggleItem>> {
  return request.get('/api/admin/settings/feature-toggles')
}

export function updateFeatureToggles(toggles: Record<string, string>): Promise<{ message: string; changes: string[] }> {
  return request.put('/api/admin/settings/feature-toggles', { toggles })
}

// ========== AI 质检 Skill 一览 ==========

export interface JudgeEnabledSkill {
  name: string
  display_name: string
  is_enabled: boolean
  dimension_count: number
  min_score: number
}

export function getJudgeEnabledSkills(): Promise<JudgeEnabledSkill[]> {
  return request.get('/api/admin/settings/judge-enabled-skills')
}

// ========== 沙箱配置 ==========

export interface SandboxConfig {
  sandbox_enabled: boolean
  sandbox_backend: string
  sandbox_timeout: number
  sandbox_memory_limit_mb: number
  sandbox_max_per_user: number
  sandbox_idle_timeout: number
}

export interface SandboxEnabledSkill {
  name: string
  display_name: string
  is_enabled: boolean
  sandbox_timeout: number
  sandbox_memory_mb: number
}

export function getSandboxConfig(): Promise<SandboxConfig> {
  return request.get('/api/admin/settings/sandbox-config')
}

export function updateSandboxConfig(config: Partial<SandboxConfig>): Promise<{ message: string; changes: string[] }> {
  return request.put('/api/admin/settings/sandbox-config', config)
}

export function getSandboxEnabledSkills(): Promise<SandboxEnabledSkill[]> {
  return request.get('/api/admin/settings/sandbox-enabled-skills')
}

// ========== PageIndex 文档索引配置 ==========

export interface PageIndexConfig {
  pageindex_enabled: string
  pageindex_supported_exts: string
  pageindex_model: string
  pageindex_search_model: string
  pageindex_small_scope_threshold: number
  pageindex_hybrid_vector_top_k: number
  pageindex_hybrid_tree_max_docs: number
  pageindex_hybrid_final_top_n: number
  pageindex_max_concurrent_index: number
  pageindex_doc_timeout: number
  pageindex_llm_call_timeout: number
  pageindex_tree_search_max_depth: number
  pageindex_tree_search_confidence_threshold: number
  pageindex_root_relevance_threshold: number
  pageindex_cache_ttl: number
}

export function getPageIndexConfig(): Promise<PageIndexConfig> {
  return request.get('/api/admin/settings/pageindex-config')
}

export function updatePageIndexConfig(
  config: Partial<PageIndexConfig>
): Promise<{ message: string; changes: string[] }> {
  return request.put('/api/admin/settings/pageindex-config', config)
}

// ========== 搜索缓存配置 ==========

export interface SearchCacheConfig {
  enabled: boolean
  ttl_web: number
  ttl_academic: number
  ttl_wikipedia: number
  ttl_web_fetch: number
}

export interface SearchCacheStats {
  available: boolean
  total?: number
  sources?: Record<string, { count: number }>
  error?: string
  message?: string
}

export function getSearchCacheConfig(): Promise<SearchCacheConfig> {
  return request.get('/api/admin/settings/search-cache-config')
}

export function updateSearchCacheConfig(
  config: Partial<SearchCacheConfig>
): Promise<{ message: string; changes: string[] }> {
  return request.put('/api/admin/settings/search-cache-config', config)
}

export function invalidateSearchCache(
  toolName?: string
): Promise<{ message: string; count: number }> {
  const params = toolName ? { tool_name: toolName } : {}
  return request.post('/api/admin/settings/search-cache/invalidate', null, { params })
}

export function getSearchCacheStats(): Promise<SearchCacheStats> {
  return request.get('/api/admin/settings/search-cache/stats')
}


// ========== 文件导出配置 ==========

export interface FileExportConfig {
  file_export_keywords: string
  file_export_truncate_limit: number
  file_export_prompt: string
  file_export_patterns: string
}

export function getFileExportConfig(): Promise<FileExportConfig> {
  return request.get('/api/admin/settings/file-export-config')
}

export function updateFileExportConfig(
  config: Partial<FileExportConfig>
): Promise<{ message: string; changes: string[] }> {
  return request.put('/api/admin/settings/file-export-config', config)
}

// ========== 模型端点管理（v2.5） ==========

export interface EndpointModelInfo {
  id: string
  name: string
  provider: string
  max_output_tokens?: number
  capabilities?: string[]
  usage: string[]
  health: {
    state: string
    success_rate: number
    total_calls: number
    consecutive_failures: number
  } | null
}

export interface EndpointConfig {
  name: string
  provider_type: string
  wire_api: string
  base_url_display: string
  api_key_display: string
  api_key_mode: 'env' | 'direct'
  api_key_env?: string
  source: 'yaml' | 'db'
  deletable?: boolean
  default_headers?: Record<string, string>
  models: EndpointModelInfo[]
}

export interface EndpointsResponse {
  endpoints: Record<string, EndpointConfig>
  provider_presets: Record<string, { label: string; wire_api: string; url_suffix: string; default_headers: any }>
  version: number
}

export interface ProbeTarget {
  model: string
}

export interface ProbeResult {
  model: string
  status: 'ok' | 'error'
  latency_ms: number
  preview?: string
  error?: string
}

export function getEndpoints(): Promise<EndpointsResponse> {
  return request.get('/api/admin/config/endpoints')
}

export function addEndpoint(data: {
  endpoint_key: string
  config: Record<string, any>
}): Promise<{ success: boolean; version: number }> {
  return request.put('/api/admin/config/endpoints', data)
}

export function deleteEndpoint(key: string): Promise<{
  success: boolean
  version?: number
  error?: string
  refs?: string[]
}> {
  return request.delete(`/api/admin/config/endpoints/${key}`)
}

export function addModel(data: {
  endpoint_key: string
  model: Record<string, any>
  assign_tier?: string
  force?: boolean
}): Promise<{
  success: boolean
  version?: number
  error?: string
  current_endpoint?: string
  current_endpoint_name?: string
}> {
  return request.put('/api/admin/config/endpoints/models', data)
}

export function updateModel(
  endpointKey: string,
  modelId: string,
  updates: Record<string, any>
): Promise<{ success: boolean; version?: number }> {
  return request.put(`/api/admin/config/endpoints/${endpointKey}/models/${modelId}`, { updates })
}

export function deleteModel(
  endpointKey: string,
  modelId: string
): Promise<{ success: boolean; version?: number; error?: string; refs?: string[] }> {
  return request.delete(`/api/admin/config/endpoints/${endpointKey}/models/${modelId}`)
}

export function probeEndpoints(
  targets: ProbeTarget[]
): Promise<{ results: ProbeResult[] }> {
  return request.post('/api/admin/config/endpoints/probe', { targets })
}

export function probeOneModel(
  model: string
): Promise<ProbeResult> {
  return request.post('/api/admin/config/endpoints/probe-one', { model })
}

export function saveEndpointProbeResults(
  results: Record<string, ProbeResult>
): Promise<{ message: string; count: number }> {
  return request.post('/api/admin/config/endpoints/probe-results', { results })
}

export function getEndpointProbeResults(): Promise<{
  results: Record<string, ProbeResult>
  probed_at: string | null
  probed_by?: string
}> {
  return request.get('/api/admin/config/endpoints/probe-results')
}

// ========== TTS 语音合成配置 ==========

export interface TTSVoiceInfo {
  id: string
  name: string
  gender: 'male' | 'female' | 'neutral'
}

export interface TTSEndpointInfo {
  key: string
  name: string
  provider_type: string
  base_url?: string
  base_url_env?: string
  api_key?: string
  api_key_env?: string
  source: string
  enabled: boolean
  default_voice: string
  featured_voices: TTSVoiceInfo[]
  speed_range: { min: number; max: number; default: number }
  rate_limit?: Record<string, number>
}

export interface TTSEndpointsResponse {
  endpoints: TTSEndpointInfo[]
  default_provider: string
  fallback_chain: string[]
}

export interface TTSVoicePreset {
  id: string
  label: string
  usage: string[]
  voices: Record<string, string>
}

export function getTTSEndpoints(): Promise<TTSEndpointsResponse> {
  return request.get('/api/admin/tts/endpoints')
}

export function saveTTSEndpoint(data: Record<string, any>): Promise<{ message: string; key: string }> {
  return request.post('/api/admin/tts/endpoints', data)
}

export function deleteTTSEndpoint(key: string): Promise<{ message: string; key: string }> {
  return request.delete(`/api/admin/tts/endpoints/${key}`)
}

export function probeTTSEndpoint(endpointKey: string): Promise<{ endpoint_key: string; available: boolean; error?: string }> {
  return request.post('/api/admin/tts/probe', { endpoint_key: endpointKey })
}

export function setTTSDefaultProvider(providerKey: string): Promise<{ message: string }> {
  return request.put(`/api/admin/tts/default-provider?provider_key=${encodeURIComponent(providerKey)}`)
}

export function setTTSFallbackChain(chain: string[]): Promise<{ message: string }> {
  return request.put('/api/admin/tts/fallback-chain', chain)
}

export function getTTSVoicePresets(): Promise<{ presets: TTSVoicePreset[] }> {
  return request.get('/api/admin/tts/voice-presets')
}
