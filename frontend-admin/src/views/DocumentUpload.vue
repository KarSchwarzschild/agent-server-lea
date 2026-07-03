<script setup lang="ts">
/**
 * 公共知识库文件上传页面 - 支持单文件上传和ZIP批量导入
 */
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import {
  NCard,
  NButton,
  NUpload,
  NUploadDragger,
  NIcon,
  NTag,
  NProgress,
  NTabs,
  NTabPane,
  NAlert,
  NCollapse,
  NCollapseItem,
  useMessage,
  type UploadFileInfo,
} from 'naive-ui'
import { CloudUploadOutline, DocumentOutline, ArrowBackOutline, FolderOpenOutline } from '@vicons/ionicons5'
import { uploadDocument, type Document } from '../api/documents'
import { createBatchImport, pollImportProgress, type ImportTaskStatus, type ImportResultsResponse } from '../api/import'

const router = useRouter()
const message = useMessage()

// 当前Tab
const activeTab = ref('single')

// ===== 单文件上传 =====
const fileList = ref<UploadFileInfo[]>([])
const uploading = ref(false)

interface UploadItem {
  name: string
  file: File
  status: 'pending' | 'uploading' | 'success' | 'error'
  progress: number
  result?: Document
  error?: string
}

const uploadList = ref<UploadItem[]>([])

// 文件变化
function handleChange(list: UploadFileInfo[]) {
  fileList.value = list.filter(f => {
    if (f.file && f.file.size > 50 * 1024 * 1024) {
      message.error(`文件 ${f.name} 超过50MB限制`)
      return false
    }
    return true
  })
}

// 开始单文件上传
async function handleUpload() {
  if (fileList.value.length === 0) {
    message.warning('请先选择文件')
    return
  }

  uploading.value = true

  // 初始化上传列表
  uploadList.value = fileList.value.map(f => ({
    name: f.name,
    file: f.file!,
    status: 'pending' as const,
    progress: 0
  }))

  let successCount = 0
  let errorCount = 0

  // 逐个上传
  for (const item of uploadList.value) {
    item.status = 'uploading'
    item.progress = 30

    try {
      const result = await uploadDocument(item.file)
      item.status = 'success'
      item.progress = 100
      item.result = result
      successCount++
    } catch (error: unknown) {
      item.status = 'error'
      const err = error as { response?: { data?: { detail?: string } } }
      item.error = err?.response?.data?.detail || '上传失败'
      errorCount++
    }
  }

  uploading.value = false

  if (successCount > 0) {
    message.success(`成功上传 ${successCount} 个文件`)
  }
  if (errorCount > 0) {
    message.warning(`${errorCount} 个文件上传失败`)
  }

  // 清空已选文件
  if (successCount === uploadList.value.length) {
    fileList.value = []
  }
}

// ===== 批量导入（ZIP）=====
const zipFile = ref<File | null>(null)
const batchUploading = ref(false)
const batchTaskId = ref('')
const batchProgress = ref<ImportTaskStatus | null>(null)
const batchResults = ref<ImportResultsResponse | null>(null)
const batchError = ref('')

// 选择ZIP文件
function handleZipChange(options: { file: UploadFileInfo; fileList: UploadFileInfo[] }) {
  const file = options.file
  if (file.file) {
    if (!file.name.toLowerCase().endsWith('.zip')) {
      message.error('请选择ZIP格式的压缩包')
      return
    }
    if (file.file.size > 200 * 1024 * 1024) {
      message.error('ZIP文件不能超过200MB')
      return
    }
    zipFile.value = file.file
  }
}

