<script setup lang="ts">
import { onMounted } from 'vue'
import {
  NSpin, NAlert, NForm, NFormItem, NDynamicTags, NButton,
  useMessage,
} from 'naive-ui'
import { useConfigSection } from '../../composables/useConfigSection'
import {
  getCategoryConfig,
  updateCategoryConfig,
  type CategoryConfig,
} from '../../api/settings'

const message = useMessage()

const {
  loading, saving, data: categoryConfig, load, save,
} = useConfigSection<CategoryConfig>({
  loadFn: getCategoryConfig,
  saveFn: async (d) => {
    await updateCategoryConfig(d)
    message.success('分类配置保存成功')
  },
  initialValue: { categories: [], sub_categories: [], methodologies: [], agent_categories: [] },
})

async function handleSave() {
  try {
    await save()
  } catch (e: any) {
    message.error(e.message || '保存分类配置失败')
  }
}

onMounted(async () => {
  try { await load() } catch (e: any) { message.error(e.message || '加载分类配置失败') }
})
</script>

<template>
  <n-spin :show="loading">
    <div class="tab-content">
      <n-alert type="info" title="说明" style="margin-bottom: 20px">
        分类体系用于文献导入时的智能分类，修改后新导入的文献将使用新的分类选项。
      </n-alert>

      <n-form label-placement="top">
        <n-form-item label="研究领域">
          <n-dynamic-tags v-model:value="categoryConfig.categories" />
        </n-form-item>

        <n-form-item label="子领域">
          <n-dynamic-tags v-model:value="categoryConfig.sub_categories" />
        </n-form-item>

        <n-form-item label="研究方法">
          <n-dynamic-tags v-model:value="categoryConfig.methodologies" />
        </n-form-item>

        <n-form-item label="智能体分类">
          <n-dynamic-tags v-model:value="categoryConfig.agent_categories" />
          <template #feedback>
            用于智能体管理中的分类筛选，修改后需要同步更新智能体的分类字段
          </template>
        </n-form-item>

        <n-form-item>
          <n-button type="primary" :loading="saving" @click="handleSave">
            保存分类配置
          </n-button>
        </n-form-item>
      </n-form>
    </div>
  </n-spin>
</template>
