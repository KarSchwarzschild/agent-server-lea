<script setup lang="ts">
/**
 * 个人中心页面 - 响应式版本
 * 移动端：合并历史记录、提供退出登录入口
 */
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import {
  NCard,
  NGrid,
  NGi,
  NStatistic,
  NDescriptions,
  NDescriptionsItem,
  NButton,
  NSpace,
  NIcon,
  NTabs,
  NTabPane,
  NList,
  NListItem,
  NTag,
  NEmpty,
  NModal,
  NForm,
  NFormItem,
  NInput,
  useMessage,
  useDialog,
  NSpin,
  NAvatar,
  NDivider,
} from 'naive-ui'
import {
  ChatbubblesOutline,
  ChatboxOutline,
  SearchOutline,
  DocumentTextOutline,
  CalendarOutline,
  TimeOutline,
  LockClosedOutline,
  CreateOutline,
  LogOutOutline,
  ChevronForwardOutline,
  FolderOpenOutline,
  NotificationsOutline,
  SparklesOutline,
} from '@vicons/ionicons5'
import { useUserStore } from '../stores/user'
import { useResponsive } from '../composables/useResponsive'
import request from '../utils/request'

const router = useRouter()
const userStore = useUserStore()
const message = useMessage()
const dialog = useDialog()
const { isMobile } = useResponsive()

// 加载状态
const loading = ref(false)
const statsLoading = ref(false)

// 用户信息
interface UserProfile {
  user_id: string
  username: string
  email: string | null
  avatar: string | null
  phone: string | null
  roles: string[]
  created_at: string | null
  last_login_at: string | null
}

// 用户统计
interface UserStats {
  total_chats: number
  total_searches: number
  total_reviews: number
  total_tokens: number
  total_messages: number
  total_super_agent_sessions: number
  monthly_chats: number
  monthly_searches: number
  monthly_reviews: number
  monthly_tokens: number
  recent_sessions: Array<{
    session_id: string
    title: string
    session_type: string
    source: 'chat' | 'super_agent'
    agent_name?: string | null
    message_count: number
    updated_at: string | null
  }>
  recent_reviews: Array<{
    task_id: string
    topic: string
    status: string
    word_count: number
    created_at: string | null
  }>
}

const profile = ref<UserProfile | null>(null)
const stats = ref<UserStats | null>(null)

// 编辑个人信息弹窗
const showEditModal = ref(false)
const editForm = ref({
  email: '',
  phone: '',
})
const editLoading = ref(false)

// 修改密码弹窗
const showPasswordModal = ref(false)
const passwordForm = ref({
  old_password: '',
  new_password: '',
  confirm_password: '',
})
const passwordLoading = ref(false)

// 获取用户信息
async function fetchProfile() {
  loading.value = true
  try {
    const res = await request.get('/api/user/profile')
    profile.value = res as unknown as UserProfile
  } catch (error) {
    console.error('获取用户信息失败:', error)
  } finally {
    loading.value = false
  }
}

// 获取用户统计
async function fetchStats() {
  statsLoading.value = true
  try {
    const res = await request.get('/api/user/profile/stats')
    stats.value = res as unknown as UserStats
  } catch (error) {
    console.error('获取统计信息失败:', error)
  } finally {
    statsLoading.value = false
  }
}

// 打开编辑弹窗
function openEditModal() {
  if (profile.value) {
    editForm.value = {
      email: profile.value.email || '',
      phone: profile.value.phone || '',
    }
  }
  showEditModal.value = true
}

// 保存个人信息
async function saveProfile() {
  editLoading.value = true
  try {
    const res = await request.put('/api/user/profile', editForm.value)
    profile.value = res as unknown as UserProfile
    message.success('保存成功')
    showEditModal.value = false
  } catch (error: any) {
    message.error(error.message || '保存失败')
  } finally {
    editLoading.value = false
  }
}

