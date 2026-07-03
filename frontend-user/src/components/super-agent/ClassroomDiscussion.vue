<script setup lang="ts">
/**
 * ClassroomDiscussion — 课堂讨论面板
 * 对齐 OpenMAIC roundtable/index.tsx 的核心 UI：
 *   - 多角色对话列表（teacher/agent/user）
 *   - 流式文本显示
 *   - 用户文字输入 + 语音输入
 *   - SSE 事件驱动（P10c 集成时对接）
 */
import { ref, computed, nextTick, watch, onMounted, onUnmounted } from 'vue'
import { useAudioRecorder } from '@/composables/useAudioRecorder'

export interface DiscussionMessage {
  id: string
  role: 'teacher' | 'agent' | 'user'
  name: string
  content: string
  isDelta?: boolean
  avatar?: string
}

const props = defineProps<{
  topic: string
  prompt?: string
  messages: DiscussionMessage[]
  isStreaming?: boolean
  sessionId?: string
}>()

const emit = defineEmits<{
  (e: 'send', text: string): void
  (e: 'pause'): void
  (e: 'resume'): void
}>()

const inputText = ref('')
const messagesEndRef = ref<HTMLElement | null>(null)
const isSending = ref(false)
const isPaused = ref(false)
const showEndFlash = ref(false)
const isInputOpen = ref(false)

// ─── 语音录制 ───
const audioRecorder = useAudioRecorder()
const showAsrTip = ref(false)

// 首次使用提示
onMounted(() => {
  if (audioRecorder.isSupported.value && !localStorage.getItem('asr_tip_shown')) {
    showAsrTip.value = true
    localStorage.setItem('asr_tip_shown', '1')
    setTimeout(() => { showAsrTip.value = false }, 3000)
  }
})

onUnmounted(() => audioRecorder.cleanup())

// 输入区动态按钮：空输入且支持语音 → 麦克风；有文字 → 发送；录音/转录中 → 停止
const showMicButton = computed(
  () => !inputText.value.trim() && audioRecorder.isSupported.value && audioRecorder.state.value === 'idle',
)
const showStopButton = computed(
  () => audioRecorder.state.value === 'recording' || audioRecorder.state.value === 'processing',
)
const showSendButton = computed(
  () => !showMicButton.value && !showStopButton.value,
)

// 状态横条可见性
const showVoiceBar = computed(() =>
  ['recording', 'processing', 'retry-ready', 'error', 'permission-denied'].includes(audioRecorder.state.value),
)

// 录音进度百分比
const recordingProgress = computed(() =>
  Math.min(100, (audioRecorder.duration.value / audioRecorder.maxDuration) * 100),
)

// 格式化时长
function formatDuration(sec: number): string {
  const m = Math.floor(sec / 60)
  const s = sec % 60
  return `${m}:${s.toString().padStart(2, '0')}`
}

// 动态 placeholder
const placeholderText = computed(() =>
  audioRecorder.isSupported.value
    ? '输入你的观点，或点击右侧麦克风说话...'
    : '输入你的观点...',
)

async function handleMicClick() {
  if (audioRecorder.state.value === 'idle') {
    await audioRecorder.startRecording()
  } else if (audioRecorder.state.value === 'recording') {
    const blob = audioRecorder.stopRecording()
    if (blob && props.sessionId) {
      try {
        const text = await audioRecorder.transcribe(blob, props.sessionId)
        if (text) {
          inputText.value = text // 填充文本框，不自动发送
        }
      } catch {
        // error 状态已在 composable 中设置
      }
    }
  }
}

function handleStopClick() {
  if (audioRecorder.state.value === 'recording') {
    handleMicClick()
  }
  // processing 状态下不做操作（等待网络返回）
}

async function handleRetry() {
  if (!props.sessionId) return
  try {
    const text = await audioRecorder.retry()
    if (text) {
      inputText.value = text
    }
  } catch {
    // error 保留在 composable
  }
}

function handleDiscard() {
  audioRecorder.reset()
}

function send() {
  const text = inputText.value.trim()
  if (!text || isSending.value) return
  isSending.value = true
  emit('send', text)
  inputText.value = ''
  // 用户消息 3s 后自动标记已处理（对齐 OpenMAIC scheduleUserMessageClear）
  setTimeout(() => { isSending.value = false }, 3000)
}

