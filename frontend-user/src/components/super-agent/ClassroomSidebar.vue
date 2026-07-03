<script setup lang="ts">
import { ref, computed, inject, onMounted, onBeforeUnmount, nextTick } from 'vue'
import SlideRenderer from './SlideRenderer.vue'
import type { SlideTheme } from './SlideRenderer.vue'
import type { UsePlaybackEngineReturn } from '../../composables/usePlaybackEngine'

// ─── Props & Emits ───

const props = defineProps<{
  scenes: Array<{
    index: number
    title: string
    scene_type: string
    status: string // 'outline_ready' | 'content_ready' | 'audio_ready' | 'ready' | 'failed' | 'generating'
    content: any | null
  }>
  currentIndex: number
  theme?: SlideTheme
}>()

const emit = defineEmits<{
  select: [index: number]
  retry: [index: number]
  regenerate: [index: number]
}>()

const regeneratingIndex = ref<number | null>(null)

const playback = inject<UsePlaybackEngineReturn>('playback')
const isLive = computed(() => playback?.engineMode.value === 'live')

// ─── Resize ───

const SIDEBAR_MIN = 170
const SIDEBAR_MAX = 400
const SIDEBAR_DEFAULT = 220

const sidebarWidth = ref(SIDEBAR_DEFAULT)
const isResizing = ref(false)
let startX = 0
let startWidth = 0

function onDragHandleMouseDown(e: MouseEvent) {
  isResizing.value = true
  startX = e.clientX
  startWidth = sidebarWidth.value
  e.preventDefault()
}

function onMouseMove(e: MouseEvent) {
  if (!isResizing.value) return
  const delta = e.clientX - startX
  const newWidth = Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, startWidth + delta))
  sidebarWidth.value = newWidth
}

function onMouseUp() {
  isResizing.value = false
}

onMounted(() => {
  document.addEventListener('mousemove', onMouseMove)
  document.addEventListener('mouseup', onMouseUp)
  setupObserver()
})

onBeforeUnmount(() => {
  document.removeEventListener('mousemove', onMouseMove)
  document.removeEventListener('mouseup', onMouseUp)
  observer?.disconnect()
})

// ─── IntersectionObserver lazy rendering ───

// Visible set: stores scene indices that should render thumbnails
const visibleIndices = ref<Set<number>>(new Set())
const itemRefs = ref<Map<number, HTMLElement>>(new Map())
let observer: IntersectionObserver | null = null

function setupObserver() {
  observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        const idx = parseInt((entry.target as HTMLElement).dataset.sceneIndex || '-1', 10)
        if (idx < 0) return

        if (entry.isIntersecting) {
          // Render visible ± 1
          const newSet = new Set(visibleIndices.value)
          newSet.add(idx)
          if (idx > 0) newSet.add(idx - 1)
          if (idx < props.scenes.length - 1) newSet.add(idx + 1)
          visibleIndices.value = newSet
        }
      })
    },
    { threshold: 0.01 }
  )

  // Observe all existing item refs
  itemRefs.value.forEach((el) => observer?.observe(el))
}

function registerItemRef(el: HTMLElement | null, sceneIndex: number) {
  if (el) {
    itemRefs.value.set(sceneIndex, el)
    observer?.observe(el)
  } else {
    const old = itemRefs.value.get(sceneIndex)
    if (old) observer?.unobserve(old)
    itemRefs.value.delete(sceneIndex)
  }
}

// ─── Computed helpers ───

const THUMB_CANVAS_WIDTH = 1000
const THUMB_CANVAS_HEIGHT = 562

function isSlideType(scene_type: string) {
  return scene_type === 'slide' || scene_type === 'slides'
}

function sceneEmoji(scene_type: string) {
  const map: Record<string, string> = {
    interactive: '🎛',
    pbl: '📋',
    video: '🎬',
    audio: '🎵',
    quiz: '📝',
    discussion: '💬',
    exercise: '✏️',
    intro: '📖',
    summary: '📋',
    activity: '🎯',
  }
  return map[scene_type] || '📄'
}

function sceneTypeLabel(scene_type: string) {
  const map: Record<string, string> = {
    interactive: '互动',
    pbl: '项目',
  }
  return map[scene_type] || scene_type
}

