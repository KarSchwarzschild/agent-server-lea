<script setup lang="ts">
defineProps<{ block: { ref_id: string; authors: string[]; title: string; year: number | string; source?: string; doi?: string; url?: string } }>()
</script>
<template>
  <div class="ir-reference" :id="`ref-${block.ref_id}`">
    <span class="ref-authors">{{ block.authors?.join(', ') }}</span>
    <span v-if="block.year"> ({{ block.year }}). </span>
    <span class="ref-title">{{ block.title }}</span>
    <span v-if="block.source">. {{ block.source }}</span>
    <span v-if="block.doi">. DOI: <a :href="`https://doi.org/${block.doi}`" target="_blank">{{ block.doi }}</a></span>
  </div>
</template>
<style scoped>
.ir-reference { margin: 6px 0; font-size: 14px; padding-left: 2em; text-indent: -2em; }
.ref-title { font-style: italic; }
.ir-reference a { color: #1890ff; }
</style>
