<script setup lang="ts">
/**
 * AI 辅助创建预设助手面板
 *
 * 对话模式 → AI 生成 Agent 配置 → 确认创建
 */
import { ref, nextTick, watch } from 'vue'
import {
  NButton, NInput, NIcon, NSpin, NTag, NScrollbar, NSpace,
  useMessage,
} from 'naive-ui'
import { SendOutline } from '@vicons/ionicons5'
import { generateAgent, type AgentGenerateResponse } from '../api/super-agent'
import MarkdownIt from 'markdown-it'

const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  config?: Record<string, any> | null
}

const props = defineProps<{
  visible: boolean
}>()

const emit = defineEmits<{
  'close': []
  'created': []
  'apply-config': [config: Record<string, any>]
}>()

const message = useMessage()

const WELCOME = `你好！我是 AI 助手配置专家。请描述你想创建什么样的 AI 助手，比如：

- 面向什么场景？（教学、科研、写作...）
- 什么风格？（严格、温和、幽默...）
- 需要什么专长？（文献检索、数据分析...）

例如："创建一个专注于教学设计的助手，能帮老师做教案"`

const messages = ref<ChatMessage[]>([{ role: 'assistant', content: WELCOME }])
const inputText = ref('')
const generating = ref(false)
const generatedConfig = ref<Record<string, any> | null>(null)
const scrollbarRef = ref<InstanceType<typeof NScrollbar> | null>(null)

function scrollToBottom() {
  nextTick(() => {
    scrollbarRef.value?.scrollTo({ top: 99999, behavior: 'smooth' })
  })
}

watch(() => messages.value.length, scrollToBottom)

async function handleSend() {
  const content = inputText.value.trim()
  if (!content || generating.value) return

  messages.value.push({ role: 'user', content })
  inputText.value = ''

  const apiMessages = messages.value.map(m => ({ role: m.role, content: m.content }))

  generating.value = true
  try {
    const res: AgentGenerateResponse = await generateAgent({ messages: apiMessages })

    messages.value.push({
      role: 'assistant',
      content: res.reply,
      config: res.agent_config,
    })

    if (res.agent_config) {
      generatedConfig.value = res.agent_config
    }
  } catch (e: any) {
    message.error(e?.response?.data?.detail || e.message || 'AI 服务暂时不可用')
    messages.value.push({ role: 'assistant', content: '抱歉，生成过程出现错误，请重试。' })
  } finally {
    generating.value = false
  }
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSend()
  }
}

function handleApplyConfig() {
  if (!generatedConfig.value) return
  emit('apply-config', generatedConfig.value)
  handleClose()
}

function handleClose() {
  messages.value = [{ role: 'assistant', content: WELCOME }]
  inputText.value = ''
  generatedConfig.value = null
  generating.value = false
  emit('close')
}
</script>

<template>
  <div v-if="props.visible" class="agent-ai-creator">
    <!-- 对话区域 -->
    <n-scrollbar ref="scrollbarRef" class="chat-area">
      <div class="chat-messages">
        <div
          v-for="(msg, idx) in messages"
          :key="idx"
          :class="['chat-message', msg.role === 'user' ? 'user-msg' : 'assistant-msg']"
        >
          <div class="msg-avatar">{{ msg.role === 'user' ? '我' : 'AI' }}</div>
          <div class="msg-content">
            <div class="msg-text markdown-body" v-html="md.render(msg.content)" />

            <!-- 配置摘要卡片 -->
            <div v-if="msg.config" class="config-card">
              <div class="config-card-title">已生成完整助手配置</div>

              <div class="config-section">
                <n-tag size="small" type="info">基本信息</n-tag>
                <div class="config-rows">
                  <div class="config-row"><span class="label">名称</span> {{ msg.config.avatar || '' }} {{ msg.config.name }}</div>
                  <div class="config-row"><span class="label">描述</span> {{ msg.config.description || '-' }}</div>
                  <div class="config-row"><span class="label">可见性</span> {{ msg.config.visibility === 'global' ? '全局' : '个人' }}</div>
                </div>
              </div>

              <div class="config-section" v-if="msg.config.soul_prompt">
                <n-tag size="small" type="info">人格设定</n-tag>
                <div class="config-rows">
                  <div class="config-row">{{ msg.config.soul_prompt.length }} 字</div>
                </div>
              </div>

              <div class="config-section" v-if="msg.config.allowed_skills">
                <n-tag size="small" type="info">技能范围</n-tag>
                <div class="config-tags">
                  <n-tag v-for="s in msg.config.allowed_skills" :key="s" size="tiny" :bordered="false">{{ s }}</n-tag>
                </div>
              </div>
              <div class="config-section" v-else>
                <n-tag size="small" type="info">技能范围</n-tag>
                <div class="config-rows"><div class="config-row">全部可用</div></div>
              </div>

              <div class="config-section" v-if="msg.config.example_questions?.length">
                <n-tag size="small" type="info">示例问题 ({{ msg.config.example_questions.length }})</n-tag>
                <div class="config-rows">
                  <div v-for="(q, i) in msg.config.example_questions.slice(0, 4)" :key="i" class="config-row">
                    {{ i + 1 }}. {{ q }}
                  </div>
                  <div v-if="msg.config.example_questions.length > 4" class="config-row" style="color: #999">
                    ...还有 {{ msg.config.example_questions.length - 4 }} 条
                  </div>
                </div>
              </div>

              <div class="config-section" v-if="msg.config.welcome_message">
                <n-tag size="small" type="info">欢迎语</n-tag>
                <div class="config-rows">
                  <div class="config-row welcome-preview">{{ msg.config.welcome_message.slice(0, 100) }}{{ msg.config.welcome_message.length > 100 ? '...' : '' }}</div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div v-if="generating" class="chat-message assistant-msg">
          <div class="msg-avatar">AI</div>
          <div class="msg-content">
            <n-spin size="small" />
            <span style="margin-left: 8px; color: #999">正在思考...</span>
          </div>
        </div>
      </div>
    </n-scrollbar>

    <!-- 已生成配置时的操作区 -->
    <div v-if="generatedConfig" class="action-bar">
      <div class="action-hint">已生成完整助手配置</div>
      <n-space size="small">
        <n-button type="primary" @click="handleApplyConfig">采纳配置</n-button>
        <n-button @click="generatedConfig = null">继续修改</n-button>
      </n-space>
    </div>

    <!-- 输入区域 -->
    <div class="chat-input">
      <n-input
        v-model:value="inputText"
        type="textarea"
        :autosize="{ minRows: 1, maxRows: 4 }"
        placeholder="描述你想创建的助手..."
        :disabled="generating"
        @keydown="handleKeydown"
      />
      <n-button
        type="primary"
        circle
        :disabled="!inputText.trim() || generating"
        :loading="generating"
        @click="handleSend"
      >
        <template #icon><n-icon :component="SendOutline" /></template>
      </n-button>
    </div>
  </div>
