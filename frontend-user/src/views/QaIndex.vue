<script setup lang="ts">
/**
 * QaIndex.vue - 问答模块容器页
 * 方案C设计：H5端点击"问答"Tab后，进入超级智能体
 * - 超级智能体 → /qa/super-agent
 */
import { ref, watch, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NTabs, NTab } from 'naive-ui'
import { useResponsive } from '../composables/useResponsive'

const route = useRoute()
const router = useRouter()
const { isMobile } = useResponsive()

// 当前激活的Tab
const activeTab = ref('super-agent')

// 根据路由同步Tab状态
const syncTabFromRoute = () => {
  activeTab.value = 'super-agent'
}

// 切换Tab
const handleTabChange = (tab: string) => {
  if (tab === 'super-agent') {
    router.push('/qa/super-agent')
  }
}

// 监听路由变化
watch(() => route.path, syncTabFromRoute, { immediate: true })

onMounted(() => {
  // 如果直接访问 /qa，重定向到默认子路由
  if (route.path === '/qa') {
    router.replace('/qa/super-agent')
  }
})
</script>

<template>
  <div class="qa-container">
    <!-- H5端顶部Tab切换 -->
    <div v-if="isMobile" class="qa-tabs-header">
      <n-tabs
        v-model:value="activeTab"
        type="segment"
        size="small"
        @update:value="handleTabChange"
      >
        <n-tab name="super-agent">超级智能体</n-tab>
      </n-tabs>
    </div>

    <!-- 内容区域 -->
    <div class="qa-content">
      <router-view />
    </div>
  </div>
</template>

<style scoped>
.qa-container {
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.qa-tabs-header {
  padding: 8px 16px;
  background: #fff;
  border-bottom: 1px solid #eee;
  flex-shrink: 0;
}

.qa-content {
  flex: 1;
  overflow: hidden;
}
</style>
