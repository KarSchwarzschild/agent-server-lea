<template>
  <div class="stage-detail-form">
    <!-- 空状态 -->
    <div v-if="!stage" class="form-empty">
      <NEmpty description="请选择左侧阶段进行编辑" />
    </div>

    <NForm
      v-else
      :model="localStage"
      label-placement="left"
      label-width="80"
      size="small"
    >
      <!-- ────────────────────────────────────────────
           区块 1: 基本信息
      ──────────────────────────────────────────── -->
      <div class="form-section">
        <div class="section-title">基本信息</div>

        <NFormItem label="标识名" path="name">
          <NInput
            v-model:value="localStage.name"
            placeholder="如 extract"
            @update:value="emitUpdate"
          />
        </NFormItem>

        <NFormItem label="显示名" path="display_name">
          <NInput
            v-model:value="localStage.display_name"
            @update:value="emitUpdate"
          />
        </NFormItem>

        <NFormItem label="输出键" path="output_key">
          <NInput
            v-model:value="localStage.output_key"
            placeholder="如 extraction_result"
            @update:value="emitUpdate"
          />
          <template #feedback>
            <span class="field-hint">后续阶段可通过 <code>{{ '{' + (localStage.output_key || 'output_key') + '}' }}</code> 引用本阶段输出</span>
          </template>
        </NFormItem>

        <NFormItem label="可见性" path="visible_to_user">
          <div class="checkbox-row">
            <NCheckbox
              v-model:checked="localStage.visible_to_user"
              @update:checked="emitUpdate"
            >
              输出对用户可见
            </NCheckbox>
            <span class="field-hint">
              {{ localStage.visible_to_user
                ? '内容在用户对话中直接流式展示'
                : '内容折叠在进度条中，用户可展开查看'
              }}
            </span>
          </div>
        </NFormItem>

        <NFormItem label="Artifact 输出" path="output_format">
          <div class="checkbox-row">
            <NCheckbox
              :checked="localStage.output_format === 'artifact'"
              :disabled="!localStage.visible_to_user"
              @update:checked="(v: boolean) => { localStage.output_format = v ? 'artifact' : null; emitUpdate() }"
            >
              输出到 Artifact 面板
            </NCheckbox>
            <span class="field-hint">
              {{ localStage.output_format === 'artifact'
                ? '该阶段内容将流式渲染到右侧 Artifact 编辑面板，支持版本管理和导出'
                : '该阶段内容走普通对话输出'
              }}
              <template v-if="!localStage.visible_to_user">
                <br>⚠️ 需先开启「输出对用户可见」
              </template>
            </span>
          </div>
        </NFormItem>
      </div>

      <!-- ────────────────────────────────────────────
           区块 2: 阶段指令
      ──────────────────────────────────────────── -->
      <div class="form-section">
        <div class="section-title">阶段指令</div>

        <NFormItem :show-label="false">
          <div style="width: 100%">
          <NInput
            ref="textareaRef"
            v-model:value="localStage.system_prompt_suffix"
            type="textarea"
            :rows="8"
            :maxlength="20000"
            show-count
            placeholder="追加到基础提示词后面的指令，用 {output_key} 引用前序阶段输出..."
            @blur="saveTextareaCursor"
            @update:value="emitUpdate"
          />

          <!-- 占位符芯片（textarea 下方） -->
          <div v-if="availablePlaceholders.length > 0" class="placeholder-chips">
            <span class="chips-label">点击插入引用：</span>
            <NTooltip v-for="ph in availablePlaceholders" :key="ph.output_key" placement="top">
              <template #trigger>
                <NTag size="small" class="ph-chip" @click="insertPlaceholder(ph.output_key)">{{ '{' + ph.output_key + '}' }}</NTag>
              </template>
              来源：{{ ph.display_name }}
            </NTooltip>
          </div>

          <!-- 向前引用警告 -->
          <NAlert v-if="forwardRefWarnings.length > 0" type="warning" :show-icon="true" class="forward-ref-alert">
            引用了后序阶段的输出：<span v-for="key in forwardRefWarnings" :key="key" class="forward-ref-key">{{ '{' + key + '}' }}</span>
          </NAlert>

          <!-- 结构检查提示 -->
          <div v-if="suffixHints.length > 0" class="suffix-hints">
            <span v-for="hint in suffixHints" :key="hint" class="suffix-hint">{{ hint }}</span>
          </div>
          </div>
        </NFormItem>
      </div>

      <!-- ────────────────────────────────────────────
           区块 3: 工具访问
      ──────────────────────────────────────────── -->
      <div class="form-section">
        <div class="section-title">工具访问</div>

        <NFormItem label="工具策略" label-placement="top">
          <NRadioGroup
            :value="toolMode"
            @update:value="onToolModeChange"
          >
            <NSpace vertical size="small">
              <NRadio value="inherit">使用 Skill 全局工具（继承自「工具与子代理」Tab）</NRadio>
              <NRadio value="specify">仅允许以下工具</NRadio>
              <NRadio value="none">不使用任何工具（纯文本推理）</NRadio>
            </NSpace>
          </NRadioGroup>
        </NFormItem>

        <NFormItem v-if="toolMode === 'specify'" label="指定工具" label-placement="top">
          <NSelect
            :value="localStage.allowed_tools ?? []"
            multiple
            filterable
            :options="toolOptions"
            :render-label="renderToolLabel"
            :render-tag="renderToolTag"
            :filter="filterTool"
            placeholder="搜索工具名称或描述..."
            max-tag-count="responsive"
            @update:value="onAllowedToolsChange"
          />
        </NFormItem>
      </div>

      <!-- ────────────────────────────────────────────
           区块 4: 行为控制
      ──────────────────────────────────────────── -->
      <div class="form-section">
        <div class="section-title">行为控制</div>

        <div class="checkbox-row">
          <NCheckbox v-model:checked="localStage.critical" @update:checked="emitUpdate">
            关键阶段
          </NCheckbox>
          <span class="field-hint">— 失败时中断整个流水线</span>
        </div>
        <div class="checkbox-row" style="margin-top: 8px">
          <NCheckbox v-model:checked="localStage.require_attachments" @update:checked="emitUpdate">
            需要用户附件
          </NCheckbox>
          <span class="field-hint">— 用户未上传附件时跳过</span>
        </div>
      </div>

      <!-- ────────────────────────────────────────────
           区块 5: 基础提示词过滤（高级折叠）
      ──────────────────────────────────────────── -->
      <div v-if="promptSectionsMeta && promptSectionsMeta.length > 1" class="form-section form-section--no-padding">
        <NCollapse>
          <NCollapseItem name="prompt-sections">
            <template #header>
              <span class="advanced-header">基础提示词过滤</span>
              <span class="advanced-summary">{{ promptFilterSummary }}</span>
            </template>

            <div class="advanced-content">
              <NRadioGroup :value="promptFilterMode" @update:value="onPromptFilterModeChange">
                <NSpace vertical size="small">
                  <NRadio value="full">使用全部内容（~{{ totalBaseTokens }} tok）</NRadio>
                  <NRadio value="custom">自定义：选择部分段落</NRadio>
                  <NRadio value="none">不使用（仅执行本阶段指令）</NRadio>
                </NSpace>
              </NRadioGroup>

              <div v-if="promptFilterMode === 'custom'" style="margin-top: 12px">
                <div
                  v-for="section in promptSectionsMeta"
                  :key="section.id"
                  class="section-check-item"
                >
                  <NCheckbox
                    :checked="section.order === 0 || selectedSectionIds.has(section.id)"
                    :disabled="section.order === 0"
                    @update:checked="(v: boolean) => toggleSection(section.id, v)"
                  >
                    <span class="section-label">{{ section.title }}</span>
                    <span class="section-chars">{{ section.chars }} 字</span>
                  </NCheckbox>
                  <div class="section-preview">{{ section.preview }}</div>
                </div>
                <div class="section-summary">
                  已选 {{ selectedSectionCount }} / {{ promptSectionsMeta.length }} 个段落 |
                  预估 ~{{ selectedTokens }} tok（原 ~{{ totalBaseTokens }} tok，节省 {{ savingPercent }}%）
                </div>
              </div>
            </div>
          </NCollapseItem>
        </NCollapse>
      </div>

      <!-- ────────────────────────────────────────────
           区块 6: 高级参数（折叠）
      ──────────────────────────────────────────── -->
      <div class="form-section form-section--no-padding">
        <NCollapse>
          <NCollapseItem name="advanced">
            <template #header>
              <span class="advanced-header">高级参数</span>
              <span class="advanced-summary">{{ advancedSummary }}</span>
            </template>

            <div class="advanced-content">
              <NFormItem label="最大轮次" path="max_iterations">
                <NInputNumber
                  v-model:value="localStage.max_iterations"
                  :min="1"
                  :max="50"
                  :show-button="true"
                  style="width: 160px"
                  @update:value="emitUpdate"
                />
              </NFormItem>

              <NFormItem label="最大Token" path="max_tokens">
                <NInputNumber
                  v-model:value="localStage.max_tokens"
                  :min="512"
                  :max="32768"
                  :step="512"
                  :show-button="true"
                  style="width: 160px"
                  @update:value="emitUpdate"
                />
              </NFormItem>

              <NFormItem label="超时权重" path="timeout_weight">
                <NInputNumber
                  v-model:value="localStage.timeout_weight"
                  :min="0"
                  :max="1"
                  :step="0.1"
                  :precision="1"
                  :show-button="true"
                  style="width: 160px"
                  @update:value="emitUpdate"
                />
              </NFormItem>
            </div>
          </NCollapseItem>
        </NCollapse>
      </div>
    </NForm>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch, h } from 'vue'
