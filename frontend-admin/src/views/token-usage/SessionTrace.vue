<script setup lang="ts">
/**
 * Tab 4: 会话追踪 — 搜索 + 时间轴 + 迷你饼图
 */
import { ref } from 'vue'
import { NInput, NButton, NSpace, NTimeline, NTimelineItem, NTag, NSpin, NEmpty, useMessage } from 'naive-ui'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { PieChart } from 'echarts/charts'
import { TooltipComponent, LegendComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import { featureLabel, featureColor } from './constants'
import { getSessionDetail, formatTokens, type SessionDetail } from '../../api/token-usage'

use([CanvasRenderer, PieChart, TooltipComponent, LegendComponent])

const props = defineProps<{ initialSessionId?: string }>()
const message = useMessage()

const searchId = ref(props.initialSessionId || '')
const loading = ref(false)
const detail = ref<SessionDetail | null>(null)

async function search() {
  const sid = searchId.value.trim()
  if (!sid) return
  loading.value = true
  try {
    detail.value = await getSessionDetail(sid)
  } catch {
    message.error('查询失败')
    detail.value = null
  } finally {
    loading.value = false
  }
}

// 外部调用
function setSessionId(sid: string) {
  searchId.value = sid
  search()
}
defineExpose({ setSessionId })

// 迷你饼图
function pieOption() {
  if (!detail.value) return {}
  const map: Record<string, number> = {}
  for (const r of detail.value.records) {
    map[r.feature] = (map[r.feature] || 0) + r.total_tokens
  }
  const data = Object.entries(map)
    .sort((a, b) => b[1] - a[1])
    .map(([f, v]) => ({ name: featureLabel(f), value: v, itemStyle: { color: featureColor(f) } }))
  return {
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { bottom: 0, type: 'scroll' },
    series: [{ type: 'pie', radius: ['30%', '60%'], data, label: { show: true, formatter: '{b}\n{d}%' } }],
  }
}

function timelineType(feature: string): 'success' | 'info' | 'warning' | 'error' {
  if (feature.startsWith('debate')) return 'warning'
  if (feature.startsWith('memory')) return 'info'
  if (feature === 'skill_llm') return 'success'
  return 'default' as any
}

/** 根据 feature + extra 生成可读描述 */
function describeRecord(r: typeof detail.value extends null ? never : NonNullable<typeof detail.value>['records'][0]) {
  const extra = r.extra ? (typeof r.extra === 'string' ? JSON.parse(r.extra) : r.extra) : {}
  const descs: Record<string, string> = {
    skill_llm: '执行 Skill/QA 对话推理',
    intent_recognition: '分析用户意图，匹配最佳 Skill',
    vision: '识别并描述上传的图片内容',
    memory_extract: '从对话中提取长期记忆',
    memory_dedup: '检查新记忆与已有记忆是否重复',
    plan_critic: '评估深度调研的质量',
    context_qa: '基于上下文生成追问回复',
    rerank: `对 ${extra.doc_count || '?'} 篇文档重排序，返回 ${extra.result_count || '?'} 篇`,
    debate_expert: `辩论专家${extra.expert_id ? ' #' + extra.expert_id : ''}发言${extra.round ? '（第' + extra.round + '轮）' : ''}`,
    debate_summary: extra.phase === 'opening' ? '辩论开场引导' : extra.phase === 'final_summary' ? '辩论最终总结' : `辩论第${extra.round || '?'}轮小结`,
    kb_search: '知识库向量检索',
  }
  return descs[r.feature] || featureLabel(r.feature)
}

// 初始加载
if (props.initialSessionId) search()
</script>

<template>
  <NSpace align="center" style="margin-bottom: 16px">
    <NInput v-model:value="searchId" placeholder="输入 Session ID" style="width: 360px" @keyup.enter="search" />
    <NButton type="primary" :loading="loading" @click="search">查询</NButton>
  </NSpace>

  <NSpin :show="loading">
    <template v-if="detail">
      <div style="margin-bottom: 12px; color: #666">
        会话: <b>{{ detail.title || detail.session_id }}</b> · 用户: <b>{{ detail.user_id }}</b> · 总Token: <b>{{ formatTokens(detail.total_tokens) }}</b>
        · {{ detail.records.length }} 次调用
      </div>

      <div style="display: grid; grid-template-columns: 1fr 280px; gap: 16px">
        <NTimeline>
          <NTimelineItem
            v-for="(r, i) in detail.records"
            :key="i"
            :type="timelineType(r.feature)"
            :content="`${formatTokens(r.total_tokens)} tokens (prompt: ${r.prompt_tokens}, completion: ${r.completion_tokens})`"
            :time="r.created_at"
          >
            <template #header>
              <span>{{ featureLabel(r.feature) }} — {{ r.model || '' }}</span>
              <NTag v-if="r.is_estimated" size="tiny" type="warning" style="margin-left: 6px">估算</NTag>
            </template>
            <template #default>
              <div style="color: #666; font-size: 13px; margin-bottom: 2px">{{ describeRecord(r) }}</div>
              <div>{{ formatTokens(r.total_tokens) }} tokens (prompt: {{ r.prompt_tokens }}, completion: {{ r.completion_tokens }})</div>
            </template>
          </NTimelineItem>
        </NTimeline>
        <VChart :option="pieOption()" style="height: 240px" autoresize />
      </div>
    </template>
    <NEmpty v-else-if="!loading" description="输入 Session ID 查询全链路追踪" />
  </NSpin>
</template>
