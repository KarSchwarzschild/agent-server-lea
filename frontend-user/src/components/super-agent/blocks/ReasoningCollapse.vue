<script setup lang="ts">
/**
 * ReasoningCollapse — 推理过程折叠区
 */
import { ref } from 'vue'

defineProps<{
  content: string
}>()

const expanded = ref(false)
</script>

<template>
  <div class="reasoning-collapse">
    <button class="reasoning-collapse__trigger" @click="expanded = !expanded">
      <span>{{ expanded ? '▾' : '▸' }} 推理过程</span>
    </button>
    <Transition name="collapse">
      <div v-if="expanded" class="reasoning-collapse__content">
        <div class="prose prose-sm max-w-none text-gray-600" v-text="content" />
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.reasoning-collapse {
  margin: 4px 0;
}
.reasoning-collapse__trigger {
  font-size: 13px;
  color: #888;
  cursor: pointer;
  background: none;
  border: none;
  padding: 4px 0;
}
.reasoning-collapse__trigger:hover {
  color: #555;
}
.reasoning-collapse__content {
  padding: 8px 12px;
  margin-top: 4px;
  background: #f9f9f9;
  border-radius: 6px;
  border-left: 3px solid #ddd;
  font-size: 13px;
}
</style>
