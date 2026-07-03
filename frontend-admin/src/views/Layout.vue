<script setup lang="ts">
/**
 * 管理后台布局组件 - Naive UI 版本
 */
import { computed, h, ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import {
  NLayout,
  NLayoutHeader,
  NLayoutSider,
  NLayoutContent,
  NMenu,
  NDropdown,
  NAvatar,
  NIcon,
  NBreadcrumb,
  NBreadcrumbItem,
  useMessage,
  type MenuOption,
} from 'naive-ui'
import {
  HomeOutline,
  PeopleOutline,
  DocumentTextOutline,
  ShieldCheckmarkOutline,
  CloudUploadOutline,
  LogOutOutline,
  PersonOutline,
  KeyOutline,
  SettingsOutline,
  StatsChartOutline,
  ExtensionPuzzleOutline,
  FlashOutline,
  AppsOutline,
  ListOutline,
  ChatbubblesOutline,
  BuildOutline,
  AnalyticsOutline,
  EyeOutline,
  ChatboxEllipsesOutline,
  SearchOutline,
} from '@vicons/ionicons5'
import { useUserStore } from '../stores/user'

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()
const message = useMessage()

const collapsed = ref(false)

// 菜单选项
const menuOptions = computed<MenuOption[]>(() => {
  const options: MenuOption[] = [
    {
      label: '仪表板',
      key: 'dashboard',
      icon: () => h(NIcon, null, { default: () => h(HomeOutline) }),
    },
  ]

  // 用户管理（需要权限）
  if (userStore.hasPermission('user:read')) {
    options.push({
      label: '用户管理',
      key: 'users',
      icon: () => h(NIcon, null, { default: () => h(PeopleOutline) }),
    })
  }

  // 角色管理（需要权限）
  if (userStore.hasPermission('role:read')) {
    options.push({
      label: '角色权限',
      key: 'roles',
      icon: () => h(NIcon, null, { default: () => h(ShieldCheckmarkOutline) }),
    })
  }

  // 公共知识库管理
  if (userStore.hasPermission('document:read')) {
    options.push({
      label: '公共知识库管理',
      key: 'documents',
      icon: () => h(NIcon, null, { default: () => h(DocumentTextOutline) }),
    })
  }

  // 文件上传
  if (userStore.hasPermission('document:write')) {
    options.push({
      label: '上传文件',
      key: 'documents/upload',
      icon: () => h(NIcon, null, { default: () => h(CloudUploadOutline) }),
    })
  }

  // 智能体管理
  if (userStore.hasPermission('system:read') || userStore.roles?.includes('super_admin') || userStore.roles?.includes('admin')) {
    options.push({
      label: '智能体管理',
      key: 'agents',
      icon: () => h(NIcon, null, { default: () => h(ExtensionPuzzleOutline) }),
    })
  }

  // 系统设置
  if (userStore.hasPermission('system:read') || userStore.roles?.includes('super_admin') || userStore.roles?.includes('admin')) {
    options.push({
      label: '系统设置',
      key: 'settings',
      icon: () => h(NIcon, null, { default: () => h(SettingsOutline) }),
    })
    options.push({
      label: '消息频道',
      key: 'channel-management',
      icon: () => h(NIcon, null, { default: () => h(ChatboxEllipsesOutline) }),
    })
  }

  // 统计分析
  if (userStore.hasPermission('stats:read') || userStore.roles?.includes('super_admin') || userStore.roles?.includes('admin')) {
    options.push({
      label: '统计分析',
      key: 'statistics',
      icon: () => h(NIcon, null, { default: () => h(StatsChartOutline) }),
    })
  }

  // 超级智能体管理（子菜单）
  if (userStore.hasPermission('system:read') || userStore.roles?.includes('super_admin') || userStore.roles?.includes('admin')) {
    options.push({
      label: '超级智能体',
      key: 'super-agent',
      icon: () => h(NIcon, null, { default: () => h(FlashOutline) }),
      children: [
        {
          label: 'AI助手',
          key: 'super-agent/assistants',
          icon: () => h(NIcon, null, { default: () => h(PersonOutline) }),
        },
        {
          label: 'Skills管理',
          key: 'super-agent/skills',
          icon: () => h(NIcon, null, { default: () => h(AppsOutline) }),
        },
        {
          label: '执行日志',
          key: 'super-agent/logs',
          icon: () => h(NIcon, null, { default: () => h(ListOutline) }),
        },
        {
          label: '意图日志',
          key: 'super-agent/intent-logs',
          icon: () => h(NIcon, null, { default: () => h(SearchOutline) }),
        },
        {
          label: '辩论配置',
          key: 'super-agent/debate',
          icon: () => h(NIcon, null, { default: () => h(ChatbubblesOutline) }),
        },
        {
          label: '专家管理',
          key: 'super-agent/experts',
          icon: () => h(NIcon, null, { default: () => h(PeopleOutline) }),
        },
        {
          label: '工具管理',
          key: 'super-agent/tools',
          icon: () => h(NIcon, null, { default: () => h(BuildOutline) }),
        },
        {
          label: 'Token用量',
          key: 'super-agent/token-usage',
          icon: () => h(NIcon, null, { default: () => h(AnalyticsOutline) }),
        },
        {
          label: '订阅监控',
          key: 'super-agent/subscription-monitor',
          icon: () => h(NIcon, null, { default: () => h(EyeOutline) }),
        },
        {
          label: '订阅模板',
          key: 'super-agent/subscription-templates',
          icon: () => h(NIcon, null, { default: () => h(ListOutline) }),
        },
      ],
    })
  }

  return options
})

// 当前选中的菜单项
const activeKey = computed(() => {
  const path = route.path
  if (path.includes('/users')) return 'users'
  if (path.includes('/roles')) return 'roles'
  if (path.includes('/documents/upload')) return 'documents/upload'
  if (path.includes('/documents')) return 'documents'
  if (path.includes('/agents')) return 'agents'
  if (path.includes('/settings')) return 'settings'
  if (path.includes('/channel-management')) return 'channel-management'
  if (path.includes('/statistics')) return 'statistics'
  // 超级智能体子菜单
  if (path.includes('/super-agent/assistants')) return 'super-agent/assistants'
  if (path.includes('/super-agent/skills')) return 'super-agent/skills'
  if (path.includes('/super-agent/intent-logs')) return 'super-agent/intent-logs'
  if (path.includes('/super-agent/logs')) return 'super-agent/logs'
  if (path.includes('/super-agent/debate')) return 'super-agent/debate'
  if (path.includes('/super-agent/experts')) return 'super-agent/experts'
  if (path.includes('/super-agent/tools')) return 'super-agent/tools'
  if (path.includes('/super-agent/token-usage')) return 'super-agent/token-usage'
  if (path.includes('/super-agent/subscription-monitor')) return 'super-agent/subscription-monitor'
  if (path.includes('/super-agent/subscription-templates')) return 'super-agent/subscription-templates'
  return 'dashboard'
})

// 面包屑
const breadcrumbItems = computed(() => {
  const items = [{ key: 'home', title: '首页', path: '/dashboard' }]
  const path = route.path

  if (path.includes('/users')) {
    items.push({ key: 'users', title: '用户管理', path: '/users' })
  } else if (path.includes('/roles')) {
    items.push({ key: 'roles', title: '角色权限', path: '/roles' })
  } else if (path.includes('/documents/upload')) {
    items.push({ key: 'documents', title: '公共知识库管理', path: '/documents' })
    items.push({ key: 'documents-upload', title: '上传文件', path: '/documents/upload' })
  } else if (path.includes('/documents')) {
    items.push({ key: 'documents', title: '公共知识库管理', path: '/documents' })
  } else if (path.includes('/agents')) {
    items.push({ key: 'agents', title: '智能体管理', path: '/agents' })
  } else if (path.includes('/settings')) {
    items.push({ key: 'settings', title: '系统设置', path: '/settings' })
  } else if (path.includes('/channel-management')) {
    items.push({ key: 'channel-management', title: '消息频道', path: '/channel-management' })
  } else if (path.includes('/statistics')) {
    items.push({ key: 'statistics', title: '统计分析', path: '/statistics' })
  } else if (path.includes('/super-agent/assistants')) {
    items.push({ key: 'super-agent', title: '超级智能体', path: '/super-agent/assistants' })
    items.push({ key: 'super-agent-assistants', title: 'AI助手管理', path: '/super-agent/assistants' })
  } else if (path.includes('/super-agent/skills')) {
    items.push({ key: 'super-agent', title: '超级智能体', path: '/super-agent/skills' })
    items.push({ key: 'super-agent-skills', title: 'Skills管理', path: '/super-agent/skills' })
  } else if (path.includes('/super-agent/intent-logs')) {
    items.push({ key: 'super-agent', title: '超级智能体', path: '/super-agent/skills' })
    items.push({ key: 'super-agent-intent-logs', title: '意图日志', path: '/super-agent/intent-logs' })
  } else if (path.includes('/super-agent/logs')) {
    items.push({ key: 'super-agent', title: '超级智能体', path: '/super-agent/skills' })
    items.push({ key: 'super-agent-logs', title: '执行日志', path: '/super-agent/logs' })
  } else if (path.includes('/super-agent/debate')) {
    items.push({ key: 'super-agent', title: '超级智能体', path: '/super-agent/skills' })
    items.push({ key: 'super-agent-debate', title: '辩论配置', path: '/super-agent/debate' })
  } else if (path.includes('/super-agent/experts')) {
    items.push({ key: 'super-agent', title: '超级智能体', path: '/super-agent/skills' })
    items.push({ key: 'super-agent-experts', title: '专家管理', path: '/super-agent/experts' })
  } else if (path.includes('/super-agent/tools')) {
    items.push({ key: 'super-agent', title: '超级智能体', path: '/super-agent/skills' })
    items.push({ key: 'super-agent-tools', title: '工具管理', path: '/super-agent/tools' })
  } else if (path.includes('/super-agent/token-usage')) {
    items.push({ key: 'super-agent', title: '超级智能体', path: '/super-agent/skills' })
    items.push({ key: 'super-agent-token-usage', title: 'Token用量', path: '/super-agent/token-usage' })
  } else if (path.includes('/super-agent/subscription-monitor')) {
    items.push({ key: 'super-agent', title: '超级智能体', path: '/super-agent/skills' })
    items.push({ key: 'sub-monitor', title: '订阅监控', path: '/super-agent/subscription-monitor' })
  } else if (path.includes('/super-agent/subscription-templates')) {
    items.push({ key: 'super-agent', title: '超级智能体', path: '/super-agent/skills' })
    items.push({ key: 'sub-templates', title: '订阅模板', path: '/super-agent/subscription-templates' })
  } else if (path.includes('/dashboard')) {
    items.push({ key: 'dashboard', title: '仪表板', path: '/dashboard' })
  }

  return items
})

// 用户下拉菜单
const userMenuOptions = [
  {
    label: '个人信息',
    key: 'profile',
    icon: () => h(NIcon, null, { default: () => h(PersonOutline) }),
  },
  {
    label: '修改密码',
    key: 'password',
    icon: () => h(NIcon, null, { default: () => h(KeyOutline) }),
  },
  {
    type: 'divider',
    key: 'd1'
  },
  {
    label: '退出登录',
    key: 'logout',
    icon: () => h(NIcon, null, { default: () => h(LogOutOutline) }),
  },
]

// 菜单点击
function handleMenuClick(key: string) {
  router.push(`/${key}`)
}

// 用户菜单点击
function handleUserMenuClick(key: string) {
  if (key === 'logout') {
    handleLogout()
  } else if (key === 'profile') {
    message.info('个人信息功能开发中')
  } else if (key === 'password') {
    message.info('修改密码功能开发中')
  }
}

// 登出
async function handleLogout() {
  await userStore.logout()
  message.success('已退出登录')
  router.push('/login')
}

// 获取角色显示名
function getRoleName(role: string): string {
  const names: Record<string, string> = {
    super_admin: '超级管理员',
    admin: '系统管理员',
    editor: '编辑员',
    user: '普通用户'
  }
  return names[role] || role
}
</script>

<template>
  <n-layout has-sider style="height: 100vh">
    <!-- 侧边栏 -->
    <n-layout-sider
      bordered
      collapse-mode="width"
      :collapsed-width="64"
      :width="220"
      :collapsed="collapsed"
      show-trigger
      @collapse="collapsed = true"
      @expand="collapsed = false"
      :native-scrollbar="false"
      style="background: #001529"
    >
      <div class="logo" :class="{ collapsed }">
        <span v-if="!collapsed">终身教育智能体</span>
        <span v-else>科</span>
      </div>
      <n-menu
        :collapsed="collapsed"
        :collapsed-width="64"
        :collapsed-icon-size="22"
        :options="menuOptions"
        :value="activeKey"
        @update:value="handleMenuClick"
        :root-indent="24"
        :indent="24"
        inverted
      />
    </n-layout-sider>

    <!-- 主体 -->
    <n-layout>
      <!-- 顶部 -->
      <n-layout-header bordered style="height: 64px; padding: 0 24px; display: flex; align-items: center; justify-content: space-between;">
        <n-breadcrumb>
          <n-breadcrumb-item v-for="item in breadcrumbItems" :key="item.key">
            <router-link :to="item.path">{{ item.title }}</router-link>
          </n-breadcrumb-item>
        </n-breadcrumb>

        <n-dropdown
          :options="userMenuOptions"
          @select="handleUserMenuClick"
          trigger="click"
        >
          <div class="user-info">
            <n-avatar :size="32" round>
              {{ userStore.user?.username?.charAt(0)?.toUpperCase() || 'A' }}
            </n-avatar>
            <div class="user-detail">
              <span class="username">{{ userStore.user?.username || '管理员' }}</span>
              <span class="role">{{ getRoleName(userStore.roles?.[0] || '') }}</span>
            </div>
          </div>
        </n-dropdown>
      </n-layout-header>

      <!-- 内容区 -->
      <n-layout-content
        content-style="padding: 24px; background: #f5f7f9;"
        :native-scrollbar="false"
      >
        <router-view />
      </n-layout-content>
    </n-layout>
  </n-layout>
</template>

<style scoped>
.logo {
  height: 64px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  font-size: 18px;
  font-weight: 600;
  background: rgba(255, 255, 255, 0.1);
  transition: all 0.3s;
}

.logo.collapsed {
  font-size: 20px;
}

.user-info {
  display: flex;
  align-items: center;
  gap: 12px;
  cursor: pointer;
  padding: 8px 12px;
  border-radius: 8px;
  transition: background 0.2s;
}

.user-info:hover {
  background: #f5f5f5;
}

.user-detail {
  display: flex;
  flex-direction: column;
}

.username {
  font-size: 14px;
  font-weight: 500;
  color: #333;
}

.role {
  font-size: 12px;
  color: #999;
}
</style>
