<template>
  <n-drawer
    v-model:show="visible"
    placement="bottom"
    :height="drawerHeight"
    :trap-focus="false"
  >
    <n-drawer-content>
      <template #header>
        <div class="drawer-header">
          <span class="drawer-title">筛选条件</span>
          <n-button text type="primary" @click="handleConfirm">完成</n-button>
        </div>
      </template>

      <div class="filter-content">
        <!-- 分类 -->
        <div class="filter-section">
          <div class="filter-label">分类</div>
          <n-select
            v-model:value="localFilters.category"
            :options="categoryOptions"
            placeholder="全部分类"
            clearable
            size="large"
          />
        </div>

        <!-- 研究方法 -->
        <div class="filter-section">
          <div class="filter-label">研究方法</div>
          <n-select
            v-model:value="localFilters.methodology"
            :options="methodologyOptions"
            placeholder="全部方法"
            clearable
            size="large"
          />
        </div>

        <!-- 年份范围 -->
        <div class="filter-section">
          <div class="filter-label">年份范围</div>
          <div class="year-range">
            <n-input-number
              v-model:value="localFilters.year_from"
              placeholder="起始年份"
              :show-button="false"
              size="large"
            />
            <span class="year-separator">至</span>
            <n-input-number
              v-model:value="localFilters.year_to"
              placeholder="结束年份"
              :show-button="false"
              size="large"
            />
          </div>
        </div>

        <!-- 重置按钮 -->
        <n-button
          block
          quaternary
          @click="handleReset"
        >
          重置筛选
        </n-button>
      </div>
    </n-drawer-content>
  </n-drawer>
</template>

<script setup lang="ts">
/**
 * MobileFilterDrawer - 移动端筛选条件底部抽屉
 */
import { computed, reactive, watch } from 'vue'
import {
  NDrawer,
  NDrawerContent,
  NButton,
  NSelect,
  NInputNumber,
} from 'naive-ui'

interface FilterValues {
  category: string | null
  methodology: string | null
  year_from: number | null
  year_to: number | null
}

const props = withDefaults(defineProps<{
  show: boolean
  filters: FilterValues
  categoryOptions: Array<{ label: string; value: string }>
  methodologyOptions: Array<{ label: string; value: string }>
}>(), {
  categoryOptions: () => [],
  methodologyOptions: () => []
})

const emit = defineEmits<{
  'update:show': [value: boolean]
  'update:filters': [filters: FilterValues]
  'confirm': []
  'reset': []
}>()

const visible = computed({
  get: () => props.show,
  set: (val) => emit('update:show', val)
})

// 本地筛选状态
const localFilters = reactive<FilterValues>({
  category: null,
  methodology: null,
  year_from: null,
  year_to: null
})

// 同步外部筛选条件到本地
watch(() => props.filters, (newFilters) => {
  localFilters.category = newFilters.category
  localFilters.methodology = newFilters.methodology
  localFilters.year_from = newFilters.year_from
  localFilters.year_to = newFilters.year_to
}, { immediate: true, deep: true })

const drawerHeight = '50vh'

const handleConfirm = () => {
  emit('update:filters', { ...localFilters })
  emit('confirm')
  visible.value = false
}

const handleReset = () => {
  localFilters.category = null
  localFilters.methodology = null
  localFilters.year_from = null
  localFilters.year_to = null
  emit('reset')
}
</script>

<style scoped>
.drawer-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
}

.drawer-title {
  font-size: 16px;
  font-weight: 600;
}

.filter-content {
  padding: 8px 0;
}

.filter-section {
  margin-bottom: 20px;
}

.filter-label {
  font-size: 14px;
  color: #666;
  margin-bottom: 8px;
}

.year-range {
  display: flex;
  align-items: center;
  gap: 12px;
}

.year-range :deep(.n-input-number) {
  flex: 1;
}

.year-separator {
  color: #999;
}
</style>
