<script setup lang="ts">
/**
 * 通知渠道管理页面（Webhook 群机器人）
 * 用户自建飞书/钉钉群机器人，通过 Webhook 接收订阅通知
 */
import { ref, onMounted, computed } from 'vue'
import {
  NCard,
  NButton,
  NIcon,
  NModal,
  NSpace,
  NTag,
  NSpin,
  NAlert,
  NInput,
  NRadioGroup,
  NRadio,
  NCollapse,
  NCollapseItem,
  NEmpty,
  NSwitch,
  NSelect,
  useMessage,
  useDialog,
} from 'naive-ui'
import {
  AddOutline,
  TrashOutline,
  CreateOutline,
  PaperPlaneOutline,
  ArrowBackOutline,
  CheckmarkCircleOutline,
  WarningOutline,
  TimeOutline,
} from '@vicons/ionicons5'
import { useRouter } from 'vue-router'
import {
  getWebhooks,
  createWebhook,
  updateWebhook,
  deleteWebhook,
  testWebhook,
  type Webhook,
  type WebhookCreateData,
} from '../../api/webhook'
import { useResponsive } from '../../composables/useResponsive'

const router = useRouter()
const message = useMessage()
const dialog = useDialog()
const { isMobile } = useResponsive()

const loading = ref(false)
const webhooks = ref<Webhook[]>([])

// 弹窗状态
const showFormModal = ref(false)
const formMode = ref<'create' | 'edit'>('create')
const formLoading = ref(false)
const editingId = ref<number | null>(null)

// 表单数据
const formChannelType = ref('feishu_webhook')
const formName = ref('')
const formUrl = ref('')
const formSecret = ref('')
// 非 Webhook 类型额外字段
const formTelegramChatId = ref('')
const formEmailPreset = ref<string | null>(null)
const formSmtpHost = ref('')
const formSmtpPort = ref(587)
const formSmtpUser = ref('')
const formToAddr = ref('')
const showEmailAdvanced = ref(false)

// SMTP 预设
const smtpPresets = [
  { label: 'QQ邮箱', value: 'qq', host: 'smtp.qq.com', port: 465, secretLabel: '授权码（非登录密码）', hint: 'QQ邮箱授权码获取：设置 → 账户 → POP3/SMTP → 生成授权码' },
  { label: '网易163', value: '163', host: 'smtp.163.com', port: 465, secretLabel: '授权码（非登录密码）', hint: '163邮箱授权码获取：设置 → POP3/SMTP → 开启并获取授权码' },
  { label: 'Gmail', value: 'gmail', host: 'smtp.gmail.com', port: 587, secretLabel: '应用专用密码', hint: 'Gmail 需开启两步验证后生成应用专用密码' },
  { label: 'Outlook', value: 'outlook', host: 'smtp-mail.outlook.com', port: 587, secretLabel: '密码', hint: '' },
  { label: '自定义...', value: 'custom', host: '', port: 587, secretLabel: '密码', hint: '' },
]

const emailPresetOptions = smtpPresets.map(p => ({ label: p.label, value: p.value }))

const currentSmtpPreset = computed(() =>
  smtpPresets.find(p => p.value === formEmailPreset.value)
)

const emailSecretLabel = computed(() =>
  currentSmtpPreset.value?.secretLabel || '密码'
)

const emailHint = computed(() =>
  currentSmtpPreset.value?.hint || ''
)

