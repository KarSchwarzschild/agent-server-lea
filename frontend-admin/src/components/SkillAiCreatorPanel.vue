<script setup lang="ts">
/**
 * AI 辅助 Skill 创建面板（Phase 3: 4 步向导 + 对话混合模式）
 *
 * Step 1: 需求理解（对话）
 * Step 2: 生成+审核配置（对话 + 配置卡片）
 * Step 3: 优化+测试（描述唯一性 + 意图匹配测试）
 * Step 4: 发布确认
 */
import { ref, nextTick, watch, computed } from 'vue'
import {
  NButton, NInput, NIcon, NSpin, NAlert, NSpace, NTag,
  NScrollbar, NSteps, NStep, NProgress, NCard,
  useMessage, useDialog,
} from 'naive-ui'
import { SendOutline, SparklesOutline, CheckmarkCircleOutline } from '@vicons/ionicons5'
import {
  generateSkill, evaluateDescription, testIntent,
  type SkillGenerateResponse, type DescriptionEvalResult, type IntentTestResult,
} from '../api/super-agent'
import MarkdownIt from 'markdown-it'

const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  config?: Record<string, any> | null
  validation?: { passed: boolean; warnings: string[]; errors: string[] } | null
}

const props = defineProps<{
  visible: boolean
}>()

const emit = defineEmits<{
  'config-generated': [config: Record<string, any>]
  'close': []
}>()

const message = useMessage()
const dialog = useDialog()

// ==================== 向导步骤状态 ====================
const currentStep = ref(1)

// ==================== Step 1-2: 对话状态 ====================
const WELCOME_MESSAGE = `你好！我是技能配置助手，可以帮你快速创建超级智能体的新技能。

请描述你想创建的技能，尽量包含以下信息：

1. 功能：这个技能做什么？（如：批改作文、生成教案、分析政策文件）
2. 触发方式：用户说什么话时触发？（如：帮我改作文、生成一份教案）
3. 输出形式：期望输出什么？（如：评分报告、结构化大纲、分析摘要）
4. 是否需要联网搜索或知识库检索？

示例输入："创建一个批改作文的技能，用户说'批改作文'触发，输出包含评分和修改建议的报告，需要搜索范文"

我会根据你的描述自动生成全部 5 个板块的配置：基本信息、系统提示词、工具与子代理、质量验证、意图识别。`

const messages = ref<ChatMessage[]>([
  {
    role: 'assistant',
    content: WELCOME_MESSAGE,
  },
])
const inputText = ref('')
const generating = ref(false)
const generatedConfig = ref<Record<string, any> | null>(null)
const scrollbarRef = ref<InstanceType<typeof NScrollbar> | null>(null)

// ==================== Step 3: 优化+测试状态 ====================
const descEvalResult = ref<DescriptionEvalResult | null>(null)
const descEvalLoading = ref(false)
const intentTestPrompt = ref('')
const intentTestResult = ref<IntentTestResult | null>(null)
const intentTestLoading = ref(false)

// ==================== Step 4: 发布状态 ====================
const publishing = ref(false)

// ==================== 计算属性 ====================
const descScoreColor = computed(() => {
  const score = descEvalResult.value?.score ?? 0
  if (score >= 80) return '#18a058'
  if (score >= 60) return '#f0a020'
  return '#d03050'
})

const descScoreStatus = computed(() => {
  const score = descEvalResult.value?.score ?? 0
  if (score >= 80) return 'success'
  if (score >= 60) return 'warning'
  return 'error'
})

// ==================== 对话逻辑 ====================
function scrollToBottom() {
  nextTick(() => {
    scrollbarRef.value?.scrollTo({ top: 99999, behavior: 'smooth' })
  })
}

watch(() => messages.value.length, scrollToBottom)

