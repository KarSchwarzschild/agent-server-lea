/**
 * ECharts 图表渲染 Vue Composable
 *
 * 功能：
 * - MutationObserver 监听 DOM 变化
 * - 500ms throttle 防止高频触发
 * - 流式期间不渲染新图表，流式结束后统一渲染
 * - data-echarts-rendered 标记防重复
 * - resize 监听 + onUnmounted dispose 防内存泄漏
 * - MAX_CHARTS_PER_PAGE = 20 限制
 */

import { ref, onMounted, onUnmounted, watch, type Ref } from 'vue'
import { renderEChartsFromCode, disposeECharts, resizeECharts } from '../utils/echartsRenderer'
import { quickDetectECharts } from '../utils/echartsDetector'
import { createChartWrapper } from '../utils/chartUtils'
import type { ECharts } from 'echarts'

// ==================== 常量 ====================

const MAX_CHARTS_PER_PAGE = 20

// ==================== 类型定义 ====================

export interface UseEChartsChartOptions {
  /** 容器元素引用 */
  containerRef: Ref<HTMLElement | null>
  /** 是否正在流式输出 */
  isStreaming?: Ref<boolean>
  /** debounce 延迟（毫秒） */
  debounceMs?: number
}

export interface UseEChartsChartReturn {
  /** 是否正在渲染 */
  isRendering: Ref<boolean>
  /** 渲染错误列表 */
  errors: Ref<string[]>
  /** 当前图表数量 */
  chartCount: Ref<number>
  /** 手动触发渲染 */
  triggerRender: () => void
}

// ==================== Composable ====================

