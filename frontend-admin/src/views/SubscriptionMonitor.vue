<script setup lang="ts">
/**
 * 订阅监控页面 — 管理后台
 *
 * 异常导向监控：概览卡片 + 订阅列表 + Webhook 渠道
 */
import { ref, computed, onMounted, h } from 'vue'
import {
  NGrid,
  NGi,
  NCard,
  NStatistic,
  NTabs,
  NTabPane,
  NDataTable,
  NTag,
  NSpace,
  NSelect,
  NInput,
  NButton,
  NPagination,
  NEmpty,
  NSpin,
  NPopconfirm,
  NIcon,
  useMessage,
  type DataTableColumns,
} from 'naive-ui'
import { RefreshOutline } from '@vicons/ionicons5'
import {
  getOverview,
  getSubscriptions,
  getWebhooks,
  disableWebhook,
  toggleSubscription,
  getRSSHubRoutes,
  probeRSSHubRoute,
  saveProbeResults,
  type OverviewData,
  type SubscriptionItem,
  type WebhookItem,
  type RSSHubRoute,
  type RSSHubProbeResult,
} from '../api/subscription-monitor'

const message = useMessage()

// ==================== 状态 ====================

const loading = ref(true)
const overview = ref<OverviewData | null>(null)
const activeTab = ref('subscriptions')

// 订阅列表
const subLoading = ref(false)
const subItems = ref<SubscriptionItem[]>([])
const subTotal = ref(0)
const subPage = ref(1)
const subPageSize = ref(20)
const subFilters = ref({
  category: null as string | null,
  is_enabled: null as boolean | null,
  search: '',
  user_id: '',
})

// Webhook 列表
const whLoading = ref(false)
const whItems = ref<WebhookItem[]>([])
const whTotal = ref(0)
const whPage = ref(1)
const whPageSize = ref(20)
const whFilters = ref({
  channel_type: null as string | null,
  status: null as string | null,
  search: '',
  user_id: '',
})

// RSSHub 状态
interface RSSHubRouteRow {
  path: string
  label: string
  category: string
  probeStatus: 'idle' | 'probing' | 'done'  // 未检测 | 检测中 | 已完成
  status?: number       // HTTP 状态码（0=异常）
  available?: boolean
  error?: string
}
const rsshubConfigured = ref(false)
const rsshubBaseUrl = ref('')
const rsshubHasKey = ref(false)
const rsshubRoutes = ref<RSSHubRouteRow[]>([])
const rsshubRoutesLoaded = ref(false)
const rsshubProbing = ref(false)  // 是否正在检测中
const rsshubProbeProgress = ref({ done: 0, total: 0 })
const rsshubProbedAt = ref<string | null>(null)  // 上次检测时间 (ISO)

// ==================== 加载数据 ====================

/** 加载路由列表 + 恢复缓存的探测结果 */
async function loadRSSHubRoutes() {
  try {
    const data = await getRSSHubRoutes()
    rsshubConfigured.value = data.configured
    rsshubBaseUrl.value = data.base_url
    rsshubHasKey.value = data.has_access_key

    let cachedCount = 0
    rsshubRoutes.value = data.routes.map(r => {
      if (r.probe) {
        cachedCount++
        return {
          path: r.path, label: r.label, category: r.category,
          probeStatus: 'done' as const,
          status: r.probe.status,
          available: r.probe.available,
          error: r.probe.error,
        }
      }
      return { path: r.path, label: r.label, category: r.category, probeStatus: 'idle' as const }
    })

    // 有缓存时更新进度计数（让 UI 显示摘要）
    if (cachedCount > 0) {
      rsshubProbeProgress.value = { done: cachedCount, total: data.routes.length }
      rsshubProbedAt.value = data.probed_at
    }

    rsshubRoutesLoaded.value = true
  } catch (e) {
    console.error('加载RSSHub路由列表失败:', e)
  }
}

