<script setup lang="ts">
/**
 * 管理后台仪表板
 * - 统计概览（用户、文献、会话、Token）
 * - 近期活动列表
 * - 会话趋势图
 * - 系统健康状态
 * - 手动/自动刷新
 * - 缓存状态指示
 */
import { ref, onMounted, onUnmounted, computed } from 'vue'
import {
  NGrid,
  NGi,
  NCard,
  NStatistic,
  NSpin,
  NList,
  NListItem,
  NTag,
  NIcon,
  NButton,
  NSwitch,
  NSpace,
  NTooltip,
  NDivider,
  NProgress,
  useMessage,
} from 'naive-ui'
import {
  DocumentTextOutline,
  PeopleOutline,
  ChatbubblesOutline,
  FlashOutline,
  TimeOutline,
  RefreshOutline,
  ServerOutline,
  CheckmarkCircleOutline,
  AlertCircleOutline,
} from '@vicons/ionicons5'
import { getDashboard, refreshDashboard, type DashboardData } from '../api/dashboard'

const message = useMessage()
const loading = ref(true)
const refreshing = ref(false)
const dashboardData = ref<DashboardData | null>(null)

// 自动刷新
const autoRefresh = ref(false)
const autoRefreshInterval = ref(60) // 60秒
let refreshTimer: number | null = null

