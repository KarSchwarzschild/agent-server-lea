<script setup lang="ts">
/**
 * 用户端布局 - 响应式版本
 * PC端：顶部水平导航（问答菜单使用下拉）
 * 移动端：底部Tab导航
 */
import { computed, h } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import {
  NLayout,
  NLayoutHeader,
  NLayoutContent,
  NMenu,
  NButton,
  NIcon,
} from 'naive-ui'
import {
  ChatbubblesOutline,
  SearchOutline,
  DocumentTextOutline,
  TimeOutline,
  PersonOutline,
  LogOutOutline,
  FolderOpenOutline,
  NuclearOutline,
  NotificationsOutline,
  AppsOutline,
} from '@vicons/ionicons5'
import { useUserStore } from '../stores/user'
import { useResponsive } from '../composables/useResponsive'
import MobileTabBar from '../components/MobileTabBar.vue'
import NotificationCenter from '../components/NotificationCenter.vue'
import { isRouteEnabled, isFeatureEnabled } from '../config/features'

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()
const { isMobile } = useResponsive()

const user = computed(() => userStore.user)

// PC端菜单选项（未过滤）
const allMenuOptions = [
  {
    label: '超级智能体',
    key: '/qa/super-agent',
    icon: () => h(NIcon, null, { default: () => h(ChatbubblesOutline) })
  },
  {
    label: '文献检索',
    key: '/search',
    icon: () => h(NIcon, null, { default: () => h(SearchOutline) })
  },
  {
    label: '综述生成',
    key: '/review',
    icon: () => h(NIcon, null, { default: () => h(DocumentTextOutline) })
  },
  {
    label: '个人知识库',
    key: '/knowledge-base',
    icon: () => h(NIcon, null, { default: () => h(FolderOpenOutline) })
  },
  {
    label: '智能订阅',
    key: '/intelligence',
    icon: () => h(NIcon, null, { default: () => h(NuclearOutline) })
  },
  {
    label: '助手广场',
    key: '/agent-plaza',
    icon: () => h(NIcon, null, { default: () => h(AppsOutline) })
  },
  {
    label: '历史记录',
    key: '/history',
    icon: () => h(NIcon, null, { default: () => h(TimeOutline) })
  },
  {
    label: '个人中心',
    key: '/profile',
    icon: () => h(NIcon, null, { default: () => h(PersonOutline) })
  },
]

// 按 features.ts 过滤菜单：/qa/super-agent 特殊处理（root key 不在 ROUTE_FEATURE_MAP）
const menuOptions = computed(() =>
  allMenuOptions.filter(item => isRouteEnabled(item.key))
)

const userMenuOptions = computed(() => {
  const items: any[] = []
  if (isFeatureEnabled('CHANNELS')) {
    items.push({
      label: '消息渠道',
      key: 'channels',
      icon: () => h(NIcon, null, { default: () => h(NotificationsOutline) })
    })
  }
  items.push({
    label: '退出登录',
    key: 'logout',
    icon: () => h(NIcon, null, { default: () => h(LogOutOutline) })
  })
  return items
})

const activeKey = computed(() => route.path)

const handleMenuSelect = (key: string) => {
  router.push(key)
}

const handleUserMenuSelect = (key: string) => {
  if (key === 'logout') {
    userStore.logout()
    router.push('/login')
  } else if (key === 'channels') {
    router.push('/settings/channels')
  }
}
</script>

<template>
  <n-layout class="layout-container">
    <!-- PC端：顶部导航 -->
    <n-layout-header v-if="!isMobile" class="layout-header" bordered>
      <div class="header-content">
        <!-- Logo -->
        <div class="logo">
          <span class="logo-text">终身教育智能体</span>
        </div>

        <!-- 导航区域 -->
        <div class="nav-area">
          <!-- 导航菜单 -->
          <n-menu
            mode="horizontal"
            :value="activeKey"
            :options="menuOptions"
            @update:value="handleMenuSelect"
          />
        </div>

        <!-- 通知 + 用户信息 -->
        <div class="user-info">
          <NotificationCenter />
          <n-dropdown
            :options="userMenuOptions"
            @select="handleUserMenuSelect"
          >
            <n-button quaternary>
              <template #icon>
                <n-icon :component="PersonOutline" />
              </template>
              {{ user?.username || '用户' }}
            </n-button>
          </n-dropdown>
        </div>
      </div>
    </n-layout-header>

    <!-- 主内容区 -->
    <n-layout-content
      :class="[
        'layout-content',
        { 'layout-content-mobile': isMobile }
      ]"
    >
      <router-view />
    </n-layout-content>

    <!-- 移动端：底部Tab导航 -->
    <MobileTabBar v-if="isMobile" />
  </n-layout>
</template>

<style scoped>
.layout-container {
  height: 100vh;
}

/* ===== PC端样式 ===== */
.layout-header {
  height: 60px;
  padding: 0 24px;
  display: flex;
  align-items: center;
  background: #fff;
}

.header-content {
  width: 100%;
  max-width: 1400px;
  margin: 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.logo {
  display: flex;
  align-items: center;
  flex-shrink: 0;
}

.logo-text {
  font-size: 20px;
  font-weight: 700;
  color: #2080f0;
  white-space: nowrap;
}

/* 导航区域 */
.nav-area {
  display: flex;
  align-items: center;
  gap: 0;
}

.user-info {
  display: flex;
  align-items: center;
  gap: 8px;
}

.layout-content {
  height: calc(100vh - 60px);
  background: #f5f7f9;
  overflow: hidden;
}

/* ===== 移动端样式 ===== */
.layout-content-mobile {
  height: 100vh;
  /* 底部留出Tab栏空间：56px Tab高度 + safe-area */
  padding-bottom: calc(56px + env(safe-area-inset-bottom, 0px));
}

:deep(.n-menu) {
  background: transparent;
}

:deep(.n-menu-item) {
  height: 60px;
}
</style>
