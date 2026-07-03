/**
 * 插件市场 API
 *
 * 包含：插件市场浏览、安装/卸载、manifest 预览、插件设置
 */
import request from '../utils/request'

// ========== 类型定义 ==========

export interface PluginMeta {
  title: string
  description: string
  avatar?: string
  tags: string[]
  author?: string
}

export interface PluginToolEndpoint {
  url: string
  method: string
}

export interface PluginToolInfo {
  name: string
  description: string
  parameters: Record<string, any>
  endpoint: PluginToolEndpoint
}

export interface PluginManifest {
  identifier: string
  meta: PluginMeta
  tools: PluginToolInfo[]
  settings_schema?: Record<string, any>
  system_role?: string
  manifest_format: 'native' | 'openai_legacy'
  manifest_url: string
  external_domains: string[]
}

export interface MarketPlugin {
  identifier: string
  meta: PluginMeta
  manifest_url: string
  installed: boolean
  source_id?: string
  homepage?: string
  created_at?: string
}

export interface MarketTag {
  name: string
  count: number
}

export interface MarketResponse {
  items: MarketPlugin[]
  total: number
  page: number
  page_size: number
  tags: MarketTag[]
}

export interface InstallResult {
  success: boolean
  source_id: string
  tools_count: number
  tool_names: string[]
  message: string
}

export interface PluginToolStatus {
  name: string
  description: string
  http_method?: string
  http_path?: string
  is_enabled: boolean
}

export interface PluginSettings {
  schema: Record<string, any> | null
  values: Record<string, any>
  meta: PluginMeta
  identifier: string
  manifest_url: string
  manifest_format: string
  origin: string
  version: string
  created_at?: string
  is_enabled: boolean
  tools: PluginToolStatus[]
}

// ========== 市场 API ==========

/** 浏览插件市场 */
export function getPluginMarket(params: {
  search?: string
  tag?: string
  status?: 'installed' | 'not_installed'
  page?: number
  page_size?: number
}): Promise<MarketResponse> {
  return request.get('/api/admin/tools/plugins/market', { params })
}

// ========== 安装/卸载 API ==========

/** 从 manifest URL 安装插件 */
export function installPlugin(manifest_url: string): Promise<InstallResult> {
  return request.post('/api/admin/tools/plugins/install', { manifest_url })
}

/** 从 LobeChat 索引一键安装 */
export function installFromIndex(identifier: string): Promise<InstallResult> {
  return request.post('/api/admin/tools/plugins/install-from-index', { identifier })
}

/** 预览 manifest（安装前检查） */
export function previewManifest(manifest_url: string): Promise<PluginManifest> {
  return request.post('/api/admin/tools/plugins/preview', { manifest_url })
}

/** 重新同步插件 manifest */
export function resyncPlugin(sourceId: string): Promise<{ success: boolean; tools_added: number; tools_removed: number; message: string }> {
  return request.post(`/api/admin/tools/plugins/${sourceId}/resync`)
}

// ========== 设置 API ==========

/** 获取插件设置 */
export function getPluginSettings(sourceId: string): Promise<PluginSettings> {
  return request.get(`/api/admin/tools/plugins/${sourceId}/settings`)
}

/** 更新插件设置 */
export function updatePluginSettings(sourceId: string, values: Record<string, any>): Promise<{ success: boolean; message: string }> {
  return request.put(`/api/admin/tools/plugins/${sourceId}/settings`, { values })
}
