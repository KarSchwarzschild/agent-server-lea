<template>
  <n-modal v-model:show="showModal" preset="card" title="选择文档模板" :style="{ maxWidth: '600px' }">
    <!-- 分类 Tab -->
    <n-tabs v-model:value="selectedCategory" type="segment" size="small">
      <n-tab-pane name="all" tab="全部" />
      <n-tab-pane name="official" tab="🏛️ 公文" />
      <n-tab-pane name="academic" tab="📚 学术" />
      <n-tab-pane name="teaching" tab="📖 教学" />
      <n-tab-pane name="report" tab="📊 报告" />
    </n-tabs>

    <!-- 模板卡片网格 -->
    <div class="template-grid">
      <!-- 空白文档（始终显示） -->
      <div class="template-card" @click="handleSelect(null)">
        <div class="card-icon">📄</div>
        <div class="card-name">空白文档</div>
        <div class="card-desc">从零开始撰写</div>
      </div>

      <div
        v-for="tmpl in filteredTemplates"
        :key="tmpl.id"
        class="template-card"
        @click="handleSelect(tmpl)"
      >
        <div class="card-icon">📋</div>
        <div class="card-name">{{ tmpl.name }}</div>
        <div class="card-desc">{{ tmpl.description || tmpl.docCategory }}</div>
      </div>
    </div>

    <template #footer>
      <n-button @click="showModal = false">取消</n-button>
    </template>
  </n-modal>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { NModal, NTabs, NTabPane, NButton } from 'naive-ui'
import type { ArtifactTemplate } from '@/types/artifact'
import { listTemplates } from '@/api/artifact'

const showModal = defineModel<boolean>('show', { default: false })

const emit = defineEmits<{
  select: [template: ArtifactTemplate | null]
}>()

const selectedCategory = ref('all')
const templates = ref<ArtifactTemplate[]>([])

const filteredTemplates = computed(() => {
  if (selectedCategory.value === 'all') return templates.value
  return templates.value.filter(t => t.docCategory === selectedCategory.value)
})

onMounted(async () => {
  try {
    templates.value = await listTemplates()
  } catch (e) {
    console.warn('[TemplateSelector] Failed to load templates:', e)
  }
})

function handleSelect(tmpl: ArtifactTemplate | null) {
  emit('select', tmpl)
  showModal.value = false
}
</script>

<style scoped>
.template-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 12px;
  margin-top: 16px;
}

.template-card {
  padding: 16px 12px;
  border: 1px solid #e8e8e8;
  border-radius: 8px;
  cursor: pointer;
  text-align: center;
  transition: all 0.15s;
}
.template-card:hover {
  border-color: #2080f0;
  box-shadow: 0 2px 8px rgba(32, 128, 240, 0.1);
}

.card-icon {
  font-size: 28px;
  margin-bottom: 8px;
}
.card-name {
  font-size: 14px;
  font-weight: 500;
  margin-bottom: 4px;
}
.card-desc {
  font-size: 12px;
  color: #999;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
