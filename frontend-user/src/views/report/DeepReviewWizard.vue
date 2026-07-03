<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useMessage } from 'naive-ui'
import { createDeepReview, type DeepReviewCreateParams } from '../../api/deep-review'
import { getDocuments, type UserDocument } from '../../api/user-kb'
import DocumentUploader from '../../components/DocumentUploader.vue'

const router = useRouter()
const route = useRoute()
const message = useMessage()

// 步骤控制
const currentStep = ref(1)
const mode = ref<'quick' | 'deep'>('deep')

// 表单数据
const formData = ref<DeepReviewCreateParams>({
  topic: '',
  requirements: '',
  report_type: 'literature_review',
  target_words: 5000,
  citation_style: 'apa7',
  language: 'zh',
  literature_sources: {
    public_kb: true,
    user_kb: true,
    academic_search: true,
    manual_docs: [],
  },
})

const submitting = ref(false)

// 手动补充文献
const showDocPicker = ref(false)
const showUploader = ref(false)
const userDocs = ref<UserDocument[]>([])
const selectedDocIds = ref<string[]>([])
const loadingDocs = ref(false)

async function loadUserDocs() {
  loadingDocs.value = true
  try {
    const res = await getDocuments({ page: 1, page_size: 100, status: 'active', upload_status: 'completed' })
    userDocs.value = res.items || []
  } catch {
    userDocs.value = []
  } finally {
    loadingDocs.value = false
  }
}

function toggleDocPicker() {
  showDocPicker.value = !showDocPicker.value
  if (showDocPicker.value && userDocs.value.length === 0) {
    loadUserDocs()
  }
}

function toggleDocSelection(docId: string) {
  const idx = selectedDocIds.value.indexOf(docId)
  if (idx >= 0) {
    selectedDocIds.value.splice(idx, 1)
  } else {
    selectedDocIds.value.push(docId)
  }
  formData.value.literature_sources!.manual_docs = [...selectedDocIds.value]
}

function handleDocUploaded(doc: UserDocument) {
  showUploader.value = false
  userDocs.value.unshift(doc)
  toggleDocSelection(doc.doc_id)
  message.success(`已添加「${doc.filename}」`)
}

// 报告类型选项
const reportTypes = [
  { label: '文献综述', value: 'literature_review' },
  { label: '政策分析', value: 'policy_analysis' },
  { label: '研究报告', value: 'research_report' },
]

const citationStyles = [
  { label: 'APA 7th', value: 'apa7' },
  { label: 'GB/T 7714', value: 'gbt7714' },
  { label: 'Chicago', value: 'chicago' },
]

const languages = [
  { label: '中文', value: 'zh' },
  { label: 'English', value: 'en' },
]

const wordCountOptions = [
  { label: '3000 字', value: 3000 },
  { label: '5000 字', value: 5000 },
  { label: '8000 字', value: 8000 },
  { label: '10000 字', value: 10000 },
]

// 预计耗时
const estimatedTime = computed(() => {
  const words = formData.value.target_words || 5000
  if (words <= 3000) return '3-5 分钟'
  if (words <= 5000) return '5-8 分钟'
  if (words <= 8000) return '8-12 分钟'
  return '12-18 分钟'
})

// 表单校验
const canProceedStep2 = computed(() => {
  return formData.value.topic.trim().length >= 2
})

// 步骤操作
function nextStep() {
  if (currentStep.value === 1) {
    if (mode.value === 'quick') {
      router.push('/review/quick')
      return
    }
    currentStep.value = 2
  } else if (currentStep.value === 2) {
    if (!canProceedStep2.value) {
      message.warning('请输入研究主题')
      return
    }
    currentStep.value = 3
  }
}

function prevStep() {
  if (currentStep.value > 1) {
    currentStep.value--
  }
}

async function handleSubmit() {
  submitting.value = true
  try {
    const result = await createDeepReview(formData.value)
    message.success('任务已创建，正在生成...')
    router.push(`/review/progress/${result.task_id}`)
  } catch (e: any) {
    message.error(e?.response?.data?.detail || '创建失败')
  } finally {
    submitting.value = false
  }
}

