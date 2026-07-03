<template>
  <div
    class="base-element-text"
    :style="{
      transform: elementInfo.rotate ? `rotate(${elementInfo.rotate}deg)` : undefined,
    }"
  >
    <div
      class="element-content"
      :style="{
        backgroundColor: elementInfo.fill,
        opacity: elementInfo.opacity,
        textShadow: shadowStyle,
        lineHeight: elementInfo.lineHeight,
        letterSpacing: (elementInfo.wordSpace || 0) + 'px',
        color: elementInfo.defaultColor,
        fontFamily: elementInfo.defaultFontName,
        writingMode: elementInfo.vertical ? 'vertical-rl' : undefined,
      }"
    >
      <ElementOutline
        v-if="elementInfo.outline"
        :width="elementInfo.width || 0"
        :height="elementInfo.height || 0"
        :outline="elementInfo.outline"
      />
      <div
        class="text ProseMirror-static"
        :class="{ 'thumbnail': target === 'thumbnail' }"
        :style="{ '--paragraphSpace': `${elementInfo.paragraphSpace === undefined ? 5 : elementInfo.paragraphSpace}px` }"
        v-html="elementInfo.content"
      />
    </div>
  </div>
</template>

<script lang="ts" setup>
/**
 * BaseTextElement — 富文本渲染（剥离绝对定位，适配 CSS Grid 流式布局）
 * 来源：PPTist (MIT License)
 */
import { computed } from 'vue'
import type { PPTElementOutline, PPTElementShadow } from './types'
import ElementOutline from './ElementOutline.vue'
import useElementShadow from './hooks/useElementShadow'

export interface TextElementInfo {
  content: string
  defaultFontName?: string
  defaultColor?: string
  outline?: PPTElementOutline
  fill?: string
  lineHeight?: number
  wordSpace?: number
  opacity?: number
  shadow?: PPTElementShadow
  paragraphSpace?: number
  vertical?: boolean
  rotate?: number
  width?: number
  height?: number
}

const props = defineProps<{
  elementInfo: TextElementInfo
  target?: string
}>()

const shadow = computed(() => props.elementInfo.shadow)
const { shadowStyle } = useElementShadow(shadow)
</script>

<style scoped>
.base-element-text { width: 100%; }
.element-content {
  position: relative;
  padding: 10px;
  line-height: 1.5;
  word-break: break-word;
}
.element-content .text { position: relative; }
.element-content .text.thumbnail { pointer-events: none; }
</style>
