/** 来源：PPTist (MIT License) — 计算翻转样式 */
import { computed, type Ref } from 'vue'

export default (flipH: Ref<boolean | undefined>, flipV: Ref<boolean | undefined>) => {
  const flipStyle = computed(() => {
    if (flipH.value && flipV.value) return 'rotateX(180deg) rotateY(180deg)'
    if (flipV.value) return 'rotateX(180deg)'
    if (flipH.value) return 'rotateY(180deg)'
    return ''
  })
  return { flipStyle }
}
