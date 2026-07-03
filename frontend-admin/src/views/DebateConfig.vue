<script setup lang="ts">
/**
 * 辩论配置页面
 *
 * 功能：
 * - 查看专家列表
 * - 配置辩论参数（轮次、专家数量等）
 * - 模型分配配置
 * - 专家模型配置（v1.3 新增：可配置每个等级使用的模型）
 * - 主持人风格配置（v2.0 新增：预设选择）
 */

import { ref, reactive, computed, onMounted, watch } from 'vue'
import {
  NCard,
  NButton,
  NSpace,
  NGrid,
  NGridItem,
  NTag,
  NSlider,
  NInputNumber,
  NIcon,
  NEmpty,
  NSpin,
  NForm,
  NFormItem,
  NAlert,
  NDivider,
  NSelect,
  NTabs,
  NTabPane,
  NStatistic,
  NCollapse,
  NCollapseItem,
  NInput,
  useMessage,
  useDialog
} from 'naive-ui'
import {
  SpeedometerOutline,
  InformationCircleOutline,
  SaveOutline,
  RefreshOutline,
  FlashOutline,
  SparklesOutline,
  SettingsOutline,
  TimeOutline,
  DocumentTextOutline,
  CheckmarkCircleOutline
} from '@vicons/ionicons5'
import {
  getModelConfig,
  getRoleModels,
  updateRoleModels,
  getExpertsConfig,
  buildModelTierMap,
  MODEL_TIER_MAP,
  type AvailableModel,
  type ExpertConfig,
  // v2.0 新增
  getDebateConfigV2,
  updateDebateParamsV2,
  updateCoordinatorStyle,
  resetDebateConfigV2,
  getDebatePresets,
  previewDebateConfig,
  previewExperts,
  PRESET_INFO,
  type DebateParamsV2,
  type DebatePresetSummary,
  type ConfigPreview,
  type PreviewExpertItem,
  type PreviewExpertsResponse
} from '../api/super-agent'

const message = useMessage()
const dialog = useDialog()

// ==================== Tab 控制 ====================
const activeTab = ref('style')  // v2.0: 默认显示风格配置

// ==================== 响应式数据 ====================

const loading = ref(false)
const experts = ref<ExpertConfig[]>([])

// 模型配置（只用于显示）
const modelTierMap = ref<Record<string, string>>({ ...MODEL_TIER_MAP })

// 专家模型配置
const availableModels = ref<AvailableModel[]>([])
const expertModels = reactive({
  debate_expert_high: '',
  debate_expert_mid: '',
  debate_expert_low: '',
  debate_coordinator: ''
})
const originalExpertModels = ref<string>('')
const expertModelSaving = ref(false)

const hasExpertModelChanges = computed(() => {
  return JSON.stringify(expertModels) !== originalExpertModels.value
})

// ==================== 计算属性 ====================

const expertsByTier = computed(() => {
  const result: Record<string, ExpertConfig[]> = {
    high: [],
    mid: [],
    low: []
  }
  experts.value.forEach(expert => {
    const tier = expert.model_tier || 'mid'
    if (result[tier]) {
      result[tier].push(expert)
    }
  })
  return result
})

const expertStats = computed(() => {
  const total = experts.value.length
  const active = experts.value.filter(e => e.is_active).length
  const categories = new Set(experts.value.map(e => e.category)).size
  return { total, active, inactive: total - active, categories }
})

// v2.0: 主持人风格配置
const styleConfig = reactive<{
  params: DebateParamsV2
  coordinator_style: { preset_id: string }
  version: number
}>({
  params: {
    default_rounds: 2,
    expert_count: 3,
    timeout_seconds: 120,
    speech_min_length: 200,
    speech_max_length: 400
  },
  coordinator_style: { preset_id: 'standard' },
  version: 0
})
const originalStyleConfig = ref<string>('')
const presets = ref<DebatePresetSummary[]>([])
const preview = ref<ConfigPreview | null>(null)
const styleSaving = ref(false)