function isGenerating(status: string) {
  // content_ready / audio_ready / ready 都意味着内容已生成，不再是"生成中"
  return status === 'generating' || status === 'outline_ready' || status === 'pending'
}

function isFailed(status: string) {
  return status === 'failed'
}

function isReady(status: string) {
  return status === 'ready'
}

// Scale for thumbnail: fit into container width
function thumbScale(containerWidth: number) {
  return containerWidth / THUMB_CANVAS_WIDTH
}

// Thumbnail container height = 16:9 ratio of containerWidth
function thumbContainerHeight(containerWidth: number) {
  return (containerWidth / 16) * 9
}

// The inner render area: full 1000x562, scaled down
// containerWidth is the available width for the thumbnail (sidebar - padding)
const thumbnailInnerWidth = computed(() => sidebarWidth.value - 24) // 12px padding each side

/** 清除重新生成状态（父组件在 API 完成后调用）*/
function clearRegenerating() {
  regeneratingIndex.value = null
}
defineExpose({ clearRegenerating })
</script>

<template>
  <aside
    class="classroom-sidebar"
    :style="{ width: sidebarWidth + 'px' }"
    :class="{ 'is-resizing': isResizing }"
  >
    <!-- Drag Handle -->
    <div
      class="drag-handle"
      @mousedown="onDragHandleMouseDown"
    />

    <!-- Scroll Container -->
    <div class="sidebar-scroll">
      <div
        v-for="scene in scenes"
        :key="scene.index"
        :ref="(el) => registerItemRef(el as HTMLElement | null, scene.index)"
        :data-scene-index="scene.index"
        class="scene-item"
        :class="{
          'scene-item--active': scene.index === currentIndex,
          'scene-item--generating': isGenerating(scene.status),
          'scene-item--failed': isFailed(scene.status),
        }"
        @click="!isLive && !isGenerating(scene.status) && !isFailed(scene.status) && emit('select', scene.index)"
      >
        <!-- Header row: number + title -->
        <div class="scene-header">
          <span
            class="scene-number"
            :class="{ 'scene-number--active': scene.index === currentIndex }"
          >
            {{ scene.index + 1 }}
          </span>
          <span class="scene-title">{{ scene.title }}</span>
        </div>

        <!-- Thumbnail area -->
        <div
          class="thumb-wrapper"
          :style="{
            width: thumbnailInnerWidth + 'px',
            height: thumbContainerHeight(thumbnailInnerWidth) + 'px',
          }"
        >
          <!-- Generating skeleton -->
          <template v-if="isGenerating(scene.status)">
            <div class="thumb-skeleton">
              <div class="skeleton-bar skeleton-bar--wide" />
              <div class="skeleton-bar skeleton-bar--medium" />
              <div class="skeleton-bar skeleton-bar--narrow" />
            </div>
            <div class="generating-label">生成中...</div>
          </template>

          <!-- Failed state -->
          <template v-else-if="isFailed(scene.status)">
            <div class="thumb-failed">
              <span class="failed-icon">⚠</span>
              <span class="failed-text">生成失败</span>
              <button
                class="retry-btn"
                @click.stop="emit('retry', scene.index)"
              >
                🔄 重试
              </button>
            </div>
          </template>

          <!-- Slide thumbnail: only render if visible -->
          <template v-else-if="isSlideType(scene.scene_type) && scene.content">
            <template v-if="visibleIndices.has(scene.index)">
              <div
                class="thumb-slide-clip"
                :style="{
                  width: thumbnailInnerWidth + 'px',
                  height: thumbContainerHeight(thumbnailInnerWidth) + 'px',
                }"
              >
                <div
                  class="thumb-slide-inner"
                  :style="{
                    width: THUMB_CANVAS_WIDTH + 'px',
                    height: THUMB_CANVAS_HEIGHT + 'px',
                    transform: `scale(${thumbScale(thumbnailInnerWidth)})`,
                    transformOrigin: 'top left',
                  }"
                >
                  <SlideRenderer
                    :slide="scene.content"
                    :theme="props.theme"
                    mode="thumbnail"
                  />
                </div>
                <!-- 重新生成按钮（hover 显示）-->
                <button
                  v-if="regeneratingIndex !== scene.index"
                  class="regenerate-btn"
                  title="重新生成此场景"
                  @click.stop="regeneratingIndex = scene.index; emit('regenerate', scene.index)"
                >
                  ↻
                </button>
                <div v-else class="regenerating-overlay">
                  <span class="regenerating-spinner" />
                  <span class="regenerating-text">重新生成中...</span>
                </div>
              </div>
            </template>
            <!-- Lazy placeholder while not visible -->
            <template v-else>
              <div class="thumb-placeholder thumb-placeholder--slide">
                <span>📊</span>
              </div>
            </template>
          </template>

          <!-- Quiz 专用缩略图（橙色渐变 + 选项网格）-->
          <template v-else-if="scene.scene_type === 'quiz'">
            <div class="thumb-quiz">
              <div class="thumb-quiz-bar" />
              <div class="thumb-quiz-grid">
                <div class="thumb-quiz-opt" v-for="i in 4" :key="i" />
              </div>
            </div>
          </template>

          <!-- Discussion 专用缩略图（聊天气泡样）-->
          <template v-else-if="scene.scene_type === 'discussion'">
            <div class="thumb-discussion">
              <div class="thumb-bubble thumb-bubble--left" />
              <div class="thumb-bubble thumb-bubble--right" />
              <div class="thumb-bubble thumb-bubble--left thumb-bubble--short" />
            </div>
          </template>

          <!-- 其他类型 fallback -->
          <template v-else>
            <div class="thumb-placeholder">
              <span class="thumb-emoji">{{ sceneEmoji(scene.scene_type) }}</span>
              <span class="thumb-type-label">{{ sceneTypeLabel(scene.scene_type) }}</span>
            </div>
          </template>
        </div>
      </div>
    </div>
  </aside>