// 加载仪表板数据
async function loadDashboard(forceRefresh = false) {
  if (forceRefresh) {
    refreshing.value = true
  } else {
    loading.value = true
  }

  try {
    const data = forceRefresh ? await refreshDashboard() : await getDashboard()
    dashboardData.value = data

    if (forceRefresh) {
      message.success('数据已刷新')
    }
  } catch (error) {
    console.error('Failed to load dashboard:', error)
    message.error('加载仪表板数据失败')
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

// 手动刷新
async function handleRefresh() {
  await loadDashboard(true)
}

// 切换自动刷新
function toggleAutoRefresh(value: boolean) {
  autoRefresh.value = value
  if (value) {
    startAutoRefresh()
    message.info(`已开启自动刷新，每 ${autoRefreshInterval.value} 秒刷新一次`)
  } else {
    stopAutoRefresh()
    message.info('已关闭自动刷新')
  }
}

function startAutoRefresh() {
  stopAutoRefresh()
  refreshTimer = window.setInterval(() => {
    loadDashboard(false)
  }, autoRefreshInterval.value * 1000)
}

function stopAutoRefresh() {
  if (refreshTimer) {
    clearInterval(refreshTimer)
    refreshTimer = null
  }
}

// 格式化时间
function formatTime(time: string): string {
  const date = new Date(time)
  const now = new Date()
  const diff = now.getTime() - date.getTime()

  if (diff < 60000) {
    return '刚刚'
  } else if (diff < 3600000) {
    return `${Math.floor(diff / 60000)} 分钟前`
  } else if (diff < 86400000) {
    return `${Math.floor(diff / 3600000)} 小时前`
  } else {
    return date.toLocaleDateString()
  }
}

// 格式化运行时间
function formatUptime(seconds: number): string {
  if (seconds < 60) return `${seconds}秒`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}分钟`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}小时`
  return `${Math.floor(seconds / 86400)}天`
}

// 获取活动类型标签
function getActivityLabel(action: string): { type: 'success' | 'info' | 'warning' | 'error'; label: string } {
  const map: Record<string, { type: 'success' | 'info' | 'warning' | 'error'; label: string }> = {
    login: { type: 'success', label: '登录' },
    logout: { type: 'info', label: '登出' },
    create: { type: 'success', label: '创建' },
    update: { type: 'info', label: '更新' },
    delete: { type: 'error', label: '删除' },
  }
  return map[action] || { type: 'warning', label: action }
}

// 趋势图最大值
const trendMax = computed(() => {
  if (!dashboardData.value?.evaluation_trend) return 1
  const values = Object.values(dashboardData.value.evaluation_trend)
  return Math.max(...values, 1)
})

onMounted(() => {
  loadDashboard()
})

onUnmounted(() => {
  stopAutoRefresh()
})
</script>

<template>
  <div class="dashboard">
    <n-spin :show="loading">
      <!-- 顶部操作栏 -->
      <div class="dashboard-header">
        <n-space align="center">
          <n-button
            :loading="refreshing"
            @click="handleRefresh"
            :disabled="loading"
          >
            <template #icon>
              <n-icon :component="RefreshOutline" />
            </template>
            刷新
          </n-button>

          <n-divider vertical />

          <n-space align="center">
            <span class="auto-refresh-label">自动刷新</span>
            <n-switch
              :value="autoRefresh"
              @update:value="toggleAutoRefresh"
            />
          </n-space>

          <!-- 缓存状态指示 -->
          <n-divider vertical />
          <n-tooltip v-if="dashboardData?.cache_info">
            <template #trigger>
              <n-tag :type="dashboardData.cache_info.cached ? 'info' : 'success'" size="small">
                {{ dashboardData.cache_info.cached ? '缓存' : '实时' }}
              </n-tag>
            </template>
            <div v-if="dashboardData.cache_info.cached">
              缓存于: {{ dashboardData.cache_info.cached_at ? new Date(dashboardData.cache_info.cached_at).toLocaleString() : '-' }}
            </div>
            <div v-else>数据来自数据库</div>
          </n-tooltip>
        </n-space>
      </div>

      <!-- 统计卡片 -->
      <n-grid :cols="4" :x-gap="16" :y-gap="16" style="margin-bottom: 16px">
        <n-gi>
          <n-card class="stat-card">
            <n-statistic label="用户总数" :value="dashboardData?.stats?.total_users ?? 0">
              <template #prefix>
                <n-icon :component="PeopleOutline" class="stat-icon" />
              </template>
            </n-statistic>
            <div class="stat-footer">
              活跃: {{ dashboardData?.stats?.active_users ?? 0 }}
            </div>
          </n-card>
        </n-gi>

        <n-gi>
          <n-card class="stat-card">
            <n-statistic label="文献总数" :value="dashboardData?.stats?.total_documents ?? 0">
              <template #prefix>
                <n-icon :component="DocumentTextOutline" class="stat-icon doc-icon" />
              </template>
            </n-statistic>
          </n-card>
        </n-gi>

        <n-gi>
          <n-card class="stat-card">
            <n-statistic label="会话总数" :value="dashboardData?.stats?.total_sessions ?? 0">
              <template #prefix>
                <n-icon :component="ChatbubblesOutline" class="stat-icon chat-icon" />
              </template>
            </n-statistic>
            <div class="stat-footer">
              消息: {{ dashboardData?.stats?.total_messages ?? 0 }}
            </div>
          </n-card>
        </n-gi>

        <n-gi>
          <n-card class="stat-card">
            <n-statistic label="今日Token">
              <template #prefix>
                <n-icon :component="FlashOutline" class="stat-icon token-icon" />
              </template>
              <template #default>
                {{ (dashboardData?.stats?.tokens_used_today ?? 0).toLocaleString() }}
              </template>
            </n-statistic>
            <div class="stat-footer">
              总计: {{ (dashboardData?.stats?.tokens_used_total ?? 0).toLocaleString() }}
            </div>
          </n-card>
        </n-gi>
      </n-grid>

      <!-- 近期活动和趋势 -->
      <n-grid :cols="2" :x-gap="16" :y-gap="16" style="margin-bottom: 16px">
        <n-gi>
          <n-card title="近期活动">
            <n-list v-if="dashboardData?.recent_activities?.length" :show-divider="false">
              <n-list-item v-for="activity in dashboardData.recent_activities.slice(0, 8)" :key="activity.id">
                <div class="activity-item">
                  <div class="activity-info">
                    <n-tag :type="getActivityLabel(activity.action).type" size="small">
                      {{ getActivityLabel(activity.action).label }}
                    </n-tag>
                    <span class="activity-desc">
                      {{ activity.username }} - {{ activity.resource }}
                    </span>
                  </div>
                  <div class="activity-time">
                    <n-icon :component="TimeOutline" size="14" />
                    <span>{{ formatTime(activity.created_at) }}</span>
                  </div>
                </div>
              </n-list-item>
            </n-list>
            <div v-else class="empty-state">暂无活动记录</div>
          </n-card>
        </n-gi>

        <n-gi>
          <n-card title="会话趋势（近7天）">
            <div class="trend-list" v-if="dashboardData?.evaluation_trend">
              <div
                v-for="(count, date) in dashboardData.evaluation_trend"
                :key="date"
                class="trend-item"
              >
                <span class="trend-date">{{ date }}</span>
                <div class="trend-bar-container">
                  <n-progress
                    type="line"
                    :percentage="(count / trendMax) * 100"
                    :show-indicator="false"
                    :height="8"
                    :border-radius="4"
                    color="#18a058"
                    rail-color="#eee"
                  />
                </div>
                <span class="trend-count">{{ count }}</span>
              </div>
            </div>
            <div v-else class="empty-state">暂无数据</div>
          </n-card>
        </n-gi>
      </n-grid>

      <!-- 系统健康状态 -->
      <n-card title="系统状态">
        <n-space align="center" :size="32" wrap>
          <!-- 健康状态 -->
          <n-space align="center">
            <n-icon
              :component="dashboardData?.system_health?.status === 'healthy' ? CheckmarkCircleOutline : AlertCircleOutline"
              :color="dashboardData?.system_health?.status === 'healthy' ? '#18a058' : '#d03050'"
              size="24"
            />
            <div class="system-info">
              <span class="system-label">系统状态</span>
              <n-tag :type="dashboardData?.system_health?.status === 'healthy' ? 'success' : 'error'" size="small">
                {{ dashboardData?.system_health?.status === 'healthy' ? '健康' : '异常' }}
              </n-tag>
            </div>
          </n-space>

          <!-- 数据库 -->
          <n-space align="center">
            <n-icon :component="ServerOutline" color="#2080f0" size="24" />
            <div class="system-info">
              <span class="system-label">数据库</span>
              <n-tag :type="dashboardData?.system_health?.database === 'connected' ? 'success' : 'error'" size="small">
                {{ dashboardData?.system_health?.database === 'connected' ? '正常' : '异常' }}
              </n-tag>
            </div>
          </n-space>

          <!-- 运行时间 -->
          <n-space align="center">
            <n-icon :component="TimeOutline" color="#f0a020" size="24" />
            <div class="system-info">
              <span class="system-label">运行时间</span>
              <span class="system-value">{{ formatUptime(dashboardData?.system_health?.uptime_seconds || 0) }}</span>
            </div>
          </n-space>
        </n-space>
      </n-card>
    </n-spin>
  </div>
</template>

<style scoped>
.dashboard {
  min-height: 100%;
}

.dashboard-header {
  margin-bottom: 16px;
  padding: 12px 16px;
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);
}

