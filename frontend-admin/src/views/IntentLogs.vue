<script setup lang="ts">
/**
 * 意图决策日志页面
 *
 * 功能：
 * - 查看意图识别日志列表
 * - 按会话ID/用户名/Skill名称/时间筛选
 * - 查看决策详情（规则链路、候选列表、置信度变化）
 * - 刷新意图配置缓存
 */

import { ref, reactive, computed, onMounted, h } from 'vue'
import {
  NCard,
  NDataTable,
  NButton,
  NSpace,
  NInput,
  NSelect,
  NTag,
  NDrawer,
  NDrawerContent,
  NDescriptions,
  NDescriptionsItem,
  NPagination,
  NSpin,
  NIcon,
  NDatePicker,
  NTimeline,
  NTimelineItem,
  NEmpty,
  NTooltip,
  useMessage,
  type DataTableColumns
} from 'naive-ui'
import { RefreshOutline, SearchOutline } from '@vicons/ionicons5'
import { getIntentLogs, getIntentLogDetail, refreshIntentConfig } from '../api/intent-logs'
import { getSkills } from '../api/super-agent'

const message = useMessage()

// ==================== 响应式数据 ====================

const loading = ref(false)
const logs = ref<any[]>([])
const skillOptions = ref<{ label: string; value: string }[]>([])

// 分页
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(20)

// 筛选条件
const filters = reactive({
  session_id: '',
  user_id: '',
  skill_name: '',
  followup_type: '' as string,
  agent_id: null as number | null,
  date_range: null as [number, number] | null
})

// Follow-up 类型选项
const followupTypeOptions = [
  { label: '全部', value: '' },
  { label: '新任务', value: 'new_task' },
  { label: '二次加工', value: 'refine' },
  { label: '继续对话', value: 'continuation' },
  { label: '确认', value: 'acknowledge' },
]

// Follow-up 类型中文映射
const followupTypeNameMap: Record<string, string> = {
  'new_task': '新任务',
  'refine': '二次加工',
  'continuation': '继续对话',
  'acknowledge': '确认',
}

function formatFollowupType(type: string | null): string {
  if (!type) return '-'
  return followupTypeNameMap[type] || type
}

// Follow-up 类型颜色映射
function getFollowupTypeColor(type: string | null): 'error' | 'info' | 'success' | 'warning' | 'default' {
  if (!type) return 'default'
  const map: Record<string, 'error' | 'info' | 'success' | 'warning'> = {
    'new_task': 'error',
    'refine': 'info',
    'continuation': 'success',
    'acknowledge': 'warning',
  }
  return map[type] || 'default'
}

// 详情抽屉
const detailDrawer = ref(false)
const currentLog = ref<any>(null)
const detailLoading = ref(false)
const showUnmatchedRules = ref(false)

// 刷新配置
const refreshLoading = ref(false)

// ==================== 工具函数 ====================

function getConfidenceType(confidence: number): 'success' | 'info' | 'warning' | 'error' {
  if (confidence >= 0.9) return 'success'
  if (confidence >= 0.7) return 'info'
  if (confidence >= 0.5) return 'warning'
  return 'error'
}

function formatConfidence(val: number): string {
  return (val * 100).toFixed(1) + '%'
}

