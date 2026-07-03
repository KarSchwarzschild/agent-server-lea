<script setup lang="ts">
/**
 * AI 助手选择面板
 * Tab 布局：「系统预设」/「我的助手」
 */
import { ref, computed, watch } from 'vue'
import { NModal, NInput, NButton, NIcon, NEmpty, NSpin, NTabs, NTabPane, NBadge } from 'naive-ui'
import { SearchOutline, AddOutline } from '@vicons/ionicons5'
import { useRouter } from 'vue-router'
import { getAgents, type AgentInfo } from '../../api/superagent'

const router = useRouter()

const props = defineProps<{
  show: boolean
  currentAgentId?: number | null
  defaultAgentId?: number | null
}>()

const emit = defineEmits<{
  (e: 'update:show', value: boolean): void
  (e: 'select', agent: AgentInfo | null): void
  (e: 'create'): void
  (e: 'set-default', agentId: number): void
  (e: 'clear-default'): void
}>()

const loading = ref(false)
const agents = ref<AgentInfo[]>([])
const searchText = ref('')
const activeTab = ref('system')

const systemAgents = computed(() =>
  agents.value.filter(a => a.is_system && matchSearch(a))
)
const personalAgents = computed(() =>
  agents.value.filter(a => a.is_mine && matchSearch(a))
)

function matchSearch(agent: AgentInfo): boolean {
  if (!searchText.value) return true
  const q = searchText.value.toLowerCase()
  return (agent.name?.toLowerCase().includes(q) || agent.description?.toLowerCase().includes(q)) ?? false
}

async function loadAgents() {
  loading.value = true
  try {
    const res = await getAgents()
    agents.value = (res as any)?.items || []
  } catch (e) {
    console.error('加载助手列表失败', e)
  } finally {
    loading.value = false
  }
}

function handleSelect(agent: AgentInfo | null) {
  emit('select', agent)
  emit('update:show', false)
}

function toggleDefault(agent: AgentInfo, event: Event) {
  event.stopPropagation()
  if (props.defaultAgentId === agent.id) {
    emit('clear-default')
  } else {
    emit('set-default', agent.id)
  }
}

watch(() => props.show, (val) => {
  if (val) loadAgents()
})
</script>

