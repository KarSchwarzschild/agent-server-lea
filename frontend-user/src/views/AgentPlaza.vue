<script setup lang="ts">
/**
 * Agent 广场页面
 * 浏览系统预设助手 & 管理个人助手
 */
import { ref, computed, watch, onMounted } from 'vue'
import {
  NInput, NButton, NTabs, NTabPane, NTag, NSpace, NCard,
  NIcon, NEmpty, NSpin, useMessage, useDialog
} from 'naive-ui'
import { SearchOutline, CreateOutline, TrashOutline } from '@vicons/ionicons5'
import { useRouter } from 'vue-router'
import {
  getAgents,
  getAgentCategories,
  deleteAgent,
  setDefaultAgent,
  clearDefaultAgent,
  getDefaultAgent,
  type AgentInfo
} from '../api/superagent'
import AgentCreatorDrawer from '../components/super-agent/AgentCreatorDrawer.vue'

const router = useRouter()
const message = useMessage()
const dialog = useDialog()

// ===== 状态 =====
const loading = ref(false)
const agents = ref<AgentInfo[]>([])
const categories = ref<Record<string, string>>({})
const searchText = ref('')
const activeTab = ref<'system' | 'mine'>('system')
const selectedCategory = ref<string | null>(null)
const defaultAgentId = ref<number | null>(null)

// 创建/编辑抽屉
const showCreator = ref(false)
const editingAgent = ref<AgentInfo | null>(null)

// ===== 分类选项 =====
const categoryOptions = computed(() => [
  { key: null, label: '全部' },
  ...Object.entries(categories.value).map(([key, label]) => ({ key, label }))
])

// ===== 过滤后的列表 =====
const filteredAgents = computed(() => {
  return agents.value.filter(a => {
    const inScope = activeTab.value === 'system' ? a.is_system : a.is_mine
    if (!inScope) return false
    if (selectedCategory.value && a.category !== selectedCategory.value) return false
    if (searchText.value) {
      const q = searchText.value.toLowerCase()
      return (a.name?.toLowerCase().includes(q) || (a.description || '').toLowerCase().includes(q))
    }
    return true
  })
})

// ===== 数据加载 =====
async function loadData() {
  loading.value = true
  try {
    const [agentsRes, catsRes, defaultRes] = await Promise.all([
      getAgents({ scope: 'all' }),
      getAgentCategories(),
      getDefaultAgent().catch(() => null)
    ])
    agents.value = (agentsRes as any)?.items || []
    categories.value = (catsRes as any) || {}
    defaultAgentId.value = (defaultRes as any)?.agent_id ?? null
  } catch (e) {
    console.error('加载数据失败', e)
  } finally {
    loading.value = false
  }
}

// ===== 操作 =====
function handleStartChat(agent: AgentInfo) {
  router.push({
    path: '/qa/super-agent',
    query: { agent_id: String(agent.id), new_session: 'true' }
  })
}

async function handleToggleDefault(agent: AgentInfo, event: Event) {
  event.stopPropagation()
  try {
    if (defaultAgentId.value === agent.id) {
      await clearDefaultAgent()
      defaultAgentId.value = null
      message.success('已取消默认助手')
    } else {
      await setDefaultAgent(agent.id)
      defaultAgentId.value = agent.id
      message.success(`已将「${agent.name}」设为默认助手`)
    }
  } catch {
    message.error('操作失败')
  }
}

function handleEdit(agent: AgentInfo, event: Event) {
  event.stopPropagation()
  editingAgent.value = agent
  showCreator.value = true
}

function handleDelete(agent: AgentInfo, event: Event) {
  event.stopPropagation()
  dialog.warning({
    title: '删除助手',
    content: `确定要删除「${agent.name}」吗？`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await deleteAgent(agent.id)
        agents.value = agents.value.filter(a => a.id !== agent.id)
        message.success('已删除')
      } catch {
        message.error('删除失败')
      }
    }
  })
}

function handleCreateNew() {
  editingAgent.value = null
  showCreator.value = true
}

function handleSaved(agent: AgentInfo) {
  const idx = agents.value.findIndex(a => a.id === agent.id)
  if (idx >= 0) {
    agents.value[idx] = agent
  } else {
    agents.value.push(agent)
  }
}

watch(activeTab, () => {
  selectedCategory.value = null
})

onMounted(loadData)
</script>

