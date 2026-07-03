<script setup lang="ts">
/**
 * AI 助手管理页面 (SuperAgent)
 *
 * 编辑弹窗采用 Tab 布局：
 *   Tab 1 "基本信息" — 名称、头像、描述、可见性、人格设定、欢迎语、排序
 *   Tab 2 "技能与示例" — allowed_skills + example_questions
 */
import { ref, computed, onMounted, h } from 'vue'
import {
  NCard, NButton, NSpace, NTag, NModal, NForm, NFormItem,
  NInput, NInputNumber, NSelect, NEmpty, NSpin, NDataTable,
  NCheckboxGroup, NCheckbox, NRadioGroup, NRadio, NTabs, NTabPane,
  NTooltip,
  useMessage, type DataTableColumns,
} from 'naive-ui'
import {
  getSuperAgents, createSuperAgent, updateSuperAgent, toggleSuperAgent, getSkills,
  type SuperAgentItem, type SuperAgentUpdateData, type Skill,
} from '../api/super-agent'
import AgentAiCreatorPanel from '../components/AgentAiCreatorPanel.vue'

const message = useMessage()

// ========== 数据 ==========
const loading = ref(false)
const agents = ref<SuperAgentItem[]>([])
const showEditModal = ref(false)
const editAgent = ref<SuperAgentItem | null>(null)
const isCreating = ref(false)
const saving = ref(false)
const activeTab = ref('basic')

const editForm = ref<SuperAgentUpdateData>({})

// ========== Skills ==========
const allSkills = ref<Skill[]>([])
const skillMode = ref<'all' | 'custom'>('all')
const selectedSkills = ref<string[]>([])
const exampleQuestions = ref<string[]>([])
const newQuestion = ref('')

// ========== AI 辅助创建 ==========
const showAiCreator = ref(false)

const skillSearch = ref('')

const CATEGORY_LABELS: Record<string, string> = {
  research: '学术研究',
  education: '教学辅助',
  general: '通用能力',
  tool: '工具',
}
const CATEGORY_ORDER = ['research', 'education', 'general', 'tool']

const groupedSkills = computed(() => {
  const keyword = skillSearch.value.trim().toLowerCase()
  const filtered = keyword
    ? allSkills.value.filter(s =>
        (s.display_name || s.name).toLowerCase().includes(keyword) ||
        s.name.toLowerCase().includes(keyword) ||
        (s.description || '').toLowerCase().includes(keyword))
    : allSkills.value

  const groups: { key: string; label: string; skills: Skill[] }[] = []
  const catMap = new Map<string, Skill[]>()
  for (const s of filtered) {
    const cat = s.category || 'general'
    if (!catMap.has(cat)) catMap.set(cat, [])
    catMap.get(cat)!.push(s)
  }
  for (const key of CATEGORY_ORDER) {
    const skills = catMap.get(key)
    if (skills?.length) {
      groups.push({ key, label: CATEGORY_LABELS[key] || key, skills })
    }
  }
  // 处理未知分类
  for (const [key, skills] of catMap) {
    if (!CATEGORY_ORDER.includes(key) && skills.length) {
      groups.push({ key, label: key, skills })
    }
  }
  return groups
})

function selectCategory(catSkills: Skill[]) {
  const names = catSkills.map(s => s.name)
  const allSelected = names.every(n => selectedSkills.value.includes(n))
  if (allSelected) {
    selectedSkills.value = selectedSkills.value.filter(n => !names.includes(n))
  } else {
    const toAdd = names.filter(n => !selectedSkills.value.includes(n))
    selectedSkills.value = [...selectedSkills.value, ...toAdd]
  }
}

function isCategoryAllSelected(catSkills: Skill[]) {
  return catSkills.every(s => selectedSkills.value.includes(s.name))
}

function isCategoryPartial(catSkills: Skill[]) {
  const some = catSkills.some(s => selectedSkills.value.includes(s.name))
  return some && !isCategoryAllSelected(catSkills)
}

function selectAllSkills() {
  selectedSkills.value = allSkills.value.map(s => s.name)
}

function clearAllSkills() {
  selectedSkills.value = []
}

async function loadSkills() {
  try {
    const res = await getSkills({ enabled: true })
    allSkills.value = res.skills || []
  } catch {}
}

function addQuestion() {
  const q = newQuestion.value.trim()
  if (!q) return
  if (q.length > 200) { message.warning('每条示例问题不超过200字'); return }
  if (exampleQuestions.value.length >= 10) { message.warning('最多10条示例问题'); return }
  exampleQuestions.value.push(q)
  newQuestion.value = ''
}

