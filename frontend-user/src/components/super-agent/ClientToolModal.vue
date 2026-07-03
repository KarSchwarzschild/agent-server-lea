<script setup lang="ts">
/**
 * 客户端工具请求弹窗
 *
 * 后端 agent 请求前端执行工具时显示。
 * 根据 toolName 渲染不同 UI：
 * - confirm: 确认对话框
 * - user_select: 选项选择
 * - text_input: 文本输入
 * - 默认: 显示工具信息 + 确认/取消
 */
import { ref, computed } from 'vue'
import { NModal, NButton, NInput, NRadioGroup, NRadio, NSpace } from 'naive-ui'
import type { ClientToolRequest } from '../../types/super-agent'

const props = defineProps<{
  request: ClientToolRequest
}>()

const emit = defineEmits<{
  submit: [toolUseId: string, content: string, isError: boolean]
}>()

const textInput = ref('')
const selectedOption = ref('')

const toolType = computed(() => {
  const name = props.request.toolName
  if (name === 'confirm' || name === 'client_confirm') return 'confirm'
  if (name === 'user_select' || name === 'client_select') return 'select'
  if (name === 'text_input' || name === 'client_input') return 'input'
  return 'confirm'
})

const title = computed(() => {
  const label = props.request.toolLabel || props.request.toolName
  return `${label}`
})

const message = computed(() => {
  return (props.request.toolInput?.message as string)
    || (props.request.toolInput?.prompt as string)
    || (props.request.toolInput?.description as string)
    || '请确认此操作'
})

const options = computed(() => {
  const opts = props.request.toolInput?.options
  if (Array.isArray(opts)) return opts as string[]
  return []
})

function handleConfirm() {
  if (toolType.value === 'input') {
    emit('submit', props.request.toolUseId, textInput.value || '(空)', false)
  } else if (toolType.value === 'select') {
    emit('submit', props.request.toolUseId, selectedOption.value || options.value[0] || '', false)
  } else {
    emit('submit', props.request.toolUseId, 'confirmed', false)
  }
}

function handleCancel() {
  emit('submit', props.request.toolUseId, '用户取消了操作', true)
}
</script>

<template>
  <NModal
    :show="true"
    preset="dialog"
    :title="title"
    :mask-closable="false"
    :closable="false"
    style="max-width: 480px"
  >
    <div class="client-tool-body">
      <p class="client-tool-message">{{ message }}</p>

      <!-- 选项选择 -->
      <div v-if="toolType === 'select' && options.length" class="client-tool-options">
        <NRadioGroup v-model:value="selectedOption">
          <NSpace vertical>
            <NRadio v-for="opt in options" :key="opt" :value="opt">{{ opt }}</NRadio>
          </NSpace>
        </NRadioGroup>
      </div>

      <!-- 文本输入 -->
      <div v-if="toolType === 'input'" class="client-tool-input">
        <NInput
          v-model:value="textInput"
          type="textarea"
          :rows="3"
          placeholder="请输入..."
          @keydown.enter.ctrl="handleConfirm"
        />
      </div>

      <!-- 工具参数预览（debug 用） -->
      <details v-if="Object.keys(request.toolInput).length > 1" class="client-tool-details">
        <summary>工具参数</summary>
        <pre>{{ JSON.stringify(request.toolInput, null, 2) }}</pre>
      </details>
    </div>

    <template #action>
      <NSpace>
        <NButton @click="handleCancel">取消</NButton>
        <NButton type="primary" @click="handleConfirm">确认</NButton>
      </NSpace>
    </template>
  </NModal>
</template>

<style scoped>
.client-tool-body {
  padding: 8px 0;
}
.client-tool-message {
  margin: 0 0 12px;
  font-size: 14px;
  line-height: 1.6;
  color: var(--n-text-color);
}
.client-tool-options {
  margin-bottom: 12px;
}
.client-tool-input {
  margin-bottom: 12px;
}
.client-tool-details {
  margin-top: 8px;
  font-size: 12px;
  color: #999;
}
.client-tool-details pre {
  margin: 4px 0 0;
  padding: 8px;
  background: #f5f5f5;
  border-radius: 4px;
  font-size: 11px;
  max-height: 120px;
  overflow: auto;
}
</style>
