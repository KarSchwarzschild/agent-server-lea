<script setup lang="ts">
/**
 * InteractivePlayer — HTML 交互式学习内容沙箱播放器
 *
 * 安全模型：与 AnimationPlayer 一致
 *   iframe sandbox="allow-scripts" + CSP（双重防护）
 *   全屏使用同页面 modal
 *
 * 与 AnimationPlayer 的关键差异：
 *   - 高度自适应内容（非固定 16:9）
 *   - 工具栏始终可见（非悬浮隐藏）
 *   - 进度条展示学习状态
 *   - 加载态显示代码预览（与 AnimationPlayer 一致）
 *   - 移动端分级渲染（非完全隐藏）
 *
 * 状态机：LOADING → RENDERING → PLAYING / ERROR / TRUNCATED
 */
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount, watchEffect, inject } from 'vue'
import { injectInteractiveCSP, validateHtmlSafety, detectTruncation, getInteractiveSandboxAttr, getInteractiveCspAttr } from '../../composables/useHtmlSandbox'
import { useResponsive } from '../../composables/useResponsive'
import { INTERACTIVE_PLAYER_REGISTRY_KEY } from '../../composables/useInteractivePlayerRegistry'

const props = defineProps<{
  /** 消息 ID，用于注册表标识 */
  msgId?: string
  /** 已提取的纯 HTML 内容（非 Markdown） */
  htmlContent: string
  /** 是否正在流式生成中 */
  isStreaming?: boolean
  /** 流式生成中的代码预览文本 */
  streamingCode?: string
  /** 学习进度 (0-100) */
  progress?: number
  /** 当前知识点名称 */
  currentTopic?: string
  /** 当前/总数文本，如 "2/5" */
  progressText?: string
  /** 当前知识点序号（从 1 开始） */
  currentIndex?: number
  /** 知识点总数 */
  total?: number
  /** 各知识点掌握情况 {"1":"good","2":"pending"} */
  mastery?: Record<string, string>
}>()

const emit = defineEmits<{
  (e: 'retry-question'): void
  (e: 'navigate-to', index: number): void
}>()

const { isMobile } = useResponsive()

// ==================== 注册表（全屏标签切换） ====================
const registry = inject(INTERACTIVE_PLAYER_REGISTRY_KEY, null)

// ==================== 状态 ====================
type PlayerState = 'loading' | 'rendering' | 'playing' | 'error' | 'truncated'

const state = ref<PlayerState>('loading')
const errorMessage = ref('')
const iframeRef = ref<HTMLIFrameElement | null>(null)
const fullscreenIframeRef = ref<HTMLIFrameElement | null>(null)
const iframeLoaded = ref(false)
const showFullscreen = ref(false)
const fullscreenLoaded = ref(false)
const showCode = ref(false)
const iframeKey = ref(0)
const codePreviewRef = ref<HTMLPreElement | null>(null)
const fullscreenOverlayRef = ref<HTMLDivElement | null>(null)
const iframeHeight = ref(200) // 初始值偏小，等 postMessage 上报实际内容高度
const fullscreenIframeHeight = ref(0) // 全屏 iframe 独立高度（宽度不同，内容回流后高度不同）

// ==================== 计算属性 ====================

/** 是否使用节点式进度条 */
const useNodeProgress = computed(() => (props.total ?? 0) >= 2)

/** 节点列表（用于节点式进度条渲染） */
const progressNodes = computed(() => {
  const total = props.total ?? 0
  const current = props.currentIndex ?? 0
  const mastery = props.mastery ?? {}
  const nodes = []
  for (let i = 1; i <= total; i++) {
    const status = i < current ? (mastery[String(i)] || 'done')
      : i === current ? 'current'
      : 'pending'
    nodes.push({ index: i, status })
  }
  return nodes
})

/** 处理后的安全 HTML（注入 Interactive CSP） */
const safeHtml = computed(() => {
  if (!props.htmlContent) return ''

  // 流式生成中不做截断检测 — HTML 天然不完整，检测必然误报
  if (props.isStreaming) return ''

  const safety = validateHtmlSafety(props.htmlContent)
  if (!safety.valid) {
    state.value = 'error'
    errorMessage.value = safety.error || '不安全的 HTML 内容'
    return ''
  }

  if (detectTruncation(props.htmlContent)) {
    state.value = 'truncated'
    return ''
  }

  return injectInteractiveCSP(props.htmlContent)
})

