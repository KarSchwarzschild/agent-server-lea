<template>
  <div class="base-element-line">
    <div
      class="element-content"
      :style="{ filter: shadowStyle ? `drop-shadow(${shadowStyle})` : '' }"
    >
      <svg overflow="visible" :width="svgWidth" :height="svgHeight">
        <defs>
          <LinePointMarker
            v-if="elementInfo.points[0]"
            :id="elementInfo.id"
            position="start"
            :type="elementInfo.points[0]"
            :color="elementInfo.color"
            :baseSize="elementInfo.width"
          />
          <LinePointMarker
            v-if="elementInfo.points[1]"
            :id="elementInfo.id"
            position="end"
            :type="elementInfo.points[1]"
            :color="elementInfo.color"
            :baseSize="elementInfo.width"
          />
        </defs>
        <path
          :d="path"
          :stroke="elementInfo.color"
          :stroke-width="elementInfo.width"
          :stroke-dasharray="lineDashArray"
          fill="none"
          :marker-start="elementInfo.points[0] ? `url(#${elementInfo.id}-${elementInfo.points[0]}-start)` : ''"
          :marker-end="elementInfo.points[1] ? `url(#${elementInfo.id}-${elementInfo.points[1]}-end)` : ''"
        />
      </svg>
    </div>
  </div>
</template>

<script lang="ts" setup>
/**
 * BaseLineElement — 线条/箭头渲染（剥离绝对定位）
 * 来源：PPTist (MIT License)
 */
import { computed } from 'vue'
import type { PPTElementShadow } from './types'
import useElementShadow from './hooks/useElementShadow'
import LinePointMarker from './LinePointMarker.vue'

type LinePoint = '' | 'arrow' | 'dot'

export interface LineElementInfo {
  id: string
  start: [number, number]
  end: [number, number]
  width: number
  style: 'solid' | 'dashed' | 'dotted'
  color: string
  points: [LinePoint, LinePoint]
  shadow?: PPTElementShadow
  broken?: [number, number]
  curve?: [number, number]
  cubic?: [[number, number], [number, number]]
}

const props = defineProps<{ elementInfo: LineElementInfo }>()

const shadow = computed(() => props.elementInfo.shadow)
const { shadowStyle } = useElementShadow(shadow)

const svgWidth = computed(() => {
  const w = Math.abs(props.elementInfo.start[0] - props.elementInfo.end[0])
  return w < 24 ? 24 : w
})
const svgHeight = computed(() => {
  const h = Math.abs(props.elementInfo.start[1] - props.elementInfo.end[1])
  return h < 24 ? 24 : h
})

const lineDashArray = computed(() => {
  const size = props.elementInfo.width
  if (props.elementInfo.style === 'dashed') return size <= 8 ? `${size * 5} ${size * 2.5}` : `${size * 5} ${size * 1.5}`
  if (props.elementInfo.style === 'dotted') return size <= 8 ? `${size * 1.8} ${size * 1.6}` : `${size * 1.5} ${size * 1.2}`
  return '0 0'
})

const path = computed(() => {
  const start = props.elementInfo.start.join(',')
  const end = props.elementInfo.end.join(',')
  if (props.elementInfo.broken) return `M${start} L${props.elementInfo.broken.join(',')} L${end}`
  if (props.elementInfo.curve) return `M${start} Q${props.elementInfo.curve.join(',')} ${end}`
  if (props.elementInfo.cubic) {
    const [c1, c2] = props.elementInfo.cubic
    return `M${start} C${c1.join(',')},${c2.join(',')},${end}`
  }
  return `M${start} L${end}`
})
</script>

<style scoped>
.element-content { width: 100%; height: 100%; position: relative; }
.element-content svg { transform-origin: 0 0; overflow: visible; }
</style>
