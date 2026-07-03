<script setup lang="ts">
/**
 * 统计分析页面
 * 提供文献统计、使用统计、热门查询、Token分析等可视化功能
 */
import { ref, onMounted, computed, watch } from 'vue'
import {
  NCard,
  NGrid,
  NGi,
  NStatistic,
  NTabs,
  NTabPane,
  NDatePicker,
  NSelect,
  NSpace,
  NButton,
  NSpin,
  NTable,
  NTag,
  NProgress,
  NEmpty,
  useMessage,
} from 'naive-ui'
import {
  getStatisticsOverview,
  getDocumentStatistics,
  getUsageStatistics,
  getHotQueries,
  getTokenStatistics,
  formatNumber,
  formatTokens,
  toPieChartData,
  chartColors,
  type StatisticsOverview,
  type DocumentStats,
  type UsageStats,
  type HotQueriesResponse,
  type TokenStats,
} from '../api/statistics'

const message = useMessage()

// ==================== 状态 ====================

const loading = ref(false)
const activeTab = ref('overview')

// 时间范围
const dateRange = ref<[number, number] | null>(null)
const granularity = ref<'day' | 'week' | 'month'>('day')

// 数据
const overview = ref<StatisticsOverview | null>(null)
const documentStats = ref<DocumentStats | null>(null)
const usageStats = ref<UsageStats | null>(null)
const hotQueries = ref<HotQueriesResponse | null>(null)
const tokenStats = ref<TokenStats | null>(null)

// ==================== 计算属性 ====================

const dateRangeParams = computed(() => {
  if (!dateRange.value) return {}
  const [start, end] = dateRange.value
  return {
    start_date: new Date(start).toISOString().split('T')[0],
    end_date: new Date(end).toISOString().split('T')[0]
  }
})

const granularityOptions = [
  { label: '按天', value: 'day' },
  { label: '按周', value: 'week' },
  { label: '按月', value: 'month' }
]

// 饼图数据
const categoryPieData = computed(() => {
  if (!documentStats.value) return []
  return toPieChartData(documentStats.value.by_category)
})

const methodologyPieData = computed(() => {
  if (!documentStats.value) return []
  return toPieChartData(documentStats.value.by_methodology)
})

// 使用趋势数据
const usageTrendData = computed(() => {
  if (!usageStats.value) return []
  return usageStats.value.data
})

// Token趋势数据
const tokenTrendData = computed(() => {
  if (!tokenStats.value) return []
  return tokenStats.value.daily_trend
})

// 获取最大值用于进度条显示
const maxUsageValue = computed(() => {
  if (!usageStats.value) return 1
  return Math.max(
    ...usageStats.value.data.map(d => Math.max(d.chats, d.searches, d.reviews)),
    1
  )
})

const maxTokenValue = computed(() => {
  if (!tokenStats.value || tokenStats.value.daily_trend.length === 0) return 1
  return Math.max(...tokenStats.value.daily_trend.map(d => d.tokens), 1)
})

// ==================== 数据加载 ====================

async function loadOverview() {
  try {
    overview.value = await getStatisticsOverview()
  } catch (e: any) {
    message.error(e.message || '加载概览失败')
  }
}

async function loadDocumentStats() {
  loading.value = true
  try {
    documentStats.value = await getDocumentStatistics(dateRangeParams.value)
  } catch (e: any) {
    message.error(e.message || '加载文献统计失败')
  } finally {
    loading.value = false
  }
}

async function loadUsageStats() {
  loading.value = true
  try {
    usageStats.value = await getUsageStatistics({
      ...dateRangeParams.value,
      granularity: granularity.value
    })
  } catch (e: any) {
    message.error(e.message || '加载使用统计失败')
  } finally {
    loading.value = false
  }
}

async function loadHotQueries() {
  loading.value = true
  try {
    hotQueries.value = await getHotQueries({
      ...dateRangeParams.value,
      limit: 20
    })
  } catch (e: any) {
    message.error(e.message || '加载热门查询失败')
  } finally {
    loading.value = false
  }
}

async function loadTokenStats() {
  loading.value = true
  try {
    tokenStats.value = await getTokenStatistics(dateRangeParams.value)
  } catch (e: any) {
    message.error(e.message || '加载Token统计失败')
  } finally {
    loading.value = false
  }
}

