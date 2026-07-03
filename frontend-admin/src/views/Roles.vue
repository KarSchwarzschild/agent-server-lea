<script setup lang="ts">
/**
 * 角色权限管理页面
 * - 统计卡片（角色数量、权限数量）
 * - 角色列表（vxe-table）
 * - 新建/编辑角色弹窗（权限选择）
 * - 初始化系统角色按钮
 */
import { ref, reactive, onMounted, computed } from 'vue'
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
  NCheckbox,
  NCheckboxGroup,
  NPopconfirm,
  NGrid,
  NGi,
  NStatistic,
  NCollapse,
  NCollapseItem,
  NDivider,
  NTooltip,
  NPagination,
  NSpin,
  useMessage,
  type FormInst,
  type FormRules,
} from 'naive-ui'
import {
  getRoles,
  getRoleStats,
  getPermissions,
  getResources,
  createRole,
  updateRole,
  deleteRole,
  initializeRoles,
  getResourceName,
  getActionName,
  getRoleTagType,
  groupPermissionsByResource,
  type Role,
  type Permission,
  type RoleStats,
  type CreateRoleRequest,
  type UpdateRoleRequest,
} from '../api/roles'

const message = useMessage()
const formRef = ref<FormInst | null>(null)

// 统计数据
const stats = ref<RoleStats>({
  total_roles: 0,
  system_roles: 0,
  custom_roles: 0,
  total_permissions: 0,
  by_resource: {}
})

// 表格数据
const tableData = ref<Role[]>([])
const loading = ref(false)
const statsLoading = ref(false)
const initLoading = ref(false)
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(20)

// 筛选条件
const filters = reactive({
  search: '',
  is_system: null as string | null
})

// 权限数据
const allPermissions = ref<Permission[]>([])
const permissionsByResource = computed(() => groupPermissionsByResource(allPermissions.value))
const resources = ref<string[]>([])

// 弹窗
const showModal = ref(false)
const modalTitle = ref('新建角色')
const editingId = ref<number | null>(null)
const isSystemRole = ref(false)
const submitLoading = ref(false)

// 表单数据
const formData = reactive<CreateRoleRequest>({
  name: '',
  code: '',
  description: '',
  permissions: [],
})

// 表单验证规则
const rules: FormRules = {
  name: [
    { required: true, message: '请输入角色名称', trigger: 'blur' },
    { min: 2, max: 50, message: '角色名称2-50个字符', trigger: 'blur' },
  ],
  code: [
    { required: true, message: '请输入角色代码', trigger: 'blur' },
    { min: 2, max: 50, message: '角色代码2-50个字符', trigger: 'blur' },
    { pattern: /^[a-z][a-z0-9_]*$/, message: '只能包含小写字母、数字和下划线，且以字母开头', trigger: 'blur' },
  ],
}

// 系统角色筛选选项
const systemOptions: Array<{ label: string; value: string | null }> = [
  { label: '全部', value: null },
  { label: '系统角色', value: 'true' },
  { label: '自定义角色', value: 'false' },
]

// 加载统计数据
async function loadStats() {
  statsLoading.value = true
  try {
    stats.value = await getRoleStats()
  } catch (error) {
    console.error('Failed to load stats:', error)
  } finally {
    statsLoading.value = false
  }
}

// 加载权限列表
async function loadPermissions() {
  try {
    allPermissions.value = await getPermissions()
    resources.value = await getResources()
  } catch (error) {
    console.error('Failed to load permissions:', error)
  }
}

// 加载角色列表
async function loadData() {
  loading.value = true
  try {
    const params = {
      page: currentPage.value,
      page_size: pageSize.value,
      search: filters.search || undefined,
      is_system: filters.is_system === 'true' ? true : filters.is_system === 'false' ? false : undefined,
    }
    const response = await getRoles(params)
    tableData.value = response.items
    total.value = response.total
  } catch (error) {
    console.error('Failed to load roles:', error)
    message.error('加载角色列表失败')
  } finally {
    loading.value = false
  }
}

// 搜索变化（防抖）
let searchTimeout: number | null = null
function handleSearchChange() {
  if (searchTimeout) {
    clearTimeout(searchTimeout)
  }
  searchTimeout = window.setTimeout(() => {
    currentPage.value = 1
    loadData()
  }, 300)
}

// 筛选变化
function handleFilterChange() {
  currentPage.value = 1
  loadData()
}

// 分页变化
function handlePageChange(page: number) {
  currentPage.value = page
  loadData()
}

// 打开新建弹窗
function handleCreate() {
  modalTitle.value = '新建角色'
  editingId.value = null
  isSystemRole.value = false
  formData.name = ''
  formData.code = ''
  formData.description = ''
  formData.permissions = []
  showModal.value = true
}