function removeQuestion(index: number) {
  exampleQuestions.value.splice(index, 1)
}

// ========== 头像选项 ==========
const avatarOptions = ['🤖', '🎓', '📊', '✍️', '🔬', '📚', '💡', '🎯', '🧠', '🌟']

// ========== 表格列定义 ==========
const columns: DataTableColumns<SuperAgentItem> = [
  {
    title: '头像',
    key: 'avatar',
    width: 60,
    render: (row) => row.avatar || '🤖',
  },
  {
    title: '名称',
    key: 'name',
    width: 150,
  },
  {
    title: '描述',
    key: 'description',
    ellipsis: { tooltip: true },
  },
  {
    title: '类型',
    key: 'owner_user_id',
    width: 80,
    render: (row) => row.owner_user_id ? '用户创建' : '系统预设',
  },
  {
    title: '可见性',
    key: 'visibility',
    width: 80,
    render: (row) => row.visibility === 'global' ? '全局' : '个人',
  },
  {
    title: '技能',
    key: 'allowed_skills',
    width: 80,
    render: (row) => row.allowed_skills ? `${row.allowed_skills.length}个` : '全部',
  },
  {
    title: '人设',
    key: 'soul_prompt',
    width: 80,
    render: (row) => row.soul_prompt ? `${row.soul_prompt.length}字` : '-',
  },
  {
    title: '状态',
    key: 'is_enabled',
    width: 70,
    render: (row) => h(NTag, { type: row.is_enabled ? 'success' : 'default', size: 'small' }, { default: () => row.is_enabled ? '启用' : '禁用' }),
  },
  {
    title: '操作',
    key: 'actions',
    width: 160,
    fixed: 'right',
    render: (row) => h(NSpace, { size: 'small' }, {
      default: () => [
        h(NButton, { size: 'small', onClick: () => openEdit(row) }, { default: () => '编辑' }),
        h(NButton, {
          size: 'small',
          type: row.is_enabled ? 'warning' : 'success',
          onClick: () => handleToggle(row),
        }, { default: () => row.is_enabled ? '禁用' : '启用' }),
      ],
    }),
  },
]

