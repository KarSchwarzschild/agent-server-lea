<script setup lang="ts">
/**
 * 智能订阅管理页面
 * PC端：标题卡片 + 统计概览 + 分类筛选 + 订阅卡片列表
 * 移动端：精简卡片列表
 */
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import {
  NCard,
  NButton,
  NSpace,
  NTag,
  NSpin,
  NEmpty,
  NIcon,
  NPopconfirm,
  NSwitch,
  NTabs,
  NTabPane,
  useMessage,
  useDialog,
} from 'naive-ui'
import {
  AddOutline,
  RefreshOutline,
  PlayOutline,
  CreateOutline,
  TrashOutline,
  EllipsisVertical,
  PauseOutline,
  DocumentTextOutline,
  SearchOutline,
  TrendingUpOutline,
  SettingsOutline,
  EyeOutline,
  NuclearOutline,
} from '@vicons/ionicons5'
import {
  getSubscriptions,
  updateSubscription,
  deleteSubscription,
  triggerSubscription,
  type Subscription,
} from '../../api/subscription'
import {
  getTemplates,
  installTemplate,
  type SubscriptionTemplate,
} from '../../api/subscriptionTemplate'
import { useResponsive } from '../../composables/useResponsive'
import CreateDialog from './CreateDialog.vue'

const router = useRouter()
const message = useMessage()
const dialog = useDialog()
const { isMobile } = useResponsive()

const loading = ref(false)
const subscriptions = ref<Subscription[]>([])
const activeCategory = ref<string>('all')
const showCreateDialog = ref(false)
const editingSubscription = ref<Subscription | null>(null)
const triggeringIds = ref<Set<string>>(new Set())

// 模板推荐
const recommendedTemplates = ref<SubscriptionTemplate[]>([])
const showQuickStart = ref(true)
const installingSlug = ref<string | null>(null)

// 分类定义
const categories = [
  { key: 'all', label: '全部' },
  { key: 'academic', label: '学术追踪' },
  { key: 'tech', label: '领域动态' },
  { key: 'trending', label: '热点雷达' },
  { key: 'custom', label: '自定义' },
]

// 分类图标映射
const categoryIcons: Record<string, any> = {
  academic: DocumentTextOutline,
  tech: SettingsOutline,
  trending: TrendingUpOutline,
  custom: NuclearOutline,
}

// 执行模式标签
const execModeLabels: Record<string, string> = {
  quick: '每日简报',
  deep: '深度报告',
  hybrid: '简报+周报',
}

// 筛选后的订阅列表
const filteredSubscriptions = computed(() => {
  if (activeCategory.value === 'all') return subscriptions.value
  return subscriptions.value.filter(s => s.category === activeCategory.value)
})

// 统计数据
const stats = computed(() => {
  const active = subscriptions.value.filter(s => s.is_enabled).length
  return {
    activeCount: active,
    totalCount: subscriptions.value.length,
  }
})

// 加载订阅列表
const loadSubscriptions = async () => {
  loading.value = true
  try {
    const res = await getSubscriptions()
    subscriptions.value = res.items
  } catch {
    message.error('加载订阅列表失败')
  } finally {
    loading.value = false
  }
}

// 加载推荐模板
const loadTemplates = async () => {
  try {
    const res = await getTemplates()
    recommendedTemplates.value = res.items.slice(0, 4)
  } catch {
    // 静默
  }
}

// 快速安装模板
const handleQuickInstall = async (tmpl: SubscriptionTemplate) => {
  installingSlug.value = tmpl.slug
  try {
    const res = await installTemplate(tmpl.slug)
    message.success(`已创建订阅「${tmpl.name}」`)
    loadSubscriptions()
    // A5: 安装后提示是否立即执行
    if (res.subscription_id) {
      let triggered = false
      dialog.info({
        title: '订阅已创建',
        content: `「${tmpl.name}」将按计划自动执行。是否立即执行一次？`,
        positiveText: '立即执行',
        negativeText: '稍后再说',
        onPositiveClick: async () => {
          if (triggered) return
          triggered = true
          try {
            await triggerSubscription(res.subscription_id)
            message.success('已触发执行')
            loadSubscriptions()
          } catch {
            triggered = false
            message.warning('触发失败，请稍后手动触发')
          }
        },
      })
    }
  } catch {
    message.error('安装失败')
  } finally {
    installingSlug.value = null
  }
}

