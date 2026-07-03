<script setup lang="ts">
import { onMounted } from 'vue'
import {
  NSpin, NAlert, NForm, NFormItem, NInput, NButton,
  useMessage,
} from 'naive-ui'
import { useConfigSection } from '../../composables/useConfigSection'
import {
  getPromptConfig,
  updatePromptConfig,
  type PromptConfig,
} from '../../api/settings'

const message = useMessage()

const {
  loading, saving, data: promptConfig, load, save,
} = useConfigSection<PromptConfig>({
  loadFn: getPromptConfig,
  saveFn: async (d) => {
    await updatePromptConfig(d)
    message.success('提示词配置保存成功')
  },
  initialValue: { classification_prompt: '', review_prompt: '', chat_system_prompt: '' },
})

async function handleSave() {
  try {
    await save()
  } catch (e: any) {
    message.error(e.message || '保存提示词配置失败')
  }
}

onMounted(async () => {
  try { await load() } catch (e: any) { message.error(e.message || '加载提示词配置失败') }
})
</script>

<template>
  <n-spin :show="loading">
    <div class="tab-content">
      <n-alert type="warning" title="注意" style="margin-bottom: 20px">
        修改提示词可能影响AI的响应质量，请谨慎操作。建议在测试环境验证后再更新生产配置。
      </n-alert>

      <n-form label-placement="top">
        <n-form-item label="文献分类提示词">
          <n-input
            v-model:value="promptConfig.classification_prompt"
            type="textarea"
            :rows="10"
            placeholder="用于文献智能分类的提示词模板"
          />
        </n-form-item>

        <n-form-item label="综述生成提示词">
          <n-input
            v-model:value="promptConfig.review_prompt"
            type="textarea"
            :rows="8"
            placeholder="用于生成学术综述的提示词模板"
          />
        </n-form-item>

        <n-form-item label="问答系统提示词">
          <n-input
            v-model:value="promptConfig.chat_system_prompt"
            type="textarea"
            :rows="6"
            placeholder="智能问答的系统提示词"
          />
        </n-form-item>

        <n-form-item>
          <n-button type="primary" :loading="saving" @click="handleSave">
            保存提示词配置
          </n-button>
        </n-form-item>
      </n-form>
    </div>
  </n-spin>
</template>
