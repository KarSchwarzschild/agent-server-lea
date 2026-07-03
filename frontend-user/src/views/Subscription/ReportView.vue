<script setup lang="ts">
/**
 * 订阅结果报告查看页
 * 展示单次执行结果的详细报告，支持 Markdown 渲染 + 引用标记 + 参考资料 + 执行过程
 */
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NCard,
  NButton,
  NSpace,
  NTag,
  NSpin,
  NIcon,
  NDivider,
  NDrawer,
  NDrawerContent,
  useMessage,
} from 'naive-ui'
import {
  ArrowBackOutline,
  TimeOutline,
  DocumentTextOutline,
  ChevronDownOutline,
  ChevronUpOutline,
} from '@vicons/ionicons5'
import {
  getSubscriptionResult,
  type SubscriptionResult,
  type ToolCallDetail,
} from '../../api/subscription'
import type { Reference } from '@/types/super-agent'
import { useResponsive } from '../../composables/useResponsive'
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'
import 'highlight.js/styles/github.css'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const { isMobile } = useResponsive()

const subId = route.params.subId as string
const resultId = route.params.resultId as string
const result = ref<SubscriptionResult | null>(null)
const loading = ref(false)

// 当前引用列表（供 citationPlugin 渲染时访问）
const currentRefs = ref<Reference[]>([])

// ref_xxx → 顺序编号映射（按首次出现顺序 1,2,3...）
const refIdToSeqNum = ref<Record<string, number>>({})