/** 逐项探测所有路由（3 并发） */
async function startProbing() {
  if (rsshubProbing.value) return

  // 重置所有行状态
  rsshubRoutes.value.forEach(r => {
    r.probeStatus = 'idle'
    r.status = undefined
    r.available = undefined
    r.error = undefined
  })

  rsshubProbing.value = true
  rsshubProbeProgress.value = { done: 0, total: rsshubRoutes.value.length }

  const queue = [...rsshubRoutes.value.map((_, i) => i)]  // 索引队列
  const concurrency = 5

  await new Promise<void>(resolve => {
    let running = 0
    let finished = 0

    function runNext() {
      while (running < concurrency && queue.length > 0) {
        const idx = queue.shift()!
        const route = rsshubRoutes.value[idx]
        route.probeStatus = 'probing'
        running++

        probeRSSHubRoute(route.path).then(result => {
          route.status = result.status
          route.available = result.available
          route.error = result.error
          route.probeStatus = 'done'
        }).catch(() => {
          route.status = 0
          route.available = false
          route.error = '请求失败'
          route.probeStatus = 'done'
        }).finally(() => {
          running--
          finished++
          rsshubProbeProgress.value.done = finished
          if (finished === rsshubRoutes.value.length) {
            resolve()
          } else {
            runNext()
          }
        })
      }
    }

    runNext()
  })

  // 收集探测结果并持久化到数据库
  const collectedResults: Record<string, { path: string; status: number; available: boolean; error?: string }> = {}
  for (const route of rsshubRoutes.value) {
    if (route.probeStatus === 'done') {
      collectedResults[route.path] = {
        path: route.path,
        status: route.status ?? 0,
        available: route.available ?? false,
        error: route.error,
      }
    }
  }

  try {
    await saveProbeResults(collectedResults)
  } catch (e) {
    console.error('保存探测结果失败:', e)
  }

  rsshubProbing.value = false
  rsshubProbedAt.value = new Date().toISOString()
}

async function loadOverview() {
  try {
    overview.value = await getOverview()
  } catch (e) {
    console.error('加载概览失败:', e)
  }
}

async function loadSubscriptions() {
  subLoading.value = true
  try {
    const params: Record<string, any> = {
      page: subPage.value,
      page_size: subPageSize.value,
    }
    if (subFilters.value.category) params.category = subFilters.value.category
    if (subFilters.value.is_enabled !== null) params.is_enabled = subFilters.value.is_enabled
    if (subFilters.value.search) params.search = subFilters.value.search
    if (subFilters.value.user_id) params.user_id = subFilters.value.user_id

    const res = await getSubscriptions(params)
    subItems.value = res.items
    subTotal.value = res.total
  } catch (e) {
    console.error('加载订阅列表失败:', e)
  } finally {
    subLoading.value = false
  }
}

async function loadWebhooks() {
  whLoading.value = true
  try {
    const params: Record<string, any> = {
      page: whPage.value,
      page_size: whPageSize.value,
    }
    if (whFilters.value.channel_type) params.channel_type = whFilters.value.channel_type
    if (whFilters.value.status) params.status = whFilters.value.status
    if (whFilters.value.search) params.search = whFilters.value.search
    if (whFilters.value.user_id) params.user_id = whFilters.value.user_id

    const res = await getWebhooks(params)
    whItems.value = res.items
    whTotal.value = res.total
  } catch (e) {
    console.error('加载Webhook列表失败:', e)
  } finally {
    whLoading.value = false
  }
}

async function loadAll() {
  loading.value = true
  await Promise.all([loadOverview(), loadSubscriptions(), loadWebhooks(), loadRSSHubRoutes()])
  loading.value = false
}

// ==================== 操作 ====================

async function handleToggleSub(id: string, enable: boolean) {
  try {
    await toggleSubscription(id, enable)
    message.success(enable ? '已启用' : '已停用')
    await Promise.all([loadOverview(), loadSubscriptions()])
  } catch (e) {
    message.error('操作失败')
  }
}

async function handleDisableWebhook(id: number) {
  try {
    await disableWebhook(id)
    message.success('已停用')
    await Promise.all([loadOverview(), loadWebhooks()])
  } catch (e) {
    message.error('操作失败')
  }
}

