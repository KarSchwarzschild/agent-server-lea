<script setup lang="ts">
/**
 * TTS 端点管理 Tab 组件
 * 位于 Settings.vue 的「语音合成」tab 中
 *
 * 功能：
 * - TTS 端点列表（状态、探测、编辑）
 * - 默认 Provider 选择
 * - 降级链配置
 * - 声音预设管理
 * - 试听功能
 */
import { ref, computed, onMounted } from 'vue'
import {
  NCard, NButton, NSpace, NTag, NModal, NForm, NFormItem, NInput,
  NSelect, NSwitch, NCollapse, NCollapseItem, NAlert, NSpin,
  NDescriptions, NDescriptionsItem, NTooltip, NPopconfirm,
  useMessage,
} from 'naive-ui'
import {
  getTTSEndpoints, saveTTSEndpoint, deleteTTSEndpoint,
  probeTTSEndpoint, setTTSDefaultProvider, setTTSFallbackChain,
  getTTSVoicePresets,
  type TTSEndpointsResponse, type TTSEndpointInfo, type TTSVoicePreset,
} from '../../api/settings'

const message = useMessage()

// ==================== 状态 ====================
const loading = ref(false)
const data = ref<TTSEndpointsResponse | null>(null)
const presets = ref<TTSVoicePreset[]>([])

// 探测状态
const probingKey = ref('')

// 编辑对话框
const showEditModal = ref(false)
const editForm = ref({
  key: '',
  name: '',
  provider_type: 'jizuo_tts',
  base_url: '',
  base_url_env: '',
  api_key: '',
  api_key_env: '',
  enabled: true,
  default_voice: '',
  featured_voices: [] as Array<{ id: string; name: string; gender: string }>,
  max_text_length: 4096,
})
const editSaving = ref(false)
const isNewEndpoint = ref(false)

// 试听
const previewAudio = ref<HTMLAudioElement | null>(null)
const previewingVoice = ref('')

// ==================== 计算属性 ====================
const endpoints = computed(() => data.value?.endpoints || [])
const defaultProvider = computed(() => data.value?.default_provider || '')
const fallbackChain = computed(() => data.value?.fallback_chain || [])

// 降级链编辑
const editFallbackChain = ref<string[]>([])
const availableFallbackOptions = computed(() => {
  const allKeys = endpoints.value.filter(e => e.enabled).map(e => e.key)
  return allKeys
    .filter(k => !editFallbackChain.value.includes(k))
    .map(k => ({ label: k, value: k }))
})

async function saveFallbackChain() {
  try {
    await setTTSFallbackChain(editFallbackChain.value)
    message.success('降级链已更新')
    await loadData()
  } catch (e: any) {
    message.error(e.message || e)
  }
}

const providerOptions = [
  { label: '智能基座 TTS (Volcengine)', value: 'jizuo_tts' },
  { label: 'OpenAI TTS', value: 'openai_tts' },
  { label: 'Edge TTS (免费)', value: 'edge_tts' },
]

// ==================== 加载 ====================
async function loadData() {
  loading.value = true
  try {
    const [epRes, presetRes] = await Promise.all([
      getTTSEndpoints(),
      getTTSVoicePresets(),
    ])
    data.value = epRes
    presets.value = presetRes.presets || []
    editFallbackChain.value = [...(epRes.fallback_chain || [])]
  } catch (e: any) {
    message.error('加载 TTS 配置失败: ' + (e.message || e))
  } finally {
    loading.value = false
  }
}

onMounted(loadData)

// ==================== 探测 ====================
async function probeEndpoint(key: string) {
  probingKey.value = key
  try {
    const res = await probeTTSEndpoint(key)
    if (res.available) {
      message.success(`${key} 探测成功`)
    } else {
      message.warning(`${key} 不可用: ${res.error || '未知错误'}`)
    }
  } catch (e: any) {
    message.error('探测失败: ' + (e.message || e))
  } finally {
    probingKey.value = ''
  }
}

// ==================== 编辑 ====================
function openAdd() {
  isNewEndpoint.value = true
  editForm.value = {
    key: '',
    name: '',
    provider_type: 'jizuo_tts',
    base_url: '',
    base_url_env: '',
    api_key: '',
    api_key_env: '',
    enabled: true,
    default_voice: '',
    featured_voices: [],
    max_text_length: 4096,
  }
  showEditModal.value = true
}

