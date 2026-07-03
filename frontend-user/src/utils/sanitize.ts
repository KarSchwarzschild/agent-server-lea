/**
 * 统一 DOMPurify 配置模块
 *
 * 功能：
 * 1. KaTeX 公式保护（随机 token 机制，防止占位符伪造）
 * 2. SVG 白名单（支持 Mermaid/ECharts 图表渲染）
 * 3. 安全加固（FORBID_TAGS/FORBID_ATTR）
 *
 * 安全修复：
 * - S1: KaTeX 占位符使用随机 UUID token，回填时校验 class="katex"
 * - S3: 从 SVG 白名单移除 foreignObject（可嵌入任意 HTML）
 */
import DOMPurify from 'dompurify'

// ==================== 配置常量 ====================

/** SVG 相关标签白名单（Mermaid/ECharts 渲染需要） */
const SVG_TAGS = [
  'svg', 'path', 'rect', 'circle', 'ellipse', 'line',
  'polyline', 'polygon', 'g', 'defs', 'use', 'text',
  'tspan', 'marker', 'clipPath', 'mask', 'pattern',
  'linearGradient', 'radialGradient', 'stop', 'image',
  'symbol', 'title', 'desc',
]

/** SVG 相关属性白名单 */
const SVG_ATTRS = [
  'viewBox', 'd', 'fill', 'stroke', 'stroke-width', 'stroke-linecap',
  'stroke-linejoin', 'stroke-dasharray', 'stroke-dashoffset', 'stroke-opacity',
  'fill-opacity', 'opacity', 'transform', 'x', 'y', 'x1', 'y1', 'x2', 'y2',
  'rx', 'ry', 'cx', 'cy', 'r', 'width', 'height', 'preserveAspectRatio',
  'xmlns', 'xmlns:xlink', 'xlink:href', 'href', 'points', 'offset',
  'stop-color', 'stop-opacity', 'gradientUnits', 'gradientTransform',
  'patternUnits', 'patternTransform', 'markerWidth', 'markerHeight',
  'refX', 'refY', 'orient', 'markerUnits', 'clip-path', 'mask',
  'font-size', 'font-family', 'font-weight', 'text-anchor', 'dominant-baseline',
  'alignment-baseline', 'dx', 'dy', 'textLength', 'lengthAdjust',
]

/** 禁止的标签（安全加固） */
const FORBID_TAGS = ['script', 'style', 'iframe', 'object', 'embed', 'foreignObject']

/** Mermaid/ECharts SVG 专用禁止标签（允许 <style>，因为 SVG 依赖内联样式表） */
const SVG_FORBID_TAGS = ['script', 'iframe', 'object', 'embed', 'foreignObject']

/** 禁止的属性（防止内联事件 XSS） */
const FORBID_ATTR = [
  'onerror', 'onload', 'onclick', 'onmouseover', 'onmouseout',
  'onfocus', 'onblur', 'onchange', 'onsubmit', 'onkeydown',
  'onkeyup', 'onkeypress', 'oncontextmenu',
]

// ==================== 基础配置 ====================

/** 基础 DOMPurify 配置（不含 SVG） */
export const BASE_SANITIZE_CONFIG: DOMPurify.Config = {
  ADD_ATTR: ['target', 'rel', 'class'],
  FORBID_TAGS,
  FORBID_ATTR,
  ALLOWED_URI_REGEXP: /^(?:https?|mailto|tel|data:image\/)/,
}

/** 带 SVG 支持的 DOMPurify 配置（用于图表渲染） */
export const SVG_SANITIZE_CONFIG: DOMPurify.Config = {
  ...BASE_SANITIZE_CONFIG,
  ADD_TAGS: SVG_TAGS,
  ADD_ATTR: [...(BASE_SANITIZE_CONFIG.ADD_ATTR as string[]), ...SVG_ATTRS],
}

/** 带引用数据属性的配置（用于学术引用 + 音频播放器） */
export const CITATION_SANITIZE_CONFIG: DOMPurify.Config = {
  ...SVG_SANITIZE_CONFIG,
  ADD_TAGS: [...(SVG_SANITIZE_CONFIG.ADD_TAGS as string[] || []), 'audio'],
  ADD_ATTR: [
    ...(SVG_SANITIZE_CONFIG.ADD_ATTR as string[]),
    'data-ref-id', 'data-ref-title', 'data-ref-authors',
    'data-ref-year', 'data-ref-source', 'data-ref-url', 'data-ref-abstract',
    'data-audio-file-id', 'controls', 'preload', 'src',
  ],
}

