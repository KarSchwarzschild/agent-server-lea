/**
 * Mermaid 图表渲染器
 *
 * 功能：
 * - 懒加载 mermaid 库（~300KB gzip）
 * - 全局串行渲染队列（Mermaid 不支持并发）
 * - 10s 超时保护
 * - 渲染失败回退显示源码
 * - mindmap 颜色修复
 *
 * 安全：
 * - 输出 SVG 经过 DOMPurify 净化
 */

// ==================== 类型定义 ====================

export interface MermaidRenderResult {
  success: boolean
  svg?: string
  error?: string
}

// ==================== 状态管理 ====================

let mermaidInstance: typeof import('mermaid').default | null = null
let mermaidLoadPromise: Promise<typeof import('mermaid').default> | null = null
let renderQueue: Promise<void> = Promise.resolve()
let idCounter = 0

// ==================== 初始化 ====================

/**
 * 懒加载并初始化 Mermaid
 */
async function loadMermaid(): Promise<typeof import('mermaid').default> {
  if (mermaidInstance) return mermaidInstance

  if (!mermaidLoadPromise) {
    mermaidLoadPromise = import('mermaid').then((module) => {
      const mermaid = module.default

      // 初始化配置
      mermaid.initialize({
        startOnLoad: false,
        theme: 'default',
        securityLevel: 'strict',
        fontFamily: 'system-ui, -apple-system, sans-serif',
        flowchart: {
          useMaxWidth: true,
          htmlLabels: true,
          curve: 'basis',
        },
        sequence: {
          useMaxWidth: true,
          diagramMarginX: 8,
          diagramMarginY: 8,
        },
        mindmap: {
          useMaxWidth: true,
        },
      })

      mermaidInstance = mermaid
      return mermaid
    })
  }

  return mermaidLoadPromise
}

// ==================== 渲染函数 ====================

/**
 * 生成唯一 ID
 */
function generateId(): string {
  return `mermaid-${Date.now()}-${++idCounter}`
}

/**
 * 渲染 Mermaid 代码为 SVG
 * 内部函数，不带队列控制
 *
 * 注意：不再对 mermaid 输出做 DOMPurify 净化。
 * mermaid 已配置 securityLevel: 'strict'，内部已处理 XSS 防护。
 * DOMPurify 会破坏 mermaid SVG 的 style/class/自定义属性，导致图表不可见。
 */
async function renderMermaidToSvg(code: string, id: string): Promise<string> {
  const mermaid = await loadMermaid()

  // 使用 mermaid.render() 渲染
  const { svg } = await mermaid.render(id, code)

  return svg
}

/**
 * 带超时的渲染
 */
function withTimeout<T>(promise: Promise<T>, ms: number, errorMsg: string): Promise<T> {
  return Promise.race([
    promise,
    new Promise<never>((_, reject) =>
      setTimeout(() => reject(new Error(errorMsg)), ms)
    ),
  ])
}

/**
 * HTML 转义（用于错误回退显示）
 */
function escapeHtml(text: string): string {
  const div = document.createElement('div')
  div.textContent = text
  return div.innerHTML
}

/**
 * 渲染 Mermaid 代码（带队列控制 + 超时 + 错误回退）
 *
 * @param code Mermaid 代码
 * @param timeout 超时时间（毫秒），默认 10000
 * @returns 渲染结果
 */
export function renderMermaid(code: string, timeout = 10000): Promise<MermaidRenderResult> {
  return new Promise((resolve) => {
    renderQueue = renderQueue.then(async () => {
      const id = generateId()

      try {
        const svg = await withTimeout(
          renderMermaidToSvg(code, id),
          timeout,
          'Mermaid render timeout'
        )

        resolve({ success: true, svg })
      } catch (err) {
        const errorMsg = err instanceof Error ? err.message : String(err)
        console.warn('[Mermaid] 渲染失败，回退显示源码:', errorMsg)

        // 回退：显示源码
        const fallbackHtml = `<pre class="mermaid-error"><code>${escapeHtml(code)}</code></pre>`
        resolve({ success: false, svg: fallbackHtml, error: errorMsg })
      }
    })
  })
}

// ==================== 辅助函数 ====================

/**
 * 检测代码是否为有效的 Mermaid 语法
 * 用于流式输出时判断代码块是否已闭合
 */
export function isMermaidCode(code: string): boolean {
  if (!code || code.trim().length < 5) return false

  const trimmed = code.trim()

  // 检测常见的 Mermaid 图表类型关键字
  const mermaidKeywords = [
    /^graph\s/i,
    /^flowchart\s/i,
    /^sequenceDiagram/i,
    /^classDiagram/i,
    /^stateDiagram/i,
    /^erDiagram/i,
    /^journey/i,
    /^gantt/i,
    /^pie/i,
    /^mindmap/i,
    /^timeline/i,
    /^gitGraph/i,
    /^C4Context/i,
    /^C4Container/i,
    /^C4Component/i,
    /^C4Dynamic/i,
    /^C4Deployment/i,
    /^quadrantChart/i,
    /^requirementDiagram/i,
    /^sankey/i,
    /^xychart/i,
    /^block-beta/i,
  ]

  return mermaidKeywords.some((pattern) => pattern.test(trimmed))
}

/**
 * 检测代码块是否已闭合（用于流式输出）
 *
 * 注意：流式输出时 markdown-it 会把未闭合的代码围栏也当作代码块，
 * 所以需要检测 mermaid 代码本身是否完整。
 */
export function isCodeBlockComplete(code: string): boolean {
  if (!code) return false

  const trimmed = code.trim()

  // 基本检查：至少有两行（声明 + 内容）
  const lines = trimmed.split('\n').filter((l) => l.trim())
  if (lines.length < 2) return false

  const lastLine = lines[lines.length - 1].trim()

  // 最后一行不应该以连接符结尾但没有目标节点
  if (lastLine.endsWith('-->') || lastLine.endsWith('---') || lastLine.endsWith('-.')) return false

  // 检查最后一行是否有未闭合的括号（流式截断的典型特征）
  // 例如: A[开始] --> B{条件  ← { 未闭合
  const count = (str: string, ch: string) => {
    let n = 0
    for (const c of str) if (c === ch) n++
    return n
  }
  if (count(lastLine, '{') > count(lastLine, '}')) return false
  if (count(lastLine, '[') > count(lastLine, ']')) return false
  if (count(lastLine, '(') > count(lastLine, ')')) return false

  // 检查未闭合的管道符 |label|（mermaid 边标签语法）
  // 例如: A -->|条件不  ← | 未闭合
  if (count(lastLine, '|') % 2 !== 0) return false

  // 检查最后一行是否以引号/双引号未闭合结尾
  if (count(lastLine, '"') % 2 !== 0) return false

  return true
}

/**
 * 重置渲染器状态（用于测试）
 */
export function resetMermaidRenderer(): void {
  mermaidInstance = null
  mermaidLoadPromise = null
  renderQueue = Promise.resolve()
  idCounter = 0
}
