<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import {
  Database, FileCode2, Link2, Plus, Power, RefreshCw,
  Save, Search, ServerCog, Settings2, Trash2, X,
} from '@lucide/vue'
import { ApiError, api } from '@/api'

type ResourceKind = 'skills' | 'tools' | 'mcp-servers' | 'knowledge-bases'

const props = defineProps<{ kind: string }>()
const emit = defineEmits<{ notice: [type: 'success' | 'error', text: string] }>()

const kind = computed<ResourceKind>(() => props.kind as ResourceKind)
const meta = computed(() => ({
  skills: { singular: 'Skill', label: 'Skills', icon: FileCode2 },
  tools: { singular: 'Tool', label: 'Tools', icon: Settings2 },
  'mcp-servers': { singular: 'MCP Server', label: 'MCP Servers', icon: ServerCog },
  'knowledge-bases': { singular: '知识库', label: '知识库', icon: Database },
}[kind.value]))

const loading = ref(false)
const saving = ref(false)
const items = ref<any[]>([])
const total = ref(0)
const search = ref('')
const editorOpen = ref(false)
const editorMode = ref<'create' | 'edit'>('create')
const selected = ref<any | null>(null)
const confirmDelete = ref(false)
const deleteConfirmation = ref('')
const publicationTarget = ref<'tenant' | 'global'>('tenant')
const publicationNote = ref('')

const form = reactive({
  name: '', display_name: '', description: '', category: 'general', version: '1.0.0',
  model_tier: 'standard', system_prompt: '', allowed_tools_text: '', enabled: true,
  execution_type: 'api', schema_text: '{\n  "type": "object",\n  "properties": {}\n}',
  endpoint: '', adapter_text: '', mcp_server_id: '',
  transport: 'streamable_http', auth_text: '', tool_filter_text: '',
  visibility: 'private', retrieval_mode: 'hybrid', retrieval_top_k: 5,
  score_threshold: 0.5, embedding_model: 'bge-m3', embedding_dim: 1024,
  reranker_model: '', reranker_enabled: true, vlm_model: '', vlm_enabled: true,
  multimodal: false, expected_updated_at: null as string | null,
})

const errorText = (error: unknown) => error instanceof Error ? error.message : '请求失败'
const formatTime = (value?: string | null) => value
  ? new Intl.DateTimeFormat('zh-CN', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value))
  : '-'
const visibilityLabel = (value?: string) => ({ private: '私有', tenant: '租户共享', global: '全平台', shared: '租户共享', public: '全平台', personal: '个人库' }[value || ''] || value || '-')
const canEdit = (item: any) => item.allowed_actions?.includes('update') || item.can_edit
const canPublish = computed(() => selected.value?.allowed_actions?.includes('submit_publication'))
const filteredItems = computed(() => {
  const query = search.value.trim().toLowerCase()
  if (!query) return items.value
  return items.value.filter((item) => [item.name, item.display_name, item.description, item.endpoint]
    .some((value) => String(value || '').toLowerCase().includes(query)))
})
const deleteWarning = computed(() => ({
  skills: '删除后，已绑定该 Skill 的 Agent 将失去这项能力；待审批申请也会被取消。',
  tools: '删除后，所有引用该 Tool 的 Agent 可能执行失败。名称不可通过重新创建自动恢复绑定。',
  'mcp-servers': '删除会连带删除该 Server 同步生成的全部 Tool，并影响绑定这些 Tool 的 Agent。',
  'knowledge-bases': `删除会永久清除该知识库及其 ${selected.value?.document_count || 0} 个文档和全部向量数据。`,
}[kind.value]))

function splitValues(value: string) {
  return value.split(/[\n,]/).map((item) => item.trim()).filter(Boolean)
}

function parseObject(value: string, label: string, optional = false) {
  if (!value.trim() && optional) return null
  try {
    const parsed = JSON.parse(value)
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error()
    return parsed
  } catch {
    throw new Error(`${label} 必须是合法的 JSON 对象`)
  }
}

