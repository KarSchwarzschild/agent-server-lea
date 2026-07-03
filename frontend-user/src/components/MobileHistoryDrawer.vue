<template>
  <n-drawer
    v-model:show="visible"
    placement="bottom"
    :height="drawerHeight"
    :trap-focus="false"
    :block-scroll="true"
  >
    <n-drawer-content :native-scrollbar="false">
      <template #header>
        <div class="drawer-header">
          <div class="drawer-tabs">
            <div
              class="drawer-tab"
              :class="{ active: activeTab === 'history' }"
              @click="activeTab = 'history'"
            >
              检索历史
            </div>
            <div
              class="drawer-tab"
              :class="{ active: activeTab === 'favorites' }"
              @click="activeTab = 'favorites'"
            >
              我的收藏
            </div>
          </div>
          <n-button text @click="visible = false">
            <n-icon :size="20">
              <CloseOutline />
            </n-icon>
          </n-button>
        </div>
      </template>

      <!-- 检索历史 -->
      <div v-show="activeTab === 'history'" class="tab-content">
        <div class="tab-header">
          <span class="count-text">共 {{ historyList.length }} 条</span>
          <n-popconfirm
            @positive-click="handleClearHistory"
            positive-text="确定"
            negative-text="取消"
          >
            <template #trigger>
              <n-button text type="error" size="small" :disabled="historyList.length === 0">
                清空全部
              </n-button>
            </template>
            确定清空所有检索历史吗？
          </n-popconfirm>
        </div>

        <n-spin :show="historyLoading">
          <n-empty v-if="historyList.length === 0" description="暂无检索历史" class="empty-state" />
          <div v-else class="list-container">
            <div
              v-for="item in historyList"
              :key="item.search_id"
              class="list-item"
              @click="handleRestoreHistory(item)"
            >
              <div class="item-content">
                <div class="item-title">{{ item.query }}</div>
                <div class="item-meta">
                  <span>{{ item.results_count }} 条结果</span>
                  <span>{{ formatTime(item.created_at) }}</span>
                </div>
                <div v-if="item.category || item.methodology" class="item-tags">
                  <n-tag v-if="item.category" size="tiny">{{ item.category }}</n-tag>
                  <n-tag v-if="item.methodology" size="tiny" type="warning">{{ item.methodology }}</n-tag>
                  <n-tag v-if="item.year_from || item.year_to" size="tiny" type="info">
                    {{ item.year_from || '?' }}-{{ item.year_to || '?' }}
                  </n-tag>
                </div>
              </div>
              <n-button
                text
                class="delete-btn"
                @click.stop="handleDeleteHistory(item.search_id)"
              >
                <n-icon :size="18">
                  <TrashOutline />
                </n-icon>
              </n-button>
            </div>
          </div>
        </n-spin>
      </div>

      <!-- 我的收藏 -->
      <div v-show="activeTab === 'favorites'" class="tab-content">
        <div class="tab-header">
          <span class="count-text">共 {{ favoritesList.length }} 篇</span>
          <n-dropdown
            :options="exportOptions"
            :disabled="favoritesList.length === 0"
            @select="handleExportFavorites"
          >
            <n-button text type="primary" size="small" :disabled="favoritesList.length === 0">
              <n-icon :size="16" style="margin-right: 4px;">
                <DownloadOutline />
              </n-icon>
              导出
            </n-button>
          </n-dropdown>
        </div>

        <n-spin :show="favoritesLoading">
          <n-empty v-if="favoritesList.length === 0" description="暂无收藏" class="empty-state" />
          <div v-else class="list-container">
            <div
              v-for="item in favoritesList"
              :key="item.id"
              class="list-item"
              @click="handleOpenFavorite(item)"
            >
              <div class="item-content">
                <div class="item-title">{{ item.title || '未命名文献' }}</div>
                <div class="item-meta">
                  <span v-if="item.authors">{{ item.authors }}</span>
                  <span v-if="item.year">{{ item.year }}年</span>
                </div>
                <div v-if="item.category" class="item-tags">
                  <n-tag size="tiny">{{ item.category }}</n-tag>
                </div>
              </div>
              <n-button
                text
                class="delete-btn"
                @click.stop="handleRemoveFavorite(item)"
              >
                <n-icon :size="18">
                  <TrashOutline />
                </n-icon>
              </n-button>
            </div>
          </div>
        </n-spin>
      </div>
    </n-drawer-content>
  </n-drawer>
