<script setup lang="ts">
/**
 * ChatV2 — 直连 Go lea-agent 的精简对话页
 *
 * 与 SuperAgent.vue 完全独立，不共享任何状态；只关心 Go 已经出来的能力：
 *   - agent 列表 / 默认 agent
 *   - session / message CRUD
 *   - SSE 7 事件（start/delta/thinking/tool_event/result/error/done）
 *   - 附件上传（→ lea-parser → 拼进消息）
 *   - Cursor 风格 slash → skill 提示词模板
 */
import { computed, onMounted, onUnmounted, nextTick, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import {
  NButton, NIcon, NInput, NSelect, NSpin, NTag, NUpload, useMessage,
} from 'naive-ui'
import type { UploadFileInfo } from 'naive-ui'
import {
  AttachOutline, PaperPlaneOutline, TrashOutline, AddOutline, ChatbubbleEllipsesOutline,
  CloudDownloadOutline, DocumentOutline, FlashOutline,
} from '@vicons/ionicons5'
import { useAgentChatStore } from '../stores/agent-chat'
import type { MessageRow, SandboxCallRow, ToolCallRow, SkillInfo } from '../api/agent-chat'
import { downloadArtifact } from '../api/agent-chat'
import SkillPicker from '../components/chat-v2/SkillPicker.vue'
import WorkspaceDrawer from '../components/chat-v2/WorkspaceDrawer.vue'

const store = useAgentChatStore()
const {
  agents, activeAgentId, activeAgent, activeAgentSkills,
  sessions, activeSessionId, messages, currentTurn, pendingAttachments, streaming,
} = storeToRefs(store)

const route = useRoute()
const router = useRouter()
const message = useMessage()

// ── input state ──
const inputText = ref('')
const inputRef = ref<InstanceType<typeof NInput> | null>(null)
const uploading = ref(false)

// ── skill picker state ──
const showPicker = ref(false)
const pickerKeyword = ref('')
const pickerRef = ref<InstanceType<typeof SkillPicker> | null>(null)

// 检测输入框以 "/xxx" 开头 → 弹面板
watch(inputText, v => {
  if (v.startsWith('/') && !v.includes('\n')) {
    pickerKeyword.value = v.slice(1)
    showPicker.value = activeAgentSkills.value.length > 0
  } else {
    showPicker.value = false
    pickerKeyword.value = ''
  }
})

const onKeyDown = (e: KeyboardEvent) => {
  if (showPicker.value && pickerRef.value) {
    const keysHandled = ['ArrowDown', 'ArrowUp', 'Enter', 'Escape']
    if (keysHandled.includes(e.key)) {
      pickerRef.value.onKey(e)
      return
    }
  }
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    void doSend()
  }
}

const onPickSkill = (s: SkillInfo) => {
  const label = s.display_name || s.name
  inputText.value = `请使用「${label}」技能：`
  showPicker.value = false
  // 光标定位到末尾
  nextTick(() => {
    const el = document.querySelector<HTMLTextAreaElement>('.chat-v2-input textarea')
    if (el) {
      el.focus()
      el.setSelectionRange(el.value.length, el.value.length)
    }
  })
}

// ── send flow ──
const doSend = async () => {
  if (streaming.value) return
  const text = inputText.value.trim()
  if (!text && pendingAttachments.value.length === 0) return
  inputText.value = ''
  showPicker.value = false
  try {
    await store.send(text)
    // send 内部会把 activeSessionId 置为后端返回值；同步到路由
    if (activeSessionId.value && !route.params.sessionId) {
      router.replace({ name: 'ChatV2', params: { sessionId: activeSessionId.value } })
    }
  } catch (e) {
    message.error(`发送失败：${(e as Error).message}`)
  }
}

// ── attachments ──
const onFileChange = async ({ file }: { file: UploadFileInfo }) => {
  if (!file.file) return
  uploading.value = true
  try {
    const uploaded = await store.addAttachment(file.file)
    message.success(`已解析：${uploaded.filename}`)
  } catch (e) {
    message.error(`上传失败：${(e as Error).message}`)
  } finally {
    uploading.value = false
  }
}

// ── sessions sidebar ──
const openSessionFromList = async (sid: string) => {
  await store.openSession(sid)
  router.replace({ name: 'ChatV2', params: { sessionId: sid } })
}

const newChat = () => {
  store.newSession()
  router.replace({ name: 'ChatV2', params: {} })
}

