<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { Archive, Bot, Check, Copy, Eye, EyeOff, Link2, Plus, RefreshCw, Save, Search, Settings2, Trash2, Unlink, X } from '@lucide/vue'
import { ApiError, api, type Agent, type AgentBindings } from '@/api'

const emit = defineEmits<{ notice: [type: 'success' | 'error', text: string] }>()
const loading = ref(false)
const agents = ref<Agent[]>([])
const total = ref(0)
const search = ref('')
const enabledFilter = ref('')
const editorOpen = ref(false)
const editorMode = ref<'create' | 'edit'>('create')
const activeTab = ref<'profile' | 'runtime' | 'bindings'>('profile')
const selectedAgent = ref<Agent | null>(null)
const confirmDelete = ref(false)
const shareResult = ref<any>(null)
const saving = ref(false)

const form = reactive({
  name: '', description: '', avatar: '', welcome_message: '', soul_prompt: '', category: 'general',
  model: '', temperature: 7, max_turns: 100, max_tokens: 4096, kb_mode: 'tool', memory_mode: 'always',
  timeout_seconds: 120, enabled: true, sort_order: 0, example_questions_text: '', expected_updated_at: null as string | null,
})
const bindings = ref<AgentBindings>({ skills: [], knowledge_bases: [], tools: [], mcp_servers: [] })
const options = reactive({ skills: [] as any[], tools: [] as any[], mcpServers: [] as any[], knowledgeBases: [] as any[], models: [] as any[] })
const addBinding = reactive({ skill: '', tool: '', mcp: '', kb: '', skillPriority: 100, kbStrategy: 'hybrid', kbTopK: 5 })

const errorText = (error: unknown) => error instanceof Error ? error.message : '请求失败'
const visibilityLabels = { private: '私有', tenant: '租户共享', global: '全平台' } as const
const visibilityLabel = (agent: Agent) => agent.visibility
  ? visibilityLabels[agent.visibility]
  : (agent.publication_status === 'draft' ? '私有' : '已共享')
const optionName = (kind: string, id: number) => {
  const source = kind === 'skill' ? options.skills : kind === 'tool' ? options.tools : kind === 'mcp' ? options.mcpServers : options.knowledgeBases
  const item = source.find((row: any) => Number(row.id) === Number(id))
  return item?.display_name || item?.name || `#${id}`
}
const filteredAgents = computed(() => agents.value)
const currentVisibility = computed(() => selectedAgent.value ? visibilityLabel(selectedAgent.value) : '私有')

async function loadAgents() {
  loading.value = true
  try {
    const result = await api.agents({ search: search.value || undefined, enabled: enabledFilter.value === '' ? undefined : enabledFilter.value === 'true', page_size: 200 })
    agents.value = result.items
    total.value = result.total
  } catch (error) { emit('notice', 'error', errorText(error)) }
  finally { loading.value = false }
}

function resetForm(agent?: Agent) {
  Object.assign(form, agent ? {
    name: agent.name, description: agent.description, avatar: agent.avatar === '🤖' ? '' : agent.avatar,
    welcome_message: agent.welcome_message, soul_prompt: agent.soul_prompt, category: agent.category,
    model: agent.model, temperature: agent.temperature, max_turns: agent.max_turns, max_tokens: agent.max_tokens,
    kb_mode: agent.kb_mode, memory_mode: agent.memory_mode, timeout_seconds: agent.timeout_seconds,
    enabled: agent.is_enabled, sort_order: agent.sort_order, example_questions_text: agent.example_questions.join('\n'),
    expected_updated_at: agent.updated_at,
  } : {
    name: '', description: '', avatar: '', welcome_message: '', soul_prompt: '', category: 'general', model: options.models[0]?.name || '',
    temperature: 7, max_turns: 100, max_tokens: 4096, kb_mode: 'tool', memory_mode: 'always', timeout_seconds: 120,
    enabled: true, sort_order: 0, example_questions_text: '', expected_updated_at: null,
  })
}

async function ensureOptions() {
  if (options.models.length || options.skills.length) return
  const result = await api.agentOptions()
  Object.assign(options, result)
}

async function createAgent() {
  await ensureOptions()
  editorMode.value = 'create'
  selectedAgent.value = null
  bindings.value = { skills: [], knowledge_bases: [], tools: [], mcp_servers: [] }
  resetForm()
  activeTab.value = 'profile'
  editorOpen.value = true
}

