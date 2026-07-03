<script setup lang="ts">
import { computed, ref } from 'vue'

const props = defineProps<{
  scene: {
    title: string
    content: Record<string, any>
  }
}>()

const content = computed(() => props.scene?.content || {})
const title = computed(() => String(content.value.title || props.scene.title || '项目式学习'))
const description = computed(() => String(content.value.description || ''))
const targetSkills = computed<string[]>(() => {
  return Array.isArray(content.value.target_skills) ? content.value.target_skills.map(String) : []
})
const roles = computed(() => {
  return Array.isArray(content.value.roles) ? content.value.roles : []
})
const milestones = computed(() => {
  return Array.isArray(content.value.milestones) ? content.value.milestones : []
})
const rubric = computed(() => {
  return Array.isArray(content.value.rubric) ? content.value.rubric : []
})
const resources = computed(() => {
  return Array.isArray(content.value.resources) ? content.value.resources : []
})
const tasks = computed(() => {
  return Array.isArray(content.value.tasks) ? content.value.tasks : []
})
const submissionPrompts = computed<string[]>(() => {
  return Array.isArray(content.value.submission_prompts) ? content.value.submission_prompts.map(String) : []
})
const selectedRoleId = ref('')
const taskStatuses = ref<Record<string, string>>({})
const submissionDrafts = ref<Record<number, string>>({})
const selectedRole = computed(() => {
  return roles.value.find((role: any) => String(role.id || role.name) === selectedRoleId.value)
})

function selectRole(role: any) {
  selectedRoleId.value = String(role.id || role.name)
}

function taskStatus(task: any) {
  return taskStatuses.value[String(task.id || task.title)] || String(task.status || 'todo')
}

function toggleTask(task: any) {
  const id = String(task.id || task.title)
  const current = taskStatus(task)
  taskStatuses.value = {
    ...taskStatuses.value,
    [id]: current === 'todo' ? 'doing' : (current === 'doing' ? 'done' : 'todo'),
  }
}

function taskStatusLabel(task: any) {
  const status = taskStatus(task)
  if (status === 'doing') return '进行中'
  if (status === 'done') return '已完成'
  return '待开始'
}

function setSubmissionDraft(index: number, value: string) {
  submissionDrafts.value = {
    ...submissionDrafts.value,
    [index]: value,
  }
}
</script>

<template>
  <section
    class="pbl-view"
    :class="{ 'pbl-workspace': content.kind === 'pbl_workspace' }"
    aria-label="项目式学习"
  >
    <header class="pbl-header">
      <span class="pbl-kicker">项目式学习</span>
      <h3>{{ title }}</h3>
      <p v-if="description">{{ description }}</p>
    </header>

    <div v-if="targetSkills.length" class="pbl-section">
      <h4>目标能力</h4>
      <div class="skill-list">
        <span v-for="skill in targetSkills" :key="skill" class="skill-chip">{{ skill }}</span>
      </div>
    </div>

    <div v-if="roles.length" class="pbl-section">
      <h4>项目角色</h4>
      <div class="role-grid">
        <article v-for="role in roles" :key="role.id || role.name" class="role-card">
          <button
            class="role-select"
            type="button"
            :data-test="`pbl-role-${role.id || role.name}`"
            @click="selectRole(role)"
          >
            选择
          </button>
          <strong>{{ role.name }}</strong>
          <p>{{ role.responsibility }}</p>
        </article>
      </div>
      <p v-if="selectedRole" class="pbl-current-role">当前角色：{{ selectedRole.name }}</p>
    </div>

    <div v-if="resources.length" class="pbl-section">
      <h4>资料区</h4>
      <div class="resource-list">
        <article v-for="resource in resources" :key="resource.id || resource.title" class="resource-card">
          <strong>{{ resource.title }}</strong>
          <p>{{ resource.summary }}</p>
        </article>
      </div>
    </div>

    <div v-if="tasks.length" class="pbl-section">
      <h4>任务看板</h4>
      <div class="task-list">
        <button
          v-for="task in tasks"
          :key="task.id || task.title"
          class="task-card"
          type="button"
          :data-test="`pbl-task-${task.id || task.title}`"
          @click="toggleTask(task)"
        >
          <strong>{{ task.title }}</strong>
          <span>{{ taskStatusLabel(task) }}</span>
          <p v-if="task.description">{{ task.description }}</p>
        </button>
      </div>
    </div>

    <div v-if="submissionPrompts.length" class="pbl-section">
      <h4>成果提交</h4>
      <label
        v-for="(prompt, index) in submissionPrompts"
        :key="`${prompt}-${index}`"
        class="submission-item"
      >
        <span>{{ prompt }}</span>
        <textarea
          :data-test="`pbl-submission-${index}`"
          :value="submissionDrafts[index] || ''"
          rows="3"
          @input="setSubmissionDraft(index, ($event.target as HTMLTextAreaElement).value)"
        />
      </label>
    </div>

    <div class="pbl-section">
      <h4>里程碑</h4>
      <ol class="milestone-list">
        <li v-for="(item, index) in milestones" :key="item.id || index" class="milestone-item">
          <span class="milestone-index">{{ index + 1 }}</span>
          <div>
            <strong>{{ item.title }}</strong>
            <p v-if="item.description">{{ item.description }}</p>
            <small>{{ item.assessment_criterion }}</small>
          </div>
        </li>
      </ol>
    </div>

    <div v-if="rubric.length" class="pbl-section">
      <h4>评价标准</h4>
      <div class="rubric-list">
        <article v-for="item in rubric" :key="`${item.criterion}-${item.level}`" class="rubric-card">
          <strong>{{ item.criterion }}</strong>
          <span>{{ item.level }}</span>
          <p>{{ item.description }}</p>
        </article>
      </div>
    </div>
  </section>