const doArchive = async (sid: string) => {
  try {
    await store.archiveSession(sid)
    message.success('会话已归档')
    if (activeSessionId.value === null) router.replace({ name: 'ChatV2', params: {} })
  } catch (e) {
    message.error(`归档失败：${(e as Error).message}`)
  }
}

// ── agent switch ──
const agentOptions = computed(() =>
  agents.value.map(a => ({ label: a.name, value: a.id })),
)

const onAgentChange = async (id: number) => {
  await store.switchAgent(id)
}

// ── mount / route sync ──
onMounted(async () => {
  await store.loadAgents()
  await store.loadSessions()
  const sid = route.params.sessionId as string | undefined
  if (sid) await store.openSession(sid)
})

onUnmounted(() => {
  // stream 停止交给 store 内部（openSession/newSession 已经清）
})

watch(() => route.params.sessionId, async v => {
  const sid = v as string | undefined
  if (sid && sid !== activeSessionId.value) await store.openSession(sid)
})

// ── UI helpers ──
const shouldShowEmpty = computed(() =>
  messages.value.length === 0 && !streaming.value && activeAgent.value,
)

const exampleQuestions = computed(() => activeAgent.value?.example_questions || [])

const pickExample = (q: string) => {
  inputText.value = q
}

const formatArtifact = (a: string) => a

const downloadingArtifacts = ref(new Set<string>())

// 点击 artifact 卡：fetch → blob → 触发浏览器 download。
// 需要 activeSessionId（老历史消息也在同一个 session 下），无 session_id 时不给点。
const handleArtifactClick = async (relPath: string) => {
  if (!activeSessionId.value) {
    message.warning('会话尚未就绪，请稍后再试')
    return
  }
  const key = `${activeSessionId.value}:${relPath}`
  if (downloadingArtifacts.value.has(key)) return
  downloadingArtifacts.value.add(key)
  try {
    await downloadArtifact(activeSessionId.value, relPath)
  } catch (e: any) {
    message.error(e?.message || '下载失败')
  } finally {
    downloadingArtifacts.value.delete(key)
  }
}
const isArtifactDownloading = (relPath: string) =>
  !!activeSessionId.value &&
  downloadingArtifacts.value.has(`${activeSessionId.value}:${relPath}`)

// 展开 tool 卡时的详细内容：
// - result_full 有 → pretty-print JSON（结构化数据首选）
// - 否则用 result（一行摘要 fallback）
// - 空 / 占位词 → 空串（模板层用 v-if 判断不渲染）
function formatToolDetail(ref: { result?: string; result_full?: unknown }): string {
  if (ref.result_full != null) {
    try {
      return JSON.stringify(ref.result_full, null, 2)
    } catch { /* 循环引用等异常，回退 */ }
  }
  const r = (ref.result || '').trim()
  if (!r) return ''
  return r
}

function formatToolArgs(input: unknown): string {
  if (!input || typeof input !== 'object') return ''
  const entries = Object.entries(input as Record<string, unknown>)
  if (!entries.length) return ''
  return entries.map(([k, v]) => {
    const s = typeof v === 'string' ? v : JSON.stringify(v) ?? ''
    return `${k}=${s.length > 40 ? '"' + s.slice(0, 37) + '…"' : JSON.stringify(v)}`
  }).join(', ')
}

// Go engine 目前 tool 完成事件的 Message 只塞 "completed"/"failed" 之类占位状态词，
// 展开一个纯占位串没有信息量。判断这类"通用状态词"直接不给可展开 UI。
// 未来 agentloop 补真实 Result 字段后，把这个 filter 移掉即可。
const TOOL_RESULT_PLACEHOLDERS = new Set(['completed', 'success', 'ok', 'done', ''])
function hasToolDetail(ref: { result?: string }): boolean {
  const r = (ref?.result || '').trim().toLowerCase()
  return r.length > 0 && !TOOL_RESULT_PLACEHOLDERS.has(r)
}
const isAssistant = (m: MessageRow) => m.role === 'assistant'

// 一轮跑完（streaming true → false）通知抽屉刷新：沙箱可能刚写了文件。
// 流式过程中不刷 —— 文件还没落完，白跑请求。
const workspaceRefreshKey = ref(0)
watch(streaming, (now, before) => {
  if (before && !now) workspaceRefreshKey.value++
})
</script>