// 切换订阅启用状态
const handleToggleEnabled = async (sub: Subscription) => {
  try {
    await updateSubscription(sub.id, { is_enabled: !sub.is_enabled })
    sub.is_enabled = !sub.is_enabled
    message.success(sub.is_enabled ? '已启用' : '已暂停')
  } catch {
    message.error('操作失败')
  }
}

// 手动触发
const handleTrigger = async (sub: Subscription) => {
  triggeringIds.value.add(sub.id)
  try {
    await triggerSubscription(sub.id)
    message.success('已触发检查，结果稍后可在历史中查看')
  } catch {
    message.error('触发失败')
  } finally {
    triggeringIds.value.delete(sub.id)
  }
}

// 删除订阅
const handleDelete = async (sub: Subscription) => {
  try {
    await deleteSubscription(sub.id)
    subscriptions.value = subscriptions.value.filter(s => s.id !== sub.id)
    message.success('删除成功')
  } catch {
    message.error('删除失败')
  }
}

// 查看结果
const handleViewResults = (sub: Subscription) => {
  router.push(`/intelligence/${sub.id}/results`)
}

// 编辑订阅
const handleEdit = (sub: Subscription) => {
  editingSubscription.value = sub
  showCreateDialog.value = true
}

// 创建成功回调
const handleCreateSuccess = () => {
  showCreateDialog.value = false
  editingSubscription.value = null
  loadSubscriptions()
}

