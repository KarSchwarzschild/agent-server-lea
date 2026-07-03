<script setup lang="ts">
/**
 * 智能问答页面 - 响应式版本
 * PC端：左侧会话列表 + 右侧聊天区
 * 移动端：底部抽屉会话列表 + 全屏聊天 + 一体式输入
 */
import { ref, nextTick, onMounted, computed, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  NLayout,
  NLayoutSider,
  NLayoutContent,
  NInput,
  NButton,
  NTag,
  NSpin,
  NIcon,
  NScrollbar,
  NEmpty,
  NPopconfirm,
  NModal,
  useMessage,
  useDialog,
} from 'naive-ui'
import {
  SendOutline,
  SchoolOutline,
  AddOutline,
  TrashOutline,
  ChatbubbleOutline,
  DownloadOutline,
  SwapHorizontalOutline,
  ListOutline,
  EllipsisVertical,
} from '@vicons/ionicons5'
import {
  getSessions,
  getSessionMessages,
  deleteSession,
  type Session,
  type Message as ApiMessage,
} from '../api/chat'
import { getAgents, type AgentPublic } from '../api/agents'
import { useUserStore } from '../stores/user'
import { useResponsive } from '../composables/useResponsive'
import AgentSelector from '../components/AgentSelector.vue'
import MobileChatInput from '../components/MobileChatInput.vue'
import MobileSessionDrawer from '../components/MobileSessionDrawer.vue'

// Markdown渲染
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'
import 'highlight.js/styles/github.css'
import katex from 'katex'
import 'katex/dist/katex.min.css'

const route = useRoute()
const message = useMessage()
const dialog = useDialog()
const userStore = useUserStore()
const { isMobile } = useResponsive()

// ==================== Markdown配置 ====================

// 代码高亮函数
function highlightCode(str: string, lang: string): string {
  if (lang && hljs.getLanguage(lang)) {
    try {
      return '<pre class="hljs"><code>' +
        hljs.highlight(str, { language: lang, ignoreIllegals: true }).value +
        '</code></pre>'
    } catch {
      // 忽略高亮错误
    }
  }
  return '<pre class="hljs"><code>' + str.replace(/[&<>"']/g, (m) => {
    const map: Record<string, string> = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }
    return map[m] || m
  }) + '</code></pre>'
}

const md: MarkdownIt = new MarkdownIt({
  html: true,
  linkify: true,
  typographer: true,
  highlight: highlightCode
})

// KaTeX公式渲染（行内公式 $...$ 和块级公式 $$...$$）
function renderMathInText(text: string): string {
  if (!text) return ''

  // 块级公式 $$...$$
  text = text.replace(/\$\$([\s\S]+?)\$\$/g, (_, formula) => {
    try {
      return katex.renderToString(formula.trim(), { displayMode: true, throwOnError: false })
    } catch (e) {
      return `<span class="katex-error">$$${formula}$$</span>`
    }
  })

  // 行内公式 $...$（避免匹配货币符号）
  text = text.replace(/\$([^\$\n]+?)\$/g, (_, formula) => {
    try {
      return katex.renderToString(formula.trim(), { displayMode: false, throwOnError: false })
    } catch (e) {
      return `<span class="katex-error">$${formula}$</span>`
    }
  })

  return text
}

function renderMarkdown(text: string): string {
  if (!text) return ''
  // 先渲染公式，再渲染Markdown
  const withMath = renderMathInText(text)
  return md.render(withMath)
}

// ==================== 类型定义 ====================

interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  citations?: Array<{ content?: string; document_id?: string }>
  isStreaming?: boolean
}

// ==================== 状态 ====================

const messagesContainer = ref<HTMLElement>()
const messages = ref<ChatMessage[]>([])
const inputText = ref('')
const loading = ref(false)
const currentSessionId = ref<string>('')

// 智能体相关状态
const currentAgent = ref<AgentPublic | null>(null)
const showAgentSelector = ref(false)
const agents = ref<AgentPublic[]>([])
const DEFAULT_AGENT_ID = 'general-qa'

// 历史会话
const sessions = ref<Session[]>([])
const sessionsLoading = ref(false)
const siderCollapsed = ref(false)

// 移动端：会话抽屉
const showSessionDrawer = ref(false)

// 移动端：更多操作ActionSheet
const showMoreActions = ref(false)

// 引用弹窗
const showCitationModal = ref(false)
const currentCitation = ref<{ content: string; document_id?: string } | null>(null)

// 移动端：引用折叠状态
const expandedCitations = ref<Set<string>>(new Set())

const exampleQuestions = [
  '什么是终身教育？',
  '成人学习有哪些特点？',
  '远程教育的发展趋势是什么？',
  '终身学习理念的核心是什么？'
]

