<script setup lang="ts">
import { onMounted } from 'vue'
import {
  NSpin, NAlert, NForm, NFormItem, NInput, NButton,
  useMessage,
} from 'naive-ui'
import { useConfigSection } from '../../composables/useConfigSection'
import {
  getConnectionConfig,
  updateConnectionConfig,
  type ConnectionConfig,
} from '../../api/settings'

const message = useMessage()

const {
  loading, saving, data: connectionConfig, load, save,
} = useConfigSection<ConnectionConfig>({
  loadFn: getConnectionConfig,
  saveFn: async (d) => {
    await updateConnectionConfig(d)
    message.success('连接配置保存成功')
  },
  initialValue: { tenant_api_base: '', app_api_base: '', default_kb_id: '' },
})

async function handleSave() {
  try {
    await save()
  } catch (e: any) {
    message.error(e.message || '保存连接配置失败')
  }
}

onMounted(async () => {
  try { await load() } catch (e: any) { message.error(e.message || '加载连接配置失败') }
})
</script>

<template>
  <n-spin :show="loading">
    <div class="tab-content">
      <n-alert type="info" title="说明" style="margin-bottom: 20px">
        配置智能基座的API地址和默认知识库。API密钥请在.env文件中配置，不在此处显示。
      </n-alert>

      <n-form label-placement="top" style="max-width: 600px">
        <n-form-item label="租户API地址">
          <n-input
            v-model:value="connectionConfig.tenant_api_base"
            placeholder="https://tenantapi-ai.ouchn.edu.cn"
          />
        </n-form-item>

        <n-form-item label="应用API地址">
          <n-input
            v-model:value="connectionConfig.app_api_base"
            placeholder="https://appapi-ai.ouchn.edu.cn"
          />
        </n-form-item>

        <n-form-item label="默认知识库ID">
          <n-input
            v-model:value="connectionConfig.default_kb_id"
            placeholder="输入默认知识库ID"
          />
        </n-form-item>

        <n-form-item>
          <n-button type="primary" :loading="saving" @click="handleSave">
            保存连接配置
          </n-button>
        </n-form-item>
      </n-form>
    </div>
  </n-spin>
</template>