// ========== 方法 ==========
async function loadAgents() {
  loading.value = true
  try {
    const res = await getSuperAgents()
    agents.value = (Array.isArray(res) ? res : res.data) || []
  } catch (e: any) {
    message.error('加载失败: ' + (e?.message || '未知错误'))
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editAgent.value = null
  isCreating.value = true
  activeTab.value = 'basic'
  editForm.value = {
    name: '',
    description: '',
    soul_prompt: '',
    visibility: 'global',
    avatar: '🤖',
    welcome_message: '',
    sort_order: 0,
  }
  skillMode.value = 'all'
  selectedSkills.value = []
  exampleQuestions.value = []
  newQuestion.value = ''
  showEditModal.value = true
}

function openEdit(agent: SuperAgentItem) {
  editAgent.value = agent
  isCreating.value = false
  activeTab.value = 'basic'
  editForm.value = {
    name: agent.name,
    description: agent.description || '',
    soul_prompt: agent.soul_prompt,
    visibility: agent.visibility as 'global' | 'personal',
    avatar: agent.avatar,
    welcome_message: agent.welcome_message,
    sort_order: agent.sort_order,
  }
  if (agent.allowed_skills && agent.allowed_skills.length > 0) {
    skillMode.value = 'custom'
    selectedSkills.value = [...agent.allowed_skills]
  } else {
    skillMode.value = 'all'
    selectedSkills.value = []
  }
  exampleQuestions.value = agent.example_questions ? [...agent.example_questions] : []
  newQuestion.value = ''
  showEditModal.value = true
}

async function handleSave() {
  if (!isCreating.value && !editAgent.value) return
  if (!editForm.value.name?.trim()) {
    message.warning('请填写助手名称')
    return
  }
  saving.value = true
  try {
    const data: SuperAgentUpdateData = {
      ...editForm.value,
      allowed_skills: skillMode.value === 'custom' && selectedSkills.value.length > 0
        ? selectedSkills.value
        : null,
      example_questions: exampleQuestions.value.length > 0 ? exampleQuestions.value : null,
    }
    if (isCreating.value) {
      await createSuperAgent(data)
      message.success('创建成功')
    } else {
      await updateSuperAgent(editAgent.value!.id, data)
      message.success('保存成功')
    }
    showEditModal.value = false
    await loadAgents()
  } catch (e: any) {
    message.error((isCreating.value ? '创建' : '保存') + '失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    saving.value = false
  }
}

async function handleToggle(agent: SuperAgentItem) {
  try {
    await toggleSuperAgent(agent.id)
    message.success(agent.is_enabled ? '已禁用' : '已启用')
    await loadAgents()
  } catch (e: any) {
    message.error('操作失败')
  }
}

function handleApplyAiConfig(config: Record<string, any>) {
  showAiCreator.value = false
  // 填充创建表单
  editAgent.value = null
  isCreating.value = true
  activeTab.value = 'basic'
  editForm.value = {
    name: config.name || '',
    description: config.description || '',
    soul_prompt: config.soul_prompt || '',
    visibility: config.visibility || 'global',
    avatar: config.avatar || '🤖',
    welcome_message: config.welcome_message || '',
    sort_order: config.sort_order || 0,
  }
  if (config.allowed_skills && config.allowed_skills.length > 0) {
    skillMode.value = 'custom'
    selectedSkills.value = [...config.allowed_skills]
  } else {
    skillMode.value = 'all'
    selectedSkills.value = []
  }
  exampleQuestions.value = config.example_questions ? [...config.example_questions] : []
  newQuestion.value = ''
  showEditModal.value = true
  message.success('AI 配置已填充到表单，可微调后创建')
}

onMounted(() => { loadAgents(); loadSkills() })
</script>

<template>
  <div class="super-agent-management">
    <n-card title="AI 助手管理" :bordered="false">
      <template #header-extra>
        <n-space size="small">
          <n-button @click="loadAgents" :loading="loading" size="small">刷新</n-button>
          <n-button size="small" @click="showAiCreator = true">AI 辅助创建</n-button>
          <n-button type="primary" size="small" @click="openCreate">创建预设助手</n-button>
        </n-space>
      </template>

      <n-spin :show="loading">
        <n-data-table
          :columns="columns"
          :data="agents"
          :bordered="false"
          :row-key="(row: SuperAgentItem) => row.id"
          :scroll-x="900"
        >
          <template #empty>
            <n-empty description="暂无 AI 助手" />
          </template>
        </n-data-table>
      </n-spin>
    </n-card>

    <!-- 编辑弹窗 -->
    <n-modal
      v-model:show="showEditModal"
      preset="card"
      :title="isCreating ? '创建预设助手' : `编辑助手 — ${editAgent?.name || ''}`"
      :style="{ width: '760px' }"
      :content-style="{ padding: '0 24px 16px' }"
    >
      <n-tabs v-model:value="activeTab" type="line" animated>
        <!-- ===== Tab 1: 基本信息 ===== -->
        <n-tab-pane name="basic" tab="基本信息">
          <n-form label-placement="top" :show-feedback="false" class="tab-form">
            <n-form-item label="助手名称">
              <n-input v-model:value="editForm.name" placeholder="如：学术研究助手" />
            </n-form-item>

            <n-form-item label="头像">
              <div class="avatar-picker">
                <span
                  v-for="emoji in avatarOptions"
                  :key="emoji"
                  class="avatar-option"
                  :class="{ active: editForm.avatar === emoji }"
                  @click="editForm.avatar = emoji"
                >{{ emoji }}</span>
              </div>
            </n-form-item>

            <n-form-item label="描述">
              <n-input v-model:value="editForm.description" type="textarea" placeholder="一句话介绍这个助手的定位和能力" :autosize="{ minRows: 2, maxRows: 3 }" />
            </n-form-item>

            <div class="form-row-2col">
              <n-form-item label="可见性">
                <n-select
                  v-model:value="editForm.visibility"
                  :options="[
                    { label: '全局可见（所有用户）', value: 'global' },
                    { label: '仅创建者可见', value: 'personal' },
                  ]"
                />
              </n-form-item>
              <n-form-item label="排序权重">
                <template #label>
                  <span>排序权重</span>
                  <span class="form-label-hint">越大越靠前</span>
                </template>
                <n-input-number
                  v-model:value="editForm.sort_order"
                  :min="0"
                  :max="9999"
                  style="width: 100%"
                />
              </n-form-item>
            </div>

            <n-form-item>
              <template #label>
                <span>人格设定</span>
                <span class="form-label-hint">定义助手的性格、专长、回答风格</span>
              </template>
              <n-input
                v-model:value="editForm.soul_prompt"
                type="textarea"
                placeholder="例如：你是一位严谨的学术研究助手，擅长文献检索和方法论指导..."
                :autosize="{ minRows: 3, maxRows: 10 }"
                show-count
                :maxlength="2000"
              />
            </n-form-item>

            <n-form-item>
              <template #label>
                <span>欢迎语</span>
                <span class="form-label-hint">新建对话时展示的开场白</span>
              </template>
              <n-input
                v-model:value="editForm.welcome_message"
                type="textarea"
                placeholder="例如：你好！我是你的学术研究助手。我擅长文献综述..."
                :autosize="{ minRows: 2, maxRows: 4 }"
              />
            </n-form-item>
          </n-form>
        </n-tab-pane>

        <!-- ===== Tab 2: 技能与示例 ===== -->
        <n-tab-pane name="skills" tab="技能与示例">
          <div class="tab-form">
            <!-- 技能范围 -->
            <div class="section">
              <div class="section-header">
                <span class="section-title">技能范围</span>
                <span class="section-desc">限制该助手可使用的技能，不选则全部可用</span>
              </div>

              <n-radio-group v-model:value="skillMode" class="skill-mode-radio">
                <n-radio value="all">全部可用</n-radio>
                <n-radio value="custom">指定技能</n-radio>
              </n-radio-group>

              <div v-if="skillMode === 'custom'" class="skill-panel">
                <!-- 搜索 + 批量操作 -->
                <div class="skill-toolbar">
                  <n-input
                    v-model:value="skillSearch"
                    placeholder="搜索技能..."
                    size="small"
                    clearable
                    style="width: 200px"
                  />
                  <div class="skill-toolbar-actions">
                    <n-button text size="tiny" @click="selectAllSkills">全选</n-button>
                    <span class="skill-toolbar-sep">|</span>
                    <n-button text size="tiny" @click="clearAllSkills">清空</n-button>
                    <span class="skill-toolbar-count">
                      已选 <strong>{{ selectedSkills.length }}</strong> / {{ allSkills.length }}
                    </span>
                  </div>
                </div>

                <!-- 按分类分组 -->
                <div class="skill-groups">
                  <n-checkbox-group v-model:value="selectedSkills">
                    <div v-for="group in groupedSkills" :key="group.key" class="skill-category">
                      <div class="skill-category-header" @click="selectCategory(group.skills)">
                        <n-checkbox
                          :checked="isCategoryAllSelected(group.skills)"
                          :indeterminate="isCategoryPartial(group.skills)"
                          @click.stop
                          @update:checked="selectCategory(group.skills)"
                        />
                        <span class="skill-category-label">{{ group.label }}</span>
                        <span class="skill-category-count">{{ group.skills.length }}</span>
                      </div>
                      <div class="skill-category-grid">
                        <n-tooltip v-for="skill in group.skills" :key="skill.name" placement="top" :delay="400">
                          <template #trigger>
                            <div class="skill-item">
                              <n-checkbox :value="skill.name">
                                {{ skill.display_name || skill.name }}
                              </n-checkbox>
                            </div>
                          </template>
                          <div style="max-width: 280px">
                            <div style="font-weight: 500; margin-bottom: 4px">{{ skill.display_name || skill.name }}</div>
                            <div style="font-size: 12px; color: #ccc">{{ skill.name }}</div>
                            <div v-if="skill.description" style="font-size: 12px; margin-top: 4px; opacity: 0.85">{{ skill.description }}</div>
                          </div>
                        </n-tooltip>
                      </div>
                    </div>
                  </n-checkbox-group>
                </div>
              </div>
            </div>

            <!-- 示例问题 -->
            <div class="section" style="margin-top: 28px">
              <div class="section-header">
                <span class="section-title">示例问题</span>
                <span class="section-desc">新建对话时展示的引导问题，留空则使用系统默认</span>
              </div>

              <div v-if="exampleQuestions.length > 0" class="eq-list">
                <div v-for="(q, idx) in exampleQuestions" :key="idx" class="eq-item">
                  <span class="eq-idx">{{ idx + 1 }}</span>
                  <span class="eq-content">{{ q }}</span>
                  <n-button text size="tiny" type="error" @click="removeQuestion(idx)">删除</n-button>
                </div>
              </div>
              <div v-else class="eq-empty">
                暂无自定义示例问题，将使用系统默认
              </div>

              <div v-if="exampleQuestions.length < 10" class="eq-add">
                <n-input
                  v-model:value="newQuestion"
                  placeholder="输入示例问题，回车添加"
                  size="small"
                  :maxlength="200"
                  @keyup.enter="addQuestion"
                />
                <n-button size="small" @click="addQuestion" :disabled="!newQuestion.trim()">添加</n-button>
              </div>
              <div class="hint">最多 10 条，每条不超过 200 字</div>
            </div>
          </div>
        </n-tab-pane>
      </n-tabs>

      <template #footer>
        <n-space justify="end">
          <n-button @click="showEditModal = false">取消</n-button>
          <n-button type="primary" @click="handleSave" :loading="saving">{{ isCreating ? '创建' : '保存' }}</n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- AI 辅助创建弹窗 -->
    <n-modal
      v-model:show="showAiCreator"
      preset="card"
      title="AI 辅助创建预设助手"
      :style="{ width: '680px' }"
      :content-style="{ padding: '0', overflow: 'hidden' }"
    >
      <AgentAiCreatorPanel
        :visible="showAiCreator"
        @close="showAiCreator = false"
        @created="showAiCreator = false; loadAgents()"
        @apply-config="handleApplyAiConfig"
      />
    </n-modal>
  </div>
</template>

<style scoped>
.super-agent-management {
  padding: 0;
}

/* ===== Tab 内表单 ===== */
.tab-form {
  padding: 16px 0 4px;
}

.tab-form :deep(.n-form-item) {
  margin-bottom: 20px;
}

.form-row-2col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
}

