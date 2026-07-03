<script setup lang="ts">
/**
 * SlideRenderer — 幻灯片渲染组件（双模式）
 *
 * 模式 1（Canvas 画布）: PPTSlide 格式 → ScreenElement 绝对定位渲染
 *   - 虚拟画布 1000×562，通过 canvasScale 缩放适配容器
 *   - 每个元素通过 position: absolute + left/top/width/height 精确定位
 *
 * 模式 2（Legacy CSS Grid）: SlideData 格式 → ElementAdapter 布局渲染
 *   - 向后兼容历史数据
 *
 * 检测逻辑: elements[0].left !== undefined → Canvas 模式
 */
import { computed, ref, onMounted, onBeforeUnmount } from 'vue'
import ElementAdapter from './pptist-elements/ElementAdapter.vue'
import ScreenElement from './pptist-elements/ScreenElement.vue'
import type { PPTSlide, PPTSlideBackground } from './pptist-elements/types'

// ─── 旧格式类型（向后兼容）───

export interface SlideElement {
  type: 'heading' | 'text' | 'list' | 'latex' | 'callout' | 'image' | 'mermaid_svg' | 'code' | 'table' | 'chart'
  content?: string
  items?: string[]
  level?: number
  style?: string
  display?: string
  ordered?: boolean
  column?: 'L' | 'R' | 'A' | 'B'
  url?: string
  caption?: string
  alt?: string
  svg?: string
  language?: string
  headers?: string[]
  rows?: string[][]
  chartType?: string
  data?: { labels: string[]; legends: string[]; series: number[][] }
}

export interface SlideData {
  layout: 'title' | 'content' | 'two_column' | 'image_left' | 'comparison' | 'blank'
  title?: string
  subtitle?: string
  elements: SlideElement[]
  column_labels?: { L?: string; R?: string; A?: string; B?: string }
  speaker_notes?: string
}

export interface SlideTheme {
  id?: string
  primary?: string
  secondary?: string
  background?: string
  text?: string
}

// ─── Props ───

const CANVAS_WIDTH = 1000
const CANVAS_HEIGHT = 562

const props = withDefaults(defineProps<{
  slide: SlideData | PPTSlide | Record<string, any>
  theme?: SlideTheme
  mode?: 'full' | 'thumbnail'
}>(), {
  mode: 'full',
  theme: () => ({
    id: 'edu-warm',
    primary: '#1a73e8',
    secondary: '#fbbc04',
    background: '#ffffff',
    text: '#202124',
  }),
})

// ─── 模式检测 ───

const isCanvasMode = computed(() => {
  const s = props.slide as any
  // PPTSlide 格式：有 background 或 elements[0] 有 left 属性
  if (s.background && s.elements) return true
  if (s.elements?.length > 0 && s.elements[0].left !== undefined) return true
  return false
})

// ─── Canvas 模式 ───

const canvasRef = ref<HTMLElement | null>(null)
const canvasScale = ref(1)

function updateScale() {
  if (!canvasRef.value) return
  canvasScale.value = canvasRef.value.clientWidth / CANVAS_WIDTH
}

let resizeObserver: ResizeObserver | null = null

onMounted(() => {
  if (canvasRef.value) {
    resizeObserver = new ResizeObserver(updateScale)
    resizeObserver.observe(canvasRef.value)
    updateScale()
  }
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
})

const pptSlide = computed(() => props.slide as PPTSlide)

const bgStyle = computed(() => {
  if (!isCanvasMode.value) return ''
  const bg = (props.slide as PPTSlide).background
  if (!bg) return '#ffffff'

  if (bg.type === 'solid') {
    return bg.color || '#ffffff'
  }

  if (bg.type === 'gradient' && bg.gradient) {
    const g = bg.gradient
    const stops = g.colors
      .map(c => `${c.color} ${c.pos * 100}%`)
      .join(', ')
    if (g.type === 'radial') {
      return `radial-gradient(circle, ${stops})`
    }
    return `linear-gradient(${g.rotate || 0}deg, ${stops})`
  }

  // 图片背景（对齐 OpenMAIC use-slide-background-style.ts）
  if (bg.type === 'image' && bg.image) {
    return '#ffffff' // 颜色由 bgImageStyle 处理
  }

  return '#ffffff'
})

const bgImageStyle = computed(() => {
  if (!isCanvasMode.value) return null
  const bg = (props.slide as PPTSlide).background
  if (!bg || bg.type !== 'image' || !bg.image) return null

  const src = bg.image
  const size = bg.imageSize || 'cover'

  if (size === 'repeat') {
    return {
      backgroundImage: `url(${src})`,
      backgroundRepeat: 'repeat',
      backgroundSize: 'contain',
    }
  }
  return {
    backgroundImage: `url(${src})`,
    backgroundRepeat: 'no-repeat',
    backgroundSize: size,
  }
})

