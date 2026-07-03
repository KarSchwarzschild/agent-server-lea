<script setup lang="ts">
defineProps<{ block: { ordered: boolean; items: string[] } }>()
</script>
<template>
  <component :is="block.ordered ? 'ol' : 'ul'" class="ir-list">
    <li v-for="(item, i) in block.items" :key="i">{{ item }}</li>
  </component>
</template>
<style scoped>
.ir-list { margin: 12px 0; padding-left: 2em; }
.ir-list li { margin: 4px 0; }
</style>