// ==================== 计算属性 ====================

// 当前会话标题
const currentSessionTitle = computed(() => {
  if (!currentSessionId.value) return '新对话'
  const session = sessions.value.find(s => s.session_id === currentSessionId.value)
  return session?.title || '对话'
})

// ==================== 历史会话管理 ====================

async function loadSessions() {
  sessionsLoading.value = true
  try {
    const res = await getSessions({ page: 1, page_size: 50 })
    sessions.value = res.sessions
  } catch (e: any) {
    console.error('加载会话列表失败:', e)
  } finally {
    sessionsLoading.value = false
  }
}

async function switchSession(sessionId: string) {
  if (sessionId === currentSessionId.value) return

  currentSessionId.value = sessionId
  messages.value = []
  loading.value = true

  try {
    const res = await getSessionMessages(sessionId)
    messages.value = res.messages.map((m: ApiMessage) => ({
      id: m.message_id,
      role: m.role,
      content: m.content,
      citations: m.citations as ChatMessage['citations']
    }))
    // 设置会话对应的智能体
    if (res.agent) {
      currentAgent.value = res.agent
      saveLastAgent(res.agent.agent_id)
    }
    scrollToBottom()
  } catch (e: any) {
    message.error('加载对话历史失败')
  } finally {
    loading.value = false
  }
}

function startNewChat() {
  currentSessionId.value = ''
  messages.value = []
  inputText.value = ''
}

async function handleDeleteSession(sessionId: string) {
  try {
    await deleteSession(sessionId)
    sessions.value = sessions.value.filter(s => s.session_id !== sessionId)
    if (currentSessionId.value === sessionId) {
      startNewChat()
    }
    message.success('已删除')
  } catch (e: any) {
    message.error('删除失败')
  }
}

// ==================== 消息处理 ====================

const scrollToBottom = () => {
  nextTick(() => {
    if (messagesContainer.value) {
      messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
    }
  })
}

const sendExample = (text: string) => {
  inputText.value = text
  sendMessage()
}

async function sendMessage() {
  const query = inputText.value.trim()
  if (!query || loading.value) return

  // 添加用户消息
  const userMsgId = `user_${Date.now()}`
  messages.value.push({ id: userMsgId, role: 'user', content: query })
  inputText.value = ''
  loading.value = true
  scrollToBottom()

  // 创建流式响应占位
  const assistantMsgId = `assistant_${Date.now()}`
  messages.value.push({
    id: assistantMsgId,
    role: 'assistant',
    content: '',
    isStreaming: true
  })
  scrollToBottom()

  try {
    // 使用fetch调用流式接口
    const baseURL = import.meta.env.VITE_API_BASE_URL || ''
    const response = await fetch(`${baseURL}/api/user/chat/stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${userStore.token}`
      },
      body: JSON.stringify({
        query,
        session_id: currentSessionId.value || undefined,
        agent_id: currentAgent.value?.agent_id || DEFAULT_AGENT_ID,
      })
    })

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }

    const reader = response.body?.getReader()
    const decoder = new TextDecoder()
    let fullContent = ''
    let citations: ChatMessage['citations'] = []

    if (reader) {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        const chunk = decoder.decode(value, { stream: true })
        const lines = chunk.split('\n')

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6))

              if (data.type === 'session') {
                // 获取到session_id
                currentSessionId.value = data.session_id
              } else if (data.type === 'citations') {
                // 收到知识库检索结果，立即更新到消息中显示
                citations = data.citations || []
                const msgIndex = messages.value.findIndex(m => m.id === assistantMsgId)
                if (msgIndex !== -1 && messages.value[msgIndex]) {
                  messages.value[msgIndex]!.citations = citations
                }
              } else if (data.type === 'content') {
                // 流式内容
                fullContent += data.content
                // 更新消息内容
                const msgIndex = messages.value.findIndex(m => m.id === assistantMsgId)
                if (msgIndex !== -1 && messages.value[msgIndex]) {
                  messages.value[msgIndex]!.content = fullContent
                }
                scrollToBottom()
              } else if (data.type === 'done') {
                // 完成
                citations = data.citations || []
                const msgIndex = messages.value.findIndex(m => m.id === assistantMsgId)
                if (msgIndex !== -1 && messages.value[msgIndex]) {
                  messages.value[msgIndex]!.isStreaming = false
                  messages.value[msgIndex]!.citations = citations
                }
                // 刷新会话列表
                loadSessions()
              } else if (data.type === 'error') {
                throw new Error(data.message)
              }
            } catch (e) {
              // JSON解析错误，忽略
            }
          }
        }
      }
    }
  } catch (error: any) {
    console.error('流式请求失败:', error)
    // 更新为错误消息
    const msgIndex = messages.value.findIndex(m => m.id === assistantMsgId)
    if (msgIndex !== -1 && messages.value[msgIndex]) {
      messages.value[msgIndex]!.content = '抱歉，发生了错误，请稍后重试。'
      messages.value[msgIndex]!.isStreaming = false
    }
    message.error(error.message || '请求失败')
  } finally {
    loading.value = false
    scrollToBottom()
  }
}