// 概览卡片联动
function jumpToEmptyResults() {
  activeTab.value = 'subscriptions'
  // 清空筛选后重新加载会自动按创建时间排序，空结果行会高亮
  subFilters.value = { category: null, is_enabled: null, search: '', user_id: '' }
  subPage.value = 1
  loadSubscriptions()
}

function jumpToAutoDisabled() {
  activeTab.value = 'webhooks'
  whFilters.value = { channel_type: null, status: 'auto_disabled', search: '', user_id: '' }
  whPage.value = 1
  loadWebhooks()
}

function jumpToFailedPush() {
  activeTab.value = 'webhooks'
  whFilters.value = { channel_type: null, status: 'failed', search: '', user_id: '' }
  whPage.value = 1
  loadWebhooks()
}


// 用户名点击 → 填充筛选
function filterByUser(userId: string) {
  if (activeTab.value === 'subscriptions') {
    subFilters.value.user_id = userId
    subPage.value = 1
    loadSubscriptions()
  } else {
    whFilters.value.user_id = userId
    whPage.value = 1
    loadWebhooks()
  }
}

// ==================== 表格列定义 ====================

const categoryLabel: Record<string, string> = {
  academic: '学术',
  tech: '技术',
  news: '新闻/财经',
  trending: '社区/热点',
  industry: '行业/产品',
  custom: '自定义',
}

const execModeLabel: Record<string, string> = {
  quick: '每日简报',
  deep: '深度报告',
  hybrid: '简报+周报',
}

// cron 表达式转中文
function cronToText(cron?: string): string {
  if (!cron) return '-'
  const parts = cron.trim().split(/\s+/)
  if (parts.length < 5) return cron
  const minute = parts[0]
  const hour = parts[1]
  const dow = parts[4]
  const time = `${hour.padStart(2, '0')}:${minute.padStart(2, '0')}`
  if (dow === '*') return `每天 ${time}`
  if (dow === '1-5') return `工作日 ${time}`
  if (dow === '1') return `每周一 ${time}`
  return `${cron}（${time}）`
}

// 通知渠道转中文
function channelsToText(channels: string[]): string {
  if (!channels || channels.length === 0) return 'Web 站内'
  const labels = channels.map(ch => {
    if (ch === 'web') return 'Web 站内'
    return `Webhook #${ch}`
  })
  return labels.join('、')
}

// 展开行渲染
function renderExpand(row: SubscriptionItem) {
  const items = [
    { label: '更新方式', value: execModeLabel[row.exec_mode || ''] || row.exec_mode || '-' },
    { label: '推送频率', value: cronToText(row.cron_schedule) },
    { label: '通知渠道', value: channelsToText(row.notification_channels) },
    { label: '最近结果摘要', value: row.last_result_summary || '-' },
  ]
  if (row.description) {
    items.unshift({ label: '调研需求', value: row.description })
  }
  return h('div', { style: 'padding: 8px 16px; display: flex; flex-wrap: wrap; gap: 16px 32px;' },
    items.map(item =>
      h('div', { style: 'min-width: 200px;' }, [
        h('span', { style: 'color: #999; font-size: 12px; margin-right: 8px;' }, item.label + '：'),
        h('span', { style: 'font-size: 13px;' }, item.value),
      ])
    )
  )
}