<template>
  <div class="chat-v2">
    <!-- 左侧会话列表 -->
    <aside class="chat-v2-sider">
      <div class="chat-v2-sider-head">
        <NButton block secondary size="small" @click="newChat">
          <template #icon><NIcon :component="AddOutline" /></template>
          新对话
        </NButton>
      </div>

      <div class="chat-v2-sider-list">
        <div
          v-for="s in sessions"
          :key="s.id"
          class="chat-v2-sider-item"
          :class="{ active: s.id === activeSessionId }"
          @click="openSessionFromList(s.id)"
        >
          <div class="chat-v2-sider-item-title">{{ s.title || '新对话' }}</div>
          <div class="chat-v2-sider-item-meta">
            {{ s.message_count }} 条 · {{ new Date(s.last_active_at).toLocaleDateString() }}
          </div>
          <NButton
            text size="tiny" class="chat-v2-sider-archive"
            @click.stop="doArchive(s.id)"
          >
            <NIcon :component="TrashOutline" />
          </NButton>
        </div>
        <div v-if="!sessions.length" class="chat-v2-sider-empty">还没有会话</div>
      </div>
    </aside>

    <!-- 中间聊天区 -->
    <main class="chat-v2-main">
      <header class="chat-v2-header">
        <div class="chat-v2-header-left">
          <NIcon :component="ChatbubbleEllipsesOutline" size="20" />
          <span class="chat-v2-title">{{ activeAgent?.name || '对话' }}</span>
        </div>
        <div class="chat-v2-header-right">
          <NSelect
            v-model:value="activeAgentId"
            :options="agentOptions"
            :consistent-menu-width="false"
            size="small"
            style="min-width: 180px"
            @update:value="onAgentChange"
          />
        </div>
      </header>

      <div class="chat-v2-body">
        <!-- 空态 -->
        <div v-if="shouldShowEmpty" class="chat-v2-empty">
          <h2 class="chat-v2-empty-title">{{ activeAgent?.welcome_message || '你好，我能帮你什么？' }}</h2>
          <div v-if="exampleQuestions.length" class="chat-v2-empty-examples">
            <NButton
              v-for="q in exampleQuestions"
              :key="q"
              size="small"
              @click="pickExample(q)"
            >
              {{ q }}
            </NButton>
          </div>
        </div>

        <!-- 历史消息 -->
        <div v-else class="chat-v2-messages">
          <div
            v-for="m in messages"
            :key="m.id"
            class="chat-v2-msg"
            :class="{ user: m.role === 'user', assistant: m.role === 'assistant' }"
          >
            <div class="chat-v2-msg-role">{{ m.role === 'user' ? '你' : (activeAgent?.name || '助手') }}</div>

            <!-- 有 segments（本次会话内的 turn）：按时间线渲染 -->
            <div v-if="isAssistant(m) && m.extra?.segments?.length" class="chat-v2-timeline">
              <template v-for="(seg, i) in m.extra.segments" :key="i">
                <div v-if="seg.kind === 'text'" class="chat-v2-msg-content chat-v2-seg-text">{{ seg.content }}</div>

                <details v-else-if="seg.kind === 'thinking'" class="chat-v2-seg-thinking">
                  <summary>思考 · {{ seg.content.length }} 字</summary>
                  <pre>{{ seg.content }}</pre>
                </details>

                <details v-else-if="seg.kind === 'tool'" class="chat-v2-seg-tool" :open="seg.ref.status === 'error'">
                  <summary class="chat-v2-tool-summary">
                    <span class="chat-v2-tool-icon">{{ seg.ref.status === 'error' ? '✗' : '✓' }}</span>
                    <span class="chat-v2-tool-call">{{ seg.ref.tool }}({{ formatToolArgs(seg.ref.input) }})</span>
                    <span v-if="seg.ref.durationMs" class="chat-v2-tool-ms">{{ seg.ref.durationMs }}ms</span>
                  </summary>
                  <pre v-if="formatToolDetail(seg.ref)" class="chat-v2-seg-tool-result">{{ formatToolDetail(seg.ref) }}</pre>
                </details>

                <div v-else-if="seg.kind === 'sandbox'" class="chat-v2-seg-sandbox">
                  <div class="chat-v2-sandbox-head">
                    <NTag size="tiny" :type="seg.ref.status === 'error' ? 'error' : 'success'">
                      <NIcon :component="FlashOutline" /> {{ seg.ref.language || 'code' }}
                    </NTag>
                    <span v-if="seg.ref.durationMs" class="chat-v2-tool-ms">{{ seg.ref.durationMs }} ms</span>
                  </div>
                  <pre v-if="seg.ref.codePreview" class="chat-v2-sandbox-code">{{ seg.ref.codePreview }}</pre>
                  <div v-if="seg.ref.stdoutPreview" class="chat-v2-sandbox-stdout">{{ seg.ref.stdoutPreview }}</div>
                  <div v-if="seg.ref.artifacts?.length" class="chat-v2-sandbox-artifacts">
                    <div class="chat-v2-tools-title">生成的文件</div>
                    <div
                      v-for="a in seg.ref.artifacts"
                      :key="a"
                      class="chat-v2-artifact chat-v2-artifact--clickable"
                      :class="{ 'is-downloading': isArtifactDownloading(a) }"
                      role="button"
                      tabindex="0"
                      :title="`下载 ${a}`"
                      @click="handleArtifactClick(a)"
                      @keydown.enter="handleArtifactClick(a)"
                    >
                      <NIcon :component="DocumentOutline" />
                      <span>{{ formatArtifact(a) }}</span>
                      <NIcon
                        v-if="isArtifactDownloading(a)"
                        :component="CloudDownloadOutline"
                        class="chat-v2-artifact-spinner"
                      />
                    </div>
                  </div>
                </div>
              </template>
            </div>

            <!-- 无 segments（刷新回填的历史消息 / user 消息）：走 content + 底部展开 -->
            <template v-else>
              <div class="chat-v2-msg-content" v-text="m.content" />

              <template v-if="isAssistant(m) && m.extra">
                <div v-if="m.extra.tool_calls?.length" class="chat-v2-tools">
                  <div class="chat-v2-tools-title">工具调用 ({{ m.extra.tool_calls.length }})</div>
                  <div v-for="tc in m.extra.tool_calls" :key="tc.toolCallId" class="chat-v2-tool-item">
                    <NTag size="tiny" :type="tc.status === 'error' ? 'error' : 'success'">
                      {{ tc.tool }}
                    </NTag>
                    <span v-if="tc.durationMs" class="chat-v2-tool-ms">{{ tc.durationMs }} ms</span>
                  </div>
                </div>

                <div v-if="m.extra.sandbox_calls?.length" class="chat-v2-sandbox">
                  <div class="chat-v2-tools-title">
                    <NIcon :component="FlashOutline" /> 沙箱执行
                  </div>
                  <div v-for="sc in m.extra.sandbox_calls" :key="sc.callId" class="chat-v2-sandbox-item">
                    <div class="chat-v2-sandbox-head">
                      <NTag size="tiny" :type="sc.status === 'error' ? 'error' : 'success'">
                        {{ sc.language || 'code' }}
                      </NTag>
                      <span v-if="sc.durationMs" class="chat-v2-tool-ms">{{ sc.durationMs }} ms</span>
                    </div>
                    <pre v-if="sc.codePreview" class="chat-v2-sandbox-code">{{ sc.codePreview }}</pre>
                    <div v-if="sc.stdoutPreview" class="chat-v2-sandbox-stdout">{{ sc.stdoutPreview }}</div>
                    <div v-if="sc.artifacts?.length" class="chat-v2-sandbox-artifacts">
                      <div class="chat-v2-tools-title">生成的文件</div>
                      <div
                        v-for="a in sc.artifacts"
                        :key="a"
                        class="chat-v2-artifact chat-v2-artifact--clickable"
                        :class="{ 'is-downloading': isArtifactDownloading(a) }"
                        role="button"
                        tabindex="0"
                        :title="`下载 ${a}`"
                        @click="handleArtifactClick(a)"
                        @keydown.enter="handleArtifactClick(a)"
                      >
                        <NIcon :component="DocumentOutline" />
                        <span>{{ formatArtifact(a) }}</span>
                        <NIcon
                          v-if="isArtifactDownloading(a)"
                          :component="CloudDownloadOutline"
                          class="chat-v2-artifact-spinner"
                        />
                      </div>
                    </div>
                  </div>
                </div>

                <details v-if="m.extra.reasoning_blocks?.length" class="chat-v2-reasoning">
                  <summary>思考过程</summary>
                  <pre v-for="(b, i) in m.extra.reasoning_blocks" :key="i">{{ b.reasoning }}</pre>
                </details>
              </template>
            </template>
          </div>

          <!-- 正在流式的 turn — 按 segments 时间线渲染 -->
          <div v-if="streaming" class="chat-v2-msg assistant streaming">
            <div class="chat-v2-msg-role">{{ activeAgent?.name || '助手' }} <NSpin size="small" /></div>
            <div class="chat-v2-timeline">
              <template v-for="(seg, i) in currentTurn.segments" :key="i">
                <div v-if="seg.kind === 'text'" class="chat-v2-msg-content chat-v2-seg-text">{{ seg.content }}</div>

                <details v-else-if="seg.kind === 'thinking'" class="chat-v2-seg-thinking">
                  <summary>思考 · {{ seg.content.length }} 字</summary>
                  <pre>{{ seg.content }}</pre>
                </details>

                <details v-else-if="seg.kind === 'tool'" class="chat-v2-seg-tool" :open="seg.ref.status === 'error'">
                  <summary class="chat-v2-tool-summary">
                    <span class="chat-v2-tool-icon">{{ seg.ref.status === 'error' ? '✗' : (seg.ref.status === 'success' ? '✓' : '●') }}</span>
                    <span class="chat-v2-tool-call">{{ seg.ref.tool }}({{ formatToolArgs(seg.ref.input) }})</span>
                    <span v-if="seg.ref.durationMs" class="chat-v2-tool-ms">{{ seg.ref.durationMs }}ms</span>
                  </summary>
                  <pre v-if="formatToolDetail(seg.ref)" class="chat-v2-seg-tool-result">{{ formatToolDetail(seg.ref) }}</pre>
                </details>

                <div v-else-if="seg.kind === 'sandbox'" class="chat-v2-seg-sandbox">
                  <div class="chat-v2-sandbox-head">
                    <NTag size="tiny" :type="seg.ref.status === 'error' ? 'error' : (seg.ref.status === 'success' ? 'success' : 'info')">
                      <NIcon :component="FlashOutline" /> {{ seg.ref.language || 'code' }} · {{ seg.ref.status || 'running' }}
                    </NTag>
                    <span v-if="seg.ref.durationMs" class="chat-v2-tool-ms">{{ seg.ref.durationMs }} ms</span>
                  </div>
                  <pre v-if="seg.ref.codePreview" class="chat-v2-sandbox-code">{{ seg.ref.codePreview }}</pre>
                  <div v-if="seg.ref.stdoutPreview" class="chat-v2-sandbox-stdout">{{ seg.ref.stdoutPreview }}</div>
                  <div v-if="seg.ref.artifacts?.length" class="chat-v2-sandbox-artifacts">
                    <div class="chat-v2-tools-title">生成的文件</div>
                    <div
                      v-for="a in seg.ref.artifacts"
                      :key="a"
                      class="chat-v2-artifact chat-v2-artifact--clickable"
                      :class="{ 'is-downloading': isArtifactDownloading(a) }"
                      role="button"
                      tabindex="0"
                      :title="`下载 ${a}`"
                      @click="handleArtifactClick(a)"
                      @keydown.enter="handleArtifactClick(a)"
                    >
                      <NIcon :component="DocumentOutline" />
                      <span>{{ formatArtifact(a) }}</span>
                      <NIcon
                        v-if="isArtifactDownloading(a)"
                        :component="CloudDownloadOutline"
                        class="chat-v2-artifact-spinner"
                      />
                    </div>
                  </div>
                </div>
              </template>
            </div>
          </div>
        </div>
      </div>

      <!-- 底部输入区 -->
      <div class="chat-v2-input-wrap">
        <!-- 待发送的附件 -->
        <div v-if="pendingAttachments.length" class="chat-v2-attachments">
          <NTag
            v-for="a in pendingAttachments" :key="a.id"
            closable size="small"
            @close="store.removeAttachment(a.id)"
          >
            {{ a.filename }}
          </NTag>
        </div>

        <div class="chat-v2-input-row">
          <SkillPicker
            ref="pickerRef"
            :visible="showPicker"
            :keyword="pickerKeyword"
            :skills="activeAgentSkills"
            @pick="onPickSkill"
            @close="showPicker = false"
          />

          <NInput
            ref="inputRef"
            v-model:value="inputText"
            class="chat-v2-input"
            type="textarea"
            :autosize="{ minRows: 1, maxRows: 6 }"
            placeholder="发消息…  输入 / 选技能, Shift+Enter 换行"
            :disabled="streaming"
            @keydown="onKeyDown"
          />

          <NUpload
            :show-file-list="false"
            :custom-request="() => { /* placeholder */ }"
            @change="onFileChange"
          >
            <NButton :loading="uploading" quaternary circle>
              <template #icon><NIcon :component="AttachOutline" /></template>
            </NButton>
          </NUpload>

          <NButton type="primary" :loading="streaming" @click="doSend">
            <template #icon><NIcon :component="PaperPlaneOutline" /></template>
            发送
          </NButton>
        </div>
      </div>
    </main>

    <!-- 右侧工作区文件抽屉 -->
    <WorkspaceDrawer
      class="chat-v2-workspace"
      :session-id="activeSessionId"
      :refresh-key="workspaceRefreshKey"
    />
  </div>
