<script setup lang="ts">
/**
 * 消息频道管理页面
 *
 * 功能：频道状态监控 + 启用/禁用开关 + 凭据配置（Drawer + 操作指引）
 * v1.4: OpenFang 架构迁移
 */
import { h, ref, onMounted, onUnmounted } from 'vue'
import {
  NCard,
  NSpace,
  NTag,
  NDataTable,
  NButton,
  NSwitch,
  NAlert,
  NIcon,
  NDrawer,
  NDrawerContent,
  NForm,
  NFormItem,
  NInput,
  NSpin,
  NText,
  NDivider,
  useMessage,
  type DataTableColumns,
} from 'naive-ui'
import { RefreshOutline, SettingsOutline } from '@vicons/ionicons5'
import request from '../utils/request'

interface ChannelInfo {
  name: string
  displayName: string
  status: 'online' | 'offline' | 'not_configured'
  connectionType: string
  bindCount: number
  enabled: boolean
}

interface ConfigField {
  key: string
  label: string
  secret: boolean
  help: string
  value: string
  configured: boolean
}

const message = useMessage()
const loading = ref(false)
const toggling = ref<string | null>(null)
let refreshTimer: ReturnType<typeof setInterval> | null = null

// Drawer 状态
const drawerVisible = ref(false)
const drawerChannel = ref('')
const drawerTitle = ref('')
const configLoading = ref(false)
const configSaving = ref(false)
const configFields = ref<ConfigField[]>([])
const configForm = ref<Record<string, string>>({})
const configGuide = ref('')

const channels = ref<ChannelInfo[]>([
  { name: 'web', displayName: 'Web', status: 'online', connectionType: 'SSE', bindCount: -1, enabled: true },
  { name: 'feishu', displayName: '飞书', status: 'not_configured', connectionType: 'WebSocket', bindCount: 0, enabled: false },
  { name: 'dingtalk', displayName: '钉钉', status: 'not_configured', connectionType: 'Stream', bindCount: 0, enabled: false },
  { name: 'wecom', displayName: '企业微信', status: 'not_configured', connectionType: 'HTTP 回调', bindCount: 0, enabled: false },
  { name: 'email', displayName: 'Email', status: 'not_configured', connectionType: 'IMAP + SMTP', bindCount: 0, enabled: false },
  { name: 'telegram', displayName: 'Telegram', status: 'not_configured', connectionType: 'Long Polling', bindCount: 0, enabled: false },
])

const columns: DataTableColumns<ChannelInfo> = [
  {
    title: '频道',
    key: 'displayName',
    width: 120,
  },
  {
    title: '状态',
    key: 'status',
    width: 120,
    render(row) {
      const statusMap: Record<string, { type: 'success' | 'default' | 'warning'; label: string }> = {
        online: { type: 'success', label: '在线' },
        offline: { type: 'warning', label: '离线' },
        not_configured: { type: 'default', label: '未配置' },
      }
      const info = statusMap[row.status] || { type: 'default', label: '未知' }
      return h(NTag, { type: info.type, size: 'small', round: true }, { default: () => info.label })
    },
  },
  {
    title: '连接方式',
    key: 'connectionType',
    width: 140,
  },
  {
    title: '绑定用户',
    key: 'bindCount',
    width: 100,
    render(row) {
      return row.bindCount < 0 ? '-' : String(row.bindCount)
    },
  },
  {
    title: '启用',
    key: 'enabled',
    width: 80,
    render(row) {
      if (row.name === 'web') {
        return h(NTag, { size: 'small', type: 'info' }, { default: () => '常驻' })
      }
      return h(NSwitch, {
        value: row.enabled,
        loading: toggling.value === row.name,
        size: 'small',
        onUpdateValue: (val: boolean) => toggleChannel(row.name, val),
      })
    },
  },
  {
    title: '操作',
    key: 'actions',
    width: 100,
    render(row) {
      if (row.name === 'web') return ''
      return h(
        NButton,
        {
          size: 'small',
          text: true,
          type: 'primary',
          onClick: () => openConfigDrawer(row.name, row.displayName),
        },
        {
          icon: () => h(NIcon, null, { default: () => h(SettingsOutline) }),
          default: () => '配置',
        },
      )
    },
  },
]

async function fetchChannelStatus() {
  loading.value = true
  try {
    const data: any = await request.get('/api/admin/config/channels')
    if (data && Array.isArray(data)) {
      for (const ch of data) {
        const local = channels.value.find(c => c.name === ch.name)
        if (local) {
          local.status = ch.status || 'not_configured'
          local.bindCount = ch.bind_count ?? local.bindCount
          if (ch.enabled !== undefined) {
            local.enabled = ch.enabled
          } else {
            local.enabled = ch.status !== 'not_configured'
          }
        }
      }
    }
  } catch {
    // API 可能还未实现，使用默认值
  } finally {
    loading.value = false
  }
}

async function toggleChannel(channelName: string, enabled: boolean) {
  toggling.value = channelName
  try {
    await request.put(`/api/admin/config/channels/${channelName}/toggle`, { enabled })
    const ch = channels.value.find(c => c.name === channelName)
    if (ch) {
      ch.enabled = enabled
      if (!enabled) ch.status = 'offline'
    }
    message.success(`${enabled ? '启用' : '禁用'}成功，重启后端后生效`)
  } catch (e: any) {
    message.error(e?.response?.data?.detail || '操作失败')
  } finally {
    toggling.value = null
  }
}