// 渠道类型配置
const channelTypes = [
  {
    key: 'feishu_webhook',
    label: '飞书',
    fullLabel: '飞书群机器人',
    color: '#3370ff',
    icon: '飞',
    desc: '适合飞书群组通知，支持富文本卡片',
    isWebhook: true,
    guide: [
      '打开飞书，创建或进入一个群',
      '群设置 → 群机器人 → 添加机器人',
      '选择「自定义机器人」',
      '安全设置可选「签名校验」',
      '复制 Webhook 地址粘贴到上方',
    ],
  },
  {
    key: 'dingtalk_webhook',
    label: '钉钉',
    fullLabel: '钉钉群机器人',
    color: '#0089ff',
    icon: '钉',
    desc: '适合钉钉群组通知，支持 Markdown',
    isWebhook: true,
    guide: [
      '打开钉钉，创建或进入一个群',
      '群设置 → 智能群助手 → 添加机器人',
      '选择「自定义（通过 Webhook 接入）」',
      '安全设置可选「加签」',
      '复制 Webhook 地址粘贴到上方',
    ],
  },
  {
    key: 'wework_webhook',
    label: '企微',
    fullLabel: '企业微信 Webhook',
    color: '#07c160',
    icon: '微',
    desc: '适合企业内部通知，简单无需签名',
    isWebhook: true,
    guide: [
      '打开企业微信 → 目标群聊 → 设置',
      '群机器人 → 添加机器人 → 自定义',
      '复制 Webhook 地址粘贴到上方',
      '此渠道无需填写签名密钥',
    ],
  },
  {
    key: 'telegram_bot',
    label: 'Telegram',
    fullLabel: 'Telegram Bot',
    color: '#0088cc',
    icon: 'T',
    desc: '适合海外用户，需能访问 Telegram',
    isWebhook: false,
    guide: [
      '在 Telegram 搜索 @BotFather → 发送 /newbot',
      '按提示设置 Bot 名称和用户名',
      '复制 HTTP API Token 填入下方',
      '给你的 Bot 发送任意消息',
      '回到本页面填写 Chat ID',
    ],
  },
  {
    key: 'email',
    label: 'Email',
    fullLabel: 'Email 邮件',
    color: '#e74c3c',
    icon: '@',
    desc: '邮件通知，完整报告不分批',
    isWebhook: false,
    guide: [],
  },
]

const getChannelConfig = (type: string) => channelTypes.find(c => c.key === type)

/** 获取渠道中文名称（修复三元表达式 bug） */
const getChannelLabel = (type: string) => {
  return getChannelConfig(type)?.label || type
}

/** 当前渠道是否为 Webhook 类型 */
const isWebhookType = computed(() => {
  const config = getChannelConfig(formChannelType.value)
  return config?.isWebhook !== false
})

/** 检测 webhook 是否因密钥变更导致损坏 */
const isCorrupted = (wh: Webhook) => wh.webhook_url_masked.includes('解密失败')

const currentGuide = computed(() => {
  const config = getChannelConfig(formChannelType.value)
  return config?.guide || []
})

// ==================== 数据加载 ====================

const loadWebhooks = async () => {
  loading.value = true
  try {
    const res = await getWebhooks()
    webhooks.value = res.items
  } catch {
    message.error('加载通知渠道失败')
  } finally {
    loading.value = false
  }
}

// ==================== 添加/编辑 ====================

const resetFormFields = () => {
  formName.value = ''
  formUrl.value = ''
  formSecret.value = ''
  formTelegramChatId.value = ''
  formEmailPreset.value = null
  formSmtpHost.value = ''
  formSmtpPort.value = 587
  formSmtpUser.value = ''
  formToAddr.value = ''
  showEmailAdvanced.value = false
}

const handleEmailPresetChange = (val: string | null) => {
  const preset = smtpPresets.find(p => p.value === val)
  if (preset && val !== 'custom') {
    formSmtpHost.value = preset.host
    formSmtpPort.value = preset.port
  }
}

const openCreateModal = () => {
  formMode.value = 'create'
  editingId.value = null
  formChannelType.value = 'feishu_webhook'
  resetFormFields()
  showFormModal.value = true
}

