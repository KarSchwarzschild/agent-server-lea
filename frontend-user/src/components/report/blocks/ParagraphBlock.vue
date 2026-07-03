<script setup lang="ts">
const props = defineProps<{
  block: { text: string; source_ids?: string[]; marks?: any[] }
}>()
const emit = defineEmits<{ 'cite-click': [sourceId: string] }>()

function renderText(text: string): string {
  return text.replace(/\[\[([^\]]+)\]\]/g, '<sup class="ir-cite" data-source="$1">[$1]</sup>')
}
</script>
<template>
  <p class="ir-paragraph" v-html="renderText(block.text)" @click="(e: MouseEvent) => {
    const target = e.target as HTMLElement
    if (target.classList.contains('ir-cite')) {
      emit('cite-click', target.dataset.source || '')
    }
  }" />
</template>
<style scoped>
.ir-paragraph { margin: 8px 0; text-indent: 2em; text-align: justify; }
:deep(.ir-cite) { color: #1890ff; cursor: pointer; font-size: 12px; }
:deep(.ir-cite:hover) { text-decoration: underline; }
</style>