const sandboxAttr = computed(() => getInteractiveSandboxAttr())
const cspAttr = computed(() => getInteractiveCspAttr())

// ==================== 生命周期 ====================

let lastRenderedContent = ''

watchEffect(() => {
  const streaming = props.isStreaming
  const content = props.htmlContent

  if (streaming) {
    state.value = 'loading'
    return
  }

  if (content && content !== lastRenderedContent) {
    lastRenderedContent = content
    state.value = 'rendering'
    iframeLoaded.value = false
    iframeKey.value++
  }
})

// 代码预览区自动滚动到底部
watch(() => props.streamingCode, () => {
  nextTick(() => {
    if (codePreviewRef.value) {
      codePreviewRef.value.scrollTop = codePreviewRef.value.scrollHeight
    }
  })
})

// iframe 高度自适应：监听 postMessage
// 关键：通过 e.source 判断消息来源，只处理自己的 iframe（防止多实例互相干扰）
function onIframeMessage(e: MessageEvent) {
  // 来源校验：必须是自己的 inline iframe 或 fullscreen iframe
  const fromInline = iframeRef.value && e.source === iframeRef.value.contentWindow
  const fromFullscreen = fullscreenIframeRef.value && e.source === fullscreenIframeRef.value.contentWindow
  if (!fromInline && !fromFullscreen) return

  if (e.data?.type === 'interactive-height' && typeof e.data.height === 'number') {
    const newH = Math.max(100, Math.ceil(e.data.height) + 16)
    // 高度变化 < 8px 忽略，避免动画引起的像素级抖动
    if (fromInline) {
      if (Math.abs(newH - iframeHeight.value) >= 8) {
        iframeHeight.value = newH
      }
    } else if (fromFullscreen) {
      if (Math.abs(newH - fullscreenIframeHeight.value) >= 8) {
        fullscreenIframeHeight.value = newH
      }
    }
  } else if (e.data?.type === 'interactive-escape') {
    // iframe 内按 ESC → 关闭全屏
    if (showFullscreen.value) {
      closeFullscreen()
    }
  }
}

onMounted(() => {
  window.addEventListener('message', onIframeMessage)
})