const openEditModal = (wh: Webhook) => {
  formMode.value = 'edit'
  editingId.value = wh.id
  formChannelType.value = wh.channel_type
  resetFormFields()
  formName.value = wh.name
  // 回填 extra_config 中的可见字段
  if (wh.extra_config) {
    if (wh.channel_type === 'telegram_bot') {
      formTelegramChatId.value = wh.extra_config.chat_id || ''
    } else if (wh.channel_type === 'email') {
      formSmtpHost.value = wh.extra_config.smtp_host || ''
      formSmtpPort.value = wh.extra_config.smtp_port || 587
      formSmtpUser.value = wh.extra_config.smtp_user || ''
      formToAddr.value = wh.extra_config.to_addr || ''
      // 尝试匹配预设
      const matched = smtpPresets.find(p => p.host === formSmtpHost.value && p.value !== 'custom')
      formEmailPreset.value = matched ? matched.value : 'custom'
    }
  }
  showFormModal.value = true
}

/** 构建创建/编辑请求数据 */
const buildFormData = (): WebhookCreateData | null => {
  const ct = formChannelType.value
  const isWebhook = getChannelConfig(ct)?.isWebhook !== false

  if (isWebhook) {
    // Webhook 类型：需要 URL
    if (formMode.value === 'create' && !formUrl.value.trim()) {
      message.warning('请输入 Webhook 地址')
      return null
    }
    const data: WebhookCreateData = {
      name: formName.value.trim(),
      channel_type: ct,
      webhook_url: formUrl.value.trim(),
    }
    if (formSecret.value.trim()) {
      data.webhook_secret = formSecret.value.trim()
    }
    return data
  } else if (ct === 'telegram_bot') {
    if (!formSecret.value.trim()) {
      message.warning('请输入 Bot Token')
      return null
    }
    if (!formTelegramChatId.value.trim()) {
      message.warning('请输入 Chat ID')
      return null
    }
    return {
      name: formName.value.trim(),
      channel_type: ct,
      webhook_secret: formSecret.value.trim(),
      extra_config: { chat_id: formTelegramChatId.value.trim() },
    }
  } else if (ct === 'email') {
    if (!formSmtpUser.value.trim()) {
      message.warning('请输入发件邮箱')
      return null
    }
    if (!formSecret.value.trim()) {
      message.warning(`请输入${emailSecretLabel.value}`)
      return null
    }
    if (!formToAddr.value.trim()) {
      message.warning('请输入收件邮箱')
      return null
    }
    if (!formSmtpHost.value.trim()) {
      message.warning('请选择邮箱服务商或手动输入 SMTP 服务器')
      return null
    }
    return {
      name: formName.value.trim(),
      channel_type: ct,
      webhook_secret: formSecret.value.trim(),
      extra_config: {
        smtp_host: formSmtpHost.value.trim(),
        smtp_port: formSmtpPort.value,
        smtp_user: formSmtpUser.value.trim(),
        to_addr: formToAddr.value.trim(),
      },
    }
  }
  return null
}

const handleTestAndSave = async () => {
  if (!formName.value.trim()) {
    message.warning('请输入名称')
    return
  }

  formLoading.value = true

  try {
    if (formMode.value === 'create') {
      const data = buildFormData()
      if (!data) { formLoading.value = false; return }

      const created = await createWebhook(data)

      // 测试
      const testResult = await testWebhook(created.id)
      if (testResult.success) {
        message.success('测试发送成功，渠道已添加')
      } else {
        message.warning('渠道已添加，但测试发送失败，请检查配置')
      }
    } else if (editingId.value) {
      const updateData: Record<string, any> = {
        name: formName.value.trim(),
      }

      const ct = formChannelType.value
      const isWebhook = getChannelConfig(ct)?.isWebhook !== false

      if (isWebhook && formUrl.value.trim()) {
        updateData.webhook_url = formUrl.value.trim()
      }
      if (formSecret.value.trim()) {
        updateData.webhook_secret = formSecret.value.trim()
      }

      // 更新 extra_config
      if (ct === 'telegram_bot' && formTelegramChatId.value.trim()) {
        updateData.extra_config = { chat_id: formTelegramChatId.value.trim() }
      } else if (ct === 'email') {
        if (formSmtpHost.value.trim()) {
          updateData.extra_config = {
            smtp_host: formSmtpHost.value.trim(),
            smtp_port: formSmtpPort.value,
            smtp_user: formSmtpUser.value.trim(),
            to_addr: formToAddr.value.trim(),
          }
        }
      }

      await updateWebhook(editingId.value, updateData)

      // 如果更新了关键配置，测试一下
      if (formUrl.value.trim() || formSecret.value.trim() || updateData.extra_config) {
        const testResult = await testWebhook(editingId.value)
        if (testResult.success) {
          message.success('配置已更新，测试发送成功')
        } else {
          message.warning('配置已更新，但测试发送失败，请检查配置')
        }
      } else {
        message.success('配置已更新')
      }
    }

    showFormModal.value = false
    loadWebhooks()
  } catch (err: any) {
    const detail = err?.response?.data?.detail || err?.message || '操作失败'
    message.error(detail)
  } finally {
    formLoading.value = false
  }
}