const hasStyleChanges = computed(() => {
  return JSON.stringify(styleConfig) !== originalStyleConfig.value
})

// ==================== 方法 ====================

async function loadData() {
  loading.value = true
  try {
    const [expertsRes, modelRes, roleRes, styleRes, presetsRes] = await Promise.all([
      getExpertsConfig(),
      getModelConfig(),
      getRoleModels(),
      getDebateConfigV2().catch(() => null),
      getDebatePresets().catch(() => [])
    ])
    experts.value = expertsRes.experts || []

    // 更新模型显示名称
    modelTierMap.value = buildModelTierMap(modelRes)

    // 加载可用模型和专家模型配置
    availableModels.value = modelRes.available_models || []
    expertModels.debate_expert_high = roleRes.roles?.debate_expert_high?.model || ''
    expertModels.debate_expert_mid = roleRes.roles?.debate_expert_mid?.model || ''
    expertModels.debate_expert_low = roleRes.roles?.debate_expert_low?.model || ''
    expertModels.debate_coordinator = roleRes.roles?.debate_coordinator?.model || ''
    originalExpertModels.value = JSON.stringify(expertModels)

    // v2.0: 加载风格配置
    if (styleRes) {
      styleConfig.params = styleRes.params
      styleConfig.coordinator_style = styleRes.coordinator_style
      styleConfig.version = styleRes.version
      originalStyleConfig.value = JSON.stringify(styleConfig)
    }
    presets.value = presetsRes || []

    // 计算预览
    await updatePreview()
  } catch (error: any) {
    message.error(error.message || '加载数据失败')
  } finally {
    loading.value = false
  }
}

// 保存专家模型配置
async function handleSaveExpertModels() {
  expertModelSaving.value = true
  try {
    const updates: Record<string, string> = {}
    const original = JSON.parse(originalExpertModels.value)
    for (const [role, model] of Object.entries(expertModels)) {
      if (model !== original[role]) {
        updates[role] = model as string
      }
    }
    await updateRoleModels(updates)
    originalExpertModels.value = JSON.stringify(expertModels)
    message.success('专家模型配置已保存')
  } catch (error: any) {
    message.error(error.message || '保存失败')
  } finally {
    expertModelSaving.value = false
  }
}

function handleResetExpertModels() {
  const original = JSON.parse(originalExpertModels.value)
  Object.assign(expertModels, original)
  message.info('已重置专家模型配置')
}

// v2.0 新增：更新预览
async function updatePreview() {
  try {
    preview.value = await previewDebateConfig(styleConfig.params)
  } catch (error) {
    // 忽略预览错误
  }
}

// v2.0 新增：选择预设
async function handleSelectPreset(presetId: string) {
  styleConfig.coordinator_style.preset_id = presetId
}

// v2.0 新增：保存风格配置
async function handleSaveStyleConfig() {
  styleSaving.value = true
  try {
    // 先保存参数
    const paramsRes = await updateDebateParamsV2(styleConfig.params, styleConfig.version)
    styleConfig.version = paramsRes.version

    // 再保存风格
    const styleRes = await updateCoordinatorStyle(
      styleConfig.coordinator_style.preset_id,
      styleConfig.version
    )
    styleConfig.version = styleRes.version

    originalStyleConfig.value = JSON.stringify(styleConfig)
    message.success('配置已保存')
  } catch (error: any) {
    if (error.response?.status === 409) {
      dialog.warning({
        title: '保存失败',
        content: '配置已被他人修改，请刷新页面后重试',
        positiveText: '刷新',
        onPositiveClick: () => {
          loadData()
        }
      })
    } else {
      message.error(error.message || '保存失败')
    }
  } finally {
    styleSaving.value = false
  }
}

