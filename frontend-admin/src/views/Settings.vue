<script setup lang="ts">
/**
 * 系统设置页面 - Shell
 * 各 Tab 已拆分为独立组件，本文件仅负责 Tab 路由同步和组件编排
 */
import { ref, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NCard,
  NTabs,
  NTabPane,
} from 'naive-ui'
import {
  getModelConfig,
  type AvailableModel,
} from '../api/super-agent'

// Tab 组件
import CategoryTab from '../components/settings/CategoryTab.vue'
import PromptTab from '../components/settings/PromptTab.vue'
import ConnectionTab from '../components/settings/ConnectionTab.vue'
import KnowledgeBaseTab from '../components/settings/KnowledgeBaseTab.vue'
import ModelEndpointsTab from '../components/settings/ModelEndpointsTab.vue'
import ModelTierTab from '../components/settings/ModelTierTab.vue'
import RoleModelsTab from '../components/settings/RoleModelsTab.vue'
import FeatureTogglesTab from '../components/settings/FeatureTogglesTab.vue'
import SandboxTab from '../components/settings/SandboxTab.vue'
import HealthTab from '../components/settings/HealthTab.vue'
import TTSEndpointsTab from '../components/settings/TTSEndpointsTab.vue'
import DocumentIndexSettings from '../components/settings/DocumentIndexSettings.vue'
import SearchCacheTab from '../components/settings/SearchCacheTab.vue'
import FileExportTab from '../components/settings/FileExportTab.vue'

const route = useRoute()
const router = useRouter()

// Tab 状态与 URL query 参数 ?tab=xxx 同步
const activeSettingsTab = ref((route.query.tab as string) || 'category')

// 共享的 availableModels（ModelTier 和 RoleModels 都需要）
const availableModels = ref<AvailableModel[]>([])

const modelTierRef = ref<InstanceType<typeof ModelTierTab> | null>(null)
const roleModelsRef = ref<InstanceType<typeof RoleModelsTab> | null>(null)

watch(activeSettingsTab, (tab) => {
  router.replace({ query: { ...route.query, tab } })
  // 切换到依赖可用模型列表的 Tab 时，重新加载数据
  if (tab === 'model') modelTierRef.value?.loadModelConfig()
  if (tab === 'roles') roleModelsRef.value?.loadRoleModels()
})

async function loadAvailableModels() {
  try {
    const res = await getModelConfig()
    availableModels.value = res.available_models || []
  } catch {
    // 静默失败，子组件会各自处理错误
  }
}

onMounted(() => {
  loadAvailableModels()
})
</script>

<template>
  <div class="settings-page">
    <n-card title="系统设置">
      <n-tabs v-model:value="activeSettingsTab" type="line" animated>
        <!-- 分类体系配置 -->
        <n-tab-pane name="category" tab="分类体系">
          <CategoryTab />
        </n-tab-pane>

        <!-- 提示词配置 -->
        <n-tab-pane name="prompt" tab="提示词配置" display-directive="if">
          <PromptTab />
        </n-tab-pane>

        <!-- 连接配置 -->
        <n-tab-pane name="connection" tab="基座连接" display-directive="if">
          <ConnectionTab />
        </n-tab-pane>

        <!-- 知识库管理 -->
        <n-tab-pane name="kb" tab="知识库管理" display-directive="if">
          <KnowledgeBaseTab />
        </n-tab-pane>

        <!-- 模型端点管理 -->
        <n-tab-pane name="endpoints" tab="模型端点" display-directive="if">
          <ModelEndpointsTab @switch-tab="(tab: string) => { activeSettingsTab = tab }" />
        </n-tab-pane>

        <!-- 语音合成 -->
        <n-tab-pane name="tts" tab="语音合成" display-directive="if">
          <TTSEndpointsTab />
        </n-tab-pane>

        <!-- AI模型配置 -->
        <n-tab-pane name="model" tab="AI模型（等级）" display-directive="if">
          <ModelTierTab ref="modelTierRef" :available-models="availableModels" />
        </n-tab-pane>

        <!-- 角色模型配置 -->
        <n-tab-pane name="roles" tab="AI模型（角色）" display-directive="if">
          <RoleModelsTab ref="roleModelsRef" :available-models="availableModels" />
        </n-tab-pane>

        <!-- 功能开关 -->
        <n-tab-pane name="toggles" tab="功能开关" display-directive="if">
          <FeatureTogglesTab />
        </n-tab-pane>

        <!-- 沙箱隔离 -->
        <n-tab-pane name="sandbox" tab="沙箱隔离" display-directive="if">
          <SandboxTab />
        </n-tab-pane>

        <!-- 模型健康 -->
        <n-tab-pane name="model-health" tab="模型健康" display-directive="if">
          <HealthTab />
        </n-tab-pane>

        <n-tab-pane name="pageindex" tab="文档索引" display-directive="if">
          <DocumentIndexSettings />
        </n-tab-pane>

        <!-- 搜索缓存 -->
        <n-tab-pane name="search-cache" tab="搜索缓存" display-directive="if">
          <SearchCacheTab />
        </n-tab-pane>

        <!-- 文件导出 -->
        <n-tab-pane name="file-export" tab="文件导出" display-directive="if">
          <FileExportTab />
        </n-tab-pane>
      </n-tabs>
    </n-card>
  </div>
</template>

<style scoped>
.settings-page {
  min-height: 100%;
}

:deep(.tab-content) {
  padding: 20px 0;
}

:deep(.n-dynamic-tags) {
  width: 100%;
}

:deep(.n-form-item) {
  margin-bottom: 24px;
}
</style>
