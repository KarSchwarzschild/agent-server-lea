<script setup lang="ts">
/**
 * PlanStagesEditor — plan_stages 可视化编辑器容器组件
 *
 * 左右分栏布局：左侧 StageList（160px）+ 右侧 StageDetailForm
 * 数据流：cloneDeep 本地副本 + 脏检查 + 去抖 emit
 */
import { ref, watch, computed, nextTick } from 'vue'
import { NButton, NModal, NCode, NAlert, NEmpty, NInput, useMessage } from 'naive-ui'
import { CopyOutline, SparklesOutline } from '@vicons/ionicons5'
import { NIcon } from 'naive-ui'
import StageList from './StageList.vue'
import StageDetailForm from './StageDetailForm.vue'
import type { PlanStageConfig } from '../../../api/super-agent'
import { generateStages } from '../../../api/super-agent'
import type { ToolInfo } from '../../../api/tools'

type StageWithUid = PlanStageConfig & { _uid: string }

import type { PromptSection } from '../../../api/super-agent'

const props = defineProps<{
  modelValue: PlanStageConfig[]
  availableTools: ToolInfo[]
  executionMode: string
  systemPrompt?: string
  promptSectionsMeta?: PromptSection[]
}>()

const emit = defineEmits<{
  'update:modelValue': [value: PlanStageConfig[]]
}>()

const message = useMessage()

// ==================== 本地状态 ====================

const localStages = ref<StageWithUid[]>([])
const selectedUid = ref('')
const jsonPreviewVisible = ref(false)
let emitTimer: ReturnType<typeof setTimeout> | null = null

// 为阶段数组添加 _uid
function withUids(stages: PlanStageConfig[]): StageWithUid[] {
  return (stages || []).map(s => ({
    ...s,
    _uid: (s as any)._uid || crypto.randomUUID()
  }))
}

// 去除 _uid 后用于比较和 emit
function stripUids(stages: StageWithUid[]): PlanStageConfig[] {
  return stages.map(({ _uid, ...rest }) => rest)
}

// ==================== Props → Local 同步（脏检查）====================

watch(() => props.modelValue, (newVal) => {
  const incoming = JSON.stringify(newVal || [])
  const current = JSON.stringify(stripUids(localStages.value))
  if (incoming !== current) {
    localStages.value = withUids(newVal || [])
    // 选中第一个（如果当前选中已不存在）
    if (!localStages.value.find(s => s._uid === selectedUid.value) && localStages.value.length > 0) {
      selectedUid.value = localStages.value[0]._uid
    }
  }
}, { deep: true, immediate: true })

// ==================== Local → Parent 去抖 emit ====================

watch(localStages, () => {
  if (emitTimer) clearTimeout(emitTimer)
  emitTimer = setTimeout(() => {
    emit('update:modelValue', stripUids(localStages.value))
  }, 300)
}, { deep: true })

// ==================== 选中阶段 ====================

const selectedStage = computed(() =>
  localStages.value.find(s => s._uid === selectedUid.value) || null
)

// ==================== 增删排序 ====================

function addStage() {
  const n = localStages.value.length + 1
  const newStage: StageWithUid = {
    _uid: crypto.randomUUID(),
    name: `stage_${n}`,
    display_name: '新阶段',
    system_prompt_suffix: '## 本阶段任务\n\n\n## 输出要求\n\n',
    allowed_tools: null,
    max_iterations: 5,
    output_key: `stage_${n}_output`,
    critical: false,
    max_tokens: 4096,
    require_attachments: false,
    timeout_weight: 0,
    visible_to_user: n === 1,  // 第一个阶段默认可见（仅一个时）
    output_format: null,  // null=普通输出, 'artifact'=输出到Artifact面板
  }
  localStages.value.push(newStage)
  selectedUid.value = newStage._uid
}

function removeStage(uid: string) {
  const idx = localStages.value.findIndex(s => s._uid === uid)
  if (idx === -1) return
  localStages.value.splice(idx, 1)
  // 选中相邻阶段
  if (selectedUid.value === uid) {
    const next = localStages.value[Math.min(idx, localStages.value.length - 1)]
    selectedUid.value = next?._uid || ''
  }
}