// ——— XSS 安全辅助 ———
function escapeAttr(str: string): string {
  return str
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

// ——— 报告文本预处理 ———

/** 从报告末尾的参考文献区块解析出结构化引用（references_data 为空时的降级方案） */
function parseFallbackRefs(report: string): Reference[] {
  const refs: Reference[] = []
  const lines = report.split('\n')
  let inRefSection = false
  for (const line of lines) {
    if (/\*{0,2}参考文献\*{0,2}/.test(line)) {
      inRefSection = true
      continue
    }
    if (!inRefSection) continue
    const m = line.match(/^\d+\.\s*\[ref_(\d{3})\]\s*(.+)/)
    if (m) {
      const refId = `ref_${m[1]}`
      const rest = m[2].trim()
      const pm = rest.match(/^(.+?)\s*\((\d{4})\)\.\s*(.+)/)
      if (pm) {
        refs.push({ id: refId, title: pm[3], authors: pm[1].split(/,\s*/), year: parseInt(pm[2]), url: '', abstract: '', source: '' } as any)
      } else {
        refs.push({ id: refId, title: rest, authors: [], url: '', abstract: '', source: '' } as any)
      }
    }
  }
  return refs
}

/** 去掉报告末尾的参考文献区块（避免与参考资料卡片重复） */
function stripTrailingReferences(report: string): string {
  const lines = report.split('\n')
  let refSectionStart = -1
  for (let i = lines.length - 1; i >= 0; i--) {
    if (/^#{0,3}\s*-{0,3}\s*\*{0,2}参考文献\*{0,2}/.test(lines[i].trim())) {
      refSectionStart = i
      break
    }
  }
  if (refSectionStart === -1) return report
  const trailing = lines.slice(refSectionStart).join('\n')
  const refCount = (trailing.match(/\[ref_\d{3}\]/g) || []).length
  if (refCount < 2) return report
  // 同时去掉参考文献前的 "---" 分割线（如有）
  let cutStart = refSectionStart
  if (cutStart > 0 && lines[cutStart - 1].trim() === '---') cutStart--
  return lines.slice(0, cutStart).join('\n').replace(/\n{3,}$/, '\n')
}

/** 构建 ref_xxx → 顺序编号映射（按首次出现顺序） */
function buildRefSeqMap(report: string): Record<string, number> {
  const map: Record<string, number> = {}
  let seq = 1
  const regex = /\[ref_(\d{3})\]/g
  let match
  while ((match = regex.exec(report)) !== null) {
    const refId = `ref_${match[1]}`
    if (!(refId in map)) {
      map[refId] = seq++
    }
  }
  return map
}

// ——— markdown-it 引用标记插件 ———
function citationPlugin(md: MarkdownIt) {
  md.inline.ruler.push('citation_ref', (state, silent) => {
    const start = state.pos
    if (state.src.charCodeAt(start) !== 0x5b /* [ */) return false
    const match = state.src.slice(start).match(/^\[ref_(\d{3})\]/)
    if (!match) return false
    if (!silent) {
      const token = state.push('citation_ref', '', 0)
      token.content = match[1] // "027"
    }
    state.pos = start + match[0].length
    return true
  })

  md.renderer.rules.citation_ref = (tokens, idx) => {
    const rawNum = tokens[idx].content // "027"
    const refId = `ref_${rawNum}`
    const seqNum = refIdToSeqNum.value[refId] || parseInt(rawNum)
    const ref = currentRefs.value?.find(r => r.id === refId)
    const title = ref?.title || ''
    const authors = Array.isArray(ref?.authors) ? ref.authors.join(', ') : (ref?.authors as string || '')
    const year = ref?.year?.toString() || ''
    const source = ref?.source || ''
    const url = ref?.url || ''
    const abstract = ref?.abstract || ''

    return (
      `<button class="citation-mark" ` +
      `data-ref-id="${escapeAttr(refId)}" ` +
      `data-ref-idx="${seqNum}" ` +
      `data-ref-title="${escapeAttr(title)}" ` +
      `data-ref-authors="${escapeAttr(authors)}" ` +
      `data-ref-year="${escapeAttr(year)}" ` +
      `data-ref-source="${escapeAttr(source)}" ` +
      `data-ref-url="${escapeAttr(url)}" ` +
      `data-ref-abstract="${escapeAttr(abstract)}" ` +
      `aria-label="引用参考 ${seqNum}: ${escapeAttr(title)}" ` +
      `type="button">[${seqNum}]</button>`
    )
  }
}

// ——— 连续引用合并后处理 ———
function mergeConsecutiveCitations(html: string): string {
  return html.replace(
    /(<button class="citation-mark"[^>]*>\[(\d+)\]<\/button>)(\s*<button class="citation-mark"[^>]*>\[(\d+)\]<\/button>)+/g,
    match => {
      const nums = [...match.matchAll(/\[(\d+)\]/g)].map(m => m[1])
      const firstBtn = match.match(/<button[^>]*>/)![0]
      return `${firstBtn}[${nums.join(', ')}]</button>`
    }
  )
}

// ——— Markdown 渲染器 ———
const md: MarkdownIt = new MarkdownIt({
  html: false,
  linkify: true,
  typographer: true,
  highlight: function (str: string, lang: string) {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return (
          '<pre class="hljs"><code>' +
          hljs.highlight(str, { language: lang, ignoreIllegals: true }).value +
          '</code></pre>'
        )
      } catch (__) {}
    }
    return '<pre class="hljs"><code>' + md.utils.escapeHtml(str) + '</code></pre>'
  },
})
md.use(citationPlugin)

const renderMarkdown = (content: string): string => {
  if (!content) return ''
  // 清理来源可信度 emoji 标记
  const cleaned = content.replace(/🔴|🟡|🔵/g, '')
  return md.render(cleaned)
}

function renderReportWithRefs(content: string): string {
  // 去掉末尾参考文献区块（已在卡片中展示）
  let cleaned = stripTrailingReferences(content)
  // 清理 LLM 来源可信度 emoji 标记（🔴🟡🔵），这些是 skill prompt 指示 LLM 生成的残留
  cleaned = cleaned.replace(/🔴|🟡|🔵/g, '')
  const rendered = md.render(cleaned)
  return mergeConsecutiveCitations(rendered)
}