// ==================== 智能体管理 ====================

// localStorage key
const LAST_AGENT_KEY = 'chat_last_agent_id'

// 保存上次使用的智能体
function saveLastAgent(agentId: string) {
  localStorage.setItem(LAST_AGENT_KEY, agentId)
}

// 读取上次使用的智能体
function getLastAgentId(): string {
  return localStorage.getItem(LAST_AGENT_KEY) || DEFAULT_AGENT_ID
}

// 加载智能体列表
async function loadAgents() {
  try {
    const res = await getAgents()
    agents.value = res.items

    // 恢复上次使用的智能体
    const lastAgentId = getLastAgentId()
    const lastAgent = res.items.find(a => a.agent_id === lastAgentId)
    if (lastAgent) {
      currentAgent.value = lastAgent
    } else if (res.items.length > 0) {
      // 默认使用第一个智能体
      const defaultAgent = res.items.find(a => a.agent_id === DEFAULT_AGENT_ID)
      currentAgent.value = defaultAgent ?? res.items[0]!
    }
  } catch (e) {
    console.error('加载智能体列表失败:', e)
  }
}

// 处理智能体选择
function handleAgentSelect(agent: AgentPublic) {
  // 如果已有对话，询问是否开始新对话
  if (messages.value.length > 0 && agent.agent_id !== currentAgent.value?.agent_id) {
    dialog.warning({
      title: '切换智能体',
      content: `切换到「${agent.name}」将开始新的对话，当前对话将被保存到历史记录。是否继续？`,
      positiveText: '确认切换',
      negativeText: '取消',
      onPositiveClick: () => {
        currentAgent.value = agent
        saveLastAgent(agent.agent_id)
        startNewChat()
        message.success(`已切换到 ${agent.name}`)
      }
    })
  } else {
    currentAgent.value = agent
    saveLastAgent(agent.agent_id)
    if (messages.value.length === 0) {
      message.success(`已选择 ${agent.name}`)
    }
  }
}

// ==================== 移动端：会话选择 ====================

function handleMobileSessionSelect(session: Session) {
  switchSession(session.session_id)
}

// ==================== 生命周期 ====================

onMounted(async () => {
  console.log('Chat.vue onMounted, route.query:', route.query)

  // 并行加载会话列表和智能体列表
  await Promise.all([loadSessions(), loadAgents()])

  // 检查路由参数，如果有预设问题则自动填充到输入框
  const queryQ = route.query.q
  console.log('queryQ:', queryQ)
  if (queryQ) {
    // route.query.q 可能是 string 或 string[]
    const question = Array.isArray(queryQ) ? queryQ[0] : queryQ
    if (question) {
      console.log('Setting inputText to:', question)
      inputText.value = question
    }
  }
})

// 监听消息变化，自动滚动
watch(messages, () => {
  scrollToBottom()
}, { deep: true })

// ==================== 引用处理 ====================

// 去除HTML标签，提取纯文本
function stripHtml(html: string): string {
  if (!html) return ''
  // 移除HTML标签
  return html.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim()
}

// 截断文本，保留指定长度
function truncateText(text: string, maxLength: number = 100): string {
  const plainText = stripHtml(text)
  if (plainText.length <= maxLength) return plainText
  return plainText.substring(0, maxLength) + '...'
}

// 对引用内容进行去重
function deduplicateCitations(citations: Array<{ content?: string; document_id?: string }> | undefined): Array<{ content?: string; document_id?: string }> {
  if (!citations || citations.length === 0) return []

  const seen = new Set<string>()
  const result: Array<{ content?: string; document_id?: string }> = []

  for (const cite of citations) {
    // 使用纯文本内容的前200字符作为去重key
    const plainText = stripHtml(cite.content || '')
    const key = plainText.substring(0, 200)

    if (!seen.has(key)) {
      seen.add(key)
      result.push(cite)
    }
  }

  return result
}

// 打开引用详情弹窗
function openCitationDetail(citation: { content?: string; document_id?: string }) {
  currentCitation.value = {
    content: stripHtml(citation.content || ''),
    document_id: citation.document_id
  }
  showCitationModal.value = true
}