async function handleSend() {
  const content = inputText.value.trim()
  if (!content || generating.value) return

  messages.value.push({ role: 'user', content })
  inputText.value = ''

  const apiMessages = messages.value.map(m => ({
    role: m.role,
    content: m.content,
  }))

  generating.value = true
  try {
    const res: SkillGenerateResponse = await generateSkill({ messages: apiMessages })

    messages.value.push({
      role: 'assistant',
      content: res.reply,
      config: res.skill_config,
      validation: res.validation,
    })

    if (res.skill_config) {
      generatedConfig.value = res.skill_config
      // 自动进入 Step 2
      if (currentStep.value === 1) {
        currentStep.value = 2
      }
    }
  } catch (e: any) {
    message.error(e.message || 'AI 服务暂时不可用')
    messages.value.push({
      role: 'assistant',
      content: '抱歉，生成过程出现错误，请重试。',
    })
  } finally {
    generating.value = false
  }
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSend()
  }
}

// ==================== Step 3: 测试逻辑 ====================
async function handleEvaluateDescription() {
  if (!generatedConfig.value?.description) {
    message.warning('配置中缺少描述字段')
    return
  }
  descEvalLoading.value = true
  try {
    descEvalResult.value = await evaluateDescription({
      description: generatedConfig.value.description,
      exclude_name: generatedConfig.value.name || undefined,
    })
  } catch (e: any) {
    message.error('描述评估失败: ' + (e.message || '未知错误'))
  } finally {
    descEvalLoading.value = false
  }
}

async function handleTestIntent() {
  const prompt = intentTestPrompt.value.trim()
  if (!prompt) {
    message.warning('请输入测试用的用户提示词')
    return
  }
  intentTestLoading.value = true
  try {
    intentTestResult.value = await testIntent({ prompt })
  } catch (e: any) {
    message.error('意图测试失败: ' + (e.message || '未知错误'))
  } finally {
    intentTestLoading.value = false
  }
}

// ==================== 步骤导航 ====================
function goToStep(step: number) {
  if (step === 3 && !generatedConfig.value) {
    message.warning('请先生成配置后再进入测试')
    return
  }
  if (step === 4 && !generatedConfig.value) {
    message.warning('请先生成配置后再发布')
    return
  }
  currentStep.value = step
}

function handleNextStep() {
  if (currentStep.value === 2) {
    // 进入 Step 3 时自动触发描述评估
    currentStep.value = 3
    handleEvaluateDescription()
  } else if (currentStep.value === 3) {
    currentStep.value = 4
  }
}

function handlePrevStep() {
  if (currentStep.value > 1) {
    currentStep.value = currentStep.value - 1
  }
}

// ==================== Step 4: 发布 ====================
function handlePublish() {
  if (generatedConfig.value) {
    emit('config-generated', generatedConfig.value)
  }
}

function handleSwitchToForm() {
  if (generatedConfig.value) {
    emit('config-generated', generatedConfig.value)
  }
}

// ==================== 关闭与重置 ====================
function handleClose() {
  if (messages.value.length > 1) {
    dialog.warning({
      title: '确认关闭',
      content: '对话进行中，关闭后将丢失当前进度。确定关闭吗？',
      positiveText: '确定关闭',
      negativeText: '继续对话',
      onPositiveClick: () => {
        resetState()
        emit('close')
      },
    })
  } else {
    resetState()
    emit('close')
  }
}

function resetState() {
  messages.value = [{ role: 'assistant', content: WELCOME_MESSAGE }]
  inputText.value = ''
  generatedConfig.value = null
  generating.value = false
  currentStep.value = 1
  descEvalResult.value = null
  descEvalLoading.value = false
  intentTestPrompt.value = ''
  intentTestResult.value = null
  intentTestLoading.value = false
  publishing.value = false
}

defineExpose({ resetState, handleClose })
</script>