// ─── Legacy CSS Grid 模式 ───

const primaryColor = computed(() => props.theme?.primary || '#1a73e8')

const cssVars = computed(() => ({
  '--slide-primary': primaryColor.value,
  '--slide-secondary': props.theme?.secondary || '#fbbc04',
  '--slide-bg': props.theme?.background || '#ffffff',
  '--slide-text': props.theme?.text || '#202124',
}))

const legacySlide = computed(() => props.slide as SlideData)
const leftElements = computed(() => legacySlide.value.elements?.filter(e => e.column === 'L' || e.column === 'A') || [])
const rightElements = computed(() => legacySlide.value.elements?.filter(e => e.column === 'R' || e.column === 'B') || [])
const ungroupedElements = computed(() => legacySlide.value.elements?.filter(e => !e.column) || [])
</script>

<template>
  <!-- ════════ Canvas 画布模式（PPTSlide）════════ -->
  <div
    v-if="isCanvasMode"
    ref="canvasRef"
    class="slide-canvas"
    :class="[`slide-canvas--${mode}`]"
  >
    <!-- 外层 viewport：缩放后尺寸，负责裁切 + 圆角 + 阴影 + 背景 -->
    <div
      class="canvas-viewport"
      :style="{ width: CANVAS_WIDTH * canvasScale + 'px', height: CANVAS_HEIGHT * canvasScale + 'px', background: bgStyle || '#ffffff', ...bgImageStyle }"
    >
      <!-- 内层 content：原始 1000×562 坐标系，整体 transform: scale 缩放（对齐 OpenMAIC） -->
      <div
        class="canvas-content"
        :style="{
          width: CANVAS_WIDTH + 'px',
          height: CANVAS_HEIGHT + 'px',
          transform: `scale(${canvasScale})`,
        }"
      >
        <ScreenElement
          v-for="(el, i) in pptSlide.elements"
          :key="(el as any).id || i"
          :elementInfo="el"
          :elementIndex="i"
          :canvasScale="1"
        />
      </div>
    </div>
  </div>

  <!-- ════════ Legacy CSS Grid 模式（SlideData）════════ -->
  <div
    v-else
    class="slide-renderer"
    :class="[`slide-renderer--${mode}`, `layout-${legacySlide.layout}`]"
    :style="cssVars"
  >
    <!-- Title Layout -->
    <div v-if="legacySlide.layout === 'title'" class="slide-title-layout">
      <h1 class="slide-main-title">{{ legacySlide.title }}</h1>
      <p v-if="legacySlide.subtitle" class="slide-subtitle">{{ legacySlide.subtitle }}</p>
      <div v-if="ungroupedElements.length" class="slide-title-elements">
        <ElementAdapter v-for="(el, i) in ungroupedElements" :key="i" :el="el" :primaryColor="primaryColor" :target="mode" />
      </div>
    </div>

    <!-- Content Layout -->
    <div v-else-if="legacySlide.layout === 'content'" class="slide-content-layout">
      <h2 v-if="legacySlide.title" class="slide-heading">{{ legacySlide.title }}</h2>
      <div class="slide-elements">
        <ElementAdapter
          v-for="(el, i) in ungroupedElements.length ? ungroupedElements : legacySlide.elements"
          :key="i"
          :el="el"
          :primaryColor="primaryColor"
          :target="mode"
        />
      </div>
    </div>

    <!-- Two Column / Comparison Layout -->
    <div v-else-if="legacySlide.layout === 'two_column' || legacySlide.layout === 'comparison'" class="slide-two-col-layout">
      <h2 v-if="legacySlide.title" class="slide-heading">{{ legacySlide.title }}</h2>
      <div class="slide-columns">
        <div class="slide-col slide-col--left">
          <div v-if="legacySlide.column_labels?.L || legacySlide.column_labels?.A" class="col-label">
            {{ legacySlide.column_labels?.L || legacySlide.column_labels?.A }}
          </div>
          <ElementAdapter v-for="(el, i) in leftElements" :key="'l'+i" :el="el" :primaryColor="primaryColor" :target="mode" />
        </div>
        <div class="slide-col slide-col--right">
          <div v-if="legacySlide.column_labels?.R || legacySlide.column_labels?.B" class="col-label">
            {{ legacySlide.column_labels?.R || legacySlide.column_labels?.B }}
          </div>
          <ElementAdapter v-for="(el, i) in rightElements" :key="'r'+i" :el="el" :primaryColor="primaryColor" :target="mode" />
        </div>
      </div>
    </div>

    <!-- Image Left Layout -->
    <div v-else-if="legacySlide.layout === 'image_left'" class="slide-image-left-layout">
      <h2 v-if="legacySlide.title" class="slide-heading">{{ legacySlide.title }}</h2>
      <div class="slide-image-left-body">
        <div class="slide-image-left-img">
          <ElementAdapter v-for="(el, i) in leftElements" :key="'l'+i" :el="el" :primaryColor="primaryColor" :target="mode" />
        </div>
        <div class="slide-image-left-content">
          <ElementAdapter v-for="(el, i) in rightElements" :key="'r'+i" :el="el" :primaryColor="primaryColor" :target="mode" />
        </div>
      </div>
    </div>

    <!-- Blank / Other layouts → fallback -->
    <div v-else class="slide-content-layout">
      <h2 v-if="legacySlide.title" class="slide-heading">{{ legacySlide.title }}</h2>
      <div class="slide-elements">
        <ElementAdapter v-for="(el, i) in legacySlide.elements" :key="i" :el="el" :primaryColor="primaryColor" :target="mode" />
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ════════ Canvas 画布模式 ════════ */