async function loadAllData() {
  loading.value = true
  try {
    await Promise.all([
      loadOverview(),
      loadDocumentStats(),
      loadUsageStats(),
      loadHotQueries(),
      loadTokenStats()
    ])
  } finally {
    loading.value = false
  }
}

function handleRefresh() {
  loadAllData()
}

// 监听Tab切换
watch(activeTab, (tab) => {
  switch (tab) {
    case 'documents':
      if (!documentStats.value) loadDocumentStats()
      break
    case 'usage':
      if (!usageStats.value) loadUsageStats()
      break
    case 'hot-queries':
      if (!hotQueries.value) loadHotQueries()
      break
    case 'tokens':
      if (!tokenStats.value) loadTokenStats()
      break
  }
})

// 监听日期范围变化
watch(dateRange, () => {
  loadAllData()
})

// 监听粒度变化
watch(granularity, () => {
  loadUsageStats()
})

// ==================== 初始化 ====================

onMounted(() => {
  // 设置默认日期范围为最近30天
  const now = Date.now()
  const thirtyDaysAgo = now - 30 * 24 * 60 * 60 * 1000
  dateRange.value = [thirtyDaysAgo, now]
})
</script>

<template>
  <div class="statistics-page">
    <!-- 顶部操作栏 -->
    <n-card size="small" style="margin-bottom: 16px">
      <n-space justify="space-between" align="center">
        <n-space>
          <n-date-picker
            v-model:value="dateRange"
            type="daterange"
            clearable
            :shortcuts="{
              '最近7天': () => {
                const now = Date.now()
                return [now - 7 * 24 * 60 * 60 * 1000, now]
              },
              '最近30天': () => {
                const now = Date.now()
                return [now - 30 * 24 * 60 * 60 * 1000, now]
              },
              '最近90天': () => {
                const now = Date.now()
                return [now - 90 * 24 * 60 * 60 * 1000, now]
              },
              '最近一年': () => {
                const now = Date.now()
                return [now - 365 * 24 * 60 * 60 * 1000, now]
              }
            }"
            style="width: 280px"
          />
          <n-select
            v-model:value="granularity"
            :options="granularityOptions"
            style="width: 100px"
          />
        </n-space>
        <n-button type="primary" :loading="loading" @click="handleRefresh">
          刷新数据
        </n-button>
      </n-space>
    </n-card>

    <!-- 概览卡片 -->
    <n-grid :cols="5" :x-gap="16" :y-gap="16" style="margin-bottom: 16px">
      <n-gi>
        <n-card>
          <n-statistic label="用户总数" :value="overview?.users.total || 0">
            <template #suffix>
              <n-tag size="small" type="info">
                本周活跃 {{ overview?.users.active_this_week || 0 }}
              </n-tag>
            </template>
          </n-statistic>
        </n-card>
      </n-gi>
      <n-gi>
        <n-card>
          <n-statistic label="文献总数" :value="overview?.documents.total || 0">
            <template #suffix>
              <n-tag size="small" type="success">
                本月 +{{ overview?.documents.this_month || 0 }}
              </n-tag>
            </template>
          </n-statistic>
        </n-card>
      </n-gi>
      <n-gi>
        <n-card>
          <n-statistic label="会话总数" :value="overview?.sessions.total || 0">
            <template #suffix>
              <n-tag size="small" type="warning">
                今日 {{ overview?.sessions.today || 0 }}
              </n-tag>
            </template>
          </n-statistic>
        </n-card>
      </n-gi>
      <n-gi>
        <n-card>
          <n-statistic label="综述任务" :value="overview?.reviews.total || 0">
            <template #suffix>
              <n-tag size="small" :type="(overview?.reviews.completion_rate || 0) > 80 ? 'success' : 'warning'">
                完成率 {{ overview?.reviews.completion_rate || 0 }}%
              </n-tag>
            </template>
          </n-statistic>
        </n-card>
      </n-gi>
      <n-gi>
        <n-card>
          <n-statistic label="Token消耗" :value="formatTokens(overview?.tokens.total || 0)">
            <template #suffix>
              <n-tag size="small" type="error">
                今日 {{ formatTokens(overview?.tokens.today || 0) }}
              </n-tag>
            </template>
          </n-statistic>
        </n-card>
      </n-gi>
    </n-grid>

    <!-- 详细统计 Tabs -->
    <n-card>
      <n-tabs v-model:value="activeTab" type="line" animated>
        <!-- 文献统计 -->
        <n-tab-pane name="documents" tab="文献统计">
          <n-spin :show="loading">
            <div class="tab-content" v-if="documentStats">
              <n-grid :cols="2" :x-gap="24" :y-gap="24">
                <!-- 按研究领域分布 -->
                <n-gi>
                  <n-card title="按研究领域分布" size="small">
                    <div class="pie-chart-container">
                      <div v-if="categoryPieData.length > 0" class="simple-pie-list">
                        <div
                          v-for="(item, index) in categoryPieData"
                          :key="item.name"
                          class="pie-item"
                        >
                          <span
                            class="color-dot"
                            :style="{ backgroundColor: chartColors[index % chartColors.length] }"
                          ></span>
                          <span class="name">{{ item.name }}</span>
                          <span class="value">{{ item.value }}</span>
                          <n-progress
                            type="line"
                            :percentage="Math.round(item.value / documentStats.total * 100)"
                            :show-indicator="false"
                            :color="chartColors[index % chartColors.length]"
                            style="flex: 1; margin-left: 12px"
                          />
                        </div>
                      </div>
                      <n-empty v-else description="暂无数据" />
                    </div>
                  </n-card>
                </n-gi>

                <!-- 按研究方法分布 -->
                <n-gi>
                  <n-card title="按研究方法分布" size="small">
                    <div class="pie-chart-container">
                      <div v-if="methodologyPieData.length > 0" class="simple-pie-list">
                        <div
                          v-for="(item, index) in methodologyPieData"
                          :key="item.name"
                          class="pie-item"
                        >
                          <span
                            class="color-dot"
                            :style="{ backgroundColor: chartColors[index % chartColors.length] }"
                          ></span>
                          <span class="name">{{ item.name }}</span>
                          <span class="value">{{ item.value }}</span>
                          <n-progress
                            type="line"
                            :percentage="Math.round(item.value / documentStats.total * 100)"
                            :show-indicator="false"
                            :color="chartColors[index % chartColors.length]"
                            style="flex: 1; margin-left: 12px"
                          />
                        </div>
                      </div>
                      <n-empty v-else description="暂无数据" />
                    </div>
                  </n-card>
                </n-gi>

                <!-- 按年份分布 -->
                <n-gi :span="2">
                  <n-card title="按年份分布" size="small">
                    <div class="bar-chart-container">
                      <div v-if="Object.keys(documentStats.by_year).length > 0" class="simple-bar-chart">
                        <div
                          v-for="(count, year) in documentStats.by_year"
                          :key="year"
                          class="bar-item"
                        >
                          <span class="label">{{ year }}</span>
                          <n-progress
                            type="line"
                            :percentage="Math.round(count / Math.max(...Object.values(documentStats.by_year)) * 100)"
                            :show-indicator="false"
                            color="#5470c6"
                            style="flex: 1; margin: 0 12px"
                          />
                          <span class="value">{{ count }}</span>
                        </div>
                      </div>
                      <n-empty v-else description="暂无数据" />
                    </div>
                  </n-card>
                </n-gi>

                <!-- 最近上传 -->
                <n-gi :span="2">
                  <n-card title="最近上传" size="small">
                    <n-table :data="documentStats.recent_uploads" :bordered="false" :single-line="false">
                      <thead>
                        <tr>
                          <th>标题</th>
                          <th>分类</th>
                          <th>上传时间</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr v-for="doc in documentStats.recent_uploads" :key="doc.id">
                          <td>{{ doc.title }}</td>
                          <td>
                            <n-tag v-if="doc.category" size="small">{{ doc.category }}</n-tag>
                            <span v-else style="color: #999">-</span>
                          </td>
                          <td>{{ doc.created_at || '-' }}</td>
                        </tr>
                      </tbody>
                    </n-table>
                  </n-card>
                </n-gi>
              </n-grid>
            </div>
            <n-empty v-else description="加载中..." />
          </n-spin>
        </n-tab-pane>

        <!-- 使用统计 -->
        <n-tab-pane name="usage" tab="使用统计">
          <n-spin :show="loading">
            <div class="tab-content" v-if="usageStats">
              <!-- 汇总数据 -->
              <n-grid :cols="4" :x-gap="16" style="margin-bottom: 24px">
                <n-gi>
                  <n-card size="small">
                    <n-statistic label="总聊天次数" :value="usageStats.summary.total_chats" />
                  </n-card>
                </n-gi>
                <n-gi>
                  <n-card size="small">
                    <n-statistic label="总检索次数" :value="usageStats.summary.total_searches" />
                  </n-card>
                </n-gi>
                <n-gi>
                  <n-card size="small">
                    <n-statistic label="总综述任务" :value="usageStats.summary.total_reviews" />
                  </n-card>
                </n-gi>
                <n-gi>
                  <n-card size="small">
                    <n-statistic label="平均日聊天" :value="usageStats.summary.avg_daily_chats" />
                  </n-card>
                </n-gi>
              </n-grid>

              <!-- 趋势图 -->
              <n-card title="使用趋势" size="small">
                <div class="trend-chart">
                  <div v-if="usageTrendData.length > 0" class="trend-list">
                    <div class="trend-header">
                      <span class="date-col">日期</span>
                      <span class="metric-col">聊天</span>
                      <span class="metric-col">检索</span>
                      <span class="metric-col">综述</span>
                    </div>
                    <div
                      v-for="item in usageTrendData.slice(-15)"
                      :key="item.date"
                      class="trend-row"
                    >
                      <span class="date-col">{{ item.date }}</span>
                      <span class="metric-col">
                        <n-progress
                          type="line"
                          :percentage="Math.round(item.chats / maxUsageValue * 100)"
                          :show-indicator="false"
                          color="#5470c6"
                          style="width: 80px; display: inline-block"
                        />
                        {{ item.chats }}
                      </span>
                      <span class="metric-col">
                        <n-progress
                          type="line"
                          :percentage="Math.round(item.searches / maxUsageValue * 100)"
                          :show-indicator="false"
                          color="#91cc75"
                          style="width: 80px; display: inline-block"
                        />
                        {{ item.searches }}
                      </span>
                      <span class="metric-col">
                        <n-progress
                          type="line"
                          :percentage="Math.round(item.reviews / maxUsageValue * 100)"
                          :show-indicator="false"
                          color="#fac858"
                          style="width: 80px; display: inline-block"
                        />
                        {{ item.reviews }}
                      </span>
                    </div>
                  </div>
                  <n-empty v-else description="暂无数据" />
                </div>
              </n-card>
            </div>
            <n-empty v-else description="加载中..." />
          </n-spin>
        </n-tab-pane>

        <!-- 热门查询 -->
        <n-tab-pane name="hot-queries" tab="热门查询">
          <n-spin :show="loading">
            <div class="tab-content" v-if="hotQueries">
              <n-card title="热门查询词 Top 20" size="small">
                <n-table
                  v-if="hotQueries.queries.length > 0"
                  :data="hotQueries.queries"
                  :bordered="false"
                  :single-line="false"
                >
                  <thead>
                    <tr>
                      <th style="width: 60px">排名</th>
                      <th>查询内容</th>
                      <th style="width: 100px">次数</th>
                      <th style="width: 160px">最后查询时间</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="(query, index) in hotQueries.queries" :key="index">
                      <td>
                        <n-tag
                          :type="index < 3 ? 'warning' : 'default'"
                          size="small"
                        >
                          {{ index + 1 }}
                        </n-tag>
                      </td>
                      <td>{{ query.query }}</td>
                      <td>
                        <n-tag type="info" size="small">{{ query.count }} 次</n-tag>
                      </td>
                      <td>{{ query.last_used || '-' }}</td>
                    </tr>
                  </tbody>
                </n-table>
                <n-empty v-else description="暂无热门查询数据" />
              </n-card>
            </div>
            <n-empty v-else description="加载中..." />
          </n-spin>
        </n-tab-pane>

        <!-- Token统计 -->
        <n-tab-pane name="tokens" tab="Token统计">
          <n-spin :show="loading">
            <div class="tab-content" v-if="tokenStats">
              <n-grid :cols="2" :x-gap="24" :y-gap="24">
                <!-- 总消耗 -->
                <n-gi>
                  <n-card size="small">
                    <n-statistic label="总Token消耗">
                      <template #default>
                        {{ formatNumber(tokenStats.total_used) }}
                      </template>
                    </n-statistic>
                  </n-card>
                </n-gi>

                <!-- 按功能分布 -->
                <n-gi>
                  <n-card title="按功能分布" size="small">
                    <div class="simple-pie-list">
                      <div
                        v-for="(value, name, index) in tokenStats.by_function"
                        :key="name"
                        class="pie-item"
                      >
                        <span
                          class="color-dot"
                          :style="{ backgroundColor: chartColors[index % chartColors.length] }"
                        ></span>
                        <span class="name">{{ name }}</span>
                        <span class="value">{{ formatNumber(value) }}</span>
                        <n-progress
                          type="line"
                          :percentage="Math.round(value / tokenStats.total_used * 100)"
                          :show-indicator="true"
                          :color="chartColors[index % chartColors.length]"
                          style="flex: 1; margin-left: 12px"
                        />
                      </div>
                    </div>
                  </n-card>
                </n-gi>

                <!-- 按用户排行 -->
                <n-gi>
                  <n-card title="用户Token消耗排行" size="small">
                    <n-table
                      v-if="tokenStats.by_user.length > 0"
                      :data="tokenStats.by_user"
                      :bordered="false"
                      :single-line="false"
                    >
                      <thead>
                        <tr>
                          <th>用户</th>
                          <th>Token消耗</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr v-for="(user, index) in tokenStats.by_user" :key="user.username">
                          <td>
                            <n-tag
                              :type="index < 3 ? 'warning' : 'default'"
                              size="small"
                              style="margin-right: 8px"
                            >
                              {{ index + 1 }}
                            </n-tag>
                            {{ user.username }}
                          </td>
                          <td>{{ formatNumber(user.tokens) }}</td>
                        </tr>
                      </tbody>
                    </n-table>
                    <n-empty v-else description="暂无数据" />
                  </n-card>
                </n-gi>

                <!-- 每日趋势 -->
                <n-gi>
                  <n-card title="Token消耗趋势" size="small">
                    <div class="trend-list" v-if="tokenTrendData.length > 0">
                      <div
                        v-for="item in tokenTrendData.slice(-10)"
                        :key="item.date"
                        class="trend-row"
                      >
                        <span class="date-col">{{ item.date }}</span>
                        <n-progress
                          type="line"
                          :percentage="Math.round(item.tokens / maxTokenValue * 100)"
                          :show-indicator="false"
                          color="#ee6666"
                          style="flex: 1; margin: 0 12px"
                        />
                        <span class="value-col">{{ formatNumber(item.tokens) }}</span>
                      </div>
                    </div>
                    <n-empty v-else description="暂无数据" />
                  </n-card>
                </n-gi>
              </n-grid>
            </div>
            <n-empty v-else description="加载中..." />
          </n-spin>
        </n-tab-pane>
      </n-tabs>
    </n-card>
  </div>
