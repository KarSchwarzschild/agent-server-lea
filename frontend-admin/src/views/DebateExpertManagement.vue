<script setup lang="ts">
/**
 * 辩论专家管理页面
 *
 * 功能：
 * - 专家列表（卡片视图）
 * - 新增/编辑专家（Drawer）
 * - 启用/禁用、删除
 * - 重置为默认配置
 */
import { ref, computed, onMounted, reactive } from 'vue'
import {
  NCard,
  NButton,
  NInput,
  NSelect,
  NTag,
  NSwitch,
  NSpin,
  NEmpty,
  NGrid,
  NGridItem,
  NStatistic,
  NDrawer,
  NDrawerContent,
  NForm,
  NFormItem,
  NInputNumber,
  NDynamicTags,
  NPopconfirm,
  NPopover,
  NRadioGroup,
  NRadio,
  NSpace,
  NBadge,
  useMessage,
  useDialog,
} from 'naive-ui'
import {
  getExpertsConfig,
  createExpert,
  updateExpert,
  deleteExpert,
  toggleExpert,
  resetExperts,
  type ExpertConfig,
  type ExpertCreateRequest,
} from '../api/super-agent'

const message = useMessage()
const dialog = useDialog()
const loading = ref(false)
const experts = ref<ExpertConfig[]>([])
const searchText = ref('')
const filterCategory = ref<string | null>(null)

// ==================== 类别配置 ====================
const CATEGORIES = [
  { label: '基础', value: '基础', color: '#2080f0' },
  { label: '教育', value: '教育', color: '#18a058' },
  { label: '批判', value: '批判', color: '#d03050' },
  { label: '未来', value: '未来', color: '#8a2be2' },
  { label: '政策', value: '政策', color: '#f0a020' },
  { label: '实践', value: '实践', color: '#36ad6a' },
]

const TIER_OPTIONS = [
  { label: 'High（高性能）', value: 'high' },
  { label: 'Mid（标准）', value: 'mid' },
  { label: 'Low（轻量）', value: 'low' },
]

const CATEGORY_OPTIONS = CATEGORIES.map(c => ({ label: c.label, value: c.value }))

const categoryColorMap: Record<string, string> = Object.fromEntries(
  CATEGORIES.map(c => [c.value, c.color])
)

// ==================== 统计 ====================
const stats = computed(() => {
  const total = experts.value.length
  const active = experts.value.filter(e => e.is_active).length
  const highTier = experts.value.filter(e => e.model_tier === 'high' && e.is_active).length
  return { total, active, inactive: total - active, highTier }
})

// ==================== 过滤 ====================
const filteredExperts = computed(() => {
  let list = experts.value
  if (filterCategory.value) {
    list = list.filter(e => e.category === filterCategory.value)
  }
  if (searchText.value) {
    const q = searchText.value.toLowerCase()
    list = list.filter(e =>
      e.name.toLowerCase().includes(q) ||
      e.title.toLowerCase().includes(q) ||
      e.slug.toLowerCase().includes(q)
    )
  }
  return list
})

// ==================== Drawer 编辑 ====================
const drawerVisible = ref(false)
const isEditing = ref(false)
const editingId = ref<number | null>(null)
const formRef = ref()
const formData = ref<ExpertCreateRequest>({
  slug: '',
  name: '',
  title: '',
  category: '基础',
  perspective: '',
  persona: '',
  keywords: [],
  model_tier: 'mid',
  priority: 50,
})

function openCreate() {
  isEditing.value = false
  editingId.value = null
  formData.value = {
    slug: '', name: '', title: '', category: '基础',
    perspective: '', persona: '', keywords: [], model_tier: 'mid', priority: 50,
  }
  drawerVisible.value = true
}

function openEdit(expert: ExpertConfig) {
  isEditing.value = true
  editingId.value = expert.id
  formData.value = {
    slug: expert.slug,
    name: expert.name,
    title: expert.title,
    category: expert.category,
    perspective: expert.perspective,
    persona: expert.persona,
    keywords: [...expert.keywords],
    model_tier: expert.model_tier,
    priority: expert.priority,
  }
  drawerVisible.value = true
}