// 注册表：mount 时注册，内容变化时更新
watchEffect(() => {
  if (registry && props.msgId && safeHtml.value) {
    registry.register({
      msgId: props.msgId,
      safeHtml: safeHtml.value,
      topic: props.currentTopic || `知识点 ${props.currentIndex || ''}`.trim(),
      knowledgeIndex: props.currentIndex || 0,
    })
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('message', onIframeMessage)
  if (registry && props.msgId) registry.unregister(props.msgId)
  if (iframeRef.value) {
    iframeRef.value.srcdoc = ''
  }
})

// ==================== 事件处理 ====================

function handleNodeClick(index: number) {
  emit('navigate-to', index)
}

function onIframeLoad() {
  iframeLoaded.value = true
  setTimeout(() => {
    state.value = 'playing'
  }, 100)
}

function onIframeError() {
  state.value = 'error'
  errorMessage.value = '交互内容渲染失败'
}

function handleReplay() {
  state.value = 'rendering'
  iframeLoaded.value = false
  iframeKey.value++
}

// ==================== 全屏标签切换 ====================
const fullscreenActiveMsgId = ref('')

const fullscreenDisplayHtml = computed(() => {
  if (!registry || !fullscreenActiveMsgId.value) return safeHtml.value
  const target = registry.allPlayers.value.find(p => p.msgId === fullscreenActiveMsgId.value)
  return target?.safeHtml || safeHtml.value
})

function handleFullscreen() {
  fullscreenActiveMsgId.value = props.msgId || ''
  showFullscreen.value = true
}

function switchTab(msgId: string) {
  if (msgId === fullscreenActiveMsgId.value) return
  fullscreenActiveMsgId.value = msgId
  fullscreenLoaded.value = false
  fullscreenIframeHeight.value = 0
}

function closeFullscreen() {
  showFullscreen.value = false
  fullscreenLoaded.value = false
  fullscreenIframeHeight.value = 0
}

function onFullscreenIframeLoad() {
  fullscreenLoaded.value = true
}

function handleDownload() {
  if (!props.htmlContent) return
  const blob = new Blob([props.htmlContent], { type: 'text/html;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `interactive-${Date.now()}.html`
  a.click()
  URL.revokeObjectURL(url)
}

function handleRetry() {
  emit('retry-question')
}

function toggleCode() {
  showCode.value = !showCode.value
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && showFullscreen.value) {
    closeFullscreen()
  }
}

watch(showFullscreen, async (val) => {
  if (val) {
    document.addEventListener('keydown', onKeydown)
    // 自动 focus overlay，让 ESC 在非 iframe 区域也能工作
    await nextTick()
    fullscreenOverlayRef.value?.focus()
  } else {
    document.removeEventListener('keydown', onKeydown)
  }
})
</script>

<template>
  <div class="interactive-player">
    <!-- 进度条 -->
    <div v-if="progress !== undefined || progressText" class="player-progress" role="progressbar" :aria-valuenow="progress" aria-valuemin="0" aria-valuemax="100">
      <!-- 节点式进度条 -->
      <template v-if="useNodeProgress">
        <div class="progress-header">
          <span class="progress-label">学习进度</span>
          <span v-if="currentTopic" class="progress-topic">{{ currentTopic }}</span>
        </div>
        <div class="progress-nodes">
          <template v-for="(node, idx) in progressNodes" :key="node.index">
            <div
              class="progress-node clickable"
              :class="[`node-${node.status}`]"
              :title="`跳转到知识点 ${node.index}`"
              @click="handleNodeClick(node.index)"
            >
              <span v-if="node.status === 'current'" class="node-dot current-dot"></span>
              <span v-else-if="node.status === 'good'" class="node-dot good-dot">&#10003;</span>
              <span v-else-if="node.status === 'done'" class="node-dot done-dot">&#10003;</span>
              <span v-else class="node-dot pending-dot"></span>
              <span class="node-label">{{ node.index }}</span>
            </div>
            <div v-if="idx < progressNodes.length - 1" class="node-connector" :class="{ filled: node.status !== 'pending' }"></div>
          </template>
        </div>
      </template>
      <!-- 降级：线性进度条（total < 2 时） -->
      <template v-else>
        <div class="progress-info">
          <span class="progress-label">学习进度</span>
          <span v-if="progressText" class="progress-text">{{ progressText }}</span>
          <span v-if="currentTopic" class="progress-topic">{{ currentTopic }}</span>
        </div>
        <div class="progress-bar-track">
          <div class="progress-bar-fill" :style="{ width: (progress || 0) + '%' }"></div>
        </div>
      </template>
    </div>

    <!-- 加载态：流式生成中 -->
    <div v-if="state === 'loading'" class="player-loading">
      <!-- 代码预览区 -->
      <div v-if="streamingCode" class="code-preview">
        <div class="code-preview-header">
          <span class="code-preview-dot red"></span>
          <span class="code-preview-dot yellow"></span>
          <span class="code-preview-dot green"></span>
          <span class="code-preview-title">HTML 交互内容生成中...</span>
        </div>
        <pre ref="codePreviewRef" class="code-preview-content"><code>{{ streamingCode }}</code></pre>
      </div>
      <div v-if="!streamingCode" class="loading-indicator">
        <span class="loading-spinner"></span>
        <span class="loading-text">
          {{ currentTopic ? `正在准备「${currentTopic}」的学习内容...` : '正在创作交互式内容...' }}
        </span>
      </div>
    </div>

    <!-- 渲染态 + 播放态 -->
    <div
      v-else-if="state === 'rendering' || state === 'playing'"
      class="player-viewport"
    >
      <div class="iframe-wrapper" :class="{ loaded: iframeLoaded }" :style="{ height: iframeHeight + 'px' }">
        <iframe
          ref="iframeRef"
          :key="iframeKey"
          :srcdoc="safeHtml"
          :sandbox="sandboxAttr"
          :csp="cspAttr"
          referrerpolicy="no-referrer"
          class="interactive-iframe"
          @load="onIframeLoad"
          @error="onIframeError"
        ></iframe>
      </div>

      <!-- 工具栏（始终可见） -->
      <div v-if="state === 'playing'" class="player-toolbar">
        <button class="toolbar-btn" title="重播" @click="handleReplay">
          <span class="btn-icon">&#8635;</span>
          <span class="btn-label">重播</span>
        </button>
        <button class="toolbar-btn" title="全屏" @click="handleFullscreen">
          <span class="btn-icon">&#9974;</span>
          <span class="btn-label">全屏</span>
        </button>
        <button class="toolbar-btn" title="下载" @click="handleDownload">
          <span class="btn-icon">&#8615;</span>
          <span class="btn-label">下载</span>
        </button>
        <button class="toolbar-btn" title="源代码" @click="toggleCode">
          <span class="btn-icon">&#60;/&#62;</span>
          <span class="btn-label">源码</span>
        </button>
      </div>
    </div>

    <!-- 截断态 -->
    <div v-else-if="state === 'truncated'" class="player-error truncated">
      <div class="error-icon">&#9888;</div>
      <div class="error-title">交互内容生成被意外中断</div>
      <div class="error-actions">
        <button class="action-btn primary" @click="handleRetry">重新生成</button>
        <button class="action-btn secondary" @click="toggleCode">
          {{ showCode ? '隐藏代码' : '查看已生成的代码' }}
        </button>
      </div>
    </div>

    <!-- 错误态 -->
    <div v-else-if="state === 'error'" class="player-error">
      <div class="error-icon">&#10060;</div>
      <div class="error-title">交互内容无法渲染</div>
      <div v-if="errorMessage" class="error-detail">{{ errorMessage }}</div>
      <div class="error-actions">
        <button class="action-btn primary" @click="handleRetry">重新生成</button>
        <button class="action-btn secondary" @click="toggleCode">查看代码</button>
      </div>
    </div>

    <!-- 源代码折叠区 -->
    <div v-if="showCode || state === 'error' || state === 'truncated'" class="code-section">
      <button class="code-toggle" @click="toggleCode">
        {{ showCode ? '&#9660; 隐藏源代码' : '&#9654; 查看源代码' }}
      </button>
      <div v-if="showCode" class="code-block">
        <pre><code>{{ htmlContent }}</code></pre>
      </div>
    </div>

    <!-- 全屏 Modal -->
    <Teleport to="body">
      <div v-if="showFullscreen" class="fullscreen-overlay" tabindex="-1" ref="fullscreenOverlayRef" @click.self="closeFullscreen" @keydown.esc="closeFullscreen">
        <div class="fullscreen-container" :class="{ 'has-tabs': registry && registry.allPlayers.value.length > 1 }">
          <!-- 顶部栏：标签 + 关闭按钮 -->
          <div class="fullscreen-header">
            <div v-if="registry && registry.allPlayers.value.length > 1" class="fullscreen-tabs">
              <button
                v-for="p in registry.allPlayers.value"
                :key="p.msgId"
                class="fullscreen-tab"
                :class="{ active: p.msgId === fullscreenActiveMsgId }"
                @click="switchTab(p.msgId)"
              >
                <span class="tab-index">{{ p.knowledgeIndex }}</span>
                <span class="tab-topic">{{ p.topic }}</span>
              </button>
            </div>
            <div v-else class="fullscreen-header-spacer"></div>
            <button class="fullscreen-close" @click="closeFullscreen" title="关闭 (ESC)">&times;</button>
          </div>
          <div class="fullscreen-scroll">
            <div class="fullscreen-scroll-inner">
              <iframe
                ref="fullscreenIframeRef"
                :srcdoc="fullscreenDisplayHtml"
                :sandbox="sandboxAttr"
                :csp="cspAttr"
                referrerpolicy="no-referrer"
                class="fullscreen-iframe"
                :class="{ loaded: fullscreenLoaded }"
                :style="fullscreenIframeHeight ? { height: fullscreenIframeHeight + 'px' } : undefined"
                @load="onFullscreenIframeLoad"
              ></iframe>
            </div>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.interactive-player {
  background: #fafafa;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  overflow: hidden;
  margin-top: 8px;
}

/* ==================== 进度条 ==================== */
.player-progress {
  padding: 10px 14px 8px;
  border-bottom: 1px solid #f0f0f0;
}

.progress-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
  font-size: 13px;
}

.progress-info {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
  font-size: 13px;
}

.progress-label {
  color: #6b7280;
}

.progress-text {
  color: #3b82f6;
  font-weight: 600;
}

.progress-topic {
  color: #374151;
  font-weight: 500;
}

/* 线性进度条（降级） */
.progress-bar-track {
  height: 4px;
  background: #e5e7eb;
  border-radius: 2px;
  overflow: hidden;
}

.progress-bar-fill {
  height: 100%;
  background: #3b82f6;
  border-radius: 2px;
  transition: width 0.3s ease;
}

/* 节点式进度条 */
.progress-nodes {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0;
  padding: 2px 0 4px;
}

.progress-node {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  position: relative;
  z-index: 1;
}

.node-dot {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
  transition: all 0.2s ease;
}

.pending-dot {
  background: #f3f4f6;
  border: 2px solid #d1d5db;
  color: transparent;
}

.current-dot {
  background: #3b82f6;
  border: 2px solid #3b82f6;
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.2);
  animation: pulse-dot 1.5s ease-in-out infinite;
}

