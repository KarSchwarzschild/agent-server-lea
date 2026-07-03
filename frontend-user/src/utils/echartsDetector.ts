/**
 * ECharts 配置智能检测器
 *
 * 功能：
 * - 检测 JSON 是否为有效的 ECharts 配置
 * - 降低误判率（要求多个特征同时存在）
 *
 * 安全：
 * - 仅检测结构，不执行任何代码
 */

// ==================== 类型定义 ====================

export interface EChartsDetectionResult {
  isECharts: boolean
  confidence: 'high' | 'medium' | 'low' | 'none'
  reason?: string
}

// ==================== 检测逻辑 ====================

/**
 * ECharts 核心配置项
 * 这些是 ECharts 特有的配置项，普通 JSON 不太可能同时包含
 */
const ECHARTS_CORE_KEYS = [
  'series',      // 系列配置（必需）
  'xAxis',       // X 轴
  'yAxis',       // Y 轴
  'tooltip',     // 提示框
  'legend',      // 图例
  'grid',        // 网格
  'title',       // 标题
  'dataZoom',    // 数据缩放
  'visualMap',   // 视觉映射
  'toolbox',     // 工具栏
  'polar',       // 极坐标
  'radar',       // 雷达图
  'geo',         // 地理坐标
  'parallel',    // 平行坐标
  'calendar',    // 日历
  'dataset',     // 数据集
]

/**
 * ECharts series 类型
 */
const ECHARTS_SERIES_TYPES = [
  'line', 'bar', 'pie', 'scatter', 'effectScatter',
  'radar', 'tree', 'treemap', 'sunburst', 'boxplot',
  'candlestick', 'heatmap', 'map', 'parallel', 'lines',
  'graph', 'sankey', 'funnel', 'gauge', 'pictorialBar',
  'themeRiver', 'custom',
]

/**
 * 检测对象是否为 ECharts 配置
 */
export function isEChartsConfig(obj: unknown): EChartsDetectionResult {
  // 基础类型检查
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) {
    return { isECharts: false, confidence: 'none', reason: 'Not an object' }
  }

  const config = obj as Record<string, unknown>
  const keys = Object.keys(config)

  // 检测包含的 ECharts 核心配置项数量
  const coreKeysFound = ECHARTS_CORE_KEYS.filter(key => key in config)
  const coreKeyCount = coreKeysFound.length

  // 必须有 series 配置
  if (!('series' in config)) {
    // 没有 series，但有其他多个 ECharts 配置项，可能是部分配置
    if (coreKeyCount >= 3) {
      return { isECharts: true, confidence: 'low', reason: 'Multiple ECharts keys but no series' }
    }
    return { isECharts: false, confidence: 'none', reason: 'No series configuration' }
  }

  // 验证 series 结构
  const series = config.series
  if (!Array.isArray(series) && typeof series !== 'object') {
    return { isECharts: false, confidence: 'none', reason: 'Invalid series type' }
  }

  // 检查 series 中是否有有效的 type
  const seriesArray = Array.isArray(series) ? series : [series]
  const hasValidSeriesType = seriesArray.some((s: unknown) => {
    if (!s || typeof s !== 'object') return false
    const seriesObj = s as Record<string, unknown>
    return typeof seriesObj.type === 'string' &&
           ECHARTS_SERIES_TYPES.includes(seriesObj.type)
  })

  // 高置信度：有 series + 有效的 type + 至少 2 个其他核心配置项
  if (hasValidSeriesType && coreKeyCount >= 3) {
    return { isECharts: true, confidence: 'high', reason: 'Valid series type with multiple ECharts keys' }
  }

  // 中置信度：有 series + 有效的 type
  if (hasValidSeriesType) {
    return { isECharts: true, confidence: 'medium', reason: 'Valid series type' }
  }

  // 低置信度：有 series + 多个核心配置项，但没有有效的 type
  if (coreKeyCount >= 3) {
    return { isECharts: true, confidence: 'low', reason: 'Multiple ECharts keys' }
  }

  // 不确定
  return { isECharts: false, confidence: 'none', reason: 'Insufficient ECharts characteristics' }
}

/**
 * 检测 JSON 字符串是否为 ECharts 配置
 */
export function detectEChartsFromJson(jsonStr: string): EChartsDetectionResult {
  if (!jsonStr || typeof jsonStr !== 'string') {
    return { isECharts: false, confidence: 'none', reason: 'Invalid input' }
  }

  try {
    // 移除 JS 注释（单行和多行）
    const cleaned = jsonStr
      .replace(/\/\/.*$/gm, '')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .trim()

    const obj = JSON.parse(cleaned)
    return isEChartsConfig(obj)
  } catch {
    return { isECharts: false, confidence: 'none', reason: 'Invalid JSON' }
  }
}

/**
 * 快速检测（用于代码块语言判断）
 * 只返回 boolean，用于决定是否尝试渲染
 */
export function quickDetectECharts(code: string): boolean {
  if (!code || typeof code !== 'string') return false

  // 先尝试 JSON 检测
  const result = detectEChartsFromJson(code)
  if (result.isECharts && (result.confidence === 'high' || result.confidence === 'medium')) {
    return true
  }

  // JS 格式检测（移植自 md-viewer isEChartsConfig）
  return quickDetectEChartsJs(code)
}

/**
 * 检测 JavaScript 代码块是否为 ECharts 配置（移植自 md-viewer）
 * 基于特征属性匹配，不执行代码
 */
export function quickDetectEChartsJs(code: string): boolean {
  // 注释中包含 ECharts 标记
  if (/\/\/.*echarts|\/\*.*echarts.*\*\//i.test(code)) {
    return true
  }

  // 尝试提取对象内容
  const objectMatch = code.match(/(?:(?:const|var|let)\s+\w+\s*=\s*)?\{[\s\S]*\}/)
  if (!objectMatch) return false

  const objectStr = objectMatch[0]

  // 检查 ECharts 特征属性
  const echartsKeys = [
    'title', 'series', 'xAxis', 'yAxis', 'tooltip',
    'legend', 'grid', 'radar', 'dataZoom', 'visualMap',
  ]

  let matchCount = 0
  for (const key of echartsKeys) {
    if (new RegExp(`['"]?${key}['"]?\\s*:`).test(objectStr)) {
      matchCount++
    }
  }

  return matchCount >= 2
}