export function useEChartsChart(options: UseEChartsChartOptions): UseEChartsChartReturn {
  const {
    containerRef,
    isStreaming = ref(false),
    debounceMs = 500,
  } = options

  const isRendering = ref(false)
  const errors = ref<string[]>([])
  const chartCount = ref(0)

  // 存储所有 ECharts 实例，用于清理
  const instances: Map<HTMLElement, ECharts> = new Map()

  let observer: MutationObserver | null = null
  let debounceTimer: ReturnType<typeof setTimeout> | null = null
  let resizeObserver: ResizeObserver | null = null
  let lastProcessTime = 0

  /**
   * HTML 转义（用于错误回退显示）
   */
  function escapeHtml(text: string): string {
    const div = document.createElement('div')
    div.textContent = text
    return div.innerHTML
  }

  /**
   * 处理单个 ECharts 代码块
   */
  async function processBlock(block: HTMLElement): Promise<void> {
    // 已渲染过，跳过
    if (block.dataset.echartsRendered === 'true' || block.dataset.echartsRendered === 'pending') return

    // 检查图表数量限制
    if (chartCount.value >= MAX_CHARTS_PER_PAGE) {
      console.warn('[useEChartsChart] 达到最大图表数量限制:', MAX_CHARTS_PER_PAGE)
      return
    }

    const codeEl = block.querySelector('code')
    const code = (codeEl || block).textContent || ''
    if (!code.trim()) return

    // 流式输出中：不渲染新图表，等流式结束后统一处理
    if (isStreaming.value) return

    // 标记为正在渲染
    block.dataset.echartsRendered = 'pending'

    try {
      // 使用 createChartWrapper 创建统一 DOM 结构
      const { wrapper, chartContainer } = createChartWrapper({
        chartType: 'echarts',
        code,
      })

      // ECharts 需要容器有固定高度
      chartContainer.style.width = '100%'
      chartContainer.style.height = '400px'
      chartContainer.style.minHeight = '300px'

      // 先插入 DOM，再渲染（ECharts 需要容器有尺寸）
      block.replaceWith(wrapper)

      const result = await renderEChartsFromCode(chartContainer, code)

      if (result.success && result.instance) {
        instances.set(chartContainer, result.instance)
        chartCount.value++

        // 设置 resize 监听
        if (resizeObserver) {
          resizeObserver.observe(chartContainer)
        }
      } else {
        // 渲染失败，显示错误回退
        wrapper.className = 'echarts-error'
        wrapper.innerHTML = `<pre><code>${escapeHtml(code)}</code></pre>
          <div class="echarts-error-msg">ECharts 渲染失败: ${result.error || '未知错误'}</div>`
        if (result.error) {
          errors.value.push(result.error)
        }
      }
    } catch (err) {
      block.dataset.echartsRendered = 'error'
      const errorMsg = err instanceof Error ? err.message : String(err)
      errors.value.push(errorMsg)
      console.error('[useEChartsChart] 渲染失败:', errorMsg)
    }
  }

  /**
   * 扫描并处理所有 ECharts 代码块
   */
  async function processBlocks(): Promise<void> {
    const container = containerRef.value
    if (!container) return

    // 查找所有 echarts 代码块
    const echartsBlocks = container.querySelectorAll<HTMLElement>(
      'pre.language-echarts:not([data-echarts-rendered]), ' +
      'pre code.language-echarts:not([data-echarts-rendered]), ' +
      '.echarts-placeholder:not([data-echarts-rendered])'
    )

    // 查找所有 json / javascript 代码块，检测是否为 ECharts 配置
    const detectBlocks = container.querySelectorAll<HTMLElement>(
      'pre.language-json:not([data-echarts-rendered]), ' +
      'pre code.language-json:not([data-echarts-rendered]), ' +
      'pre.language-javascript:not([data-echarts-rendered]), ' +
      'pre code.language-javascript:not([data-echarts-rendered]), ' +
      'pre.language-js:not([data-echarts-rendered]), ' +
      'pre code.language-js:not([data-echarts-rendered])'
    )

    const blocksToProcess: HTMLElement[] = [...echartsBlocks]

    // 智能检测 JSON / JavaScript 代码块
    for (const block of detectBlocks) {
      const codeEl = block.querySelector('code')
      const code = (codeEl || block).textContent || ''
      if (quickDetectECharts(code)) {
        blocksToProcess.push(block)
      }
    }

    if (blocksToProcess.length === 0) return

    isRendering.value = true

    // 串行处理
    for (const block of blocksToProcess) {
      await processBlock(block)
    }

    isRendering.value = false
  }

  /**
   * throttle 包装的处理函数
   */
  function throttledProcess(): void {
    const now = Date.now()
    const elapsed = now - lastProcessTime

    if (debounceTimer) {
      clearTimeout(debounceTimer)
    }

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

  /**
   * 手动触发渲染
   */
  function triggerRender(): void {
    processBlocks()
  }

  /**
   * 设置 MutationObserver
   */
  function setupObserver(): void {
    const container = containerRef.value
    if (!container || observer) return

    observer = new MutationObserver((mutations) => {
      const hasRelevantChange = mutations.some((mutation) => {
        if (mutation.type === 'childList' && mutation.addedNodes.length > 0) {
          return true
        }
        if (mutation.type === 'characterData') {
          return true
        }
        return false
      })

      if (hasRelevantChange) {
        throttledProcess()
      }
    })

    observer.observe(container, {
      childList: true,
      subtree: true,
      characterData: true,
    })

    // 设置 ResizeObserver
    resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const instance = instances.get(entry.target as HTMLElement)
        if (instance) {
          resizeECharts(instance)
        }
      }
    })

    // 初始处理
    processBlocks()
  }

  /**
   * 清理
   */
  function cleanup(): void {
    // 清理 MutationObserver
    if (observer) {
      observer.disconnect()
      observer = null
    }

    // 清理 debounce timer
    if (debounceTimer) {
      clearTimeout(debounceTimer)
      debounceTimer = null
    }

    // 清理 ResizeObserver
    if (resizeObserver) {
      resizeObserver.disconnect()
      resizeObserver = null
    }

    // 销毁所有 ECharts 实例
    for (const instance of instances.values()) {
      disposeECharts(instance)
    }
    instances.clear()
    chartCount.value = 0
  }

  // 生命周期
  onMounted(() => {
    setupObserver()
  })

  onUnmounted(() => {
    cleanup()
  })

  // 监听容器变化
  watch(containerRef, (newVal, oldVal) => {
    if (oldVal) {
      cleanup()
    }
    if (newVal) {
      setupObserver()
    }
  })

  // 流式输出结束时，强制处理一次
  watch(isStreaming, (val) => {
    if (!val) {
      setTimeout(() => processBlocks(), 100)
    }
  })

  return {
    isRendering,
    errors,
    chartCount,
    triggerRender,
  }
}