function moveUp(uid: string) {
  const idx = localStages.value.findIndex(s => s._uid === uid)
  if (idx <= 0) return
  const [item] = localStages.value.splice(idx, 1)
  localStages.value.splice(idx - 1, 0, item)
}

function moveDown(uid: string) {
  const idx = localStages.value.findIndex(s => s._uid === uid)
  if (idx === -1 || idx >= localStages.value.length - 1) return
  const [item] = localStages.value.splice(idx, 1)
  localStages.value.splice(idx + 1, 0, item)
}

function updateStage(updated: StageWithUid) {
  const idx = localStages.value.findIndex(s => s._uid === updated._uid)
  if (idx !== -1) {
    localStages.value[idx] = { ...updated }
  }
}

// ==================== 校验（供父组件调用）====================

function validate(): string[] {
  const errors: string[] = []
  const names = new Set<string>()
  const keys = new Set<string>()

  for (const stage of localStages.value) {
    if (!stage.name.trim()) errors.push('阶段名称不能为空')
    if (names.has(stage.name)) errors.push(`阶段名称 "${stage.name}" 重复`)
    names.add(stage.name)

    if (stage.output_key) {
      if (keys.has(stage.output_key)) errors.push(`输出键 "${stage.output_key}" 重复`)
      keys.add(stage.output_key)
    }
  }

  // 向前引用检测
  const outputKeys = localStages.value.map(s => s.output_key).filter(Boolean)
  for (let i = 0; i < localStages.value.length; i++) {
    const suffix = localStages.value[i].system_prompt_suffix || ''
    const refs = suffix.match(/\{(\w+)\}/g) || []
    for (const ref of refs) {
      const key = ref.slice(1, -1)
      if (outputKeys.includes(key)) {
        const refIdx = localStages.value.findIndex(s => s.output_key === key)
        if (refIdx > i) {
          errors.push(`阶段 "${localStages.value[i].name}" 引用了后序阶段的输出 {${key}}`)
        }
      }
    }
  }

  if (localStages.value.length > 0 && !localStages.value.some(s => s.visible_to_user)) {
    errors.push('至少一个阶段的「输出对用户可见」必须开启')
  }

  // output_format 校验：artifact 模式必须 visible_to_user
  for (const s of localStages.value) {
    if (s.output_format === 'artifact' && !s.visible_to_user) {
      errors.push(`阶段「${s.display_name}」：输出到 Artifact 面板时必须对用户可见`)
    }
  }

  return errors
}

defineExpose({ validate })

// ==================== AI 生成阶段 ====================

const aiGenerating = ref(false)
const aiDescription = ref('')
const aiDialogVisible = ref(false)

async function handleAiGenerate() {
  if (!aiDescription.value.trim()) {
    message.warning('请输入需求描述')
    return
  }
  aiGenerating.value = true
  try {
    const toolNames = props.availableTools.map(t => t.name)
    const result = await generateStages({
      description: aiDescription.value,
      system_prompt: props.systemPrompt || '',
      available_tools: toolNames,
      skill_name: '',
    })
    if (result.success && result.stages.length > 0) {
      // 为每个阶段加 _uid
      localStages.value = result.stages.map(s => ({
        ...s,
        _uid: crypto.randomUUID(),
      }))
      selectedUid.value = localStages.value[0]._uid
      aiDialogVisible.value = false
      aiDescription.value = ''
      message.success(`已生成 ${result.stages.length} 个执行阶段，请检查并调整`)
    } else {
      message.error(result.error || '生成失败，请重试')
    }
  } catch (e: any) {
    message.error(e?.message || '生成失败')
  } finally {
    aiGenerating.value = false
  }
}

// ==================== JSON 预览 ====================

const jsonPreview = computed(() =>
  JSON.stringify(stripUids(localStages.value), null, 2)
)

function copyJson() {
  navigator.clipboard.writeText(jsonPreview.value)
  message.success('已复制到剪贴板')
}
</script>

