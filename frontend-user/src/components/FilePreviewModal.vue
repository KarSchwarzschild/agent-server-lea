<script setup lang="ts">
/**
 * 文件预览模态框组件
 *
 * 功能：
 * - Markdown 文件：渲染后展示
 * - PDF 文件：iframe 原生预览
 * - TXT/JSON/CSV 文件：代码块展示
 * - Word 文件：提示下载
 *
 * 操作按钮：
 * - 转PDF（仅支持 md/txt/json）
 * - 下载
 * - 关闭
 */
import { ref, computed, watch, toRef } from 'vue'
import {
  NModal,
  NCard,
  NButton,
  NIcon,
  NSpin,
  NSpace,
  NTag,
  useMessage,
} from 'naive-ui'
import {
  CloseOutline,
  DownloadOutline,
  DocumentOutline,
} from '@vicons/ionicons5'
import {
  getFileContent,
  convertToPdf,
  downloadFile,
  getPdfPreviewUrl,
  getPreviewToken,
  getKkFileViewUrl,
  type GeneratedFileV2,
  type FileContentResponse,
} from '../api/superagent'
import { useUserStore } from '../stores/user'

// Markdown 渲染（复用现有配置）
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'
import 'highlight.js/styles/github.css'
import katex from 'katex'
import 'katex/dist/katex.min.css'
import { useMermaidChart } from '../composables/useMermaidChart'
import { useEChartsChart } from '../composables/useEChartsChart'
import { quickDetectECharts } from '../utils/echartsDetector'

// Props
interface Props {
  show: boolean
  file: GeneratedFileV2 | null
}

const props = defineProps<Props>()

// Emits
const emit = defineEmits<{
  (e: 'update:show', value: boolean): void
  (e: 'pdf-created', file: GeneratedFileV2): void
}>()

const message = useMessage()
const userStore = useUserStore()

// 状态
const loading = ref(false)
const converting = ref(false)
const fileContent = ref<FileContentResponse | null>(null)
const error = ref<string | null>(null)

// 图表渲染
const mdContentRef = ref<HTMLElement | null>(null)
const isStreamingRef = ref(false)

useMermaidChart({
  containerRef: mdContentRef,
  isStreaming: isStreamingRef,
})

useEChartsChart({
  containerRef: mdContentRef,
  isStreaming: isStreamingRef,
})

// Markdown 渲染配置
function highlightCode(str: string, lang: string): string {
  // Mermaid 图表：返回占位容器
  if (lang === 'mermaid') {
    const escaped = str.replace(/[&<>"']/g, (m) => {
      const map: Record<string, string> = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }
      return map[m] || m
    })
    return `<pre class="mermaid-placeholder language-mermaid"><code class="language-mermaid">${escaped}</code></pre>`
  }

  // ECharts 图表：显式标记或 JSON/JS 智能检测
  if (lang === 'echarts' || ((lang === 'json' || lang === 'javascript' || lang === 'js') && quickDetectECharts(str))) {
    const escaped = str.replace(/[&<>"']/g, (m) => {
      const map: Record<string, string> = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }
      return map[m] || m
    })
    return `<pre class="echarts-placeholder language-echarts"><code class="language-echarts">${escaped}</code></pre>`
  }

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

// 配置链接在新标签页打开
const defaultRender = md.renderer.rules.link_open || function(tokens, idx, options, _env, self) {
  return self.renderToken(tokens, idx, options)
}

md.renderer.rules.link_open = function(tokens, idx, options, env, self) {
  const token = tokens[idx]
  const targetIndex = token.attrIndex('target')
  const relIndex = token.attrIndex('rel')

  if (targetIndex < 0) {
    token.attrPush(['target', '_blank'])
  } else {
    token.attrs![targetIndex][1] = '_blank'
  }

  if (relIndex < 0) {
    token.attrPush(['rel', 'noopener noreferrer'])
  } else {
    token.attrs![relIndex][1] = 'noopener noreferrer'
  }

  return defaultRender(tokens, idx, options, env, self)
}