// 从路由参数预填 topic，跳过模式选择直接进入配置
onMounted(() => {
  const topic = route.query.topic as string
  if (topic) {
    formData.value.topic = topic
    mode.value = 'deep'
    currentStep.value = 2
  }
})
</script>

<template>
  <div class="deep-review-wizard">
    <n-card :bordered="false">
      <template #header>
        <div class="wizard-header">
          <span class="wizard-title">新建文献综述</span>
        </div>
      </template>

      <!-- 步骤指示器 -->
      <n-steps :current="currentStep" size="small" style="margin-bottom: 32px;">
        <n-step title="选择模式" />
        <n-step title="配置任务" />
        <n-step title="确认生成" />
      </n-steps>

      <!-- Step 1: 模式选择 -->
      <div v-if="currentStep === 1" class="step-content">
        <div class="mode-cards">
          <div
            class="mode-card"
            :class="{ active: mode === 'quick' }"
            @click="mode = 'quick'"
          >
            <div class="mode-badge">轻量</div>
            <div class="mode-icon">⚡</div>
            <div class="mode-title">快速综述</div>
            <div class="mode-desc">输入主题，快速生成概览性综述</div>
            <div class="mode-features">
              <div class="feature-item"><span class="feature-dot" />1-2 分钟生成</div>
              <div class="feature-item"><span class="feature-dot" />输入主题即可</div>
              <div class="feature-item"><span class="feature-dot" />Markdown 输出</div>
              <div class="feature-item"><span class="feature-dot" />适合快速了解</div>
            </div>
            <div class="mode-select-hint">{{ mode === 'quick' ? '✓ 已选择' : '点击选择' }}</div>
          </div>

          <div
            class="mode-card"
            :class="{ active: mode === 'deep' }"
            @click="mode = 'deep'"
          >
            <div class="mode-badge pro">推荐</div>
            <div class="mode-icon">🔬</div>
            <div class="mode-title">深度综述</div>
            <div class="mode-desc">多阶段管线，逐章生成高质量学术综述</div>
            <div class="mode-features">
              <div class="feature-item"><span class="feature-dot pro" />5-10 分钟生成</div>
              <div class="feature-item"><span class="feature-dot pro" />可选文献 + 精细配置</div>
              <div class="feature-item"><span class="feature-dot pro" />结构化章节 + 引用追溯</div>
              <div class="feature-item"><span class="feature-dot pro" />适合正式研究</div>
            </div>
            <div class="mode-select-hint">{{ mode === 'deep' ? '✓ 已选择' : '点击选择' }}</div>
          </div>
        </div>
        <div class="mode-tip">
          <n-icon size="16" style="vertical-align: middle; margin-right: 4px;">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-6h2v6zm0-8h-2V7h2v2z"/></svg>
          </n-icon>
          深度综述支持章节级控制、来源追溯、多格式导出
        </div>
      </div>

      <!-- Step 2: 配置任务 -->
      <div v-if="currentStep === 2" class="step-content">
        <n-form label-placement="top">
          <n-form-item label="研究主题" required>
            <n-input
              v-model:value="formData.topic"
              type="textarea"
              placeholder="例如：终身教育视角下的老年数字素养提升路径研究"
              :autosize="{ minRows: 2, maxRows: 4 }"
            />
          </n-form-item>

          <n-form-item label="研究要求（可选）">
            <n-input
              v-model:value="formData.requirements"
              type="textarea"
              placeholder="例如：请重点关注2020年后的实证研究，对比中外差异..."
              :autosize="{ minRows: 2, maxRows: 4 }"
            />
          </n-form-item>

          <n-form-item label="文献来源">
            <n-space>
              <n-checkbox v-model:checked="formData.literature_sources!.public_kb">公共知识库</n-checkbox>
              <n-checkbox v-model:checked="formData.literature_sources!.user_kb">个人知识库</n-checkbox>
              <n-checkbox v-model:checked="formData.literature_sources!.academic_search">学术搜索（在线）</n-checkbox>
            </n-space>
          </n-form-item>

          <n-form-item label="手动补充文献（可选）">
            <div class="manual-docs">
              <n-space>
                <n-button size="small" @click="toggleDocPicker">
                  {{ showDocPicker ? '收起' : '从个人知识库选择' }}
                </n-button>
                <n-button size="small" @click="showUploader = true">上传新文献</n-button>
              </n-space>

              <!-- 已选文献标签 -->
              <div v-if="selectedDocIds.length > 0" class="selected-docs">
                <n-tag
                  v-for="docId in selectedDocIds"
                  :key="docId"
                  closable
                  size="small"
                  @close="toggleDocSelection(docId)"
                >
                  {{ userDocs.find(d => d.doc_id === docId)?.filename || docId }}
                </n-tag>
              </div>

              <!-- 文献选择列表 -->
              <div v-if="showDocPicker" class="doc-picker">
                <n-spin :show="loadingDocs" size="small">
                  <div v-if="userDocs.length === 0 && !loadingDocs" class="doc-picker-empty">
                    <n-text depth="3">个人知识库暂无文献</n-text>
                  </div>
                  <div v-else class="doc-picker-list">
                    <div
                      v-for="doc in userDocs"
                      :key="doc.doc_id"
                      class="doc-picker-item"
                      :class="{ selected: selectedDocIds.includes(doc.doc_id) }"
                      @click="toggleDocSelection(doc.doc_id)"
                    >
                      <div class="doc-info">
                        <div class="doc-name">{{ doc.filename }}</div>
                        <div class="doc-meta">
                          <n-text depth="3" style="font-size: 12px;">
                            {{ doc.file_type?.toUpperCase() }} · {{ doc.file_size_kb }}KB
                          </n-text>
                        </div>
                      </div>
                      <n-checkbox :checked="selectedDocIds.includes(doc.doc_id)" />
                    </div>
                  </div>
                </n-spin>
              </div>
            </div>
          </n-form-item>

          <!-- 文献上传弹窗 -->
          <DocumentUploader
            v-model:visible="showUploader"
            @uploaded="handleDocUploaded"
          />

          <n-grid :cols="2" :x-gap="16">
            <n-gi>
              <n-form-item label="报告类型">
                <n-select v-model:value="formData.report_type" :options="reportTypes" />
              </n-form-item>
            </n-gi>
            <n-gi>
              <n-form-item label="目标字数">
                <n-select v-model:value="formData.target_words" :options="wordCountOptions" />
              </n-form-item>
            </n-gi>
            <n-gi>
              <n-form-item label="引用风格">
                <n-select v-model:value="formData.citation_style" :options="citationStyles" />
              </n-form-item>
            </n-gi>
            <n-gi>
              <n-form-item label="语言">
                <n-select v-model:value="formData.language" :options="languages" />
              </n-form-item>
            </n-gi>
          </n-grid>
        </n-form>
      </div>

      <!-- Step 3: 确认生成 -->
      <div v-if="currentStep === 3" class="step-content">
        <n-card embedded>
          <n-descriptions :column="1" label-placement="left" bordered>
            <n-descriptions-item label="主题">{{ formData.topic }}</n-descriptions-item>
            <n-descriptions-item label="文献来源">
              <n-space>
                <n-tag v-if="formData.literature_sources?.public_kb" size="small" type="info">公共库</n-tag>
                <n-tag v-if="formData.literature_sources?.user_kb" size="small" type="info">个人库</n-tag>
                <n-tag v-if="formData.literature_sources?.academic_search" size="small" type="info">学术搜索</n-tag>
              </n-space>
            </n-descriptions-item>
            <n-descriptions-item label="目标">
              约 {{ formData.target_words }} 字 · {{ reportTypes.find(r => r.value === formData.report_type)?.label }} · {{ citationStyles.find(c => c.value === formData.citation_style)?.label }} · {{ languages.find(l => l.value === formData.language)?.label }}
            </n-descriptions-item>
            <n-descriptions-item label="预计耗时">{{ estimatedTime }}</n-descriptions-item>
          </n-descriptions>
          <n-alert type="info" :bordered="false" style="margin-top: 16px;">
            生成过程中可以离开此页面，稍后在「综述历史」中查看结果。
          </n-alert>
        </n-card>
      </div>

      <!-- 底部操作 -->
      <div class="wizard-actions">
        <n-button v-if="currentStep > 1" @click="prevStep">上一步</n-button>
        <div style="flex: 1" />
        <n-button
          v-if="currentStep < 3"
          type="primary"
          @click="nextStep"
        >
          下一步
        </n-button>
        <n-button
          v-if="currentStep === 3"
          type="primary"
          :loading="submitting"
          @click="handleSubmit"
        >
          🚀 开始生成
        </n-button>
      </div>
    </n-card>
  </div>
