<template>
  <div class="source-list">
    <!-- 我的文档 -->
    <div class="list-header">
      <span class="title">我的文档</span>
      <n-button size="small" type="primary" @click="$emit('upload')">上传</n-button>
    </div>

    <div v-if="totalSelected > 0" class="selection-bar">
      <n-text depth="2" style="font-size: 12px">已选 {{ totalSelected }} 篇用于对话</n-text>
      <n-button text size="tiny" @click="$emit('clearSelection')">清除</n-button>
    </div>

    <n-spin :show="loading" style="min-height: 80px">
      <div v-if="sources.length === 0 && !loading" class="empty-hint">
        <n-text depth="3">暂无文档，点击上传开始</n-text>
      </div>

      <div
        v-for="s in sources"
        :key="s.id"
        class="source-item"
        :class="{ active: s.id === activeId }"
        @click="$emit('select', s.id)"
      >
        <div class="source-info">
          <n-checkbox
            :checked="selectedIds.includes(s.id)"
            :disabled="s.status !== 'ready'"
            @click.stop
            @update:checked="$emit('toggleCheck', s.id)"
          />
          <span class="filename">{{ s.filename }}</span>
          <n-tag :type="statusType(s.status)" size="tiny" round>{{ statusLabel(s.status) }}</n-tag>
        </div>
        <div class="source-meta">
          <n-text depth="3" style="font-size: 12px">
            {{ formatSize(s.file_size) }}
            <template v-if="s.created_at"> &middot; {{ formatDate(s.created_at) }}</template>
          </n-text>
          <n-button text type="error" size="tiny" @click.stop="$emit('delete', s.id)">删除</n-button>
        </div>
      </div>
    </n-spin>

    <!-- 知识库 -->
    <div class="list-header kb-header">
      <span class="title">知识库</span>
    </div>

    <n-spin :show="kbLoading" style="min-height: 40px">
      <div v-if="kbList.length === 0 && !kbLoading" class="empty-hint">
        <n-text depth="3">暂无可用知识库</n-text>
      </div>

      <div v-for="kb in kbList" :key="kb.id" class="kb-group">
        <div class="kb-row" @click="toggleExpand(kb.id)">
          <n-checkbox
            :checked="isKBFullyChecked(kb.id)"
            :indeterminate="isKBIndeterminate(kb.id)"
            @click.stop
            @update:checked="(v: boolean) => handleKBCheck(kb.id, v)"
          />
          <span class="kb-name">{{ kb.name }}</span>
          <n-tag size="tiny" :bordered="false">{{ kb.document_count }}篇</n-tag>
          <n-icon
            :component="expanded.has(kb.id) ? ChevronUp : ChevronDown"
            size="14"
            style="margin-left: auto; cursor: pointer"
          />
        </div>

        <div v-if="expanded.has(kb.id)" class="kb-docs">
          <div v-if="loadingKB.has(kb.id)" style="padding: 6px 32px">
            <n-spin size="tiny" />
          </div>
          <div
            v-else
            v-for="doc in kbDocs.get(kb.id) || []"
            :key="doc.id"
            class="kb-doc-item"
          >
            <n-checkbox
              :checked="selectedKBDocIds.includes(doc.id)"
              :disabled="doc.status !== 'ready'"
              @update:checked="$emit('toggleKBDoc', doc.id)"
            />
            <span class="doc-name">{{ doc.filename }}</span>
          </div>
        </div>
      </div>
    </n-spin>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { NButton, NTag, NText, NSpin, NCheckbox, NIcon } from 'naive-ui'
import { ChevronUp, ChevronDown } from '@vicons/ionicons5'
import type { Source } from '../composables/useNotebook'
import request from '../../utils/request'

interface KBItem { id: number; name: string; document_count: number; visibility: string }
interface KBDoc { id: number; filename: string; status: string }

const props = defineProps<{
  sources: Source[]
  loading: boolean
  activeId: number | null
  selectedIds: number[]
  selectedKBDocIds: number[]
  selectedKBIds: number[]
}>()

