<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  scene: {
    title: string
    content: Record<string, any>
  }
}>()

const content = computed(() => props.scene?.content || {})
const elements = computed(() => Array.isArray(content.value.elements) ? content.value.elements : [])

function elementStyle(element: any) {
  return {
    left: `${Number(element.x || 0) / 10}%`,
    top: `${Number(element.y || 0) / 5.62}%`,
    width: `${Number(element.width || 160) / 10}%`,
    minHeight: `${Number(element.height || 48)}px`,
  }
}

function elementLabel(element: any) {
  return element.text || element.latex || element.alt || ''
}
</script>

<template>
  <section class="whiteboard-view" aria-label="课堂白板">
    <header class="whiteboard-head">
      <span>课堂白板</span>
      <h3>{{ content.title || scene.title }}</h3>
      <p>{{ content.prompt }}</p>
    </header>
    <div class="whiteboard-canvas">
      <div
        v-for="element in elements"
        :key="element.id"
        class="whiteboard-element"
        :class="`wb-${element.type || 'text'}`"
        :style="elementStyle(element)"
        :data-test="`whiteboard-element-${element.id}`"
      >
        {{ elementLabel(element) }}
      </div>
    </div>
  </section>
</template>

<style scoped>
.whiteboard-view {
  width: min(900px, 100%);
  margin: 0 auto;
  padding: 18px;
  border: 1px solid #d8dee8;
  border-radius: 8px;
  background: #fff;
  color: #1f2937;
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.08);
}

.whiteboard-head {
  margin-bottom: 12px;
}

.whiteboard-head span {
  color: #7c3aed;
  font-size: 13px;
  font-weight: 700;
}

.whiteboard-head h3 {
  margin: 4px 0 6px;
}

.whiteboard-head p {
  margin: 0;
  color: #4b5563;
}

.whiteboard-canvas {
  position: relative;
  aspect-ratio: 1000 / 562;
  overflow: hidden;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  background: #fbfdff;
}

.whiteboard-element {
  position: absolute;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 8px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  background: #f8fafc;
  color: #111827;
  font-weight: 700;
  text-align: center;
}

.wb-sticky {
  background: #fef3c7;
  border-color: #f59e0b;
}

.wb-arrow {
  background: #eef2ff;
  border-color: #6366f1;
}

.wb-formula {
  background: #ecfdf5;
  border-color: #10b981;
}
</style>
