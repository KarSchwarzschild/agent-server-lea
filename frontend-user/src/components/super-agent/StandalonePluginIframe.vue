<script setup lang="ts">
/**
 * Standalone 插件 iframe 持久渲染组件（v3.0 — Phase 7 suspend-resume）
 *
 * 核心行为：
 * 1. 持久渲染：iframe 作为 ToolCall 的一部分常驻消息中
 * 2. 安全：settings 传空对象（API Key 不到达浏览器）
 * 3. postMessage 精确 origin 发送 + source/origin 双重校验
 * 4. suspend-resume（Phase 7）：
 *    - 有 pendingToolUseId 时，fillContent 自动回传 submitToolResult → LLM 恢复
 *    - 30s 后显示"不等了，继续对话"跳过按钮
 *    - 60s 后端超时自动降级
 * 5. 历史记录中无 pendingToolUseId → 不触发回传，不影响已完成会话
 */
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import type { ToolCall } from '../../types/super-agent'
import { submitToolResult } from '../../api/superagent'

const props = defineProps<{ toolCall: ToolCall }>()

const iframeRef = ref<HTMLIFrameElement | null>(null)
const status = ref<'loading' | 'running' | 'done'>('loading')
const showSlowHint = ref(false)
const resultSubmitted = ref(false)
const showSkipBtn = ref(false)
let slowTimer: ReturnType<typeof setTimeout> | null = null
let skipTimer: ReturnType<typeof setTimeout> | null = null
let fallbackTimer: ReturnType<typeof setTimeout> | null = null

const sp = computed(() => props.toolCall.standalonePlugin!)
const isFullscreen = ref(false)
const fullscreenScale = ref(1)
const frameWrapRef = ref<HTMLElement>()

function toggleFullscreen() {
  isFullscreen.value = !isFullscreen.value
  if (isFullscreen.value) {
    document.body.style.overflow = 'hidden'
    nextTick(() => setTimeout(computeScale, 50))
  } else {
    document.body.style.overflow = ''
    fullscreenScale.value = 1
  }
}

function computeScale() {
  const wrap = frameWrapRef.value
  if (!wrap) return
  const containerH = wrap.clientHeight
  const contentH = sp.value.iframeHeight || 350
  if (containerH > contentH) {
    fullscreenScale.value = containerH / contentH
  }
}

function handleEscKey(e: KeyboardEvent) {
  if (e.key === 'Escape' && isFullscreen.value) {
    toggleFullscreen()
  }
}
const pluginOrigin = computed(() => {
  try { return new URL(sp.value.iframeUrl).origin }
  catch { return '' }
})

// LobeChat SDK PluginChannel 常量
const PluginChannel = {
  pluginReadyForRender:        'lobe-chat:plugin-ready-for-render',
  initStandalonePlugin:        'lobe-chat:init-standalone-plugin',
  fillStandalonePluginContent: 'lobe-chat:fill-plugin-content',
  fetchPluginSettings:         'lobe-chat:fetch-plugin-settings',
  renderPluginSettings:        'lobe-chat:render-plugin-settings',
  fetchPluginMessage:          'lobe-chat:fetch-plugin-message',
  renderPlugin:                'lobe-chat:render-plugin',
} as const

/** 从 toolName 中提取 apiName（去除 identifier__ 前缀，仅作 fallback） */
function parsedApiName(): string {
  const parts = props.toolCall.tool.split('__')
  return parts.length > 1 ? parts.slice(1).join('__') : parts[0]
}

/** 获取 postMessage 需要的原始 API 名（优先使用 manifest 原始值） */
function apiName(): string {
  return sp.value.originalApiName || parsedApiName()
}

/** 获取 postMessage 需要的原始 identifier（优先使用 manifest 原始值） */
function pluginIdentifier(): string {
  return sp.value.originalIdentifier || sp.value.pluginIdentifier
}

