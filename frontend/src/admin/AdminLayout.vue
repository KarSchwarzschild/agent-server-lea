<template>
  <div>
    <n-tabs type="line" :value="activeTab" @update:value="handleTab">
      <n-tab name="/admin/dashboard">概览</n-tab>
      <n-tab name="/admin/apps">应用管理</n-tab>
      <n-tab name="/admin/tools">工具管理</n-tab>
      <n-tab name="/admin/skills">技能管理</n-tab>
      <n-tab name="/admin/knowledge">知识库管理</n-tab>
      <n-tab name="/admin/monitoring">基础设施监控</n-tab>
    </n-tabs>
    <div style="padding-top: 12px">
      <router-view />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NTabs, NTab } from 'naive-ui'

const route = useRoute()
const router = useRouter()
const activeTab = computed(() => route.path)

function handleTab(key: string) {
  router.push(key)
}
</script>
