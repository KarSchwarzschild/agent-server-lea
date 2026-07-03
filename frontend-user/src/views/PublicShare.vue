<script setup lang="ts">
/**
 * 公开全文查看页面（无需登录）
 *
 * 通过 HMAC-SHA256 token 验证分享链接，展示消息全文。
 * 复用 MarkdownIt + highlight.js + KaTeX 渲染。
 */
import { ref, onMounted, computed } from 'vue'
import { useRoute } from 'vue-router'
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'
import 'highlight.js/styles/github.css'
import katex from 'katex'
import 'katex/dist/katex.min.css'
import { sanitizeWithKatex } from '../utils/sanitize'

const route = useRoute()
const loading = ref(true)
const error = ref('')
const content = ref('')
const title = ref('')
const createdAt = ref('')

// Markdown 渲染实例
const md = new MarkdownIt({
  html: false,
  linkify: true,
  typographer: true,
  highlight(str: string, lang: string): string {
    if (lang && hljs.getLanguage(lang)) {
      try {
        const highlighted = hljs.highlight(str, { language: lang, ignoreIllegals: true }).value
        return `<pre class="hljs"><code class="language-${lang}">${highlighted}</code></pre>`
      } catch { /* fallback */ }
    }
    return `<pre class="hljs"><code>${md.utils.escapeHtml(str)}</code></pre>`
  },
})

// KaTeX 行内/块级公式
function addKatexSupport(mdi: MarkdownIt) {
  // 行内公式 $...$
  mdi.inline.ruler.after('escape', 'math_inline', (state, silent) => {
    if (state.src[state.pos] !== '$') return false
    if (state.src[state.pos + 1] === '$') return false
    const end = state.src.indexOf('$', state.pos + 1)
    if (end === -1) return false
    if (!silent) {
      const token = state.push('math_inline', 'math', 0)
      token.content = state.src.slice(state.pos + 1, end)
    }
    state.pos = end + 1
    return true
  })
  mdi.renderer.rules.math_inline = (tokens, idx) => {
    try {
      return katex.renderToString(tokens[idx].content, { throwOnError: false })
    } catch {
      return `<code>${tokens[idx].content}</code>`
    }
  }
}

addKatexSupport(md)

const renderedHtml = computed(() => {
  if (!content.value) return ''
  const raw = md.render(content.value)
  return sanitizeWithKatex(raw)
})

const formattedDate = computed(() => {
  if (!createdAt.value) return ''
  try {
    const d = new Date(createdAt.value)
    return d.toLocaleDateString('zh-CN', {
      year: 'numeric', month: 'long', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
    })
  } catch {
    return createdAt.value
  }
})

const expiryDate = computed(() => {
  if (!createdAt.value) return ''
  try {
    // 分享链接 7 天有效，从分享创建时算（这里用 createdAt 近似）
    return '链接有效期 7 天'
  } catch {
    return ''
  }
})

async function fetchContent() {
  const shareId = route.params.shareId as string
  const token = route.query.t as string

  if (!shareId || !token) {
    error.value = '无效的分享链接'
    loading.value = false
    return
  }

  try {
    const baseURL = import.meta.env.VITE_API_BASE_URL || ''
    const resp = await fetch(
      `${baseURL}/api/public/share/${encodeURIComponent(shareId)}?t=${encodeURIComponent(token)}`
    )
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}))
      error.value = data.detail || '分享链接无效或已过期'
      loading.value = false
      return
    }
    const data = await resp.json()
    content.value = data.content
    title.value = data.title
    createdAt.value = data.created_at
  } catch (e) {
    error.value = '网络错误，请稍后重试'
  } finally {
    loading.value = false
  }
}

function copyContent() {
  navigator.clipboard.writeText(content.value).catch(() => {})
}

onMounted(fetchContent)
</script>

