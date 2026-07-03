<template>
  <n-drawer
    v-model:show="visible"
    :height="drawerHeight"
    placement="bottom"
    :mask-closable="true"
    :close-on-esc="true"
  >
    <n-drawer-content title="选择智能体" closable>
      <!-- 加载状态 -->
      <div v-if="loading" class="loading-container">
        <n-spin size="large" />
        <p>加载中...</p>
      </div>

      <!-- 空状态 -->
      <div v-else-if="agents.length === 0" class="empty-container">
        <n-empty description="暂无可用智能体" />
      </div>

      <!-- 智能体列表（按分类分组） -->
      <div v-else class="agent-list">
        <div
          v-for="category in categories"
          :key="category"
          class="category-group"
        >
          <!-- 分类标题 -->
          <div class="category-header">
            <div
              class="category-indicator"
              :style="{ backgroundColor: getCategoryBorderColor(category, categories) }"
            ></div>
            <span class="category-name">{{ category }}</span>
            <span class="category-count">{{ getAgentsByCategory(category).length }}</span>
          </div>

          <!-- 该分类下的智能体卡片 -->
          <div class="agent-grid">
            <AgentCard
              v-for="agent in getAgentsByCategory(category)"
              :key="agent.agent_id"
              :agent="agent"
              :selected="agent.agent_id === currentAgentId"
              :category-color="getCategoryBorderColor(category, categories)"
              @select="handleSelect"
            />
          </div>
        </div>
      </div>
    </n-drawer-content>
  </n-drawer>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { NDrawer, NDrawerContent, NSpin, NEmpty } from 'naive-ui'
import AgentCard from './AgentCard.vue'
import { getAgents, getAgentCategories, type AgentPublic } from '../api/agents'
import { getCategoryBorderColor } from '../utils/categoryColors'

interface Props {
  visible: boolean
  currentAgentId?: string
}

const props = withDefaults(defineProps<Props>(), {
  currentAgentId: ''
})

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (e: 'select', agent: AgentPublic): void
}>()

// 状态
const loading = ref(false)
const agents = ref<AgentPublic[]>([])
const categories = ref<string[]>([])

// 双向绑定visible
const visible = computed({
  get: () => props.visible,
  set: (val) => emit('update:visible', val)
})

// 抽屉高度（根据内容自适应，最大70%）
const drawerHeight = computed(() => {
  const baseHeight = 200 // 头部和padding
  const categoryHeight = categories.value.length * 50 // 分类标题
  const agentRowHeight = Math.ceil(agents.value.length / 3) * 140 // 卡片行数
  const totalHeight = baseHeight + categoryHeight + agentRowHeight
  return Math.min(totalHeight, window.innerHeight * 0.7)
})

// 根据分类获取智能体
const getAgentsByCategory = (category: string) => {
  return agents.value.filter(a => a.category === category)
}

// 加载数据
const loadData = async () => {
  loading.value = true
  try {
    const [agentsRes, categoriesRes] = await Promise.all([
      getAgents(),
      getAgentCategories()
    ])
    agents.value = agentsRes.items
    categories.value = categoriesRes
  } catch (error) {
    console.error('加载智能体列表失败:', error)
  } finally {
    loading.value = false
  }
}

// 选择智能体
const handleSelect = (agent: AgentPublic) => {
  emit('select', agent)
  visible.value = false
}

// 首次显示时加载数据
watch(() => props.visible, (newVal) => {
  if (newVal && agents.value.length === 0) {
    loadData()
  }
})

// 组件挂载时预加载
onMounted(() => {
  // 延迟加载，避免影响首屏
  setTimeout(() => {
    if (agents.value.length === 0) {
      loadData()
    }
  }, 1000)
})
</script>

<style scoped>
.loading-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 48px 0;
  color: #6b7280;
}

.loading-container p {
  margin-top: 16px;
}

.empty-container {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 48px 0;
}

.agent-list {
  padding: 0 8px;
}

.category-group {
  margin-bottom: 24px;
}

.category-group:last-child {
  margin-bottom: 0;
}

.category-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid #f3f4f6;
}

.category-indicator {
  width: 4px;
  height: 16px;
  border-radius: 2px;
}

.category-name {
  font-size: 15px;
  font-weight: 600;
  color: #374151;
}

.category-count {
  font-size: 12px;
  color: #9ca3af;
  background: #f3f4f6;
  padding: 2px 8px;
  border-radius: 10px;
}

.agent-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}

/* 移动端适配 */
@media (max-width: 640px) {
  .agent-grid {
    grid-template-columns: repeat(2, 1fr);
    gap: 8px;
  }
}

@media (max-width: 400px) {
  .agent-grid {
    grid-template-columns: 1fr;
  }
}
</style>
