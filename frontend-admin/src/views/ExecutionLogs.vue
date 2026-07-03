<script setup lang="ts">
/**
 * Skill 执行日志页面
 *
 * 功能：
 * - 查看执行日志列表
 * - 按 Skill/状态/时间筛选
 * - 查看执行详情
 * - 统计分析（每日趋势、成功率等）
 * - 批量删除日志
 */

import { ref, reactive, computed, onMounted, h } from 'vue'
import MarkdownIt from 'markdown-it'
import {
  NCard,
  NButton,
  NSpace,
  NGrid,
  NGridItem,
  NStatistic,
  NTag,
  NSelect,
  NIcon,
  NDrawer,
  NDrawerContent,
  NDescriptions,
  NDescriptionsItem,
  NSpin,
  NDatePicker,
  NPagination,
  NPopconfirm,
  NDataTable,
  NTabs,
  NTabPane,
  NCollapse,
  NCollapseItem,
  NEmpty,
  NTooltip,
  useMessage,
  type DataTableColumns
} from 'naive-ui'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart, BarChart } from 'echarts/charts'
import {
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  GridComponent
} from 'echarts/components'
import VChart from 'vue-echarts'
import {
  RefreshOutline,
  SearchOutline,
  TrashOutline,
  TimeOutline,
  CheckmarkCircleOutline,
  CloseCircleOutline,
  SpeedometerOutline,
  DocumentTextOutline
} from '@vicons/ionicons5'
import {
  getSkillLogs,
  getLogStats,
  getDailyStats,
  getLogDetail,
  deleteLog,
  batchDeleteLogs,
  getSkills,
  getToolCallsSummary,
  getToolCallDetail,
  getWaterfallData,
  LOG_STATUS_MAP,
  LOG_STATUS_TYPE,
  TOOL_CALL_STATUS_MAP,
  TOOL_CALL_STATUS_TYPE,
  formatDuration,
  formatTokens,
  getToolDisplayName,
  type SkillLog,
  type LogStats,
  type DailyStats,
  type ToolCallSummaryResponse,
  type ToolCallDetail,
  type WaterfallResponse
} from '../api/super-agent'

// 注册 ECharts 组件
use([
  CanvasRenderer,
  LineChart,
  BarChart,
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  GridComponent
])

const message = useMessage()

// Markdown 渲染器
const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true
})

function renderMarkdown(text: string): string {
  if (!text) return ''
  return md.render(text)
}

// ==================== 响应式数据 ====================

const loading = ref(false)
const logs = ref<SkillLog[]>([])
const stats = ref<LogStats | null>(null)
const dailyStats = ref<DailyStats[]>([])
const skillOptions = ref<{ label: string; value: string }[]>([])

// 分页
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(20)

// 筛选条件
const filters = reactive({
  skill_name: '' as string,
  status: '' as string,
  date_range: null as [number, number] | null
})

// 详情抽屉
const detailDrawer = ref(false)
const currentLog = ref<SkillLog | null>(null)
const detailLoading = ref(false)
const activeTab = ref('basic')

// 工具调用相关
const toolCallsLoading = ref(false)
const toolCallsSummary = ref<ToolCallSummaryResponse | null>(null)
const selectedToolCall = ref<ToolCallDetail | null>(null)
const toolCallDetailDrawer = ref(false)
const toolCallDetailLoading = ref(false)
const waterfallData = ref<WaterfallResponse | null>(null)
const waterfallLoading = ref(false)

// 删除确认
const deleteLoading = ref(false)

// 工具调用详情默认展开项
const defaultExpandedNames = ['input', 'output']

// ==================== 计算属性 ====================

const statusOptions = [
  { label: '全部状态', value: '' },
  { label: '等待中', value: 'pending' },
  { label: '执行中', value: 'running' },
  { label: '已完成', value: 'completed' },
  { label: '失败', value: 'failed' }
]

const successRate = computed(() => {
  if (!stats.value || stats.value.total_executions === 0) return '0%'
  const rate = (stats.value.success_count / stats.value.total_executions) * 100
  return `${rate.toFixed(1)}%`
})

// 每日趋势图配置
const chartOption = computed(() => {
  // 计算Y轴最大值，确保至少为5，避免数据少时图表比例失调
  const maxExec = Math.max(...dailyStats.value.map(d => d.executions), 0)
  const yAxisMax = Math.max(maxExec + 2, 5)

  return {
    tooltip: {
      trigger: 'axis'
    },
    legend: {
      data: ['执行次数', '成功', '失败'],
      top: 0,
      left: 'center'
    },
    grid: {
      left: '3%',
      right: '4%',
      top: '40px',
      bottom: '30px',
      containLabel: true
    },
    xAxis: {
      type: 'category',
      data: dailyStats.value.map(d => d.date),
      axisLabel: {
        rotate: dailyStats.value.length > 5 ? 30 : 0
      }
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: yAxisMax,
      minInterval: 1
    },
    series: [
      {
        name: '执行次数',
        type: 'line',
        data: dailyStats.value.map(d => d.executions),
        smooth: true,
        symbolSize: 6
      },
      {
        name: '成功',
        type: 'bar',
        data: dailyStats.value.map(d => d.success),
        itemStyle: { color: '#18a058' },
        barMaxWidth: 40
      },
      {
        name: '失败',
        type: 'bar',
        data: dailyStats.value.map(d => d.failure),
        itemStyle: { color: '#d03050' },
        barMaxWidth: 40
      }
    ]
  }
})