const subColumns = computed<DataTableColumns<SubscriptionItem>>(() => [
  {
    type: 'expand',
    renderExpand: (row) => renderExpand(row),
  },
  {
    title: '用户',
    key: 'username',
    width: 100,
    render: (row) => {
      const name = row.username || row.user_id
      return h(
        'a',
        {
          style: 'color: #2080f0; cursor: pointer; text-decoration: none;',
          onClick: () => filterByUser(row.user_id),
        },
        name,
      )
    },
  },
  {
    title: '名称',
    key: 'name',
    width: 220,
    render: (row) =>
      h('div', {}, [
        h('div', { style: 'font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;' }, row.name),
        row.description
          ? h('div', {
              style: 'font-size: 12px; color: #999; margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;',
              title: row.description,
            }, row.description)
          : null,
      ]),
  },
  {
    title: '分类',
    key: 'category',
    width: 80,
    render: (row) => categoryLabel[row.category || ''] || row.category || '-',
  },
  {
    title: '模式',
    key: 'exec_mode',
    width: 70,
    render: (row) => execModeLabel[row.exec_mode || ''] || row.exec_mode || '-',
  },
  {
    title: '状态',
    key: 'is_enabled',
    width: 80,
    render: (row) =>
      h(NTag, { type: row.is_enabled ? 'success' : 'default', size: 'small' }, {
        default: () => (row.is_enabled ? '启用' : '停用'),
      }),
  },
  {
    title: '最近结果',
    key: 'last_result_item_count',
    width: 120,
    render: (row) => {
      if (row.last_result_item_count === null || row.last_result_item_count === undefined) {
        return h('span', { style: 'color:#999' }, '尚未执行')
      }
      if (row.last_result_item_count === 0) {
        return h(NTag, { type: 'warning', size: 'small' }, { default: () => '空结果' })
      }
      return h(NTag, { type: 'success', size: 'small' }, {
        default: () => `发现 ${row.last_result_item_count} 条`,
      })
    },
  },
  { title: '执行次数', key: 'total_runs', width: 90 },
  {
    title: '操作',
    key: 'actions',
    width: 80,
    render: (row) => {
      if (row.is_enabled) {
        return h(
          NPopconfirm,
          { onPositiveClick: () => handleToggleSub(row.id, false) },
          {
            trigger: () =>
              h(NButton, { size: 'small', type: 'warning', quaternary: true }, { default: () => '停用' }),
            default: () => '确认停用该订阅？',
          },
        )
      }
      return h(
        NButton,
        { size: 'small', type: 'success', quaternary: true, onClick: () => handleToggleSub(row.id, true) },
        { default: () => '启用' },
      )
    },
  },
])

const whColumns = computed<DataTableColumns<WebhookItem>>(() => [
  {
    title: '用户',
    key: 'username',
    width: 100,
    render: (row) => {
      const name = row.username || row.user_id
      return h(
        'a',
        {
          style: 'color: #2080f0; cursor: pointer; text-decoration: none;',
          onClick: () => filterByUser(row.user_id),
        },
        name,
      )
    },
  },
  { title: '名称', key: 'name', ellipsis: { tooltip: true }, width: 150 },
  {
    title: '类型',
    key: 'channel_type',
    width: 80,
    render: (row) => {
      const typeLabels: Record<string, string> = {
        feishu_webhook: '飞书',
        dingtalk_webhook: '钉钉',
        wework_webhook: '企业微信',
        telegram_bot: 'Telegram',
        email: 'Email',
      }
      return typeLabels[row.channel_type] || row.channel_type
    },
  },
  {
    title: '状态',
    key: 'is_active',
    width: 100,
    render: (row) => {
      if (!row.is_active && row.consecutive_failures >= 3) {
        return h(NTag, { type: 'error', size: 'small' }, { default: () => '自动停用' })
      }
      if (!row.is_active) {
        return h(NTag, { type: 'default', size: 'small' }, { default: () => '已停用' })
      }
      if (row.last_push_status === 'failed') {
        return h(NTag, { type: 'warning', size: 'small' }, { default: () => '推送失败' })
      }
      return h(NTag, { type: 'success', size: 'small' }, { default: () => '正常' })
    },
  },
  { title: '连续失败', key: 'consecutive_failures', width: 90 },
  {
    title: '最近推送',
    key: 'last_push_at',
    width: 140,
    render: (row) => {
      if (!row.last_push_at) return h('span', { style: 'color:#999' }, '无记录')
      const date = new Date(row.last_push_at).toLocaleDateString('zh-CN')
      const statusTag = row.last_push_status === 'success'
        ? h(NTag, { type: 'success', size: 'tiny' }, { default: () => '成功' })
        : h(NTag, { type: 'error', size: 'tiny' }, { default: () => '失败' })
      return h(NSpace, { size: 4, align: 'center' }, { default: () => [statusTag, date] })
    },
  },
  { title: '关联订阅', key: 'bound_subscriptions_count', width: 90 },
  {
    title: '操作',
    key: 'actions',
    width: 100,
    render: (row) => {
      if (row.is_active) {
        return h(
          NPopconfirm,
          { onPositiveClick: () => handleDisableWebhook(row.id) },
          {
            trigger: () =>
              h(NButton, { size: 'small', type: 'error', quaternary: true }, { default: () => '强制停用' }),
            default: () => '确认强制停用该 Webhook？',
          },
        )
      }
      return h('span', { style: 'color:#999' }, '已停用')
    },
  },
])

