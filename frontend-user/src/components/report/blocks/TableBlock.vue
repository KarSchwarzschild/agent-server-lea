<script setup lang="ts">
defineProps<{ block: { caption?: string; headers: string[]; rows: string[][] } }>()
</script>
<template>
  <div class="ir-table-wrapper">
    <table class="ir-table">
      <caption v-if="block.caption">{{ block.caption }}</caption>
      <thead>
        <tr><th v-for="h in block.headers" :key="h">{{ h }}</th></tr>
      </thead>
      <tbody>
        <tr v-for="(row, i) in block.rows" :key="i">
          <td v-for="(cell, j) in row" :key="j">{{ cell }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
<style scoped>
.ir-table-wrapper { overflow-x: auto; margin: 16px 0; }
.ir-table { width: 100%; border-collapse: collapse; font-size: 14px; }
.ir-table th, .ir-table td { border: 1px solid #e0e0e0; padding: 8px 12px; text-align: left; }
.ir-table th { background: #f5f5f5; font-weight: 600; }
.ir-table caption { caption-side: top; font-size: 13px; color: #666; margin-bottom: 8px; }
</style>
