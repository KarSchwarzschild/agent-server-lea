<script setup lang="ts">
/**
 * 登录页面 - Naive UI 版本
 */
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import {
  NCard,
  NForm,
  NFormItem,
  NInput,
  NButton,
  NSpace,
  useMessage,
  type FormInst,
  type FormRules
} from 'naive-ui'
import { useUserStore } from '../stores/user'

const router = useRouter()
const message = useMessage()
const userStore = useUserStore()
const formRef = ref<FormInst | null>(null)
const loading = ref(false)

const formData = reactive({
  username: '',
  password: ''
})

const rules: FormRules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' }
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' }
  ]
}

const handleLogin = async () => {
  try {
    await formRef.value?.validate()
  } catch {
    return
  }

  loading.value = true
  try {
    await userStore.login({
      username: formData.username,
      password: formData.password
    })
    message.success('登录成功')
    router.push('/dashboard')
  } catch (error: unknown) {
    const err = error as { response?: { data?: { detail?: string } } }
    message.error(err?.response?.data?.detail || '登录失败')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-container">
    <n-card class="login-card" :bordered="false">
      <div class="login-header">
        <h1>终身教育智能体管理后台</h1>
        <p>面向终身教育研究的科研型智能体</p>
      </div>

      <n-form
        ref="formRef"
        :model="formData"
        :rules="rules"
        label-placement="left"
        label-width="0"
        size="large"
      >
        <n-form-item path="username">
          <n-input
            v-model:value="formData.username"
            placeholder="请输入用户名"
            @keyup.enter="handleLogin"
          />
        </n-form-item>

        <n-form-item path="password">
          <n-input
            v-model:value="formData.password"
            type="password"
            placeholder="请输入密码"
            show-password-on="click"
            @keyup.enter="handleLogin"
          />
        </n-form-item>

        <n-form-item>
          <n-button
            type="primary"
            block
            :loading="loading"
            @click="handleLogin"
          >
            登录
          </n-button>
        </n-form-item>
      </n-form>

      <div class="login-footer">
        <n-space justify="center">
          <span class="hint">默认账号: admin / admin123</span>
        </n-space>
      </div>
    </n-card>
  </div>
</template>

<style scoped>
.login-container {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
}

.login-card {
  width: 400px;
  border-radius: 12px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.1);
}

.login-header {
  text-align: center;
  margin-bottom: 32px;
}

.login-header h1 {
  font-size: 24px;
  font-weight: 600;
  color: #333;
  margin: 0 0 8px;
}

.login-header p {
  font-size: 14px;
  color: #666;
  margin: 0;
}

.login-footer {
  margin-top: 16px;
}

.hint {
  font-size: 12px;
  color: #999;
}
</style>
