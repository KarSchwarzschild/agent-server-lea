<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import katex from 'katex'

const props = defineProps<{ block: { latex: string; display?: boolean } }>()
const rendered = ref('')

function render() {
  try {
    rendered.value = katex.renderToString(props.block.latex, {
      displayMode: props.block.display !== false,
      throwOnError: false,
    })
  } catch {
    rendered.value = `<span class="math-error">${props.block.latex}</span>`
  }
}

onMounted(render)
watch(() => props.block.latex, render)
</script>
<template>
  <div class="ir-math" :class="{ 'display-mode': block.display !== false }" v-html="rendered" />
</template>
<style scoped>
.ir-math { margin: 12px 0; }
.ir-math.display-mode { text-align: center; overflow-x: auto; }
.ir-math :deep(.math-error) { color: #e53e3e; font-family: monospace; }
</style>
