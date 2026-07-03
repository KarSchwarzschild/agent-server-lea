<script setup lang="ts">
/**
 * 内置数据源选择器
 * 支持 fixed 源（卡片点击切换）和 parameterized 源（输入 + 探测）
 */
import { ref, computed, onMounted } from 'vue'
import {
  NTag,
  NSpin,
  NInput,
  NButton,
  NAlert,
  NAutoComplete,
  NTooltip,
  NEllipsis,
} from 'naive-ui'
import {
  getBuiltinSources,
  testBuiltinSource,
  previewBuiltinSource,
  type BuiltinSource,
  type SourceSelection,
  type TestSourceResponse,
  type PreviewSourceResponse,
  type SampleItem,
} from '../../api/subscription'
import { useResponsive } from '../../composables/useResponsive'

const props = withDefaults(defineProps<{
  modelValue: SourceSelection[]
  excludeSourceIds?: string[]
  /** inline = Step1 嵌入（不显示确认按钮）; drawer = 抽屉（显示确认按钮） */
  mode?: 'inline' | 'drawer'
}>(), {
  mode: 'inline',
})

const emit = defineEmits<{
  (e: 'update:modelValue', value: SourceSelection[]): void
  (e: 'confirm'): void
}>()

const { isMobile } = useResponsive()

// icon 映射
const iconMap: Record<string, string> = {
  academic: '🎓',
  tech: '💻',
  trending: '🔥',
  industry: '📰',
  default: '📋',
}
const getIcon = (icon?: string) => iconMap[icon || 'default'] || iconMap.default

// 数据源列表
const loading = ref(false)
const sources = ref<BuiltinSource[]>([])
const categories = ref<{ value: string; label: string }[]>([])
const activeCategory = ref('all')

// parameterized 源输入状态：source_id -> { value, testing, result, error }
const paramInputs = ref<Record<string, {
  value: string
  testing: boolean
  result: TestSourceResponse | null
  error: string | null
}>>({})

// fixed 源预览状态：source_id -> { loading, data }
const previewStates = ref<Record<string, {
  loading: boolean
  data: PreviewSourceResponse | null
  expanded: boolean
}>>({})

const loadSources = async () => {
  loading.value = true
  try {
    const res = await getBuiltinSources()
    sources.value = res.sources
    categories.value = res.categories
  } catch {
    // 静默失败
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadSources()
})

// 按分类筛选
const filteredSources = computed(() => {
  const list = sources.value
  if (activeCategory.value === 'all') return list
  return list.filter(s => s.category === activeCategory.value)
})

const fixedSources = computed(() => filteredSources.value.filter(s => s.type === 'fixed'))
const parameterizedSources = computed(() => filteredSources.value.filter(s => s.type === 'parameterized'))

// 是否禁用某个源
const isDisabled = (source: BuiltinSource) => {
  if (source.enabled === false) return true
  if (props.excludeSourceIds?.includes(source.id)) return true
  return false
}

// fixed 源是否已选中
const isFixedSelected = (source: BuiltinSource) => {
  return props.modelValue.some(s => s.source_id === source.id)
}

// 切换 fixed 源选中状态
const toggleFixed = (source: BuiltinSource) => {
  if (isDisabled(source)) return
  const current = props.modelValue
  const idx = current.findIndex(s => s.source_id === source.id)
  if (idx >= 0) {
    emit('update:modelValue', current.filter((_, i) => i !== idx))
  } else {
    emit('update:modelValue', [...current, { source_id: source.id }])
  }
}

// 获取 parameterized 源输入状态
const getParamInput = (sourceId: string) => {
  if (!paramInputs.value[sourceId]) {
    paramInputs.value[sourceId] = { value: '', testing: false, result: null, error: null }
  }
  return paramInputs.value[sourceId]
}

// autocomplete 选中后提取纯代码值
// 输入可能是 "ZGDJ"（直接输入）或 "中国高等教育（ZGDJ）"（下拉选中）
const extractParamValue = (source: BuiltinSource, raw: string): string => {
  const trimmed = raw.trim()
  if (source.input_type === 'autocomplete' && source.options?.length) {
    // 优先精确匹配 option label 格式 "xxx（CODE）"
    const match = trimmed.match(/[（(]([A-Z0-9]{2,10})[）)]/)
    if (match) return match[1]
    // 也可能用户直接选了 value（某些组件版本）
    const opt = source.options.find(o => o.value === trimmed || o.label === trimmed)
    if (opt) return opt.value
  }
  return trimmed
}

