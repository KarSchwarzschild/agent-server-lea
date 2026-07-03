<template>
  <div class="session-list">
    <div class="session-header">
      <span class="session-title">对话列表</span>
      <button class="new-btn" @click="createSession">+ 新对话</button>
    </div>
    <div class="session-items">
      <div
        v-for="s in sessions"
        :key="s.session_id"
        :class="['session-item', { active: s.session_id === currentId }]"
        @click="$emit('select', s.session_id)"
      >
        <span class="item-title">{{ s.title }}</span>
        <span class="item-count">{{ s.message_count }}条</span>
      </div>
      <div v-if="!sessions.length" class="empty">暂无对话</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import request from '../../utils/request'

interface SessionItem {
  id: number
  session_id: string
  title: string
  message_count: number
  last_active_at: string
}

const props = defineProps<{ currentId: string }>()
const emit = defineEmits<{ select: [id: string] }>()

const sessions = ref<SessionItem[]>([])

async function fetchSessions() {
  try {
    const { data } = await request.get('/api/chat/sessions')
    sessions.value = data.sessions || []
  } catch { /* ignore */ }
}

async function createSession() {
  try {
    const { data } = await request.post('/api/chat/sessions', { title: '新对话' })
    await fetchSessions()
    emit('select', data.session_id)
  } catch { /* ignore */ }
}

onMounted(fetchSessions)
defineExpose({ refresh: fetchSessions })
</script>

<style scoped>
.session-list {
  width: 240px;
  border-right: 1px solid #e5e7eb;
  display: flex;
  flex-direction: column;
  height: 100%;
  background: #fafafa;
}

.session-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid #e5e7eb;
}

.session-title { font-size: 14px; font-weight: 600; color: #374151; }

.new-btn {
  font-size: 12px;
  padding: 4px 10px;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  background: #fff;
  cursor: pointer;
}
.new-btn:hover { background: #f3f4f6; }

.session-items {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}

.session-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  border-radius: 6px;
  cursor: pointer;
  margin-bottom: 4px;
}
.session-item:hover { background: #f3f4f6; }
.session-item.active { background: #e0e7ff; }

.item-title {
  font-size: 13px;
  color: #374151;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}

.item-count { font-size: 11px; color: #9ca3af; margin-left: 8px; }

.empty { text-align: center; color: #9ca3af; font-size: 13px; padding: 20px 0; }
</style>
