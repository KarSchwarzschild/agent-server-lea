<script setup lang="ts">
/**
 * 文献检索页面 - 响应式版本
 * PC端：左侧历史/收藏 + 右侧搜索结果
 * 移动端：全屏搜索 + 底部筛选抽屉
 */
import { ref, reactive, onMounted } from 'vue'
import {
  NCard,
  NInput,
  NButton,
  NSpace,
  NSelect,
  NInputNumber,
  NTag,
  NSpin,
  NEmpty,
  NIcon,
  NLayout,
  NLayoutSider,
  NList,
  NListItem,
  NPopconfirm,
  NTooltip,
  NDropdown,
  NTabs,
  NTabPane,
  NScrollbar,
  NModal,
  NDescriptions,
  NDescriptionsItem,
  useMessage,
} from 'naive-ui'
import {
  SearchOutline,
  TrashOutline,
  HeartOutline,
  Heart,
  DownloadOutline,
  ChatboxOutline,
  DocumentTextOutline,
  FunnelOutline,
  TimeOutline,
  EllipsisVertical,
} from '@vicons/ionicons5'
import {
  searchDocuments,
  getCategories,
  getSearchHistory,
  deleteSearchHistory,
  clearSearchHistory,
  addFavorite,
  removeFavorite,
  getFavorites,
  exportSearchResults,
  exportFavorites,
  type SearchResult,
  type Categories,
  type SearchHistoryItem,
  type FavoriteItem,
} from '../api/search'
import { useRouter, useRoute } from 'vue-router'
import { useResponsive } from '../composables/useResponsive'
import MobileFilterDrawer from '../components/MobileFilterDrawer.vue'
import MobileHistoryDrawer from '../components/MobileHistoryDrawer.vue'

const router = useRouter()
const route = useRoute()
const message = useMessage()
const { isMobile } = useResponsive()

// 移动端筛选抽屉
const showFilterDrawer = ref(false)
// 移动端历史/收藏抽屉
const showHistoryDrawer = ref(false)
// 移动端操作菜单
const showActionSheet = ref(false)
const actionSheetTarget = ref<SearchResult | null>(null)

// 搜索状态
const searchQuery = ref('')
const searchScope = ref<string>('all')
const loading = ref(false)
const searched = ref(false)
const total = ref(0)
const results = ref<SearchResult[]>([])
const currentSearchId = ref<string | null>(null)

// 分类数据
const categories = ref<Categories>({
  categories: [],
  sub_categories: [],
  methodologies: []
})

// 筛选条件
const filters = reactive({
  category: null as string | null,
  methodology: null as string | null,
  year_from: null as number | null,
  year_to: null as number | null
})

// 分类选项
const categoryOptions = ref<Array<{ label: string; value: string }>>([])
const methodologyOptions = ref<Array<{ label: string; value: string }>>([])

// 历史和收藏
const historyList = ref<SearchHistoryItem[]>([])
const favoritesList = ref<FavoriteItem[]>([])
const historyLoading = ref(false)
const favoritesLoading = ref(false)
const activeTab = ref('history')

// 侧边栏折叠
const siderCollapsed = ref(false)

// 文献详情弹窗
const showDetailModal = ref(false)
const selectedDocument = ref<SearchResult | null>(null)

// 打开文献详情
const openDocumentDetail = (item: SearchResult) => {
  selectedDocument.value = item
  showDetailModal.value = true
}

// 加载分类选项
const loadCategories = async () => {
  try {
    const res = await getCategories()
    categories.value = res

    const predefinedCats = ['终身教育', '成人教育', '远程教育', '职业教育', '理论研究', '政策研究', '实践研究', '方法研究']
    const allCats = [...new Set([...res.categories, ...predefinedCats])]
    categoryOptions.value = allCats.map(c => ({ label: c, value: c }))

    const predefinedMethods = ['实证研究', '理论研究', '文献综述', '案例研究', '调查研究', '实验研究']
    const allMethods = [...new Set([...res.methodologies, ...predefinedMethods])]
    methodologyOptions.value = allMethods.map(m => ({ label: m, value: m }))
  } catch (error) {
    console.error('Failed to load categories:', error)
  }
}

// 加载检索历史
const loadHistory = async () => {
  historyLoading.value = true
  try {
    const res = await getSearchHistory({ page: 1, page_size: 20 })
    historyList.value = res.histories
  } catch (error) {
    console.error('Failed to load history:', error)
  } finally {
    historyLoading.value = false
  }
}

// 加载收藏列表
const loadFavorites = async () => {
  favoritesLoading.value = true
  try {
    const res = await getFavorites({ page: 1, page_size: 50 })
    favoritesList.value = res.favorites
  } catch (error) {
    console.error('Failed to load favorites:', error)
  } finally {
    favoritesLoading.value = false
  }
}