// v2.0 新增：重置风格配置
async function handleResetStyleConfig() {
  dialog.warning({
    title: '确认重置',
    content: '确定要重置为默认配置吗？',
    positiveText: '确定',
    negativeText: '取消',
    onPositiveClick: async () => {
      styleSaving.value = true
      try {
        const res = await resetDebateConfigV2()
        styleConfig.params = res.params
        styleConfig.coordinator_style = res.coordinator_style
        styleConfig.version = res.version
        originalStyleConfig.value = JSON.stringify(styleConfig)
        await updatePreview()
        message.success('已重置为默认配置')
      } catch (error: any) {
        message.error(error.message || '重置失败')
      } finally {
        styleSaving.value = false
      }
    }
  })
}

// 监听参数变化，更新预览
watch(() => styleConfig.params, () => {
  updatePreview()
}, { deep: true })

function getModelTierColor(tier: string): string {
  const colors: Record<string, string> = {
    high: '#18a058',
    mid: '#2080f0',
    low: '#909399'
  }
  return colors[tier] ?? colors['mid'] ?? '#2080f0'
}

function getModelTierIcon(tier: string): typeof FlashOutline {
  const icons: Record<string, typeof FlashOutline> = {
    high: FlashOutline,
    mid: SparklesOutline,
    low: SpeedometerOutline
  }
  return icons[tier] || SpeedometerOutline
}

function getPresetIcon(presetId: string): string {
  return PRESET_INFO[presetId]?.icon || '📋'
}

function getPresetColor(presetId: string): string {
  return PRESET_INFO[presetId]?.color || '#2080f0'
}

const categoryColorMap: Record<string, string> = {
  '基础': '#2080f0',
  '教育': '#18a058',
  '批判': '#d03050',
  '未来': '#8a2be2',
  '政策': '#f0a020',
  '实践': '#36ad6a',
}

// ==================== 专家匹配预览 ====================
const previewTopic = ref('')
const previewResult = ref<PreviewExpertsResponse | null>(null)
const previewLoading = ref(false)

const previewSummary = computed(() => {
  if (!previewResult.value) return `将匹配 ${styleConfig.params.expert_count} 位专家参与辩论`
  return `已匹配 ${previewResult.value.experts.length} 位专家`
})

async function handlePreviewExperts() {
  if (!previewTopic.value.trim()) {
    message.warning('请输入话题')
    return
  }
  previewLoading.value = true
  try {
    previewResult.value = await previewExperts(
      previewTopic.value,
      styleConfig.params.expert_count
    )
  } catch (e: any) {
    message.error('匹配预览失败: ' + (e.message || e))
  } finally {
    previewLoading.value = false
  }
}

// ==================== 生命周期 ====================

onMounted(() => {
  loadData()
})
</script>