.form-label-hint {
  font-size: 12px;
  color: #999;
  font-weight: 400;
  margin-left: 8px;
}

.hint {
  font-size: 12px;
  color: #999;
  margin-top: 6px;
}

/* ===== 头像选择 ===== */
.avatar-picker {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.avatar-option {
  font-size: 26px;
  cursor: pointer;
  width: 44px;
  height: 44px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 10px;
  border: 2px solid #eee;
  transition: all 0.15s;
  background: #fafafa;
}

.avatar-option:hover {
  background: #f0f0f0;
  border-color: #ccc;
  transform: scale(1.08);
}

.avatar-option.active {
  border-color: #2080f0;
  background: #e8f4ff;
}

/* ===== Tab2: 技能与示例 ===== */
.section-header {
  margin-bottom: 14px;
}

.section-title {
  font-size: 14px;
  font-weight: 600;
  color: #333;
}

.section-desc {
  font-size: 12px;
  color: #999;
  margin-left: 10px;
}

.skill-mode-radio {
  margin-bottom: 12px;
}

.skill-mode-radio :deep(.n-radio) {
  margin-right: 20px;
}

/* ===== 技能选择面板 ===== */
.skill-panel {
  background: #fafafa;
  border-radius: 8px;
  border: 1px solid #eee;
  padding: 12px 16px;
}

.skill-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.skill-toolbar-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #666;
}

