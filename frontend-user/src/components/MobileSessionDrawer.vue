<template>
  <n-drawer
    v-model:show="visible"
    placement="bottom"
    :height="drawerHeight"
    :trap-focus="false"
  >
    <n-drawer-content>
      <template #header>
        <div class="drawer-header">
          <span class="drawer-title">历史对话</span>
          <n-button
            type="primary"
            size="small"
            @click="handleNewChat"
          >
            <template #icon>
              <n-icon :component="AddOutline" />
            </template>
            新建
          </n-button>
        </div>
      </template>

      <n-scrollbar style="max-height: calc(60vh - 60px)">
        <n-spin :show="loading">
          <div v-if="sessions.length === 0 && !loading" class="no-sessions">
            <n-empty description="暂无历史对话" size="small" />
          </div>
          <div
            v-for="session in sessions"
            :key="session.session_id"
            class="session-item"
            :class="{ active: session.session_id === currentSessionId }"
            @click="handleSelectSession(session)"
          >
            <n-icon :component="ChatbubbleOutline" class="session-icon" />
            <div class="session-info">
              <span class="session-title">{{ session.title || '未命名对话' }}</span>
              <span class="session-meta">
                {{ session.message_count || 0 }}条消息
              </span>
            </div>
            <n-popconfirm
              @positive-click="handleDelete(session.session_id)"
              positive-text="删除"
              negative-text="取消"
            >
              <template #trigger>
                <n-button
                  text
                  size="tiny"
                  class="delete-btn"
                  @click.stop
                >
                  <n-icon :component="TrashOutline" />
                </n-button>
              </template>
              确定删除这个对话吗？
            </n-popconfirm>
          </div>
        </n-spin>
      </n-scrollbar>
    </n-drawer-content>
  </n-drawer>
</template>

<script setup lang="ts">
/**
 * MobileSessionDrawer - 移动端会话列表底部抽屉
 */
import { computed } from 'vue'
import {
  NDrawer,
  NDrawerContent,
  NButton,
  NIcon,
  NScrollbar,
  NSpin,
  NEmpty,
  NPopconfirm,
} from 'naive-ui'
import {
  AddOutline,
  ChatbubbleOutline,
  TrashOutline,
} from '@vicons/ionicons5'
import type { Session } from '../api/chat'

const props = withDefaults(defineProps<{
  show: boolean
  sessions: Session[]
  currentSessionId: string
  loading?: boolean
}>(), {
  loading: false
})

const emit = defineEmits<{
  'update:show': [value: boolean]
  'select': [session: Session]
  'delete': [sessionId: string]
  'new-chat': []
}>()

const visible = computed({
  get: () => props.show,
  set: (val) => emit('update:show', val)
})

// 抽屉高度：60vh
const drawerHeight = '60vh'

const handleSelectSession = (session: Session) => {
  emit('select', session)
  visible.value = false
}

const handleDelete = (sessionId: string) => {
  emit('delete', sessionId)
}

const handleNewChat = () => {
  emit('new-chat')
  visible.value = false
}
</script>

<style scoped>
.drawer-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
}

.drawer-title {
  font-size: 16px;
  font-weight: 600;
}

.no-sessions {
  padding: 32px 0;
}

.session-item {
  display: flex;
  align-items: center;
  padding: 12px 16px;
  border-bottom: 1px solid #f0f0f0;
  cursor: pointer;
  transition: background 0.2s;
  min-height: 56px;
}

.session-item:active {
  background: #f5f5f5;
}

.session-item.active {
  background: #e8f4ff;
}

.session-icon {
  flex-shrink: 0;
  margin-right: 12px;
  color: #999;
}

.session-item.active .session-icon {
  color: var(--le-color-primary, #2080f0);
}

.session-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.session-title {
  font-size: 14px;
  color: #333;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.session-meta {
  font-size: 12px;
  color: #999;
}

.delete-btn {
  flex-shrink: 0;
  opacity: 0.6;
}

.delete-btn:active {
  opacity: 1;
}
</style>