// 开始批量导入
async function handleBatchImport() {
  if (!zipFile.value) {
    message.warning('请先选择ZIP文件')
    return
  }

  batchUploading.value = true
  batchError.value = ''
  batchProgress.value = null
  batchResults.value = null

  try {
    // 创建导入任务
    const response = await createBatchImport(zipFile.value)
    batchTaskId.value = response.task_id
    message.success('导入任务已创建')

    // 开始轮询进度
    pollImportProgress(
      response.task_id,
      (status) => {
        batchProgress.value = status
      },
      (results) => {
        batchResults.value = results
        batchUploading.value = false
        if (results.status === 'completed') {
          message.success(`导入完成: 成功${results.success_count}个, 失败${results.failed_count}个`)
        } else {
          message.error('导入失败')
        }
      },
      (error) => {
        batchError.value = error
        batchUploading.value = false
        message.error(error)
      },
      1000
    )
  } catch (error: unknown) {
    const err = error as { response?: { data?: { detail?: string } } }
    batchError.value = err?.response?.data?.detail || '创建导入任务失败'
    batchUploading.value = false
    message.error(batchError.value)
  }
}

// 重置批量导入
function resetBatchImport() {
  zipFile.value = null
  batchTaskId.value = ''
  batchProgress.value = null
  batchResults.value = null
  batchError.value = ''
}

// 计算进度百分比
const batchProgressPercent = computed(() => {
  if (!batchProgress.value) return 0
  if (batchProgress.value.total_files === 0) return 0
  return Math.round((batchProgress.value.processed_files / batchProgress.value.total_files) * 100)
})

// 成功的结果
const successResults = computed(() => {
  if (!batchResults.value) return []
  return batchResults.value.results.filter(r => r.status === 'success')
})

// 失败的结果
const failedResults = computed(() => {
  if (!batchResults.value) return []
  return batchResults.value.results.filter(r => r.status === 'failed')
})

// 跳过的结果
const skippedResults = computed(() => {
  if (!batchResults.value) return []
  return batchResults.value.results.filter(r => r.status === 'skipped')
})

// 返回列表
function goBack() {
  router.push('/documents')
}
</script>

