<template>
  <div class="chart" ref="chartRef"></div>
</template>

<script lang="ts" setup>
/**
 * ECharts 图表渲染组件
 * 来源：PPTist (MIT License)
 */
import { onMounted, useTemplateRef, computed, watch, onUnmounted } from 'vue'
import type { ChartData, ChartOptions, ChartType } from './types'
import { getChartOption } from './chartOption'

import * as echarts from 'echarts/core'
import { BarChart, LineChart, PieChart, ScatterChart, RadarChart } from 'echarts/charts'
import { LegendComponent } from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'

echarts.use([BarChart, LineChart, PieChart, ScatterChart, RadarChart, LegendComponent, SVGRenderer])

const props = defineProps<{
  width: number
  height: number
  type: ChartType
  data: ChartData
  themeColors: string[]
  textColor?: string
  lineColor?: string
  options?: ChartOptions
}>()

let chart: echarts.ECharts | null = null
const chartRef = useTemplateRef<HTMLElement>('chartRef')

/**
 * 色系补全：当 themeColors 不足时，用 HSL 色相旋转生成类似色系
 * 参考 OpenMAIC Chart.tsx 的 tinycolor.analogous() 逻辑
 */
function hexToHsl(hex: string): [number, number, number] {
  const r = parseInt(hex.slice(1, 3), 16) / 255
  const g = parseInt(hex.slice(3, 5), 16) / 255
  const b = parseInt(hex.slice(5, 7), 16) / 255
  const max = Math.max(r, g, b), min = Math.min(r, g, b)
  const l = (max + min) / 2
  if (max === min) return [0, 0, l]
  const d = max - min
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min)
  let h = 0
  if (max === r) h = ((g - b) / d + (g < b ? 6 : 0)) / 6
  else if (max === g) h = ((b - r) / d + 2) / 6
  else h = ((r - g) / d + 4) / 6
  return [h * 360, s, l]
}

function hslToHex(h: number, s: number, l: number): string {
  h = ((h % 360) + 360) % 360
  const c = (1 - Math.abs(2 * l - 1)) * s
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1))
  const m = l - c / 2
  let r = 0, g = 0, b = 0
  if (h < 60) { r = c; g = x }
  else if (h < 120) { r = x; g = c }
  else if (h < 180) { g = c; b = x }
  else if (h < 240) { g = x; b = c }
  else if (h < 300) { r = x; b = c }
  else { r = c; b = x }
  const toHex = (v: number) => Math.round((v + m) * 255).toString(16).padStart(2, '0')
  return `#${toHex(r)}${toHex(g)}${toHex(b)}`
}

function generateAnalogousColors(baseHex: string, count: number): string[] {
  const [h, s, l] = hexToHsl(baseHex)
  const step = 30 // 30° 色相步进
  return Array.from({ length: count }, (_, i) => hslToHex(h + (i - Math.floor(count / 2)) * step, s, l))
}

const themeColors = computed(() => {
  const raw = props.themeColors
  if (!raw || raw.length === 0) return ['#5b9bd5', '#ed7d31', '#a5a5a5', '#ffc000', '#4472c4', '#70ad47']
  if (raw.length >= 6) return raw
  // 不足 6 色时，基于第一个颜色生成类似色系
  const base = raw[0]
  if (!/^#[0-9a-fA-F]{6}$/.test(base)) return raw
  const generated = generateAnalogousColors(base, 10)
  return [...new Set([...raw, ...generated])].slice(0, 10)
})

const updateOption = () => {
  const option = getChartOption({
    type: props.type, data: props.data, themeColors: themeColors.value,
    textColor: props.textColor, lineColor: props.lineColor,
    lineSmooth: props.options?.lineSmooth || false, stack: props.options?.stack || false,
  })
  if (option && chart) chart.setOption(option, true)
}

let resizeObserver: ResizeObserver | null = null

onMounted(() => {
  chart = echarts.init(chartRef.value!, null, { renderer: 'svg' })
  updateOption()
  resizeObserver = new ResizeObserver(() => chart?.resize())
  resizeObserver.observe(chartRef.value!)
})

onUnmounted(() => {
  resizeObserver?.disconnect()
  chart?.dispose()
})

watch(() => props.type, updateOption)
watch(() => props.data, updateOption)
watch(() => props.themeColors, updateOption)
watch(() => props.textColor, updateOption)
</script>

<style scoped>
.chart { width: 100%; height: 100%; }
</style>
