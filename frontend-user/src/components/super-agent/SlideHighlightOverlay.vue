<script setup lang="ts">
/**
 * SlideHighlightOverlay — Spotlight 高亮覆盖层
 * 对齐 OpenMAIC SpotlightOverlay.tsx：
 *   - dimness=0.7 明显暗化
 *   - 白色边框 1.2px + non-scaling-stroke
 *   - 收缩动画 0.6s (Web Animations API)
 *   - viewBox 百分比坐标系
 */
import { ref, watch, nextTick, inject, onMounted, onUnmounted, computed } from 'vue'
import type { UsePlaybackEngineReturn } from '../../composables/usePlaybackEngine'

const props = defineProps<{
  containerRef: HTMLElement | null
}>()

const playback = inject<UsePlaybackEngineReturn>('playback')!

// 百分比坐标（viewBox 0-100）
const rect = ref<{ x: number; y: number; w: number; h: number } | null>(null)
const cutoutRef = ref<SVGRectElement | null>(null)
const borderRef = ref<SVGRectElement | null>(null)

const dimness = computed(() => playback.spotlightOptions?.value?.dimness ?? 0.7)

// ─── Highlight 模式支持（P8）───
interface HighlightRect { x: number; y: number; w: number; h: number; color?: string }
const highlightRects = ref<HighlightRect[]>([])

function measureHighlights(elementIds: string[], color?: string) {
  const container = props.containerRef
  if (!container || !elementIds.length) {
    highlightRects.value = []
    return
  }

  const cRect = container.getBoundingClientRect()
  if (cRect.width === 0 || cRect.height === 0) { highlightRects.value = []; return }

  highlightRects.value = elementIds.map(id => {
    const el = container.querySelector(`[data-element-id="${id}"]`)
    if (!el) return null
    const eRect = el.getBoundingClientRect()
    return {
      x: ((eRect.left - cRect.left) / cRect.width) * 100,
      y: ((eRect.top - cRect.top) / cRect.height) * 100,
      w: (eRect.width / cRect.width) * 100,
      h: (eRect.height / cRect.height) * 100,
      color,
    }
  }).filter(Boolean) as HighlightRect[]
}

// 暴露给父组件
defineExpose({ measureHighlights })
const maskId = computed(() => `spotlight-mask-${playback.spotlightElementId.value || 'none'}`)

function measure() {
  const id = playback.spotlightElementId.value
  const container = props.containerRef
  if (!id || !container) {
    rect.value = null
    return
  }

  const el = container.querySelector(`[data-element-id="${id}"]`)
  if (!el) {
    rect.value = null
    return
  }

  const cRect = container.getBoundingClientRect()
  const eRect = el.getBoundingClientRect()

  if (cRect.width === 0 || cRect.height === 0) {
    rect.value = null
    return
  }

  // 转换为百分比坐标 (0-100)
  rect.value = {
    x: ((eRect.left - cRect.left) / cRect.width) * 100,
    y: ((eRect.top - cRect.top) / cRect.height) * 100,
    w: (eRect.width / cRect.width) * 100,
    h: (eRect.height / cRect.height) * 100,
  }
}

// 收缩动画（对齐 OpenMAIC: padding 8→0.4, rx 4→1, 0.6s）
function animateCutout() {
  if (!rect.value) return
  const r = rect.value

  // 挖孔收缩动画
  if (cutoutRef.value) {
    cutoutRef.value.animate([
      { x: r.x - 8, y: r.y - 8, width: r.w + 16, height: r.h + 16, rx: 4 },
      { x: r.x - 0.4, y: r.y - 0.6, width: r.w + 0.8, height: r.h + 1.2, rx: 1 },
    ], { duration: 600, easing: 'cubic-bezier(0.16, 1, 0.3, 1)', fill: 'forwards' })
  }

  // 边框延迟 50ms
  if (borderRef.value) {
    borderRef.value.animate([
      { x: r.x - 4, y: r.y - 4, width: r.w + 8, height: r.h + 8, opacity: 0, rx: 2 },
      { x: r.x - 0.4, y: r.y - 0.6, width: r.w + 0.8, height: r.h + 1.2, opacity: 1, rx: 1 },
    ], { duration: 500, delay: 50, easing: 'cubic-bezier(0.16, 1, 0.3, 1)', fill: 'forwards' })
  }
}

