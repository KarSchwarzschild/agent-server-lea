<template>
  <div class="chat-page">
    <SessionList :current-id="sessionId" @select="switchSession" ref="sessionListRef" />
    <div class="chat-container">
      <div class="messages" ref="messagesRef">
        <div v-for="(msg, i) in messages" :key="i" :class="['message', msg.role]">
          <div class="message-content">
            <div class="role-label">{{ msg.role === 'user' ? '你' : '助手' }}</div>
            <div v-if="msg.skillName" class="skill-badge">{{ msg.skillName }}</div>
            <div class="text" v-html="msg.content"></div>
          </div>
        </div>

        <!-- Plan 面板 -->
        <PlanPanel
          :plan="currentPlan"
          :approved="planApproved"
          @approve="handlePlanApprove"
          @reject="handlePlanReject"
        />

        <!-- Artifact 面板 -->
        <ArtifactPanel
          ref="artifactPanelRef"
          :slides="artifactSlides"
          :artifact-id="artifactId"
        />

        <div v-if="streaming" class="message assistant">
          <div class="message-content">
            <div class="role-label">助手</div>
            <div class="text">{{ streamingText }}<span class="cursor">|</span></div>
          </div>
        </div>
      </div>

      <ChatInput
        :skills="skills"
        :disabled="streaming"
        @send="sendMessage"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, nextTick, onMounted } from 'vue'
import { useUserStore } from '../stores/user'
import ChatInput from './ChatInput.vue'
import PlanPanel from './PlanPanel.vue'
import ArtifactPanel from './components/ArtifactPanel.vue'
import SessionList from './components/SessionList.vue'
import type { SkillItem } from './SkillPanel.vue'
import request from '../utils/request'

const userStore = useUserStore()

interface Message {
  role: 'user' | 'assistant'
  content: string
  skillName?: string
}

interface PlanStep {
  step: number
  task: string
  tool?: string
  status: string
}

const messages = ref<Message[]>([])
const streaming = ref(false)
const streamingText = ref('')
const messagesRef = ref<HTMLElement>()
const skills = ref<SkillItem[]>([])
const currentPlan = ref<PlanStep[]>([])
const planApproved = ref(false)
const sessionId = ref('default')
const sessionListRef = ref<InstanceType<typeof SessionList>>()

const artifactSlides = ref<string[]>([])
const artifactId = ref('')
const artifactPanelRef = ref<InstanceType<typeof ArtifactPanel>>()

function scrollToBottom() {
  nextTick(() => {
    if (messagesRef.value) {
      messagesRef.value.scrollTop = messagesRef.value.scrollHeight
    }
  })
}

async function fetchSkills() {
  try {
    const { data } = await request.get('/api/skills')
    skills.value = data.skills || []
  } catch { /* ignore */ }
}

async function sendMessage(text: string, skillId: number | null, attachmentIds: number[] = []) {
  if (!text || streaming.value) return

  const skillName = skillId
    ? skills.value.find(s => s.id === skillId)?.display_name
    : undefined

  messages.value.push({ role: 'user', content: text, skillName })
  streaming.value = true
  streamingText.value = ''
  scrollToBottom()

  try {
    const body: Record<string, any> = { message: text, session_id: sessionId.value }
    if (skillId) body.skill_id = skillId
    if (attachmentIds.length) body.attachment_ids = attachmentIds

    const response = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${userStore.token}`,
      },
      body: JSON.stringify(body),
    })

    const reader = response.body?.getReader()
    const decoder = new TextDecoder()
    if (!reader) return

    let buffer = ''
    let currentEventType = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (line.startsWith('event: ')) {
          currentEventType = line.slice(7).trim()
          continue
        }
        if (!line.startsWith('data: ')) continue
        try {
          const data = JSON.parse(line.slice(6))
          const eventType = currentEventType || ''
          currentEventType = ''

          if (data.text) {
            streamingText.value += data.text
            scrollToBottom()
          }
          if (data.plan) {
            currentPlan.value = data.plan
            planApproved.value = false
            scrollToBottom()
          }
          // Slide 事件处理（用 SSE event type 判断）
          if (eventType === 'slide') {
            artifactSlides.value.push(data.svg)
            scrollToBottom()
          }
          if (eventType === 'slide_updated') {
            artifactSlides.value[data.index] = data.svg
            artifactPanelRef.value?.highlightUpdated(data.index)
          }
          if (eventType === 'slide_added') {
            artifactSlides.value.splice(data.index, 0, data.svg)
            artifactPanelRef.value?.highlightUpdated(data.index)
          }
          if (eventType === 'slide_deleted') {
            artifactSlides.value.splice(data.index, 1)
          }
          if (eventType === 'slides_created') {
            artifactId.value = data.artifact_id
          }
          // Plan 步骤执行事件
          if (eventType === 'plan_step_start' && currentPlan.value[data.step - 1]) {
            currentPlan.value[data.step - 1].status = 'in_progress'
          }
          if (eventType === 'plan_step_done' && currentPlan.value[data.step - 1]) {
            currentPlan.value[data.step - 1].status = 'completed'
          }
        } catch { /* skip */ }
      }
    }

    messages.value.push({ role: 'assistant', content: streamingText.value })
  } catch {
    messages.value.push({ role: 'assistant', content: '抱歉，请求出错了。' })
  } finally {
    streaming.value = false
    streamingText.value = ''
  }
}

onMounted(fetchSkills)

function switchSession(newId: string) {
  sessionId.value = newId
  messages.value = []
  currentPlan.value = []
  planApproved.value = false
  artifactSlides.value = []
  artifactId.value = ''
  loadMessages(newId)
}

async function loadMessages(sid: string) {
  try {
    const response = await fetch(`/api/chat/sessions/${sid}/messages`, {
      headers: { 'Authorization': `Bearer ${userStore.token}` },
    })
    const data = await response.json()
    if (data.messages && data.messages.length) {
      messages.value = data.messages
      scrollToBottom()
    }
  } catch { /* ignore */ }
}

function handlePlanApprove() {
  planApproved.value = true
  sendMessage('确认执行', null, [])
}

function handlePlanReject() {
  currentPlan.value = []
  planApproved.value = false
  sendMessage('取消', null, [])
}
</script>

<style scoped>
.chat-page {
  display: flex;
  height: calc(100vh - 82px);
}

.chat-container {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-width: 0;
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

.skill-badge {
  display: inline-block;
  font-size: 11px;
  background: #e0e7ff;
  color: #4338ca;
  padding: 1px 8px;
  border-radius: 10px;
  margin-bottom: 6px;
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
</style>