.auto-refresh-label {
  font-size: 14px;
  color: #666;
}

.stat-card {
  transition: transform 0.2s, box-shadow 0.2s;
}

.stat-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
}

.stat-icon {
  font-size: 20px;
  color: #18a058;
}

.doc-icon {
  color: #2080f0;
}

.chat-icon {
  color: #722ed1;
}

.token-icon {
  color: #f0a020;
}

.stat-footer {
  margin-top: 8px;
  font-size: 12px;
  color: #999;
}

.activity-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 4px 0;
}

.activity-info {
  display: flex;
  align-items: center;
  gap: 8px;
}

.activity-desc {
  font-size: 14px;
}

.activity-time {
  display: flex;
  align-items: center;
  gap: 4px;
  color: #999;
  font-size: 12px;
}

.empty-state {
  text-align: center;
  padding: 24px;
  color: #999;
}

.trend-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.trend-item {
  display: flex;
  align-items: center;
  gap: 12px;
}

.trend-date {
  width: 90px;
  font-size: 12px;
  color: #666;
}

.trend-bar-container {
  flex: 1;
}

.trend-count {
  width: 30px;
  text-align: right;
  font-size: 12px;
  font-weight: 600;
}

.system-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.system-label {
  font-size: 12px;
  color: #666;
}

.system-value {
  font-size: 14px;
  font-weight: 600;
  color: #333;
}

/* 卡片统一阴影和圆角 */
:deep(.n-card) {
  border-radius: 8px;
  box-shadow: 0 4px 18px rgba(0, 0, 0, 0.04);
}
</style>