async function editAgent(item: Agent) {
  loading.value = true
  try {
    await ensureOptions()
    const [detail, currentBindings] = await Promise.all([api.agent(item.id), api.agentBindings(item.id)])
    selectedAgent.value = detail
    bindings.value = currentBindings
    resetForm(detail)
    editorMode.value = 'edit'
    activeTab.value = 'profile'
    shareResult.value = null
    confirmDelete.value = false
    editorOpen.value = true
  } catch (error) { emit('notice', 'error', errorText(error)) }
  finally { loading.value = false }
}

function payload() {
  return {
    name: form.name.trim(), description: form.description.trim(), avatar: form.avatar.trim(),
    welcome_message: form.welcome_message.trim(), soul_prompt: form.soul_prompt.trim(), category: form.category.trim() || 'general',
    model: form.model, temperature: Number(form.temperature), max_turns: Number(form.max_turns), max_tokens: Number(form.max_tokens),
    kb_mode: form.kb_mode, memory_mode: form.memory_mode, timeout_seconds: Number(form.timeout_seconds), enabled: form.enabled,
    sort_order: Number(form.sort_order), example_questions: form.example_questions_text.split('\n').map((value) => value.trim()).filter(Boolean),
  }
}

async function saveAgent() {
  if (!form.name.trim()) return emit('notice', 'error', 'Agent 名称不能为空')
  saving.value = true
  try {
    if (editorMode.value === 'create') {
      const created = await api.createAgent(payload())
      emit('notice', 'success', 'Agent 已创建')
      await loadAgents()
      await editAgent(created)
    } else if (selectedAgent.value) {
      const updated = await api.updateAgent(selectedAgent.value.id, { ...payload(), expected_updated_at: form.expected_updated_at })
      selectedAgent.value = updated
      resetForm(updated)
      emit('notice', 'success', 'Agent 配置已保存')
      await loadAgents()
    }
  } catch (error) {
    emit('notice', 'error', error instanceof ApiError && error.status === 409 ? `${error.message}，请关闭编辑器后重新打开` : errorText(error))
  } finally { saving.value = false }
}

async function duplicateAgent(item: Agent) {
  loading.value = true
  try {
    const [detail, sourceBindings] = await Promise.all([api.agent(item.id), api.agentBindings(item.id)])
    const baseName = `${detail.name} 副本`
    let name = baseName
    let index = 2
    while (agents.value.some((agent) => agent.name === name)) name = `${baseName} ${index++}`
    const created = await api.createAgent({
      name, description: detail.description, avatar: detail.avatar === '🤖' ? '' : detail.avatar, welcome_message: detail.welcome_message,
      soul_prompt: detail.soul_prompt, category: detail.category, model: detail.model, temperature: detail.temperature,
      max_turns: detail.max_turns, max_tokens: detail.max_tokens, kb_mode: detail.kb_mode, memory_mode: detail.memory_mode,
      timeout_seconds: detail.timeout_seconds, enabled: false, sort_order: detail.sort_order, example_questions: detail.example_questions,
    })
    const tasks: Promise<unknown>[] = []
    sourceBindings.skills.forEach((row) => tasks.push(api.bindAgentSkill(created.id, row.skill_id, row.priority)))
    sourceBindings.tools.forEach((row) => tasks.push(api.bindAgentTool(created.id, row.tool_id)))
    sourceBindings.mcp_servers.forEach((row) => tasks.push(api.bindAgentMcp(created.id, row.mcp_server_id)))
    sourceBindings.knowledge_bases.forEach((row) => tasks.push(api.bindAgentKb(created.id, row.kb_id, row.strategy, row.top_k)))
    const results = await Promise.allSettled(tasks)
    const failed = results.filter((result) => result.status === 'rejected').length
    emit('notice', failed ? 'error' : 'success', failed ? `副本已创建，但有 ${failed} 项依赖因可见性限制未复制` : 'Agent 与依赖已复制，新副本默认停用')
    await loadAgents()
  } catch (error) { emit('notice', 'error', errorText(error)) }
  finally { loading.value = false }
}

async function toggleEnabled(item: Agent) {
  try {
    const updated = await api.updateAgent(item.id, { enabled: !item.is_enabled, expected_updated_at: item.updated_at })
    Object.assign(item, updated)
    emit('notice', 'success', updated.is_enabled ? 'Agent 已启用' : 'Agent 已停用')
  } catch (error) { emit('notice', 'error', errorText(error)); await loadAgents() }
}

