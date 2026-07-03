<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { getDeepReviewStatus, type DeepReviewTask } from '../../api/deep-review'
import IrRenderer from '../../components/report/IrRenderer.vue'

const route = useRoute()
const router = useRouter()
const message = useMessage()

const taskId = route.params.taskId as string
const task = ref<DeepReviewTask | null>(null)
const loading = ref(true)
const showTocDrawer = ref(false)
const showCitePreview = ref(false)
const citeSourceId = ref('')

// IR 文档数据
const documentIR = ref<any>(null)

// 渲染模式
const renderMode = computed(() => documentIR.value ? 'ir' : 'markdown')

// 目录结构
interface TocItem {
  id: string
  title: string
  level: number
}
const tocItems = ref<TocItem[]>([])
const activeSection = ref('')

/** 清除 {#anchor} 语法，转换为真实 id 属性；清理冗余内容 */
function preprocessMarkdown(md: string): string {
  return md
    // 处理标题行的 {#id} → 保留为注释锚点
    .replace(/^(#{1,6}\s+.+?)\s*\{#([\w-]+)\}\s*$/gm, (_, heading, id) => {
      return `${heading} <!-- anchor:${id} -->`
    })
    // 隐藏正文中的冗余"目录"章节（侧边栏已有）
    .replace(/^## 目录\s*\n([\s\S]*?)(?=\n## )/m, '')
    // 移除摘要正文开头重复的 **摘要** 标记（LLM 生成冗余）
    .replace(/\*\*摘要\*\*\s{0,3}\n/m, '')
    // 移除参考文献占位符（如果仍存在旧数据）
    .replace(/（参考文献列表将在.*?生成）/g, '')
    // 给"参考文献"标题加锚点 id
    .replace(/^(#{1,3})\s*参考文献\s*$/m, '$1 参考文献 <!-- anchor:references -->')
    // 旧报告兼容：参考文献条目挤在一行时，按 [N] 拆行
    .replace(/(\[(\d+)\]\s*[^[\n]+?)(?=\s*\[\d+\])/g, '$1\n\n')
}

/** 把 <!-- anchor:xxx --> 注释转成真实 id，并美化摘要/关键词区域 */
function postprocessHtml(html: string): string {
  return html
    // 转换锚点注释为 id
    .replace(/<(h[1-6])([^>]*)>(.*?)\s*<!-- anchor:([\w-]+) -->\s*<\/\1>/g,
      '<$1$2 id="$4">$3</$1>')
    // 给"关键词"行加样式（匹配多种格式）
    // 情况1：关键词独占一个 <p>
    .replace(/<p><strong>关键词<\/strong>[：:]\s*(.*?)<\/p>/,
      '<div class="keywords"><span class="kw-label">关键词</span>$1</div>')
    .replace(/<p>关键词[：:]\s*(.*?)<\/p>/,
      '<div class="keywords"><span class="kw-label">关键词</span>$1</div>')
    // 情况2：关键词在摘要 <p> 内用 <br> 分隔 — 拆分出来
    .replace(/<br><strong>关键词<\/strong>[：:]\s*(.*?)<\/p>/,
      '</p><div class="keywords"><span class="kw-label">关键词</span>$1</div>')
    .replace(/<br>关键词[：:]\s*(.*?)<\/p>/,
      '</p><div class="keywords"><span class="kw-label">关键词</span>$1</div>')
    // 给参考文献每条 [N] 加锚点 id
    .replace(/<p>\[(\d+)\]/g, '<p id="ref-$1" class="ref-entry"><span class="ref-num">[$1]</span>')
    // 解析 ref-meta 注释，生成来源标签/链接
    .replace(/<!-- ref-meta:([\w_]*?):([\w\-]*?):(.*?) -->/g, (_m, source, docId, url) => {
      if (url) {
        return `<a class="ref-link" href="${url}" target="_blank" rel="noopener">查看原文 ↗</a>`
      }
      if (source === 'kb_api' && docId) {
        return `<button class="ref-kb-btn" data-doc-id="${docId}" title="查看知识库文档">知识库 →</button>`
      }
      if (source === 'academic_search') {
        return '<span class="ref-source-tag">学术搜索</span>'
      }
      return ''
    })
}

/**
 * 从 Markdown 参考文献列表中提取每条 [N] 的标题和摘要（用于 tooltip）
 * 返回 Map<编号字符串, 标题文本>
 */
function buildRefInfo(md: string): Map<string, string> {
  const map = new Map<string, string>()
  // 新格式：匹配 [N] 标题. 摘要...（可能跨行）
  const refSection = md.split(/^##\s*参考文献/m)[1] || ''
  const entries = refSection.split(/(?=\[\d+\])/).filter(s => s.trim())
  for (const entry of entries) {
    const m = entry.match(/^\[(\d+)\]\s*(.+)/s)
    if (m) {
      const num = m[1]
      let text = m[2].replace(/\n/g, ' ').trim()
      if (text.length > 120) text = text.slice(0, 120) + '…'
      map.set(num, text)
    }
  }
  // 旧格式兼容：[N] Author (Year).
  const oldPattern = /\[(\d+)\]\s*(.+?)\s*\((\d{4})\)/g
  let om
  while ((om = oldPattern.exec(md)) !== null) {
    if (!map.has(om[1])) {
      map.set(om[1], `${om[2].trim()} (${om[3]})`)
      // 映射 "Author, Year" -> refId
      map.set(`${om[2].trim()}, ${om[3]}`, `ref-${om[1]}`)
      map.set(`${om[2].trim()},${om[3]}`, `ref-${om[1]}`)
    }
  }
  return map
}

// 渲染后的 HTML（Markdown 模式）
const renderedHtml = computed(() => {
  if (!task.value?.result_markdown) return ''
  const rawMd = task.value.result_markdown
  const refInfo = buildRefInfo(rawMd)
  const processed = preprocessMarkdown(rawMd)
  const html = marked(processed) as string
  let result = postprocessHtml(html)

  // 新格式：[文献N] → 带 tooltip 的可点击链接
  result = result.replace(
    /\[文献(\d+)\]/g,
    (_match, num) => {
      const tip = refInfo.get(num) || `文献${num}`
      const escaped = tip.replace(/"/g, '&quot;')
      return `<a class="cite-inline cite-tooltip" href="#ref-${num}" data-tip="${escaped}">[${num}]</a>`
    }
  )

  // 旧格式兼容：(作者, 年份) → 带 tooltip 的链接
  result = result.replace(
    /\(([^()]{2,60},\s*\d{4}[a-z]?)\)/g,
    (_match, cite) => {
      const normalized = cite.replace(/\s+/g, ' ').trim()
      const refId = refInfo.get(normalized) || refInfo.get(normalized.replace(/ /g, ''))
      const href = refId ? `#${refId}` : '#references'
      return `<a class="cite-inline" href="${href}" title="${cite}">(${cite})</a>`
    }
  )

  return DOMPurify.sanitize(result, {
    ADD_ATTR: ['id', 'data-tip', 'data-doc-id'],
    ADD_TAGS: ['button'],
  })
})

onMounted(async () => {
  try {
    task.value = await getDeepReviewStatus(taskId)
    if (task.value.status !== 'completed') {
      if (task.value.status === 'running' || task.value.status === 'pending') {
        router.replace(`/review/progress/${taskId}`)
      }
      return
    }
    // 尝试解析 IR 数据
    if (task.value.result_ir) {
      try {
        documentIR.value = typeof task.value.result_ir === 'string'
          ? JSON.parse(task.value.result_ir)
          : task.value.result_ir
      } catch {
        documentIR.value = null
      }
    }
    await nextTick()
    extractToc()
    _bindScroll()
  } catch (e: any) {
    message.error('加载报告失败')
  } finally {
    loading.value = false
  }
})

onUnmounted(() => {
  _unbindScroll()
})

function extractToc() {
  if (documentIR.value) {
    // IR 模式：从 DocumentIR 提取目录
    const items: TocItem[] = []
    documentIR.value.chapters?.forEach((ch: any, i: number) => {
      items.push({
        id: `chapter-${ch.chapter_id}`,
        title: ch.title,
        level: 1,
      })
      // 提取子标题
      ch.blocks?.forEach((block: any, j: number) => {
        if (block.type === 'heading') {
          items.push({
            id: block.anchor || `chapter-${ch.chapter_id}-h${j}`,
            title: block.text,
            level: block.level || 2,
          })
        }
      })
    })
    tocItems.value = items
    if (items.length > 0) activeSection.value = items[0].id
    return
  }

  // Markdown 模式：从渲染后的 HTML 提取标题
  const container = document.querySelector('.report-content')
  if (!container) return

  const headings = container.querySelectorAll('h1, h2, h3, h4')
  const items: TocItem[] = []
  headings.forEach((h, i) => {
    // 优先使用已有的 id（来自 anchor 处理），否则生成
    const id = h.id || `section-${i}`
    if (!h.id) h.setAttribute('id', id)
    // 清理标题文本中残留的 {#xxx} 和序号前缀
    let title = (h.textContent || '').replace(/\s*\{#[\w-]+\}\s*/g, '').trim()
    // 跳过冗余的目录标题（侧边栏已有）
    if (title === '目录') return
    items.push({ id, title, level: parseInt(h.tagName[1]) })
  })
  tocItems.value = items
  if (items.length > 0) activeSection.value = items[0].id
}

function handleCiteClick(sourceId: string) {
  citeSourceId.value = sourceId
  showCitePreview.value = true
}

function scrollToSection(id: string) {
  activeSection.value = id
  const el = document.getElementById(id)
  if (el) {
    el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
  showTocDrawer.value = false
}

// 知识库文档预览 Drawer
const showKbDrawer = ref(false)
const kbDocContent = ref('')
const kbDocTitle = ref('')
const kbDocLoading = ref(false)

async function handleKbDocPreview(docId: string) {
  showKbDrawer.value = true
  kbDocLoading.value = true
  kbDocContent.value = ''

  // 先从页面上的参考文献条目中提取已有内容（作为兜底）
  let fallbackTitle = '知识库文档'
  let fallbackContent = ''
  const refEntries = document.querySelectorAll('.ref-entry')
  for (const entry of refEntries) {
    const btn = entry.querySelector(`.ref-kb-btn[data-doc-id="${docId}"]`)
    if (btn) {
      const clone = entry.cloneNode(true) as HTMLElement
      clone.querySelectorAll('button, .ref-num').forEach(el => el.remove())
      const text = clone.textContent?.trim() || ''
      const dotIdx = text.indexOf('.')
      if (dotIdx > 0 && dotIdx < 100) {
        fallbackTitle = text.slice(0, dotIdx).trim()
        fallbackContent = text.slice(dotIdx + 1).trim()
      } else {
        fallbackTitle = text.slice(0, 60)
        fallbackContent = text
      }
      break
    }
  }
  kbDocTitle.value = fallbackTitle

  // 尝试调 API 获取更多内容
  try {
    const token = localStorage.getItem('token')
    const resp = await fetch(`/api/user/report/kb-doc/${docId}?q=${encodeURIComponent(fallbackTitle)}`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    if (resp.ok) {
      const data = await resp.json()
      if (data.segments?.length > 0) {
        kbDocTitle.value = data.title || fallbackTitle
        kbDocContent.value = data.segments.join('\n\n---\n\n')
      } else {
        // API 没搜到，用页面上的内容
        kbDocContent.value = fallbackContent || '暂无更多内容'
      }
    } else {
      kbDocContent.value = fallbackContent || '暂无更多内容'
    }
  } catch {
    kbDocContent.value = fallbackContent || '加载失败'
  } finally {
    kbDocLoading.value = false
  }
}

// 参考文献回链：点击 [N] 跳到正文中引用 #ref-N 的第一个 <a>
function handleReportClick(e: Event) {
  const target = e.target as HTMLElement

  // 知识库按钮点击 → 打开预览 Drawer
  const kbBtn = target.closest('.ref-kb-btn') as HTMLButtonElement
  if (kbBtn) {
    e.preventDefault()
    const docId = kbBtn.dataset.docId
    if (docId) handleKbDocPreview(docId)
    return
  }
}

function exportMarkdown() {
  if (!task.value?.result_markdown) return
  const blob = new Blob([task.value.result_markdown], { type: 'text/markdown' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${task.value.topic || '综述'}.md`
  a.click()
  URL.revokeObjectURL(url)
  message.success('导出成功')
}

function exportTxt() {
  if (!task.value?.result_markdown) return
  // 简单去除 Markdown 标记
  const text = task.value.result_markdown
    .replace(/#{1,6}\s/g, '')
    .replace(/\*\*(.*?)\*\*/g, '$1')
    .replace(/\*(.*?)\*/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
  const blob = new Blob([text], { type: 'text/plain' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${task.value.topic || '综述'}.txt`
  a.click()
  URL.revokeObjectURL(url)
  message.success('导出成功')
}

function goBack() {
  router.push('/review')
}

// 阅读进度
const readProgress = ref(0)
function handleScroll() {
  const main = document.querySelector('.report-main')
  if (!main) return
  const { scrollTop, scrollHeight, clientHeight } = main as HTMLElement
  readProgress.value = Math.round((scrollTop / (scrollHeight - clientHeight)) * 100) || 0
}

// 文内搜索
const showSearch = ref(false)
const searchText = ref('')
const searchCount = ref(0)
function toggleSearch() {
  showSearch.value = !showSearch.value
  if (!showSearch.value) clearSearch()
}
function doSearch() {
  if (!searchText.value.trim()) { clearSearch(); return }
  const main = document.querySelector('.report-main')
  if (!main) return
  const html = (main as HTMLElement).innerHTML
  const cleaned = html.replace(/<mark class="search-hl">/g, '').replace(/<\/mark>/g, '')
  const regex = new RegExp(`(${searchText.value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi')
  let count = 0
  const highlighted = cleaned.replace(regex, (_: string, m: string) => { count++; return `<mark class="search-hl">${m}</mark>` })
  ;(main as HTMLElement).innerHTML = highlighted
  searchCount.value = count
}
function clearSearch() {
  searchText.value = ''
  searchCount.value = 0
  const main = document.querySelector('.report-main')
  if (main) {
    (main as HTMLElement).innerHTML = (main as HTMLElement).innerHTML
      .replace(/<mark class="search-hl">/g, '').replace(/<\/mark>/g, '')
  }
}

// 重新生成
function regenerate() {
  if (!task.value) return
  router.push({ path: '/review/deep', query: { topic: task.value.topic } })
}

// 分享（复制链接）
function shareReport() {
  const url = window.location.href
  navigator.clipboard.writeText(url).then(() => {
    message.success('链接已复制')
  }).catch(() => {
    message.error('复制失败')
  })
}

// 滚动监听清理
let _scrollEl: HTMLElement | null = null
function _bindScroll() {
  _scrollEl = document.querySelector('.report-main')
  if (_scrollEl) _scrollEl.addEventListener('scroll', handleScroll)
}
function _unbindScroll() {
  if (_scrollEl) _scrollEl.removeEventListener('scroll', handleScroll)
}
</script>

<template>
  <div class="report-viewer">
    <n-spin :show="loading">
      <template v-if="task && task.status === 'completed'">
        <!-- 顶部工具栏 -->
        <div class="toolbar">
          <n-space>
            <n-button text @click="goBack">← 返回</n-button>
          </n-space>
          <n-space>
            <n-button size="small" quaternary @click="toggleSearch">搜索</n-button>
            <n-button size="small" quaternary @click="regenerate">重新生成</n-button>
            <n-button size="small" quaternary @click="shareReport">分享</n-button>
            <n-dropdown
              trigger="click"
              :options="[
                { label: 'Markdown (.md)', key: 'md' },
                { label: '纯文本 (.txt)', key: 'txt' },
              ]"
              @select="(key: string) => key === 'md' ? exportMarkdown() : exportTxt()"
            >
              <n-button size="small">导出</n-button>
            </n-dropdown>
            <n-button size="small" class="toc-toggle" @click="showTocDrawer = true">目录</n-button>
          </n-space>
        </div>

        <!-- 阅读进度条 -->
        <div class="read-progress" :style="{ width: readProgress + '%' }" />

        <!-- 搜索栏 -->
        <div v-if="showSearch" class="search-bar">
          <n-input
            v-model:value="searchText"
            size="small"
            placeholder="搜索内容..."
            @keyup.enter="doSearch"
            style="width: 240px;"
          />
          <n-text v-if="searchCount > 0" depth="3" style="font-size: 12px;">{{ searchCount }} 个结果</n-text>
          <n-button size="tiny" text @click="doSearch">搜索</n-button>
          <n-button size="tiny" text @click="clearSearch">清除</n-button>
        </div>

        <div class="viewer-layout">
          <!-- 左侧目录（桌面端） -->
          <aside class="toc-sidebar">
            <div class="toc-header">目录导航</div>
            <div class="toc-list">
              <div
                v-for="item in tocItems"
                :key="item.id"
                class="toc-item"
                :class="{ active: activeSection === item.id }"
                :style="{ paddingLeft: `${(item.level - 1) * 16 + 12}px` }"
                :title="item.title"
                @click="scrollToSection(item.id)"
              >
                {{ item.title }}
              </div>
            </div>

            <!-- 报告信息 -->
            <div class="report-meta">
              <div>字数: {{ task.word_count?.toLocaleString() }}</div>
              <div>章节: {{ task.chapters_count }}</div>
              <div>耗时: {{ task.duration_seconds }}秒</div>
            </div>
          </aside>

          <!-- 右侧正文 -->
          <main class="report-main" @click="handleReportClick">
            <!-- IR 渲染模式 -->
            <IrRenderer
              v-if="renderMode === 'ir' && documentIR"
              :document="documentIR"
              :on-cite-click="handleCiteClick"
            />
            <!-- Markdown 渲染模式 -->
            <div v-else class="report-content" v-html="renderedHtml" />
          </main>
        </div>

        <!-- 移动端目录 Drawer -->
        <n-drawer v-model:show="showTocDrawer" placement="left" :width="280">
          <n-drawer-content title="目录导航">
            <div
              v-for="item in tocItems"
              :key="item.id"
              class="toc-item drawer-toc-item"
              :class="{ active: activeSection === item.id }"
              :style="{ paddingLeft: `${(item.level - 1) * 16 + 12}px` }"
              :title="item.title"
              @click="scrollToSection(item.id)"
            >
              {{ item.title }}
            </div>
          </n-drawer-content>
        </n-drawer>

        <!-- 引用来源预览 Drawer -->
        <n-drawer v-model:show="showCitePreview" placement="right" :width="360">
          <n-drawer-content title="引用来源">
            <div class="cite-preview">
              <n-text depth="3">来源标识: {{ citeSourceId }}</n-text>
              <n-divider />
              <template v-if="documentIR?.source_map?.[citeSourceId]">
                <div v-for="(attr, i) in documentIR.source_map[citeSourceId]" :key="i" class="cite-attr">
                  <div>文献 ID: {{ attr.doc_id }}</div>
                  <div v-if="attr.page_start">页码: {{ attr.page_start }}-{{ attr.page_end }}</div>
                  <div>置信度: {{ (attr.confidence * 100).toFixed(0) }}%</div>
                </div>
              </template>
              <n-empty v-else description="暂无来源详情" />
            </div>
          </n-drawer-content>
        </n-drawer>

        <!-- 知识库文档预览 Drawer -->
        <n-drawer v-model:show="showKbDrawer" placement="right" :width="480">
          <n-drawer-content :title="kbDocTitle">
            <n-spin :show="kbDocLoading">
              <div class="kb-doc-preview">
                <div v-if="kbDocContent" class="kb-doc-text" v-html="kbDocContent.replace(/\n/g, '<br>')"></div>
                <n-empty v-else description="暂无内容" />
              </div>
            </n-spin>
          </n-drawer-content>
        </n-drawer>
      </template>

      <n-result v-else-if="task && task.status === 'failed'" status="error" :title="task.error_message || '生成失败'">
        <template #footer>
          <n-button @click="goBack">返回</n-button>
        </template>
      </n-result>
    </n-spin>
  </div>
</template>

<style scoped>
.report-viewer {
  height: 100vh;
  display: flex;
  flex-direction: column;
}

.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 24px;
  border-bottom: 1px solid #eee;
  background: #fff;
  position: sticky;
  top: 0;
  z-index: 10;
}

.viewer-layout {
  display: flex;
  flex: 1;
  overflow: hidden;
}

.toc-sidebar {
  width: 240px;
  border-right: 1px solid #eee;
  padding: 16px 0;
  overflow-y: auto;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
}

.toc-header {
  padding: 0 16px 12px;
  font-weight: 600;
  font-size: 14px;
  border-bottom: 1px solid #f0f0f0;
  margin-bottom: 8px;
}

.toc-list {
  flex: 1;
  overflow-y: auto;
}

.toc-item {
  padding: 6px 12px;
  font-size: 13px;
  cursor: pointer;
  color: #666;
  border-left: 2px solid transparent;
  transition: all 0.2s;
  /* 长标题截断 */
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.toc-item:hover {
  color: #333;
  background: #f5f5f5;
}

.toc-item.active {
  color: #18a058;
  border-left-color: #18a058;
  background: #f0faf4;
}

.report-meta {
  padding: 12px 16px;
  border-top: 1px solid #f0f0f0;
  font-size: 12px;
  color: #999;
  line-height: 1.8;
}

.report-main {
  flex: 1;
  overflow-y: auto;
  padding: 32px 48px;
}

.report-content {
  max-width: 800px;
  margin: 0 auto;
  font-size: 15px;
  line-height: 1.8;
  color: #333;
}

.report-content :deep(h1) {
  font-size: 24px;
  margin: 32px 0 16px;
  padding-bottom: 8px;
  border-bottom: 2px solid #18a058;
}

.report-content :deep(h2) {
  font-size: 20px;
  margin: 28px 0 12px;
}

.report-content :deep(h3) {
  font-size: 17px;
  margin: 24px 0 8px;
}

.report-content :deep(p) {
  margin: 12px 0;
  text-align: justify;
}

.report-content :deep(blockquote) {
  border-left: 4px solid #18a058;
  padding: 8px 16px;
  margin: 16px 0;
  background: #f9f9f9;
  color: #555;
}

.report-content :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 16px 0;
}

.report-content :deep(th),
.report-content :deep(td) {
  border: 1px solid #ddd;
  padding: 8px 12px;
  text-align: left;
}

.report-content :deep(th) {
  background: #f5f5f5;
  font-weight: 600;
}

.toc-toggle {
  display: none;
}

.read-progress {
  height: 3px;
  background: linear-gradient(90deg, #18a058, #36ad6a);
  transition: width 0.1s;
  position: sticky;
  top: 0;
  z-index: 11;
}

.search-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 24px;
  background: #fafafa;
  border-bottom: 1px solid #eee;
}

/* 关键词标签样式 */
.report-content :deep(.keywords) {
  margin: 16px 0 24px;
  padding: 12px 16px;
  background: #f8fafb;
  border-radius: 8px;
  border: 1px solid #e8eef3;
  font-size: 14px;
  line-height: 1.8;
  color: #555;
}
.report-content :deep(.kw-label) {
  display: inline-block;
  font-weight: 600;
  color: #18a058;
  margin-right: 8px;
}

/* 摘要区域美化 — 紧跟 h1 之后的 h2"摘要" */
.report-content :deep(h2:first-of-type) {
  font-size: 18px;
  color: #444;
}

/* 行内引用 — 可点击链接 */
.report-content :deep(.cite-inline) {
  color: #18a058;
  font-weight: 600;
  text-decoration: none;
  cursor: pointer;
  position: relative;
}
.report-content :deep(.cite-inline:hover) {
  text-decoration: underline;
  text-underline-offset: 2px;
}
/* CSS tooltip — hover 时显示文献信息 */
.report-content :deep(.cite-tooltip)::after {
  content: attr(data-tip);
  position: absolute;
  bottom: calc(100% + 8px);
  left: 50%;
  transform: translateX(-50%);
  background: #333;
  color: #fff;
  padding: 8px 12px;
  border-radius: 8px;
  font-size: 12px;
  font-weight: 400;
  line-height: 1.5;
  white-space: normal;
  width: max-content;
  max-width: 320px;
  opacity: 0;
  visibility: hidden;
  transition: opacity 0.15s, visibility 0.15s;
  pointer-events: none;
  z-index: 100;
  box-shadow: 0 4px 12px rgba(0,0,0,0.15);
}
.report-content :deep(.cite-tooltip)::before {
  content: '';
  position: absolute;
  bottom: calc(100% + 2px);
  left: 50%;
  transform: translateX(-50%);
  border: 6px solid transparent;
  border-top-color: #333;
  opacity: 0;
  visibility: hidden;
  transition: opacity 0.15s, visibility 0.15s;
  pointer-events: none;
  z-index: 100;
}
.report-content :deep(.cite-tooltip:hover)::after,
.report-content :deep(.cite-tooltip:hover)::before {
  opacity: 1;
  visibility: visible;
}

/* 参考文献列表条目 */
.report-content :deep(.ref-entry) {
  margin: 6px 0;
  padding: 6px 12px 6px 2.5em;
  text-indent: -2.2em;
  font-size: 14px;
  line-height: 1.6;
  border-radius: 6px;
  transition: background 0.15s;
}
.report-content :deep(.ref-entry:target) {
  background: #fff8e1;
}
.report-content :deep(.ref-entry:hover) {
  background: #f8f9fa;
}
.report-content :deep(.ref-num) {
  color: #18a058;
  font-weight: 700;
}
.report-content :deep(.ref-link) {
  display: inline-block;
  margin-left: 8px;
  font-size: 12px;
  color: #2080f0;
  text-decoration: none;
  padding: 1px 6px;
  border: 1px solid #d0e0f0;
  border-radius: 4px;
  transition: all 0.15s;
}
.report-content :deep(.ref-link:hover) {
  background: #eef4ff;
  border-color: #2080f0;
}
.report-content :deep(.ref-source-tag) {
  display: inline-block;
  margin-left: 8px;
  font-size: 11px;
  color: #999;
  padding: 1px 6px;
  background: #f5f5f5;
  border-radius: 4px;
}
.report-content :deep(.ref-kb-btn) {
  display: inline-block;
  margin-left: 8px;
  font-size: 12px;
  color: #18a058;
  padding: 2px 10px;
  border: 1px solid #c8e6d0;
  border-radius: 4px;
  background: #f0faf4;
  cursor: pointer;
  transition: all 0.15s;
  font-family: inherit;
}
.report-content :deep(.ref-kb-btn:hover) {
  background: #e0f5e8;
  border-color: #18a058;
}

/* 知识库文档预览 */
.kb-doc-preview {
  min-height: 200px;
}
.kb-doc-text {
  font-size: 14px;
  line-height: 1.8;
  color: #333;
  word-break: break-word;
}
.report-content :deep(.cite-highlight) {
  background: #fff3b0 !important;
  padding: 2px 4px;
  border-radius: 4px;
  transition: background 0.3s;
}

/* 正文有序列表（目录链接）美化 */
.report-content :deep(ol) {
  padding-left: 1.5em;
  margin: 12px 0;
}
.report-content :deep(ol li) {
  margin: 4px 0;
}
.report-content :deep(a) {
  color: #18a058;
  text-decoration: none;
}
.report-content :deep(a:hover) {
  text-decoration: underline;
}

/* Drawer 内目录也支持截断 */
.drawer-toc-item {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

:deep(.search-hl) {
  background: #fff3b0;
  padding: 1px 2px;
  border-radius: 2px;
}

@media (max-width: 768px) {
  .toc-sidebar {
    display: none;
  }
  .toc-toggle {
    display: inline-flex !important;
  }
  .report-main {
    padding: 16px;
  }
}
</style>
