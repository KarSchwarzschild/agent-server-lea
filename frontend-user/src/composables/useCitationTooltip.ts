/**
 * 引用 Tooltip composable
 *
 * 提取自 MessageItem.vue，供 ArtifactPanel 和 MessageItem 复用。
 * 处理 .citation-mark 元素的 mousemove/leave 事件，
 * 读取 data-ref-* 属性显示悬浮 tooltip。
 */

import { ref, onUnmounted } from 'vue'

export interface TooltipContent {
  title: string
  authors: string
  year: string
  source: string
  url: string
  abstract: string
}

export function useCitationTooltip() {
  const tooltipVisible = ref(false)
  const tooltipContent = ref<TooltipContent>({
    title: '', authors: '', year: '', source: '', url: '', abstract: '',
  })
  const tooltipPosition = ref({ x: 0, y: 0 })
  let activeRefId = ''
  let hideTimer: ReturnType<typeof setTimeout> | null = null

  function clearHideTimer() {
    if (hideTimer) { clearTimeout(hideTimer); hideTimer = null }
  }

  function scheduleHide() {
    clearHideTimer()
    hideTimer = setTimeout(() => {
      tooltipVisible.value = false
      activeRefId = ''
      hideTimer = null
    }, 200)
  }

  /** 在包含 .citation-mark 的容器上监听 mousemove */
  function handleCitationMousemove(e: MouseEvent) {
    const target = (e.target as HTMLElement)?.closest?.('.citation-mark') as HTMLElement | null

    if (target) {
      clearHideTimer()
      const refId = target.dataset.refId || ''
      if (refId !== activeRefId) {
        activeRefId = refId
        tooltipContent.value = {
          title: target.dataset.refTitle || '',
          authors: target.dataset.refAuthors || '',
          year: target.dataset.refYear || '',
          source: target.dataset.refSource || '',
          url: target.dataset.refUrl || '',
          abstract: target.dataset.refAbstract || '',
        }
      }
      const rect = target.getBoundingClientRect()
      tooltipPosition.value = { x: rect.left + rect.width / 2, y: rect.top }
      tooltipVisible.value = true
    } else if (tooltipVisible.value) {
      scheduleHide()
    }
  }

  /** tooltip 本体 mouseenter — 取消隐藏 */
  function handleTooltipEnter() {
    clearHideTimer()
  }

  /** tooltip 本体 mouseleave — 延迟隐藏 */
  function handleTooltipLeave() {
    scheduleHide()
  }

  onUnmounted(() => {
    clearHideTimer()
  })

  return {
    tooltipVisible,
    tooltipContent,
    tooltipPosition,
    handleCitationMousemove,
    handleTooltipEnter,
    handleTooltipLeave,
  }
}