import type { SelectOption, SelectRenderLabel, SelectRenderTag } from 'naive-ui'
import {
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NSelect,
  NRadioGroup,
  NRadio,
  NCheckbox,
  NTag,
  NTooltip,
  NCollapse,
  NCollapseItem,
  NAlert,
  NSpace,
  NEmpty,
} from 'naive-ui'
import type { PlanStageConfig } from '../../../api/super-agent'
import type { ToolInfo } from '../../../api/tools'

// ────────────────────────────────────────────
// Types
// ────────────────────────────────────────────

type StageWithUid = PlanStageConfig & { _uid: string }

// ────────────────────────────────────────────
// Props & Emits
// ────────────────────────────────────────────

import type { PromptSection } from '../../../api/super-agent'

const props = defineProps<{
  stage: StageWithUid | null
  allStages: StageWithUid[]
  availableTools: ToolInfo[]
  promptSectionsMeta?: PromptSection[]
}>()

const emit = defineEmits<{
  'update:stage': [stage: StageWithUid]
}>()

// ────────────────────────────────────────────
// Local state
// ────────────────────────────────────────────

function makeLocalStage(src: StageWithUid): StageWithUid {
  return {
    name: src.name || '',
    display_name: src.display_name || '',
    output_key: src.output_key || '',
    system_prompt_suffix: src.system_prompt_suffix || '',
    allowed_tools: src.allowed_tools === null ? null
      : src.allowed_tools === undefined ? null
      : [...src.allowed_tools],
    max_iterations: src.max_iterations ?? 5,
    max_tokens: src.max_tokens ?? 4096,
    timeout_weight: src.timeout_weight ?? 0,
    critical: src.critical ?? false,
    require_attachments: src.require_attachments ?? false,
    visible_to_user: src.visible_to_user ?? true,
    prompt_section_ids: src.prompt_section_ids ?? null,
    output_format: src.output_format ?? null,
    _uid: src._uid,
  }
}