</template>

<style scoped>
.chat-v2 {
  display: flex;
  height: calc(100vh - 60px);
  background: #f9fafb;
}

.chat-v2-sider {
  width: 240px;
  border-right: 1px solid #e5e7eb;
  background: #fff;
  display: flex;
  flex-direction: column;
}

.chat-v2-workspace {
  width: 260px;
  flex: 0 0 260px;
}

/* 窄屏先让聊天区占满；移动端抽屉留后续 sprint */
@media (max-width: 1200px) {
  .chat-v2-workspace {
    display: none;
  }
}

.chat-v2-sider-head {
  padding: 12px;
  border-bottom: 1px solid #f3f4f6;
}

.chat-v2-sider-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}

.chat-v2-sider-item {
  position: relative;
  padding: 10px 12px;
  border-radius: 6px;
  cursor: pointer;
  margin-bottom: 4px;
  transition: background 0.15s;
}

.chat-v2-sider-item:hover {
  background: #f3f4f6;
}

.chat-v2-sider-item.active {
  background: #eff6ff;
}

.chat-v2-sider-item-title {
  font-size: 13px;
  color: #111827;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chat-v2-sider-item-meta {
  font-size: 11px;
  color: #9ca3af;
  margin-top: 2px;
}

.chat-v2-sider-archive {
  position: absolute;
  top: 6px;
  right: 6px;
  opacity: 0;
  transition: opacity 0.15s;
}