// 移动端：切换引用展开状态
function toggleCitationsExpand(msgId: string) {
  if (expandedCitations.value.has(msgId)) {
    expandedCitations.value.delete(msgId)
  } else {
    expandedCitations.value.add(msgId)
  }
}

// ==================== 导出功能 ====================

async function exportChat(format: 'markdown' | 'txt' = 'markdown') {
  if (!currentSessionId.value) {
    message.warning('请先选择一个对话')
    return
  }

  try {
    const baseURL = import.meta.env.VITE_API_BASE_URL || ''
    const response = await fetch(
      `${baseURL}/api/user/export/chat/${currentSessionId.value}?format=${format}`,
      {
        method: 'GET',
        headers: {
          'Authorization': `Bearer ${userStore.token}`
        }
      }
    )

    if (!response.ok) {
      throw new Error(`导出失败: HTTP ${response.status}`)
    }

    // 从响应头获取文件名
    const contentDisposition = response.headers.get('Content-Disposition')
    let filename = `对话_${currentSessionId.value}.${format === 'markdown' ? 'md' : 'txt'}`
    if (contentDisposition) {
      const match = contentDisposition.match(/filename\*=UTF-8''(.+)/)
      if (match && match[1]) {
        filename = decodeURIComponent(match[1])
      }
    }

    // 下载文件
    const blob = await response.blob()
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    window.URL.revokeObjectURL(url)
    document.body.removeChild(a)

    message.success('导出成功')
  } catch (error: any) {
    console.error('导出失败:', error)
    message.error(error.message || '导出失败')
  }
}
</script>

