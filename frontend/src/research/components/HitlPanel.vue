<template>
  <n-card title="人工审批" size="small" class="hitl-panel">
    <template #header-extra>
      <n-tag :type="stageColor">{{ stageLabel }}</n-tag>
    </template>

    <p class="message">{{ payload.message }}</p>

    <!-- Search stage: paper table -->
    <template v-if="payload.stage === 'search'">
      <n-data-table
        :columns="paperColumns"
        :data="payload.papers || []"
        :max-height="260"
        size="small"
        :bordered="false"
      />
    </template>

    <!-- Analysis stage: summary table -->
    <template v-else-if="payload.stage === 'analysis'">
      <n-data-table
        :columns="analysisColumns"
        :data="payload.analyses_summary || []"
        :max-height="260"
        size="small"
        :bordered="false"
      />
    </template>

    <!-- Outline stage: editable -->
    <template v-else-if="payload.stage === 'outline'">
      <n-input
        v-model:value="outlineText"
        type="textarea"
        :autosize="{ minRows: 6, maxRows: 16 }"
        placeholder="可直接修改大纲内容..."
      />
    </template>

    <!-- Final stage: review preview -->
    <template v-else-if="payload.stage === 'final'">
      <div class="review-preview">{{ payload.review_preview }}</div>
    </template>

    <!-- Feedback input for rejection -->
    <n-input
      v-model:value="feedbackText"
      type="textarea"
      :autosize="{ minRows: 2, maxRows: 6 }"
      placeholder="输入修改意见（驳回时会根据你的反馈定向修改，而非从头重做）"
      class="feedback-input"
    />

    <!-- Action buttons -->
    <div class="actions">
      <n-button type="error" ghost @click="handleReject" :loading="loading" :disabled="!feedbackText.trim()">
        驳回并修改
      </n-button>
      <n-button type="success" @click="handleApprove" :loading="loading">
        通过
      </n-button>
    </div>
  </n-card>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { NCard, NTag, NDataTable, NInput, NButton } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import type { InterruptPayload } from '../composables/useReviewSSE'

const props = defineProps<{
  payload: InterruptPayload
  loading?: boolean
}>()

const emit = defineEmits<{
  (e: 'approve', feedback?: string): void
  (e: 'reject', feedback?: string): void
}>()

const outlineText = ref(props.payload.outline || '')
const feedbackText = ref('')

const stageLabel = computed(() => {
  const map: Record<string, string> = {
    search: '检索审查', analysis: '分析审查',
    outline: '大纲审查', final: '终审',
  }
  return map[props.payload.stage] || props.payload.stage
})

const stageColor = computed(() => {
  const map: Record<string, 'warning' | 'info' | 'success' | 'error'> = {
    search: 'info', analysis: 'info', outline: 'warning', final: 'success',
  }
  return map[props.payload.stage] || 'warning'
})

const paperColumns: DataTableColumns = [
  { title: '标题', key: 'title', ellipsis: { tooltip: true } },
  { title: '摘要', key: 'abstract', ellipsis: { tooltip: true }, width: 300 },
]

const analysisColumns: DataTableColumns = [
  { title: '论文', key: 'title', ellipsis: { tooltip: true } },
  { title: '相关度', key: 'relevance', width: 80 },
]

function handleApprove() {
  const feedback = props.payload.stage === 'outline' ? outlineText.value : undefined
  emit('approve', feedback)
}

function handleReject() {
  emit('reject', feedbackText.value)
}
</script>

<style scoped>
.hitl-panel {
  margin-top: 12px;
}

.message {
  color: #666;
  margin-bottom: 12px;
}

.feedback-input {
  margin-top: 12px;
}

.review-preview {
  max-height: 300px;
  overflow-y: auto;
  white-space: pre-wrap;
  padding: 12px;
  background: #fafafa;
  border-radius: 4px;
  font-size: 13px;
  line-height: 1.8;
}

.actions {
  display: flex;
  gap: 12px;
  justify-content: flex-end;
  margin-top: 16px;
}
</style>
