<script setup lang="ts">
/**
 * 个人知识库文档选择器
 *
 * 用于从个人知识库多选文档导入为聊天附件
 */
import { ref, computed, onMounted } from 'vue'
import {
  NModal,
  NInput,
  NButton,
  NCheckbox,
  NCheckboxGroup,
  NSpin,
  NEmpty,
  NTag,
  NIcon,
  useMessage,
} from 'naive-ui'
import { SearchOutline } from '@vicons/ionicons5'
import { getDocuments, type UserDocument } from '../../api/user-kb'

const props = defineProps<{
  show: boolean
}>()

const emit = defineEmits<{
  (e: 'update:show', value: boolean): void
  (e: 'confirm', docIds: string[]): void
}>()

const message = useMessage()
const loading = ref(false)
const documents = ref<UserDocument[]>([])
const selectedIds = ref<string[]>([])
const searchText = ref('')

// 过滤后的文档列表
const filteredDocs = computed(() => {
  if (!searchText.value.trim()) return documents.value
  const keyword = searchText.value.toLowerCase()
  return documents.value.filter(
    doc => (doc.filename || '').toLowerCase().includes(keyword) ||
           (doc.title || '').toLowerCase().includes(keyword)
  )
})

// 文件类型图标
const fileTypeIcon = (type?: string): string => {
  const icons: Record<string, string> = {
    pdf: 'PDF',
    docx: 'DOC',
    doc: 'DOC',
    xlsx: 'XLS',
    xls: 'XLS',
    pptx: 'PPT',
    txt: 'TXT',
    md: 'MD',
    csv: 'CSV',
  }
  return icons[(type || '').toLowerCase()] || 'FILE'
}

// 格式化文件大小
const formatSize = (kb: number): string => {
  if (kb < 1024) return `${kb} KB`
  return `${(kb / 1024).toFixed(1)} MB`
}

const loadDocuments = async () => {
  loading.value = true
  try {
    const res = await getDocuments({ page_size: 100, upload_status: 'completed' })
    documents.value = res.items
  } catch (error) {
    console.error('Failed to load documents:', error)
    message.error('加载文档列表失败')
  } finally {
    loading.value = false
  }
}

const handleConfirm = () => {
  if (selectedIds.value.length === 0) {
    message.warning('请至少选择一个文档')
    return
  }
  emit('confirm', [...selectedIds.value])
  emit('update:show', false)
  selectedIds.value = []
  searchText.value = ''
}

let lastClickIndex = -1

const toggleDoc = (docId: string, event?: MouseEvent) => {
  const docs = filteredDocs.value
  const clickIndex = docs.findIndex(d => d.doc_id === docId)

  if (event?.shiftKey && lastClickIndex >= 0 && lastClickIndex !== clickIndex) {
    // Shift+Click：范围选择/取消
    const from = Math.min(lastClickIndex, clickIndex)
    const to = Math.max(lastClickIndex, clickIndex)
    const isSelecting = !selectedIds.value.includes(docId)
    for (let i = from; i <= to; i++) {
      const id = docs[i].doc_id
      const exists = selectedIds.value.includes(id)
      if (isSelecting && !exists) {
        selectedIds.value.push(id)
      } else if (!isSelecting && exists) {
        selectedIds.value.splice(selectedIds.value.indexOf(id), 1)
      }
    }
  } else {
    // 普通点击：切换单个
    const idx = selectedIds.value.indexOf(docId)
    if (idx === -1) {
      selectedIds.value.push(docId)
    } else {
      selectedIds.value.splice(idx, 1)
    }
  }

  lastClickIndex = clickIndex
}

const handleClose = () => {
  emit('update:show', false)
  selectedIds.value = []
  searchText.value = ''
}

onMounted(() => {
  if (props.show) {
    loadDocuments()
  }
})

// 当弹窗打开时加载
const onAfterEnter = () => {
  loadDocuments()
}
</script>

<template>
  <n-modal
    :show="show"
    @update:show="emit('update:show', $event)"
    preset="card"
    title="从知识库选择文档"
    :style="{ width: '520px', maxWidth: '95vw' }"
    @after-enter="onAfterEnter"
  >
    <!-- 搜索框 -->
    <div class="picker-search">
      <n-input
        v-model:value="searchText"
        placeholder="搜索文档..."
        clearable
        size="small"
      >
        <template #prefix>
          <n-icon :component="SearchOutline" />
        </template>
      </n-input>
      <div class="picker-hint">
        找不到需要的文档？前往 <a href="/qa/user-kb" target="_blank">个人知识库</a> 上传
      </div>
    </div>

    <!-- 文档列表 -->
    <div class="picker-body">
      <n-spin v-if="loading" class="picker-loading" />

      <n-empty
        v-else-if="documents.length === 0"
        description="暂无可用文档，请先在个人知识库上传文件"
        class="picker-empty"
      />

      <n-empty
        v-else-if="filteredDocs.length === 0"
        description="未找到匹配的文档"
        class="picker-empty"
      />

      <n-checkbox-group v-else v-model:value="selectedIds">
        <div class="doc-list">
          <div
            v-for="doc in filteredDocs"
            :key="doc.doc_id"
            class="doc-item"
            @click="toggleDoc(doc.doc_id, $event)"
          >
            <n-checkbox :value="doc.doc_id" @click.stop />
            <div class="doc-info">
              <div class="doc-name">{{ doc.filename }}</div>
              <div class="doc-meta">
                <n-tag size="tiny" :bordered="false">{{ fileTypeIcon(doc.file_type) }}</n-tag>
                <span>{{ formatSize(doc.file_size_kb) }}</span>
              </div>
            </div>
          </div>
        </div>
      </n-checkbox-group>
    </div>

    <!-- 底部操作 -->
    <template #footer>
      <div class="picker-footer">
        <span class="picker-count" v-if="selectedIds.length > 0">
          已选 {{ selectedIds.length }} 个文档
        </span>
        <div class="picker-actions">
          <n-button size="small" @click="handleClose">取消</n-button>
          <n-button
            type="primary"
            size="small"
            :disabled="selectedIds.length === 0"
            @click="handleConfirm"
          >
            确认导入
          </n-button>
        </div>
      </div>
    </template>
  </n-modal>
</template>

<style scoped>
.picker-search {
  margin-bottom: 12px;
}
.picker-hint {
  margin-top: 6px;
  font-size: 12px;
  color: #999;
}
.picker-hint a {
  color: #2080f0;
  text-decoration: none;
}
.picker-hint a:hover {
  text-decoration: underline;
}

.picker-body {
  max-height: 360px;
  overflow-y: auto;
}

.picker-loading {
  display: flex;
  justify-content: center;
  padding: 40px 0;
}

.picker-empty {
  padding: 24px 0;
}

.doc-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.doc-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.15s;
}

.doc-item:hover {
  background: #f5f7fa;
}

.doc-info {
  flex: 1;
  min-width: 0;
}

.doc-name {
  font-size: 13px;
  color: #333;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.doc-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 2px;
  font-size: 12px;
  color: #999;
}

.picker-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.picker-count {
  font-size: 13px;
  color: #2080f0;
}

.picker-actions {
  display: flex;
  gap: 8px;
}
</style>
