<template>
  <div>
    <n-h2>App 管理</n-h2>
    <n-space vertical :size="16">
      <n-card v-for="app in apps" :key="app.name" :title="app.display_name">
        <template #header-extra>
          <n-tag type="info">v{{ app.version }}</n-tag>
        </template>
        <p>{{ app.description }}</p>
        <n-descriptions :column="3" bordered>
          <n-descriptions-item label="名称">{{ app.name }}</n-descriptions-item>
          <n-descriptions-item label="Agent数">{{ app.agents?.length || 0 }}</n-descriptions-item>
          <n-descriptions-item label="Skill数">{{ app.skills?.length || 0 }}</n-descriptions-item>
        </n-descriptions>

        <n-h4 v-if="app.agents?.length">Agents</n-h4>
        <n-table v-if="app.agents?.length" :bordered="false" size="small">
          <thead><tr><th>名称</th><th>描述</th><th>使用的Skill</th></tr></thead>
          <tbody>
            <tr v-for="agent in app.agents" :key="agent.name">
              <td>{{ agent.display_name }}</td>
              <td>{{ agent.description }}</td>
              <td>{{ agent.skill_names?.join(', ') || '-' }}</td>
            </tr>
          </tbody>
        </n-table>

        <n-h4 v-if="app.skills?.length">Skills</n-h4>
        <n-space v-if="app.skills?.length">
          <n-tag v-for="skill in app.skills" :key="skill.name" type="success">
            {{ skill.display_name }}
          </n-tag>
        </n-space>
      </n-card>
    </n-space>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import {
  NH2, NH4, NCard, NSpace, NTag, NTable, NDescriptions, NDescriptionsItem
} from 'naive-ui'
import request from '../utils/request'

const apps = ref<any[]>([])

onMounted(async () => {
  try {
    const { data } = await request.get('/api/admin/apps')
    apps.value = data.apps || []
  } catch { /* ignore */ }
})
</script>
