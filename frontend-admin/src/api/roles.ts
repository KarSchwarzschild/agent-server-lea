/**
 * 角色权限管理 API
 */
import request from '../utils/request'

// ========== 类型定义 ==========

/** 权限 */
export interface Permission {
  id: number
  code: string
  name: string
  resource: string
  action: string
  description?: string
}

/** 角色 */
export interface Role {
  id: number
  code: string
  name: string
  description?: string
  is_system: boolean
  is_active: boolean
  permissions: string[]
  user_count: number
  created_at: string
}

/** 创建角色请求 */
export interface CreateRoleRequest {
  code: string
  name: string
  description?: string
  permissions: string[]
}

/** 更新角色请求 */
export interface UpdateRoleRequest {
  name?: string
  description?: string
  permissions?: string[]
}

/** 角色列表响应 */
export interface RoleListResponse {
  items: Role[]
  total: number
}

/** 角色统计 */
export interface RoleStats {
  total_roles: number
  system_roles: number
  custom_roles: number
  total_permissions: number
  by_resource: Record<string, number>
}

/** 角色查询参数 */
export interface RoleQueryParams {
  page?: number
  page_size?: number
  search?: string
  is_system?: boolean
}

// ========== 常量 ==========

/** 资源类型映射 */
export const RESOURCE_NAME_MAP: Record<string, string> = {
  user: '用户管理',
  role: '角色管理',
  document: '公共知识库管理',
  kb: '知识库',
  stats: '统计',
  log: '日志',
  config: '配置'
}

/** 操作类型映射 */
export const ACTION_NAME_MAP: Record<string, string> = {
  read: '查看',
  write: '编辑',
  delete: '删除'
}

/** 系统角色标签颜色 */
export const ROLE_TAG_COLORS: Record<string, 'error' | 'warning' | 'success' | 'info' | 'default'> = {
  super_admin: 'error',
  admin: 'warning',
  editor: 'success',
  user: 'info'
}

// ========== API 函数 ==========

/**
 * 获取角色列表
 */
export const getRoles = (params?: RoleQueryParams): Promise<RoleListResponse> => {
  return request.get('/api/admin/roles', { params })
}

/**
 * 获取角色统计
 */
export const getRoleStats = (): Promise<RoleStats> => {
  return request.get('/api/admin/roles/stats')
}

/**
 * 获取权限列表
 */
export const getPermissions = (): Promise<Permission[]> => {
  return request.get('/api/admin/roles/permissions')
}

/**
 * 获取资源类型列表
 */
export const getResources = (): Promise<string[]> => {
  return request.get('/api/admin/roles/resources')
}

/**
 * 获取角色详情
 */
export const getRole = (roleId: number): Promise<Role> => {
  return request.get(`/api/admin/roles/${roleId}`)
}

/**
 * 创建角色
 */
export const createRole = (data: CreateRoleRequest): Promise<Role> => {
  return request.post('/api/admin/roles', data)
}

/**
 * 更新角色
 */
export const updateRole = (roleId: number, data: UpdateRoleRequest): Promise<Role> => {
  return request.put(`/api/admin/roles/${roleId}`, data)
}

/**
 * 删除角色
 */
export const deleteRole = (roleId: number): Promise<void> => {
  return request.delete(`/api/admin/roles/${roleId}`)
}

/**
 * 初始化系统角色和权限
 */
export const initializeRoles = (): Promise<{
  message: string
  roles_count: number
  permissions_count: number
}> => {
  return request.post('/api/admin/roles/initialize')
}

// ========== 工具函数 ==========

/**
 * 获取资源中文名称
 */
export function getResourceName(resource: string): string {
  return RESOURCE_NAME_MAP[resource] || resource
}

/**
 * 获取操作中文名称
 */
export function getActionName(action: string): string {
  return ACTION_NAME_MAP[action] || action
}

/**
 * 格式化权限显示名称
 */
export function formatPermissionName(permission: Permission): string {
  const resource = getResourceName(permission.resource)
  const action = getActionName(permission.action)
  return `${resource} - ${action}`
}

/**
 * 按资源分组权限
 */
export function groupPermissionsByResource(permissions: Permission[]): Record<string, Permission[]> {
  const grouped: Record<string, Permission[]> = {}
  for (const perm of permissions) {
    const resource = perm.resource
    if (!grouped[resource]) {
      grouped[resource] = []
    }
    grouped[resource].push(perm)
  }
  return grouped
}

/**
 * 获取角色标签类型
 */
export function getRoleTagType(roleCode: string): 'default' | 'error' | 'warning' | 'success' | 'info' {
  return ROLE_TAG_COLORS[roleCode] || 'default'
}
