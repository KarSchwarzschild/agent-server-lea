<template>
  <div class="mobile-tab-bar">
    <div
      v-for="tab in tabs"
      :key="tab.path"
      :class="['tab-item', { active: isActive(tab.path) }]"
      @click="navigateTo(tab.path)"
    >
      <n-icon :component="tab.icon" :size="24" />
      <span class="tab-label">{{ tab.label }}</span>
    </div>
    <!-- iOS安全区域 -->
    <div class="safe-area-bottom" />
  </div>
</template>

<script setup lang="ts">
/**
 * MobileTabBar - 移动端底部Tab导航
 * 方案C设计：4个Tab（问答/检索/综述/我的）
 * 点击"问答"进入超级智能体
 */
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NIcon } from 'naive-ui'
import {
  ChatbubblesOutline,
  SearchOutline,
  DocumentTextOutline,
  PersonOutline,
  NuclearOutline,
} from '@vicons/ionicons5'
import { isRouteEnabled } from '../config/features'

const route = useRoute()
const router = useRouter()

const allTabs = [
  { path: '/qa/super-agent', label: '问答', icon: ChatbubblesOutline },
  { path: '/search', label: '检索', icon: SearchOutline },
  { path: '/review', label: '综述', icon: DocumentTextOutline },
  { path: '/intelligence', label: '订阅', icon: NuclearOutline },
  { path: '/profile', label: '我的', icon: PersonOutline },
]

// 按 features.ts 过滤
const tabs = computed(() => allTabs.filter(tab => isRouteEnabled(tab.path)))

const isActive = (path: string) => {
  // 精确匹配或前缀匹配
  return route.path === path || route.path.startsWith(path + '/')
}

const navigateTo = (path: string) => {
  if (route.path !== path) {
    router.push(path)
  }
}
</script>

<style scoped>
.mobile-tab-bar {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  display: flex;
  flex-wrap: wrap;
  background: #fff;
  border-top: 1px solid #eee;
  padding-top: 8px;
  padding-bottom: 8px;
  z-index: var(--le-z-tab-bar, 100);
}

.tab-item {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 4px 0;
  color: #999;
  transition: color 0.2s;
  cursor: pointer;
  /* 确保点击区域足够大 - 44px最小触摸区域 */
  min-height: 44px;
  -webkit-tap-highlight-color: transparent;
  touch-action: manipulation;
}

.tab-item:active {
  opacity: 0.7;
}

.tab-item.active {
  color: var(--le-color-primary, #2080f0);
}

.tab-label {
  font-size: 11px;
  line-height: 1.2;
}

.safe-area-bottom {
  width: 100%;
  height: env(safe-area-inset-bottom, 0px);
  flex-shrink: 0;
}
</style>