// 修改密码
async function changePassword() {
  if (passwordForm.value.new_password !== passwordForm.value.confirm_password) {
    message.error('两次输入的密码不一致')
    return
  }
  if (passwordForm.value.new_password.length < 6) {
    message.error('新密码长度至少6位')
    return
  }

  passwordLoading.value = true
  try {
    await request.post('/api/user/profile/change-password', {
      old_password: passwordForm.value.old_password,
      new_password: passwordForm.value.new_password,
    })
    message.success('密码修改成功，请重新登录')
    showPasswordModal.value = false
    // 清除登录状态
    userStore.logout()
    router.push('/login')
  } catch (error: any) {
    message.error(error.message || '密码修改失败')
  } finally {
    passwordLoading.value = false
  }
}

// 格式化时间
function formatTime(timeStr: string | null): string {
  if (!timeStr) return '-'
  const date = new Date(timeStr)
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

// 状态标签类型
function getStatusType(status: string): 'success' | 'warning' | 'error' | 'info' {
  switch (status) {
    case 'completed':
      return 'success'
    case 'processing':
      return 'warning'
    case 'failed':
      return 'error'
    default:
      return 'info'
  }
}

// 状态文字
function getStatusText(status: string): string {
  switch (status) {
    case 'completed':
      return '已完成'
    case 'processing':
      return '生成中'
    case 'failed':
      return '失败'
    case 'pending':
      return '待处理'
    default:
      return status
  }
}

// 跳转到会话
function goToSession(session: UserStats['recent_sessions'][0]) {
  if (session.source === 'super_agent') {
    router.push(`/qa/super-agent?session=${session.session_id}`)
  } else {
    router.push(`/qa/super-agent`)
  }
}

// 跳转到综述
function goToReview(taskId: string) {
  router.push(`/review?task=${taskId}`)
}

// 用户头像文字
const avatarText = computed(() => {
  if (profile.value?.username) {
    return profile.value.username.charAt(0).toUpperCase()
  }
  return 'U'
})

onMounted(() => {
  fetchProfile()
  fetchStats()
})

// 退出登录（移动端）
function handleLogout() {
  dialog.warning({
    title: '退出登录',
    content: '确定要退出登录吗？',
    positiveText: '退出',
    negativeText: '取消',
    onPositiveClick: () => {
      userStore.logout()
      router.push('/login')
      message.success('已退出登录')
    }
  })
}

// 跳转到历史记录（移动端入口）
function goToHistory() {
  router.push('/history')
}

// 跳转到个人知识库（移动端入口）
function goToKnowledgeBase() {
  router.push('/knowledge-base')
}
</script>

<template>
  <div class="profile-container" :class="{ 'profile-container-mobile': isMobile }">
    <n-spin :show="loading">
      <!-- 用户信息卡片 -->
      <n-card class="profile-card" :bordered="false">
        <div :class="['profile-header', { 'profile-header-mobile': isMobile }]">
          <n-avatar
            v-if="profile?.avatar"
            :src="profile.avatar"
            :size="isMobile ? 64 : 80"
            round
          />
          <n-avatar v-else :size="isMobile ? 64 : 80" round color="#2080f0">
            {{ avatarText }}
          </n-avatar>
          <div class="profile-info">
            <h2 :class="['username', { 'username-mobile': isMobile }]">{{ profile?.username || '-' }}</h2>
            <div class="roles">
              <n-tag v-for="role in profile?.roles || []" :key="role" size="small" type="info">
                {{ role }}
              </n-tag>
            </div>
          </div>
          <!-- PC端按钮 -->
          <div v-if="!isMobile" class="profile-actions">
            <n-space>
              <n-button @click="openEditModal">
                <template #icon>
                  <n-icon :component="CreateOutline" />
                </template>
                编辑资料
              </n-button>
              <n-button @click="showPasswordModal = true">
                <template #icon>
                  <n-icon :component="LockClosedOutline" />
                </template>
                修改密码
              </n-button>
            </n-space>
          </div>
        </div>

        <!-- PC端详细信息 -->
        <template v-if="!isMobile">
          <n-divider />
          <n-descriptions :column="3" label-placement="left">
            <n-descriptions-item label="用户ID">
              {{ profile?.user_id || '-' }}
            </n-descriptions-item>
            <n-descriptions-item label="邮箱">
              {{ profile?.email || '-' }}
            </n-descriptions-item>
            <n-descriptions-item label="手机">
              {{ profile?.phone || '-' }}
            </n-descriptions-item>
            <n-descriptions-item label="注册时间">
              {{ formatTime(profile?.created_at || null) }}
            </n-descriptions-item>
            <n-descriptions-item label="最后登录">
              {{ formatTime(profile?.last_login_at || null) }}
            </n-descriptions-item>
          </n-descriptions>
        </template>
      </n-card>

      <!-- 移动端：功能入口 -->
      <template v-if="isMobile">
        <n-card :bordered="false" class="mobile-menu-card">
          <div class="mobile-menu-item" @click="goToHistory">
            <n-icon :component="TimeOutline" :size="22" color="#2080f0" />
            <span class="menu-text">历史记录</span>
            <n-icon :component="ChevronForwardOutline" :size="18" color="#999" />
          </div>
          <div class="mobile-menu-item" @click="goToKnowledgeBase">
            <n-icon :component="FolderOpenOutline" :size="22" color="#f0a020" />
            <span class="menu-text">个人知识库</span>
            <n-icon :component="ChevronForwardOutline" :size="18" color="#999" />
          </div>
          <div class="mobile-menu-item" @click="router.push('/settings/channels')">
            <n-icon :component="NotificationsOutline" :size="22" color="#3370ff" />
            <span class="menu-text">消息渠道</span>
            <n-icon :component="ChevronForwardOutline" :size="18" color="#999" />
          </div>
          <div class="mobile-menu-item" @click="openEditModal">
            <n-icon :component="CreateOutline" :size="22" color="#18a058" />
            <span class="menu-text">编辑资料</span>
            <n-icon :component="ChevronForwardOutline" :size="18" color="#999" />
          </div>
          <div class="mobile-menu-item" @click="showPasswordModal = true">
            <n-icon :component="LockClosedOutline" :size="22" color="#f0a020" />
            <span class="menu-text">修改密码</span>
            <n-icon :component="ChevronForwardOutline" :size="18" color="#999" />
          </div>
          <div class="mobile-menu-item logout" @click="handleLogout">
            <n-icon :component="LogOutOutline" :size="22" color="#d03050" />
            <span class="menu-text" style="color: #d03050;">退出登录</span>
            <n-icon :component="ChevronForwardOutline" :size="18" color="#999" />
          </div>
        </n-card>
      </template>

      <!-- 统计卡片 -->
      <n-spin :show="statsLoading">
        <n-grid :cols="isMobile ? 2 : 4" :x-gap="isMobile ? 12 : 16" :y-gap="isMobile ? 12 : 16" class="stats-grid">
          <n-gi>
            <n-card :bordered="false" class="stat-card">
              <n-statistic :label="isMobile ? '对话' : '总对话次数'">
                <template #prefix>
                  <n-icon :component="ChatbubblesOutline" color="#2080f0" />
                </template>
                {{ stats?.total_chats || 0 }}
                <template v-if="!isMobile" #suffix>
                  <span class="stat-sub">本月 {{ stats?.monthly_chats || 0 }}</span>
                </template>
              </n-statistic>
            </n-card>
          </n-gi>
          <n-gi>
            <n-card :bordered="false" class="stat-card">
              <n-statistic :label="isMobile ? '检索' : '总检索次数'">
                <template #prefix>
                  <n-icon :component="SearchOutline" color="#18a058" />
                </template>
                {{ stats?.total_searches || 0 }}
                <template v-if="!isMobile" #suffix>
                  <span class="stat-sub">本月 {{ stats?.monthly_searches || 0 }}</span>
                </template>
              </n-statistic>
            </n-card>
          </n-gi>
          <n-gi>
            <n-card :bordered="false" class="stat-card">
              <n-statistic :label="isMobile ? '综述' : '综述生成数'">
                <template #prefix>
                  <n-icon :component="DocumentTextOutline" color="#f0a020" />
                </template>
                {{ stats?.total_reviews || 0 }}
                <template v-if="!isMobile" #suffix>
                  <span class="stat-sub">本月 {{ stats?.monthly_reviews || 0 }}</span>
                </template>
              </n-statistic>
            </n-card>
          </n-gi>
          <n-gi>
            <n-card :bordered="false" class="stat-card">
              <n-statistic :label="isMobile ? '消息' : '总消息数'">
                <template #prefix>
                  <n-icon :component="ChatboxOutline" color="#d03050" />
                </template>
                {{ stats?.total_messages || 0 }}
              </n-statistic>
            </n-card>
          </n-gi>
        </n-grid>

        <!-- PC端：最近活动 -->
        <n-card v-if="!isMobile" :bordered="false" class="activity-card" title="最近活动">
          <n-tabs type="line">
            <n-tab-pane name="sessions" tab="最近会话">
              <n-list v-if="stats?.recent_sessions?.length" hoverable clickable>
                <n-list-item
                  v-for="session in stats.recent_sessions"
                  :key="session.session_id"
                  @click="goToSession(session)"
                >
                  <div class="activity-item">
                    <div class="activity-icon" :style="{ background: session.source === 'super_agent' ? '#f0e6ff' : '#f5f7f9' }">
                      <n-icon
                        :component="session.source === 'super_agent' ? SparklesOutline : ChatbubblesOutline"
                        size="20"
                        :color="session.source === 'super_agent' ? '#8b5cf6' : '#2080f0'"
                      />
                    </div>
                    <div class="activity-content">
                      <div class="activity-title">
                        {{ session.title }}
                        <n-tag
                          v-if="session.source === 'super_agent' && session.agent_name"
                          size="small"
                          :bordered="false"
                          type="info"
                          style="margin-left: 8px;"
                        >
                          {{ session.agent_name }}
                        </n-tag>
                      </div>
                      <div class="activity-meta">
                        <span>{{ session.message_count }} 条消息</span>
                        <span class="activity-time">
                          <n-icon :component="TimeOutline" size="14" />
                          {{ formatTime(session.updated_at) }}
                        </span>
                      </div>
                    </div>
                  </div>
                </n-list-item>
              </n-list>
              <n-empty v-else description="暂无会话记录" />
            </n-tab-pane>

            <n-tab-pane name="reviews" tab="最近综述">
              <n-list v-if="stats?.recent_reviews?.length" hoverable clickable>
                <n-list-item
                  v-for="review in stats.recent_reviews"
                  :key="review.task_id"
                  @click="goToReview(review.task_id)"
                >
                  <div class="activity-item">
                    <div class="activity-icon">
                      <n-icon :component="DocumentTextOutline" size="20" color="#f0a020" />
                    </div>
                    <div class="activity-content">
                      <div class="activity-title">
                        {{ review.topic }}
                        <n-tag
                          :type="getStatusType(review.status)"
                          size="small"
                          style="margin-left: 8px;"
                        >
                          {{ getStatusText(review.status) }}
                        </n-tag>
                      </div>
                      <div class="activity-meta">
                        <span>{{ review.word_count }} 字</span>
                        <span class="activity-time">
                          <n-icon :component="CalendarOutline" size="14" />
                          {{ formatTime(review.created_at) }}
                        </span>
                      </div>
                    </div>
                  </div>
                </n-list-item>
              </n-list>
              <n-empty v-else description="暂无综述记录" />
            </n-tab-pane>
          </n-tabs>
        </n-card>
      </n-spin>
    </n-spin>

    <!-- 编辑个人信息弹窗 -->
    <n-modal
      v-model:show="showEditModal"
      title="编辑个人信息"
      preset="dialog"
      :show-icon="false"
      :style="{ width: isMobile ? '90vw' : '450px' }"
    >
      <n-form :model="editForm" :label-placement="isMobile ? 'top' : 'left'" :label-width="isMobile ? undefined : '80px'">
        <n-form-item label="邮箱">
          <n-input v-model:value="editForm.email" placeholder="请输入邮箱" />
        </n-form-item>
        <n-form-item label="手机">
          <n-input v-model:value="editForm.phone" placeholder="请输入手机号" />
        </n-form-item>
      </n-form>
      <template #action>
        <n-space :vertical="isMobile" :size="isMobile ? 12 : 'medium'" :style="{ width: isMobile ? '100%' : 'auto' }">
          <n-button :block="isMobile" @click="showEditModal = false">取消</n-button>
          <n-button :block="isMobile" type="primary" :loading="editLoading" @click="saveProfile">保存</n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 修改密码弹窗 -->
    <n-modal
      v-model:show="showPasswordModal"
      title="修改密码"
      preset="dialog"
      :show-icon="false"
      :style="{ width: isMobile ? '90vw' : '450px' }"
    >
      <n-form :model="passwordForm" :label-placement="isMobile ? 'top' : 'left'" :label-width="isMobile ? undefined : '100px'">
        <n-form-item label="原密码">
          <n-input
            v-model:value="passwordForm.old_password"
            type="password"
            placeholder="请输入原密码"
            show-password-on="click"
          />
        </n-form-item>
        <n-form-item label="新密码">
          <n-input
            v-model:value="passwordForm.new_password"
            type="password"
            placeholder="请输入新密码（至少6位）"
            show-password-on="click"
          />
        </n-form-item>
        <n-form-item label="确认新密码">
          <n-input
            v-model:value="passwordForm.confirm_password"
            type="password"
            placeholder="请再次输入新密码"
            show-password-on="click"
          />
        </n-form-item>
      </n-form>
      <template #action>
        <n-space :vertical="isMobile" :size="isMobile ? 12 : 'medium'" :style="{ width: isMobile ? '100%' : 'auto' }">
          <n-button :block="isMobile" @click="showPasswordModal = false">取消</n-button>
          <n-button :block="isMobile" type="primary" :loading="passwordLoading" @click="changePassword">
            确认修改
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.profile-container {
  padding: 24px;
  max-width: 1200px;
  margin: 0 auto;
  height: calc(100vh - 60px);
  overflow-y: auto;
}

.profile-card {
  margin-bottom: 16px;
}

.profile-header {
  display: flex;
  align-items: center;
  gap: 24px;
}

.profile-info {
  flex: 1;
}

.username {
  margin: 0 0 8px 0;
  font-size: 24px;
  font-weight: 600;
}

.roles {
  display: flex;
  gap: 8px;
}

.profile-actions {
  display: flex;
  align-items: center;
}

.stats-grid {
  margin-bottom: 16px;
}

.stat-card {
  text-align: center;
}

.stat-sub {
  display: block;
  font-size: 12px;
  color: #999;
  margin-top: 4px;
}

.activity-card {
  margin-bottom: 16px;
}

.activity-item {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 8px 0;
}

.activity-icon {
  width: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f5f7f9;
  border-radius: 8px;
}

.activity-content {
  flex: 1;
}

.activity-title {
  font-size: 14px;
  font-weight: 500;
  margin-bottom: 4px;
  display: flex;
  align-items: center;
}

.activity-meta {
  font-size: 12px;
  color: #999;
  display: flex;
  gap: 16px;
}

.activity-time {
  display: flex;
  align-items: center;
  gap: 4px;
}

/* ===== 移动端样式 ===== */
.profile-container-mobile {
  padding: 12px;
  height: auto;
  padding-bottom: calc(56px + env(safe-area-inset-bottom, 0px) + 12px);
}

.profile-header-mobile {
  flex-direction: column;
  gap: 12px;
  text-align: center;
}

.profile-header-mobile .profile-info {
  text-align: center;
}

.profile-header-mobile .roles {
  justify-content: center;
}

.username-mobile {
  font-size: 20px;
}

.mobile-menu-card {
  margin-bottom: 16px;
}

.mobile-menu-item {
  display: flex;
  align-items: center;
  padding: 16px 0;
  border-bottom: 1px solid #f0f0f0;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}

.mobile-menu-item:last-child {
  border-bottom: none;
}

.mobile-menu-item:active {
  background: #f9f9f9;
  margin: 0 -16px;
  padding: 16px;
}

.mobile-menu-item .menu-text {
  flex: 1;
  font-size: 15px;
  color: #333;
  margin-left: 12px;
}

.mobile-menu-item.logout {
  border-bottom: none;
}
</style>
