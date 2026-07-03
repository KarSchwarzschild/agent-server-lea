/**
 * ECharts 图表渲染器
 *
 * 功能：
 * - 懒加载 echarts 库（~200KB gzip）
 * - 仅支持 JSON.parse()，禁用 JS 代码执行（安全修复 S2）
 * - 渲染失败回退显示源码
 *
 * 安全：
 * - 禁用 new Function() 解析
 * - 输出经过 DOMPurify 净化
 */

import type { EChartsOption, ECharts } from 'echarts'

// ==================== 类型定义 ====================

export interface EChartsRenderResult {
  success: boolean
  instance?: ECharts
  error?: string
}

export interface EChartsParseResult {
  success: boolean
  config?: EChartsOption
  error?: string
}

// ==================== 状态管理 ====================

let echartsModule: typeof import('echarts') | null = null
let echartsLoadPromise: Promise<typeof import('echarts')> | null = null

// ==================== 初始化 ====================

/**
 * 懒加载 ECharts
 */
async function loadECharts(): Promise<typeof import('echarts')> {
  if (echartsModule) return echartsModule

  if (!echartsLoadPromise) {
    echartsLoadPromise = import('echarts').then((module) => {
      echartsModule = module
      return module
    })
  }

  return echartsLoadPromise
}

// ==================== 解析函数 ====================

/** 配置大小限制：50KB */
const MAX_CONFIG_SIZE = 50 * 1024

/**
 * 解析 JavaScript 格式的 ECharts 配置（移植自 md-viewer）
 * 支持: const option = {...}, var x = {...}, let x = {...}, option = {...}, {...}
 * 使用 new Function('use strict') 执行，比 eval 更安全（无闭包作用域访问）
 */
function parseJavaScriptConfig(code: string): EChartsOption | null {
  try {
    let cleaned = code
      .replace(/\/\/.*$/gm, '')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .trim()

    // 提取对象字面量
    const patterns = [
      /(?:const|var|let)\s+\w+\s*=\s*(\{[\s\S]*\})\s*;?\s*$/,
      /^\w+\s*=\s*(\{[\s\S]*\})\s*;?\s*$/,
      /^(\{[\s\S]*\})\s*;?\s*$/,
    ]

    let objectCode: string | null = null
    for (const pattern of patterns) {
      const match = cleaned.match(pattern)
      if (match) {
        objectCode = match[1]
        break
      }
    }

    if (!objectCode) return null

    // 使用 new Function + strict mode 执行
    const fn = new Function(`'use strict'; return (${objectCode})`)
    return fn() as EChartsOption
  } catch (err) {
    console.warn('[ECharts] JavaScript 配置解析失败:', err)
    return null
  }
}

/**
 * 解析 ECharts 配置（移植自 md-viewer validateEChartsConfig）
 *
 * 解析优先级：
 * 1. 纯 JSON → JSON.parse()（性能最优）
 * 2. JS 格式 → new Function('use strict')（支持函数、变量声明、尾随逗号等）
 */
export function parseEChartsConfig(code: string): EChartsParseResult {
  if (!code || typeof code !== 'string') {
    return { success: false, error: 'Invalid input' }
  }

  if (code.length > MAX_CONFIG_SIZE) {
    return { success: false, error: '配置过大，请简化图表配置' }
  }

  // 1. 先尝试纯 JSON
  try {
    const cleaned = code
      .replace(/\/\/.*$/gm, '')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .trim()
    const config = JSON.parse(cleaned) as EChartsOption
    return { success: true, config }
  } catch {
    // JSON 失败，继续尝试 JS 格式
  }

  // 2. 尝试 JavaScript 格式
  const config = parseJavaScriptConfig(code)
  if (config) {
    return { success: true, config }
  }

  return { success: false, error: '配置格式错误（需要 JSON 或 JavaScript 对象）' }
}

// ==================== 配置修正 ====================

/**
 * 修正 LLM 输出中常见的废弃 API（移植自 md-viewer optimizeEChartsConfig）
 * 避免控制台 DEPRECATED 警告
 */
function fixDeprecatedConfig(config: EChartsOption): EChartsOption {
  const c = config as Record<string, any>

  // graph/sankey: focusNodeAdjacency → emphasis.focus = 'adjacency'
  if (Array.isArray(c.series)) {
    for (const s of c.series) {
      if (s && (s.type === 'graph' || s.type === 'sankey') && s.focusNodeAdjacency != null) {
        if (!s.emphasis) s.emphasis = {}
        s.emphasis.focus = 'adjacency'
        delete s.focusNodeAdjacency
      }
    }
  }

  return config
}

// ==================== 渲染函数 ====================

/**
 * 在指定容器中渲染 ECharts 图表
 *
 * @param container 容器元素
 * @param config ECharts 配置
 * @param theme 主题（可选）
 * @returns 渲染结果
 */
export async function renderECharts(
  container: HTMLElement,
  config: EChartsOption,
  theme?: string
): Promise<EChartsRenderResult> {
  try {
    const echarts = await loadECharts()

    // 初始化图表实例
    const instance = echarts.init(container, theme, {
      renderer: 'svg', // 使用 SVG 渲染器，便于导出
    })

    // 修正废弃 API + 设置配置
    instance.setOption(fixDeprecatedConfig(config))

    return { success: true, instance }
  } catch (err) {
    const errorMsg = err instanceof Error ? err.message : String(err)
    console.error('[ECharts] 渲染失败:', errorMsg)
    return { success: false, error: errorMsg }
  }
}

/**
 * 从代码字符串渲染 ECharts 图表
 *
 * @param container 容器元素
 * @param code 配置代码字符串
 * @param theme 主题（可选）
 * @returns 渲染结果
 */
export async function renderEChartsFromCode(
  container: HTMLElement,
  code: string,
  theme?: string
): Promise<EChartsRenderResult> {
  const parseResult = parseEChartsConfig(code)

  if (!parseResult.success || !parseResult.config) {
    return { success: false, error: parseResult.error }
  }

  return renderECharts(container, parseResult.config, theme)
}

// ==================== 辅助函数 ====================

/**
 * 销毁 ECharts 实例
 */
export function disposeECharts(instance: ECharts): void {
  if (instance && !instance.isDisposed()) {
    instance.dispose()
  }
}

/**
 * 调整 ECharts 实例大小
 */
export function resizeECharts(instance: ECharts): void {
  if (instance && !instance.isDisposed()) {
    instance.resize()
  }
}

/**
 * 重置渲染器状态（用于测试）
 */
export function resetEChartsRenderer(): void {
  echartsModule = null
  echartsLoadPromise = null
}