async function loadResources() {
  loading.value = true
  try {
    if (kind.value === 'skills') {
      const result = await api.skills({ size: 200 })
      items.value = result.skills
      total.value = result.total
    } else if (kind.value === 'tools') {
      const result = await api.tools({ page_size: 200 })
      items.value = result.items
      total.value = result.total
    } else if (kind.value === 'mcp-servers') {
      const result = await api.mcpServers({ size: 200 })
      items.value = result.items
      total.value = result.total
    } else {
      const result = await api.knowledgeBases({ size: 200 })
      items.value = result.knowledge_bases
      total.value = result.total
    }
  } catch (error) {
    emit('notice', 'error', errorText(error))
  } finally {
    loading.value = false
  }
}

function resetForm(item?: any) {
  Object.assign(form, {
    name: item?.name || '', display_name: item?.display_name || '', description: item?.description || '',
    category: item?.category || 'general', version: item?.version || '1.0.0', model_tier: item?.model_tier || 'standard',
    system_prompt: item?.system_prompt || '', allowed_tools_text: (item?.allowed_tools || []).join('\n'),
    enabled: item?.enabled ?? true, execution_type: item?.execution_type || 'api',
    schema_text: JSON.stringify(item?.schema_json || { type: 'object', properties: {} }, null, 2),
    endpoint: item?.endpoint || '', adapter_text: item?.http_adapter_config ? JSON.stringify(item.http_adapter_config, null, 2) : '',
    mcp_server_id: item?.mcp_server_id ? String(item.mcp_server_id) : '', transport: item?.transport || 'streamable_http',
    auth_text: '', tool_filter_text: (item?.tool_filter || []).join('\n'), visibility: item?.visibility || 'private',
    retrieval_mode: item?.retrieval_mode || 'hybrid', retrieval_top_k: item?.retrieval_top_k ?? 5,
    score_threshold: item?.score_threshold ?? 0.5, embedding_model: item?.embedding_model || 'bge-m3',
    embedding_dim: item?.embedding_dim || 1024, reranker_model: item?.reranker_model || '',
    reranker_enabled: item?.reranker_enabled ?? true, vlm_model: item?.vlm_model || '',
    vlm_enabled: item?.vlm_enabled ?? true, multimodal: item?.multimodal ?? false,
    expected_updated_at: item?.updated_at || null,
  })
}

function openCreate() {
  selected.value = null
  editorMode.value = 'create'
  resetForm()
  confirmDelete.value = false
  deleteConfirmation.value = ''
  editorOpen.value = true
}

async function openEdit(item: any) {
  loading.value = true
  try {
    let detail = item
    if (kind.value === 'skills') detail = await api.skill(item.id)
    if (kind.value === 'mcp-servers') detail = await api.mcpServer(item.id)
    if (kind.value === 'knowledge-bases') detail = await api.knowledgeBase(item.id)
    selected.value = detail
    editorMode.value = 'edit'
    resetForm(detail)
    confirmDelete.value = false
    deleteConfirmation.value = ''
    publicationTarget.value = detail.visibility === 'tenant' || detail.visibility === 'shared' ? 'global' : 'tenant'
    publicationNote.value = ''
    editorOpen.value = true
  } catch (error) {
    emit('notice', 'error', errorText(error))
  } finally {
    loading.value = false
  }
}

function skillPayload() {
  return {
    display_name: form.display_name.trim(), description: form.description.trim(), category: form.category.trim() || 'general',
    system_prompt: form.system_prompt, model_tier: form.model_tier, allowed_tools: splitValues(form.allowed_tools_text),
    enabled: form.enabled,
  }
}

function toolPayload() {
  const adapter = parseObject(form.adapter_text, 'HTTP Adapter 配置', true)
  return {
    display_name: form.display_name.trim(), description: form.description.trim(), category: form.category.trim() || 'general',
    execution_type: form.execution_type, schema_json: parseObject(form.schema_text, '参数 Schema'),
    endpoint: form.endpoint.trim() || null, http_adapter_config: adapter,
    mcp_server_id: form.mcp_server_id ? Number(form.mcp_server_id) : null, enabled: form.enabled,
  }
}

function mcpPayload(isCreate: boolean) {
  const payload: Record<string, unknown> = {
    name: form.name.trim(), description: form.description.trim(), category: form.category.trim() || 'general',
    transport: form.transport, endpoint: form.endpoint.trim(), tool_filter: splitValues(form.tool_filter_text), enabled: form.enabled,
  }
  if (form.auth_text.trim()) payload.auth_config = parseObject(form.auth_text, '认证配置')
  else if (isCreate) payload.auth_config = null
  return payload
}

