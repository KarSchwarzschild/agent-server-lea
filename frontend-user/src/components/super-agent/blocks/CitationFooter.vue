<script setup lang="ts">
/**
 * CitationFooter — 引���脚注
 */
defineProps<{
  refId: string
  text: string
}>()
</script>

<template>
  <div class="citation-footer">
    <span class="citation-footer__id">[{{ refId }}]</span>
    <span class="citation-footer__text">{{ text }}</span>
  </div>
</template>

<style scoped>
.citation-footer {
  font-size: 13px;
  color: #666;
  padding: 2px 0;
  line-height: 1.6;
}
.citation-footer__id {
  color: #1890ff;
  font-weight: 500;
  margin-right: 4px;
}
</style>
