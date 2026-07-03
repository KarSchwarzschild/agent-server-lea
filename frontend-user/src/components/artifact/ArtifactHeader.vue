<template>
  <div class="artifact-header">
    <div class="header-left">
      <VersionNavigator />
      <span class="artifact-title" :title="artifact?.title">
        {{ displayTitle }}
      </span>
      <span v-if="panelMode === 'streaming'" class="streaming-badge">
        生成中
      </span>
    </div>

    <div class="header-actions">
      <!-- 编辑/只读 模式切换 -->
      <n-button
        v-if="panelMode === 'readonly' || panelMode === 'version-browse'"
        size="small" quaternary
        @click="artifactStore.enterEditMode()"
        title="进入编辑模式"
      >
        <template #icon><n-icon><CreateOutline /></n-icon></template>
        编辑
      </n-button>
      <n-button
        v-if="panelMode === 'editing'"
        size="small" type="primary" quaternary
        @click="emit('save-version')"
        title="保存版本 (Ctrl+S)"
      >
        <template #icon><n-icon><SaveOutline /></n-icon></template>
        保存
      </n-button>
      <n-button
        v-if="panelMode === 'editing'"
        size="small" quaternary
        @click="artifactStore.exitEditMode()"
        title="放弃编辑，不保存"
      >
        取消
      </n-button>

      <!-- 导出下拉 -->
      <n-dropdown trigger="click" :options="exportOptions" @select="handleExport">
        <n-button size="small" quaternary>
          <template #icon><n-icon><DownloadOutline /></n-icon></template>
          导出
        </n-button>
      </n-dropdown>

      <!-- 全屏/专注模式 -->
      <n-button size="small" quaternary @click="emit('toggle-fullscreen')" :title="isFullscreen ? '退出专注' : '专注模式'">
        <n-icon size="16"><Contract v-if="isFullscreen" /><Expand v-else /></n-icon>
      </n-button>

      <!-- 关闭 -->
      <n-button size="small" quaternary @click="emit('close')" title="关闭面板">
        <n-icon size="16"><Close /></n-icon>
      </n-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { NButton, NIcon, NDropdown } from 'naive-ui'
import { Close, Contract, Expand, DownloadOutline, SaveOutline, CreateOutline } from '@vicons/ionicons5'
import { useArtifactStore } from '@/stores/artifact'
import VersionNavigator from './VersionNavigator.vue'

const emit = defineEmits<{
  'save-version': []
  'toggle-fullscreen': []
  'close': []
  'export': [format: string]
}>()

const props = defineProps<{
  isFullscreen?: boolean
}>()

const artifactStore = useArtifactStore()
const artifact = computed(() => artifactStore.activeArtifact)
const panelMode = computed(() => artifactStore.panelMode)

const displayTitle = computed(() => {
  // 优先从文档内容提取第一个标题
  const content = artifactStore.displayContent
  if (content) {
    const match = content.match(/^#{1,3}\s+(.+)/m)
    if (match) {
      const title = match[1].trim()
      return title.length > 40 ? title.slice(0, 40) + '...' : title
    }
  }
  // fallback: 用 artifact 标题（截短）
  const raw = artifact.value?.title || '未命名文档'
  return raw.length > 40 ? raw.slice(0, 40) + '...' : raw
})

const exportOptions = [
  {
    label: '导出为 Word',
    key: 'docx-group',
    children: [
      { label: '通用格式', key: 'docx:standard' },
      { label: '行政公文（GB/T 9704）', key: 'docx:official' },
      { label: '内部文件', key: 'docx:internal' },
      { label: '研究报告', key: 'docx:report' },
    ],
  },
  { label: '导出为 PDF', key: 'pdf' },
  { type: 'divider', key: 'd1' },
  { label: '复制 Markdown 源码', key: 'copy-md' },
  { label: '复制纯文本', key: 'copy-text' },
]

function handleExport(key: string) {
  emit('export', key)
}
</script>

<style scoped>
.artifact-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  border-bottom: 1px solid #f3f4f6;
  background: #fafbfc;
  min-height: 48px;
  flex-shrink: 0;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
  min-width: 0;
}

.artifact-title {
  font-size: 14px;
  font-weight: 500;
  color: var(--le-text-primary, #333);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
}

.streaming-badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  font-size: 11px;
  font-weight: 500;
  color: #2080f0;
  background: #eff6ff;
  border-radius: 10px;
  animation: streaming-pulse 1.5s ease-in-out infinite;
  flex-shrink: 0;
}
@keyframes streaming-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}
</style>
