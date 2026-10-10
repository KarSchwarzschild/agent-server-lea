<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Activity, Archive, Bot, Boxes, Check, ChevronRight, CircleGauge, Database,
  FileClock, FileText, History, KeyRound, Library, LogOut, Menu, Play,
  RefreshCw, RotateCcw, Search, ServerCog, Settings, ShieldCheck, SlidersHorizontal,
  Square, X, XCircle, Zap,
} from '@lucide/vue'
import { ApiError, api, authStore, type AuditEvent, type ConsoleContext, type DocumentJob, type Provider, type Publication, type PublicationRequest, type Run } from './api'
import AgentManager from './components/AgentManager.vue'
import ResourceManager from './components/ResourceManager.vue'

type Section = 'overview' | 'approvals' | 'resources' | 'operations' | 'providers' | 'quotas' | 'audit'
type Notice = { type: 'success' | 'error'; text: string }

const route = useRoute()
const router = useRouter()
const context = ref<ConsoleContext | null>(null)
const loading = ref(false)
const fatalError = ref('')
const notice = ref<Notice | null>(null)
const mobileNav = ref(false)
const showCredentials = ref(false)
const tokenInput = ref(authStore.token)
const tenantInput = ref(authStore.tenantId)
const isDevelopment = import.meta.env.DEV
const activeSection = computed<Section>(() => (String(route.params.section || 'overview') as Section))

const nav = [
  { id: 'overview', label: '工作台', icon: CircleGauge },
  { id: 'approvals', label: '审批中心', icon: ShieldCheck },
  { id: 'resources', label: '资源治理', icon: Boxes },
  { id: 'operations', label: '运行与任务', icon: Activity },
  { id: 'providers', label: '模型 Provider', icon: ServerCog },
  { id: 'quotas', label: '用量与配额', icon: SlidersHorizontal },
  { id: 'audit', label: '审计日志', icon: History },
] as const

const sectionMeta = computed(() => nav.find((item) => item.id === activeSection.value) || nav[0])
const toast = (type: Notice['type'], text: string) => {
  notice.value = { type, text }
  window.setTimeout(() => { notice.value = null }, 3500)
}
const errorText = (error: unknown) => error instanceof Error ? error.message : '请求失败'
const formatTime = (value?: string) => value ? new Intl.DateTimeFormat('zh-CN', { dateStyle: 'short', timeStyle: 'medium' }).format(new Date(value)) : '-'
const formatBytes = (value = 0) => value < 1024 * 1024 ? `${Math.round(value / 1024)} KB` : `${(value / 1024 / 1024).toFixed(1)} MB`
const resourceLabel = (value: string) => ({ agent: 'Agent', skill: 'Skill', tool: 'Tool', mcp_server: 'MCP', knowledge_base: '知识库' }[value] || value)
const statusClass = (status = '') => `status status-${status.toLowerCase().replace(/_/g, '-')}`

async function fetchLocalTestIdentity() {
  const response = await fetch('/__dev/console-session')
  const result = await response.json()
  if (!response.ok) throw new Error(result.error || '无法生成本地测试身份')
  tokenInput.value = result.token
  tenantInput.value = result.tenant_id
  authStore.save(result.token, result.tenant_id)
}

async function bootstrap(retryWithLocalIdentity = true) {
  if (!authStore.token || !authStore.tenantId) {
    showCredentials.value = true
    return
  }
  loading.value = true
  fatalError.value = ''
  try {
    context.value = await api.context()
    showCredentials.value = false
    await loadSection()
  } catch (error) {
    if (isDevelopment && retryWithLocalIdentity && error instanceof ApiError && error.status === 401) {
      authStore.clear()
      try {
        await fetchLocalTestIdentity()
        await bootstrap(false)
        return
      } catch (localError) {
        fatalError.value = errorText(localError)
      }
    } else {
      fatalError.value = errorText(error)
    }
    if (error instanceof ApiError && [401, 403].includes(error.status)) showCredentials.value = true
  } finally {
    loading.value = false
  }
}

