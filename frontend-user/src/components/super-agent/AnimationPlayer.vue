<script setup lang="ts">
/**
 * AnimationPlayer — HTML5 动画沙箱播放器
 *
 * 安全模型：
 *   iframe sandbox="allow-scripts" + CSP（双重防护）
 *   全屏使用同页面 modal（不使用 window.open/Blob URL）
 *
 * 状态机：LOADING → RENDERING → PLAYING / ERROR / TRUNCATED
 */
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount, watchEffect } from 'vue'
import { injectCSP, validateHtmlSafety, detectTruncation, getSandboxAttr, getCspAttr } from '../../composables/useHtmlSandbox'

const props = defineProps<{
  /** 已提取的纯 HTML 内容（非 Markdown） */
  htmlContent: string
  /** 是否正在流式生成中 */
  isStreaming?: boolean
  /** 流式生成中的代码预览（Markdown 原文，用于代码预览区） */
  streamingCode?: string
}>()

const emit = defineEmits<{
  (e: 'retry-question'): void
}>()

// ==================== 状态 ====================
type PlayerState = 'loading' | 'rendering' | 'playing' | 'error' | 'truncated'

const state = ref<PlayerState>('loading')
const errorMessage = ref('')
const iframeRef = ref<HTMLIFrameElement | null>(null)
const iframeLoaded = ref(false)
const showFullscreen = ref(false)
const fullscreenLoaded = ref(false)
const showCode = ref(false)
const iframeKey = ref(0) // 用于强制重建 iframe
const codePreviewRef = ref<HTMLPreElement | null>(null)
const fullscreenIframeRef = ref<HTMLIFrameElement | null>(null)
const fullscreenOverlayRef = ref<HTMLDivElement | null>(null)
const iframeHeight = ref(0) // 0 = 使用 CSS aspect-ratio 16:9 默认，postMessage 上报后覆盖
const fullscreenIframeHeight = ref(0)

// ==================== 计算属性 ====================

/** 处理后的安全 HTML（注入 CSP） */
const safeHtml = computed(() => {
  if (!props.htmlContent) return ''

  // 安全校验
  const safety = validateHtmlSafety(props.htmlContent)
  if (!safety.valid) {
    state.value = 'error'
    errorMessage.value = safety.error || '不安全的 HTML 内容'
    return ''
  }

  // 截断检测
  if (detectTruncation(props.htmlContent)) {
    state.value = 'truncated'
    return ''
  }

  // 注入 CSP
  return injectCSP(props.htmlContent)
})

const sandboxAttr = computed(() => getSandboxAttr())
const cspAttr = computed(() => getCspAttr())

// ==================== 生命周期 ====================

// 统一管理渲染时机，避免 htmlContent 和 isStreaming 同时变化时双触发 iframe 重建
let lastRenderedContent = ''

watchEffect(() => {
  const streaming = props.isStreaming
  const content = props.htmlContent

  if (streaming) {
    state.value = 'loading'
    return
  }

  // 非流式态 + 有内容 + 内容有变化 → 进入渲染
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

// iframe 高度自适应：监听 postMessage（带 flatten 的脚本已注入 iframe）
function onIframeMessage(e: MessageEvent) {
  const fromInline = iframeRef.value && e.source === iframeRef.value.contentWindow
  const fromFullscreen = fullscreenIframeRef.value && e.source === fullscreenIframeRef.value.contentWindow
  if (!fromInline && !fromFullscreen) return

  if (e.data?.type === 'animation-height' && typeof e.data.height === 'number') {
    const newH = Math.max(100, Math.ceil(e.data.height) + 16)
    if (fromInline && Math.abs(newH - iframeHeight.value) >= 8) {
      iframeHeight.value = newH
    } else if (fromFullscreen && Math.abs(newH - fullscreenIframeHeight.value) >= 8) {
      fullscreenIframeHeight.value = newH
    }
  } else if (e.data?.type === 'animation-escape') {
    if (showFullscreen.value) {
      closeFullscreen()
    }
  }
}

onMounted(() => {
  window.addEventListener('message', onIframeMessage)
})

onBeforeUnmount(() => {
  window.removeEventListener('message', onIframeMessage)
  if (iframeRef.value) {
    iframeRef.value.srcdoc = ''
  }
})

// ==================== 事件处理 ====================

function onIframeLoad() {
  iframeLoaded.value = true
  // 100ms 延迟淡入，避免白屏闪烁
  setTimeout(() => {
    state.value = 'playing'
  }, 100)
}

function onIframeError() {
  state.value = 'error'
  errorMessage.value = '动画渲染失败'
}

function handleReplay() {
  state.value = 'rendering'
  iframeLoaded.value = false
  iframeKey.value++
}

function handleFullscreen() {
  showFullscreen.value = true
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
  a.download = `animation-${Date.now()}.html`
  a.click()
  URL.revokeObjectURL(url)
}

function handleRetry() {
  emit('retry-question')
}

function toggleCode() {
  showCode.value = !showCode.value
}

// ESC 关闭全屏
function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && showFullscreen.value) {
    closeFullscreen()
  }
}