/** 自动回传工具入参作为兜底结果（纯渲染型插件不发 fillContent 时触发） */
function fallbackSubmit() {
  if (!props.toolCall.pendingToolUseId || resultSubmitted.value) return
  resultSubmitted.value = true
  showSkipBtn.value = false
  if (skipTimer) { clearTimeout(skipTimer); skipTimer = null }
  const content = JSON.stringify(props.toolCall.input ?? {})
  submitToolResult({
    tool_use_id: props.toolCall.pendingToolUseId,
    content: content.length > 2000 ? content.slice(0, 2000) + '...[truncated]' : content,
  }).catch(err => console.error('[StandalonePlugin] fallback 回传失败:', err))
}

/** 向 iframe 发送初始化数据 */
function sendInitData() {
  const iframe = iframeRef.value
  if (!iframe?.contentWindow || !pluginOrigin.value) return

  iframe.contentWindow.postMessage({
    type: PluginChannel.initStandalonePlugin,
    payload: {
      apiName: apiName(),
      arguments: JSON.stringify(props.toolCall.input ?? {}),
      identifier: pluginIdentifier(),
    },
    settings: {},  // 空对象 — API Key 不传给 iframe
    state: {},
  }, pluginOrigin.value)  // 精确 origin
  status.value = 'running'
  showSlowHint.value = false
  if (slowTimer) { clearTimeout(slowTimer); slowTimer = null }

  // Fallback: 纯渲染型插件不会发 fillStandalonePluginContent，
  // 8s 后若仍未回传，自动用工具入参作为兜底结果
  if (props.toolCall.pendingToolUseId && !resultSubmitted.value) {
    if (fallbackTimer) clearTimeout(fallbackTimer)
    fallbackTimer = setTimeout(() => {
      if (!resultSubmitted.value) fallbackSubmit()
    }, 8000)
  }
}

/** postMessage 监听 — 双重安全校验 */
function handleMessage(event: MessageEvent) {
  // 安全校验 1: source 必须是当前 iframe
  if (event.source !== iframeRef.value?.contentWindow) return
  // 安全校验 2: origin 必须匹配
  if (event.origin !== pluginOrigin.value) return

  const { data } = event
  if (!data || typeof data !== 'object') return

  switch (data.type) {
    // 插件就绪 → 发送初始化数据
    case PluginChannel.pluginReadyForRender:
      sendInitData()
      break

    // 插件返回内容 → 自动回传后端（有 pendingToolUseId 时走 suspend-resume）
    case PluginChannel.fillStandalonePluginContent:
      status.value = 'done'
      // 清除所有计时器
      if (skipTimer) { clearTimeout(skipTimer); skipTimer = null }
      if (fallbackTimer) { clearTimeout(fallbackTimer); fallbackTimer = null }
      showSkipBtn.value = false
      // 自动回传结果给后端（仅流式会话中有 pendingToolUseId）
      if (props.toolCall.pendingToolUseId && !resultSubmitted.value) {
        resultSubmitted.value = true
        const payload = data.content || data.payload || ''
        const raw = typeof payload === 'string' ? payload : JSON.stringify(payload)
        const MAX = 2000
        let content: string
        if (raw.length > MAX) {
          // 安全截断：尝试保持 JSON 结构
          try {
            const parsed = JSON.parse(raw)
            content = JSON.stringify(parsed).slice(0, MAX)
          } catch {
            content = raw.slice(0, MAX)
          }
          content += '...[truncated]'
        } else {
          content = raw
        }
        submitToolResult({
          tool_use_id: props.toolCall.pendingToolUseId,
          content,
        }).catch(err => console.error('[StandalonePlugin] 回传失败:', err))
      }
      break

    // 插件请求设置 → 返回空对象
    case PluginChannel.fetchPluginSettings:
      iframeRef.value?.contentWindow?.postMessage({
        type: PluginChannel.renderPluginSettings,
        value: {},
      }, pluginOrigin.value)
      break

    // 插件请求消息内容（兼容 default 类型渲染流）
    case PluginChannel.fetchPluginMessage:
      iframeRef.value?.contentWindow?.postMessage({
        type: PluginChannel.renderPlugin,
        props: {
          content: JSON.parse(JSON.stringify(props.toolCall.input ?? {})),
          name: apiName(),
        },
      }, pluginOrigin.value)
      break
  }
}

/** iframe 加载完成 */
function handleIframeLoad() {
  // 插件未发 pluginReadyForRender 则主动初始化
  setTimeout(() => {
    if (status.value === 'loading') sendInitData()
  }, 1000)
}

