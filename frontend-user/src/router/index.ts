import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'
import { isRouteEnabled } from '../config/features'

const routes: RouteRecordRaw[] = [
  {
    path: '/share/:shareId',
    name: 'PublicShare',
    component: () => import('../views/PublicShare.vue'),
    meta: { title: '全文查看', requiresAuth: false }
  },
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
        redirect: '/qa/super-agent'
      },
      // 方案C：问答模块（嵌套路由）
      {
        path: 'qa',
        component: () => import('../views/QaIndex.vue'),
        children: [
          {
            path: '',
            redirect: '/qa/super-agent'
          },
          {
            path: 'chat',
            redirect: '/qa/super-agent'
          },
          {
            path: 'super-agent/:sessionId?',
            name: 'SuperAgent',
            component: () => import('../views/SuperAgent.vue'),
            meta: { title: '超级智能体' }
          },
          {
            path: 'documents',
            name: 'Documents',
            component: () => import('../views/Documents.vue'),
            meta: { title: '文档中心' }
          }
        ]
      },
      // 兼容旧路由：重定向到超级智能体
      {
        path: 'chat',
        redirect: '/qa/super-agent'
      },
      {
        path: 'super-agent/:sessionId?',
        redirect: to => `/qa/super-agent/${(to.params.sessionId as string) || ''}`.replace(/\/$/, '')
      },
      {
        path: 'agent-plaza',
        name: 'AgentPlaza',
        component: () => import('../views/AgentPlaza.vue'),
        meta: { title: '助手广场' }
      },
      {
        path: 'search',
        name: 'Search',
        component: () => import('../views/Search.vue'),
        meta: { title: '文献检索' }
      },
      {
        path: 'review',
        redirect: '/review/history'
      },
      {
        path: 'review/quick',
        name: 'QuickReview',
        component: () => import('../views/Review.vue'),
        meta: { title: '快速综述' }
      },
      {
        path: 'review/history',
        name: 'ReviewHistory',
        component: () => import('../views/report/ReviewHistory.vue'),
        meta: { title: '综述历史' }
      },
      {
        path: 'review/new',
        name: 'DeepReview',
        component: () => import('../views/report/DeepReviewWizard.vue'),
        meta: { title: '深度综述' },
      },
      {
        path: 'review/progress/:taskId',
        name: 'ReportProgress',
        component: () => import('../views/report/ReportProgress.vue'),
        meta: { title: '综述生成中' },
      },
      {
        path: 'review/report/:taskId',
        name: 'ReportViewer',
        component: () => import('../views/report/ReportViewer.vue'),
        meta: { title: '综述报告' },
      },
      {
        path: 'knowledge-base',
        name: 'KnowledgeBase',
        component: () => import('../views/UserKnowledgeBase.vue'),
        meta: { title: '个人知识库' }
      },
      {
        path: 'intelligence',
        name: 'Intelligence',
        component: () => import('../views/Subscription/index.vue'),
        meta: { title: '智能订阅' }
      },
      {
        path: 'intelligence/:subId/results',
        name: 'SubscriptionResults',
        component: () => import('../views/Subscription/ResultList.vue'),
        meta: { title: '订阅结果' }
      },
      {
        path: 'intelligence/:subId/results/:resultId',
        name: 'SubscriptionReport',
        component: () => import('../views/Subscription/ReportView.vue'),
        meta: { title: '执行报告' }
      },
      {
        path: 'history',
        name: 'History',
        component: () => import('../views/History.vue'),
        meta: { title: '历史记录' }
      },
      {
        path: 'profile',
        name: 'Profile',
        component: () => import('../views/Profile.vue'),
        meta: { title: '个人中心' }
      },
      {
        path: 'settings/channels',
        name: 'ChannelBinding',
        component: () => import('../views/Settings/ChannelBinding.vue'),
        meta: { title: '消息渠道管理' }
      }
    ]
  }
]

if (import.meta.env.DEV) {
  routes.unshift({
    path: '/_eval/classroom-scene-render',
    name: 'ClassroomSceneRenderEval',
    component: () => import('../views/eval/ClassroomSceneRenderEval.vue'),
    meta: { title: 'Classroom Scene Eval', requiresAuth: false }
  })
  routes.unshift({
    path: '/_eval/classroom-slide-render',
    name: 'ClassroomSlideRenderEval',
    component: () => import('../views/eval/ClassroomSlideRenderEval.vue'),
    meta: { title: 'Classroom Slide Eval', requiresAuth: false }
  })
}

const router = createRouter({
  history: createWebHistory(),
  routes
})

// 路由守卫
router.beforeEach((to, _from, next) => {
  const token = localStorage.getItem('token')

  // 特性开关：未点亮的路由重定向到主聊天
  if (!isRouteEnabled(to.path)) {
    next('/qa/super-agent')
    return
  }

  if (to.meta.requiresAuth !== false && !token) {
    next('/login')
  } else if (to.path === '/login' && token) {
    next('/qa/super-agent')
  } else {
    next()
  }
})

export default router
