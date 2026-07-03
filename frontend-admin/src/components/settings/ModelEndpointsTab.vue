<script setup lang="ts">
/**
 * 模型端点管理 Tab 组件
 * 位于 Settings.vue 的「模型端点」tab 中
 */
import { ref, computed, onMounted, h } from 'vue'
import {
  NCard, NButton, NSpace, NTag, NModal, NForm, NFormItem, NInput,
  NSelect, NRadioGroup, NRadio, NProgress, NAlert, NSpin, NIcon,
  NPopconfirm, NGrid, NGridItem, NStatistic, NCollapse, NCollapseItem,
  NDynamicInput, NInputNumber, NDynamicTags, NSwitch, NText,
  useMessage, NDescriptions, NDescriptionsItem, NTooltip,
} from 'naive-ui'
import {
  getEndpoints, addEndpoint, deleteEndpoint, addModel, updateModel, deleteModel, probeOneModel,
  saveEndpointProbeResults, getEndpointProbeResults,
  type EndpointsResponse, type EndpointConfig, type EndpointModelInfo, type ProbeResult
} from '../../api/settings'

const emit = defineEmits<{
  (e: 'switch-tab', tab: string): void
}>()

const message = useMessage()

// ==================== 状态 ====================
const loading = ref(false)
const endpointsData = ref<EndpointsResponse | null>(null)
const providerPresets = ref<Record<string, any>>({})

// 探测状态
const probing = ref(false)
const probeProgress = ref(0)
const probeTotalModels = ref(0)
const probeResults = ref<Record<string, ProbeResult>>({})
const probeStartTime = ref(0)
const probeElapsed = ref(0)
const probedAt = ref<string | null>(null)
let probeTimer: ReturnType<typeof setInterval> | null = null

// 新增端点对话框
const showAddEndpoint = ref(false)
const addEndpointForm = ref({
  provider_type: 'openai',
  name: '',
  endpoint_key: '',
  base_url: '',
  api_key_mode: 'env' as 'env' | 'direct',
  api_key_env: '',
  api_key: '',
  wire_api: 'chat',
  default_headers: [] as Array<{ key: string; value: string }>,
})
const addEndpointSaving = ref(false)

// 编辑端点对话框
const showEditEndpoint = ref(false)
const editEndpointKey = ref('')
const editEndpointForm = ref<any>({})

// 添加/编辑模型对话框
const showAddModel = ref(false)
const addModelEndpointKey = ref('')
const editingModelId = ref('')  // 编辑模式时保存原 model_id，空串表示新增
const addModelForm = ref({
  id: '',
  name: '',
  actual_model_id: '',
  provider: '',
  max_output_tokens: 16384,
  capabilities: ['chat'] as string[],
  assign_tier: '' as string,
  probe_after: true,
})
const addModelSaving = ref(false)

// 折叠状态
const expandedEndpoints = ref<string[]>([])

// ==================== 计算属性 ====================
const endpoints = computed(() => endpointsData.value?.endpoints || {})

const stats = computed(() => {
  const eps = endpoints.value
  let totalEndpoints = 0
  let totalModels = 0
  let healthyModels = 0
  let unhealthyModels = 0
  for (const ep of Object.values(eps)) {
    totalEndpoints++
    for (const m of ep.models) {
      totalModels++
      if (m.health) {
        if (m.health.state === 'closed') healthyModels++
        else if (m.health.state === 'open') unhealthyModels++
      }
    }
  }
  return { totalEndpoints, totalModels, healthyModels, unhealthyModels }
})

const allModels = computed(() => {
  const result: Array<{ model: string; endpoint_key: string }> = []
  for (const [epKey, ep] of Object.entries(endpoints.value)) {
    for (const m of ep.models) {
      result.push({ model: m.id, endpoint_key: epKey })
    }
  }
  return result
})

// ==================== 加载数据 ====================
async function loadEndpoints() {
  loading.value = true
  try {
    const data = await getEndpoints()
    endpointsData.value = data
    providerPresets.value = data.provider_presets || {}

    // 自动展开有异常的端点
    const expanded: string[] = []
    for (const [key, ep] of Object.entries(data.endpoints)) {
      const hasIssue = ep.models.some(m =>
        m.health && (m.health.state === 'open' || m.health.state === 'half_open')
      )
      if (hasIssue) expanded.push(key)
    }
    expandedEndpoints.value = expanded

    // 加载缓存的探测结果
    try {
      const cached = await getEndpointProbeResults()
      if (cached.results && Object.keys(cached.results).length > 0) {
        probeResults.value = cached.results
        probedAt.value = cached.probed_at
      }
    } catch {
      // 忽略，缓存不影响主流程
    }
  } catch (e: any) {
    message.error('加载端点数据失败: ' + (e.message || e))
  } finally {
    loading.value = false
  }
}

