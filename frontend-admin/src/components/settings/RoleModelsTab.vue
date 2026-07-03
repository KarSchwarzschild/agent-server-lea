<script setup lang="ts">
import { ref, reactive, computed, onMounted, h } from 'vue'
import {
  NSpin, NAlert, NCollapse, NCollapseItem, NGrid, NGridItem,
  NSelect, NSpace, NButton, NIcon, NTag, NText,
  useMessage,
} from 'naive-ui'
import type { SelectOption, SelectRenderLabel } from 'naive-ui'
import { SaveOutline } from '@vicons/ionicons5'
import {
  getRoleModels,
  updateRoleModels,
  type AvailableModel,
  type RoleModelsResponse,
  type RoleModelUpdateRequest,
  ROLE_DISPLAY_MAP,
  ROLE_COLOR_MAP,
} from '../../api/super-agent'

const props = defineProps<{
  availableModels: AvailableModel[]
}>()

const message = useMessage()

const roleLoading = ref(false)
const roleSaving = ref(false)
const roleConfig = ref<RoleModelsResponse | null>(null)
const selectedRoleModels = reactive<Record<string, string>>({
  intent_recognition: '',
  qa_chat: '',
  skill_execution: '',
  debate_expert_high: '',
  debate_expert_mid: '',
  debate_expert_low: '',
  debate_coordinator: '',
  review_generation: '',
  vision: '',
  memory: '',
  query_rewrite: '',
  judge: '',
  title_generation: '',
  compression: '',
  budget: '',
  kb_text_model: '',
  artifact_edit: ''
})
const originalRoleModels = ref<string>('')

const hasRoleChanges = computed(() => {
  return JSON.stringify(selectedRoleModels) !== originalRoleModels.value
})

const roleList = [
  'intent_recognition', 'qa_chat', 'skill_execution',
  'debate_expert_high', 'debate_expert_mid', 'debate_expert_low',
  'debate_coordinator', 'review_generation',
  'vision', 'memory', 'query_rewrite', 'judge',
  'title_generation', 'compression', 'budget', 'kb_text_model', 'artifact_edit', 'suggestions'
]

const ROLE_GROUPS = [
  {
    key: 'core',
    label: '核心对话',
    roles: ['intent_recognition', 'qa_chat', 'skill_execution'],
    defaultExpanded: true
  },
  {
    key: 'debate',
    label: '辩论',
    roles: ['debate_expert_high', 'debate_expert_mid', 'debate_expert_low', 'debate_coordinator', 'review_generation'],
    defaultExpanded: true
  },
  {
    key: 'system',
    label: '系统辅助',
    roles: ['vision', 'memory', 'query_rewrite', 'judge', 'title_generation', 'compression', 'budget', 'kb_text_model', 'artifact_edit', 'suggestions'],
    defaultExpanded: false
  }
]

const roleGroupExpanded = ref<string[]>(['core', 'debate'])

async function loadRoleModels() {
  roleLoading.value = true
  try {
    const res = await getRoleModels()
    roleConfig.value = res
    for (const role of roleList) {
      selectedRoleModels[role] = res.roles?.[role]?.model || ''
    }
    originalRoleModels.value = JSON.stringify(selectedRoleModels)
  } catch (e: any) {
    message.error(e.message || '加载角色模型配置失败')
  } finally {
    roleLoading.value = false
  }
}

async function saveRoleModels() {
  roleSaving.value = true
  try {
    const updates: RoleModelUpdateRequest = {}
    const original = JSON.parse(originalRoleModels.value)
    for (const role of roleList) {
      if (selectedRoleModels[role] !== original[role]) {
        (updates as any)[role] = selectedRoleModels[role]
      }
    }
    await updateRoleModels(updates)
    originalRoleModels.value = JSON.stringify(selectedRoleModels)
    message.success('角色模型配置保存成功')
  } catch (e: any) {
    message.error(e.message || '保存角色模型配置失败')
  } finally {
    roleSaving.value = false
  }
}

