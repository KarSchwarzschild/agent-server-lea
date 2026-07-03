import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('../views/Login.vue'),
    meta: { title: '登录', requiresAuth: false }
  },
  {
    path: '/',
    component: () => import('../views/Layout.vue'),
    meta: { requiresAuth: true },
    children: [
      {
        path: '',
        redirect: '/dashboard'
      },
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('../views/Dashboard.vue'),
        meta: { title: '仪表盘' }
      },
      {
        path: 'users',
        name: 'Users',
        component: () => import('../views/Users.vue'),
        meta: { title: '用户管理', permission: 'user:read' }
      },
      {
        path: 'roles',
        name: 'Roles',
        component: () => import('../views/Roles.vue'),
        meta: { title: '角色权限', permission: 'role:read' }
      },
      {
        path: 'documents',
        name: 'Documents',
        component: () => import('../views/Documents.vue'),
        meta: { title: '公共知识库管理', permission: 'document:read' }
      },
      {
        path: 'documents/upload',
        name: 'DocumentUpload',
        component: () => import('../views/DocumentUpload.vue'),
        meta: { title: '上传文件', permission: 'document:write' }
      },
      {
        path: 'agents',
        name: 'Agents',
        component: () => import('../views/AgentManagement.vue'),
        meta: { title: '智能体管理', permission: 'system:read' }
      },
      {
        path: 'settings',
        name: 'Settings',
        component: () => import('../views/Settings.vue'),
        meta: { title: '系统设置', permission: 'system:read' }
      },
      {
        path: 'statistics',
        name: 'Statistics',
        component: () => import('../views/Statistics.vue'),
        meta: { title: '统计分析', permission: 'stats:read' }
      },
      // 超级智能体管理
      {
        path: 'super-agent/assistants',
        name: 'SuperAgentManagement',
        component: () => import('../views/SuperAgentManagement.vue'),
        meta: { title: 'AI助手管理', permission: 'system:read' }
      },
      {
        path: 'super-agent/skills',
        name: 'SkillManagement',
        component: () => import('../views/SkillManagement.vue'),
        meta: { title: 'Skills管理', permission: 'system:read' }
      },
      {
        path: 'super-agent/logs',
        name: 'ExecutionLogs',
        component: () => import('../views/ExecutionLogs.vue'),
        meta: { title: '执行日志', permission: 'system:read' }
      },
      {
        path: 'super-agent/intent-logs',
        name: 'IntentLogs',
        component: () => import('../views/IntentLogs.vue'),
        meta: { title: '意图日志', permission: 'system:read' }
      },
      {
        path: 'super-agent/debate',
        name: 'DebateConfig',
        component: () => import('../views/DebateConfig.vue'),
        meta: { title: '辩论配置', permission: 'system:read' }
      },
      {
        path: 'super-agent/experts',
        name: 'DebateExpertManagement',
        component: () => import('../views/DebateExpertManagement.vue'),
        meta: { title: '专家管理', permission: 'system:read' }
      },
      {
        path: 'super-agent/tools',
        name: 'ToolManagement',
        component: () => import('../views/ToolManagement.vue'),
        meta: { title: '工具管理', permission: 'system:read' }
      },
      {
        path: 'super-agent/token-usage',
        name: 'TokenUsage',
        component: () => import('../views/TokenUsage.vue'),
        meta: { title: 'Token用量', permission: 'system:read' }
      },
      {
        path: 'super-agent/subscription-monitor',
        name: 'SubscriptionMonitor',
        component: () => import('../views/SubscriptionMonitor.vue'),
        meta: { title: '订阅监控', permission: 'system:read' }
      },
      {
        path: 'super-agent/subscription-templates',
        name: 'SubscriptionTemplates',
        component: () => import('../views/SubscriptionTemplates.vue'),
        meta: { title: '订阅模板', permission: 'system:read' }
      },
      // 频道管理（v1.4: OpenFang 架构迁移）
      {
        path: 'channel-management',
        name: 'ChannelManagement',
        component: () => import('../views/ChannelManagement.vue'),
        meta: { title: '消息频道', permission: 'system:read' }
      }
    ]
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

// 路由守卫
router.beforeEach((to, _from, next) => {
  const token = localStorage.getItem('token')

  if (to.meta.requiresAuth !== false && !token) {
    next('/login')
  } else if (to.path === '/login' && token) {
    next('/dashboard')
  } else {
    next()
  }
})

export default router
