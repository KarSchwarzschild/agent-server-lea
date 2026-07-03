/**
 * 图表工具函数
 *
 * 功能：
 * - SVG 导出为 PNG（2x 高清）
 * - 复制代码到剪贴板
 * - 全屏显示
 */

// ==================== 类型定义 ====================

export interface DownloadOptions {
  /** 文件名（不含扩展名） */
  filename?: string
  /** 缩放倍数（默认 2x 高清） */
  scale?: number
  /** 背景色（默认白色） */
  backgroundColor?: string
}

// ==================== 导出函数 ====================

/**
 * 将 SVG 元素导出为 PNG 并下载
 *
 * 移植自 md-viewer，经过验证的实现：
 * - 使用 getBBox() 裁剪到实际内容区域，去除空白
 * - data URL 方式加载（同源，不触发 canvas tainted）
 * - Mermaid securityLevel:'strict' 不会产生 foreignObject，无需额外处理
 */
export function downloadSvgAsPng(
  svgElement: SVGElement | HTMLElement,
  options: DownloadOptions = {}
): void {
  const {
    filename = 'chart',
    scale = 2,
    backgroundColor = '#ffffff',
  } = options

  let svg: SVGSVGElement | null = null
  if (svgElement instanceof SVGSVGElement) {
    svg = svgElement
  } else {
    svg = svgElement.querySelector('svg')
  }

  if (!svg) {
    console.error('[downloadSvgAsPng] 未找到 SVG 元素')
    return
  }

  const svgClone = svg.cloneNode(true) as SVGSVGElement
  const padding = 10

  // 使用 getBBox() 裁剪到实际内容区域
  let width: number
  let height: number
  try {
    const bbox = svg.getBBox()
    if (bbox.width > 0 && bbox.height > 0) {
      const vx = bbox.x - padding
      const vy = bbox.y - padding
      const vw = bbox.width + padding * 2
      const vh = bbox.height + padding * 2
      svgClone.setAttribute('viewBox', `${vx} ${vy} ${vw} ${vh}`)
      svgClone.setAttribute('width', String(vw))
      svgClone.setAttribute('height', String(vh))
      width = vw
      height = vh
    } else {
      width = svg.clientWidth || 800
      height = svg.clientHeight || 600
    }
  } catch {
    width = svg.clientWidth || 800
    height = svg.clientHeight || 600
  }

  const svgData = new XMLSerializer().serializeToString(svgClone)
  const canvas = document.createElement('canvas')
  canvas.width = width * scale
  canvas.height = height * scale
  const ctx = canvas.getContext('2d')!
  const img = new Image()

  img.onload = () => {
    ctx.fillStyle = backgroundColor
    ctx.fillRect(0, 0, canvas.width, canvas.height)
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height)

    const a = document.createElement('a')
    a.download = `${filename}.png`
    a.href = canvas.toDataURL('image/png')
    a.click()
  }

  img.onerror = () => {
    console.error('[downloadSvgAsPng] SVG 加载失败')
  }

  img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svgData)
}

/**
 * 复制文本到剪贴板
 *
 * @param text 要复制的文本
 */
export async function copyToClipboard(text: string): Promise<void> {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    await navigator.clipboard.writeText(text)
  } else {
    // 降级方案
    const textarea = document.createElement('textarea')
    textarea.value = text
    textarea.style.position = 'fixed'
    textarea.style.left = '-9999px'
    document.body.appendChild(textarea)
    textarea.select()
    document.execCommand('copy')
    document.body.removeChild(textarea)
  }
}

/**
 * 全屏显示元素
 *
 * @param element 要全屏的元素
 */
export async function enterFullscreen(element: HTMLElement): Promise<void> {
  if (element.requestFullscreen) {
    await element.requestFullscreen()
  } else if ((element as any).webkitRequestFullscreen) {
    await (element as any).webkitRequestFullscreen()
  } else if ((element as any).mozRequestFullScreen) {
    await (element as any).mozRequestFullScreen()
  } else if ((element as any).msRequestFullscreen) {
    await (element as any).msRequestFullscreen()
  }
}

/**
 * 退出全屏
 */
export async function exitFullscreen(): Promise<void> {
  if (document.exitFullscreen) {
    await document.exitFullscreen()
  } else if ((document as any).webkitExitFullscreen) {
    await (document as any).webkitExitFullscreen()
  } else if ((document as any).mozCancelFullScreen) {
    await (document as any).mozCancelFullScreen()
  } else if ((document as any).msExitFullscreen) {
    await (document as any).msExitFullscreen()
  }
}

/**
 * 检查是否处于全屏状态
 */
export function isFullscreen(): boolean {
  return !!(
    document.fullscreenElement ||
    (document as any).webkitFullscreenElement ||
    (document as any).mozFullScreenElement ||
    (document as any).msFullscreenElement
  )
}