// ==================== 探测 ====================
async function probeAll() {
  const targets = allModels.value.map(m => m.model)
  if (!targets.length) return

  probing.value = true
  probeProgress.value = 0
  probeTotalModels.value = targets.length
  probeResults.value = {}
  probeStartTime.value = Date.now()
  probeElapsed.value = 0

  // 标记所有为 loading 态
  for (const id of targets) {
    probeResults.value[id] = { model: id, status: 'ok', latency_ms: -1 }
  }

  probeTimer = setInterval(() => {
    probeElapsed.value = Math.floor((Date.now() - probeStartTime.value) / 1000)
  }, 1000)

  // 5 并发逐个探测，完成一个立即更新 UI
  let done = 0
  const queue = [...targets]
  const concurrency = 5

  await new Promise<void>(resolve => {
    let running = 0

    function runNext() {
      while (running < concurrency && queue.length > 0) {
        const modelId = queue.shift()!
        running++

        probeOneModel(modelId).then(result => {
          probeResults.value[modelId] = result
        }).catch(() => {
          probeResults.value[modelId] = { model: modelId, status: 'error', error: '请求失败', latency_ms: 0 }
        }).finally(() => {
          running--
          done++
          probeProgress.value = Math.round((done / targets.length) * 100)
          if (done === targets.length) {
            resolve()
          } else {
            runNext()
          }
        })
      }
    }

    runNext()
  })

  const passed = Object.values(probeResults.value).filter(r => r.status === 'ok').length
  message.success(`探测完成: ${passed}/${targets.length} 通过`)

  probing.value = false
  if (probeTimer) { clearInterval(probeTimer); probeTimer = null }

  // 持久化探测结果
  probedAt.value = new Date().toISOString()
  try {
    const completed = Object.fromEntries(
      Object.entries(probeResults.value).filter(([, r]) => r.latency_ms !== -1)
    )
    await saveEndpointProbeResults(completed)
  } catch {
    // 保存失败不影响主流程
  }
}

async function probeSingle(modelId: string) {
  probeResults.value[modelId] = { model: modelId, status: 'ok', latency_ms: -1 } // loading state
  try {
    const result = await probeOneModel(modelId)
    probeResults.value[modelId] = result
  } catch (e: any) {
    probeResults.value[modelId] = { model: modelId, status: 'error', error: e.message, latency_ms: 0 }
  }
  // 更新持久化缓存
  probedAt.value = new Date().toISOString()
  try {
    const completed = Object.fromEntries(
      Object.entries(probeResults.value).filter(([, r]) => r.latency_ms !== -1)
    )
    await saveEndpointProbeResults(completed)
  } catch { /* ignore */ }
}

// 端点级探测（探测该端点下所有模型）
const probingEndpoint = ref<string>('')
async function probeEndpoint(endpointKey: string, models: EndpointModelInfo[]) {
  if (!models.length) {
    message.info('该端点下没有模型')
    return
  }
  probingEndpoint.value = endpointKey
  // 标记所有为 loading
  for (const m of models) {
    probeResults.value[m.id] = { model: m.id, status: 'ok', latency_ms: -1 }
  }
  // 串行探测（避免并发压力）
  for (const m of models) {
    try {
      const result = await probeOneModel(m.id)
      probeResults.value[m.id] = result
    } catch (e: any) {
      probeResults.value[m.id] = { model: m.id, status: 'error', error: e.message, latency_ms: 0 }
    }
  }
  probingEndpoint.value = ''
  // 保存结果
  probedAt.value = new Date().toISOString()
  try {
    const completed = Object.fromEntries(
      Object.entries(probeResults.value).filter(([, r]) => r.latency_ms !== -1)
    )
    await saveEndpointProbeResults(completed)
  } catch { /* ignore */ }
  message.success(`${models.length} 个模型探测完成`)
}

// ==================== CRUD 操作 ====================

/** 判断 base_url 是否是预设的空值/默认值 */
function isPresetBaseUrl(url: string) {
  const trimmed = url.trim()
  return !trimmed || trimmed === 'https://' || trimmed === 'https://api.example.com/v1'
}

/** 根据当前供应商获取 base_url 的 placeholder 提示 */
function getBaseUrlPlaceholder(providerType: string) {
  const suffix = providerPresets.value[providerType]?.url_suffix || '/v1'
  return `https://api.example.com${suffix}（须包含 ${suffix} 后缀）`
}

function onProviderTypeChange(pt: string) {
  const preset = providerPresets.value[pt]
  if (preset) {
    addEndpointForm.value.wire_api = preset.wire_api || 'chat'
    // 自动拼接 url_suffix 到 base_url（如果用户还没填或正在用旧的预设值）
    const oldUrl = addEndpointForm.value.base_url || ''
    if (!oldUrl || isPresetBaseUrl(oldUrl)) {
      addEndpointForm.value.base_url = ''  // 清空让用户填，placeholder 会提示
    }
  }
  // 自动生成端点标识
  const existing = Object.keys(endpoints.value)
  let idx = 1
  let key = pt
  while (existing.includes(key) || existing.includes(`${pt}_${idx}`)) {
    key = `${pt}_${idx}`
    idx++
  }
  addEndpointForm.value.endpoint_key = key
}

