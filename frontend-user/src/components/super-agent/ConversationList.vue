<script setup lang="ts">
/**
 * 会话列表组件（PC端左侧栏内容）
 * 包含：新建对话按钮、Skills概览、历史会话列表
 */
import {
  NButton,
  NTag,
  NSpin,
  NIcon,
  NScrollbar,
  NEmpty,
  NPopconfirm,
  NBadge,
} from 'naive-ui'
import {
  AddOutline,
  TrashOutline,
  ChatbubbleOutline,
  ChevronDownOutline,
} from '@vicons/ionicons5'
import type { Skill, SuperAgentSession, AgentInfo } from '../../api/superagent'

const props = defineProps<{
  sessions: SuperAgentSession[]
  sessionsLoading: boolean
  currentSessionId: string
  skills: Skill[]
  currentSkill: string
  currentAgent?: AgentInfo | null
  defaultAgentId?: number | null
}>()

const emit = defineEmits<{
  (e: 'new-chat'): void
  (e: 'switch-session', sessionId: string): void
  (e: 'delete-session', sessionId: string): void
  (e: 'manage-agents'): void
  (e: 'set-default', agentId: number): void
  (e: 'clear-default'): void
}>()

// Skills 面板展开状态（组件内部管理）
import { ref } from 'vue'
const showSkillsPanel = ref(false)
</script>

<template>
  <div class="sider-content">
    <div class="sider-header">
      <n-button type="primary" block @click="emit('new-chat')">
        <template #icon>
          <n-icon :component="AddOutline" />
        </template>
        新建对话
      </n-button>
    </div>

    <!-- 当前助手指示 -->
    <div v-if="props.currentAgent" class="current-agent-indicator">
      <span class="indicator-avatar">{{ props.currentAgent.avatar || '🤖' }}</span>
      <span class="indicator-name">{{ props.currentAgent.name }}</span>
      <span
        class="indicator-star"
        :class="{ active: props.defaultAgentId === props.currentAgent.id }"
        @click="props.defaultAgentId === props.currentAgent.id ? emit('clear-default') : emit('set-default', props.currentAgent.id)"
        :title="props.defaultAgentId === props.currentAgent.id ? '取消默认' : '设为默认'"
      >{{ props.defaultAgentId === props.currentAgent.id ? '\u2B50' : '\u2606' }}</span>
      <n-button text size="tiny" @click="emit('manage-agents')" title="切换助手" class="indicator-switch">
        切换
      </n-button>
    </div>

    <!-- Skills概览 -->
    <div class="skills-overview">
      <div class="skills-header" @click="showSkillsPanel = !showSkillsPanel">
        <span class="skills-title">{{ props.currentAgent ? `${props.currentAgent.name}的专长` : '可用技能' }}</span>
        <n-badge :value="props.skills.length" type="info" />
        <n-icon
          :component="ChevronDownOutline"
          class="expand-icon"
          :class="{ expanded: showSkillsPanel }"
        />
      </div>
      <div v-if="showSkillsPanel" class="skills-list">
        <div
          v-for="skill in props.skills"
          :key="skill.name"
          class="skill-item"
          :class="{ active: props.currentSkill === skill.name }"
        >
          <span class="skill-icon">{{ skill.icon }}</span>
          <span class="skill-name">{{ skill.display_name }}</span>
        </div>
      </div>
    </div>

    <div class="sessions-title">
      <span>{{ props.currentAgent ? `${props.currentAgent.name}的对话` : '历史对话' }}</span>
      <a href="/history" class="view-all-link" title="查看所有助手的历史记录">全部历史</a>
    </div>

    <div class="session-scroll-wrapper">
    <n-scrollbar>
      <n-spin :show="props.sessionsLoading">
        <div v-if="props.sessions.length === 0 && !props.sessionsLoading" class="no-sessions">
          <n-empty :description="props.currentAgent ? `暂无${props.currentAgent.name}的历史对话` : '暂无历史对话'" size="small" />
        </div>
        <div
          v-for="session in props.sessions"
          :key="session.session_id"
          class="session-item"
          :class="{ active: session.session_id === props.currentSessionId }"
          @click="emit('switch-session', session.session_id)"
        >
          <n-icon :component="ChatbubbleOutline" class="session-icon" />
          <div class="session-info">
            <span class="session-title">{{ session.title || '未命名对话' }}</span>
            <span v-if="!props.currentAgent && session.agent_name" class="session-agent-name">{{ session.agent_name }}</span>
          </div>
          <n-popconfirm
            @positive-click="emit('delete-session', session.session_id)"
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
    </div>

    <!-- 侧边栏底部 -->
    <div class="sider-footer">
      <n-button text size="small" @click="emit('manage-agents')">
        ⚙️ 管理我的助手
      </n-button>
    </div>
  </div>
