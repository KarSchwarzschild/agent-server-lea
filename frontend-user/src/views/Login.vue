<script setup lang="ts">
/**
 * 用户端登录页 - Naive UI 版本
 */
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import {
  NCard,
  NForm,
  NFormItem,
  NInput,
  NButton,
  NTabs,
  NTabPane,
  useMessage,
  type FormInst,
  type FormRules,
} from 'naive-ui'
import { useUserStore } from '../stores/user'
import { register } from '../api/auth'

const router = useRouter()
const userStore = useUserStore()
const message = useMessage()
const formRef = ref<FormInst | null>(null)

const activeTab = ref('login')
const loading = ref(false)

const formData = reactive({
  username: '',
  password: '',
  email: ''
})

const rules: FormRules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 3, max: 50, message: '用户名3-50个字符', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, message: '密码至少6个字符', trigger: 'blur' },
  ],
}

const handleSubmit = async () => {
  try {
    await formRef.value?.validate()
  } catch {
    return
  }

  loading.value = true

  try {
    if (activeTab.value === 'login') {
      await userStore.login({
        username: formData.username,
        password: formData.password
      })
      message.success('登录成功')
      router.push('/qa/super-agent')
    } else {
      await register({
        username: formData.username,
        password: formData.password,
        email: formData.email || undefined
      })
      // 注册成功后自动登录
      await userStore.login({
        username: formData.username,
        password: formData.password
      })
      message.success('注册成功')
      router.push('/qa/super-agent')
    }
  } catch (err: unknown) {
    const e = err as { response?: { data?: { detail?: string } } }
    message.error(e?.response?.data?.detail || (activeTab.value === 'login' ? '登录失败' : '注册失败'))
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <div class="login-container">
      <n-card class="login-card">
        <div class="login-header">
          <h1>终身教育超级智能体</h1>
        </div>

        <n-tabs v-model:value="activeTab" type="segment" animated>
          <n-tab-pane name="login" tab="登录">
            <n-form
              ref="formRef"
              :model="formData"
              :rules="rules"
              label-placement="top"
              style="margin-top: 20px"
            >
              <n-form-item label="用户名" path="username">
                <n-input
                  v-model:value="formData.username"
                  placeholder="请输入用户名"
                  size="large"
                  @keyup.enter="handleSubmit"
                />
              </n-form-item>
              <n-form-item label="密码" path="password">
                <n-input
                  v-model:value="formData.password"
                  type="password"
                  placeholder="请输入密码"
                  size="large"
                  show-password-on="click"
                  @keyup.enter="handleSubmit"
                />
              </n-form-item>
              <n-button
                type="primary"
                block
                size="large"
                :loading="loading"
                @click="handleSubmit"
              >
                登录
              </n-button>
            </n-form>
          </n-tab-pane>

          <n-tab-pane name="register" tab="注册">
            <n-form
              ref="formRef"
              :model="formData"
              :rules="rules"
              label-placement="top"
              style="margin-top: 20px"
            >
              <n-form-item label="用户名" path="username">
                <n-input
                  v-model:value="formData.username"
                  placeholder="请输入用户名"
                  size="large"
                />
              </n-form-item>
              <n-form-item label="邮箱（可选）">
                <n-input
                  v-model:value="formData.email"
                  placeholder="请输入邮箱"
                  size="large"
                />
              </n-form-item>
              <n-form-item label="密码" path="password">
                <n-input
                  v-model:value="formData.password"
                  type="password"
                  placeholder="请输入密码"
                  size="large"
                  show-password-on="click"
                />
              </n-form-item>
              <n-button
                type="primary"
                block
                size="large"
                :loading="loading"
                @click="handleSubmit"
              >
                注册
              </n-button>
            </n-form>
          </n-tab-pane>
        </n-tabs>

        <div class="login-footer">
          <p>测试账号: testuser / test123</p>
        </div>
      </n-card>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  padding: 20px;
}

.login-container {
  width: 100%;
  max-width: 420px;
}

.login-card {
  border-radius: 16px;
}

.login-header {
  text-align: center;
  margin-bottom: 24px;
}

.login-header h1 {
  margin: 0;
  font-size: 28px;
  font-weight: 700;
  color: #333;
}

.login-header p {
  margin: 8px 0 0;
  color: #666;
  font-size: 14px;
}

.login-footer {
  margin-top: 24px;
  text-align: center;
  font-size: 12px;
  color: #999;
}

.login-footer p {
  margin: 0;
}
</style>