/** 跳过函数 — 用户主动放弃等待 */
function handleSkip() {
  if (!props.toolCall.pendingToolUseId || resultSubmitted.value) return
  resultSubmitted.value = true
  showSkipBtn.value = false
  if (fallbackTimer) { clearTimeout(fallbackTimer); fallbackTimer = null }
  submitToolResult({
    tool_use_id: props.toolCall.pendingToolUseId,
    content: '用户主动跳过插件等待',
    is_error: true,
  }).catch(err => console.error('[StandalonePlugin] 跳过失败:', err))
}

onMounted(() => {
  window.addEventListener('message', handleMessage)
  window.addEventListener('keydown', handleEscKey)
  // fallback: 3s 后若 @load 未触发，强制初始化
  setTimeout(() => {
    if (status.value === 'loading') sendInitData()
  }, 3000)
  // 30s 后显示网络提示（仅非 suspend-resume 场景）
  slowTimer = setTimeout(() => {
    if (status.value === 'loading') {
      showSlowHint.value = true
    }
  }, 30000)
  // suspend-resume：30s 后显示跳过按钮
  if (props.toolCall.pendingToolUseId) {
    skipTimer = setTimeout(() => {
      if (!resultSubmitted.value) showSkipBtn.value = true
    }, 30000)
  }
})

onUnmounted(() => {
  window.removeEventListener('message', handleMessage)
  window.removeEventListener('keydown', handleEscKey)
  if (isFullscreen.value) document.body.style.overflow = ''
  if (slowTimer) { clearTimeout(slowTimer); slowTimer = null }
  if (skipTimer) { clearTimeout(skipTimer); skipTimer = null }
  if (fallbackTimer) { clearTimeout(fallbackTimer); fallbackTimer = null }
})
</script>

<template>
  <div class="spi" :class="{ 'spi--fullscreen': isFullscreen }">
    <!-- 全屏遮罩（teleport 到 body 避免 z-index 层叠问题） -->
    <Teleport to="body">
      <div v-if="isFullscreen" class="spi-fullscreen-overlay" @click="toggleFullscreen" />
    </Teleport>
    <div class="spi-bar">
      <span class="spi-title">{{ toolCall.toolDisplayName || toolCall.tool }}</span>
      <span class="spi-badge">插件</span>
      <button class="spi-fullscreen-btn" @click="toggleFullscreen" :title="isFullscreen ? '退出全屏 (Esc)' : '全屏查看'">
        <template v-if="!isFullscreen">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
            <path d="M1 1h5v2H3v3H1V1zm9 0h5v5h-2V3h-3V1zM1 10h2v3h3v2H1v-5zm12 3h-3v2h5v-5h-2v3z"/>
          </svg>
          <span>全屏</span>
        </template>
        <template v-else>
          <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
            <path d="M4 4H1v2h3v3h2V4H4zm8 0h2v2h-3v3h-2V4h3zM4 12H1v-2h3V7h2v5H4zm8 0h2v-2h-3V7h-2v5h3z"/>
          </svg>
          <span>退出</span>
        </template>
      </button>
    </div>
    <div ref="frameWrapRef" class="spi-frame-wrap" :style="{ minHeight: isFullscreen ? 'auto' : (sp.iframeHeight || 350) + 'px' }">
      <div v-if="status === 'loading'" class="spi-loading">加载中...</div>
      <iframe
        ref="iframeRef"
        :src="sp.iframeUrl"
        :title="`${toolCall.toolDisplayName || toolCall.tool} 插件`"
        :style="{
          height: (sp.iframeHeight || 350) + 'px',
          width: '100%',
          opacity: status === 'loading' ? 0 : 1,
          transition: 'opacity 0.3s',
          transform: isFullscreen && fullscreenScale > 1 ? `scale(${fullscreenScale})` : undefined,
          transformOrigin: 'left top',
        }"
        sandbox="allow-scripts allow-same-origin allow-forms"
        class="spi-iframe"
        @load="handleIframeLoad"
      />
    </div>
    <!-- suspend-resume 等待状态 -->
    <div v-if="toolCall.pendingToolUseId && status === 'running' && !resultSubmitted" class="spi-waiting">
      <span>正在获取数据...</span>
    </div>
    <!-- suspend-resume 跳过按钮 -->
    <div v-if="showSkipBtn && !resultSubmitted" class="spi-skip">
      <span>插件响应较慢</span>
      <button class="spi-skip-btn" @click="handleSkip">不等了，继续对话</button>
      <span class="spi-skip-note">AI 将在没有插件数据的情况下继续回复</span>
    </div>
    <!-- 非 suspend-resume 模式下的慢网络提示 -->
    <div v-if="showSlowHint && !toolCall.pendingToolUseId" class="spi-slow-hint">
      <span>插件加载时间较长，可能受网络环境影响</span>
      <button class="spi-slow-close-btn" @click="showSlowHint = false">关闭提示</button>
    </div>
  </div>