async function saveCredentials() {
  authStore.save(tokenInput.value, tenantInput.value)
  await bootstrap()
}
async function useLocalTestIdentity() {
  loading.value = true
  fatalError.value = ''
  try {
    await fetchLocalTestIdentity()
    await bootstrap(false)
  } catch (error) {
    fatalError.value = errorText(error)
  } finally {
    loading.value = false
  }
}
function clearCredentials() {
  authStore.clear()
  context.value = null
  tokenInput.value = ''
  tenantInput.value = ''
  showCredentials.value = true
}
function navigate(section: string) {
  mobileNav.value = false
  router.push(`/${section}`)
}

const overview = reactive({ pending: 0, active: 0, runIssues: 0, documentIssues: 0, recentAudits: [] as AuditEvent[] })
async function loadOverview() {
  const [requests, publications, runs, docs, audits] = await Promise.all([
    api.requests({ status: 'pending', size: 1 }), api.publications({ status: 'active', size: 1 }),
    api.runs({ status: 'failed', size: 1 }), api.documentJobs({ status: 'failed', size: 1 }), api.audits({ size: 6 }),
  ])
  overview.pending = requests.total
  overview.active = publications.total
  overview.runIssues = runs.total
  overview.documentIssues = docs.total
  overview.recentAudits = audits.items || []
}

const approvalFilters = reactive({ status: 'pending', resource_type: '', target_scope: '' })
const approvals = ref<PublicationRequest[]>([])
const approvalTotal = ref(0)
async function loadApprovals() {
  const result = await api.requests({ ...approvalFilters, size: 100 })
  approvals.value = result.requests || []
  approvalTotal.value = result.total
}

const publications = ref<Publication[]>([])
const publicationFilters = reactive({ resource_type: '', status: '' })
const resourceTab = ref('catalog')
const catalogType = ref('agents')
async function loadPublications() {
  const result = await api.publications({ ...publicationFilters, size: 100 })
  publications.value = result.items || []
}

const operationTab = ref<'runs' | 'documents'>('runs')
const operationStatus = ref('')
const runs = ref<Run[]>([])
const documents = ref<DocumentJob[]>([])
async function loadOperations() {
  if (operationTab.value === 'runs') {
    const result = await api.runs({ status: operationStatus.value || undefined, size: 100 })
    runs.value = result.items || []
  } else {
    const result = await api.documentJobs({ status: operationStatus.value || undefined, size: 100 })
    documents.value = result.items || []
  }
}

const providers = ref<Provider[]>([])
const providerEditor = ref(false)
const editingProvider = ref<Provider | null>(null)
const providerForm = reactive({ name: '', base_url: '', api_key: '', models: '', enabled: true, is_default: false, reason: '' })
async function loadProviders() { providers.value = (await api.providers()).items }
function openProvider(item?: Provider) {
  editingProvider.value = item || null
  Object.assign(providerForm, item ? { ...item, api_key: '', models: item.models.join(', '), reason: '' } : { name: '', base_url: '', api_key: '', models: '', enabled: true, is_default: false, reason: '' })
  providerEditor.value = true
}
async function saveProvider() {
  if (!providerForm.reason.trim()) return toast('error', '请填写变更原因')
  const payload = { ...providerForm }
  if (editingProvider.value && !payload.api_key) delete (payload as Partial<typeof payload>).api_key
  try {
    if (editingProvider.value) await api.updateProvider(editingProvider.value.id, payload)
    else await api.createProvider(payload)
    providerEditor.value = false
    toast('success', editingProvider.value ? 'Provider 已更新' : 'Provider 已创建')
    await loadProviders()
  } catch (error) { toast('error', errorText(error)) }
}

const audits = ref<AuditEvent[]>([])
const auditTotal = ref(0)
const auditFilters = reactive({ action: '', resource_type: '', result: '' })
async function loadAudits() {
  const result = await api.audits({ ...auditFilters, size: 100 })
  audits.value = result.items || []
  auditTotal.value = result.total
}

