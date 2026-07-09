<script setup lang="ts">
/**
 * SlideArtifactViewer — 在 ArtifactPanel 内渲染 slides 类型 artifact。
 *
 * 数据源：artifactStore.currentContent (完成态) 或 streamingContent (流式态)
 * 数据格式：SlideData JSON（对齐 backend slide_generator.py 输出 + SlidesPresenter props）
 *
 * B1 一次全推：delta 只推一次完整 JSON，所以解析成功率高。
 * 兜底：JSON.parse 失败时显示"生成中/加载失败"状态。
 */
import { computed } from 'vue'
import { useArtifactStore } from '@/stores/artifact'
import SlidesPresenter from '@/components/super-agent/SlidesPresenter.vue'

const artifactStore = useArtifactStore()

const contentString = computed(() => {
  return (
    artifactStore.currentContent?.content
    || artifactStore.streamingContent
    || ''
  )
})

interface SlidesArtifactPayload {
  version?: number
  theme?: Record<string, any>
  metadata?: Record<string, any>
  slides?: Array<Record<string, any>>
}

const parsed = computed<SlidesArtifactPayload | null>(() => {
  const s = contentString.value
  if (!s) return null
  try {
    const obj = JSON.parse(s)
    if (obj && typeof obj === 'object' && Array.isArray(obj.slides)) {
      return obj as SlidesArtifactPayload
    }
    return null
  } catch {
    return null
  }
})

const hasSlides = computed(() => !!parsed.value?.slides?.length)

const isGenerating = computed(() => {
  return (
    !hasSlides.value
    && (artifactStore.isStreaming || artifactStore._isBackgroundStreaming)
  )
})
</script>

<template>
  <div class="slide-artifact-viewer">
    <SlidesPresenter
      v-if="hasSlides"
      :slides-data="parsed as any"
    />
    <div v-else-if="isGenerating" class="viewer-loading">
      <div class="loading-spinner" />
      <span>正在生成演示文稿...</span>
    </div>
    <div v-else class="viewer-error">
      <div class="viewer-error-icon">⚠</div>
      <div>演示文稿加载失败</div>
      <div class="viewer-error-hint">JSON 数据解析失败，请重新生成</div>
    </div>
  </div>
</template>

<style scoped>
.slide-artifact-viewer {
  height: 100%;
  padding: 12px 16px;
  overflow-y: auto;
  background: #fafafa;
}

.viewer-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 80px 20px;
  color: #6b7280;
  font-size: 14px;
}
.loading-spinner {
  width: 32px;
  height: 32px;
  border: 3px solid #dbeafe;
  border-top-color: #2563eb;
  border-radius: 50%;
  animation: viewer-spin 0.8s linear infinite;
}
@keyframes viewer-spin {
  to { transform: rotate(360deg); }
}

.viewer-error {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 60px 20px;
  color: #ef4444;
  font-size: 14px;
}
.viewer-error-icon {
  font-size: 32px;
}
.viewer-error-hint {
  font-size: 12px;
  color: #9ca3af;
}
</style>
