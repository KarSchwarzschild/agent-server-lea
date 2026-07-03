<template>
  <n-tag
    :color="{
      color: tagColorValue.light,
      textColor: tagColorValue.primary,
      borderColor: 'transparent'
    }"
    :size="size"
  >
    {{ category }}
  </n-tag>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { NTag } from 'naive-ui'

const props = defineProps<{
  category: string
  allCategories: string[]
  size?: 'small' | 'medium' | 'large'
}>()

// 默认灰色
const DEFAULT_COLOR = { primary: '#8c8c8c', light: '#f5f5f5', dark: '#595959' }

// 颜色池（6色循环）
const CATEGORY_COLOR_POOL = [
  { primary: '#1890ff', light: '#e6f7ff', dark: '#096dd9' },  // 蓝色 - 索引0
  { primary: '#fa8c16', light: '#fff7e6', dark: '#d46b08' },  // 橙色 - 索引1
  { primary: '#52c41a', light: '#f6ffed', dark: '#389e0d' },  // 绿色 - 索引2
  { primary: '#13c2c2', light: '#e6fffb', dark: '#08979c' },  // 青色 - 索引3
  { primary: '#eb2f96', light: '#fff0f6', dark: '#c41d7f' },  // 粉色 - 索引4
  { primary: '#722ed1', light: '#f9f0ff', dark: '#531dab' },  // 紫色 - 索引5
]

// 动态获取颜色
const tagColorValue = computed(() => {
  const index = props.allCategories.indexOf(props.category)
  if (index === -1) {
    return DEFAULT_COLOR
  }
  return CATEGORY_COLOR_POOL[index % CATEGORY_COLOR_POOL.length] || DEFAULT_COLOR
})
</script>

<style scoped>
/* 无需额外样式，使用Naive UI的Tag组件 */
</style>
