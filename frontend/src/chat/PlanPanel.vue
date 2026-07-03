<template>
  <div v-if="plan.length" class="plan-panel">
    <div class="plan-header">
      <span class="plan-title">执行计划</span>
      <n-tag v-if="approved" type="success" size="tiny">已确认</n-tag>
    </div>
    <div class="plan-steps">
      <div
        v-for="item in plan"
        :key="item.step"
        class="plan-step"
        :class="{ done: item.status === 'done' }"
      >
        <span class="step-num">{{ item.step }}</span>
        <span class="step-task">{{ item.task }}</span>
        <n-tag v-if="item.tool" size="tiny" :bordered="false">{{ item.tool }}</n-tag>
      </div>
    </div>
    <div v-if="!approved" class="plan-actions">
      <n-button size="small" type="primary" @click="$emit('approve')">确认执行</n-button>
      <n-button size="small" @click="$emit('reject')">取消</n-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { NTag, NButton } from 'naive-ui'

interface PlanStep {
  step: number
  task: string
  tool?: string
  status: string
}

defineProps<{
  plan: PlanStep[]
  approved: boolean
}>()

defineEmits<{
  approve: []
  reject: []
}>()
</script>

<style scoped>
.plan-panel {
  margin: 12px 16px;
  padding: 12px;
  background: #f0f7ff;
  border: 1px solid #d0e3f7;
  border-radius: 8px;
}

.plan-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}

.plan-title {
  font-weight: 600;
  font-size: 14px;
  color: #1a56db;
}

.plan-steps {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.plan-step {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  background: #fff;
  border-radius: 6px;
  font-size: 13px;
}

.plan-step.done {
  opacity: 0.6;
  text-decoration: line-through;
}

.step-num {
  width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #1a56db;
  color: #fff;
  border-radius: 50%;
  font-size: 11px;
  flex-shrink: 0;
}

.step-task {
  flex: 1;
}

.plan-actions {
  display: flex;
  gap: 8px;
  margin-top: 10px;
}
</style>
