/**
 * 工具管理 API
 *
 * 包含：工具列表/详情/启用禁用、工具源 CRUD/同步、工具创建/删除/测试
 */
import request from '../utils/request'

// ========== 类型定义 ==========

export interface ToolParam {
  name: string
  type: string
  description: string
  required: boolean
  default?: any
}

export interface ToolInfo {
  name: string
  description: string
  display_name?: string  // 管理员自定义显示名（中文）
  category: string
  enabled: boolean
  source_type: string  // builtin / openapi / mcp / client
  source_id?: string
  parameters: ToolParam[]
}

export interface ToolDetail extends ToolInfo {
  returns: string
  exec_type?: string
  http_method?: string
  http_path?: string
  source_name?: string
  used_by_skills: Array<{ name: string; display_name: string }>
}

export interface ToolListResponse {
  tools: ToolInfo[]
  disabled: string[]
}

// 工具源
export interface ToolSourceConfig {
  // OpenAPI
  spec_url?: string
  spec_content?: string
  base_url?: string
  auth?: { type: string; token?: string; key?: string; header?: string; username?: string; password?: string }
  // MCP
  transport?: string
  command?: string
  args?: string[]
  url?: string
}

export interface ToolSource {
  id: string
  name: string
  type: 'openapi' | 'mcp' | 'client' | 'plugin'
  config: ToolSourceConfig
  is_enabled: boolean
  tool_count: number
  created_by: string
  created_at: string
  updated_at?: string
  needs_settings?: boolean  // 仅 plugin 类型返回
}

export interface ToolSourceDetail extends ToolSource {
  tools: Array<{
    id: string
    name: string
    description: string
    input_schema?: object
    exec_type: string
    http_method?: string
    http_path?: string
    is_enabled: boolean
  }>
}

// ========== 工具 API ==========

/** 获取所有工具及状态 */
export function getTools(): Promise<ToolListResponse> {
  return request.get('/api/admin/tools')
}

/** 获取工具详情 */
export function getToolDetail(toolName: string): Promise<ToolDetail> {
  return request.get(`/api/admin/tools/${toolName}`)
}

/** 禁用工具 */
export function disableTool(toolName: string): Promise<{ success: boolean; message: string }> {
  return request.post(`/api/admin/tools/${toolName}/disable`)
}

/** 启用工具 */
export function enableTool(toolName: string): Promise<{ success: boolean; message: string }> {
  return request.post(`/api/admin/tools/${toolName}/enable`)
}

/** 更新工具显示名 */
export function updateToolDisplayName(toolName: string, displayName: string): Promise<{ success: boolean; message: string }> {
  return request.patch(`/api/admin/tools/${toolName}`, { display_name: displayName })
}

/** 删除工具 */
export function deleteTool(toolName: string): Promise<{ success: boolean; message: string }> {
  return request.delete(`/api/admin/tools/${toolName}/delete`)
}

/** 测试执行工具 */
export function testTool(toolName: string, input: Record<string, any>): Promise<{ success: boolean; result?: string; error?: string }> {
  return request.post(`/api/admin/tools/${toolName}/test`, { input })
}

/** 创建工具（Client 源） */
export function createTool(data: {
  name: string
  description: string
  input_schema?: object
  source_id: string
}): Promise<{ success: boolean; tool_id: string; message: string }> {
  return request.post('/api/admin/tools/create', data)
}

// ========== 工具源 API ==========

/** 获取所有工具源 */
export function getToolSources(): Promise<{ sources: ToolSource[] }> {
  return request.get('/api/admin/tools/sources/list')
}

/** 创建工具源 */
export function createToolSource(data: {
  name: string
  type: 'openapi' | 'mcp' | 'client'
  config: ToolSourceConfig
}): Promise<{ success: boolean; source_id: string; tools_count: number; message: string }> {
  return request.post('/api/admin/tools/sources', data)
}

/** 获取工具源详情 */
export function getToolSourceDetail(sourceId: string): Promise<ToolSourceDetail> {
  return request.get(`/api/admin/tools/sources/${sourceId}`)
}

/** 删除工具源 */
export function deleteToolSource(sourceId: string): Promise<{ success: boolean; message: string }> {
  return request.delete(`/api/admin/tools/sources/${sourceId}`)
}

/** 启用/禁用工具源 */
export function toggleToolSource(sourceId: string): Promise<{
  success: boolean
  is_enabled: boolean
  affected_tools: number
  message: string
}> {
  return request.post(`/api/admin/tools/sources/${sourceId}/toggle`)
}

/** 同步工具源 */
export function syncToolSource(sourceId: string): Promise<{ success: boolean; tools_count: number; message: string }> {
  return request.post(`/api/admin/tools/sources/${sourceId}/sync`)
}