.skill-toolbar-sep {
  color: #ddd;
}

.skill-toolbar-count strong {
  color: #2080f0;
}

.skill-groups {
  max-height: 360px;
  overflow-y: auto;
}

.skill-category {
  margin-bottom: 12px;
}

.skill-category:last-child {
  margin-bottom: 0;
}

.skill-category-header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 0;
  cursor: pointer;
  user-select: none;
}

.skill-category-label {
  font-size: 13px;
  font-weight: 600;
  color: #333;
}

.skill-category-count {
  font-size: 11px;
  color: #aaa;
  background: #f0f0f0;
  padding: 0 6px;
  border-radius: 8px;
  line-height: 18px;
}

.skill-category-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 2px 12px;
  padding-left: 24px;
  margin-top: 4px;
}

.skill-item {
  font-size: 13px;
  line-height: 1.8;
  overflow: hidden;
}

.skill-item :deep(.n-checkbox__label) {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ===== 示例问题 ===== */
.eq-list {
  margin-bottom: 10px;
}

.eq-item {
  display: flex;
  align-items: center;
  padding: 8px 12px;
  margin-bottom: 6px;
  background: #f8f8f8;
  border-radius: 6px;
  border: 1px solid #f0f0f0;
}

.eq-idx {
  font-size: 12px;
  color: #2080f0;
  font-weight: 600;
  min-width: 20px;
}

.eq-content {
  font-size: 13px;
  flex: 1;
  margin: 0 10px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #333;
}

.eq-empty {
  padding: 16px;
  text-align: center;
  color: #bbb;
  font-size: 13px;
  background: #fafafa;
  border-radius: 6px;
  border: 1px dashed #e0e0e0;
  margin-bottom: 10px;
}

.eq-add {
  display: flex;
  gap: 8px;
}

.eq-add .n-input {
  flex: 1;
}
</style>