</template>

<style scoped>
.statistics-page {
  min-height: 100%;
}

.tab-content {
  padding: 16px 0;
}

/* 简单饼图列表样式 */
.simple-pie-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.pie-item {
  display: flex;
  align-items: center;
  gap: 8px;
}

.color-dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  flex-shrink: 0;
}

.pie-item .name {
  width: 80px;
  flex-shrink: 0;
  font-size: 13px;
}

.pie-item .value {
  width: 50px;
  text-align: right;
  flex-shrink: 0;
  font-weight: 500;
}

/* 条形图样式 */
.simple-bar-chart {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.bar-item {
  display: flex;
  align-items: center;
}

.bar-item .label {
  width: 60px;
  flex-shrink: 0;
  font-size: 13px;
}

.bar-item .value {
  width: 40px;
  text-align: right;
  flex-shrink: 0;
  font-weight: 500;
}

/* 趋势图样式 */
.trend-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.trend-header {
  display: flex;
  align-items: center;
  padding: 8px 0;
  border-bottom: 1px solid #eee;
  font-weight: 500;
  color: #666;
}

.trend-row {
  display: flex;
  align-items: center;
  padding: 4px 0;
}

.date-col {
  width: 100px;
  flex-shrink: 0;
  font-size: 13px;
}

.metric-col {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}

.value-col {
  width: 80px;
  text-align: right;
  flex-shrink: 0;
  font-size: 13px;
}

/* 卡片内部样式 */
.pie-chart-container,
.bar-chart-container,
.trend-chart {
  min-height: 200px;
}
</style>
