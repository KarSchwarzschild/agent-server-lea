<template>
  <div class="studio-panel">
    <!-- 工具菜单 -->
    <template v-if="!previewSource">
      <h1 class="studio-title">Studio</h1>
      <div class="tool-grid">
        <div
          v-for="tool in tools"
          :key="tool.key"
          class="tool-card"
          @click="$emit('toolClick', tool.key)"
        >
          <span class="tool-icon">{{ tool.icon }}</span>
          <span class="tool-label">{{ tool.label }}</span>
        </div>
      </div>
    </template>

    <!-- 文档预览 -->
    <template v-else>
      <div class="preview-top">
        <button class="back-btn" @click="$emit('closePreview')">&larr; 返回</button>
        <h2 class="preview-filename">{{ previewSource.filename }}</h2>
      </div>

      <div class="preview-scroll">
        <div v-if="previewSource.summary" class="section">
          <h3 class="section-label">来源指南</h3>
          <p class="section-text">{{ previewSource.summary }}</p>
        </div>

        <div v-if="previewSource.key_topics?.length" class="section">
          <h3 class="section-label">关键主题</h3>
          <div class="topic-tags">
            <span v-for="t in previewSource.key_topics" :key="t" class="topic-chip">{{ t }}</span>
          </div>
        </div>

        <div v-if="previewSource.parsed_content" class="section doc-body">
          <h3 class="section-label">文档正文</h3>
          <div class="body-text" v-html="renderedContent" />
        </div>

        <div v-else-if="previewSource.status === 'processing'" class="loading-hint">
          <n-spin size="small" />
          <span style="margin-left: 8px; opacity: 0.6">解析中...</span>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { NSpin } from 'naive-ui'
import type { Source } from '../composables/useNotebook'

const props = defineProps<{
  previewSource: Source | null
}>()

defineEmits<{
  toolClick: [key: string]
  closePreview: []
}>()

const tools = [
  { key: 'flashcards', icon: '🎴', label: 'Flashcards' },
  { key: 'mindmap', icon: '🧠', label: '思维导图' },
  { key: 'quiz', icon: '📝', label: '自测题' },
  { key: 'ppt', icon: '📊', label: 'PPT 生成' },
]

const renderedContent = computed(() => {
  if (!props.previewSource?.parsed_content) return ''
  return props.previewSource.parsed_content
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\n/g, '<br>')
})
</script>

<style scoped>
.studio-panel {
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 16px;
}

.studio-title {
  font-size: 20px;
  font-weight: 700;
  margin: 0 0 16px;
}

.tool-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.tool-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 20px 8px;
  border-radius: 12px;
  cursor: pointer;
  transition: all 0.2s;
  background: #fff;
  border: 1px solid #e5e5e5;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.06);
}

.tool-card:hover {
  border-color: #c0c0c0;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
  transform: translateY(-1px);
}

.tool-icon {
  font-size: 28px;
}

.tool-label {
  font-size: 13px;
  font-weight: 500;
}

/* 预览 */
.preview-top {
  margin-bottom: 16px;
}

.back-btn {
  background: none;
  border: none;
  color: var(--n-text-color-3);
  cursor: pointer;
  padding: 0;
  font-size: 13px;
  margin-bottom: 8px;
}

.back-btn:hover {
  color: var(--n-text-color);
}

.preview-filename {
  font-size: 17px;
  font-weight: 700;
  margin: 0;
}

.preview-scroll {
  flex: 1;
  overflow-y: auto;
}

.section {
  margin-bottom: 20px;
}

.section-label {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  opacity: 0.5;
  margin: 0 0 8px;
}

.section-text {
  margin: 0;
  font-size: 14px;
  line-height: 1.6;
}

.topic-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.topic-chip {
  padding: 3px 10px;
  border-radius: 999px;
  font-size: 12px;
  background: var(--n-color-hover);
}

.doc-body {
  border-top: 1px solid var(--n-border-color);
  padding-top: 16px;
}

.body-text {
  font-size: 14px;
  line-height: 1.7;
  word-break: break-word;
}

.loading-hint {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px 0;
}
</style>
