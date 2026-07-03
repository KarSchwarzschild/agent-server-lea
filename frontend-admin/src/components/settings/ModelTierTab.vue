<script setup lang="ts">
import { ref, reactive, computed, onMounted, h } from 'vue'
import {
  NSpin, NAlert, NGrid, NGridItem, NIcon, NSelect, NDynamicInput,
  NSpace, NButton, NTag, NText,
  useMessage,
} from 'naive-ui'
import type { SelectOption, SelectRenderLabel, SelectRenderTag } from 'naive-ui'
import { FlashOutline, SparklesOutline, SpeedometerOutline, SaveOutline } from '@vicons/ionicons5'
import {
  getModelConfig,
  updateModelConfig,
  getModelFallback,
  updateModelFallback,
  type ModelConfigResponse,
  type AvailableModel,
} from '../../api/super-agent'

const props = defineProps<{
  availableModels: AvailableModel[]
}>()

const message = useMessage()

const modelLoading = ref(false)
const modelSaving = ref(false)
const modelConfig = ref<ModelConfigResponse | null>(null)
const selectedModels = reactive({
  high: '',
  mid: '',
  low: ''
})
const originalModels = ref<string>('')

const fallbackConfig = reactive<Record<string, string[]>>({
  high: [],
  mid: [],
  low: []
})
const originalFallback = ref<string>('')

const hasModelChanges = computed(() => {
  return JSON.stringify(selectedModels) !== originalModels.value ||
    JSON.stringify(fallbackConfig) !== originalFallback.value
})

async function loadModelConfig() {
  modelLoading.value = true
  try {
    const [res, fallbackRes] = await Promise.all([
      getModelConfig(),
      getModelFallback().catch(() => ({} as Record<string, string[]>))
    ])
    modelConfig.value = res
    selectedModels.high = res.tiers?.high?.model || ''
    selectedModels.mid = res.tiers?.mid?.model || ''
    selectedModels.low = res.tiers?.low?.model || ''
    originalModels.value = JSON.stringify(selectedModels)

    const fbTiers = (fallbackRes as any)?.tiers ?? {}
    fallbackConfig.high = fbTiers?.high?.fallback ?? []
    fallbackConfig.mid = fbTiers?.mid?.fallback ?? []
    fallbackConfig.low = fbTiers?.low?.fallback ?? []
    originalFallback.value = JSON.stringify(fallbackConfig)
  } catch (e: any) {
    message.error(e.message || '加载模型配置失败')
  } finally {
    modelLoading.value = false
  }
}

async function saveModelConfig() {
  modelSaving.value = true
  try {
    const updates: { high?: string; mid?: string; low?: string } = {}
    const original = JSON.parse(originalModels.value)
    if (selectedModels.high !== original.high) updates.high = selectedModels.high
    if (selectedModels.mid !== original.mid) updates.mid = selectedModels.mid
    if (selectedModels.low !== original.low) updates.low = selectedModels.low

    const tierChanged = Object.keys(updates).length > 0
    const originalFb = JSON.parse(originalFallback.value)
    const fbChanged = JSON.stringify(fallbackConfig) !== JSON.stringify(originalFb)

    if (!tierChanged && !fbChanged) {
      message.info('没有检测到配置变更')
      return
    }

    if (tierChanged) {
      await updateModelConfig(updates)
    }

    if (fbChanged) {
      await updateModelFallback({
        high: fallbackConfig.high,
        mid: fallbackConfig.mid,
        low: fallbackConfig.low
      })
    }

    await loadModelConfig()
    message.success('模型配置保存成功')
  } catch (e: any) {
    message.error(e.message || '保存模型配置失败')
  } finally {
    modelSaving.value = false
  }
}

function resetModelConfig() {
  const original = JSON.parse(originalModels.value)
  Object.assign(selectedModels, original)
  const originalFb = JSON.parse(originalFallback.value)
  fallbackConfig.high = originalFb.high ?? []
  fallbackConfig.mid = originalFb.mid ?? []
  fallbackConfig.low = originalFb.low ?? []
  message.info('已重置模型配置')
}

const modelOptions = computed(() =>
  props.availableModels.map(m => ({
    label: m.name,
    value: m.id,
    provider: m.provider,
    modelId: m.id,
  }))
)

// Select2 风格：下拉选项渲染（显示名 + provider 标签 + model ID）
const renderModelOption: SelectRenderLabel = (option: SelectOption) => {
  const provider = (option as any).provider || ''
  const modelId = (option as any).modelId || option.value
  return h('div', { style: 'display:flex; align-items:center; gap:8px; padding:2px 0' }, [
    h('span', { style: 'font-weight:500' }, option.label as string),
    provider ? h(NTag, { size: 'tiny', type: 'info', bordered: false }, () => provider) : null,
    h(NText, { depth: 3, style: 'font-size:11px; margin-left:auto' }, () => String(modelId)),
  ])
}

// 搜索过滤：支持按名称、provider、model ID 搜索
function filterModel(pattern: string, option: SelectOption): boolean {
  const p = pattern.toLowerCase()
  const label = ((option.label as string) || '').toLowerCase()
  const provider = ((option as any).provider || '').toLowerCase()
  const modelId = ((option as any).modelId || '').toLowerCase()
  return label.includes(p) || provider.includes(p) || modelId.includes(p)
}