<template>
  <div class="chat-page" :class="{ 'chat-page-mobile': isMobile }">
    <!-- ===== PC端布局 ===== -->
    <n-layout v-if="!isMobile" has-sider style="height: 100%">
      <!-- 左侧会话列表 -->
      <n-layout-sider
        bordered
        :collapsed="siderCollapsed"
        collapse-mode="width"
        :collapsed-width="0"
        :width="260"
        show-trigger="bar"
        @collapse="siderCollapsed = true"
        @expand="siderCollapsed = false"
        style="background: #fff"
      >
        <div class="sider-content">
          <div class="sider-header">
            <n-button type="primary" block @click="startNewChat">
              <template #icon>
                <n-icon :component="AddOutline" />
              </template>
              新建对话
            </n-button>
          </div>

          <div class="sessions-title">历史对话</div>

          <n-scrollbar style="max-height: calc(100vh - 180px)">
            <n-spin :show="sessionsLoading">
              <div v-if="sessions.length === 0 && !sessionsLoading" class="no-sessions">
                <n-empty description="暂无历史对话" size="small" />
              </div>
              <div
                v-for="session in sessions"
                :key="session.session_id"
                class="session-item"
                :class="{ active: session.session_id === currentSessionId }"
                @click="switchSession(session.session_id)"
              >
                <n-icon :component="ChatbubbleOutline" class="session-icon" />
                <span class="session-title">{{ session.title || '未命名对话' }}</span>
                <n-popconfirm
                  @positive-click="handleDeleteSession(session.session_id)"
                  positive-text="删除"
                  negative-text="取消"
                >
                  <template #trigger>
                    <n-button
                      text
                      size="tiny"
                      class="delete-btn"
                      @click.stop
                    >
                      <n-icon :component="TrashOutline" />
                    </n-button>
                  </template>
                  确定删除这个对话吗？
                </n-popconfirm>
              </div>
            </n-spin>
          </n-scrollbar>
        </div>
      </n-layout-sider>

      <!-- 右侧聊天区域 -->
      <n-layout-content style="background: #f5f7f9">
        <div class="chat-main">
          <!-- 智能体显示区 -->
          <div class="agent-header">
            <div class="agent-info" @click="showAgentSelector = true">
              <span class="agent-icon">{{ currentAgent?.icon || '🤖' }}</span>
              <div class="agent-detail">
                <span class="agent-name">{{ currentAgent?.name || '选择智能体' }}</span>
                <span class="agent-desc">{{ currentAgent?.description || '点击选择智能体' }}</span>
              </div>
              <n-button size="small" quaternary class="switch-btn">
                <template #icon>
                  <n-icon :component="SwapHorizontalOutline" />
                </template>
                切换
              </n-button>
            </div>
          </div>

          <!-- 顶部工具栏 -->
          <div v-if="currentSessionId && messages.length > 0" class="chat-toolbar">
            <span class="toolbar-title">{{ currentSessionTitle }}</span>
            <div class="toolbar-actions">
              <n-button size="small" quaternary @click="exportChat('markdown')">
                <template #icon>
                  <n-icon :component="DownloadOutline" />
                </template>
                导出 MD
              </n-button>
              <n-button size="small" quaternary @click="exportChat('txt')">
                <template #icon>
                  <n-icon :component="DownloadOutline" />
                </template>
                导出 TXT
              </n-button>
            </div>
          </div>

          <!-- 消息区域 -->
          <div ref="messagesContainer" class="messages-container">
            <!-- 欢迎信息 -->
            <div v-if="messages.length === 0" class="welcome-section">
              <div class="welcome-icon">
                <n-icon :component="SchoolOutline" size="64" color="#2080f0" />
              </div>
              <h2>欢迎使用终身教育智能体</h2>
              <p>我可以帮助您解答终身教育领域的问题，基于知识库进行智能问答。</p>
              <p class="feature-hint">支持 Markdown、代码高亮、数学公式渲染</p>
              <div class="example-questions">
                <n-tag
                  v-for="example in exampleQuestions"
                  :key="example"
                  round
                  :bordered="false"
                  type="info"
                  class="example-tag"
                  @click="sendExample(example)"
                >
                  {{ example }}
                </n-tag>
              </div>
            </div>

            <!-- 消息列表 -->
            <template v-for="msg in messages" :key="msg.id">
              <!-- 用户消息 -->
              <div v-if="msg.role === 'user'" class="message user-message">
                <div class="message-content user-content">
                  {{ msg.content }}
                </div>
              </div>

              <!-- AI消息 -->
              <div v-else class="message ai-message">
                <div class="message-content ai-content">
                  <!-- 流式输出时的光标 -->
                  <div
                    class="markdown-content"
                    v-html="renderMarkdown(msg.content)"
                  ></div>
                  <span v-if="msg.isStreaming" class="typing-cursor">▋</span>

                  <!-- 引用来源 -->
                  <div v-if="msg.citations && msg.citations.length > 0 && !msg.isStreaming" class="citations">
                    <p class="citations-title">参考来源：</p>
                    <div class="citations-list">
                      <div
                        v-for="(cite, i) in deduplicateCitations(msg.citations)"
                        :key="i"
                        class="citation-item"
                        @click="openCitationDetail(cite)"
                      >
                        <span class="citation-index">[{{ i + 1 }}]</span>
                        <span class="citation-text">{{ truncateText(cite.content || '', 120) }}</span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </template>
          </div>

          <!-- 输入区域 -->
          <div class="input-section">
            <div class="input-wrapper">
              <n-input
                v-model:value="inputText"
                type="textarea"
                placeholder="输入您的问题...（Enter发送，Shift+Enter换行）"
                :autosize="{ minRows: 1, maxRows: 4 }"
                :disabled="loading"
                @keydown.enter.exact.prevent="sendMessage"
              />
              <n-button
                type="primary"
                :disabled="loading || !inputText.trim()"
                :loading="loading"
                @click="sendMessage"
              >
                <template #icon>
                  <n-icon :component="SendOutline" />
                </template>
                发送
              </n-button>
            </div>
          </div>
        </div>
      </n-layout-content>
    </n-layout>

    <!-- ===== 移动端布局 ===== -->
    <template v-else>
      <div class="mobile-chat-container">
        <!-- 移动端顶部栏 -->
        <div class="mobile-header">
          <div class="mobile-header-content">
            <div class="mobile-agent-info" @click="showAgentSelector = true">
              <span class="mobile-agent-icon">{{ currentAgent?.icon || '🤖' }}</span>
              <span class="mobile-agent-name">{{ currentAgent?.name || '选择智能体' }}</span>
              <n-icon :component="SwapHorizontalOutline" :size="16" class="mobile-switch-icon" />
            </div>
            <div class="mobile-header-actions">
              <!-- 更多操作按钮（有对话时显示） -->
              <n-button
                v-if="currentSessionId && messages.length > 0"
                quaternary
                circle
                @click="showMoreActions = true"
              >
                <template #icon>
                  <n-icon :component="EllipsisVertical" :size="20" />
                </template>
              </n-button>
              <!-- 会话列表按钮 -->
              <n-button quaternary circle @click="showSessionDrawer = true">
                <template #icon>
                  <n-icon :component="ListOutline" :size="22" />
                </template>
              </n-button>
            </div>
          </div>
        </div>

        <!-- 消息区域 -->
        <div ref="messagesContainer" class="mobile-messages-container">
          <!-- 欢迎信息 -->
          <div v-if="messages.length === 0" class="mobile-welcome-section">
            <div class="welcome-icon">
              <n-icon :component="SchoolOutline" size="48" color="#2080f0" />
            </div>
            <h3>欢迎使用终身教育智能体</h3>
            <p>我可以帮助您解答终身教育领域的问题</p>
            <div class="mobile-example-questions">
              <div
                v-for="example in exampleQuestions"
                :key="example"
                class="mobile-example-item"
                @click="sendExample(example)"
              >
                {{ example }}
              </div>
            </div>
          </div>

          <!-- 消息列表 -->
          <template v-for="msg in messages" :key="msg.id">
            <!-- 用户消息 -->
            <div v-if="msg.role === 'user'" class="message user-message mobile-message">
              <div class="message-content user-content">
                {{ msg.content }}
              </div>
            </div>

            <!-- AI消息 -->
            <div v-else class="message ai-message mobile-message">
              <div class="message-content ai-content">
                <div
                  class="markdown-content"
                  v-html="renderMarkdown(msg.content)"
                ></div>
                <span v-if="msg.isStreaming" class="typing-cursor">▋</span>

                <!-- 移动端引用：可折叠 -->
                <div v-if="msg.citations && msg.citations.length > 0 && !msg.isStreaming" class="mobile-citations">
                  <div
                    class="mobile-citations-header"
                    @click="toggleCitationsExpand(msg.id)"
                  >
                    <span class="citations-title">参考来源 ({{ deduplicateCitations(msg.citations).length }})</span>
                    <span class="expand-icon">{{ expandedCitations.has(msg.id) ? '▲' : '▼' }}</span>
                  </div>
                  <div v-if="expandedCitations.has(msg.id)" class="mobile-citations-list">
                    <div
                      v-for="(cite, i) in deduplicateCitations(msg.citations)"
                      :key="i"
                      class="citation-item"
                      @click="openCitationDetail(cite)"
                    >
                      <span class="citation-index">[{{ i + 1 }}]</span>
                      <span class="citation-text">{{ truncateText(cite.content || '', 80) }}</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </template>
        </div>

        <!-- 移动端输入区 -->
        <MobileChatInput
          v-model="inputText"
          :loading="loading"
          :disabled="loading"
          @send="sendMessage"
        />
      </div>

      <!-- 移动端会话抽屉 -->
      <MobileSessionDrawer
        v-model:show="showSessionDrawer"
        :sessions="sessions"
        :current-session-id="currentSessionId"
        :loading="sessionsLoading"
        @select="handleMobileSessionSelect"
        @delete="handleDeleteSession"
        @new-chat="startNewChat"
      />

      <!-- 移动端更多操作ActionSheet -->
      <n-modal
        v-model:show="showMoreActions"
        :mask-closable="true"
        transform-origin="center"
      >
        <div class="mobile-action-sheet">
          <div class="action-sheet-item" @click="exportChat('markdown'); showMoreActions = false">
            <n-icon :size="20" style="margin-right: 12px;">
              <DownloadOutline />
            </n-icon>
            导出为 Markdown
          </div>
          <div class="action-sheet-item" @click="exportChat('txt'); showMoreActions = false">
            <n-icon :size="20" style="margin-right: 12px;">
              <DownloadOutline />
            </n-icon>
            导出为 TXT
          </div>
          <div class="action-sheet-cancel" @click="showMoreActions = false">
            取消
          </div>
        </div>
      </n-modal>
    </template>

    <!-- 引用详情弹窗（PC和移动端共用） -->
    <n-modal
      v-model:show="showCitationModal"
      preset="card"
      title="引用详情"
      :style="{ width: isMobile ? '90vw' : '600px', maxWidth: '90vw' }"
      :bordered="false"
    >
      <div v-if="currentCitation" class="citation-detail">
        <div v-if="currentCitation.document_id" class="citation-source">
          <span class="label">来源文档：</span>
          <span class="value">{{ currentCitation.document_id }}</span>
        </div>
        <div class="citation-content">
          {{ currentCitation.content }}
        </div>
      </div>
    </n-modal>

    <!-- 智能体选择器（PC和移动端共用） -->
    <AgentSelector
      v-model:visible="showAgentSelector"
      :current-agent-id="currentAgent?.agent_id"
      @select="handleAgentSelect"
    />
  </div>
