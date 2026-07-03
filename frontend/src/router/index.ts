import { createRouter, createWebHistory } from 'vue-router'
import { useUserStore } from '../stores/user'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'Login',
      component: () => import('../views/Login.vue'),
    },
    {
      path: '/',
      component: () => import('../shell/AppShell.vue'),
      children: [
        {
          path: '',
          redirect: '/chat',
        },
        {
          path: 'chat',
          name: 'Chat',
          component: () => import('../chat/ChatView.vue'),
        },
        {
          path: 'research',
          name: 'Research',
          component: () => import('../research/ResearchView.vue'),
        },
        {
          path: 'research/:id',
          name: 'ReviewFlow',
          component: () => import('../research/ReviewFlow.vue'),
        },
        {
          path: 'notebook',
          name: 'Notebook',
          component: () => import('../notebook/NotebookView.vue'),
        },
        {
          path: 'knowledge',
          name: 'Knowledge',
          component: () => import('../knowledge/KnowledgeList.vue'),
        },
        {
          path: 'knowledge/:id',
          name: 'KnowledgeDetail',
          component: () => import('../knowledge/KnowledgeDetail.vue'),
        },
        {
          path: 'admin',
          component: () => import('../admin/AdminLayout.vue'),
          children: [
            {
              path: '',
              redirect: '/admin/dashboard',
            },
            {
              path: 'dashboard',
              name: 'Dashboard',
              component: () => import('../admin/Dashboard.vue'),
            },
            {
              path: 'apps',
              name: 'Apps',
              component: () => import('../admin/Apps.vue'),
            },
            {
              path: 'tools',
              name: 'Tools',
              component: () => import('../admin/Tools.vue'),
            },
            {
              path: 'skills',
              name: 'Skills',
              component: () => import('../admin/Skills.vue'),
            },
            {
              path: 'knowledge',
              name: 'AdminKnowledge',
              component: () => import('../admin/Knowledge.vue'),
            },
            {
              path: 'monitoring',
              name: 'Monitoring',
              component: () => import('../admin/Monitoring.vue'),
            },
          ],
        },
      ],
    },
  ],
})

router.beforeEach((to) => {
  const userStore = useUserStore()
  if (to.path !== '/login' && !userStore.isLoggedIn) {
    return '/login'
  }
})

export default router
