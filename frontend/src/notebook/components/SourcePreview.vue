<template>
  <div class="source-preview">
    <template v-if="source">
      <div v-if="source.key_topics?.length" class="topic-tags">
        <n-tag v-for="t in source.key_topics" :key="t" size="small" round>{{ t }}</n-tag>
      </div>

      <div v-if="source.summary" class="summary-section">
        <n-text strong>摘要</n-text>
        <p>{{ source.summary }}</p>
      </div>

      <div v-if="source.parsed_content" class="content-section">
        <n-text strong>文档内容</n-text>
        <div class="markdown-body" v-html="renderedContent" />
      </div>

      <div v-else-if="source.status === 'processing'" class="loading-hint">
        <n-spin size="small" />
        <n-text depth="3" style="margin-left: 8px">文档正在解析中...</n-text>
      </div>
    </template>

    <div v-else class="empty-state">
      <n-text depth="3">选择一个文档查看详情</n-text>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { NTag, NText, NSpin } from 'naive-ui'
import type { Source } from '../composables/useNotebook'

const props = defineProps<{ source: Source | null }>()

const renderedContent = computed(() => {
  if (!props.source?.parsed_content) return ''
  return props.source.parsed_content
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\n/g, '<br>')
})
</script>

<style scoped>
.source-preview {
  height: 100%;
  padding: 16px;
  overflow-y: auto;
}

.topic-tags {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.summary-section {
  margin-bottom: 16px;
}

.summary-section p {
  margin: 6px 0 0;
  line-height: 1.6;
  color: var(--n-text-color);
}

.content-section {
  margin-top: 12px;
}

.markdown-body {
  margin-top: 8px;
  line-height: 1.7;
  font-size: 14px;
  word-break: break-word;
}

.loading-hint {
  display: flex;
  align-items: center;
  padding: 40px 0;
  justify-content: center;
}

.empty-state {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
}
</style>