// ── Drawer 凭据配置 ──

async function openConfigDrawer(channelName: string, displayName: string) {
  drawerChannel.value = channelName
  drawerTitle.value = `${displayName} — 凭据配置`
  drawerVisible.value = true
  configLoading.value = true
  configFields.value = []
  configForm.value = {}
  configGuide.value = ''

  try {
    const data: any = await request.get(`/api/admin/config/channels/${channelName}/config`)
    if (data && data.fields) {
      configFields.value = data.fields
      configGuide.value = data.guide || ''
      for (const field of data.fields) {
        configForm.value[field.key] = field.value || ''
      }
    }
  } catch (e: any) {
    message.error('加载配置失败: ' + (e?.response?.data?.detail || e?.message || '未知错误'))
  } finally {
    configLoading.value = false
  }
}

async function saveConfig() {
  configSaving.value = true
  try {
    await request.put(
      `/api/admin/config/channels/${drawerChannel.value}/config`,
      configForm.value,
    )
    message.success('配置已保存，重启后端后生效')
    drawerVisible.value = false
    await fetchChannelStatus()
  } catch (e: any) {
    message.error('保存失败: ' + (e?.response?.data?.detail || e?.message || '未知错误'))
  } finally {
    configSaving.value = false
  }
}

onMounted(() => {
  fetchChannelStatus()
  refreshTimer = setInterval(fetchChannelStatus, 30000)
})

onUnmounted(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})
</script>

<template>
  <NSpace vertical :size="16">
    <NCard title="消息频道管理">
      <template #header-extra>
        <NButton
          :loading="loading"
          size="small"
          @click="fetchChannelStatus"
        >
          <template #icon>
            <NIcon><RefreshOutline /></NIcon>
          </template>
          刷新
        </NButton>
      </template>

      <NAlert type="info" :bordered="false" style="margin-bottom: 16px">
        <template #header>频道配置说明</template>
        <p style="margin: 0 0 4px">消息频道使用户能通过飞书、钉钉、企微等平台直接与 AI 助手对话。</p>
        <p style="margin: 0 0 4px">
          <b>配置流程</b>：点击「配置」填写凭据 → 开启「启用」开关 → <b>重启后端服务</b>生效。
        </p>
        <p style="margin: 0; color: var(--n-text-color3)">
          频道启用后，用户可通过对应平台直接与 AI 助手对话。
        </p>
      </NAlert>

      <NDataTable
        :columns="columns"
        :data="channels"
        :loading="loading"
        :bordered="false"
        :single-line="false"
      />
    </NCard>
  </NSpace>

  <!-- 频道凭据配置 Drawer -->
  <NDrawer v-model:show="drawerVisible" :width="500" placement="right">
    <NDrawerContent :title="drawerTitle" closable>
      <NSpin :show="configLoading">
        <!-- 操作指引 -->
        <NAlert
          v-if="configGuide"
          type="info"
          :bordered="false"
          style="margin-bottom: 20px; white-space: pre-line;"
        >
          <template #header>如何获取凭据</template>
          {{ configGuide }}
        </NAlert>

        <!-- 配置表单 -->
        <NForm
          v-if="configFields.length > 0"
          label-placement="top"
          :show-feedback="false"
        >
          <NFormItem
            v-for="field in configFields"
            :key="field.key"
            :label="field.label"
            style="margin-bottom: 20px;"
          >
            <template #label>
              <span>{{ field.label }}</span>
              <NTag
                v-if="field.configured"
                type="success"
                size="tiny"
                :bordered="false"
                round
                style="margin-left: 8px;"
              >
                已配置
              </NTag>
            </template>
            <div style="width: 100%;">
              <NInput
                v-model:value="configForm[field.key]"
                :type="field.secret ? 'password' : 'text'"
                :show-password-on="field.secret ? 'click' : undefined"
                :placeholder="`请输入${field.label}`"
                clearable
              />
              <NText
                v-if="field.help"
                depth="3"
                style="font-size: 12px; margin-top: 4px; display: block;"
              >
                {{ field.help }}
              </NText>
            </div>
          </NFormItem>
        </NForm>

        <NAlert
          v-if="!configLoading && configFields.length === 0"
          type="warning"
        >
          未找到该频道的配置字段定义
        </NAlert>

        <!-- 底部提示 -->
        <NDivider v-if="configFields.length > 0" style="margin: 16px 0 8px" />
        <NText v-if="configFields.length > 0" depth="3" style="font-size: 12px;">
          密钥字段已脱敏显示，未修改的密钥保存时会保留原值。配置保存后需重启后端服务生效。
        </NText>
      </NSpin>

      <template #footer>
        <NSpace justify="end">
          <NButton @click="drawerVisible = false">取消</NButton>
          <NButton
            type="primary"
            :loading="configSaving"
            :disabled="configLoading || configFields.length === 0"
            @click="saveConfig"
          >
            保存配置
          </NButton>
        </NSpace>
      </template>
    </NDrawerContent>
  </NDrawer>
</template>