<template>
  <div class="ai-creator-panel">
    <!-- 步骤指示器 -->
    <div class="steps-header">
      <n-steps :current="currentStep" size="small">
        <n-step title="需求理解" />
        <n-step title="生成配置" />
        <n-step title="优化测试" />
        <n-step title="发布" />
      </n-steps>
    </div>

    <!-- Step 1 & 2: 对话区域 -->
    <template v-if="currentStep <= 2">
      <n-scrollbar ref="scrollbarRef" class="chat-area">
        <div class="chat-messages">
          <div
            v-for="(msg, idx) in messages"
            :key="idx"
            :class="['chat-message', msg.role === 'user' ? 'user-message' : 'assistant-message']"
          >
            <div class="message-avatar">
              {{ msg.role === 'user' ? '管理员' : 'AI' }}
            </div>
            <div class="message-content">
              <div class="message-text markdown-body" v-html="md.render(msg.content)" />

              <!-- 配置摘要卡片 -->
              <div v-if="msg.config" class="config-summary">
                <div class="config-summary-title">
                  <n-icon :component="SparklesOutline" size="16" />
                  已生成完整技能配置
                </div>
                <div class="config-summary-items">
                  <div class="config-section">
                    <div class="config-section-label">
                      <n-tag size="small" type="info">基本信息</n-tag>
                    </div>
                    <div class="config-section-body">
                      <div class="config-row">
                        <span class="config-label">名称</span>
                        <span>{{ msg.config.name }} / {{ msg.config.display_name }}</span>
                      </div>
                      <div class="config-row">
                        <span class="config-label">描述</span>
                        <span>{{ msg.config.description || '-' }}</span>
                      </div>
                      <div class="config-row">
                        <n-tag size="tiny" :bordered="false">{{ msg.config.category || 'general' }}</n-tag>
                        <n-tag size="tiny" :bordered="false">{{ msg.config.execution_mode || 'normal' }}</n-tag>
                        <n-tag size="tiny" :bordered="false">模型: {{ msg.config.model_tier || 'mid' }}</n-tag>
                        <n-tag size="tiny" :bordered="false" v-if="msg.config.max_iterations">迭代: {{ msg.config.max_iterations }}</n-tag>
                      </div>
                    </div>
                  </div>

                  <div class="config-section">
                    <div class="config-section-label">
                      <n-tag size="small" type="info">系统提示词</n-tag>
                    </div>
                    <div class="config-section-body">
                      <span>{{ (msg.config.system_prompt || '').length }} 字符</span>
                      <span class="config-hint" v-if="(msg.config.system_prompt || '').length > 0">
                        （含角色定义、核心职责、工具使用、输出要求、注意事项）
                      </span>
                    </div>
                  </div>

                  <div class="config-section">
                    <div class="config-section-label">
                      <n-tag size="small" type="info">工具与子代理</n-tag>
                    </div>
                    <div class="config-section-body">
                      <div class="config-row">
                        <span class="config-label">工具层级</span>
                        <n-tag size="tiny" :bordered="false">{{ msg.config.tool_tier || '无' }}</n-tag>
                      </div>
                      <div class="config-row" v-if="msg.config.extra_tools?.length">
                        <span class="config-label">额外工具</span>
                        <n-tag size="tiny" :bordered="false" v-for="t in msg.config.extra_tools" :key="t">{{ t }}</n-tag>
                      </div>
                      <div class="config-row" v-if="msg.config.denied_tools?.length">
                        <span class="config-label">排除工具</span>
                        <n-tag size="tiny" type="error" :bordered="false" v-for="t in msg.config.denied_tools" :key="t">{{ t }}</n-tag>
                      </div>
                    </div>
                  </div>

                  <div class="config-section">
                    <div class="config-section-label">
                      <n-tag size="small" type="info">质量验证</n-tag>
                    </div>
                    <div class="config-section-body">
                      <template v-if="msg.config.verification_config">
                        <div class="config-row">
                          <span class="config-label">策略</span>
                          <span>{{ msg.config.verification_config.strategy }}</span>
                          <span v-if="msg.config.verification_config.min_score != null">
                            最低 {{ msg.config.verification_config.min_score }} 分
                          </span>
                        </div>
                        <div class="config-row" v-if="msg.config.verification_config.dimensions?.length">
                          <span class="config-label">维度</span>
                          <n-tag size="tiny" :bordered="false" v-for="d in msg.config.verification_config.dimensions" :key="d.name">
                            {{ d.name }} ({{ (d.weight * 100).toFixed(0) }}%)
                          </n-tag>
                        </div>
                      </template>
                      <span v-else class="config-hint">无（简单问答类）</span>
                    </div>
                  </div>

                  <div class="config-section">
                    <div class="config-section-label">
                      <n-tag size="small" type="info">意图识别</n-tag>
                    </div>
                    <div class="config-section-body">
                      <div class="config-row">
                        <span class="config-label">触发词</span>
                        <span>{{ (msg.config.trigger_keywords || []).length }} 个</span>
                        <span v-if="msg.config.intent_priority != null" style="margin-left: 12px">
                          <span class="config-label">优先级</span> {{ msg.config.intent_priority }}
                        </span>
                      </div>
                      <div class="config-row config-keywords" v-if="(msg.config.trigger_keywords || []).length">
                        <n-tag size="tiny" :bordered="false" v-for="kw in msg.config.trigger_keywords" :key="kw">{{ kw }}</n-tag>
                      </div>
                      <div class="config-row" v-if="msg.config.regex_patterns?.length">
                        <span class="config-label">正则</span>
                        <span>{{ msg.config.regex_patterns.length }} 条</span>
                      </div>
                    </div>
                  </div>
                </div>

                <div v-if="msg.validation" class="config-validation">
                  <n-alert v-if="msg.validation.errors?.length" type="error" :bordered="false" style="margin-bottom: 8px">
                    <div v-for="(err, i) in msg.validation.errors" :key="i">{{ err }}</div>
                  </n-alert>
                  <n-alert v-if="msg.validation.warnings?.length" type="warning" :bordered="false">
                    <div v-for="(w, i) in msg.validation.warnings" :key="i">{{ w }}</div>
                  </n-alert>
                  <n-alert v-if="msg.validation.passed && !msg.validation.warnings?.length" type="success" :bordered="false">
                    配置校验通过
                  </n-alert>
                </div>
              </div>
            </div>
          </div>

          <div v-if="generating" class="chat-message assistant-message">
            <div class="message-avatar">AI</div>
            <div class="message-content">
              <n-spin size="small" />
              <span style="margin-left: 8px; color: #999">正在思考...</span>
            </div>
          </div>
        </div>
      </n-scrollbar>

      <!-- Step 2: 配置生成后的操作区 -->
      <div v-if="generatedConfig && currentStep === 2" class="switch-prompt">
        <n-alert type="success" :bordered="false">
          <template #header>AI 已生成配置</template>
          您可以继续对话修改配置，或进入下一步进行优化测试。
        </n-alert>
        <n-space style="margin-top: 8px">
          <n-button type="primary" @click="handleNextStep">
            下一步：优化测试
          </n-button>
          <n-button @click="handleSwitchToForm">
            直接切换到表单模式
          </n-button>
        </n-space>
      </div>

      <!-- 输入区域 -->
      <div class="chat-input">
        <n-input
          v-model:value="inputText"
          type="textarea"
          :autosize="{ minRows: 1, maxRows: 4 }"
          placeholder="描述技能的功能、触发方式和输出形式..."
          :disabled="generating"
          @keydown="handleKeydown"
        />
        <n-button
          type="primary"
          circle
          :disabled="!inputText.trim() || generating"
          :loading="generating"
          @click="handleSend"
        >
          <template #icon>
            <n-icon :component="SendOutline" />
          </template>
        </n-button>
      </div>
    </template>

    <!-- Step 3: 优化+测试 -->
    <template v-if="currentStep === 3">
      <div class="test-panel">
        <div class="test-panel-grid">
          <!-- 左侧：描述唯一性评分 -->
          <n-card title="描述唯一性评分" size="small">
            <template #header-extra>
              <n-button size="small" :loading="descEvalLoading" @click="handleEvaluateDescription">
                重新评估
              </n-button>
            </template>

            <div v-if="descEvalLoading" style="text-align: center; padding: 20px">
              <n-spin size="medium" />
            </div>

            <div v-else-if="descEvalResult">
              <div class="score-display">
                <n-progress
                  type="circle"
                  :percentage="descEvalResult.score"
                  :color="descScoreColor"
                  :rail-color="descScoreColor + '20'"
                  :stroke-width="8"
                  style="width: 100px"
                >
                  <span class="score-number">{{ descEvalResult.score }}</span>
                </n-progress>
                <div class="score-label">
                  <n-tag :type="descScoreStatus" size="small">
                    {{ descEvalResult.score >= 80 ? '高唯一性' : descEvalResult.score >= 60 ? '中等' : '低唯一性' }}
                  </n-tag>
                </div>
              </div>

              <div v-if="descEvalResult.suggestion" class="eval-suggestion">
                <n-alert type="warning" :bordered="false" style="margin-top: 12px">
                  {{ descEvalResult.suggestion }}
                </n-alert>
              </div>

              <div v-if="descEvalResult.conflicts.length" class="eval-conflicts">
                <div class="conflicts-title">相似技能：</div>
                <div v-for="c in descEvalResult.conflicts" :key="c.name" class="conflict-item">
                  <span class="conflict-name">{{ c.display_name }}</span>
                  <n-tag size="tiny" :bordered="false">
                    相似度 {{ (c.similarity * 100).toFixed(0) }}%
                  </n-tag>
                </div>
              </div>
            </div>

            <div v-else style="color: #999; text-align: center; padding: 20px">
              点击"重新评估"开始检测
            </div>
          </n-card>

          <!-- 右侧：意图匹配测试 -->
          <n-card title="意图匹配测试" size="small">
            <div class="intent-test-input">
              <n-input
                v-model:value="intentTestPrompt"
                placeholder="输入模拟用户提示词，如：帮我批改这篇作文"
                :disabled="intentTestLoading"
                @keydown.enter="handleTestIntent"
              />
              <n-button
                type="primary"
                size="small"
                :loading="intentTestLoading"
                :disabled="!intentTestPrompt.trim()"
                @click="handleTestIntent"
                style="margin-top: 8px"
              >
                测试匹配
              </n-button>
            </div>

            <div v-if="intentTestLoading" style="text-align: center; padding: 20px">
              <n-spin size="medium" />
            </div>

            <div v-else-if="intentTestResult" class="intent-result">
              <div class="intent-result-main">
                <div class="intent-result-row">
                  <span class="intent-label">匹配类型</span>
                  <n-tag :type="intentTestResult.result.type === 'skill' ? 'success' : 'info'" size="small">
                    {{ intentTestResult.result.type }}
                  </n-tag>
                </div>
                <div class="intent-result-row" v-if="intentTestResult.result.skill_name">
                  <span class="intent-label">匹配技能</span>
                  <n-tag size="small">{{ intentTestResult.result.skill_name }}</n-tag>
                </div>
                <div class="intent-result-row">
                  <span class="intent-label">置信度</span>
                  <n-progress
                    type="line"
                    :percentage="Math.round(intentTestResult.result.confidence * 100)"
                    :color="intentTestResult.result.confidence >= 0.8 ? '#18a058' : intentTestResult.result.confidence >= 0.6 ? '#f0a020' : '#d03050'"
                    style="width: 150px"
                  />
                </div>
                <div class="intent-result-row" v-if="intentTestResult.result.match_source">
                  <span class="intent-label">匹配来源</span>
                  <n-tag size="tiny" :bordered="false">{{ intentTestResult.result.match_source }}</n-tag>
                </div>
              </div>

              <!-- 是否匹配到当前正在创建的 Skill -->
              <n-alert
                v-if="intentTestResult.result.type === 'skill' && intentTestResult.result.skill_name === generatedConfig?.name"
                type="success"
                :bordered="false"
                style="margin-top: 8px"
              >
                匹配到当前创建的技能，意图识别正常。
              </n-alert>
              <n-alert
                v-else-if="intentTestResult.result.type === 'skill' && intentTestResult.result.skill_name !== generatedConfig?.name"
                type="warning"
                :bordered="false"
                style="margin-top: 8px"
              >
                匹配到其他技能「{{ intentTestResult.result.skill_name }}」，建议调整触发词或描述。
              </n-alert>
              <n-alert
                v-else-if="intentTestResult.result.type === 'qa'"
                type="info"
                :bordered="false"
                style="margin-top: 8px"
              >
                未匹配到任何技能（走 QA 通道），建议检查触发词和正则配置。
              </n-alert>
            </div>
          </n-card>
        </div>

        <!-- 底部操作 -->
        <div class="step-actions">
          <n-button @click="handlePrevStep">上一步</n-button>
          <n-button type="primary" @click="handleNextStep">下一步：发布确认</n-button>
        </div>
      </div>
    </template>

    <!-- Step 4: 发布确认 -->
    <template v-if="currentStep === 4">
      <div class="publish-panel" v-if="generatedConfig">
        <n-card title="配置摘要" size="small">
          <div class="publish-summary">
            <div class="publish-row">
              <span class="publish-label">技能名称</span>
              <span>{{ generatedConfig.name }}</span>
            </div>
            <div class="publish-row">
              <span class="publish-label">显示名称</span>
              <span>{{ generatedConfig.display_name }}</span>
            </div>
            <div class="publish-row">
              <span class="publish-label">描述</span>
              <span>{{ generatedConfig.description || '-' }}</span>
            </div>
            <div class="publish-row">
              <span class="publish-label">分类</span>
              <n-tag size="small">{{ generatedConfig.category || 'general' }}</n-tag>
            </div>
            <div class="publish-row">
              <span class="publish-label">执行模式</span>
              <n-tag size="small">{{ generatedConfig.execution_mode || 'normal' }}</n-tag>
            </div>
            <div class="publish-row">
              <span class="publish-label">模型等级</span>
              <n-tag size="small">{{ generatedConfig.model_tier || 'mid' }}</n-tag>
            </div>
            <div class="publish-row">
              <span class="publish-label">工具层级</span>
              <n-tag size="small">{{ generatedConfig.tool_tier || '无' }}</n-tag>
            </div>
            <div class="publish-row">
              <span class="publish-label">触发词数量</span>
              <span>{{ (generatedConfig.trigger_keywords || []).length }} 个</span>
            </div>
            <div class="publish-row">
              <span class="publish-label">系统提示词</span>
              <span>{{ (generatedConfig.system_prompt || '').length }} 字符</span>
            </div>
          </div>
        </n-card>

        <!-- 校验状态 -->
        <div class="publish-checks" style="margin-top: 12px">
          <n-alert
            v-if="descEvalResult && descEvalResult.score < 60"
            type="warning"
            :bordered="false"
            style="margin-bottom: 8px"
          >
            描述唯一性得分较低（{{ descEvalResult.score }}），发布后可能与现有技能产生意图冲突。
          </n-alert>
          <n-alert type="info" :bordered="false">
            点击"发布"后将切换到表单模式，您可以在表单中进一步调整每个板块的细节。
          </n-alert>
        </div>

        <!-- 底部操作 -->
        <div class="step-actions" style="margin-top: 16px">
          <n-button @click="handlePrevStep">上一步</n-button>
          <n-space>
            <n-button @click="handleSwitchToForm">切换到表单模式</n-button>
            <n-button type="primary" :loading="publishing" @click="handlePublish">
              <template #icon>
                <n-icon :component="CheckmarkCircleOutline" />
              </template>
              确认发布
            </n-button>
          </n-space>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.ai-creator-panel {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 120px);
}

