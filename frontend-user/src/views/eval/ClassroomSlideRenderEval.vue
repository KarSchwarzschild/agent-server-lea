<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import SlideRenderer from '../../components/super-agent/SlideRenderer.vue'

type EvalSlidePayload = {
  case_id?: string
  scene_index?: number | string
  slide?: Record<string, any>
} & Record<string, any>

const current = ref<{
  case_id: string
  scene_index: number
  slide: Record<string, any>
}>({
  case_id: '',
  scene_index: 0,
  slide: {
    background: { type: 'solid', color: '#ffffff' },
    elements: [],
  },
})
const rootRef = ref<HTMLElement | null>(null)
const lastSetAt = ref(0)

async function setSlide(payload: EvalSlidePayload) {
  current.value = {
    case_id: String(payload?.case_id || ''),
    scene_index: Number(payload?.scene_index || 0),
    slide: payload?.slide || payload,
  }
  lastSetAt.value = Date.now()
  await nextTick()
}

function clippedElements() {
  const root = rootRef.value
  if (!root) return []
  const tolerance = 2
  const contentTypes = new Set(['text', 'table', 'latex', 'chart', 'image'])
  return Array.from(root.querySelectorAll<HTMLElement>('.screen-element'))
    .map(element => {
      if (!contentTypes.has(element.dataset.elementType || '')) return null
      const rect = element.getBoundingClientRect()
      const childOverflow = Array.from(element.children).some(child => {
        const childRect = child.getBoundingClientRect()
        return (
          childRect.right > rect.right + tolerance
          || childRect.bottom > rect.bottom + tolerance
          || childRect.left < rect.left - tolerance
          || childRect.top < rect.top - tolerance
        )
      })
      const scrollOverflow = (
        element.scrollWidth > element.clientWidth + tolerance
        || element.scrollHeight > element.clientHeight + tolerance
      )
      if (!childOverflow && !scrollOverflow) return null
      return {
        id: element.dataset.elementId || '',
        type: element.dataset.elementType || '',
        client_width: element.clientWidth,
        client_height: element.clientHeight,
        scroll_width: element.scrollWidth,
        scroll_height: element.scrollHeight,
      }
    })
    .filter(Boolean)
}

function evalSlideState() {
  const clipped = clippedElements()
  return {
    case_id: current.value.case_id,
    scene_index: current.value.scene_index,
    visible_element_count: current.value.slide?.elements?.length || 0,
    width: rootRef.value?.clientWidth || 0,
    height: rootRef.value?.clientHeight || 0,
    clipped_element_count: clipped.length,
    clipped_elements: clipped,
    last_set_at: lastSetAt.value,
  }
}

onMounted(() => {
  ;(window as any).__setSlide = setSlide
  ;(window as any).__evalSlideState = evalSlideState
  ;(window as any).__evalReady = true
})

onBeforeUnmount(() => {
  delete (window as any).__setSlide
  delete (window as any).__evalSlideState
  delete (window as any).__evalReady
})
</script>

<template>
  <main class="eval-page">
    <section
      ref="rootRef"
      class="eval-slide-root"
      data-eval-slide-root="true"
      :data-eval-case-id="current.case_id"
      :data-eval-scene-index="String(current.scene_index)"
    >
      <SlideRenderer :slide="current.slide" mode="full" />
    </section>
  </main>
</template>

<style scoped>
.eval-page {
  margin: 0;
  min-height: 100vh;
  display: grid;
  place-items: center;
  background: #f4f4f5;
}

.eval-slide-root {
  width: 1000px;
  height: 562px;
  overflow: hidden;
  background: #fff;
}

.eval-slide-root :deep(.slide-canvas) {
  width: 100%;
  height: 100%;
}
</style>
