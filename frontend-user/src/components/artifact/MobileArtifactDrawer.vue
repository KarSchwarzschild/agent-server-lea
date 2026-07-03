<template>
  <!-- 半屏抽屉触发按钮（嵌入对话区域底部） -->
  <div
    v-if="artifactStore.panelVisible && !isExpanded"
    class="artifact-peek-bar"
    @click="isExpanded = true"
  >
    <span class="peek-icon">📄</span>
    <span class="peek-title">{{ artifactStore.activeArtifact?.title || '查看文档' }}</span>
    <span class="peek-arrow">▲</span>
  </div>

  <!-- 半屏/全屏抽屉 -->
  <Teleport to="body">
    <Transition name="drawer">
      <div v-if="isExpanded" class="mobile-artifact-overlay" @click.self="isExpanded = false">
        <div
          class="mobile-artifact-drawer"
          :class="{ 'is-fullscreen': isDrawerFullscreen }"
        >
          <!-- 拖拽条 -->
          <div class="drawer-handle" @click="isDrawerFullscreen = !isDrawerFullscreen">
            <div class="handle-bar" />
          </div>

          <!-- 标题栏 -->
          <div class="drawer-header">
            <VersionNavigator />
            <span class="drawer-title">{{ artifactStore.activeArtifact?.title || '文档' }}</span>
            <div class="drawer-actions">
              <button class="drawer-btn" @click="isDrawerFullscreen = !isDrawerFullscreen">
                {{ isDrawerFullscreen ? '半屏' : '全屏' }}
              </button>
              <button class="drawer-btn" @click="isExpanded = false">✕</button>
            </div>
          </div>

          <!-- 内容区 -->
          <div class="drawer-body" v-html="renderedHtml" />

          <!-- 底部操作 + 内嵌输入框 -->
          <div class="drawer-footer">
            <div class="footer-export">
              <n-popselect
                :options="docxPresets"
                @update:value="handleDocxExport"
                trigger="click"
              >
                <button class="export-btn">Word ▾</button>
              </n-popselect>
              <button class="export-btn" @click="handlePdfExport" :disabled="exporting">PDF</button>
              <button class="export-btn" @click="handleCopy">复制</button>
            </div>
            <div class="footer-input">
              <input
                v-model="instruction"
                placeholder="AI修改指令..."
                @keydown.enter="handleSubmit"
              />
              <button class="send-btn" :disabled="!instruction.trim()" @click="handleSubmit">▶</button>
            </div>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { NPopselect, useMessage } from 'naive-ui'
import MarkdownIt from 'markdown-it'
import { useArtifactStore } from '@/stores/artifact'
import VersionNavigator from './VersionNavigator.vue'

const emit = defineEmits<{
  'send-instruction': [text: string]
}>()

const artifactStore = useArtifactStore()
const message = useMessage()
const isExpanded = ref(false)
const isDrawerFullscreen = ref(false)
const instruction = ref('')
const exporting = ref(false)

const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

const renderedHtml = computed(() => {
  const content = artifactStore.displayContent
  return content ? md.render(content) : ''
})

const docxPresets = [
  { label: '通用格式', value: 'standard' },
  { label: '行政公文', value: 'official' },
  { label: '内部文件', value: 'internal' },
  { label: '研究报告', value: 'report' },
]

/** 带鉴权的文件下载 */
async function downloadFileById(fileId: string, filename: string) {
  const token = localStorage.getItem('token') || ''
  const baseURL = import.meta.env.VITE_API_BASE_URL || ''
  const resp = await fetch(`${baseURL}/api/user/super-agent/files/${fileId}/download`, {
    headers: { 'Authorization': `Bearer ${token}` },
  })
  if (!resp.ok) throw new Error(`下载失败: HTTP ${resp.status}`)
  const blob = await resp.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  URL.revokeObjectURL(url)
  document.body.removeChild(a)
}

