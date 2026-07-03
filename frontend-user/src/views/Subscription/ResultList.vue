<script setup lang="ts">
/**
 * 订阅结果历史列表
 * 显示某个订阅的历次执行结果
 */
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NCard,
  NButton,
  NSpace,
  NTag,
  NSpin,
  NEmpty,
  NIcon,
  NPagination,
  NAlert,
  useMessage,
} from 'naive-ui'
import {
  ArrowBackOutline,
  DocumentTextOutline,
  TimeOutline,
  CopyOutline,
  RefreshOutline,
} from '@vicons/ionicons5'
import {
  getSubscription,
  getSubscriptionResults,
  regenerateFeedToken,
  type Subscription,
  type SubscriptionResult,
} from '../../api/subscription'
import { useResponsive } from '../../composables/useResponsive'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const { isMobile } = useResponsive()

const subId = route.params.subId as string
const subscription = ref<Subscription | null>(null)
const results = ref<SubscriptionResult[]>([])
const loading = ref(false)
const total = ref(0)
const page = ref(1)
const pageSize = 20

const loadSubscription = async () => {
  try {
    const res = await getSubscription(subId)
    subscription.value = res.subscription
  } catch {
    message.error('加载订阅信息失败')
  }
}

const loadResults = async () => {
  loading.value = true
  try {
    const res = await getSubscriptionResults(subId, {
      page: page.value,
      page_size: pageSize,
    })
    results.value = res.items
    total.value = res.total
  } catch {
    message.error('加载结果列表失败')
  } finally {
    loading.value = false
  }
}

const handleViewReport = (result: SubscriptionResult) => {
  router.push(`/intelligence/${subId}/results/${result.id}`)
}

const handlePageChange = (p: number) => {
  page.value = p
  loadResults()
}