// ==================== API 操作 ====================
async function loadExperts() {
  loading.value = true
  try {
    const res = await getExpertsConfig()
    experts.value = res.experts
  } catch (e: any) {
    message.error('加载专家列表失败: ' + (e.message || e))
  } finally {
    loading.value = false
  }
}

async function handleSave() {
  try {
    if (isEditing.value && editingId.value) {
      // 更新：只发送变更字段
      const { slug, ...updateData } = formData.value
      await updateExpert(editingId.value, updateData)
      message.success('专家已更新')
    } else {
      await createExpert(formData.value)
      message.success('专家已创建')
    }
    drawerVisible.value = false
    await loadExperts()
  } catch (e: any) {
    message.error('保存失败: ' + (e.message || e))
  }
}

async function handleToggle(expert: ExpertConfig) {
  try {
    const res = await toggleExpert(expert.id)
    message.success(res.message)
    await loadExperts()
  } catch (e: any) {
    message.error('操作失败: ' + (e.message || e))
  }
}

async function handleDelete(expert: ExpertConfig) {
  try {
    await deleteExpert(expert.id)
    message.success(`专家 ${expert.name} 已删除`)
    await loadExperts()
  } catch (e: any) {
    message.error('删除失败: ' + (e.message || e))
  }
}

async function handleReset() {
  dialog.warning({
    title: '确认重置',
    content: '将清空所有专家数据并恢复为默认配置，此操作不可撤销。',
    positiveText: '确认重置',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        const res = await resetExperts()
        message.success(res.message)
        await loadExperts()
      } catch (e: any) {
        message.error('重置失败: ' + (e.message || e))
      }
    }
  })
}

function tierTagType(tier: string): 'error' | 'warning' | 'default' {
  if (tier === 'high') return 'error'
  if (tier === 'mid') return 'warning'
  return 'default'
}

// ==================== Tier 快速切换 ====================
const tierPopoverVisible = reactive<Record<number, boolean>>({})
const tierPopoverValue = reactive<Record<number, string>>({})
const tierSaving = ref<number | null>(null)

const TIER_DISPLAY: Record<string, { shape: string; label: string }> = {
  high: { shape: '●', label: 'HIGH' },
  mid: { shape: '◆', label: 'MID' },
  low: { shape: '○', label: 'LOW' },
}

function openTierPopover(expert: ExpertConfig) {
  tierPopoverValue[expert.id] = expert.model_tier
  tierPopoverVisible[expert.id] = true
}

function closeTierPopover(expertId: number) {
  tierPopoverVisible[expertId] = false
}

async function confirmTierChange(expert: ExpertConfig) {
  const newTier = tierPopoverValue[expert.id]
  if (newTier === expert.model_tier) return

  tierSaving.value = expert.id
  try {
    await updateExpert(expert.id, { model_tier: newTier })
    message.success(`${expert.name} tier 已更新为 ${newTier}`)
    closeTierPopover(expert.id)
    await loadExperts()
  } catch (e: any) {
    message.error('更新失败: ' + (e.message || e))
  } finally {
    tierSaving.value = null
  }
}

onMounted(loadExperts)
</script>