<template>
  <div class="agent-plaza">
    <div class="plaza-inner">
      <!-- 页面标题 + 搜索 -->
      <div class="plaza-top">
        <div>
          <h2 class="plaza-title">助手广场</h2>
          <p class="plaza-subtitle">发现并使用专业 AI 助手，或创建属于你的专属助手</p>
        </div>
        <n-input
          v-model:value="searchText"
          placeholder="搜索助手名称或描述..."
          clearable
          size="small"
          class="search-input"
        >
          <template #prefix>
            <n-icon :component="SearchOutline" />
          </template>
        </n-input>
      </div>

      <!-- Tab + 分类 + 卡片 -->
      <n-card :bordered="false">
        <n-tabs v-model:value="activeTab" type="line" animated>
          <n-tab-pane name="system" tab="全部应用" />
          <n-tab-pane name="mine" tab="我的应用" />
        </n-tabs>

        <!-- 分类筛选 -->
        <div class="category-bar">
          <n-button
            v-for="cat in categoryOptions"
            :key="String(cat.key)"
            size="tiny"
            :secondary="selectedCategory !== cat.key"
            :type="selectedCategory === cat.key ? 'primary' : 'default'"
            @click="selectedCategory = cat.key"
          >
            {{ cat.label }}
          </n-button>
        </div>

        <!-- Agent 卡片网格 -->
        <n-spin :show="loading">
          <div class="agent-grid" v-if="filteredAgents.length > 0 || activeTab === 'mine'">
            <div
              v-for="agent in filteredAgents"
              :key="agent.id"
              class="agent-card"
            >
              <!-- 头部：头像 + 操作 -->
              <div class="card-top">
                <div class="agent-avatar">{{ agent.avatar || '🤖' }}</div>
                <div class="card-actions">
                  <span
                    class="action-btn star-btn"
                    :class="{ active: defaultAgentId === agent.id }"
                    :title="defaultAgentId === agent.id ? '取消默认' : '设为默认'"
                    @click="handleToggleDefault(agent, $event)"
                  >{{ defaultAgentId === agent.id ? '⭐' : '☆' }}</span>
                  <template v-if="activeTab === 'mine'">
                    <span class="action-btn" title="编辑" @click="handleEdit(agent, $event)">
                      <n-icon :component="CreateOutline" :size="14" />
                    </span>
                    <span class="action-btn danger-btn" title="删除" @click="handleDelete(agent, $event)">
                      <n-icon :component="TrashOutline" :size="14" />
                    </span>
                  </template>
                </div>
              </div>

              <!-- 名称 + 描述 -->
              <div class="agent-name">{{ agent.name }}</div>
              <div class="agent-desc">{{ agent.description || '暂无描述' }}</div>

              <!-- 底部信息 -->
              <div class="card-footer">
                <n-tag v-if="agent.category_label" size="tiny" :bordered="false" type="info">
                  {{ agent.category_label }}
                </n-tag>
                <span v-if="agent.allowed_skills && agent.allowed_skills.length > 0" class="skills-count">
                  {{ agent.allowed_skills.length }} 个专长
                </span>
              </div>

              <n-button size="small" type="primary" block @click="handleStartChat(agent)">
                开始对话
              </n-button>
            </div>

            <!-- 创建新助手（仅我的应用） -->
            <div v-if="activeTab === 'mine'" class="agent-card create-card" @click="handleCreateNew">
              <div class="create-inner">
                <span class="create-plus">+</span>
                <span class="create-label">创建新助手</span>
              </div>
            </div>
          </div>

          <!-- 空状态 -->
          <n-empty
            v-if="!loading && filteredAgents.length === 0 && activeTab === 'system'"
            description="暂无匹配的助手"
            style="margin: 40px 0"
          />
          <div v-if="!loading && filteredAgents.length === 0 && activeTab === 'mine'" style="text-align: center; padding: 40px 0">
            <n-empty description="还没有自定义助手" style="margin-bottom: 16px" />
            <n-button type="primary" @click="handleCreateNew">立即创建</n-button>
          </div>
        </n-spin>
      </n-card>
    </div>

    <AgentCreatorDrawer
      v-model:show="showCreator"
      :edit-agent="editingAgent"
      @saved="handleSaved"
    />
  </div>
</template>

<style scoped>
.agent-plaza {
  height: 100%;
  overflow-y: auto;
  background: #f5f7f9;
}

.plaza-inner {
  max-width: 1100px;
  margin: 0 auto;
  padding: 20px 24px;
}

.plaza-top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}

.plaza-title {
  font-size: 20px;
  font-weight: 600;
  color: #1a1a1a;
  margin: 0 0 4px;
}

.plaza-subtitle {
  font-size: 13px;
  color: #999;
  margin: 0;
}

.search-input {
  width: 280px;
  flex-shrink: 0;
}

:deep(.n-tabs-nav) {
  margin-bottom: 0;
}

.category-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 14px 0 18px;
}

/* 卡片网格 */
.agent-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 14px;
}

.agent-card {
  padding: 16px;
  background: #fff;
  border: 1px solid #eee;
  border-radius: 10px;
  cursor: default;
  transition: all 0.2s;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.agent-card:hover {
  border-color: #d0d0d0;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);
}

.card-top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
}

.agent-avatar {
  font-size: 28px;
  line-height: 1;
}

.card-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  opacity: 0;
  transition: opacity 0.15s;
}

.agent-card:hover .card-actions {
  opacity: 1;
}

.action-btn {
  width: 26px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  cursor: pointer;
  color: #bbb;
  font-size: 16px;
  transition: all 0.15s;
}

.action-btn:hover {
  background: #f5f5f5;
  color: #666;
}

.star-btn.active {
  color: #f0a020;
  opacity: 1 !important;
}

/* 始终显示已激活的星标 */
.agent-card .star-btn.active {
  opacity: 1;
}

.danger-btn:hover {
  background: #fff1f0;
  color: #ff4d4f;
}

.agent-name {
  font-size: 14px;
  font-weight: 600;
  color: #333;
}

.agent-desc {
  font-size: 12px;
  color: #999;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  flex: 1;
  min-height: 36px;
}

.card-footer {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  min-height: 20px;
}

.skills-count {
  font-size: 11px;
  color: #ccc;
}

/* 创建卡片 */
.create-card {
  border-style: dashed;
  border-color: #d9d9d9;
  background: #fafafa;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  min-height: 180px;
}

.create-card:hover {
  border-color: #2080f0;
  background: #f8fbff;
}

.create-inner {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
}

.create-plus {
  font-size: 36px;
  color: #ccc;
  line-height: 1;
}

.create-card:hover .create-plus {
  color: #2080f0;
}

.create-label {
  font-size: 13px;
  color: #999;
}

.create-card:hover .create-label {
  color: #2080f0;
}
</style>