// 执行搜索
const handleSearch = async () => {
  if (!searchQuery.value.trim()) return

  loading.value = true
  searched.value = true

  try {
    const res = await searchDocuments({
      query: searchQuery.value,
      category: filters.category || undefined,
      methodology: filters.methodology || undefined,
      year_from: filters.year_from || undefined,
      year_to: filters.year_to || undefined,
      top_k: 20,
      save_history: true,
      scope: searchScope.value,
    })

    total.value = res.total
    results.value = res.results
    currentSearchId.value = res.search_id

    // 刷新历史列表
    loadHistory()
  } catch (error) {
    console.error('Search failed:', error)
    results.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

// 从历史恢复搜索
const restoreFromHistory = (item: SearchHistoryItem) => {
  searchQuery.value = item.query
  filters.category = item.category
  filters.methodology = item.methodology
  filters.year_from = item.year_from
  filters.year_to = item.year_to
  handleSearch()
}

// 删除单条历史
const handleDeleteHistory = async (searchId: string) => {
  try {
    await deleteSearchHistory(searchId)
    historyList.value = historyList.value.filter(h => h.search_id !== searchId)
    message.success('已删除')
  } catch (error) {
    message.error('删除失败')
  }
}

// 清空所有历史
const handleClearHistory = async () => {
  try {
    await clearSearchHistory()
    historyList.value = []
    message.success('已清空所有历史')
  } catch (error) {
    message.error('清空失败')
  }
}

// 收藏/取消收藏
const toggleFavorite = async (item: SearchResult) => {
  try {
    if (item.is_favorited) {
      await removeFavorite(item.doc_id)
      item.is_favorited = false
      message.success('已取消收藏')
    } else {
      await addFavorite({ doc_id: item.doc_id })
      item.is_favorited = true
      message.success('已收藏')
    }
    // 刷新收藏列表
    loadFavorites()
  } catch (error) {
    message.error('操作失败')
  }
}

// 导出当前结果
const handleExport = (format: 'csv' | 'json') => {
  if (!currentSearchId.value) {
    message.warning('请先执行搜索')
    return
  }
  exportSearchResults(currentSearchId.value, format)
  message.success(`正在导出为 ${format.toUpperCase()} 格式`)
}

// 导出收藏
const handleExportFavorites = (format: 'csv' | 'json') => {
  exportFavorites(format)
  message.success(`正在导出收藏为 ${format.toUpperCase()} 格式`)
}

// 下载文献原文
const downloadDocument = async (docId: string, filename?: string | null) => {
  try {
    const baseURL = import.meta.env.VITE_API_BASE_URL || ''
    const token = localStorage.getItem('token')
    const response = await fetch(
      `${baseURL}/api/user/search/document/${docId}/download`,
      {
        method: 'GET',
        headers: {
          'Authorization': `Bearer ${token}`
        }
      }
    )

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}))
      throw new Error(errorData.detail || `下载失败: HTTP ${response.status}`)
    }

    // 下载文件
    const blob = await response.blob()
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename || `document_${docId}`
    document.body.appendChild(a)
    a.click()
    window.URL.revokeObjectURL(url)
    document.body.removeChild(a)

    message.success('下载成功')
  } catch (error: any) {
    console.error('下载失败:', error)
    message.error(error.message || '下载失败')
  }
}

// 基于结果提问（跳转到Chat页面）
const askAboutResult = (item: SearchResult) => {
  // 构建完整的上下文信息
  const context = {
    title: item.title || item.filename || '未命名文献',
    authors: item.authors || '',
    abstract: item.abstract || '',
    category: item.category || '',
    year: item.year || ''
  }
  const question = `请帮我分析这篇文献的主要观点和研究方法：\n\n标题：${context.title}${context.authors ? `\n作者：${context.authors}` : ''}${context.year ? `\n年份：${context.year}` : ''}${context.abstract ? `\n摘要：${context.abstract}` : ''}`

  router.push({
    path: '/qa/super-agent',
    query: {
      q: question,
      docTitle: context.title,
      docId: item.doc_id
    }
  })
}

// 基于结果生成综述（跳转到深度综述向导）
// 优先使用搜索关键词作为主题（因为文献 title/filename 可能是 UUID）
const generateReviewFromResult = (item: SearchResult) => {
  const isUuidLike = (s: string) => /^[0-9a-f]{8}-[0-9a-f]{4}-/.test(s)
  const title = item.title && !isUuidLike(item.title) ? item.title : null
  const topic = title || searchQuery.value || ''
  router.push({
    path: '/review/new',
    query: { topic }
  })
}

// 基于当前搜索结果生成综述
const generateReviewFromSearch = () => {
  router.push({
    path: '/review/new',
    query: {
      topic: searchQuery.value,
    }
  })
}

// 基于当前搜索结果提问
const askAboutSearch = () => {
  const question = `关于"${searchQuery.value}"这个主题，基于检索到的${total.value}篇文献，请帮我总结相关研究的主要观点和发展趋势。`
  router.push({
    path: '/qa/super-agent',
    query: {
      q: question,
      searchQuery: searchQuery.value,
      category: filters.category || undefined
    }
  })
}

// 重置筛选
const resetFilters = () => {
  filters.category = null
  filters.methodology = null
  filters.year_from = null
  filters.year_to = null
}

// 移动端：打开操作菜单
const openActionSheet = (item: SearchResult) => {
  actionSheetTarget.value = item
  showActionSheet.value = true
}

// 移动端：处理操作菜单选择
const handleActionSheetSelect = (action: string) => {
  if (!actionSheetTarget.value) return
  const item = actionSheetTarget.value

  switch (action) {
    case 'ask':
      askAboutResult(item)
      break
    case 'review':
      generateReviewFromResult(item)
      break
    case 'download':
      downloadDocument(item.doc_id, item.filename)
      break
  }
  showActionSheet.value = false
  actionSheetTarget.value = null
}