</template>

<style scoped>
/* ════════ Sidebar container ════════ */

.classroom-sidebar {
  position: relative;
  height: 100%;
  flex-shrink: 0;
  background: rgba(255, 255, 255, 0.8);
  backdrop-filter: blur(24px);
  -webkit-backdrop-filter: blur(24px);
  border-right: 1px solid rgba(0, 0, 0, 0.08);
  user-select: none;
  transition: width 0s; /* no transition during drag */
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.classroom-sidebar.is-resizing {
  cursor: col-resize;
}

/* ════════ Drag handle ════════ */

.drag-handle {
  position: absolute;
  top: 0;
  right: 0;
  width: 6px;
  height: 100%;
  cursor: col-resize;
  z-index: 10;
  background: transparent;
  transition: background 0.15s;
}

.drag-handle:hover,
.classroom-sidebar.is-resizing .drag-handle {
  background: rgba(59, 130, 246, 0.5); /* blue-500 @ 50% */
}

/* ════════ Scroll container ════════ */

.sidebar-scroll {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 8px 0 8px 0;
  scrollbar-width: thin;
  scrollbar-color: rgba(0, 0, 0, 0.15) transparent;
}

.sidebar-scroll::-webkit-scrollbar {
  width: 4px;
}
.sidebar-scroll::-webkit-scrollbar-thumb {
  background: rgba(0, 0, 0, 0.15);
  border-radius: 2px;
}

/* ════════ Scene item ════════ */

.scene-item {
  margin: 2px 8px;
  padding: 8px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.12s;
  border: 1px solid transparent;
}

.scene-item:hover:not(.scene-item--active):not(.scene-item--generating):not(.scene-item--failed) {
  background: rgba(0, 0, 0, 0.05);
}

.scene-item--active {
  background: #eff6ff; /* blue-50 */
  border-color: #bfdbfe; /* blue-200 */
  box-shadow: 0 0 0 1px #bfdbfe;
}

.scene-item--generating {
  opacity: 0.6;
  cursor: default;
}

.scene-item--failed {
  cursor: default;
}

/* ════════ Scene header ════════ */

.scene-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
}

.scene-number {
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-weight: 600;
  background: rgba(0, 0, 0, 0.08);
  color: #374151;
  transition: background 0.12s, color 0.12s;
}

.scene-number--active {
  background: #2563eb; /* blue-600 */
  color: #ffffff;
}

.scene-title {
  flex: 1;
  font-size: 12px;
  font-weight: 500;
  color: #374151;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  line-height: 1.4;
}

/* ════════ Thumbnail area ════════ */