const quotaUserId = ref('')
const quotas = ref<any>(null)
const quotaReason = ref('')
async function loadQuotas() { quotas.value = await api.quotas(quotaUserId.value || undefined) }
async function saveQuota(scope: 'tenant' | 'user') {
  if (!quotaReason.value.trim()) return toast('error', '请填写调整原因')
  try {
    if (scope === 'tenant') await api.updateTenantQuota({ ...quotas.value.tenant, reason: quotaReason.value })
    else if (quotaUserId.value && quotas.value.user) await api.updateUserQuota(quotaUserId.value, { ...quotas.value.user, reason: quotaReason.value })
    toast('success', '配额已更新')
    quotaReason.value = ''
    await loadQuotas()
  } catch (error) { toast('error', errorText(error)) }
}

const actionDialog = reactive({ open: false, title: '', description: '', confirmLabel: '确认', danger: false, reason: '', execute: null as null | ((reason: string) => Promise<unknown>) })
function requestAction(options: { title: string; description: string; confirmLabel?: string; danger?: boolean; execute: (reason: string) => Promise<unknown> }) {
  Object.assign(actionDialog, { open: true, reason: '', confirmLabel: '确认', danger: false, ...options })
}
async function confirmAction() {
  if (!actionDialog.reason.trim()) return toast('error', '请填写操作原因')
  if (!actionDialog.execute) return
  try {
    await actionDialog.execute(actionDialog.reason)
    actionDialog.open = false
    toast('success', '操作已完成')
    await loadSection()
  } catch (error) { toast('error', errorText(error)) }
}
function review(item: PublicationRequest, decision: 'approved' | 'rejected') {
  requestAction({
    title: decision === 'approved' ? '通过发布申请' : '驳回发布申请',
    description: `${resourceLabel(item.resource_type)} #${item.resource_id} 将${decision === 'approved' ? `发布到${item.target_scope === 'global' ? '全平台' : '当前租户'}` : '保持当前可见范围'}。`,
    confirmLabel: decision === 'approved' ? '确认通过' : '确认驳回', danger: decision === 'rejected',
    execute: (reason) => api.review(item.id, decision, reason),
  })
}
function transition(item: Publication, action: 'suspend' | 'resume' | 'archive') {
  const labels = { suspend: '暂停资源', resume: '恢复资源', archive: '归档资源' }
  requestAction({ title: labels[action], description: action === 'archive' ? '归档不可恢复，并会禁用运行时资源。' : `该操作将立即影响 ${resourceLabel(item.resource_type)} #${item.resource_id} 的可用状态。`, confirmLabel: labels[action], danger: action !== 'resume', execute: (reason) => api.transition(item, action, reason) })
}
function operationAction(kind: 'run' | 'document', id: string | number, action: 'cancel' | 'retry') {
  requestAction({ title: action === 'cancel' ? '取消任务' : '重试任务', description: action === 'retry' ? '系统会创建新尝试或清理旧索引后重新执行，原记录仍保留。' : '只有处于可安全取消状态的任务才会被处理。', confirmLabel: action === 'cancel' ? '确认取消' : '确认重试', danger: action === 'cancel', execute: (reason) => kind === 'run' ? api.runAction(String(id), action, reason) : api.documentAction(Number(id), action, reason) })
}
async function testProvider(item: Provider) {
  try { const result = await api.testProvider(item.id); toast(result.ok ? 'success' : 'error', result.message) } catch (error) { toast('error', errorText(error)) }
}
function removeProvider(item: Provider) {
  requestAction({ title: '删除 Provider', description: '只有已停用且非默认的 Provider 可以删除，此操作不可恢复。', confirmLabel: '确认删除', danger: true, execute: (reason) => api.deleteProvider(item.id, reason) })
}

async function loadSection() {
  if (!context.value) return
  loading.value = true
  fatalError.value = ''
  try {
    if (activeSection.value === 'overview') await loadOverview()
    if (activeSection.value === 'approvals') await loadApprovals()
    if (activeSection.value === 'resources' && resourceTab.value === 'publications') await loadPublications()
    if (activeSection.value === 'operations') await loadOperations()
    if (activeSection.value === 'providers') await loadProviders()
    if (activeSection.value === 'quotas') await loadQuotas()
    if (activeSection.value === 'audit') await loadAudits()
  } catch (error) { fatalError.value = errorText(error) }
  finally { loading.value = false }
}