// ==================== 图表包装器（移植自 md-viewer） ====================

export interface ChartWrapperOptions {
  /** 图表类型 */
  chartType: 'mermaid' | 'echarts'
  /** 原始代码 */
  code: string
  /** 代码高亮语言 */
  language?: string
}

export interface ChartWrapperResult {
  wrapper: HTMLElement
  toggleBar: HTMLElement
  chartContainer: HTMLElement
  codeView: HTMLElement
}

/**
 * 创建图表通用 DOM 包装结构（移植自 md-viewer createChartWrapper）
 *
 * 结构：wrapper > toggleBar + chartContainer + codeView
 * - toggleBar: 悬停显示的工具栏（查看代码/下载/全屏）
 * - chartContainer: 图表视图（data-view="chart"）
 * - codeView: 代码视图（data-view="code"，默认隐藏）
 *   - backBtn: 返回图表按钮
 *   - copyBtn: 复制代码按钮
 *   - pre > code: 代码内容
 */
export function createChartWrapper(options: ChartWrapperOptions): ChartWrapperResult {
  const { chartType, code, language = chartType === 'mermaid' ? 'mermaid' : 'json' } = options

  // 包装容器
  const wrapper = document.createElement('div')
  wrapper.className = `${chartType}-chart-wrapper`

  // 工具栏
  const toggleBar = document.createElement('div')
  toggleBar.className = 'chart-toggle-bar no-export'

  const actions = [
    { action: 'toggleCode', label: '💻', tooltip: '查看代码' },
    { action: 'download', label: '💾', tooltip: '下载图片' },
    { action: 'fullscreen', label: '⛶', tooltip: '全屏查看' },
  ]
  for (const { action, label, tooltip } of actions) {
    const btn = document.createElement('button')
    btn.className = 'chart-action-btn'
    btn.dataset.action = action
    btn.dataset.tooltip = tooltip
    btn.textContent = label
    toggleBar.appendChild(btn)
  }

  // 图表容器
  const chartContainer = document.createElement('div')
  chartContainer.className = `${chartType}-container`
  chartContainer.dataset.view = 'chart'

  // 代码视图
  const codeView = document.createElement('div')
  codeView.className = 'chart-code-view'
  codeView.dataset.view = 'code'
  codeView.style.display = 'none'

  // 顶部按钮栏（右上角，图表+复制并排）
  const codeToolbar = document.createElement('div')
  codeToolbar.className = 'chart-code-toolbar'

  const backBtn = document.createElement('button')
  backBtn.className = 'chart-code-btn no-export'
  backBtn.textContent = '📊'
  backBtn.dataset.tooltip = '返回图表'
  codeToolbar.appendChild(backBtn)

  const copyBtn = document.createElement('button')
  copyBtn.className = 'chart-code-btn no-export'
  copyBtn.textContent = '📋'
  copyBtn.dataset.tooltip = '复制代码'
  codeToolbar.appendChild(copyBtn)

  codeView.appendChild(codeToolbar)

  // 代码内容（不带 language class，避免被 hljs 覆盖颜色）
  const codeEl = document.createElement('code')
  codeEl.textContent = code

  const preEl = document.createElement('pre')
  preEl.appendChild(codeEl)
  codeView.appendChild(preEl)

  // 组装
  wrapper.appendChild(toggleBar)
  wrapper.appendChild(chartContainer)
  wrapper.appendChild(codeView)

  // ==================== 事件绑定 ====================

  // 工具栏按钮点击
  toggleBar.addEventListener('click', (e) => {
    e.stopPropagation()
    const target = (e.target as HTMLElement).closest('[data-action]') as HTMLElement
    if (!target) return

    const action = target.dataset.action
    if (action === 'toggleCode') {
      // 切换到代码视图
      chartContainer.style.display = 'none'
      toggleBar.style.display = 'none'
      codeView.style.display = ''
    } else if (action === 'download') {
      downloadSvgAsPng(wrapper, { filename: `${chartType}-chart-${Date.now()}` })
    } else if (action === 'fullscreen') {
      if (isFullscreen()) {
        exitFullscreen().catch(() => {})
      } else {
        enterFullscreen(wrapper).catch(() => {})
      }
    }
  })

  // 返回按钮
  backBtn.addEventListener('click', (e) => {
    e.stopPropagation()
    chartContainer.style.display = ''
    toggleBar.style.display = ''
    codeView.style.display = 'none'
  })

  // 复制按钮
  copyBtn.addEventListener('click', async (e) => {
    e.stopPropagation()
    try {
      await copyToClipboard(code)
      const orig = copyBtn.textContent
      copyBtn.textContent = '✓ 已复制'
      setTimeout(() => { copyBtn.textContent = orig }, 1500)
    } catch (err) {
      console.error('[ChartWrapper] 复制失败:', err)
    }
  })

  return { wrapper, toggleBar, chartContainer, codeView }
}
