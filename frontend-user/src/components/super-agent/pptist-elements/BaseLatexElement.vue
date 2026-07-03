<template>
  <div class="base-element-latex">
    <div class="element-content" :style="contentStyle">
      <!-- 优先使用 KaTeX HTML 渲染（OpenMAIC 方案）+ 自动缩放 -->
      <div
        v-if="renderedHtml"
        ref="katexRef"
        class="katex-content"
        :class="elementInfo.align || 'center'"
        :style="katexScaleStyle"
        v-html="renderedHtml"
      />
      <!-- 后备：SVG path 渲染（PPTist 原版方案） -->
      <svg
        v-else-if="elementInfo.path && elementInfo.viewBox"
        overflow="visible"
        :width="elementInfo.width"
        :height="elementInfo.height"
        :stroke="elementInfo.color"
        :stroke-width="elementInfo.strokeWidth"
        fill="none"
        stroke-linecap="round"
        stroke-linejoin="round"
      >
        <g :transform="`scale(${elementInfo.width! / elementInfo.viewBox[0]}, ${elementInfo.height! / elementInfo.viewBox[1]})`">
          <path :d="elementInfo.path" />
        </g>
      </svg>
      <!-- 最终后备：原始文本 -->
      <span v-else class="latex-fallback">{{ elementInfo.latex }}</span>
    </div>
  </div>
</template>

<script lang="ts" setup>
/**
 * BaseLatexElement — LaTeX 公式渲染 + 自动缩放
 * 混合方案：优先 KaTeX HTML（OpenMAIC），后备 SVG path（PPTist 原版）
 * 自动缩放：参考 OpenMAIC BaseLatexElement.tsx KatexContent 逻辑
 *   → 测量自然宽高 → Math.min(w/natW, h/natH) → transform: scale
 * 来源：PPTist (MIT License) + OpenMAIC KaTeX 增强
 */
import { computed, ref, watch, nextTick, onMounted } from 'vue'
import katex from 'katex'
import 'katex/dist/katex.min.css'

export interface LatexElementInfo {
  latex: string
  path?: string
  color?: string
  strokeWidth?: number
  viewBox?: [number, number]
  width?: number
  height?: number
  align?: 'left' | 'center' | 'right'
}

const props = defineProps<{
  elementInfo: LatexElementInfo
}>()

const katexRef = ref<HTMLElement | null>(null)
const scale = ref(1)

const renderedHtml = computed(() => {
  if (!props.elementInfo.latex) return ''
  try {
    return katex.renderToString(props.elementInfo.latex, {
      throwOnError: false,
      displayMode: true,
      output: 'html',
    })
  }
  catch {
    return ''
  }
})

// 自动缩放：测量 KaTeX 渲染的自然宽高，缩放到容器内
function measureAndScale() {
  const el = katexRef.value
  if (!el || !props.elementInfo.width || !props.elementInfo.height) {
    scale.value = 1
    return
  }
  // 先重置 scale 以获取自然尺寸
  el.style.transform = 'none'
  nextTick(() => {
    const naturalW = el.scrollWidth
    const naturalH = el.scrollHeight
    if (naturalW > 0 && naturalH > 0) {
      const w = props.elementInfo.width!
      const h = props.elementInfo.height!
      scale.value = Math.min(w / naturalW, h / naturalH, 1) // 不放大，只缩小
    } else {
      scale.value = 1
    }
  })
}

onMounted(measureAndScale)
watch(() => renderedHtml.value, measureAndScale)
watch(() => [props.elementInfo.width, props.elementInfo.height], measureAndScale)

const contentStyle = computed(() => {
  const color = props.elementInfo.color
  return color ? { color } : {}
})

// transform-origin 根据 align 设置
const katexScaleStyle = computed(() => {
  if (scale.value >= 1) return {}
  const align = props.elementInfo.align || 'center'
  const originX = align === 'left' ? '0' : align === 'right' ? '100%' : '50%'
  return {
    transform: `scale(${scale.value})`,
    transformOrigin: `${originX} top`,
  }
})
</script>

<style scoped>
.base-element-latex { width: 100%; height: 100%; }
.element-content { width: 100%; height: 100%; position: relative; display: flex; align-items: center; }
.element-content svg { transform-origin: 0 0; overflow: visible; }
.katex-content { width: 100%; }
.katex-content.center { text-align: center; }
.katex-content.left { text-align: left; }
.katex-content.right { text-align: right; }
.katex-content :deep(.katex-display) { margin: 0; }
.katex-content :deep(.katex) { font-size: 1.15em; }
.latex-fallback { font-family: 'KaTeX_Math', serif; color: #c62828; font-style: italic; }
</style>