// 全屏时监听 ESC + focus overlay
watch(showFullscreen, async (val) => {
  if (val) {
    document.addEventListener('keydown', onKeydown)
    await nextTick()
    fullscreenOverlayRef.value?.focus()
  } else {
    document.removeEventListener('keydown', onKeydown)
  }
})
</script>

<template>
  <div class="animation-player">
    <!-- 加载态：流式生成中 -->
    <div v-if="state === 'loading'" class="player-loading">
      <!-- 代码预览区 -->
      <div v-if="streamingCode" class="code-preview">
        <div class="code-preview-header">
          <span class="code-preview-dot red"></span>
          <span class="code-preview-dot yellow"></span>
          <span class="code-preview-dot green"></span>
          <span class="code-preview-title">HTML 动画代码生成中...</span>
        </div>
        <pre ref="codePreviewRef" class="code-preview-content"><code>{{ streamingCode }}</code></pre>
      </div>
      <div v-if="!streamingCode" class="loading-indicator">
        <span class="loading-spinner"></span>
        <span class="loading-text">正在创作动画...</span>
      </div>
    </div>

    <!-- 渲染态 + 播放态 -->
    <div
      v-else-if="state === 'rendering' || state === 'playing'"
      class="player-viewport"
    >
      <div class="iframe-wrapper" :class="{ loaded: iframeLoaded }" :style="iframeHeight > 0 ? { height: iframeHeight + 'px' } : undefined">
        <iframe
          ref="iframeRef"
          :key="iframeKey"
          :srcdoc="safeHtml"
          :sandbox="sandboxAttr"
          :csp="cspAttr"
          referrerpolicy="no-referrer"
          class="animation-iframe"
          @load="onIframeLoad"
          @error="onIframeError"
        ></iframe>
      </div>

      <!-- 悬浮工具栏 -->
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
      <div class="error-title">动画生成被意外中断，内容不完整</div>
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
      <div class="error-title">动画无法播放，代码可能存在问题</div>
      <div v-if="errorMessage" class="error-detail">{{ errorMessage }}</div>
      <div class="error-actions">
        <button class="action-btn primary" @click="handleRetry">重新生成</button>
        <button class="action-btn secondary" @click="toggleCode">查看代码</button>
      </div>
    </div>

    <!-- 源代码折叠区：仅用户手动触发或错误/截断态可见 -->
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
        <div class="fullscreen-container">
          <button class="fullscreen-close" @click="closeFullscreen" title="关闭 (ESC)">&times;</button>
          <div class="fullscreen-scroll">
            <iframe
              ref="fullscreenIframeRef"
              :srcdoc="safeHtml"
              :sandbox="sandboxAttr"
              :csp="cspAttr"
              referrerpolicy="no-referrer"
              class="fullscreen-iframe"
              :class="{ loaded: fullscreenLoaded }"
              :style="(fullscreenIframeHeight || iframeHeight) ? { height: (fullscreenIframeHeight || iframeHeight) + 'px' } : undefined"
              @load="onFullscreenIframeLoad"
            ></iframe>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.animation-player {
  background: #fafafa;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  overflow: hidden;
  margin-top: 8px;
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
  scrollbar-width: none; /* Firefox */
}

.code-preview-content::-webkit-scrollbar {
  display: none; /* Chrome/Safari */
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
  aspect-ratio: 16 / 9; /* 初始 16:9，postMessage 上报后 style.height 覆盖 */
  overflow: hidden;
  background: #f0f0f0;
  transition: height 0.2s ease;
}

.animation-iframe {
  width: 100%;
  height: 100%;
  border: none;
  opacity: 0;
  transition: opacity 0.4s ease;
}

.iframe-wrapper.loaded .animation-iframe {
  opacity: 1;
}

/* ==================== 悬浮工具栏 ==================== */
.player-toolbar {
  position: absolute;
  bottom: 12px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  gap: 8px;
  padding: 6px 12px;
  background: rgba(0, 0, 0, 0.65);
  backdrop-filter: blur(8px);
  border-radius: 20px;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.2s ease;
}

.player-viewport:hover .player-toolbar {
  opacity: 1;
  pointer-events: auto;
}

.toolbar-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border: none;
  background: transparent;
  color: #fff;
  cursor: pointer;
  border-radius: 12px;
  font-size: 13px;
  transition: background 0.15s;
}

.toolbar-btn:hover {
  background: rgba(255, 255, 255, 0.15);
}

.btn-icon {
  font-size: 16px;
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
}

.fullscreen-container {
  position: relative;
  width: 92vw;
  height: 90vh;
  max-width: 1600px;
  background: #000;
  border-radius: 12px;
  overflow: hidden;
}

.fullscreen-scroll {
  width: 100%;
  height: 100%;
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
}

.fullscreen-close {
  position: absolute;
  top: 12px;
  right: 16px;
  z-index: 10;
  width: 36px;
  height: 36px;
  border: none;
  background: rgba(0, 0, 0, 0.5);
  color: #fff;
  font-size: 24px;
  cursor: pointer;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.15s;
}

.fullscreen-close:hover {
  background: rgba(0, 0, 0, 0.7);
}

.fullscreen-iframe {
  width: 100%;
  min-height: 100%;
  border: none;
  opacity: 0;
  transition: opacity 0.3s ease;
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
    padding: 4px 8px;
    gap: 4px;
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
}
</style>