function openEdit(ep: TTSEndpointInfo) {
  isNewEndpoint.value = false
  editForm.value = {
    key: ep.key,
    name: ep.name,
    provider_type: ep.provider_type,
    base_url: ep.base_url || '',
    base_url_env: ep.base_url_env || '',
    api_key: '', // 不回显
    api_key_env: ep.api_key_env || '',
    enabled: ep.enabled,
    default_voice: ep.default_voice,
    featured_voices: ep.featured_voices.map(v => ({ id: v.id, name: v.name, gender: v.gender })),
    max_text_length: 4096,
  }
  showEditModal.value = true
}

async function saveEndpoint() {
  const f = editForm.value
  if (!f.key || !f.name) {
    message.warning('请填写端点标识和名称')
    return
  }
  editSaving.value = true
  try {
    await saveTTSEndpoint({
      key: f.key,
      name: f.name,
      provider_type: f.provider_type,
      base_url: f.base_url,
      base_url_env: f.base_url_env,
      api_key: f.api_key || undefined,
      api_key_env: f.api_key_env,
      enabled: f.enabled,
      default_voice: f.default_voice,
      featured_voices: f.featured_voices,
      max_text_length: f.max_text_length,
    })
    message.success('已保存')
    showEditModal.value = false
    await loadData()
  } catch (e: any) {
    message.error('保存失败: ' + (e.message || e))
  } finally {
    editSaving.value = false
  }
}

async function removeEndpoint(key: string) {
  try {
    await deleteTTSEndpoint(key)
    message.success('已删除')
    await loadData()
  } catch (e: any) {
    message.error('删除失败: ' + (e.message || e))
  }
}

// ==================== 默认 Provider ====================
async function changeDefaultProvider(key: string) {
  try {
    await setTTSDefaultProvider(key)
    message.success('默认 Provider 已更新')
    await loadData()
  } catch (e: any) {
    message.error(e.message || e)
  }
}

// ==================== 试听 ====================
function previewVoice(voiceId: string, providerKey: string) {
  if (previewAudio.value) {
    previewAudio.value.pause()
    previewAudio.value = null
  }
  previewingVoice.value = voiceId
  const url = `/api/user/tts/generate`
  const token = localStorage.getItem('token')
  fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({
      text: '你好，这是语音合成试听效果。',
      provider: providerKey,
      voice: voiceId,
    }),
  })
    .then(res => {
      if (!res.ok) throw new Error('试听请求失败')
      return res.blob()
    })
    .then(blob => {
      const audio = new Audio(URL.createObjectURL(blob))
      previewAudio.value = audio
      audio.onended = () => { previewingVoice.value = '' }
      audio.play()
    })
    .catch(e => {
      message.error('试听失败: ' + e.message)
      previewingVoice.value = ''
    })
}

function statusTag(ep: TTSEndpointInfo) {
  if (!ep.enabled) return { type: 'default' as const, text: '已禁用' }
  return { type: 'success' as const, text: '已启用' }
}
</script>