<template>
  <div class="upload-page">
    <n-card>
      <template #header>
        <div class="card-header">
          <span>上传文件</span>
          <n-button text @click="goBack">
            <template #icon>
              <n-icon><ArrowBackOutline /></n-icon>
            </template>
            返回列表
          </n-button>
        </div>
      </template>

      <n-tabs v-model:value="activeTab" type="segment" animated>
        <!-- 单文件上传 Tab -->
        <n-tab-pane name="single" tab="单文件上传">
          <div class="tab-content">
            <n-upload
              multiple
              directory-dnd
              :max="10"
              :default-upload="false"
              :file-list="fileList"
              @update:file-list="handleChange"
              accept=".txt,.pdf,.docx,.md,.xlsx,.pptx"
            >
              <n-upload-dragger>
                <div class="upload-area">
                  <n-icon size="48" :depth="3">
                    <CloudUploadOutline />
                  </n-icon>
                  <p class="upload-text">将文件拖到此处，或点击上传</p>
                  <p class="upload-hint">支持 txt、pdf、docx、md、xlsx、pptx 格式，单个文件不超过 50MB</p>
                </div>
              </n-upload-dragger>
            </n-upload>

            <!-- 上传进度 -->
            <div v-if="uploadList.length > 0" class="upload-progress">
              <h4>上传进度</h4>
              <div v-for="item in uploadList" :key="item.name" class="upload-item">
                <div class="upload-item-info">
                  <n-icon><DocumentOutline /></n-icon>
                  <span class="filename">{{ item.name }}</span>
                  <n-tag v-if="item.status === 'success'" type="success" size="small">
                    上传成功
                  </n-tag>
                  <n-tag v-else-if="item.status === 'error'" type="error" size="small">
                    上传失败
                  </n-tag>
                  <n-tag v-else-if="item.status === 'uploading'" type="warning" size="small">
                    上传中...
                  </n-tag>
                  <n-tag v-else type="default" size="small">
                    待上传
                  </n-tag>
                </div>
                <n-progress
                  v-if="item.status === 'uploading'"
                  type="line"
                  :percentage="item.progress"
                  :height="6"
                  :border-radius="4"
                />
                <div v-if="item.status === 'success' && item.result" class="upload-result">
                  <span>分类: {{ item.result.category || '未识别' }}</span>
                  <span>方法: {{ item.result.methodology || '未识别' }}</span>
                </div>
                <div v-if="item.status === 'error'" class="upload-error">
                  {{ item.error }}
                </div>
              </div>
            </div>

            <!-- 上传按钮 -->
            <div class="upload-actions">
              <n-button
                type="primary"
                size="large"
                :loading="uploading"
                :disabled="fileList.length === 0"
                @click="handleUpload"
              >
                <template #icon>
                  <n-icon><CloudUploadOutline /></n-icon>
                </template>
                开始上传 ({{ fileList.length }} 个文件)
              </n-button>
            </div>
          </div>
        </n-tab-pane>

        <!-- 批量导入 Tab -->
        <n-tab-pane name="batch" tab="批量导入 (ZIP)">
          <div class="tab-content">
            <n-alert type="info" title="批量导入说明" style="margin-bottom: 20px">
              <ul style="margin: 0; padding-left: 20px;">
                <li>将多个文件打包成ZIP压缩包上传</li>
                <li>支持格式：txt、pdf、docx、md、xlsx、pptx</li>
                <li>ZIP文件大小不超过200MB，单个文件不超过50MB</li>
                <li>系统会自动解压并逐个处理文件</li>
              </ul>
            </n-alert>

            <!-- 未开始导入 -->
            <template v-if="!batchTaskId">
              <n-upload
                :max="1"
                :default-upload="false"
                accept=".zip"
                @change="handleZipChange"
              >
                <n-upload-dragger>
                  <div class="upload-area">
                    <n-icon size="48" :depth="3">
                      <FolderOpenOutline />
                    </n-icon>
                    <p class="upload-text">
                      {{ zipFile ? zipFile.name : '将ZIP文件拖到此处，或点击选择' }}
                    </p>
                    <p class="upload-hint">仅支持 ZIP 格式，最大 200MB</p>
                  </div>
                </n-upload-dragger>
              </n-upload>

              <div class="upload-actions">
                <n-button
                  type="primary"
                  size="large"
                  :loading="batchUploading"
                  :disabled="!zipFile"
                  @click="handleBatchImport"
                >
                  <template #icon>
                    <n-icon><CloudUploadOutline /></n-icon>
                  </template>
                  开始批量导入
                </n-button>
              </div>
            </template>

            <!-- 导入进行中 -->
            <template v-else-if="batchUploading || (batchProgress && !batchResults)">
              <div class="batch-progress">
                <h3>正在导入中...</h3>
                <n-progress
                  type="line"
                  :percentage="batchProgressPercent"
                  :height="20"
                  :border-radius="10"
                  indicator-placement="inside"
                />
                <div class="progress-detail">
                  <p>当前文件: {{ batchProgress?.current_file || '准备中...' }}</p>
                  <p>
                    进度: {{ batchProgress?.processed_files || 0 }} / {{ batchProgress?.total_files || 0 }}
                    (成功: {{ batchProgress?.success_count || 0 }},
                    失败: {{ batchProgress?.failed_count || 0 }},
                    跳过: {{ batchProgress?.skipped_count || 0 }})
                  </p>
                </div>
              </div>
            </template>

            <!-- 导入完成 -->
            <template v-else-if="batchResults">
              <div class="batch-results">
                <n-alert
                  :type="batchResults.status === 'completed' ? 'success' : 'error'"
                  :title="batchResults.status === 'completed' ? '导入完成' : '导入失败'"
                  style="margin-bottom: 20px"
                >
                  共处理 {{ batchResults.total_files }} 个文件，
                  成功 {{ batchResults.success_count }} 个，
                  失败 {{ batchResults.failed_count }} 个，
                  跳过 {{ batchResults.skipped_count }} 个
                </n-alert>

                <n-collapse>
                  <n-collapse-item v-if="successResults.length > 0" :title="`成功 (${successResults.length})`" name="success">
                    <div v-for="item in successResults" :key="item.filename" class="result-item success">
                      <n-icon><DocumentOutline /></n-icon>
                      <span class="filename">{{ item.filename }}</span>
                      <n-tag v-if="item.category" size="small">{{ item.category }}</n-tag>
                      <n-tag v-if="item.is_relevant === false" size="small" type="warning">非相关文献</n-tag>
                    </div>
                  </n-collapse-item>

                  <n-collapse-item v-if="failedResults.length > 0" :title="`失败 (${failedResults.length})`" name="failed">
                    <div v-for="item in failedResults" :key="item.filename" class="result-item failed">
                      <n-icon><DocumentOutline /></n-icon>
                      <span class="filename">{{ item.filename }}</span>
                      <span class="reason">{{ item.reason }}</span>
                    </div>
                  </n-collapse-item>

                  <n-collapse-item v-if="skippedResults.length > 0" :title="`跳过 (${skippedResults.length})`" name="skipped">
                    <div v-for="item in skippedResults" :key="item.filename" class="result-item skipped">
                      <n-icon><DocumentOutline /></n-icon>
                      <span class="filename">{{ item.filename }}</span>
                      <span class="reason">{{ item.reason }}</span>
                    </div>
                  </n-collapse-item>
                </n-collapse>

                <div class="upload-actions">
                  <n-button type="primary" @click="resetBatchImport">
                    继续导入
                  </n-button>
                  <n-button @click="goBack">
                    返回文件列表
                  </n-button>
                </div>
              </div>
            </template>

            <!-- 错误提示 -->
            <template v-if="batchError && !batchUploading">
              <n-alert type="error" :title="batchError" style="margin-top: 20px">
                <n-button size="small" @click="resetBatchImport">重试</n-button>
              </n-alert>
            </template>
          </div>
        </n-tab-pane>
      </n-tabs>
    </n-card>
  </div>