// ==================== 测试 ====================

const handleTest = async (wh: Webhook) => {
  try {
    const res = await testWebhook(wh.id)
    if (res.success) {
      message.success('测试消息发送成功')
      loadWebhooks()
    } else {
      message.error(res.message)
    }
  } catch (err: any) {
    const detail = err?.response?.data?.detail || '测试失败'
    message.error(detail)
  }
}

// ==================== 删除 ====================

const handleDelete = (wh: Webhook) => {
  const config = getChannelConfig(wh.channel_type)
  dialog.warning({
    title: '删除群机器人',
    content: `确定要删除「${wh.name}」吗？关联的订阅将不再向该渠道推送。`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await deleteWebhook(wh.id)
        message.success('已删除')
        loadWebhooks()
      } catch {
        message.error('删除失败')
      }
    },
  })
}

// ==================== 启用/禁用开关 ====================

const handleToggleActive = async (wh: Webhook, active: boolean) => {
  try {
    await updateWebhook(wh.id, { is_active: active })
    wh.is_active = active
    if (active) wh.consecutive_failures = 0
    message.success(active ? '渠道已启用' : '渠道已禁用')
  } catch {
    message.error('操作失败')
  }
}

// ==================== 格式化 ====================

const formatTime = (dateStr?: string) => {
  if (!dateStr) return '-'
  return new Date(dateStr).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

onMounted(() => {
  loadWebhooks()
})
</script>

<template>
  <div class="channel-binding-container" :class="{ 'channel-binding-mobile': isMobile }">
    <!-- 页面标题 -->
    <div class="page-header">
      <n-button quaternary circle @click="router.back()">
        <template #icon>
          <n-icon :component="ArrowBackOutline" />
        </template>
      </n-button>
      <h2 class="page-title">通知渠道管理</h2>
    </div>

    <p class="page-desc">添加通知渠道后，订阅内容将自动推送到对应渠道</p>

    <div class="add-btn-row">
      <n-button type="primary" @click="openCreateModal">
        <template #icon>
          <n-icon :component="AddOutline" />
        </template>
        添加通知渠道
      </n-button>
    </div>

    <n-spin :show="loading">
      <!-- 空状态 -->
      <n-empty
        v-if="!loading && webhooks.length === 0"
        description="您还没有添加通知渠道，点击上方按钮添加"
        style="margin-top: 60px"
      />

      <!-- 列表 -->
      <div class="channel-list" v-if="webhooks.length > 0">
        <n-card
          v-for="wh in webhooks"
          :key="wh.id"
          :bordered="true"
          class="channel-card"
        >
          <div class="channel-header">
            <div
              class="channel-icon"
              :style="{
                backgroundColor: (getChannelConfig(wh.channel_type)?.color || '#999') + '15',
              }"
            >
              <span
                class="channel-icon-text"
                :style="{ color: getChannelConfig(wh.channel_type)?.color || '#999' }"
              >
                {{ getChannelConfig(wh.channel_type)?.icon || '?' }}
              </span>
            </div>
            <div class="channel-info">
              <div class="channel-name">
                {{ getChannelLabel(wh.channel_type) }} · {{ wh.name }}
              </div>
              <div class="channel-url">{{ wh.webhook_url_masked }}</div>
            </div>
          </div>

          <!-- 损坏态：加密密钥变更导致无法解密 -->
          <template v-if="isCorrupted(wh)">
            <n-alert type="warning" :bordered="false" style="margin-bottom: 8px">
              此渠道配置已损坏（服务端密钥变更），无法使用。请删除后重新添加。
            </n-alert>
            <div class="bind-actions">
              <n-button size="small" type="error" @click="handleDelete(wh)">
                <template #icon>
                  <n-icon :component="TrashOutline" />
                </template>
                删除并重新添加
              </n-button>
            </div>
          </template>

          <!-- 已启用状态 -->
          <template v-else-if="wh.is_active">
            <div class="bind-status bound">
              <n-switch :value="true" @update:value="() => handleToggleActive(wh, false)" size="small" />
              <n-tag type="success" size="small">
                <template #icon>
                  <n-icon :component="CheckmarkCircleOutline" />
                </template>
                已启用
              </n-tag>
              <span v-if="wh.last_push_at" class="push-info">
                <n-icon :component="TimeOutline" :size="13" />
                {{ formatTime(wh.last_push_at) }}
                <n-tag
                  :type="wh.last_push_status === 'success' ? 'success' : 'error'"
                  size="tiny"
                  :bordered="false"
                >
                  {{ wh.last_push_status === 'success' ? '成功' : '失败' }}
                </n-tag>
              </span>
            </div>
            <div class="bind-actions">
              <n-button size="small" quaternary @click="handleTest(wh)">
                <template #icon>
                  <n-icon :component="PaperPlaneOutline" />
                </template>
                测试
              </n-button>
              <n-button size="small" quaternary @click="openEditModal(wh)">
                <template #icon>
                  <n-icon :component="CreateOutline" />
                </template>
                编辑
              </n-button>
              <n-button size="small" quaternary type="error" @click="handleDelete(wh)">
                <template #icon>
                  <n-icon :component="TrashOutline" />
                </template>
                删除
              </n-button>
            </div>
          </template>

          <!-- 已停用状态 -->
          <template v-else>
            <div class="bind-status disabled">
              <n-switch :value="false" @update:value="() => handleToggleActive(wh, true)" size="small" />
              <!-- 自动停用：consecutive_failures >= 3 -->
              <template v-if="wh.consecutive_failures >= 3">
                <n-tag type="warning" size="small">
                  <template #icon>
                    <n-icon :component="WarningOutline" />
                  </template>
                  已自动停用
                </n-tag>
                <span class="disable-reason">连续推送失败 {{ wh.consecutive_failures }} 次</span>
              </template>
              <!-- 手动禁用 -->
              <template v-else>
                <n-tag type="default" size="small">已禁用</n-tag>
              </template>
            </div>
            <div class="bind-actions">
              <n-button size="small" quaternary @click="openEditModal(wh)">
                <template #icon>
                  <n-icon :component="CreateOutline" />
                </template>
                编辑
              </n-button>
              <n-button size="small" quaternary type="error" @click="handleDelete(wh)">
                <template #icon>
                  <n-icon :component="TrashOutline" />
                </template>
                删除
              </n-button>
            </div>
          </template>
        </n-card>
      </div>
    </n-spin>

    <!-- 添加/编辑弹窗 -->
    <n-modal
      :show="showFormModal"
      @update:show="(v: boolean) => { showFormModal = v }"
      transform-origin="center"
    >
      <n-card
        :title="formMode === 'create' ? '添加通知渠道' : '编辑通知渠道'"
        :bordered="false"
        :style="{ width: isMobile ? '95vw' : '520px', maxWidth: '95vw' }"
        closable
        @close="showFormModal = false"
      >
        <div class="form-content">
          <!-- 渠道类型（新建时可选，编辑时禁用） -->
          <div class="form-field">
            <label class="field-label">渠道类型</label>
            <n-radio-group
              v-model:value="formChannelType"
              :disabled="formMode === 'edit'"
            >
              <n-space :wrap="true">
                <n-radio
                  v-for="ct in channelTypes"
                  :key="ct.key"
                  :value="ct.key"
                >
                  {{ ct.fullLabel }}
                </n-radio>
              </n-space>
            </n-radio-group>
          </div>

          <!-- Telegram 大陆访问提示 -->
          <n-alert
            v-if="formChannelType === 'telegram_bot'"
            type="warning"
            :bordered="false"
            style="margin-bottom: 4px"
          >
            使用 Telegram 需要能够访问 api.telegram.org
          </n-alert>

          <!-- 名称 -->
          <div class="form-field">
            <label class="field-label">渠道名称</label>
            <n-input
              v-model:value="formName"
              :placeholder="formChannelType === 'email' ? '如：工作邮箱通知' : formChannelType === 'telegram_bot' ? '如：我的 Telegram' : '如：项目通知群'"
              :maxlength="100"
            />
          </div>

          <!-- ===== Webhook 类型字段 ===== -->
          <template v-if="isWebhookType">
            <!-- Webhook 地址 -->
            <div class="form-field">
              <label class="field-label">
                Webhook 地址
                <span v-if="formMode === 'edit'" class="field-hint">（留空表示不修改）</span>
              </label>
              <n-input
                v-model:value="formUrl"
                :placeholder="formMode === 'edit' ? '留空不修改，填写则更新' : 'https://...'"
                type="textarea"
                :rows="2"
              />
            </div>

            <!-- 签名密钥（企微不需要） -->
            <div v-if="formChannelType !== 'wework_webhook'" class="form-field">
              <label class="field-label">
                签名密钥（可选）
                <span v-if="formMode === 'edit'" class="field-hint">（留空表示不修改）</span>
              </label>
              <n-input
                v-model:value="formSecret"
                placeholder="如果设置了签名校验，请输入密钥"
                type="password"
                show-password-on="click"
              />
            </div>
            <p v-if="formChannelType === 'wework_webhook'" class="field-tip">此渠道无需单独填写签名密钥</p>
          </template>

          <!-- ===== Telegram 字段 ===== -->
          <template v-if="formChannelType === 'telegram_bot'">
            <div class="form-field">
              <label class="field-label">
                Bot Token
                <span v-if="formMode === 'edit'" class="field-hint">（留空表示不修改）</span>
              </label>
              <n-input
                v-model:value="formSecret"
                placeholder="从 @BotFather 获取，格式：123456:ABC-DEF..."
                type="password"
                show-password-on="click"
              />
            </div>
            <div class="form-field">
              <label class="field-label">Chat ID</label>
              <n-input
                v-model:value="formTelegramChatId"
                placeholder="个人/群组 Chat ID（数字）"
              />
              <span class="field-tip">不知道 Chat ID？先给 Bot 发消息，然后访问 https://api.telegram.org/bot{TOKEN}/getUpdates 查看</span>
            </div>
          </template>

          <!-- ===== Email 字段 ===== -->
          <template v-if="formChannelType === 'email'">
            <!-- 服务商预设 -->
            <div class="form-field">
              <label class="field-label">邮箱服务商</label>
              <n-select
                v-model:value="formEmailPreset"
                :options="emailPresetOptions"
                placeholder="选择邮箱服务商"
                @update:value="handleEmailPresetChange"
              />
            </div>

            <!-- 发件邮箱 -->
            <div class="form-field">
              <label class="field-label">发件邮箱</label>
              <n-input
                v-model:value="formSmtpUser"
                placeholder="your-email@example.com"
              />
            </div>

            <!-- 密码/授权码 -->
            <div class="form-field">
              <label class="field-label">
                {{ emailSecretLabel }}
                <span v-if="formMode === 'edit'" class="field-hint">（留空表示不修改）</span>
              </label>
              <n-input
                v-model:value="formSecret"
                placeholder="请输入密码或授权码"
                type="password"
                show-password-on="click"
              />
              <span v-if="emailHint" class="field-tip">{{ emailHint }}</span>
            </div>

            <!-- 收件邮箱 -->
            <div class="form-field">
              <label class="field-label">收件邮箱</label>
              <n-input
                v-model:value="formToAddr"
                placeholder="recipient@example.com"
              />
            </div>

            <!-- 高级设置 -->
            <n-collapse>
              <n-collapse-item title="高级设置（通常无需修改）" name="email-advanced">
                <div class="form-field">
                  <label class="field-label">SMTP 服务器</label>
                  <n-input v-model:value="formSmtpHost" placeholder="smtp.example.com" />
                </div>
                <div class="form-field">
                  <label class="field-label">端口</label>
                  <n-input
                    :value="String(formSmtpPort)"
                    @update:value="(v: string) => formSmtpPort = Number(v) || 587"
                    placeholder="465 或 587"
                  />
                  <span class="field-tip">465 = SSL，587 = STARTTLS</span>
                </div>
              </n-collapse-item>
            </n-collapse>
          </template>

          <!-- 操作指引（Webhook 和 Telegram） -->
          <n-collapse v-if="currentGuide.length > 0">
            <n-collapse-item title="如何获取？" name="guide">
              <div class="guide-steps">
                <div
                  v-for="(step, idx) in currentGuide"
                  :key="idx"
                  class="guide-step"
                >
                  <span class="step-num">{{ idx + 1 }}</span>
                  <span>{{ step }}</span>
                </div>
              </div>
            </n-collapse-item>
          </n-collapse>

          <!-- 操作按钮 -->
          <div class="form-actions">
            <n-button @click="showFormModal = false">取消</n-button>
            <n-button
              type="primary"
              :loading="formLoading"
              @click="handleTestAndSave"
            >
              {{ formChannelType === 'email'
                ? (formMode === 'create' ? '发送测试邮件并保存' : '保存')
                : (formMode === 'create' ? '测试并保存' : '保存')
              }}
            </n-button>
          </div>
        </div>
      </n-card>
    </n-modal>
  </div>