// 表格列定义
const columns: DataTableColumns<SkillLog> = [
  {
    title: 'ID',
    key: 'id',
    width: 80
  },
  {
    title: 'Skill',
    key: 'skill_name',
    width: 150,
    render(row) {
      return h(NTag, { size: 'small', type: 'info' }, { default: () => row.skill_name })
    }
  },
  {
    title: '用户',
    key: 'username',
    width: 120,
    render(row) {
      return row.username || row.user_id
    }
  },
  {
    title: '输入',
    key: 'input_text',
    ellipsis: {
      tooltip: true
    },
    render(row) {
      return row.input_text?.substring(0, 50) + (row.input_text?.length > 50 ? '...' : '')
    }
  },
  {
    title: '状态',
    key: 'status',
    width: 100,
    render(row) {
      return h(
        NTag,
        { size: 'small', type: LOG_STATUS_TYPE[row.status] },
        { default: () => LOG_STATUS_MAP[row.status] }
      )
    }
  },
  {
    title: '工具调用',
    key: 'tool_calls',
    width: 120,
    render(row) {
      const total = row.tool_calls_count || 0
      const success = row.tool_calls_success || 0
      const failed = row.tool_calls_failed || 0
      if (total === 0) return '-'
      return h(NSpace, { size: 'small', align: 'center' }, {
        default: () => [
          h('span', {}, `${total}次`),
          failed > 0
            ? h(NTag, { size: 'tiny', type: 'error' }, { default: () => `${failed}失败` })
            : success === total
              ? h(NTag, { size: 'tiny', type: 'success' }, { default: () => '全部成功' })
              : null
        ].filter(Boolean)
      })
    }
  },
  {
    title: '耗时',
    key: 'duration_ms',
    width: 100,
    render(row) {
      return formatDuration(row.duration_ms)
    }
  },
  {
    title: 'Tokens',
    key: 'tokens_used',
    width: 100,
    render(row) {
      return formatTokens(row.tokens_used)
    }
  },
  {
    title: '时间',
    key: 'created_at',
    width: 180,
    render(row) {
      return new Date(row.created_at).toLocaleString('zh-CN')
    }
  },
  {
    title: '操作',
    key: 'actions',
    width: 120,
    render(row) {
      return h(NSpace, { size: 'small' }, {
        default: () => [
          h(
            NButton,
            {
              size: 'small',
              quaternary: true,
              onClick: () => handleViewDetail(row)
            },
            { default: () => '详情' }
          ),
          h(
            NPopconfirm,
            {
              onPositiveClick: () => handleDelete(row)
            },
            {
              trigger: () => h(
                NButton,
                { size: 'small', quaternary: true, type: 'error' },
                { default: () => '删除' }
              ),
              default: () => '确认删除此日志？'
            }
          )
        ]
      })
    }
  }
]

// ==================== 方法 ====================

async function loadData() {
  loading.value = true
  try {
    const params: any = {
      page: currentPage.value,
      page_size: pageSize.value
    }
    if (filters.skill_name) params.skill_name = filters.skill_name
    if (filters.status) params.status = filters.status
    if (filters.date_range) {
      params.date_from = new Date(filters.date_range[0]).toISOString().split('T')[0]
      params.date_to = new Date(filters.date_range[1]).toISOString().split('T')[0]
    }

    const [logsRes, statsRes, dailyRes] = await Promise.all([
      getSkillLogs(params),
      getLogStats({ days: 7 }),
      getDailyStats({ days: 7 })
    ])

    logs.value = logsRes.logs || []
    total.value = logsRes.total || 0
    stats.value = statsRes
    dailyStats.value = dailyRes || []
  } catch (error: any) {
    message.error(error.message || '加载数据失败')
  } finally {
    loading.value = false
  }
}

async function loadSkillOptions() {
  try {
    const res = await getSkills()
    skillOptions.value = [
      { label: '全部 Skills', value: '' },
      ...(res.skills || []).map(s => ({
        label: s.title,
        value: s.name
      }))
    ]
  } catch (error) {
    // 忽略错误
  }
}

async function handleViewDetail(log: SkillLog) {
  detailDrawer.value = true
  detailLoading.value = true
  activeTab.value = 'basic'
  // 重置工具调用相关数据
  toolCallsSummary.value = null
  selectedToolCall.value = null
  waterfallData.value = null
  try {
    currentLog.value = await getLogDetail(log.id)
  } catch (error: any) {
    message.error(error.message || '获取详情失败')
    currentLog.value = log
  } finally {
    detailLoading.value = false
  }
}

// 加载工具调用摘要
async function loadToolCallsSummary() {
  if (!currentLog.value) return
  toolCallsLoading.value = true
  try {
    toolCallsSummary.value = await getToolCallsSummary(currentLog.value.id)
  } catch (error: any) {
    message.error(error.message || '加载工具调用失败')
  } finally {
    toolCallsLoading.value = false
  }
}

// 加载工具调用详情
async function loadToolCallDetail(callId: number) {
  toolCallDetailLoading.value = true
  toolCallDetailDrawer.value = true
  try {
    selectedToolCall.value = await getToolCallDetail(callId)
  } catch (error: any) {
    message.error(error.message || '加载详情失败')
  } finally {
    toolCallDetailLoading.value = false
  }
}

// 加载瀑布图数据
async function loadWaterfallData() {
  if (!currentLog.value) return
  waterfallLoading.value = true
  try {
    waterfallData.value = await getWaterfallData(currentLog.value.id)
  } catch (error: any) {
    message.error(error.message || '加载瀑布图失败')
  } finally {
    waterfallLoading.value = false
  }
}

// Tab 切换时加载数据
function handleTabChange(name: string) {
  if (name === 'tool-calls' && !toolCallsSummary.value) {
    loadToolCallsSummary()
  } else if (name === 'waterfall' && !waterfallData.value) {
    loadWaterfallData()
  }
}

// 计算瀑布图条形宽度百分比
function getWaterfallBarWidth(durationMs: number, totalMs: number): string {
  if (totalMs === 0) return '0%'
  const width = Math.max((durationMs / totalMs) * 100, 0.5) // 最小0.5%
  return `${Math.min(width, 100)}%`
}

// 计算瀑布图条形偏移百分比
function getWaterfallBarOffset(startOffsetMs: number, totalMs: number): string {
  if (totalMs === 0) return '0%'
  const offset = (startOffsetMs / totalMs) * 100
  return `${Math.min(offset, 100)}%`
}