// parameterized 源：校验参数
const validateParam = (source: BuiltinSource, rawValue: string): string | null => {
  const value = extractParamValue(source, rawValue)
  if (!value) return '请输入内容'
  if (source.validation_regex) {
    const regex = new RegExp(source.validation_regex)
    if (!regex.test(value)) {
      return source.validation_error || '格式不正确'
    }
  }
  return null
}

// parameterized 源：确认添加（探测 + 添加）
const handleAddParam = async (source: BuiltinSource) => {
  const input = getParamInput(source.id)
  const actualValue = extractParamValue(source, input.value)
  const validationError = validateParam(source, input.value)
  if (validationError) {
    input.error = validationError
    return
  }
  input.error = null
  input.testing = true
  input.result = null

  const params = source.param_key ? { [source.param_key]: actualValue } : {}

  try {
    const result = await testBuiltinSource(source.id, params)
    input.result = result
    if (result.ok) {
      doAddParam(source, params)
      input.value = ''
      input.result = null
    }
    // 不可用时显示结果，让用户选择降级添加
  } catch {
    input.result = { ok: false, message: '服务暂不可用，请稍后重试' }
  } finally {
    input.testing = false
  }
}

// 实际添加 parameterized 源
const doAddParam = (source: BuiltinSource, params: Record<string, string>) => {
  // 去重：同 source_id + 同 params 值
  const exists = props.modelValue.some(s => {
    if (s.source_id !== source.id) return false
    const paramKey = source.param_key
    if (!paramKey) return true
    return s.params?.[paramKey] === params[paramKey]
  })
  if (!exists) {
    emit('update:modelValue', [...props.modelValue, { source_id: source.id, params }])
  }
}

// 降级添加（服务不可用时仍然添加）
const handleForceAdd = (source: BuiltinSource) => {
  const input = getParamInput(source.id)
  const actualValue = extractParamValue(source, input.value)
  const params = source.param_key ? { [source.param_key]: actualValue } : {}
  doAddParam(source, params)
  input.value = ''
  input.result = null
  input.error = null
}

// fixed 源预览
const getPreviewState = (sourceId: string) => {
  if (!previewStates.value[sourceId]) {
    previewStates.value[sourceId] = { loading: false, data: null, expanded: false }
  }
  return previewStates.value[sourceId]
}

const handlePreview = async (source: BuiltinSource, event: MouseEvent) => {
  event.stopPropagation() // 阻止冒泡到卡片点击（toggle 选中）
  const state = getPreviewState(source.id)
  // 已有数据时仅切换展开/收起
  if (state.data) {
    state.expanded = !state.expanded
    return
  }
  state.loading = true
  state.expanded = true
  try {
    state.data = await previewBuiltinSource(source.id)
  } catch {
    state.data = { ok: false, message: '预览请求失败' }
  } finally {
    state.loading = false
  }
}

// 移除已选
// autocomplete 源的下拉选项（根据输入值过滤）
const getAutoCompleteOptions = (source: BuiltinSource) => {
  const input = getParamInput(source.id)
  const keyword = (input.value || '').trim().toUpperCase()
  if (!source.options) return []
  return source.options
    .filter(opt => {
      if (!keyword) return true
      return opt.value.includes(keyword) || opt.label.includes(input.value.trim())
    })
    .map(opt => ({
      label: `${opt.label}（${opt.value}）`,
      value: opt.value,
    }))
}

const removeSelected = (idx: number) => {
  const updated = [...props.modelValue]
  updated.splice(idx, 1)
  emit('update:modelValue', updated)
}

// 已选源的显示名称
const getSelectionLabel = (selection: SourceSelection): string => {
  const source = sources.value.find(s => s.id === selection.source_id)
  if (!source) return selection.source_id
  if (source.param_key && selection.params?.[source.param_key]) {
    return `${source.name} · ${selection.params[source.param_key]}`
  }
  return source.name
}
</script>

