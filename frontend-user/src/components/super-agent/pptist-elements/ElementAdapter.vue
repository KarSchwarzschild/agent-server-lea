<template>
  <!-- heading → BaseTextElement -->
  <BaseTextElement
    v-if="el.type === 'heading'"
    :elementInfo="headingInfo"
    :target="target"
  />

  <!-- text → BaseTextElement -->
  <BaseTextElement
    v-else-if="el.type === 'text'"
    :elementInfo="textInfo"
    :target="target"
  />

  <!-- list → BaseTextElement -->
  <BaseTextElement
    v-else-if="el.type === 'list'"
    :elementInfo="listInfo"
    :target="target"
  />

  <!-- latex → BaseLatexElement (thumbnail: 降级为色块占位) -->
  <div v-else-if="el.type === 'latex' && target === 'thumbnail'" class="el-placeholder" style="background: #f3e8ff; min-height: 16px; border-radius: 3px;" />
  <div v-else-if="el.type === 'latex'" class="el-latex-wrapper" :class="el.display === 'block' ? 'el-latex--block' : 'el-latex--inline'">
    <BaseLatexElement :elementInfo="latexInfo" />
  </div>

  <!-- callout → BaseShapeElement + 内嵌文本 -->
  <BaseShapeElement
    v-else-if="el.type === 'callout'"
    :elementInfo="calloutShapeInfo"
  />

  <!-- image → BaseImageElement -->
  <BaseImageElement
    v-else-if="el.type === 'image'"
    :elementInfo="imageInfo"
  />

  <!-- table → StaticTable (PPTist) -->
  <div v-else-if="el.type === 'table'" class="el-table-wrapper">
    <StaticTable
      v-if="tableData"
      :data="tableData"
      :width="700"
      :cellMinHeight="32"
      :colWidths="tableColWidths"
      :outline="{ style: 'solid', width: 1, color: '#e0e0e0' }"
      :theme="{ color: primaryColor, rowHeader: true, rowFooter: false, colHeader: false, colFooter: false }"
    />
  </div>

  <!-- code (thumbnail: 降级为色块占位) -->
  <div v-else-if="el.type === 'code' && target === 'thumbnail'" class="el-placeholder" style="background: #263238; min-height: 20px; border-radius: 3px;" />
  <!-- code → highlight.js -->
  <div v-else-if="el.type === 'code'" class="el-code-wrapper">
    <div class="el-code-header" v-if="el.language">
      <span class="el-code-lang">{{ el.language }}</span>
    </div>
    <pre><code v-html="highlightedCode" /></pre>
  </div>

  <!-- chart → Chart (thumbnail: 降级为色块占位) -->
  <div v-else-if="el.type === 'chart' && target === 'thumbnail'" class="el-placeholder" style="background: #e3f2fd; min-height: 40px; border-radius: 3px;" />
  <!-- chart → Chart (PPTist ECharts) -->
  <div v-else-if="el.type === 'chart'" class="el-chart-wrapper">
    <Chart
      :width="600"
      :height="300"
      :type="(el.chartType as any) || 'bar'"
      :data="el.data || { labels: [], legends: [], series: [] }"
      :themeColors="chartColors"
    />
  </div>

  <!-- mermaid_svg -->
  <div v-else-if="el.type === 'mermaid_svg'" class="el-mermaid-wrapper" v-html="el.svg" />
</template>

<script lang="ts" setup>
/**
 * ElementAdapter — SlideJSON 元素 → PPTist Base*Element 薄映射层
 *
 * 架构：规划方案 Phase 2b
 * 职责：将 SlideJSON 语义元素转换为 PPTist Base*Element 的 props，委托渲染
 * 来源：自研适配层 + PPTist (MIT License) 元素渲染器
 */
import { computed } from 'vue'
import hljs from 'highlight.js'
import 'highlight.js/styles/vs2015.css'
import './prosemirror.css'

import type { SlideElement } from '../SlideRenderer.vue'
import type { TableCell } from './types'

import BaseTextElement from './BaseTextElement.vue'
import type { TextElementInfo } from './BaseTextElement.vue'
import BaseImageElement from './BaseImageElement.vue'
import type { ImageElementInfo } from './BaseImageElement.vue'
import BaseShapeElement from './BaseShapeElement.vue'
import type { ShapeElementInfo } from './BaseShapeElement.vue'
import BaseLatexElement from './BaseLatexElement.vue'
import type { LatexElementInfo } from './BaseLatexElement.vue'
import StaticTable from './StaticTable.vue'
import Chart from './Chart.vue'

const props = defineProps<{
  el: SlideElement
  primaryColor?: string
  target?: 'full' | 'thumbnail'
}>()

const primary = computed(() => props.primaryColor || '#1a73e8')

// ─── heading → BaseTextElement ───

const headingInfo = computed<TextElementInfo>(() => {
  const level = props.el.level || 2
  const fontSize = level === 1 ? '28px' : level === 2 ? '22px' : '18px'
  return {
    content: `<p style="font-size: ${fontSize}; font-weight: 600;">${props.el.content || ''}</p>`,
    defaultColor: primary.value,
    defaultFontName: '',
  }
})

// ─── text → BaseTextElement ───

