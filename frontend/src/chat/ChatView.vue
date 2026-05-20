<template>
  <div class="chat-container">
    <div class="messages" ref="messagesRef">
      <div v-for="(msg, i) in messages" :key="i" :class="['message', msg.role]">
        <div class="message-content">
          <div class="role-label">{{ msg.role === 'user' ? '你' : '助手' }}</div>
          <div class="text" v-html="msg.content"></div>
        </div>
      </div>
      <div v-if="streaming" class="message assistant">
        <div class="message-content">
          <div class="role-label">助手</div>
          <div class="text">{{ streamingText }}<span class="cursor">|</span></div>
        </div>
      </div>
    </div>
    <div class="input-area">
      <n-input
        v-model:value="inputText"
        type="textarea"
        :autosize="{ minRows: 1, maxRows: 4 }"
        placeholder="输入消息..."
        @keyup.enter.exact="sendMessage"
        :disabled="streaming"
      />
      <n-button type="primary" :loading="streaming" @click="sendMessage">
        发送
      </n-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, nextTick } from 'vue'
import { NInput, NButton } from 'naive-ui'
import { useUserStore } from '../stores/user'

const userStore = useUserStore()

interface Message {
  role: 'user' | 'assistant'
  content: string
}

const messages = ref<Message[]>([])
const inputText = ref('')
const streaming = ref(false)
const streamingText = ref('')
const messagesRef = ref<HTMLElement>()

function scrollToBottom() {
  nextTick(() => {
    if (messagesRef.value) {
      messagesRef.value.scrollTop = messagesRef.value.scrollHeight
    }
  })
}

async function sendMessage() {
  const text = inputText.value.trim()
  if (!text || streaming.value) return

  messages.value.push({ role: 'user', content: text })
  inputText.value = ''
  streaming.value = true
  streamingText.value = ''
  scrollToBottom()

  try {
    const response = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${userStore.token}`,
      },
      body: JSON.stringify({ message: text, session_id: 'default' }),
    })

    const reader = response.body?.getReader()
    const decoder = new TextDecoder()

    if (!reader) return

    let buffer = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6))
            if (data.text) {
              streamingText.value += data.text
              scrollToBottom()
            }
          } catch { /* skip non-JSON lines */ }
        }
      }
    }

    messages.value.push({ role: 'assistant', content: streamingText.value })
  } catch (error) {
    messages.value.push({ role: 'assistant', content: '抱歉，请求出错了。' })
  } finally {
    streaming.value = false
    streamingText.value = ''
  }
}
</script>

<style scoped>
.chat-container {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 82px);
}

.messages {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
}

.message {
  margin-bottom: 16px;
}

.message.user .message-content {
  background: #e8f4fd;
  margin-left: 60px;
}

.message.assistant .message-content {
  background: #f5f5f5;
  margin-right: 60px;
}

.message-content {
  padding: 12px 16px;
  border-radius: 8px;
}

.role-label {
  font-size: 12px;
  color: #999;
  margin-bottom: 4px;
}

.text {
  white-space: pre-wrap;
  line-height: 1.6;
}

.cursor {
  animation: blink 1s infinite;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}

.input-area {
  display: flex;
  gap: 8px;
  padding: 16px;
  border-top: 1px solid #e8e8e8;
}
</style>
