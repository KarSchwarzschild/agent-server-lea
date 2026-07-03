<template>
  <n-modal v-model:show="visible" preset="dialog" title="上传文档" style="width: 480px">
    <n-upload
      :custom-request="handleUpload"
      :max="1"
      accept=".pdf,.txt,.md,.docx"
      directory-dnd
    >
      <n-upload-dragger>
        <div style="padding: 20px 0">
          <p style="font-size: 16px; margin: 0 0 8px">点击或拖拽文件到此处</p>
          <n-text depth="3">支持 PDF / TXT / MD / DOCX，最大 50MB</n-text>
        </div>
      </n-upload-dragger>
    </n-upload>

    <n-progress
      v-if="uploading"
      type="line"
      :percentage="progress"
      :status="progress === 100 ? 'success' : 'default'"
      style="margin-top: 12px"
    />

    <template #action>
      <n-button @click="visible = false" :disabled="uploading">关闭</n-button>
    </template>
  </n-modal>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { NModal, NUpload, NUploadDragger, NText, NProgress, NButton, useMessage } from 'naive-ui'
import type { UploadCustomRequestOptions } from 'naive-ui'

const visible = defineModel<boolean>('show', { default: false })
const emit = defineEmits<{ uploaded: [source: any] }>()

const props = defineProps<{
  uploadFn: (file: File, onProgress?: (pct: number) => void) => Promise<any>
}>()

const message = useMessage()
const uploading = ref(false)
const progress = ref(0)

async function handleUpload({ file }: UploadCustomRequestOptions) {
  if (!file.file) return
  uploading.value = true
  progress.value = 0

  try {
    const source = await props.uploadFn(file.file, (pct) => { progress.value = pct })
    message.success(`${file.name} 上传成功，正在解析...`)
    emit('uploaded', source)
    visible.value = false
  } catch {
    message.error('上传失败')
  } finally {
    uploading.value = false
    progress.value = 0
  }
}
</script>