function renderMathInText(text: string): string {
  if (!text) return ''
  text = text.replace(/\$\$([\s\S]+?)\$\$/g, (_, formula) => {
    try {
      return katex.renderToString(formula.trim(), { displayMode: true, throwOnError: false })
    } catch {
      return `<span class="katex-error">$$${formula}$$</span>`
    }
  })
  text = text.replace(/\$([^\$\n]+?)\$/g, (_, formula) => {
    try {
      return katex.renderToString(formula.trim(), { displayMode: false, throwOnError: false })
    } catch {
      return `<span class="katex-error">$${formula}$</span>`
    }
  })
  return text
}

function renderMarkdown(text: string): string {
  if (!text) return ''
  const withMath = renderMathInText(text)
  return md.render(withMath)
}

// 计算属性
const visible = computed({
  get: () => props.show,
  set: (value) => emit('update:show', value)
})

const fileType = computed(() => props.file?.file_type || '')

const isTextFile = computed(() => ['md', 'txt', 'json', 'csv', 'html', 'xml', 'py', 'js', 'css', 'yaml', 'yml', 'sql', 'sh', 'log', 'ini', 'toml'].includes(fileType.value))
const isPdfFile = computed(() => fileType.value === 'pdf')
const isImageFile = computed(() => ['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'bmp'].includes(fileType.value))
const isOfficeFile = computed(() => ['docx', 'doc', 'xlsx', 'xls', 'pptx', 'ppt'].includes(fileType.value))
const canConvertToPdf = computed(() => ['md', 'txt', 'json'].includes(fileType.value))
const isSandboxFile = computed(() => props.file?.id?.startsWith('sandbox_') || !!(props.file as any)?._blobUrl)

// PDF Blob URL（用于 iframe 预览）
const pdfBlobUrl = ref<string>('')
const pdfLoading = ref(false)

// kkFileView 预览
const kkPreviewUrl = ref<string>('')
const kkLoading = ref(false)
const kkError = ref(false)

// 代码文件扩展名 → hljs 语言映射
const CODE_LANG_MAP: Record<string, string> = {
  py: 'python', js: 'javascript', ts: 'typescript', css: 'css',
  html: 'xml', xml: 'xml', json: 'json', yaml: 'yaml', yml: 'yaml',
  sql: 'sql', sh: 'bash', ini: 'ini', toml: 'ini',
}

const renderedContent = computed(() => {
  if (!fileContent.value) return ''

  if (fileType.value === 'md') {
    return renderMarkdown(fileContent.value.content)
  }

  // 代码文件：使用 hljs 语法高亮
  const lang = CODE_LANG_MAP[fileType.value]
  if (lang && hljs.getLanguage(lang)) {
    const highlighted = hljs.highlight(fileContent.value.content, { language: lang, ignoreIllegals: true }).value
    return `<pre class="code-preview hljs"><code>${highlighted}</code></pre>`
  }

  // 其他文本文件（txt/csv/log 等）：纯文本代码块
  return `<pre class="code-preview"><code>${escapeHtml(fileContent.value.content)}</code></pre>`
})

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

// 格式化文件大小
function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

// 沙箱文件 Blob URL
const sandboxBlobUrl = ref<string>('')

// 加载文件内容
async function loadContent() {
  if (!props.file) return

  // 沙箱图片文件：直接使用传入的 blobUrl
  if (isSandboxFile.value && isImageFile.value) {
    const blobUrl = (props.file as any)?._blobUrl
    if (blobUrl) {
      sandboxBlobUrl.value = blobUrl
    }
    return
  }

  // 沙箱文本文件：使用预取的内容
  const textContent = (props.file as any)?._textContent
  if (isSandboxFile.value && textContent) {
    fileContent.value = {
      id: props.file!.id,
      filename: props.file!.filename,
      file_type: props.file!.file_type,
      content: textContent,
      size: props.file!.size,
    }
    return
  }

  // PDF 文件使用 Blob URL 预览
  if (isPdfFile.value) {
    await loadPdfBlob()
    return
  }

  // Office 文件使用 kkFileView 预览（沙箱文件跳过，kkFileView 无法访问需认证的 URL）
  if (isOfficeFile.value) {
    if (isSandboxFile.value) {
      // 沙箱 Office 文件：直接显示 fallback（kkFileView 无法访问带 Auth 的沙箱 URL）
      kkError.value = true
      return
    }
    await loadKkPreview()
    return
  }

  loading.value = true
  error.value = null

  try {
    fileContent.value = await getFileContent(props.file.id)
  } catch (e: unknown) {
    const err = e as Error
    error.value = err.message || '加载失败'
    message.error(`加载失败: ${error.value}`)
  } finally {
    loading.value = false
  }
}

// 加载 PDF 为 Blob URL（解决 iframe 认证问题）
async function loadPdfBlob() {
  if (!props.file) return

  pdfLoading.value = true
  error.value = null

  try {
    const response = await fetch(getPdfPreviewUrl(props.file.id), {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${userStore.token}`
      }
    })

    if (!response.ok) {
      throw new Error(`加载失败: ${response.status}`)
    }

    const blob = await response.blob()
    // 释放旧的 Blob URL
    if (pdfBlobUrl.value) {
      URL.revokeObjectURL(pdfBlobUrl.value)
    }
    pdfBlobUrl.value = URL.createObjectURL(blob)
  } catch (e: unknown) {
    const err = e as Error
    error.value = err.message || 'PDF 加载失败'
    message.error(`PDF 加载失败: ${error.value}`)
  } finally {
    pdfLoading.value = false
  }
}

// 加载 kkFileView 预览（Office 文件）
async function loadKkPreview() {
  if (!props.file) return

  kkLoading.value = true
  kkError.value = false
  error.value = null

  try {
    const resp = await getPreviewToken(props.file.id)
    kkPreviewUrl.value = getKkFileViewUrl(resp.preview_url)
  } catch (e: unknown) {
    const err = e as Error
    kkError.value = true
    error.value = err.message || '预览加载失败'
    message.error(`预览加载失败: ${error.value}`)
  } finally {
    kkLoading.value = false
  }
}

// 下载文件
async function handleDownload() {
  if (!props.file) return

  // 沙箱文件：使用 blobUrl 或通过 downloadUrl 下载
  const blobUrl = sandboxBlobUrl.value || (props.file as any)?._blobUrl
  if (isSandboxFile.value && blobUrl) {
    const a = document.createElement('a')
    a.href = blobUrl
    a.download = props.file.filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    message.success(`已下载: ${props.file.filename}`)
    return
  }

  // 沙箱文本文件：通过 downloadUrl 带认证下载
  const downloadUrl = (props.file as any)?._downloadUrl
  if (isSandboxFile.value && downloadUrl) {
    try {
      const response = await fetch(downloadUrl, {
        headers: { 'Authorization': `Bearer ${userStore.token}` }
      })
      if (!response.ok) throw new Error(`${response.status}`)
      const blob = await response.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = props.file.filename
      document.body.appendChild(a)
      a.click()
      URL.revokeObjectURL(url)
      document.body.removeChild(a)
      message.success(`已下载: ${props.file.filename}`)
    } catch (e: unknown) {
      message.error(`下载失败: ${(e as Error).message}`)
    }
    return
  }

  try {
    await downloadFile(props.file, userStore.token)
    message.success('已开始下载')
  } catch (e: unknown) {
    const err = e as Error
    message.error(`下载失败: ${err.message}`)
  }
}

// 转换为PDF
async function handleConvertToPdf() {
  if (!props.file || !canConvertToPdf.value) return

  converting.value = true

  try {
    const result = await convertToPdf(props.file.id)
    message.success(`转换成功，耗时 ${result.conversion_time_ms}ms`)
    emit('pdf-created', result.pdf_file)
  } catch (e: unknown) {
    const err = e as Error
    message.error(`转换失败: ${err.message}`)
  } finally {
    converting.value = false
  }
}

// 关闭模态框
function handleClose() {
  visible.value = false
  fileContent.value = null
  error.value = null
  kkPreviewUrl.value = ''
  kkError.value = false
  // 清理 PDF Blob URL
  if (pdfBlobUrl.value) {
    URL.revokeObjectURL(pdfBlobUrl.value)
    pdfBlobUrl.value = ''
  }
  // 清理沙箱 Blob URL
  if (sandboxBlobUrl.value) {
    URL.revokeObjectURL(sandboxBlobUrl.value)
    sandboxBlobUrl.value = ''
  }
}

// 监听显示状态变化
watch(() => props.show, (newVal) => {
  if (newVal && props.file) {
    loadContent()
  }
})
</script>

<template>
  <n-modal
    v-model:show="visible"
    preset="card"
    :title="file?.filename || '文件预览'"
    :style="{ width: '95vw', maxWidth: '1200px' }"
    :segmented="{ content: true, footer: 'soft' }"
    @close="handleClose"
  >
    <template #header-extra>
      <n-space>
        <n-tag :bordered="false" size="small">
          {{ fileType.toUpperCase() }}
        </n-tag>
        <n-tag :bordered="false" size="small" type="info">
          {{ formatFileSize(file?.size || 0) }}
        </n-tag>
      </n-space>
    </template>

    <div class="preview-content">
      <!-- 加载中 -->
      <div v-if="loading" class="preview-loading">
        <n-spin size="large" />
        <p>加载中...</p>
      </div>

      <!-- 错误状态 -->
      <div v-else-if="error" class="preview-error">
        <p>{{ error }}</p>
        <n-button @click="loadContent">重试</n-button>
      </div>

      <!-- PDF 预览 -->
      <div v-else-if="isPdfFile" class="pdf-preview">
        <div v-if="pdfLoading" class="preview-loading">
          <n-spin size="large" />
          <p>加载 PDF 中...</p>
        </div>
        <iframe
          v-else-if="pdfBlobUrl"
          :src="pdfBlobUrl"
          width="100%"
          height="750"
          frameborder="0"
        ></iframe>
        <div v-else-if="error" class="preview-error">
          <p>{{ error }}</p>
          <n-button @click="loadContent">重试</n-button>
        </div>
      </div>

      <!-- 图片预览 -->
      <div v-else-if="isImageFile && sandboxBlobUrl" class="image-preview">
        <img :src="sandboxBlobUrl" :alt="file?.filename" class="preview-image" />
      </div>

      <!-- Office 文件预览（kkFileView） -->
      <div v-else-if="isOfficeFile" class="office-preview">
        <div v-if="kkLoading" class="preview-loading">
          <n-spin size="large" />
          <p>加载预览中...</p>
        </div>
        <iframe
          v-else-if="kkPreviewUrl && !kkError"
          :src="kkPreviewUrl"
          width="100%"
          height="750"
          frameborder="0"
          class="kkfileview-iframe"
          @error="kkError = true"
          @load="kkLoading = false"
        ></iframe>
        <div v-else class="word-preview">
          <n-icon :size="48" color="#2563eb">
            <DocumentOutline />
          </n-icon>
          <p class="word-title">{{ file?.filename }}</p>
          <p class="word-hint">在线预览不可用，请下载后查看</p>
          <n-button type="primary" @click="handleDownload">
            <template #icon>
              <n-icon><DownloadOutline /></n-icon>
            </template>
            下载文件
          </n-button>
        </div>
      </div>

      <!-- 文本文件预览 -->
      <div v-else-if="isTextFile && fileContent" class="text-preview">
        <div
          ref="mdContentRef"
          class="markdown-content"
          v-html="renderedContent"
        ></div>
      </div>

      <!-- 未知类型 -->
      <div v-else class="preview-unknown">
        <p>暂不支持预览此类型文件</p>
      </div>
    </div>

    <template #footer>
      <n-space justify="space-between" style="width: 100%">
        <n-space>
          <!-- 转PDF按钮 -->
          <n-button
            v-if="canConvertToPdf"
            type="primary"
            :loading="converting"
            @click="handleConvertToPdf"
          >
            <template #icon>
              <n-icon><DocumentOutline /></n-icon>
            </template>
            转为 PDF
          </n-button>
        </n-space>

        <n-space>
          <!-- 下载按钮 -->
          <n-button @click="handleDownload">
            <template #icon>
              <n-icon><DownloadOutline /></n-icon>
            </template>
            下载
          </n-button>

          <!-- 关闭按钮 -->
          <n-button @click="handleClose">
            <template #icon>
              <n-icon><CloseOutline /></n-icon>
            </template>
            关闭
          </n-button>
        </n-space>
      </n-space>
    </template>
  </n-modal>
</template>

<style scoped>
.preview-content {
  min-height: 400px;
  max-height: 80vh;
  overflow-y: auto;
}

.preview-loading,
.preview-error,
.preview-unknown {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 300px;
  gap: 16px;
  color: #666;
}

.preview-error {
  color: #dc2626;
}

.pdf-preview {
  width: 100%;
}

.pdf-preview iframe {
  border-radius: 8px;
  border: 1px solid #e5e7eb;
}

.image-preview {
  display: flex;
  justify-content: center;
  align-items: center;
  padding: 16px;
  background: #f8fafc;
  border-radius: 8px;
  min-height: 200px;
}

.preview-image {
  max-width: 100%;
  max-height: 70vh;
  border-radius: 4px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.office-preview {
  width: 100%;
}

.kkfileview-iframe {
  border-radius: 8px;
  border: 1px solid #e5e7eb;
}

.word-preview {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 300px;
  gap: 16px;
  padding: 40px;
  background: #f9fafb;
  border-radius: 8px;
}

.word-title {
  font-size: 16px;
  font-weight: 600;
  color: #1f2937;
  margin: 0;
}

.word-hint {
  color: #6b7280;
  margin: 0;
}

.text-preview {
  padding: 16px;
  background: #fff;
  border-radius: 8px;
}

/* Markdown 样式 */
.text-preview :deep(.markdown-content) {
  line-height: 1.8;
  color: #374151;
}

.text-preview :deep(.markdown-content h1) {
  font-size: 1.5em;
  font-weight: 700;
  margin: 1em 0 0.5em;
  padding-bottom: 0.3em;
  border-bottom: 1px solid #e5e7eb;
}

.text-preview :deep(.markdown-content h2) {
  font-size: 1.3em;
  font-weight: 600;
  margin: 1em 0 0.5em;
}

.text-preview :deep(.markdown-content h3) {
  font-size: 1.1em;
  font-weight: 600;
  margin: 1em 0 0.5em;
}

.text-preview :deep(.markdown-content p) {
  margin: 0.8em 0;
}

.text-preview :deep(.markdown-content pre) {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  padding: 12px;
  overflow-x: auto;
  margin: 1em 0;
}

.text-preview :deep(.markdown-content code) {
  font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
  font-size: 0.9em;
}

.text-preview :deep(.markdown-content pre code) {
  background: none;
  padding: 0;
}

.text-preview :deep(.markdown-content table) {
  width: 100%;
  border-collapse: collapse;
  margin: 1em 0;
}

.text-preview :deep(.markdown-content th),
.text-preview :deep(.markdown-content td) {
  border: 1px solid #d1d5db;
  padding: 8px 12px;
  text-align: left;
}

.text-preview :deep(.markdown-content th) {
  background: #f3f4f6;
  font-weight: 600;
}

.text-preview :deep(.markdown-content blockquote) {
  margin: 1em 0;
  padding: 0.5em 1em;
  background: #f8fafc;
  border-left: 4px solid #2563eb;
  color: #4b5563;
}

.text-preview :deep(.markdown-content ul),
.text-preview :deep(.markdown-content ol) {
  margin: 0.8em 0;
  padding-left: 1.5em;
}

.text-preview :deep(.markdown-content li) {
  margin: 0.3em 0;
}

.text-preview :deep(.markdown-content a) {
  color: #2563eb;
  text-decoration: none;
}

.text-preview :deep(.markdown-content a:hover) {
  text-decoration: underline;
}

/* 代码预览样式（特异性需高于 .markdown-content pre） */
.text-preview :deep(.markdown-content .code-preview) {
  background: #f6f8fa;
  color: #24292e;
  border-radius: 8px;
  padding: 16px;
  overflow-x: auto;
  border: 1px solid #e1e4e8;
  font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
}

.text-preview :deep(.markdown-content .code-preview code) {
  background: none;
  color: inherit;
}
/* Mermaid / ECharts 图表样式 */
.text-preview :deep(.mermaid-chart-wrapper) {
  position: relative;
  margin: 1.5em 0;
  padding: 16px;
  background: #fafafa;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  overflow-x: auto;
}

.text-preview :deep(.mermaid-chart-wrapper .mermaid-container) {
  display: flex;
  justify-content: center;
}

.text-preview :deep(.mermaid-chart-wrapper svg) {
  max-width: 100%;
  height: auto;
}

.text-preview :deep(.echarts-chart-wrapper) {
  position: relative;
  width: 100%;
  margin: 1.5em 0;
  background: #fafafa;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  overflow: hidden;
}

.text-preview :deep(.chart-toggle-bar) {
  position: absolute;
  top: 8px;
  right: 8px;
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 4px;
  background: rgba(255, 255, 255, 0.88);
  backdrop-filter: blur(8px);
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  z-index: 10;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.2s ease;
}

.text-preview :deep(.mermaid-chart-wrapper:hover .chart-toggle-bar),
.text-preview :deep(.echarts-chart-wrapper:hover .chart-toggle-bar) {
  opacity: 1;
  pointer-events: auto;
}

.text-preview :deep(.chart-action-btn) {
  position: relative;
  padding: 4px 8px;
  font-size: 13px;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: #6b7280;
  cursor: pointer;
  transition: all 0.15s;
  line-height: 1;
}

.text-preview :deep(.chart-action-btn:hover) {
  background: #f3f4f6;
  color: #1f2937;
}

.text-preview :deep([data-tooltip]) {
  position: relative;
}

.text-preview :deep([data-tooltip])::after {
  content: attr(data-tooltip);
  position: absolute;
  top: calc(100% + 6px);
  left: 50%;
  transform: translateX(-50%);
  padding: 4px 8px;
  font-size: 12px;
  line-height: 1.3;
  color: #fff;
  background: #1f2937;
  border-radius: 4px;
  white-space: nowrap;
  pointer-events: none;
  opacity: 0;
  transition: opacity 0.15s;
  z-index: 20;
}

.text-preview :deep([data-tooltip]:hover)::after {
  opacity: 1;
}

.text-preview :deep(.chart-code-view) {
  position: relative;
  width: 100%;
  max-height: 600px;
  overflow: auto;
  background: #1e293b;
  border-radius: 8px;
}

.text-preview :deep(.chart-code-view pre) {
  margin: 0;
  padding: 36px 16px 16px;
  background: transparent;
  overflow-x: auto;
}

.text-preview :deep(.chart-code-view code) {
  color: #e2e8f0 !important;
  background: transparent !important;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
}

.text-preview :deep(.chart-code-toolbar) {
  position: absolute;
  top: 8px;
  right: 8px;
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 4px;
  background: rgba(255, 255, 255, 0.12);
  border: 1px solid rgba(255, 255, 255, 0.15);
  border-radius: 6px;
  z-index: 10;
}

.text-preview :deep(.chart-code-btn) {
  padding: 4px 8px;
  font-size: 13px;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: #e2e8f0;
  cursor: pointer;
  transition: all 0.15s;
  line-height: 1;
}

.text-preview :deep(.chart-code-btn:hover) {
  background: rgba(255, 255, 255, 0.2);
}
</style>