// ==================== KaTeX 保护机制 ====================

/**
 * 生成随机 token（用于 KaTeX 占位符）
 * 使用 crypto.randomUUID() 确保不可预测
 */
function generateKatexToken(): string {
  // 浏览器环境使用 crypto.randomUUID()
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return `@@KATEX_${crypto.randomUUID().replace(/-/g, '')}@@`
  }
  // 降级方案：Math.random()（安全性较低但可用）
  return `@@KATEX_${Math.random().toString(36).slice(2)}${Date.now().toString(36)}@@`
}

/**
 * KaTeX 输出匹配正则
 * 匹配 <span class="katex"> 或 <span class="katex-display"> 及其内容
 */
const KATEX_OUTPUT_REGEX = /<span class="katex(?:-display)?">[\s\S]*?<\/span>(?=(?:<\/span>)*(?:[^<]|<(?!span)|$))/g

/**
 * 带 KaTeX 保护的 HTML 净化
 *
 * 工作流程：
 * 1. 用随机 token 替换 KaTeX 输出（保护精确定位样式）
 * 2. 执行 DOMPurify.sanitize()
 * 3. 还原 KaTeX 内容（校验 class="katex" 防止伪造）
 *
 * @param html 原始 HTML
 * @param config DOMPurify 配置（默认使用 SVG 配置）
 * @returns 净化后的 HTML
 */
export function sanitizeWithKatex(
  html: string,
  config: DOMPurify.Config = SVG_SANITIZE_CONFIG
): string {
  if (!html) return ''

  const katexMap = new Map<string, string>()

  // 1. 用随机 token 替换 KaTeX 输出
  // 匹配完整的 katex span（包括嵌套的 span）
  let processed = html
  let match: RegExpExecArray | null

  // 重置正则状态
  KATEX_OUTPUT_REGEX.lastIndex = 0

  // 使用更精确的匹配：找到 class="katex" 的 span，然后匹配到对应的闭合标签
  processed = html.replace(
    /<span class="katex(?:-display)?"[^>]*>[\s\S]*?<\/span>(?:<\/span>)*(?=\s*(?:[^<]|<(?!\/span)|$))/g,
    (match) => {
      // 验证这确实是 KaTeX 输出（包含 katex-html 或 katex-mathml）
      if (match.includes('class="katex') && (match.includes('katex-html') || match.includes('katex-mathml'))) {
        const token = generateKatexToken()
        katexMap.set(token, match)
        return token
      }
      return match
    }
  )

  // 2. 执行 sanitize
  let sanitized = DOMPurify.sanitize(processed, config)

  // 3. 还原 KaTeX 内容（校验防止伪造）
  for (const [token, content] of katexMap) {
    // 二次校验：确保内容确实包含 class="katex"
    if (content.includes('class="katex')) {
      sanitized = sanitized.replace(token, content)
    }
    // 如果校验失败，token 会被保留在输出中（可见的异常标记）
  }

  return sanitized
}

// ==================== 便捷函数 ====================

/**
 * 基础 HTML 净化（不含 KaTeX 保护）
 */
export function sanitizeHtml(html: string, config?: DOMPurify.Config): string {
  return DOMPurify.sanitize(html, config || BASE_SANITIZE_CONFIG)
}

/**
 * 带 SVG 支持的 HTML 净化（用于图表渲染）
 * 允许 <style> 标签，因为 Mermaid/ECharts SVG 依赖内联样式表
 */
export function sanitizeSvgHtml(html: string): string {
  return DOMPurify.sanitize(html, {
    ...SVG_SANITIZE_CONFIG,
    FORBID_TAGS: SVG_FORBID_TAGS,
  })
}

/**
 * 带引用数据属性的 HTML 净化（用于学术引用）
 */
export function sanitizeCitationHtml(html: string): string {
  return sanitizeWithKatex(html, CITATION_SANITIZE_CONFIG)
}

/**
 * 轻量级净化（用于工具结果、子代理输出等）
 * 不需要 KaTeX 保护，只需基础 XSS 防护
 */
export function sanitizeLightHtml(html: string): string {
  return DOMPurify.sanitize(html, {
    ADD_ATTR: ['target', 'rel'],
    FORBID_TAGS,
    FORBID_ATTR,
  })
}
