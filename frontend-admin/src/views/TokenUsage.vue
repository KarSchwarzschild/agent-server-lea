<script setup lang="ts">
/**
 * Token 用量分析 — 主页面
 */
import { ref, computed, onMounted, watch } from 'vue'
import {
  NCard, NGrid, NGi, NStatistic, NTabs, NTabPane,
  NDatePicker, NSelect, NSpace, NButton, NSpin, NTag,
  useMessage,
} from 'naive-ui'
import { getTokenOverview, getByFeature, getTrend, getByUser, formatTokens } from '../api/token-usage'
import type { TokenOverview, FeatureRow, TrendRow, UserRow } from '../api/token-usage'
import { FEATURE_LABELS } from './token-usage/constants'
import FeatureDistribution from './token-usage/FeatureDistribution.vue'
import TimeTrend from './token-usage/TimeTrend.vue'
import UserRanking from './token-usage/UserRanking.vue'
import SessionTrace from './token-usage/SessionTrace.vue'

const message = useMessage()

// 日期范围：默认最近 7 天
const now = Date.now()
const dateRange = ref<[number, number]>([now - 7 * 86400000, now])
const modelFilter = ref<string | null>(null)
const featureFilter = ref<string | null>(null)
const activeTab = ref('feature')
const loading = ref(false)
const granularity = ref('day')

// 数据
const overview = ref<TokenOverview | null>(null)
const featureData = ref<FeatureRow[]>([])
const trendData = ref<TrendRow[]>([])
const userData = ref<UserRow[]>([])

// session trace ref
const sessionTraceRef = ref<InstanceType<typeof SessionTrace> | null>(null)

const dateParams = computed(() => ({
  start_date: new Date(dateRange.value[0]).toISOString().split('T')[0],
  end_date: new Date(dateRange.value[1]).toISOString().split('T')[0],
}))

const featureOptions = computed(() => [
  { label: '全部功能', value: null as any },
  ...Object.entries(FEATURE_LABELS).map(([k, v]) => ({ label: v, value: k })),
])

// 模型选项从 featureData 中提取
const modelOptions = computed(() => {
  const models = [...new Set(featureData.value.map(r => r.model).filter(Boolean))]
  return [{ label: '全部模型', value: null as any }, ...models.map(m => ({ label: m, value: m }))]
})

async function loadAll() {
  loading.value = true
  try {
    const base = { ...dateParams.value }
    const [ov, feat] = await Promise.all([
      getTokenOverview({ ...base, model: modelFilter.value || undefined, feature: featureFilter.value || undefined }),
      getByFeature({ ...base, model: modelFilter.value || undefined }),
    ])
    overview.value = ov
    featureData.value = feat
    // 按需加载当前 tab
    if (activeTab.value === 'trend') await loadTrend()
    if (activeTab.value === 'user') await loadUsers()
  } catch {
    message.error('加载失败')
  } finally {
    loading.value = false
  }
}

async function loadTrend() {
  trendData.value = await getTrend({
    ...dateParams.value,
    granularity: granularity.value,
    model: modelFilter.value || undefined,
    feature: featureFilter.value || undefined,
  })
}

async function loadUsers() {
  userData.value = await getByUser({ ...dateParams.value, limit: 20 })
}

function onTabChange(tab: string) {
  if (tab === 'trend' && !trendData.value.length) loadTrend()
  if (tab === 'user' && !userData.value.length) loadUsers()
}

function onGranularityChange(g: string) {
  granularity.value = g
  loadTrend()
}

function onTraceSession(sid: string) {
  activeTab.value = 'session'
  setTimeout(() => sessionTraceRef.value?.setSessionId(sid), 100)
}

function wowTag(pct: number) {
  if (pct > 0) return `↑${pct}%`
  if (pct < 0) return `↓${Math.abs(pct)}%`
  return '-'
}

function wowType(pct: number): 'success' | 'error' | 'default' {
  if (pct > 0) return 'error'
  if (pct < 0) return 'success'
  return 'default'
}

watch(dateRange, () => { loadAll() })
watch([modelFilter, featureFilter], () => { loadAll() })
onMounted(loadAll)
</script>

<template>
  <NSpin :show="loading">
    <!-- 顶部筛选 -->
    <NSpace align="center" style="margin-bottom: 16px">
      <NDatePicker v-model:value="dateRange" type="daterange" clearable style="width: 280px" />
      <NSelect v-model:value="modelFilter" :options="modelOptions" placeholder="模型" clearable style="width: 160px" />
      <NSelect v-model:value="featureFilter" :options="featureOptions" placeholder="功能" clearable style="width: 160px" />
      <NButton @click="loadAll">刷新</NButton>
    </NSpace>

    <!-- 概览卡片 -->
    <NGrid :cols="4" :x-gap="12" :y-gap="12" style="margin-bottom: 16px" responsive="screen" item-responsive>
      <NGi span="4 s:2 m:1">
        <NCard size="small">
          <NStatistic label="总 Token" :value="overview?.total_tokens ?? 0">
            <template #suffix>
              <NTag v-if="overview" :type="wowType(overview.week_over_week.tokens_pct)" size="small" round>
                {{ wowTag(overview.week_over_week.tokens_pct) }}
              </NTag>
            </template>
          </NStatistic>
        </NCard>
      </NGi>
      <NGi span="4 s:2 m:1">
        <NCard size="small">
          <NStatistic label="总调用" :value="overview?.total_calls ?? 0">
            <template #suffix>
              <NTag v-if="overview" :type="wowType(overview.week_over_week.calls_pct)" size="small" round>
                {{ wowTag(overview.week_over_week.calls_pct) }}
              </NTag>
            </template>
          </NStatistic>
        </NCard>
      </NGi>
      <NGi span="4 s:2 m:1">
        <NCard size="small">
          <NStatistic label="活跃用户" :value="overview?.active_users ?? 0">
            <template #suffix>
              <NTag v-if="overview" :type="wowType(overview.week_over_week.users_pct)" size="small" round>
                {{ wowTag(overview.week_over_week.users_pct) }}
              </NTag>
            </template>
          </NStatistic>
        </NCard>
      </NGi>
      <NGi span="4 s:2 m:1">
        <NCard size="small">
          <NStatistic label="估算值占比">
            <template #default>
              <span v-if="overview">{{ overview.estimated_ratio.pct }}%</span>
              <span v-else>-</span>
            </template>
            <template #suffix>
              <NTag v-if="overview && overview.estimated_ratio.count > 0" size="small" type="warning">
                ⚠️ {{ overview.estimated_ratio.count }}/{{ overview.estimated_ratio.total }}
              </NTag>
            </template>
          </NStatistic>
        </NCard>
      </NGi>
    </NGrid>

    <!-- Tab 区域 -->
    <NCard>
      <NTabs v-model:value="activeTab" type="line" @update:value="onTabChange">
        <NTabPane name="feature" tab="功能分布">
          <FeatureDistribution :data="featureData" />
        </NTabPane>
        <NTabPane name="trend" tab="时间趋势">
          <TimeTrend :data="trendData" @granularity="onGranularityChange" />
        </NTabPane>
        <NTabPane name="user" tab="用户排行">
          <UserRanking :data="userData" @trace="onTraceSession" />
        </NTabPane>
        <NTabPane name="session" tab="会话追踪">
          <SessionTrace ref="sessionTraceRef" />
        </NTabPane>
      </NTabs>
    </NCard>
  </NSpin>
</template>