<template>
  <div class="source-selector">
    <!-- 分类筛选条 -->
    <div class="source-category-bar">
      <span
        :class="['source-cat-tag', { active: activeCategory === 'all' }]"
        @click="activeCategory = 'all'"
      >
        全部
      </span>
      <span
        v-for="cat in categories"
        :key="cat.value"
        :class="['source-cat-tag', { active: activeCategory === cat.value }]"
        @click="activeCategory = cat.value"
      >
        {{ cat.label }}
      </span>
    </div>

    <!-- 加载中 -->
    <div v-if="loading" class="source-loading">
      <n-spin size="small" />
    </div>

    <template v-else>
      <div class="source-scroll-area">
      <!-- Fixed 源卡片网格 -->
      <div v-if="fixedSources.length > 0" :class="['source-grid', { mobile: isMobile }]">
        <template
          v-for="source in fixedSources"
          :key="source.id"
        >
          <div
            :class="[
              'source-card',
              {
                selected: isFixedSelected(source),
                disabled: isDisabled(source),
              }
            ]"
            @click="toggleFixed(source)"
          >
            <div class="source-card-header">
              <span class="source-icon">{{ getIcon(source.icon) }}</span>
              <span class="source-name">{{ source.name }}</span>
              <span v-if="props.excludeSourceIds?.includes(source.id)" class="source-badge added">已添加</span>
              <span v-else-if="isFixedSelected(source)" class="source-badge selected-badge">已选</span>
            </div>
            <p class="source-desc">{{ source.description }}</p>
            <!-- 预览按钮 -->
            <div class="source-card-footer">
              <span
                class="preview-link"
                :class="{ active: getPreviewState(source.id).expanded }"
                @click="handlePreview(source, $event)"
              >
                <n-spin v-if="getPreviewState(source.id).loading" :size="12" style="margin-right: 4px" />
                {{ getPreviewState(source.id).expanded ? '收起预览' : '预览内容' }}
              </span>
            </div>
          </div>
          <!-- 预览内容（独立 grid item，跨两列） -->
          <div
            v-if="getPreviewState(source.id).expanded && getPreviewState(source.id).data"
            class="preview-row"
          >
            <template v-if="getPreviewState(source.id).data!.ok">
              <span class="preview-row-label">{{ source.name }} · 最新内容：</span>
              <ul class="preview-list">
                <li v-for="(item, idx) in getPreviewState(source.id).data!.sample_items" :key="idx">
                  <n-tooltip :disabled="!item.summary" placement="top" :style="{ maxWidth: '360px' }">
                    <template #trigger>
                      <a
                        v-if="item.link"
                        :href="item.link"
                        target="_blank"
                        rel="noopener noreferrer"
                        class="preview-item-link"
                        @click.stop
                      >{{ item.title }}</a>
                      <span v-else class="preview-item-text">{{ item.title }}</span>
                    </template>
                    <div class="preview-tooltip-content">
                      <p v-if="item.summary">{{ item.summary }}</p>
                      <span v-if="item.published" class="preview-tooltip-time">{{ item.published }}</span>
                    </div>
                  </n-tooltip>
                </li>
              </ul>
            </template>
            <p v-else class="preview-error">{{ getPreviewState(source.id).data!.message }}</p>
          </div>
        </template>
      </div>

      <!-- Parameterized 源 -->
      <div v-if="parameterizedSources.length > 0" class="param-sources">
        <div
          v-for="source in parameterizedSources"
          :key="source.id"
          class="param-source-item"
        >
          <div class="param-source-header">
            <span class="source-icon">{{ getIcon(source.icon) }}</span>
            <span class="source-name">{{ source.name }}</span>
            <span v-if="isDisabled(source)" class="source-badge disabled-badge">不可用</span>
          </div>
          <p class="source-desc">{{ source.description }}</p>
          <div v-if="!isDisabled(source)" class="param-input-row">
            <!-- autocomplete 模式：下拉选择 + 支持自定义输入 -->
            <n-auto-complete
              v-if="source.input_type === 'autocomplete'"
              v-model:value="getParamInput(source.id).value"
              :options="getAutoCompleteOptions(source)"
              :placeholder="source.param_placeholder || '选择或输入'"
              size="small"
              style="flex: 1"
              :status="getParamInput(source.id).error ? 'error' : undefined"
              clearable
              :get-show="() => true"
            />
            <!-- 普通文本输入 -->
            <n-input
              v-else
              v-model:value="getParamInput(source.id).value"
              :placeholder="source.param_placeholder || '请输入'"
              size="small"
              style="flex: 1"
              :status="getParamInput(source.id).error ? 'error' : undefined"
              @keyup.enter="handleAddParam(source)"
            />
            <n-button
              size="small"
              type="primary"
              :loading="getParamInput(source.id).testing"
              :disabled="!getParamInput(source.id).value.trim()"
              @click="handleAddParam(source)"
            >
              确认添加
            </n-button>
          </div>
          <!-- 帮助链接 / 帮助文字 -->
          <a
            v-if="source.help_url"
            :href="source.help_url"
            target="_blank"
            class="source-help-link"
          >{{ source.help_url_label || '查看更多' }}</a>
          <p v-if="source.help_text" class="source-help-text">{{ source.help_text }}</p>
          <!-- 校验错误 -->
          <p v-if="getParamInput(source.id).error" class="param-error">
            {{ getParamInput(source.id).error }}
          </p>
          <!-- 探测结果 -->
          <div v-if="getParamInput(source.id).result" class="param-result">
            <n-alert
              v-if="getParamInput(source.id).result!.ok"
              type="success"
              :bordered="false"
              style="padding: 6px 10px;"
            >
              {{ getParamInput(source.id).result!.message }}
              <span v-if="getParamInput(source.id).result!.feed_title">
                · {{ getParamInput(source.id).result!.feed_title }}
              </span>
              <ul v-if="getParamInput(source.id).result!.sample_items?.length" class="preview-list" style="margin-top: 6px;">
                <li v-for="(item, idx) in getParamInput(source.id).result!.sample_items" :key="idx">
                  <n-tooltip :disabled="!item.summary" placement="top" :style="{ maxWidth: '360px' }">
                    <template #trigger>
                      <a
                        v-if="item.link"
                        :href="item.link"
                        target="_blank"
                        rel="noopener noreferrer"
                        class="preview-item-link"
                        @click.stop
                      >{{ item.title }}</a>
                      <span v-else class="preview-item-text">{{ item.title }}</span>
                    </template>
                    <div class="preview-tooltip-content">
                      <p v-if="item.summary">{{ item.summary }}</p>
                      <span v-if="item.published" class="preview-tooltip-time">{{ item.published }}</span>
                    </div>
                  </n-tooltip>
                </li>
              </ul>
            </n-alert>
            <div v-else class="param-unavailable">
              <span class="param-unavailable-msg">{{ getParamInput(source.id).result!.message }}</span>
              <n-button text type="primary" size="small" @click="handleForceAdd(source)">
                仍然添加
              </n-button>
            </div>
          </div>
        </div>
      </div>
      </div><!-- /source-scroll-area -->
    </template>

    <!-- 已选数据源摘要 -->
    <div v-if="modelValue.length > 0" class="selected-summary">
      <span class="selected-count">已选 {{ modelValue.length }} 个数据源：</span>
      <div class="selected-tags">
        <n-tag
          v-for="(sel, idx) in modelValue"
          :key="idx"
          closable
          size="small"
          type="success"
          @close="removeSelected(idx)"
        >
          {{ getSelectionLabel(sel) }}
        </n-tag>
      </div>
    </div>

    <!-- 确认按钮（仅抽屉模式显示） -->
    <div v-if="mode === 'drawer' && modelValue.length > 0" class="selector-confirm">
      <n-button type="primary" @click="emit('confirm')">
        确认选择 ({{ modelValue.length }})
      </n-button>
    </div>
  </div>