// 异常行高亮
function subRowClassName(row: SubscriptionItem) {
  if (row.last_result_item_count === 0) return 'row-warning'
  return ''
}

function whRowClassName(row: WebhookItem) {
  if (!row.is_active) return 'row-danger'
  if (row.last_push_status === 'failed') return 'row-warning'
  return ''
}

// 筛选选项
const categoryOptions = [
  { label: '全部分类', value: null },
  { label: '学术', value: 'academic' },
  { label: '技术', value: 'tech' },
  { label: '新闻/财经', value: 'news' },
  { label: '社区/热点', value: 'trending' },
  { label: '行业/产品', value: 'industry' },
  { label: '自定义', value: 'custom' },
]

const enabledOptions = [
  { label: '全部状态', value: null },
  { label: '启用中', value: true },
  { label: '已停用', value: false },
]

const channelTypeOptions = [
  { label: '全部类型', value: null },
  { label: '飞书', value: 'feishu_webhook' },
  { label: '钉钉', value: 'dingtalk_webhook' },
  { label: '企业微信', value: 'wework_webhook' },
  { label: 'Telegram', value: 'telegram_bot' },
  { label: 'Email', value: 'email' },
]

const whStatusOptions = [
  { label: '全部状态', value: null },
  { label: '正常', value: 'active' },
  { label: '自动停用', value: 'auto_disabled' },
  { label: '推送失败', value: 'failed' },
]

// RSSHub 分类 tag 颜色
const rsshubCategoryType: Record<string, 'success' | 'info' | 'warning' | 'error' | 'default'> = {
  '学术前沿': 'info',
  '技术/开发': 'success',
  '新闻/财经': 'warning',
  '社区/热点': 'default',
  '行业/产品': 'default',
}

// RSSHub 路由表格列
const rsshubColumns = computed<DataTableColumns<RSSHubRouteRow>>(() => [
  { title: '数据源', key: 'label', width: 150, ellipsis: { tooltip: true } },
  {
    title: '分类',
    key: 'category',
    width: 110,
    render: (row) => h(NTag, {
      size: 'small',
      type: rsshubCategoryType[row.category] || 'default',
      bordered: false,
    }, { default: () => row.category }),
  },
  {
    title: '路由',
    key: 'path',
    width: 280,
    ellipsis: { tooltip: true },
    render: (row) => h('code', { style: 'font-size: 12px; color: #666;' }, row.path),
  },
  {
    title: '状态',
    key: 'probeStatus',
    width: 110,
    align: 'center' as const,
    render: (row) => {
      if (row.probeStatus === 'idle') return h(NTag, { type: 'default', size: 'small', bordered: false }, { default: () => '未检测' })
      if (row.probeStatus === 'probing') return h(NTag, { type: 'info', size: 'small', bordered: false }, { default: () => '检测中…' })
      // done
      if (row.available) return h(NTag, { type: 'success', size: 'small' }, { default: () => '可用' })
      if (row.status === 404) return h(NTag, { type: 'default', size: 'small' }, { default: () => '不存在' })
      if (row.status === 503) return h(NTag, { type: 'warning', size: 'small' }, { default: () => '被阻断' })
      if (row.status === 0) {
        const errMsg = row.error || ''
        const label = errMsg.includes('timed out') || errMsg.includes('Timeout') ? '超时' : '连接失败'
        return h(NTag, { type: 'error', size: 'small' }, { default: () => label })
      }
      return h(NTag, { type: 'error', size: 'small' }, { default: () => `${row.status}` })
    },
  },
])