// ——— 参考资料 ———
const refsExpanded = ref(false)
// 按报告中出现顺序排序的参考资料列表
const sortedRefs = computed(() => {
  const refs = currentRefs.value || []
  const seqMap = refIdToSeqNum.value
  return [...refs].sort((a, b) => (seqMap[a.id] || 999) - (seqMap[b.id] || 999))
})
const displayedRefs = computed(() => {
  return refsExpanded.value ? sortedRefs.value : sortedRefs.value.slice(0, 8)
})

let lastClickedCitationBtn: HTMLElement | null = null
function scrollBackToContent() {
  if (lastClickedCitationBtn) {
    lastClickedCitationBtn.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }
}

// ——— 执行过程 ———
const executionExpanded = ref(false)
const toolCallsExpanded = ref(false)

const displayedToolCalls = computed(() => {
  const tcs = result.value?.tool_calls_detail || []
  return toolCallsExpanded.value ? tcs : tcs.slice(0, 5)
})

const searchCount = computed(() =>
  (result.value?.tool_calls_detail || []).filter(tc => tc.name.includes('search')).length
)
const fetchCount = computed(() =>
  (result.value?.tool_calls_detail || []).filter(
    tc => tc.name.includes('fetch') || tc.name.includes('read')
  ).length
)

const TOOL_DISPLAY_NAMES_MAP: Record<string, string> = {
  academic_search: '学术搜索',
  web_search: '网页搜索',
  web_fetch: '网页抓取',
  github_search: 'GitHub 搜索',
  kb_query: '知识库查询',
}

const TOOL_ICONS_MAP: Record<string, string> = {
  academic_search: '🔍',
  web_search: '🌐',
  web_fetch: '📄',
  github_search: '📂',
  kb_query: '📚',
}

function getToolDisplayName(name: string): string {
  return TOOL_DISPLAY_NAMES_MAP[name] || name
}

function getToolIcon(name: string): string {
  return TOOL_ICONS_MAP[name] || '🔧'
}

function getToolArgsSummary(tc: ToolCallDetail): string {
  const args = tc.arguments || {}
  if (args.query) return `"${String(args.query).slice(0, 40)}"`
  if (args.url) return String(args.url).slice(0, 50)
  const str = JSON.stringify(args)
  return str.length > 40 ? str.slice(0, 40) + '...' : str
}

function formatDuration(ms?: number): string {
  if (!ms) return '0秒'
  const seconds = Math.floor(ms / 1000)
  if (seconds < 60) return `${seconds}秒`
  const minutes = Math.floor(seconds / 60)
  const remainSeconds = seconds % 60
  return `${minutes}分${remainSeconds}秒`
}

// ——— Tooltip (PC 端，单例) ———
const tooltipVisible = ref(false)
const tooltipData = ref<{
  title: string
  authors: string
  year: string
  source: string
  url: string
  abstract: string
  x: number
  y: number
} | null>(null)
let tooltipTimer: ReturnType<typeof setTimeout> | null = null
const tooltipRef = ref<HTMLElement | null>(null)
const reportContentRef = ref<HTMLElement | null>(null)

function onReportMouseOver(e: MouseEvent) {
  const btn = (e.target as HTMLElement).closest('.citation-mark') as HTMLElement
  if (!btn) return
  if (tooltipTimer) clearTimeout(tooltipTimer)
  tooltipTimer = setTimeout(() => {
    tooltipData.value = {
      title: btn.dataset.refTitle || '',
      authors: btn.dataset.refAuthors || '',
      year: btn.dataset.refYear || '',
      source: btn.dataset.refSource || '',
      url: btn.dataset.refUrl || '',
      abstract: btn.dataset.refAbstract || '',
      x: btn.getBoundingClientRect().left + btn.offsetWidth / 2,
      y: btn.getBoundingClientRect().top,
    }
    tooltipVisible.value = true
  }, 200)
}

