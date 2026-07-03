<template>
  <div
    class="base-element-image"
    :style="{
      transform: elementInfo.rotate ? `rotate(${elementInfo.rotate}deg)` : undefined,
    }"
  >
    <div
      class="element-content"
      :style="{
        filter: shadowStyle ? `drop-shadow(${shadowStyle})` : '',
        transform: flipStyle,
        borderRadius: elementInfo.radius ? `${elementInfo.radius}px` : undefined,
      }"
    >
      <div class="image-content" :style="{ borderRadius: elementInfo.radius ? `${elementInfo.radius}px` : undefined }">
        <img
          :src="elementInfo.src"
          :draggable="false"
          :style="{ filter: filterStyle }"
          alt=""
        />
        <div
          class="color-mask"
          v-if="elementInfo.colorMask"
          :style="{ backgroundColor: elementInfo.colorMask }"
        />
      </div>
    </div>
  </div>
</template>

<script lang="ts" setup>
/**
 * BaseImageElement — 图片渲染（剥离绝对定位，简化裁剪）
 * 来源：PPTist (MIT License)
 */
import { computed } from 'vue'
import type { PPTElementShadow } from './types'
import useElementShadow from './hooks/useElementShadow'
import useElementFlip from './hooks/useElementFlip'

export interface ImageElementInfo {
  src: string
  fixedRatio?: boolean
  flipH?: boolean
  flipV?: boolean
  shadow?: PPTElementShadow
  radius?: number
  colorMask?: string
  rotate?: number
  filters?: Record<string, string>
}

const props = defineProps<{
  elementInfo: ImageElementInfo
}>()

const shadow = computed(() => props.elementInfo.shadow)
const { shadowStyle } = useElementShadow(shadow)

const flipH = computed(() => props.elementInfo.flipH)
const flipV = computed(() => props.elementInfo.flipV)
const { flipStyle } = useElementFlip(flipH, flipV)

const filterStyle = computed(() => {
  if (!props.elementInfo.filters) return ''
  return Object.entries(props.elementInfo.filters).map(([k, v]) => `${k}(${v})`).join(' ')
})
</script>

<style scoped>
.base-element-image { width: 100%; }
.element-content { width: 100%; position: relative; }
.image-content { width: 100%; overflow: hidden; position: relative; }
.image-content img { width: 100%; height: auto; display: block; }
.color-mask { position: absolute; top: 0; bottom: 0; left: 0; right: 0; }
</style>