async function saveEndpoint() {
  const f = addEndpointForm.value
  if (!f.name || !f.base_url) {
    message.warning('请填写显示名称和端点地址')
    return
  }

  // 自动补全 url_suffix（如 /v1）
  let baseUrl = f.base_url.trim().replace(/\/+$/, '')
  const suffix = providerPresets.value[f.provider_type]?.url_suffix || '/v1'
  if (suffix && !baseUrl.endsWith(suffix)) {
    baseUrl = baseUrl + suffix
  }

  addEndpointSaving.value = true
  try {
    const config: Record<string, any> = {
      name: f.name,
      provider_type: f.provider_type,
      wire_api: f.wire_api,
      base_url: baseUrl,
    }
    if (f.api_key_mode === 'env') {
      config.api_key_env = f.api_key_env
    } else {
      config.api_key = f.api_key || null
    }
    if (f.default_headers.length) {
      const hdrs: Record<string, string> = {}
      f.default_headers.forEach(h => { if (h.key) hdrs[h.key] = h.value })
      config.default_headers = hdrs
    }

    const resp = await addEndpoint({ endpoint_key: f.endpoint_key, config })
    if (resp.success) {
      message.success('端点保存成功')
      showAddEndpoint.value = false
      resetAddEndpointForm()
      await loadEndpoints()
    }
  } catch (e: any) {
    message.error('保存失败: ' + (e.message || e))
  } finally {
    addEndpointSaving.value = false
  }
}

function resetAddEndpointForm() {
  addEndpointForm.value = {
    provider_type: 'openai',
    name: '',
    endpoint_key: '',
    base_url: '',
    api_key_mode: 'env',
    api_key_env: '',
    api_key: '',
    wire_api: 'chat',
    default_headers: [],
  }
}

function openEditEndpoint(key: string, ep: EndpointConfig) {
  editEndpointKey.value = key
  editEndpointForm.value = {
    name: ep.name,
    provider_type: ep.provider_type,
    wire_api: ep.wire_api,
    base_url: ep.base_url_display,
    api_key_mode: ep.api_key_mode || 'env',
    api_key_env: ep.api_key_env || '',
    api_key: '',  // 编辑时不回显原 Key，空值表示不更改
    api_key_display: ep.api_key_display || '',
    source: ep.source,
    deletable: ep.deletable ?? false,
    models_count: ep.models?.length || 0,  // 用于判断是否锁定供应商类型
    default_headers: ep.default_headers
      ? Object.entries(ep.default_headers).map(([k, v]) => ({ key: k, value: v }))
      : [],
  }
  showEditEndpoint.value = true
}

async function saveEditEndpoint() {
  const f = editEndpointForm.value
  addEndpointSaving.value = true
  try {
    const config: Record<string, any> = {
      name: f.name,
      base_url: f.base_url,
      provider_type: f.provider_type,
      wire_api: f.wire_api,
    }
    // API Key — 所有端点都能改
    config.api_key_mode = f.api_key_mode
    if (f.api_key_mode === 'env' && f.api_key_env) {
      config.api_key_env = f.api_key_env
      config.api_key = ''  // 用空字符串清除直接存储的 key
    } else if (f.api_key_mode === 'direct' && f.api_key) {
      config.api_key = f.api_key
      config.api_key_env = ''  // 用空字符串清除环境变量引用
    }
    // 自定义 Headers
    if (f.default_headers && f.default_headers.length > 0) {
      const headers: Record<string, string> = {}
      for (const h of f.default_headers) {
        if (h.key) headers[h.key] = h.value || ''
      }
      if (Object.keys(headers).length > 0) config.default_headers = headers
    }
    const resp = await addEndpoint({ endpoint_key: editEndpointKey.value, config })
    if (resp.success) {
      message.success('端点更新成功')
      showEditEndpoint.value = false
      await loadEndpoints()
    }
  } catch (e: any) {
    message.error('更新失败: ' + (e.message || e))
  } finally {
    addEndpointSaving.value = false
  }
}

function openAddModel(endpointKey: string) {
  addModelEndpointKey.value = endpointKey
  editingModelId.value = ''  // 新增模式
  addModelForm.value = {
    id: '', name: '', actual_model_id: '', provider: '', max_output_tokens: 16384,
    capabilities: ['chat'], assign_tier: '', probe_after: true,
  }
  showAddModel.value = true
}

function openEditModel(endpointKey: string, model: EndpointModelInfo) {
  addModelEndpointKey.value = endpointKey
  editingModelId.value = model.id  // 编辑模式
  addModelForm.value = {
    id: model.id,
    name: model.name || model.id,
    actual_model_id: model.actual_model_id || '',
    provider: model.provider || '',
    max_output_tokens: model.max_output_tokens || 16384,
    capabilities: model.capabilities || ['chat'],
    assign_tier: '',  // 编辑时不修改 tier
    probe_after: false,
  }
  showAddModel.value = true
}

