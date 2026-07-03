/**
 * 特性开关 — 渐进点亮机制
 *
 * 设计目的：lifelong-edu frontend 有很多路由/组件是给"未来的 skill / 后端"用的。
 * lea-platform Ph1 只需主聊天 + 文档 + KB 就能演示。其余路由 hide 起来，
 * 后端就绪后在 .env 或 docker-compose 里翻牌，无需改代码。
 *
 * 分层：
 *  - Ph1: 硬编码 true，属于 M1 核心
 *  - Ph2: 环境变量开关，默认 false
 *  - Ph3: 环境变量开关，默认 false
 *  - Rendering: SSE 渲染相关开关
 *
 * 用法：
 *   import { isFeatureEnabled } from '@/config/features'
 *   if (isFeatureEnabled('ACADEMIC_SEARCH')) { ... }
 */

const flag = (envKey: string, defaultValue = false): boolean => {
  const raw = import.meta.env[envKey]
  if (raw === undefined) return defaultValue
  return raw === 'true' || raw === '1'
}

export const FEATURES = {
  // ==================== Ph1 核心（硬开启）====================
  SUPER_AGENT: true,        // 超级智能体主聊天
  DOCUMENTS: true,          // 文档中心
  KNOWLEDGE_BASE: true,     // 个人知识库
  HISTORY: true,            // 历史记录
  PROFILE: true,            // 个人中心

  // ==================== Ph2（.env 翻牌）====================
  AGENT_PLAZA: flag('VITE_FEATURE_AGENT_PLAZA'),        // 助手广场（需要 Agent CRUD）
  ACADEMIC_SEARCH: flag('VITE_FEATURE_ACADEMIC_SEARCH'), // 文献检索（需要学术搜索）
  REVIEW: flag('VITE_FEATURE_REVIEW'),                   // 综述系统（Research App）
  CHANNELS: flag('VITE_FEATURE_CHANNELS'),               // 消息渠道管理

  // ==================== Ph3（.env 翻牌）====================
  SUBSCRIPTION: flag('VITE_FEATURE_SUBSCRIPTION'),       // 智能订阅

  // ==================== SSE 渲染开关 ====================
  SHOW_THINKING: flag('VITE_SHOW_THINKING', true),       // 展示模型 reasoning content
  SHOW_TOOL_TIMELINE: flag('VITE_SHOW_TOOL_TIMELINE', true), // 展示工具调用时间线
} as const

export type FeatureFlag = keyof typeof FEATURES

export function isFeatureEnabled(flag: FeatureFlag): boolean {
  return FEATURES[flag] === true
}

/**
 * 路由 path → feature flag 的映射
 * router beforeEach 和 Layout 菜单过滤用这个表
 */
export const ROUTE_FEATURE_MAP: Record<string, FeatureFlag> = {
  '/agent-plaza': 'AGENT_PLAZA',
  '/search': 'ACADEMIC_SEARCH',
  '/review': 'REVIEW',
  '/review/quick': 'REVIEW',
  '/review/history': 'REVIEW',
  '/review/new': 'REVIEW',
  '/intelligence': 'SUBSCRIPTION',
  '/settings/channels': 'CHANNELS',
}

/**
 * 判断某个路径是否可访问（未映射的路径默认可访问）
 */
export function isRouteEnabled(path: string): boolean {
  // 精确或前缀匹配
  for (const [prefix, flag] of Object.entries(ROUTE_FEATURE_MAP)) {
    if (path === prefix || path.startsWith(prefix + '/')) {
      return isFeatureEnabled(flag)
    }
  }
  return true
}
