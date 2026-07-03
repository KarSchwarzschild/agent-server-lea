<script setup lang="ts">
/**
 * 历史记录页面 - 支持智能体筛选（Chip Filter）
 * 响应式版本：PC端和移动端双布局
 * 数据源：super-agent sessions API（含 agent_id 过滤）
 */
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import {
  NButton,
  NSpace,
  NTag,
  NSpin,
  NEmpty,
  NModal,
  NPopconfirm,
  NIcon,
  useMessage,
} from 'naive-ui'
import { ChatbubblesOutline, TrashOutline, TimeOutline, EllipsisVertical } from '@vicons/ionicons5'
import {
  getSessions as getSuperAgentSessions,
  getSessionDetail,
  deleteSession as deleteSuperAgentSession,
  getAgents,
  type SuperAgentSession,
  type AgentInfo,
} from '../api/superagent'
import { useResponsive } from '../composables/useResponsive'
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'
import 'highlight.js/styles/github.css'

const router = useRouter()
const message = useMessage()
const { isMobile } = useResponsive()

// Markdown 渲染器
const md: MarkdownIt = new MarkdownIt({
  html: true,
  linkify: true,
  typographer: true,
  highlight: function (str: string, lang: string) {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return '<pre class="hljs"><code>' +
          hljs.highlight(str, { language: lang, ignoreIllegals: true }).value +
          '</code></pre>'
      } catch (__) {}
    }
    return '<pre class="hljs"><code>' + md.utils.escapeHtml(str) + '</code></pre>'
  }
})

const renderMarkdown = (content: string): string => {
  if (!content) return ''
  return md.render(content)
}

// 移动端操作菜单
const showActionSheet = ref(false)
const actionTarget = ref<SuperAgentSession | null>(null)

const loading = ref(false)
const sessions = ref<SuperAgentSession[]>([])
const showMessageModal = ref(false)
const currentSession = ref<SuperAgentSession | null>(null)
const currentMessages = ref<Array<{ role: string; content: string }>>([])
const messagesLoading = ref(false)

// 智能体筛选
const agents = ref<AgentInfo[]>([])
const selectedAgentId = ref<number | null>(null) // null=全部

// 智能体 chip 列表（含"全部"）
interface AgentChip {
  id: number | null  // null=全部, 0=默认助手
  label: string
  avatar: string
}
const agentChips = computed<AgentChip[]>(() => {
  const chips: AgentChip[] = [
    { id: null, label: '全部', avatar: '' },
    { id: 0, label: '默认助手', avatar: '' },
  ]
  agents.value.forEach(agent => {
    chips.push({
      id: agent.id,
      label: agent.name,
      avatar: agent.avatar || '',
    })
  })
  return chips
})

// 加载智能体列表
const loadAgents = async () => {
  try {
    const res = await getAgents()
    agents.value = res.items
  } catch (error) {
    console.error('Failed to load agents:', error)
  }
}

const loadSessions = async () => {
  loading.value = true
  try {
    const params: { limit: number; agent_id?: number } = { limit: 50 }
    if (selectedAgentId.value !== null) {
      params.agent_id = selectedAgentId.value
    }
    const res = await getSuperAgentSessions(params)
    sessions.value = res
  } catch (error) {
    console.error('Failed to load sessions:', error)
    message.error('加载会话列表失败')
  } finally {
    loading.value = false
  }
}

// Chip 点击筛选
const selectAgent = (agentId: number | null) => {
  selectedAgentId.value = agentId
  loadSessions()
}

// 查看会话：跳转到 SuperAgent 页面
const viewSession = (session: SuperAgentSession) => {
  router.push({ name: 'SuperAgent', params: { sessionId: session.session_id } })
}

// 查看详情弹窗
const viewSessionDetail = async (session: SuperAgentSession) => {
  currentSession.value = session
  messagesLoading.value = true
  showMessageModal.value = true

  try {
    const res = await getSessionDetail(session.session_id)
    currentMessages.value = res.messages || []
  } catch (error) {
    console.error('Failed to load messages:', error)
    message.error('加载消息失败')
  } finally {
    messagesLoading.value = false
  }
}

