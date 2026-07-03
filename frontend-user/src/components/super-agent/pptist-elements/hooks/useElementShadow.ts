/** 来源：PPTist (MIT License) — 计算元素阴影样式 */
import { computed, type Ref } from 'vue'
import type { PPTElementShadow } from '../types'

export default (shadow: Ref<PPTElementShadow | undefined>) => {
  const shadowStyle = computed(() => {
    if (shadow.value) {
      const { h, v, blur, color } = shadow.value
      return `${h}px ${v}px ${blur}px ${color}`
    }
    return ''
  })
  return { shadowStyle }
}
