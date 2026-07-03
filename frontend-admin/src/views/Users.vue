<script setup lang="ts">
/**
 * 用户管理页面 - Naive UI + vxe-table 版本
 */
import { ref, reactive, onMounted } from 'vue'
import {
  NCard,
  NButton,
  NSpace,
  NTag,
  NModal,
  NForm,
  NFormItem,
  NInput,
  NSelect,
  NSwitch,
  NPopconfirm,
  NGrid,
  NGi,
  NStatistic,
  NSpin,
  NPagination,
  NTabs,
  NTabPane,
  NProgress,
  NEmpty,
  NTimeline,
  NTimelineItem,
  useMessage,
  type FormInst,
  type FormRules,
} from 'naive-ui'
import {
  getUsers,
  getUserStats,
  createUser,
  updateUser,
  deleteUser,
  resetUserPassword,
  toggleUserStatus,
  getUserUsage,
  getUserOperations,
  ROLE_NAME_MAP,
  type User,
  type UserStats,
  type UserQueryParams,
  type UserUsageStats,
  type UserOperationLogs
} from '../api/users'

const message = useMessage()
const formRef = ref<FormInst | null>(null)

// 统计数据
const stats = ref<UserStats>({
  total: 0,
  active: 0,
  inactive: 0,
  by_role: {},
  recent_logins_24h: 0
})

// 表格数据
const tableData = ref<User[]>([])
const loading = ref(false)
const statsLoading = ref(false)
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(20)

// 筛选条件
const filters = reactive({
  search: '',
  role: null as string | null,
  is_active: null as string | null
})

// 角色选项
const roleOptions: Array<{ label: string; value: string | null }> = [
  { label: '全部角色', value: null },
  { label: '超级管理员', value: 'super_admin' },
  { label: '系统管理员', value: 'admin' },
  { label: '编辑员', value: 'editor' },
  { label: '普通用户', value: 'user' },
]

// 状态选项
const statusOptions: Array<{ label: string; value: string | null }> = [
  { label: '全部', value: null },
  { label: '启用', value: 'true' },
  { label: '禁用', value: 'false' },
]

// 弹窗
const showModal = ref(false)
const modalTitle = ref('新建用户')
const editingId = ref<string | null>(null)
const submitLoading = ref(false)

// 表单数据
const formData = reactive({
  username: '',
  email: '',
  password: '',
  phone: '',
  roles: [] as string[],
  is_active: true,
})

// 表单验证规则
const rules: FormRules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 3, max: 50, message: '用户名3-50个字符', trigger: 'blur' },
  ],
  email: [
    { type: 'email', message: '请输入有效的邮箱地址', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, message: '密码至少6个字符', trigger: 'blur' },
  ],
}

// ========== 用户详情弹窗 ==========
const showDetailModal = ref(false)
const detailLoading = ref(false)
const detailUser = ref<User | null>(null)
const detailTab = ref('usage')
const usageStats = ref<UserUsageStats | null>(null)
const operationLogs = ref<UserOperationLogs | null>(null)

// 操作类型映射
const ACTION_TYPE_MAP: Record<string, string> = {
  chat: '智能问答',
  search: '文献检索',
  review: '综述生成',
  import: '文献导入'
}

// 操作动作映射
const ACTION_MAP: Record<string, string> = {
  create: '创建',
  update: '更新',
  delete: '删除',
  login: '登录',
  logout: '登出',
  reset_password: '重置密码',
  toggle_status: '切换状态'
}

// 资源类型映射
const RESOURCE_MAP: Record<string, string> = {
  user: '用户',
  role: '角色',
  document: '文献',
  session: '会话'
}

// 加载统计数据
async function loadStats() {
  statsLoading.value = true
  try {
    stats.value = await getUserStats()
  } catch (error) {
    console.error('Failed to load stats:', error)
  } finally {
    statsLoading.value = false
  }
}

