<template>
  <Teleport to="body">
    <Transition name="toolbar-fade">
      <div
        v-if="visible && position"
        ref="toolbarRef"
        class="highlight-toolbar"
        :style="toolbarStyle"
      >
        <!-- 主按钮（最多 3 个 + 更多） -->
        <button
          v-for="preset in mainPresets"
          :key="preset"
          class="preset-btn"
          @click="handlePresetClick(preset)"
        >
          {{ LABELS[preset] }}
        </button>

        <!-- 更多按钮 -->
        <button v-if="extraPresets.length > 0" class="preset-btn more-btn" @click="showMore = !showMore">
          ⋯
        </button>

        <!-- 展开的更多面板 -->
        <div v-if="showMore" class="more-panel">
          <button
            v-for="preset in extraPresets"
            :key="preset"
            class="preset-btn"
            @click="handlePresetClick(preset)"
          >
            {{ LABELS[preset] }}
          </button>
          <div class="custom-input-row">
            <input
              ref="customInputRef"
              v-model="customInstruction"
              placeholder="输入修改指令..."
              @keydown.enter="handleCustomSubmit"
            />
            <button class="send-btn" :disabled="!customInstruction.trim()" @click="handleCustomSubmit">发送</button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick } from 'vue'
import { useArtifactStore } from '@/stores/artifact'
import {
  DOC_CATEGORY_META,
  HIGHLIGHT_PRESET_LABELS as LABELS,
  HIGHLIGHT_PRESET_PROMPTS,
  type HighlightPreset,
  type DocCategory,
} from '@/types/artifact'

const props = defineProps<{
  visible: boolean
  /** 选区在视口中的位置 */
  position: { x: number; y: number } | null
}>()

const emit = defineEmits<{
  rewrite: [instruction: string]
}>()

const artifactStore = useArtifactStore()
const toolbarRef = ref<HTMLElement>()
const showMore = ref(false)
const customInstruction = ref('')
const customInputRef = ref<HTMLInputElement>()

// 根据 doc_category 获取预设操作列表
const allPresets = computed(() => {
  const category = (artifactStore.activeArtifact?.docCategory ?? 'general') as DocCategory
  return DOC_CATEGORY_META[category].highlightActions
})

// 主按钮（前 3 个，不含 custom）
const mainPresets = computed(() => allPresets.value.filter(p => p !== 'custom').slice(0, 3))

// 更多按钮（第 3 个之后 + custom）
const extraPresets = computed(() => allPresets.value.filter(p => p !== 'custom').slice(3))

// 工具栏定位样式（选区上方，边界保护 16px）
const toolbarStyle = computed(() => {
  if (!props.position) return {}
  const x = Math.max(16, Math.min(props.position.x, window.innerWidth - 300))
  const y = Math.max(16, props.position.y - 48) // 选区上方 48px
  return {
    left: `${x}px`,
    top: `${y}px`,
  }
})

function handlePresetClick(preset: HighlightPreset) {
  if (preset === 'custom') {
    showMore.value = true
    nextTick(() => customInputRef.value?.focus())
    return
  }
  const prompt = HIGHLIGHT_PRESET_PROMPTS[preset]
  if (prompt) {
    emit('rewrite', prompt)
    showMore.value = false
  }
}

function handleCustomSubmit() {
  if (customInstruction.value.trim()) {
    emit('rewrite', customInstruction.value.trim())
    customInstruction.value = ''
    showMore.value = false
  }
}

// 300ms 延迟弹出（防误触）
const delayedVisible = ref(false)
let showTimer: ReturnType<typeof setTimeout> | null = null

watch(() => props.visible, (v) => {
  if (v) {
    showTimer = setTimeout(() => { delayedVisible.value = true }, 300)
  } else {
    if (showTimer) clearTimeout(showTimer)
    delayedVisible.value = false
    showMore.value = false
  }
})
</script>

<style scoped>
.highlight-toolbar {
  position: fixed;
  z-index: 3000;
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 6px;
  background: #fff;
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
}

.preset-btn {
  padding: 4px 10px;
  border: none;
  border-radius: 4px;
  background: transparent;
  font-size: 13px;
  cursor: pointer;
  white-space: nowrap;
  color: #333;
  transition: background 0.15s;
}
.preset-btn:hover {
  background: #f0f4ff;
  color: #2080f0;
}
.more-btn {
  font-size: 16px;
  letter-spacing: 2px;
}

.more-panel {
  position: absolute;
  top: 100%;
  left: 0;
  margin-top: 4px;
  padding: 6px;
  background: #fff;
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  min-width: 200px;
}

.custom-input-row {
  display: flex;
  gap: 4px;
  width: 100%;
  margin-top: 4px;
  padding-top: 4px;
  border-top: 1px solid #eee;
}
.custom-input-row input {
  flex: 1;
  padding: 4px 8px;
  border: 1px solid #ddd;
  border-radius: 4px;
  font-size: 13px;
  outline: none;
}
.custom-input-row input:focus {
  border-color: #2080f0;
}
.send-btn {
  padding: 4px 10px;
  border: none;
  border-radius: 4px;
  background: #2080f0;
  color: #fff;
  font-size: 13px;
  cursor: pointer;
}
.send-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* Transition */
.toolbar-fade-enter-active { transition: opacity 0.15s, transform 0.15s; }
.toolbar-fade-leave-active { transition: opacity 0.1s; }
.toolbar-fade-enter-from { opacity: 0; transform: translateY(4px); }
.toolbar-fade-leave-to { opacity: 0; }
</style>
