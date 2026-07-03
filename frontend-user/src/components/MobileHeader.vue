<template>
  <div class="mobile-header">
    <!-- 安全区域顶部 -->
    <div class="safe-area-top" />

    <div class="header-content">
      <!-- 左侧：返回按钮或占位 -->
      <div class="header-left">
        <n-button
          v-if="showBack"
          quaternary
          circle
          @click="handleBack"
        >
          <template #icon>
            <n-icon :component="ArrowBackOutline" :size="22" />
          </template>
        </n-button>
      </div>

      <!-- 中间：标题 -->
      <div class="header-title">
        {{ title }}
      </div>

      <!-- 右侧：操作区 -->
      <div class="header-right">
        <slot name="right" />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * MobileHeader - 移动端顶部导航栏
 */
import { useRouter } from 'vue-router'
import { NButton, NIcon } from 'naive-ui'
import { ArrowBackOutline } from '@vicons/ionicons5'

const props = withDefaults(defineProps<{
  title?: string
  showBack?: boolean
}>(), {
  title: '',
  showBack: false
})

const emit = defineEmits<{
  back: []
}>()

const router = useRouter()

const handleBack = () => {
  emit('back')
  // 如果没有自定义处理，默认返回上一页
  if (window.history.length > 1) {
    router.back()
  } else {
    router.push('/qa/super-agent')
  }
}
</script>

<style scoped>
.mobile-header {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  background: #fff;
  border-bottom: 1px solid #eee;
  z-index: var(--le-z-header, 100);
}

.safe-area-top {
  height: env(safe-area-inset-top, 0px);
  background: #fff;
}

.header-content {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: var(--le-header-height, 48px);
  padding: 0 8px;
}

.header-left,
.header-right {
  width: 48px;
  display: flex;
  align-items: center;
}

.header-left {
  justify-content: flex-start;
}

.header-right {
  justify-content: flex-end;
}

.header-title {
  flex: 1;
  text-align: center;
  font-size: 17px;
  font-weight: 600;
  color: #333;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
