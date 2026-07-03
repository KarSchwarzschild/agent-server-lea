<script setup lang="ts">
/**
 * ScreenElement — PPTSlide 画布元素分发器
 *
 * 职责：根据 PPTElement.type 分发到对应 Base*Element 组件
 * 外层容器负责 absolute 定位 + 缩放，内部组件 100% 填满
 *
 * 参考：PPTist ScreenElement.vue (MIT License)
 */
import { computed } from 'vue'
import type {
  PPTElement, PPTTextElement, PPTShapeElement,
  PPTLineElement, PPTLatexElement, PPTImageElement,
  PPTTableElement, PPTChartElement,
} from './types'

import BaseTextElement from './BaseTextElement.vue'
import BaseImageElement from './BaseImageElement.vue'
import BaseShapeElement from './BaseShapeElement.vue'
import BaseLineElement from './BaseLineElement.vue'
import BaseLatexElement from './BaseLatexElement.vue'
import StaticTable from './StaticTable.vue'
import Chart from './Chart.vue'

const props = withDefaults(defineProps<{
  elementInfo: PPTElement
  elementIndex: number
  canvasScale: number
  themeFontName?: string
}>(), {
  themeFontName: '',
})

const el = computed(() => props.elementInfo)
const scale = computed(() => props.canvasScale)

// ─── 容器定位样式 ───

const wrapperStyle = computed(() => {
  const e = el.value
  const s = scale.value

  if (e.type === 'line') {
    // LineElement 特殊：BaseLineElement 内部用原始坐标渲染 SVG
    // wrapper 只做 position + transform: scale 整体缩放
    const line = e as PPTLineElement
    const svgW = Math.max(Math.abs(line.end[0] - line.start[0]), 1)
    const svgH = Math.max(Math.abs(line.end[1] - line.start[1]), 1)
    const padding = (line.width || 3) * 3
    return {
      position: 'absolute' as const,
      left: `${e.left * s}px`,
      top: `${e.top * s}px`,
      width: `${svgW + padding * 2}px`,
      height: `${svgH + padding * 2}px`,
      transform: `scale(${s})`,
      transformOrigin: 'top left',
    }
  }

  // 文本元素使用 auto height + min-height（对齐 OpenMAIC ScreenElement.tsx:36-37）
  // absolute 定位不参与文档流，auto height 不影响其他元素
  const isTextType = e.type === 'text'
  const isVertical = (e as any).vertical

  const style: Record<string, string> = {
    position: 'absolute',
    left: `${e.left * s}px`,
    top: `${(e as any).top * s}px`,
  }

  if (isVertical) {
    style.width = 'auto'
    style.minWidth = `${e.width * s}px`
    style.height = `${(e as any).height * s}px`
    style.writingMode = 'vertical-rl'
  } else if (isTextType && (e as any).height) {
    style.width = `${e.width * s}px`
    style.height = 'auto'
    style.minHeight = `${(e as any).height * s}px`
  } else {
    style.width = `${e.width * s}px`
    if ((e as any).height) {
      style.height = `${(e as any).height * s}px`
    }
  }

  if ((e as any).rotate) {
    style.transform = `rotate(${(e as any).rotate}deg)`
  }

  if ((e as any).opacity !== undefined && (e as any).opacity !== 1) {
    style.opacity = String((e as any).opacity)
  }

  return style
})

// ─── Line 元素：直接传原始数据给 BaseLineElement ───

const lineInfo = computed(() => {
  if (el.value.type !== 'line') return null
  return el.value as PPTLineElement
})

// ─── Text → 需要把 PPTTextElement 映射到 BaseTextElement 的 props ───

const textInfo = computed(() => {
  if (el.value.type !== 'text') return null
  const t = el.value as PPTTextElement
  return {
    content: t.content,
    defaultColor: t.defaultColor,
    defaultFontName: t.defaultFontName || props.themeFontName,
    lineHeight: t.lineHeight,
    fill: t.fill,
    outline: t.outline,
    width: t.width,
    height: t.height,
  }
})

// ─── Shape → BaseShapeElement ───

const shapeInfo = computed(() => {
  if (el.value.type !== 'shape') return null
  const s = el.value as PPTShapeElement
  return {
    id: s.id,
    width: s.width,
    height: s.height,
    path: s.path,
    viewBox: s.viewBox,
    fill: s.fill,
    fixedRatio: s.fixedRatio,
    outline: s.outline,
    text: s.text,
    rotate: s.rotate,
    opacity: s.opacity,
  }
})

// ─── Image → BaseImageElement ───

const imageInfo = computed(() => {
  if (el.value.type !== 'image') return null
  const img = el.value as PPTImageElement
  return {
    src: img.src,
  }
})

// ─── LaTeX → BaseLatexElement ───

const latexInfo = computed(() => {
  if (el.value.type !== 'latex') return null
  const l = el.value as PPTLatexElement
  return {
    latex: l.latex,
    color: l.color,
    align: l.align || 'center',
    width: l.width,
    height: l.height,
    path: l.path,
    viewBox: l.viewBox,
  }
})

// ─── Table → StaticTable ───

const tableEl = computed(() => {
  if (el.value.type !== 'table') return null
  return el.value as PPTTableElement
})

// ─── Chart → Chart ───

const chartEl = computed(() => {
  if (el.value.type !== 'chart') return null
  return el.value as PPTChartElement
})
</script>

<template>
  <div class="screen-element" :style="wrapperStyle" :data-element-id="el.id" :data-element-type="el.type">
    <!-- Text -->
    <BaseTextElement
      v-if="el.type === 'text' && textInfo"
      :elementInfo="textInfo"
    />

    <!-- Shape -->
    <BaseShapeElement
      v-else-if="el.type === 'shape' && shapeInfo"
      :elementInfo="shapeInfo"
      :themeFontName="themeFontName"
    />

    <!-- Image -->
    <BaseImageElement
      v-else-if="el.type === 'image' && imageInfo"
      :elementInfo="imageInfo"
    />

    <!-- Line -->
    <BaseLineElement
      v-else-if="el.type === 'line' && lineInfo"
      :elementInfo="lineInfo"
    />

    <!-- LaTeX -->
    <BaseLatexElement
      v-else-if="el.type === 'latex' && latexInfo"
      :elementInfo="latexInfo"
    />

    <!-- Table -->
    <StaticTable
      v-else-if="el.type === 'table' && tableEl"
      :data="tableEl.data"
      :width="el.width * scale"
      :cellMinHeight="(tableEl.cellMinHeight || 32) * scale"
      :colWidths="tableEl.colWidths"
      :outline="tableEl.outline"
      :theme="tableEl.theme"
    />

    <!-- Chart -->
    <Chart
      v-else-if="el.type === 'chart' && chartEl"
      :width="el.width * scale"
      :height="(el as any).height * scale"
      :type="chartEl.chartType"
      :data="chartEl.data"
      :themeColors="chartEl.themeColors"
    />
  </div>
</template>

<style scoped>
.screen-element {
  /* 外层定位容器，内部组件 100% 填满 */
  overflow: hidden;
}
</style>