</template>

<style scoped>
.pbl-view {
  box-sizing: border-box;
  width: min(820px, 100%);
  max-height: 100%;
  margin: 0 auto;
  padding: 20px;
  overflow-y: auto;
  scrollbar-gutter: stable;
  border: 1px solid #d8dee8;
  border-radius: 8px;
  background: #fff;
  color: #1f2937;
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.08);
}

.pbl-header {
  margin-bottom: 18px;
}

.pbl-workspace {
  width: min(900px, 100%);
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  padding: 16px;
}

.pbl-workspace .pbl-header,
.pbl-workspace .pbl-section:has(.submission-item) {
  grid-column: 1 / -1;
}

.pbl-workspace .pbl-header {
  margin-bottom: 0;
}

.pbl-workspace .pbl-section {
  margin-top: 0;
}

.pbl-workspace .role-card,
.pbl-workspace .resource-card,
.pbl-workspace .task-card,
.pbl-workspace .rubric-card {
  padding: 10px;
}

.pbl-workspace .submission-item textarea {
  min-height: 72px;
}

.pbl-kicker {
  display: block;
  color: #0f766e;
  font-size: 13px;
  font-weight: 700;
  margin-bottom: 4px;
}

.pbl-header h3 {
  margin: 0 0 8px;
  font-size: 22px;
}

.pbl-header p,
.role-card p,
.milestone-item p,
.rubric-card p {
  margin: 0;
  color: #4b5563;
  line-height: 1.6;
}

.pbl-section {
  margin-top: 18px;
}

.pbl-section h4 {
  margin: 0 0 10px;
  font-size: 15px;
  color: #334155;
}

.skill-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.skill-chip {
  padding: 6px 10px;
  border-radius: 999px;
  background: #ecfdf5;
  color: #047857;
  font-size: 13px;
  font-weight: 700;
}

.role-grid,
.resource-list,
.task-list,
.rubric-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 10px;
}

.role-card,
.resource-card,
.task-card,
.rubric-card {
  padding: 12px;
  border: 1px solid #d8dee8;
  border-radius: 8px;
  background: #f8fafc;
}

.task-card {
  text-align: left;
  color: inherit;
  cursor: pointer;
}

.role-card strong,
.resource-card strong,
.task-card strong,
.rubric-card strong {
  display: block;
  margin-bottom: 6px;
}

.role-select {
  float: right;
  border: 1px solid #99f6e4;
  border-radius: 6px;
  background: #f0fdfa;
  color: #0f766e;
  font-weight: 700;
}

.pbl-current-role,
.task-card span {
  color: #0f766e;
  font-weight: 700;
}

.submission-item {
  display: grid;
  gap: 8px;
  margin-bottom: 10px;
  color: #334155;
  font-weight: 700;
}

.submission-item textarea {
  width: 100%;
  resize: vertical;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  padding: 10px 12px;
  font: inherit;
  background: #f8fafc;
}

.milestone-list {
  display: grid;
  gap: 10px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.milestone-item {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr);
  gap: 10px;
  padding: 12px;
  border: 1px solid #d8dee8;
  border-radius: 8px;
  background: #f8fafc;
}

.milestone-index {
  width: 28px;
  height: 28px;
  border-radius: 999px;
  background: #ccfbf1;
  color: #0f766e;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
}

.milestone-item small,
.rubric-card span {
  display: block;
  margin-top: 6px;
  color: #0f766e;
  font-weight: 700;
}
</style>
