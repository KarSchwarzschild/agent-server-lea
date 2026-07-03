<script setup lang="ts">
/**
 * SlidesPresenter — 在线演示播放器（PPT 在线演示模式）
 *
 * 从 :::slides JSON 数据渲染为可翻页的幻灯片播放器。
 * 复用 SlideRenderer，比 ClassroomPlayer 更轻量（无 TTS/Quiz/Discussion）。
 */
import { ref, computed } from 'vue'
import { NButton, NSpace } from 'naive-ui'
import SlideRenderer from './SlideRenderer.vue'
import type { SlideData, SlideTheme } from './SlideRenderer.vue'

const props = defineProps<{
  slidesData: {
    version?: number
    theme?: SlideTheme
    slides: Array<SlideData & { id?: string; speaker_notes?: string }>
    metadata?: Record<string, any>
  }
}>()

const currentIndex = ref(0)
const isFullscreen = ref(false)
const presenterRef = ref<HTMLElement>()

const currentSlide = computed(() => {
  if (!props.slidesData?.slides?.length) return null
  return props.slidesData.slides[currentIndex.value]
})

const total = computed(() => props.slidesData?.slides?.length || 0)
const canPrev = computed(() => currentIndex.value > 0)
const canNext = computed(() => currentIndex.value < total.value - 1)

function prev() { if (canPrev.value) currentIndex.value-- }
function next() { if (canNext.value) currentIndex.value++ }

function toggleFullscreen() {
  if (!presenterRef.value) return
  if (!document.fullscreenElement) {
    presenterRef.value.requestFullscreen().then(() => { isFullscreen.value = true })
  } else {
    document.exitFullscreen().then(() => { isFullscreen.value = false })
  }
}

function onKeyDown(e: KeyboardEvent) {
  if (e.key === 'ArrowLeft') prev()
  else if (e.key === 'ArrowRight' || e.key === ' ') { e.preventDefault(); next() }
  else if (e.key === 'Escape' && isFullscreen.value) document.exitFullscreen()
}
</script>

<template>
  <div
    v-if="slidesData?.slides?.length"
    ref="presenterRef"
    class="slides-presenter"
    :class="{ 'slides-presenter--fullscreen': isFullscreen }"
    tabindex="0"
    @keydown="onKeyDown"
  >
    <!-- 幻灯片主区域 -->
    <div class="sp-slide-area">
      <Transition name="sp-slide" mode="out-in">
        <SlideRenderer
          v-if="currentSlide"
          :key="currentSlide.id || currentIndex"
          :slide="currentSlide"
          :theme="slidesData.theme"
          mode="full"
        />
      </Transition>
    </div>

    <!-- 讲稿（如果有） -->
    <div v-if="currentSlide?.speaker_notes" class="sp-notes">
      {{ currentSlide.speaker_notes }}
    </div>

    <!-- 底部控制栏 -->
    <div class="sp-controls">
      <n-button size="small" :disabled="!canPrev" @click="prev">◁</n-button>
      <span class="sp-progress">{{ currentIndex + 1 }} / {{ total }}</span>
      <n-button size="small" :disabled="!canNext" @click="next">▷</n-button>
      <n-button size="small" @click="toggleFullscreen">
        {{ isFullscreen ? '退出全屏' : '⛶ 全屏' }}
      </n-button>
    </div>

    <!-- 缩略图条 -->
    <div v-if="total > 1" class="sp-thumbnails">
      <div
        v-for="(slide, idx) in slidesData.slides"
        :key="slide.id || idx"
        class="sp-thumb"
        :class="{ 'sp-thumb--active': idx === currentIndex }"
        @click="currentIndex = idx"
      >
        <SlideRenderer :slide="slide" :theme="slidesData.theme" mode="thumbnail" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.slides-presenter {
  border: 1px solid #e0e0e0;
  border-radius: 12px;
  overflow: hidden;
  background: #fafafa;
  margin: 12px 0;
  outline: none;
}

.slides-presenter--fullscreen {
  border: none;
  border-radius: 0;
  background: #000;
}

.sp-slide-area {
  max-width: 720px;
  margin: 0 auto;
  padding: 16px;
}
.slides-presenter--fullscreen .sp-slide-area {
  max-width: 100%;
  height: calc(100vh - 100px);
  display: flex;
  align-items: center;
  justify-content: center;
}

.sp-notes {
  padding: 8px 16px;
  font-size: 12px;
  color: #666;
  background: #f5f5f5;
  border-top: 1px solid #eee;
  max-height: 60px;
  overflow-y: auto;
  line-height: 1.5;
}

.sp-controls {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 8px;
  border-top: 1px solid #eee;
}
.sp-progress {
  font-size: 13px;
  color: #666;
  min-width: 50px;
  text-align: center;
}

.sp-thumbnails {
  display: flex;
  gap: 6px;
  padding: 8px 12px;
  overflow-x: auto;
  border-top: 1px solid #eee;
}
.sp-thumb {
  width: 80px;
  flex-shrink: 0;
  cursor: pointer;
  border: 2px solid transparent;
  border-radius: 4px;
  overflow: hidden;
  transition: border-color 0.2s;
}
.sp-thumb--active {
  border-color: #1a73e8;
}
.sp-thumb:hover {
  border-color: #90caf9;
}

/* Transition */
.sp-slide-enter-active, .sp-slide-leave-active {
  transition: all 0.25s ease;
}
.sp-slide-enter-from { opacity: 0; transform: translateX(20px); }
.sp-slide-leave-to { opacity: 0; transform: translateX(-20px); }
</style>
