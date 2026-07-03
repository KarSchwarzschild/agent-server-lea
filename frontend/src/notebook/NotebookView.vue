<template>
  <div class="notebook-view">
    <n-layout has-sider style="height: calc(100vh - 82px)">
      <!-- Left: source list -->
      <n-layout-sider bordered :width="280" style="overflow-y: auto">
        <SourceList
          :sources="sources"
          :loading="loading"
          :active-id="currentSource?.id ?? null"
          :selected-ids="selectedSourceIds"
          :selected-k-b-doc-ids="selectedKBDocIds"
          :selected-k-b-ids="selectedKBIds"
          @select="handleSelect"
          @delete="handleDelete"
          @upload="showUpload = true"
          @toggle-check="handleToggleCheck"
          @toggle-k-b-doc="handleToggleKBDoc"
          @toggle-k-b="handleToggleKB"
          @clear-selection="clearAll"
        />
      </n-layout-sider>

      <!-- Center: chat -->
      <n-layout>
        <ChatPanel
          :source-ids="selectedSourceIds"
          :source-count="selectedSourceIds.length + selectedKBDocIds.length"
          :kb-ids="selectedKBIds"
          :document-ids="allDocumentIds"
          @jump-to-source="handleJumpToSource"
        />
      </n-layout>

      <!-- Right: studio + preview -->
      <n-layout-sider bordered :width="320" style="overflow-y: auto">
        <StudioPanel
          :preview-source="currentSource"
          @tool-click="handleToolClick"
          @close-preview="currentSource = null"
        />
      </n-layout-sider>
    </n-layout>

    <UploadDialog
      v-model:show="showUpload"
      :upload-fn="uploadFile"
      @uploaded="handleUploaded"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { NLayout, NLayoutSider, useMessage } from 'naive-ui'
import SourceList from './components/SourceList.vue'
import ChatPanel from './components/ChatPanel.vue'
import StudioPanel from './components/StudioPanel.vue'
import UploadDialog from './components/UploadDialog.vue'
import { useNotebook } from './composables/useNotebook'

const message = useMessage()
const { sources, loading, currentSource, fetchSources, fetchSourceDetail, uploadFile, pollStatus, deleteSource } = useNotebook()

const showUpload = ref(false)
const selectedSourceIds = ref<number[]>([])
const selectedKBDocIds = ref<number[]>([])
const selectedKBIds = ref<number[]>([])

const allDocumentIds = computed(() => [...selectedSourceIds.value, ...selectedKBDocIds.value])

function handleToggleCheck(id: number) {
  const idx = selectedSourceIds.value.indexOf(id)
  if (idx >= 0) {
    selectedSourceIds.value.splice(idx, 1)
  } else {
    selectedSourceIds.value.push(id)
  }
}

function handleToggleKBDoc(id: number) {
  const idx = selectedKBDocIds.value.indexOf(id)
  if (idx >= 0) {
    selectedKBDocIds.value.splice(idx, 1)
  } else {
    selectedKBDocIds.value.push(id)
  }
}

function handleToggleKB(id: number, checked: boolean) {
  if (checked) {
    if (!selectedKBIds.value.includes(id)) {
      selectedKBIds.value.push(id)
    }
  } else {
    selectedKBIds.value = selectedKBIds.value.filter(i => i !== id)
  }
}

function clearAll() {
  selectedSourceIds.value = []
  selectedKBDocIds.value = []
  selectedKBIds.value = []
}

async function handleSelect(id: number) {
  try {
    await fetchSourceDetail(id)
  } catch {
    message.error('获取文档详情失败')
  }
}

async function handleDelete(id: number) {
  try {
    await deleteSource(id)
    selectedSourceIds.value = selectedSourceIds.value.filter(i => i !== id)
    message.success('已删除')
  } catch {
    message.error('删除失败')
  }
}

function handleJumpToSource(source: any) {
  if (source.source_id) {
    fetchSourceDetail(source.source_id)
  }
}

function handleToolClick(key: string) {
  message.info(`${key} 功能即将上线`)
}

async function handleUploaded(source: any) {
  await fetchSources()

  if (source.status === 'processing') {
    const finalStatus = await pollStatus(source.id)
    if (finalStatus === 'ready') {
      message.success(`${source.filename} 解析完成`)
    } else {
      message.error(`${source.filename} 解析失败`)
    }
    await fetchSources()
  }
}

onMounted(fetchSources)
</script>

<style scoped>
.notebook-view {
  height: 100%;
}
</style>
