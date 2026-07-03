<script setup lang="ts">
/**
 * 通知中心组件
 * PC端：铃铛图标 + 未读 badge + 下拉面板
 * 移动端：仅铃铛图标 + badge，点击跳转通知页
 */
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import {
  NPopover,
  NBadge,
  NButton,
  NIcon,
  NEmpty,
  NSpin,
  NTag,
  NDivider,
  useMessage,
} from 'naive-ui'
import {
  NotificationsOutline,
  CheckmarkDoneOutline,
} from '@vicons/ionicons5'
import { useNotificationStore } from '../stores/notification'
import {
  getNotifications,
  markRead,
  markAllRead,
  type Notification,
} from '../api/notification'

const props = defineProps<{
  isMobile?: boolean
}>()

const router = useRouter()
const message = useMessage()
const notificationStore = useNotificationStore()

const showPopover = ref(false)
const notifications = ref<Notification[]>([])
const loading = ref(false)

// 加载最近通知
const loadNotifications = async () => {
  loading.value = true
  try {
    const res = await getNotifications({ page: 1, page_size: 5 })
    notifications.value = res.items
  } catch {
    // 静默失败
  } finally {
    loading.value = false
  }
}

// 标记单条已读
const handleMarkRead = async (notif: Notification) => {
  if (notif.read_at) return
  try {
    await markRead(notif.id)
    notif.read_at = new Date().toISOString()
    notificationStore.fetchUnreadCount()
  } catch {
    message.error('操作失败')
  }
}

// 全部已读
const handleMarkAllRead = async () => {
  try {
    await markAllRead()
    notifications.value.forEach(n => {
      if (!n.read_at) n.read_at = new Date().toISOString()
    })
    notificationStore.fetchUnreadCount()
    message.success('已全部标记已读')
  } catch {
    message.error('操作失败')
  }
}

// 点击通知
const handleClickNotification = (notif: Notification) => {
  handleMarkRead(notif)
  // 根据通知类型跳转
  if (notif.data?.subscription_id) {
    if (notif.data?.result_id) {
      router.push(`/intelligence/${notif.data.subscription_id}/results/${notif.data.result_id}`)
    } else {
      router.push(`/intelligence/${notif.data.subscription_id}/results`)
    }
  }
  showPopover.value = false
}

// 查看全部
const handleViewAll = () => {
  router.push('/intelligence')
  showPopover.value = false
}

// Popover 显示时加载通知
const handlePopoverShow = () => {
  loadNotifications()
}

// 铃铛点击（移动端直接跳转）
const handleBellClick = () => {
  if (props.isMobile) {
    router.push('/intelligence')
  }
}

// 通知类型颜色
const getTypeColor = (type: string): string => {
  const map: Record<string, string> = {
    subscription_update: '#18a058',
    report_ready: '#2080f0',
    system: '#f0a020',
  }
  return map[type] || '#999'
}

// 格式化时间
const formatTime = (dateStr?: string) => {
  if (!dateStr) return ''
  const d = new Date(dateStr)
  const now = new Date()
  const diff = now.getTime() - d.getTime()
  const minutes = Math.floor(diff / 60000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes}分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}小时前`
  const days = Math.floor(hours / 24)
  if (days < 7) return `${days}天前`
  return d.toLocaleDateString('zh-CN')
}

onMounted(() => {
  notificationStore.startPolling()
})

onUnmounted(() => {
  notificationStore.stopPolling()
})
</script>

<template>
  <!-- 移动端：纯铃铛按钮 -->
  <template v-if="isMobile">
    <n-badge :value="notificationStore.unreadCount" :max="99" :offset="[-4, 4]">
      <n-button text @click="handleBellClick">
        <n-icon :component="NotificationsOutline" :size="22" />
      </n-button>
    </n-badge>
  </template>

  <!-- PC端：Popover 下拉面板 -->
  <template v-else>
    <n-popover
      v-model:show="showPopover"
      trigger="click"
      placement="bottom-end"
      :width="360"
      @update:show="(val: boolean) => val && handlePopoverShow()"
    >
      <template #trigger>
        <n-badge :value="notificationStore.unreadCount" :max="99" :offset="[-4, 4]">
          <n-button quaternary circle>
            <template #icon>
              <n-icon :component="NotificationsOutline" :size="20" />
            </template>
          </n-button>
        </n-badge>
      </template>

      <!-- 下拉面板内容 -->
      <div class="notification-panel">
        <div class="panel-header">
          <span class="panel-title">通知中心</span>
          <n-button
            v-if="notificationStore.unreadCount > 0"
            text
            size="small"
            @click="handleMarkAllRead"
          >
            <template #icon>
              <n-icon :component="CheckmarkDoneOutline" />
            </template>
            全部已读
          </n-button>
        </div>

        <div class="panel-body">
          <n-spin v-if="loading" size="small" class="panel-loading" />

          <n-empty
            v-else-if="notifications.length === 0"
            description="暂无通知"
            size="small"
            class="panel-empty"
          />

          <div v-else class="notification-list">
            <div
              v-for="notif in notifications"
              :key="notif.id"
              :class="['notification-item', { unread: !notif.read_at }]"
              @click="handleClickNotification(notif)"
            >
              <div
                class="type-indicator"
                :style="{ backgroundColor: getTypeColor(notif.type) }"
              />
              <div class="notification-content">
                <div class="notification-title">{{ notif.title }}</div>
                <div v-if="notif.summary" class="notification-summary">
                  {{ notif.summary }}
                </div>
                <div class="notification-time">{{ formatTime(notif.created_at) }}</div>
              </div>
            </div>
          </div>
        </div>

        <div class="panel-footer">
          <n-button text block @click="handleViewAll">
            查看全部
          </n-button>
        </div>
      </div>
    </n-popover>
  </template>
</template>

<style scoped>
.notification-panel {
  margin: -12px -16px;
}

.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  border-bottom: 1px solid #f0f0f0;
}

.panel-title {
  font-size: 15px;
  font-weight: 600;
  color: #333;
}

.panel-body {
  max-height: 320px;
  overflow-y: auto;
}

.panel-loading {
  display: flex;
  justify-content: center;
  padding: 24px 0;
}

.panel-empty {
  padding: 24px 0;
}

.notification-list {
  display: flex;
  flex-direction: column;
}

.notification-item {
  display: flex;
  gap: 10px;
  padding: 12px 16px;
  cursor: pointer;
  transition: background 0.2s;
}

.notification-item:hover {
  background: #f9f9f9;
}

.notification-item.unread {
  background: #f0f7ff;
}

.notification-item.unread:hover {
  background: #e5f0ff;
}

.type-indicator {
  width: 4px;
  min-height: 100%;
  border-radius: 2px;
  flex-shrink: 0;
}

.notification-content {
  flex: 1;
  min-width: 0;
}

.notification-title {
  font-size: 14px;
  font-weight: 500;
  color: #333;
  margin-bottom: 4px;
  line-height: 1.4;
}

.notification-summary {
  font-size: 13px;
  color: #666;
  margin-bottom: 4px;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.notification-time {
  font-size: 12px;
  color: #bbb;
}

.panel-footer {
  padding: 8px 16px;
  border-top: 1px solid #f0f0f0;
}
</style>