function togglePause() {
  isPaused.value = !isPaused.value
  if (isPaused.value) {
    emit('pause')
  } else {
    emit('resume')
  }
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    send()
  }
}

// 键盘快捷键（对齐 OpenMAIC roundtable: T/V/Space/Esc）
function onGlobalKeydown(e: KeyboardEvent) {
  if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
  if (e.ctrlKey || e.metaKey || e.altKey) return

  switch (e.key) {
    case 't':
    case 'T':
      e.preventDefault()
      isInputOpen.value = !isInputOpen.value
      break
    case ' ':
    case 'Spacebar':
      e.preventDefault()
      togglePause()
      break
    case 'Escape':
      e.preventDefault()
      isInputOpen.value = false
      break
  }
}

onMounted(() => { window.addEventListener('keydown', onGlobalKeydown) })
onUnmounted(() => { window.removeEventListener('keydown', onGlobalKeydown) })

// agent 开始回复时提前清除发送冷却
watch(() => props.messages.length, (newLen, oldLen) => {
  if (newLen > oldLen && isSending.value) {
    const last = props.messages[newLen - 1]
    if (last?.role !== 'user') {
      isSending.value = false
    }
  }
})

// 暴露方法给父组件
function showFlash() {
  showEndFlash.value = true
  setTimeout(() => { showEndFlash.value = false }, 1800)
}

defineExpose({ showFlash })

// 自动滚动到底部
watch(() => props.messages.length, () => {
  nextTick(() => {
    messagesEndRef.value?.scrollIntoView({ behavior: 'smooth' })
  })
})

onMounted(() => {
  messagesEndRef.value?.scrollIntoView()
})

// 角色颜色
function roleBgClass(role: string) {
  if (role === 'agent') return 'msg-agent'
  if (role === 'user') return 'msg-user'
  return 'msg-teacher'
}

function roleLabel(role: string) {
  if (role === 'agent') return '学生'
  if (role === 'user') return '你'
  return '教师'
}

function avatarFallback(name: string) {
  return name?.charAt(0) || '?'
}
</script>

