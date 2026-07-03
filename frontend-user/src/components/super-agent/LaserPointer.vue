<script setup lang="ts">
/**
 * LaserPointer — 激光笔效果
 * 红色圆点 + radial gradient 发光，跟踪目标元素中心，3s 自动消失
 */
import { ref, watch, nextTick, onUnmounted } from 'vue'

const props = defineProps<{
  elementId: string | null
  containerRef: HTMLElement | null
  color?: string
  duration?: number
}>()

const pos = ref<{ x: number; y: number } | null>(null)
const visible = ref(false)
let fadeTimer: ReturnType<typeof setTimeout> | null = null

function measure() {
  if (!props.elementId || !props.containerRef) {
    pos.value = null
    visible.value = false
    return
  }

  const el = props.containerRef.querySelector(`[data-element-id="${props.elementId}"]`)
  if (!el) {
    pos.value = null
    visible.value = false
    return
  }

  const cRect = props.containerRef.getBoundingClientRect()
  const eRect = el.getBoundingClientRect()

  pos.value = {
    x: eRect.left - cRect.left + eRect.width / 2,
    y: eRect.top - cRect.top + eRect.height / 2,
  }
  visible.value = true

  // 3s 后自动消失
  if (fadeTimer) clearTimeout(fadeTimer)
  fadeTimer = setTimeout(() => {
    visible.value = false
  }, props.duration || 3000)
}

watch(() => props.elementId, () => nextTick(measure))
onUnmounted(() => { if (fadeTimer) clearTimeout(fadeTimer) })

const laserColor = ref(props.color || '#ff0000')
</script>

<template>
  <Transition name="laser-fade">
    <div
      v-if="visible && pos"
      class="laser-dot"
      :style="{
        left: pos.x + 'px',
        top: pos.y + 'px',
        '--laser-color': laserColor,
      }"
    />
  </Transition>
</template>

<style scoped>
.laser-dot {
  position: absolute;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  transform: translate(-50%, -50%);
  z-index: 110;
  pointer-events: none;
  background: var(--laser-color, #ff0000);
  box-shadow:
    0 0 8px 4px var(--laser-color),
    0 0 20px 8px rgba(255, 0, 0, 0.3);
}

.laser-fade-enter-active { transition: all 0.15s ease; }
.laser-fade-leave-active { transition: all 0.5s ease; }
.laser-fade-enter-from { opacity: 0; transform: translate(-50%, -50%) scale(0.5); }
.laser-fade-leave-to { opacity: 0; transform: translate(-50%, -50%) scale(2); }
</style>
