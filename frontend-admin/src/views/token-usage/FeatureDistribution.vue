<script setup lang="ts">
/**
 * Tab 1: 功能分布 — 饼图 + 功能明细表 + 模型柱状图
 */
import { computed } from 'vue'
import { NTable, NTag } from 'naive-ui'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { PieChart, BarChart } from 'echarts/charts'
import { TitleComponent, TooltipComponent, LegendComponent, GridComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import { featureLabel, featureColor } from './constants'
import { formatTokens, type FeatureRow } from '../../api/token-usage'

use([CanvasRenderer, PieChart, BarChart, TitleComponent, TooltipComponent, LegendComponent, GridComponent])

const props = defineProps<{ data: FeatureRow[] }>()

// 按 feature 聚合（API 返回按 feature+model 分组）
const byFeature = computed(() => {
  const map: Record<string, { tokens: number; calls: number; estimated: number }> = {}
  for (const r of props.data) {
    if (!map[r.feature]) map[r.feature] = { tokens: 0, calls: 0, estimated: 0 }
    map[r.feature].tokens += r.total_tokens
    map[r.feature].calls += r.call_count
    map[r.feature].estimated += r.estimated_count
  }
  return Object.entries(map)
    .map(([f, v]) => ({ feature: f, ...v }))
    .sort((a, b) => b.tokens - a.tokens)
})

// 饼图: Top 5 + Others（过滤 token=0 的项如 rerank）
const pieOption = computed(() => {
  const items = byFeature.value.filter(i => i.tokens > 0)
  const top5 = items.slice(0, 5)
  const othersTokens = items.slice(5).reduce((s, i) => s + i.tokens, 0)
  const pieData = top5.map(i => ({
    name: featureLabel(i.feature),
    value: i.tokens,
    itemStyle: { color: featureColor(i.feature) },
  }))
  if (othersTokens > 0) pieData.push({ name: '其他', value: othersTokens, itemStyle: { color: '#ccc' } })
  return {
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { bottom: 0, type: 'scroll' },
    series: [{ type: 'pie', radius: ['35%', '65%'], data: pieData, label: { show: true, formatter: '{b}\n{d}%' } }],
  }
})

// 按模型聚合柱状图
const modelBarOption = computed(() => {
  const map: Record<string, number> = {}
  for (const r of props.data) {
    const m = r.model || 'unknown'
    map[m] = (map[m] || 0) + r.total_tokens
  }
  const sorted = Object.entries(map).sort((a, b) => b[1] - a[1])
  return {
    tooltip: { trigger: 'axis' },
    grid: { left: 120, right: 30, top: 10, bottom: 20 },
    xAxis: { type: 'value' },
    yAxis: { type: 'category', data: sorted.map(s => s[0]).reverse() },
    series: [{ type: 'bar', data: sorted.map(s => s[1]).reverse(), itemStyle: { color: '#5470c6' } }],
  }
})

const grandTotal = computed(() => byFeature.value.reduce((s, i) => s + i.tokens, 0) || 1)
</script>

<template>
  <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px">
    <VChart :option="pieOption" style="height: 320px" autoresize />
    <NTable :bordered="false" :single-line="false" size="small">
      <thead><tr><th>功能</th><th>Token</th><th>占比</th><th>调用数</th><th></th></tr></thead>
      <tbody>
        <tr v-for="r in byFeature" :key="r.feature">
          <td>{{ featureLabel(r.feature) }}</td>
          <td>{{ formatTokens(r.tokens) }}</td>
          <td>{{ (r.tokens / grandTotal * 100).toFixed(1) }}%</td>
          <td>{{ r.calls }}</td>
          <td><NTag v-if="r.estimated > 0" size="small" type="warning">⚠️ 含估算</NTag></td>
        </tr>
      </tbody>
    </NTable>
  </div>
  <VChart :option="modelBarOption" style="height: 240px" autoresize />
</template>
