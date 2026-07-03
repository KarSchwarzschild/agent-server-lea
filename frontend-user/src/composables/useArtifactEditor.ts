/**
 * Tiptap 编辑器 composable
 *
 * 提供：
 * - Tiptap 编辑器初始化（含表格、高亮、Markdown 往返）
 * - 外部内容更新（流式完成后加载）
 * - 选区捕获（TextHighlight 数据）
 * - Markdown 序列化
 * - 编辑状态管理
 */

import { ref, watch, markRaw, shallowRef, type Ref } from 'vue'
import type { TextHighlight } from '@/types/artifact'

// 动态 import Tiptap（按需加载，不影响首屏）
let _editorModule: any = null
async function loadTiptap() {
  if (_editorModule) return _editorModule
  const [
    { Editor, EditorContent },
    { default: StarterKit },
    { default: Table },
    { default: TableRow },
    { default: TableCell },
    { default: TableHeader },
    { default: Highlight },
    { default: Placeholder },
    { Markdown },
  ] = await Promise.all([
    import('@tiptap/vue-3'),
    import('@tiptap/starter-kit'),
    import('@tiptap/extension-table'),
    import('@tiptap/extension-table-row'),
    import('@tiptap/extension-table-cell'),
    import('@tiptap/extension-table-header'),
    import('@tiptap/extension-highlight'),
    import('@tiptap/extension-placeholder'),
    import('tiptap-markdown'),
  ])
  _editorModule = {
    Editor, EditorContent,
    StarterKit, Table, TableRow, TableCell, TableHeader,
    Highlight, Placeholder, Markdown,
  }
  return _editorModule
}

export interface ArtifactEditorOptions {
  editable: Ref<boolean>
  onChange: (markdown: string) => void
  onSelectionChange: (highlight: TextHighlight | null) => void
}

export function useArtifactEditor(options: ArtifactEditorOptions) {
  const editor = shallowRef<any>(null)
  const isReady = ref(false)
  const EditorContentComponent = shallowRef<any>(null)

  async function init(initialContent: string = '') {
    const mod = await loadTiptap()
    EditorContentComponent.value = markRaw(mod.EditorContent)

    editor.value = markRaw(new mod.Editor({
      extensions: [
        mod.StarterKit.configure({
          heading: { levels: [1, 2, 3, 4] },
        }),
        mod.Table.configure({ resizable: true }),
        mod.TableRow,
        mod.TableCell,
        mod.TableHeader,
        mod.Highlight,
        mod.Placeholder.configure({ placeholder: '开始编写...' }),
        mod.Markdown.configure({
          html: false,
          transformPastedText: true,
        }),
      ],
      content: initialContent,
      editable: options.editable.value,
      onUpdate: ({ editor: ed }: any) => {
        const markdown = ed.storage.markdown.getMarkdown()
        options.onChange(markdown)
      },
      onSelectionUpdate: ({ editor: ed }: any) => {
        const { from, to } = ed.state.selection
        if (from === to) {
          options.onSelectionChange(null)
          return
        }

        const selectedText = ed.state.doc.textBetween(from, to, ' ')
        if (!selectedText.trim()) {
          options.onSelectionChange(null)
          return
        }

        // 获取选区所在的块级节点文本
        const $from = ed.state.doc.resolve(from)
        const blockNode = $from.node($from.depth)
        const markdownBlock = blockNode?.textContent ?? selectedText

        const fullMarkdown = ed.storage.markdown.getMarkdown()

        options.onSelectionChange({
          fullMarkdown,
          markdownBlock,
          selectedText: selectedText.trim(),
        })
      },
    }))

    isReady.value = true
  }

  // 监听 editable 变化
  watch(options.editable, (val) => {
    editor.value?.setEditable(val)
  })

  /** 外部更新内容（流式完成后、版本切换时调用） */
  function updateContent(markdown: string) {
    if (!editor.value) return

    // 保存滚动位置
    const editorEl = editor.value.view?.dom?.parentElement
    const scrollTop = editorEl?.scrollTop ?? 0

    // 通过 Markdown 扩展加载内容
    editor.value.commands.setContent(markdown)

    // 恢复滚动位置（nextTick）
    requestAnimationFrame(() => {
      if (editorEl) {
        editorEl.scrollTop = Math.min(scrollTop, editorEl.scrollHeight)
      }
    })
  }

  /** 获取当前编辑器的完整 Markdown */
  function getMarkdown(): string {
    return editor.value?.storage.markdown.getMarkdown() ?? ''
  }

  /** 销毁编辑器 */
  function destroy() {
    editor.value?.destroy()
    editor.value = null
    isReady.value = false
  }

  // 注意：不在此处调用 onBeforeUnmount（可能在非 setup 上下文中使用）
  // 调用方应在组件 onBeforeUnmount 中手动调用 destroy()

  return {
    editor,
    isReady,
    EditorContentComponent,
    init,
    updateContent,
    getMarkdown,
    destroy,
  }
}