function onReportMouseOut(e: MouseEvent) {
  const btn = (e.target as HTMLElement).closest('.citation-mark')
  if (!btn) return
  if (tooltipTimer) clearTimeout(tooltipTimer)
  setTimeout(() => {
    if (!tooltipRef.value?.matches(':hover')) {
      tooltipVisible.value = false
    }
  }, 100)
}

function onTooltipMouseEnter() {
  if (tooltipTimer) clearTimeout(tooltipTimer)
  tooltipVisible.value = true
}

function onTooltipMouseLeave() {
  tooltipVisible.value = false
}

// ——— 移动端 Drawer ———
const drawerVisible = ref(false)
const drawerData = ref<typeof tooltipData.value>(null)

function onCitationClick(e: MouseEvent) {
  const btn = (e.target as HTMLElement).closest('.citation-mark') as HTMLElement
  if (!btn) return

  if (isMobile.value) {
    drawerData.value = {
      title: btn.dataset.refTitle || '',
      authors: btn.dataset.refAuthors || '',
      year: btn.dataset.refYear || '',
      source: btn.dataset.refSource || '',
      url: btn.dataset.refUrl || '',
      abstract: btn.dataset.refAbstract || '',
      x: 0,
      y: 0,
    }
    drawerVisible.value = true
  } else {
    const idx = btn.dataset.refIdx
    const card = document.getElementById(`ref-card-${idx}`)
    card?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    lastClickedCitationBtn = btn
  }
}

// Esc 关闭 tooltip
function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') tooltipVisible.value = false
}

// ——— 数据加载 ———
const loadResult = async () => {
  loading.value = true
  try {
    const res = await getSubscriptionResult(subId, resultId)
    result.value = res.result
    const report = res.result.report || ''

    // 引用列表：优先使用 API 返回的 references_data，否则从报告末尾解析
    let refs = res.result.references_data || []
    if (!refs.length && report) {
      refs = parseFallbackRefs(report)
    }

    // 构建 ref_xxx → 顺序编号映射（正文引用按首次出现排序，未引用的追加在后）
    const seqMap = buildRefSeqMap(report)
    let nextSeq = Object.keys(seqMap).length + 1
    for (const r of refs) {
      if (!(r.id in seqMap)) {
        seqMap[r.id] = nextSeq++
      }
    }
    refIdToSeqNum.value = seqMap
    currentRefs.value = refs
  } catch {
    message.error('加载报告失败')
  } finally {
    loading.value = false
  }
}

const goBack = () => {
  router.push(`/intelligence/${subId}/results`)
}

