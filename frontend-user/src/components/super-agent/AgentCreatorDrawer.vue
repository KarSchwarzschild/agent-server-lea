<script setup lang="ts">
/**
 * AI 助手创建/编辑抽屉
 * Phase 4a: 表单 + AI 帮写功能（无调试预览）
 */
import { ref, computed, watch, onMounted, nextTick } from 'vue'
import {
  NDrawer, NDrawerContent, NForm, NFormItem, NInput,
  NButton, NSelect, NSpace, NRadioGroup, NRadio, NCheckboxGroup,
  NCheckbox, NTag, NIcon, NTabs, NTabPane, NScrollbar, NSpin,
  NTooltip, useMessage
} from 'naive-ui'
import { CloseCircleOutline, SendOutline } from '@vicons/ionicons5'
import { createAgent, updateAgent, getSkills, generateAgent, getAgentCategories, type AgentInfo, type Skill } from '../../api/superagent'
import MarkdownIt from 'markdown-it'

const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

const props = defineProps<{
  show: boolean
  editAgent?: AgentInfo | null  // 编辑模式时传入
}>()

const emit = defineEmits<{
  (e: 'update:show', value: boolean): void
  (e: 'saved', agent: AgentInfo): void
}>()

const message = useMessage()
const saving = ref(false)
const aiWriting = ref(false)

const form = ref({
  name: '',
  description: '',
  avatar: '🤖',
  welcome_message: '',
  soul_prompt: '',
  category: null as string | null,
  allowed_skills: [] as string[],
  skillMode: 'all' as 'all' | 'custom',
  example_questions: [] as string[],
})

// 可用技能列表
const availableSkills = ref<Skill[]>([])
const newExample = ref('')
const skillSearch = ref('')

// 技能分类标签
const SKILL_CATEGORY_LABELS: Record<string, string> = {
  research: '学术研究', education: '教学辅助', general: '通用能力', tool: '工具',
}
const SKILL_CATEGORY_ORDER = ['research', 'education', 'general', 'tool']

const groupedSkills = computed(() => {
  const keyword = skillSearch.value.trim().toLowerCase()
  const filtered = keyword
    ? availableSkills.value.filter(s =>
        (s.display_name || s.name).toLowerCase().includes(keyword) ||
        (s.description || '').toLowerCase().includes(keyword))
    : availableSkills.value
  const catMap = new Map<string, Skill[]>()
  for (const s of filtered) {
    const cat = (s as any).category || 'general'
    if (!catMap.has(cat)) catMap.set(cat, [])
    catMap.get(cat)!.push(s)
  }
  const groups: { key: string; label: string; skills: Skill[] }[] = []
  for (const key of SKILL_CATEGORY_ORDER) {
    const skills = catMap.get(key)
    if (skills?.length) groups.push({ key, label: SKILL_CATEGORY_LABELS[key] || key, skills })
  }
  for (const [key, skills] of catMap) {
    if (!SKILL_CATEGORY_ORDER.includes(key) && skills.length) {
      groups.push({ key, label: SKILL_CATEGORY_LABELS[key] || key, skills })
    }
  }
  return groups
})

function toggleCategory(catSkills: Skill[]) {
  const names = catSkills.map(s => s.name)
  const allSelected = names.every(n => form.value.allowed_skills.includes(n))
  if (allSelected) {
    form.value.allowed_skills = form.value.allowed_skills.filter(n => !names.includes(n))
  } else {
    const toAdd = names.filter(n => !form.value.allowed_skills.includes(n))
    form.value.allowed_skills = [...form.value.allowed_skills, ...toAdd]
  }
}

function isCatAllSelected(catSkills: Skill[]) {
  return catSkills.every(s => form.value.allowed_skills.includes(s.name))
}

function isCatPartial(catSkills: Skill[]) {
  return catSkills.some(s => form.value.allowed_skills.includes(s.name)) && !isCatAllSelected(catSkills)
}

// 分类选项
const categories = ref<Record<string, string>>({})
const categoryOptions = computed(() =>
  Object.entries(categories.value).map(([key, label]) => ({ label, value: key }))
)

// ========== Tab 切换 ==========
const activeTab = ref<'manual' | 'ai'>('manual')

// ========== AI 辅助创建 ==========
interface AiChatMessage {
  role: 'user' | 'assistant'
  content: string
  config?: Record<string, any> | null
}