.thumb-wrapper {
  position: relative;
  border-radius: 4px;
  overflow: hidden;
  background: #f3f4f6;
}

/* Slide thumbnail: clip + scale */
.thumb-slide-clip {
  position: relative;
  overflow: hidden;
  border-radius: 4px;
}

.thumb-slide-inner {
  position: absolute;
  top: 0;
  left: 0;
  pointer-events: none;
}

/* Emoji placeholder */
.thumb-placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  background: var(--cp-bg, #f9fafb);
  border-radius: 4px;
}

.thumb-emoji {
  font-size: 24px;
  line-height: 1;
}

.thumb-type-label {
  font-size: 10px;
  color: var(--cp-text-muted, #9ca3af);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.thumb-placeholder--slide .thumb-emoji {
  font-size: 20px;
}

/* Quiz 缩略图（对齐 OpenMAIC scene-sidebar.tsx: 橙色渐变 + 选项网格）*/
.thumb-quiz {
  width: 100%;
  height: 100%;
  background: linear-gradient(135deg, #fff7ed, #fef3c7);
  border-radius: 4px;
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.thumb-quiz-bar {
  height: 6px;
  width: 80%;
  background: rgba(245,158,11,0.3);
  border-radius: 3px;
}
.thumb-quiz-grid {
  flex: 1;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 4px;
}
.thumb-quiz-opt {
  background: rgba(245,158,11,0.15);
  border-radius: 3px;
  border: 1px solid rgba(245,158,11,0.2);
}

/* Discussion 缩略图（聊天气泡样）*/
.thumb-discussion {
  width: 100%;
  height: 100%;
  background: linear-gradient(135deg, #f0f4ff, #e8eeff);
  border-radius: 4px;
  padding: 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  justify-content: center;
}
.thumb-bubble {
  height: 8px;
  border-radius: 4px;
}
.thumb-bubble--left {
  width: 65%;
  background: rgba(124,58,237,0.15);
  align-self: flex-start;
}
.thumb-bubble--right {
  width: 50%;
  background: rgba(59,130,246,0.15);
  align-self: flex-end;
}
.thumb-bubble--short {
  width: 45%;
}

/* ════════ Generating skeleton ════════ */

.thumb-skeleton {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px;
  box-sizing: border-box;
}

.skeleton-bar {
  height: 8px;
  border-radius: 4px;
  background: linear-gradient(
    90deg,
    #e5e7eb 25%,
    #f3f4f6 50%,
    #e5e7eb 75%
  );
  background-size: 200% 100%;
  animation: shimmer 2s infinite;
}

.skeleton-bar--wide  { width: 85%; }
.skeleton-bar--medium { width: 65%; }
.skeleton-bar--narrow { width: 45%; }

@keyframes shimmer {
  0%   { background-position: 200% center; }
  100% { background-position: -200% center; }
}

.generating-label {
  position: absolute;
  bottom: 4px;
  left: 0;
  right: 0;
  text-align: center;
  font-size: 10px;
  color: #6b7280;
}

/* ════════ Failed state ════════ */

.thumb-failed {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  background: #fef2f2;
  border-radius: 4px;
}

.failed-icon {
  font-size: 18px;
  color: #ef4444;
}

.failed-text {
  font-size: 11px;
  color: #b91c1c;
}

.retry-btn {
  margin-top: 2px;
  padding: 2px 8px;
  font-size: 11px;
  border: 1px solid #fca5a5;
  border-radius: 4px;
  background: #ffffff;
  color: #ef4444;
  cursor: pointer;
  transition: background 0.1s;
}

.retry-btn:hover {
  background: #fef2f2;
}

/* ════════ Regenerate button (hover overlay) ════════ */

.regenerate-btn {
  position: absolute;
  top: 4px;
  right: 4px;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  border: none;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s, background 0.15s;
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 5;
}

.thumb-slide-clip:hover .regenerate-btn {
  opacity: 1;
}

.regenerate-btn:hover {
  background: rgba(37, 99, 235, 0.85);
}

/* Regenerating overlay */
.regenerating-overlay {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  border-radius: 4px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  z-index: 5;
}

.regenerating-spinner {
  width: 20px;
  height: 20px;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.regenerating-text {
  font-size: 11px;
  color: #fff;
}
</style>
