/**
 * 通知状态管理
 * 提供未读通知数的轮询和全局访问
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getUnreadCount } from '../api/notification'

export const useNotificationStore = defineStore('notification', () => {
  const unreadCount = ref(0)

  let pollTimer: ReturnType<typeof setInterval> | null = null

  async function fetchUnreadCount() {
    try {
      const res = await getUnreadCount()
      unreadCount.value = res.unread_count || 0
    } catch {
      // 静默失败，不影响主流程
    }
  }

  function startPolling() {
    fetchUnreadCount()
    if (pollTimer) clearInterval(pollTimer)
    pollTimer = setInterval(fetchUnreadCount, 60000)
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  return { unreadCount, fetchUnreadCount, startPolling, stopPolling }
})