</template>

<style scoped>
.deep-review-wizard {
  max-width: 780px;
  margin: 32px auto;
  padding: 0 20px;
}
.wizard-header {
  display: flex;
  align-items: center;
  gap: 8px;
}
.wizard-title {
  font-size: 20px;
  font-weight: 700;
  color: #1a1a1a;
}
.step-content {
  min-height: 320px;
}
.mode-cards {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
}
.mode-card {
  position: relative;
  cursor: pointer;
  border: 2px solid #e8e8e8;
  border-radius: 16px;
  padding: 28px 24px 20px;
  text-align: center;
  transition: all 0.25s ease;
  background: #fff;
  overflow: hidden;
}
.mode-card:hover {
  border-color: #c0c0c0;
  transform: translateY(-2px);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.06);
}
.mode-card.active {
  border-color: #18a058;
  background: linear-gradient(180deg, #f0faf4 0%, #fff 100%);
  box-shadow: 0 4px 20px rgba(24, 160, 88, 0.12);
}
.mode-badge {
  position: absolute;
  top: 12px;
  right: 12px;
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 10px;
  background: #f0f0f0;
  color: #888;
  font-weight: 500;
}
.mode-badge.pro {
  background: linear-gradient(135deg, #18a058, #36ad6a);
  color: #fff;
}
.mode-icon {
  font-size: 48px;
  margin-bottom: 12px;
  line-height: 1;
}
.mode-title {
  font-size: 18px;
  font-weight: 700;
  margin-bottom: 6px;
  color: #1a1a1a;
}
.mode-desc {
  font-size: 13px;
  color: #888;
  margin-bottom: 16px;
}
.mode-features {
  text-align: left;
  display: inline-block;
}
.feature-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13.5px;
  color: #555;
  padding: 3px 0;
}
.feature-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #ccc;
  flex-shrink: 0;
}
.feature-dot.pro {
  background: #18a058;
}
.mode-card.active .feature-dot {
  background: #18a058;
}
.mode-select-hint {
  margin-top: 16px;
  font-size: 13px;
  color: #bbb;
  font-weight: 500;
  transition: color 0.2s;
}
.mode-card.active .mode-select-hint {
  color: #18a058;
  font-weight: 600;
}
.mode-tip {
  text-align: center;
  margin-top: 20px;
  font-size: 13px;
  color: #999;
}
.wizard-actions {
  display: flex;
  align-items: center;
  margin-top: 24px;
  padding-top: 16px;
  border-top: 1px solid #f0f0f0;
}

/* 手动补充文献 */
.manual-docs {
  width: 100%;
}
.selected-docs {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
}
.doc-picker {
  margin-top: 12px;
  border: 1px solid #e8e8e8;
  border-radius: 8px;
  max-height: 240px;
  overflow-y: auto;
}
.doc-picker-empty {
  padding: 24px;
  text-align: center;
}
.doc-picker-list {
  padding: 4px 0;
}
.doc-picker-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  cursor: pointer;
  transition: background 0.15s;
}
.doc-picker-item:hover {
  background: #f8f9fa;
}
.doc-picker-item.selected {
  background: #f0faf4;
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
  margin-top: 2px;
}

@media (max-width: 640px) {
  .deep-review-wizard {
    padding: 0 12px;
    margin: 16px auto;
  }
  .mode-cards {
    grid-template-columns: 1fr;
  }
  .mode-card {
    padding: 20px 16px 16px;
  }
}
</style>