const AI_WELCOME = `告诉我你想要什么样的助手，比如：
- "一个帮我改论文的严厉导师"
- "一个温柔的英语口语陪练"
- "一个擅长数据分析的助手"`

const aiMessages = ref<AiChatMessage[]>([{ role: 'assistant', content: AI_WELCOME }])
const aiInputText = ref('')
const aiGenerating = ref(false)
const aiGeneratedConfig = ref<Record<string, any> | null>(null)
const aiScrollRef = ref<InstanceType<typeof NScrollbar> | null>(null)

function aiScrollToBottom() {
  nextTick(() => {
    aiScrollRef.value?.scrollTo({ top: 99999, behavior: 'smooth' })
  })
}

watch(() => aiMessages.value.length, aiScrollToBottom)

async function handleAiSend() {
  const content = aiInputText.value.trim()
  if (!content || aiGenerating.value) return

  aiMessages.value.push({ role: 'user', content })
  aiInputText.value = ''

  const apiMessages = aiMessages.value.map(m => ({ role: m.role, content: m.content }))

  aiGenerating.value = true
  try {
    const res = await generateAgent({ messages: apiMessages }) as any
    aiMessages.value.push({
      role: 'assistant',
      content: res.reply,
      config: res.agent_config,
    })
    if (res.agent_config) {
      aiGeneratedConfig.value = res.agent_config
    }
  } catch (e: any) {
    message.error(e?.response?.data?.detail || e.message || 'AI 服务暂时不可用')
    aiMessages.value.push({ role: 'assistant', content: '抱歉，生成过程出现错误，请重试。' })
  } finally {
    aiGenerating.value = false
  }
}

function handleAiKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleAiSend()
  }
}

function applyAiConfig() {
  if (!aiGeneratedConfig.value) return
  const cfg = aiGeneratedConfig.value
  form.value = {
    name: cfg.name || '',
    description: cfg.description || '',
    avatar: cfg.avatar || '🤖',
    welcome_message: cfg.welcome_message || '',
    soul_prompt: cfg.soul_prompt || '',
    category: cfg.category || null,
    allowed_skills: cfg.allowed_skills || [],
    skillMode: cfg.allowed_skills && cfg.allowed_skills.length > 0 ? 'custom' : 'all',
    example_questions: cfg.example_questions || [],
  }
  activeTab.value = 'manual'
  message.success('配置已填充到表单，可微调后保存')
}

function resetAiChat() {
  aiMessages.value = [{ role: 'assistant', content: AI_WELCOME }]
  aiInputText.value = ''
  aiGeneratedConfig.value = null
  aiGenerating.value = false
}

async function loadSkills() {
  try {
    const res = await getSkills()
    availableSkills.value = res || []
  } catch {
    // 静默失败
  }
}

async function loadCategories() {
  try {
    const res = await getAgentCategories()
    categories.value = (res as any) || {}
  } catch {
    // 静默失败
  }
}

function addExampleQuestion() {
  const text = newExample.value.trim()
  if (!text) return
  if (text.length > 200) {
    message.warning('示例问题不能超过 200 字符')
    return
  }
  if (form.value.example_questions.length >= 10) {
    message.warning('最多添加 10 个示例问题')
    return
  }
  form.value.example_questions.push(text)
  newExample.value = ''
}

function removeExampleQuestion(index: number) {
  form.value.example_questions.splice(index, 1)
}

onMounted(() => {
  loadSkills()
  loadCategories()
})

// 预设模板
const soulPromptTemplates = [
  { label: '苏格拉底式启发者', value: '你是一位擅长苏格拉底式教学的导师。你不直接给出答案，而是通过层层追问帮助用户自己发现问题和改进方向。你的提问应该循序渐进，由浅入深，引导用户深入思考。' },
  { label: '严厉判卷老师', value: '你是一位标准严格的判卷老师。你会仔细审查每一个细节，指出所有错误和不足之处。你的评价客观、直接，不会为了照顾情绪而降低标准。同时你也会指出做得好的地方并给予肯定。' },
  { label: '双语辅导员', value: '你是一位双语辅导员，精通中文和英文。你会根据用户的语言水平调整表达方式，在讲解中自然地融入双语对照。对于专业术语，你会同时给出中英文表述和解释。' },
  { label: '数据分析师', value: '你是一位数据驱动的分析师。你习惯用数据说话，善于从数据中发现规律和洞察。你的回答会尽量包含量化分析、数据对比和趋势判断。你擅长将复杂数据转化为易懂的图表和结论。' },
]

