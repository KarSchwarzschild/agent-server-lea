<template>
  <div
    class="base-element-shape"
    :style="{
      transform: elementInfo.rotate ? `rotate(${elementInfo.rotate}deg)` : undefined,
      width: elementInfo.width + 'px',
      height: elementInfo.height + 'px',
    }"
  >
    <div
      class="element-content"
      :style="{
        opacity: elementInfo.opacity,
        filter: shadowStyle ? `drop-shadow(${shadowStyle})` : '',
        transform: flipStyle,
        color: text.defaultColor,
        fontFamily: text.defaultFontName,
      }"
    >
      <svg
        overflow="visible"
        :width="elementInfo.width"
        :height="elementInfo.height"
      >
        <defs>
          <PatternDefs
            v-if="elementInfo.pattern"
            :id="`base-pattern-${elementInfo.id}`"
            :src="elementInfo.pattern"
          />
          <GradientDefs
            v-else-if="elementInfo.gradient"
            :id="`base-gradient-${elementInfo.id}`"
            :type="elementInfo.gradient.type"
            :colors="elementInfo.gradient.colors"
            :rotate="elementInfo.gradient.rotate"
          />
        </defs>
        <g :transform="`matrix(${elementInfo.width / elementInfo.viewBox[0]},0,0,${elementInfo.height / elementInfo.viewBox[1]},0,0)`">
          <path
            vector-effect="non-scaling-stroke"
            stroke-linecap="butt"
            stroke-miterlimit="8"
            :d="elementInfo.path"
            :fill="fill"
            :stroke="outlineColor"
            :stroke-width="outlineWidth"
            :stroke-dasharray="strokeDashArray"
          />
        </g>
      </svg>
      <div class="shape-text" :class="text.align">
        <div class="ProseMirror-static" v-html="text.content" />
      </div>
    </div>
  </div>
</template>

<script lang="ts" setup>
/**
 * BaseShapeElement — SVG 形状渲染（剥离绝对定位 + Store 改 props）
 * 来源：PPTist (MIT License)
 */
import { computed } from 'vue'
import type { PPTElementOutline, PPTElementShadow, Gradient, GradientColor, GradientType } from './types'
import useElementOutline from './hooks/useElementOutline'
import useElementShadow from './hooks/useElementShadow'
import useElementFlip from './hooks/useElementFlip'
import useElementFill from './hooks/useElementFill'
import GradientDefs from './GradientDefs.vue'
import PatternDefs from './PatternDefs.vue'

interface ShapeText {
  content: string
  defaultFontName: string
  defaultColor: string
  align: 'top' | 'middle' | 'bottom'
}

export interface ShapeElementInfo {
  id: string
  width: number
  height: number
  viewBox: [number, number]
  path: string
  fixedRatio?: boolean
  fill: string
  gradient?: Gradient
  pattern?: string
  outline?: PPTElementOutline
  opacity?: number
  flipH?: boolean
  flipV?: boolean
  shadow?: PPTElementShadow
  text?: ShapeText
  rotate?: number
}

const props = withDefaults(defineProps<{
  elementInfo: ShapeElementInfo
  themeFontName?: string
  themeFontColor?: string
}>(), {
  themeFontName: 'system-ui, sans-serif',
  themeFontColor: '#333',
})

const element = computed(() => props.elementInfo)
const { fill } = useElementFill(element, 'base')

const outline = computed(() => props.elementInfo.outline)
const { outlineWidth, outlineColor, strokeDashArray } = useElementOutline(outline)

const shadow = computed(() => props.elementInfo.shadow)
const { shadowStyle } = useElementShadow(shadow)

const flipH = computed(() => props.elementInfo.flipH)
const flipV = computed(() => props.elementInfo.flipV)
const { flipStyle } = useElementFlip(flipH, flipV)

const text = computed<ShapeText>(() => {
  const defaultText: ShapeText = {
    content: '',
    align: 'middle',
    defaultFontName: props.themeFontName,
    defaultColor: props.themeFontColor,
  }
  return props.elementInfo.text || defaultText
})
</script>

<style scoped>
.element-content { width: 100%; height: 100%; position: relative; }
.element-content svg { transform-origin: 0 0; overflow: visible; display: block; }
.shape-text {
  position: absolute; top: 0; bottom: 0; left: 0; right: 0;
  display: flex; flex-direction: column; padding: 10px; line-height: 1.2; word-break: break-word;
}
.shape-text.top { justify-content: flex-start; }
.shape-text.middle { justify-content: center; }
.shape-text.bottom { justify-content: flex-end; }
</style>