</template>

<style scoped>
.sider-content {
  display: flex;
  flex-direction: column;
  height: 100%;
  max-height: 100%;
  overflow: hidden;
  padding: 16px;
  box-sizing: border-box;
}

.sider-header {
  margin-bottom: 16px;
}

.current-agent-indicator {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  margin-bottom: 12px;
  background: #f0f7ff;
  border-radius: 8px;
  border: 1px solid #d6e4ff;
}

.indicator-avatar {
  font-size: 18px;
  line-height: 1;
}

.indicator-name {
  flex: 1;
  font-size: 13px;
  font-weight: 500;
  color: #333;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.indicator-star {
  flex-shrink: 0;
  font-size: 16px;
  cursor: pointer;
  color: #ccc;
  transition: all 0.2s;
  line-height: 1;
}

.indicator-star:hover {
  color: #f0a020;
  transform: scale(1.15);
}

.indicator-star.active {
  color: #f0a020;
}

.indicator-switch {
  flex-shrink: 0;
  font-size: 12px;
  color: #2080f0 !important;
}

/* Skills概览 */
.skills-overview {
  margin-bottom: 16px;
  padding: 12px;
  background: #f8fafc;
  border-radius: 8px;
}

.skills-header {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  user-select: none;
}

.skills-title {
  flex: 1;
  font-size: 12px;
  color: #666;
  font-weight: 500;
}

.skills-header .expand-icon {
  transition: transform 0.2s;
}

.skills-header .expand-icon.expanded {
  transform: rotate(180deg);
}

.skills-list {
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.skill-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 6px;
  font-size: 12px;
  color: #666;
  transition: all 0.2s;
}

.skill-item:hover {
  background: #e8f4ff;
  color: #2080f0;
}

.skill-item.active {
  background: #2080f0;
  color: #fff;
}

.skill-icon {
  font-size: 14px;
}

.sessions-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
  color: #999;
  margin-bottom: 8px;
  padding-left: 4px;
  padding-right: 4px;
}

.view-all-link {
  font-size: 11px;
  color: #2080f0;
  text-decoration: none;
  flex-shrink: 0;
}

.view-all-link:hover {
  text-decoration: underline;
}

.no-sessions {
  padding: 24px 0;
}

.session-item {
  display: flex;
  align-items: center;
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s;
  margin-bottom: 4px;
}

.session-item:hover {
  background: #f0f0f0;
}

.session-item.active {
  background: #e8f4ff;
  color: #2080f0;
}

.session-icon {
  flex-shrink: 0;
  margin-right: 8px;
}

.session-info {
  flex: 1;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.session-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}

.session-agent-name {
  font-size: 11px;
  color: #9ca3af;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.delete-btn {
  opacity: 0;
  flex-shrink: 0;
  transition: opacity 0.2s;
}

.session-item:hover .delete-btn {
  opacity: 1;
}

.session-scroll-wrapper {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.sider-footer {
  flex-shrink: 0;
  padding-top: 12px;
  border-top: 1px solid #f0f0f0;
}
</style>