</template>

<style scoped>
.upload-page {
  max-width: 900px;
  margin: 0 auto;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.tab-content {
  padding: 20px 0;
}

.upload-area {
  padding: 40px;
  text-align: center;
}

.upload-text {
  margin: 16px 0 8px;
  font-size: 16px;
  color: #333;
}

.upload-hint {
  font-size: 12px;
  color: #999;
}

.upload-progress {
  margin: 24px 0;
  padding: 20px;
  background: #f5f7fa;
  border-radius: 8px;
}

.upload-progress h4 {
  margin: 0 0 16px 0;
  font-size: 14px;
  color: #333;
}

.upload-item {
  padding: 12px;
  background: white;
  border-radius: 4px;
  margin-bottom: 8px;
}

.upload-item-info {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.filename {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.upload-result {
  font-size: 12px;
  color: #18a058;
  display: flex;
  gap: 20px;
  margin-top: 8px;
}

.upload-error {
  font-size: 12px;
  color: #d03050;
  margin-top: 8px;
}

.upload-actions {
  text-align: center;
  margin-top: 24px;
  display: flex;
  justify-content: center;
  gap: 12px;
}

/* 批量导入样式 */
.batch-progress {
  text-align: center;
  padding: 40px;
}

.batch-progress h3 {
  margin-bottom: 24px;
  color: #333;
}

.progress-detail {
  margin-top: 16px;
  color: #666;
  font-size: 14px;
}

.progress-detail p {
  margin: 8px 0;
}

.batch-results {
  padding: 20px 0;
}

.result-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 4px;
  margin-bottom: 4px;
}

.result-item.success {
  background: #f0fdf4;
}

.result-item.failed {
  background: #fef2f2;
}

.result-item.skipped {
  background: #fffbeb;
}

.result-item .filename {
  flex: 1;
  font-size: 13px;
}

.result-item .reason {
  font-size: 12px;
  color: #999;
}
</style>