function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return '-'
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`
}

function formatTime(iso: string | null): string {
  if (!iso) return '-'
  return new Date(iso).toLocaleString('zh-CN')
}

// 匹配来源中文映射
const matchSourceMap: Record<string, string> = {
  'regex_match': '正则匹配',
  'keyword_match': '关键词匹配',
  'llm_router': 'LLM 智能路由',
  'llm_router+rule_agree': 'LLM+规则一致',
  'debate_intercept': '辩论拦截',
  'tech_qa_intercept': '技术问答拦截',
  'negation_intercept': '否定拦截',
  'inquiry_intercept': '询问拦截',
  'sticky_skill': '技能粘性保持',
  'follow_up': '跟进对话',
  'llm_router_excluded': 'LLM路由(排除)',
  'regex': '正则匹配',
  'keyword': '关键词匹配',
  // 编排器阶段产生的 match_source
  'followup_exit': '跟进退出',
  'followup_continuation': '跟进继续',
  'followup_acknowledge': '跟进确认',
  'followup_acknowledge_conv': '跟进确认',
  'followup_refine': '跟进优化',
  'allowed_skills_fallback': '白名单回退',
  'allowed_skills_sticky_fallback': '粘性回退',
  'allowed_skills_guard': '白名单拦截',
  'plugin_override': '插件覆盖',
  'late_promotion': '补录',
  'context_qa': '上下文问答',
}

function formatMatchSource(source: string | null): string {
  if (!source) return '-'
  return matchSourceMap[source] || source
}

// 规则名称中文映射
const ruleNameMap: Record<string, string> = {
  'debate_intercept': '辩论意图检测',
  'tech_qa_intercept': '技术/代码拦截',
  'negation_intercept': '否定词拦截',
  'inquiry_intercept': '询问模式拦截',
  'regex_match': '正则模式匹配',
  'keyword_match': '关键词匹配',
  'boost_modifier': 'Boost 提升',
  'negative_signal': '负面信号降权',
  'default_qa': '默认 QA',
  'llm_router': 'LLM 快速路由'
}

function getRuleName(rule: string): string {
  return ruleNameMap[rule] || rule
}

// 编排器阶段名称映射
const stageNameMap: Record<string, string> = {
  'force_skill': '强制指定技能',
  'guard_point_0': '技能权限检查（强制指定）',
  'sticky_bonus': '技能粘性加权',
  'guard_point_1': '技能权限检查（当前粘性）',
  'followup': '跟进类型检测',
  'guard_point_2': '技能权限检查（最终）',
  'final_dispatch': '最终分发',
  'plugin_override': '插件路由覆盖',
  'context_qa': '上下文问答（二次加工）',
}

// 编排器事件 action 中文映射
const actionNameMap: Record<string, string> = {
  'pass': '通过',
  'fallback': '回退',
  'blocked': '拦截',
  'downgrade': '降级',
  'applied': '已应用',
  'new_task': '新任务',
  'refine': '二次加工',
  'continuation': '继续对话',
  'acknowledge': '确认',
  'skill': '技能',
  'qa': '通用问答',
  'context_qa': '上下文问答',
  'debate': '辩论',
}

function formatStageName(stage: string): string {
  return stageNameMap[stage] || stage
}

function formatRuleDetail(detail: string): string {
  if (!detail) return ''
  // skill=xxx, base_conf=0.72 → 匹配到 Skill「xxx」，基础置信度 72%
  const skillConfMatch = detail.match(/skill=([^,]+),\s*base_conf=([0-9.]+)/)
  if (skillConfMatch) {
    const name = getSkillDisplayName(skillConfMatch[1])
    const conf = (parseFloat(skillConfMatch[2]) * 100).toFixed(0)
    return `匹配到「${name}」，基础置信度 ${conf}%`
  }
  // llm=xxx(conf=1.0) > rule=xxx(conf=0.72) → LLM 判定「xxx」(100%) 置信度高于规则匹配(72%)，采用 LLM 结果
  const llmRuleMatch = detail.match(/llm=([^(]+)\(conf=([0-9.]+)\)\s*>\s*rule=([^(]+)\(conf=([0-9.]+)\)/)
  if (llmRuleMatch) {
    const llmName = getSkillDisplayName(llmRuleMatch[1])
    const llmConf = (parseFloat(llmRuleMatch[2]) * 100).toFixed(0)
    const ruleConf = (parseFloat(llmRuleMatch[4]) * 100).toFixed(0)
    return `LLM 判定「${llmName}」(${llmConf}%) 高于规则匹配(${ruleConf}%)，采用 LLM 结果`
  }
  // llm=xxx(conf=0.8) → LLM 判定「xxx」，置信度 80%
  const llmMatch = detail.match(/llm=([^(]+)\(conf=([0-9.]+)\)/)
  if (llmMatch) {
    const name = getSkillDisplayName(llmMatch[1])
    const conf = (parseFloat(llmMatch[2]) * 100).toFixed(0)
    return `LLM 判定「${name}」，置信度 ${conf}%`
  }
  // best_conf=0.6 → 当前最高置信度 60%，未达阈值
  const bestConfMatch = detail.match(/best_conf=([0-9.]+)/)
  if (bestConfMatch) {
    const conf = (parseFloat(bestConfMatch[1]) * 100).toFixed(0)
    return `当前最高置信度 ${conf}%，未达阈值，转交 LLM 路由`
  }
  return detail
}

function formatConfReason(reason: string): string {
  if (!reason) return ''
  const map: Record<string, string> = {
    'boost(委托词+动词)': '检测到用户指令（如"帮我生成/请制作"），提升置信度',
    'negative_signal': '检测到否定/犹豫信号，降低置信度',
  }
  return map[reason] || reason
}

function getSkillDisplayName(name: string | null): string {
  if (!name) return '-'
  const found = skillOptions.value.find(s => s.value === name)
  return found ? found.label : name
}

// ==================== 表格列定义 ====================

// 表头 tooltip 辅助
function headerWithTip(title: string, tip: string) {
  return () => h(NTooltip, { trigger: 'hover' }, {
    trigger: () => h('span', { class: 'header-with-tip' }, title),
    default: () => tip
  })
}

const columns = computed<DataTableColumns>(() => [
  {
    title: '时间',
    key: 'created_at',
    width: 150,
    render(row: any) {
      return formatTime(row.created_at)
    }
  },
  {
    title: '用户',
    key: 'username',
    width: 80,
    ellipsis: { tooltip: true },
    render(row: any) {
      return row.username || row.user_id || '-'
    }
  },
  {
    title: '用户输入',
    key: 'user_input',
    minWidth: 200,
    ellipsis: { tooltip: true },
    render(row: any) {
      const text = row.user_input || ''
      return text.length > 60 ? text.slice(0, 60) + '...' : text
    }
  },
  {
    title: headerWithTip('路由结果', '系统判定应由哪个技能处理该请求'),
    key: 'final_skill_name',
    width: 130,
    render(row: any) {
      const type = row.final_intent_type || '-'
      const skill = row.final_skill_name
      if ((type === 'skill' || type === 'context_qa') && skill) {
        return h(NTag, { size: 'small', type: type === 'context_qa' ? 'warning' : 'info', bordered: false },
          { default: () => getSkillDisplayName(skill) })
      }
      if (type === 'debate') {
        return h(NTag, { size: 'small', type: 'warning', bordered: false }, { default: () => '辩论' })
      }
      if (type === 'context_qa') {
        return h(NTag, { size: 'small', type: 'warning', bordered: false }, { default: () => '上下文问答' })
      }
      return h(NTag, { size: 'small', bordered: false }, { default: () => type === 'qa' ? '通用问答' : type })
    }
  },
  {
    title: headerWithTip('置信度', '系统对路由判断的确信程度'),
    key: 'final_confidence',
    width: 75,
    render(row: any) {
      const val = row.final_confidence ?? 0
      return h(NTag, {
        type: getConfidenceType(val),
        size: 'small',
        bordered: false
      }, { default: () => formatConfidence(val) })
    }
  },
  {
    title: headerWithTip('决策方式', '系统如何做出路由判断'),
    key: 'match_source',
    width: 110,
    ellipsis: { tooltip: true },
    render(row: any) {
      return formatMatchSource(row.match_source)
    }
  },
  {
    title: headerWithTip('跟进类型', '多轮对话中的跟进判断结果'),
    key: 'followup_type',
    width: 80,
    render(row: any) {
      if (!row.followup_type) return '-'
      return h(NTag, {
        type: getFollowupTypeColor(row.followup_type),
        size: 'small',
        bordered: false,
      }, { default: () => formatFollowupType(row.followup_type) })
    }
  },
  {
    title: '耗时',
    key: 'total_duration_ms',
    width: 70,
    render(row: any) {
      return formatDuration(row.total_duration_ms)
    }
  }
])

// ==================== 数据加载 ====================

async function loadLogs() {
  loading.value = true
  try {
    const params: any = {
      limit: pageSize.value,
      offset: (currentPage.value - 1) * pageSize.value
    }
    if (filters.session_id) params.session_id = filters.session_id
    if (filters.user_id) params.user_id = filters.user_id
    if (filters.skill_name) params.skill_name = filters.skill_name
    if (filters.followup_type) params.followup_type = filters.followup_type
    if (filters.agent_id != null) params.agent_id = filters.agent_id
    if (filters.date_range) {
      params.date_from = new Date(filters.date_range[0]).toISOString()
      params.date_to = new Date(filters.date_range[1]).toISOString()
    }
    const res: any = await getIntentLogs(params)
    logs.value = res.logs || []
    total.value = res.total || 0
  } catch (e: any) {
    message.error(e.message || '加载日志失败')
  } finally {
    loading.value = false
  }
}

async function loadSkillOptions() {
  try {
    const res = await getSkills()
    skillOptions.value = (res.skills || []).map((s: any) => ({
      label: s.display_name || s.name,
      value: s.name
    }))
  } catch {
    // 静默失败
  }
}

async function handleRowClick(row: any) {
  detailDrawer.value = true
  detailLoading.value = true
  showUnmatchedRules.value = false
  currentLog.value = row
  try {
    const detail = await getIntentLogDetail(row.id)
    currentLog.value = detail
  } catch (e: any) {
    message.error(e.message || '加载详情失败')
  } finally {
    detailLoading.value = false
  }
}

async function handleRefreshConfig() {
  refreshLoading.value = true
  try {
    await refreshIntentConfig()
    message.success('意图配置缓存已刷新')
  } catch (e: any) {
    message.error(e.message || '刷新失败')
  } finally {
    refreshLoading.value = false
  }
}

function handleSearch() {
  currentPage.value = 1
  loadLogs()
}

function handleReset() {
  filters.session_id = ''
  filters.user_id = ''
  filters.skill_name = ''
  filters.followup_type = ''
  filters.agent_id = null
  filters.date_range = null
  currentPage.value = 1
  loadLogs()
}

function handlePageChange(page: number) {
  currentPage.value = page
  loadLogs()
}

function formatJson(data: any): string {
  if (!data) return '-'
  try {
    return JSON.stringify(data, null, 2)
  } catch {
    return String(data)
  }
}

function rowProps(row: any) {
  return {
    style: 'cursor: pointer',
    onClick: () => handleRowClick(row)
  }
}

function hasData(arr: any): boolean {
  return Array.isArray(arr) && arr.length > 0
}

onMounted(() => {
  loadLogs()
  loadSkillOptions()
})
</script>

<template>
  <div class="intent-logs">
    <!-- 页面说明 -->
    <n-card size="small" style="margin-bottom: 12px">
      <div class="page-description">
        记录每次用户输入的意图识别过程：系统如何判断用户需求，选择了哪个技能来处理，以及判断依据和置信度。点击任意行查看完整决策链路。
      </div>
    </n-card>

    <!-- 筛选区域 -->
    <n-card size="small" style="margin-bottom: 16px">
      <n-space align="center" :wrap="true" :size="12">
        <n-input
          v-model:value="filters.session_id"
          placeholder="会话ID"
          clearable
          style="width: 200px"
          @keyup.enter="handleSearch"
        />
        <n-input
          v-model:value="filters.user_id"
          placeholder="用户名"
          clearable
          style="width: 160px"
          @keyup.enter="handleSearch"
        />
        <n-select
          v-model:value="filters.skill_name"
          :options="skillOptions"
          placeholder="技能筛选"
          clearable
          style="width: 160px"
        />
        <n-select
          v-model:value="filters.followup_type"
          :options="followupTypeOptions"
          placeholder="跟进类型"
          clearable
          style="width: 120px"
        />
        <n-date-picker
          v-model:value="filters.date_range"
          type="datetimerange"
          clearable
          style="width: 340px"
        />
        <n-button type="primary" @click="handleSearch">
          <template #icon><n-icon><SearchOutline /></n-icon></template>
          搜索
        </n-button>
        <n-button @click="handleReset">重置</n-button>
        <div style="flex: 1" />
        <n-button
          type="warning"
          :loading="refreshLoading"
          @click="handleRefreshConfig"
        >
          <template #icon><n-icon><RefreshOutline /></n-icon></template>
          刷新配置
        </n-button>
      </n-space>
    </n-card>

    <!-- 数据表格 -->
    <n-card>
      <n-spin :show="loading">
        <n-data-table
          :columns="columns"
          :data="logs"
          :row-props="rowProps"
          :bordered="false"
          :single-line="false"
          size="small"
          style="min-height: 400px"
        />
      </n-spin>

      <div style="display: flex; justify-content: flex-end; margin-top: 16px">
        <n-pagination
          v-model:page="currentPage"
          :page-count="Math.ceil(total / pageSize)"
          :page-size="pageSize"
          show-quick-jumper
          @update:page="handlePageChange"
        />
      </div>
    </n-card>

    <!-- 详情抽屉 -->
    <n-drawer v-model:show="detailDrawer" :width="640" placement="right">
      <n-drawer-content title="意图决策详情" closable>
        <n-spin :show="detailLoading">
          <template v-if="currentLog">
            <!-- 基本信息 -->
            <div class="detail-section">
              <div class="section-title">基本信息</div>
              <n-descriptions label-placement="left" bordered :column="2" size="small" label-style="width: 90px; font-weight: 500; color: #666; white-space: nowrap">
                <n-descriptions-item label="时间" :span="2">
                  {{ formatTime(currentLog.created_at) }}
                </n-descriptions-item>
                <n-descriptions-item label="用户">
                  {{ currentLog.username || currentLog.user_id || '-' }}
                </n-descriptions-item>
                <n-descriptions-item label="耗时">
                  {{ formatDuration(currentLog.total_duration_ms) }}
                </n-descriptions-item>
                <n-descriptions-item label="用户输入" :span="2">
                  <div class="user-input-text">{{ currentLog.user_input || '-' }}</div>
                </n-descriptions-item>
              </n-descriptions>
            </div>

            <!-- 路由结果 -->
            <div class="detail-section">
              <div class="section-title">路由结果</div>
              <div class="route-result">
                <div class="route-item">
                  <span class="route-label">意图类型</span>
                  <n-tag size="small" :type="currentLog.final_intent_type === 'skill' ? 'info' : ['debate', 'context_qa'].includes(currentLog.final_intent_type) ? 'warning' : 'default'">
                    {{ {skill: '技能', debate: '辩论', qa: '通用问答', context_qa: '上下文问答'}[currentLog.final_intent_type] || currentLog.final_intent_type || '-' }}
                  </n-tag>
                </div>
                <div v-if="currentLog.final_skill_name" class="route-item">
                  <span class="route-label">目标技能</span>
                  <n-tooltip trigger="hover">
                    <template #trigger>
                      <n-tag size="small" type="info" style="cursor: help">{{ getSkillDisplayName(currentLog.final_skill_name) }}</n-tag>
                    </template>
                    内部标识：{{ currentLog.final_skill_name }}
                  </n-tooltip>
                </div>
                <div class="route-item">
                  <span class="route-label">置信度</span>
                  <n-tag
                    v-if="currentLog.final_confidence != null"
                    :type="getConfidenceType(currentLog.final_confidence)"
                    size="small"
                  >
                    {{ formatConfidence(currentLog.final_confidence) }}
                  </n-tag>
                  <span v-else>-</span>
                </div>
                <div class="route-item">
                  <span class="route-label">匹配来源</span>
                  <span class="route-value">{{ formatMatchSource(currentLog.match_source) }}</span>
                </div>
                <div class="route-item" style="opacity: 0.6">
                  <span class="route-label">会话ID</span>
                  <span class="route-value mono" style="font-size: 11px">{{ currentLog.session_id || '-' }}</span>
                </div>
              </div>
            </div>

            <!-- 完整决策链路（对话上下文 + 规则链路 + 跟进检测 + 编排器决策） -->
            <div class="detail-section">
              <div class="section-title">
                完整决策链路
                <n-tooltip trigger="hover">
                  <template #trigger><span class="tip-icon">?</span></template>
                  从对话上下文到最终分发的完整决策过程。
                </n-tooltip>
              </div>
              <n-timeline>
                <!-- 阶段 0: 对话上下文（嵌入 Timeline 头部） -->
                <n-timeline-item
                  v-if="currentLog?.followup_context?.recent_messages?.length"
                  type="default"
                >
                  <template #header>
                    <div class="rule-step-header">
                      <n-tag size="tiny" :bordered="false" type="default" style="margin-right: 4px">会话上下文</n-tag>
                      <span class="rule-step-name">最近 {{ currentLog.followup_context.recent_messages.filter((m: any) => !m.is_current).length }} 条历史 + 当前输入</span>
                    </div>
                  </template>
                  <div class="context-timeline">
                    <div
                      v-for="(m, idx) in currentLog.followup_context.recent_messages"
                      :key="idx"
                      :class="['ctx-tl-item', m.role === 'user' ? 'ctx-user' : 'ctx-assistant', m.is_current ? 'ctx-current' : '']"
                    >
                      <div class="ctx-tl-rail">
                        <span :class="['ctx-tl-dot', m.is_current ? 'dot-current' : m.role === 'user' ? 'dot-user' : 'dot-ai']" />
                        <span v-if="idx < currentLog.followup_context.recent_messages.length - 1" class="ctx-tl-line" />
                      </div>
                      <div class="ctx-tl-body">
                        <div class="ctx-tl-header">
                          <span class="ctx-role">{{ m.is_current ? '► 当前输入' : (m.role === 'user' ? '用户' : 'AI') }}</span>
                          <n-tag v-if="m.skill" size="tiny" :bordered="false" type="info" class="ctx-skill-tag">{{ m.skill }}</n-tag>
                        </div>
                        <div class="ctx-content">{{ m.content }}</div>
                      </div>
                    </div>
                  </div>
                </n-timeline-item>

                <!-- 阶段 1: 规则执行链路（保持原始顺序，未命中紧凑显示） -->
                <template v-if="hasData(currentLog.rule_executions)">
                  <n-timeline-item
                    v-for="(step, idx) in currentLog.rule_executions"
                    :key="'rule-' + idx"
                    :type="step.matched ? 'success' : 'default'"
                    :class="{ 'compact-item': !step.matched }"
                  >
                    <template #header>
                      <div :class="['rule-step-header', { 'rule-dimmed': !step.matched }]">
                        <n-tag size="tiny" :bordered="false" type="default" style="margin-right: 4px">意图识别</n-tag>
                        <span class="rule-step-name">{{ getRuleName(step.rule) }}</span>
                        <n-tag :type="step.matched ? 'success' : 'default'" size="tiny" :bordered="false">
                          {{ step.matched ? '命中' : '跳过' }}
                        </n-tag>
                      </div>
                    </template>
                    <div v-if="step.detail" class="rule-step-detail">{{ formatRuleDetail(step.detail) }}</div>
                  </n-timeline-item>
                </template>
                <n-timeline-item v-else type="default">
                  <template #header>
                    <div class="rule-step-header">
                      <n-tag size="tiny" :bordered="false" type="default" style="margin-right: 4px">意图识别</n-tag>
                      <span class="rule-step-name" style="color: #bbb">无规则执行记录</span>
                    </div>
                  </template>
                </n-timeline-item>

                <!-- 候选列表（内联） -->
                <n-timeline-item v-if="hasData(currentLog.candidates)" type="info">
                  <template #header>
                    <div class="rule-step-header">
                      <n-tag size="tiny" :bordered="false" type="default" style="margin-right: 4px">意图识别</n-tag>
                      <span class="rule-step-name">候选结果</span>
                    </div>
                  </template>
                  <div class="candidate-list-inline">
                    <span v-for="(c, idx) in currentLog.candidates" :key="idx" class="candidate-inline">
                      <n-tag size="tiny" type="info">{{ getSkillDisplayName(c.skill_name) || c.intent_type || '-' }}</n-tag>
                      <span v-if="c.confidence != null" class="candidate-conf">{{ formatConfidence(c.confidence) }}</span>
                    </span>
                  </div>
                </n-timeline-item>

                <!-- 置信度变化（内联） -->
                <n-timeline-item v-if="hasData(currentLog.confidence_changes)" type="warning">
                  <template #header>
                    <div class="rule-step-header">
                      <n-tag size="tiny" :bordered="false" type="default" style="margin-right: 4px">意图识别</n-tag>
                      <span class="rule-step-name">置信度调整</span>
                    </div>
                  </template>
                  <div class="conf-changes-inline">
                    <div v-for="(ch, idx) in currentLog.confidence_changes" :key="idx" class="conf-inline">
                      <span>{{ getSkillDisplayName(ch.skill) || ch.skill }}</span>
                      <span class="conf-from">{{ ch.from != null ? formatConfidence(ch.from) : '?' }}</span>
                      <span class="conf-arrow">&rarr;</span>
                      <span :style="{ color: (ch.to ?? 0) > (ch.from ?? 0) ? '#18a058' : '#d03050' }">{{ ch.to != null ? formatConfidence(ch.to) : '?' }}</span>
                      <span v-if="ch.reason" class="conf-reason-inline">{{ formatConfReason(ch.reason) }}</span>
                    </div>
                  </div>
                </n-timeline-item>

                <!-- 阶段 2: 编排器决策链路（含跟进检测） -->
                <template v-if="hasData(currentLog?.orchestrator_events)">
                  <n-timeline-item
                    v-for="(evt, idx) in currentLog.orchestrator_events"
                    :key="'orch-' + idx"
                    :type="evt.stage === 'followup' ? getFollowupTypeColor(evt.action) : evt.stage === 'final_dispatch' ? 'success' : evt.action === 'pass' || evt.action === 'skill' ? 'success' : evt.action === 'fallback' || evt.action === 'blocked' ? 'error' : 'info'"
                    :line-type="idx === currentLog.orchestrator_events.length - 1 ? 'dashed' : 'default'"
                  >
                    <template #header>
                      <div class="rule-step-header">
                        <n-tag size="tiny" :bordered="false"
                          :type="evt.stage === 'final_dispatch' ? 'success' : evt.stage === 'followup' ? 'warning' : 'info'"
                          style="margin-right: 4px"
                        >
                          {{ evt.stage === 'final_dispatch' ? '最终结果' : evt.stage === 'followup' ? '跟进判断' : '编排器' }}
                        </n-tag>
                        <span class="rule-step-name">{{ formatStageName(evt.stage) }}</span>
                        <n-tag
                          :type="evt.stage === 'followup' ? getFollowupTypeColor(evt.action) : evt.action === 'pass' || evt.action === 'skill' ? 'success' : evt.action === 'fallback' || evt.action === 'blocked' ? 'error' : 'info'"
                          size="tiny"
                          :bordered="false"
                        >{{ evt.stage === 'followup' ? (formatFollowupType(evt.action) || evt.action) : (actionNameMap[evt.action] || evt.action) }}</n-tag>
                      </div>
                    </template>
                    <!-- followup 事件：显示上下文信息 -->
                    <div v-if="evt.stage === 'followup' && currentLog.followup_context" class="rule-step-detail">
                      <span v-if="evt.input_skill">当前技能：{{ getSkillDisplayName(evt.input_skill) }}</span>
                      <span v-if="currentLog.followup_context.last_assistant_len != null" style="margin-left: 8px">上轮输出 {{ currentLog.followup_context.last_assistant_len }} 字</span>
                      <span v-if="currentLog.followup_context.error_status" style="margin-left: 8px; color: #d03050">错误：{{ currentLog.followup_context.error_status }}</span>
                    </div>
                    <!-- 其他事件：显示技能流转和原因 -->
                    <div v-else-if="evt.input_skill || evt.output_skill || evt.reason" class="rule-step-detail">
                      <span v-if="evt.input_skill && evt.output_skill && evt.input_skill !== evt.output_skill">
                        {{ getSkillDisplayName(evt.input_skill) }} → {{ getSkillDisplayName(evt.output_skill) }}
                      </span>
                      <span v-else-if="evt.output_skill">{{ getSkillDisplayName(evt.output_skill) }}</span>
                      <span v-if="evt.reason" style="margin-left: 8px; color: #999">{{ matchSourceMap[evt.reason] || actionNameMap[evt.reason] || evt.reason }}</span>
                    </div>
                  </n-timeline-item>
                </template>
              </n-timeline>
            </div>

            <!-- 置信度变化已合并到「完整决策链路」Timeline 中 -->
          </template>
        </n-spin>
      </n-drawer-content>
    </n-drawer>
  </div>
</template>

<style scoped>
.intent-logs {
  width: 100%;
}

.page-description {
  font-size: 13px;
  color: #666;
  line-height: 1.6;
}

.header-with-tip {
  border-bottom: 1px dashed #ccc;
  cursor: help;
}

.tip-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: #e8e8e8;
  color: #999;
  font-size: 11px;
  cursor: help;
  margin-left: 6px;
  vertical-align: middle;
}

/* 详情区块 */
.detail-section {
  margin-bottom: 20px;
}

.section-title {
  font-size: 14px;
  font-weight: 600;
  color: #333;
  margin-bottom: 10px;
  padding-bottom: 6px;
  border-bottom: 1px solid #f0f0f0;
}

.user-input-text {
  word-break: break-all;
  line-height: 1.5;
}

/* 路由结果 */
.route-result {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
  background: #fafafa;
  border-radius: 6px;
  border: 1px solid #f0f0f0;
}

.route-item {
  display: flex;
  align-items: center;
  gap: 8px;
}

.route-label {
  flex-shrink: 0;
  width: 72px;
  font-size: 13px;
  color: #666;
}

.route-value {
  font-size: 13px;
  color: #333;
}

.mono {
  font-family: monospace;
  font-size: 12px;
  color: #999;
}

/* 规则步骤 */
.rule-step-header {
  display: flex;
  align-items: center;
  gap: 8px;
}

.rule-step-name {
  font-size: 13px;
  font-weight: 500;
}

.rule-step-detail {
  font-size: 12px;
  color: #666;
  margin-top: 4px;
  padding: 4px 8px;
  background: #f6f8fa;
  border-radius: 4px;
}

/* 未命中规则：紧凑灰化 */
.compact-item {
  opacity: 0.5;
}

.compact-item :deep(.n-timeline-item-content) {
  min-height: 24px !important;
}

.rule-dimmed .rule-step-name {
  color: #bbb;
  font-weight: 400;
}

/* 候选列表 */
.candidate-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.candidate-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  background: #fafafa;
  border-radius: 4px;
  border: 1px solid #f0f0f0;
}

.candidate-rank {
  font-size: 12px;
  font-weight: 600;
  color: #999;
  width: 24px;
  flex-shrink: 0;
}

.candidate-source {
  font-size: 12px;
  color: #999;
  margin-left: auto;
}

/* 置信度变化 */
.conf-changes {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.conf-change-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  background: #fafafa;
  border-radius: 4px;
  border: 1px solid #f0f0f0;
}

.conf-from {
  font-size: 13px;
  color: #999;
}

.conf-arrow {
  font-size: 13px;
  color: #ccc;
}

.conf-reason {
  font-size: 12px;
  color: #666;
  margin-left: 4px;
  padding: 1px 6px;
  background: #f0f0f0;
  border-radius: 3px;
}

/* 对话上下文 */
.context-messages {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px 10px;
  background: #fafafa;
  border-radius: 6px;
  border: 1px solid #eee;
}

/* ── 会话上下文时间线 ── */
.context-timeline {
  display: flex;
  flex-direction: column;
}
.ctx-tl-item {
  display: flex;
  gap: 10px;
  min-height: 32px;
}
.ctx-tl-rail {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 12px;
  flex-shrink: 0;
}
.ctx-tl-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  border: 1.5px solid #d0d0d0;
  background: #fff;
  flex-shrink: 0;
  margin-top: 5px;
}
.dot-user { border-color: #1565c0; background: #e3f2fd; }
.dot-ai { border-color: #2e7d32; background: #e8f5e9; }
.dot-current { border-color: #2080f0; background: #2080f0; }
.ctx-tl-line {
  flex: 1;
  width: 1.5px;
  background: #e0e0e0;
  min-height: 8px;
}
.ctx-tl-body {
  flex: 1;
  min-width: 0;
  padding-bottom: 8px;
  font-size: 12px;
  line-height: 1.5;
}
.ctx-tl-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 2px;
}
.ctx-skill-tag {
  max-width: 100px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.ctx-role {
  flex-shrink: 0;
  font-size: 11px;
  font-weight: 600;
  color: #999;
  white-space: nowrap;
}
.ctx-user .ctx-role { color: #1565c0; }
.ctx-assistant .ctx-role { color: #2e7d32; }

.ctx-content {
  color: #666;
  word-break: break-all;
  max-height: 40px;
  overflow: hidden;
  line-height: 1.5;
}
.ctx-user .ctx-content {
  color: #333;
  font-weight: 500;
  max-height: none;
}

.ctx-current .ctx-tl-body {
  background: #e8f4ff;
  border-radius: 4px;
  padding: 6px 10px 6px 8px;
  margin: -2px 0;
}
.ctx-current .ctx-role {
  color: #2080f0;
  font-weight: 700;
}
.ctx-current .ctx-content {
  color: #1a1a1a;
  font-weight: 600;
  max-height: none;
}

/* 内联候选列表 */
.candidate-list-inline {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.candidate-inline {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.candidate-conf {
  font-size: 11px;
  color: #999;
}

/* 内联置信度变化 */
.conf-changes-inline {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
}

.conf-inline {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #666;
}

.conf-reason-inline {
  font-size: 11px;
  color: #999;
  padding: 1px 5px;
  background: #f5f5f5;
  border-radius: 3px;
}
</style>