</template>

<style scoped>
.chat-page {
  height: 100%;
}

/* ===== PC端样式 ===== */

/* 侧边栏样式 */
.sider-content {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 16px;
}

.sider-header {
  margin-bottom: 16px;
}

.sessions-title {
  font-size: 12px;
  color: #999;
  margin-bottom: 8px;
  padding-left: 4px;
}

.no-sessions {
  padding: 24px 0;
}

.session-item {
  display: flex;
  align-items: center;
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s;
  margin-bottom: 4px;
}

.session-item:hover {
  background: #f0f0f0;
}

.session-item.active {
  background: #e8f4ff;
  color: #2080f0;
}

.session-icon {
  flex-shrink: 0;
  margin-right: 8px;
}

.session-title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}

.delete-btn {
  opacity: 0;
  flex-shrink: 0;
  transition: opacity 0.2s;
}

.session-item:hover .delete-btn {
  opacity: 1;
}

/* 聊天主区域 */
.chat-main {
  height: 100%;
  display: flex;
  flex-direction: column;
}

/* 智能体显示区 */
.agent-header {
  padding: 12px 24px;
  background: #fff;
  border-bottom: 1px solid #eee;
}

.agent-info {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  background: #f8fafc;
  border-radius: 12px;
  cursor: pointer;
  transition: all 0.2s;
}