function kbPayload(isCreate: boolean) {
  const payload: Record<string, unknown> = {
    name: form.name.trim(), description: form.description.trim(), retrieval_mode: form.retrieval_mode,
    retrieval_top_k: Number(form.retrieval_top_k), score_threshold: Number(form.score_threshold),
    embedding_model: form.embedding_model.trim(), reranker_model: form.reranker_model.trim(),
    reranker_enabled: form.reranker_enabled, vlm_model: form.vlm_model.trim(), vlm_enabled: form.vlm_enabled,
    multimodal: form.multimodal,
  }
  if (isCreate) payload.visibility = 'private'
  else {
    payload.enabled = form.enabled
    payload.embedding_dim = Number(form.embedding_dim)
    payload.expected_updated_at = form.expected_updated_at
  }
  return payload
}

async function saveResource() {
  if (!form.name.trim()) return emit('notice', 'error', '名称不能为空')
  saving.value = true
  try {
    let saved: any
    let successMessage = `${meta.value.singular} 配置已保存`
    if (kind.value === 'skills') {
      const payload = skillPayload()
      saved = editorMode.value === 'create'
        ? await api.createSkill({ name: form.name.trim(), version: form.version.trim() || '1.0.0', ...payload })
        : await api.updateSkill(selected.value.id, payload)
      if (saved.impact?.publication_request_invalidated) successMessage = '配置已保存，原待审批申请因资源变更已自动取消'
    } else if (kind.value === 'tools') {
      const payload = toolPayload()
      saved = editorMode.value === 'create'
        ? await api.createTool({ name: form.name.trim(), ...payload })
        : await api.updateTool(selected.value.id, payload)
    } else if (kind.value === 'mcp-servers') {
      const payload = mcpPayload(editorMode.value === 'create')
      saved = editorMode.value === 'create'
        ? await api.createMcpServer(payload)
        : await api.updateMcpServer(selected.value.id, payload)
    } else {
      const payload = kbPayload(editorMode.value === 'create')
      saved = editorMode.value === 'create'
        ? await api.createKnowledgeBase(payload)
        : await api.updateKnowledgeBase(selected.value.id, payload)
    }
    selected.value = saved
    editorMode.value = 'edit'
    resetForm(saved)
    await loadResources()
    emit('notice', saved.impact?.publication_request_invalidated ? 'error' : 'success', successMessage)
  } catch (error) {
    const message = error instanceof ApiError && error.status === 409 ? `${error.message}，请重新打开后再修改` : errorText(error)
    emit('notice', 'error', message)
  } finally {
    saving.value = false
  }
}

async function toggleEnabled(item: any) {
  try {
    let updated: any
    if (kind.value === 'skills') updated = await api.updateSkill(item.id, { enabled: !item.enabled })
    else if (kind.value === 'tools') updated = await api.updateTool(item.id, { enabled: !item.enabled })
    else if (kind.value === 'mcp-servers') updated = await api.updateMcpServer(item.id, { enabled: !item.enabled })
    else updated = await api.updateKnowledgeBase(item.id, { enabled: !item.enabled, expected_updated_at: item.updated_at })
    Object.assign(item, updated)
    emit('notice', 'success', updated.enabled ? '资源已启用' : '资源已停用')
  } catch (error) {
    emit('notice', 'error', errorText(error))
    await loadResources()
  }
}

async function removeResource() {
  if (!selected.value || deleteConfirmation.value !== selected.value.name) return
  try {
    let result: any
    if (kind.value === 'skills') result = await api.deleteSkill(selected.value.id)
    else if (kind.value === 'tools') await api.deleteTool(selected.value.id)
    else if (kind.value === 'mcp-servers') await api.deleteMcpServer(selected.value.id)
    else await api.deleteKnowledgeBase(selected.value.id)
    editorOpen.value = false
    await loadResources()
    const bindings = Number(result?.impact?.active_agent_bindings || 0)
    emit('notice', bindings ? 'error' : 'success', bindings ? `资源已删除，${bindings} 个 Agent 绑定受到影响` : '资源已删除')
  } catch (error) {
    emit('notice', 'error', errorText(error))
  }
}