const localStage = reactive<StageWithUid>({
  name: '',
  display_name: '',
  output_key: '',
  system_prompt_suffix: '',
  allowed_tools: null,
  max_iterations: 5,
  max_tokens: 4096,
  timeout_weight: 1.0,
  critical: false,
  require_attachments: false,
  visible_to_user: true,
  prompt_section_ids: null as string[] | null,
  output_format: null as string | null,
  _uid: '',
})

// 当 stage prop 变化时（切换阶段），同步到 localStage
watch(
  () => props.stage,
  (newStage) => {
    if (!newStage) return
    Object.assign(localStage, makeLocalStage(newStage))
  },
  { immediate: true, deep: false },
)

// ────────────────────────────────────────────
// Textarea cursor
// ────────────────────────────────────────────

const textareaRef = ref<InstanceType<typeof NInput> | null>(null)
const savedCursor = ref<number>(0)

function saveTextareaCursor() {
  const el = textareaRef.value?.$el?.querySelector('textarea') as HTMLTextAreaElement | null
  if (el) {
    savedCursor.value = el.selectionStart ?? localStage.system_prompt_suffix.length
  }
}

function insertPlaceholder(outputKey: string) {
  const token = `{${outputKey}}`
  const text = localStage.system_prompt_suffix ?? ''
  const pos = savedCursor.value
  localStage.system_prompt_suffix = text.slice(0, pos) + token + text.slice(pos)
  // 更新游标到插入内容之后
  savedCursor.value = pos + token.length
  emitUpdate()

  // 恢复 textarea 焦点和光标位置
  const el = textareaRef.value?.$el?.querySelector('textarea') as HTMLTextAreaElement | null
  if (el) {
    el.focus()
    const newPos = pos + token.length
    requestAnimationFrame(() => {
      el.setSelectionRange(newPos, newPos)
      savedCursor.value = newPos
    })
  }
}