async function saveModel() {
  const f = addModelForm.value
  if (!f.id || !f.name) {
    message.warning('请填写模型 ID 和显示名称')
    return
  }

  addModelSaving.value = true
  try {
    // 编辑模式
    if (editingModelId.value) {
      const resp = await updateModel(addModelEndpointKey.value, editingModelId.value, {
        id: f.id,
        name: f.name,
        actual_model_id: f.actual_model_id || undefined,
        provider: f.provider,
        max_output_tokens: f.max_output_tokens,
        capabilities: f.capabilities,
      })
      if (resp.success) {
        message.success('模型更新成功')
        showAddModel.value = false
        await loadEndpoints()
        if (f.probe_after) {
          await probeSingle(f.id)
        }
      }
      return
    }

    // 新增模式
    const resp = await addModel({
      endpoint_key: addModelEndpointKey.value,
      model: {
        id: f.id,
        name: f.name,
        actual_model_id: f.actual_model_id || undefined,
        provider: f.provider,
        max_output_tokens: f.max_output_tokens,
        capabilities: f.capabilities,
      },
      assign_tier: f.assign_tier || undefined,
    })

    if (!resp.success && resp.error === 'conflict') {
      message.warning(`模型 ${f.id} 已在端点「${resp.current_endpoint_name}」下，如需迁移请勾选强制`)
      return
    }

    if (resp.success) {
      message.success('模型添加成功')
      showAddModel.value = false
      await loadEndpoints()

      if (f.probe_after) {
        await probeSingle(f.id)
      }
    }
  } catch (e: any) {
    message.error((editingModelId.value ? '更新' : '添加') + '失败: ' + (e.message || e))
  } finally {
    addModelSaving.value = false
  }
}

async function handleDeleteEndpoint(key: string) {
  try {
    const resp = await deleteEndpoint(key)
    if (resp.success) {
      message.success('端点已删除')
      await loadEndpoints()
    } else if (resp.error === 'referenced') {
      message.error('无法删除: 端点下的模型被引用 — ' + (resp.refs || []).join(', '))
    }
  } catch (e: any) {
    message.error('删除失败: ' + (e.message || e))
  }
}

async function handleDeleteModel(epKey: string, modelId: string) {
  try {
    const resp = await deleteModel(epKey, modelId)
    if (resp.success) {
      message.success('模型已移除')
      await loadEndpoints()
    } else if (resp.error === 'referenced') {
      message.error('无法移除: 模型被引用 — ' + (resp.refs || []).join(', '))
    }
  } catch (e: any) {
    message.error('移除失败: ' + (e.message || e))
  }
}

// ==================== 状态标签 ====================
function getHealthTag(model: EndpointModelInfo) {
  // 探测中
  const pr = probeResults.value[model.id]
  if (pr && pr.latency_ms === -1) return { type: 'default' as const, label: '探测中...', bordered: false }

  // 探测结果
  if (pr) {
    if (pr.status === 'ok') return { type: 'success' as const, label: '正常', bordered: false }
    return { type: 'error' as const, label: '异常', bordered: false }
  }

  // CircuitBreaker 状态
  if (!model.health) return { type: 'default' as const, label: '未验证', bordered: false }
  const s = model.health.state
  if (s === 'closed') return { type: 'success' as const, label: '正常', bordered: false }
  if (s === 'open') return { type: 'error' as const, label: '熔断', bordered: true }
  if (s === 'half_open') return { type: 'warning' as const, label: '恢复中', bordered: false }
  return { type: 'error' as const, label: '异常', bordered: false }
}

const probeSummary = computed(() => {
  const all = Object.values(probeResults.value).filter(r => r.latency_ms !== -1)
  const passCount = all.filter(r => r.status === 'ok').length
  return { passCount, failCount: all.length - passCount, total: all.length }
})

const probeSummaryList = computed(() => {
  return Object.values(probeResults.value)
    .filter(r => r.latency_ms !== -1)
    .sort((a, b) => {
      if (a.status === 'ok' && b.status !== 'ok') return 1
      if (a.status !== 'ok' && b.status === 'ok') return -1
      return 0
    })
})

/** 清理 preview 中的 markdown/latex 标记，显示干净文本 */
function cleanPreview(text: string): string {
  if (!text) return ''
  return text
    .replace(/\$\$\s*(.*?)\s*\$\$/g, '$1')   // $$ ... $$
    .replace(/\$\s*(.*?)\s*\$/g, '$1')         // $ ... $
    .replace(/\\boxed\{(.*?)\}/g, '[$1]')      // \boxed{2} → [2]
    .replace(/\\(?:text|mathrm)\{(.*?)\}/g, '$1')
    .replace(/\*\*(.*?)\*\*/g, '$1')           // **bold**
    .replace(/<think>.*?<\/think>/gs, '')       // <think>...</think>
    .replace(/<[^>]+>/g, '')                    // 其余 HTML 标签
    .trim()
}

function getEndpointSummary(ep: EndpointConfig) {
  const total = ep.models.length
  let healthy = 0, unhealthy = 0, unknown = 0
  for (const m of ep.models) {
    const pr = probeResults.value[m.id]
    if (pr && pr.latency_ms !== -1) {
      // 优先用探测结果
      if (pr.status === 'ok') healthy++
      else unhealthy++
    } else if (!m.health) {
      unknown++
    } else if (m.health.state === 'closed') {
      healthy++
    } else {
      unhealthy++
    }
  }
  if (unhealthy > 0) return { text: `${unhealthy} 异常`, type: 'error' as const }
  if (healthy === total) return { text: '全部正常', type: 'success' as const }
  if (healthy > 0) return { text: `${healthy} 正常 ${unknown} 未验证`, type: 'warning' as const }
  return { text: `${unknown} 未验证`, type: 'default' as const }
}

function getProviderLabel(pt: string) {
  const preset = providerPresets.value[pt]
  return preset?.label || pt
}

// ==================== 生命周期 ====================
onMounted(() => {
  loadEndpoints()
})
</script>

