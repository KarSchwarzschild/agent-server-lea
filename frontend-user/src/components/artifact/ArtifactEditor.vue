<template>
  <div class="artifact-editor" ref="containerRef">
    <!-- 模式 1: 流式渲染（markdown-it + citation，只读） -->
    <div
      v-if="isStreaming"
      class="streaming-view markdown-body"
      v-html="streamingHtml"
    />

    <!-- 模式 2: Tiptap 编辑器（仅 editing 模式） -->
    <template v-else-if="isEditing && editorReady && EditorContent">
      <component
        :is="EditorContent"
        :editor="editor"
        class="tiptap-editor"
      />
    </template>

    <!-- 模式 3: 只读/版本浏览（markdown-it + citation） -->
    <div
      v-else
      class="readonly-view markdown-body"
      v-html="staticHtml"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount } from 'vue'
import MarkdownIt from 'markdown-it'
import { useArtifactStore } from '@/stores/artifact'
import { useArtifactEditor } from '@/composables/useArtifactEditor'
import { protectCitationMarks, restoreCitationMarks, hasRefPattern } from '@/utils/citationRenderer'
import type { TextHighlight } from '@/types/artifact'

const props = defineProps<{
  editable?: boolean
}>()

const emit = defineEmits<{
  'content-change': [markdown: string]
  'selection-change': [highlight: TextHighlight | null]
}>()

const artifactStore = useArtifactStore()
const containerRef = ref<HTMLElement>()

const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

const isStreaming = computed(() => artifactStore.isStreaming)
const isEditing = computed(() => artifactStore.panelMode === 'editing')
const editableRef = computed(() => props.editable !== false && isEditing.value)

// ==================== 引用渲染辅助 ====================

function renderWithCitations(rawContent: string): string {
  if (!rawContent) return ''
  const refs = artifactStore.activeReferences || []
  if (refs.length === 0 && !hasRefPattern(rawContent)) {
    return md.render(rawContent)
  }
  const { content: protected_, placeholders } = protectCitationMarks(rawContent)
  let html = md.render(protected_)
  html = restoreCitationMarks(html, placeholders, refs)
  return html
}

// ==================== 流式渲染 ====================

const streamingHtml = computed(() => {
  if (!artifactStore.streamingContent) return ''
  return renderWithCitations(artifactStore.streamingContent)
})

// ==================== 静态渲染（只读 / 版本浏览） ====================

const staticHtml = computed(() => {
  let content = artifactStore.displayContent || artifactStore.currentContent?.content
  if (!content) return ''
  // 清理开头的对话性文字：从第一个 Markdown 标题开始
  const titleIdx = content.search(/^#{1,4}\s+\S/m)
  if (titleIdx > 0) {
    content = content.slice(titleIdx)
  }
  // 清理开头的 #--- 残留
  content = content.replace(/^[\s#]*---[\s]*\n*/m, '')
  return renderWithCitations(content)
})

// ==================== Tiptap 编辑器 ====================

const editorReady = ref(false)
let editor: any = null
let EditorContent: any = null

const {
  editor: tiptapEditor,
  isReady,
  EditorContentComponent,
  init: initEditor,
  updateContent,
  getMarkdown,
  destroy: destroyEditor,
} = useArtifactEditor({
  editable: editableRef,
  onChange: (markdown: string) => {
    emit('content-change', markdown)
  },
  onSelectionChange: (highlight: TextHighlight | null) => {
    emit('selection-change', highlight)
  },
})

// 进入编辑模式时初始化 Tiptap
watch(isEditing, async (editing, wasEditing) => {
  if (editing && !wasEditing) {
    const scrollTop = containerRef.value?.scrollTop ?? 0
    const content = artifactStore.currentContent?.content || artifactStore.displayContent
    await initEditor(content)
    editor = tiptapEditor.value
    EditorContent = EditorContentComponent.value
    editorReady.value = true
    await nextTick()
    if (containerRef.value) {
      containerRef.value.scrollTop = scrollTop
    }
  } else if (!editing && wasEditing) {
    // 退出编辑模式：销毁 Tiptap，回到 markdown-it 渲染
    destroyEditor()
    editor = null
    EditorContent = null
    editorReady.value = false
  }
})

// 版本切换时：如果在编辑模式则更新 Tiptap 内容
watch(() => artifactStore.activeArtifact?.currentIndex, (newIdx, oldIdx) => {
  if (newIdx !== oldIdx && editorReady.value && isEditing.value && !isStreaming.value) {
    const content = artifactStore.currentContent?.content
    if (content) {
      updateContent(content)
    }
  }
})

onBeforeUnmount(() => {
  destroyEditor()
})

// 暴露 editor 实例给父组件（用于构建锚点三元组）
defineExpose({
  getEditor: () => tiptapEditor.value,
})
</script>

<style scoped>
.artifact-editor {
  height: 100%;
  overflow-y: auto;
  scroll-behavior: smooth;
}

.streaming-view,
.readonly-view,
.tiptap-editor {
  font-size: 15px;
  line-height: 1.8;
  color: #374151;
  word-break: break-word;
}

/* Tiptap 编辑器样式 */
.tiptap-editor :deep(.ProseMirror) {
  outline: none;
  min-height: 300px;
}
.tiptap-editor :deep(.ProseMirror p.is-editor-empty:first-child::before) {
  content: attr(data-placeholder);
  color: #aaa;
  pointer-events: none;
  float: left;
  height: 0;
}
</style>

<!-- 非 scoped：引用角标样式（markdown-it 输出在 v-html 中，scoped 无法穿透） -->
<style>
.artifact-editor .citation-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 4px;
  font-size: 11px;
  font-weight: 600;
  color: #2080f0;
  background: #eff6ff;
  border-radius: 3px;
  cursor: pointer;
  vertical-align: super;
  line-height: 1;
  transition: background 0.15s;
}
.artifact-editor .citation-mark:hover {
  background: #dbeafe;
  color: #1d4ed8;
}
.artifact-editor .citation-mark + .citation-mark {
  margin-left: 2px;
}
</style>