// ==================== 生命周期 ====================

onMounted(loadAll)
</script>

<template>
  <n-spin :show="loading">
    <!-- 概览卡片 -->
    <n-grid :cols="6" :x-gap="12" :y-gap="12" style="margin-bottom: 16px">
      <n-gi>
        <n-card size="small">
          <n-statistic label="总订阅" :value="overview?.subscriptions?.total ?? 0" />
        </n-card>
      </n-gi>
      <n-gi>
        <n-card size="small">
          <n-statistic label="启用中" :value="overview?.subscriptions?.enabled ?? 0" />
        </n-card>
      </n-gi>
      <n-gi>
        <n-card size="small" :style="(overview?.subscriptions?.empty_result_streak ?? 0) > 0 ? 'border-color: #f0a020' : ''">
          <n-statistic label="空结果订阅" :value="overview?.subscriptions?.empty_result_streak ?? 0">
            <template #suffix>
              <n-button
                v-if="(overview?.subscriptions?.empty_result_streak ?? 0) > 0"
                text
                size="tiny"
                type="warning"
                @click="jumpToEmptyResults"
              >
                查看
              </n-button>
            </template>
          </n-statistic>
        </n-card>
      </n-gi>
      <n-gi>
        <n-card size="small" :style="(overview?.execution?.failures_today ?? 0) > 0 ? 'border-color: #d03050' : ''">
          <n-statistic label="今日推送失败" :value="overview?.execution?.failures_today ?? 0">
            <template #suffix>
              <n-button
                v-if="(overview?.execution?.failures_today ?? 0) > 0"
                text
                size="tiny"
                type="error"
                @click="jumpToFailedPush"
              >
                查看
              </n-button>
            </template>
          </n-statistic>
        </n-card>
      </n-gi>
      <n-gi>
        <n-card size="small">
          <n-statistic label="Webhook" :value="`${overview?.webhooks?.active ?? 0}/${overview?.webhooks?.total ?? 0}`" />
        </n-card>
      </n-gi>
      <n-gi>
        <n-card size="small" :style="(overview?.webhooks?.auto_disabled ?? 0) > 0 ? 'border-color: #d03050' : ''">
          <n-statistic label="自动停用" :value="overview?.webhooks?.auto_disabled ?? 0">
            <template #suffix>
              <n-button
                v-if="(overview?.webhooks?.auto_disabled ?? 0) > 0"
                text
                size="tiny"
                type="error"
                @click="jumpToAutoDisabled"
              >
                查看
              </n-button>
            </template>
          </n-statistic>
        </n-card>
      </n-gi>
    </n-grid>

    <!-- Tab 切换 -->
    <n-card>
      <n-tabs v-model:value="activeTab" type="line">
        <!-- 订阅列表 Tab -->
        <n-tab-pane name="subscriptions" tab="订阅列表">
          <n-space style="margin-bottom: 12px" align="center">
            <n-select
              v-model:value="subFilters.category"
              :options="categoryOptions"
              size="small"
              style="width: 120px"
              placeholder="分类"
              clearable
            />
            <n-select
              v-model:value="subFilters.is_enabled"
              :options="enabledOptions"
              size="small"
              style="width: 120px"
              placeholder="状态"
              clearable
            />
            <n-input
              v-model:value="subFilters.search"
              size="small"
              placeholder="搜索名称/描述"
              clearable
              style="width: 180px"
              @keyup.enter="subPage = 1; loadSubscriptions()"
            />
            <n-input
              v-model:value="subFilters.user_id"
              size="small"
              placeholder="用户ID"
              clearable
              style="width: 120px"
              @keyup.enter="subPage = 1; loadSubscriptions()"
            />
            <n-button size="small" type="primary" @click="subPage = 1; loadSubscriptions()">
              查询
            </n-button>
            <n-button size="small" quaternary @click="loadAll()">
              <template #icon>
                <n-icon :component="RefreshOutline" />
              </template>
            </n-button>
          </n-space>

          <n-data-table
            :columns="subColumns"
            :data="subItems"
            :loading="subLoading"
            :row-class-name="subRowClassName"
            :row-key="(row: SubscriptionItem) => row.id"
            :bordered="false"
            size="small"
            :pagination="false"
          />

          <div v-if="subItems.length === 0 && !subLoading" style="padding: 40px 0">
            <n-empty description="暂无订阅数据" />
          </div>

          <div style="display: flex; justify-content: flex-end; margin-top: 12px" v-if="subTotal > 0">
            <n-pagination
              v-model:page="subPage"
              v-model:page-size="subPageSize"
              :item-count="subTotal"
              :page-sizes="[10, 20, 50]"
              show-size-picker
              @update:page="loadSubscriptions"
              @update:page-size="(size: number) => { subPageSize = size; subPage = 1; loadSubscriptions() }"
            />
          </div>
        </n-tab-pane>

        <!-- Webhook 渠道 Tab -->
        <n-tab-pane name="webhooks" tab="Webhook 渠道">
          <n-space style="margin-bottom: 12px" align="center">
            <n-select
              v-model:value="whFilters.channel_type"
              :options="channelTypeOptions"
              size="small"
              style="width: 120px"
              placeholder="类型"
              clearable
            />
            <n-select
              v-model:value="whFilters.status"
              :options="whStatusOptions"
              size="small"
              style="width: 120px"
              placeholder="状态"
              clearable
            />
            <n-input
              v-model:value="whFilters.search"
              size="small"
              placeholder="搜索名称"
              clearable
              style="width: 180px"
              @keyup.enter="whPage = 1; loadWebhooks()"
            />
            <n-input
              v-model:value="whFilters.user_id"
              size="small"
              placeholder="用户ID"
              clearable
              style="width: 120px"
              @keyup.enter="whPage = 1; loadWebhooks()"
            />
            <n-button size="small" type="primary" @click="whPage = 1; loadWebhooks()">
              查询
            </n-button>
          </n-space>

          <n-data-table
            :columns="whColumns"
            :data="whItems"
            :loading="whLoading"
            :row-class-name="whRowClassName"
            :bordered="false"
            size="small"
            :pagination="false"
          />

          <div v-if="whItems.length === 0 && !whLoading" style="padding: 40px 0">
            <n-empty description="暂无 Webhook 数据" />
          </div>

          <div style="display: flex; justify-content: flex-end; margin-top: 12px" v-if="whTotal > 0">
            <n-pagination
              v-model:page="whPage"
              v-model:page-size="whPageSize"
              :item-count="whTotal"
              :page-sizes="[10, 20, 50]"
              show-size-picker
              @update:page="loadWebhooks"
              @update:page-size="(size: number) => { whPageSize = size; whPage = 1; loadWebhooks() }"
            />
          </div>
        </n-tab-pane>
        <!-- RSSHub 数据源 Tab -->
        <n-tab-pane name="rsshub" tab="RSSHub 数据源">
          <!-- 顶部：配置信息 + 操作 -->
          <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px;">
            <n-space align="center" :size="8">
              <n-tag v-if="rsshubRoutesLoaded" :type="rsshubConfigured ? 'success' : 'error'" size="small">
                {{ rsshubConfigured ? '已配置' : '未配置' }}
              </n-tag>
              <n-tag v-if="rsshubRoutesLoaded && rsshubHasKey" type="success" size="small">KEY 已配置</n-tag>
              <n-tag v-else-if="rsshubRoutesLoaded" type="warning" size="small">KEY 未配置</n-tag>
              <span v-if="rsshubBaseUrl" style="color: #aaa; font-size: 12px;">
                {{ rsshubBaseUrl }}
              </span>
              <span style="color: #bbb;">·</span>
              <span style="color: #999; font-size: 13px;">共 {{ rsshubRoutes.length }} 个路由</span>
            </n-space>
            <n-space align="center" :size="12">
              <!-- 检测完毕摘要 + 上次检测时间 -->
              <template v-if="!rsshubProbing && rsshubProbeProgress.done > 0">
                <span v-if="rsshubProbedAt" style="color: #bbb; font-size: 12px;">
                  上次检测：{{ new Date(rsshubProbedAt).toLocaleString('zh-CN') }}
                </span>
                <n-tag type="success" size="small" :bordered="false">
                  ✓ {{ rsshubRoutes.filter(r => r.available).length }}
                </n-tag>
                <n-tag v-if="rsshubRoutes.filter(r => r.probeStatus === 'done' && !r.available).length > 0" type="error" size="small" :bordered="false">
                  ✗ {{ rsshubRoutes.filter(r => r.probeStatus === 'done' && !r.available).length }}
                </n-tag>
              </template>
              <n-button
                :type="rsshubProbeProgress.done > 0 && !rsshubProbing ? 'default' : 'primary'"
                size="small"
                :loading="rsshubProbing"
                :disabled="!rsshubConfigured"
                @click="startProbing"
              >
                <template #icon><n-icon :component="RefreshOutline" /></template>
                {{ rsshubProbeProgress.done > 0 && !rsshubProbing ? '重新检测' : '开始检测' }}
              </n-button>
            </n-space>
          </div>

          <!-- 进度条 -->
          <div v-if="rsshubProbing" style="margin-bottom: 12px;">
            <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 4px;">
              <span style="font-size: 12px; color: #666;">检测中</span>
              <span style="font-size: 12px; color: #999;">{{ rsshubProbeProgress.done }}/{{ rsshubProbeProgress.total }}</span>
            </div>
            <div style="width: 100%; height: 3px; background: #f0f0f0; border-radius: 2px; overflow: hidden;">
              <div
                :style="{
                  width: (rsshubProbeProgress.total > 0 ? (rsshubProbeProgress.done / rsshubProbeProgress.total * 100) : 0) + '%',
                  height: '100%',
                  background: '#18a058',
                  borderRadius: '2px',
                  transition: 'width 0.3s ease',
                }"
              />
            </div>
          </div>

          <!-- 路由列表 -->
          <n-data-table
            v-if="rsshubRoutesLoaded"
            :columns="rsshubColumns"
            :data="rsshubRoutes"
            :bordered="false"
            size="small"
            :row-key="(row: RSSHubRouteRow) => row.path"
            :row-class-name="(row: RSSHubRouteRow) => row.probeStatus === 'done' && !row.available ? 'row-warning' : ''"
            :max-height="480"
            virtual-scroll
          />

          <!-- 使用说明（折叠式） -->
          <details style="margin-top: 16px; font-size: 13px; color: #888;">
            <summary style="cursor: pointer; user-select: none;">RSSHub 使用说明</summary>
            <div style="line-height: 1.8; padding: 8px 0 0 4px; color: #666;">
              <p>自建的 RSS 聚合服务（diygod/rsshub:2025-01-13），将社交媒体和新闻平台内容 RSS 化，供订阅系统采集。</p>
              <p>状态含义：<strong>可用</strong>=正常 | <strong>不存在</strong>=该 RSSHub 版本未收录此路由 | <strong>被阻断</strong>=目标站反爬 | <strong>超时</strong>=网络不通</p>
              <p>路由不可用时系统自动降级为 web_search 工具，不影响订阅执行。</p>
            </div>
          </details>
        </n-tab-pane>
      </n-tabs>
    </n-card>
  </n-spin>
</template>

<style>
/* 异常行高亮（需要全局样式，不加 scoped） */
.row-warning td {
  background-color: #fff7e6 !important;
}
.row-danger td {
  background-color: #fff1f0 !important;
}
</style>