const formatDateTime = (dateStr?: string) => {
  if (!dateStr) return '-'
  return new Date(dateStr).toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

const execModeLabels: Record<string, string> = {
  quick: '快速检查',
  deep: '深度分析',
}

onMounted(() => {
  loadResult()
  document.addEventListener('keydown', onKeydown)
})

onUnmounted(() => {
  document.removeEventListener('keydown', onKeydown)
  if (tooltipTimer) clearTimeout(tooltipTimer)
})
</script>

<template>
  <div class="report-page" :class="{ 'report-page-mobile': isMobile }">
    <!-- 头部 -->
    <div :class="isMobile ? 'mobile-header' : 'header-wrapper'">
      <n-card v-if="!isMobile" class="header-card">
        <div class="header-content">
          <n-space align="center" size="small">
            <n-button text @click="goBack">
              <n-icon :component="ArrowBackOutline" :size="20" />
            </n-button>
            <h2>执行报告</h2>
          </n-space>
          <n-space v-if="result" size="small">
            <n-tag size="small" :type="result.exec_mode === 'deep' ? 'warning' : 'info'">
              {{ execModeLabels[result.exec_mode] || result.exec_mode }}
            </n-tag>
            <n-tag size="small">
              <template #icon>
                <n-icon :component="TimeOutline" />
              </template>
              {{ formatDateTime(result.run_at) }}
            </n-tag>
            <n-tag size="small">{{ result.item_count }} 条发现</n-tag>
          </n-space>
        </div>
      </n-card>
      <template v-else>
        <div class="mobile-header-inner">
          <n-button text @click="goBack" size="small">
            <n-icon :component="ArrowBackOutline" :size="20" />
          </n-button>
          <h2>执行报告</h2>
          <span style="width: 32px" />
        </div>
      </template>
    </div>

    <!-- 报告内容 -->
    <div :class="isMobile ? 'mobile-content' : 'content-section'">
      <div v-if="loading" class="loading-state">
        <n-spin size="large" />
      </div>

      <template v-else-if="result">
        <!-- 移动端也显示元数据 -->
        <div v-if="isMobile" class="mobile-meta">
          <n-space size="small">
            <n-tag size="small" :type="result.exec_mode === 'deep' ? 'warning' : 'info'">
              {{ execModeLabels[result.exec_mode] || result.exec_mode }}
            </n-tag>
            <n-tag size="small">{{ formatDateTime(result.run_at) }}</n-tag>
            <n-tag size="small">{{ result.item_count }} 条发现</n-tag>
          </n-space>
        </div>

        <!-- 摘要 -->
        <n-card v-if="result.summary" class="report-section">
          <template #header>
            <n-space align="center" size="small">
              <n-icon :component="DocumentTextOutline" :size="18" />
              <span>摘要</span>
            </n-space>
          </template>
          <div class="markdown-content" v-html="renderMarkdown(result.summary)" />
        </n-card>

        <!-- 深度报告（含引用标记） -->
        <n-card v-if="result.report" class="report-section">
          <template #header>
            <n-space align="center" size="small">
              <n-icon :component="DocumentTextOutline" :size="18" />
              <span>深度报告</span>
            </n-space>
          </template>
          <div
            ref="reportContentRef"
            class="markdown-content"
            v-html="renderReportWithRefs(result.report)"
            @mouseover="onReportMouseOver"
            @mouseout="onReportMouseOut"
            @click="onCitationClick"
          />
        </n-card>

        <!-- 参考资料 -->
        <n-card v-if="currentRefs.length" class="report-section references-card">
          <template #header>
            <span>参考资料（{{ currentRefs.length }} 篇）</span>
          </template>
          <div class="references-list">
            <div
              v-for="ref in displayedRefs"
              :key="ref.id"
              :id="`ref-card-${refIdToSeqNum[ref.id] || 0}`"
              class="reference-item"
            >
              <div class="ref-header">
                <span class="ref-number">[{{ refIdToSeqNum[ref.id] || '?' }}]</span>
                <span class="ref-title">{{ ref.title }}</span>
              </div>
              <div class="ref-meta">
                <span v-if="ref.authors?.length">{{ Array.isArray(ref.authors) ? ref.authors.join(', ') : ref.authors }}</span>
                <span v-if="ref.year"> · {{ ref.year }}</span>
                <span v-if="ref.source"> · {{ ref.source }}</span>
              </div>
              <p v-if="ref.abstract" class="ref-abstract">{{ ref.abstract }}</p>
              <div class="ref-actions">
                <a
                  v-if="ref.url"
                  :href="ref.url"
                  target="_blank"
                  rel="noopener"
                  class="ref-link"
                >查看原文 →</a>
                <button class="ref-back-btn" @click="scrollBackToContent">↑ 返回正文</button>
              </div>
            </div>
          </div>
          <!-- 渐变遮罩 + 展开按钮 -->
          <div
            v-if="!refsExpanded && currentRefs.length > 8"
            class="refs-fade-overlay"
          >
            <n-button text @click="refsExpanded = true">
              展开全部 {{ currentRefs.length }} 篇参考资料
            </n-button>
          </div>
        </n-card>

        <!-- 执行过程 -->
        <n-card v-if="result.tool_calls_detail?.length" class="report-section execution-card">
          <div class="execution-summary" @click="executionExpanded = !executionExpanded">
            <span>耗时 {{ formatDuration(result.execution_time_ms) }} · {{ result.tool_calls_detail.length }} 次工具调用</span>
            <span v-if="searchCount"> · {{ searchCount }} 次搜索</span>
            <span v-if="fetchCount"> · {{ fetchCount }} 次阅读</span>
            <n-icon :component="executionExpanded ? ChevronUpOutline : ChevronDownOutline" />
          </div>
          <div v-if="executionExpanded" class="execution-timeline">
            <div
              v-for="(tc, idx) in displayedToolCalls"
              :key="idx"
              class="timeline-item"
            >
              <span class="tool-icon">{{ getToolIcon(tc.name) }}</span>
              <span class="tool-name">{{ getToolDisplayName(tc.name) }}</span>
              <span class="tool-args">{{ getToolArgsSummary(tc) }}</span>
              <span class="tool-duration">{{ (tc.duration_ms / 1000).toFixed(1) }}s</span>
            </div>
            <n-button
              v-if="!toolCallsExpanded && result.tool_calls_detail.length > 5"
              text
              @click="toolCallsExpanded = true"
            >
              展开更多（{{ result.tool_calls_detail.length - 5 }} 项）
            </n-button>
          </div>
        </n-card>

        <!-- 发现条目 -->
        <n-card v-if="result.items?.length" class="report-section">
          <template #header>
            发现条目（{{ result.item_count }}）
          </template>
          <div class="items-list">
            <div
              v-for="(item, idx) in result.items"
              :key="idx"
              class="result-item"
            >
              <div class="item-header">
                <span class="item-index">{{ idx + 1 }}</span>
                <span class="item-title">{{ item.title || item.name || `条目 ${idx + 1}` }}</span>
              </div>
              <p v-if="item.summary || item.description" class="item-desc">
                {{ item.summary || item.description }}
              </p>
              <a
                v-if="item.url || item.link"
                :href="item.url || item.link"
                target="_blank"
                rel="noopener noreferrer"
                class="item-link"
              >
                查看原文
              </a>
            </div>
          </div>
        </n-card>

        <!-- 空结果 -->
        <n-card
          v-if="!result.summary && !result.report && !result.items?.length"
          class="report-section"
        >
          <div class="empty-report">暂无详细报告内容</div>
        </n-card>
      </template>
    </div>

    <!-- 单例 Tooltip (PC, Teleport to body) -->
    <Teleport to="body">
      <div
        v-if="tooltipVisible && tooltipData"
        ref="tooltipRef"
        class="citation-tooltip"
        :style="{ left: tooltipData.x + 'px', top: (tooltipData.y - 8) + 'px' }"
        @mouseenter="onTooltipMouseEnter"
        @mouseleave="onTooltipMouseLeave"
      >
        <div class="tooltip-title">{{ tooltipData.title }}</div>
        <div class="tooltip-meta" v-if="tooltipData.authors || tooltipData.year">
          {{ tooltipData.authors }}
          <span v-if="tooltipData.year"> · {{ tooltipData.year }}</span>
          <span v-if="tooltipData.source"> · {{ tooltipData.source }}</span>
        </div>
        <p v-if="tooltipData.abstract" class="tooltip-abstract">{{ tooltipData.abstract }}</p>
        <a
          v-if="tooltipData.url"
          :href="tooltipData.url"
          target="_blank"
          rel="noopener"
          class="tooltip-link"
        >查看原文 →</a>
      </div>
    </Teleport>

    <!-- NDrawer (移动端引用详情) -->
    <n-drawer v-model:show="drawerVisible" placement="bottom" :height="360">
      <n-drawer-content v-if="drawerData" :title="drawerData.title">
        <div class="drawer-meta" v-if="drawerData.authors || drawerData.year">
          {{ drawerData.authors }}
          <span v-if="drawerData.year"> · {{ drawerData.year }}</span>
        </div>
        <p v-if="drawerData.abstract">{{ drawerData.abstract }}</p>
        <n-button
          v-if="drawerData.url"
          tag="a"
          :href="drawerData.url"
          target="_blank"
          type="primary"
          block
        >
          查看原文 →
        </n-button>
      </n-drawer-content>
    </n-drawer>
  </div>
</template>

<style scoped>
.report-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 24px;
  overflow: hidden;
}

