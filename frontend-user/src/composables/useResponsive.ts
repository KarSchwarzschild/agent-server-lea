/**
 * useResponsive Hook
 * 响应式设备检测，全局单例模式避免重复监听
 */
import { ref, onMounted, onUnmounted, computed } from 'vue'

interface ResponsiveState {
  isMobile: boolean
  isTablet: boolean
  isDesktop: boolean
  width: number
  height: number
}

const BREAKPOINTS = {
  MOBILE: 768,
  TABLET: 1024,
} as const

// 全局单例状态
const state = ref<ResponsiveState>({
  isMobile: false,
  isTablet: false,
  isDesktop: true,
  width: typeof window !== 'undefined' ? window.innerWidth : 1200,
  height: typeof window !== 'undefined' ? window.innerHeight : 800,
})

let listenerCount = 0
let debounceTimer: ReturnType<typeof setTimeout> | null = null

const updateState = () => {
  if (typeof window === 'undefined') return

  const width = window.innerWidth
  const height = window.innerHeight

  state.value = {
    isMobile: width < BREAKPOINTS.MOBILE,
    isTablet: width >= BREAKPOINTS.MOBILE && width < BREAKPOINTS.TABLET,
    isDesktop: width >= BREAKPOINTS.TABLET,
    width,
    height,
  }
}

const handleResize = () => {
  if (debounceTimer) clearTimeout(debounceTimer)
  debounceTimer = setTimeout(updateState, 100)
}

const handleOrientationChange = () => {
  setTimeout(updateState, 100)
}

export function useResponsive() {
  onMounted(() => {
    if (listenerCount === 0) {
      updateState()
      window.addEventListener('resize', handleResize)
      window.addEventListener('orientationchange', handleOrientationChange)
    }
    listenerCount++
  })

  onUnmounted(() => {
    listenerCount--
    if (listenerCount === 0) {
      window.removeEventListener('resize', handleResize)
      window.removeEventListener('orientationchange', handleOrientationChange)
      if (debounceTimer) {
        clearTimeout(debounceTimer)
        debounceTimer = null
      }
    }
  })

  return {
    isMobile: computed(() => state.value.isMobile),
    isTablet: computed(() => state.value.isTablet),
    isDesktop: computed(() => state.value.isDesktop),
    width: computed(() => state.value.width),
    height: computed(() => state.value.height),
    state: computed(() => state.value),
  }
}
