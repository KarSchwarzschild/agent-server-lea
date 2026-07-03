<template>
  <svg
    class="element-outline"
    v-if="outline"
    overflow="visible"
    :width="width"
    :height="height"
  >
    <path
      vector-effect="non-scaling-stroke"
      stroke-linecap="butt"
      stroke-miterlimit="8"
      fill="transparent"
      :d="`M0,0 L${width},0 L${width},${height} L0,${height} Z`"
      :stroke="outlineColor"
      :stroke-width="outlineWidth"
      :stroke-dasharray="strokeDashArray"
    />
  </svg>
</template>

<script lang="ts" setup>
/** 来源：PPTist (MIT License) — 元素边框 SVG */
import { toRef } from 'vue'
import type { PPTElementOutline } from './types'
import useElementOutline from './hooks/useElementOutline'

const props = defineProps<{
  width: number
  height: number
  outline?: PPTElementOutline
}>()

const { outlineWidth, outlineColor, strokeDashArray } = useElementOutline(toRef(props, 'outline'))
</script>

<style scoped>
svg { overflow: visible; position: absolute; top: 0; left: 0; }
</style>