<template>
  <n-spin :show="loading">
    <div class="model-endpoints-tab">
      <!-- 工具栏 -->
      <div class="toolbar">
        <n-space>
          <n-button type="primary" @click="showAddEndpoint = true">
            + 新增端点
          </n-button>
          <n-button :loading="probing" @click="probeAll" :disabled="probing">
            全部探测
          </n-button>
          <template v-if="probing">
            <n-progress
              type="line"
              :percentage="probeProgress"
              :show-indicator="false"
              style="width: 120px; margin-top: 8px"
            />
            <n-text depth="3" style="font-size: 12px">
              已等待 {{ probeElapsed }}s
            </n-text>
          </template>
          <template v-else-if="Object.keys(probeResults).length > 0">
            <n-tooltip trigger="hover" placement="bottom" :style="{ maxWidth: '420px' }">
              <template #trigger>
                <n-tag
                  :type="probeSummary.failCount === 0 ? 'success' : (probeSummary.passCount === 0 ? 'error' : 'warning')"
                  size="small"
                  round
                  style="cursor: pointer"
                >
                  {{ probeSummary.passCount }}/{{ probeSummary.total }} 通过
                </n-tag>
              </template>
              <div style="font-size: 12px; line-height: 1.8">
                <div style="font-weight: 600; margin-bottom: 4px; border-bottom: 1px solid rgba(255,255,255,0.15); padding-bottom: 4px">
                  探测结果明细
                </div>
                <div v-for="r in probeSummaryList" :key="r.model" style="display: flex; justify-content: space-between; gap: 12px">
                  <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 180px">{{ r.model }}</span>
                  <span v-if="r.status === 'ok'" style="color: #63e2b7">{{ r.latency_ms }}ms</span>
                  <span v-else style="color: #e88080">{{ r.error?.slice(0, 30) || '失败' }}</span>
                </div>
              </div>
            </n-tooltip>
            <n-text v-if="probedAt" depth="3" style="font-size: 12px">
              上次检测：{{ new Date(probedAt).toLocaleString('zh-CN') }}
            </n-text>
          </template>
        </n-space>
      </div>

      <!-- 概览统计 -->
      <n-grid :cols="4" :x-gap="12" style="margin-bottom: 20px">
        <n-grid-item>
          <n-card size="small"><n-statistic label="端点服务" :value="stats.totalEndpoints" /></n-card>
        </n-grid-item>
        <n-grid-item>
          <n-card size="small"><n-statistic label="模型总数" :value="stats.totalModels" /></n-card>
        </n-grid-item>
        <n-grid-item>
          <n-card size="small"><n-statistic label="正常模型" :value="stats.healthyModels" /></n-card>
        </n-grid-item>
        <n-grid-item>
          <n-card size="small"><n-statistic label="异常模型" :value="stats.unhealthyModels" /></n-card>
        </n-grid-item>
      </n-grid>

      <!-- 端点卡片列表 -->
      <n-collapse v-model:expanded-names="expandedEndpoints" accordion>
        <n-collapse-item
          v-for="(ep, epKey) in endpoints"
          :key="epKey"
          :name="epKey"
        >
          <template #header>
            <n-space align="center" :size="8">
              <strong>{{ ep.name }}</strong>
              <n-tag size="small" :bordered="false">{{ getProviderLabel(ep.provider_type) }}</n-tag>
              <n-tag size="small" :bordered="false" :type="ep.source === 'yaml' ? 'info' : 'default'">
                {{ ep.source === 'yaml' ? '系统内置' : '自定义' }}
              </n-tag>
              <n-text depth="3" style="font-size: 12px">
                {{ ep.models.length }} 个模型
              </n-text>
              <n-tag size="small" :type="getEndpointSummary(ep).type">
                {{ getEndpointSummary(ep).text }}
              </n-tag>
            </n-space>
          </template>
          <template #header-extra>
            <n-space :size="4" @click.stop>
              <n-button
                size="tiny"
                quaternary
                :loading="probingEndpoint === String(epKey)"
                @click.stop="probeEndpoint(String(epKey), ep.models)"
              >
                探测
              </n-button>
              <n-button size="tiny" quaternary @click.stop="openEditEndpoint(String(epKey), ep)">编辑</n-button>
              <n-button size="tiny" quaternary @click.stop="openAddModel(String(epKey))">+ 添加模型</n-button>
              <n-popconfirm
                v-if="ep.deletable"
                @positive-click="handleDeleteEndpoint(String(epKey))"
              >
                <template #trigger>
                  <n-button size="tiny" quaternary type="error" @click.stop>删除</n-button>
                </template>
                确认删除端点「{{ ep.name }}」？该端点下 {{ ep.models.length }} 个模型将被同时移除。
              </n-popconfirm>
            </n-space>
          </template>

          <div class="endpoint-detail">
            <n-descriptions :column="2" label-placement="left" bordered size="small" style="margin-bottom: 12px">
              <n-descriptions-item label="端点地址">{{ ep.base_url_display }}</n-descriptions-item>
              <n-descriptions-item label="API Key">{{ ep.api_key_display }}</n-descriptions-item>
              <n-descriptions-item label="协议" v-if="ep.wire_api === 'responses'">
                <n-tag size="small" type="warning">Responses API</n-tag>
              </n-descriptions-item>
              <n-descriptions-item label="协议" v-else>
                Chat Completions
              </n-descriptions-item>
            </n-descriptions>

            <!-- 模型表格 -->
            <table class="model-table">
              <thead>
                <tr>
                  <th>模型 ID</th>
                  <th>显示名</th>
                  <th>Provider</th>
                  <th>状态</th>
                  <th>使用状态</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="model in ep.models" :key="model.id">
                  <td>
                    <code>{{ model.id }}</code>
                    <n-text v-if="model.actual_model_id" depth="3" style="font-size: 12px; display: block;">
                      API: {{ model.actual_model_id }}
                    </n-text>
                  </td>
                  <td>{{ model.name }}</td>
                  <td>{{ model.provider }}</td>
                  <td>
                    <n-spin v-if="probeResults[model.id]?.latency_ms === -1" :size="14" />
                    <n-tag v-else size="small" :type="getHealthTag(model).type" :bordered="getHealthTag(model).bordered">
                      {{ getHealthTag(model).label }}
                    </n-tag>
                  </td>
                  <td>
                    <template v-if="model.usage && model.usage.length">
                      <n-tag
                        v-for="u in model.usage"
                        :key="u"
                        size="small"
                        :bordered="false"
                        type="info"
                        style="margin-right: 4px"
                      >{{ u }}</n-tag>
                    </template>
                    <n-tag
                      v-else
                      size="small"
                      :bordered="false"
                      style="cursor: pointer"
                      @click="emit('switch-tab', 'model')"
                    >未分配</n-tag>
                  </td>
                  <td>
                    <n-space :size="4" align="center">
                      <n-tooltip
                        v-if="probeResults[model.id] && probeResults[model.id].latency_ms !== -1"
                        trigger="hover"
                        placement="left"
                      >
                        <template #trigger>
                          <n-tag
                            size="small"
                            :type="probeResults[model.id].status === 'ok' ? 'success' : 'error'"
                            :bordered="false"
                            style="cursor: default"
                          >
                            {{ probeResults[model.id].status === 'ok' ? probeResults[model.id].latency_ms + 'ms' : '失败' }}
                          </n-tag>
                        </template>
                        <div style="font-size: 12px; max-width: 360px">
                          <template v-if="probeResults[model.id].status === 'ok'">
                            <div>延迟: {{ probeResults[model.id].latency_ms }}ms</div>
                            <div style="margin-top: 4px; color: #ccc">{{ cleanPreview(probeResults[model.id].preview || '') }}</div>
                          </template>
                          <template v-else>
                            <div style="color: #e88080">{{ probeResults[model.id].error }}</div>
                          </template>
                        </div>
                      </n-tooltip>
                      <n-button size="tiny" quaternary @click="probeSingle(model.id)" :loading="probeResults[model.id]?.latency_ms === -1">
                        探测
                      </n-button>
                      <n-button size="tiny" quaternary @click="openEditModel(String(epKey), model)">
                        编辑
                      </n-button>
                      <n-popconfirm
                        v-if="ep.deletable"
                        @positive-click="handleDeleteModel(String(epKey), model.id)"
                      >
                        <template #trigger>
                          <n-button size="tiny" quaternary type="error">删除</n-button>
                        </template>
                        确认从 {{ ep.name }} 移除模型「{{ model.id }}」？
                      </n-popconfirm>
                    </n-space>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </n-collapse-item>
      </n-collapse>

      <!-- 空状态 -->
      <n-alert
        v-if="Object.keys(endpoints).length === 0 && !loading"
        type="info"
        title="提示"
        style="margin-top: 20px"
      >
        暂无端点配置。点击「新增端点」添加自定义 LLM 服务。
        支持 Chat Completions API 和 Responses API 两种协议。
      </n-alert>

      <!-- ==================== 新增端点对话框 ==================== -->
      <n-modal
        v-model:show="showAddEndpoint"
        preset="dialog"
        title="新增端点服务"
        :show-icon="false"
        style="width: 600px"
      >
        <n-form label-placement="top" :model="addEndpointForm">
          <n-form-item label="供应商类型" required>
            <n-select
              v-model:value="addEndpointForm.provider_type"
              :options="Object.entries(providerPresets).map(([k, v]) => ({
                label: v.label + (v.wire_api === 'responses' ? ' (Responses API)' : ''),
                value: k
              }))"
              @update:value="onProviderTypeChange"
            />
          </n-form-item>

          <n-form-item label="显示名称" required>
            <n-input v-model:value="addEndpointForm.name" placeholder="如: PackyAPI-GPT" />
          </n-form-item>

          <n-form-item label="端点地址" required>
            <n-input v-model:value="addEndpointForm.base_url" :placeholder="getBaseUrlPlaceholder(addEndpointForm.provider_type)" />
          </n-form-item>

          <n-form-item label="API Key" required>
            <div style="width: 100%">
              <n-radio-group v-model:value="addEndpointForm.api_key_mode" style="margin-bottom: 8px">
                <n-radio value="env">引用环境变量 (推荐)</n-radio>
                <n-radio value="direct">直接输入</n-radio>
              </n-radio-group>
              <n-input
                v-if="addEndpointForm.api_key_mode === 'env'"
                v-model:value="addEndpointForm.api_key_env"
                placeholder="环境变量名，如 MY_API_KEY"
              />
              <template v-else>
                <n-alert type="warning" style="margin-bottom: 8px; font-size: 12px" :bordered="false">
                  API Key 将加密存储在数据库。GET 接口仅返回脱敏值。
                </n-alert>
                <n-input
                  v-model:value="addEndpointForm.api_key"
                  type="password"
                  show-password-on="click"
                  placeholder="sk-xxxxxx"
                />
              </template>
            </div>
          </n-form-item>

          <n-collapse>
            <n-collapse-item title="高级选项">
              <n-form-item label="端点标识">
                <n-input v-model:value="addEndpointForm.endpoint_key" placeholder="自动生成" />
              </n-form-item>
              <n-form-item label="API 协议">
                <n-select
                  v-model:value="addEndpointForm.wire_api"
                  :options="[
                    { label: 'Chat Completions', value: 'chat' },
                    { label: 'Responses API', value: 'responses' },
                  ]"
                  :disabled="addEndpointForm.provider_type !== 'custom'"
                />
              </n-form-item>
              <n-form-item label="自定义 Headers">
                <n-dynamic-input
                  v-model:value="addEndpointForm.default_headers"
                  :on-create="() => ({ key: '', value: '' })"
                >
                  <template #default="{ value }">
                    <n-space>
                      <n-input v-model:value="value.key" placeholder="Header Name" style="width: 180px" />
                      <n-input v-model:value="value.value" placeholder="Header Value" style="width: 200px" />
                    </n-space>
                  </template>
                </n-dynamic-input>
              </n-form-item>
            </n-collapse-item>
          </n-collapse>
        </n-form>

        <template #action>
          <n-space>
            <n-button @click="showAddEndpoint = false">取消</n-button>
            <n-button type="primary" :loading="addEndpointSaving" @click="saveEndpoint">
              保存
            </n-button>
          </n-space>
        </template>
      </n-modal>

      <!-- ==================== 编辑端点对话框 ==================== -->
      <n-modal
        v-model:show="showEditEndpoint"
        preset="dialog"
        title="编辑端点"
        :show-icon="false"
        style="width: 600px"
      >
        <n-form label-placement="top" :model="editEndpointForm">
          <n-form-item label="端点标识">
            <n-input :value="editEndpointKey" disabled />
          </n-form-item>

          <n-form-item label="来源">
            <n-tag :type="editEndpointForm.source === 'yaml' ? 'info' : 'default'" size="small">
              {{ editEndpointForm.source === 'yaml' ? '系统内置' : '自定义' }}
            </n-tag>
            <n-text v-if="editEndpointForm.source === 'yaml'" depth="3" style="margin-left: 8px; font-size: 12px">
              编辑后将覆盖保存到数据库
            </n-text>
          </n-form-item>

          <n-form-item label="供应商类型">
            <n-select
              v-model:value="editEndpointForm.provider_type"
              :options="Object.entries(providerPresets).map(([k, v]) => ({
                label: v.label + (v.wire_api === 'responses' ? ' (Responses API)' : ''),
                value: k
              }))"
              :disabled="editEndpointForm.models_count > 0"
              @update:value="(val: string) => {
                const preset = providerPresets[val]
                if (preset) editEndpointForm.wire_api = preset.wire_api
              }"
            />
            <n-text v-if="editEndpointForm.models_count > 0" depth="3" style="margin-left: 8px; font-size: 12px">
              已有 {{ editEndpointForm.models_count }} 个模型绑定，供应商类型不可更改
            </n-text>
          </n-form-item>

          <n-form-item label="显示名称" required>
            <n-input v-model:value="editEndpointForm.name" />
          </n-form-item>

          <n-form-item label="端点地址" required>
            <n-input v-model:value="editEndpointForm.base_url" :placeholder="getBaseUrlPlaceholder(editEndpointForm.provider_type)" />
          </n-form-item>

          <n-form-item label="API Key">
            <div style="width: 100%">
              <n-radio-group v-model:value="editEndpointForm.api_key_mode" style="margin-bottom: 8px">
                <n-radio value="env">引用环境变量</n-radio>
                <n-radio value="direct">直接输入</n-radio>
              </n-radio-group>
              <template v-if="editEndpointForm.api_key_mode === 'env'">
                <n-input
                  v-model:value="editEndpointForm.api_key_env"
                  placeholder="环境变量名，如 MY_API_KEY"
                />
                <n-text v-if="editEndpointForm.api_key_display" depth="3" style="font-size: 12px; margin-top: 4px; display: block">
                  当前值: {{ editEndpointForm.api_key_display }}
                </n-text>
              </template>
              <template v-else>
                <n-alert type="warning" style="margin-bottom: 8px; font-size: 12px" :bordered="false">
                  API Key 将加密存储在数据库。GET 接口仅返回脱敏值。
                </n-alert>
                <n-input
                  v-model:value="editEndpointForm.api_key"
                  type="password"
                  show-password-on="click"
                  :placeholder="editEndpointForm.api_key_display ? '留空则保持不变 (' + editEndpointForm.api_key_display + ')' : 'sk-xxxxxx'"
                />
              </template>
            </div>
          </n-form-item>

          <n-collapse>
            <n-collapse-item title="高级选项">
              <n-form-item label="API 协议">
                <n-select
                  v-model:value="editEndpointForm.wire_api"
                  :options="[
                    { label: 'Chat Completions (/v1/chat/completions)', value: 'chat' },
                    { label: 'Responses API (/v1/responses)', value: 'responses' },
                  ]"
                />
                <template #feedback>
                  实际调用 LLM 服务的协议。大部分代理服务使用 Chat Completions。
                </template>
              </n-form-item>
              <n-form-item label="自定义 Headers">
                <n-dynamic-input
                  v-model:value="editEndpointForm.default_headers"
                  :on-create="() => ({ key: '', value: '' })"
                >
                  <template #default="{ value }">
                    <n-space>
                      <n-input v-model:value="value.key" placeholder="Header Name" style="width: 180px" />
                      <n-input v-model:value="value.value" placeholder="Header Value" style="width: 200px" />
                    </n-space>
                  </template>
                </n-dynamic-input>
              </n-form-item>
            </n-collapse-item>
          </n-collapse>
        </n-form>

        <template #action>
          <n-space>
            <n-button @click="showEditEndpoint = false">取消</n-button>
            <n-button type="primary" :loading="addEndpointSaving" @click="saveEditEndpoint">
              保存
            </n-button>
          </n-space>
        </template>
      </n-modal>

      <!-- ==================== 添加/编辑模型对话框 ==================== -->
      <n-modal
        v-model:show="showAddModel"
        preset="dialog"
        :title="editingModelId ? '编辑模型: ' + editingModelId : '添加模型到: ' + (endpoints[addModelEndpointKey]?.name || addModelEndpointKey)"
        :show-icon="false"
        style="width: 550px"
      >
        <n-form label-placement="top" :model="addModelForm">
          <n-form-item label="模型 ID" required>
            <n-input v-model:value="addModelForm.id" placeholder="如: claude-sonnet-4-5:newcli" />
            <template #feedback>系统内部路由标识，多端点同模型时需添加后缀区分（如 :newcli）</template>
          </n-form-item>

          <n-form-item label="实际模型 ID（选填）">
            <n-input v-model:value="addModelForm.actual_model_id" placeholder="如: claude-sonnet-4-5-20250929" />
            <template #feedback>发给 LLM API 的真实模型名。留空则使用上方的模型 ID</template>
          </n-form-item>

          <n-form-item label="显示名称" required>
            <n-input v-model:value="addModelForm.name" placeholder="如: Claude Sonnet 4.5 (NewCLI)" />
            <template #feedback>对外显示名称，不可与其他模型重复</template>
          </n-form-item>

          <n-form-item label="提供商">
            <n-input v-model:value="addModelForm.provider" placeholder="如: OpenAI" />
          </n-form-item>

          <n-form-item label="最大输出 Tokens">
            <n-input-number v-model:value="addModelForm.max_output_tokens" :min="256" :max="1048576" />
          </n-form-item>

          <n-form-item label="模型能力">
            <n-checkbox-group v-model:value="addModelForm.capabilities">
              <n-space>
                <n-checkbox value="chat" label="chat 基础对话" />
                <n-checkbox value="tools" label="tools 函数调用" />
                <n-checkbox value="vision" label="vision 图像理解" />
                <n-checkbox value="reasoning" label="reasoning 思考推理" />
              </n-space>
            </n-checkbox-group>
          </n-form-item>

          <n-form-item v-if="!editingModelId" label="分配到模型等级（可选）">
            <n-select
              v-model:value="addModelForm.assign_tier"
              clearable
              placeholder="不分配（仅注册）"
              :options="[
                { label: 'high - 高性能模型', value: 'high' },
                { label: 'mid - 标准模型', value: 'mid' },
                { label: 'low - 轻量模型', value: 'low' },
              ]"
            />
          </n-form-item>

          <n-form-item>
            <n-switch v-model:value="addModelForm.probe_after" />
            <n-text style="margin-left: 8px">保存后立即探测连通性</n-text>
          </n-form-item>
        </n-form>

        <template #action>
          <n-space>
            <n-button @click="showAddModel = false">取消</n-button>
            <n-button type="primary" :loading="addModelSaving" @click="saveModel">
              {{ editingModelId ? '保存' : '添加模型' }}
            </n-button>
          </n-space>
        </template>
      </n-modal>
    </div>
  </n-spin>
</template>

<style scoped>
.model-endpoints-tab {
  padding: 0;
}

.toolbar {
  margin-bottom: 16px;
}

.model-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.model-table th {
  text-align: left;
  padding: 8px 12px;
  border-bottom: 1px solid var(--n-border-color, #eee);
  font-weight: 600;
  color: var(--n-text-color-3, #999);
  font-size: 12px;
}

.model-table td {
  padding: 8px 12px;
  border-bottom: 1px solid var(--n-divider-color, #f0f0f0);
}

.model-table tr:hover td {
  background: var(--n-color-hover, #f8f8f8);
}

.model-table code {
  font-size: 12px;
  padding: 1px 4px;
  background: var(--n-code-color, #f5f5f5);
  border-radius: 3px;
}

.endpoint-detail {
  padding: 0 4px;
}
</style>