const handleDelete = async (session: SuperAgentSession) => {
  try {
    await deleteSuperAgentSession(session.session_id)
    sessions.value = sessions.value.filter(s => s.session_id !== session.session_id)
    message.success('删除成功')
  } catch (error) {
    console.error('Failed to delete session:', error)
    message.error('删除失败')
  }
}

const goToSuperAgent = () => {
  router.push({ name: 'SuperAgent' })
}

// 格式化时间
const formatTime = (dateStr: string): string => {
  if (!dateStr) return ''
  const date = new Date(dateStr)
  const now = new Date()
  const diff = now.getTime() - date.getTime()
  const minutes = Math.floor(diff / 60000)
  const hours = Math.floor(diff / 3600000)
  const days = Math.floor(diff / 86400000)

  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes}分钟前`
  if (hours < 24) return `${hours}小时前`
  if (days < 7) return `${days}天前`
  return date.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
}

// 筛选无结果时的引导文案
const emptyDescription = computed(() => {
  if (selectedAgentId.value === null) return '暂无对话记录'
  if (selectedAgentId.value === 0) return '默认助手暂无对话记录'
  const agent = agents.value.find(a => a.id === selectedAgentId.value)
  return agent ? `"${agent.name}" 暂无对话记录` : '该助手暂无对话记录'
})

// 移动端：打开操作菜单
const openActionSheet = (session: SuperAgentSession) => {
  actionTarget.value = session
  showActionSheet.value = true
}

// 移动端：处理操作
const handleAction = async (action: string) => {
  if (!actionTarget.value) return
  const session = actionTarget.value

  if (action === 'open') {
    viewSession(session)
  } else if (action === 'detail') {
    viewSessionDetail(session)
  } else if (action === 'delete') {
    await handleDelete(session)
  }
  showActionSheet.value = false
  actionTarget.value = null
}

onMounted(() => {
  loadAgents()
  loadSessions()
})
</script>

<template>
  <div class="history-page" :class="{ 'history-page-mobile': isMobile }">
    <!-- ===== PC端布局 ===== -->
    <template v-if="!isMobile">
      <!-- 标题 -->
      <div class="header-section">
        <div class="header-content">
          <div>
            <h2>对话历史</h2>
            <p>查看和管理所有智能体的历史对话</p>
          </div>
          <n-button type="primary" @click="goToSuperAgent">
            <template #icon>
              <n-icon :component="ChatbubblesOutline" />
            </template>
            新建对话
          </n-button>
        </div>

        <!-- Chip 筛选器 -->
        <div class="chip-filter">
          <button
            v-for="chip in agentChips"
            :key="chip.id ?? 'all'"
            :class="['chip', { 'chip-active': selectedAgentId === chip.id }]"
            @click="selectAgent(chip.id)"
          >
            <span v-if="chip.avatar" class="chip-avatar">{{ chip.avatar }}</span>
            {{ chip.label }}
          </button>
        </div>
      </div>

      <!-- 内容区域 -->
      <div class="content-section">
        <!-- 加载中 -->
        <div v-if="loading" class="loading-state">
          <n-spin size="large" />
        </div>

        <!-- 无记录 -->
        <n-empty v-else-if="sessions.length === 0" :description="emptyDescription" class="empty-state">
          <template #extra>
            <n-space>
              <n-button v-if="selectedAgentId !== null" @click="selectAgent(null)">查看全部会话</n-button>
              <n-button type="primary" @click="goToSuperAgent">开始对话</n-button>
            </n-space>
          </template>
        </n-empty>

        <!-- 会话列表 -->
        <div v-else class="sessions-list">
          <div
            v-for="session in sessions"
            :key="session.session_id"
            class="session-card"
            @click="viewSession(session)"
          >
            <div class="session-info">
              <div class="session-header">
                <h3 class="session-title">
                  {{ session.title || '未命名对话' }}
                </h3>
                <n-tag
                  v-if="session.agent_name"
                  size="small"
                  :bordered="false"
                  type="info"
                >
                  {{ session.agent_name }}
                </n-tag>
                <n-tag
                  v-else
                  size="small"
                  :bordered="false"
                >
                  默认助手
                </n-tag>
              </div>
              <div class="session-meta">
                <n-icon :component="TimeOutline" :size="14" />
                <span>{{ formatTime(session.updated_at) }}</span>
                <n-tag
                  :type="session.status === 'active' ? 'success' : 'default'"
                  size="tiny"
                >
                  {{ session.status === 'active' ? '进行中' : '已结束' }}
                </n-tag>
              </div>
            </div>

            <n-space class="session-actions" @click.stop>
              <n-popconfirm @positive-click="handleDelete(session)">
                <template #trigger>
                  <n-button size="small" quaternary type="error">
                    <template #icon>
                      <n-icon :component="TrashOutline" />
                    </template>
                  </n-button>
                </template>
                确定要删除这个对话吗？
              </n-popconfirm>
            </n-space>
          </div>
        </div>
      </div>
    </template>

    <!-- ===== 移动端布局 ===== -->
    <template v-else>
      <!-- 移动端头部 -->
      <div class="mobile-header">
        <div class="mobile-header-top">
          <h2>对话历史</h2>
          <n-button type="primary" size="small" @click="goToSuperAgent">
            <template #icon>
              <n-icon :component="ChatbubblesOutline" />
            </template>
            新建
          </n-button>
        </div>
        <!-- 移动端 chip 筛选 - 横向滚动 -->
        <div class="mobile-chip-scroll">
          <button
            v-for="chip in agentChips"
            :key="chip.id ?? 'all'"
            :class="['chip chip-sm', { 'chip-active': selectedAgentId === chip.id }]"
            @click="selectAgent(chip.id)"
          >
            <span v-if="chip.avatar" class="chip-avatar">{{ chip.avatar }}</span>
            {{ chip.label }}
          </button>
        </div>
      </div>

      <!-- 移动端内容 -->
      <div class="mobile-content">
        <!-- 加载中 -->
        <div v-if="loading" class="loading-state">
          <n-spin size="large" />
        </div>

        <!-- 无记录 -->
        <n-empty v-else-if="sessions.length === 0" :description="emptyDescription" class="empty-state">
          <template #extra>
            <n-space>
              <n-button v-if="selectedAgentId !== null" size="small" @click="selectAgent(null)">查看全部</n-button>
              <n-button type="primary" size="small" @click="goToSuperAgent">开始对话</n-button>
            </n-space>
          </template>
        </n-empty>

        <!-- 会话列表 -->
        <div v-else class="mobile-sessions-list">
          <div
            v-for="session in sessions"
            :key="session.session_id"
            class="mobile-session-card"
            @click="viewSession(session)"
          >
            <div class="mobile-session-info">
              <div class="mobile-session-title">
                {{ session.title || '未命名对话' }}
              </div>
              <div class="mobile-session-meta">
                <n-tag
                  v-if="session.agent_name"
                  size="tiny"
                  :bordered="false"
                  type="info"
                >
                  {{ session.agent_name }}
                </n-tag>
                <n-tag v-else size="tiny" :bordered="false">默认助手</n-tag>
                <span class="mobile-session-time">{{ formatTime(session.updated_at) }}</span>
              </div>
            </div>
            <n-button text @click.stop="openActionSheet(session)">
              <n-icon :size="20" :component="EllipsisVertical" />
            </n-button>
          </div>
        </div>
      </div>

      <!-- 移动端操作菜单 -->
      <n-modal
        v-model:show="showActionSheet"
        :mask-closable="true"
        transform-origin="center"
      >
        <div class="action-sheet">
          <div class="action-sheet-item" @click="handleAction('open')">
            <n-icon :size="20" style="margin-right: 12px;">
              <ChatbubblesOutline />
            </n-icon>
            继续对话
          </div>
          <div class="action-sheet-item action-sheet-danger" @click="handleAction('delete')">
            <n-icon :size="20" style="margin-right: 12px;">
              <TrashOutline />
            </n-icon>
            删除对话
          </div>
          <div class="action-sheet-cancel" @click="showActionSheet = false">
            取消
          </div>
        </div>
      </n-modal>
    </template>

    <!-- 查看消息弹窗（PC和移动端共用） -->
    <n-modal
      v-model:show="showMessageModal"
      :title="currentSession?.title || '对话详情'"
      preset="card"
      :style="{ width: isMobile ? '95vw' : '700px', maxWidth: '95vw' }"
    >
      <div class="messages-modal">
        <n-spin v-if="messagesLoading" />
        <div v-else-if="currentMessages.length === 0" class="empty-messages">
          暂无消息
        </div>
        <div v-else class="messages-list">
          <div
            v-for="(msg, index) in currentMessages"
            :key="index"
            :class="['message-item', msg.role === 'user' ? 'user-msg' : 'ai-msg']"
          >
            <div class="message-role">{{ msg.role === 'user' ? '我' : 'AI' }}</div>
            <div v-if="msg.role === 'user'" class="message-text">{{ msg.content }}</div>
            <div v-else class="message-text markdown-content" v-html="renderMarkdown(msg.content)"></div>
          </div>
        </div>
      </div>
    </n-modal>
  </div>
</template>

<style scoped>
.history-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 24px;
  overflow: hidden;
}

.header-section {
  flex-shrink: 0;
  margin-bottom: 16px;
}

.header-content {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.header-content h2 {
  margin: 0 0 4px;
  font-size: 20px;
  font-weight: 600;
}

.header-content p {
  margin: 0;
  color: #666;
  font-size: 14px;
}

/* Chip 筛选器 */
.chip-filter {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 6px 14px;
  border-radius: 16px;
  border: 1px solid #e0e0e0;
  background: #fff;
  font-size: 13px;
  color: #555;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
}

.chip:hover {
  border-color: #2080f0;
  color: #2080f0;
}

.chip-active {
  background: #2080f0;
  border-color: #2080f0;
  color: #fff;
}

.chip-active:hover {
  background: #1870d5;
  color: #fff;
}

.chip-avatar {
  font-size: 14px;
  line-height: 1;
}

.chip-sm {
  padding: 4px 10px;
  font-size: 12px;
}

.content-section {
  flex: 1;
  overflow-y: auto;
}

.loading-state {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 200px;
}

.empty-state {
  margin-top: 48px;
}

.sessions-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.session-card {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 18px;
  border-radius: 10px;
  background: #fff;
  border: 1px solid #f0f0f0;
  cursor: pointer;
  transition: all 0.2s;
}

.session-card:hover {
  border-color: #d0d0d0;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
}

.session-info {
  flex: 1;
  min-width: 0;
}

.session-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.session-title {
  margin: 0;
  font-size: 15px;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 400px;
}

.session-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #999;
  font-size: 12px;
}

.session-actions {
  flex-shrink: 0;
  margin-left: 12px;
}

.messages-modal {
  max-height: 60vh;
  overflow-y: auto;
}

.empty-messages {
  text-align: center;
  padding: 40px;
  color: #999;
}

.messages-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.message-item {
  padding: 12px 16px;
  border-radius: 12px;
  max-width: 85%;
}

.user-msg {
  background: #2080f0;
  color: #fff;
  margin-left: auto;
  border-bottom-right-radius: 4px;
}

.ai-msg {
  background: #f5f5f5;
  color: #333;
  border-bottom-left-radius: 4px;
}

.message-role {
  font-size: 11px;
  opacity: 0.7;
  margin-bottom: 4px;
}

.message-text {
  line-height: 1.5;
}

/* Markdown 内容样式 */
.markdown-content { line-height: 1.6; }
.markdown-content :deep(p) { margin: 0 0 0.8em; }
.markdown-content :deep(p:last-child) { margin-bottom: 0; }
.markdown-content :deep(h1), .markdown-content :deep(h2), .markdown-content :deep(h3), .markdown-content :deep(h4) { margin: 1em 0 0.5em; font-weight: 600; }
.markdown-content :deep(h1) { font-size: 1.4em; }
.markdown-content :deep(h2) { font-size: 1.2em; }
.markdown-content :deep(h3) { font-size: 1.1em; }
.markdown-content :deep(ul), .markdown-content :deep(ol) { margin: 0.5em 0; padding-left: 1.5em; }
.markdown-content :deep(li) { margin: 0.25em 0; }
.markdown-content :deep(blockquote) { margin: 0.5em 0; padding: 0.5em 1em; border-left: 3px solid #ddd; background: rgba(0, 0, 0, 0.03); }
.markdown-content :deep(pre.hljs) { margin: 0.5em 0; padding: 12px; border-radius: 6px; overflow-x: auto; background: #f6f8fa; }
.markdown-content :deep(code) { font-family: 'SF Mono', Monaco, Consolas, monospace; font-size: 0.9em; }
.markdown-content :deep(:not(pre) > code) { padding: 0.2em 0.4em; background: rgba(0, 0, 0, 0.05); border-radius: 3px; }
.markdown-content :deep(table) { border-collapse: collapse; margin: 0.5em 0; width: 100%; }
.markdown-content :deep(th), .markdown-content :deep(td) { border: 1px solid #ddd; padding: 8px; text-align: left; }
.markdown-content :deep(th) { background: #f5f5f5; font-weight: 600; }
.markdown-content :deep(a) { color: #2080f0; text-decoration: none; }
.markdown-content :deep(a:hover) { text-decoration: underline; }

/* ===== 移动端样式 ===== */
.history-page-mobile {
  padding: 0;
  background: #f5f7f9;
}

.mobile-header {
  background: #fff;
  padding: 16px;
  border-bottom: 1px solid #eee;
}

.mobile-header-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.mobile-header-top h2 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
}

.mobile-chip-scroll {
  display: flex;
  gap: 6px;
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
  padding-bottom: 4px;
}

.mobile-chip-scroll::-webkit-scrollbar {
  display: none;
}

.mobile-content {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  padding-bottom: calc(56px + env(safe-area-inset-bottom, 0px) + 16px);
}

.mobile-sessions-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.mobile-session-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #fff;
  border-radius: 12px;
  padding: 14px 16px;
  cursor: pointer;
}

.mobile-session-card:active {
  background: #f9f9f9;
}

.mobile-session-info {
  flex: 1;
  min-width: 0;
}

.mobile-session-title {
  font-size: 15px;
  font-weight: 500;
  color: #333;
  margin-bottom: 6px;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.mobile-session-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.mobile-session-time {
  font-size: 12px;
  color: #999;
}

/* ActionSheet 样式 */
.action-sheet {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  background: #fff;
  border-radius: 12px 12px 0 0;
  padding-bottom: env(safe-area-inset-bottom, 0px);
  animation: slideUp 0.25s ease-out;
}

@keyframes slideUp {
  from { transform: translateY(100%); }
  to { transform: translateY(0); }
}

.action-sheet-item {
  display: flex;
  align-items: center;
  padding: 16px 20px;
  font-size: 16px;
  color: #333;
  border-bottom: 1px solid #f0f0f0;
  cursor: pointer;
  transition: background 0.2s;
}

.action-sheet-item:active {
  background: #f5f5f5;
}

.action-sheet-danger {
  color: #e03131;
}

.action-sheet-cancel {
  padding: 16px 20px;
  font-size: 16px;
  color: #999;
  text-align: center;
  cursor: pointer;
  margin-top: 8px;
  background: #f5f5f5;
}

.action-sheet-cancel:active {
  background: #eee;
}
</style>
