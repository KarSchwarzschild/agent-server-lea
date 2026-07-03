<script setup lang="ts">
/**
 * Tab 3: 用户排行 — 表格 + 展开行
 */
import { NTable, NTag, NButton } from 'naive-ui'
import { ref } from 'vue'
import { featureLabel } from './constants'
import { formatTokens, type UserRow } from '../../api/token-usage'

const props = defineProps<{ data: UserRow[] }>()
const emit = defineEmits<{ (e: 'trace', sessionId: string): void }>()

const expandedUser = ref<string | null>(null)

function toggle(uid: string) {
  expandedUser.value = expandedUser.value === uid ? null : uid
}
</script>

<template>
  <NTable :bordered="false" :single-line="false" size="small">
    <thead>
      <tr>
        <th style="width: 30px"></th>
        <th>#</th>
        <th>用户名</th>
        <th>总Token</th>
        <th>调用次数</th>
        <th>会话数</th>
        <th>主要功能</th>
      </tr>
    </thead>
    <tbody>
      <template v-for="(u, idx) in data" :key="u.user_id">
        <tr class="user-row" @click="toggle(u.user_id)">
          <td style="text-align: center; color: #999; font-size: 12px">
            <span :style="{ display: 'inline-block', transition: 'transform 0.2s', transform: expandedUser === u.user_id ? 'rotate(90deg)' : '' }">▶</span>
          </td>
          <td>{{ idx + 1 }}</td>
          <td><b>{{ u.username }}</b></td>
          <td>{{ formatTokens(u.total_tokens) }}</td>
          <td>{{ u.call_count }}</td>
          <td>{{ u.session_count }}</td>
          <td><NTag size="small">{{ featureLabel(u.top_feature) }}</NTag></td>
        </tr>
        <tr v-if="expandedUser === u.user_id">
          <td colspan="7" style="background: #fafafa; padding: 8px 16px">
            <div style="font-size: 13px; margin-bottom: 6px; color: #666">📋 最近会话:</div>
            <div v-for="s in u.recent_sessions" :key="s.session_id" style="margin-bottom: 4px; padding-left: 8px">
              <NButton text size="small" type="primary" @click.stop="emit('trace', s.session_id)">
                {{ s.title || s.session_id }}
              </NButton>
              <span style="margin-left: 8px; color: #999; font-size: 12px">{{ formatTokens(s.total_tokens) }} · {{ s.created_at }}</span>
            </div>
            <div v-if="!u.recent_sessions?.length" style="color: #999">无会话记录</div>
          </td>
        </tr>
      </template>
    </tbody>
  </NTable>
</template>

<style scoped>
.user-row {
  cursor: pointer;
  transition: background-color 0.15s;
}
.user-row:hover {
  background-color: #f5f7fa;
}
</style>