.steps-header {
  padding: 12px 16px;
  border-bottom: 1px solid #f0f0f0;
  background: #fafafa;
}

.chat-area {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
}

.chat-messages {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.chat-message {
  display: flex;
  gap: 12px;
}

.user-message {
  flex-direction: row-reverse;
}

.message-avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
  flex-shrink: 0;
}

.assistant-message .message-avatar {
  background: #e8f5e9;
  color: #2e7d32;
}

.user-message .message-avatar {
  background: #e3f2fd;
  color: #1565c0;
}

.message-content {
  max-width: 80%;
  background: #f5f5f5;
  border-radius: 12px;
  padding: 12px 16px;
  font-size: 14px;
  line-height: 1.6;
}

.user-message .message-content {
  background: #e3f2fd;
}

.config-summary {
  margin-top: 12px;
  padding: 12px;
  background: #fff;
  border: 1px solid #e0e0e0;
  border-radius: 8px;
}

.config-summary-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  font-size: 14px;
  margin-bottom: 10px;
  color: #1a7f37;
}

.config-summary-items {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.config-section {
  border-left: 3px solid #e0e0e0;
  padding-left: 10px;
}

.config-section-label {
  margin-bottom: 4px;
}

.config-section-body {
  font-size: 13px;
  color: #555;
}

.config-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 2px;
}

