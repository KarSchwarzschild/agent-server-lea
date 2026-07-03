<script setup lang="ts">
import { h, ref, onMounted, onBeforeUnmount } from 'vue'
import {
  NSpin, NSpace, NAlert, NSwitch, NButton, NCard, NTag,
  NText, NProgress, NDataTable, NEmpty,
  useMessage,
} from 'naive-ui'
import { getProviderHealth } from '../../api/super-agent'

const message = useMessage()

interface BreakerInfo {
  name: string
  state: 'closed' | 'open' | 'half_open'
  failure_count: number
  total_calls: number
  total_failures: number
  success_rate: number
  last_failure_ago: string | null
}

interface EndpointInfo {
  name: string
  displayName: string
  status: 'closed' | 'open' | 'half_open'
  total_calls: number
  total_failures: number
  success_rate: number
  models: BreakerInfo[]
}

const healthLoading = ref(false)
const healthEndpoints = ref<EndpointInfo[]>([])
const healthAutoRefresh = ref(false)
let healthTimer: ReturnType<typeof setInterval> | null = null

const HEALTH_STATE_MAP: Record<string, { label: string; type: 'success' | 'error' | 'warning' }> = {
  closed: { label: '正常运行', type: 'success' },
  open: { label: '故障保护中', type: 'error' },
  half_open: { label: '恢复检测中', type: 'warning' }
}

async function loadProviderHealth() {
  healthLoading.value = true
  try {
    const res = await getProviderHealth()
    const rawEndpoints = (res as any)?.endpoints ?? {}
    const arr: EndpointInfo[] = []
    for (const [key, ep] of Object.entries(rawEndpoints)) {
      const epData = ep as any
      arr.push({
        name: key,
        displayName: epData.display_name || key,
        status: epData.status || 'closed',
        total_calls: epData.total_calls || 0,
        total_failures: epData.total_failures || 0,
        success_rate: epData.success_rate ?? 1.0,
        models: (epData.models || []).map((m: any) => ({
          ...m,
          name: (m.name || '').replace(/^llm:/, ''),
        })),
      })
    }
    healthEndpoints.value = arr
  } catch (e: any) {
    message.error(e.message || '加载模型健康状态失败')
  } finally {
    healthLoading.value = false
  }
}

const breakerColumns = [
  { title: '模型', key: 'name', width: 200 },
  {
    title: '状态',
    key: 'state',
    width: 100,
    render(row: BreakerInfo) {
      const info = HEALTH_STATE_MAP[row.state] || { label: row.state, type: 'default' as const }
      return h(NTag, { size: 'small', type: info.type, round: true }, { default: () => info.label })
    },
  },
  { title: '调用次数', key: 'total_calls', width: 80 },
  { title: '失败次数', key: 'total_failures', width: 80 },
  {
    title: '成功率',
    key: 'success_rate',
    width: 80,
    render(row: BreakerInfo) {
      return `${(row.success_rate * 100).toFixed(1)}%`
    },
  },
  {
    title: '连续失败',
    key: 'failure_count',
    width: 80,
  },
  {
    title: '上次失败',
    key: 'last_failure_ago',
    width: 100,
    render(row: BreakerInfo) {
      if (!row.last_failure_ago) return '-'
      const secs = Number(row.last_failure_ago)
      if (isNaN(secs)) return row.last_failure_ago
      if (secs < 60) return `${Math.round(secs)}秒前`
      if (secs < 3600) return `${Math.round(secs / 60)}分钟前`
      if (secs < 86400) return `${(secs / 3600).toFixed(1)}小时前`
      return `${(secs / 86400).toFixed(1)}天前`
    },
  },
]

function toggleHealthAutoRefresh(val: boolean) {
  healthAutoRefresh.value = val
  if (val) {
    healthTimer = setInterval(loadProviderHealth, 30000)
  } else {
    if (healthTimer) {
      clearInterval(healthTimer)
      healthTimer = null
    }
  }
}

onBeforeUnmount(() => {
  if (healthTimer) clearInterval(healthTimer)
})

onMounted(() => { loadProviderHealth() })
</script>

<template>
  <n-spin :show="healthLoading">
    <div class="tab-content">
      <n-space justify="space-between" align="center" style="margin-bottom: 16px">
        <n-alert type="info" title="说明" :bordered="false" style="flex: 1">
          查看各 Provider 端点的熔断器状态和成功率。熔断器在首次 LLM 调用后自动创建。
        </n-alert>
        <n-space>
          <n-switch
            :value="healthAutoRefresh"
            @update:value="toggleHealthAutoRefresh"
          >
            <template #checked>自动刷新</template>
            <template #unchecked>自动刷新</template>
          </n-switch>
          <n-button @click="loadProviderHealth" :loading="healthLoading">
            刷新
          </n-button>
        </n-space>
      </n-space>

      <div v-if="healthEndpoints.length === 0 && !healthLoading" style="text-align: center; padding: 60px 20px;">
        <n-empty description="尚无健康数据">
          <template #extra>
            <n-text depth="3" style="font-size: 13px;">
              熔断器在用户发起首次 AI 对话后自动创建。请先通过用户端发送一条消息，然后刷新此页面。
            </n-text>
          </template>
        </n-empty>
      </div>

      <n-space vertical :size="16" v-else>
        <n-card
          v-for="ep in healthEndpoints"
          :key="ep.name"
          size="small"
          :bordered="true"
        >
          <template #header>
            <n-space align="center" :size="8">
              <span style="font-weight: 600;">{{ ep.displayName }}</span>
              <n-tag
                size="small"
                round
                :type="HEALTH_STATE_MAP[ep.status]?.type || 'default'"
              >
                {{ HEALTH_STATE_MAP[ep.status]?.label || ep.status }}
              </n-tag>
            </n-space>
          </template>
          <template #header-extra>
            <n-space :size="16">
              <n-text depth="3" style="font-size: 13px;">
                总调用: {{ ep.total_calls }}
              </n-text>
              <n-text depth="3" style="font-size: 13px;">
                失败: {{ ep.total_failures }}
              </n-text>
            </n-space>
          </template>

          <div style="margin-bottom: 12px;">
            <n-space justify="space-between" align="center" style="margin-bottom: 4px;">
              <n-text depth="3" style="font-size: 13px;">成功率</n-text>
              <n-text :style="{ color: ep.success_rate >= 0.9 ? '#18a058' : ep.success_rate >= 0.5 ? '#f0a020' : '#d03050', fontWeight: 600 }">
                {{ (ep.success_rate * 100).toFixed(1) }}%
              </n-text>
            </n-space>
            <n-progress
              type="line"
              :percentage="Math.round(ep.success_rate * 100)"
              :color="ep.success_rate >= 0.9 ? '#18a058' : ep.success_rate >= 0.5 ? '#f0a020' : '#d03050'"
              :rail-color="'#e8e8e8'"
              :show-indicator="false"
            />
          </div>

          <n-text depth="3" style="font-size: 12px; display: block; margin-bottom: 8px;">模型级熔断器</n-text>
          <n-data-table
            :columns="breakerColumns"
            :data="ep.models"
            :bordered="false"
            :single-line="false"
            size="small"
            :pagination="false"
          />
        </n-card>
      </n-space>
    </div>
  </n-spin>
</template>
