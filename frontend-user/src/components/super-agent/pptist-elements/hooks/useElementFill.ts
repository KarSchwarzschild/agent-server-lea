/** 来源：PPTist (MIT License) — 计算形状填充 */
import { computed, type Ref } from 'vue'

interface ShapeElement {
  id: string
  fill: string
  pattern?: string
  gradient?: { type: string; colors: { pos: number; color: string }[]; rotate: number }
}

export default (element: Ref<ShapeElement>, source: string) => {
  const fill = computed(() => {
    if (element.value.pattern) return `url(#${source}-pattern-${element.value.id})`
    if (element.value.gradient) return `url(#${source}-gradient-${element.value.id})`
    return element.value.fill || 'none'
  })
  return { fill }
}