// 移动端：从历史/收藏抽屉恢复搜索
const handleRestoreFromDrawer = (item: SearchHistoryItem) => {
  restoreFromHistory(item)
}

// 移动端：从抽屉删除历史
const handleDeleteFromDrawer = async (searchId: string) => {
  await handleDeleteHistory(searchId)
}

// 移动端：从抽屉清空历史
const handleClearFromDrawer = async () => {
  await handleClearHistory()
}

// 移动端：从抽屉打开收藏文献
const handleOpenFavoriteFromDrawer = (item: FavoriteItem) => {
  // 将FavoriteItem转换为SearchResult格式以打开详情
  const doc: SearchResult = {
    doc_id: item.doc_id,
    title: item.title,
    authors: item.authors,
    year: item.year,
    category: item.category,
    sub_category: null,
    methodology: null,
    keywords: null,
    abstract: null,
    matched_content: null,
    relevance_score: 0,
    filename: null,
    file_path: null,
    file_url: null,
    is_favorited: true
  }
  selectedDocument.value = doc
  showDetailModal.value = true
}

// 移动端：从抽屉移除收藏
const handleRemoveFavoriteFromDrawer = async (item: FavoriteItem) => {
  try {
    await removeFavorite(item.doc_id)
    loadFavorites()
    message.success('已取消收藏')
  } catch (error) {
    message.error('操作失败')
  }
}

// 格式化时间
const formatTime = (isoString: string) => {
  if (!isoString) return ''
  const date = new Date(isoString)
  const now = new Date()
  const diff = now.getTime() - date.getTime()
  const minutes = Math.floor(diff / 60000)
  const hours = Math.floor(diff / 3600000)
  const days = Math.floor(diff / 86400000)

  if (minutes < 60) return `${minutes}分钟前`
  if (hours < 24) return `${hours}小时前`
  if (days < 7) return `${days}天前`
  return date.toLocaleDateString('zh-CN')
}

// 导出下拉菜单选项
const exportOptions = [
  { label: '导出为 CSV', key: 'csv' },
  { label: '导出为 JSON', key: 'json' }
]

const handleResultAction = (key: string, item: SearchResult) => {
  switch (key) {
    case 'favorite':
      toggleFavorite(item)
      break
    case 'ask':
      askAboutResult(item)
      break
    case 'review':
      generateReviewFromResult(item)
      break
  }
}

onMounted(() => {
  loadCategories()
  loadHistory()
  loadFavorites()
  // 从 URL 读取 scope 参数（如从知识库页面跳转过来）
  const urlScope = route.query.scope as string
  if (urlScope && ['all', 'public', 'personal'].includes(urlScope)) {
    searchScope.value = urlScope
  }
})
</script>