</template>

<style scoped>
.agent-ai-creator {
  display: flex;
  flex-direction: column;
  height: 560px;
}

/* 对话滚动区 */
.chat-area {
  flex: 1;
  overflow-y: auto;
}

.chat-messages {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 20px 24px;
}

.chat-message {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}

.user-msg {
  flex-direction: row-reverse;
}

/* 头像 */
.msg-avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-weight: 600;
  flex-shrink: 0;
  margin-top: 2px;
}

.assistant-msg .msg-avatar { background: #e8f5e9; color: #2e7d32; }
.user-msg .msg-avatar { background: #e3f2fd; color: #1565c0; }

/* 消息气泡 */
.msg-content {
  max-width: 85%;
  background: #f5f7fa;
  border-radius: 14px;
  padding: 12px 16px;
  font-size: 14px;
  line-height: 1.6;
  word-break: break-word;
}

.user-msg .msg-content {
  background: #e3f2fd;
}

/* Markdown 渲染 */
.markdown-body :deep(p) { margin: 0 0 8px 0; }
.markdown-body :deep(p:last-child) { margin-bottom: 0; }
.markdown-body :deep(ul), .markdown-body :deep(ol) { margin: 4px 0; padding-left: 20px; }
.markdown-body :deep(li) { margin: 2px 0; }
.markdown-body :deep(pre) { background: #eef1f5; padding: 10px; border-radius: 6px; overflow-x: auto; font-size: 13px; }
.markdown-body :deep(code) { font-size: 13px; }

/* 配置摘要卡片 */
.config-card {
  margin-top: 12px;
  padding: 14px 16px;
  background: linear-gradient(135deg, #f0faf4, #f8fffe);
  border: 1px solid #c8e6c9;
  border-radius: 10px;
}

.config-card-title {
  font-weight: 600;
  font-size: 13px;
  margin-bottom: 10px;
  color: #1a7f37;
  padding-bottom: 8px;
  border-bottom: 1px solid #e8f5e9;
}

.config-section {
  border-left: 3px solid #a5d6a7;
  padding-left: 12px;
  margin-bottom: 10px;
}

.config-section:last-child { margin-bottom: 0; }

.config-rows { font-size: 13px; color: #555; margin-top: 4px; }
.config-row { margin-bottom: 3px; line-height: 1.5; }
.label { color: #888; font-size: 12px; margin-right: 6px; min-width: 42px; display: inline-block; }

.config-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 4px;
}

.welcome-preview {
  color: #666;
  font-style: italic;
  font-size: 12px;
  line-height: 1.5;
}

/* 操作条 */
.action-bar {
  padding: 10px 24px;
  border-top: 1px solid #e8e8e8;
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #f0f7ff;
  flex-shrink: 0;
}

.action-hint {
  font-size: 13px;
  color: #2080f0;
  font-weight: 500;
}

/* 底部输入框 */
.chat-input {
  display: flex;
  gap: 8px;
  align-items: flex-end;
  padding: 12px 24px 16px;
  border-top: 1px solid #e8e8e8;
  flex-shrink: 0;
  background: #fff;
}

.chat-input .n-input { flex: 1; }
</style>
