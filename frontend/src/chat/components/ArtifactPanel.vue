<template>
  <div v-if="slides.length" class="artifact-panel">
    <div class="artifact-header">
      <span class="artifact-title">演示文稿 ({{ slides.length }}页)</span>
      <button class="download-btn" @click="handleDownload" :disabled="exporting">
        {{ exporting ? '导出中...' : '下载 PPTX' }}
      </button>
    </div>

    <!-- 缩略图网格 -->
    <div class="thumbnail-grid">
      <div
        v-for="(svg, idx) in normalizedSlides"
        :key="idx"
        :class="['thumbnail-item', { active: selectedIndex === idx, updated: updatedIndex === idx }]"
        @click="selectedIndex = idx"
      >
        <div class="thumbnail-svg" v-html="svg"></div>
        <span class="page-num">{{ idx + 1 }}</span>
      </div>
    </div>

    <!-- 大图预览 -->
    <div v-if="selectedIndex !== null" class="preview-overlay" @click.self="selectedIndex = null">
      <div class="preview-content">
        <div class="preview-header">
          <span>第 {{ selectedIndex + 1 }}/{{ slides.length }} 页</span>
          <button class="close-btn" @click="selectedIndex = null">✕</button>
        </div>
        <div class="preview-svg" v-html="normalizedSlides[selectedIndex]"></div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'

const props = defineProps<{
  slides: string[]
  artifactId: string
}>()

const emit = defineEmits<{
  download: [artifactId: string]
}>()

const selectedIndex = ref<number | null>(null)
const updatedIndex = ref<number | null>(null)
const exporting = ref(false)

function normalizeSvg(svg: string): string {
  return svg.replace(/<svg([^>]*)>/, (match, attrs) => {
    const cleaned = attrs
      .replace(/\s+width\s*=\s*"[^"]*"/g, '')
      .replace(/\s+height\s*=\s*"[^"]*"/g, '')
    return `<svg${cleaned}>`
  })
}

const normalizedSlides = computed(() => props.slides.map(normalizeSvg))

function highlightUpdated(index: number) {
  updatedIndex.value = index
  selectedIndex.value = index
  setTimeout(() => { updatedIndex.value = null }, 1500)
}

async function handleDownload() {
  exporting.value = true
  try {
    const response = await fetch(`/api/chat/artifacts/${props.artifactId}/export?format=pptx`, {
      method: 'POST',
    })
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `slides_${props.artifactId}.pptx`
    a.click()
    URL.revokeObjectURL(url)
  } finally {
    exporting.value = false
  }
}

defineExpose({ highlightUpdated })
</script>

<style scoped>
.artifact-panel {
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  padding: 16px;
  margin: 12px 0;
  background: #fafafa;
}

.artifact-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.artifact-title {
  font-size: 14px;
  font-weight: 600;
  color: #374151;
}

.download-btn {
  font-size: 12px;
  padding: 4px 12px;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  background: #fff;
  cursor: pointer;
  color: #374151;
}

.download-btn:hover { background: #f3f4f6; }
.download-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.thumbnail-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
}

.thumbnail-item {
  position: relative;
  border: 2px solid #e5e7eb;
  border-radius: 8px;
  overflow: hidden;
  cursor: pointer;
  aspect-ratio: 16/9;
  transition: border-color 0.2s, box-shadow 0.2s;
}

.thumbnail-item:hover { border-color: #93c5fd; }
.thumbnail-item.active { border-color: #3b82f6; box-shadow: 0 0 0 2px rgba(59,130,246,0.2); }

.thumbnail-item.updated {
  animation: flash 1.5s ease;
}

@keyframes flash {
  0%, 100% { border-color: #3b82f6; }
  50% { border-color: #10b981; box-shadow: 0 0 8px rgba(16,185,129,0.4); }
}

.thumbnail-svg {
  width: 100%;
  height: 100%;
}

.thumbnail-svg :deep(svg) {
  width: 100%;
  height: 100%;
  display: block;
  max-width: 100%;
  max-height: 100%;
}

.page-num {
  position: absolute;
  bottom: 2px;
  right: 4px;
  font-size: 10px;
  color: #6b7280;
  background: rgba(255,255,255,0.8);
  padding: 0 4px;
  border-radius: 3px;
}

.preview-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.6);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.preview-content {
  background: #fff;
  border-radius: 12px;
  padding: 16px;
  max-width: 90vw;
  max-height: 90vh;
  overflow: auto;
}

.preview-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
  font-size: 14px;
  color: #374151;
}

.close-btn {
  background: none;
  border: none;
  font-size: 18px;
  cursor: pointer;
  color: #6b7280;
}

.preview-svg :deep(svg) {
  width: 100%;
  max-width: 960px;
  height: auto;
  display: block;
  max-height: 80vh;
}
</style>
