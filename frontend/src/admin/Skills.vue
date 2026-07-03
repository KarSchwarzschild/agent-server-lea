<template>
  <div class="skills-page">
    <div class="header">
      <n-h2 style="margin: 0">技能管理</n-h2>
      <n-space>
        <n-button @click="syncSkills" :loading="syncing" secondary>同步内置</n-button>
        <n-button type="primary" @click="openCreate">创建技能</n-button>
      </n-space>
    </div>

    <n-alert v-if="error" type="error" style="margin-bottom: 16px" closable @close="error = ''">
      {{ error }}
    </n-alert>

    <!-- 筛选 -->
    <n-space style="margin-bottom: 16px">
      <n-select
        v-model:value="filterScope"
        :options="scopeOptions"
        placeholder="作用域"
        clearable
        style="width: 120px"
        @update:value="fetchSkills"
      />
      <n-select
        v-model:value="filterCategory"
        :options="categoryOptions"
        placeholder="分类"
        clearable
        style="width: 140px"
        @update:value="fetchSkills"
      />
      <n-text depth="3" style="line-height: 34px">共 {{ total }} 个技能</n-text>
    </n-space>

    <!-- 技能卡片 -->
    <n-spin :show="loading">
      <n-grid v-if="skills.length" :cols="3" :x-gap="12" :y-gap="12">
        <n-gi v-for="skill in skills" :key="skill.id">
          <n-card size="small" hoverable :class="{ 'disabled-card': !skill.enabled }">
            <template #header>
              <div class="card-header">
                <span class="skill-name">{{ skill.display_name }}</span>
                <n-space :size="4">
                  <n-tag :type="scopeColor(skill.scope)" size="small">{{ scopeLabel(skill.scope) }}</n-tag>
                  <n-tag :type="skill.execution_mode === 'pipeline' ? 'warning' : 'info'" size="small">
                    {{ skill.execution_mode === 'pipeline' ? 'Pipeline' : 'ReAct' }}
                  </n-tag>
                </n-space>
              </div>
            </template>
            <p class="desc">{{ skill.description || '暂无描述' }}</p>
            <n-space v-if="skill.allowed_tools.length" :size="4" style="margin-bottom: 8px">
              <n-tag v-for="t in skill.allowed_tools" :key="t" size="tiny" :bordered="false">{{ t }}</n-tag>
            </n-space>
            <n-space v-if="skill.trigger_keywords.length" :size="4" style="margin-bottom: 8px">
              <n-tag v-for="kw in skill.trigger_keywords" :key="kw" size="tiny" type="success" :bordered="false">{{ kw }}</n-tag>
            </n-space>
            <n-descriptions :column="2" size="small">
              <n-descriptions-item label="分类">{{ skill.category }}</n-descriptions-item>
              <n-descriptions-item label="模型">{{ skill.model_tier }}</n-descriptions-item>
            </n-descriptions>
            <template #action>
              <n-space justify="end">
                <n-switch
                  :value="skill.enabled"
                  @update:value="(v: boolean) => toggleEnabled(skill, v)"
                  size="small"
                >
                  <template #checked>启用</template>
                  <template #unchecked>禁用</template>
                </n-switch>
                <n-button size="small" secondary @click="openEdit(skill)">编辑</n-button>
                <n-popconfirm
                  v-if="!skill.is_builtin"
                  @positive-click="handleDelete(skill)"
                >
                  <template #trigger>
                    <n-button size="small" type="error" secondary>删除</n-button>
                  </template>
                  确定删除「{{ skill.display_name }}」？
                </n-popconfirm>
                <n-tooltip v-else>
                  <template #trigger>
                    <n-button size="small" type="error" secondary disabled>删除</n-button>
                  </template>
                  内置技能不可删除
                </n-tooltip>
              </n-space>
            </template>
          </n-card>
        </n-gi>
      </n-grid>
      <n-empty v-else-if="!loading" description="暂无技能数据">
        <template #extra>
          <n-space vertical align="center">
            <n-text depth="3">点击「同步内置」从 skill.md 导入系统技能，或「创建技能」手动添加</n-text>
            <n-space>
              <n-button size="small" @click="syncSkills" :loading="syncing">同步内置</n-button>
              <n-button size="small" type="primary" @click="openCreate">创建技能</n-button>
            </n-space>
          </n-space>
        </template>
      </n-empty>
    </n-spin>

    <!-- Create / Edit Modal -->
    <n-modal v-model:show="modalVisible" preset="card" :title="editingSkill ? '编辑技能' : '创建技能'" style="width: 680px">
      <n-form ref="formRef" :model="form" label-placement="left" label-width="90">
        <n-grid :cols="2" :x-gap="16">
          <n-gi>
            <n-form-item label="名称" path="name">
              <n-input v-model:value="form.name" :disabled="!!editingSkill" placeholder="英文短横线，如 doc-writer" />
            </n-form-item>
          </n-gi>
          <n-gi>
            <n-form-item label="显示名" path="display_name">
              <n-input v-model:value="form.display_name" placeholder="中文名称" />
            </n-form-item>
          </n-gi>
        </n-grid>
        <n-form-item label="描述">
          <n-input v-model:value="form.description" type="textarea" :rows="2" />
        </n-form-item>
        <n-grid :cols="2" :x-gap="16">
          <n-gi>
            <n-form-item label="分类">
              <n-input v-model:value="form.category" placeholder="general / education / research" />
            </n-form-item>
          </n-gi>
          <n-gi>
            <n-form-item label="模型等级">
              <n-select v-model:value="form.model_tier" :options="tierOptions" />
            </n-form-item>
          </n-gi>
        </n-grid>
        <n-form-item label="允许工具">
          <n-select
            v-model:value="form.allowed_tools"
            :options="toolOptions"
            multiple
            filterable
            placeholder="选择可用的 Tool（不选则允许全部）"
          />
        </n-form-item>
        <n-form-item label="触发词">
          <n-dynamic-tags v-model:value="form.trigger_keywords" />
        </n-form-item>
        <n-form-item label="系统提示词">
          <n-input
            v-model:value="form.system_prompt"
            type="textarea"
            :rows="10"
            placeholder="Skill 的 system prompt..."
            style="font-family: 'Cascadia Code', 'Fira Code', monospace; font-size: 13px"
          />
        </n-form-item>
      </n-form>
      <template #action>
        <n-space justify="end">
          <n-button @click="modalVisible = false">取消</n-button>
          <n-button type="primary" @click="handleSubmit" :loading="submitting">
            {{ editingSkill ? '保存' : '创建' }}
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import {
  NH2, NSpace, NButton, NGrid, NGi, NCard, NTag, NDescriptions, NDescriptionsItem,
  NSwitch, NModal, NForm, NFormItem, NInput, NSelect, NDynamicTags, NSpin,
  NEmpty, NAlert, NText, NPopconfirm, NTooltip,
  useMessage,
} from 'naive-ui'
import request from '../utils/request'