.chat-v2-sider-item:hover .chat-v2-sider-archive {
  opacity: 1;
}

.chat-v2-sider-empty {
  padding: 24px 12px;
  text-align: center;
  color: #9ca3af;
  font-size: 12px;
}

.chat-v2-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.chat-v2-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 20px;
  background: #fff;
  border-bottom: 1px solid #e5e7eb;
}

.chat-v2-header-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.chat-v2-title {
  font-size: 15px;
  font-weight: 600;
  color: #111827;
}

.chat-v2-body {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}

.chat-v2-empty {
  max-width: 720px;
  margin: 60px auto;
  text-align: center;
}

.chat-v2-empty-title {
  font-size: 22px;
  color: #111827;
  margin-bottom: 24px;
}

.chat-v2-empty-examples {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;
}

.chat-v2-messages {
  max-width: 860px;
  margin: 0 auto;
}

.chat-v2-msg {
  padding: 12px 0;
  margin-bottom: 16px;
}

.chat-v2-msg-role {
  font-size: 12px;
  color: #6b7280;
  margin-bottom: 6px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.chat-v2-msg-content {
  padding: 10px 14px;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  white-space: pre-wrap;
  color: #111827;
  font-size: 14px;
  line-height: 1.6;
}

.chat-v2-msg.user .chat-v2-msg-content {
  background: #eff6ff;
  border-color: #dbeafe;
}

.chat-v2-tools, .chat-v2-sandbox {
  margin-top: 8px;
  padding: 10px 12px;
  background: #f9fafb;
  border: 1px solid #f3f4f6;
  border-radius: 8px;
}

.chat-v2-tools-title {
  font-size: 12px;
  color: #6b7280;
  margin-bottom: 6px;
  display: flex;
  align-items: center;
  gap: 4px;
}

.chat-v2-tool-item {
  margin-bottom: 4px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.chat-v2-tool-ms {
  font-size: 11px;
  color: #9ca3af;
}

.chat-v2-sandbox-item + .chat-v2-sandbox-item {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid #f3f4f6;
}

.chat-v2-sandbox-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.chat-v2-sandbox-code {
  background: #111827;
  color: #f9fafb;
  padding: 8px 10px;
  border-radius: 4px;
  font-size: 12px;
  overflow-x: auto;
  margin: 4px 0;
}

.chat-v2-sandbox-stdout {
  background: #fff;
  border: 1px solid #e5e7eb;
  padding: 6px 8px;
  border-radius: 4px;
  font-family: monospace;
  font-size: 12px;
  white-space: pre-wrap;
}

.chat-v2-sandbox-artifacts {
  margin-top: 6px;
}

.chat-v2-artifact {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  padding: 2px 6px;
  margin: 1px 0;
  color: #374151;
  border-radius: 4px;
  transition: background-color 0.12s ease, color 0.12s ease;
}

.chat-v2-artifact--clickable {
  cursor: pointer;
  user-select: none;
}

.chat-v2-artifact--clickable:hover {
  background-color: #eff6ff;
  color: #1d4ed8;
}

.chat-v2-artifact--clickable:focus-visible {
  outline: 2px solid #93c5fd;
  outline-offset: 1px;
}

.chat-v2-artifact.is-downloading {
  color: #6b7280;
  cursor: progress;
}

.chat-v2-artifact-spinner {
  margin-left: auto;
  animation: chat-v2-spin 1s linear infinite;
}

@keyframes chat-v2-spin {
  from { transform: rotate(0deg); }
  to   { transform: rotate(360deg); }
}

.chat-v2-reasoning {
  margin-top: 6px;
  padding: 6px 10px;
  background: #f9fafb;
  border-radius: 4px;
  font-size: 12px;
}

.chat-v2-reasoning summary {
  cursor: pointer;
  color: #6b7280;
}

.chat-v2-reasoning pre {
  font-size: 12px;
  color: #374151;
  white-space: pre-wrap;
  margin: 6px 0 0;
}

.chat-v2-thinking pre {
  font-size: 12px;
  color: #6b7280;
  white-space: pre-wrap;
  margin: 6px 0 0;
}

/* ── 时间线气泡 ── */
.chat-v2-timeline {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.chat-v2-seg-text {
  /* 复用 .chat-v2-msg-content 样式，无需重复 */
}

.chat-v2-seg-thinking {
  padding: 6px 10px;
  background: #f9fafb;
  border-left: 2px solid #e5e7eb;
  border-radius: 4px;
  font-size: 12px;
}

.chat-v2-seg-thinking summary {
  cursor: pointer;
  color: #9ca3af;
  user-select: none;
}

.chat-v2-seg-thinking pre {
  font-size: 12px;
  color: #6b7280;
  white-space: pre-wrap;
  margin: 6px 0 0;
}

.chat-v2-seg-tool {
  padding: 4px 10px;
  background: #f8fafc;
  border-left: 2px solid #cbd5e1;
  border-radius: 3px;
  font-size: 13px;
  color: #475569;
}

.chat-v2-tool-summary {
  cursor: pointer;
  user-select: none;
  display: flex;
  align-items: center;
  gap: 8px;
  list-style: none;
}

.chat-v2-tool-summary::-webkit-details-marker { display: none; }

.chat-v2-tool-icon {
  font-family: monospace;
  color: #10b981;
  font-weight: 600;
  width: 12px;
  text-align: center;
}

.chat-v2-seg-tool[open] .chat-v2-tool-icon { color: #6b7280; }

.chat-v2-tool-call {
  font-family: 'SF Mono', Menlo, monospace;
  font-size: 12px;
  color: #334155;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}

.chat-v2-seg-tool-result {
  flex-basis: 100%;
  font-size: 12px;
  color: #374151;
  white-space: pre-wrap;
  margin-top: 4px;
  padding-top: 4px;
  border-top: 1px dashed #dbeafe;
}

.chat-v2-seg-sandbox {
  padding: 8px 10px;
  background: #fefce8;
  border: 1px solid #fef08a;
  border-radius: 6px;
}

.chat-v2-input-wrap {
  padding: 12px 20px 20px;
  background: #fff;
  border-top: 1px solid #e5e7eb;
}

.chat-v2-attachments {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}

.chat-v2-input-row {
  position: relative;
  display: flex;
  align-items: flex-end;
  gap: 8px;
}

.chat-v2-input {
  flex: 1;
}
</style>