</template>

<style scoped>
.spi {
  border-left: 3px solid #52c41a;
  background: #f6ffed;
  border-radius: 8px;
  padding: 12px;
  margin: 8px 0;
  transition: border-color 0.3s, background 0.3s;
}

/* 全屏模式：撑满屏幕，留出小边距 */
.spi--fullscreen {
  position: fixed;
  inset: 24px;
  z-index: 9999;
  margin: 0;
  border-radius: 12px;
  padding: 0;
  display: flex;
  flex-direction: column;
  background: #fff;
  border-left: none;
  box-shadow: 0 8px 40px rgba(0, 0, 0, 0.25);
}
.spi--fullscreen .spi-bar {
  padding: 12px 20px;
  border-bottom: 1px solid #e8e8e8;
  flex-shrink: 0;
  background: #fafafa;
  border-radius: 12px 12px 0 0;
}
.spi--fullscreen .spi-frame-wrap {
  flex: 1;
  border: none;
  border-radius: 0 0 12px 12px;
  overflow: auto;
}
/* 遮罩样式移到非 scoped（Teleport 到 body 不受 scoped 限制） */
.spi-fullscreen-btn {
  margin-left: auto;
  background: none;
  border: 1px solid #d9d9d9;
  border-radius: 4px;
  padding: 3px 8px;
  cursor: pointer;
  color: #666;
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  transition: all 0.2s;
}
.spi-fullscreen-btn:hover {
  color: #1890ff;
  border-color: #1890ff;
  background: #f0f7ff;
}

.spi-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.spi-title {
  font-weight: 600;
  font-size: 14px;
}
.spi-badge {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 4px;
  background: #fa8c16;
  color: #fff;
}

.spi-frame-wrap {
  position: relative;
  border-radius: 6px;
  overflow-x: auto;
  overflow-y: hidden;
  border: 1px solid #e8e8e8;
  background: #fff;
}
.spi-loading {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #8c8c8c;
  font-size: 13px;
  z-index: 1;
}
.spi-iframe {
  width: 100%;
  border: none;
  display: block;
}

.spi-waiting {
  margin-top: 8px;
  font-size: 12px;
  color: #1890ff;
  animation: spi-pulse 1.5s ease-in-out infinite;
}
@keyframes spi-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

.spi-skip {
  margin-top: 8px;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #faad14;
}
.spi-skip-note {
  color: #8c8c8c;
  font-size: 11px;
}
.spi-skip-btn {
  padding: 2px 10px;
  font-size: 12px;
  border: 1px solid #faad14;
  background: #fff;
  color: #faad14;
  border-radius: 4px;
  cursor: pointer;
}
.spi-skip-btn:hover {
  background: #fffbe6;
}

.spi-slow-hint {
  margin-top: 8px;
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 12px;
  color: #faad14;
}
.spi-slow-close-btn {
  padding: 2px 10px;
  font-size: 12px;
  border: 1px solid #d9d9d9;
  background: #fff;
  color: #666;
  border-radius: 4px;
  cursor: pointer;
}
.spi-slow-close-btn:hover {
  background: #f5f5f5;
}
</style>

<style>
/* 全屏遮罩（Teleport 到 body，需要非 scoped） */
.spi-fullscreen-overlay {
  position: fixed;
  inset: 0;
  z-index: 9998;
  background: rgba(0, 0, 0, 0.45);
}
</style>