.done-dot {
  background: #d1d5db;
  border: 2px solid #d1d5db;
  color: #fff;
  font-size: 11px;
}

.good-dot {
  background: #16a34a;
  border: 2px solid #16a34a;
  color: #fff;
  font-size: 11px;
}

.progress-node.clickable {
  cursor: pointer;
}

.progress-node.clickable:hover .node-dot {
  transform: scale(1.15);
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.15);
}

.node-label {
  font-size: 11px;
  color: #9ca3af;
  font-weight: 500;
}

.node-current .node-label {
  color: #3b82f6;
  font-weight: 600;
}

.node-good .node-label {
  color: #16a34a;
}

.node-connector {
  width: 24px;
  height: 2px;
  background: #e5e7eb;
  margin-bottom: 18px;
  transition: background 0.2s ease;
}

.node-connector.filled {
  background: #3b82f6;
}

@keyframes pulse-dot {
  0%, 100% { box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.2); }
  50% { box-shadow: 0 0 0 5px rgba(59, 130, 246, 0.1); }
}

/* ==================== 加载态 ==================== */
.player-loading {
  padding: 20px;
}

.code-preview {
  max-height: 240px;
  overflow: hidden;
  border-radius: 8px;
  background: #1a1b26;
  margin-bottom: 16px;
  position: relative;
  animation: fadeIn 0.4s ease;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.12);
}