<template>
  <div class="share-page">
    <div class="share-container">
      <!-- 头部 -->
      <header class="share-header">
        <h1 class="share-title">终身教育科研助手 · 全文查看</h1>
        <div v-if="formattedDate" class="share-meta">
          <span>生成时间：{{ formattedDate }}</span>
          <span class="share-expiry">{{ expiryDate }}</span>
        </div>
      </header>

      <!-- 加载状态 -->
      <div v-if="loading" class="share-loading">
        <div class="spinner"></div>
        <p>正在加载内容…</p>
      </div>

      <!-- 错误状态 -->
      <div v-else-if="error" class="share-error">
        <div class="error-icon">!</div>
        <p>{{ error }}</p>
      </div>

      <!-- 内容 -->
      <article v-else class="share-content markdown-body" v-html="renderedHtml"></article>

      <!-- 底部 -->
      <footer class="share-footer">
        <p class="share-disclaimer">由终身教育科研助手生成 · 内容仅供学术参考</p>
        <div v-if="!loading && !error" class="share-actions">
          <button class="action-btn" @click="copyContent">复制内容</button>
        </div>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.share-page {
  min-height: 100vh;
  background: #f5f6fa;
  padding: 24px 16px;
}

.share-container {
  max-width: 800px;
  margin: 0 auto;
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
  overflow: hidden;
}

.share-header {
  padding: 24px 32px 16px;
  border-bottom: 1px solid #eee;
}

.share-title {
  font-size: 18px;
  font-weight: 600;
  color: #1a1a1a;
  margin: 0 0 8px;
}

.share-meta {
  display: flex;
  gap: 16px;
  font-size: 13px;
  color: #888;
}

.share-expiry {
  color: #f59e0b;
}

.share-loading {
  padding: 80px 32px;
  text-align: center;
  color: #888;
}

.spinner {
  width: 32px;
  height: 32px;
  border: 3px solid #e5e7eb;
  border-top: 3px solid #3b82f6;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  margin: 0 auto 16px;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.share-error {
  padding: 80px 32px;
  text-align: center;
}

.error-icon {
  width: 48px;
  height: 48px;
  border-radius: 50%;
  background: #fee2e2;
  color: #ef4444;
  font-size: 24px;
  font-weight: bold;
  line-height: 48px;
  margin: 0 auto 16px;
}

.share-error p {
  color: #666;
  font-size: 15px;
}

.share-content {
  padding: 32px;
  line-height: 1.8;
  font-size: 15px;
  color: #333;
  overflow-wrap: break-word;
}

.share-content :deep(h1) { font-size: 1.6em; margin: 1.2em 0 0.6em; }
.share-content :deep(h2) { font-size: 1.35em; margin: 1em 0 0.5em; }
.share-content :deep(h3) { font-size: 1.15em; margin: 0.8em 0 0.4em; }
.share-content :deep(p) { margin: 0.6em 0; }
.share-content :deep(ul), .share-content :deep(ol) { padding-left: 1.5em; }
.share-content :deep(blockquote) {
  border-left: 4px solid #3b82f6;
  padding: 8px 16px;
  margin: 12px 0;
  background: #f8fafc;
  color: #555;
}
.share-content :deep(pre) {
  background: #f6f8fa;
  border-radius: 8px;
  padding: 16px;
  overflow-x: auto;
  font-size: 13px;
  line-height: 1.5;
}
.share-content :deep(code) {
  font-family: 'Menlo', 'Monaco', 'Consolas', monospace;
}
.share-content :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 12px 0;
}
.share-content :deep(th),
.share-content :deep(td) {
  border: 1px solid #ddd;
  padding: 8px 12px;
  text-align: left;
}
.share-content :deep(th) {
  background: #f6f8fa;
  font-weight: 600;
}
.share-content :deep(a) {
  color: #3b82f6;
  text-decoration: none;
}
.share-content :deep(a:hover) {
  text-decoration: underline;
}

.share-footer {
  padding: 16px 32px;
  border-top: 1px solid #eee;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.share-disclaimer {
  font-size: 12px;
  color: #aaa;
  margin: 0;
}

.share-actions {
  display: flex;
  gap: 8px;
}

.action-btn {
  padding: 6px 16px;
  border: 1px solid #ddd;
  border-radius: 6px;
  background: #fff;
  color: #555;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s;
}

.action-btn:hover {
  background: #f5f6fa;
  border-color: #bbb;
}

@media (max-width: 640px) {
  .share-page { padding: 12px 8px; }
  .share-container { border-radius: 8px; }
  .share-header { padding: 16px 20px 12px; }
  .share-content { padding: 20px; }
  .share-footer {
    flex-direction: column;
    gap: 12px;
    padding: 12px 20px;
  }
  .share-meta { flex-direction: column; gap: 4px; }
}
</style>