<template>
  <div class="search-page" :class="{ 'search-page-mobile': isMobile }">
    <!-- ===== PC端布局 ===== -->
    <n-layout v-if="!isMobile" has-sider style="height: 100%">
      <!-- 左侧边栏 - 历史记录 -->
      <n-layout-sider
        :collapsed="siderCollapsed"
        :collapsed-width="0"
        :width="280"
        show-trigger="bar"
        collapse-mode="width"
        bordered
        @update:collapsed="siderCollapsed = $event"
      >
        <div class="sider-content">
          <n-tabs v-model:value="activeTab" type="line" size="small">
            <n-tab-pane name="history" tab="检索历史">
              <div class="history-header">
                <span class="history-count">共 {{ historyList.length }} 条</span>
                <n-popconfirm @positive-click="handleClearHistory">
                  <template #trigger>
                    <n-button quaternary size="tiny" :disabled="historyList.length === 0">
                      清空
                    </n-button>
                  </template>
                  确定清空所有检索历史吗？
                </n-popconfirm>
              </div>

              <n-scrollbar style="max-height: calc(100vh - 200px)">
                <n-spin :show="historyLoading">
                  <n-empty v-if="historyList.length === 0" description="暂无检索历史" size="small" />
                  <n-list v-else hoverable clickable>
                    <n-list-item
                      v-for="item in historyList"
                      :key="item.search_id"
                      @click="restoreFromHistory(item)"
                    >
                      <div class="history-item">
                        <div class="history-query">{{ item.query }}</div>
                        <div class="history-meta">
                          <span>{{ item.results_count }} 条结果</span>
                          <span>{{ formatTime(item.created_at) }}</span>
                        </div>
                        <div v-if="item.category || item.methodology" class="history-tags">
                          <n-tag v-if="item.category" size="tiny">{{ item.category }}</n-tag>
                          <n-tag v-if="item.methodology" size="tiny" type="warning">{{ item.methodology }}</n-tag>
                        </div>
                      </div>
                      <template #suffix>
                        <n-button
                          quaternary
                          circle
                          size="tiny"
                          @click.stop="handleDeleteHistory(item.search_id)"
                        >
                          <template #icon>
                            <n-icon :component="TrashOutline" />
                          </template>
                        </n-button>
                      </template>
                    </n-list-item>
                  </n-list>
                </n-spin>
              </n-scrollbar>
            </n-tab-pane>

            <n-tab-pane name="favorites" tab="我的收藏">
              <div class="history-header">
                <span class="history-count">共 {{ favoritesList.length }} 篇</span>
                <n-dropdown :options="exportOptions" @select="(k: string) => handleExportFavorites(k as 'csv' | 'json')">
                  <n-button quaternary size="tiny" :disabled="favoritesList.length === 0">
                    导出
                  </n-button>
                </n-dropdown>
              </div>

              <n-scrollbar style="max-height: calc(100vh - 200px)">
                <n-spin :show="favoritesLoading">
                  <n-empty v-if="favoritesList.length === 0" description="暂无收藏" size="small" />
                  <n-list v-else hoverable>
                    <n-list-item v-for="item in favoritesList" :key="item.id">
                      <div class="favorite-item">
                        <div class="favorite-title">{{ item.title || '未命名文献' }}</div>
                        <div class="favorite-meta">
                          <span v-if="item.authors">{{ item.authors }}</span>
                          <span v-if="item.year">{{ item.year }}年</span>
                        </div>
                        <n-tag v-if="item.category" size="tiny">{{ item.category }}</n-tag>
                      </div>
                      <template #suffix>
                        <n-button
                          quaternary
                          circle
                          size="tiny"
                          @click="removeFavorite(item.doc_id).then(() => loadFavorites())"
                        >
                          <template #icon>
                            <n-icon :component="TrashOutline" />
                          </template>
                        </n-button>
                      </template>
                    </n-list-item>
                  </n-list>
                </n-spin>
              </n-scrollbar>
            </n-tab-pane>
          </n-tabs>
        </div>
      </n-layout-sider>

      <!-- 主内容区 -->
      <n-layout>
        <div class="main-content">
          <!-- 搜索区域 -->
          <n-card class="search-card">
            <n-space vertical size="large">
              <!-- 搜索框 -->
              <n-space>
                <n-input
                  v-model:value="searchQuery"
                  placeholder="输入检索关键词，如：终身教育、成人学习、远程教育..."
                  style="width: 500px"
                  size="large"
                  clearable
                  @keyup.enter="handleSearch"
                >
                  <template #prefix>
                    <n-icon :component="SearchOutline" />
                  </template>
                </n-input>
                <n-button
                  type="primary"
                  size="large"
                  :loading="loading"
                  :disabled="!searchQuery.trim()"
                  @click="handleSearch"
                >
                  检索
                </n-button>
                <!-- 导出按钮 -->
                <n-dropdown
                  v-if="currentSearchId"
                  :options="exportOptions"
                  @select="(k: string) => handleExport(k as 'csv' | 'json')"
                >
                  <n-button size="large">
                    <template #icon>
                      <n-icon :component="DownloadOutline" />
                    </template>
                    导出
                  </n-button>
                </n-dropdown>
              </n-space>

              <!-- 筛选条件 -->
              <n-space align="center">
                <span class="filter-label">范围:</span>
                <n-select
                  v-model:value="searchScope"
                  :options="[
                    { label: '全部', value: 'all' },
                    { label: '公共知识库', value: 'public' },
                    { label: '个人知识库', value: 'personal' },
                  ]"
                  style="width: 130px"
                />

                <span class="filter-label">分类:</span>
                <n-select
                  v-model:value="filters.category"
                  :options="categoryOptions"
                  placeholder="全部分类"
                  clearable
                  style="width: 140px"
                />

                <span class="filter-label">研究方法:</span>
                <n-select
                  v-model:value="filters.methodology"
                  :options="methodologyOptions"
                  placeholder="全部方法"
                  clearable
                  style="width: 140px"
                />

                <span class="filter-label">年份:</span>
                <n-input-number
                  v-model:value="filters.year_from"
                  placeholder="从"
                  :show-button="false"
                  style="width: 80px"
                />
                <span>-</span>
                <n-input-number
                  v-model:value="filters.year_to"
                  placeholder="到"
                  :show-button="false"
                  style="width: 80px"
                />

                <n-button quaternary @click="resetFilters">重置筛选</n-button>
              </n-space>
            </n-space>
          </n-card>

          <!-- 搜索结果 -->
          <div class="results-section">
            <!-- 加载中 -->
            <div v-if="loading" class="loading-state">
              <n-spin size="large" />
              <p>搜索中...</p>
            </div>

            <!-- 未搜索 -->
            <n-empty v-else-if="!searched" description="输入关键词开始检索文献" class="empty-state">
              <template #icon>
                <n-icon :component="SearchOutline" size="64" />
              </template>
            </n-empty>

            <!-- 无结果 -->
            <n-empty v-else-if="results.length === 0" description="未找到相关文献" class="empty-state" />

            <!-- 结果列表 -->
            <div v-else class="results-list">
              <div class="results-header">
                <span class="results-count">找到 {{ total }} 篇相关文献</span>
                <n-space>
                  <n-button size="small" type="primary" @click="askAboutSearch">
                    <template #icon>
                      <n-icon :component="ChatboxOutline" />
                    </template>
                    基于结果提问
                  </n-button>
                  <n-button size="small" @click="generateReviewFromSearch">
                    <template #icon>
                      <n-icon :component="DocumentTextOutline" />
                    </template>
                    生成综述
                  </n-button>
                </n-space>
              </div>

              <n-card
                v-for="item in results"
                :key="item.doc_id"
                class="result-card"
                hoverable
                @click="openDocumentDetail(item)"
              >
                <div class="result-header">
                  <h3 class="result-title">
                    <n-tag
                      v-if="item.source_type === 'personal'"
                      type="success"
                      size="small"
                      style="margin-right: 6px; vertical-align: middle;"
                    >
                      个人
                    </n-tag>
                    <n-tag
                      v-else-if="item.source_type === 'public'"
                      type="info"
                      size="small"
                      style="margin-right: 6px; vertical-align: middle;"
                    >
                      公共
                    </n-tag>
                    {{ item.title || item.filename || '未命名文献' }}
                  </h3>
                  <n-space>
                    <n-tag
                      v-if="item.relevance_score > 0"
                      type="info"
                      size="small"
                      round
                    >
                      相关度: {{ (item.relevance_score * 100).toFixed(0) }}%
                    </n-tag>
                    <!-- 收藏按钮 -->
                    <n-tooltip>
                      <template #trigger>
                        <n-button
                          quaternary
                          circle
                          size="small"
                          :type="item.is_favorited ? 'error' : 'default'"
                          @click.stop="toggleFavorite(item)"
                        >
                          <template #icon>
                            <n-icon :component="item.is_favorited ? Heart : HeartOutline" />
                          </template>
                        </n-button>
                      </template>
                      {{ item.is_favorited ? '取消收藏' : '收藏' }}
                    </n-tooltip>
                    <!-- 更多操作 -->
                    <n-dropdown
                      :options="[
                        { label: '基于此提问', key: 'ask' },
                        { label: '生成综述', key: 'review' }
                      ]"
                      @select="(k: string) => handleResultAction(k, item)"
                    >
                      <n-button quaternary circle size="small" @click.stop>
                        <template #icon>
                          <n-icon :component="ChatboxOutline" />
                        </template>
                      </n-button>
                    </n-dropdown>
                  </n-space>
                </div>

                <n-space size="small" style="margin-bottom: 12px">
                  <n-tag v-if="item.category" type="primary" size="small">
                    {{ item.category }}
                  </n-tag>
                  <n-tag v-if="item.sub_category" type="success" size="small">
                    {{ item.sub_category }}
                  </n-tag>
                  <n-tag v-if="item.methodology" type="warning" size="small">
                    {{ item.methodology }}
                  </n-tag>
                  <n-tag v-if="item.year" size="small">
                    {{ item.year }}年
                  </n-tag>
                </n-space>

                <p v-if="item.authors" class="result-authors">
                  作者: {{ item.authors }}
                </p>

                <p v-if="item.abstract" class="result-abstract">
                  {{ item.abstract }}
                </p>

                <div v-if="item.matched_content" class="matched-content">
                  <span class="matched-label">匹配内容: </span>{{ item.matched_content }}
                </div>

                <div v-if="item.keywords" class="result-keywords">
                  关键词: {{ item.keywords }}
                </div>
              </n-card>
            </div>
          </div>
        </div>
      </n-layout>
    </n-layout>

    <!-- ===== 移动端布局 ===== -->
    <template v-else>
      <div class="mobile-search-container">
        <!-- 搜索头部 -->
        <div class="mobile-search-header">
          <!-- 历史入口 -->
          <n-button
            quaternary
            class="history-btn"
            @click="showHistoryDrawer = true"
          >
            <n-icon :size="20">
              <TimeOutline />
            </n-icon>
          </n-button>
          <div class="mobile-search-input">
            <n-input
              v-model:value="searchQuery"
              placeholder="搜索文献..."
              size="large"
              clearable
              @keyup.enter="handleSearch"
            >
              <template #prefix>
                <n-icon :component="SearchOutline" />
              </template>
            </n-input>
          </div>
          <n-button
            type="primary"
            :loading="loading"
            :disabled="!searchQuery.trim()"
            @click="handleSearch"
          >
            搜索
          </n-button>
        </div>

        <!-- 筛选Chip区 -->
        <div class="mobile-filter-chips">
          <n-select
            v-model:value="searchScope"
            :options="[
              { label: '全部', value: 'all' },
              { label: '公共库', value: 'public' },
              { label: '个人库', value: 'personal' },
            ]"
            size="small"
            style="width: 90px"
          />
          <div
            class="filter-chip"
            :class="{ active: filters.category || filters.methodology || filters.year_from || filters.year_to }"
            @click="showFilterDrawer = true"
          >
            <n-icon :component="FunnelOutline" :size="16" />
            <span>筛选</span>
          </div>
          <n-tag
            v-if="filters.category"
            closable
            size="small"
            @close="filters.category = null"
          >
            {{ filters.category }}
          </n-tag>
          <n-tag
            v-if="filters.methodology"
            closable
            size="small"
            type="warning"
            @close="filters.methodology = null"
          >
            {{ filters.methodology }}
          </n-tag>
          <n-tag
            v-if="filters.year_from || filters.year_to"
            closable
            size="small"
            @close="filters.year_from = null; filters.year_to = null"
          >
            {{ filters.year_from || '?' }}-{{ filters.year_to || '?' }}
          </n-tag>
        </div>

        <!-- 结果区域 -->
        <div class="mobile-results-container">
          <!-- 加载中 -->
          <div v-if="loading" class="loading-state">
            <n-spin size="large" />
            <p>搜索中...</p>
          </div>

          <!-- 未搜索 -->
          <n-empty v-else-if="!searched" description="输入关键词开始检索文献" class="empty-state">
            <template #icon>
              <n-icon :component="SearchOutline" size="48" />
            </template>
          </n-empty>

          <!-- 无结果 -->
          <n-empty v-else-if="results.length === 0" description="未找到相关文献" class="empty-state" />

          <!-- 结果列表 -->
          <template v-else>
            <!-- 结果操作栏 -->
            <div class="mobile-results-header">
              <span>{{ total }} 篇结果</span>
              <div class="mobile-results-actions">
                <n-button text type="primary" size="small" @click="askAboutSearch">
                  <n-icon :size="16" style="margin-right: 2px;">
                    <ChatboxOutline />
                  </n-icon>
                  提问
                </n-button>
                <n-button text type="primary" size="small" @click="generateReviewFromSearch">
                  <n-icon :size="16" style="margin-right: 2px;">
                    <DocumentTextOutline />
                  </n-icon>
                  综述
                </n-button>
                <n-dropdown
                  :options="exportOptions"
                  @select="(k: string) => handleExport(k as 'csv' | 'json')"
                >
                  <n-button text type="primary" size="small">
                    <n-icon :size="16" style="margin-right: 2px;">
                      <DownloadOutline />
                    </n-icon>
                    导出
                  </n-button>
                </n-dropdown>
              </div>
            </div>

            <div class="mobile-results-list">
              <div
                v-for="item in results"
                :key="item.doc_id"
                class="mobile-result-card"
                @click="openDocumentDetail(item)"
              >
                <div class="mobile-result-title">
                  <n-tag
                    v-if="item.source_type === 'personal'"
                    type="success"
                    size="tiny"
                    style="margin-right: 4px; vertical-align: middle;"
                  >
                    个人
                  </n-tag>
                  <n-tag
                    v-else-if="item.source_type === 'public'"
                    type="info"
                    size="tiny"
                    style="margin-right: 4px; vertical-align: middle;"
                  >
                    公共
                  </n-tag>
                  {{ item.title || item.filename || '未命名文献' }}
                </div>
                <div class="mobile-result-meta">
                  <span v-if="item.relevance_score > 0" class="relevance">
                    {{ (item.relevance_score * 100).toFixed(0) }}%
                  </span>
                  <n-tag v-if="item.category" size="tiny">{{ item.category }}</n-tag>
                  <div class="mobile-result-btns">
                    <n-button
                      text
                      :type="item.is_favorited ? 'error' : 'default'"
                      @click.stop="toggleFavorite(item)"
                    >
                      <n-icon :component="item.is_favorited ? Heart : HeartOutline" />
                    </n-button>
                    <n-button
                      text
                      @click.stop="openActionSheet(item)"
                    >
                      <n-icon :component="EllipsisVertical" />
                    </n-button>
                  </div>
                </div>
                <div v-if="item.authors" class="mobile-result-authors">
                  {{ item.authors }} · {{ item.year || '' }}
                </div>
                <p v-if="item.abstract" class="mobile-result-abstract">
                  {{ item.abstract }}
                </p>
              </div>
            </div>
          </template>
        </div>
      </div>

      <!-- 筛选抽屉 -->
      <MobileFilterDrawer
        v-model:show="showFilterDrawer"
        :filters="filters"
        :category-options="categoryOptions"
        :methodology-options="methodologyOptions"
        @update:filters="(f) => Object.assign(filters, f)"
        @confirm="handleSearch"
        @reset="resetFilters"
      />

      <!-- 历史/收藏抽屉 -->
      <MobileHistoryDrawer
        v-model:show="showHistoryDrawer"
        :history-list="historyList"
        :favorites-list="favoritesList"
        :history-loading="historyLoading"
        :favorites-loading="favoritesLoading"
        @restore-history="handleRestoreFromDrawer"
        @delete-history="handleDeleteFromDrawer"
        @clear-history="handleClearFromDrawer"
        @open-favorite="handleOpenFavoriteFromDrawer"
        @remove-favorite="handleRemoveFavoriteFromDrawer"
        @export-favorites="handleExportFavorites"
      />

      <!-- 操作菜单 ActionSheet -->
      <n-modal
        v-model:show="showActionSheet"
        :mask-closable="true"
        transform-origin="center"
      >
        <div class="action-sheet">
          <div class="action-sheet-item" @click="handleActionSheetSelect('ask')">
            <n-icon :size="20" style="margin-right: 12px;">
              <ChatboxOutline />
            </n-icon>
            基于此文献提问
          </div>
          <div class="action-sheet-item" @click="handleActionSheetSelect('review')">
            <n-icon :size="20" style="margin-right: 12px;">
              <DocumentTextOutline />
            </n-icon>
            生成相关综述
          </div>
          <div
            v-if="actionSheetTarget?.file_path"
            class="action-sheet-item"
            @click="handleActionSheetSelect('download')"
          >
            <n-icon :size="20" style="margin-right: 12px;">
              <DownloadOutline />
            </n-icon>
            下载原文
          </div>
          <div class="action-sheet-cancel" @click="showActionSheet = false">
            取消
          </div>
        </div>
      </n-modal>
    </template>

    <!-- 文献详情弹窗（PC和移动端共用） -->
    <n-modal
      v-model:show="showDetailModal"
      preset="card"
      title="文献详情"
      :style="{ width: isMobile ? '95vw' : '700px', maxWidth: '95vw' }"
      :bordered="false"
    >
      <template v-if="selectedDocument">
        <n-descriptions
          :column="1"
          label-placement="left"
          bordered
          size="small"
          :label-style="isMobile ? { width: '70px', minWidth: '70px', whiteSpace: 'nowrap' } : {}"
        >
          <n-descriptions-item label="标题">
            <strong>{{ selectedDocument.title || selectedDocument.filename || '未命名文献' }}</strong>
          </n-descriptions-item>
          <n-descriptions-item v-if="selectedDocument.authors" label="作者">
            {{ selectedDocument.authors }}
          </n-descriptions-item>
          <n-descriptions-item v-if="selectedDocument.year" label="年份">
            {{ selectedDocument.year }}年
          </n-descriptions-item>
          <n-descriptions-item v-if="selectedDocument.category" label="领域">
            <n-tag type="primary" size="small">{{ selectedDocument.category }}</n-tag>
            <n-tag v-if="selectedDocument.sub_category" type="success" size="small" style="margin-left: 8px;">
              {{ selectedDocument.sub_category }}
            </n-tag>
          </n-descriptions-item>
          <n-descriptions-item v-if="selectedDocument.methodology" label="方法">
            <n-tag type="warning" size="small">{{ selectedDocument.methodology }}</n-tag>
          </n-descriptions-item>
          <n-descriptions-item v-if="selectedDocument.keywords" label="关键词">
            {{ selectedDocument.keywords }}
          </n-descriptions-item>
          <n-descriptions-item v-if="selectedDocument.relevance_score" label="相关度">
            {{ (selectedDocument.relevance_score * 100).toFixed(0) }}%
          </n-descriptions-item>
          <n-descriptions-item v-if="selectedDocument.filename" label="文件名">
            {{ selectedDocument.filename }}
          </n-descriptions-item>
          <n-descriptions-item v-if="selectedDocument.file_path" label="原文">
            <n-button
              text
              type="primary"
              @click="downloadDocument(selectedDocument.doc_id, selectedDocument.filename)"
            >
              <template #icon>
                <n-icon :component="DownloadOutline" />
              </template>
              下载原文
            </n-button>
          </n-descriptions-item>
          <n-descriptions-item label="文档ID">
            <code style="font-size: 12px; color: #999;">{{ selectedDocument.doc_id }}</code>
          </n-descriptions-item>
        </n-descriptions>

        <div v-if="selectedDocument.abstract" class="detail-section">
          <h4>摘要</h4>
          <p class="detail-abstract">{{ selectedDocument.abstract }}</p>
        </div>

        <div v-if="selectedDocument.matched_content" class="detail-section">
          <h4>匹配内容</h4>
          <div class="detail-matched">{{ selectedDocument.matched_content }}</div>
        </div>

        <div class="detail-actions" :class="{ 'detail-actions-mobile': isMobile }">
          <n-space :wrap="isMobile" :size="isMobile ? [8, 8] : 12">
            <n-button
              :type="selectedDocument.is_favorited ? 'error' : 'default'"
              :size="isMobile ? 'small' : 'medium'"
              @click.stop="toggleFavorite(selectedDocument)"
            >
              <template #icon>
                <n-icon :component="selectedDocument.is_favorited ? Heart : HeartOutline" />
              </template>
              {{ selectedDocument.is_favorited ? '取消收藏' : '收藏' }}
            </n-button>
            <n-button
              type="primary"
              :size="isMobile ? 'small' : 'medium'"
              @click="askAboutResult(selectedDocument); showDetailModal = false"
            >
              <template #icon>
                <n-icon :component="ChatboxOutline" />
              </template>
              基于此提问
            </n-button>
            <n-button
              :size="isMobile ? 'small' : 'medium'"
              @click="generateReviewFromResult(selectedDocument); showDetailModal = false"
            >
              <template #icon>
                <n-icon :component="DocumentTextOutline" />
              </template>
              生成综述
            </n-button>
          </n-space>
        </div>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.search-page {
  height: 100%;
  overflow: hidden;
}