// 格式化时间
const formatTime = (dateStr?: string) => {
  if (!dateStr) return '暂无'
  const d = new Date(dateStr)
  const now = new Date()
  const diff = now.getTime() - d.getTime()
  const minutes = Math.floor(diff / 60000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.floor(hours / 24)
  if (days < 7) return `${days} 天前`
  return d.toLocaleDateString('zh-CN')
}

// 获取分类标签颜色
const getCategoryType = (category: string): 'success' | 'info' | 'warning' | 'default' => {
  const map: Record<string, any> = {
    academic: 'success',
    tech: 'info',
    trending: 'warning',
    custom: 'default',
  }
  return map[category] || 'default'
}

// 获取分类中文名
const getCategoryLabel = (category: string) => {
  const item = categories.find(c => c.key === category)
  return item?.label || category
}

// 移动端操作菜单
const showActionSheet = ref(false)
const actionTarget = ref<Subscription | null>(null)

const openActionSheet = (sub: Subscription) => {
  actionTarget.value = sub
  showActionSheet.value = true
}

const handleMobileAction = async (action: string) => {
  if (!actionTarget.value) return
  const sub = actionTarget.value

  if (action === 'view') {
    handleViewResults(sub)
  } else if (action === 'trigger') {
    await handleTrigger(sub)
  } else if (action === 'edit') {
    handleEdit(sub)
  } else if (action === 'toggle') {
    await handleToggleEnabled(sub)
  } else if (action === 'delete') {
    dialog.warning({
      title: '确认删除',
      content: `确定要删除订阅「${sub.name}」吗？`,
      positiveText: '删除',
      negativeText: '取消',
      onPositiveClick: () => handleDelete(sub),
    })
  }
  showActionSheet.value = false
  actionTarget.value = null
}

onMounted(() => {
  loadSubscriptions()
  loadTemplates()
})
</script>

<template>
  <div class="subscription-page" :class="{ 'subscription-page-mobile': isMobile }">
    <!-- ===== PC端布局 ===== -->
    <template v-if="!isMobile">
      <!-- 标题卡片 -->
      <n-card class="header-card">
        <div class="header-content">
          <div>
            <h2>我的智能订阅</h2>
            <p>追踪学术前沿、领域动态和热点话题</p>
          </div>
          <n-space>
            <n-button @click="loadSubscriptions" :loading="loading">
              <template #icon>
                <n-icon :component="RefreshOutline" />
              </template>
              刷新
            </n-button>
            <n-button type="primary" @click="editingSubscription = null; showCreateDialog = true">
              <template #icon>
                <n-icon :component="AddOutline" />
              </template>
              创建订阅
            </n-button>
          </n-space>
        </div>
      </n-card>

      <!-- 统计概览 -->
      <div class="stats-row">
        <n-card class="stat-card">
          <div class="stat-value">{{ stats.activeCount }}</div>
          <div class="stat-label">活跃订阅</div>
        </n-card>
        <n-card class="stat-card">
          <div class="stat-value">{{ stats.totalCount }}</div>
          <div class="stat-label">全部订阅</div>
        </n-card>
      </div>

      <!-- 快速开始 - 模板推荐 -->
      <n-card v-if="recommendedTemplates.length > 0" class="quick-start-card" size="small">
        <div class="quick-start-header">
          <span class="quick-start-label">{{ showQuickStart ? '从模板一键创建订阅:' : '模板推荐' }}</span>
          <n-button text size="small" @click="showQuickStart = !showQuickStart">
            {{ showQuickStart ? '收起' : '展开' }}
          </n-button>
        </div>
        <div v-if="showQuickStart" class="quick-start-list">
          <n-button
            v-for="tmpl in recommendedTemplates"
            :key="tmpl.slug"
            size="small"
            :loading="installingSlug === tmpl.slug"
            @click="handleQuickInstall(tmpl)"
          >
            {{ tmpl.icon || '📋' }} {{ tmpl.name }}
          </n-button>
          <n-button
            size="small"
            type="primary"
            text
            @click="editingSubscription = null; showCreateDialog = true"
          >
            查看全部 &rarr;
          </n-button>
        </div>
      </n-card>

      <!-- 分类筛选 -->
      <n-tabs
        v-model:value="activeCategory"
        type="line"
        class="category-tabs"
      >
        <n-tab-pane
          v-for="cat in categories"
          :key="cat.key"
          :name="cat.key"
          :tab="cat.label"
        />
      </n-tabs>

      <!-- 订阅列表 -->
      <div class="content-section">
        <div v-if="loading" class="loading-state">
          <n-spin size="large" />
        </div>

        <n-empty
          v-else-if="filteredSubscriptions.length === 0"
          :description="activeCategory === 'all' ? '暂无订阅，点击上方按钮创建' : '该分类下暂无订阅'"
          class="empty-state"
        >
          <template #extra>
            <n-button type="primary" @click="editingSubscription = null; showCreateDialog = true">创建订阅</n-button>
          </template>
        </n-empty>

        <div v-else class="subscription-list">
          <n-card
            v-for="sub in filteredSubscriptions"
            :key="sub.id"
            class="subscription-card"
            hoverable
          >
            <div class="sub-card-content">
              <div class="sub-card-header">
                <div class="sub-card-title-row">
                  <n-icon
                    :component="categoryIcons[sub.category] || NuclearOutline"
                    :size="20"
                    class="sub-category-icon"
                  />
                  <h3 class="sub-name">{{ sub.name }}</h3>
                  <n-tag
                    :type="getCategoryType(sub.category)"
                    size="small"
                  >
                    {{ getCategoryLabel(sub.category) }}
                  </n-tag>
                  <n-tag size="small" :bordered="false">
                    {{ execModeLabels[sub.exec_mode] || sub.exec_mode }}
                  </n-tag>
                  <n-tag v-if="sub.has_feed" size="small" :bordered="false" type="success">
                    Feed
                  </n-tag>
                </div>
                <n-switch
                  :value="sub.is_enabled"
                  @update:value="() => handleToggleEnabled(sub)"
                  size="small"
                />
              </div>

              <p v-if="sub.description" class="sub-description">{{ sub.description }}</p>

              <!-- 关键词标签 -->
              <div v-if="sub.config?.keywords?.length" class="sub-keywords">
                <n-tag
                  v-for="kw in sub.config.keywords.slice(0, 5)"
                  :key="kw"
                  size="tiny"
                  :bordered="false"
                  type="info"
                >
                  {{ kw }}
                </n-tag>
                <span v-if="sub.config.keywords.length > 5" class="more-keywords">
                  +{{ sub.config.keywords.length - 5 }}
                </span>
              </div>

              <div class="sub-card-footer">
                <span class="sub-meta">
                  最近检查: {{ formatTime(sub.last_check_at) }}
                </span>
                <n-space size="small">
                  <n-button
                    size="small"
                    quaternary
                    @click="handleViewResults(sub)"
                  >
                    <template #icon>
                      <n-icon :component="EyeOutline" />
                    </template>
                    查看结果
                  </n-button>
                  <n-button
                    size="small"
                    quaternary
                    :loading="triggeringIds.has(sub.id)"
                    @click="handleTrigger(sub)"
                  >
                    <template #icon>
                      <n-icon :component="PlayOutline" />
                    </template>
                    立即检查
                  </n-button>
                  <n-button
                    size="small"
                    quaternary
                    @click="handleEdit(sub)"
                  >
                    <template #icon>
                      <n-icon :component="CreateOutline" />
                    </template>
                    编辑
                  </n-button>
                  <n-popconfirm @positive-click="handleDelete(sub)">
                    <template #trigger>
                      <n-button size="small" quaternary type="error">
                        <template #icon>
                          <n-icon :component="TrashOutline" />
                        </template>
                      </n-button>
                    </template>
                    确定要删除订阅「{{ sub.name }}」吗？
                  </n-popconfirm>
                </n-space>
              </div>
            </div>
          </n-card>
        </div>
      </div>
    </template>

    <!-- ===== 移动端布局 ===== -->
    <template v-else>
      <div class="mobile-header">
        <div class="mobile-header-top">
          <h2>智能订阅</h2>
          <n-button type="primary" size="small" @click="editingSubscription = null; showCreateDialog = true">
            <template #icon>
              <n-icon :component="AddOutline" />
            </template>
            创建
          </n-button>
        </div>
        <!-- 移动端分类快捷筛选 -->
        <div class="mobile-category-bar">
          <span
            v-for="cat in categories"
            :key="cat.key"
            :class="['mobile-cat-tag', { active: activeCategory === cat.key }]"
            @click="activeCategory = cat.key"
          >
            {{ cat.label }}
          </span>
        </div>
      </div>

      <div class="mobile-content">
        <div v-if="loading" class="loading-state">
          <n-spin size="large" />
        </div>

        <n-empty
          v-else-if="filteredSubscriptions.length === 0"
          description="暂无订阅"
          class="empty-state"
        >
          <template #extra>
            <n-button type="primary" size="small" @click="editingSubscription = null; showCreateDialog = true">创建订阅</n-button>
          </template>
        </n-empty>

        <div v-else class="mobile-sub-list">
          <div
            v-for="sub in filteredSubscriptions"
            :key="sub.id"
            class="mobile-sub-card"
            @click="handleViewResults(sub)"
          >
            <div class="mobile-sub-main">
              <div class="mobile-sub-title-row">
                <n-icon
                  :component="categoryIcons[sub.category] || NuclearOutline"
                  :size="18"
                  :style="{ color: sub.is_enabled ? '#2080f0' : '#ccc' }"
                />
                <span class="mobile-sub-name">{{ sub.name }}</span>
                <n-tag
                  :type="sub.is_enabled ? 'success' : 'default'"
                  size="tiny"
                >
                  {{ sub.is_enabled ? '运行中' : '已暂停' }}
                </n-tag>
              </div>
              <p v-if="sub.description" class="mobile-sub-desc">{{ sub.description }}</p>
              <div class="mobile-sub-meta">
                <n-tag size="tiny" :bordered="false" :type="getCategoryType(sub.category)">
                  {{ getCategoryLabel(sub.category) }}
                </n-tag>
                <span class="mobile-sub-time">{{ formatTime(sub.last_check_at) }}</span>
              </div>
            </div>
            <n-button text @click.stop="openActionSheet(sub)">
              <n-icon :size="20" :component="EllipsisVertical" />
            </n-button>
          </div>
        </div>
      </div>

      <!-- 移动端操作菜单 -->
      <Teleport to="body">
        <div v-if="showActionSheet" class="action-sheet-mask" @click="showActionSheet = false">
          <div class="action-sheet" @click.stop>
            <div class="action-sheet-item" @click="handleMobileAction('view')">
              <n-icon :size="20" :component="EyeOutline" style="margin-right: 12px;" />
              查看结果
            </div>
            <div class="action-sheet-item" @click="handleMobileAction('trigger')">
              <n-icon :size="20" :component="PlayOutline" style="margin-right: 12px;" />
              立即检查
            </div>
            <div class="action-sheet-item" @click="handleMobileAction('edit')">
              <n-icon :size="20" :component="CreateOutline" style="margin-right: 12px;" />
              编辑订阅
            </div>
            <div class="action-sheet-item" @click="handleMobileAction('toggle')">
              <n-icon
                :size="20"
                :component="actionTarget?.is_enabled ? PauseOutline : PlayOutline"
                style="margin-right: 12px;"
              />
              {{ actionTarget?.is_enabled ? '暂停订阅' : '启用订阅' }}
            </div>
            <div class="action-sheet-item action-sheet-danger" @click="handleMobileAction('delete')">
              <n-icon :size="20" :component="TrashOutline" style="margin-right: 12px;" />
              删除订阅
            </div>
            <div class="action-sheet-cancel" @click="showActionSheet = false">
              取消
            </div>
          </div>
        </div>
      </Teleport>
    </template>

    <!-- 创建/编辑订阅对话框 -->
    <CreateDialog
      v-model:show="showCreateDialog"
      :edit-subscription="editingSubscription"
      @success="handleCreateSuccess"
      @update:show="(v: boolean) => { if (!v) editingSubscription = null }"
    />
  </div>
</template>

<style scoped>
.subscription-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 24px;
  overflow: hidden;
}