<template>
  <div class="debate-config">
    <!-- 提示信息 -->
    <NAlert type="info" class="info-alert" closable>
      <template #icon>
        <NIcon :component="InformationCircleOutline" />
      </template>
      辩论功能允许多个AI专家从不同视角分析问题，通过多轮对话形成综合结论。
      配置更改后需要点击"保存配置"才会生效。
    </NAlert>

    <!-- v2.0 新增：Tab 导航 -->
    <NTabs v-model:value="activeTab" type="line" animated>
      <!-- Tab 1: 主持人风格配置 -->
      <NTabPane name="style" tab="主持人风格">
        <NGrid :cols="24" :x-gap="16" :y-gap="16">
          <!-- 左侧：风格选择和参数 -->
          <NGridItem :span="16">
            <!-- 风格预设选择 -->
            <NCard title="主持人风格" size="small">
              <template #header-extra>
                <NSpace>
                  <NButton
                    size="small"
                    :disabled="!hasStyleChanges"
                    @click="handleResetStyleConfig"
                  >
                    重置
                  </NButton>
                  <NButton
                    type="primary"
                    size="small"
                    :loading="styleSaving"
                    :disabled="!hasStyleChanges"
                    @click="handleSaveStyleConfig"
                  >
                    <template #icon>
                      <NIcon :component="SaveOutline" />
                    </template>
                    保存配置
                  </NButton>
                </NSpace>
              </template>

              <div class="preset-cards">
                <div
                  v-for="preset in presets"
                  :key="preset.id"
                  class="preset-card"
                  :class="{ active: styleConfig.coordinator_style.preset_id === preset.id }"
                  @click="handleSelectPreset(preset.id)"
                >
                  <div class="preset-icon">{{ getPresetIcon(preset.id) }}</div>
                  <div class="preset-info">
                    <div class="preset-name">{{ preset.name }}</div>
                    <div class="preset-desc">{{ preset.description }}</div>
                  </div>
                  <NIcon
                    v-if="styleConfig.coordinator_style.preset_id === preset.id"
                    :component="CheckmarkCircleOutline"
                    class="check-icon"
                    :color="getPresetColor(preset.id)"
                  />
                </div>
              </div>
            </NCard>

            <!-- 辩论参数 -->
            <NCard title="辩论参数" size="small" style="margin-top: 16px">
              <NForm label-placement="left" label-width="120">
                <NFormItem label="默认轮次">
                  <NSpace align="center">
                    <NSlider
                      v-model:value="styleConfig.params.default_rounds"
                      :min="1"
                      :max="5"
                      :step="1"
                      style="width: 180px"
                    />
                    <NInputNumber
                      v-model:value="styleConfig.params.default_rounds"
                      :min="1"
                      :max="5"
                      size="small"
                      style="width: 100px"
                    />
                    <span class="hint-text">轮</span>
                  </NSpace>
                </NFormItem>

                <NFormItem label="专家数量">
                  <NSpace align="center">
                    <NSlider
                      v-model:value="styleConfig.params.expert_count"
                      :min="2"
                      :max="5"
                      :step="1"
                      style="width: 180px"
                    />
                    <NInputNumber
                      v-model:value="styleConfig.params.expert_count"
                      :min="2"
                      :max="5"
                      size="small"
                      style="width: 100px"
                    />
                    <span class="hint-text">位</span>
                  </NSpace>
                </NFormItem>

                <NFormItem label="单轮超时">
                  <NSpace align="center">
                    <NSlider
                      v-model:value="styleConfig.params.timeout_seconds"
                      :min="60"
                      :max="300"
                      :step="30"
                      style="width: 180px"
                    />
                    <NInputNumber
                      v-model:value="styleConfig.params.timeout_seconds"
                      :min="60"
                      :max="300"
                      size="small"
                      style="width: 100px"
                    />
                    <span class="hint-text">秒</span>
                  </NSpace>
                </NFormItem>

                <NDivider>发言字数</NDivider>

                <NFormItem label="最少字数">
                  <NSpace align="center">
                    <NSlider
                      v-model:value="styleConfig.params.speech_min_length"
                      :min="100"
                      :max="300"
                      :step="50"
                      style="width: 180px"
                    />
                    <NInputNumber
                      v-model:value="styleConfig.params.speech_min_length"
                      :min="100"
                      :max="300"
                      size="small"
                      style="width: 100px"
                    />
                    <span class="hint-text">字</span>
                  </NSpace>
                </NFormItem>

                <NFormItem label="最多字数">
                  <NSpace align="center">
                    <NSlider
                      v-model:value="styleConfig.params.speech_max_length"
                      :min="200"
                      :max="500"
                      :step="50"
                      style="width: 180px"
                    />
                    <NInputNumber
                      v-model:value="styleConfig.params.speech_max_length"
                      :min="200"
                      :max="500"
                      size="small"
                      style="width: 100px"
                    />
                    <span class="hint-text">字</span>
                  </NSpace>
                </NFormItem>
              </NForm>
            </NCard>
          </NGridItem>

          <!-- 右侧：配置预览 -->
          <NGridItem :span="8">
            <NCard title="配置预览" size="small">
              <template #header-extra>
                <NIcon :component="TimeOutline" />
              </template>

              <div class="preview-stats" v-if="preview">
                <div class="stat-item">
                  <NStatistic label="总发言次数" :value="preview.total_speeches">
                    <template #suffix>次</template>
                  </NStatistic>
                </div>
                <div class="stat-item">
                  <NStatistic label="预计时长">
                    <template #default>
                      {{ preview.estimated_duration_min }}-{{ preview.estimated_duration_max }}
                    </template>
                    <template #suffix>分钟</template>
                  </NStatistic>
                </div>
                <div class="stat-item">
                  <NStatistic label="预计字数">
                    <template #default>
                      {{ (preview.estimated_words_min / 1000).toFixed(1) }}-{{ (preview.estimated_words_max / 1000).toFixed(1) }}
                    </template>
                    <template #suffix>K字</template>
                  </NStatistic>
                </div>
              </div>

              <NDivider />

              <div class="flow-description">
                <NIcon :component="DocumentTextOutline" />
                <span>{{ preview?.flow_description || '加载中...' }}</span>
              </div>
            </NCard>

            <!-- 配置说明 -->
            <NCard title="说明" size="small" style="margin-top: 16px">
              <div class="help-list">
                <div class="help-item">
                  <strong>标准风格</strong>：结构化引导，详细总结，适合大多数场景
                </div>
                <div class="help-item">
                  <strong>简洁风格</strong>：快速推进，简短总结，适合快速决策
                </div>
                <div class="help-item">
                  <strong>学术风格</strong>：严谨学术，深度分析，适合学术研讨
                </div>
              </div>
            </NCard>
          </NGridItem>
        </NGrid>

        <!-- 专家匹配预览（折叠面板） -->
        <NCollapse style="margin-top: 16px">
          <NCollapseItem :title="previewSummary" name="preview">
            <NSpace vertical :size="12">
              <NSpace>
                <NInput
                  v-model:value="previewTopic"
                  placeholder="输入话题测试匹配效果..."
                  style="width: 400px"
                  @keyup.enter="handlePreviewExperts"
                />
                <NButton
                  type="primary"
                  :loading="previewLoading"
                  @click="handlePreviewExperts"
                >
                  测试匹配
                </NButton>
              </NSpace>

              <div v-if="previewResult" class="preview-result">
                <div
                  v-for="(expert, idx) in previewResult.experts"
                  :key="expert.id"
                  class="preview-expert-row"
                >
                  <span class="preview-rank">#{{ idx + 1 }}</span>
                  <span class="preview-name">{{ expert.name }}（{{ expert.title }}）</span>
                  <NTag size="tiny" :type="expert.model_tier === 'high' ? 'success' : expert.model_tier === 'low' ? 'default' : 'warning'">
                    {{ expert.model_tier }}
                  </NTag>
                  <span class="preview-score">匹配分: {{ expert.score }}</span>
                  <span class="preview-keywords" v-if="expert.matched_keywords.length">
                    命中: {{ expert.matched_keywords.join(', ') }}
                  </span>
                </div>

                <div class="preview-tier-summary">
                  Tier 分布:
                  high×{{ previewResult.tier_distribution.high || 0 }}
                  mid×{{ previewResult.tier_distribution.mid || 0 }}
                  low×{{ previewResult.tier_distribution.low || 0 }}
                </div>
              </div>
            </NSpace>
          </NCollapseItem>
        </NCollapse>
      </NTabPane>

      <!-- Tab 2: AI 模型 -->
      <NTabPane name="experts" tab="AI 模型">
        <NGrid :cols="2" :x-gap="16" :y-gap="16">
          <!-- 左侧：专家模型配置 -->
          <NGridItem>
            <NCard title="专家模型配置" size="small">
              <template #header-extra>
                <NSpace>
                  <NButton
                    size="small"
                    :disabled="!hasExpertModelChanges"
                    @click="handleResetExpertModels"
                  >
                    重置
                  </NButton>
                  <NButton
                    type="primary"
                    size="small"
                    :loading="expertModelSaving"
                    :disabled="!hasExpertModelChanges"
                    @click="handleSaveExpertModels"
                  >
                    <template #icon>
                      <NIcon :component="SaveOutline" />
                    </template>
                    保存配置
                  </NButton>
                </NSpace>
              </template>

              <NAlert type="info" style="margin-bottom: 16px">
                为不同等级的辩论专家配置使用的AI模型。高性能专家适合核心观点阐述，轻量专家适合快速补充。
              </NAlert>

              <NForm label-placement="left" label-width="140">
                <NFormItem label="高性能专家模型">
                  <NSelect
                    v-model:value="expertModels.debate_expert_high"
                    :options="availableModels.map(m => ({ label: `${m.name} (${m.provider})`, value: m.id }))"
                    placeholder="选择模型"
                    style="width: 100%"
                  />
                </NFormItem>

                <NFormItem label="标准专家模型">
                  <NSelect
                    v-model:value="expertModels.debate_expert_mid"
                    :options="availableModels.map(m => ({ label: `${m.name} (${m.provider})`, value: m.id }))"
                    placeholder="选择模型"
                    style="width: 100%"
                  />
                </NFormItem>

                <NFormItem label="轻量专家模型">
                  <NSelect
                    v-model:value="expertModels.debate_expert_low"
                    :options="availableModels.map(m => ({ label: `${m.name} (${m.provider})`, value: m.id }))"
                    placeholder="选择模型"
                    style="width: 100%"
                  />
                </NFormItem>

                <NFormItem label="主持人模型">
                  <NSelect
                    v-model:value="expertModels.debate_coordinator"
                    :options="availableModels.map(m => ({ label: `${m.name} (${m.provider})`, value: m.id }))"
                    placeholder="选择模型"
                    style="width: 100%"
                  />
                </NFormItem>
              </NForm>
            </NCard>
          </NGridItem>

          <!-- 右侧：专家概览（只读） -->
          <NGridItem>
            <NCard title="专家概览" size="small">
              <template #header-extra>
                <NButton size="small" @click="loadData" :loading="loading">
                  <template #icon>
                    <NIcon :component="RefreshOutline" />
                  </template>
                  刷新
                </NButton>
              </template>

              <NSpin :show="loading">
                <!-- 统计 header -->
                <div class="expert-overview-stats">
                  <div class="stat-row">
                    <span>总数: <strong>{{ expertStats.total }}</strong></span>
                    <span>启用: <strong>{{ expertStats.active }}</strong></span>
                    <span>分类: <strong>{{ expertStats.categories }}</strong></span>
                  </div>
                  <!-- Tier 分布条形图 -->
                  <div class="tier-distribution">
                    <div class="tier-bar-row" v-for="tier in ['high', 'mid', 'low']" :key="tier">
                      <span class="tier-label-text">{{ tier }}</span>
                      <div class="tier-bar-track">
                        <div
                          class="tier-bar-fill"
                          :class="tier"
                          :style="{ width: experts.length ? (expertsByTier[tier].length / experts.length * 100) + '%' : '0%' }"
                        />
                      </div>
                      <span class="tier-count-text">{{ expertsByTier[tier].length }}</span>
                    </div>
                  </div>
                </div>

                <NDivider style="margin: 12px 0" />

                <!-- 只读专家列表 -->
                <div class="expert-readonly-list" v-if="experts.length > 0">
                  <div
                    v-for="expert in experts"
                    :key="expert.id"
                    class="expert-readonly-item"
                    :style="{ opacity: expert.is_active ? 1 : 0.5 }"
                  >
                    <div class="expert-readonly-left">
                      <span class="expert-readonly-dot" :class="expert.is_active ? 'active' : 'inactive'" />
                      <span class="expert-readonly-name">{{ expert.name }}</span>
                      <NTag size="tiny" :bordered="false" :color="{ color: (categoryColorMap[expert.category] || '#909399') + '20', textColor: categoryColorMap[expert.category] || '#909399' }">
                        {{ expert.category }}
                      </NTag>
                    </div>
                    <NTag size="tiny" :type="expert.model_tier === 'high' ? 'success' : expert.model_tier === 'low' ? 'default' : 'warning'">
                      {{ expert.model_tier }}
                    </NTag>
                  </div>
                </div>

                <NEmpty v-else description="暂无专家配置" />

                <!-- 跳转链接 -->
                <div class="expert-goto-link">
                  <NButton text type="primary" @click="$router.push('/super-agent/experts')">
                    前往专家管理 →
                  </NButton>
                </div>
              </NSpin>
            </NCard>
          </NGridItem>
        </NGrid>
      </NTabPane>
    </NTabs>
  </div>
