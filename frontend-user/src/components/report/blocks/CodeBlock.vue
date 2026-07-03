<script setup lang="ts">
import { computed } from 'vue'
import hljs from 'highlight.js'

const props = defineProps<{ block: { language?: string; code: string } }>()

const highlighted = computed(() => {
  if (props.block.language && hljs.getLanguage(props.block.language)) {
    return hljs.highlight(props.block.code, { language: props.block.language }).value
  }
  return hljs.highlightAuto(props.block.code).value
})
</script>
<template>
  <div class="ir-code-block">
    <div v-if="block.language" class="code-lang">{{ block.language }}</div>
    <pre><code v-html="highlighted" /></pre>
  </div>
</template>
<style scoped>
.ir-code-block { margin: 16px 0; border-radius: 8px; overflow: hidden; background: #1e1e1e; }
.code-lang { padding: 4px 12px; font-size: 12px; color: #999; background: #2d2d2d; }
.ir-code-block pre { margin: 0; padding: 16px; overflow-x: auto; }
.ir-code-block code { font-family: 'Fira Code', monospace; font-size: 13px; color: #d4d4d4; }
</style>