/* ===== 标题卡片 ===== */
.header-card {
  flex-shrink: 0;
  margin-bottom: 16px;
}

.header-content {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.header-content h2 {
  margin: 0 0 4px;
  font-size: 20px;
  font-weight: 600;
}

.header-content p {
  margin: 0;
  color: #666;
  font-size: 14px;
}

/* ===== 统计概览 ===== */
.stats-row {
  display: flex;
  gap: 16px;
  margin-bottom: 16px;
  flex-shrink: 0;
}

.stat-card {
  flex: 1;
  text-align: center;
}

.stat-value {
  font-size: 28px;
  font-weight: 700;
  color: #2080f0;
}

.stat-label {
  font-size: 13px;
  color: #999;
  margin-top: 4px;
}

/* ===== 分类筛选 ===== */
.category-tabs {
  flex-shrink: 0;
  margin-bottom: 16px;
}

/* ===== 快速开始 ===== */
.quick-start-card {
  flex-shrink: 0;
  margin-bottom: 16px;
}

.quick-start-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.quick-start-label {
  font-size: 13px;
  color: #666;
}

.quick-start-list {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  align-items: center;
}

/* ===== 订阅列表 ===== */
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

.subscription-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.subscription-card {
  transition: transform 0.2s;
}

.subscription-card:hover {
  transform: translateY(-2px);
}

.sub-card-content {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.sub-card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
}

.sub-card-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.sub-category-icon {
  color: #2080f0;
}

.sub-name {
  margin: 0;
  font-size: 16px;
  font-weight: 500;
}

.sub-description {
  margin: 0;
  font-size: 13px;
  color: #666;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.sub-keywords {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  align-items: center;
}

.more-keywords {
  font-size: 12px;
  color: #999;
}

.sub-card-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-top: 8px;
  border-top: 1px solid #f0f0f0;
}