watch(
  () => playback.spotlightElementId.value,
  () => {
    nextTick(() => {
      measure()
      nextTick(animateCutout)
    })
  },
)

let resizeObserver: ResizeObserver | null = null

onMounted(() => {
  if (props.containerRef) {
    resizeObserver = new ResizeObserver(measure)
    resizeObserver.observe(props.containerRef)
  }
})

onUnmounted(() => {
  resizeObserver?.disconnect()
})

watch(
  () => props.containerRef,
  (el) => {
    resizeObserver?.disconnect()
    if (el) {
      resizeObserver = new ResizeObserver(measure)
      resizeObserver.observe(el)
    }
  },
)
</script>

<template>
  <Transition name="spotlight-fade">
    <div
      v-if="rect && playback.spotlightElementId.value"
      class="spotlight-container"
    >
      <svg
        width="100%"
        height="100%"
        viewBox="0 0 100 100"
        preserveAspectRatio="none"
        class="spotlight-svg"
      >
        <defs>
          <mask :id="maskId">
            <rect x="0" y="0" width="100" height="100" fill="white" />
            <rect
              ref="cutoutRef"
              fill="black"
              :x="rect.x - 0.4"
              :y="rect.y - 0.6"
              :width="rect.w + 0.8"
              :height="rect.h + 1.2"
              rx="1"
            />
          </mask>
        </defs>

        <!-- 暗化遮罩 -->
        <rect
          width="100"
          height="100"
          :fill="`rgba(0,0,0,${dimness})`"
          :mask="`url(#${maskId})`"
        />

        <!-- 白色边框 -->
        <rect
          ref="borderRef"
          fill="none"
          stroke="rgba(255,255,255,0.7)"
          stroke-width="1.2"
          style="vector-effect: non-scaling-stroke"
          :x="rect.x - 0.4"
          :y="rect.y - 0.6"
          :width="rect.w + 0.8"
          :height="rect.h + 1.2"
          rx="1"
          opacity="0"
        />
      </svg>
    </div>
  </Transition>

  <!-- ─── Highlight 模式：多元素彩色边框（无暗化）─── -->
  <div
    v-if="highlightRects.length > 0"
    class="spotlight-container"
  >
    <svg
      width="100%"
      height="100%"
      viewBox="0 0 100 100"
      preserveAspectRatio="none"
      class="spotlight-svg"
    >
      <rect
        v-for="(hr, i) in highlightRects"
        :key="'hl-' + i"
        fill="none"
        :stroke="hr.color || '#ff6b6b'"
        stroke-width="2"
        style="vector-effect: non-scaling-stroke"
        :x="hr.x - 0.5"
        :y="hr.y - 0.5"
        :width="hr.w + 1"
        :height="hr.h + 1"
        rx="1"
        class="highlight-rect"
      />
    </svg>
  </div>
</template>

<style scoped>
.spotlight-container {
  position: absolute;
  inset: 0;
  z-index: 100;
  pointer-events: none;
  overflow: hidden;
}

.spotlight-svg {
  position: absolute;
  inset: 0;
}

.spotlight-fade-enter-active,
.spotlight-fade-leave-active {
  transition: opacity 0.3s ease;
}
.spotlight-fade-enter-from,
.spotlight-fade-leave-to {
  opacity: 0;
}

/* Highlight pulse */
.highlight-rect {
  animation: highlight-pulse 1.5s ease-in-out infinite;
}

@keyframes highlight-pulse {
  0%, 100% { opacity: 0.7; }
  50% { opacity: 1; }
}
</style>