.config-label {
  color: #888;
  font-size: 12px;
  min-width: 48px;
}

.config-hint {
  color: #aaa;
  font-size: 12px;
}

.config-keywords {
  flex-wrap: wrap;
}

/* Markdown 渲染样式 */
.markdown-body :deep(p) {
  margin: 0 0 8px 0;
}
.markdown-body :deep(p:last-child) {
  margin-bottom: 0;
}
.markdown-body :deep(ol),
.markdown-body :deep(ul) {
  margin: 4px 0;
  padding-left: 20px;
}
.markdown-body :deep(li) {
  margin: 2px 0;
}
.markdown-body :deep(code) {
  background: #e8e8e8;
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 13px;
}
.markdown-body :deep(strong) {
  font-weight: 600;
}

.config-validation {
  margin-top: 10px;
}

.switch-prompt {
  padding: 12px 16px;
  border-top: 1px solid #f0f0f0;
}

.chat-input {
  display: flex;
  gap: 8px;
  align-items: flex-end;
  padding: 12px 16px;
  border-top: 1px solid #f0f0f0;
}

.chat-input .n-input {
  flex: 1;
}

/* Step 3: 测试面板 */
.test-panel {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
}

.test-panel-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  flex: 1;
}

@media (max-width: 800px) {
  .test-panel-grid {
    grid-template-columns: 1fr;
  }
}

.score-display {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 12px 0;
}

.score-number {
  font-size: 24px;
  font-weight: 700;
}

.score-label {
  text-align: center;
}

.eval-conflicts {
  margin-top: 12px;
}

.conflicts-title {
  font-size: 13px;
  color: #888;
  margin-bottom: 6px;
}

.conflict-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 0;
  font-size: 13px;
}

.conflict-name {
  color: #333;
}

.intent-test-input {
  margin-bottom: 12px;
}

.intent-result {
  margin-top: 8px;
}

.intent-result-main {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.intent-result-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.intent-label {
  color: #888;
  font-size: 12px;
  min-width: 60px;
}

.step-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 0;
  margin-top: auto;
  border-top: 1px solid #f0f0f0;
}

/* Step 4: 发布面板 */
.publish-panel {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
}

.publish-summary {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.publish-row {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 14px;
}

.publish-label {
  color: #888;
  font-size: 13px;
  min-width: 80px;
  flex-shrink: 0;
}
</style>