// 格式化输入参数为简洁格式
function formatInputParams(text: string): string {
  if (!text) return '-'
  try {
    // 尝试解析 JSON
    const obj = typeof text === 'string' && text.startsWith('{') ? JSON.parse(text) : null
    if (obj) {
      // 只显示关键字段
      const parts: string[] = []
      if (obj.query) parts.push(`"${obj.query}"`)
      if (obj.source) parts.push(`来源: ${obj.source}`)
      if (obj.limit) parts.push(`限制: ${obj.limit}`)
      if (obj.year_from) parts.push(`年份: ${obj.year_from}+`)
      if (obj.path) parts.push(`文件: ${obj.path}`)
      if (obj.content) parts.push(`内容: ${obj.content.substring(0, 30)}...`)
      return parts.length > 0 ? parts.join(' | ') : text.substring(0, 80)
    }
  } catch {
    // 忽略解析错误
  }
  // 清理文本
  let cleaned = text.replace(/[{}"]/g, '').replace(/,/g, ' | ').trim()
  return cleaned.length > 80 ? cleaned.substring(0, 80) + '...' : cleaned
}

// 格式化输出预览
function formatOutputPreview(text: string): string {
  if (!text) return '无输出'
  // 清理转义字符
  let cleaned = text.replace(/\\n/g, ' ').replace(/\\r/g, '').replace(/\\t/g, ' ')
  // 移除多余空格
  cleaned = cleaned.replace(/\s+/g, ' ').trim()
  // 如果是返回结果数量的格式，保留
  if (cleaned.startsWith('返回')) return cleaned
  // 截断长文本 - 显示更多内容
  return cleaned.length > 150 ? cleaned.substring(0, 150) + '...' : cleaned
}

// 格式化工具数据为可读格式
function formatToolData(data: any): string {
  if (!data) return '-'
  if (typeof data === 'string') {
    // 已经是字符串，尝试解析为 JSON 再格式化
    try {
      const parsed = JSON.parse(data)
      return JSON.stringify(parsed, null, 2)
    } catch {
      // 不是有效 JSON，直接返回
      return data
    }
  }
  return JSON.stringify(data, null, 2)
}

// 判断是否为纯文本输出（不是 JSON 对象/数组）
function isPlainTextOutput(data: any): boolean {
  if (!data) return false
  // 如果是字符串
  if (typeof data === 'string') {
    const trimmed = data.trim()
    // 如果以 { 或 [ 开头，尝试解析为 JSON
    if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
      try {
        JSON.parse(trimmed)
        return false // 是有效的 JSON 对象/数组
      } catch {
        return true // 解析失败，当作纯文本
      }
    }
    // 其他字符串都当作纯文本
    return true
  }
  // 如果已经是对象/数组，不是纯文本
  if (typeof data === 'object') {
    return false
  }
  return true
}

// 格式化工具输出文本，处理换行符
function formatToolOutputText(data: any): string {
  if (!data) return ''
  let text = typeof data === 'string' ? data : String(data)

  // 移除首尾引号（如果有）- 数据库存的可能是 JSON 字符串格式
  if (text.startsWith('"') && text.endsWith('"')) {
    text = text.slice(1, -1)
  }

  // 将转义的换行符 \\n 转换为实际换行 \n
  // 注意：数据库中存的是 \\n（两个字符），不是 \n
  text = text.replace(/\\n/g, '\n')
  text = text.replace(/\\r/g, '')
  text = text.replace(/\\t/g, '\t')

  // 移除 HTML 的 <br> 标签，转为换行
  text = text.replace(/<br\s*\/?>/gi, '\n')

  // 为每个结果项添加额外的换行，使其更易读
  // 匹配 【数字】 或 📄 [ref_xxx] 格式的标记，在前面加换行
  text = text.replace(/\n?(【\d+】)/g, '\n\n$1')
  text = text.replace(/\n?(📄\s*\[ref_\d+\])/g, '\n\n$1')

  // 去掉开头多余的换行
  text = text.replace(/^\n+/, '')

  return text
}

async function handleDelete(log: SkillLog) {
  try {
    await deleteLog(log.id)
    message.success('删除成功')
    await loadData()
  } catch (error: any) {
    message.error(error.message || '删除失败')
  }
}

async function handleBatchDelete() {
  deleteLoading.value = true
  try {
    const params: any = {}
    if (filters.skill_name) params.skill_name = filters.skill_name
    if (filters.date_range) {
      params.before_date = new Date(filters.date_range[0]).toISOString().split('T')[0]
    }

    const res = await batchDeleteLogs(params)
    message.success(`已删除 ${res.deleted_count} 条日志`)
    await loadData()
  } catch (error: any) {
    message.error(error.message || '批量删除失败')
  } finally {
    deleteLoading.value = false
  }
}

function handlePageChange(page: number) {
  currentPage.value = page
  loadData()
}

function handlePageSizeChange(size: number) {
  pageSize.value = size
  currentPage.value = 1
  loadData()
}

function handleSearch() {
  currentPage.value = 1
  loadData()
}

function handleReset() {
  filters.skill_name = ''
  filters.status = ''
  filters.date_range = null
  currentPage.value = 1
  loadData()
}

// ==================== 生命周期 ====================

onMounted(() => {
  loadData()
  loadSkillOptions()
})
</script>

<template>
  <div class="execution-logs">
    <!-- 统计卡片 -->
    <NGrid :cols="5" :x-gap="16" :y-gap="16" class="stats-grid">
      <NGridItem>
        <NCard size="small">
          <NStatistic label="总执行次数" :value="stats?.total_executions || 0">
            <template #prefix>
              <NIcon :component="DocumentTextOutline" />
            </template>
          </NStatistic>
        </NCard>
      </NGridItem>
      <NGridItem>
        <NCard size="small">
          <NStatistic label="成功次数" :value="stats?.success_count || 0">
            <template #prefix>
              <NIcon :component="CheckmarkCircleOutline" color="#18a058" />
            </template>
          </NStatistic>
        </NCard>
      </NGridItem>
      <NGridItem>
        <NCard size="small">
          <NStatistic label="失败次数" :value="stats?.failure_count || 0">
            <template #prefix>
              <NIcon :component="CloseCircleOutline" color="#d03050" />
            </template>
          </NStatistic>
        </NCard>
      </NGridItem>
      <NGridItem>
        <NCard size="small">
          <NStatistic label="成功率" :value="successRate">
            <template #prefix>
              <NIcon :component="SpeedometerOutline" />
            </template>
          </NStatistic>
        </NCard>
      </NGridItem>
      <NGridItem>
        <NCard size="small">
          <NStatistic
            label="平均耗时"
            :value="formatDuration(stats?.avg_duration_ms || 0)"
          >
            <template #prefix>
              <NIcon :component="TimeOutline" />
            </template>
          </NStatistic>
        </NCard>
      </NGridItem>
    </NGrid>

    <!-- 每日趋势图 -->
    <NCard title="近7天执行趋势" size="small" class="chart-card" v-if="dailyStats.length > 0">
      <VChart :option="chartOption" style="height: 300px" autoresize />
    </NCard>

    <!-- 筛选操作栏 -->
    <NCard size="small" class="filter-card">
      <NSpace justify="space-between" align="center" wrap>
        <NSpace>
          <NSelect
            v-model:value="filters.skill_name"
            :options="skillOptions"
            placeholder="选择 Skill"
            clearable
            style="width: 150px"
          />
          <NSelect
            v-model:value="filters.status"
            :options="statusOptions"
            placeholder="选择状态"
            clearable
            style="width: 120px"
          />
          <NDatePicker
            v-model:value="filters.date_range"
            type="daterange"
            clearable
            style="width: 260px"
          />
          <NButton type="primary" @click="handleSearch">
            <template #icon>
              <NIcon :component="SearchOutline" />
            </template>
            搜索
          </NButton>
          <NButton @click="handleReset">重置</NButton>
        </NSpace>
        <NSpace>
          <NButton @click="loadData" :loading="loading">
            <template #icon>
              <NIcon :component="RefreshOutline" />
            </template>
            刷新
          </NButton>
          <NPopconfirm @positive-click="handleBatchDelete">
            <template #trigger>
              <NButton type="error" :loading="deleteLoading">
                <template #icon>
                  <NIcon :component="TrashOutline" />
                </template>
                批量删除
              </NButton>
            </template>
            确认批量删除符合筛选条件的日志？
          </NPopconfirm>
        </NSpace>
      </NSpace>
    </NCard>

    <!-- 日志列表 -->
    <NCard size="small" class="table-card">
      <NDataTable
        :columns="columns"
        :data="logs"
        :loading="loading"
        :pagination="false"
        :bordered="false"
        :single-line="false"
        striped
      />

      <div class="pagination-wrapper" v-if="total > 0">
        <NPagination
          v-model:page="currentPage"
          v-model:page-size="pageSize"
          :item-count="total"
          :page-sizes="[10, 20, 50, 100]"
          show-size-picker
          show-quick-jumper
          @update:page="handlePageChange"
          @update:page-size="handlePageSizeChange"
        />
      </div>
    </NCard>

    <!-- 详情抽屉 -->
    <NDrawer
      v-model:show="detailDrawer"
      :width="900"
      placement="right"
    >
      <NDrawerContent title="执行日志详情" closable>
        <NSpin :show="detailLoading">
          <template v-if="currentLog">
            <!-- 统计卡片 -->
            <NGrid :cols="4" :x-gap="12" class="detail-stats">
              <NGridItem>
                <NCard size="small" :bordered="false">
                  <NStatistic label="耗时" :value="formatDuration(currentLog.duration_ms)" />
                </NCard>
              </NGridItem>
              <NGridItem>
                <NCard size="small" :bordered="false">
                  <NStatistic label="Token消耗" :value="formatTokens(currentLog.tokens_used)" />
                </NCard>
              </NGridItem>
              <NGridItem>
                <NCard size="small" :bordered="false">
                  <NStatistic label="工具调用">
                    <template #default>
                      <span>{{ currentLog.tool_calls_count }}次</span>
                      <NTag v-if="currentLog.tool_calls_failed > 0" size="tiny" type="error" style="margin-left: 4px">
                        {{ currentLog.tool_calls_failed }}失败
                      </NTag>
                    </template>
                  </NStatistic>
                </NCard>
              </NGridItem>
              <NGridItem>
                <NCard size="small" :bordered="false">
                  <NStatistic label="状态">
                    <template #default>
                      <NTag :type="LOG_STATUS_TYPE[currentLog.status]">
                        {{ LOG_STATUS_MAP[currentLog.status] }}
                      </NTag>
                    </template>
                  </NStatistic>
                </NCard>
              </NGridItem>
            </NGrid>

            <!-- Tab切换 -->
            <NTabs v-model:value="activeTab" @update:value="handleTabChange" type="line" style="margin-top: 16px">
              <!-- 基本信息 Tab -->
              <NTabPane name="basic" tab="基本信息">
                <NDescriptions
                  :column="2"
                  label-placement="left"
                  bordered
                  :label-style="{ width: '100px', whiteSpace: 'nowrap' }"
                >
                  <NDescriptionsItem label="日志ID">
                    {{ currentLog.id }}
                  </NDescriptionsItem>
                  <NDescriptionsItem label="会话ID">
                    <span class="session-id">{{ currentLog.session_id }}</span>
                  </NDescriptionsItem>
                  <NDescriptionsItem label="Skill">
                    <NTag type="info">{{ currentLog.skill_name }}</NTag>
                  </NDescriptionsItem>
                  <NDescriptionsItem label="用户">
                    {{ currentLog.username || currentLog.user_id }}
                  </NDescriptionsItem>
                  <NDescriptionsItem label="创建时间">
                    {{ new Date(currentLog.created_at).toLocaleString('zh-CN') }}
                  </NDescriptionsItem>
                  <NDescriptionsItem label="完成时间">
                    {{ currentLog.completed_at ? new Date(currentLog.completed_at).toLocaleString('zh-CN') : '-' }}
                  </NDescriptionsItem>
                  <NDescriptionsItem label="输入内容" :span="2">
                    <div class="log-content input">{{ currentLog.input_text }}</div>
                  </NDescriptionsItem>
                  <NDescriptionsItem label="输出摘要" :span="2" v-if="currentLog.output_summary">
                    <div class="log-content output" v-html="renderMarkdown(currentLog.output_summary)"></div>
                  </NDescriptionsItem>
                  <NDescriptionsItem label="错误信息" :span="2" v-if="currentLog.error_message">
                    <div class="log-content error">{{ currentLog.error_message }}</div>
                  </NDescriptionsItem>
                </NDescriptions>
              </NTabPane>

              <!-- 工具调用时间线 Tab -->
              <NTabPane name="tool-calls" tab="工具调用">
                <NSpin :show="toolCallsLoading">
                  <template v-if="toolCallsSummary">
                    <!-- 统计摘要 - 蓝绿色调 -->
                    <div :style="{
                      display: 'flex',
                      gap: '32px',
                      padding: '18px 24px',
                      background: 'linear-gradient(135deg, #0ea5e9 0%, #14b8a6 100%)',
                      borderRadius: '10px',
                      marginBottom: '20px'
                    }">
                      <div :style="{ display: 'flex', flexDirection: 'column', alignItems: 'center' }">
                        <span :style="{ fontSize: '28px', fontWeight: '700', color: '#fff' }">{{ toolCallsSummary.summary.total }}</span>
                        <span :style="{ fontSize: '12px', color: 'rgba(255,255,255,0.85)', marginTop: '2px' }">次调用</span>
                      </div>
                      <div :style="{ display: 'flex', flexDirection: 'column', alignItems: 'center' }">
                        <span :style="{ fontSize: '28px', fontWeight: '700', color: '#bbf7d0' }">{{ toolCallsSummary.summary.success }}</span>
                        <span :style="{ fontSize: '12px', color: 'rgba(255,255,255,0.85)', marginTop: '2px' }">成功</span>
                      </div>
                      <div v-if="toolCallsSummary.summary.failed > 0" :style="{ display: 'flex', flexDirection: 'column', alignItems: 'center' }">
                        <span :style="{ fontSize: '28px', fontWeight: '700', color: '#fecaca' }">{{ toolCallsSummary.summary.failed }}</span>
                        <span :style="{ fontSize: '12px', color: 'rgba(255,255,255,0.85)', marginTop: '2px' }">失败</span>
                      </div>
                      <div :style="{ display: 'flex', flexDirection: 'column', alignItems: 'center' }">
                        <span :style="{ fontSize: '28px', fontWeight: '700', color: '#fff' }">{{ formatDuration(toolCallsSummary.summary.total_duration_ms) }}</span>
                        <span :style="{ fontSize: '12px', color: 'rgba(255,255,255,0.85)', marginTop: '2px' }">总耗时</span>
                      </div>
                    </div>

                    <!-- 工具调用卡片列表 -->
                    <div v-if="toolCallsSummary.calls.length > 0" :style="{ display: 'flex', flexDirection: 'column', gap: '14px' }">
                      <div
                        v-for="call in toolCallsSummary.calls"
                        :key="call.id"
                        :style="{
                          background: call.status === 'failed' ? '#fef2f2' : '#fff',
                          border: call.status === 'failed' ? '1px solid #fca5a5' : '1px solid #e2e8f0',
                          borderRadius: '10px',
                          overflow: 'hidden',
                          boxShadow: '0 1px 3px rgba(0,0,0,0.05)'
                        }"
                      >
                        <!-- 卡片头部 -->
                        <div :style="{
                          display: 'flex',
                          justifyContent: 'space-between',
                          alignItems: 'center',
                          padding: '12px 16px',
                          background: call.status === 'failed' ? '#fef2f2' : '#f8fafc',
                          borderBottom: '1px solid #e2e8f0'
                        }">
                          <div :style="{ display: 'flex', alignItems: 'center', gap: '10px' }">
                            <span :style="{
                              display: 'inline-flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              width: '26px',
                              height: '26px',
                              background: '#0ea5e9',
                              color: '#fff',
                              fontSize: '12px',
                              fontWeight: '600',
                              borderRadius: '6px'
                            }">#{{ call.call_index + 1 }}</span>
                            <span :style="{ fontWeight: '600', color: '#1e293b', fontSize: '14px' }">{{ getToolDisplayName(call.tool_name) }}</span>
                          </div>
                          <div :style="{ display: 'flex', alignItems: 'center', gap: '10px' }">
                            <span :style="{
                              display: 'inline-flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              width: '20px',
                              height: '20px',
                              borderRadius: '50%',
                              fontSize: '11px',
                              fontWeight: '600',
                              background: call.status === 'success' ? '#dcfce7' : call.status === 'failed' ? '#fee2e2' : '#e0f2fe',
                              color: call.status === 'success' ? '#16a34a' : call.status === 'failed' ? '#dc2626' : '#0284c7'
                            }">{{ call.status === 'success' ? '✓' : call.status === 'failed' ? '✗' : '●' }}</span>
                            <span :style="{
                              color: '#64748b',
                              fontSize: '12px',
                              fontFamily: 'Monaco, monospace',
                              background: '#f1f5f9',
                              padding: '3px 8px',
                              borderRadius: '4px'
                            }">{{ formatDuration(call.duration_ms) }}</span>
                          </div>
                        </div>

                        <!-- 卡片内容 -->
                        <div :style="{ padding: '14px 16px' }">
                          <!-- 输入参数 -->
                          <div :style="{ display: 'flex', alignItems: 'flex-start', gap: '8px', marginBottom: '10px' }">
                            <span :style="{ flexShrink: 0, fontSize: '12px', color: '#94a3b8', fontWeight: '500', width: '32px' }">输入</span>
                            <span :style="{ flex: 1, fontSize: '13px', lineHeight: '1.5', color: '#475569', wordBreak: 'break-word' }">{{ formatInputParams(call.input_preview) }}</span>
                          </div>
                          <!-- 输出结果 -->
                          <div :style="{ display: 'flex', alignItems: 'flex-start', gap: '8px' }">
                            <span :style="{ flexShrink: 0, fontSize: '12px', color: '#94a3b8', fontWeight: '500', width: '32px' }">输出</span>
                            <span :style="{ flex: 1, fontSize: '13px', lineHeight: '1.5', color: call.status === 'failed' ? '#dc2626' : '#059669', wordBreak: 'break-word' }">{{ call.error_message || formatOutputPreview(call.output_preview) }}</span>
                          </div>
                        </div>

                        <!-- 卡片底部 -->
                        <div :style="{ padding: '8px 16px', background: '#f8fafc', borderTop: '1px solid #e2e8f0', textAlign: 'right' }">
                          <NButton size="tiny" text @click="loadToolCallDetail(call.id)" :style="{ color: '#0ea5e9' }">
                            查看详情 →
                          </NButton>
                        </div>
                      </div>
                    </div>
                    <NEmpty v-else description="暂无工具调用记录" />
                  </template>
                </NSpin>
              </NTabPane>

              <!-- 瀑布图 Tab -->
              <NTabPane name="waterfall" tab="瀑布图">
                <NSpin :show="waterfallLoading">
                  <template v-if="waterfallData && waterfallData.calls.length > 0">
                    <!-- 表头 -->
                    <div style="display: flex; padding: 8px 0; border-bottom: 1px solid #e8e8e8; font-size: 13px; color: #666;">
                      <div style="width: 120px; flex-shrink: 0;">工具名称</div>
                      <div style="width: 70px; flex-shrink: 0; text-align: right; padding-right: 12px;">耗时</div>
                      <div style="flex: 1; display: flex; justify-content: space-between;">
                        <span>0</span>
                        <span>{{ formatDuration(waterfallData.total_duration_ms / 2) }}</span>
                        <span>{{ formatDuration(waterfallData.total_duration_ms) }}</span>
                      </div>
                    </div>
                    <!-- 数据行 (v2.0: 支持 LLM 思考时间) -->
                    <div
                      v-for="(call, index) in waterfallData.calls"
                      :key="index"
                      role="listitem"
                      :aria-label="`${call.tool_name}, 耗时 ${formatDuration(call.duration_ms)}`"
                      :style="{
                        display: 'flex',
                        alignItems: 'center',
                        padding: '8px 0',
                        borderBottom: '1px solid #f0f0f0',
                        background: call.item_type === 'llm_thinking' ? '#faf5ff' : 'transparent'
                      }"
                    >
                      <!-- 工具名称 -->
                      <div :style="{
                        width: '120px',
                        flexShrink: 0,
                        fontSize: '12px',
                        color: call.item_type === 'llm_thinking' ? '#7c3aed' : '#333',
                        paddingLeft: call.item_type === 'llm_thinking' ? '12px' : '0',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '4px'
                      }">
                        <span v-if="call.item_type === 'llm_thinking'" aria-hidden="true">🧠</span>
                        <NTag
                          v-else
                          size="tiny"
                          :type="call.status === 'success' ? 'success' : call.status === 'failed' ? 'error' : 'default'"
                        >
                          {{ getToolDisplayName(call.tool_name) }}
                        </NTag>
                        <span v-if="call.item_type === 'llm_thinking'">{{ call.tool_name }}</span>
                      </div>
                      <!-- 耗时 -->
                      <div :style="{
                        width: '70px',
                        flexShrink: 0,
                        textAlign: 'right',
                        paddingRight: '12px',
                        fontSize: '12px',
                        color: call.item_type === 'llm_thinking' ? '#a78bfa' : '#666',
                        fontFamily: 'Monaco, Menlo, monospace'
                      }">
                        {{ formatDuration(call.duration_ms) }}
                      </div>
                      <!-- 条形图轨道 -->
                      <div style="flex: 1; position: relative; height: 20px; background: #f0f0f0; border-radius: 3px; overflow: hidden;">
                        <!-- 条形图 -->
                        <NTooltip trigger="hover" :delay="200">
                          <template #trigger>
                            <div
                              role="progressbar"
                              :aria-valuenow="call.duration_ms"
                              :aria-valuemin="0"
                              :aria-valuemax="waterfallData.total_duration_ms"
                              tabindex="0"
                              :style="{
                                position: 'absolute',
                                top: 0,
                                left: getWaterfallBarOffset(call.start_offset_ms, waterfallData.total_duration_ms),
                                width: getWaterfallBarWidth(call.duration_ms, waterfallData.total_duration_ms),
                                height: '100%',
                                borderRadius: '3px',
                                minWidth: '4px',
                                cursor: 'pointer',
                                background: call.item_type === 'llm_thinking'
                                  ? 'linear-gradient(90deg, #c4b5fd 0%, #a78bfa 100%)'
                                  : call.status === 'success' ? '#10b981'
                                  : call.status === 'failed' ? '#ef4444'
                                  : '#6b7280'
                              }"
                            ></div>
                          </template>
                          <!-- Tooltip 内容 -->
                          <div style="max-width: 250px; font-size: 13px; line-height: 1.6;">
                            <div style="font-weight: 600; margin-bottom: 8px;">
                              {{ call.item_type === 'llm_thinking' ? '🧠 LLM 思考时间' : `🔧 ${getToolDisplayName(call.tool_name)}` }}
                            </div>
                            <div style="display: flex; flex-direction: column; gap: 4px;">
                              <div v-if="call.item_type === 'tool_call'">
                                <span style="color: #999;">状态:</span>
                                <span :style="{ marginLeft: '8px', color: call.status === 'success' ? '#10b981' : '#ef4444' }">
                                  {{ call.status === 'success' ? '成功' : '失败' }}
                                </span>
                              </div>
                              <div>
                                <span style="color: #999;">耗时:</span>
                                <span style="margin-left: 8px; font-family: Monaco, monospace;">
                                  {{ formatDuration(call.duration_ms) }}
                                </span>
                              </div>
                              <div>
                                <span style="color: #999;">占比:</span>
                                <span style="margin-left: 8px;">
                                  {{ waterfallData.total_duration_ms > 0 ? ((call.duration_ms / waterfallData.total_duration_ms) * 100).toFixed(1) : 0 }}%
                                </span>
                              </div>
                            </div>
                            <div v-if="call.item_type === 'llm_thinking'" style="margin-top: 8px; padding-top: 8px; border-top: 1px solid rgba(255,255,255,0.2); font-size: 12px; color: #ccc;">
                              包括：分析上下文、规划工具调用、生成回复
                            </div>
                          </div>
                        </NTooltip>
                      </div>
                    </div>

                    <!-- 统计摘要 (v2.0 新增) -->
                    <div :style="{
                      marginTop: '20px',
                      padding: '16px',
                      background: 'linear-gradient(135deg, #f9fafb 0%, #ffffff 100%)',
                      borderRadius: '8px',
                      border: '1px solid #e5e7eb'
                    }">
                      <!-- 统计卡片 -->
                      <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 12px;">
                        <!-- 总耗时 -->
                        <div style="display: flex; align-items: center; gap: 10px; padding: 10px; background: #f3f4f6; border-radius: 6px;">
                          <span style="font-size: 18px;">⏱️</span>
                          <div>
                            <div style="font-size: 11px; color: #6b7280;">总耗时</div>
                            <div style="font-size: 16px; font-weight: 600; color: #111827;">
                              {{ formatDuration(waterfallData.total_duration_ms) }}
                            </div>
                          </div>
                        </div>
                        <!-- 工具执行 -->
                        <div style="display: flex; align-items: center; gap: 10px; padding: 10px; background: linear-gradient(135deg, #ecfdf5 0%, #d1fae5 100%); border-radius: 6px;">
                          <span style="font-size: 18px;">🔧</span>
                          <div>
                            <div style="font-size: 11px; color: #6b7280;">工具执行</div>
                            <div style="font-size: 16px; font-weight: 600; color: #111827;">
                              {{ formatDuration(waterfallData.tool_execution_ms) }}
                              <span style="font-size: 12px; color: #9ca3af; margin-left: 4px;">
                                ({{ waterfallData.total_duration_ms > 0 ? ((waterfallData.tool_execution_ms / waterfallData.total_duration_ms) * 100).toFixed(1) : 0 }}%)
                              </span>
                            </div>
                          </div>
                        </div>
                        <!-- LLM 思考 -->
                        <div style="display: flex; align-items: center; gap: 10px; padding: 10px; background: linear-gradient(135deg, #faf5ff 0%, #ede9fe 100%); border-radius: 6px;">
                          <span style="font-size: 18px;">🧠</span>
                          <div>
                            <div style="font-size: 11px; color: #6b7280;">LLM 思考</div>
                            <div style="font-size: 16px; font-weight: 600; color: #111827;">
                              {{ formatDuration(waterfallData.llm_thinking_ms) }}
                              <span style="font-size: 12px; color: #9ca3af; margin-left: 4px;">
                                ({{ waterfallData.total_duration_ms > 0 ? ((waterfallData.llm_thinking_ms / waterfallData.total_duration_ms) * 100).toFixed(1) : 0 }}%)
                              </span>
                            </div>
                          </div>
                        </div>
                      </div>
                      <!-- 折叠说明 -->
                      <details style="margin-top: 8px;">
                        <summary style="cursor: pointer; font-size: 12px; color: #6b7280; padding: 6px 10px; border-radius: 4px; user-select: none;">
                          什么是 LLM 思考时间？
                        </summary>
                        <ul style="margin: 10px 0 0 20px; padding: 10px; background: white; border-radius: 4px; border-left: 3px solid #8b5cf6; font-size: 12px; color: #4b5563; line-height: 1.8;">
                          <li>📖 分析用户输入和对话上下文</li>
                          <li>🎯 规划需要调用的工具序列</li>
                          <li>🔄 处理工具返回结果并决策下一步</li>
                          <li>✍️ 生成最终回复内容</li>
                        </ul>
                      </details>
                    </div>
                  </template>
                  <NEmpty v-else-if="!waterfallLoading" description="暂无瀑布图数据" />
                </NSpin>
              </NTabPane>
            </NTabs>

            <!-- 工具调用详情弹窗 -->
            <NDrawer
              v-model:show="toolCallDetailDrawer"
              :width="600"
              placement="right"
            >
              <NDrawerContent
                v-if="selectedToolCall"
                :title="`工具调用详情 - ${getToolDisplayName(selectedToolCall.tool_name)}`"
                closable
                :native-scrollbar="false"
                :body-content-style="{ paddingBottom: '24px' }"
              >
                <NSpin :show="toolCallDetailLoading">
                  <NDescriptions :column="2" label-placement="left" bordered size="small">
                    <NDescriptionsItem label="调用序号">
                      #{{ selectedToolCall.call_index + 1 }}
                    </NDescriptionsItem>
                    <NDescriptionsItem label="状态">
                      <NTag :type="TOOL_CALL_STATUS_TYPE[selectedToolCall.status]">
                        {{ TOOL_CALL_STATUS_MAP[selectedToolCall.status] }}
                      </NTag>
                    </NDescriptionsItem>
                    <NDescriptionsItem label="耗时">
                      {{ formatDuration(selectedToolCall.duration_ms) }}
                    </NDescriptionsItem>
                    <NDescriptionsItem label="Token">
                      {{ formatTokens(selectedToolCall.tokens_used) }}
                    </NDescriptionsItem>
                  </NDescriptions>

                  <NCollapse style="margin-top: 16px" :default-expanded-names="defaultExpandedNames">
                    <NCollapseItem title="输入参数" name="input">
                      <div
                        :style="{
                          padding: '12px',
                          background: '#f5f5f5',
                          borderRadius: '6px',
                          fontSize: '13px',
                          lineHeight: '1.6',
                          whiteSpace: 'pre-wrap',
                          wordBreak: 'break-word',
                          color: '#333',
                          fontFamily: 'Monaco, Menlo, monospace',
                          border: '1px solid #e8e8e8'
                        }"
                      >{{ formatToolData(selectedToolCall.tool_input) }}</div>
                    </NCollapseItem>
                    <NCollapseItem title="输出结果" name="output">
                      <NTag v-if="selectedToolCall.output_truncated" type="warning" size="small" style="margin-bottom: 8px">
                        输出已截断
                      </NTag>
                      <div
                        v-if="isPlainTextOutput(selectedToolCall.tool_output)"
                        :style="{
                          maxHeight: '60vh',
                          overflowY: 'auto',
                          padding: '16px',
                          background: '#f5f5f5',
                          borderRadius: '6px',
                          fontSize: '13px',
                          lineHeight: '1.8',
                          whiteSpace: 'pre-wrap',
                          wordBreak: 'break-word',
                          color: '#333',
                          fontFamily: 'Monaco, Menlo, monospace',
                          border: '1px solid #e8e8e8'
                        }"
                      >{{ formatToolOutputText(selectedToolCall.tool_output) }}</div>
                      <div
                        v-else
                        :style="{
                          maxHeight: '60vh',
                          overflowY: 'auto',
                          padding: '12px',
                          background: '#f5f5f5',
                          borderRadius: '6px',
                          fontSize: '13px',
                          lineHeight: '1.6',
                          whiteSpace: 'pre-wrap',
                          wordBreak: 'break-word',
                          color: '#333',
                          fontFamily: 'Monaco, Menlo, monospace',
                          border: '1px solid #e8e8e8'
                        }"
                      >{{ formatToolData(selectedToolCall.tool_output) }}</div>
                    </NCollapseItem>
                    <NCollapseItem v-if="selectedToolCall.error_message" title="错误信息" name="error">
                      <div class="log-content error">{{ selectedToolCall.error_message }}</div>
                    </NCollapseItem>
                  </NCollapse>
                </NSpin>
              </NDrawerContent>
            </NDrawer>
          </template>
        </NSpin>
      </NDrawerContent>
    </NDrawer>
  </div>