async function changeVisibility() {
  if (!selectedAgent.value) return
  try {
    if (selectedAgent.value.visibility === 'private' || selectedAgent.value.publication_status === 'draft') {
      shareResult.value = await api.shareCheck(selectedAgent.value.id)
      if (!shareResult.value.can_share) return
      const result = await api.shareAgent(selectedAgent.value.id)
      selectedAgent.value = result.agent
      resetForm(result.agent)
      shareResult.value = result
      emit('notice', 'success', 'Agent 已共享；在 default 租户中共享即为全平台可见')
    } else {
      const result = await api.unshareAgent(selectedAgent.value.id)
      selectedAgent.value = result.agent
      resetForm(result.agent)
      shareResult.value = null
      emit('notice', 'success', 'Agent 已撤回为私有')
    }
    await loadAgents()
  } catch (error) { emit('notice', 'error', errorText(error)) }
}

async function removeAgent() {
  if (!selectedAgent.value) return
  try {
    await api.deleteAgent(selectedAgent.value.id)
    editorOpen.value = false
    emit('notice', 'success', 'Agent 已删除')
    await loadAgents()
  } catch (error) { emit('notice', 'error', errorText(error)) }
}

async function bind(kind: 'skill' | 'tool' | 'mcp' | 'kb') {
  if (!selectedAgent.value) return
  const raw = addBinding[kind]
  if (!raw) return
  const id = Number(raw)
  try {
    if (kind === 'skill') await api.bindAgentSkill(selectedAgent.value.id, id, Number(addBinding.skillPriority))
    if (kind === 'tool') await api.bindAgentTool(selectedAgent.value.id, id)
    if (kind === 'mcp') await api.bindAgentMcp(selectedAgent.value.id, id)
    if (kind === 'kb') await api.bindAgentKb(selectedAgent.value.id, id, addBinding.kbStrategy, Number(addBinding.kbTopK))
    addBinding[kind] = ''
    bindings.value = await api.agentBindings(selectedAgent.value.id)
    emit('notice', 'success', '依赖已绑定')
  } catch (error) { emit('notice', 'error', errorText(error)) }
}

async function unbind(kind: 'skill' | 'tool' | 'mcp' | 'kb', id: number) {
  if (!selectedAgent.value) return
  try {
    if (kind === 'skill') await api.unbindAgentSkill(selectedAgent.value.id, id)
    if (kind === 'tool') await api.unbindAgentTool(selectedAgent.value.id, id)
    if (kind === 'mcp') await api.unbindAgentMcp(selectedAgent.value.id, id)
    if (kind === 'kb') await api.unbindAgentKb(selectedAgent.value.id, id)
    bindings.value = await api.agentBindings(selectedAgent.value.id)
    emit('notice', 'success', '依赖已解绑')
  } catch (error) { emit('notice', 'error', errorText(error)) }
}

onMounted(loadAgents)
</script>