</template>

<style scoped lang="scss">
.debate-config {
  .info-alert {
    margin-bottom: 16px;
  }

  // v2.0 新增：预设卡片样式
  .preset-cards {
    display: flex;
    gap: 16px;
    flex-wrap: wrap;

    .preset-card {
      flex: 1;
      min-width: 150px;
      max-width: 200px;
      padding: 16px;
      border: 2px solid #e8e8e8;
      border-radius: 12px;
      cursor: pointer;
      transition: all 0.2s ease;
      position: relative;

      &:hover {
        border-color: #2080f0;
        background: #f0f7ff;
      }

      &.active {
        border-color: #18a058;
        background: #f0fff4;
      }

      .preset-icon {
        font-size: 32px;
        margin-bottom: 8px;
      }

      .preset-info {
        .preset-name {
          font-size: 16px;
          font-weight: 600;
          margin-bottom: 4px;
        }
        .preset-desc {
          font-size: 12px;
          color: #666;
        }
      }

      .check-icon {
        position: absolute;
        top: 12px;
        right: 12px;
        font-size: 20px;
      }
    }
  }

  // v2.0 新增：预览统计
  .preview-stats {
    display: flex;
    flex-direction: column;
    gap: 12px;

    .stat-item {
      padding: 8px 0;
      border-bottom: 1px dashed #e8e8e8;

      &:last-child {
        border-bottom: none;
      }
    }
  }

  .flow-description {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    color: #666;
    font-size: 13px;
    line-height: 1.6;
  }

  // v2.0 新增：提示文字
  .hint-text {
    color: #999;
    font-size: 12px;
  }

  // v2.0 新增：帮助列表
  .help-list {
    display: flex;
    flex-direction: column;
    gap: 12px;

    .help-item {
      font-size: 13px;
      color: #666;
      line-height: 1.5;

      strong {
        color: #333;
      }
    }
  }

  .model-distribution-visual {
    margin-top: 16px;
    padding-top: 16px;
    border-top: 1px dashed #e8e8e8;

    .distribution-bar {
      display: flex;
      height: 20px;
      border-radius: 4px;
      overflow: hidden;
      background: #f5f5f5;

      .bar-segment {
        transition: width 0.3s ease;

        &.high {
          background: #18a058;
        }
        &.mid {
          background: #2080f0;
        }
        &.low {
          background: #909399;
        }
      }
    }

    .distribution-legend {
      display: flex;
      justify-content: center;
      gap: 24px;
      margin-top: 12px;

      .legend-item {
        display: flex;
        align-items: center;
        gap: 6px;
        font-size: 12px;
        color: #666;

        .dot {
          width: 10px;
          height: 10px;
          border-radius: 50%;

          &.high { background: #18a058; }
          &.mid { background: #2080f0; }
          &.low { background: #909399; }
        }
      }
    }
  }

  .expert-list {
    max-height: 500px;
    overflow-y: auto;

    .expert-item {
      padding: 12px;
      border-radius: 8px;
      background: #f9f9f9;
      margin-bottom: 8px;
      border-left: 3px solid transparent;

      &.customized {
        border-left-color: #f0a020;
        background: #fffbe6;
      }

      &:last-child {
        margin-bottom: 0;
      }

      .expert-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 8px;

        .expert-name {
          font-weight: 600;
        }
      }

      .expert-perspective {
        font-size: 13px;
        color: #333;
        margin-bottom: 4px;
      }

      .expert-model {
        display: flex;
        align-items: center;
        gap: 4px;
        font-size: 11px;
        color: #999;
        margin-top: 6px;

        .default-hint {
          color: #f0a020;
        }
      }

      .expert-description {
        font-size: 12px;
        color: #999;
      }
    }
  }

  .help-card {
    margin-top: 16px;

    .help-item {
      display: flex;
      gap: 12px;

      .help-content {
        h4 {
          margin: 0 0 8px 0;
          font-size: 14px;
          color: #333;
        }
        p {
          margin: 0;
          font-size: 13px;
          color: #666;
          line-height: 1.5;
        }
      }
    }
  }

  // 只读专家概览样式
  .expert-overview-stats {
    .stat-row {
      display: flex;
      gap: 20px;
      font-size: 13px;
      color: #666;
      margin-bottom: 12px;

      strong {
        color: #333;
        font-size: 15px;
      }
    }

    .tier-distribution {
      display: flex;
      flex-direction: column;
      gap: 6px;

      .tier-bar-row {
        display: flex;
        align-items: center;
        gap: 8px;

        .tier-label-text {
          width: 32px;
          font-size: 11px;
          color: #999;
          text-align: right;
        }

        .tier-bar-track {
          flex: 1;
          height: 8px;
          background: #f0f0f0;
          border-radius: 4px;
          overflow: hidden;

          .tier-bar-fill {
            height: 100%;
            border-radius: 4px;
            transition: width 0.3s ease;

            &.high { background: #18a058; }
            &.mid { background: #2080f0; }
            &.low { background: #909399; }
          }
        }

        .tier-count-text {
          width: 20px;
          font-size: 11px;
          color: #666;
        }
      }
    }
  }

  .expert-readonly-list {
    max-height: 400px;
    overflow-y: auto;

    .expert-readonly-item {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 6px 8px;
      border-radius: 4px;
      margin-bottom: 2px;

      &:hover {
        background: #f9f9f9;
      }

      .expert-readonly-left {
        display: flex;
        align-items: center;
        gap: 8px;

        .expert-readonly-dot {
          width: 6px;
          height: 6px;
          border-radius: 50%;

          &.active { background: #18a058; }
          &.inactive { background: #d0d0d0; }
        }

        .expert-readonly-name {
          font-size: 13px;
          font-weight: 500;
        }
      }
    }
  }

  .expert-goto-link {
    text-align: center;
    margin-top: 12px;
    padding-top: 12px;
    border-top: 1px dashed #e8e8e8;
  }

  // 匹配预览样式
  .preview-result {
    .preview-expert-row {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 8px 12px;
      border-radius: 6px;
      background: #f9f9f9;
      margin-bottom: 6px;
      font-size: 13px;

      .preview-rank {
        color: #999;
        font-weight: 600;
        width: 24px;
      }

      .preview-name {
        font-weight: 500;
        min-width: 160px;
      }

      .preview-score {
        color: #666;
        font-size: 12px;
      }

      .preview-keywords {
        color: #18a058;
        font-size: 12px;
      }
    }

    .preview-tier-summary {
      margin-top: 8px;
      font-size: 12px;
      color: #666;
      padding: 6px 12px;
      background: #f0f7ff;
      border-radius: 4px;
    }
  }
}
</style>