.code-preview-header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 14px;
  background: #13141c;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}

.code-preview-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
}

.code-preview-dot.red { background: #ff5f57; }
.code-preview-dot.yellow { background: #febc2e; }
.code-preview-dot.green { background: #28c840; }

.code-preview-title {
  margin-left: 8px;
  font-size: 12px;
  color: #6b7280;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}

.code-preview-content {
  margin: 0;
  padding: 14px 16px;
  font-size: 12.5px;
  line-height: 1.6;
  color: #a9b1d6;
  font-family: 'Menlo', 'Monaco', 'Courier New', monospace;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 200px;
  overflow-y: scroll;
  overscroll-behavior: contain;
  scrollbar-width: none;
}

.code-preview-content::-webkit-scrollbar {
  display: none;
}

.loading-indicator {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 12px 0 4px;
}

.loading-spinner {
  width: 18px;
  height: 18px;
  border: 2px solid #e5e7eb;
  border-top-color: #3b82f6;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

.loading-text {
  font-size: 14px;
  color: #6b7280;
}

/* ==================== 渲染态 + 播放态 ==================== */
.player-viewport {
  position: relative;
}

.iframe-wrapper {
  width: 100%;
  min-height: 100px;
  overflow: hidden;
  background: #fff;
  transition: height 0.2s ease;
}

.interactive-iframe {
  width: 100%;
  height: 100%;
  border: none;
  opacity: 0;
  transition: opacity 0.4s ease;
}

.iframe-wrapper.loaded .interactive-iframe {
  opacity: 1;
}

/* ==================== 工具栏（始终可见） ==================== */
.player-toolbar {
  display: flex;
  justify-content: center;
  gap: 8px;
  padding: 8px 12px;
  background: #f9fafb;
  border-top: 1px solid #e5e7eb;
}

.toolbar-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border: 1px solid #e5e7eb;
  background: #fff;
  color: #4b5563;
  cursor: pointer;
  border-radius: 6px;
  font-size: 13px;
  transition: all 0.15s;
}

.toolbar-btn:hover {
  background: #f3f4f6;
  border-color: #d1d5db;
}

.btn-icon {
  font-size: 15px;
}

.btn-label {
  font-size: 12px;
}

/* ==================== 错误态 ==================== */
.player-error {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 32px 16px;
  text-align: center;
}

.player-error.truncated .error-icon {
  color: #f59e0b;
}

.error-icon {
  font-size: 32px;
  margin-bottom: 8px;
  color: #ef4444;
}

.error-title {
  font-size: 15px;
  color: #374151;
  margin-bottom: 6px;
  font-weight: 500;
}

.error-detail {
  font-size: 13px;
  color: #9ca3af;
  margin-bottom: 16px;
}

.error-actions {
  display: flex;
  gap: 10px;
  margin-top: 12px;
}

.action-btn {
  padding: 6px 16px;
  border-radius: 6px;
  font-size: 13px;
  cursor: pointer;
  border: 1px solid #d1d5db;
  transition: all 0.15s;
}

.action-btn.primary {
  background: #3b82f6;
  color: #fff;
  border-color: #3b82f6;
}

.action-btn.primary:hover {
  background: #2563eb;
}

.action-btn.secondary {
  background: #fff;
  color: #374151;
}

.action-btn.secondary:hover {
  background: #f3f4f6;
}

/* ==================== 源代码折叠区 ==================== */
.code-section {
  border-top: 1px solid #e5e7eb;
  padding: 0 16px;
}

.code-toggle {
  display: block;
  width: 100%;
  padding: 10px 0;
  border: none;
  background: transparent;
  color: #6b7280;
  font-size: 13px;
  cursor: pointer;
  text-align: left;
  transition: color 0.15s;
}

.code-toggle:hover {
  color: #374151;
}

.code-block {
  max-height: 300px;
  overflow: auto;
  background: #1e1e1e;
  border-radius: 6px;
  margin-bottom: 12px;
}

.code-block pre {
  margin: 0;
  padding: 12px;
}

.code-block code {
  font-size: 12px;
  line-height: 1.5;
  color: #d4d4d4;
  font-family: 'Menlo', 'Monaco', 'Courier New', monospace;
  white-space: pre-wrap;
  word-break: break-all;
}

/* ==================== 全屏 Modal ==================== */
.fullscreen-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.75);
  z-index: 9999;
  display: flex;
  align-items: center;
  justify-content: center;
  animation: fadeIn 0.2s ease;
  outline: none;
}

.fullscreen-container {
  display: flex;
  flex-direction: column;
  width: 92vw;
  max-width: 1600px;
  max-height: 90vh;
  min-height: 200px;
  background: #fff;
  border-radius: 12px;
  overflow: hidden;
}

/* 顶部栏 */
.fullscreen-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  background: #f8f9fa;
  border-bottom: 1px solid #e5e7eb;
  flex-shrink: 0;
}

.fullscreen-header-spacer {
  flex: 1;
}

/* 标签栏 */
.fullscreen-tabs {
  display: flex;
  gap: 4px;
  overflow-x: auto;
  flex: 1;
  min-width: 0;
  scrollbar-width: none;
}

.fullscreen-tabs::-webkit-scrollbar {
  display: none;
}

.fullscreen-tab {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  border: none;
  background: transparent;
  color: #6b7280;
  border-radius: 6px;
  font-size: 13px;
  white-space: nowrap;
  cursor: pointer;
  transition: all 0.15s;
  flex-shrink: 0;
}

.fullscreen-tab:hover {
  background: #e5e7eb;
  color: #374151;
}

.fullscreen-tab.active {
  background: #fff;
  color: #1d4ed8;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
}

.tab-index {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: #e5e7eb;
  color: #6b7280;
  font-size: 11px;
  font-weight: 600;
  flex-shrink: 0;
}

.fullscreen-tab.active .tab-index {
  background: #3b82f6;
  color: #fff;
}

.tab-topic {
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.fullscreen-scroll {
  width: 100%;
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
  background: #f0f1f3;
}

.fullscreen-scroll-inner {
  padding: 16px;
}

.fullscreen-close {
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  border: none;
  background: transparent;
  color: #9ca3af;
  font-size: 22px;
  cursor: pointer;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;
}

.fullscreen-close:hover {
  background: #e5e7eb;
  color: #374151;
}

.fullscreen-iframe {
  width: 100%;
  min-height: 200px;
  border: none;
  opacity: 0;
  transition: opacity 0.3s ease, height 0.2s ease;
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.06);
}

.fullscreen-iframe.loaded {
  opacity: 1;
}

/* ==================== 动画 ==================== */
@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* ==================== 移动端适配 ==================== */
@media (max-width: 768px) {
  .player-toolbar {
    gap: 4px;
    padding: 6px 8px;
  }

  .btn-label {
    display: none;
  }

  .toolbar-btn {
    padding: 6px;
  }

  .btn-icon {
    font-size: 18px;
  }

}

@media (max-width: 480px) {
  .fullscreen-container {
    width: 100vw;
    height: 100vh;
    border-radius: 0;
  }

  .fullscreen-tab {
    padding: 5px 10px;
    font-size: 12px;
  }

  .tab-topic {
    max-width: 80px;
  }
}
</style>