</template>

<script setup lang="ts">
/**
 * MobileHistoryDrawer - 移动端检索历史与收藏抽屉
 * 底部弹出，Tab切换历史/收藏
 */
import { ref, computed, watch } from 'vue'
import {
  NDrawer,
  NDrawerContent,
  NButton,
  NIcon,
  NTag,
  NSpin,
  NEmpty,
  NPopconfirm,
  NDropdown,
  useMessage,
} from 'naive-ui'
import {
  CloseOutline,
  TrashOutline,
  DownloadOutline,
} from '@vicons/ionicons5'
import type { SearchHistoryItem, FavoriteItem } from '../api/search'

const props = defineProps<{
  show: boolean
  historyList: SearchHistoryItem[]
  favoritesList: FavoriteItem[]
  historyLoading: boolean
  favoritesLoading: boolean
}>()

const emit = defineEmits<{
  'update:show': [value: boolean]
  'restore-history': [item: SearchHistoryItem]
  'delete-history': [searchId: string]
  'clear-history': []
  'open-favorite': [item: FavoriteItem]
  'remove-favorite': [item: FavoriteItem]
  'export-favorites': [format: 'csv' | 'json']
}>()

const message = useMessage()

const visible = computed({
  get: () => props.show,
  set: (val) => emit('update:show', val)
})

const activeTab = ref<'history' | 'favorites'>('history')
const drawerHeight = '60vh'

// 导出选项
const exportOptions = [
  { label: '导出为 CSV', key: 'csv' },
  { label: '导出为 JSON', key: 'json' }
]

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

// 恢复历史搜索
const handleRestoreHistory = (item: SearchHistoryItem) => {
  emit('restore-history', item)
  visible.value = false
}

// 删除单条历史
const handleDeleteHistory = (searchId: string) => {
  emit('delete-history', searchId)
}

// 清空所有历史
const handleClearHistory = () => {
  emit('clear-history')
}

// 打开收藏文献
const handleOpenFavorite = (item: FavoriteItem) => {
  emit('open-favorite', item)
  visible.value = false
}

// 移除收藏
const handleRemoveFavorite = (item: FavoriteItem) => {
  emit('remove-favorite', item)
}

// 导出收藏
const handleExportFavorites = (key: string) => {
  emit('export-favorites', key as 'csv' | 'json')
  message.success(`正在导出收藏为 ${key.toUpperCase()} 格式`)
}

// 打开时重置到历史Tab
watch(visible, (val) => {
  if (val) {
    activeTab.value = 'history'
  }
})
</script>

<style scoped>
.drawer-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
}

.drawer-tabs {
  display: flex;
  gap: 24px;
}

.drawer-tab {
  font-size: 16px;
  font-weight: 500;
  color: #999;
  padding-bottom: 8px;
  border-bottom: 2px solid transparent;
  cursor: pointer;
  transition: all 0.2s;
}

.drawer-tab.active {
  color: #2080f0;
  border-bottom-color: #2080f0;
}

.tab-content {
  min-height: 200px;
}

.tab-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 0;
  border-bottom: 1px solid #f0f0f0;
  margin-bottom: 8px;
}

.count-text {
  font-size: 13px;
  color: #999;
}

.empty-state {
  padding: 40px 0;
}

.list-container {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.list-item {
  display: flex;
  align-items: flex-start;
  padding: 12px;
  background: #f9f9f9;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.2s;
}

.list-item:active {
  background: #f0f0f0;
}

.item-content {
  flex: 1;
  min-width: 0;
}

.item-title {
  font-size: 15px;
  font-weight: 500;
  color: #333;
  margin-bottom: 6px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.item-meta {
  font-size: 12px;
  color: #999;
  display: flex;
  gap: 12px;
  margin-bottom: 6px;
}

.item-tags {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.delete-btn {
  flex-shrink: 0;
  color: #999;
  margin-left: 8px;
  padding: 4px;
}

.delete-btn:hover {
  color: #ff4d4f;
}
</style>