<template>
  <n-modal
    :show="props.show"
    @update:show="emit('update:show', $event)"
    preset="card"
    title="选择 AI 助手"
    :style="{ width: '680px', maxHeight: '80vh' }"
    :content-style="{ overflowY: 'auto', padding: '0 24px 16px' }"
    :mask-closable="true"
    :closable="true"
  >
    <n-input
      v-model:value="searchText"
      placeholder="搜索助手..."
      clearable
      size="small"
      style="margin: 12px 0"
    >
      <template #prefix>
        <n-icon :component="SearchOutline" />
      </template>
    </n-input>

    <n-spin :show="loading">
      <n-tabs v-model:value="activeTab" type="line" animated>
        <!-- ===== 系统预设 ===== -->
        <n-tab-pane name="system">
          <template #tab>
            系统预设
            <span v-if="systemAgents.length" class="tab-count">{{ systemAgents.length }}</span>
          </template>
          <div class="agent-grid">
            <div
              v-for="agent in systemAgents"
              :key="agent.id"
              class="agent-card"
              :class="{ 'current-agent': agent.id === props.currentAgentId }"
              @click="handleSelect(agent)"
            >
              <div class="agent-card-header">
                <div class="agent-avatar">{{ agent.avatar || '🤖' }}</div>
                <div class="header-right">
                  <span v-if="agent.id === props.currentAgentId" class="current-badge">当前使用</span>
                  <span
                    class="default-star"
                    :class="{ active: props.defaultAgentId === agent.id }"
                    @click="toggleDefault(agent, $event)"
                    :title="props.defaultAgentId === agent.id ? '取消默认' : '设为默认'"
                  >{{ props.defaultAgentId === agent.id ? '\u2B50' : '\u2606' }}</span>
                </div>
              </div>
              <div class="agent-name">{{ agent.name }}</div>
              <div class="agent-desc">{{ agent.description || '暂无描述' }}</div>
              <div v-if="agent.allowed_skills && agent.allowed_skills.length > 0" class="agent-skills-tag">
                <span style="font-size: 11px; color: #666;">📌 {{ agent.allowed_skills.length }} 个专长</span>
              </div>
              <div v-if="agent.example_questions && agent.example_questions.length > 0" class="agent-examples">
                <div v-for="(q, idx) in agent.example_questions.slice(0, 1)" :key="idx" class="agent-example-item">
                  「{{ q }}」
                </div>
              </div>
              <n-button size="small" type="primary" block>开始对话</n-button>
            </div>
          </div>
          <n-empty v-if="!loading && systemAgents.length === 0" description="无匹配的系统预设助手" style="margin: 20px 0" />
        </n-tab-pane>

        <!-- ===== 我的助手 ===== -->
        <n-tab-pane name="personal">
          <template #tab>
            我的助手
            <span v-if="personalAgents.length" class="tab-count">{{ personalAgents.length }}</span>
          </template>
          <div class="agent-grid">
            <div
              v-for="agent in personalAgents"
              :key="agent.id"
              class="agent-card"
              :class="{ 'current-agent': agent.id === props.currentAgentId }"
              @click="handleSelect(agent)"
            >
              <div class="agent-card-header">
                <div class="agent-avatar">{{ agent.avatar || '✨' }}</div>
                <div class="header-right">
                  <span v-if="agent.id === props.currentAgentId" class="current-badge">当前使用</span>
                  <span
                    class="default-star"
                    :class="{ active: props.defaultAgentId === agent.id }"
                    @click="toggleDefault(agent, $event)"
                    :title="props.defaultAgentId === agent.id ? '取消默认' : '设为默认'"
                  >{{ props.defaultAgentId === agent.id ? '\u2B50' : '\u2606' }}</span>
                </div>
              </div>
              <div class="agent-name">{{ agent.name }}</div>
              <div class="agent-desc">{{ agent.description || '暂无描述' }}</div>
              <div v-if="agent.allowed_skills && agent.allowed_skills.length > 0" class="agent-skills-tag">
                <span style="font-size: 11px; color: #666;">📌 {{ agent.allowed_skills.length }} 个专长</span>
              </div>
              <div v-if="agent.example_questions && agent.example_questions.length > 0" class="agent-examples">
                <div v-for="(q, idx) in agent.example_questions.slice(0, 1)" :key="idx" class="agent-example-item">
                  「{{ q }}」
                </div>
              </div>
              <n-button size="small" type="primary" block>开始对话</n-button>
            </div>

            <!-- 创建新助手 -->
            <div class="agent-card create-card" @click="emit('create')">
              <n-icon :component="AddOutline" :size="32" />
              <div class="agent-name">创建新助手</div>
              <n-button size="small" dashed block>创建</n-button>
            </div>
          </div>
          <n-empty v-if="!loading && personalAgents.length === 0 && !searchText" description="还没有自定义助手，试试创建一个" style="margin: 20px 0" />
        </n-tab-pane>
      </n-tabs>
    </n-spin>

    <!-- 浏览更多链接 -->
    <div class="browse-more-bar">
      <span
        class="browse-more-link"
        @click="() => { emit('update:show', false); router.push('/agent-plaza') }"
      >浏览更多助手 →</span>
    </div>
  </n-modal>
</template>

<style scoped>
.tab-count {
  font-size: 11px;
  color: #999;
  margin-left: 4px;
}

.browse-more-bar {
  text-align: center;
  padding: 10px 0 4px;
  border-top: 1px solid #f0f0f0;
  margin-top: 8px;
}

.browse-more-link {
  font-size: 13px;
  color: #2080f0;
  cursor: pointer;
  text-decoration: none;
}

.browse-more-link:hover {
  text-decoration: underline;
}

.agent-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 12px;
  padding: 4px 0;
}

.agent-card {
  padding: 16px;
  border: 1px solid #e8e8e8;
  border-radius: 12px;
  cursor: pointer;
  transition: all 0.2s;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.agent-card:hover {
  border-color: #2080f0;
  box-shadow: 0 2px 8px rgba(32, 128, 240, 0.1);
}

.agent-avatar {
  font-size: 28px;
  line-height: 1;
}

.agent-name {
  font-size: 14px;
  font-weight: 600;
  color: #333;
}

.agent-desc {
  font-size: 12px;
  color: #999;
  line-height: 1.4;
  flex: 1;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.create-card {
  align-items: center;
  justify-content: center;
  border-style: dashed;
  color: #999;
}

.create-card:hover {
  color: #2080f0;
}

.current-agent {
  border-color: #18a058;
  border-width: 2px;
  box-shadow: 0 2px 8px rgba(24, 160, 88, 0.15);
}

.agent-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 6px;
}

.current-badge {
  font-size: 11px;
  color: #18a058;
  font-weight: 500;
}

.default-star {
  font-size: 18px;
  cursor: pointer;
  color: #ccc;
  transition: all 0.2s;
  line-height: 1;
}

.default-star:hover {
  color: #f0a020;
  transform: scale(1.15);
}

.default-star.active {
  color: #f0a020;
}

.agent-skills-tag {
  margin-top: -2px;
}

.agent-examples {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.agent-example-item {
  font-size: 12px;
  color: #999;
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