interface SkillItem {
  id: number
  name: string
  display_name: string
  description: string
  category: string
  execution_mode: string
  model_tier: string
  allowed_tools: string[]
  trigger_keywords: string[]
  system_prompt: string
  resources: Record<string, any>
  scope: string
  is_builtin: boolean
  enabled: boolean
}

const message = useMessage()

const skills = ref<SkillItem[]>([])
const total = ref(0)
const loading = ref(false)
const syncing = ref(false)
const submitting = ref(false)
const modalVisible = ref(false)
const editingSkill = ref<SkillItem | null>(null)
const filterScope = ref<string | null>(null)
const filterCategory = ref<string | null>(null)
const error = ref('')
const toolOptions = ref<{ label: string; value: string }[]>([])

const form = reactive({
  name: '',
  display_name: '',
  description: '',
  category: 'general',
  model_tier: 'mid',
  allowed_tools: [] as string[],
  trigger_keywords: [] as string[],
  system_prompt: '',
})

const scopeOptions = [
  { label: '系统', value: 'system' },
  { label: '公共', value: 'public' },
  { label: '私有', value: 'private' },
]

const tierOptions = [
  { label: 'High — 最强模型', value: 'high' },
  { label: 'Mid — 平衡模型', value: 'mid' },
  { label: 'Low — 轻量模型', value: 'low' },
]