// Select2 风格：已选标签渲染
const renderModelTag: SelectRenderTag = ({ option }) => {
  const provider = (option as any).provider || ''
  return h('div', { style: 'display:flex; align-items:center; gap:4px' }, [
    h('span', null, option.label as string),
    provider ? h(NTag, { size: 'tiny', type: 'info', bordered: false, style: 'margin-left:2px' }, () => provider) : null,
  ])
}

onMounted(() => { loadModelConfig() })

defineExpose({ loadModelConfig })
</script>

<template>
  <n-spin :show="modelLoading">
    <div class="tab-content">
      <n-alert type="info" title="说明" style="margin-bottom: 20px">
        配置各等级使用的AI模型。这是旧版配置，建议使用"AI模型（角色）"进行更精细的控制。
      </n-alert>

      <n-grid :cols="3" :x-gap="16">
        <n-grid-item>
          <div class="model-tier-card high">
            <div class="tier-header">
              <n-icon :component="FlashOutline" size="20" color="#18a058" />
              <span>高性能模型</span>
            </div>
            <div class="tier-desc">用于辩论专家、综述生成、复杂任务</div>
            <n-select
              v-model:value="selectedModels.high"
              :options="modelOptions"
              filterable
              :filter="filterModel"
              :render-label="renderModelOption"
              :render-tag="renderModelTag"
              placeholder="搜索或选择模型"
            />
            <div class="fallback-section">
              <div class="fallback-label">备选模型（主模型不可用时按顺序尝试）</div>
              <n-dynamic-input
                v-model:value="fallbackConfig.high"
                :min="0"
                :max="5"
              >
                <template #default="{ value, index }">
                  <n-select
                    :value="fallbackConfig.high[index]"
                    @update:value="(v: string) => fallbackConfig.high[index] = v"
                    :options="modelOptions"
                    filterable
                    :filter="filterModel"
                    :render-label="renderModelOption"
                    placeholder="搜索备选模型"
                    style="flex: 1"
                  />
                </template>
              </n-dynamic-input>
            </div>
          </div>
        </n-grid-item>
        <n-grid-item>
          <div class="model-tier-card mid">
            <div class="tier-header">
              <n-icon :component="SparklesOutline" size="20" color="#2080f0" />
              <span>标准模型</span>
            </div>
            <div class="tier-desc">用于Skill执行、一般对话</div>
            <n-select
              v-model:value="selectedModels.mid"
              :options="modelOptions"
              filterable
              :filter="filterModel"
              :render-label="renderModelOption"
              :render-tag="renderModelTag"
              placeholder="搜索或选择模型"
            />
            <div class="fallback-section">
              <div class="fallback-label">备选模型（主模型不可用时按顺序尝试）</div>
              <n-dynamic-input
                v-model:value="fallbackConfig.mid"
                :min="0"
                :max="5"
              >
                <template #default="{ value, index }">
                  <n-select
                    :value="fallbackConfig.mid[index]"
                    @update:value="(v: string) => fallbackConfig.mid[index] = v"
                    :options="modelOptions"
                    filterable
                    :filter="filterModel"
                    :render-label="renderModelOption"
                    placeholder="搜索备选模型"
                    style="flex: 1"
                  />
                </template>
              </n-dynamic-input>
            </div>
          </div>
        </n-grid-item>
        <n-grid-item>
          <div class="model-tier-card low">
            <div class="tier-header">
              <n-icon :component="SpeedometerOutline" size="20" color="#909399" />
              <span>轻量模型</span>
            </div>
            <div class="tier-desc">用于意图识别、快速分类</div>
            <n-select
              v-model:value="selectedModels.low"
              :options="modelOptions"
              filterable
              :filter="filterModel"
              :render-label="renderModelOption"
              :render-tag="renderModelTag"
              placeholder="搜索或选择模型"
            />
            <div class="fallback-section">
              <div class="fallback-label">备选模型（主模型不可用时按顺序尝试）</div>
              <n-dynamic-input
                v-model:value="fallbackConfig.low"
                :min="0"
                :max="5"
              >
                <template #default="{ value, index }">
                  <n-select
                    :value="fallbackConfig.low[index]"
                    @update:value="(v: string) => fallbackConfig.low[index] = v"
                    :options="modelOptions"
                    filterable
                    :filter="filterModel"
                    :render-label="renderModelOption"
                    placeholder="搜索备选模型"
                    style="flex: 1"
                  />
                </template>
              </n-dynamic-input>
            </div>
          </div>
        </n-grid-item>
      </n-grid>

      <n-space style="margin-top: 24px">
        <n-button @click="resetModelConfig" :disabled="!hasModelChanges">
          重置
        </n-button>
        <n-button type="primary" :loading="modelSaving" :disabled="!hasModelChanges" @click="saveModelConfig">
          <template #icon>
            <n-icon :component="SaveOutline" />
          </template>
          保存模型配置
        </n-button>
      </n-space>
    </div>
  </n-spin>
</template>

<style scoped>
.model-tier-card {
  padding: 16px;
  border-radius: 8px;
  background: #f9f9f9;
  border-left: 3px solid #e8e8e8;
}
.model-tier-card.high { border-left-color: #18a058; }
.model-tier-card.mid { border-left-color: #2080f0; }
.model-tier-card.low { border-left-color: #909399; }
.tier-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  margin-bottom: 8px;
}
.tier-desc {
  font-size: 12px;
  color: #999;
  margin-bottom: 12px;
}
.fallback-section {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px dashed #e0e0e0;
}
.fallback-label {
  font-size: 12px;
  color: #888;
  margin-bottom: 8px;
}
</style>