<template>
  <div class="agent-manager">
    <div class="toolbar agent-toolbar">
      <div class="search-field"><Search :size="16" /><input v-model="search" placeholder="搜索 Agent 名称" @keyup.enter="loadAgents" /></div>
      <select v-model="enabledFilter" @change="loadAgents"><option value="">全部状态</option><option value="true">已启用</option><option value="false">已停用</option></select>
      <button class="secondary-button" :disabled="loading" @click="loadAgents"><RefreshCw :size="15" :class="{ spin: loading }" />刷新</button>
      <span class="result-count">{{ total }} 个 Agent</span>
      <button class="primary-button" @click="createAgent"><Plus :size="16" />新建 Agent</button>
    </div>

    <div class="agent-grid">
      <article v-for="item in filteredAgents" :key="item.id" class="agent-card" @click="editAgent(item)">
        <div class="agent-card-head"><span class="agent-avatar">{{ item.avatar || 'A' }}</span><div><strong>{{ item.name }}</strong><span class="mono">#{{ item.id }} · {{ item.tenant_id || '未标记租户' }}</span></div><span :class="`status status-${item.is_enabled ? 'active' : 'disabled'}`">{{ item.is_enabled ? '启用' : '停用' }}</span></div>
        <p>{{ item.description || '暂无描述' }}</p>
        <div class="agent-card-meta"><span>{{ visibilityLabel(item) }}</span><span>{{ item.model || '默认模型' }}</span><span>{{ item.max_turns }} turns</span></div>
        <div class="agent-card-actions" @click.stop><button class="small-button" @click="editAgent(item)"><Settings2 :size="14" />编辑</button><button class="icon-button" title="复制" @click="duplicateAgent(item)"><Copy :size="15" /></button><button class="icon-button" :title="item.is_enabled ? '停用' : '启用'" @click="toggleEnabled(item)"><EyeOff v-if="item.is_enabled" :size="15" /><Eye v-else :size="15" /></button></div>
      </article>
      <div v-if="!agents.length && !loading" class="empty agent-empty"><Bot :size="28" /><strong>没有 Agent</strong><span>创建第一个可治理的 Agent</span></div>
    </div>

    <div v-if="editorOpen" class="drawer-backdrop" @click.self="editorOpen = false">
      <aside class="agent-drawer">
        <header class="drawer-header"><div><span class="eyebrow">{{ editorMode === 'create' ? 'NEW AGENT' : `AGENT #${selectedAgent?.id}` }}</span><h2>{{ editorMode === 'create' ? '新建 Agent' : selectedAgent?.name }}</h2></div><button class="icon-button" @click="editorOpen = false"><X :size="18" /></button></header>
        <div class="drawer-tabs"><button :class="{ active: activeTab === 'profile' }" @click="activeTab = 'profile'">基本信息</button><button :class="{ active: activeTab === 'runtime' }" @click="activeTab = 'runtime'">运行参数</button><button :disabled="editorMode === 'create'" :class="{ active: activeTab === 'bindings' }" @click="activeTab = 'bindings'">能力绑定</button></div>

        <div class="drawer-body">
          <template v-if="activeTab === 'profile'">
            <div class="form-grid"><label><span>名称</span><input v-model="form.name" maxlength="100" /></label><label><span>分类</span><input v-model="form.category" /></label><label class="full-field"><span>描述</span><textarea v-model="form.description" rows="3"></textarea></label><label class="full-field"><span>欢迎语</span><textarea v-model="form.welcome_message" rows="3"></textarea></label><label class="full-field"><span>系统 Prompt</span><textarea v-model="form.soul_prompt" class="prompt-editor" rows="12"></textarea></label><label class="full-field"><span>示例问题，每行一个</span><textarea v-model="form.example_questions_text" rows="4"></textarea></label><label><span>头像文本或 URL</span><input v-model="form.avatar" /></label><label><span>排序值</span><input v-model.number="form.sort_order" type="number" /></label></div>
            <section v-if="selectedAgent" class="governance-panel"><div><span class="eyebrow">VISIBILITY</span><h3>{{ currentVisibility }}</h3><p>{{ selectedAgent.visibility === 'private' || selectedAgent.publication_status === 'draft' ? '共享前会检查私有 Skill、Tool、MCP 和专家团成员。' : '撤回共享会立即影响其他使用者。' }}</p></div><button class="secondary-button" @click="changeVisibility"><Link2 v-if="selectedAgent.visibility === 'private' || selectedAgent.publication_status === 'draft'" :size="15" /><Unlink v-else :size="15" />{{ selectedAgent.visibility === 'private' || selectedAgent.publication_status === 'draft' ? '检查并共享' : '撤回共享' }}</button></section>
            <div v-if="shareResult && (!shareResult.can_share || shareResult.warnings?.length)" class="share-report"><strong>{{ shareResult.can_share === false ? '共享被阻止' : '共享完成，但有提示' }}</strong><p v-for="item in [...(shareResult.blockers || []), ...(shareResult.warnings || [])]" :key="`${item.kind}-${item.id}`">{{ item.kind }} · {{ item.name }}：{{ item.reason }}</p></div>
          </template>

          <template v-else-if="activeTab === 'runtime'">
            <div class="form-grid"><label><span>模型</span><select v-model="form.model"><option value="">使用默认模型</option><option v-for="item in options.models" :key="item.name" :value="item.name">{{ item.name }} · {{ item.provider }}</option></select></label><label><span>Temperature × 10</span><input v-model.number="form.temperature" type="number" min="0" max="20" /></label><label><span>最大轮次</span><input v-model.number="form.max_turns" type="number" min="1" /></label><label><span>最大 Token</span><input v-model.number="form.max_tokens" type="number" min="256" /></label><label><span>超时秒数</span><input v-model.number="form.timeout_seconds" type="number" min="10" /></label><label><span>知识库模式</span><select v-model="form.kb_mode"><option value="tool">工具调用</option><option value="always">始终检索</option><option value="off">关闭</option></select></label><label><span>记忆模式</span><select v-model="form.memory_mode"><option value="always">始终启用</option><option value="off">关闭</option></select></label><label class="toggle-field"><input v-model="form.enabled" type="checkbox" /><span>启用 Agent</span></label></div>
          </template>

          <template v-else>
            <section class="binding-section"><div class="binding-heading"><div><h3>Skills</h3><span>{{ bindings.skills.length }} 项</span></div><div class="binding-add"><select v-model="addBinding.skill"><option value="">选择 Skill</option><option v-for="item in options.skills" :key="item.id" :value="item.id">{{ item.display_name || item.name }} · {{ item.visibility }}</option></select><input v-model.number="addBinding.skillPriority" type="number" min="0" title="优先级" /><button class="icon-button" title="绑定" @click="bind('skill')"><Plus :size="16" /></button></div></div><div class="binding-list"><div v-for="row in bindings.skills" :key="row.skill_id"><span><strong>{{ optionName('skill', row.skill_id) }}</strong><small>优先级 {{ row.priority }}</small></span><button class="icon-button danger" title="解绑" @click="unbind('skill', row.skill_id)"><Trash2 :size="15" /></button></div><p v-if="!bindings.skills.length">尚未绑定 Skill</p></div></section>
            <section class="binding-section"><div class="binding-heading"><div><h3>Tools</h3><span>{{ bindings.tools.length }} 项</span></div><div class="binding-add"><select v-model="addBinding.tool"><option value="">选择 Tool</option><option v-for="item in options.tools" :key="item.id" :value="item.id">{{ item.display_name || item.name }} · {{ item.tenant_id }}</option></select><button class="icon-button" title="绑定" @click="bind('tool')"><Plus :size="16" /></button></div></div><div class="binding-list"><div v-for="row in bindings.tools" :key="row.tool_id"><span><strong>{{ optionName('tool', row.tool_id) }}</strong></span><button class="icon-button danger" title="解绑" @click="unbind('tool', row.tool_id)"><Trash2 :size="15" /></button></div><p v-if="!bindings.tools.length">尚未绑定 Tool</p></div></section>
            <section class="binding-section"><div class="binding-heading"><div><h3>MCP Servers</h3><span>{{ bindings.mcp_servers.length }} 项</span></div><div class="binding-add"><select v-model="addBinding.mcp"><option value="">选择 MCP Server</option><option v-for="item in options.mcpServers" :key="item.id" :value="item.id">{{ item.name }} · {{ item.connection_status }}</option></select><button class="icon-button" title="绑定" @click="bind('mcp')"><Plus :size="16" /></button></div></div><div class="binding-list"><div v-for="row in bindings.mcp_servers" :key="row.mcp_server_id"><span><strong>{{ optionName('mcp', row.mcp_server_id) }}</strong></span><button class="icon-button danger" title="解绑" @click="unbind('mcp', row.mcp_server_id)"><Trash2 :size="15" /></button></div><p v-if="!bindings.mcp_servers.length">尚未绑定 MCP Server</p></div></section>
            <section class="binding-section"><div class="binding-heading"><div><h3>知识库</h3><span>{{ bindings.knowledge_bases.length }} 项</span></div><div class="binding-add kb-add"><select v-model="addBinding.kb"><option value="">选择知识库</option><option v-for="item in options.knowledgeBases" :key="item.id" :value="item.id">{{ item.name }} · {{ item.visibility }}</option></select><select v-model="addBinding.kbStrategy"><option value="hybrid">混合</option><option value="semantic">语义</option><option value="keyword">关键词</option></select><input v-model.number="addBinding.kbTopK" type="number" min="1" max="50" title="Top K" /><button class="icon-button" title="绑定" @click="bind('kb')"><Plus :size="16" /></button></div></div><div class="binding-list"><div v-for="row in bindings.knowledge_bases" :key="row.kb_id"><span><strong>{{ optionName('kb', row.kb_id) }}</strong><small>{{ row.strategy }} · Top {{ row.top_k }}</small></span><button class="icon-button danger" title="解绑" @click="unbind('kb', row.kb_id)"><Trash2 :size="15" /></button></div><p v-if="!bindings.knowledge_bases.length">尚未绑定知识库</p></div></section>
          </template>
        </div>

        <footer class="drawer-footer"><div v-if="editorMode === 'edit'" class="delete-zone"><button v-if="!confirmDelete" class="text-button danger-text" @click="confirmDelete = true"><Archive :size="15" />删除 Agent</button><template v-else><span>确认永久删除？</span><button class="danger-button" @click="removeAgent"><Trash2 :size="14" />删除</button><button class="secondary-button" @click="confirmDelete = false">取消</button></template></div><button class="primary-button" :disabled="saving" @click="saveAgent"><RefreshCw v-if="saving" :size="15" class="spin" /><Save v-else :size="15" />{{ editorMode === 'create' ? '创建 Agent' : '保存配置' }}</button></footer>
      </aside>
    </div>
  </div>
</template>