// 加载用户列表
async function loadData() {
  loading.value = true
  try {
    const params: UserQueryParams = {
      page: currentPage.value,
      page_size: pageSize.value,
      search: filters.search || undefined,
      role: filters.role || undefined,
      is_active: filters.is_active === 'true' ? true : filters.is_active === 'false' ? false : undefined,
    }
    const response = await getUsers(params)
    tableData.value = response.items
    total.value = response.total
  } catch (error) {
    console.error('Failed to load users:', error)
    message.error('加载用户列表失败')
  } finally {
    loading.value = false
  }
}

// 搜索
function handleSearch() {
  currentPage.value = 1
  loadData()
}

// 重置筛选
function handleReset() {
  filters.search = ''
  filters.role = null
  filters.is_active = null
  currentPage.value = 1
  loadData()
}

// 分页
function handlePageChange(page: number) {
  currentPage.value = page
  loadData()
}

// 打开新建弹窗
function handleCreate() {
  modalTitle.value = '新建用户'
  editingId.value = null
  Object.assign(formData, {
    username: '',
    email: '',
    password: '',
    phone: '',
    roles: ['user'],
    is_active: true,
  })
  showModal.value = true
}

// 打开编辑弹窗
function handleEdit(row: User) {
  modalTitle.value = '编辑用户'
  editingId.value = row.user_id
  Object.assign(formData, {
    username: row.username,
    email: row.email || '',
    password: '',
    phone: row.phone || '',
    roles: row.roles,
    is_active: row.is_active,
  })
  showModal.value = true
}

// 提交表单
async function handleSubmit() {
  try {
    await formRef.value?.validate()
  } catch {
    return
  }

  submitLoading.value = true
  try {
    if (editingId.value) {
      await updateUser(editingId.value, {
        email: formData.email || undefined,
        phone: formData.phone || undefined,
        roles: formData.roles,
        is_active: formData.is_active,
      })
      message.success('更新成功')
    } else {
      await createUser({
        username: formData.username,
        email: formData.email || undefined,
        password: formData.password,
        phone: formData.phone || undefined,
        roles: formData.roles,
        is_active: formData.is_active,
      })
      message.success('创建成功')
    }
    showModal.value = false
    loadData()
    loadStats()
  } catch (error: unknown) {
    const err = error as { response?: { data?: { detail?: string } } }
    message.error(err?.response?.data?.detail || '操作失败')
  } finally {
    submitLoading.value = false
  }
}

// 删除用户
async function handleDelete(row: User) {
  try {
    await deleteUser(row.user_id)
    message.success('删除成功')
    loadData()
    loadStats()
  } catch (error: unknown) {
    const err = error as { response?: { data?: { detail?: string } } }
    message.error(err?.response?.data?.detail || '删除失败')
  }
}

// 重置密码
async function handleResetPassword(row: User) {
  try {
    const res = await resetUserPassword(row.user_id)
    message.success(`密码已重置为: ${res.new_password}`)
  } catch (error: unknown) {
    const err = error as { response?: { data?: { detail?: string } } }
    message.error(err?.response?.data?.detail || '重置失败')
  }
}

// 切换状态
async function handleToggleStatus(row: User) {
  try {
    const res = await toggleUserStatus(row.user_id)
    message.success(res.is_active ? '已启用' : '已禁用')
    loadData()
    loadStats()
  } catch (error: unknown) {
    const err = error as { response?: { data?: { detail?: string } } }
    message.error(err?.response?.data?.detail || '操作失败')
  }
}

// 查看用户详情
async function handleViewDetail(row: User) {
  detailUser.value = row
  detailTab.value = 'usage'
  showDetailModal.value = true
  await loadUserDetail(row.user_id)
}

// 加载用户详情数据
async function loadUserDetail(userId: string) {
  detailLoading.value = true
  try {
    const [usage, operations] = await Promise.all([
      getUserUsage(userId),
      getUserOperations(userId)
    ])
    usageStats.value = usage
    operationLogs.value = operations
  } catch (error) {
    console.error('Failed to load user detail:', error)
    message.error('加载用户详情失败')
  } finally {
    detailLoading.value = false
  }
}

// 获取角色名称
function getRoleName(role: string): string {
  return ROLE_NAME_MAP[role] || role
}