watch(() => route.params.section, loadSection)
watch([operationTab, operationStatus], loadOperations)
watch(resourceTab, () => activeSection.value === 'resources' && loadSection())
onMounted(bootstrap)
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar" :class="{ open: mobileNav }">
      <div class="brand">
        <div class="brand-mark"><Zap :size="19" /></div>
        <div><strong>LEA Console</strong><span>治理控制台</span></div>
      </div>
      <nav>
        <button v-for="item in nav" :key="item.id" :class="{ active: activeSection === item.id }" @click="navigate(item.id)">
          <component :is="item.icon" :size="18" /><span>{{ item.label }}</span><ChevronRight :size="15" class="nav-arrow" />
        </button>
      </nav>
      <div class="sidebar-foot" v-if="context">
        <div class="scope-dot"></div>
        <div><strong>{{ context.role === 'platform_admin' ? '平台管理员' : '租户管理员' }}</strong><span>{{ context.tenant_id }}</span></div>
        <button class="icon-button" title="连接设置" @click="showCredentials = true"><Settings :size="17" /></button>
      </div>
    </aside>

    <main>
      <header class="topbar">
        <button class="icon-button menu-button" title="打开导航" @click="mobileNav = !mobileNav"><Menu :size="20" /></button>
        <div><span class="eyebrow">LEA PLATFORM / GOVERNANCE</span><h1>{{ sectionMeta.label }}</h1></div>
        <div class="top-actions">
          <span class="environment"><i></i> TEST</span>
          <button class="icon-button" title="刷新" :disabled="loading" @click="loadSection"><RefreshCw :size="18" :class="{ spin: loading }" /></button>
        </div>
      </header>

      <div class="content">
        <div v-if="fatalError" class="error-banner"><XCircle :size="18" /><span>{{ fatalError }}</span><button @click="loadSection">重试</button></div>

        <template v-if="activeSection === 'overview'">
          <section class="summary-grid">
            <button class="metric amber" @click="navigate('approvals')"><span>待处理审批</span><strong>{{ overview.pending }}</strong><ShieldCheck :size="21" /></button>
            <button class="metric green" @click="navigate('resources')"><span>已发布资源</span><strong>{{ overview.active }}</strong><Library :size="21" /></button>
            <button class="metric red" @click="navigate('operations')"><span>失败运行</span><strong>{{ overview.runIssues }}</strong><Activity :size="21" /></button>
            <button class="metric blue" @click="navigate('operations')"><span>失败文档任务</span><strong>{{ overview.documentIssues }}</strong><FileClock :size="21" /></button>
          </section>
          <section class="section-block">
            <div class="section-heading"><div><h2>最近管理操作</h2><p>所有高风险操作均保留不可变审计记录</p></div><button class="text-button" @click="navigate('audit')">查看全部 <ChevronRight :size="16" /></button></div>
            <div class="timeline" v-if="overview.recentAudits.length">
              <div v-for="event in overview.recentAudits" :key="event.event_id" class="timeline-row">
                <span :class="statusClass(event.result)">{{ event.result }}</span><strong>{{ event.action }}</strong><span>{{ resourceLabel(event.resource_type) }} · {{ event.resource_id }}</span><time>{{ formatTime(event.created_at) }}</time>
              </div>
            </div>
            <div v-else class="empty"><History :size="28" /><strong>暂无审计记录</strong><span>完成管理操作后会显示在这里</span></div>
          </section>
        </template>

        <template v-else-if="activeSection === 'approvals'">
          <div class="toolbar">
            <select v-model="approvalFilters.status" @change="loadApprovals"><option value="pending">待审批</option><option value="approved">已通过</option><option value="rejected">已驳回</option><option value="cancelled">已取消</option></select>
            <select v-model="approvalFilters.resource_type" @change="loadApprovals"><option value="">全部资源</option><option value="agent">Agent</option><option value="skill">Skill</option><option value="tool">Tool</option><option value="mcp_server">MCP</option><option value="knowledge_base">知识库</option></select>
            <select v-model="approvalFilters.target_scope" @change="loadApprovals"><option value="">全部范围</option><option value="tenant">当前租户</option><option value="global">全平台</option></select>
            <span class="result-count">{{ approvalTotal }} 条申请</span>
          </div>
          <div class="table-wrap">
            <table><thead><tr><th>申请</th><th>目标范围</th><th>申请人</th><th>风险信息</th><th>时间</th><th></th></tr></thead>
              <tbody><tr v-for="item in approvals" :key="item.id">
                <td><div class="primary-cell"><span class="resource-icon">{{ resourceLabel(item.resource_type).slice(0, 1) }}</span><div><strong>{{ resourceLabel(item.resource_type) }} #{{ item.resource_id }}</strong><span>版本 {{ item.resource_version || '-' }} · 申请 #{{ item.id }}</span></div></div></td>
                <td><span :class="statusClass(item.target_scope)">{{ item.target_scope === 'global' ? '全平台' : '当前租户' }}</span></td><td>{{ item.requested_by }}</td>
                <td class="muted">{{ Object.keys(item.risk_snapshot || {}).length ? `${Object.keys(item.risk_snapshot).length} 项检查` : '无风险项' }}</td><td>{{ formatTime(item.created_at) }}</td>
                <td class="actions"><template v-if="item.status === 'pending'"><button class="icon-button approve" title="通过" @click="review(item, 'approved')"><Check :size="17" /></button><button class="icon-button danger" title="驳回" @click="review(item, 'rejected')"><X :size="17" /></button></template><span v-else :class="statusClass(item.status)">{{ item.status }}</span></td>
              </tr></tbody>
            </table><div v-if="!approvals.length" class="empty"><ShieldCheck :size="28" /><strong>当前筛选下没有申请</strong></div>
          </div>
        </template>

        <template v-else-if="activeSection === 'resources'">
          <div class="segmented"><button :class="{ active: resourceTab === 'publications' }" @click="resourceTab = 'publications'">发布状态</button><button :class="{ active: resourceTab === 'catalog' }" @click="resourceTab = 'catalog'">资源目录</button></div>
          <div v-if="resourceTab === 'publications'" class="toolbar"><select v-model="publicationFilters.resource_type" @change="loadPublications"><option value="">全部资源</option><option value="agent">Agent</option><option value="skill">Skill</option><option value="tool">Tool</option><option value="mcp_server">MCP</option><option value="knowledge_base">知识库</option></select><select v-model="publicationFilters.status" @change="loadPublications"><option value="">全部状态</option><option value="active">已发布</option><option value="suspended">已暂停</option><option value="archived">已归档</option></select></div>
          <div v-else class="toolbar resource-kind-toolbar"><select v-model="catalogType"><option value="agents">Agent</option><option value="skills">Skill</option><option value="tools">Tool</option><option value="mcp-servers">MCP Server</option><option value="knowledge-bases">知识库</option></select></div>
          <div class="table-wrap" v-if="resourceTab === 'publications'"><table><thead><tr><th>资源</th><th>范围</th><th>状态</th><th>租户</th><th>最后更新</th><th></th></tr></thead><tbody><tr v-for="item in publications" :key="item.id"><td><strong>{{ resourceLabel(item.resource_type) }} #{{ item.resource_id }}</strong></td><td>{{ item.scope }}</td><td><span :class="statusClass(item.status)">{{ item.status }}</span></td><td class="mono">{{ item.tenant_id }}</td><td>{{ formatTime(item.updated_at) }}</td><td class="actions"><button v-if="item.status === 'active'" class="small-button warning" @click="transition(item, 'suspend')"><Square :size="14" />暂停</button><button v-if="item.status === 'suspended'" class="small-button" @click="transition(item, 'resume')"><Play :size="14" />恢复</button><button v-if="item.status !== 'archived'" class="icon-button danger" title="归档" @click="transition(item, 'archive')"><Archive :size="16" /></button></td></tr></tbody></table><div v-if="!publications.length" class="empty"><Library :size="28" /><strong>没有发布记录</strong></div></div>
          <AgentManager v-else-if="catalogType === 'agents'" @notice="toast" />
          <ResourceManager v-else :kind="catalogType" @notice="toast" />
        </template>

        <template v-else-if="activeSection === 'operations'">
          <div class="toolbar"><div class="segmented compact"><button :class="{ active: operationTab === 'runs' }" @click="operationTab = 'runs'">Agent Run</button><button :class="{ active: operationTab === 'documents' }" @click="operationTab = 'documents'">文档任务</button></div><select v-model="operationStatus"><option value="">全部状态</option><option value="running">运行中</option><option value="queued">排队中</option><option value="failed">失败</option><option value="error">错误</option><option value="canceled">已取消</option><option value="completed">已完成</option></select></div>
          <div class="table-wrap" v-if="operationTab === 'runs'"><table><thead><tr><th>Run</th><th>会话 / Agent</th><th>状态</th><th>Attempt</th><th>更新时间</th><th></th></tr></thead><tbody><tr v-for="item in runs" :key="item.id || item.run_id"><td class="mono"><strong>{{ item.id || item.run_id }}</strong></td><td><span>{{ item.session_id || '-' }}</span><span class="cell-note">Agent {{ item.agent_id || '-' }}</span></td><td><span :class="statusClass(item.status)">{{ item.status }}</span></td><td>{{ item.attempt || item.run_attempt || 1 }}</td><td>{{ formatTime(item.updated_at || item.created_at) }}</td><td class="actions"><button v-if="['queued','running','cancelling'].includes(item.status)" class="small-button warning" @click="operationAction('run', item.id || item.run_id, 'cancel')"><Square :size="14" />取消</button><button v-if="['failed','canceled','cancelled'].includes(item.status)" class="small-button" @click="operationAction('run', item.id || item.run_id, 'retry')"><RotateCcw :size="14" />重试</button></td></tr></tbody></table><div v-if="!runs.length" class="empty"><Bot :size="28" /><strong>没有运行记录</strong></div></div>
          <div class="table-wrap" v-else><table><thead><tr><th>文档</th><th>知识库</th><th>状态</th><th>分块 / 大小</th><th>更新时间</th><th></th></tr></thead><tbody><tr v-for="item in documents" :key="item.id"><td><strong>{{ item.filename }}</strong><span class="cell-note mono">#{{ item.id }} · {{ item.error_message }}</span></td><td>#{{ item.kb_id }}</td><td><span :class="statusClass(item.status)">{{ item.status }}</span></td><td>{{ item.chunk_count }} chunks · {{ formatBytes(item.file_size) }}</td><td>{{ formatTime(item.updated_at) }}</td><td class="actions"><button v-if="item.status === 'processing'" class="small-button warning" @click="operationAction('document', item.id, 'cancel')"><Square :size="14" />取消</button><button v-if="['failed','error','canceled'].includes(item.status)" class="small-button" @click="operationAction('document', item.id, 'retry')"><RotateCcw :size="14" />重试</button></td></tr></tbody></table><div v-if="!documents.length" class="empty"><FileText :size="28" /><strong>没有文档任务</strong></div></div>
        </template>

        <template v-else-if="activeSection === 'providers'">
          <div class="toolbar align-end"><div><h2>模型接入点</h2><p>密钥只可写入或替换，不会回显</p></div><button class="primary-button" @click="openProvider()"><ServerCog :size="16" />新增 Provider</button></div>
          <div class="provider-list"><article v-for="item in providers" :key="item.id" class="provider-row"><div class="provider-symbol"><ServerCog :size="21" /></div><div class="provider-main"><div><strong>{{ item.name }}</strong><span v-if="item.is_default" class="status status-default">默认</span><span :class="statusClass(item.enabled ? 'active' : 'disabled')">{{ item.enabled ? '启用' : '停用' }}</span></div><code>{{ item.base_url }}</code><p>{{ item.models.length ? item.models.join(' · ') : '未配置模型' }}</p></div><div class="credential-state"><KeyRound :size="15" /><span>{{ item.has_api_key ? '密钥已配置' : '无密钥' }}</span></div><div class="actions"><button class="small-button" @click="testProvider(item)"><Activity :size="14" />测试</button><button class="small-button" @click="openProvider(item)"><Settings :size="14" />编辑</button><button class="icon-button danger" title="删除" @click="removeProvider(item)"><Archive :size="16" /></button></div></article><div v-if="!providers.length" class="empty"><ServerCog :size="28" /><strong>尚未配置 Provider</strong></div></div>
        </template>

        <template v-else-if="activeSection === 'quotas'">
          <div class="toolbar"><div class="search-field"><Search :size="16" /><input v-model="quotaUserId" placeholder="输入用户 ID 查看用户配额" @keyup.enter="loadQuotas" /></div><button class="secondary-button" @click="loadQuotas">查询</button></div>
          <div class="quota-layout" v-if="quotas"><section class="quota-section"><div class="section-heading"><div><h2>租户解析配额</h2><p>{{ context?.tenant_id }}</p></div></div><div class="form-grid"><label v-for="key in ['max_calls_per_hour','max_file_size_mb','max_monthly_calls','max_monthly_size_mb']" :key="key"><span>{{ key }}</span><input v-model.number="quotas.tenant[key]" type="number" min="0" /></label></div><label class="reason-field"><span>调整原因</span><textarea v-model="quotaReason" placeholder="该原因将写入审计日志"></textarea></label><button class="primary-button" @click="saveQuota('tenant')">保存租户配额</button></section><section class="quota-section" v-if="quotas.user"><div class="section-heading"><div><h2>用户解析配额</h2><p>{{ quotaUserId }}</p></div></div><div class="form-grid"><label v-for="key in ['max_calls_per_hour','max_file_size_mb','max_monthly_calls']" :key="key"><span>{{ key }}</span><input v-model.number="quotas.user[key]" type="number" min="0" /></label></div><button class="secondary-button" @click="saveQuota('user')">保存用户配额</button></section></div>
        </template>

        <template v-else-if="activeSection === 'audit'">
          <div class="toolbar"><div class="search-field"><Search :size="16" /><input v-model="auditFilters.action" placeholder="按动作筛选，例如 provider.update" @keyup.enter="loadAudits" /></div><select v-model="auditFilters.resource_type" @change="loadAudits"><option value="">全部资源</option><option value="provider">Provider</option><option value="publication_request">发布申请</option><option value="agent_run">Agent Run</option><option value="document_job">文档任务</option><option value="tenant_quota">租户配额</option></select><select v-model="auditFilters.result" @change="loadAudits"><option value="">全部结果</option><option value="success">成功</option><option value="failed">失败</option><option value="blocked">已阻止</option><option value="partial">部分成功</option></select><span class="result-count">{{ auditTotal }} 条事件</span></div>
          <div class="table-wrap"><table><thead><tr><th>动作</th><th>目标</th><th>操作人</th><th>结果</th><th>原因</th><th>时间</th></tr></thead><tbody><tr v-for="item in audits" :key="item.event_id"><td><strong>{{ item.action }}</strong><span class="cell-note mono">{{ item.event_id }}</span></td><td>{{ resourceLabel(item.resource_type) }} · {{ item.resource_id }}</td><td><span>{{ item.actor_user_id }}</span><span class="cell-note">{{ item.actor_role }}</span></td><td><span :class="statusClass(item.result)">{{ item.result }}</span></td><td class="reason-cell">{{ item.reason || '-' }}</td><td>{{ formatTime(item.created_at) }}</td></tr></tbody></table><div v-if="!audits.length" class="empty"><History :size="28" /><strong>没有审计事件</strong></div></div>
        </template>
      </div>
    </main>

    <div v-if="showCredentials" class="modal-backdrop">
      <section class="modal credential-modal"><div class="modal-header"><div><span class="eyebrow">SECURE CONNECTION</span><h2>连接管理网关</h2></div><button v-if="context" class="icon-button" @click="showCredentials = false"><X :size="18" /></button></div><p>使用基座平台签发的访问令牌。测试环境可由网关将已登录成员映射为管理员，但仍会验证 JWT 与租户成员关系。</p><button v-if="isDevelopment" class="local-login-button" :disabled="loading" @click="useLocalTestIdentity"><Zap :size="17" /><span><strong>使用本地测试身份进入</strong><small>仅开发服务器可用，身份为平台管理员</small></span><ChevronRight :size="17" /></button><div v-if="isDevelopment" class="credential-divider"><span>或使用真实凭据</span></div><label><span>访问令牌</span><textarea v-model="tokenInput" rows="4" placeholder="Bearer token"></textarea></label><label><span>租户 ID</span><input v-model="tenantInput" placeholder="选择要管理的租户" @keyup.enter="saveCredentials" /></label><div v-if="fatalError" class="inline-error">{{ fatalError }}</div><div class="modal-actions"><button v-if="context" class="text-button danger-text" @click="clearCredentials"><LogOut :size="15" />清除凭据</button><button class="primary-button" :disabled="!tokenInput.trim() || !tenantInput.trim() || loading" @click="saveCredentials"><RefreshCw v-if="loading" :size="16" class="spin" /><ShieldCheck v-else :size="16" />验证并进入</button></div></section>
    </div>

    <div v-if="actionDialog.open" class="modal-backdrop" @click.self="actionDialog.open = false"><section class="modal"><div class="modal-header"><div><span class="eyebrow">CONFIRM OPERATION</span><h2>{{ actionDialog.title }}</h2></div><button class="icon-button" @click="actionDialog.open = false"><X :size="18" /></button></div><p>{{ actionDialog.description }}</p><label class="reason-field"><span>操作原因 <b>必填</b></span><textarea v-model="actionDialog.reason" autofocus placeholder="说明执行该操作的原因，内容会写入审计日志"></textarea></label><div class="modal-actions"><button class="secondary-button" @click="actionDialog.open = false">取消</button><button :class="actionDialog.danger ? 'danger-button' : 'primary-button'" @click="confirmAction">{{ actionDialog.confirmLabel }}</button></div></section></div>

    <div v-if="providerEditor" class="modal-backdrop" @click.self="providerEditor = false"><section class="modal wide"><div class="modal-header"><div><span class="eyebrow">MODEL PROVIDER</span><h2>{{ editingProvider ? '编辑 Provider' : '新增 Provider' }}</h2></div><button class="icon-button" @click="providerEditor = false"><X :size="18" /></button></div><div class="form-grid"><label><span>名称</span><input v-model="providerForm.name" /></label><label><span>Base URL</span><input v-model="providerForm.base_url" placeholder="https://api.example.com/v1" /></label><label><span>API Key</span><input v-model="providerForm.api_key" type="password" :placeholder="editingProvider?.has_api_key ? '留空表示不替换' : '可选'" /></label><label><span>模型列表</span><input v-model="providerForm.models" placeholder="model-a, model-b" /></label></div><div class="switch-row"><label><input v-model="providerForm.enabled" type="checkbox" />启用</label><label><input v-model="providerForm.is_default" type="checkbox" />设为默认</label></div><label class="reason-field"><span>变更原因 <b>必填</b></span><textarea v-model="providerForm.reason" placeholder="内容会写入审计日志"></textarea></label><div class="modal-actions"><button class="secondary-button" @click="providerEditor = false">取消</button><button class="primary-button" @click="saveProvider">保存</button></div></section></div>

    <transition name="toast"><div v-if="notice" class="toast" :class="notice.type"><Check v-if="notice.type === 'success'" :size="17" /><XCircle v-else :size="17" />{{ notice.text }}</div></transition>
  </div>
</template>
