<template>
  <div class="chat-panel">
    <div class="chat-header">
      <span class="title">文档对话</span>
      <n-tag v-if="sourceCount > 0" size="small">{{ sourceCount }} 篇文档</n-tag>
    </div>

    <div ref="messagesRef" class="messages">
      <div v-if="messages.length === 0" class="empty-hint">
        <n-text depth="3">选择文档后，向 AI 提问任何关于文档内容的问题</n-text>
      </div>

      <div
        v-for="msg in messages"
        :key="msg.id || msg.tempId"
        class="message"
        :class="msg.role"
      >
        <div class="bubble">
          <div class="content">{{ msg.content }}</div>
          <div v-if="msg.sources?.length" class="sources-list">
            <n-tag
              v-for="s in msg.sources"
              :key="s.index"
              size="tiny"
              round
              :bordered="false"
              style="cursor: pointer"
              @click="$emit('jumpToSource', s)"
            >
              [{{ s.index }}] p.{{ s.page }}
            </n-tag>
          </div>
        </div>
      </div>

      <div v-if="streaming" class="message assistant">
        <div class="bubble">
          <div class="content">{{ streamContent }}<span class="cursor">|</span></div>
        </div>
      </div>
    </div>

    <div class="input-area">
      <n-input
        v-model:value="input"
        type="textarea"
        :autosize="{ minRows: 1, maxRows: 4 }"
        placeholder="输入问题..."
        :disabled="streaming"
        @keydown.enter.exact.prevent="handleSend"
      />
      <n-button
        type="primary"
        :loading="streaming"
        :disabled="!input.trim() || streaming"
        @click="handleSend"
      >
        发送
      </n-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, nextTick, watch } from 'vue'
import { NInput, NButton, NTag, NText } from 'naive-ui'

const props = defineProps<{
  sourceIds: number[]
  sourceCount: number
  kbIds?: number[]
  documentIds?: number[]
}>()

defineEmits<{ jumpToSource: [source: any] }>()

interface Message {
  id?: number
  tempId?: string
  role: string
  content: string
  sources?: any[]
}

const API = '/api/apps/notebook'

const input = ref('')
const messages = ref<Message[]>([])
const streaming = ref(false)
const streamContent = ref('')
const sessionId = ref<number | null>(null)
const messagesRef = ref<HTMLElement>()

function scrollToBottom() {
  nextTick(() => {
    if (messagesRef.value) {
      messagesRef.value.scrollTop = messagesRef.value.scrollHeight
    }
  })
}

watch(() => props.sourceIds, () => {
  messages.value = []
  sessionId.value = null
  streamContent.value = ''
})

async function handleSend() {
  const question = input.value.trim()
  if (!question || streaming.value) return

  const userMsg: Message = { tempId: `u-${Date.now()}`, role: 'user', content: question }
  messages.value.push(userMsg)
  input.value = ''
  scrollToBottom()

  streaming.value = true
  streamContent.value = ''
  let sources: any[] = []

  try {
    const body = JSON.stringify({
      question,
      source_ids: null,
      kb_ids: props.kbIds?.length ? props.kbIds : null,
      document_ids: props.documentIds?.length ? props.documentIds : null,
      session_id: sessionId.value,
    })

    const token = localStorage.getItem('token') || ''
    const resp = await fetch(`${API}/chat/stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`,
      },
      body,
    })

    if (!resp.ok || !resp.body) throw new Error('请求失败')

    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (line.startsWith('event: ')) continue
        if (!line.startsWith('data: ')) continue

        try {
          const data = JSON.parse(line.slice(6))

          if (data.text !== undefined) {
            streamContent.value += data.text
            scrollToBottom()
          } else if (data.session_id !== undefined) {
            sessionId.value = data.session_id
          } else if (data.items !== undefined) {
            sources = data.items
          }
        } catch { /* ignore parse errors */ }
      }
    }

    messages.value.push({
      tempId: `a-${Date.now()}`,
      role: 'assistant',
      content: streamContent.value,
      sources,
    })
  } catch (err: any) {
    messages.value.push({
      tempId: `e-${Date.now()}`,
      role: 'assistant',
      content: `出错了: ${err.message}`,
    })
  } finally {
    streaming.value = false
    streamContent.value = ''
    scrollToBottom()
  }
}
</script>

<style scoped>
.chat-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.chat-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--n-border-color);
}

.chat-header .title {
  font-weight: 600;
  font-size: 15px;
}

.messages {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  background: #fafafa;
}

.empty-hint {
  text-align: center;
  padding: 60px 20px;
}

.message {
  margin-bottom: 16px;
  display: flex;
}

.message.user {
  justify-content: flex-end;
}

.bubble {
  max-width: 75%;
  padding: 10px 14px;
  border-radius: 18px;
  line-height: 1.6;
  font-size: 14px;
  word-break: break-word;
}

.message.user .bubble {
  background: #4b6ec6;
  color: #fff;
  border-bottom-right-radius: 4px;
}

.message.assistant .bubble {
  background: #e8e8ec;
  color: #222;
  border-bottom-left-radius: 4px;
}

.sources-list {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
  margin-top: 8px;
}

.cursor {
  animation: blink 1s steps(1) infinite;
}

@keyframes blink {
  50% { opacity: 0; }
}

.input-area {
  display: flex;
  gap: 8px;
  padding: 12px 16px;
  border-top: 1px solid var(--n-border-color);
  align-items: flex-end;
}

.input-area .n-input {
  flex: 1;
}
</style>