<template>
  <div class="expert-management">
    <!-- 统计卡片 -->
    <NGrid :cols="4" :x-gap="16" style="margin-bottom: 20px">
      <NGridItem>
        <NCard size="small">
          <NStatistic label="专家总数" :value="stats.total" />
        </NCard>
      </NGridItem>
      <NGridItem>
        <NCard size="small">
          <NStatistic label="已启用" :value="stats.active">
            <template #suffix>
              <span style="color: #18a058; font-size: 14px">/ {{ stats.total }}</span>
            </template>
          </NStatistic>
        </NCard>
      </NGridItem>
      <NGridItem>
        <NCard size="small">
          <NStatistic label="已禁用" :value="stats.inactive" />
        </NCard>
      </NGridItem>
      <NGridItem>
        <NCard size="small">
          <NStatistic label="高性能专家" :value="stats.highTier" />
        </NCard>
      </NGridItem>
    </NGrid>

    <!-- 操作栏 -->
    <NCard size="small" style="margin-bottom: 16px">
      <NSpace justify="space-between" align="center">
        <NSpace>
          <NInput
            v-model:value="searchText"
            placeholder="搜索专家..."
            clearable
            style="width: 200px"
          />
          <NSelect
            v-model:value="filterCategory"
            :options="[{ label: '全部类别', value: null }, ...CATEGORY_OPTIONS]"
            style="width: 140px"
          />
        </NSpace>
        <NSpace>
          <NButton type="primary" @click="openCreate">新增专家</NButton>
          <NButton @click="loadExperts">刷新</NButton>
          <NPopconfirm @positive-click="handleReset">
            <template #trigger>
              <NButton type="warning" secondary>重置为默认</NButton>
            </template>
            确认重置所有专家为默认配置？
          </NPopconfirm>
        </NSpace>
      </NSpace>
    </NCard>

    <!-- 专家卡片列表 -->
    <NSpin :show="loading">
      <NEmpty v-if="filteredExperts.length === 0 && !loading" description="暂无专家数据" />
      <NGrid :cols="3" :x-gap="16" :y-gap="16" v-else>
        <NGridItem v-for="expert in filteredExperts" :key="expert.id">
          <NCard
            size="small"
            hoverable
            :style="{ opacity: expert.is_active ? 1 : 0.55, borderLeft: `3px solid ${categoryColorMap[expert.category] || '#909399'}` }"
          >
            <template #header>
              <NSpace align="center" :size="8">
                <span>{{ expert.name }}（{{ expert.category }}专家）</span>
                <NPopover
                  trigger="manual"
                  :show="tierPopoverVisible[expert.id]"
                  @update:show="(v: boolean) => { if (!v) closeTierPopover(expert.id) }"
                >
                  <template #trigger>
                    <NTag
                      size="small"
                      :type="tierTagType(expert.model_tier)"
                      style="cursor: pointer"
                      @click="openTierPopover(expert)"
                    >
                      {{ TIER_DISPLAY[expert.model_tier]?.shape }} {{ TIER_DISPLAY[expert.model_tier]?.label }}
                    </NTag>
                  </template>
                  <div style="padding: 4px 0; min-width: 180px">
                    <div style="font-weight: 600; margin-bottom: 8px">修改 Tier</div>
                    <NRadioGroup v-model:value="tierPopoverValue[expert.id]">
                      <NSpace vertical>
                        <NRadio value="high">● HIGH（高性能）</NRadio>
                        <NRadio value="mid">◆ MID（标准）</NRadio>
                        <NRadio value="low">○ LOW（轻量）</NRadio>
                      </NSpace>
                    </NRadioGroup>
                    <NSpace justify="end" style="margin-top: 10px">
                      <NButton size="tiny" @click="closeTierPopover(expert.id)">取消</NButton>
                      <NButton
                        size="tiny"
                        type="primary"
                        :loading="tierSaving === expert.id"
                        :disabled="tierPopoverValue[expert.id] === expert.model_tier"
                        @click="confirmTierChange(expert)"
                      >
                        确认
                      </NButton>
                    </NSpace>
                  </div>
                </NPopover>
                <NTag v-if="!expert.is_active" size="small" type="default">已禁用</NTag>
              </NSpace>
            </template>
            <template #header-extra>
              <NSpace :size="4">
                <NSwitch
                  :value="expert.is_active"
                  size="small"
                  @update:value="() => handleToggle(expert)"
                />
              </NSpace>
            </template>

            <div class="expert-card-body">
              <div class="expert-title">{{ expert.title }}</div>
              <div class="expert-perspective">{{ expert.perspective }}</div>
              <div class="expert-keywords" v-if="expert.keywords.length">
                <NTag
                  v-for="kw in expert.keywords.slice(0, 5)"
                  :key="kw"
                  size="tiny"
                  :bordered="false"
                  style="margin: 2px"
                >
                  {{ kw }}
                </NTag>
                <NTag v-if="expert.keywords.length > 5" size="tiny" :bordered="false">
                  +{{ expert.keywords.length - 5 }}
                </NTag>
              </div>
              <div class="expert-meta">
                <span>优先级: {{ expert.priority }}</span>
                <span v-if="expert.current_model" style="margin-left: 12px; color: #999">
                  模型: {{ expert.current_model }}
                </span>
              </div>
            </div>

            <template #action>
              <NSpace justify="end">
                <NButton size="small" @click="openEdit(expert)">编辑</NButton>
                <NPopconfirm @positive-click="() => handleDelete(expert)">
                  <template #trigger>
                    <NButton size="small" type="error" secondary>删除</NButton>
                  </template>
                  确认删除专家「{{ expert.name }}」？
                </NPopconfirm>
              </NSpace>
            </template>
          </NCard>
        </NGridItem>
      </NGrid>
    </NSpin>

    <!-- 新增/编辑 Drawer -->
    <NDrawer v-model:show="drawerVisible" :width="520">
      <NDrawerContent :title="isEditing ? '编辑专家' : '新增专家'" closable>
        <NForm :model="formData" label-placement="top" ref="formRef">
          <NFormItem label="Slug（唯一标识）" path="slug">
            <NInput
              v-model:value="formData.slug"
              placeholder="如 scholar_senior"
              :disabled="isEditing"
            />
          </NFormItem>
          <NFormItem label="姓名" path="name">
            <NInput v-model:value="formData.name" placeholder="如 张学远" />
          </NFormItem>
          <NFormItem label="头衔" path="title">
            <NInput v-model:value="formData.title" placeholder="如 理论·资深学者" />
          </NFormItem>
          <NGrid :cols="2" :x-gap="12">
            <NGridItem>
              <NFormItem label="类别" path="category">
                <NSelect v-model:value="formData.category" :options="CATEGORY_OPTIONS" />
              </NFormItem>
            </NGridItem>
            <NGridItem>
              <NFormItem label="模型等级" path="model_tier">
                <NSelect v-model:value="formData.model_tier" :options="TIER_OPTIONS" />
              </NFormItem>
            </NGridItem>
          </NGrid>
          <NFormItem label="视角描述" path="perspective">
            <NInput v-model:value="formData.perspective" placeholder="从...角度分析" />
          </NFormItem>
          <NFormItem label="匹配关键词" path="keywords">
            <NDynamicTags v-model:value="formData.keywords" />
          </NFormItem>
          <NFormItem label="优先级" path="priority">
            <NInputNumber v-model:value="formData.priority" :min="0" :max="999" style="width: 100%" />
          </NFormItem>
          <NFormItem label="专家人设（System Prompt）" path="persona">
            <NInput
              v-model:value="formData.persona"
              type="textarea"
              :rows="10"
              placeholder="你是...教授，一位..."
            />
          </NFormItem>
        </NForm>

        <template #footer>
          <NSpace justify="end">
            <NButton @click="drawerVisible = false">取消</NButton>
            <NButton type="primary" @click="handleSave">
              {{ isEditing ? '保存修改' : '创建专家' }}
            </NButton>
          </NSpace>
        </template>
      </NDrawerContent>
    </NDrawer>
  </div>
</template>

<style scoped>
.expert-management {
  padding: 0;
}

.expert-card-body {
  font-size: 13px;
}

.expert-title {
  color: #666;
  margin-bottom: 6px;
}

.expert-perspective {
  color: #333;
  margin-bottom: 8px;
  line-height: 1.5;
}

.expert-keywords {
  margin-bottom: 8px;
}

.expert-meta {
  font-size: 12px;
  color: #999;
  margin-top: 4px;
}
</style>