async function requestPublication() {
  if (!selected.value) return
  try {
    await api.requestResourcePublication(kind.value, selected.value.id, publicationTarget.value, publicationNote.value.trim())
    emit('notice', 'success', `已提交${publicationTarget.value === 'global' ? '全平台' : '租户'}可见申请`)
    publicationNote.value = ''
    await loadResources()
    if (kind.value === 'skills') selected.value = await api.skill(selected.value.id)
  } catch (error) {
    emit('notice', 'error', errorText(error))
  }
}

async function syncMcp() {
  if (!selected.value) return
  saving.value = true
  try {
    const result = await api.syncMcpServer(selected.value.id)
    selected.value = await api.mcpServer(selected.value.id)
    resetForm(selected.value)
    await loadResources()
    emit('notice', 'success', `同步完成，已更新 ${result.synced} 个 Tool，移除 ${result.removed.length} 个旧 Tool`)
  } catch (error) {
    emit('notice', 'error', errorText(error))
  } finally {
    saving.value = false
  }
}

watch(kind, () => {
  editorOpen.value = false
  search.value = ''
  loadResources()
})
onMounted(loadResources)
</script>

<template>
  <div class="resource-manager">
    <div class="toolbar resource-toolbar">
      <div class="search-field"><Search :size="16" /><input v-model="search" :placeholder="`搜索 ${meta.label}`" /></div>
      <button class="secondary-button" :disabled="loading" @click="loadResources"><RefreshCw :size="15" :class="{ spin: loading }" />刷新</button>
      <span class="result-count">{{ filteredItems.length }} / {{ total }} 项</span>
      <button class="primary-button" @click="openCreate"><Plus :size="16" />新建 {{ meta.singular }}</button>
    </div>

    <div class="table-wrap resource-table">
      <table>
        <thead><tr><th>资源</th><th>范围 / 类型</th><th>状态</th><th>关键配置</th><th>更新时间</th><th></th></tr></thead>
        <tbody><tr v-for="item in filteredItems" :key="item.id">
          <td><div class="primary-cell"><span class="resource-icon"><component :is="meta.icon" :size="16" /></span><div><strong>{{ item.display_name || item.name }}</strong><span class="cell-note mono">{{ item.name }} · #{{ item.id }}</span></div></div></td>
          <td><span>{{ visibilityLabel(item.visibility || (item.owner_scope === 'user' ? 'private' : item.tenant_id === 'default' ? 'global' : 'tenant')) }}</span><span class="cell-note">{{ item.execution_type || item.transport || item.category || item.retrieval_mode }}</span></td>
          <td><span :class="`status status-${item.enabled ? 'active' : 'disabled'}`">{{ item.enabled ? '启用' : '停用' }}</span><span v-if="item.connection_status" :class="`cell-note connection-${item.connection_status}`">{{ item.connection_status }}</span></td>
          <td><span v-if="kind === 'skills'">{{ item.model_tier }} · {{ item.version }}</span><span v-else-if="kind === 'tools'">{{ item.execution_type }} · {{ Object.keys(item.schema_json || {}).length }} schema keys</span><span v-else-if="kind === 'mcp-servers'">{{ item.endpoint }}</span><span v-else>{{ item.document_count }} 文档 · Top {{ item.retrieval_top_k }}</span><span class="cell-note">{{ item.description || '暂无描述' }}</span></td>
          <td>{{ formatTime(item.updated_at || item.last_sync_at || item.created_at) }}</td>
          <td class="actions"><button class="small-button" :disabled="!canEdit(item)" @click="openEdit(item)"><Settings2 :size="14" />编辑</button><button class="icon-button" :disabled="!canEdit(item)" :title="item.enabled ? '停用' : '启用'" @click="toggleEnabled(item)"><Power :size="15" /></button></td>
        </tr></tbody>
      </table>
      <div v-if="!filteredItems.length && !loading" class="empty"><component :is="meta.icon" :size="28" /><strong>没有匹配的{{ meta.label }}</strong></div>
    </div>

    <div v-if="editorOpen" class="drawer-backdrop" @click.self="editorOpen = false">
      <aside class="agent-drawer resource-drawer">
        <header class="drawer-header"><div><span class="eyebrow">{{ editorMode === 'create' ? 'NEW RESOURCE' : `${meta.singular.toUpperCase()} #${selected?.id}` }}</span><h2>{{ editorMode === 'create' ? `新建 ${meta.singular}` : (selected?.display_name || selected?.name) }}</h2></div><button class="icon-button" @click="editorOpen = false"><X :size="18" /></button></header>
        <div class="drawer-body">
          <div v-if="kind === 'skills'" class="impact-notice">修改 Skill 会取消尚未审批的发布申请；停用会影响所有已绑定该 Skill 的 Agent。</div>
          <div v-if="kind === 'tools'" class="impact-notice">平台管理员在 default 租户新建的 Tool 会成为全平台资源。内部名称创建后不可修改；执行侧缓存异常时，配置变更最多延迟 5 分钟生效。</div>
          <div v-if="kind === 'mcp-servers'" class="impact-notice">平台管理员在 default 租户新建的 MCP Server 会成为全平台连接。修改 Endpoint 会重置连接状态；认证配置不会回显，留空表示保留原凭证。</div>
          <div v-if="kind === 'knowledge-bases' && selected?.document_count" class="impact-notice danger-notice">该知识库已有文档，Embedding 模型和维度已锁定；修改它们需要新建知识库并重新导入。</div>

          <div class="form-grid resource-form">
            <label><span>内部名称</span><input v-model="form.name" :disabled="editorMode === 'edit' && kind !== 'mcp-servers'" maxlength="200" /></label>
            <label v-if="kind === 'skills' || kind === 'tools'"><span>显示名称</span><input v-model="form.display_name" /></label>
            <label v-if="kind !== 'knowledge-bases'"><span>分类</span><input v-model="form.category" /></label>
            <label v-if="kind === 'skills'"><span>版本</span><input v-model="form.version" :disabled="editorMode === 'edit'" /></label>
            <label class="full-field"><span>描述</span><textarea v-model="form.description" rows="3"></textarea></label>

            <template v-if="kind === 'skills'">
              <label><span>模型等级</span><select v-model="form.model_tier"><option value="standard">standard</option><option value="advanced">advanced</option><option value="reasoning">reasoning</option></select></label>
              <label class="toggle-field"><input v-model="form.enabled" type="checkbox" :disabled="editorMode === 'create'" /><span>启用 Skill</span></label>
              <label class="full-field"><span>允许调用的 Tool 名称，每行一个</span><textarea v-model="form.allowed_tools_text" rows="4"></textarea></label>
              <label class="full-field"><span>System Prompt</span><textarea v-model="form.system_prompt" class="prompt-editor" rows="14"></textarea></label>
            </template>

            <template v-else-if="kind === 'tools'">
              <label><span>执行类型</span><select v-model="form.execution_type" :disabled="editorMode === 'edit' && !!selected?.mcp_server_id"><option value="api">API</option><option value="mcp">MCP</option></select></label>
              <label class="toggle-field"><input v-model="form.enabled" type="checkbox" :disabled="editorMode === 'create'" /><span>启用 Tool</span></label>
              <label class="full-field"><span>Endpoint</span><input v-model="form.endpoint" placeholder="https://api.example.com/action" /></label>
              <label v-if="form.execution_type === 'mcp'"><span>MCP Server ID</span><input v-model="form.mcp_server_id" type="number" min="1" /></label>
              <label class="full-field"><span>参数 Schema（JSON）</span><textarea v-model="form.schema_text" class="json-editor" rows="12"></textarea></label>
              <label class="full-field"><span>HTTP Adapter 配置（JSON，可选）</span><textarea v-model="form.adapter_text" class="json-editor" rows="10" placeholder="留空表示不配置"></textarea></label>
            </template>

            <template v-else-if="kind === 'mcp-servers'">
              <label><span>传输协议</span><select v-model="form.transport"><option value="streamable_http">Streamable HTTP</option></select></label>
              <label class="toggle-field"><input v-model="form.enabled" type="checkbox" /><span>启用 MCP Server</span></label>
              <label class="full-field"><span>Endpoint</span><input v-model="form.endpoint" placeholder="https://mcp.example.com/mcp" /></label>
              <label class="full-field"><span>Tool Filter，每行一个；留空同步全部</span><textarea v-model="form.tool_filter_text" rows="4"></textarea></label>
              <label class="full-field"><span>认证配置（JSON）</span><textarea v-model="form.auth_text" class="json-editor" rows="6" :placeholder="selected?.has_auth ? '凭证已配置；留空表示不替换' : '{\n  &quot;token&quot;: &quot;...&quot;\n}'"></textarea></label>
              <div v-if="selected" class="resource-health full-field"><span :class="`status status-${selected.connection_status === 'connected' ? 'active' : selected.connection_status === 'error' ? 'failed' : 'pending'}`">{{ selected.connection_status }}</span><code>{{ selected.last_error || `上次同步 ${formatTime(selected.last_sync_at)}` }}</code><button class="secondary-button" :disabled="saving || !selected.enabled" @click="syncMcp"><RefreshCw :size="15" />同步 Tools</button></div>
            </template>

            <template v-else>
              <label><span>检索模式</span><select v-model="form.retrieval_mode"><option value="hybrid">混合检索</option><option value="semantic">语义检索</option><option value="keyword">关键词检索</option></select></label>
              <label><span>Top K</span><input v-model.number="form.retrieval_top_k" type="number" min="1" max="100" /></label>
              <label><span>分数阈值</span><input v-model.number="form.score_threshold" type="number" min="0" max="1" step="0.01" /></label>
              <label class="toggle-field"><input v-model="form.enabled" type="checkbox" :disabled="editorMode === 'create'" /><span>启用知识库</span></label>
              <label><span>Embedding 模型</span><input v-model="form.embedding_model" :disabled="editorMode === 'edit' && selected?.document_count > 0" /></label>
              <label><span>Embedding 维度</span><input v-model.number="form.embedding_dim" type="number" min="1" :disabled="editorMode === 'create' || selected?.document_count > 0" /></label>
              <label><span>Reranker 模型</span><input v-model="form.reranker_model" /></label>
              <label class="toggle-field"><input v-model="form.reranker_enabled" type="checkbox" /><span>启用 Reranker</span></label>
              <label><span>VLM 模型</span><input v-model="form.vlm_model" /></label>
              <label class="toggle-field"><input v-model="form.vlm_enabled" type="checkbox" /><span>启用 VLM</span></label>
              <label class="toggle-field"><input v-model="form.multimodal" type="checkbox" /><span>启用多模态解析</span></label>
              <div v-if="selected" class="resource-stats"><strong>{{ selected.document_count }}</strong><span>文档</span><strong>{{ selected.word_count }}</strong><span>词数</span></div>
            </template>
          </div>

          <section v-if="selected" class="governance-panel resource-governance"><div><span class="eyebrow">VISIBILITY</span><h3>{{ visibilityLabel(selected.visibility || (selected.owner_scope === 'user' ? 'private' : selected.tenant_id === 'default' ? 'global' : 'tenant')) }}</h3><p>{{ canPublish ? '可提交到租户或全平台；审批通过前不会扩大可见范围。' : selected.pending_publish ? `正在申请 ${selected.pending_publish}` : '当前资源没有可提交的更高可见范围。' }}</p></div><div v-if="canPublish" class="publication-controls"><select v-model="publicationTarget"><option value="tenant">租户可见</option><option value="global">全平台可见</option></select><input v-model="publicationNote" placeholder="申请说明" /><button class="secondary-button" @click="requestPublication"><Link2 :size="15" />提交申请</button></div></section>

          <section v-if="selected && confirmDelete" class="delete-confirmation"><Trash2 :size="18" /><div><strong>永久删除 {{ selected.name }}</strong><p>{{ deleteWarning }}</p><label><span>输入资源名称确认</span><input v-model="deleteConfirmation" :placeholder="selected.name" /></label></div><button class="danger-button" :disabled="deleteConfirmation !== selected.name" @click="removeResource">确认删除</button></section>
        </div>
        <footer class="drawer-footer"><button v-if="selected && !confirmDelete" class="text-button danger-text" @click="confirmDelete = true"><Trash2 :size="15" />删除资源</button><button class="primary-button" :disabled="saving" @click="saveResource"><RefreshCw v-if="saving" :size="15" class="spin" /><Save v-else :size="15" />{{ editorMode === 'create' ? '创建资源' : '保存配置' }}</button></footer>
      </aside>
    </div>
  </div>
</template>
