<script setup lang="ts">
/**
 * 文档上传组件
 * 支持多文件上传、拖拽上传、逐个串行上传、独立进度显示
 */
import { ref, computed, watch } from 'vue'
import {
  NModal,
  NCard,
  NUpload,
  NUploadDragger,
  NIcon,
  NText,
  NProgress,
  NTag,
  NButton,
  NSpace,
  useMessage,
} from 'naive-ui'
import { CloudUploadOutline, CloseCircleOutline } from '@vicons/ionicons5'
import type { UploadFileInfo } from 'naive-ui'
import { uploadDocument, getDocumentDetail } from '../api/user-kb'
import type { UserDocument } from '../api/user-kb'

interface FileTask {
  id: string
  file: File
  filename: string
  status: 'pending' | 'uploading' | 'polling' | 'success' | 'failed' | 'duplicate'
  percent: number
  doc?: UserDocument
  error?: string
}

const props = defineProps<{
  visible: boolean
}>()

const emit = defineEmits<{
  (e: 'update:visible', val: boolean): void
  (e: 'uploaded', doc: UserDocument): void
}>()

const message = useMessage()
const tasks = ref<FileTask[]>([])
const processing = ref(false)

const ALLOWED_TYPES = ['.pdf', '.docx', '.txt', '.md']
const MAX_SIZE_MB = 100

const hasActiveTasks = computed(() =>
  tasks.value.some(t => t.status === 'uploading' || t.status === 'polling')
)

function handleClose() {
  if (!hasActiveTasks.value) {
    tasks.value = []
    emit('update:visible', false)
  }
}

function removeTask(id: string) {
  const t = tasks.value.find(t => t.id === id)
  if (t && (t.status === 'pending' || t.status === 'success' || t.status === 'failed' || t.status === 'duplicate')) {
    tasks.value = tasks.value.filter(t => t.id !== id)
  }
}

function beforeUpload({ file }: { file: UploadFileInfo }) {
  const ext = '.' + (file.name?.split('.').pop()?.toLowerCase() || '')
  if (!ALLOWED_TYPES.includes(ext)) {
    message.error(`不支持的文件类型: ${file.name}，允许: ${ALLOWED_TYPES.join(', ')}`)
    return false
  }
  const sizeMB = (file.file?.size || 0) / 1024 / 1024
  if (sizeMB > MAX_SIZE_MB) {
    message.error(`${file.name} 超过大小限制（最大 ${MAX_SIZE_MB}MB）`)
    return false
  }
  return true
}

function handleUpload({ file }: { file: UploadFileInfo }) {
  if (!file.file) return
  const task: FileTask = {
    id: file.id || `${Date.now()}-${Math.random()}`,
    file: file.file,
    filename: file.name || file.file.name,
    status: 'pending',
    percent: 0,
  }
  tasks.value.push(task)
  processQueue()
}

async function processQueue() {
  if (processing.value) return
  processing.value = true

  while (true) {
    const next = tasks.value.find(t => t.status === 'pending')
    if (!next) break
    await uploadOne(next)
  }

  processing.value = false
}

async function uploadOne(task: FileTask) {
  task.status = 'uploading'
  task.percent = 0

  try {
    const doc = await uploadDocument(task.file, (percent) => {
      task.percent = percent
    })
    task.doc = doc

    if (doc.upload_status === 'processing') {
      task.status = 'polling'
      await pollStatus(task, doc.doc_id)
    } else {
      task.status = 'success'
      emit('uploaded', doc)
    }
  } catch (error: any) {
    const status = error?.response?.status
    const detail = error?.response?.data?.detail
    if (status === 409) {
      task.status = 'duplicate'
      task.error = typeof detail === 'object' ? detail.message : (detail || '该文件已上传过')
    } else if (status === 413) {
      task.status = 'failed'
      task.error = detail || '存储空间不足'
    } else if (status === 400) {
      task.status = 'failed'
      task.error = detail || '文件格式不正确'
    } else {
      task.status = 'failed'
      task.error = detail || '上传失败'
    }
  }
}