.agent-info:hover {
  background: #f0f4f8;
}

.agent-icon {
  font-size: 32px;
  line-height: 1;
}

.agent-detail {
  flex: 1;
  min-width: 0;
}

.agent-name {
  display: block;
  font-size: 15px;
  font-weight: 600;
  color: #1f2937;
  margin-bottom: 2px;
}

.agent-desc {
  display: block;
  font-size: 12px;
  color: #6b7280;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.switch-btn {
  flex-shrink: 0;
}

/* 顶部工具栏 */
.chat-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 24px;
  background: #fff;
  border-bottom: 1px solid #eee;
}

.toolbar-title {
  font-size: 14px;
  font-weight: 500;
  color: #333;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 300px;
}

.toolbar-actions {
  display: flex;
  gap: 8px;
}

.messages-container {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
  display: flex;
  flex-direction: column;
}

.welcome-section {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  flex: 1;
  min-height: 300px;
  text-align: center;
}

.welcome-icon {
  margin-bottom: 16px;
}

.welcome-section h2 {
  margin: 0 0 8px;
  font-size: 24px;
  font-weight: 600;
  color: #333;
}

.welcome-section p {
  margin: 0 0 8px;
  color: #666;
  max-width: 400px;
}

.feature-hint {
  font-size: 12px;
  color: #999;
  margin-bottom: 16px !important;
}

.example-questions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 8px;
  margin-top: 8px;
}

.example-tag {
  cursor: pointer;
  transition: transform 0.2s;
}

.example-tag:hover {
  transform: scale(1.05);
}

/* 消息样式 */
.message {
  display: flex;
  margin-bottom: 16px;
}

.user-message {
  justify-content: flex-end;
}

.ai-message {
  justify-content: flex-start;
}

.message-content {
  max-width: 75%;
  padding: 12px 16px;
  border-radius: 16px;
}

.user-content {
  background: #2080f0;
  color: #fff;
  border-bottom-right-radius: 4px;
  white-space: pre-wrap;
}

.ai-content {
  background: #fff;
  color: #333;
  border-bottom-left-radius: 4px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
}

/* 打字机光标 */
.typing-cursor {
  display: inline-block;
  animation: blink 1s step-end infinite;
  color: #2080f0;
  font-weight: bold;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}

/* Markdown内容样式 */
.markdown-content {
  line-height: 1.7;
  word-break: break-word;
}

.markdown-content :deep(p) {
  margin: 0 0 12px;
}

.markdown-content :deep(p:last-child) {
  margin-bottom: 0;
}

.markdown-content :deep(h1),
.markdown-content :deep(h2),
.markdown-content :deep(h3),
.markdown-content :deep(h4) {
  margin: 16px 0 8px;
  font-weight: 600;
}

.markdown-content :deep(h1) { font-size: 1.5em; }
.markdown-content :deep(h2) { font-size: 1.3em; }
.markdown-content :deep(h3) { font-size: 1.1em; }

.markdown-content :deep(ul),
.markdown-content :deep(ol) {
  margin: 8px 0;
  padding-left: 24px;
}

.markdown-content :deep(li) {
  margin: 4px 0;
}

.markdown-content :deep(blockquote) {
  margin: 12px 0;
  padding: 8px 16px;
  border-left: 4px solid #2080f0;
  background: #f5f7f9;
  color: #666;
}

.markdown-content :deep(pre.hljs) {
  background: #f6f8fa;
  padding: 12px;
  border-radius: 8px;
  overflow-x: auto;
  margin: 12px 0;
  font-size: 13px;
}

.markdown-content :deep(code) {
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
}

.markdown-content :deep(:not(pre) > code) {
  background: #f0f0f0;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 0.9em;
  color: #d63384;
}

.markdown-content :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 12px 0;
}

.markdown-content :deep(th),
.markdown-content :deep(td) {
  border: 1px solid #ddd;
  padding: 8px 12px;
  text-align: left;
}