.sider-content {
  padding: 16px;
  height: 100%;
}

.history-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  padding: 0 4px;
}

.history-count {
  font-size: 12px;
  color: #999;
}

.history-item {
  width: 100%;
}

.history-query {
  font-size: 14px;
  font-weight: 500;
  color: #333;
  margin-bottom: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.history-meta {
  font-size: 12px;
  color: #999;
  display: flex;
  gap: 8px;
}

.history-tags {
  margin-top: 4px;
  display: flex;
  gap: 4px;
}

.favorite-item {
  width: 100%;
}

.favorite-title {
  font-size: 14px;
  font-weight: 500;
  color: #333;
  margin-bottom: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.favorite-meta {
  font-size: 12px;
  color: #999;
  display: flex;
  gap: 8px;
  margin-bottom: 4px;
}

.main-content {
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 24px;
  overflow: hidden;
}

.search-card {
  flex-shrink: 0;
  margin-bottom: 16px;
}

.filter-label {
  color: #666;
  font-size: 14px;
}

.results-section {
  flex: 1;
  overflow-y: auto;
}

.loading-state,
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 200px;
  color: #999;
}

.results-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.results-count {
  color: #666;
  font-size: 14px;
}

.results-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.result-card {
  transition: transform 0.2s;
}

.result-card:hover {
  transform: translateY(-2px);
}

.result-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 12px;
}

