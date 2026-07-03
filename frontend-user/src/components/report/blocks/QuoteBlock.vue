<script setup lang="ts">
defineProps<{ block: { text: string; attribution?: string } }>()
</script>
<template>
  <blockquote class="ir-quote">
    <p>{{ block.text }}</p>
    <footer v-if="block.attribution">— {{ block.attribution }}</footer>
  </blockquote>
</template>
<style scoped>
.ir-quote { border-left: 4px solid #1890ff; padding: 12px 16px; margin: 16px 0; background: #f6f8fa; }
.ir-quote p { margin: 0; font-style: italic; }
.ir-quote footer { margin-top: 8px; font-size: 13px; color: #666; }
</style>
