<template>
  <n-layout has-sider style="height: 100vh">
    <n-layout-sider
      bordered
      :width="220"
      :collapsed-width="64"
      show-trigger
      collapse-mode="width"
    >
      <div class="logo">LEA</div>
      <n-menu
        :value="activeKey"
        :options="menuOptions"
        @update:value="handleMenuSelect"
      />
    </n-layout-sider>
    <n-layout>
      <n-layout-header bordered style="height: 50px; padding: 0 16px; display: flex; align-items: center; justify-content: space-between">
        <span style="font-weight: 500">{{ pageTitle }}</span>
        <n-dropdown :options="userMenuOptions" @select="handleUserMenu">
          <n-button text>{{ userStore.userInfo?.username || '用户' }}</n-button>
        </n-dropdown>
      </n-layout-header>
      <n-layout-content style="padding: 16px">
        <router-view />
      </n-layout-content>
    </n-layout>
  </n-layout>
</template>

<script setup lang="ts">
import { computed, ref, onMounted, h } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import {
  NLayout, NLayoutSider, NLayoutHeader, NLayoutContent,
  NMenu, NButton, NDropdown
} from 'naive-ui'
import { ChatbubbleEllipsesOutline, SettingsOutline, AppsOutline, LibraryOutline } from '@vicons/ionicons5'
import { NIcon } from 'naive-ui'
import { useUserStore } from '../stores/user'
import request from '../utils/request'

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()

const activeKey = computed(() => route.path)
const pageTitle = computed(() => {
  if (route.path.startsWith('/chat')) return '智能对话'
  if (route.path.startsWith('/admin')) return '管理后台'
  return 'LEA Platform'
})

interface AppInfo {
  name: string
  display_name: string
  routes: { path: string; label: string; icon: string }[]
}
const dynamicApps = ref<AppInfo[]>([])

function renderIcon(icon: any) {
  return () => h(NIcon, null, { default: () => h(icon) })
}

const menuOptions = computed(() => {
  const items: any[] = [
    { label: '智能对话', key: '/chat', icon: renderIcon(ChatbubbleEllipsesOutline) },
    { label: '知识库', key: '/knowledge', icon: renderIcon(LibraryOutline) },
  ]

  for (const app of dynamicApps.value) {
    for (const r of app.routes) {
      items.push({ label: r.label, key: r.path, icon: renderIcon(AppsOutline) })
    }
  }

  items.push({ label: '管理后台', key: '/admin/dashboard', icon: renderIcon(SettingsOutline) })

  return items
})

const userMenuOptions = [
  { label: '退出登录', key: 'logout' },
]

function handleMenuSelect(key: string) {
  router.push(key)
}

function handleUserMenu(key: string) {
  if (key === 'logout') {
    userStore.logout()
    router.push('/login')
  }
}

onMounted(async () => {
  if (!userStore.userInfo) {
    try { await userStore.fetchUserInfo() } catch { /* ignore */ }
  }

  try {
    const { data } = await request.get('/api/admin/apps')
    dynamicApps.value = data.apps || []
  } catch { /* ignore */ }
})
</script>

<style scoped>
.logo {
  height: 50px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  font-weight: bold;
  border-bottom: 1px solid var(--n-border-color);
}
</style>