.slide-canvas {
  aspect-ratio: 16 / 9;
  width: 100%;
  position: relative;
  overflow: hidden;
  font-family: 'Microsoft YaHei', '微软雅黑', 'PingFang SC', 'Hiragino Sans GB', 'Noto Sans SC', system-ui, -apple-system, sans-serif;
}

.slide-canvas--thumbnail {
  border-radius: 4px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.1);
}

.canvas-viewport {
  position: relative;
  overflow: hidden;
}

/* full 模式：viewport 自带圆角 + 阴影（对齐 OpenMAIC ScreenCanvas） */
.slide-canvas--full .canvas-viewport {
  border-radius: 8px;
  box-shadow:
    0 0 0 1px rgba(0, 0, 0, 0.01),
    0 0 12px 0 rgba(0, 0, 0, 0.1);
}

.canvas-content {
  position: absolute;
  top: 0;
  left: 0;
  transform-origin: top left;
  overflow: hidden;
  box-sizing: border-box;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  text-rendering: optimizeLegibility;
}

.canvas-content :deep(*),
.canvas-content :deep(*::before),
.canvas-content :deep(*::after) {
  box-sizing: inherit;
}

/* ════════ Legacy CSS Grid 模式 ════════ */

.slide-renderer {
  background: var(--slide-bg, #fff);
  color: var(--slide-text, #202124);
  aspect-ratio: 16 / 9;
  padding: 40px 48px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  font-family: 'Microsoft YaHei', '微软雅黑', 'PingFang SC', 'Hiragino Sans GB', 'Noto Sans SC', system-ui, -apple-system, sans-serif;
  border-radius: 8px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
}

.slide-renderer--thumbnail {
  padding: 8px 10px;
  font-size: 4px;
  border-radius: 4px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.1);
}

/* Title layout */
.slide-title-layout {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  gap: 8px;
}
.slide-main-title {
  font-size: 2.2em;
  font-weight: 700;
  color: var(--slide-primary);
  margin: 0 0 8px;
  line-height: 1.2;
}
.slide-subtitle {
  font-size: 1.1em;
  color: #666;
  margin: 0;
}
.slide-title-elements {
  margin-top: 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-width: 80%;
}

/* Content layout */
.slide-content-layout {
  flex: 1;
  display: flex;
  flex-direction: column;
}
.slide-heading {
  font-size: 1.5em;
  font-weight: 600;
  color: var(--slide-primary);
  margin: 0 0 20px;
  padding-bottom: 8px;
  border-bottom: 3px solid var(--slide-primary);
}
.slide-elements {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* Two column */
.slide-two-col-layout {
  flex: 1;
  display: flex;
  flex-direction: column;
}
.slide-columns {
  flex: 1;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 32px;
  margin-top: 16px;
}
.slide-col {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.col-label {
  font-weight: 600;
  color: var(--slide-primary);
  padding: 4px 12px;
  background: color-mix(in srgb, var(--slide-primary) 10%, transparent);
  border-radius: 4px;
  font-size: 0.9em;
}

/* Image left layout */
.slide-image-left-layout {
  flex: 1;
  display: flex;
  flex-direction: column;
}
.slide-image-left-body {
  flex: 1;
  display: grid;
  grid-template-columns: 2fr 3fr;
  gap: 28px;
  margin-top: 16px;
}
.slide-image-left-img {
  display: flex;
  align-items: center;
  justify-content: center;
}
.slide-image-left-content {
  display: flex;
  flex-direction: column;
  gap: 12px;
  justify-content: center;
}

/* Thumbnail overrides */
.slide-renderer--thumbnail .slide-main-title { font-size: 8px; }
.slide-renderer--thumbnail .slide-heading { font-size: 6px; border-bottom-width: 1px; }
</style>