const textStyleMap: Record<string, string> = {
  emphasis: 'font-weight: 600;',
  caption: 'font-size: 14px; color: #666;',
  quote: 'font-style: italic; color: #555;',
}

const textInfo = computed<TextElementInfo>(() => {
  const inlineStyle = textStyleMap[props.el.style || ''] || ''
  return {
    content: `<p style="${inlineStyle}">${props.el.content || ''}</p>`,
    defaultColor: '',
    defaultFontName: '',
  }
})

// ─── list → BaseTextElement ───

const listInfo = computed<TextElementInfo>(() => {
  const tag = props.el.ordered ? 'ol' : 'ul'
  const items = (props.el.items || []).map(item => `<li>${item}</li>`).join('')
  return {
    content: `<${tag}>${items}</${tag}>`,
    defaultColor: '',
    defaultFontName: '',
  }
})

// ─── latex → BaseLatexElement ───

const latexInfo = computed<LatexElementInfo>(() => ({
  latex: props.el.content || '',
  align: props.el.display === 'block' ? 'center' : 'left',
}))

// ─── callout → BaseShapeElement ───

const calloutColorMap: Record<string, { fill: string; border: string }> = {
  info:    { fill: '#e8f0fe', border: '#1a73e8' },
  warning: { fill: '#fef7e0', border: '#f9ab00' },
  success: { fill: '#e6f4ea', border: '#34a853' },
  tip:     { fill: '#e6f4ea', border: '#34a853' },
  example: { fill: '#f3e5f5', border: '#9c27b0' },
}
const calloutIconMap: Record<string, string> = {
  info: 'ℹ️', warning: '⚠️', success: '✅', tip: '💡', example: '📝',
}

const calloutShapeInfo = computed<ShapeElementInfo>(() => {
  const style = props.el.style || 'info'
  const colors = calloutColorMap[style] || calloutColorMap.info
  const icon = calloutIconMap[style] || 'ℹ️'
  return {
    id: `callout-${style}`,
    width: 700,
    height: 60,
    viewBox: [700, 60],
    path: 'M 0 0 L 700 0 L 700 60 L 0 60 Z',
    fill: colors.fill,
    outline: { style: 'solid', width: 0, color: 'transparent' },
    text: {
      content: `<p>${icon} ${props.el.content || ''}</p>`,
      defaultFontName: '',
      defaultColor: '#333',
      align: 'middle',
    },
  }
})

// ─── image → BaseImageElement ───

const imageInfo = computed<ImageElementInfo>(() => ({
  src: props.el.url || '',
  radius: 8,
}))

// ─── table → StaticTable ───

const tableData = computed<TableCell[][] | null>(() => {
  if (props.el.type !== 'table') return null
  const headers = props.el.headers || []
  const rows = props.el.rows || []
  const result: TableCell[][] = []
  if (headers.length) {
    result.push(headers.map((h, i) => ({
      id: `h_${i}`, colspan: 1, rowspan: 1, text: h,
      style: { bold: true, color: '#fff', backcolor: primary.value, align: 'center' as const },
    })))
  }
  rows.forEach((row, ri) => {
    result.push(row.map((cell, ci) => ({ id: `r${ri}_c${ci}`, colspan: 1, rowspan: 1, text: cell })))
  })
  return result.length ? result : null
})

const tableColWidths = computed(() => {
  const cols = (props.el.headers || props.el.rows?.[0] || []).length
  return cols > 0 ? Array(cols).fill(1 / cols) : []
})

// ─── code → highlight.js ───

const highlightedCode = computed(() => {
  if (props.el.type !== 'code' || !props.el.content) return ''
  if (props.el.language && hljs.getLanguage(props.el.language)) {
    return hljs.highlight(props.el.content, { language: props.el.language }).value
  }
  return hljs.highlightAuto(props.el.content).value
})

// ─── chart colors ───

const chartColors = computed(() => [primary.value, '#ed7d31', '#a5a5a5', '#ffc000', '#4472c4', '#70ad47', '#9c27b0', '#e91e63'])
</script>

<style scoped>
/* LaTeX wrapper */
.el-latex--block { padding: 12px 8px; background: rgba(0,0,0,0.02); border-radius: 6px; overflow-x: auto; }
.el-latex--inline { display: inline; }

/* Table wrapper */
.el-table-wrapper { width: 100%; overflow-x: auto; border-radius: 6px; }
.el-table-wrapper :deep(table) { font-size: 0.9em; }

/* Code wrapper */
.el-code-wrapper { border-radius: 8px; overflow: hidden; background: #1e1e1e; font-size: 0.85em; }
.el-code-header { padding: 6px 14px; background: #2d2d2d; border-bottom: 1px solid #3d3d3d; }
.el-code-lang { font-size: 0.75em; color: #999; text-transform: uppercase; letter-spacing: 0.5px; }
.el-code-wrapper pre { margin: 0; padding: 14px 18px; overflow-x: auto; line-height: 1.5; }
.el-code-wrapper code { font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', Consolas, monospace; color: #d4d4d4; background: none; padding: 0; }

/* Chart wrapper */
.el-chart-wrapper { width: 100%; height: 300px; border-radius: 6px; overflow: hidden; }

/* Mermaid wrapper */
.el-mermaid-wrapper { text-align: center; }
.el-mermaid-wrapper :deep(svg) { max-width: 100%; }
</style>