const categoryOptions = ref<{ label: string; value: string }[]>([])

function scopeColor(scope: string) {
  if (scope === 'system') return 'info'
  if (scope === 'public') return 'success'
  return 'default'
}

function scopeLabel(scope: string) {
  if (scope === 'system') return '系统'
  if (scope === 'public') return '公共'
  return '私有'
}

async function fetchSkills() {
  loading.value = true
  error.value = ''
  try {
    const params: Record<string, any> = {}
    if (filterScope.value) params.scope = filterScope.value
    if (filterCategory.value) params.category = filterCategory.value
    const { data } = await request.get('/api/admin/skills', { params })
    skills.value = data.skills || []
    total.value = data.total || 0

    const cats = new Set<string>()
    skills.value.forEach(s => cats.add(s.category))
    categoryOptions.value = [...cats].map(c => ({ label: c, value: c }))
  } catch (e: any) {
    error.value = e.response?.data?.detail || '获取技能列表失败，请确认后端服务已启动'
  }
  loading.value = false
}

async function fetchTools() {
  try {
    const { data } = await request.get('/api/admin/tools')
    toolOptions.value = (data.tools || []).map((t: any) => ({
      label: `${t.name} — ${t.description || '无描述'}`,
      value: t.name,
    }))
  } catch { /* tools list is nice-to-have */ }
}

function resetForm() {
  form.name = ''
  form.display_name = ''
  form.description = ''
  form.category = 'general'
  form.model_tier = 'mid'
  form.allowed_tools = []
  form.trigger_keywords = []
  form.system_prompt = ''
}

function openCreate() {
  editingSkill.value = null
  resetForm()
  modalVisible.value = true
}

function openEdit(skill: SkillItem) {
  editingSkill.value = skill
  form.name = skill.name
  form.display_name = skill.display_name
  form.description = skill.description
  form.category = skill.category
  form.model_tier = skill.model_tier
  form.allowed_tools = [...skill.allowed_tools]
  form.trigger_keywords = [...skill.trigger_keywords]
  form.system_prompt = skill.system_prompt
  modalVisible.value = true
}

async function handleSubmit() {
  if (!form.name || !form.display_name) {
    message.warning('名称和显示名不能为空')
    return
  }
  submitting.value = true
  try {
    if (editingSkill.value) {
      await request.put(`/api/admin/skills/${editingSkill.value.id}`, {
        display_name: form.display_name,
        description: form.description,
        category: form.category,
        model_tier: form.model_tier,
        allowed_tools: form.allowed_tools,
        trigger_keywords: form.trigger_keywords,
        system_prompt: form.system_prompt,
      })
      message.success('已保存')
    } else {
      await request.post('/api/admin/skills', { ...form })
      message.success('已创建')
    }
    modalVisible.value = false
    fetchSkills()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '操作失败')
  }
  submitting.value = false
}

async function toggleEnabled(skill: SkillItem, enabled: boolean) {
  try {
    await request.put(`/api/admin/skills/${skill.id}`, { enabled })
    skill.enabled = enabled
    message.success(enabled ? '已启用' : '已禁用')
  } catch (e: any) {
    message.error(e.response?.data?.detail || '操作失败')
  }
}

async function handleDelete(skill: SkillItem) {
  try {
    await request.delete(`/api/admin/skills/${skill.id}`)
    message.success('已删除')
    fetchSkills()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '删除失败')
  }
}

async function syncSkills() {
  syncing.value = true
  try {
    const { data } = await request.post('/api/admin/skills/sync')
    message.success(data.message || '同步完成')
    fetchSkills()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '同步失败')
  }
  syncing.value = false
}

onMounted(() => {
  fetchSkills()
  fetchTools()
})
</script>

<style scoped>
.skills-page {
  padding: 8px;
}

.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.skill-name {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 160px;
}

.desc {
  color: #666;
  font-size: 13px;
  margin: 0 0 8px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.disabled-card {
  opacity: 0.55;
}
</style>
