<template>
  <div class="login-container">
    <n-card title="LEA Platform" class="login-card">
      <n-tabs v-model:value="mode" type="segment" animated>
        <n-tab-pane name="login" tab="登录">
          <n-form ref="loginFormRef" :model="loginForm" :rules="loginRules">
            <n-form-item label="用户名" path="username">
              <n-input v-model:value="loginForm.username" placeholder="请输入用户名" @keyup.enter="handleLogin" />
            </n-form-item>
            <n-form-item label="密码" path="password">
              <n-input
                v-model:value="loginForm.password"
                type="password"
                placeholder="请输入密码"
                show-password-on="click"
                @keyup.enter="handleLogin"
              />
            </n-form-item>
            <n-button type="primary" block :loading="loading" @click="handleLogin">
              登录
            </n-button>
          </n-form>
        </n-tab-pane>

        <n-tab-pane name="register" tab="注册">
          <n-form ref="registerFormRef" :model="registerForm" :rules="registerRules">
            <n-form-item label="用户名" path="username">
              <n-input v-model:value="registerForm.username" placeholder="请输入用户名" @keyup.enter="handleRegister" />
            </n-form-item>
            <n-form-item label="邮箱" path="email">
              <n-input v-model:value="registerForm.email" placeholder="请输入邮箱（选填）" @keyup.enter="handleRegister" />
            </n-form-item>
            <n-form-item label="密码" path="password">
              <n-input
                v-model:value="registerForm.password"
                type="password"
                placeholder="请输入密码"
                show-password-on="click"
                @keyup.enter="handleRegister"
              />
            </n-form-item>
            <n-form-item label="确认密码" path="confirmPassword">
              <n-input
                v-model:value="registerForm.confirmPassword"
                type="password"
                placeholder="请再次输入密码"
                show-password-on="click"
                @keyup.enter="handleRegister"
              />
            </n-form-item>
            <n-button type="primary" block :loading="loading" @click="handleRegister">
              注册
            </n-button>
          </n-form>
        </n-tab-pane>
      </n-tabs>
    </n-card>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useMessage, NCard, NForm, NFormItem, NInput, NButton, NTabs, NTabPane } from 'naive-ui'
import type { FormRules } from 'naive-ui'
import { useUserStore } from '../stores/user'

const router = useRouter()
const message = useMessage()
const userStore = useUserStore()

const loading = ref(false)
const mode = ref('login')

const loginFormRef = ref()
const loginForm = ref({ username: '', password: '' })
const loginRules: FormRules = {
  username: { required: true, message: '请输入用户名', trigger: 'blur' },
  password: { required: true, message: '请输入密码', trigger: 'blur' },
}

const registerFormRef = ref()
const registerForm = ref({ username: '', email: '', password: '', confirmPassword: '' })
const registerRules: FormRules = {
  username: { required: true, message: '请输入用户名', trigger: 'blur' },
  password: { required: true, message: '请输入密码', trigger: 'blur' },
  confirmPassword: [
    { required: true, message: '请确认密码', trigger: 'blur' },
    {
      validator: (_rule: unknown, value: string) => value === registerForm.value.password,
      message: '两次密码不一致',
      trigger: 'blur',
    },
  ],
}

async function handleLogin() {
  loading.value = true
  try {
    await userStore.login(loginForm.value.username, loginForm.value.password)
    message.success('登录成功')
    router.push('/')
  } catch {
    message.error('登录失败，请检查用户名和密码')
  } finally {
    loading.value = false
  }
}

async function handleRegister() {
  loading.value = true
  try {
    await userStore.register(
      registerForm.value.username,
      registerForm.value.password,
      registerForm.value.email,
    )
    message.success('注册成功')
    router.push('/')
  } catch {
    message.error('注册失败，用户名可能已存在')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-container {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
  background: #f5f7fa;
}
.login-card {
  width: 420px;
}
</style>