const emit = defineEmits<{
  select: [id: number]
  delete: [id: number]
  upload: []
  toggleCheck: [id: number]
  toggleKBDoc: [id: number]
  toggleKB: [id: number, checked: boolean]
  clearSelection: []
}>()

const totalSelected = computed(() => props.selectedIds.length + props.selectedKBDocIds.length)

const kbList = ref<KBItem[]>([])
const kbLoading = ref(false)
const expanded = reactive(new Set<number>())
const loadingKB = reactive(new Set<number>())
const kbDocs = reactive(new Map<number, KBDoc[]>())

function isKBFullyChecked(kbId: number): boolean {
  return props.selectedKBIds.includes(kbId)
}

function isKBIndeterminate(kbId: number): boolean {
  if (props.selectedKBIds.includes(kbId)) return false
  const docs = kbDocs.get(kbId)
  if (!docs || docs.length === 0) return false
  const count = docs.filter(d => props.selectedKBDocIds.includes(d.id)).length
  return count > 0 && count < docs.length
}

function handleKBCheck(kbId: number, checked: boolean) {
  emit('toggleKB', kbId, checked)
}

async function toggleExpand(kbId: number) {
  if (expanded.has(kbId)) {
    expanded.delete(kbId)
  } else {
    expanded.add(kbId)
    if (!kbDocs.has(kbId)) {
      await fetchKBDocs(kbId)
    }
  }
}

async function fetchKBList() {
  kbLoading.value = true
  try {
    const { data } = await request.get('/api/knowledge-bases')
    // Exclude personal KBs (those show in "我的文档" already)
    kbList.value = (data.knowledge_bases || []).filter(
      (kb: KBItem) => kb.visibility !== 'personal'
    )
  } finally {
    kbLoading.value = false
  }
}

async function fetchKBDocs(kbId: number) {
  loadingKB.add(kbId)
  try {
    const { data } = await request.get(`/api/knowledge-bases/${kbId}/documents`)
    kbDocs.set(kbId, (data.documents || []).map((d: any) => ({
      id: d.id, filename: d.filename, status: d.status,
    })))
  } finally {
    loadingKB.delete(kbId)
  }
}

function statusType(status: string) {
  if (status === 'ready') return 'success'
  if (status === 'processing') return 'info'
  if (status === 'failed') return 'error'
  return 'default'
}

function statusLabel(status: string) {
  const map: Record<string, string> = { ready: '已就绪', processing: '解析中', failed: '失败' }
  return map[status] || status
}

function formatSize(bytes: number) {
  if (bytes < 1024) return bytes + 'B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + 'KB'
  return (bytes / (1024 * 1024)).toFixed(1) + 'MB'
}

function formatDate(d: string) {
  return new Date(d).toLocaleDateString()
}

onMounted(fetchKBList)
</script>

<style scoped>
.source-list {
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
}

.list-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  border-bottom: 1px solid var(--n-border-color);
}

.kb-header {
  border-top: 1px solid var(--n-border-color);
  margin-top: 8px;
}

.list-header .title {
  font-weight: 600;
  font-size: 15px;
}

.empty-hint {
  padding: 20px 16px;
  text-align: center;
}

.source-item {
  padding: 10px 16px;
  cursor: pointer;
  border-bottom: 1px solid var(--n-border-color);
  transition: background 0.15s;
}

.source-item:hover {
  background: var(--n-color-hover);
}

.source-item.active {
  background: var(--n-color-target);
}

.selection-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 16px;
  background: var(--n-color-target);
  border-bottom: 1px solid var(--n-border-color);
}

.source-info {
  display: flex;
  align-items: center;
  gap: 8px;
}

.filename {
  font-size: 14px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}

.source-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 4px;
}

.kb-group {
  border-bottom: 1px solid var(--n-border-color);
}

.kb-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  cursor: pointer;
}

.kb-row:hover {
  background: var(--n-color-hover);
}

.kb-name {
  font-size: 13px;
  font-weight: 500;
}

.kb-docs {
  padding-left: 16px;
  border-top: 1px solid #f5f5f5;
}

.kb-doc-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 16px;
}

.kb-doc-item:hover {
  background: #fafafa;
}

.doc-name {
  font-size: 12px;
  color: #555;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