// ────────────────────────────────────────────
// Computed: 可引用的前序阶段占位符
// ────────────────────────────────────────────

const availablePlaceholders = computed(() => {
  if (!props.stage) return []
  const currentIndex = props.allStages.findIndex(s => s._uid === props.stage!._uid)
  if (currentIndex <= 0) return []
  return props.allStages
    .slice(0, currentIndex)
    .filter(s => s.output_key)
    .map(s => ({ output_key: s.output_key, display_name: s.display_name || s.name }))
})

// ────────────────────────────────────────────
// Computed: 向前引用警告
// ────────────────────────────────────────────

const forwardRefWarnings = computed(() => {
  if (!props.stage) return []
  const currentIndex = props.allStages.findIndex(s => s._uid === props.stage!._uid)
  // 收集后序阶段的 output_key 集合
  const futureKeys = new Set(
    props.allStages
      .slice(currentIndex + 1)
      .map(s => s.output_key)
      .filter(Boolean),
  )
  if (futureKeys.size === 0) return []
  // 扫描 system_prompt_suffix 中的 {xxx} 模式
  const matches = localStage.system_prompt_suffix.match(/\{([^}]+)\}/g) ?? []
  const referenced = matches
    .map(m => m.slice(1, -1))
    .filter(key => futureKeys.has(key))
  return [...new Set(referenced)]
})

// ────────────────────────────────────────────
// Computed: 阶段指令结构检查
// ────────────────────────────────────────────

const suffixHints = computed(() => {
  const text = (localStage.system_prompt_suffix || '').trim()
  const hints: string[] = []
  if (!text) return hints
  if (text.length < 20) {
    hints.push('💡 指令过短，建议补充任务描述和输出要求')
  }
  if (text.length >= 20 && !text.includes('输出') && !text.includes('格式') && !text.includes('返回')) {
    hints.push('💡 建议添加"输出要求"段落，明确本阶段的输出格式')
  }
  // 最后一个 visible 阶段缺模板
  if (localStage.visible_to_user && !text.includes('模板') && !text.includes('格式') && !text.includes('```')) {
    const isLast = props.allStages.filter(s => s.visible_to_user).pop()?._uid === localStage._uid
    if (isLast) {
      hints.push('💡 最终展示阶段建议包含输出模板（如 Markdown 格式示例）')
    }
  }
  return hints
})

// ────────────────────────────────────────────
// Computed: 工具模式
// ────────────────────────────────────────────

type ToolMode = 'inherit' | 'specify' | 'none'

const toolMode = computed<ToolMode>(() => {
  const t = localStage.allowed_tools
  if (t === null || t === undefined) return 'inherit'
  if (Array.isArray(t) && t.length === 0) return 'none'
  return 'specify'
})

function onToolModeChange(mode: ToolMode) {
  if (mode === 'inherit') {
    localStage.allowed_tools = null
  } else if (mode === 'none') {
    localStage.allowed_tools = []
  } else {
    // specify — 保留已选工具，如果之前是 null/[] 则初始化为空数组
    const current = localStage.allowed_tools
    localStage.allowed_tools = Array.isArray(current) && current.length > 0
      ? [...current]
      : []
  }
  emitUpdate()
}