// 格式化时间
function formatTime(time: string | null): string {
  if (!time) return '-'
  return new Date(time).toLocaleString()
}

onMounted(() => {
  loadStats()
  loadData()
})
</script>

<template>
  <div class="users-page">
    <!-- 统计卡片 -->
    <n-grid :cols="4" :x-gap="16" :y-gap="16" style="margin-bottom: 16px">
      <n-gi>
        <n-card>
          <n-statistic label="用户总数" :value="stats.total" />
        </n-card>
      </n-gi>
      <n-gi>
        <n-card>
          <n-statistic label="活跃用户" :value="stats.active" />
        </n-card>
      </n-gi>
      <n-gi>
        <n-card>
          <n-statistic label="禁用用户" :value="stats.inactive" />
        </n-card>
      </n-gi>
      <n-gi>
        <n-card>
          <n-statistic label="24小时登录" :value="stats.recent_logins_24h" />
        </n-card>
      </n-gi>
    </n-grid>

    <!-- 主卡片 -->
    <n-card title="用户列表">
      <!-- 筛选栏 -->
      <template #header-extra>
        <n-space>
          <n-input
            v-model:value="filters.search"
            placeholder="搜索用户名/邮箱"
            clearable
            style="width: 200px"
            @keyup.enter="handleSearch"
          />
          <n-select
            v-model:value="filters.role"
            :options="(roleOptions as any)"
            placeholder="角色"
            clearable
            style="width: 140px"
            @update:value="handleSearch"
          />
          <n-select
            v-model:value="filters.is_active"
            :options="(statusOptions as any)"
            placeholder="状态"
            clearable
            style="width: 100px"
            @update:value="handleSearch"
          />
          <n-button @click="handleReset">重置</n-button>
          <n-button type="primary" @click="handleCreate">新建用户</n-button>
        </n-space>
      </template>

      <!-- 表格 -->
      <n-spin :show="loading">
        <vxe-table
          :data="tableData"
          stripe
          border
          round
          height="500"
        >
        <vxe-column field="username" title="用户名" width="120" />
        <vxe-column field="email" title="邮箱" min-width="180" />
        <vxe-column title="角色" width="200">
          <template #default="{ row }">
            <n-space>
              <n-tag v-for="role in row.roles" :key="role" size="small" :type="role === 'super_admin' ? 'error' : role === 'admin' ? 'warning' : 'info'">
                {{ getRoleName(role) }}
              </n-tag>
            </n-space>
          </template>
        </vxe-column>
        <vxe-column title="状态" width="80" align="center">
          <template #default="{ row }">
            <n-tag :type="row.is_active ? 'success' : 'default'" size="small">
              {{ row.is_active ? '启用' : '禁用' }}
            </n-tag>
          </template>
        </vxe-column>
        <vxe-column title="最后登录" width="160">
          <template #default="{ row }">
            {{ formatTime(row.last_login_at) }}
          </template>
        </vxe-column>
        <vxe-column title="操作" width="280" fixed="right">
          <template #default="{ row }">
            <n-space>
              <n-button size="small" type="info" @click="handleViewDetail(row)">详情</n-button>
              <n-button size="small" @click="handleEdit(row)">编辑</n-button>
              <n-popconfirm @positive-click="handleResetPassword(row)">
                <template #trigger>
                  <n-button size="small">重置密码</n-button>
                </template>
                确定要重置 {{ row.username }} 的密码吗？
              </n-popconfirm>
              <n-button size="small" :type="row.is_active ? 'warning' : 'success'" @click="handleToggleStatus(row)">
                {{ row.is_active ? '禁用' : '启用' }}
              </n-button>
              <n-popconfirm @positive-click="handleDelete(row)">
                <template #trigger>
                  <n-button size="small" type="error">删除</n-button>
                </template>
                确定要删除用户 {{ row.username }} 吗？
              </n-popconfirm>
            </n-space>
          </template>
        </vxe-column>
      </vxe-table>
      </n-spin>

      <!-- 分页 -->
      <div style="display: flex; justify-content: flex-end; margin-top: 16px">
        <n-pagination
          v-model:page="currentPage"
          :page-size="pageSize"
          :item-count="total"
          @update:page="handlePageChange"
        />
      </div>
    </n-card>

    <!-- 新建/编辑弹窗 -->
    <n-modal
      v-model:show="showModal"
      :title="modalTitle"
      preset="card"
      style="width: 500px"
    >
      <n-form
        ref="formRef"
        :model="formData"
        :rules="rules"
        label-placement="left"
        label-width="80"
      >
        <n-form-item label="用户名" path="username">
          <n-input
            v-model:value="formData.username"
            placeholder="请输入用户名"
            :disabled="!!editingId"
          />
        </n-form-item>
        <n-form-item label="邮箱" path="email">
          <n-input v-model:value="formData.email" placeholder="请输入邮箱" />
        </n-form-item>
        <n-form-item label="密码" path="password" v-if="!editingId">
          <n-input
            v-model:value="formData.password"
            type="password"
            placeholder="请输入密码"
            show-password-on="click"
          />
        </n-form-item>
        <n-form-item label="手机" path="phone">
          <n-input v-model:value="formData.phone" placeholder="请输入手机号" />
        </n-form-item>
        <n-form-item label="角色">
          <n-select
            v-model:value="formData.roles"
            :options="(roleOptions.filter(o => o.value !== null).map(o => ({ label: o.label, value: o.value })) as any)"
            multiple
            placeholder="请选择角色"
          />
        </n-form-item>
        <n-form-item label="状态">
          <n-switch v-model:value="formData.is_active">
            <template #checked>启用</template>
            <template #unchecked>禁用</template>
          </n-switch>
        </n-form-item>
      </n-form>

      <template #footer>
        <n-space justify="end">
          <n-button @click="showModal = false">取消</n-button>
          <n-button type="primary" :loading="submitLoading" @click="handleSubmit">
            确定
          </n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 用户详情弹窗 -->
    <n-modal
      v-model:show="showDetailModal"
      :title="detailUser ? `用户详情 - ${detailUser.username}` : '用户详情'"
      preset="card"
      style="width: 700px"
    >
      <n-spin :show="detailLoading">
        <n-tabs v-model:value="detailTab" type="line">
          <!-- 使用统计 Tab -->
          <n-tab-pane name="usage" tab="使用统计">
            <template v-if="usageStats">
              <!-- 统计卡片 -->
              <n-grid :cols="4" :x-gap="12" :y-gap="12" style="margin-bottom: 16px">
                <n-gi>
                  <n-card size="small">
                    <n-statistic label="问答次数" :value="usageStats.chat_count" />
                  </n-card>
                </n-gi>
                <n-gi>
                  <n-card size="small">
                    <n-statistic label="检索次数" :value="usageStats.search_count" />
                  </n-card>
                </n-gi>
                <n-gi>
                  <n-card size="small">
                    <n-statistic label="综述生成" :value="usageStats.review_count" />
                  </n-card>
                </n-gi>
                <n-gi>
                  <n-card size="small">
                    <n-statistic label="Token消耗" :value="usageStats.total_tokens" />
                  </n-card>
                </n-gi>
              </n-grid>

              <!-- 使用比例 -->
              <n-card title="功能使用分布" size="small" style="margin-bottom: 16px">
                <div v-if="usageStats.total_actions > 0" class="usage-bars">
                  <div class="usage-bar-item">
                    <span class="label">智能问答</span>
                    <n-progress
                      type="line"
                      :percentage="Math.round((usageStats.chat_count / usageStats.total_actions) * 100)"
                      :show-indicator="false"
                      color="#18a058"
                    />
                    <span class="count">{{ usageStats.chat_count }}</span>
                  </div>
                  <div class="usage-bar-item">
                    <span class="label">文献检索</span>
                    <n-progress
                      type="line"
                      :percentage="Math.round((usageStats.search_count / usageStats.total_actions) * 100)"
                      :show-indicator="false"
                      color="#2080f0"
                    />
                    <span class="count">{{ usageStats.search_count }}</span>
                  </div>
                  <div class="usage-bar-item">
                    <span class="label">综述生成</span>
                    <n-progress
                      type="line"
                      :percentage="Math.round((usageStats.review_count / usageStats.total_actions) * 100)"
                      :show-indicator="false"
                      color="#f0a020"
                    />
                    <span class="count">{{ usageStats.review_count }}</span>
                  </div>
                  <div class="usage-bar-item">
                    <span class="label">文献导入</span>
                    <n-progress
                      type="line"
                      :percentage="Math.round((usageStats.import_count / usageStats.total_actions) * 100)"
                      :show-indicator="false"
                      color="#d03050"
                    />
                    <span class="count">{{ usageStats.import_count }}</span>
                  </div>
                </div>
                <n-empty v-else description="暂无使用记录" />
              </n-card>

              <!-- 最近记录 -->
              <n-card title="最近使用记录" size="small">
                <template v-if="usageStats.recent_records.length > 0">
                  <n-timeline>
                    <n-timeline-item
                      v-for="record in usageStats.recent_records.slice(0, 10)"
                      :key="record.id"
                      :type="record.action_type === 'chat' ? 'success' : record.action_type === 'search' ? 'info' : record.action_type === 'review' ? 'warning' : 'default'"
                    >
                      <template #header>
                        <n-tag size="small" :type="record.action_type === 'chat' ? 'success' : record.action_type === 'search' ? 'info' : record.action_type === 'review' ? 'warning' : 'default'">
                          {{ ACTION_TYPE_MAP[record.action_type] || record.action_type }}
                        </n-tag>
                        <span v-if="record.tokens_used > 0" style="margin-left: 8px; color: #999; font-size: 12px">
                          消耗 {{ record.tokens_used }} Token
                        </span>
                      </template>
                      <span style="color: #999; font-size: 12px">{{ formatTime(record.created_at) }}</span>
                    </n-timeline-item>
                  </n-timeline>
                </template>
                <n-empty v-else description="暂无使用记录" />
              </n-card>
            </template>
            <n-empty v-else description="加载中..." />
          </n-tab-pane>

          <!-- 操作日志 Tab -->
          <n-tab-pane name="operations" tab="操作日志">
            <template v-if="operationLogs">
              <n-card size="small">
                <template v-if="operationLogs.items.length > 0">
                  <n-timeline>
                    <n-timeline-item
                      v-for="log in operationLogs.items"
                      :key="log.id"
                      :type="log.status === 'success' ? 'success' : 'error'"
                    >
                      <template #header>
                        <n-space align="center">
                          <n-tag size="small" :type="log.action === 'login' ? 'info' : log.action === 'create' ? 'success' : log.action === 'delete' ? 'error' : 'default'">
                            {{ ACTION_MAP[log.action] || log.action }}
                          </n-tag>
                          <span v-if="log.resource">
                            {{ RESOURCE_MAP[log.resource] || log.resource }}
                            <span v-if="log.resource_id" style="color: #999">({{ log.resource_id.substring(0, 12) }}...)</span>
                          </span>
                        </n-space>
                      </template>
                      <div style="font-size: 12px; color: #666">
                        <div>{{ formatTime(log.created_at) }}</div>
                        <div v-if="log.ip_address" style="color: #999">IP: {{ log.ip_address }}</div>
                      </div>
                    </n-timeline-item>
                  </n-timeline>
                </template>
                <n-empty v-else description="暂无操作日志" />
              </n-card>
            </template>
            <n-empty v-else description="加载中..." />
          </n-tab-pane>
        </n-tabs>
      </n-spin>

      <template #footer>
        <n-button @click="showDetailModal = false">关闭</n-button>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.users-page {
  min-height: 100%;
}

.usage-bars {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.usage-bar-item {
  display: flex;
  align-items: center;
  gap: 12px;
}

.usage-bar-item .label {
  width: 70px;
  font-size: 13px;
  color: #333;
}

.usage-bar-item .n-progress {
  flex: 1;
}

.usage-bar-item .count {
  width: 40px;
  text-align: right;
  font-size: 13px;
  color: #666;
}
</style>