.result-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: #333;
  flex: 1;
}

.result-authors {
  margin: 0 0 8px;
  font-size: 14px;
  color: #666;
}

.result-abstract {
  margin: 0 0 12px;
  font-size: 14px;
  color: #555;
  line-height: 1.6;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.matched-content {
  padding: 12px;
  background: #f9f9f9;
  border-radius: 8px;
  font-size: 13px;
  color: #666;
  margin-bottom: 8px;
}

.matched-label {
  font-weight: 500;
}

.result-keywords {
  font-size: 12px;
  color: #999;
}

/* 文献详情弹窗样式 */
.detail-section {
  margin-top: 20px;
}

.detail-section h4 {
  margin: 0 0 8px;
  font-size: 14px;
  color: #333;
  font-weight: 600;
}

.detail-abstract {
  margin: 0;
  font-size: 14px;
  line-height: 1.8;
  color: #555;
  text-align: justify;
}

.detail-matched {
  padding: 12px;
  background: #f9f9f9;
  border-radius: 8px;
  font-size: 13px;
  color: #666;
  line-height: 1.6;
}

.detail-actions {
  margin-top: 24px;
  padding-top: 16px;
  border-top: 1px solid #eee;
}

.detail-actions-mobile {
  margin-top: 16px;
  padding-top: 12px;
}

.detail-actions-mobile :deep(.n-space) {
  justify-content: flex-start;
}

.file-path {
  font-family: monospace;
  font-size: 12px;
  color: #666;
  word-break: break-all;
}

/* ===== 移动端样式 ===== */
.search-page-mobile {
  height: 100%;
  overflow: hidden;
}

.mobile-search-container {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #f5f7f9;
}

.mobile-search-header {
  display: flex;
  gap: 8px;
  padding: 12px 16px;
  background: #fff;
  border-bottom: 1px solid #eee;
  flex-shrink: 0;
}

.mobile-search-input {
  flex: 1;
}

.mobile-filter-chips {
  display: flex;
  gap: 8px;
  padding: 12px 16px;
  background: #fff;
  overflow-x: auto;
  flex-shrink: 0;
  -webkit-overflow-scrolling: touch;
}

.filter-chip {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 12px;
  background: #f5f5f5;
  border-radius: 16px;
  font-size: 13px;
  color: #666;
  cursor: pointer;
  flex-shrink: 0;
}

.filter-chip.active {
  background: #e8f4ff;
  color: #2080f0;
}

.mobile-results-container {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  padding-bottom: calc(56px + env(safe-area-inset-bottom, 0px) + 16px);
}

.mobile-results-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  font-size: 13px;
  color: #666;
}

