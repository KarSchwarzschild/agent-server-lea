<template>
  <div class="tools-page">
    <div class="header">
      <n-h2 style="margin: 0">工具管理</n-h2>
      <n-text depth="3">
        Tool 是原子能力（代码定义），不可在线增删。Skill 通过组合 Tool 来实现功能。
      </n-text>
    </div>

    <n-alert v-if="error" type="error" style="margin-bottom: 16px" closable>
      {{ error }}
    </n-alert>

    <n-spin :show="loading">
      <n-grid v-if="tools.length" :cols="3" :x-gap="12" :y-gap="12">
        <n-gi v-for="tool in tools" :key="tool.name">
          <n-card size="small" hoverable>
            <template #header>
              <div class="card-header">
                <n-text strong>{{ tool.name }}</n-text>
                <n-tag :type="tool.source === 'platform' ? 'info' : 'success'" size="small">
                  {{ tool.source }}
                </n-tag>
              </div>
            </template>
            <n-text depth="3">{{ tool.description || '暂无描述' }}</n-text>
          </n-card>
        </n-gi>
      </n-grid>
      <n-empty v-else-if="!loading" description="暂无已注册的工具">
        <template #extra>
          <n-text depth="3">工具通过代码定义（@tool 装饰器），启动时自动注册</n-text>
        </template>
      </n-empty>
    </n-spin>

    <n-divider />

    <n-h3>概念说明</n-h3>
    <n-grid :cols="2" :x-gap="16">
      <n-gi>
        <n-card size="small" title="Tool（工具）">
          <n-ul>
            <n-li>原子能力：单一输入 → 输出</n-li>
            <n-li>不调用 LLM，无状态</n-li>
            <n-li>代码定义，不可在线增删</n-li>
            <n-li>例：kb_search、write_file、academic_search</n-li>
          </n-ul>
        </n-card>
      </n-gi>
      <n-gi>
        <n-card size="small" title="Skill（技能）">
          <n-ul>
            <n-li>编排 + 生成：system_prompt + Tool 组合</n-li>
            <n-li>支持 ReAct（LLM 自主选工具）和 Pipeline（确定性流程）</n-li>
            <n-li>数据库存储，支持在线增删改查</n-li>
            <n-li>三种作用域：系统 / 公共 / 私有</n-li>
          </n-ul>
        </n-card>
      </n-gi>
    </n-grid>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import {
  NH2, NH3, NText, NGrid, NGi, NCard, NTag, NSpin, NEmpty, NAlert,
  NDivider, NUl, NLi,
} from 'naive-ui'
import request from '../utils/request'

interface ToolInfo {
  name: string
  description: string
  source: string
}

const tools = ref<ToolInfo[]>([])
const loading = ref(false)
const error = ref('')

onMounted(async () => {
  loading.value = true
  error.value = ''
  try {
    const { data } = await request.get('/api/admin/tools')
    tools.value = data.tools || []
  } catch (e: any) {
    error.value = e.response?.data?.detail || '获取工具列表失败，请确认后端服务已启动'
  }
  loading.value = false
})
</script>

<style scoped>
.tools-page {
  padding: 8px;
}

.header {
  margin-bottom: 16px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
</style>