const goBack = () => {
  router.push('/intelligence')
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

// Feed URL 相关
const feedUrls = ref<{ rss: string; json: string } | null>(null)
const regeneratingToken = ref(false)

const handleRegenerateFeedToken = async () => {
  regeneratingToken.value = true
  try {
    const res = await regenerateFeedToken(subId)
    feedUrls.value = {
      rss: `${location.origin}${res.rss_url}`,
      json: `${location.origin}${res.json_url}`,
    }
    message.success(res.message || 'Feed Token 已重新生成')
  } catch {
    message.error('重新生成失败')
  } finally {
    regeneratingToken.value = false
  }
}

const copyToClipboard = async (text: string) => {
  try {
    await navigator.clipboard.writeText(text)
    message.success('已复制到剪贴板')
  } catch {
    message.error('复制失败')
  }
}

onMounted(() => {
  loadSubscription()
  loadResults()
})
</script>

<template>
  <div class="result-list-page" :class="{ 'result-list-mobile': isMobile }">
    <!-- 头部 -->
    <div :class="isMobile ? 'mobile-header' : 'header-card-wrapper'">
      <n-card v-if="!isMobile" class="header-card">
        <div class="header-content">
          <div>
            <n-space align="center" size="small">
              <n-button text @click="goBack">
                <n-icon :component="ArrowBackOutline" :size="20" />
              </n-button>
              <h2>{{ subscription?.name || '订阅结果' }}</h2>
            </n-space>
            <p>共 {{ total }} 次执行记录</p>
          </div>
          <n-button @click="goBack">返回订阅列表</n-button>
        </div>
      </n-card>
      <template v-else>
        <div class="mobile-header-inner">
          <n-button text @click="goBack" size="small">
            <n-icon :component="ArrowBackOutline" :size="20" />
          </n-button>
          <h2>{{ subscription?.name || '结果历史' }}</h2>
          <span style="width: 32px" />
        </div>
      </template>
    </div>

    <!-- Feed URL 区域 -->
    <div v-if="subscription && !isMobile" class="feed-section">
      <n-card size="small">
        <template #header>
          <span style="font-size: 14px; font-weight: 500;">Feed 订阅地址</span>
        </template>
        <template v-if="feedUrls">
          <div class="feed-url-row">
            <span class="feed-url-label">RSS</span>
            <code class="feed-url-text">{{ feedUrls.rss }}</code>
            <n-button text size="small" @click="copyToClipboard(feedUrls.rss)">
              <n-icon :component="CopyOutline" />
            </n-button>
          </div>
          <div class="feed-url-row">
            <span class="feed-url-label">JSON</span>
            <code class="feed-url-text">{{ feedUrls.json }}</code>
            <n-button text size="small" @click="copyToClipboard(feedUrls.json)">
              <n-icon :component="CopyOutline" />
            </n-button>
          </div>
          <n-alert type="warning" :bordered="false" style="margin-top: 8px; font-size: 12px;">
            Token 泄露可能导致数据公开，请妥善保管
          </n-alert>
        </template>
        <template v-else>
          <p style="margin: 0; font-size: 13px; color: #666;">
            生成 Feed Token 后可通过 RSS 阅读器（如 Feedly）订阅此信息源
          </p>
        </template>
        <template #action>
          <n-button
            size="small"
            :loading="regeneratingToken"
            @click="handleRegenerateFeedToken"
          >
            <template #icon>
              <n-icon :component="RefreshOutline" />
            </template>
            {{ feedUrls ? '重新生成 Token' : '生成 Feed Token' }}
          </n-button>
        </template>
      </n-card>
    </div>

    <!-- 移动端 Feed 入口 -->
    <div v-if="subscription && isMobile" class="mobile-feed-entry">
      <n-button
        block
        size="small"
        :loading="regeneratingToken"
        @click="handleRegenerateFeedToken"
      >
        <template #icon>
          <n-icon :component="RefreshOutline" />
        </template>
        {{ feedUrls ? '重新生成 Feed Token' : '生成 Feed 订阅链接' }}
      </n-button>
      <template v-if="feedUrls">
        <div class="feed-url-row mobile-feed-url">
          <span class="feed-url-label">RSS</span>
          <code class="feed-url-text">{{ feedUrls.rss }}</code>
          <n-button text size="small" @click="copyToClipboard(feedUrls.rss)">
            <n-icon :component="CopyOutline" />
          </n-button>
        </div>
      </template>
    </div>

    <!-- 结果列表 -->
    <div :class="isMobile ? 'mobile-content' : 'content-section'">
      <div v-if="loading" class="loading-state">
        <n-spin size="large" />
      </div>

      <n-empty
        v-else-if="results.length === 0"
        description="暂无执行结果"
        class="empty-state"
      />

      <template v-else>
        <div :class="isMobile ? 'mobile-result-list' : 'result-list'">
          <n-card
            v-for="result in results"
            :key="result.id"
            :class="isMobile ? 'mobile-result-card' : 'result-card'"
            hoverable
            @click="handleViewReport(result)"
            style="cursor: pointer"
          >
            <div class="result-card-content">
              <div class="result-header">
                <n-space align="center" size="small">
                  <n-icon :component="DocumentTextOutline" :size="18" />
                  <span class="result-time">{{ formatDateTime(result.run_at) }}</span>
                </n-space>
                <n-space size="small">
                  <n-tag size="small" :type="result.exec_mode === 'deep' ? 'warning' : 'info'">
                    {{ execModeLabels[result.exec_mode] || result.exec_mode }}
                  </n-tag>
                  <n-tag size="small" :type="result.item_count > 0 ? 'success' : 'default'">
                    {{ result.item_count > 0 ? `${result.item_count} 条新内容` : '暂无新内容' }}
                  </n-tag>
                </n-space>
              </div>
              <p v-if="result.summary" class="result-summary">
                {{ result.summary }}
              </p>
            </div>
          </n-card>
        </div>

        <div v-if="total > pageSize" class="pagination-wrapper">
          <n-pagination
            :page="page"
            :page-size="pageSize"
            :item-count="total"
            @update:page="handlePageChange"
          />
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.result-list-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 24px;
  overflow: hidden;
}

.header-card-wrapper {
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

.header-content p {
  margin: 4px 0 0;
  color: #666;
  font-size: 14px;
}

.content-section {
  flex: 1;
  overflow-y: auto;
}

.loading-state {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 200px;
}

.empty-state {
  margin-top: 48px;
}

.result-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.result-card {
  transition: transform 0.2s;
}

.result-card:hover {
  transform: translateY(-2px);
}

.result-card-content {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.result-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.result-time {
  font-size: 14px;
  font-weight: 500;
  color: #333;
}

.result-summary {
  margin: 0;
  font-size: 13px;
  color: #666;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.pagination-wrapper {
  display: flex;
  justify-content: center;
  padding: 16px 0;
}

/* ===== Feed URL 区域 ===== */
.feed-section {
  flex-shrink: 0;
  margin-bottom: 16px;
}

.feed-url-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
}

.feed-url-row + .feed-url-row {
  border-top: 1px solid #f0f0f0;
}

.feed-url-label {
  font-size: 12px;
  font-weight: 600;
  color: #999;
  flex-shrink: 0;
  width: 36px;
}

.feed-url-text {
  flex: 1;
  font-size: 12px;
  color: #333;
  background: #f5f5f5;
  padding: 4px 8px;
  border-radius: 4px;
  word-break: break-all;
  line-height: 1.4;
}

.mobile-feed-entry {
  padding: 0 16px 12px;
}

.mobile-feed-url {
  margin-top: 8px;
}

/* ===== 移动端样式 ===== */
.result-list-mobile {
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

.mobile-content {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  padding-bottom: calc(56px + env(safe-area-inset-bottom, 0px) + 16px);
}

.mobile-result-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.mobile-result-card {
  border-radius: 12px;
}
</style>
