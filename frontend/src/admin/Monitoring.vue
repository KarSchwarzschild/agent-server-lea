<template>
  <div class="monitoring">
    <h2>基础设施监控</h2>

    <!-- Circuit Breakers -->
    <h3>熔断器状态</h3>
    <n-grid :cols="3" :x-gap="12" :y-gap="12" v-if="Object.keys(breakers).length > 0">
      <n-gi v-for="(b, name) in breakers" :key="name">
        <n-card size="small" :bordered="true">
          <template #header>
            <div class="breaker-header">
              <span>{{ name }}</span>
              <n-tag :type="breakerColor(b.state)" size="small">{{ b.state.toUpperCase() }}</n-tag>
            </div>
          </template>
          <n-descriptions :column="1" size="small" label-placement="left">
            <n-descriptions-item label="失败计数">
              {{ b.fail_count }} / {{ b.fail_max }}
            </n-descriptions-item>
            <n-descriptions-item label="重置超时">
              {{ b.reset_timeout }}s
            </n-descriptions-item>
          </n-descriptions>
          <n-progress
            type="line"
            :percentage="Math.min(100, (b.fail_count / b.fail_max) * 100)"
            :status="b.fail_count >= b.fail_max ? 'error' : b.fail_count > 0 ? 'warning' : 'success'"
          />
        </n-card>
      </n-gi>
    </n-grid>
    <n-empty v-else description="暂无熔断器数据（尚未有 LLM 调用）" />

    <!-- Rate Limiting -->
    <h3 style="margin-top: 24px">限流配额</h3>
    <n-card size="small" v-if="usage">
      <n-grid :cols="3" :x-gap="24">
        <n-gi>
          <n-statistic label="RPM 剩余">
            <template #default>{{ usage.rpm_remaining }}</template>
            <template #suffix>/ {{ usage.rpm_limit }}</template>
          </n-statistic>
          <n-progress
            type="line"
            :percentage="rpmPercent"
            :status="rpmPercent > 80 ? 'warning' : 'success'"
          />
        </n-gi>
        <n-gi>
          <n-statistic label="今日 Token 用量">
            <template #default>{{ formatNum(usage.daily_tokens) }}</template>
          </n-statistic>
        </n-gi>
        <n-gi>
          <n-statistic label="本月 Token 用量">
            <template #default>{{ formatNum(usage.monthly_tokens) }}</template>
          </n-statistic>
        </n-gi>
      </n-grid>
    </n-card>
    <n-empty v-else description="无用量数据" />

    <div class="footer">
      <n-text depth="3">每 5 秒自动刷新</n-text>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import {
  NGrid, NGi, NCard, NTag, NDescriptions, NDescriptionsItem,
  NProgress, NStatistic, NEmpty, NText,
} from 'naive-ui'
import request from '../utils/request'

interface BreakerInfo {
  state: string
  fail_count: number
  fail_max: number
  reset_timeout: number
}

interface UsageInfo {
  daily_tokens: number
  monthly_tokens: number
  rpm_remaining: number
  rpm_limit: number
}

const breakers = ref<Record<string, BreakerInfo>>({})
const usage = ref<UsageInfo | null>(null)
let timer: ReturnType<typeof setInterval> | null = null

const rpmPercent = computed(() => {
  if (!usage.value) return 0
  const used = usage.value.rpm_limit - usage.value.rpm_remaining
  return Math.min(100, (used / usage.value.rpm_limit) * 100)
})

function breakerColor(state: string) {
  if (state === 'closed') return 'success'
  if (state === 'half_open') return 'warning'
  return 'error'
}

function formatNum(n: number) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M'
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K'
  return String(n)
}

async function fetchData() {
  try {
    const [bRes, uRes] = await Promise.all([
      request.get('/api/admin/breakers'),
      request.get('/api/admin/usage/default'),
    ])
    breakers.value = bRes.data
    usage.value = uRes.data
  } catch { /* ignore */ }
}

onMounted(() => {
  fetchData()
  timer = setInterval(fetchData, 5000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<style scoped>
.monitoring {
  padding: 8px;
}

.monitoring h2 {
  margin: 0 0 16px;
  font-size: 18px;
}

.monitoring h3 {
  margin: 0 0 12px;
  font-size: 15px;
  color: #555;
}

.breaker-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.footer {
  text-align: center;
  margin-top: 24px;
}
</style>