const isEditMode = computed(() => !!props.editAgent)

watch(() => props.show, (val) => {
  if (val && props.editAgent) {
    activeTab.value = 'manual'
    const hasCustomSkills = props.editAgent.allowed_skills && props.editAgent.allowed_skills.length > 0
    form.value = {
      name: props.editAgent.name || '',
      description: props.editAgent.description || '',
      avatar: props.editAgent.avatar || '🤖',
      welcome_message: props.editAgent.welcome_message || '',
      soul_prompt: props.editAgent.soul_prompt || '',
      category: props.editAgent.category || null,
      allowed_skills: props.editAgent.allowed_skills ? [...props.editAgent.allowed_skills] : [],
      skillMode: hasCustomSkills ? 'custom' : 'all',
      example_questions: props.editAgent.example_questions ? [...props.editAgent.example_questions] : [],
    }
  } else if (val) {
    activeTab.value = 'manual'
    form.value = {
      name: '', description: '', avatar: '🤖', welcome_message: '', soul_prompt: '',
      category: null, allowed_skills: [], skillMode: 'all', example_questions: [],
    }
    resetAiChat()
  }
})

// 常用 emoji 头像选项
const avatarOptions = ['🤖', '🎓', '📊', '✍️', '🔬', '📚', '💡', '🎯', '🧠', '🌟']