.markdown-content :deep(th) {
  background: #f5f7f9;
  font-weight: 600;
}

.markdown-content :deep(a) {
  color: #2080f0;
  text-decoration: none;
}

.markdown-content :deep(a:hover) {
  text-decoration: underline;
}

.markdown-content :deep(hr) {
  border: none;
  border-top: 1px solid #eee;
  margin: 16px 0;
}

/* KaTeX公式样式 */
.markdown-content :deep(.katex-display) {
  margin: 16px 0;
  overflow-x: auto;
}

.markdown-content :deep(.katex) {
  font-size: 1.1em;
}

.markdown-content :deep(.katex-error) {
  color: #cc0000;
  background: #fff0f0;
  padding: 2px 4px;
  border-radius: 4px;
}

/* 引用来源 */
.citations {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid #eee;
}

.citations-title {
  margin: 0 0 8px;
  font-size: 12px;
  color: #999;
  font-weight: 500;
}

.citations-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.citation-item {
  font-size: 12px;
  color: #666;
  line-height: 1.5;
  padding: 8px 10px;
  background: #f9f9f9;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.2s;
  display: flex;
  align-items: flex-start;
  gap: 6px;
}

.citation-item:hover {
  background: #e8f4ff;
  color: #2080f0;
}

.citation-index {
  font-weight: 600;
  color: #2080f0;
  flex-shrink: 0;
}

.citation-text {
  flex: 1;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

/* 引用详情弹窗 */
.citation-detail {
  padding: 8px 0;
}

.citation-source {
  margin-bottom: 12px;
  padding-bottom: 12px;
  border-bottom: 1px solid #eee;
  font-size: 13px;
}

.citation-source .label {
  color: #999;
}

.citation-source .value {
  color: #2080f0;
  font-family: monospace;
  font-size: 12px;
}

.citation-content {
  font-size: 14px;
  line-height: 1.8;
  color: #333;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 400px;
  overflow-y: auto;
}

/* 输入区域 */
.input-section {
  padding: 16px 24px;
  background: #fff;
  border-top: 1px solid #eee;
}

.input-wrapper {
  max-width: 800px;
  margin: 0 auto;
  display: flex;
  gap: 12px;
  align-items: flex-end;
}

.input-wrapper :deep(.n-input) {
  flex: 1;
}

/* ===== 移动端样式 ===== */
.chat-page-mobile {
  height: 100%;
  overflow: hidden;
}

.mobile-chat-container {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #f5f7f9;
}

/* 移动端顶部栏 */
.mobile-header {
  background: #fff;
  border-bottom: 1px solid #eee;
  flex-shrink: 0;
}

.mobile-header-content {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
}

.mobile-agent-info {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: #f5f7f9;
  border-radius: 20px;
  cursor: pointer;
}

.mobile-agent-info:active {
  background: #e8f4ff;
}

.mobile-agent-icon {
  font-size: 20px;
}

.mobile-agent-name {
  font-size: 14px;
  font-weight: 500;
  color: #333;
}

.mobile-switch-icon {
  color: #999;
}

/* 移动端消息区域 */
.mobile-messages-container {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  /* 底部留出输入框空间 */
  padding-bottom: calc(60px + 56px + env(safe-area-inset-bottom, 0px));
}

/* 移动端欢迎 */
.mobile-welcome-section {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 40px 20px;
  text-align: center;
}

.mobile-welcome-section h3 {
  margin: 12px 0 8px;
  font-size: 18px;
  color: #333;
}

.mobile-welcome-section p {
  margin: 0 0 20px;
  font-size: 14px;
  color: #666;
}

.mobile-example-questions {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}

.mobile-example-item {
  padding: 12px 16px;
  background: #fff;
  border-radius: 12px;
  font-size: 14px;
  color: #333;
  cursor: pointer;
  transition: background 0.2s;
  text-align: left;
}

.mobile-example-item:active {
  background: #e8f4ff;
}

/* 移动端消息 */
.mobile-message .message-content {
  max-width: 90%;
}

/* 移动端引用折叠 */
.mobile-citations {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid #eee;
}

.mobile-citations-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 0;
  cursor: pointer;
}

.mobile-citations-header .citations-title {
  margin: 0;
}

.expand-icon {
  font-size: 12px;
  color: #999;
}

.mobile-citations-list {
  margin-top: 8px;
}

/* 移动端顶部操作按钮组 */
.mobile-header-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

/* 移动端ActionSheet样式 */
.mobile-action-sheet {
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
  from {
    transform: translateY(100%);
  }
  to {
    transform: translateY(0);
  }
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