.mobile-results-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.mobile-result-card {
  background: #fff;
  border-radius: 12px;
  padding: 14px 16px;
  cursor: pointer;
}

.mobile-result-card:active {
  background: #f9f9f9;
}

.mobile-result-title {
  font-size: 15px;
  font-weight: 600;
  color: #333;
  margin-bottom: 8px;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.mobile-result-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.mobile-result-meta .relevance {
  font-size: 12px;
  color: #2080f0;
  font-weight: 500;
}

.mobile-result-authors {
  font-size: 13px;
  color: #666;
  margin-bottom: 6px;
}

.mobile-result-abstract {
  font-size: 13px;
  color: #555;
  line-height: 1.5;
  margin: 0;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* 移动端历史按钮 */
.history-btn {
  flex-shrink: 0;
  padding: 0 8px;
}

/* 移动端结果操作栏 */
.mobile-results-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* 移动端结果卡片按钮组 */
.mobile-result-btns {
  display: flex;
  align-items: center;
  margin-left: auto;
}

/* ActionSheet 样式 */
.action-sheet {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  background: #fff;
  border-radius: 12px 12px 0 0;
  padding-bottom: env(safe-area-inset-bottom, 0px);
  animation: slideUp 0.25s ease-out;
}

@keyframes slideUp {
  from {
    transform: translateY(100%);
  }
  to {
    transform: translateY(0);
  }
}

.action-sheet-item {
  display: flex;
  align-items: center;
  padding: 16px 20px;
  font-size: 16px;
  color: #333;
  border-bottom: 1px solid #f0f0f0;
  cursor: pointer;
  transition: background 0.2s;
}

.action-sheet-item:active {
  background: #f5f5f5;
}

.action-sheet-cancel {
  padding: 16px 20px;
  font-size: 16px;
  color: #999;
  text-align: center;
  cursor: pointer;
  margin-top: 8px;
  background: #f5f5f5;
}

.action-sheet-cancel:active {
  background: #eee;
}
</style>