</template>

<style scoped>
.channel-binding-container {
  padding: 24px;
  max-width: 800px;
  margin: 0 auto;
  height: calc(100vh - 60px);
  overflow-y: auto;
}

.channel-binding-mobile {
  padding: 16px;
  height: auto;
  padding-bottom: calc(56px + env(safe-area-inset-bottom, 0px) + 16px);
}

.page-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.page-title {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
}

.page-desc {
  color: #666;
  font-size: 14px;
  margin: 0 0 16px 0;
}

.add-btn-row {
  margin-bottom: 20px;
}

.channel-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.channel-card {
  border-radius: 12px;
}

.channel-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.channel-icon {
  width: 44px;
  height: 44px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.channel-icon-text {
  font-size: 18px;
  font-weight: 700;
}

.channel-info {
  flex: 1;
  min-width: 0;
}

.channel-name {
  font-size: 15px;
  font-weight: 600;
  color: #333;
}

.channel-url {
  font-size: 12px;
  color: #999;
  margin-top: 2px;
  word-break: break-all;
}

.bind-status {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
  flex-wrap: wrap;
}

.push-info {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: #999;
}

.disable-reason {
  font-size: 13px;
  color: #e8a400;
}

.bind-actions {
  display: flex;
  gap: 4px;
}

/* 弹窗表单 */
.form-content {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field-label {
  font-size: 13px;
  font-weight: 500;
  color: #333;
}

.field-hint {
  font-weight: 400;
  color: #999;
  font-size: 12px;
}

.field-tip {
  font-size: 12px;
  color: #999;
  line-height: 1.4;
  margin-top: 2px;
}

.guide-steps {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.guide-step {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  font-size: 13px;
  color: #555;
  line-height: 1.5;
}

.step-num {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: #2080f0;
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.form-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  padding-top: 8px;
}
</style>