function onAllowedToolsChange(value: string[]) {
  localStage.allowed_tools = value
  emitUpdate()
}

// ────────────────────────────────────────────
// Computed: 工具选项
// ────────────────────────────────────────────

const toolOptions = computed(() =>
  props.availableTools.map(t => ({
    label: (t as any).display_name || t.name,
    value: t.name,
    displayName: (t as any).display_name || '',
    description: t.description || '',
    category: t.category || '',
  })),
)

// Select2 风格：选项渲染（显示名 + 标识名 + 描述）
const renderToolLabel: SelectRenderLabel = (option) => {
  const dn = (option as any).displayName
  const desc = (option as any).description
  return h('div', { style: 'padding: 4px 0' }, [
    h('div', { style: 'font-size: 13px; font-weight: 500' }, dn || option.value as string),
    dn ? h('code', { style: 'font-size: 11px; color: #999;' }, option.value as string) : null,
    desc ? h('div', {
      style: 'font-size: 11px; color: #999; margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 400px'
    }, desc) : null,
  ])
}

// Tag 渲染（紧凑：只显示显示名）
const renderToolTag: SelectRenderTag = ({ option, handleClose }) => {
  const dn = (option as any).displayName || option.value
  return h('div', {
    style: 'display: inline-flex; align-items: center; gap: 4px; padding: 0 6px; margin: 2px; border-radius: 3px; background: #f0f9ff; border: 1px solid #91caff; font-size: 12px; line-height: 22px',
  }, [
    h('span', {}, dn),
    h('span', {
      style: 'cursor: pointer; margin-left: 2px; color: #999; font-size: 14px',
      onClick: (e: Event) => { e.stopPropagation(); handleClose() },
    }, '×'),
  ])
}

// 搜索过滤：匹配 display_name、name、description
function filterTool(pattern: string, option: SelectOption): boolean {
  const p = pattern.toLowerCase()
  const name = ((option.value as string) || '').toLowerCase()
  const dn = ((option as any).displayName || '').toLowerCase()
  const desc = ((option as any).description || '').toLowerCase()
  return name.includes(p) || dn.includes(p) || desc.includes(p)
}

// ────────────────────────────────────────────
// Computed: 高级参数摘要
// ────────────────────────────────────────────

const advancedSummary = computed(() =>
  `轮次=${localStage.max_iterations ?? 5}, Token=${localStage.max_tokens ?? 4096}, 超时权重=${(localStage.timeout_weight ?? 0).toFixed(1)}`,
)

// ────────────────────────────────────────────
// Prompt section filter
// ────────────────────────────────────────────

type PromptFilterMode = 'full' | 'custom' | 'none'

const promptFilterMode = computed<PromptFilterMode>(() => {
  const ids = localStage.prompt_section_ids
  if (ids === null || ids === undefined) return 'full'
  if (Array.isArray(ids) && ids.length === 0) return 'none'
  return 'custom'
})

const selectedSectionIds = computed(() => new Set(localStage.prompt_section_ids || []))

const selectedSectionCount = computed(() => {
  if (promptFilterMode.value === 'full') return props.promptSectionsMeta?.length || 0
  if (promptFilterMode.value === 'none') return 0
  // intro 始终包含
  const introCount = (props.promptSectionsMeta || []).some(s => s.order === 0) ? 1 : 0
  return selectedSectionIds.value.size + introCount
})

const totalBaseTokens = computed(() => {
  const total = (props.promptSectionsMeta || []).reduce((sum, s) => sum + s.chars, 0)
  return Math.round(total / 4)  // 粗估
})

const selectedTokens = computed(() => {
  if (promptFilterMode.value === 'full') return totalBaseTokens.value
  if (promptFilterMode.value === 'none') return 0
  const meta = props.promptSectionsMeta || []
  const chars = meta
    .filter(s => s.order === 0 || selectedSectionIds.value.has(s.id))
    .reduce((sum, s) => sum + s.chars, 0)
  return Math.round(chars / 4)
})