</template>

<style scoped lang="scss">
.execution-logs {
  .stats-grid {
    margin-bottom: 16px;
  }

  .chart-card {
    margin-bottom: 16px;
  }

  .filter-card {
    margin-bottom: 16px;
  }

  .table-card {
    .pagination-wrapper {
      display: flex;
      justify-content: flex-end;
      margin-top: 16px;
      padding-top: 16px;
      border-top: 1px solid #e8e8e8;
    }
  }

  .session-id {
    font-family: 'Monaco', 'Menlo', monospace;
    font-size: 12px;
    color: #666;
  }

  .log-content {
    max-height: 500px;
    overflow-x: hidden;
    overflow-y: auto;
    font-size: 13px;
    line-height: 1.6;
    padding: 12px;
    border-radius: 6px;

    &.input {
      background: #f0f9ff;
      color: #0369a1;
      white-space: pre-wrap;
      word-break: break-all;
    }

    &.output {
      background: #f0fdf4;
      color: #166534;

      :deep(h2) {
        font-size: 15px;
        margin: 12px 0 8px;
        color: #166534;
      }

      :deep(h3) {
        font-size: 14px;
        margin: 10px 0 6px;
        color: #15803d;
      }

      :deep(p) {
        margin: 6px 0;
      }

      :deep(ul), :deep(ol) {
        margin: 6px 0;
        padding-left: 0;
        margin-left: 20px;
        list-style-position: inside;
      }

      :deep(li) {
        margin: 4px 0;
        padding-left: 0;
        text-indent: 0;
      }

      :deep(a) {
        color: #2563eb;
        text-decoration: none;
        &:hover {
          text-decoration: underline;
        }
      }

      :deep(strong) {
        color: #166534;
      }
    }

    &.error {
      background: #fef2f2;
      color: #dc2626;
      white-space: pre-wrap;
      word-break: break-all;
    }
  }

  // 详情抽屉样式
  .detail-stats {
    margin-bottom: 8px;

    :deep(.n-card) {
      background: #f5f5f5;
    }
  }

  // 工具调用摘要
  .tool-calls-summary {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 12px 16px;
    background: #f8f9fa;
    border-radius: 8px;
    margin-bottom: 16px;
    font-size: 14px;

    .total-duration {
      margin-left: auto;
      color: #666;
      font-size: 13px;
    }
  }

  // 工具调用卡片列表
  .tool-call-list {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .tool-call-card {
    border: 1px solid #e8e8e8;
    border-radius: 8px;
    overflow: hidden;
    transition: box-shadow 0.2s;

    &:hover {
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
    }

    &.is-failed {
      border-color: #ffccc7;
      background: #fff2f0;
    }

    .card-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 10px 14px;
      background: #fafafa;
      border-bottom: 1px solid #f0f0f0;

      .header-left {
        display: flex;
        align-items: center;
        gap: 8px;

        .call-index {
          color: #999;
          font-size: 12px;
          font-weight: 500;
        }

        .tool-name {
          font-weight: 600;
          color: #333;
          font-size: 14px;
        }
      }

      .header-right {
        display: flex;
        align-items: center;
        gap: 10px;

        .duration {
          color: #666;
          font-size: 12px;
          font-family: 'Monaco', 'Menlo', monospace;
        }
      }
    }

    .card-body {
      padding: 12px 14px;
      font-size: 13px;

      .param-line,
      .result-line,
      .error-line {
        display: flex;
        margin-bottom: 8px;
        line-height: 1.6;

        &:last-child {
          margin-bottom: 0;
        }
      }

      .param-label,
      .result-label,
      .error-label {
        width: 40px;
        flex-shrink: 0;
        color: #999;
        font-size: 12px;
      }

      .param-value {
        color: #0066cc;
        font-family: 'Monaco', 'Menlo', monospace;
        font-size: 12px;
      }

      .result-value {
        color: #333;
        word-break: break-word;
      }

      .error-value {
        color: #cf1322;
      }
    }

    .card-footer {
      padding: 8px 14px;
      border-top: 1px dashed #e8e8e8;
      background: #fafafa;
    }
  }

  // ==================== V2 工具调用样式 ====================

  // 统计摘要 V2
  .tool-calls-summary-v2 {
    display: flex;
    gap: 24px;
    padding: 16px 20px;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    border-radius: 12px;
    margin-bottom: 20px;

    .summary-item {
      display: flex;
      flex-direction: column;
      align-items: center;

      .summary-value {
        font-size: 24px;
        font-weight: 700;
        color: #fff;
        line-height: 1.2;
      }

      .summary-label {
        font-size: 12px;
        color: rgba(255, 255, 255, 0.8);
        margin-top: 4px;
      }

      &.success .summary-value {
        color: #a7f3d0;
      }

      &.failed .summary-value {
        color: #fecaca;
      }
    }
  }

  // 工具调用卡片列表 V2
  .tool-call-list-v2 {
    display: flex;
    flex-direction: column;
    gap: 16px;
  }

  .tool-call-card-v2 {
    background: #fff;
    border: 1px solid #e5e7eb;
    border-radius: 12px;
    overflow: hidden;
    transition: all 0.2s ease;

    &:hover {
      border-color: #c7d2fe;
      box-shadow: 0 4px 12px rgba(102, 126, 234, 0.15);
    }

    &.is-failed {
      border-color: #fecaca;
      background: linear-gradient(to bottom, #fef2f2, #fff);

      .card-header-v2 {
        background: #fef2f2;
      }
    }

    .card-header-v2 {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 14px 18px;
      background: #f8fafc;
      border-bottom: 1px solid #e5e7eb;

      .header-left-v2 {
        display: flex;
        align-items: center;
        gap: 12px;

        .call-index-v2 {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          width: 28px;
          height: 28px;
          background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
          color: #fff;
          font-size: 12px;
          font-weight: 600;
          border-radius: 8px;
        }

        .tool-name-v2 {
          font-weight: 600;
          color: #1f2937;
          font-size: 15px;
        }
      }

      .header-right-v2 {
        display: flex;
        align-items: center;
        gap: 12px;

        .status-badge-v2 {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          width: 22px;
          height: 22px;
          border-radius: 50%;
          font-size: 12px;
          font-weight: 600;

          &.success {
            background: #d1fae5;
            color: #059669;
          }

          &.failed {
            background: #fee2e2;
            color: #dc2626;
          }

          &.running, &.pending {
            background: #e0e7ff;
            color: #4f46e5;
          }
        }

        .duration-v2 {
          color: #6b7280;
          font-size: 13px;
          font-family: 'SF Mono', Monaco, monospace;
          background: #f3f4f6;
          padding: 4px 10px;
          border-radius: 6px;
        }
      }
    }

    .card-body-v2 {
      padding: 16px 18px;

      .info-row-v2 {
        display: flex;
        align-items: flex-start;
        gap: 10px;
        padding: 8px 0;

        &:not(:last-child) {
          border-bottom: 1px dashed #e5e7eb;
        }

        .info-icon-v2 {
          flex-shrink: 0;
          font-size: 14px;
          line-height: 1.6;
        }

        .info-content-v2 {
          flex: 1;
          font-size: 13px;
          line-height: 1.6;
          color: #374151;
          word-break: break-word;
        }

        &.result .info-content-v2 {
          color: #059669;
        }

        &.error .info-content-v2 {
          color: #dc2626;
        }
      }
    }

    .card-footer-v2 {
      padding: 10px 18px;
      background: #f8fafc;
      border-top: 1px solid #e5e7eb;
      text-align: right;
    }
  }

  // 工具输出文本样式
  .tool-output-text {
    max-height: 500px;
    overflow-y: auto;
    padding: 16px;
    background: #f8f9fa;
    border-radius: 6px;
    font-size: 13px;
    line-height: 1.8;
    white-space: pre-wrap;
    word-break: break-word;
    color: #333;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;

    // 为搜索结果添加分隔
    &::v-deep {
      .result-item {
        padding: 12px 0;
        border-bottom: 1px dashed #e8e8e8;
      }
    }
  }
}
</style>