.header-wrapper {
  flex-shrink: 0;
  margin-bottom: 16px;
}

.header-content {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.header-content h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
}

.content-section {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.loading-state {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 200px;
}

.report-section {
  flex-shrink: 0;
}

.empty-report {
  text-align: center;
  padding: 40px;
  color: #999;
}

/* 发现条目 */
.items-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.result-item {
  padding: 12px;
  background: #f9f9f9;
  border-radius: 8px;
}

.item-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.item-index {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: #2080f0;
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  flex-shrink: 0;
}

.item-title {
  font-size: 14px;
  font-weight: 500;
  color: #333;
}

.item-desc {
  margin: 0 0 6px;
  font-size: 13px;
  color: #666;
  line-height: 1.5;
}

.item-link {
  font-size: 13px;
  color: #2080f0;
  text-decoration: none;
}

.item-link:hover {
  text-decoration: underline;
}

/* Markdown 内容样式 */
.markdown-content {
  line-height: 1.6;
}

.markdown-content :deep(p) {
  margin: 0 0 0.8em;
}

.markdown-content :deep(p:last-child) {
  margin-bottom: 0;
}

.markdown-content :deep(h1),
.markdown-content :deep(h2),
.markdown-content :deep(h3),
.markdown-content :deep(h4) {
  margin: 1em 0 0.5em;
  font-weight: 600;
}

.markdown-content :deep(h1) { font-size: 1.4em; }
.markdown-content :deep(h2) { font-size: 1.2em; }
.markdown-content :deep(h3) { font-size: 1.1em; }

.markdown-content :deep(ul),
.markdown-content :deep(ol) {
  margin: 0.5em 0;
  padding-left: 1.5em;
}

.markdown-content :deep(li) {
  margin: 0.25em 0;
}

.markdown-content :deep(blockquote) {
  margin: 0.5em 0;
  padding: 0.5em 1em;
  border-left: 3px solid #ddd;
  background: rgba(0, 0, 0, 0.03);
}

.markdown-content :deep(pre.hljs) {
  margin: 0.5em 0;
  padding: 12px;
  border-radius: 6px;
  overflow-x: auto;
  background: #f6f8fa;
}

.markdown-content :deep(code) {
  font-family: 'SF Mono', Monaco, Consolas, monospace;
  font-size: 0.9em;
}

.markdown-content :deep(:not(pre) > code) {
  padding: 0.2em 0.4em;
  background: rgba(0, 0, 0, 0.05);
  border-radius: 3px;
}

.markdown-content :deep(table) {
  border-collapse: collapse;
  margin: 0.5em 0;
  width: 100%;
}

.markdown-content :deep(th),
.markdown-content :deep(td) {
  border: 1px solid #ddd;
  padding: 8px;
  text-align: left;
}

.markdown-content :deep(th) {
  background: #f5f5f5;
  font-weight: 600;
}

.markdown-content :deep(a) {
  color: #2080f0;
  text-decoration: none;
}

.markdown-content :deep(a:hover) {
  text-decoration: underline;
}

/* 引用标记 */
.markdown-content :deep(.citation-mark) {
  display: inline;
  padding: 0 2px;
  margin: 0 1px;
  font-size: 0.75em;
  font-weight: 600;
  color: #3b82f6;
  background: #eff6ff;
  border: none;
  border-radius: 3px;
  cursor: pointer;
  vertical-align: super;
  line-height: 1;
  min-width: 18px;
  text-align: center;
  font-family: inherit;
  transition: background 0.15s;
}

.markdown-content :deep(.citation-mark:hover) {
  background: #dbeafe;
  color: #2563eb;
}

/* Tooltip */
.citation-tooltip {
  position: fixed;
  z-index: 9999;
  max-width: 360px;
  padding: 12px 16px;
  background: #1f2937;
  color: #f9fafb;
  border-radius: 8px;
  box-shadow: 0 4px 24px rgba(0, 0, 0, 0.25);
  font-size: 13px;
  line-height: 1.5;
  transform: translateX(-50%) translateY(-100%);
  pointer-events: auto;
}

.tooltip-title {
  font-weight: 600;
  margin-bottom: 4px;
}

.tooltip-meta {
  font-size: 12px;
  color: #9ca3af;
  margin-bottom: 6px;
}

.tooltip-abstract {
  margin: 0;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
  color: #d1d5db;
}

.tooltip-link {
  color: #60a5fa;
  text-decoration: none;
  font-size: 12px;
}

.tooltip-link:hover {
  text-decoration: underline;
}

/* 参考资料 */
.references-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.reference-item {
  padding: 12px 16px;
  background: #f8fafc;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
}

.ref-header {
  display: flex;
  gap: 8px;
  align-items: baseline;
}

.ref-number {
  font-weight: 700;
  color: #3b82f6;
  min-width: 28px;
}

.ref-title {
  font-weight: 600;
  color: #1e293b;
}

.ref-meta {
  font-size: 13px;
  color: #64748b;
  margin-top: 4px;
}

.ref-abstract {
  font-size: 13px;
  color: #475569;
  margin: 8px 0 0;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.ref-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 8px;
}

.ref-link {
  color: #3b82f6;
  font-size: 13px;
  text-decoration: none;
}

.ref-link:hover {
  text-decoration: underline;
}

.ref-back-btn {
  background: none;
  border: none;
  color: #94a3b8;
  font-size: 12px;
  cursor: pointer;
  padding: 4px 8px;
}

.ref-back-btn:hover {
  color: #3b82f6;
}

/* 渐变遮罩 */
.refs-fade-overlay {
  position: relative;
  text-align: center;
  padding-top: 24px;
  margin-top: -40px;
  background: linear-gradient(transparent, white 60%);
}

/* 执行过程 */
.execution-summary {
  display: flex;
  align-items: center;
  gap: 4px;
  cursor: pointer;
  padding: 4px 0;
  font-size: 14px;
  color: #64748b;
  user-select: none;
}

.execution-summary:hover {
  color: #3b82f6;
}

.execution-timeline {
  margin-top: 12px;
}

.timeline-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid #f1f5f9;
  font-size: 13px;
}

