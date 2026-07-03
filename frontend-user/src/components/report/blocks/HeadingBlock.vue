<script setup lang="ts">
defineProps<{ block: { level: number; text: string; anchor?: string } }>()
</script>
<template>
  <component :is="`h${Math.min(block.level + 1, 6)}`" :id="block.anchor || undefined" class="ir-heading">
    {{ block.text }}
  </component>
</template>
<style scoped>
.ir-heading { margin: 20px 0 12px; }
</style>
