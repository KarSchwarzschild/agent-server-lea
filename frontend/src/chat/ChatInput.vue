<template>
  <div class="chat-input-wrapper">
    <!-- 选中 Skill 的提示条 -->
    <div v-if="activeSkill" class="active-skill-hint">
      <n-tag type="primary" size="small" closable @close="clearSkill">
        {{ getCategoryIcon(activeSkill.category) }} 使用 {{ activeSkill.display_name }}
      </n-tag>
    </div>

    <!-- 附件预览条 -->
    <div v-if="attachments.length" class="attachment-bar">
      <div v-for="att in attachments" :key="att.id" class="attachment-item">
        <n-tag size="small" closable @close="removeAttachment(att.id)">
          {{ att.filename }}
        </n-tag>
      </div>
    </div>

    <!-- 上方工具栏 -->
    <SkillBar
      :skills="skills"
      :selectedId="activeSkill?.id ?? null"
      @select="handleBarSelect"
      @showAll="showPanel"
    />

    <!-- 输入区 -->
    <div class="input-row">
      <SkillPanel :skills="skills" :disabled="disabled" @select="handlePanelSelect" />

      <!-- 文件上传按钮 -->
      <div class="upload-btn">
        <n-upload
          :show-file-list="false"
          :custom-request="handleFileUpload"
          :disabled="disabled || uploading"
          accept=".pdf,.docx,.txt,.md,.csv,.jpg,.jpeg,.png,.gif,.webp"
        >
          <n-button quaternary :loading="uploading" title="上传文件">
            <template #icon><span style="font-size: 18px">📎</span></template>
          </n-button>
        </n-upload>
      </div>

      <div class="input-container" ref="inputContainerRef">
        <n-input
          ref="inputRef"
          v-model:value="text"
          type="textarea"
          :autosize="{ minRows: 1, maxRows: 4 }"
          :placeholder="placeholder"
          :disabled="disabled"
          @keyup.enter.exact="handleSend"
          @input="handleInput"
        />
        <!-- @ 搜索下拉 -->
        <div v-if="showAtMenu" class="at-menu">
          <div
            v-for="skill in filteredSkills"
            :key="skill.id"
            class="at-item"
            @click="selectFromAt(skill)"
          >
            <span class="at-icon">{{ getCategoryIcon(skill.category) }}</span>
            <div>
              <div class="at-name">{{ skill.display_name }}</div>
              <div class="at-desc">{{ skill.description }}</div>
            </div>
          </div>
          <div v-if="!filteredSkills.length" class="at-empty">无匹配技能</div>
        </div>
      </div>

      <n-button type="primary" :loading="disabled" @click="handleSend" :disabled="!canSend">
        发送
      </n-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { NInput, NButton, NTag, NUpload } from 'naive-ui'
import SkillPanel from './SkillPanel.vue'
import SkillBar from './SkillBar.vue'
import type { SkillItem } from './SkillPanel.vue'
import request from '../utils/request'

interface Attachment {
  id: number
  filename: string
  media_type: string
}

const props = defineProps<{
  skills: SkillItem[]
  disabled?: boolean
}>()

const emit = defineEmits<{
  send: [message: string, skillId: number | null, attachmentIds: number[]]
}>()

const text = ref('')
const activeSkill = ref<SkillItem | null>(null)
const showAtMenu = ref(false)
const atQuery = ref('')
const attachments = ref<Attachment[]>([])
const uploading = ref(false)

const categoryIcons: Record<string, string> = {
  research: '🔍',
  education: '📚',
  writing: '✍️',
  analysis: '📊',
  general: '🤖',
}

function getCategoryIcon(category: string) {
  return categoryIcons[category] || '⚡'
}

const placeholder = computed(() => {
  if (activeSkill.value) return `使用「${activeSkill.value.display_name}」处理...`
  return '输入消息... 输入 @ 选择技能'
})

const canSend = computed(() => text.value.trim().length > 0 && !props.disabled)

const filteredSkills = computed(() => {
  const q = atQuery.value.toLowerCase()
  if (!q) return props.skills
  return props.skills.filter(
    s => s.name.toLowerCase().includes(q) || s.display_name.includes(q)
  )
})

function handleInput(value: string) {
  const atIndex = value.lastIndexOf('@')
  if (atIndex >= 0) {
    const beforeAt = value.slice(0, atIndex)
    const afterAt = value.slice(atIndex + 1)
    if (!afterAt.includes(' ') && afterAt.length < 20) {
      showAtMenu.value = true
      atQuery.value = afterAt
      return
    }
  }
  showAtMenu.value = false
  atQuery.value = ''
}

function selectFromAt(skill: SkillItem) {
  activeSkill.value = skill
  showAtMenu.value = false
  // 移除 @xxx 文字
  const atIndex = text.value.lastIndexOf('@')
  if (atIndex >= 0) {
    text.value = text.value.slice(0, atIndex).trimEnd()
  }
}

function handlePanelSelect(skill: SkillItem) {
  activeSkill.value = skill
}

function handleBarSelect(skill: SkillItem | null) {
  activeSkill.value = skill
}

function showPanel() {
  // SkillBar "更多" 按钮点击 → 打开 SkillPanel
  // SkillPanel 有自己的 popover 触发，这里可以通过 focus 触发
}

function clearSkill() {
  activeSkill.value = null
}

function handleSend() {
  const msg = text.value.trim()
  if (!msg || props.disabled) return
  const attIds = attachments.value.map(a => a.id)
  emit('send', msg, activeSkill.value?.id ?? null, attIds)
  text.value = ''
  activeSkill.value = null
  attachments.value = []
  showAtMenu.value = false
}

async function handleFileUpload({ file }: { file: { file: File } }) {
  uploading.value = true
  try {
    const formData = new FormData()
    formData.append('file', file.file)
    const { data } = await request.post('/api/chat/attachments/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    attachments.value.push({
      id: data.id,
      filename: data.filename,
      media_type: data.media_type,
    })
  } catch (e: any) {
    console.error('文件上传失败', e)
  } finally {
    uploading.value = false
  }
}

function removeAttachment(id: number) {
  attachments.value = attachments.value.filter(a => a.id !== id)
}
</script>

<style scoped>
.chat-input-wrapper {
  border-top: 1px solid #e8e8e8;
}

.active-skill-hint {
  padding: 6px 16px 0;
}

.attachment-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 6px 16px 0;
}

.attachment-item {
  max-width: 200px;
}

.input-row {
  display: flex;
  gap: 8px;
  padding: 8px 16px 12px;
  align-items: flex-end;
}

.upload-btn {
  flex-shrink: 0;
}

.input-container {
  flex: 1;
  min-width: 0;
  position: relative;
}

.at-menu {
  position: absolute;
  bottom: 100%;
  left: 0;
  right: 0;
  max-height: 240px;
  overflow-y: auto;
  background: #fff;
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  box-shadow: 0 -4px 12px rgba(0, 0, 0, 0.08);
  margin-bottom: 4px;
  z-index: 100;
}

.at-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  cursor: pointer;
  transition: background 0.1s;
}

.at-item:hover {
  background: #f3f3f5;
}

.at-icon {
  font-size: 18px;
  flex-shrink: 0;
}

.at-name {
  font-size: 13px;
  font-weight: 500;
}

.at-desc {
  font-size: 11px;
  color: #999;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 300px;
}

.at-empty {
  padding: 12px;
  text-align: center;
  color: #999;
  font-size: 13px;
}
</style>
