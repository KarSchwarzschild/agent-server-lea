<template>
  <div>
    <n-h2>Dashboard</n-h2>
    <n-grid :cols="3" :x-gap="16" :y-gap="16">
      <n-gi>
        <n-card title="已注册App">
          <n-statistic :value="stats.appCount" />
        </n-card>
      </n-gi>
      <n-gi>
        <n-card title="Agent总数">
          <n-statistic :value="stats.agentCount" />
        </n-card>
      </n-gi>
      <n-gi>
        <n-card title="Skill总数">
          <n-statistic :value="stats.skillCount" />
        </n-card>
      </n-gi>
    </n-grid>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { NH2, NGrid, NGi, NCard, NStatistic } from 'naive-ui'
import request from '../utils/request'

const stats = ref({ appCount: 0, agentCount: 0, skillCount: 0 })

onMounted(async () => {
  try {
    const { data } = await request.get('/api/admin/apps')
    const apps = data.apps || []
    stats.value.appCount = apps.length
    stats.value.agentCount = apps.reduce((sum: number, a: any) => sum + (a.agents?.length || 0), 0)
    stats.value.skillCount = apps.reduce((sum: number, a: any) => sum + (a.skills?.length || 0), 0)
  } catch { /* ignore */ }
})
</script>