function resetRoleModels() {
  const original = JSON.parse(originalRoleModels.value)
  Object.assign(selectedRoleModels, original)
  message.info('已重置角色模型配置')
}

function getRoleColor(role: string): string {
  return ROLE_COLOR_MAP[role] || '#909399'
}

const modelOptions = computed(() =>
  props.availableModels.map(m => ({
    label: m.name,
    value: m.id,
    provider: m.provider,
    modelId: m.id,
  }))
)

const renderModelOption: SelectRenderLabel = (option: SelectOption) => {
  const provider = (option as any).provider || ''
  const modelId = (option as any).modelId || option.value
  return h('div', { style: 'display:flex; align-items:center; gap:8px; padding:2px 0' }, [
    h('span', { style: 'font-weight:500' }, option.label as string),
    provider ? h(NTag, { size: 'tiny', type: 'info', bordered: false }, () => provider) : null,
    h(NText, { depth: 3, style: 'font-size:11px; margin-left:auto' }, () => String(modelId)),
  ])
}

function filterModel(pattern: string, option: SelectOption): boolean {
  const p = pattern.toLowerCase()
  return [
    (option.label as string) || '',
    (option as any).provider || '',
    (option as any).modelId || '',
  ].some(s => s.toLowerCase().includes(p))
}

onMounted(() => { loadRoleModels() })

defineExpose({ loadRoleModels })
</script>

<template>
  <n-spin :show="roleLoading">
    <div class="tab-content">
      <n-alert type="success" title="推荐" style="margin-bottom: 20px">
        细粒度控制各功能模块使用的AI模型。每个角色可以独立配置，实现成本与效果的最优平衡。
      </n-alert>

      <n-collapse v-model:expanded-names="roleGroupExpanded" display-directive="show">
        <n-collapse-item
          v-for="group in ROLE_GROUPS"
          :key="group.key"
          :name="group.key"
          :title="group.label"
        >
          <n-grid :cols="2" :x-gap="16" :y-gap="16">
            <n-grid-item v-for="role in group.roles" :key="role">
              <div class="role-model-card" :style="{ borderLeftColor: getRoleColor(role) }">
                <div class="role-header">
                  <span class="role-name">{{ ROLE_DISPLAY_MAP[role] || role }}</span>
                  <span class="role-tier" v-if="roleConfig?.roles?.[role]?.fallback_tier">
                    回退: {{ roleConfig.roles[role].fallback_tier }}
                  </span>
                </div>
                <div class="role-desc">{{ roleConfig?.roles?.[role]?.description || '' }}</div>
                <n-select
                  v-model:value="selectedRoleModels[role]"
                  :options="modelOptions"
                  filterable
                  :filter="filterModel"
                  :render-label="renderModelOption"
                  placeholder="搜索或选择模型"
                />
              </div>
            </n-grid-item>
          </n-grid>
        </n-collapse-item>
      </n-collapse>

      <n-space style="margin-top: 24px">
        <n-button @click="resetRoleModels" :disabled="!hasRoleChanges">
          重置
        </n-button>
        <n-button type="primary" :loading="roleSaving" :disabled="!hasRoleChanges" @click="saveRoleModels">
          <template #icon>
            <n-icon :component="SaveOutline" />
          </template>
          保存角色配置
        </n-button>
      </n-space>
    </div>
  </n-spin>
</template>

<style scoped>
.role-model-card {
  padding: 16px;
  border-radius: 8px;
  background: #f9f9f9;
  border-left: 3px solid #e8e8e8;
}
.role-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.role-name {
  font-weight: 600;
  font-size: 14px;
}
.role-tier {
  font-size: 11px;
  color: #999;
  background: #f0f0f0;
  padding: 2px 6px;
  border-radius: 4px;
}
.role-desc {
  font-size: 12px;
  color: #666;
  margin-bottom: 12px;
  min-height: 18px;
}
</style>