.timeline-item:last-child {
  border-bottom: none;
}

.tool-icon {
  font-size: 16px;
  min-width: 24px;
  text-align: center;
}

.tool-name {
  font-weight: 500;
  color: #334155;
  min-width: 80px;
}

.tool-args {
  flex: 1;
  color: #64748b;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tool-duration {
  color: #94a3b8;
  font-size: 12px;
  min-width: 40px;
  text-align: right;
}

/* 移动端适配 */
@media (max-width: 768px) {
  .markdown-content :deep(.citation-mark) {
    min-width: 44px;
    min-height: 44px;
    line-height: 44px;
  }

  .citation-tooltip {
    display: none;
  }

  .reference-item {
    padding: 10px 12px;
  }

  .ref-header {
    flex-direction: column;
    gap: 4px;
  }
}

/* ===== 移动端布局样式 ===== */
.report-page-mobile {
  padding: 0;
  background: #f5f7f9;
}

.mobile-header {
  background: #fff;
  border-bottom: 1px solid #eee;
}

.mobile-header-inner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
}

.mobile-header-inner h2 {
  margin: 0;
  font-size: 17px;
  font-weight: 600;
  text-align: center;
  flex: 1;
}

.mobile-meta {
  padding: 12px 16px 0;
}

.mobile-content {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  padding-bottom: calc(56px + env(safe-area-inset-bottom, 0px) + 16px);
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.drawer-meta {
  font-size: 13px;
  color: #64748b;
  margin-bottom: 8px;
}
</style>
