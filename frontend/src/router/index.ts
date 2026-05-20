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