</template>

<style scoped>
.source-selector {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* 分类筛选条 */
.source-category-bar {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.source-cat-tag {
  padding: 4px 12px;
  border-radius: 16px;
  font-size: 13px;
  color: #666;
  background: #f5f5f5;
  cursor: pointer;
  transition: all 0.2s;
  user-select: none;
}

.source-cat-tag.active {
  color: #fff;
  background: #18a058;
}

/* 可滚动区域 */
.source-scroll-area {
  max-height: 400px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* 加载 */
.source-loading {
  display: flex;
  justify-content: center;
  padding: 32px 0;
}

/* Fixed 源网格 */
.source-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.source-grid.mobile {
  grid-template-columns: 1fr;
}

.source-card {
  border: 1px solid #e8e8e8;
  border-radius: 8px;
  padding: 10px 12px;
  cursor: pointer;
  transition: border-color 0.2s, background 0.2s, box-shadow 0.2s;
  background: #fff;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.source-card:hover:not(.disabled) {
  border-color: #18a058;
  box-shadow: 0 2px 8px rgba(24, 160, 88, 0.1);
}

.source-card.selected {
  border: 2px solid #18a058;
  background: #f0faf4;
}

.source-card.disabled {
  border: 1px dashed #d0d0d0;
  background: #fafafa;
  cursor: not-allowed;
  opacity: 0.7;
}

.source-card-header {
  display: flex;
  align-items: center;
  gap: 6px;
}

.source-icon {
  font-size: 18px;
  flex-shrink: 0;
}

.source-name {
  font-size: 13px;
  font-weight: 500;
  color: #333;
  flex: 1;
}

.source-card.disabled .source-name {
  color: #999;
}

.source-badge {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 10px;
  flex-shrink: 0;
}

.source-badge.added {
  background: #e0e0e0;
  color: #999;
}

.source-badge.selected-badge {
  background: #d1f2e0;
  color: #18a058;
}

.source-badge.disabled-badge {
  background: #ffeaea;
  color: #e03030;
}

.source-desc {
  margin: 0;
  font-size: 12px;
  color: #999;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* 预览功能 */
.source-card-footer {
  display: flex;
  justify-content: flex-end;
  margin-top: 2px;
}

.preview-link {
  font-size: 12px;
  color: #999;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  user-select: none;
  transition: color 0.2s;
}

.preview-link:hover,
.preview-link.active {
  color: #18a058;
}

.preview-row {
  grid-column: 1 / -1;
  background: #f9fafb;
  border: 1px solid #e8e8e8;
  border-radius: 6px;
  padding: 8px 12px;
}

.preview-row-label {
  font-size: 12px;
  color: #666;
  font-weight: 500;
}

.preview-list {
  margin: 4px 0 0;
  padding: 0 0 0 16px;
  list-style: disc;
}

.preview-list li {
  font-size: 12px;
  color: #555;
  line-height: 1.8;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.preview-item-link {
  color: #18a058;
  text-decoration: none;
  cursor: pointer;
}

.preview-item-link:hover {
  text-decoration: underline;
  color: #0e7a3e;
}

.preview-item-text {
  color: #555;
}

.preview-tooltip-content {
  font-size: 12px;
  line-height: 1.5;
}

.preview-tooltip-content p {
  margin: 0 0 4px;
  color: #e8e8e8;
}

.preview-tooltip-time {
  color: #aaa;
  font-size: 11px;
}

.preview-error {
  margin: 0;
  font-size: 12px;
  color: #e03030;
}

.source-help-link {
  display: inline-block;
  margin-top: 6px;
  font-size: 12px;
  color: #18a058;
  text-decoration: none;
}
.source-help-link:hover {
  text-decoration: underline;
}
.source-help-text {
  margin: 6px 0 0;
  font-size: 12px;
  color: #999;
  line-height: 1.4;
}

/* Parameterized 源 */
.param-sources {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.param-source-item {
  border: 1px solid #e8e8e8;
  border-radius: 8px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.param-source-header {
  display: flex;
  align-items: center;
  gap: 6px;
}

.param-input-row {
  display: flex;
  gap: 8px;
  align-items: center;
}

.param-error {
  margin: 0;
  font-size: 12px;
  color: #e03030;
}

.param-result {
  margin-top: 2px;
}

.param-unavailable {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  background: #fff8e6;
  border-radius: 4px;
  border: 1px solid #ffcb3d;
}

.param-unavailable-msg {
  font-size: 12px;
  color: #b76a00;
  flex: 1;
}

/* 已选摘要 */
.selected-summary {
  border-top: 1px solid #f0f0f0;
  padding-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.selected-count {
  font-size: 13px;
  color: #666;
}

.selected-tags {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

/* 确认按钮 */
.selector-confirm {
  display: flex;
  justify-content: flex-end;
}
</style>