const savingPercent = computed(() => {
  if (!totalBaseTokens.value) return 0
  return Math.round((1 - selectedTokens.value / totalBaseTokens.value) * 100)
})

const promptFilterSummary = computed(() => {
  if (promptFilterMode.value === 'full') return '使用全部'
  if (promptFilterMode.value === 'none') return '不使用'
  return `已选 ${selectedSectionCount.value} 段落，节省 ${savingPercent.value}%`
})

function onPromptFilterModeChange(mode: PromptFilterMode) {
  if (mode === 'full') {
    localStage.prompt_section_ids = null as any
  } else if (mode === 'none') {
    localStage.prompt_section_ids = []
  } else {
    // custom: 初始化为所有段落 id（除 intro）
    const meta = props.promptSectionsMeta || []
    localStage.prompt_section_ids = meta.filter(s => s.order !== 0).map(s => s.id)
  }
  emitUpdate()
}

function toggleSection(sectionId: string, checked: boolean) {
  const ids = new Set(localStage.prompt_section_ids || [])
  if (checked) {
    ids.add(sectionId)
  } else {
    ids.delete(sectionId)
  }
  localStage.prompt_section_ids = [...ids]
  emitUpdate()
}

// ────────────────────────────────────────────
// Emit update
// ────────────────────────────────────────────

function emitUpdate() {
  if (!props.stage) return
  emit('update:stage', { ...localStage })
}
</script>

<style scoped>
.stage-detail-form {
  padding: 12px 16px 24px;
  height: 100%;
  overflow-y: auto;
}

.form-empty {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 200px;
}

/* ── 分区 ── */
.form-section {
  margin-bottom: 12px;
  padding: 10px 14px 6px;
  border: 1px solid var(--n-border-color, #e8e8e8);
  border-radius: 6px;
}

.form-section--no-padding {
  padding: 0;
  overflow: hidden;
}

.section-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--n-text-color-3, #999);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: 10px;
}

/* ── 字段辅助文字 ── */
.checkbox-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.field-hint {
  font-size: 12px;
  color: var(--n-text-color-3, #999);
  line-height: 1.4;
}

/* ── 阶段指令 ── */
.prompt-hint {
  margin-bottom: 10px;
  font-size: 12px;
}

.forward-ref-alert {
  margin-top: 6px;
  font-size: 12px;
}

.suffix-hints {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 6px;
}
.suffix-hint {
  font-size: 12px;
  color: #faad14;
}

.forward-ref-key {
  display: inline-block;
  background: rgba(250, 140, 22, 0.12);
  border-radius: 3px;
  padding: 0 4px;
  margin-left: 4px;
  font-family: monospace;
  font-size: 12px;
  color: #d97706;
}

.placeholder-chips {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  margin-top: 6px;
}

.chips-label {
  font-size: 12px;
  color: var(--n-text-color-3, #999);
  flex-shrink: 0;
}

.ph-chip {
  cursor: pointer;
  font-family: monospace;
  font-size: 11px;
  transition: background 0.15s;
}

.ph-chip:hover {
  background: var(--n-primary-color-hover, #4299e1) !important;
  color: #fff !important;
}

/* ── 高级参数 ── */
.advanced-header {
  font-size: 13px;
  font-weight: 500;
}

.advanced-summary {
  margin-left: 8px;
  font-size: 11px;
  color: var(--n-text-color-3, #999);
  font-family: monospace;
}

.advanced-content {
  padding: 4px 8px 8px;
}

/* ── 段落选择器 ── */
.section-check-item {
  padding: 6px 0;
  border-bottom: 1px solid #f0f0f0;
}
.section-check-item:last-child {
  border-bottom: none;
}
.section-label {
  font-size: 13px;
  font-weight: 500;
}
.section-chars {
  margin-left: 6px;
  font-size: 11px;
  color: #999;
}
.section-preview {
  font-size: 11px;
  color: #aaa;
  margin-left: 24px;
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.section-summary {
  margin-top: 10px;
  padding: 8px 12px;
  background: #f6ffed;
  border-radius: 4px;
  font-size: 12px;
  color: #52c41a;
}
</style>