.sub-meta {
  font-size: 12px;
  color: #999;
}

/* ===== 移动端样式 ===== */
.subscription-page-mobile {
  padding: 0;
  background: #f5f7f9;
}

.mobile-header {
  background: #fff;
  padding: 16px;
  border-bottom: 1px solid #eee;
}

.mobile-header-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.mobile-header-top h2 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
}

.mobile-category-bar {
  display: flex;
  gap: 8px;
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
}

.mobile-cat-tag {
  flex-shrink: 0;
  padding: 4px 12px;
  border-radius: 16px;
  font-size: 13px;
  color: #666;
  background: #f5f5f5;
  cursor: pointer;
  transition: all 0.2s;
}

.mobile-cat-tag.active {
  color: #fff;
  background: #2080f0;
}

.mobile-content {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  padding-bottom: calc(56px + env(safe-area-inset-bottom, 0px) + 16px);
}

.mobile-sub-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.mobile-sub-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #fff;
  border-radius: 12px;
  padding: 14px 16px;
  cursor: pointer;
}

.mobile-sub-card:active {
  background: #f9f9f9;
}

.mobile-sub-main {
  flex: 1;
  min-width: 0;
}

.mobile-sub-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.mobile-sub-name {
  font-size: 15px;
  font-weight: 500;
  color: #333;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mobile-sub-desc {
  margin: 0 0 6px;
  font-size: 13px;
  color: #999;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.mobile-sub-meta {
  display: flex;
  align-items: center;
  gap: 8px;
}

.mobile-sub-time {
  font-size: 12px;
  color: #bbb;
}

/* ===== ActionSheet ===== */
.action-sheet-mask {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.4);
  z-index: 1000;
  display: flex;
  align-items: flex-end;
}

.action-sheet {
  width: 100%;
  background: #fff;
  border-radius: 12px 12px 0 0;
  padding-bottom: env(safe-area-inset-bottom, 0px);
  animation: slideUp 0.25s ease-out;
}

@keyframes slideUp {
  from { transform: translateY(100%); }
  to { transform: translateY(0); }
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

.action-sheet-danger {
  color: #e03131;
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