async function handleDocxExport(style: string) {
  const id = artifactStore.activeArtifactId
  if (!id || exporting.value) return
  exporting.value = true
  try {
    const { exportArtifact } = await import('@/api/artifact')
    const result = await exportArtifact(id, 'docx', style)
    await downloadFileById(result.file_id, result.filename)
    message.success('Word 导出成功')
  } catch (e) {
    message.error(`导出失败: ${e}`)
  } finally {
    exporting.value = false
  }
}

async function handlePdfExport() {
  const id = artifactStore.activeArtifactId
  if (!id || exporting.value) return
  exporting.value = true
  try {
    const { exportArtifact } = await import('@/api/artifact')
    const result = await exportArtifact(id, 'pdf')
    await downloadFileById(result.file_id, result.filename)
    message.success('PDF 导出成功')
  } catch (e) {
    message.error(`导出失败: ${e}`)
  } finally {
    exporting.value = false
  }
}

function handleCopy() {
  navigator.clipboard.writeText(artifactStore.displayContent)
  message.success('已复制到剪贴板')
}

function handleSubmit() {
  if (instruction.value.trim()) {
    emit('send-instruction', instruction.value.trim())
    instruction.value = ''
  }
}
</script>

<style scoped>
.artifact-peek-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  background: var(--le-bg-light, #f0f4ff);
  border-top: 1px solid var(--le-border-color, #e0e0e0);
  cursor: pointer;
}
.peek-icon { font-size: 16px; }
.peek-title {
  flex: 1;
  font-size: 14px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.peek-arrow { color: #999; font-size: 12px; }

.mobile-artifact-overlay {
  position: fixed;
  inset: 0;
  z-index: 2000;
  background: rgba(0, 0, 0, 0.3);
}

.mobile-artifact-drawer {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 55vh;
  background: #fff;
  border-radius: 16px 16px 0 0;
  display: flex;
  flex-direction: column;
  transition: height 0.3s ease;
}
.mobile-artifact-drawer.is-fullscreen {
  height: 100vh;
  border-radius: 0;
}

.drawer-handle {
  display: flex;
  justify-content: center;
  padding: 8px 0 4px;
  cursor: pointer;
}
.handle-bar {
  width: 36px;
  height: 4px;
  border-radius: 2px;
  background: #ddd;
}

.drawer-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 12px 8px;
  border-bottom: 1px solid #eee;
}
.drawer-title {
  flex: 1;
  font-size: 14px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.drawer-actions {
  display: flex;
  gap: 4px;
}
.drawer-btn {
  padding: 4px 8px;
  border: none;
  background: #f5f5f5;
  border-radius: 4px;
  font-size: 12px;
  cursor: pointer;
}

.drawer-body {
  flex: 1;
  overflow-y: auto;
  padding: 12px 16px;
  font-size: 15px;
  line-height: 1.8;
}

.drawer-footer {
  border-top: 1px solid #eee;
  padding: 8px 12px;
  padding-bottom: max(8px, env(safe-area-inset-bottom));
}
.footer-export {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}
.export-btn {
  padding: 4px 12px;
  border: 1px solid #ddd;
  border-radius: 4px;
  background: #fff;
  font-size: 12px;
  cursor: pointer;
}
.footer-input {
  display: flex;
  gap: 8px;
}
.footer-input input {
  flex: 1;
  padding: 8px 12px;
  border: 1px solid #ddd;
  border-radius: 8px;
  font-size: 14px;
  outline: none;
}
.footer-input input:focus {
  border-color: #2080f0;
}
.send-btn {
  width: 36px;
  height: 36px;
  border: none;
  border-radius: 8px;
  background: #2080f0;
  color: #fff;
  cursor: pointer;
  font-size: 14px;
}
.send-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* Transition */
.drawer-enter-active, .drawer-leave-active {
  transition: opacity 0.3s ease;
}
.drawer-enter-active .mobile-artifact-drawer,
.drawer-leave-active .mobile-artifact-drawer {
  transition: transform 0.3s ease;
}
.drawer-enter-from, .drawer-leave-to {
  opacity: 0;
}
.drawer-enter-from .mobile-artifact-drawer,
.drawer-leave-to .mobile-artifact-drawer {
  transform: translateY(100%);
}
</style>