<template>
  <div class="discussion-panel">
    <!-- 话题头部 -->
    <div class="discussion-header">
      <span class="header-icon">💬</span>
      <div class="header-content">
        <div class="header-label">讨论话题</div>
        <p class="header-topic">{{ topic }}</p>
      </div>
    </div>

    <!-- 引导提示 -->
    <div v-if="prompt" class="discussion-prompt">
      <div class="prompt-label">思考引导</div>
      <p class="prompt-text">{{ prompt }}</p>
    </div>

    <!-- 对话流 -->
    <div class="discussion-messages">
      <div
        v-for="msg in messages"
        :key="msg.id"
        class="msg-item"
        :class="{ 'msg-item--right': msg.role === 'user' }"
      >
        <div v-if="msg.role !== 'user'" class="msg-avatar">
          <img v-if="msg.avatar" :src="msg.avatar" :alt="msg.name" class="avatar-img" />
          <span v-else class="avatar-fallback" :class="roleBgClass(msg.role)">
            {{ avatarFallback(msg.name) }}
          </span>
        </div>

        <div class="msg-bubble" :class="roleBgClass(msg.role)">
          <div class="msg-meta">
            <span class="msg-role">{{ roleLabel(msg.role) }}</span>
            <span class="msg-name">{{ msg.name }}</span>
          </div>
          <p class="msg-text">
            {{ msg.content }}
            <span v-if="msg.isDelta && isStreaming" class="typing-cursor">▌</span>
          </p>
        </div>

        <div v-if="msg.role === 'user'" class="msg-avatar">
          <span class="avatar-fallback msg-user">{{ avatarFallback(msg.name) }}</span>
        </div>
      </div>

      <div ref="messagesEndRef" />
    </div>

    <!-- 暂停指示 -->
    <Transition name="fade">
      <div v-if="isPaused" class="pause-indicator" @click="togglePause">
        ⏸ 讨论已暂停 — 点击恢复或按 Space
      </div>
    </Transition>

    <!-- 结束 flash -->
    <Transition name="flash-slide">
      <div v-if="showEndFlash" class="end-flash">
        💬 讨论已结束
      </div>
    </Transition>

    <!-- 输入框 -->
    <div class="discussion-input">
      <!-- 语音状态横条 -->
      <Transition name="voice-bar">
        <div
          v-if="showVoiceBar"
          class="voice-status-bar"
          role="status"
          aria-live="polite"
        >
          <!-- 录音中 -->
          <template v-if="audioRecorder.state.value === 'recording'">
            <span class="recording-dot" />
            <span class="voice-bar-text">录音中 {{ formatDuration(audioRecorder.duration.value) }}</span>
            <div class="recording-progress">
              <div class="recording-progress-fill" :style="{ width: recordingProgress + '%' }" />
            </div>
          </template>

          <!-- 转录中 -->
          <template v-else-if="audioRecorder.state.value === 'processing'">
            <span class="voice-bar-text">正在识别<span class="dots-anim">...</span></span>
          </template>

          <!-- 网络错误 / 重试 -->
          <template v-else-if="audioRecorder.state.value === 'retry-ready'">
            <span class="voice-bar-text voice-bar-error">{{ audioRecorder.errorMessage.value }}</span>
            <button class="voice-bar-btn" @click="handleRetry">重试</button>
            <button class="voice-bar-btn voice-bar-btn--muted" @click="handleDiscard">放弃</button>
          </template>

          <!-- 权限被拒绝 -->
          <template v-else-if="audioRecorder.state.value === 'permission-denied'">
            <span class="voice-bar-text voice-bar-error">{{ audioRecorder.errorMessage.value }}</span>
            <button class="voice-bar-btn voice-bar-btn--muted" @click="audioRecorder.reset()">关闭</button>
          </template>

          <!-- 通用错误 -->
          <template v-else-if="audioRecorder.state.value === 'error'">
            <span class="voice-bar-text voice-bar-error">{{ audioRecorder.errorMessage.value }}</span>
            <button class="voice-bar-btn voice-bar-btn--muted" @click="audioRecorder.reset()">关闭</button>
          </template>
        </div>
      </Transition>

      <textarea
        v-model="inputText"
        class="input-textarea"
        :placeholder="placeholderText"
        rows="1"
        @keydown="onKeydown"
        @focus="(e: FocusEvent) => (e.target as HTMLElement)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })"
      />

      <!-- 动态按钮：发送 / 麦克风 / 停止 -->
      <button
        v-if="showSendButton"
        class="input-send"
        :disabled="!inputText.trim() || isSending"
        @click="send"
      >发送</button>

      <button
        v-else-if="showMicButton"
        class="input-mic"
        :aria-label="'开始语音输入'"
        @click="handleMicClick"
      >
        <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/>
          <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
          <line x1="12" x2="12" y1="19" y2="22"/>
        </svg>
        <!-- 首次使用气泡提示 -->
        <Transition name="tip-fade">
          <span v-if="showAsrTip" class="asr-tip-bubble">可以用语音输入</span>
        </Transition>
      </button>

      <button
        v-else-if="showStopButton"
        class="input-stop"
        :aria-label="audioRecorder.state.value === 'recording' ? '停止录音' : '识别中'"
        :disabled="audioRecorder.state.value === 'processing'"
        @click="handleStopClick"
      >
        <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
          <rect x="4" y="4" width="16" height="16" rx="2"/>
        </svg>
      </button>
    </div>
  </div>
</template>

