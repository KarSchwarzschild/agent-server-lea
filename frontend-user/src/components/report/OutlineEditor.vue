<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'

interface ChapterItem {
  chapter_id: string
  title: string
  description: string
  chapter_type: string
  key_points: string[]
}

const props = defineProps<{
  chapters: ChapterItem[]
  totalChapters: number
  timeout: number // 秒
  taskId: string
}>()

const emit = defineEmits<{
  (e: 'confirm', outline: { chapters: ChapterItem[] }): void
  (e: 'skip'): void
}>()

// ── 编辑状态 ──
const editableChapters = ref<ChapterItem[]>([])
const isCollapsed = ref(false)

onMounted(() => {
  editableChapters.value = JSON.parse(JSON.stringify(props.chapters))
  startCountdown()
})

// ── 倒计时 ──
const countdown = ref(props.timeout)
const showCountdown = computed(() => countdown.value <= 30)
let timer: ReturnType<typeof setInterval> | null = null

function startCountdown() {
  timer = setInterval(() => {
    countdown.value--
    if (countdown.value <= 0) {
      clearInterval(timer!)
      emit('skip')
    }
  }, 1000)
}

onUnmounted(() => {
  if (timer) clearInterval(timer)
})

const countdownText = computed(() => {
  const m = Math.floor(countdown.value / 60)
  const s = countdown.value % 60
  return `${m}:${String(s).padStart(2, '0')}`
})

// ── 章节编辑 ──
function updateTitle(idx: number, val: string) {
  editableChapters.value[idx].title = val
}

function updateKeyPoints(idx: number, val: string) {
  editableChapters.value[idx].key_points = val.split('\n').filter(Boolean)
}

function getKeyPointsText(ch: ChapterItem): string {
  return ch.key_points.join('\n')
}

// ── 操作 ──
function handleConfirm() {
  emit('confirm', { chapters: editableChapters.value })
  isCollapsed.value = true
  if (timer) clearInterval(timer)
}

function handleSkip() {
  emit('skip')
  isCollapsed.value = true
  if (timer) clearInterval(timer)
}

// ── 统计 ──
const totalWordEstimate = computed(() => {
  return editableChapters.value.length * 1500 // 粗略估算
})
</script>

<template>
  <!-- 折叠态 -->
  <div v-if="isCollapsed" class="outline-collapsed" @click="isCollapsed = false">
    <span class="collapsed-bar" />
    <span class="collapsed-text">
      研究大纲已确认 &middot; {{ editableChapters.length }} 章节 &middot; 共约 {{ totalWordEstimate.toLocaleString() }} 字
    </span>
    <span class="collapsed-toggle">查看详情 &#x25BC;</span>
  </div>

  <!-- 展开态 -->
  <div v-else class="outline-editor">
    <div class="editor-header">
      <div class="header-left">
        <span class="header-bar" />
        <span class="header-title">研究大纲已生成</span>
      </div>
      <span v-if="showCountdown" class="countdown">
        {{ countdownText }} 后自动继续
      </span>
    </div>

    <div class="editor-body">
      <div
        v-for="(ch, idx) in editableChapters"
        :key="ch.chapter_id"
        class="chapter-edit-row"
      >
        <div class="chapter-header-row">
          <span class="chapter-num">{{ idx + 1 }}.</span>
          <input
            class="chapter-title-input"
            :value="ch.title"
            @input="updateTitle(idx, ($event.target as HTMLInputElement).value)"
            placeholder="章节标题"
          />
        </div>
        <textarea
          class="chapter-keypoints"
          :value="getKeyPointsText(ch)"
          @input="updateKeyPoints(idx, ($event.target as HTMLTextAreaElement).value)"
          placeholder="关键要点（每行一个）"
          rows="2"
        />
      </div>
    </div>

    <div class="editor-hint">
      如需修改，直接编辑上方文本后点击确认
    </div>

    <div class="editor-actions">
      <button class="btn-skip" @click="handleSkip">直接开始写作</button>
      <button class="btn-confirm" @click="handleConfirm">确认此大纲</button>
    </div>
  </div>
</template>

<style scoped>
.outline-editor {
  border: 1px solid #e0e0e0;
  border-left: 3px solid #3b82f6;
  border-radius: 8px;
  background: #fff;
  margin: 16px 0;
  overflow: hidden;
}

.editor-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  background: #f8fafc;
  border-bottom: 1px solid #f0f0f0;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.header-bar {
  width: 3px;
  height: 16px;
  background: #3b82f6;
  border-radius: 2px;
}

.header-title {
  font-size: 14px;
  font-weight: 600;
  color: #333;
}

.countdown {
  font-size: 12px;
  color: #f59e0b;
  font-weight: 500;
  animation: pulse 1.5s ease-in-out infinite;
}

.editor-body {
  padding: 16px;
}

.chapter-edit-row {
  margin-bottom: 12px;
  padding: 10px 12px;
  background: #fafbfc;
  border-radius: 6px;
  border: 1px solid #f0f0f0;
}

.chapter-header-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.chapter-num {
  font-size: 14px;
  font-weight: 600;
  color: #3b82f6;
  min-width: 24px;
}

.chapter-title-input {
  flex: 1;
  border: none;
  background: transparent;
  font-size: 14px;
  font-weight: 500;
  color: #333;
  padding: 4px 0;
  outline: none;
  border-bottom: 1px solid transparent;
}

.chapter-title-input:focus {
  border-bottom-color: #3b82f6;
}

.chapter-keypoints {
  width: 100%;
  border: none;
  background: transparent;
  font-size: 12px;
  color: #666;
  resize: vertical;
  outline: none;
  line-height: 1.6;
  padding: 4px 0 4px 32px;
}

.chapter-keypoints:focus {
  background: #f0f4ff;
  border-radius: 4px;
}

.editor-hint {
  text-align: center;
  font-size: 12px;
  color: #999;
  padding: 4px 16px;
  border-top: 1px dashed #e8e8e8;
}

.editor-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 12px 16px;
}

.btn-skip {
  padding: 6px 16px;
  border: 1px solid #d0d0d0;
  border-radius: 6px;
  background: #fff;
  color: #666;
  font-size: 13px;
  cursor: pointer;
}

.btn-skip:hover {
  background: #f5f5f5;
}

.btn-confirm {
  padding: 6px 16px;
  border: none;
  border-radius: 6px;
  background: #3b82f6;
  color: #fff;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
}

.btn-confirm:hover {
  background: #2563eb;
}

/* ── 折叠态 ── */
.outline-collapsed {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  border: 1px solid #e0e0e0;
  border-left: 3px solid #3b82f6;
  border-radius: 8px;
  background: #f8fafc;
  cursor: pointer;
  margin: 16px 0;
}

.collapsed-bar {
  width: 3px;
  height: 14px;
  background: #3b82f6;
  border-radius: 2px;
}

.collapsed-text {
  flex: 1;
  font-size: 13px;
  color: #555;
  font-weight: 500;
}

.collapsed-toggle {
  font-size: 12px;
  color: #3b82f6;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}
</style>
