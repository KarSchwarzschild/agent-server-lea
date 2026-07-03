/**
 * Mermaid 图表渲染 Vue Composable
 *
 * 功能：
 * - MutationObserver 监听 DOM 变化
 * - 500ms throttle 防止高频触发
 * - 流式期间只从缓存恢复，不调用 mermaid.render()（避免不完整代码产生错误 SVG）
 * - 流式结束后统一渲染新图表
 * - SVG 缓存：v-html 更新后从缓存瞬间恢复，消除闪烁
 * - data-mermaid-rendered 标记防重复
 * - 图表工具栏（查看代码/下载/全屏/复制）
 * - 自动清理
 */

import { ref, onMounted, onUnmounted, watch, type Ref } from 'vue'
import { renderMermaid, isMermaidCode } from '../utils/mermaidRenderer'
import { createChartWrapper } from '../utils/chartUtils'

// ==================== 类型定义 ====================

export interface UseMermaidChartOptions {
  containerRef: Ref<HTMLElement | null>
  isStreaming?: Ref<boolean>
  debounceMs?: number
  timeout?: number
}

export interface UseMermaidChartReturn {
  isRendering: Ref<boolean>
  errors: Ref<string[]>
  triggerRender: () => void
}

// ==================== Composable ====================

export function useMermaidChart(options: UseMermaidChartOptions): UseMermaidChartReturn {
  const {
    containerRef,
    isStreaming = ref(false),
    debounceMs = 500,
    timeout = 10000,
  } = options

  const isRendering = ref(false)
  const errors = ref<string[]>([])

  let observer: MutationObserver | null = null
  let debounceTimer: ReturnType<typeof setTimeout> | null = null
  let lastProcessTime = 0

  // SVG 缓存：code → wrapper outerHTML（防止 v-html 更新导致闪烁）
  const svgCache = new Map<string, string>()

  /**
   * 处理单个 Mermaid 代码块
   */
  async function processBlock(block: HTMLElement): Promise<void> {
    if (block.dataset.mermaidRendered === 'true' || block.dataset.mermaidRendered === 'pending') return

    const codeEl = block.querySelector('code')
    const code = (codeEl || block).textContent || ''

    if (!isMermaidCode(code)) return

    const codeKey = code.trim()

    // 缓存命中：瞬间恢复，不走 mermaid 渲染（消除闪烁）
    const cached = svgCache.get(codeKey)
    if (cached) {
      const temp = document.createElement('div')
      temp.innerHTML = cached
      const wrapper = temp.firstElementChild as HTMLElement
      if (wrapper) {
        block.replaceWith(wrapper)
        return
      }
    }

    // 流式输出中：缓存未命中则跳过，不调用 mermaid.render()
    // 原因：流式期间代码可能不完整，mermaid 11.x 不抛异常而是渲染错误 SVG（炸弹图标）
    // isCodeBlockComplete 启发式检查无法覆盖所有边界情况
    // 等流式结束后再统一渲染新图表
    if (isStreaming.value) return

    block.dataset.mermaidRendered = 'pending'

    try {
      const result = await renderMermaid(code, timeout)

      if (result.success && result.svg) {
        // 使用 createChartWrapper 创建统一 DOM 结构
        const { wrapper, chartContainer } = createChartWrapper({
          chartType: 'mermaid',
          code,
        })
        chartContainer.innerHTML = result.svg

        // 淡入动画
        wrapper.style.opacity = '0'
        wrapper.style.transition = 'opacity 0.3s ease'

        block.replaceWith(wrapper)

        requestAnimationFrame(() => {
          wrapper.style.opacity = '1'
        })

        // 缓存
        svgCache.set(codeKey, wrapper.outerHTML)
      } else {
        // 渲染失败（非流式，确认失败）
        block.dataset.mermaidRendered = 'error'
        if (result.error) errors.value.push(result.error)
        if (result.svg) block.outerHTML = result.svg
      }
    } catch (err) {
      block.dataset.mermaidRendered = 'error'
      const errorMsg = err instanceof Error ? err.message : String(err)
      errors.value.push(errorMsg)
      console.error('[useMermaidChart] 渲染失败:', errorMsg)
    }
  }

  /**
   * 扫描并处理所有 Mermaid 代码块
   */
  async function processBlocks(): Promise<void> {
    const container = containerRef.value
    if (!container) return

    const blocks = container.querySelectorAll<HTMLElement>(
      'pre.language-mermaid:not([data-mermaid-rendered]), ' +
      'pre code.language-mermaid:not([data-mermaid-rendered]), ' +
      'pre.lang-mermaid:not([data-mermaid-rendered]), ' +
      'pre code.lang-mermaid:not([data-mermaid-rendered]), ' +
      '.mermaid-placeholder:not([data-mermaid-rendered])'
    )

    if (blocks.length === 0) return

    isRendering.value = true
    for (const block of blocks) {
      await processBlock(block)
    }
    isRendering.value = false
  }

  function throttledProcess(): void {
    const now = Date.now()
    const elapsed = now - lastProcessTime

    if (debounceTimer) clearTimeout(debounceTimer)

    if (elapsed >= debounceMs) {
      lastProcessTime = now
      processBlocks()
    } else {
      debounceTimer = setTimeout(() => {
        lastProcessTime = Date.now()
        processBlocks()
        debounceTimer = null
      }, debounceMs - elapsed)
    }
  }

  function triggerRender(): void {
    processBlocks()
  }

  function setupObserver(): void {
    const container = containerRef.value
    if (!container || observer) return

    observer = new MutationObserver((mutations) => {
      const hasRelevantChange = mutations.some((m) =>
        (m.type === 'childList' && m.addedNodes.length > 0) || m.type === 'characterData'
      )
      if (hasRelevantChange) throttledProcess()
    })

    observer.observe(container, {
      childList: true,
      subtree: true,
      characterData: true,
    })

    processBlocks()
  }

  function cleanupObserver(): void {
    if (observer) { observer.disconnect(); observer = null }
    if (debounceTimer) { clearTimeout(debounceTimer); debounceTimer = null }
  }

  onMounted(() => setupObserver())
  onUnmounted(() => { cleanupObserver(); svgCache.clear() })

  watch(containerRef, (newVal, oldVal) => {
    if (oldVal) cleanupObserver()
    if (newVal) setupObserver()
  })

  watch(isStreaming, (val) => {
    if (!val) setTimeout(() => processBlocks(), 100)
  })

  return { isRendering, errors, triggerRender }
}