<template>
  <n-spin :show="loading">
    <!-- TTS 端点列表 -->
    <n-card title="TTS 端点" size="small" style="margin-bottom: 16px">
      <template #header-extra>
        <n-button type="primary" size="small" @click="openAdd">+ 添加 TTS 端点</n-button>
      </template>

      <div v-if="endpoints.length === 0" style="color: #999; text-align: center; padding: 24px">
        暂无 TTS 端点配置
      </div>

      <n-collapse>
        <n-collapse-item
          v-for="ep in endpoints"
          :key="ep.key"
          :title="ep.name"
          :name="ep.key"
        >
          <template #header-extra>
            <n-space :size="8" align="center" @click.stop>
              <n-tag :type="statusTag(ep).type" size="small">{{ statusTag(ep).text }}</n-tag>
              <n-tag v-if="ep.key === defaultProvider" type="info" size="small">默认</n-tag>
              <n-tag v-if="ep.source === 'yaml'" size="small">内置</n-tag>
              <n-button
                size="tiny"
                :loading="probingKey === ep.key"
                @click="probeEndpoint(ep.key)"
              >探测</n-button>
              <n-button size="tiny" @click="openEdit(ep)">编辑</n-button>
              <n-popconfirm
                v-if="ep.source !== 'yaml'"
                @positive-click="removeEndpoint(ep.key)"
              >
                <template #trigger>
                  <n-button size="tiny" type="error">删除</n-button>
                </template>
                确认删除端点「{{ ep.name }}」？
              </n-popconfirm>
            </n-space>
          </template>

          <n-descriptions :column="2" label-placement="left" size="small" bordered>
            <n-descriptions-item label="Provider">{{ ep.provider_type }}</n-descriptions-item>
            <n-descriptions-item label="端点标识">{{ ep.key }}</n-descriptions-item>
            <n-descriptions-item label="Base URL">{{ ep.base_url || `env: ${ep.base_url_env || '-'}` }}</n-descriptions-item>
            <n-descriptions-item label="API Key">{{ ep.api_key_env ? `env: ${ep.api_key_env}` : (ep.api_key ? '****' : '-') }}</n-descriptions-item>
            <n-descriptions-item label="默认声音">{{ ep.default_voice }}</n-descriptions-item>
            <n-descriptions-item label="语速范围">{{ ep.speed_range?.min }} ~ {{ ep.speed_range?.max }}</n-descriptions-item>
          </n-descriptions>

          <!-- 声音列表 -->
          <div v-if="ep.featured_voices.length" style="margin-top: 12px">
            <div style="font-weight: 500; margin-bottom: 8px">可用声音</div>
            <n-space :size="8">
              <n-tag
                v-for="v in ep.featured_voices"
                :key="v.id"
                :type="v.gender === 'female' ? 'warning' : v.gender === 'male' ? 'info' : 'default'"
                size="small"
                round
                style="cursor: pointer"
                @click="previewVoice(v.id, ep.key)"
              >
                {{ v.name }} ({{ v.id }})
                <template v-if="previewingVoice === v.id"> 🔊</template>
              </n-tag>
            </n-space>
          </div>

          <!-- 设为默认 -->
          <div v-if="ep.key !== defaultProvider && ep.enabled" style="margin-top: 12px">
            <n-button size="small" type="primary" ghost @click="changeDefaultProvider(ep.key)">
              设为默认 Provider
            </n-button>
          </div>
        </n-collapse-item>
      </n-collapse>
    </n-card>

    <!-- 默认配置 -->
    <n-card title="默认配置" size="small" style="margin-bottom: 16px">
      <n-descriptions :column="1" label-placement="left" size="small">
        <n-descriptions-item label="默认 Provider">
          <n-select
            :value="defaultProvider"
            :options="endpoints.filter(e => e.enabled).map(e => ({ label: e.name, value: e.key }))"
            size="small"
            style="width: 200px"
            @update:value="changeDefaultProvider"
          />
        </n-descriptions-item>
        <n-descriptions-item label="降级链">
          <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap">
            <n-tag
              v-for="(key, idx) in editFallbackChain"
              :key="key"
              size="small"
              closable
              @close="editFallbackChain.splice(idx, 1)"
            >
              {{ idx + 1 }}. {{ key }}
            </n-tag>
            <n-select
              v-if="availableFallbackOptions.length > 0"
              placeholder="+ 添加"
              size="tiny"
              style="width: 120px"
              :options="availableFallbackOptions"
              @update:value="(v: string) => { editFallbackChain.push(v) }"
            />
            <n-button
              size="tiny"
              type="primary"
              :disabled="JSON.stringify(editFallbackChain) === JSON.stringify(fallbackChain)"
              @click="saveFallbackChain"
            >
              保存
            </n-button>
          </div>
        </n-descriptions-item>
      </n-descriptions>
    </n-card>

    <!-- 声音预设 -->
    <n-card title="角色声音预设" size="small">
      <template #header-extra>
        <n-tooltip>
          <template #trigger>
            <span style="color: #999; font-size: 12px">按角色定义，自动适配当前 Provider</span>
          </template>
          每个预设包含所有 Provider 的声音映射，降级时自动切换对应声音
        </n-tooltip>
      </template>

      <div v-if="presets.length === 0" style="color: #999; text-align: center; padding: 16px">
        暂无声音预设
      </div>

      <n-collapse v-else>
        <n-collapse-item
          v-for="preset in presets"
          :key="preset.id"
          :title="`${preset.label} (${preset.id})`"
          :name="preset.id"
        >
          <template #header-extra>
            <n-space :size="4">
              <n-tag v-for="use in preset.usage" :key="use" size="small" type="info">{{ use }}</n-tag>
            </n-space>
          </template>

          <n-descriptions :column="1" label-placement="left" size="small" bordered>
            <n-descriptions-item
              v-for="(voiceId, providerType) in preset.voices"
              :key="providerType"
              :label="String(providerType)"
            >
              {{ voiceId }}
              <n-button
                text
                size="tiny"
                style="margin-left: 8px"
                @click="previewVoice(voiceId, String(providerType))"
              >
                {{ previewingVoice === voiceId ? '🔊' : '试听' }}
              </n-button>
            </n-descriptions-item>
          </n-descriptions>
        </n-collapse-item>
      </n-collapse>
    </n-card>

    <!-- 编辑/新增端点对话框 -->
    <n-modal
      v-model:show="showEditModal"
      :title="isNewEndpoint ? '添加 TTS 端点' : '编辑 TTS 端点'"
      preset="card"
      style="width: 560px"
    >
      <n-form label-placement="left" label-width="110">
        <n-form-item label="端点标识" required>
          <n-input
            v-model:value="editForm.key"
            :disabled="!isNewEndpoint"
            placeholder="如 my_tts"
          />
        </n-form-item>
        <n-form-item label="名称" required>
          <n-input v-model:value="editForm.name" placeholder="显示名称" />
        </n-form-item>
        <n-form-item label="Provider 类型">
          <n-select
            v-model:value="editForm.provider_type"
            :options="providerOptions"
          />
        </n-form-item>
        <n-form-item label="Base URL">
          <n-input v-model:value="editForm.base_url" placeholder="https://api.example.com/v1" />
        </n-form-item>
        <n-form-item label="Base URL 环境变量">
          <n-input v-model:value="editForm.base_url_env" placeholder="如 TENANT_API_BASE" />
        </n-form-item>
        <n-form-item label="API Key">
          <n-input
            v-model:value="editForm.api_key"
            type="password"
            show-password-on="click"
            placeholder="留空则不更新"
          />
        </n-form-item>
        <n-form-item label="API Key 环境变量">
          <n-input v-model:value="editForm.api_key_env" placeholder="如 TENANT_API_KEY" />
        </n-form-item>
        <n-form-item label="默认声音">
          <n-input v-model:value="editForm.default_voice" placeholder="如 BV001_streaming" />
        </n-form-item>
        <n-form-item label="可用声音">
          <div style="width: 100%">
            <div
              v-for="(voice, vi) in editForm.featured_voices"
              :key="vi"
              style="display: flex; gap: 6px; margin-bottom: 6px; align-items: center"
            >
              <n-input v-model:value="voice.id" placeholder="声音ID" size="small" style="flex: 2" />
              <n-input v-model:value="voice.name" placeholder="显示名" size="small" style="flex: 2" />
              <n-select
                v-model:value="voice.gender"
                :options="[{label:'女',value:'female'},{label:'男',value:'male'},{label:'中性',value:'neutral'}]"
                size="small"
                style="width: 80px"
              />
              <n-button size="tiny" type="error" quaternary @click="editForm.featured_voices.splice(vi, 1)">
                删除
              </n-button>
            </div>
            <n-button
              size="small"
              dashed
              style="width: 100%"
              @click="editForm.featured_voices.push({ id: '', name: '', gender: 'neutral' })"
            >
              + 添加声音
            </n-button>
          </div>
        </n-form-item>
        <n-form-item label="启用">
          <n-switch v-model:value="editForm.enabled" />
        </n-form-item>
      </n-form>

      <template #footer>
        <n-space justify="end">
          <n-button @click="showEditModal = false">取消</n-button>
          <n-button type="primary" :loading="editSaving" @click="saveEndpoint">保存</n-button>
        </n-space>
      </template>
    </n-modal>
  </n-spin>
</template>

<style scoped>
:deep(.n-collapse-item__header-main) {
  font-weight: 500;
}
</style>
