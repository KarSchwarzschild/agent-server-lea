/**
 * 聊天附件管理 Composable
 *
 * 管理附件状态: 添加/删除/上传进度/验证/粘贴处理/拖拽处理
 */
import { ref, computed } from 'vue'
import type { AttachmentInfo } from '../types/super-agent'
import { uploadChatAttachment, importAttachmentsFromKB } from '../api/superagent'

// 允许的文件类型
const ALLOWED_DOC_TYPES = ['pdf', 'docx', 'xlsx', 'pptx', 'txt', 'md']
const ALLOWED_IMAGE_TYPES = ['jpg', 'jpeg', 'png', 'gif', 'webp']
const ALL_ALLOWED_TYPES = [...ALLOWED_DOC_TYPES, ...ALLOWED_IMAGE_TYPES]
const MAX_FILE_SIZE = 20 * 1024 * 1024 // 20MB
const MAX_ATTACHMENT_COUNT = 5
const MAX_CONCURRENT_UPLOADS = 3

export function useAttachments(getToken: () => string) {
  const attachments = ref<AttachmentInfo[]>([])
  const isDragging = ref(false)
  const dragCounter = ref(0)

  // 就绪的附件 ID 列表
  const readyAttachmentIds = computed(() =>
    attachments.value
      .filter(a => a.status === 'ready')
      .map(a => a.id)
  )

  // 是否有正在上传的附件
  const hasUploading = computed(() =>
    attachments.value.some(a => a.status === 'uploading')
  )

  // 是否可以发送（没有正在上传的）
  const canSend = computed(() => !hasUploading.value)

  // 是否有附件
  const hasAttachments = computed(() => attachments.value.length > 0)

  function getFileExt(filename: string): string {
    return filename.split('.').pop()?.toLowerCase() || ''
  }

  function getFileCategory(ext: string): 'document' | 'image' {
    return ALLOWED_IMAGE_TYPES.includes(ext) ? 'image' : 'document'
  }

  function validateFile(file: File): string | null {
    const ext = getFileExt(file.name)
    if (!ALL_ALLOWED_TYPES.includes(ext)) {
      return `不支持的文件类型: .${ext}`
    }
    if (file.size > MAX_FILE_SIZE) {
      return `文件过大: ${(file.size / 1024 / 1024).toFixed(1)}MB，上限 20MB`
    }
    if (attachments.value.length >= MAX_ATTACHMENT_COUNT) {
      return `最多上传 ${MAX_ATTACHMENT_COUNT} 个附件`
    }
    return null
  }

  async function addFile(file: File): Promise<void> {
    const error = validateFile(file)
    if (error) {
      // 添加一个错误状态的附件用于展示
      const tempId = `temp_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`
      attachments.value.push({
        id: tempId,
        filename: file.name,
        fileCategory: getFileCategory(getFileExt(file.name)),
        fileExt: getFileExt(file.name),
        size: file.size,
        status: 'error',
        errorMessage: error,
      })
      // 3 秒后自动移除错误提示
      setTimeout(() => removeAttachment(tempId), 3000)
      return
    }

    const ext = getFileExt(file.name)
    const category = getFileCategory(ext)
    const tempId = `temp_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`

    // 图片本地预览
    let thumbnailUrl: string | undefined
    if (category === 'image') {
      thumbnailUrl = URL.createObjectURL(file)
    }

    const att: AttachmentInfo = {
      id: tempId,
      filename: file.name,
      fileCategory: category,
      fileExt: ext,
      size: file.size,
      thumbnailUrl,
      uploadProgress: 0,
      status: 'uploading',
    }
    attachments.value.push(att)

    // 上传
    try {
      const token = getToken()
      const result = await uploadChatAttachment(file, token, (percent) => {
        const idx = attachments.value.findIndex(a => a.id === tempId)
        if (idx >= 0) {
          attachments.value[idx].uploadProgress = percent
        }
      })

      // 上传成功，替换 tempId 为真实 ID
      const idx = attachments.value.findIndex(a => a.id === tempId)
      if (idx >= 0) {
        attachments.value[idx].id = result.id
        attachments.value[idx].status = 'ready'
        attachments.value[idx].uploadProgress = 100
        attachments.value[idx].indexStatus = (result.index_status || 'not_supported') as AttachmentInfo['indexStatus']
      }
    } catch (e: unknown) {
      const idx = attachments.value.findIndex(a => a.id === tempId)
      if (idx >= 0) {
        attachments.value[idx].status = 'error'
        attachments.value[idx].errorMessage = e instanceof Error ? e.message : '上传失败'
      }
    }
  }

  async function addFiles(files: FileList | File[]): Promise<void> {
    const fileArray = Array.from(files)
    // 并发限制
    const chunks: File[][] = []
    for (let i = 0; i < fileArray.length; i += MAX_CONCURRENT_UPLOADS) {
      chunks.push(fileArray.slice(i, i + MAX_CONCURRENT_UPLOADS))
    }
    for (const chunk of chunks) {
      await Promise.all(chunk.map(f => addFile(f)))
    }
  }

  function removeAttachment(id: string): void {
    const idx = attachments.value.findIndex(a => a.id === id)
    if (idx >= 0) {
      const att = attachments.value[idx]
      if (att.thumbnailUrl) {
        URL.revokeObjectURL(att.thumbnailUrl)
      }
      attachments.value.splice(idx, 1)
    }
  }

  async function importFromKB(docIds: string[], sessionId?: string): Promise<void> {
    // 检查附件数量限制
    const remaining = MAX_ATTACHMENT_COUNT - attachments.value.length
    if (remaining <= 0) {
      return
    }
    const idsToImport = docIds.slice(0, remaining)

    try {
      const results = await importAttachmentsFromKB({
        doc_ids: idsToImport,
        session_id: sessionId,
      })
      for (const r of results) {
        attachments.value.push({
          id: r.id,
          filename: r.filename,
          fileCategory: r.file_category as 'document' | 'image',
          fileExt: r.file_ext,
          size: r.size,
          status: 'ready',
          sourceType: 'kb_doc',
        })
      }
    } catch (e: unknown) {
      throw e
    }
  }

  function clearAll(): void {
    attachments.value.forEach(a => {
      if (a.thumbnailUrl) URL.revokeObjectURL(a.thumbnailUrl)
    })
    attachments.value = []
  }

  // 粘贴处理
  function handlePaste(event: ClipboardEvent): boolean {
    const items = event.clipboardData?.items
    if (!items) return false

    const imageItems: DataTransferItem[] = []
    for (const item of items) {
      if (item.type.startsWith('image/')) {
        imageItems.push(item)
      }
    }

    if (imageItems.length === 0) return false

    // 有图片 → 作为附件处理
    event.preventDefault()
    for (const item of imageItems) {
      const file = item.getAsFile()
      if (file) {
        // 给粘贴的图片一个默认文件名
        const ext = file.type.split('/')[1] || 'png'
        const namedFile = new File([file], `粘贴图片_${Date.now()}.${ext}`, { type: file.type })
        addFile(namedFile)
      }
    }
    return true
  }

  // 拖拽处理
  function handleDragEnter(e: DragEvent): void {
    e.preventDefault()
    dragCounter.value++
    isDragging.value = true
  }

  function handleDragLeave(e: DragEvent): void {
    e.preventDefault()
    dragCounter.value--
    if (dragCounter.value <= 0) {
      dragCounter.value = 0
      isDragging.value = false
    }
  }

  function handleDragOver(e: DragEvent): void {
    e.preventDefault()
  }

  function handleDrop(e: DragEvent): void {
    e.preventDefault()
    dragCounter.value = 0
    isDragging.value = false

    const files = e.dataTransfer?.files
    if (files && files.length > 0) {
      addFiles(files)
    }
  }

  return {
    attachments,
    readyAttachmentIds,
    hasUploading,
    canSend,
    hasAttachments,
    isDragging,
    addFile,
    addFiles,
    importFromKB,
    removeAttachment,
    clearAll,
    handlePaste,
    handleDragEnter,
    handleDragLeave,
    handleDragOver,
    handleDrop,
  }
}
