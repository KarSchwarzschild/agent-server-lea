/**
 * useCleanup Hook
 * 统一管理组件销毁时的清理逻辑，防止内存泄漏
 */
import { onUnmounted, onDeactivated } from 'vue'

type CleanupFn = () => void

export function useCleanup() {
  const cleanupFns: CleanupFn[] = []

  const addCleanup = (fn: CleanupFn) => {
    cleanupFns.push(fn)
  }

  const runCleanup = () => {
    cleanupFns.forEach(fn => {
      try {
        fn()
      } catch (e) {
        console.error('Cleanup error:', e)
      }
    })
    cleanupFns.length = 0
  }

  onUnmounted(runCleanup)
  onDeactivated(runCleanup)

  return { addCleanup }
}