async function pollStatus(task: FileTask, docId: string) {
  let attempts = 0
  const maxAttempts = 60

  while (attempts < maxAttempts) {
    await new Promise((r) => setTimeout(r, 2000))
    attempts++
    try {
      const doc = await getDocumentDetail(docId)
      task.doc = doc

      if (doc.upload_status === 'failed') {
        task.status = 'failed'
        task.error = '上传失败'
        return
      }
      if (doc.upload_status === 'completed') {
        if (!doc.chunk_status || doc.chunk_status === 'completed') {
          task.status = 'success'
          emit('uploaded', doc)
          return
        }
        if (doc.chunk_status === 'failed') {
          task.status = 'success'
          task.error = 'AI 处理失败，可在列表中重试'
          emit('uploaded', doc)
          return
        }
      }
    } catch {
      task.status = 'failed'
      task.error = '状态查询失败'
      return
    }
  }

  // 超时
  task.status = 'success'
  task.error = '处理时间较长，请稍后查看'
  if (task.doc) emit('uploaded', task.doc)
}

function getTaskTagType(task: FileTask) {
  switch (task.status) {
    case 'success': return task.error ? 'warning' : 'success'
    case 'failed': return 'error'
    case 'duplicate': return 'warning'
    default: return 'info'
  }
}

function getTaskTagText(task: FileTask) {
  switch (task.status) {
    case 'pending': return '排队中'
    case 'uploading': return '上传中'
    case 'polling': return '处理中'
    case 'success': return task.error || '完成'
    case 'failed': return task.error || '失败'
    case 'duplicate': return task.error || '已存在'
  }
}
</script>

<template>
  <n-modal :show="props.visible" @update:show="handleClose">
    <n-card
      title="上传文档"
      :bordered="false"
      style="width: 560px; max-width: 95vw"
      closable
      @close="handleClose"
    >
      <n-upload
        multiple
        :custom-request="({ file }: any) => handleUpload({ file })"
        :show-file-list="false"
        :disabled="hasActiveTasks"
        @before-upload="beforeUpload"
        accept=".pdf,.docx,.txt,.md"
      >
        <n-upload-dragger>
          <div style="padding: 20px 0">
            <n-icon :size="48" :depth="3" :component="CloudUploadOutline" />
            <n-text style="display: block; margin-top: 8px; font-size: 16px">
              点击或拖拽文件到此处上传
            </n-text>
            <n-text depth="3" style="display: block; margin-top: 4px">
              支持多文件，PDF、DOCX、TXT、MD，单文件最大 {{ MAX_SIZE_MB }}MB
            </n-text>
          </div>
        </n-upload-dragger>
      </n-upload>

      <!-- 文件任务列表 -->
      <div v-if="tasks.length" style="margin-top: 16px; max-height: 300px; overflow-y: auto">
        <div
          v-for="task in tasks"
          :key="task.id"
          style="display: flex; align-items: center; gap: 8px; padding: 6px 0; border-bottom: 1px solid var(--n-border-color, #efeff5)"
        >
          <span style="flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px">
            {{ task.filename }}
          </span>

          <n-progress
            v-if="task.status === 'uploading'"
            type="line"
            :percentage="task.percent"
            :processing="true"
            style="width: 100px"
            :show-indicator="false"
          />

          <n-tag :type="getTaskTagType(task)" size="small" style="flex-shrink: 0">
            {{ getTaskTagText(task) }}
          </n-tag>

          <n-icon
            v-if="task.status !== 'uploading' && task.status !== 'polling'"
            :component="CloseCircleOutline"
            :size="16"
            style="cursor: pointer; flex-shrink: 0; opacity: 0.5"
            @click="removeTask(task.id)"
          />
        </div>
      </div>

      <template #footer>
        <n-space justify="end">
          <n-button @click="handleClose" :disabled="hasActiveTasks">关闭</n-button>
        </n-space>
      </template>
    </n-card>
  </n-modal>
</template>