async function handleSave() {
  if (!form.value.name.trim()) {
    message.warning('请输入助手名称')
    return
  }
  if (form.value.soul_prompt && form.value.soul_prompt.length > 2000) {
    message.warning('助手人设不能超过 2000 字符')
    return
  }

  saving.value = true
  try {
    const payload = {
      name: form.value.name,
      description: form.value.description,
      avatar: form.value.avatar,
      welcome_message: form.value.welcome_message,
      soul_prompt: form.value.soul_prompt,
      category: form.value.category || null,
      allowed_skills: form.value.skillMode === 'custom' && form.value.allowed_skills.length > 0
        ? form.value.allowed_skills : null,
      example_questions: form.value.example_questions.length > 0
        ? form.value.example_questions : null,
    }
    let result: AgentInfo
    if (isEditMode.value && props.editAgent) {
      result = await updateAgent(props.editAgent.id, payload) as any
    } else {
      result = await createAgent(payload) as any
    }
    message.success(isEditMode.value ? '助手已更新' : '助手已创建')
    emit('saved', result)
    emit('update:show', false)
  } catch (e: any) {
    message.error(e?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

function applyTemplate(template: string) {
  form.value.soul_prompt = template
}

async function aiWriteSoulPrompt() {
  if (!form.value.name.trim()) {
    message.warning('请先输入助手名称')
    return
  }
  aiWriting.value = true
  // 简单的 AI 帮写：将用户输入扩展为结构化 prompt
  // TODO: Phase 4b+ 可以调后端 AI 接口生成
  setTimeout(() => {
    const name = form.value.name
    const desc = form.value.description || form.value.name
    form.value.soul_prompt = `你是一位专业的${name}。${desc ? `你的专长是${desc}。` : ''}

## 核心风格
- 回答准确、有深度
- 语言友好、易懂
- 善于举例说明

## 互动方式
- 先理解用户需求，必要时追问
- 给出结构化的回答
- 在合适的时候提供延伸建议`
    aiWriting.value = false
  }, 800)
}
</script>

<template>
  <n-drawer
    :show="props.show"
    @update:show="emit('update:show', $event)"
    :width="600"
    placement="right"
  >
    <n-drawer-content :title="isEditMode ? '编辑 AI 助手' : '创建 AI 助手'" closable :body-content-style="activeTab === 'ai' && !isEditMode ? 'display: flex; flex-direction: column; overflow: hidden; padding: 16px 16px 16px' : undefined">

      <!-- 创建模式显示 Tab，编辑模式直接显示表单 -->
      <n-tabs v-if="!isEditMode" v-model:value="activeTab" type="segment" size="small" style="margin-bottom: 20px; flex-shrink: 0">
        <n-tab-pane name="manual" tab="手动填写" />
        <n-tab-pane name="ai" tab="AI 帮我创建" />
      </n-tabs>

      <!-- ===== Tab: AI 帮我创建 ===== -->
      <template v-if="activeTab === 'ai' && !isEditMode">
        <div class="ai-chat-container">
          <n-scrollbar ref="aiScrollRef" class="ai-chat-area">
            <div class="ai-chat-messages">
              <div
                v-for="(msg, idx) in aiMessages"
                :key="idx"
                :class="['ai-chat-msg', msg.role === 'user' ? 'user-msg' : 'assistant-msg']"
              >
                <div class="ai-msg-avatar">{{ msg.role === 'user' ? '我' : 'AI' }}</div>
                <div class="ai-msg-body">
                  <div class="ai-msg-text markdown-body" v-html="md.render(msg.content)" />

                  <!-- 配置预览卡片 -->
                  <div v-if="msg.config" class="ai-config-card">
                    <div class="ai-config-card-title">已生成完整助手配置</div>

                    <div class="ai-config-section">
                      <n-tag size="small" type="info">基本信息</n-tag>
                      <div class="ai-config-rows">
                        <div class="ai-config-row"><span class="ai-config-label">名称</span> {{ msg.config.avatar || '' }} {{ msg.config.name }}</div>
                        <div class="ai-config-row" v-if="msg.config.description"><span class="ai-config-label">描述</span> {{ msg.config.description }}</div>
                      </div>
                    </div>

                    <div class="ai-config-section" v-if="msg.config.soul_prompt">
                      <n-tag size="small" type="info">人格设定</n-tag>
                      <div class="ai-config-rows">
                        <div class="ai-config-row">{{ msg.config.soul_prompt.length }} 字</div>
                      </div>
                    </div>

                    <div class="ai-config-section" v-if="msg.config.allowed_skills">
                      <n-tag size="small" type="info">技能范围</n-tag>
                      <div class="ai-config-tags">
                        <n-tag v-for="s in msg.config.allowed_skills" :key="s" size="tiny" :bordered="false">{{ s }}</n-tag>
                      </div>
                    </div>
                    <div class="ai-config-section" v-else>
                      <n-tag size="small" type="info">技能范围</n-tag>
                      <div class="ai-config-rows"><div class="ai-config-row">全部可用</div></div>
                    </div>

                    <div class="ai-config-section" v-if="msg.config.example_questions?.length">
                      <n-tag size="small" type="info">示例问题 ({{ msg.config.example_questions.length }})</n-tag>
                      <div class="ai-config-rows">
                        <div v-for="(q, i) in msg.config.example_questions.slice(0, 4)" :key="i" class="ai-config-row">
                          {{ i + 1 }}. {{ q }}
                        </div>
                        <div v-if="msg.config.example_questions.length > 4" class="ai-config-row" style="color: #999">
                          ...还有 {{ msg.config.example_questions.length - 4 }} 条
                        </div>
                      </div>
                    </div>

                    <div class="ai-config-section" v-if="msg.config.welcome_message">
                      <n-tag size="small" type="info">欢迎语</n-tag>
                      <div class="ai-config-rows">
                        <div class="ai-config-row ai-welcome-preview">{{ msg.config.welcome_message.slice(0, 100) }}{{ msg.config.welcome_message.length > 100 ? '...' : '' }}</div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <div v-if="aiGenerating" class="ai-chat-msg assistant-msg">
                <div class="ai-msg-avatar">AI</div>
                <div class="ai-msg-body">
                  <n-spin size="small" />
                  <span style="margin-left: 8px; color: #999">正在思考...</span>
                </div>
              </div>
            </div>
          </n-scrollbar>

          <!-- 采用配置操作条 -->
          <div v-if="aiGeneratedConfig" class="ai-action-bar">
            <span class="ai-action-hint">已生成配置</span>
            <n-space size="small">
              <n-button type="primary" size="small" @click="applyAiConfig">采用配置</n-button>
              <n-button size="small" @click="aiGeneratedConfig = null">继续修改</n-button>
            </n-space>
          </div>

          <!-- 输入区 -->
          <div class="ai-chat-input">
            <n-input
              v-model:value="aiInputText"
              type="textarea"
              :autosize="{ minRows: 1, maxRows: 3 }"
              placeholder="描述你想要的助手..."
              :disabled="aiGenerating"
              @keydown="handleAiKeydown"
            />
            <n-button
              type="primary"
              circle
              size="small"
              :disabled="!aiInputText.trim() || aiGenerating"
              :loading="aiGenerating"
              @click="handleAiSend"
            >
              <template #icon><n-icon :component="SendOutline" /></template>
            </n-button>
          </div>
        </div>
      </template>

      <!-- ===== Tab: 手动填写（原表单） ===== -->
      <template v-if="activeTab === 'manual' || isEditMode">

      <!-- 顶部说明 -->
      <div class="creator-intro">
        <p class="intro-main">自定义一个专属 AI 助手，改变它的回答风格和人设。</p>
        <p class="intro-sub">所有助手共享系统全部能力（联网搜索、学术检索、文件生成等），人设只改变"怎么回答"，不影响"能做什么"。</p>
      </div>

      <n-form label-placement="top" :show-feedback="false" class="agent-form">
        <!-- 基础信息区 -->
        <div class="form-section">
          <div class="section-label">基础信息</div>

          <n-form-item label="助手名称" required>
            <n-input v-model:value="form.name" placeholder="如：论文写作教练" maxlength="50" show-count />
          </n-form-item>

          <n-form-item>
            <template #label>
              一句话描述
              <span class="field-hint">一句话介绍助手，显示在卡片和头部</span>
            </template>
            <n-input v-model:value="form.description" placeholder="如：苏格拉底式启发写作指导" maxlength="200" />
          </n-form-item>

          <n-form-item label="头像">
            <div class="avatar-grid">
              <span
                v-for="emoji in avatarOptions"
                :key="emoji"
                class="avatar-option"
                :class="{ active: form.avatar === emoji }"
                @click="form.avatar = emoji"
              >{{ emoji }}</span>
            </div>
          </n-form-item>

          <n-form-item label="分类">
            <n-select
              v-model:value="form.category"
              :options="categoryOptions"
              placeholder="选择分类（可选）"
              clearable
            />
          </n-form-item>
        </div>

        <!-- 对话体验区 -->
        <div class="form-section">
          <div class="section-label">对话体验</div>

          <n-form-item>
            <template #label>
              欢迎语
              <span class="field-hint">助手开口说的第一段话，展示在新对话欢迎页</span>
            </template>
            <n-input
              v-model:value="form.welcome_message"
              type="textarea"
              placeholder="如：你好！我是你的论文写作教练。我擅长：&#10;- 论文结构梳理&#10;- 学术写作规范&#10;- 逻辑论证优化"
              :autosize="{ minRows: 2, maxRows: 4 }"
            />
          </n-form-item>

          <n-form-item>
            <template #label>
              助手人设与规则
              <span class="field-hint">决定 AI 的回答风格，不会覆盖系统能力</span>
            </template>
            <div style="width: 100%">
              <n-space style="margin-bottom: 8px">
                <n-button size="small" @click="aiWriteSoulPrompt" :loading="aiWriting">
                  ✨ AI 帮我写
                </n-button>
                <n-select
                  size="small"
                  placeholder="从模板选择"
                  :options="soulPromptTemplates.map(t => ({ label: t.label, value: t.value }))"
                  @update:value="applyTemplate"
                  style="width: 180px"
                  :consistent-menu-width="false"
                />
              </n-space>
              <n-input
                v-model:value="form.soul_prompt"
                type="textarea"
                placeholder="描述助手的人格、风格、规则...&#10;例如：你是一位严谨的学术导师，善于用启发式提问引导学生思考。"
                :autosize="{ minRows: 4, maxRows: 12 }"
                show-count
                :maxlength="2000"
              />
              <div class="soul-prompt-tips">
                写好人设的小技巧：明确角色身份 → 定义回答风格 → 设定互动方式
              </div>
            </div>
          </n-form-item>
        </div>

        <!-- 助手专长 -->
        <div class="form-section">
          <div class="section-label">助手专长</div>

          <n-form-item>
            <template #label>
              技能范围
              <span class="field-hint">限制助手可用的技能，不影响基础对话能力</span>
            </template>
            <div style="width: 100%">
              <n-radio-group v-model:value="form.skillMode" style="margin-bottom: 8px">
                <n-radio value="all">全部可用（推荐）</n-radio>
                <n-radio value="custom">指定专长</n-radio>
              </n-radio-group>
              <div v-if="form.skillMode === 'custom'" class="skill-panel">
                <!-- 搜索 + 统计 -->
                <div class="skill-toolbar">
                  <n-input
                    v-model:value="skillSearch"
                    placeholder="搜索技能..."
                    size="small"
                    clearable
                    style="width: 180px"
                  />
                  <span class="skill-toolbar-count">
                    已选 <strong>{{ form.allowed_skills.length }}</strong> / {{ availableSkills.length }}
                  </span>
                </div>
                <!-- 按分类分组 -->
                <div class="skill-groups">
                  <n-checkbox-group v-model:value="form.allowed_skills">
                    <div v-for="group in groupedSkills" :key="group.key" class="skill-category">
                      <div class="skill-category-header" @click="toggleCategory(group.skills)">
                        <n-checkbox
                          :checked="isCatAllSelected(group.skills)"
                          :indeterminate="isCatPartial(group.skills)"
                          @click.stop
                          @update:checked="toggleCategory(group.skills)"
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
                          <div style="max-width: 260px">
                            <div style="font-weight: 500; margin-bottom: 2px">{{ skill.display_name || skill.name }}</div>
                            <div v-if="skill.description" style="font-size: 12px; opacity: 0.85">{{ skill.description }}</div>
                          </div>
                        </n-tooltip>
                      </div>
                    </div>
                  </n-checkbox-group>
                </div>
              </div>
            </div>
          </n-form-item>
        </div>

        <!-- 示例问题 -->
        <div class="form-section">
          <div class="section-label">示例问题</div>

          <n-form-item>
            <template #label>
              引导问题
              <span class="field-hint">展示在欢迎页，帮助用户快速开始对话（最多 10 个）</span>
            </template>
            <div style="width: 100%">
              <div v-if="form.example_questions.length > 0" class="example-list">
                <n-tag
                  v-for="(q, idx) in form.example_questions"
                  :key="idx"
                  closable
                  @close="removeExampleQuestion(idx)"
                  round
                  size="medium"
                  style="max-width: 100%"
                >
                  <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">{{ q }}</span>
                </n-tag>
              </div>
              <div class="example-add-row">
                <n-input
                  v-model:value="newExample"
                  placeholder="输入示例问题..."
                  maxlength="200"
                  size="small"
                  @keyup.enter="addExampleQuestion"
                  style="flex: 1"
                />
                <n-button size="small" @click="addExampleQuestion" :disabled="form.example_questions.length >= 10">
                  添加
                </n-button>
              </div>
            </div>
          </n-form-item>
        </div>
      </n-form>
      </template>

      <template v-if="activeTab === 'manual' || isEditMode" #footer>
        <n-space justify="end">
          <n-button @click="emit('update:show', false)">取消</n-button>
          <n-button type="primary" @click="handleSave" :loading="saving">
            {{ isEditMode ? '保存' : '创建助手' }}
          </n-button>
        </n-space>
      </template>
    </n-drawer-content>
  </n-drawer>
</template>

<style scoped>
/* 顶部引导 */
.creator-intro {
  margin-bottom: 24px;
  padding: 12px 16px;
  background: #f0f7ff;
  border-radius: 8px;
  border-left: 3px solid #2080f0;
}

.intro-main {
  margin: 0 0 4px;
  font-size: 14px;
  font-weight: 500;
  color: #333;
}

.intro-sub {
  margin: 0;
  font-size: 12px;
  color: #666;
  line-height: 1.5;
}

/* 表单整体 */
.agent-form :deep(.n-form-item) {
  margin-bottom: 18px;
}

/* 分组区块 */
.form-section {
  margin-bottom: 8px;
}

.form-section + .form-section {
  padding-top: 16px;
  border-top: 1px solid #f0f0f0;
}

.section-label {
  font-size: 13px;
  font-weight: 600;
  color: #333;
  margin-bottom: 14px;
}

/* 头像网格 */
.avatar-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.avatar-option {
  width: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
  cursor: pointer;
  border-radius: 10px;
  border: 2px solid transparent;
  background: #f8f8f8;
  transition: all 0.2s;
}

.avatar-option:hover {
  background: #eee;
  transform: scale(1.08);
}

.avatar-option.active {
  border-color: #2080f0;
  background: #e8f4ff;
}

/* label 后的辅助提示 */
.field-hint {
  font-size: 12px;
  color: #aaa;
  font-weight: 400;
  margin-left: 6px;
}

/* 人设小技巧 */
.soul-prompt-tips {
  margin-top: 8px;
  padding: 6px 10px;
  font-size: 12px;
  color: #999;
  background: #fafafa;
  border-radius: 6px;
  line-height: 1.5;
}

/* 技能选择面板 */
.skill-panel {
  padding: 10px 12px;
  background: #f8fafc;
  border-radius: 8px;
  border: 1px solid #e8e8e8;
}

.skill-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.skill-toolbar-count {
  font-size: 12px;
  color: #999;
}

.skill-toolbar-count strong {
  color: #2080f0;
}

.skill-groups {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.skill-category {
  margin-bottom: 2px;
}

.skill-category-header {
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  padding: 2px 0;
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
  grid-template-columns: 1fr 1fr;
  gap: 2px 12px;
  padding-left: 24px;
  margin-top: 2px;
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

/* 示例问题列表 */
.example-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 10px;
}

.example-add-row {
  display: flex;
  gap: 8px;
  align-items: center;
}

/* ===== AI 辅助创建聊天 ===== */
.ai-chat-container {
  display: flex;
  flex-direction: column;
  flex: 1;
  overflow: hidden;
}

.ai-chat-area {
  flex: 1;
  overflow-y: auto;
}

.ai-chat-messages {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 8px 0;
}

.ai-chat-msg {
  display: flex;
  gap: 10px;
}

.ai-chat-msg.user-msg {
  flex-direction: row-reverse;
}

.ai-msg-avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
  flex-shrink: 0;
}

.assistant-msg .ai-msg-avatar { background: #e8f5e9; color: #2e7d32; }
.user-msg .ai-msg-avatar { background: #e3f2fd; color: #1565c0; }

.ai-msg-body {
  max-width: 85%;
  background: #f5f5f5;
  border-radius: 12px;
  padding: 10px 14px;
  font-size: 14px;
  line-height: 1.6;
}

.user-msg .ai-msg-body { background: #e3f2fd; }

.markdown-body :deep(p) { margin: 0 0 6px 0; }
.markdown-body :deep(p:last-child) { margin-bottom: 0; }
.markdown-body :deep(ul), .markdown-body :deep(ol) { margin: 4px 0; padding-left: 18px; }
.markdown-body :deep(li) { margin: 2px 0; }

.ai-config-card {
  margin-top: 10px;
  padding: 12px 14px;
  background: linear-gradient(135deg, #f0faf4, #f8fffe);
  border: 1px solid #c8e6c9;
  border-radius: 10px;
}

.ai-config-card-title {
  font-weight: 600;
  font-size: 13px;
  margin-bottom: 10px;
  color: #1a7f37;
  padding-bottom: 8px;
  border-bottom: 1px solid #e8f5e9;
}

.ai-config-section {
  border-left: 3px solid #a5d6a7;
  padding-left: 10px;
  margin-bottom: 10px;
}

.ai-config-section:last-child { margin-bottom: 0; }

.ai-config-rows { font-size: 13px; color: #555; margin-top: 4px; }

.ai-config-row {
  margin-bottom: 3px;
  line-height: 1.5;
}

.ai-config-label {
  color: #888;
  font-size: 12px;
  margin-right: 6px;
  min-width: 36px;
  display: inline-block;
}

.ai-config-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 4px;
}

.ai-welcome-preview {
  color: #666;
  font-style: italic;
  font-size: 12px;
  line-height: 1.5;
}

.ai-action-bar {
  padding: 8px 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-top: 1px solid #f0f0f0;
  flex-shrink: 0;
}

.ai-action-hint {
  font-size: 12px;
  color: #2080f0;
  font-weight: 500;
}

.ai-chat-input {
  display: flex;
  gap: 8px;
  align-items: flex-end;
  padding-top: 8px;
  border-top: 1px solid #f0f0f0;
  flex-shrink: 0;
}

.ai-chat-input .n-input { flex: 1; }
</style>