<template>
  <div class="plan-stages-editor">
    <!-- 继承关系提示 -->
    <NAlert type="info" :bordered="false" style="margin-bottom: 12px; font-size: 12px">
      阶段按顺序依次执行。每个阶段的输出通过 <code>{output_key}</code> 传递给后续阶段。
    </NAlert>

    <!-- 空状态 -->
    <div v-if="localStages.length === 0" class="empty-state">
      <NEmpty description="尚未配置执行阶段">
        <template #extra>
          <p style="color: #999; font-size: 13px; margin: 8px 0 16px">
            计划模式将按阶段依次执行任务。如不配置，将使用系统默认的 4 阶段流程（分析 → 研究 → 大纲 → 撰写）。
          </p>
          <div style="display: flex; gap: 12px">
            <NButton @click="addStage">手动添加</NButton>
            <NButton type="primary" @click="aiDialogVisible = true">
              <template #icon><NIcon :component="SparklesOutline" /></template>
              AI 生成阶段
            </NButton>
          </div>
        </template>
      </NEmpty>
    </div>

    <!-- 主编辑区：左右分栏 -->
    <div v-else class="editor-layout">
      <div class="editor-left">
        <StageList
          :stages="localStages"
          :selected-uid="selectedUid"
          @update:selected-uid="selectedUid = $event"
          @add="addStage"
          @remove="removeStage"
          @move-up="moveUp"
          @move-down="moveDown"
        />
      </div>
      <div class="editor-right">
        <StageDetailForm
          v-if="selectedStage"
          :stage="selectedStage"
          :all-stages="localStages"
          :available-tools="availableTools"
          :prompt-sections-meta="promptSectionsMeta"
          @update:stage="updateStage"
        />
        <NEmpty v-else description="请在左侧选择一个阶段" />
      </div>
    </div>

    <!-- JSON 预览 -->
    <div v-if="localStages.length > 0" style="margin-top: 12px; text-align: right">
      <NButton text size="small" @click="jsonPreviewVisible = true">JSON 预览</NButton>
      <NButton text size="small" type="primary" @click="aiDialogVisible = true">
        <template #icon><NIcon :component="SparklesOutline" size="14" /></template>
        AI 重新生成
      </NButton>
    </div>

    <NModal v-model:show="jsonPreviewVisible" preset="card" title="JSON 预览" style="width: 620px; max-height: 80vh">
      <div style="max-height: 60vh; overflow-y: auto">
        <NCode :code="jsonPreview" language="json" word-wrap />
      </div>
      <template #footer>
        <div style="display: flex; justify-content: space-between; align-items: center">
          <span style="color: #999; font-size: 12px">JSON 为只读预览，修改请使用上方表单。</span>
          <NButton size="small" @click="copyJson">复制</NButton>
        </div>
      </template>
    </NModal>

    <!-- AI 生成弹窗 -->
    <NModal v-model:show="aiDialogVisible" preset="card" title="AI 生成执行阶段" style="width: 500px">
      <p style="color: #666; font-size: 13px; margin-bottom: 12px">
        描述你希望这个 Skill 分哪些阶段执行，AI 将自动生成全部阶段的配置（名称、指令、工具、参数等）。
      </p>
      <NInput
        v-model:value="aiDescription"
        type="textarea"
        :rows="4"
        placeholder="例如：先提取文档中的引用，然后逐条验证引用的准确性，最后生成审核报告"
        :disabled="aiGenerating"
      />
      <template #footer>
        <div style="display: flex; justify-content: flex-end; gap: 8px">
          <NButton @click="aiDialogVisible = false" :disabled="aiGenerating">取消</NButton>
          <NButton type="primary" :loading="aiGenerating" @click="handleAiGenerate">
            {{ aiGenerating ? '生成中...' : '生成' }}
          </NButton>
        </div>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.plan-stages-editor {
  min-height: 300px;
  display: flex;
  flex-direction: column;
  height: calc(100vh - 220px);
}
.plan-stages-editor :deep(.n-alert) {
  position: sticky;
  top: 0;
  z-index: 1;
  flex-shrink: 0;
}
.empty-state {
  padding: 40px 0;
  text-align: center;
}
.editor-layout {
  display: flex;
  gap: 12px;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}
.editor-left {
  width: 200px;
  flex-shrink: 0;
  border-right: 1px solid #e8e8e8;
  padding-right: 12px;
  overflow-y: auto;
  position: sticky;
  top: 0;
  align-self: flex-start;
  max-height: 100%;
}
.editor-right {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
}
</style>
