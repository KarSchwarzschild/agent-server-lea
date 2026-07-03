<script setup lang="ts">
/**
 * Tab 2: 时间趋势 — 堆叠面积图 + 调用次数折线图
 */
import { ref, computed, watch } from 'vue'
import { NSelect } from 'naive-ui'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import { TitleComponent, TooltipComponent, LegendComponent, GridComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import { featureLabel, featureColor, FEATURE_COLOR_MAP } from './constants'
import type { TrendRow } from '../../api/token-usage'

use([CanvasRenderer, LineChart, TitleComponent, TooltipComponent, LegendComponent, GridComponent])

const props = defineProps<{ data: TrendRow[] }>()
const emit = defineEmits<{ (e: 'granularity', v: string): void }>()

const granularity = ref('day')
watch(granularity, v => emit('granularity', v))

// 提取唯一日期和 feature
const dates = computed(() => [...new Set(props.data.map(r => r.date))].sort())
const features = computed(() => [...new Set(props.data.map(r => r.feature))])

// 按 feature 构建 series
function buildSeries(field: 'total_tokens' | 'call_count') {
  return features.value.map(f => {
    const map: Record<string, number> = {}
    for (const r of props.data) {
      if (r.feature === f) map[r.date] = (map[r.date] || 0) + r[field]
    }
    return {
      name: featureLabel(f),
      type: 'line',
      stack: field === 'total_tokens' ? 'tokens' : undefined,
      areaStyle: field === 'total_tokens' ? {} : undefined,
      data: dates.value.map(d => map[d] || 0),
      itemStyle: { color: featureColor(f) },
    }
  })
}

const tokenChartOption = computed(() => ({
  tooltip: { trigger: 'axis' },
  legend: { bottom: 0, type: 'scroll' },
  grid: { left: 60, right: 20, top: 20, bottom: 40 },
  xAxis: { type: 'category', data: dates.value },
  yAxis: { type: 'value', name: 'Token' },
  series: buildSeries('total_tokens'),
}))

const callChartOption = computed(() => ({
  tooltip: { trigger: 'axis' },
  legend: { bottom: 0, type: 'scroll' },
  grid: { left: 60, right: 20, top: 20, bottom: 40 },
  xAxis: { type: 'category', data: dates.value },
  yAxis: { type: 'value', name: '调用次数' },
  series: buildSeries('call_count'),
}))

const granularityOptions = [
  { label: '按天', value: 'day' },
  { label: '按小时', value: 'hour' },
]
</script>

<template>
  <div style="margin-bottom: 12px">
    <NSelect v-model:value="granularity" :options="granularityOptions" size="small" style="width: 100px" />
  </div>
  <VChart :option="tokenChartOption" style="height: 300px" autoresize />
  <VChart :option="callChartOption" style="height: 240px; margin-top: 16px" autoresize />
</template>