<style scoped>
.discussion-panel {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  background: radial-gradient(ellipse at center, #f0f4ff, #e8eeff);
  padding: 16px;
  overflow: hidden;
}

/* ─── Header ─── */
.discussion-header {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  padding: 16px;
  background: var(--cp-card-bg, white);
  border-radius: 12px;
  box-shadow: 0 1px 4px rgba(0,0,0,0.06);
  margin-bottom: 12px;
  flex-shrink: 0;
}
.header-icon { font-size: 24px; }
.header-content { flex: 1; }
.header-label { font-size: 12px; font-weight: 600; color: #7c3aed; text-transform: uppercase; letter-spacing: 0.08em; }
.header-topic { font-size: 15px; color: var(--cp-text, #333); margin: 4px 0 0; line-height: 1.5; }

/* ─── Prompt ─── */
.discussion-prompt {
  padding: 12px 16px;
  background: rgba(124,58,237,0.06);
  border-radius: 8px;
  margin-bottom: 12px;
  flex-shrink: 0;
}
.prompt-label { font-size: 11px; font-weight: 600; color: #7c3aed; margin-bottom: 4px; }
.prompt-text { font-size: 13px; color: var(--cp-text-secondary, #555); margin: 0; line-height: 1.5; }

/* ─── Messages ─── */
.discussion-messages {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 4px 0;
}

.msg-item {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}
.msg-item--right {
  flex-direction: row-reverse;
}

.msg-avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  overflow: hidden;
  flex-shrink: 0;
}
.avatar-img { width: 100%; height: 100%; object-fit: cover; }
.avatar-fallback {
  width: 100%; height: 100%;
  display: flex; align-items: center; justify-content: center;
  font-size: 13px; font-weight: 600; color: white; border-radius: 50%;
}
.avatar-fallback.msg-teacher { background: linear-gradient(135deg, #667eea, #764ba2); }
.avatar-fallback.msg-agent { background: linear-gradient(135deg, #3b82f6, #2563eb); }
.avatar-fallback.msg-user { background: linear-gradient(135deg, #8b5cf6, #7c3aed); }

.msg-bubble {
  max-width: 70%;
  padding: 10px 14px;
  border-radius: 16px;
  font-size: 14px;
  line-height: 1.6;
}
.msg-bubble.msg-teacher { background: var(--cp-card-bg, white); border: 1px solid var(--cp-card-border, #e5e7eb); }
.msg-bubble.msg-agent { background: #eff6ff; border: 1px solid #bfdbfe; }
.msg-bubble.msg-user { background: #f5f3ff; border: 1px solid #ddd6fe; }

.msg-meta {
  display: flex;
  gap: 6px;
  align-items: center;
  margin-bottom: 4px;
}
.msg-role { font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.1em; color: var(--cp-text-muted, #999); }
.msg-name { font-size: 12px; font-weight: 500; color: var(--cp-text-secondary, #666); }
.msg-text { margin: 0; color: var(--cp-text, #333); word-break: break-word; white-space: pre-wrap; }

.typing-cursor {
  animation: blink 0.8s infinite;
  color: #7c3aed;
}
@keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: 0; } }

/* ─── Input ─── */
.discussion-input {
  position: relative;
  display: flex;
  gap: 8px;
  padding-top: 12px;
  padding-bottom: env(safe-area-inset-bottom, 0);
  flex-shrink: 0;
}
.input-textarea {
  flex: 1;
  border: 1px solid var(--cp-card-border, #e5e7eb);
  border-radius: 10px;
  padding: 8px 14px;
  font-size: 14px;
  line-height: 1.5;
  resize: none;
  outline: none;
  transition: border-color 0.2s;
  background: var(--cp-card-bg, white);
}
.input-textarea:focus { border-color: #7c3aed; }
.input-send {
  padding: 8px 20px;
  border: none;
  border-radius: 10px;
  background: linear-gradient(135deg, #7c3aed, #6d28d9);
  color: white;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
}
.input-send:disabled { opacity: 0.4; cursor: not-allowed; }
.input-send:not(:disabled):hover { transform: scale(1.02); }

/* ─── Mic button ─── */
.input-mic {
  position: relative;
  width: 40px;
  height: 40px;
  border: none;
  border-radius: 10px;
  background: linear-gradient(135deg, #7c3aed, #6d28d9);
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.2s;
  flex-shrink: 0;
}
.input-mic:hover { transform: scale(1.05); }
.input-mic:active { transform: scale(0.97); }

/* ─── Stop button ─── */
.input-stop {
  width: 40px;
  height: 40px;
  border: none;
  border-radius: 10px;
  background: #ef4444;
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.2s;
  flex-shrink: 0;
}
.input-stop:disabled { opacity: 0.5; cursor: not-allowed; }
.input-stop:not(:disabled):hover { transform: scale(1.05); background: #dc2626; }

/* ─── Voice status bar ─── */
.voice-status-bar {
  position: absolute;
  bottom: calc(100% + 4px);
  left: 0;
  right: 0;
  height: 36px;
  background: rgba(0, 0, 0, 0.75);
  border-radius: 8px;
  display: flex;
  align-items: center;
  padding: 0 12px;
  color: #fff;
  font-size: 13px;
  z-index: 10;
  gap: 8px;
}

.recording-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #ef4444;
  flex-shrink: 0;
  animation: pulse-dot 1s ease-in-out infinite;
}

@keyframes pulse-dot {
  0%, 100% { transform: scale(1); opacity: 1; }
  50% { transform: scale(1.3); opacity: 0.7; }
}

.voice-bar-text {
  flex-shrink: 0;
  white-space: nowrap;
}
.voice-bar-error {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.recording-progress {
  flex: 1;
  height: 3px;
  background: rgba(255, 255, 255, 0.2);
  border-radius: 2px;
  overflow: hidden;
}
.recording-progress-fill {
  height: 100%;
  background: #ef4444;
  border-radius: 2px;
  transition: width 1s linear;
}

.voice-bar-btn {
  padding: 2px 10px;
  border: 1px solid rgba(255, 255, 255, 0.4);
  border-radius: 4px;
  background: transparent;
  color: #fff;
  font-size: 12px;
  cursor: pointer;
  flex-shrink: 0;
  transition: background 0.15s;
}
.voice-bar-btn:hover { background: rgba(255, 255, 255, 0.15); }
.voice-bar-btn--muted { opacity: 0.7; }
.voice-bar-btn--muted:hover { opacity: 1; }

/* 三点动画 */
.dots-anim {
  display: inline-block;
  animation: dots 1.4s steps(4, end) infinite;
  width: 1.2em;
  text-align: left;
  overflow: hidden;
  vertical-align: bottom;
}
@keyframes dots {
  0% { width: 0; }
  25% { width: 0.4em; }
  50% { width: 0.8em; }
  75% { width: 1.2em; }
}

/* ─── ASR tip bubble ─── */
.asr-tip-bubble {
  position: absolute;
  bottom: calc(100% + 8px);
  right: 0;
  padding: 6px 12px;
  background: rgba(0, 0, 0, 0.8);
  color: #fff;
  font-size: 12px;
  border-radius: 6px;
  white-space: nowrap;
  pointer-events: none;
}
.asr-tip-bubble::after {
  content: '';
  position: absolute;
  top: 100%;
  right: 12px;
  border: 5px solid transparent;
  border-top-color: rgba(0, 0, 0, 0.8);
}
.tip-fade-enter-active { transition: opacity 0.3s; }
.tip-fade-leave-active { transition: opacity 0.5s; }
.tip-fade-enter-from, .tip-fade-leave-to { opacity: 0; }

/* ─── Voice bar transition ─── */
.voice-bar-enter-active { transition: all 0.2s ease; }
.voice-bar-leave-active { transition: all 0.15s ease; }
.voice-bar-enter-from { opacity: 0; transform: translateY(4px); }
.voice-bar-leave-to { opacity: 0; transform: translateY(4px); }

/* ─── Pause indicator ─── */
.pause-indicator {
  text-align: center;
  padding: 8px 16px;
  background: rgba(245,158,11,0.1);
  color: #d97706;
  font-size: 13px;
  font-weight: 500;
  border-radius: 8px;
  cursor: pointer;
  flex-shrink: 0;
  transition: background 0.2s;
}
.pause-indicator:hover { background: rgba(245,158,11,0.2); }

/* ─── End flash ─── */
.end-flash {
  position: absolute;
  bottom: 80px;
  left: 50%;
  transform: translateX(-50%);
  padding: 8px 20px;
  background: rgba(0,0,0,0.7);
  color: white;
  font-size: 14px;
  font-weight: 500;
  border-radius: 20px;
  white-space: nowrap;
  z-index: 10;
}

.flash-slide-enter-active { transition: all 0.3s ease; }
.flash-slide-leave-active { transition: all 0.5s ease; }
.flash-slide-enter-from { opacity: 0; transform: translateX(-50%) translateY(12px) scale(0.9); }
.flash-slide-leave-to { opacity: 0; transform: translateX(-50%) translateY(-8px); }

.fade-enter-active, .fade-leave-active { transition: opacity 0.3s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