// 打开编辑弹窗
function handleEdit(row: Role) {
  modalTitle.value = '编辑角色'
  editingId.value = row.id
  isSystemRole.value = row.is_system
  formData.name = row.name
  formData.code = row.code
  formData.description = row.description || ''
  formData.permissions = [...row.permissions]
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
      // 更新角色
      const data: UpdateRoleRequest = {
        description: formData.description,
        permissions: formData.permissions,
      }
      // 非系统角色可以修改名称
      if (!isSystemRole.value) {
        data.name = formData.name
      }
      await updateRole(editingId.value, data)
      message.success('角色更新成功')
    } else {
      // 创建角色
      await createRole(formData)
      message.success('角色创建成功')
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

// 删除角色
async function handleDelete(row: Role) {
  try {
    await deleteRole(row.id)
    message.success('角色删除成功')
    loadData()
    loadStats()
  } catch (error: unknown) {
    const err = error as { response?: { data?: { detail?: string } } }
    message.error(err?.response?.data?.detail || '删除失败')
  }
}

// 初始化系统角色
async function handleInitialize() {
  initLoading.value = true
  try {
    const result = await initializeRoles()
    message.success(`初始化完成：${result.roles_count} 个角色、${result.permissions_count} 个权限`)
    loadData()
    loadStats()
    loadPermissions()
  } catch (error: unknown) {
    const err = error as { response?: { data?: { detail?: string } } }
    message.error(err?.response?.data?.detail || '初始化失败')
  } finally {
    initLoading.value = false
  }
}

// 刷新数据
function handleRefresh() {
  loadData()
  loadStats()
}

// 全选/取消资源下的权限
function handleSelectAll(resource: string, checked: boolean) {
  const resourcePerms = permissionsByResource.value[resource] || []
  const codes = resourcePerms.map(p => p.code)

  if (checked) {
    // 添加该资源下所有权限
    const newPermissions = new Set(formData.permissions)
    codes.forEach(c => newPermissions.add(c))
    formData.permissions = Array.from(newPermissions)
  } else {
    // 移除该资源下所有权限
    formData.permissions = formData.permissions.filter(p => !codes.includes(p))
  }
}

// 检查资源是否全选
function isResourceAllSelected(resource: string): boolean {
  const resourcePerms = permissionsByResource.value[resource] || []
  if (resourcePerms.length === 0) return false
  return resourcePerms.every(p => formData.permissions.includes(p.code))
}

// 检查资源是否部分选中
function isResourceIndeterminate(resource: string): boolean {
  const resourcePerms = permissionsByResource.value[resource] || []
  if (resourcePerms.length === 0) return false
  const selected = resourcePerms.filter(p => formData.permissions.includes(p.code))
  return selected.length > 0 && selected.length < resourcePerms.length
}

onMounted(() => {
  loadStats()
  loadData()
  loadPermissions()
})
</script>

<template>
  <div class="roles-view">
    <!-- 统计卡片 -->
    <n-grid :cols="4" :x-gap="16" :y-gap="16" style="margin-bottom: 16px">
      <n-gi>
        <n-card size="small" class="stat-card">
          <n-statistic label="角色总数" :value="stats.total_roles" />
        </n-card>
      </n-gi>
      <n-gi>
        <n-card size="small" class="stat-card">
          <n-statistic label="系统角色" :value="stats.system_roles">
            <template #suffix>
              <n-tag type="warning" size="small">内置</n-tag>
            </template>
          </n-statistic>
        </n-card>
      </n-gi>
      <n-gi>
        <n-card size="small" class="stat-card">
          <n-statistic label="自定义角色" :value="stats.custom_roles" />
        </n-card>
      </n-gi>
      <n-gi>
        <n-card size="small" class="stat-card">
          <n-statistic label="权限总数" :value="stats.total_permissions" />
        </n-card>
      </n-gi>
    </n-grid>

    <!-- 操作栏 -->
    <n-card size="small" style="margin-bottom: 16px">
      <n-space justify="space-between" align="center">
        <n-space>
          <n-input
            v-model:value="filters.search"
            placeholder="搜索角色名称/代码"
            clearable
            style="width: 200px"
            @update:value="handleSearchChange"
          />
          <n-select
            v-model:value="filters.is_system"
            :options="(systemOptions as any)"
            placeholder="角色类型"
            style="width: 140px"
            @update:value="handleFilterChange"
          />
        </n-space>
        <n-space>
          <n-button @click="handleRefresh">刷新</n-button>
          <n-button type="primary" @click="handleCreate">新建角色</n-button>
          <n-popconfirm @positive-click="handleInitialize">
            <template #trigger>
              <n-button :loading="initLoading">初始化系统角色</n-button>
            </template>
            确认初始化系统角色和权限？这将创建预定义的角色和权限数据。
          </n-popconfirm>
        </n-space>
      </n-space>
    </n-card>

    <!-- 角色列表 -->
    <n-card size="small">
      <n-spin :show="loading">
        <vxe-table
          :data="tableData"
          stripe
          round
          border
        >
        <vxe-column field="id" title="ID" width="80" />
        <vxe-column field="name" title="角色名称" min-width="150" />
        <vxe-column field="code" title="角色代码" min-width="120">
          <template #default="{ row }">
            <n-tag :type="getRoleTagType(row.code)" size="small">
              {{ row.code }}
            </n-tag>
          </template>
        </vxe-column>
        <vxe-column field="description" title="描述" min-width="200" />
        <vxe-column title="权限数" width="100" align="center">
          <template #default="{ row }">
            {{ row.permissions.length }}
          </template>
        </vxe-column>
        <vxe-column field="user_count" title="用户数" width="100" align="center" />
        <vxe-column field="is_system" title="类型" width="120" align="center">
          <template #default="{ row }">
            <n-tag :type="row.is_system ? 'warning' : 'default'" size="small">
              {{ row.is_system ? '系统角色' : '自定义' }}
            </n-tag>
          </template>
        </vxe-column>
        <vxe-column title="操作" width="200" fixed="right">
          <template #default="{ row }">
            <n-space>
              <n-button size="small" type="primary" @click="handleEdit(row)">
                编辑
              </n-button>
              <n-popconfirm
                v-if="!row.is_system"
                @positive-click="handleDelete(row)"
              >
                <template #trigger>
                  <n-button size="small" type="error" :disabled="row.user_count > 0">
                    删除
                  </n-button>
                </template>
                <template v-if="row.user_count > 0">
                  该角色有 {{ row.user_count }} 个用户在使用，无法删除
                </template>
                <template v-else>
                  确认删除角色 "{{ row.name }}"？
                </template>
              </n-popconfirm>
              <n-tooltip v-else>
                <template #trigger>
                  <n-button size="small" type="error" disabled>删除</n-button>
                </template>
                系统角色不能删除
              </n-tooltip>
            </n-space>
          </template>
        </vxe-column>
      </vxe-table>
      </n-spin>

      <!-- 分页 -->
      <div style="margin-top: 16px; display: flex; justify-content: flex-end">
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
      style="width: 700px; max-width: 90vw"
      :mask-closable="false"
    >
      <n-form
        ref="formRef"
        :model="formData"
        :rules="rules"
        label-placement="left"
        label-width="100"
      >
        <n-form-item label="角色名称" path="name">
          <n-input
            v-model:value="formData.name"
            placeholder="请输入角色名称"
            :disabled="isSystemRole"
          />
        </n-form-item>
        <n-form-item label="角色代码" path="code">
          <n-input
            v-model:value="formData.code"
            placeholder="请输入角色代码（如: editor）"
            :disabled="editingId !== null"
          />
        </n-form-item>
        <n-form-item label="描述" path="description">
          <n-input
            v-model:value="formData.description"
            type="textarea"
            placeholder="请输入角色描述"
            :rows="2"
          />
        </n-form-item>

        <n-divider>权限配置</n-divider>

        <div class="permission-section">
          <n-collapse>
            <n-collapse-item
              v-for="resource in resources"
              :key="resource"
              :name="resource"
            >
              <template #header>
                <n-space align="center">
                  <n-checkbox
                    :checked="isResourceAllSelected(resource)"
                    :indeterminate="isResourceIndeterminate(resource)"
                    @update:checked="checked => handleSelectAll(resource, checked)"
                    @click.stop
                  />
                  <span>{{ getResourceName(resource) }}</span>
                  <n-tag size="small">
                    {{ (permissionsByResource[resource] || []).filter(p => formData.permissions.includes(p.code)).length }}
                    /
                    {{ (permissionsByResource[resource] || []).length }}
                  </n-tag>
                </n-space>
              </template>
              <n-checkbox-group v-model:value="formData.permissions">
                <n-grid :cols="2" :x-gap="8" :y-gap="8">
                  <n-gi
                    v-for="perm in permissionsByResource[resource]"
                    :key="perm.code"
                  >
                    <n-checkbox :value="perm.code">
                      {{ getActionName(perm.action) }}
                      <span style="color: #888; font-size: 12px">
                        ({{ perm.code }})
                      </span>
                    </n-checkbox>
                  </n-gi>
                </n-grid>
              </n-checkbox-group>
            </n-collapse-item>
          </n-collapse>
        </div>

        <div style="margin-top: 16px; text-align: right">
          <n-space>
            <n-button @click="showModal = false">取消</n-button>
            <n-button type="primary" :loading="submitLoading" @click="handleSubmit">
              确定
            </n-button>
          </n-space>
        </div>
      </n-form>
    </n-modal>
  </div>
</template>

<style scoped>
.roles-view {
  min-height: 100%;
}

.stat-card {
  transition: transform 0.2s, box-shadow 0.2s;
}

.stat-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
}

.permission-section {
  max-height: 400px;
  overflow-y: auto;
  border: 1px solid #e0e0e0;
  border-radius: 4px;
  padding: 8px;
}

:deep(.n-collapse-item__content-inner) {
  padding: 12px 16px;
}
</style>
